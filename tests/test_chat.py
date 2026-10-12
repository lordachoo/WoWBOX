"""Chat lines from BoxBridge: chat frames, the poller, `chat:` automation rules and the addon side."""
import numpy as np
import pytest

from wowbox.automation import AutomationEngine
from wowbox.automation.actions import MacroStep, SendStep, TypeStep, substitute
from wowbox.bridge import BridgePoller
from wowbox.bridge.decoder import ChatEvent, ClientState, decode_frame, encode, encode_chat_bytes
from wowbox.config import AppConfig, BridgeConfig
from wowbox.keys import parse_chord
from wowbox.platform.fake import FakeCapture

from conftest import HWNDS, Rig


def chat_img(sender="Tinkerdawn", text="heal me", seq=1, frame=5, channel="whisper", block=8):
    return encode({}, frame, block, data=encode_chat_bytes(channel, sender, text, seq, frame))


# ---------------------------------------------------------------- decoder
def test_chat_frame_roundtrip():
    ev = decode_frame(chat_img())
    assert isinstance(ev, ChatEvent)
    assert (ev.channel, ev.sender, ev.text, ev.seq, ev.frame) == ("whisper", "Tinkerdawn", "heal me", 1, 5)
    assert not ev.truncated


def test_chat_frame_long_and_unicode_text_is_cut_cleanly():
    long = "ää" * 20  # 80 bytes; the addon cuts at a character boundary, the reference encoder may not
    ev = decode_frame(chat_img(text=long, channel="party"))
    assert ev.channel == "party" and ev.truncated
    assert long.startswith(ev.text) and len(ev.text) == 13  # 27 bytes: the half character is dropped


def test_state_frames_still_decode():
    st = decode_frame(encode({"health": 255, "name": "Gnope"}, 3, 8))
    assert isinstance(st, ClientState) and st.name == "Gnope"


# ---------------------------------------------------------------- poller
def _poller():
    r = Rig()
    t = [0.0]
    cap = FakeCapture()
    for h in HWNDS.values():
        cap.images[h] = _screen(encode({"health": 255}, 1, 6))
    events = []
    poller = BridgePoller(BridgeConfig(stale_after=1.0), r.team, r.windows, lambda: cap, clock=lambda: t[0],
                          on_chat=lambda slot, ev: events.append((slot, ev.sender, ev.text)))
    return r, t, cap, poller, events


def _screen(strip):
    full = np.zeros((100, 400, 3), dtype=np.uint8)
    full[:strip.shape[0], :strip.shape[1]] = strip
    return full


def test_poller_reports_each_chat_line_once_and_keeps_state():
    r, t, cap, poller, events = _poller()
    poller.poll_once(cap)
    assert r.team.clients[3].state.health_pct == 100.0
    cap.images[HWNDS[3]] = _screen(chat_img(seq=1, frame=2, block=6))
    for i in range(5):  # the addon holds the frame: several polls, one event
        t[0] = 0.1 * (i + 1)
        poller.poll_once(cap)
    assert events == [(3, "Tinkerdawn", "heal me")]
    assert r.team.clients[3].state.health_pct == 100.0 and not r.team.clients[3].state.stale
    # next line straight after (state frame missed between them): different seq -> reported
    cap.images[HWNDS[3]] = _screen(chat_img(seq=2, frame=3, text="again", block=6))
    poller.poll_once(cap)
    # same seq again after a state frame (addon reloaded) -> reported again
    cap.images[HWNDS[3]] = _screen(encode({"health": 255}, 4, 6))
    poller.poll_once(cap)
    cap.images[HWNDS[3]] = _screen(chat_img(seq=2, frame=5, text="after reload", block=6))
    poller.poll_once(cap)
    assert [e[2] for e in events] == ["heal me", "again", "after reload"]


