import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wowbox.activity import ActivityFeed, kind_of  # noqa: E402
from wowbox.automation import AutomationEngine  # noqa: E402
from wowbox.automation.macros import MacroRunner  # noqa: E402
from wowbox.bridge.decoder import ChatEvent, ClientState  # noqa: E402
from wowbox.config import AppConfig, MacroConfig  # noqa: E402


@pytest.mark.parametrize("text,kind", [
    ("fired (whisper from Gnope: 'heal'): type x", "fired"), ("started", "fired"),
    ("typed '/cast Heal' -> slots [3]", "step"), ("[dry run] would type 'x' -> Kunth", "step"),
    ("still running, Gnope queued (#1)", "queued"), ("queue full (5), chat from X skipped", "skipped"),
    ("Gnope's request expired after 1m 00s in the queue", "skipped"), ("done", "done"), ("paused: x", "info"),
])
def test_kinds(text, kind):
    assert kind_of(text) == kind


def test_feed_keeps_the_last_step_per_character_and_a_bounded_history():
    f = ActivityFeed(size=3)
    f.add("rule", "heal", "fired: x")
    f.add("rule", "heal", "typed '/cast Heal' -> slots [3]", [3])
    f.add("macro", "pets", "sent 1 -> slots [2, 3]", [2, 3])
    f.add("macro", "pets", "done")
    assert [a.text for a in f.items()] == ["typed '/cast Heal' -> slots [3]", "sent 1 -> slots [2, 3]", "done"]
    assert f.last_for(3).name == "pets" and f.last_for(2).source == "macro" and f.last_for(1) is None


class Src:
    def __init__(self):
        self.st = {3: ClientState(name="Kunth")}

    def states(self):
        return self.st

    def missing_windows(self):
        return []

    def slots_for(self, target):
        return [3]


class Sender:
    held_mods = 0

    def type_text(self, text, to):
        return [3]

    def send_chord(self, chord, to, check_typing=True):
        return [3]


def test_rules_and_macros_report_into_the_feed():
    cfg = AppConfig.model_validate({
        "automation": [{"name": "heal", "chat": {"contains": ["heal"], "heard_by": "Kunth"},
                        "do": {"type": "/cast [@{sender}] Healing Wave", "to": "Kunth"}}],
        "automation_options": {"enabled_on_start": True, "dry_run": False, "pause_on_stale_bridge": False}})
    feed = ActivityFeed()
    e = AutomationEngine(cfg.automation, cfg.automation_options, Sender(), Src())
    e.on_activity = lambda name, text, slots: feed.add("rule", name, text, slots)
    e.on_chat(3, ChatEvent(channel="whisper", sender="Gnope", text="heal pls"), now=0)
    got = [(a.name, a.kind, a.slots) for a in feed.items()]
    assert got == [("heal", "fired", []), ("heal", "step", [3])]
    assert feed.items()[1].text == "typed '/cast [@Gnope] Healing Wave' -> slots [3]"

    m = MacroRunner([MacroConfig.model_validate({"name": "pets", "do": {"sequence": [
        {"type": "/petattack", "to": "all"}, {"send": "1", "to": "all"}]}})], Sender())
    m.on_activity = lambda name, text, slots: feed.add("macro", name, text, slots)
    m.start("pets").join(2)
    macro = [(a.name, a.kind, a.text) for a in feed.items() if a.source == "macro"]
    assert macro == [("pets", "fired", "started"), ("pets", "step", "typed '/petattack' -> slots [3]"),
                     ("pets", "step", "sent 1 -> slots [3]"), ("pets", "done", "done")]
    assert m.progress == {}  # finished macros leave the live queue


def test_activity_tab_renders(qapp):
    from wowbox.app import Controller
    from wowbox.config import load_config
    from wowbox.platform.fake import FakeCapture, FakeInput, FakeWindows
    from wowbox.ui.activity_tab import ActivityTab
    from pathlib import Path
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(Path(__file__).parent.parent / "config" / "team.example.yaml", tmp / "team.yaml")
    windows = FakeWindows()
    c = Controller(tmp / "team.yaml", load_config(tmp / "team.yaml"), windows, FakeInput(), lambda: FakeCapture())
    for slot in (1, 3):
        windows.add(0x100 * slot, pid=slot)
        cl = c.team.clients[slot]
        cl.hwnd, cl.pid, cl.alive = 0x100 * slot, slot, True
    c.team.clients[3].state = ClientState(name="Kunth", casting=True, health_pct=80.0)
    c.team.clients[1].state = ClientState(name="Tinkerdawn", dead=True)
    c.activity.add("rule", "heal", "typed '/cast Heal' -> slots [3]", [3])
    tab = ActivityTab(c)
    tab.refresh(force=True)
    states = {tab.chars.item(r, 0).text(): tab.chars.item(r, 1).text() for r in range(tab.chars.rowCount())}
    assert states["1 Tinkerdawn"] == "DEAD" and states["3 Kunth"].startswith("casting")
    assert tab.feed.rowCount() == 1 and tab.feed.item(0, 2).text() == "heal"
    tab.show_rules.setChecked(False)
    assert tab.feed.rowCount() == 0
    shutil.rmtree(tmp)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
