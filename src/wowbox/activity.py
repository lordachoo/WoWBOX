"""A running record of what rules and macros did, for the Activity tab.

Rules and macros report into it from their own threads; the UI reads a snapshot. Thread-safe.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

FEED_SIZE = 300


@dataclass
class Activity:
    at: float  # wall clock, for the time column
    mono: float  # monotonic, for "2s ago"
    source: str  # "rule", "macro" or "/bb"
    name: str  # the rule or macro
    text: str  # what happened
    slots: list[int] = field(default_factory=list)  # the characters it went to, if any
    kind: str = "info"  # "fired", "step", "queued", "skipped", "done", "info"


def kind_of(text: str) -> str:
    t = text.lower()
    if " fired" in t or t.startswith("started") or t.startswith("fired"):
        return "fired"
    if "skipped" in t or "expired" in t or "dropped" in t or "full" in t or "stopped" in t:
        return "skipped"
    if "queued" in t:
        return "queued"
    if t.startswith(("typed", "sent", "[dry run]", "started macro", "would")):
        return "step"
    if t.startswith("done"):
        return "done"
    return "info"


class ActivityFeed:
    def __init__(self, size: int = FEED_SIZE) -> None:
        self._items: deque[Activity] = deque(maxlen=size)
        self._last: dict[int, Activity] = {}  # slot -> the last step sent there
        self._lock = threading.Lock()
        self.version = 0  # bumps on every change, so the UI can skip redraws

    def add(self, source: str, name: str, text: str, slots: list[int] | None = None, kind: str | None = None) -> None:
        a = Activity(time.time(), time.monotonic(), source, name, text.strip(), list(slots or []),
                     kind or kind_of(text.strip()))
        with self._lock:
            self._items.append(a)
            if a.slots and (a.kind == "step" or a.source == "/bb" and a.kind == "fired"):
                for s in a.slots:
                    self._last[s] = a
            self.version += 1

    def items(self) -> list[Activity]:
        with self._lock:
            return list(self._items)

    def last_for(self, slot: int) -> Activity | None:
        with self._lock:
            return self._last.get(slot)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.version += 1
