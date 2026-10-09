"""Pause broadcasting while you type in chat.

Two sources: a key heuristic (Enter or / opens, Enter or Esc closes) that reacts instantly, and the
bridge's `typing` flag, which can't drift. When the bridge is fresh it wins: it can open the guard
(e.g. you clicked into the chat box) and it corrects a heuristic that got out of sync.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from ..keys import Chord

BRIDGE_FRESH_FOR = 1.0  # seconds a bridge report stays authoritative
SETTLE = 0.35  # a bridge report must be this much newer than the last key toggle to override it


class ChatGuard:
    def __init__(self, enabled: bool, open_keys: set[Chord], close_keys: set[Chord],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.enabled = enabled
        self.open_keys = open_keys
        self.close_keys = close_keys
        self._clock = clock
        self._open = False
        self._toggled_at = -1e9
        self._bridge: dict[int, tuple[bool, float]] = {}
        self._lock = threading.Lock()
        self.on_change: Callable[[bool], None] | None = None

    def on_key(self, chord: Chord) -> None:
        """Called for every key-down in a WoW client, before routing."""
        if not self.enabled:
            return
        with self._lock:
            before = self._open
            if self._open and chord in self.close_keys:
                self._open = False
            elif not self._open and chord in self.open_keys:
                self._open = True
            if self._open != before:
                self._toggled_at = self._clock()
        if self._open != before and self.on_change:
            self.on_change(self._open)

    def set_bridge(self, slot: int, typing: bool) -> None:
        self._bridge[slot] = (typing, self._clock())

    def forget(self, slot: int) -> None:
        self._bridge.pop(slot, None)

    def paused(self, active: int | None) -> bool:
        if not self.enabled:
            return False
        now = self._clock()
        report = self._bridge.get(active) if active is not None else None
        if report and now - report[1] <= BRIDGE_FRESH_FOR:
            typing, at = report
            if typing:
                return True
            with self._lock:
                if self._open and at - self._toggled_at >= SETTLE:
                    self._open = False  # heuristic drifted; bridge says the chat box is closed
                    changed = True
                else:
                    changed = False
            if changed and self.on_change:
                self.on_change(False)
        return self._open

    def reset(self) -> None:
        with self._lock:
            self._open = False