# ---------------------------------------------------------------- engine
class Sender:
    def __init__(self):
        self.sent = []

    def send_chord(self, chord, target):
        self.sent.append(("send", str(chord), target))
        return [1]

    def type_text(self, text, target):
        self.sent.append(("type", text, target))
        return [1]


class Source:
    names = {"tinkerdawn": 1, "gnope": 2, "kunth": 3}

    def __init__(self):
        self.st = {s: ClientState(name=n.title()) for n, s in self.names.items()}

    def states(self):
        return self.st

    def missing_windows(self):
        return []

    def slots_for(self, target):
        if target == "all":
            return [1, 2, 3]
        if isinstance(target, list):
            return target
        return [s for n, s in self.names.items() if n == str(target).lower()]


HEAL = {"name": "heal-on-whisper",
        "chat": {"sender": ["Tinkerdawn", "Gnope"], "contains": ["heal", "help"], "heard_by": "Kunth"},
        "do": {"sequence": [{"type": "/target {sender}", "to": "Kunth"}, {"send": "3", "to": "Kunth"}]},
        "cooldown": "3s"}


def engine(rules, **opts):
    cfg = AppConfig.model_validate({
        "automation": rules,
        "macros": [{"name": "m", "do": {"send": "F1"}}],
        "automation_options": {"enabled_on_start": True, "dry_run": False, "pause_on_stale_bridge": False, **opts}})
    sender, src = Sender(), Source()
    e = AutomationEngine(cfg.automation, cfg.automation_options, sender, src)
    return e, sender


def whisper(sender="Tinkerdawn", text="HEAL me pls", channel="whisper"):
    return ChatEvent(channel=channel, sender=sender, text=text)


def test_chat_rule_fires_with_sender_filled_in():
    e, sender = engine([HEAL])
    e.on_chat(3, whisper(), now=0, listener="Kunth")
    e.tick(0)
    assert sender.sent == [("type", "/target Tinkerdawn", "Kunth"), ("send", "3", "Kunth")]


@pytest.mark.parametrize("slot,ev", [
    (3, whisper(sender="Randompug")),           # not a listed sender
    (3, whisper(text="nice pull")),             # no keyword
    (2, whisper()),                             # heard by Gnope, rule listens on Kunth
    (3, whisper(channel="party")),              # only whispers by default
])
def test_chat_rule_filters(slot, ev):
    e, sender = engine([HEAL])
    e.on_chat(slot, ev, now=0)
    e.tick(0)
    assert sender.sent == []


def test_party_line_heard_by_everyone_fires_once_and_cooldown():
    rule = {"name": "follow", "chat": {"channel": ["party", "raid"], "contains": "follow"},
            "do": {"type": "/follow {sender}", "to": "followers"}}
    e, sender = engine([{**rule, "cooldown": "5s"}])
    for slot in (1, 2, 3):
        e.on_chat(slot, whisper(text="follow me", channel="party"), now=0)
    assert sender.sent == [("type", "/follow Tinkerdawn", "followers")]
    e.on_chat(1, whisper(text="follow me again", channel="party"), now=1)  # on cooldown
    assert len(sender.sent) == 1
    e.on_chat(1, whisper(text="follow me", channel="raid"), now=6)
    assert len(sender.sent) == 2


def test_chat_rule_needs_automation_on_and_respects_dry_run():
    e, sender = engine([HEAL], enabled_on_start=False)
    e.on_chat(3, whisper(), now=0)
    assert sender.sent == []
    e, sender = engine([HEAL], dry_run=True)
    events = []
    e.on_event = events.append
    e.on_chat(3, whisper(), now=0)
    assert sender.sent == [] and any("would type '/target Tinkerdawn'" in m for m in events)


def test_chat_rule_with_extra_state_condition():
    rule = {**HEAL, "when": {"slot": 3, "casting": False}}
    e, sender = engine([rule])
    e.source.st[3] = ClientState(casting=True)
    e.on_chat(3, whisper(), now=0)
    assert sender.sent == []
    e.source.st[3] = ClientState()
    e.on_chat(3, whisper(text="heal!"), now=1)
    assert sender.sent[0] == ("type", "/target Tinkerdawn", "Kunth")


