"""Typing a `/bb all|others|to` team command into the clients it's for.

The client you typed `/bb ...` in needs care: its chat box is still closing and your Enter key may still be
down when WoWBox sees the command, so typing `Enter, command, Enter` there at once gets mangled. The other
clients get the command straight away; the one you typed it in waits until its bridge says the chat box is
closed, plus ORIGIN_DELAY.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Protocol

from .bridge.decoder import ClientState
from .config import Target

log = logging.getLogger(__name__)

ORIGIN_DELAY = 0.35  # after the chat box has closed in the client you typed in
ORIGIN_TIMEOUT = 3.0  # give up waiting for that chat box to close


class TextSender(Protocol):
    def type_text(self, text: str, target: Target) -> list[int]: ...


class TeamCommands:
    def __init__(self, sender: TextSender, read: Callable[[int], ClientState | None],
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 spawn: Callable[[Callable[[], None]], None] | None = None) -> None:
        self.sender = sender
        self.read = read
        self.clock = clock
        self.sleep = sleep
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, name="bb-origin", daemon=True).start())

    def send(self, text: str, slots: list[int], origin: int) -> None:
        others = [s for s in slots if s != origin]
        if others:
            self.sender.type_text(text, others)
        if origin in slots:
            self.spawn(lambda: self._send_to_origin(text, origin))

    def _send_to_origin(self, text: str, origin: int) -> None:
        start = self.clock()
        self.sleep(ORIGIN_DELAY)
        while self._chat_open(origin):
            if self.clock() - start > ORIGIN_TIMEOUT:
                log.warning("/bb: chat box in slot %d didn't close; %r not typed there", origin, text)
                return
            self.sleep(0.05)
        if self.clock() - start > ORIGIN_DELAY * 1.5:  # it was open a while: give it the gap again after closing
            self.sleep(ORIGIN_DELAY)
        self.sender.type_text(text, [origin])

    def _chat_open(self, slot: int) -> bool:
        st = self.read(slot)
        return st is not None and not st.stale and (st.typing or st.chat_open)
