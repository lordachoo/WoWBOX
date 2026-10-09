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