def test_chat_rule_can_start_a_macro():
    rule = {"name": "go", "chat": {"contains": "go"}, "do": {"macro": "m"}}
    e, _ = engine([rule])
    started = []
    e.on_macro = started.append
    e.on_chat(1, whisper(text="go go"), now=0)
    assert started == ["m"]


def test_chat_rules_are_not_state_or_timer_rules():
    e, sender = engine([HEAL])
    for t in range(5):
        e.tick(t)
    assert sender.sent == []


def test_chat_values_cannot_inject_commands():
    steps = [TypeStep("/w {sender} you said: {message}", "Kunth"), SendStep(parse_chord("1"), "{sender}"),
             MacroStep("m")]
    out = substitute(steps, {"sender": "Bob\n/run X()", "message": "/run DoBadThings()|cff0000"})
    assert out[0].text == "/w BobrunX you said: run DoBadThings()cff0000"
    assert out[1].to == "BobrunX"
    assert out[2] == MacroStep("m")


def test_config_rejects_bad_triggers():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"automation": [{"name": "x", "every": "5s", "chat": {}, "do": {"send": "1"}}]})
    with pytest.raises(ValueError):
        AppConfig.model_validate({"automation": [{"name": "x", "chat": {"channel": "tells"}, "do": {"send": "1"}}]})
    cfg = AppConfig.model_validate({"automation": [{"name": "x", "chat": {"sender": "Bob"}, "do": {"send": "1"}}]})
    assert cfg.automation[0].chat.sender == ["Bob"] and cfg.automation[0].chat.channel == ["whisper"]


@pytest.mark.parametrize("word", ["all", "All", "anyone", "*", "everyone"])
def test_from_rejects_target_words(word):
    with pytest.raises(ValueError, match="Leave From empty to accept anyone"):
        AppConfig.model_validate({"automation": [{"name": "x", "chat": {"sender": [word]}, "do": {"send": "1"}}]})


def test_from_team_means_your_own_characters():
    class TeamSource(Source):
        def character_names(self):
            return {"Tinkerdawn", "Gnope", "Kunth"}

    rule = {"name": "team-heal", "chat": {"sender": ["team", "Randomfriend"], "contains": "heal", "heard_by": "Kunth"},
            "do": {"type": "/cast [@{sender}] Healing Wave", "to": "Kunth"}}
    cfg = AppConfig.model_validate({"automation": [rule], "automation_options": {
        "enabled_on_start": True, "dry_run": False, "pause_on_stale_bridge": False}})
    sender, src = Sender(), TeamSource()
    e = AutomationEngine(cfg.automation, cfg.automation_options, sender, src)
    e.on_chat(3, whisper(sender="gnope", text="heal pls"), now=0)          # one of yours, any case
    e.on_chat(3, whisper(sender="Randomfriend", text="heal pls"), now=10)  # named
    e.on_chat(3, whisper(sender="Randompug", text="heal pls"), now=20)     # a stranger
    assert [t for k, t, _ in sender.sent] == ["/cast [@gnope] Healing Wave", "/cast [@Randomfriend] Healing Wave"]


@pytest.mark.parametrize("text,fired", [
    ("light heal pls", ["light"]),
    ("chain heals!", ["chain-heals"]),
    ("chain heal", ["chain"]),
    ("heals", ["heals"]),
    ("heal me", ["heal"]),
    ("paladin buff", ["buff-anyone"]),  # no overlap with the heal words: unaffected
])
def test_most_specific_keyword_wins(text, fired):
    def rule(name, word):
        return {"name": name, "chat": {"contains": [word], "heard_by": "all"},
                "do": {"type": f"/say {name}", "to": "Kunth"}}
    rules = [rule("heal", "heal"), rule("heals", "heals"), rule("light", "light heal"),
             rule("chain", "chain heal"), rule("chain-heals", "chain heals"), rule("buff-anyone", "buff")]
    e, sender = engine(rules)
    for slot in (1, 2, 3):
        e.on_chat(slot, whisper(text=text), now=0)
    assert [t[1].removeprefix("/say ") for t in sender.sent] == fired


