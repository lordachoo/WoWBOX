"""Abstract platform interfaces. All Win32 access goes through these so the logic is testable anywhere."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Literal

import numpy as np


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


@dataclass(frozen=True)
class MonitorInfo:
    rect: Rect
    work: Rect
    primary: bool
    handle: int = 0


@dataclass(frozen=True)
class KeyEvent:
    vk: int
    scan: int
    down: bool
    injected: bool = False
    extended: bool = False
    time_ns: int = 0


# Called on the hook thread. Return True to swallow the key. Must be fast.
KeyCallback = Callable[[KeyEvent], bool]
CaptureMethod = Literal["mss", "dxcam", "printwindow"]


class WindowAPI(ABC):
    @abstractmethod
    def list_wow_windows(self) -> list[int]: ...

    @abstractmethod
    def windows_for_pid(self, pid: int) -> list[int]: ...

    @abstractmethod
    def pid_of(self, hwnd: int) -> int: ...

    @abstractmethod
    def title(self, hwnd: int) -> str: ...

    @abstractmethod
    def set_title(self, hwnd: int, title: str) -> None: ...

    @abstractmethod
    def is_alive(self, hwnd: int) -> bool: ...

    @abstractmethod
    def is_responding(self, hwnd: int) -> bool: ...

    @abstractmethod
    def is_minimized(self, hwnd: int) -> bool: ...

    @abstractmethod
    def client_rect(self, hwnd: int) -> Rect:
        """Client area in screen coordinates (physical pixels)."""

    @abstractmethod
    def make_borderless(self, hwnd: int) -> None: ...

    @abstractmethod
    def restore_frame(self, hwnd: int) -> None:
        """Undo make_borderless (no-op if never stripped)."""

    @abstractmethod
    def move(self, hwnd: int, rect: Rect, *, to_top: bool = False) -> None:
        """Place the window at rect (window == client once borderless). Never activates."""

    @abstractmethod
    def restore(self, hwnd: int) -> None:
        """Un-minimize without activating."""

    @abstractmethod
    def foreground(self) -> int | None: ...

    @abstractmethod
    def set_foreground(self, hwnd: int) -> bool: ...

    @abstractmethod
    def monitors(self) -> list[MonitorInfo]:
        """Primary first, then left-to-right."""

    @abstractmethod
    def is_point_visible(self, hwnd: int, x: int, y: int) -> bool:
        """True if screen point (x, y) shows this window (not covered by another)."""

    @abstractmethod
    def close(self, hwnd: int) -> None: ...


class InputAPI(ABC):
    @abstractmethod
    def post_key(self, hwnd: int, vk: int, down: bool, *, sys_key: bool = False, alt_context: bool = False,
                 repeat: bool = False) -> bool: ...

    @abstractmethod
    def post_char(self, hwnd: int, ch: str) -> bool:
        """Post one typed character (WM_CHAR): text for an open edit box, never triggers key bindings."""

    @abstractmethod
    def send_input(self, events: list[tuple[int, bool]]) -> int:
        """Inject (vk, down) events into the system input stream (goes to the foreground window)."""

    @abstractmethod
    def start_hook(self, callback: KeyCallback) -> None: ...

    @abstractmethod
    def stop_hook(self) -> None: ...

    def health(self) -> str | None:
        """A short line about how the keyboard hook is doing, for the health log (None: nothing to say)."""
        return None


class CaptureAPI(ABC):
    @abstractmethod
    def capture(self, hwnd: int, region: Rect, method: CaptureMethod) -> np.ndarray:
        """RGB uint8 (h, w, 3) of `region`, given in CLIENT coordinates of hwnd. Raises on failure."""

    @abstractmethod
    def close(self) -> None: ...
