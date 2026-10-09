import time
from pathlib import Path

from conftest import HWNDS, Rig, make_cfg

from wowbox.automation.actions import SendStep, TypeStep, WaitStep, flatten
from wowbox.automation.macros import MacroRunner
from wowbox.config import AppConfig, load_config
from wowbox.keys import VK, Mod

ROOT = Path(__file__).resolve().parents[1]
MACRO = {"name": "lock", "key": "F8", "do": {"sequence": [
    {"type": "/assist Tinkerdawn", "to": "Gnope"},
    {"wait": "10ms"},
    {"keys": ["1-3", "0"], "delay": "10ms", "to": "class:WARLOCK"}]}}


def identify(r: Rig) -> None:
    for slot, (name, cls) in {1: ("Tinkerdawn", "PALADIN"), 2: ("Gnope", "WARLOCK"), 3: ("Kunth", "SHAMAN"),
                              4: ("Mage", "MAGE")}.items():
        r.team.clients[slot].character, r.team.clients[slot].class_token = name, cls


def test_example_macros_parse_and_flatten():
    cfg = load_config(ROOT / "config" / "team.example.yaml")
    steps = flatten(cfg.macros[0].do)
    assert isinstance(steps[0], TypeStep) and steps[0].text == "/assist Tankadin"
    sends = [s for s in steps if isinstance(s, SendStep)]
    assert [str(s.chord) for s in sends] == [str(i) for i in range(1, 10)] + ["0"]
    assert sum(isinstance(s, WaitStep) for s in steps) == 1 + 9


def test_named_targets():
    r = Rig()
    identify(r)
    assert r.team.resolve("Gnope", 1) == [2]
    assert r.team.resolve("char:kunth", 1) == [3]
    assert r.team.resolve("class:warlock", 1) == [2]
    assert r.team.resolve("role:healer", 1) == []  # make_cfg slots are all dps
    assert r.team.resolve("Nobody", 1) == []


def test_type_text_posts_enter_chars_enter():
    r = Rig()
    identify(r)
    assert r.router.type_text("/assist T", "Gnope") == [2]
    r.drain()
    posted = r.posted_to()
    assert posted == {2: [(VK["ENTER"], True), (VK["ENTER"], False), (VK["ENTER"], True), (VK["ENTER"], False)]}
    assert "".join(ch for h, ch in r.input.chars if h == HWNDS[2]) == "/assist T"


class Recorder:
    held_mods = Mod.NONE

    def __init__(self):
        self.calls = []

    def send_chord(self, chord, target, check_typing=True):
        self.calls.append(("send", str(chord), target, check_typing))
        return [2]

    def type_text(self, text, target):
        self.calls.append(("type", text, target))
        return [2]


def test_runner_runs_in_order():
    cfg = AppConfig.model_validate({"macros": [MACRO]})
    rec = Recorder()
    runner = MacroRunner(cfg.macros, rec)
    runner.start("lock").join(2)
    assert rec.calls[0] == ("type", "/assist Tinkerdawn", "Gnope")
    assert [c[1] for c in rec.calls[1:]] == ["1", "2", "3", "0"]
    assert all(c[3] is False for c in rec.calls[1:])  # user macros don't skip on the typing flag
    assert not runner.is_running("lock")


def test_toggle_stops_a_running_macro():
    cfg = AppConfig.model_validate({"macros": [{"name": "slow", "do": {"keys": ["1-9"], "delay": "1s", "to": "all"}}]})
    rec = Recorder()
    runner = MacroRunner(cfg.macros, rec)
    t = runner.start("slow")
    time.sleep(0.05)
    runner.toggle("slow")
    t.join(2)
    assert len(rec.calls) == 1 and not runner.is_running("slow")


def test_waits_for_hotkey_modifiers_release():
    cfg = AppConfig.model_validate({"macros": [{"name": "m", "do": {"send": "1", "to": "all"}}]})
    rec = Recorder()
    rec.held_mods = Mod.CTRL
    runner = MacroRunner(cfg.macros, rec)
    t = runner.start("m")
    time.sleep(0.1)
    assert rec.calls == []
    rec.held_mods = Mod.NONE
    t.join(2)
    assert rec.calls == [("send", "1", "all", False)]


def test_macro_hotkey_fires_but_not_while_typing():
    r = Rig(make_cfg(macros=[MACRO]))
    r.router.set_hotkeys(r.cfg.hotkeys, r.cfg.macros)
    f8 = VK["F8"]
    assert r.tap(f8) is True
    assert r.hotkeys == ["macro:lock"]
    r.tap(VK["ENTER"])  # chat open: F8 isn't a hotkey now
    assert r.tap(f8) is False
    assert r.hotkeys == ["macro:lock"]


def test_macro_key_clash_ignored():
    r = Rig(make_cfg(macros=[{**MACRO, "key": "ScrollLock"}]))
    r.router.set_hotkeys(r.cfg.hotkeys, r.cfg.macros)
    r.tap(VK["SCROLLLOCK"])
    assert r.hotkeys == ["toggle_broadcast"]