# ---------------------------------------------------------------- request queue and spacing between rules
BUFF = {"name": "buffs", "chat": {"channel": ["whisper", "party"], "contains": ["buff"], "heard_by": "Kunth"},
        "do": {"sequence": [{"type": "/cast [@{sender}] Kings", "to": "Kunth"}, {"wait": "1.5s"},
                            {"type": "/cast [@{sender}] Might", "to": "Kunth"}]},
        "cooldown": "1s"}


def typed(sender):
    return [t[1] for t in sender.sent if t[0] == "type"]


def run(e, start, end, step=0.1):
    t = start
    while t <= end + 1e-9:
        e.tick(round(t, 3))
        t += step


def test_three_people_asking_are_served_in_turn():
    e, sender = engine([BUFF])
    e.on_chat(3, whisper(sender="Tinkerdawn", text="buff pls"), now=0)
    e.on_chat(3, whisper(sender="Gnope", text="buff me"), now=0.5)
    e.on_chat(3, whisper(sender="Randompug", text="buffs?"), now=0.7)
    assert [q.sender for q in e.rules[0].pending] == ["Gnope", "Randompug"]
    st = e.status(e.rules[0], 0.8)
    assert "for Tinkerdawn" in st.state and "queued: Gnope, Randompug" in st.state and st.next == "next: Gnope"
    run(e, 0, 10)
    assert typed(sender) == ["/cast [@Tinkerdawn] Kings", "/cast [@Tinkerdawn] Might",
                             "/cast [@Gnope] Kings", "/cast [@Gnope] Might",
                             "/cast [@Randompug] Kings", "/cast [@Randompug] Might"]
    assert not e.rules[0].pending


def test_queue_skips_repeats_and_respects_its_limits():
    e, sender = engine([{**BUFF, "queue": 1}], chat_queue_ttl=5)
    e.on_chat(3, whisper(sender="Tinkerdawn", text="buff"), now=0)
    e.on_chat(3, whisper(sender="Tinkerdawn", text="buff now"), now=0.5)  # already being served
    e.on_chat(3, whisper(sender="Gnope", text="buff"), now=0.6)
    e.on_chat(3, whisper(sender="Gnope", text="buff again"), now=0.7)  # already queued
    e.on_chat(3, whisper(sender="Randompug", text="buff"), now=0.8)  # queue: 1 -> full
    assert [q.sender for q in e.rules[0].pending] == ["Gnope"]
    run(e, 0, 1.4)
    e.on_chat(3, whisper(sender="Tinkerdawn", text="buff"), now=1.6)  # just served, cooling down: spam
    assert [q.sender for q in e.rules[0].pending] == ["Gnope"]


def test_queued_requests_expire_and_queueing_can_be_off():
    e, sender = engine([BUFF], chat_queue_ttl=2)
    e.on_chat(3, whisper(sender="Tinkerdawn", text="buff"), now=0)
    e.on_chat(3, whisper(sender="Gnope", text="buff"), now=0.1)
    e.tick(1.5)  # Tinkerdawn's second cast; the run ends
    e.tick(5)  # off cooldown, but Gnope waited 4.9 s > 2 s
    assert typed(sender) == ["/cast [@Tinkerdawn] Kings", "/cast [@Tinkerdawn] Might"]
    e2, sender2 = engine([{**BUFF, "queue": 0}])
    e2.on_chat(3, whisper(sender="Tinkerdawn", text="buff"), now=0)
    e2.on_chat(3, whisper(sender="Gnope", text="buff"), now=0.1)
    assert not e2.rules[0].pending


