import pytest

from wowbox.config import AppConfig
from wowbox.keys import parse_chord
from wowbox.platform.fake import FakeInput, FakeWindows
from wowbox.router import ChatGuard, Router, RouteTable
from wowbox.team import Team

HWNDS = {1: 0x100, 2: 0x200, 3: 0x300, 4: 0x400}


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_cfg(**overrides) -> AppConfig:
    data = {
        "team": {"leader": 1, "slots": {s: {"label": f"C{s}"} for s in HWNDS}},
        "routes": [
            {"keys": ["1-9"], "to": "all"},
            {"keys": ["F1"], "to": "followers"},
            {"keys": ["Ctrl+1"], "to": [3]},
            {"keys": ["Shift+2"], "to": "all"},
            {"keys": ["F2"], "to": "leader"},
        ],
    }
    data.update(overrides)
    return AppConfig.model_validate(data)


class Rig:
    """Team of 4 attached fake clients + router, posting synchronously (sender thread not started)."""

    def __init__(self, cfg: AppConfig | None = None, clock: Clock | None = None) -> None:
        self.cfg = cfg or make_cfg()
        self.clock = clock or Clock()
        self.windows = FakeWindows()
        self.input = FakeInput()
        self.team = Team(self.cfg)
        for slot, h in HWNDS.items():
            self.windows.add(h, pid=slot * 10)
            c = self.team.clients[slot]
            c.hwnd, c.pid, c.alive = h, slot * 10, True
        self.windows.fg = HWNDS[1]
        b = self.cfg.broadcast
        self.chatguard = ChatGuard(b.chatguard, {parse_chord(k) for k in b.chatguard_open_keys},
                                   {parse_chord(k) for k in b.chatguard_close_keys}, clock=self.clock)
        self.hotkeys: list[str] = []
        self.router = Router(self.team, RouteTable(self.cfg.routes), self.input, self.windows, self.chatguard,
                             b, self.cfg.hotkeys, on_hotkey=self.hotkeys.append)
        self.input.start_hook(self.router.on_key)

    def drain(self) -> None:
        """Run queued sender ops inline."""
        while not self.router._q.empty():
            op = self.router._q.get()
            self.router._execute(op)

    def press(self, vk: int, down: bool = True) -> bool:
        swallowed = self.input.press(vk, down)
        self.drain()
        return swallowed

    def tap(self, vk: int) -> bool:
        s = self.press(vk, True)
        self.press(vk, False)
        return s

    def posted_to(self) -> dict[int, list[tuple[int, bool]]]:
        out: dict[int, list[tuple[int, bool]]] = {}
        inv = {h: s for s, h in HWNDS.items()}
        for hwnd, vk, down, _sys in self.input.posted:
            out.setdefault(inv[hwnd], []).append((vk, down))
        return out


@pytest.fixture
def rig():
    return Rig()
