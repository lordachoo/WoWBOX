"""WoWBox's state on BoxBridge's in-game panel: three spare keys, answered through the bridge."""
from wowbox.panel import FLAGS, KEYS, MAX_TRIES, RETRY_AFTER, PanelLink, encode, presses

N = len(FLAGS) + 1  # presses per state: start + one per flag

from conftest import Clock


def test_encode_and_presses():
    bits = encode({"broadcast": True, "automation": True, "follow": True})
    assert bits == 0b010011
    start, zero, one = KEYS
    assert presses(bits) == [start, one, one, zero, zero, one, zero] + [zero] * 8
    assert encode({"rule1": True}) == 1 << 6


class Rig:
    def __init__(self):
        self.flags = {"broadcast": True}
        self.sent = []
        self.windows = {0x100: 1, 0x200: 2}
        self.clock = Clock()
        self.link = PanelLink(lambda chord, slot: self.sent.append((slot, chord)), lambda: self.flags,
                              self.windows.get, self.clock)

    def take(self):
        out, self.sent = self.sent, []
        return out


def test_only_panels_get_the_state_and_only_when_it_changes():
    r = Rig()
    r.link.tick()
    assert r.take() == []  # nobody showed a panel
    r.link.on_command(0x100, " on")
    assert [s for s, _ in r.take()] == [1] * N
    r.link.on_command(0x100, " 1")  # the addon got it
    r.clock.t += 60
    r.link.tick()
    assert r.take() == []
    r.flags = {"broadcast": True, "follow": True}
    r.link.tick()
    assert r.take() == [(1, k) for k in presses(0b010001)]


def test_lost_presses_are_sent_again_a_few_times():
    r = Rig()
    r.link.on_command(0x100, "on")
    r.take()
    r.link.tick()
    assert r.take() == []  # give it time to answer
    for _ in range(MAX_TRIES - 1):
        r.clock.t += RETRY_AFTER
        r.link.tick()
        assert len(r.take()) == N
    r.clock.t += RETRY_AFTER
    r.link.tick()
    assert r.take() == []  # gave up on this state...
    r.flags = {}
    r.link.tick()
    assert len(r.take()) == N  # ...but not on the next one


def test_wrong_answer_resends_and_on_starts_over():
    r = Rig()
    r.link.on_command(0x100, "on")
    r.take()
    r.clock.t += RETRY_AFTER
    r.link.on_command(0x100, "5")  # garbled
    assert len(r.take()) == N
    r.link.on_command(0x100, "1")
    r.link.on_command(0x100, "on")  # /reload: it knows nothing again
    assert len(r.take()) == N


def test_panel_off_and_closed_windows():
    r = Rig()
    r.link.on_command(0x100, "on")
    r.link.on_command(0x200, "on")
    r.take()
    r.link.on_command(0x100, "off")
    del r.windows[0x200]
    r.flags = {}
    r.link.tick()
    assert r.take() == [] and r.link.panels == {}
    r.link.on_command(None, "on")  # a window WoWBox doesn't know
    assert r.link.panels == {}


def test_controller_handles_panel_commands_quietly(tmp_path):
    c = _controller(tmp_path)
    sent = []
    c.router.send_chord = lambda chord, target, check_typing=True: sent.append((tuple(target), chord))
    before = len(c.activity.items())
    assert c.team_command(1, "@wowbox", "panel on") == []
    c._panel_tick()
    assert [t for t, _ in sent] == [(1,)] * N
    assert len(c.activity.items()) == before  # not in the Activity tab: it answers every change
    assert c.panel_flags()["broadcast"] == c.router.enabled


def _controller(tmp_path):
    from pathlib import Path

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from wowbox.app import Controller
    from wowbox.config import load_config
    from wowbox.platform.fake import FakeCapture, FakeInput, FakeWindows
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "team.yaml").write_text((root / "config" / "team.example.yaml").read_text(encoding="utf-8"),
                                        encoding="utf-8")
    cfg = load_config(tmp_path / "team.yaml")
    cfg.bridge.addon_dir = str(tmp_path)
    windows = FakeWindows()
    c = Controller(tmp_path / "team.yaml", cfg, windows, FakeInput(), lambda: FakeCapture())
    for slot in (1, 2):
        windows.add(0x100 * slot, pid=slot)
        cl = c.team.clients[slot]
        cl.hwnd, cl.pid, cl.alive = 0x100 * slot, slot, True
    return c