def test_a_heal_goes_between_buff_casts_instead_of_colliding():
    heal = {"name": "heal", "chat": {"contains": ["heal"], "heard_by": "Kunth"},
            "do": {"type": "/cast [@{sender}] Healing Wave", "to": "Kunth"}}
    buffs3 = {**BUFF, "do": {"sequence": [{"type": "/cast Kings", "to": "Kunth"}, {"wait": "1.5s"},
                                          {"type": "/cast Might", "to": "Kunth"}, {"wait": "1.5s"},
                                          {"type": "/cast Wisdom", "to": "Kunth"}]}}
    e, sender = engine([buffs3, heal])
    times = {}
    real = sender.type_text
    now = [0.0]
    sender.type_text = lambda text, to: times.setdefault(text, now[0]) is not None and real(text, to)
    e.on_chat(3, whisper(sender="Gnope", text="buff"), now=0)
    now[0] = 0.5
    e.on_chat(3, whisper(sender="Tinkerdawn", text="heal me"), now=0.5)  # Kunth cast Kings 0.5 s ago
    t = 0.5
    while t < 8:
        t = round(t + 0.05, 2)
        now[0] = t
        e.tick(t)
    assert typed(sender) == ["/cast Kings", "/cast [@Tinkerdawn] Healing Wave", "/cast Might", "/cast Wisdom"]
    assert times["/cast [@Tinkerdawn] Healing Wave"] == pytest.approx(1.5, abs=0.06)  # after Kings' cooldown
    assert times["/cast Might"] == pytest.approx(3.0, abs=0.06)  # the buffs make room for it
    assert times["/cast Wisdom"] == pytest.approx(4.5, abs=0.06)


def test_rule_gap_off_lets_rules_overlap():
    heal = {"name": "heal", "chat": {"contains": ["heal"], "heard_by": "Kunth"},
            "do": {"type": "/cast Healing Wave", "to": "Kunth"}}
    e, sender = engine([BUFF, heal], rule_gap=0)
    e.on_chat(3, whisper(sender="Gnope", text="buff"), now=0)
    e.on_chat(3, whisper(sender="Tinkerdawn", text="heal"), now=0.2)
    assert typed(sender) == ["/cast [@Gnope] Kings", "/cast Healing Wave"]


# ---------------------------------------------------------------- dead characters, casts, priority
LOW_HP = {"name": "chain-when-hurt", "when": {"slot": "any", "health_pct_lt": 70},
          "do": {"type": "/cast Chain Heal", "to": "Kunth"}, "cooldown": "3s"}


def test_dead_characters_dont_match_and_get_nothing():
    e, sender = engine([LOW_HP])
    src = e.source
    for s in (1, 2, 3):
        src.st[s] = ClientState(name=src.st[s].name, health_pct=0.0, dead=True)
    for t in range(0, 30):
        e.tick(t)
    assert sender.sent == []  # the whole team is dead: no endless Chain Heal
    assert "dead" in e.status(e.rules[0], 30).next
    src.st[1] = ClientState(name="Tinkerdawn", health_pct=40.0)  # Tinkerdawn alive and hurt, Kunth still dead
    e.tick(40)
    assert sender.sent == []  # the step goes to Kunth, who's dead: skipped
    src.st[3] = ClientState(name="Kunth", health_pct=100.0)
    e.tick(50)
    assert typed(sender) == ["/cast Chain Heal"]


def test_a_condition_about_death_still_sees_the_dead():
    rule = {"name": "res", "when": {"slot": 1, "dead": True}, "do": {"type": "/cast Ancestral Spirit", "to": "Kunth"}}
    e, sender = engine([rule])
    e.source.st[1] = ClientState(name="Tinkerdawn", dead=True)
    e.tick(0)
    assert typed(sender) == ["/cast Ancestral Spirit"]


