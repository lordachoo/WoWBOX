"""Keeps the thumbnail strip and tab bar above WoW, but not above everything else.

They aren't always-on-top windows: with WoW in front they sit just below the active client (they don't overlap
it, so that's the same as being on top), and when you switch to another program they go behind it like any
other window. With `follow()` false (layout.thumbs_follow_wow off) they're left alone: clicking WoW doesn't bring
them back above a program covering them."""
from __future__ import annotations

import ctypes
import sys
from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

CHECK_MS = 250
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x1, 0x2, 0x10, 0x200


class StayWithWoW:
    def __init__(self, widget: QWidget, is_wow: Callable[[int], bool],
                 follow: Callable[[], bool] = lambda: True) -> None:
        self.widget = widget
        self.is_wow = is_wow
        self.follow = follow
        self._last: int | None = None
        self._timer = QTimer(widget)
        self._timer.setInterval(CHECK_MS)
        self._timer.timeout.connect(self.check)

    def start(self) -> None:
        self._last = None
        self._timer.start()
        self.check()

    def stop(self) -> None:
        self._timer.stop()

    def check(self) -> None:
        if sys.platform != "win32" or not self.widget.isVisible():
            return
        if not self.follow():
            self._last = None  # turned back on: catch up at once
            return
        user32 = ctypes.windll.user32
        fg = user32.GetForegroundWindow()
        if fg == self._last:
            return
        self._last = fg
        if fg and self.is_wow(fg):  # WoW came to the front: go right behind it, above every other program
            user32.SetWindowPos(ctypes.c_void_p(int(self.widget.winId())), ctypes.c_void_p(fg), 0, 0, 0, 0,
                                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
