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