def test_steps_leave_out_dead_characters():
    rule = {"name": "buff-all", "every": "10s", "do": {"type": "/cast Kings", "to": "all"}}
    e, sender = engine([rule])
    e.source.st[2] = ClientState(name="Gnope", dead=True)
    e.tick(0)
    e.tick(10)
    assert sender.sent == [("type", "/cast Kings", [1, 3])]


def test_other_rules_wait_for_a_cast_in_progress():
    totems = {"name": "totems", "chat": {"contains": ["totem"], "heard_by": "Kunth"},
              "do": {"type": "/cast Call of the Elements", "to": "Kunth"}}
    e, sender = engine([totems], rule_gap=0)
    e.source.st[3] = ClientState(name="Kunth", casting=True)  # mid Chain Heal (you, or another rule)
    e.on_chat(3, whisper(text="totems pls"), now=0)
    assert typed(sender) == [] and "Kunth casting" in e.status(e.rules[0], 0).state
    e.tick(1)
    assert typed(sender) == []
    e.source.st[3] = ClientState(name="Kunth")  # cast finished
    e.tick(2.5)
    assert typed(sender) == ["/cast Call of the Elements"]


def test_a_stuck_casting_flag_only_holds_a_step_for_cast_wait_max():
    totems = {"name": "totems", "chat": {"contains": ["totem"], "heard_by": "Kunth"},
              "do": {"type": "/cast Call of the Elements", "to": "Kunth"}}
    e, sender = engine([totems], rule_gap=0, cast_wait_max=4)
    e.source.st[3] = ClientState(name="Kunth", channeling=True)
    e.on_chat(3, whisper(text="totem"), now=0)
    e.tick(3.9)
    assert typed(sender) == []
    e.tick(4.1)
    assert typed(sender) == ["/cast Call of the Elements"]


def test_a_rule_doesnt_wait_for_its_own_cast():
    rule = {"name": "chain-x2", "chat": {"contains": ["chains"], "heard_by": "Kunth"},
            "do": {"sequence": [{"type": "/cast Chain Heal", "to": "Kunth"}, {"wait": "2.6s"},
                                {"type": "/cast Chain Heal", "to": "Kunth"}]}}
    e, sender = engine([rule])
    e.on_chat(3, whisper(text="chains"), now=0)
    e.source.st[3] = ClientState(name="Kunth", casting=True)  # its own first cast
    e.tick(2.6)
    assert typed(sender) == ["/cast Chain Heal", "/cast Chain Heal"]


def test_priority_goes_first_when_rules_wait_for_the_same_character():
    def rule(name, word, prio=0):
        return {"name": name, "chat": {"contains": [word], "heard_by": "Kunth"},
                "do": {"type": f"/cast {name}", "to": "Kunth"}, "priority": prio}
    for prio, first in ((0, "heal"), (10, "totems")):  # equal: first come; higher priority: jumps ahead
        e, sender = engine([rule("chain", "chain"), rule("heal", "heal"), rule("totems", "totem", prio)])
        e.on_chat(3, whisper(text="chain"), now=0)  # Kunth casts; everything else waits 1.5 s
        e.on_chat(3, whisper(sender="Gnope", text="heal"), now=0.2)
        e.on_chat(3, whisper(sender="Gnope", text="totem"), now=0.4)
        t = 0.4
        while t < 6:
            t = round(t + 0.05, 2)
            e.tick(t)
        assert typed(sender)[1] == f"/cast {first}", prio
        assert len(typed(sender)) == 3


