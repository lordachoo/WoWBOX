"""Platform layer. Everything OS-specific lives below this package."""
from __future__ import annotations

import sys

from .base import CaptureAPI, InputAPI, KeyEvent, MonitorInfo, Rect, WindowAPI

__all__ = ["CaptureAPI", "InputAPI", "KeyEvent", "MonitorInfo", "Rect", "WindowAPI", "create_platform",
           "create_capture"]


def create_platform() -> tuple[WindowAPI, InputAPI]:
    if sys.platform != "win32":
        raise RuntimeError("WoWBox needs Windows")
    from .win32_input import Win32Input
    from .win32_windows import Win32Windows
    return Win32Windows(), Win32Input()


def create_capture() -> CaptureAPI:
    """A new capture object; each thread that captures needs its own."""
    from .win32_capture import Win32Capture
    return Win32Capture()
