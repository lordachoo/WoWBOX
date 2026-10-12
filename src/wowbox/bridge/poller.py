"""Capture loop: read each client's BoxBridge strip, decode, track staleness."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Callable

from ..config import BridgeConfig
from ..platform.base import CaptureAPI, CaptureMethod, Rect, WindowAPI
from .decoder import NUM_BLOCKS, ChatEvent, ClientState, DecodeError, decode_frame

if TYPE_CHECKING:
    from ..team import Team

log = logging.getLogger(__name__)

SLOW_POLL = 0.4  # seconds between reads: a chat line is held ~0.5 s, so slower than this can miss it
BOOST_HZ = 30.0  # while stacking: fresher positions mean shorter waits between moves


class BridgePoller:
    def __init__(self, cfg: BridgeConfig, team: "Team", windows: WindowAPI,
                 capture_factory: Callable[[], CaptureAPI],
                 on_state: Callable[[int, ClientState], None] = lambda slot, st: None,
                 on_chat: Callable[[int, ChatEvent], None] = lambda slot, ev: None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.team = team
        self.windows = windows
        self._capture_factory = capture_factory
        self.on_state = on_state
        self.on_chat = on_chat
        self._chat_seq: dict[int, int] = {}  # hwnd -> sequence of the chat frame on screen right now
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_frame: dict[int, tuple[int, float]] = {}  # hwnd -> (counter, time it last changed)
        self.last_error: dict[int, str] = {}
        self.last_method: dict[int, str] = {}
        self._boost_until = 0.0
        self._last_poll: float | None = None
        self._slow_warned = -1e9

    @property
    def region(self) -> Rect:
        b = self.cfg.block_size
        return Rect(0, 0, b * NUM_BLOCKS * 2 + 4, max(2, b * 2))  # room for up to 2x scale mismatch

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="bridge-poller", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)
            self._thread = None

    def boost(self, seconds: float) -> None:
        """Poll at BOOST_HZ for the next `seconds` (0 = back to normal now)."""
        self._boost_until = self._clock() + seconds

    def _run(self) -> None:
        cap = self._capture_factory()
        try:
            while not self._stop.is_set():
                t0 = self._clock()
                period = 1.0 / (BOOST_HZ if t0 < self._boost_until else max(0.5, self.cfg.hz))
                self.poll_once(cap)
                self._stop.wait(max(0.0, period - (self._clock() - t0)))
        finally:
            cap.close()

    def choose_method(self, hwnd: int, client: Rect) -> CaptureMethod:
        if self.cfg.method != "auto":
            return self.cfg.method
        r = self.region
        x, y = client.x + min(r.w, client.w) // 2, client.y + min(r.h, client.h) // 2
        return "mss" if self.windows.is_point_visible(hwnd, x, y) else "printwindow"

    def poll_once(self, cap: CaptureAPI) -> None:
        now = self._clock()
        if self._last_poll is not None and now - self._last_poll > SLOW_POLL and now - self._slow_warned > 60:
            self._slow_warned = now
            log.warning("bridge: %.1fs between reads (should be %.1fs): chat lines and /bb commands can be "
                        "missed. Is Windows slowing WoWBox down (efficiency mode, or the PC is busy)?",
                        now - self._last_poll, 1.0 / max(0.5, self.cfg.hz))
        self._last_poll = now
        for c in self.team.attached():
            slot, hwnd = c.slot, c.hwnd
            state: ClientState | None = None
            try:
                if self.windows.is_minimized(hwnd):
                    raise DecodeError("minimized")
                client = self.windows.client_rect(hwnd)
                r = self.region
                region = Rect(0, 0, min(r.w, client.w), min(r.h, client.h))
                method = self.choose_method(hwnd, client)
                self.last_method[slot] = method
                frame = decode_frame(cap.capture(hwnd, region, method))
                self.last_error.pop(slot, None)
                if isinstance(frame, ChatEvent):
                    self._chat(c, slot, hwnd, frame, now)
                    continue
                self._chat_seq.pop(hwnd, None)  # a state frame separates chat events
                state = frame
            except Exception as e:  # capture/decoding failures just make the client stale
                msg = f"{type(e).__name__}: {e}"
                if self.last_error.get(slot) != msg:
                    log.debug("bridge slot %d: %s", slot, msg)
                self.last_error[slot] = msg

            if state is not None:
                prev = self._last_frame.get(hwnd)
                if prev is None or prev[0] != state.frame:
                    self._last_frame[hwnd] = (state.frame, now)
                state.stale = now - self._last_frame[hwnd][1] > self.cfg.stale_after
                state.updated_at = now
            else:
                # no readable frame: once the last good one is old enough, report it stale (once)
                last = c.state
                if last is None or last.stale or now - last.updated_at <= self.cfg.stale_after:
                    continue
                last.stale = True
                state = last
            if c.hwnd != hwnd:  # slot was swapped while we were capturing; drop this sample
                continue
            c.state = state
            self.on_state(slot, state)

    def _chat(self, c, slot: int, hwnd: int, ev: ChatEvent, now: float) -> None:
        """The addon holds each chat frame for a while: report it once, and keep the client from going stale."""
        prev = self._last_frame.get(hwnd)
        if prev is None or prev[0] != ev.frame:
            self._last_frame[hwnd] = (ev.frame, now)
        if c.hwnd != hwnd:
            return
        if c.state is not None:
            # no state frames while a chat line is held: carry its typing flags and keep the client fresh,
            # or a rule would see the chat box you just typed `/bb test` in as still open
            c.state = replace(c.state, typing=ev.typing, chat_open=ev.chat_open, stale=False, updated_at=now)
            self.on_state(slot, c.state)
        if self._chat_seq.get(hwnd) == ev.seq:
            return
        self._chat_seq[hwnd] = ev.seq
        self.on_chat(slot, ev)