def test_queue_view_shows_running_waiting_and_queued():
    heal = {"name": "heal", "chat": {"contains": ["heal"], "heard_by": "Kunth"},
            "do": {"type": "/cast [@{sender}] Healing Wave", "to": "Kunth"}, "priority": 5}
    e, sender = engine([BUFF, heal])
    e.on_chat(3, whisper(sender="Tinkerdawn", text="buff"), now=0)  # buffs: Kings now, Might in 1.5 s
    e.on_chat(3, whisper(sender="Gnope", text="buff"), now=0.2)
    e.on_chat(3, whisper(sender="Gnope", text="heal"), now=0.3)  # waits out the global cooldown
    view = {(i.rule, i.kind): i for i in e.queue_view(0.5)}
    assert view[("buffs", "running")].who == "Tinkerdawn" and "in 1s" in view[("buffs", "running")].detail
    assert view[("heal", "waiting")].slots == [3] and "global cooldown" in view[("heal", "waiting")].detail
    assert view[("heal", "waiting")].priority == 5
    q = view[("buffs", "queued")]
    assert q.who == "Gnope" and q.order == 1 and q.slots == [3] and "expires in" in q.detail


# ---------------------------------------------------------------- far away characters, {name}
class FarSource(Source):
    """Tinkerdawn (1) and Kunth (3) together; Gnope (2) in another zone."""
    def too_far_apart(self, slot, other):
        return "in another zone" if 2 in (slot, other) and slot != other else None


def far_engine(rules, **opts):
    e, sender = engine(rules, **opts)
    e.source = FarSource()
    for s, st in e.source.st.items():  # everyone at full health to start with
        e.source.st[s] = ClientState(name=st.name, health_pct=100.0)
    return e, sender


def test_a_character_in_another_zone_doesnt_trigger_heals():
    e, sender = far_engine([LOW_HP])
    e.source.st[2] = ClientState(name="Gnope", health_pct=20.0)  # hurt, but far away
    e.tick(0)
    assert sender.sent == []
    st = e.status(e.rules[0], 0)
    assert "too far from Kunth: in another zone" in st.next
    e.source.st[1] = ClientState(name="Tinkerdawn", health_pct=60.0)  # hurt and with Kunth
    e.tick(10)
    assert typed(sender) == ["/cast Chain Heal"]


def test_far_check_can_be_turned_off():
    e, sender = far_engine([LOW_HP], skip_far=False)
    e.source.st[2] = ClientState(name="Gnope", health_pct=20.0)
    e.tick(0)
    assert typed(sender) == ["/cast Chain Heal"]


def test_name_is_the_lowest_health_character_that_matched():
    rule = {"name": "heal-low", "when": {"slot": "any", "health_pct_lt": 50},
            "do": {"type": "/cast [@{name}] Healing Wave", "to": "Kunth"}}
    e, sender = far_engine([rule])
    e.source.st[1] = ClientState(name="Tinkerdawn", health_pct=40.0)
    e.source.st[3] = ClientState(name="Kunth", health_pct=30.0)
    e.source.st[2] = ClientState(name="Gnope", health_pct=5.0)  # lowest, but in another zone
    e.tick(0)
    assert typed(sender) == ["/cast [@Kunth] Healing Wave"]


def test_chat_rule_with_a_condition_gets_name_too():
    rule = {"name": "heal-tank-if-low", "chat": {"contains": ["heal"], "heard_by": "Kunth"},
            "when": {"slot": [1], "health_pct_lt": 90},
            "do": {"type": "/cast [@{name}] Healing Wave", "to": "Kunth"}}
    e, sender = far_engine([rule])
    e.source.st[1] = ClientState(name="Tinkerdawn", health_pct=70.0)
    e.on_chat(3, whisper(text="heal"), now=0)
    assert typed(sender) == ["/cast [@Tinkerdawn] Healing Wave"]


def test_poller_warns_when_reads_fall_behind(caplog):
    r, t, cap, poller, events = _poller()
    poller.poll_once(cap)
    t[0] = 0.1
    poller.poll_once(cap)
    assert "between reads" not in caplog.text
    t[0] = 2.0  # Windows held WoWBox back: a held chat line could have come and gone
    poller.poll_once(cap)
    assert "1.9s between reads" in caplog.text
    caplog.clear()
    t[0] = 4.0
    poller.poll_once(cap)
    assert "between reads" not in caplog.text  # once a minute, not every read
