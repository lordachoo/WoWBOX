"""Keyboard hook events -> target clients -> PostMessage.

The hook callback (`on_key`) only decides: which slots get this key and whether the physical key is
swallowed. The actual sending happens on a sender thread so the hook never blocks (Windows drops slow
LL hooks).
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from ..config import BroadcastConfig, HotkeyConfig, Target
from ..keys import MOD_VK, MODIFIER_VKS, Chord, Mod, parse_chord
from ..platform.base import InputAPI, KeyEvent, WindowAPI
from ..team import Team
from .chatguard import ChatGuard
from .rules import RouteTable

log = logging.getLogger(__name__)

MOD_ORDER = (Mod.CTRL, Mod.ALT, Mod.SHIFT)
PRESS_HOLD = 0.03
VK_RETURN = 0x0D
CHAT_OPEN_WAIT = 0.08  # after Enter, before the text: let the client open its chat box
DEFER_POLL = 0.02  # how often waiting lines check whether their client is free


def _describe_op(op: tuple) -> str:
    if op[0] == "text":
        return f"typing {op[2]!r}"
    return f"key {op[2]:#x}"


@dataclass
class _Pressed:
    slots: tuple[int, ...]
    mods: Mod
    swallowed: bool
    hotkey: bool = False


class Router:
    def __init__(self, team: Team, routes: RouteTable, input_api: InputAPI, windows: WindowAPI,
                 chatguard: ChatGuard, cfg: BroadcastConfig, hotkeys: HotkeyConfig,
                 on_hotkey: Callable[[str], None] = lambda name: None, macros: list | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.team = team
        self.routes = routes
        self.input = input_api
        self.windows = windows
        self.chatguard = chatguard
        self.cfg = cfg
        self.on_hotkey = on_hotkey
        self.on_state_change: Callable[[], None] = lambda: None
        self._enabled = cfg.enabled_on_start
        self._held_mod_vks: set[int] = set()
        self._pressed: dict[int, _Pressed] = {}
        self._hotkeys: dict[Chord, str] = {}
        self.mirror = False  # copy movement keys to `mirror_target` as well as their normal route
        self.mirror_vks: set[int] = set()
        self.mirror_target: Target = "followers"
        # mirror_allowed(leader_slot, follower_slot): False leaves that follower out (e.g. it's in another zone)
        self.mirror_allowed: Callable[[int | None, int], bool] = lambda leader, slot: True
        self.last_movement = -1e9  # monotonic time of the last movement key event
        self.hotkeys_paused = False  # e.g. while the macro editor records a hotkey
        self.set_hotkeys(hotkeys, macros or [])
        self._own_pid = os.getpid()
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._sender: threading.Thread | None = None
        self.sent_count = 0
        self._last_key_down = -1e9  # your last physical key press (not modifiers)
        self._typing_into: frozenset[int] = frozenset()  # slots a line is being typed into right now
        # Per slot: typed lines / automation presses waiting for you to stop typing there, in order.
        self._waiting: dict[int, deque] = {}  # slot -> deque of [queued_at, op]
        self._shift: dict[int, float] = {}  # slot -> delay applied to its queue once it was free again
        self._said: dict[int, str] = {}  # slot -> the reason last logged, so a wait is logged once
        self._wait_lock = threading.Lock()
        self._deferrer: threading.Thread | None = None
        self._stop = threading.Event()
        self.heartbeat: Callable[[], None] = lambda: None  # the defer loop calls it each time round (diagnostics)
        self.sending_since: float | None = None  # when the sender started on what it's doing now (None: idle)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._sender is None:
            self._sender = threading.Thread(target=self._send_loop, name="router-sender", daemon=True)
            self._sender.start()
        if self._deferrer is None:
            self._stop.clear()
            self._deferrer = threading.Thread(target=self._defer_loop, name="router-defer", daemon=True)
            self._deferrer.start()
        self.input.start_hook(self.on_key)

    def stop(self) -> None:
        self.input.stop_hook()
        self.release_all()
        self._stop.set()
        self._deferrer = None
        self._q.put(None)
        if self._sender:
            self._sender.join(2)
            self._sender = None

    def hotkey_owner(self, chord: Chord) -> str | None:
        """What a chord is already bound to: a hotkey name or macro:<name>."""
        return self._hotkeys.get(chord)

    def set_hotkeys(self, hk: HotkeyConfig, macros: list | None = None, rules: list | None = None) -> None:
        table = {}
        for name in ("toggle_broadcast", "release_all", "cycle_layout", "next_main", "automation_kill",
                     "follow", "stay", "stack", "mirror_movement"):
            if (spec := getattr(hk, name)) is not None:
                table[parse_chord(spec)] = name
        for slot, spec in hk.focus_slot.items():
            table[parse_chord(spec)] = f"focus_slot:{slot}"
        for m in macros or []:
            if m.key:
                chord = parse_chord(m.key)
                if chord in table:
                    log.warning("macro %r key %s is already used by %s; macro key ignored", m.name, m.key, table[chord])
                    continue
                table[chord] = f"macro:{m.name}"
        for r in rules or []:  # automation rules: the key turns the rule on or off
            if r.hotkey:
                chord = parse_chord(r.hotkey)
                if chord in table:
                    log.warning("rule %r hotkey %s is already used by %s; ignored", r.name, r.hotkey, table[chord])
                    continue
                table[chord] = f"rule:{r.name}"
        self._hotkeys = table

    # -- state ------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        if value == self._enabled:
            return
        self._enabled = value
        log.info("broadcast %s", "ON" if value else "OFF")
        if not value:
            self.release_all()
        self.on_state_change()

    @property
    def held_mods(self) -> Mod:
        m = Mod.NONE
        for vk in tuple(self._held_mod_vks):  # macros read this from their own thread while keys change it
            m |= MODIFIER_VKS[vk]
        return m

    def set_mirror(self, value: bool) -> None:
        if value == self.mirror:
            return
        self.mirror = value
        log.info("movement mirroring %s", "ON" if value else "OFF")
        self.on_state_change()

    def movement_held(self) -> bool:
        return any(vk in self.mirror_vks for vk in self._pressed)

    def active_slot(self) -> int | None:
        return self.team.slot_for_hwnd(self.windows.foreground())

    # -- hook thread ------------------------------------------------------
    def on_key(self, ev: KeyEvent) -> bool:
        """Runs on the hook thread for every physical key event. Returns True to swallow it."""
        if ev.injected:
            return False
        vk = ev.vk
        if ev.down and vk not in MODIFIER_VKS:
            if self._typing_into and self.team.slot_for_hwnd(self.windows.foreground()) in self._typing_into:
                return True  # a line is being typed into this window: keep your keys out of it
            self._last_key_down = self.clock()
        if vk in self.mirror_vks:
            self.last_movement = time.monotonic()
        if vk in MODIFIER_VKS:
            if ev.down:
                self._held_mod_vks.add(vk)
            else:
                self._held_mod_vks.discard(vk)
            return False

        if not ev.down:
            p = self._pressed.pop(vk, None)
            if p is None:
                return False
            if p.slots:
                self._q.put(("up", p.slots, vk, p.mods))
            return p.swallowed

        if vk in self._pressed:  # auto-repeat
            p = self._pressed[vk]
            if p.slots:
                self._q.put(("repeat", p.slots, vk, p.mods))
            return p.swallowed

        chord = Chord(vk, self.held_mods)
        fg = self.windows.foreground()
        active = self.team.slot_for_hwnd(fg)

        name = None if self.hotkeys_paused else self._hotkeys.get(chord)
        if name and name.startswith("macro:") and active is not None and self.chatguard.paused(active):
            name = None  # you're typing in chat: let the key through as text
        if name and (active is not None or self._is_own_window(fg)):
            self._run_hotkey(name)
            self._pressed[vk] = _Pressed((), Mod.NONE, True, hotkey=True)  # swallow its key-up too
            return True

        if active is None:
            if self.cfg.only_when_wow_focused:
                return False
            active = None

        if active is not None:
            self.chatguard.on_key(chord)

        if not self._enabled or (active is not None and self.chatguard.paused(active)):
            return False

        target = self.routes.lookup(chord)
        slots = self.team.resolve(target, active)
        if self.mirror and vk in self.mirror_vks:
            mirrored = [s for s in self.team.resolve(self.mirror_target, active) if self.mirror_allowed(active, s)]
            slots = sorted(set(slots) | set(mirrored))
        others = tuple(s for s in slots if s != active)
        swallow = active is not None and active not in slots
        mods = Mod.NONE if self.cfg.modifier_mode == "strip" else chord.mods
        if others:
            if self.cfg.modifier_mode == "focus_swap" and chord.mods:
                self._q.put(("swap_press", others, vk, chord.mods, active))
                others = ()
            else:
                self._q.put(("down", others, vk, mods))
        self._pressed[vk] = _Pressed(others, mods, swallow)
        return swallow

    def _is_own_window(self, hwnd: int | None) -> bool:
        try:
            return bool(hwnd) and self.windows.pid_of(hwnd) == self._own_pid
        except Exception:
            return False

    def _run_hotkey(self, name: str) -> None:
        if name == "toggle_broadcast":
            self.set_enabled(not self._enabled)
        elif name == "release_all":
            self.release_all()
        self.on_hotkey(name)

    # -- API for automation / UI -----------------------------------------
    def _targets(self, target: Target, check_typing: bool) -> list[int]:
        active = self.active_slot()
        slots = []
        for s in self.team.resolve(target, active):
            c = self.team.clients[s]
            if check_typing and c.state is not None and not c.state.stale and c.state.typing:
                log.info("skip slot %d: typing", s)
                continue
            if s == active and self.chatguard.paused(active):
                log.info("skip slot %d: chat guard", s)
                continue
            slots.append(s)
        return slots

    def type_text(self, text: str, target: Target) -> list[int]:
        """Type a chat line in each target: Enter, the text, Enter (e.g. "/assist Tinkerdawn"). A client where
        you're typing, or the window you're pressing keys in, gets it once you've stopped (see `busy`)."""
        slots = self.team.resolve(target, self.active_slot())
        if slots:
            self._send_or_wait(slots, lambda s: ("text", s, text))
        else:
            log.info("type %r: no client matches %r", text, target)
        return slots

    def send_chord(self, chord: Chord, target: Target, check_typing: bool = True) -> list[int]:
        """Send a full press of `chord` to `target`. With check_typing (automation), a client where you're
        typing waits like a typed line does; without it (your own hotkey macros) it goes at once."""
        slots = self.team.resolve(target, self.active_slot()) if check_typing else self._targets(target, False)
        if not slots:
            log.info("send %s: no client matches %r", chord, target)
            return slots
        if self.cfg.modifier_mode == "focus_swap" and chord.mods:
            op = lambda s: ("swap_press", s, chord.vk, chord.mods, None)  # noqa: E731
        else:
            mods = Mod.NONE if self.cfg.modifier_mode == "strip" else chord.mods
            op = lambda s: ("press", s, chord.vk, mods)  # noqa: E731
        if check_typing:
            self._send_or_wait(slots, op)
        else:
            self._q.put(op(tuple(slots)))
        return slots

    # -- waiting for you to stop typing ------------------------------------
    def busy(self, slot: int) -> str | None:
        """Why a line typed into `slot` now would get mixed up with what you're doing, or None.
        "typing": its chat box is open. "keys": it's the window you're in and you're pressing keys."""
        st = self.team.clients[slot].state if slot in self.team.clients else None
        if st is not None and not st.stale and (st.typing or st.chat_open):
            return "typing"
        if slot != self.active_slot():
            return None
        if self.chatguard.paused(slot):
            return "typing"
        if any(not p.hotkey for p in tuple(self._pressed.values())) or \
                self.clock() - self._last_key_down < self.cfg.type_quiet:
            return "keys"
        return None

    def _send_or_wait(self, slots: list[int], make_op: Callable[[tuple[int, ...]], tuple]) -> None:
        now = self.clock()
        go = []
        with self._wait_lock:
            for s in slots:
                if s in self._waiting or self.busy(s):  # keep the order behind anything already waiting
                    self._waiting.setdefault(s, deque()).append([now, make_op((s,))])
                else:
                    go.append(s)
        if go:
            self._q.put(make_op(tuple(go)))
        self._pump()

    def _pump(self) -> None:
        """Send what's waiting for clients that are free now. Keeps the gaps between a rule's steps."""
        now = self.clock()
        with self._wait_lock:
            for s in list(self._waiting):
                q = self._waiting[s]
                while q:
                    queued_at, op = q[0]
                    why = self.busy(s)
                    if why and s not in self._shift:
                        if now - queued_at < self.cfg.type_wait_max:
                            if self._said.get(s) != why:
                                self._said[s] = why
                                log.info("%s: waiting for you to stop %s there", self._name(s),
                                         "typing" if why == "typing" else "pressing keys")
                            break
                        if why == "typing":
                            q.popleft()
                            log.warning("%s: skipped %s (you were still typing there after %gs)", self._name(s),
                                        _describe_op(op), self.cfg.type_wait_max)
                            continue
                        # only keys after the wait: send it; your keys are held back while it's typed
                    shift = self._shift.setdefault(s, now - queued_at)
                    if now < queued_at + shift:
                        break  # the gap a rule had between this step and the one before
                    q.popleft()
                    self._q.put(op)
                if not q:
                    del self._waiting[s]
                    self._shift.pop(s, None)
                    self._said.pop(s, None)

    def waiting(self) -> dict[int, int]:
        """Slot -> how many lines/presses wait for you there (for the UI)."""
        with self._wait_lock:
            return {s: len(q) for s, q in self._waiting.items()}

    def _name(self, slot: int) -> str:
        c = self.team.clients.get(slot)
        return c.name if c else f"slot {slot}"

    def _defer_loop(self) -> None:
        while not self._stop.wait(DEFER_POLL):
            self.heartbeat()
            if self._waiting:
                try:
                    self._pump()
                except Exception:
                    log.exception("deferred send failed")

    def release_all(self) -> None:
        """Key-up for everything we pressed, plus all modifiers, on every client."""
        pressed, self._pressed = self._pressed, {}
        self._q.put(("release_all", pressed))
        log.info("release all keys")

    # -- sender thread ----------------------------------------------------
    def _send_loop(self) -> None:
        while True:
            op = self._q.get()
            if op is None:
                return
            self.sending_since = time.monotonic()
            try:
                self._execute(op)
            except Exception:
                log.exception("send failed: %r", op)
            finally:
                self.sending_since = None

    @property
    def running(self) -> bool:
        return self._deferrer is not None and self._deferrer.is_alive()

    def _execute(self, op: tuple) -> None:
        kind = op[0]
        if kind == "release_all":
            for vk, p in op[1].items():
                for h in self._hwnds(p.slots):
                    self._post(h, vk, False, p.mods)
            for h in self._hwnds(tuple(c.slot for c in self.team.attached())):
                for m in MOD_ORDER:
                    self.input.post_key(h, MOD_VK[m], False, sys_key=m is Mod.ALT)
            return
        if kind == "swap_press":
            self._swap_press(op[1], op[2], op[3], op[4])
            return
        if kind == "text":
            hwnds = self._hwnds(op[1])
            self._typing_into = frozenset(op[1])
            try:
                self._type_line(hwnds, op[2])
            finally:
                self._typing_into = frozenset()
            return
        self._execute_keys(op)

    def _type_line(self, hwnds: list[int], text: str) -> None:
        for h in hwnds:
            self.input.post_key(h, VK_RETURN, True)
            self.input.post_key(h, VK_RETURN, False)
        time.sleep(CHAT_OPEN_WAIT)
        for h in hwnds:
            for ch in text:
                self.input.post_char(h, ch)
        time.sleep(CHAT_OPEN_WAIT / 2)
        for h in hwnds:
            self.input.post_key(h, VK_RETURN, True)
            self.input.post_key(h, VK_RETURN, False)
        time.sleep(CHAT_OPEN_WAIT / 2)  # let the box close before any key that follows

    def _execute_keys(self, op: tuple) -> None:
        kind, slots, vk, mods = op
        hwnds = self._hwnds(slots)
        if kind == "down":
            for h in hwnds:
                self._post_mods(h, mods, True)
                self._post(h, vk, True, mods)
        elif kind == "repeat":
            for h in hwnds:
                self._post(h, vk, True, mods, repeat=True)
        elif kind == "up":
            for h in hwnds:
                self._post(h, vk, False, mods)
                self._post_mods(h, mods, False)
        elif kind == "press":
            for h in hwnds:
                self._post_mods(h, mods, True)
                self._post(h, vk, True, mods)
            time.sleep(PRESS_HOLD)
            for h in hwnds:
                self._post(h, vk, False, mods)
                self._post_mods(h, mods, False)
        self.sent_count += 1

    def _hwnds(self, slots: tuple[int, ...]) -> list[int]:
        return [h for s in slots if (h := self.team.hwnd(s))]

    def _post(self, hwnd: int, vk: int, down: bool, mods: Mod, repeat: bool = False) -> None:
        alt = bool(mods & Mod.ALT)
        sys_key = alt and not (mods & Mod.CTRL)
        self.input.post_key(hwnd, vk, down, sys_key=sys_key, alt_context=alt and down, repeat=repeat)

    def _post_mods(self, hwnd: int, mods: Mod, down: bool) -> None:
        order = MOD_ORDER if down else tuple(reversed(MOD_ORDER))
        for m in order:
            if mods & m:
                self.input.post_key(hwnd, MOD_VK[m], down, sys_key=m is Mod.ALT, alt_context=m is Mod.ALT and down)

    def _swap_press(self, slots: tuple[int, ...], vk: int, mods: Mod, active: int | None) -> None:
        """Focus each target, SendInput the chord, then focus back. Physical modifiers are already held."""
        prev = self.windows.foreground()
        inject = mods & ~self.held_mods
        for h in self._hwnds(slots):
            self.windows.set_foreground(h)
            deadline = time.perf_counter() + 0.15
            while self.windows.foreground() != h and time.perf_counter() < deadline:
                time.sleep(0.002)
            evs = [(MOD_VK[m], True) for m in MOD_ORDER if inject & m] + [(vk, True), (vk, False)]
            evs += [(MOD_VK[m], False) for m in reversed(MOD_ORDER) if inject & m]
            self.input.send_input(evs)
            time.sleep(self.cfg.focus_swap_dwell)
        if prev:
            self.windows.set_foreground(prev)
