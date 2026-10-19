import pytest

from wowbox.automation import AutomationEngine
from wowbox.bridge.decoder import ClientState
from wowbox.config import AppConfig


class FakeSender:
    def __init__(self):
        self.sent = []

    def send_chord(self, chord, target):
        self.sent.append((str(chord), target))
        return [1]


class Source:
    def __init__(self):
        self.st = {1: ClientState(), 3: ClientState(power_pct=80)}
        self.missing = []

    def states(self):
        return self.st

    def missing_windows(self):
        return self.missing

    def slots_for(self, target):
        if isinstance(target, list):
            return sorted(set(target) & set(self.st))
        return {"all": sorted(self.st), "leader": [1]}.get(target, [])


def engine(rules, **opts):
    cfg = AppConfig.model_validate({"automation": rules,
                                    "automation_options": {"enabled_on_start": True, "dry_run": False, **opts}})
    sender, src = FakeSender(), Source()
    return AutomationEngine(cfg.automation, cfg.automation_options, sender, src), sender, src


DRINK = {"name": "drink", "when": {"slot": 3, "combat": False, "power_pct_lt": 40},
         "do": {"send": "Ctrl+9", "to": [3]}, "cooldown": "20s"}


def test_condition_and_cooldown():
    e, sender, src = engine([DRINK])
    e.tick(0)
    assert sender.sent == []
    src.st[3] = ClientState(power_pct=30)
    e.tick(1)
    assert sender.sent == [("Ctrl+9", [3])]
    e.tick(5)  # cooldown
    assert len(sender.sent) == 1
    e.tick(22)
    assert len(sender.sent) == 2
    src.st[3] = ClientState(power_pct=30, combat=True)
    e.tick(50)
    assert len(sender.sent) == 2


def test_state_rule_without_cooldown_fires_at_most_once_a_second():
    # a cast that keeps failing (e.g. out of range) never goes on cooldown in the game: its condition stays true
    rule = {"name": "shot", "when": {"slot": 3, "power_pct_gt": 50}, "do": {"send": "F2", "to": [3]}}
    e, sender, _ = engine([rule], rule_gap=0)
    for t in range(30):
        e.tick(t / 10)  # 3 s of ticks, 0.1 s apart
    assert len(sender.sent) == 3


def test_every_and_sequence_with_wait():
    rule = {"name": "buff", "every": "10s",
            "do": {"sequence": [{"send": "F5", "to": "all"}, {"wait": "1.5s"}, {"send": "F6", "to": "all"}]}}
    e, sender, _ = engine([rule])
    e.tick(0)  # arms the timer
    e.tick(9.9)
    assert sender.sent == []
    e.tick(10)
    assert sender.sent == [("F5", "all")]
    e.tick(11)
    assert len(sender.sent) == 1
    e.tick(11.6)
    assert sender.sent[-1] == ("F6", "all")
    e.tick(20.1)
    assert sender.sent[-1] == ("F5", "all")


def test_dry_run_sends_nothing():
    e, sender, src = engine([DRINK], dry_run=True)
    src.st[3] = ClientState(power_pct=10)
    events = []
    e.on_event = events.append
    e.tick(0)
    assert sender.sent == []
    assert any("dry run" in m for m in events)


def test_stale_bridge_pauses_only_the_rules_that_touch_it():
    leader_rule = {"name": "leader", "every": "10s", "do": {"send": "F1", "to": "leader"}}
    e, sender, src = engine([DRINK, leader_rule])
    src.st[3] = ClientState(power_pct=10)
    src.st[1] = ClientState(stale=True)
    e.tick(0)
    assert sender.sent == [("Ctrl+9", [3])]  # drink only touches slot 3: it runs
    assert e.blocked == {"leader": "can't read the bridge of slot(s) [1]"} and "1 rule(s) paused" in e.paused_reason
    assert e.status(e.rules[1], 0).state == "paused: can't read the bridge of slot(s) [1]"
    e.tick(11)
    assert len(sender.sent) == 1  # the timer waits while slot 1 is unreadable
    src.st[1] = ClientState()
    e.tick(12)
    assert e.blocked == {} and e.paused_reason is None


def test_closed_window_doesnt_pause_anything():
    e, sender, src = engine([DRINK])
    src.st[3] = ClientState(power_pct=10)
    src.missing = [2]  # you closed slot 2 on purpose
    e.tick(0)
    assert sender.sent == [("Ctrl+9", [3])] and e.paused_reason is None


def test_stale_pause_can_be_turned_off():
    e, sender, src = engine([DRINK], pause_on_stale_bridge=False)
    src.st[3] = ClientState(power_pct=10)
    src.st[1] = ClientState(stale=True)
    e.tick(0)
    assert e.blocked == {} and len(sender.sent) == 1


def test_kill_switch():
    e, sender, src = engine([DRINK])
    src.st[3] = ClientState(power_pct=10)
    e.kill()
    e.tick(0)
    assert sender.sent == []


