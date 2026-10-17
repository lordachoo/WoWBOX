"""WoWBox's state in BoxBridge's in-game party panel.

The panel (addon/BoxBridge/BoxBridgePanel.lua) shows the party itself: who follows whom, who casts what on
whom, health, mana. Only WoWBox knows its own state (broadcast, automation, follow mode...), and the only way
into the game is a key press, so WoWBox spells it out on three spare keys BoxBridge binds: a start key, then
one key per flag for 0 or 1. Only clients that showed their panel (`/bb panel`) get them, and only when the
state changes. The addon answers through the bridge with what it got (`/bb` command "panel <n>"), so a press
lost while you were typing there is sent again.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from .keys import parse_chord

log = logging.getLogger(__name__)

# start, 0, 1: the same keys as BoxBridgePanel.lua binds (ALT-CTRL-SHIFT-F7/F8/F9)
KEYS = tuple(parse_chord(k) for k in ("Ctrl+Alt+Shift+F7", "Ctrl+Alt+Shift+F8", "Ctrl+Alt+Shift+F9"))
FLAGS = ("broadcast", "automation", "dry_run", "paused", "follow", "mirror")  # bit 0 first
RETRY_AFTER = 5.0  # no answer this long after sending: send again
MAX_TRIES = 3  # per state


def encode(flags: dict[str, bool]) -> int:
    return sum(1 << i for i, f in enumerate(FLAGS) if flags.get(f))


def presses(bits: int):
    """The keys that spell `bits` out: start, then each flag from bit 0."""
    return [KEYS[0]] + [KEYS[2] if bits >> i & 1 else KEYS[1] for i in range(len(FLAGS))]


class _Panel:
    def __init__(self) -> None:
        self.acked: int | None = None  # what the addon said it shows
        self.sent: int | None = None
        self.sent_at = 0.0
        self.tries = 0


class PanelLink:
    """Keeps the panels in sync. `send(chord, slot)` presses a key in one client; `state()` is the flags;
    `slot_of(hwnd)` finds a window's slot (panels are kept by window: slots can swap)."""

    def __init__(self, send: Callable[[object, int], None], state: Callable[[], dict[str, bool]],
                 slot_of: Callable[[int], int | None], clock: Callable[[], float] = time.monotonic) -> None:
        self.send, self.state, self.slot_of, self.clock = send, state, slot_of, clock
        self.panels: dict[int, _Panel] = {}  # hwnd -> its panel

    def on_command(self, hwnd: int | None, arg: str) -> None:
        """`/bb` "panel on|off|<n>" from the addon in window `hwnd`."""
        if not hwnd:
            return
        arg = arg.strip().lower()
        if arg == "off":
            self.panels.pop(hwnd, None)
            return
        p = self.panels.setdefault(hwnd, _Panel())
        if arg.isdigit():
            p.acked = int(arg)
        else:  # "on": shown just now (login, /reload, /bb panel): it knows nothing yet
            p.acked = p.sent = None
            p.tries = 0
        self.tick()

    def tick(self) -> None:
        bits = encode(self.state())
        now = self.clock()
        for hwnd, p in list(self.panels.items()):
            slot = self.slot_of(hwnd)
            if slot is None:  # the window closed
                del self.panels[hwnd]
                continue
            if p.acked == bits:
                continue
            if p.sent != bits:
                p.tries = 0
            elif p.tries >= MAX_TRIES or now - p.sent_at < RETRY_AFTER:
                continue
            for chord in presses(bits):
                self.send(chord, slot)
            p.sent, p.sent_at, p.tries = bits, now, p.tries + 1
            log.debug("panel in slot %d: state %d (try %d)", slot, bits, p.tries)
