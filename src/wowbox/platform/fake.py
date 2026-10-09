"""In-memory fakes of the platform interfaces, for tests (any OS)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .base import CaptureAPI, CaptureMethod, InputAPI, KeyCallback, KeyEvent, MonitorInfo, Rect, WindowAPI


@dataclass
class FakeWindow:
    hwnd: int
    pid: int
    title: str = "World of Warcraft"
    rect: Rect = Rect(0, 0, 800, 600)
    minimized: bool = False
    responding: bool = True
    borderless: bool = False
    visible_points: bool = True
    z: int = 0


@dataclass
class FakeWindows(WindowAPI):
    windows: dict[int, FakeWindow] = field(default_factory=dict)
    fg: int | None = None
    mons: list[MonitorInfo] = field(default_factory=lambda: [
        MonitorInfo(Rect(0, 0, 1920, 1080), Rect(0, 0, 1920, 1040), True)])
    _z: int = 0

    def add(self, hwnd: int, pid: int, **kw) -> FakeWindow:
        self.windows[hwnd] = FakeWindow(hwnd, pid, **kw)
        return self.windows[hwnd]

    def list_wow_windows(self) -> list[int]:
        return list(self.windows)

    def windows_for_pid(self, pid: int) -> list[int]:
        return [h for h, w in self.windows.items() if w.pid == pid]

    def pid_of(self, hwnd: int) -> int:
        return self.windows[hwnd].pid

    def title(self, hwnd: int) -> str:
        return self.windows[hwnd].title

    def set_title(self, hwnd: int, title: str) -> None:
        self.windows[hwnd].title = title

    def is_alive(self, hwnd: int) -> bool:
        return hwnd in self.windows

    def is_responding(self, hwnd: int) -> bool:
        return self.windows[hwnd].responding

    def is_minimized(self, hwnd: int) -> bool:
        return self.windows[hwnd].minimized

    def client_rect(self, hwnd: int) -> Rect:
        return self.windows[hwnd].rect

    def make_borderless(self, hwnd: int) -> None:
        self.windows[hwnd].borderless = True

    def restore_frame(self, hwnd: int) -> None:
        self.windows[hwnd].borderless = False

    def move(self, hwnd: int, rect: Rect, *, to_top: bool = False) -> None:
        self.windows[hwnd].rect = rect
        if to_top:
            self._z += 1
            self.windows[hwnd].z = self._z

    def restore(self, hwnd: int) -> None:
        self.windows[hwnd].minimized = False

    def foreground(self) -> int | None:
        return self.fg

    def set_foreground(self, hwnd: int) -> bool:
        self.fg = hwnd
        self._z += 1
        self.windows[hwnd].z = self._z
        return True

    def monitors(self) -> list[MonitorInfo]:
        return self.mons

    def is_point_visible(self, hwnd: int, x: int, y: int) -> bool:
        return self.windows[hwnd].visible_points

    def close(self, hwnd: int) -> None:
        self.windows.pop(hwnd, None)


@dataclass
class FakeInput(InputAPI):
    posted: list[tuple[int, int, bool, bool]] = field(default_factory=list)  # (hwnd, vk, down, sys_key)
    injected: list[list[tuple[int, bool]]] = field(default_factory=list)
    chars: list[tuple[int, str]] = field(default_factory=list)  # (hwnd, char)
    callback: KeyCallback | None = None

    def post_key(self, hwnd: int, vk: int, down: bool, *, sys_key: bool = False, alt_context: bool = False,
                 repeat: bool = False) -> bool:
        self.posted.append((hwnd, vk, down, sys_key))
        return True

    def post_char(self, hwnd: int, ch: str) -> bool:
        self.chars.append((hwnd, ch))
        return True

    def send_input(self, events: list[tuple[int, bool]]) -> int:
        self.injected.append(list(events))
        return len(events)

    def start_hook(self, callback: KeyCallback) -> None:
        self.callback = callback

    def stop_hook(self) -> None:
        self.callback = None

    def press(self, vk: int, down: bool = True, injected: bool = False) -> bool:
        """Simulate a physical key event; returns whether it was swallowed."""
        assert self.callback is not None
        return self.callback(KeyEvent(vk=vk, scan=0, down=down, injected=injected))


@dataclass
class FakeCapture(CaptureAPI):
    images: dict[int, np.ndarray] = field(default_factory=dict)  # hwnd -> full client image
    calls: list[tuple[int, CaptureMethod]] = field(default_factory=list)

    def capture(self, hwnd: int, region: Rect, method: CaptureMethod) -> np.ndarray:
        self.calls.append((hwnd, method))
        img = self.images[hwnd]
        return img[region.y:region.y + region.h, region.x:region.x + region.w].copy()

    def close(self) -> None:
        pass