def test_disabled_rule_and_any_slot():
    rule = {"name": "low hp anywhere", "when": {"health_pct_lt": 30}, "do": {"send": "F9", "to": "leader"}}
    e, sender, src = engine([rule, {**DRINK, "enabled": False}])
    src.st[1] = ClientState(health_pct=20)
    src.st[3] = ClientState(power_pct=1)
    e.tick(0)
    assert sender.sent == [("F9", "leader")]


def test_unknown_field_rejected():
    with pytest.raises(ValueError, match="unknown condition field"):
        engine([{"name": "x", "when": {"mana_lt": 3}, "do": {"send": "1"}}])


def test_a_claim_left_by_a_rule_that_stopped_waiting_blocks_nothing():
    # a rule waiting for a character claims it (it goes first); reloading the rules, or the rule's condition
    # turning false, must not leave that claim behind: it blocked every rule on that character for good
    dot = {"name": "dot", "when": {"slot": 3, "combat": True}, "do": {"send": "F3", "to": [3]}, "priority": 10}
    assist = {"name": "assist", "when": {"slot": 3, "power_pct_gt": 50}, "do": {"send": "F2", "to": [3]},
              "cooldown": "5s"}
    e, sender, _ = engine([dot, assist])
    e._claims[3] = ("dot", 10)  # as "dot" left it, waiting when the rules were reloaded
    e.load([rt.rule for rt in e.rules])
    e.tick(0)
    assert sender.sent == [("F2", [3])]
    e._claims[3] = ("dot", 10)  # stale: "dot" isn't waiting (its condition is false)
    e.tick(6)
    assert sender.sent == [("F2", [3])] * 2


def test_a_rule_that_waited_its_turn_is_dropped_if_its_condition_is_gone():
    # ten DoTs fire together and go out 1.5 s apart: one still queued when the mob dies must not be cast (the game
    # would pick the nearest mob for it)
    first = {"name": "first", "when": {"slot": 3, "power_pct_gt": 50}, "do": {"send": "F1", "to": [3]},
             "cooldown": "1m", "priority": 5}
    second = {"name": "second", "when": {"slot": 3, "power_pct_gt": 50, "combat": False},
              "do": {"send": "F2", "to": [3]}, "cooldown": "1m"}
    e, sender, src = engine([first, second], rule_gap=1.5)
    e.tick(0)
    assert sender.sent == [("F1", [3])]  # "second" waits for the global cooldown
    src.st[3] = ClientState(power_pct=80, combat=True)  # its condition is gone before its turn
    e.tick(2)
    assert sender.sent == [("F1", [3])]
    src.st[3] = ClientState(power_pct=80)
    e.tick(3)  # nothing waits for the dropped one: it fires again once its condition holds (after its cooldown)
    assert sender.sent == [("F1", [3])]
    e.tick(61)
    e.tick(63)  # after "first" again, then the global cooldown
    assert sender.sent[-1] == ("F2", [3])


def test_a_rule_that_waited_its_turn_still_goes_if_its_condition_holds():
    first = {"name": "first", "when": {"slot": 3, "power_pct_gt": 50}, "do": {"send": "F1", "to": [3]},
             "cooldown": "1m", "priority": 5}
    second = {"name": "second", "when": {"slot": 3, "power_pct_gt": 50}, "do": {"send": "F2", "to": [3]},
              "cooldown": "1m"}
    e, sender, _ = engine([first, second], rule_gap=1.5)
    e.tick(0)
    e.tick(2)
    assert sender.sent == [("F1", [3]), ("F2", [3])]


def test_a_waiting_rule_is_not_dropped_for_the_global_cooldown():
    # the DoT before it has just put every spell on the global cooldown: `ready:`/`dot:` read "not now" for a moment
    first = {"name": "first", "when": {"slot": 3, "power_pct_gt": 50}, "do": {"send": "F1", "to": [3]},
             "cooldown": "1m", "priority": 5}
    dot = {"name": "dot", "when": {"slot": 3, "power_pct_gt": 50, "dot": "Corruption"},
           "do": {"send": "F2", "to": [3]}, "cooldown": "1m"}
    e, sender, src = engine([first, dot], rule_gap=1.5)
    src.st[3] = ClientState(power_pct=80, dots=("corruption",))
    e.tick(0)
    src.st[3] = ClientState(power_pct=80)  # global cooldown
    e.tick(2)
    assert sender.sent == [("F1", [3]), ("F2", [3])]


