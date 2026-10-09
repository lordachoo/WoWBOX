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


@dataclass
class _Pressed:
    slots: tuple[int, ...]
    mods: Mod
    swallowed: bool
    hotkey: bool = False


class Router:
    def __init__(self, team: Team, routes: RouteTable, input_api: InputAPI, windows: WindowAPI,
                 chatguard: ChatGuard, cfg: BroadcastConfig, hotkeys: HotkeyConfig,
                 on_hotkey: Callable[[str], None] = lambda name: None, macros: list | None = None) -> None:
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
        self.last_movement = -1e9  # monotonic time of the last movement key event
        self.set_hotkeys(hotkeys, macros or [])
        self._own_pid = os.getpid()
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._sender: threading.Thread | None = None
        self.sent_count = 0

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._sender is None:
            self._sender = threading.Thread(target=self._send_loop, name="router-sender", daemon=True)
            self._sender.start()
        self.input.start_hook(self.on_key)

    def stop(self) -> None:
        self.input.stop_hook()
        self.release_all()
        self._q.put(None)
        if self._sender:
            self._sender.join(2)
            self._sender = None

    def set_hotkeys(self, hk: HotkeyConfig, macros: list | None = None) -> None:
        table = {}
        for name in ("toggle_broadcast", "release_all", "cycle_layout", "next_main", "automation_kill",
                     "follow", "stay", "mirror_movement"):
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
        for vk in self._held_mod_vks:
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

        name = self._hotkeys.get(chord)
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
            slots = sorted(set(slots) | set(self.team.resolve(self.mirror_target, active)))
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
        """Type a chat line in each target: Enter, the text, Enter (e.g. "/assist Tinkerdawn")."""
        slots = self._targets(target, check_typing=False)
        if slots:
            self._q.put(("text", tuple(slots), text))
        else:
            log.info("type %r: no client matches %r", text, target)
        return slots

    def send_chord(self, chord: Chord, target: Target, check_typing: bool = True) -> list[int]:
        """Send a full press of `chord` to `target`. Automation skips clients that are typing."""
        slots = self._targets(target, check_typing)
        if not slots:
            log.info("send %s: no client matches %r", chord, target)
        if slots:
            if self.cfg.modifier_mode == "focus_swap" and chord.mods:
                self._q.put(("swap_press", tuple(slots), chord.vk, chord.mods, None))
            else:
                mods = Mod.NONE if self.cfg.modifier_mode == "strip" else chord.mods
                self._q.put(("press", tuple(slots), chord.vk, mods))
        return slots

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
            try:
                self._execute(op)
            except Exception:
                log.exception("send failed: %r", op)

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
            for h in hwnds:
                self.input.post_key(h, VK_RETURN, True)
                self.input.post_key(h, VK_RETURN, False)
            time.sleep(CHAT_OPEN_WAIT)
            for h in hwnds:
                for ch in op[2]:
                    self.input.post_char(h, ch)
            time.sleep(CHAT_OPEN_WAIT / 2)
            for h in hwnds:
                self.input.post_key(h, VK_RETURN, True)
                self.input.post_key(h, VK_RETURN, False)
            time.sleep(CHAT_OPEN_WAIT / 2)  # let the box close before any key that follows
            return
        _, slots, vk, mods = op
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