def test_a_queued_rules_cooldown_runs_from_when_it_goes_out():
    # ten DoTs fire together: the last ones wait longer than their own cooldown. Counted from joining the line,
    # the cooldown is already over when the cast goes out, and the rule fires again 0.1 s later (the DoT hasn't
    # landed yet), straight into the global cooldown: "spell isn't ready yet"
    dots = [{"name": f"dot{i}", "when": {"slot": 3, "power_pct_gt": 50}, "do": {"send": f"F{i}", "to": [3]},
             "cooldown": "6s", "priority": 10 - i} for i in range(1, 7)]
    e, sender, _ = engine(dots, rule_gap=1.5)
    at, t = [], 0.0
    while t <= 20:
        n = len(sender.sent)
        e.tick(round(t, 2))
        at += [(round(t, 2), k) for k, _ in sender.sent[n:]]
        t += 0.1
    times = [w for w, _ in at]
    assert all(b - a >= 1.5 - 1e-9 for a, b in zip(times, times[1:]))  # never two casts inside the gap
    for key in {k for _, k in at}:
        mine = [w for w, k in at if k == key]
        assert all(b - a >= 6 - 1e-9 for a, b in zip(mine, mine[1:])), (key, mine)  # 6 s from each cast


def test_a_rule_firing_again_waits_for_the_gap_after_its_own_last_step():
    rule = {"name": "dot", "when": {"slot": 3, "power_pct_gt": 50}, "do": {"send": "F2", "to": [3]},
            "cooldown": "1s"}
    e, sender, _ = engine([rule], rule_gap=1.5)
    e.tick(0)
    e.tick(1.1)  # off its cooldown, but its own cast 1.1 s ago still holds the global cooldown
    assert len(sender.sent) == 1
    e.tick(1.6)
    assert len(sender.sent) == 2


def test_a_sequence_is_not_held_up_by_its_own_steps():
    rule = {"name": "buff", "when": {"slot": 3, "power_pct_gt": 50}, "cooldown": "1m",
            "do": {"sequence": [{"send": "F1", "to": [3]}, {"wait": "200ms"}, {"send": "F2", "to": [3]}]}}
    e, sender, _ = engine([rule], rule_gap=1.5)
    e.tick(0)
    e.tick(0.3)
    assert [k for k, _ in sender.sent] == ["F1", "F2"]


def test_cooldown_counts_from_the_send_after_waiting_in_line():
    first = {"name": "first", "when": {"slot": 3, "power_pct_gt": 50}, "do": {"send": "F1", "to": [3]},
             "cooldown": "1m", "priority": 5}
    dot = {"name": "dot", "when": {"slot": 3, "power_pct_gt": 50}, "do": {"send": "F2", "to": [3]},
           "cooldown": "3s"}
    e, sender, _ = engine([first, dot], rule_gap=1.5)
    t, at = 0.0, []
    while t <= 6:
        n = len(sender.sent)
        e.tick(round(t, 2))
        at += [(round(t, 2), k) for k, _ in sender.sent[n:]]
        t += 0.1
    assert [w for w, k in at if k == "F2"] == [1.5, 4.5]  # 3 s after it went out at 1.5 s, not after joining at 0


def test_a_no_gcd_rule_skips_the_gap_and_does_not_hold_up_spells():
    """/assist and /petattack aren't on the game's global cooldown: they went out 1.5 s after a spell and held the
    next spell up 1.5 s, so the pet and the first DoT were 1.5-3 s late on every pull."""
    cond = {"slot": 3, "power_pct_gt": 50}
    rules = [{"name": "dot1", "when": cond, "do": {"send": "F1", "to": [3]}, "cooldown": "10s"},
             {"name": "assist", "when": cond, "do": {"send": "F2", "to": [3]}, "cooldown": "500ms", "gcd": False},
             {"name": "pet", "when": cond, "do": {"send": "F3", "to": [3]}, "cooldown": "10s", "gcd": False},
             {"name": "dot2", "when": cond, "do": {"send": "F4", "to": [3]}, "cooldown": "10s"}]
    e, sender, src = engine(rules, rule_gap=1.5)
    at, t = [], 0.0
    while t <= 2:
        if round(t, 2) == 0.5:
            src.st[3] = ClientState(power_pct=80, casting=True)  # a cast doesn't hold /assist up either
        n = len(sender.sent)
        e.tick(round(t, 2))
        at += [(k, round(t, 2)) for k, _ in sender.sent[n:]]
        t += 0.1
    first = {k: w for k, w in reversed(at)}
    assert first["F1"] == 0 and first["F2"] == 0 and first["F3"] == 0  # straight away, next to the spell
    assert [w for k, w in at if k == "F2"][:2] == [0, 0.5]  # again on its own cooldown, mid-cast
    src.st[3] = ClientState(power_pct=80)
    e.tick(2.1)
    assert ("F4", [3]) in sender.sent and [k for k, _ in sender.sent].index("F4") > 0
    assert all(w >= 1.5 for k, w in at if k == "F4")  # the next spell still waits out the gap after dot1 only


def test_gcd_false_is_saved_and_loaded():
    from wowbox.rule_store import dump_rules, load_rules_text
    cfg = AppConfig.model_validate({"automation": [
        {"name": "a", "when": {"slot": 1}, "do": {"send": "F1", "to": [1]}, "gcd": False},
        {"name": "b", "when": {"slot": 1}, "do": {"send": "F1", "to": [1]}}]})
    text = dump_rules(cfg.automation)
    assert text.count("gcd: false") == 1
    assert [r.gcd for r in load_rules_text(text)] == [False, True]
