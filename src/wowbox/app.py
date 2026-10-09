"""Controller: owns every subsystem and bridges worker threads to the Qt main thread."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from .automation import AutomationEngine
from .automation.macros import MacroRunner
from .commands import TeamCommands
from .follow import FollowManager
from .stack import Stacker, StackManager, StackResult
from .bridge import BridgePoller, ChatEvent, ClientState
from .config import AppConfig, RouteConfig, load_config, save_config
from .keys import parse_chord
from .launcher import Launcher
from .layout import LayoutManager
from .layout.main_thumbs import ThumbStrip
from .layout.tabs import TabBar
from .memread.reader import MemReadPoller
from .platform.base import CaptureAPI, InputAPI, WindowAPI
from .router import ChatGuard, RouteTable, Router
from .team import Team
from .ui.overlay import Overlay

log = logging.getLogger(__name__)


class QtLogHandler(logging.Handler):
    def __init__(self, emit: Callable[[str], None]) -> None:
        super().__init__(logging.INFO)
        self._emit = emit
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._emit(self.format(record))
        except Exception:
            pass


class Controller(QObject):
    sig_hotkey = Signal(str)
    sig_log = Signal(str)
    sig_changed = Signal()
    sig_launch_done = Signal()
    sig_identity = Signal()
    sig_chat = Signal(int, object)
    sig_stack_done = Signal(object)

    def __init__(self, cfg_path: Path, cfg: AppConfig, windows: WindowAPI, input_api: InputAPI,
                 capture_factory: Callable[[], CaptureAPI]) -> None:
        super().__init__()
        self.cfg_path = cfg_path
        self.cfg = cfg
        self.windows = windows
        self.input = input_api
        self._lost: set[int] = set()
        self._launching = False

        self.log_handler = QtLogHandler(self.sig_log.emit)
        logging.getLogger().addHandler(self.log_handler)

        self.team = Team(cfg)
        self.launcher = Launcher(cfg, self.team, windows)
        self.chatguard = ChatGuard(cfg.broadcast.chatguard, self._chords(cfg.broadcast.chatguard_open_keys),
                                   self._chords(cfg.broadcast.chatguard_close_keys))
        self.router = Router(self.team, RouteTable(cfg.routes), input_api, windows, self.chatguard, cfg.broadcast,
                             cfg.hotkeys, on_hotkey=self.sig_hotkey.emit, macros=cfg.macros)
        self.router.on_state_change = self.sig_changed.emit
        self.follow = FollowManager(cfg.follow, self.team, self.router, on_change=self.sig_changed.emit)
        self.chatguard.on_change = lambda _open: self.sig_changed.emit()

        self.thumbs = ThumbStrip(self.label_for)
        self.tabs = TabBar(self.label_for)
        self.layout = LayoutManager(cfg.layout, self.team, windows, self.thumbs, self.tabs)
        self.thumbs.clicked.connect(lambda s: self.layout.set_main(s))
        self.tabs.clicked.connect(lambda s: self.layout.set_main(s))

        self.bridge = BridgePoller(cfg.bridge, self.team, windows, capture_factory, on_state=self._on_bridge_state,
                                   on_chat=self.sig_chat.emit)
        self.engine = AutomationEngine(cfg.automation, cfg.automation_options, self.router, self,
                                       bridge_enabled=cfg.bridge.enabled)
        self.macros = MacroRunner(cfg.macros, self.router)
        self.engine.on_macro = self.macros.start
        self.memread = MemReadPoller(cfg.memread, self._pids, self._on_mem_values)
        self.stacker = Stacker(cfg.stack, self._client_state, self._post_raw)
        self.commands = TeamCommands(self.router, self._client_state)
        self.stack = StackManager(cfg.stack, self.stacker, lambda anchor: self.team.resolve(self.cfg.stack.who, anchor),
                                  on_done=self.sig_stack_done.emit, boost=self.bridge.boost,
                                  moving_since=lambda: self.router.last_movement)
        self._apply_stack_cfg()
        self.overlay = Overlay()

        self.sig_hotkey.connect(self._on_hotkey)
        self.sig_changed.connect(self._update_overlay)
        self.sig_launch_done.connect(self._after_launch)
        self.sig_identity.connect(self._on_identity)
        self.sig_chat.connect(self._on_chat)
        self.sig_stack_done.connect(self._on_stack_done)

        self._refresh_timer = QTimer(self, timeout=self._refresh, interval=1000)
        self._auto_timer = QTimer(self, timeout=self._tick,
                                  interval=int(1000 / max(1.0, cfg.automation_options.tick_hz)))
        self._overlay_timer = QTimer(self, timeout=self._update_overlay, interval=300)

    def _tick(self) -> None:
        now = time.monotonic()
        self.engine.tick(now)
        self.follow.tick(now)

    @staticmethod
    def _chords(specs: list[str]) -> set:
        return {parse_chord(s) for s in specs}

    # ------------------------------------------------------------------ lifecycle
    def start(self, attach: bool = True) -> None:
        if attach and self.launcher.attach_existing():
            self.layout.apply()
        self.router.start()
        if self.cfg.bridge.enabled:
            self.bridge.start()
        self.memread.start()
        self._refresh_timer.start()
        self._auto_timer.start()
        if self.cfg.ui.get("overlay", False):
            self.set_overlay(True)
        log.info("WoWBox ready: %d client(s) attached, %d route chord(s), broadcast %s",
                 len(self.team.attached()), len(self.router.routes), "ON" if self.router.enabled else "OFF")

    def shutdown(self) -> None:
        log.info("shutting down")
        self._refresh_timer.stop()
        self._auto_timer.stop()
        self.engine.set_enabled(False)
        self.macros.stop_all()
        self.stack.cancel()
        self.router.stop()
        self.bridge.stop()
        self.memread.stop()
        self.overlay.hide()
        self.thumbs.clear()
        self.tabs.hide()
        logging.getLogger().removeHandler(self.log_handler)

    # ------------------------------------------------------------------ actions
    def attach(self) -> None:
        slots = self.launcher.attach_existing()
        self._lost -= set(slots)
        if slots:
            self.layout.apply()
        else:
            log.info("no unassigned WoW windows found")

    def launch(self) -> None:
        if self._launching:
            return
        self._launching = True

        def work():
            try:
                started = self.launcher.launch_missing(progress=log.info)
                self._lost -= set(started)
            except Exception as e:
                log.error("launch failed: %s", e)
            finally:
                self.sig_launch_done.emit()

        threading.Thread(target=work, name="launcher", daemon=True).start()

    def _after_launch(self) -> None:
        self._launching = False
        self.layout.apply()

    def set_leader(self, slot: int) -> None:
        if slot is None:
            return
        self.team.leader = slot
        self.cfg.team.leader = slot
        log.info("leader -> slot %d", slot)

    def set_routes(self, routes: list[RouteConfig]) -> None:
        self.cfg.routes = routes
        self.router.routes = RouteTable(routes)
        log.info("routes applied: %d rule(s), %d chord(s)", len(routes), len(self.router.routes))

    def set_dry_run(self, on: bool) -> None:
        self.engine.dry_run = on
        self.cfg.automation_options.dry_run = on
        log.info("automation dry run %s", "ON" if on else "OFF")

    def set_overlay(self, on: bool) -> None:
        self.cfg.ui["overlay"] = on
        if on:
            self._update_overlay()
            self.overlay.show()
            self._overlay_timer.start()
        else:
            self.overlay.hide()
            self._overlay_timer.stop()

    def save_config(self) -> None:
        save_config(self.cfg, self.cfg_path)
        log.info("config saved to %s (previous version kept as .bak)", self.cfg_path)

    def reload_config(self) -> None:
        try:
            cfg = load_config(self.cfg_path)
        except Exception as e:
            log.error("config not reloaded: %s", e)
            return
        self.cfg.routes = cfg.routes
        self.cfg.automation = cfg.automation
        self.cfg.macros = cfg.macros
        self.cfg.hotkeys = cfg.hotkeys
        self.cfg.team = cfg.team
        self.cfg.follow = cfg.follow
        self.follow.load(cfg.follow)
        self.cfg.stack = cfg.stack
        self.stack.cfg = cfg.stack
        self.stacker.load(cfg.stack)
        self._apply_stack_cfg()
        self.team.reload(cfg)
        self.router.routes = RouteTable(cfg.routes)
        self.router.set_hotkeys(cfg.hotkeys, cfg.macros)
        self.macros.load(cfg.macros)
        self.chatguard.open_keys = self._chords(cfg.broadcast.chatguard_open_keys)
        self.chatguard.close_keys = self._chords(cfg.broadcast.chatguard_close_keys)
        self.engine.load(cfg.automation)
        log.info("config reloaded (routes, hotkeys, macros, team, follow, automation). Restart for other sections.")
        self.sig_changed.emit()
        if hasattr(self, "window"):
            self.window.load_routes()
            self.window.load_automation()
            self.window.load_macros()

    # ------------------------------------------------------------------ callbacks
    def _on_hotkey(self, name: str) -> None:
        if name == "cycle_layout":
            log.info("layout -> %s", self.layout.cycle())
        elif name == "next_main":
            self.layout.next_main()
        elif name == "automation_kill":
            self.engine.kill()
        elif name == "follow":
            self.follow.follow()
        elif name == "stay":
            self.follow.stay()
        elif name == "stack":
            self.stack_on_me()
        elif name == "mirror_movement":
            self.follow.toggle_mirror()
        elif name.startswith("macro:"):
            self.macros.toggle(name.split(":", 1)[1])
        elif name.startswith("focus_slot:"):
            self.layout.set_main(int(name.split(":")[1]))
        self.sig_changed.emit()

    def _on_bridge_state(self, slot: int, st: ClientState) -> None:  # bridge thread
        self.chatguard.set_bridge(slot, st.typing and not st.stale)
        c = self.team.clients.get(slot)
        if c and st.name and not st.stale and (st.name, st.class_token) != (c.character, c.class_token):
            c.character, c.class_token = st.name, st.class_token
            self.sig_identity.emit()

    def _on_chat(self, slot: int, ev: ChatEvent) -> None:  # main thread
        c = self.team.clients.get(slot)
        listener = c.name if c else str(slot)
        if ev.channel == "command":
            self.team_command(slot, ev.sender, ev.text)
            return
        cut = "..." if ev.truncated else ""
        log.info("chat [%s] %s from %s: %s%s", listener, ev.channel, ev.sender, ev.text, cut)
        self.engine.on_chat(slot, ev, time.monotonic(), listener=listener)

    # ------------------------------------------------------------------ stacking
    def _client_state(self, slot: int) -> ClientState | None:
        c = self.team.clients.get(slot)
        return c.state if c and c.attached else None

    def _post_raw(self, slot: int, vk: int, down: bool) -> None:  # stack threads: plain key, no modifiers
        if hwnd := self.team.hwnd(slot):
            self.input.post_key(hwnd, vk, down)

    def _apply_stack_cfg(self) -> None:
        self.follow.stack_on_stop = self.stack.stack_on if self.cfg.stack.on_stop else None
        self.follow.stack_delay = self.cfg.stack.on_stop_delay

    def stack_on_me(self, anchor: int | None = None) -> None:
        """Stack everyone on `anchor` (default: the window you're in, else the leader). Again = cancel."""
        if self.stack.running:
            self.stack.cancel()
            log.info("stack: cancelled")
            return
        anchor = anchor or self.router.active_slot() or self.team.leader
        self.stack.stack_on(anchor)

    def _on_stack_done(self, results: list[StackResult]) -> None:
        for r in results:
            name = self.team.clients[r.slot].name if r.slot in self.team.clients else str(r.slot)
            where = f"{r.distance_yards:.2f} yd" if r.distance_yards is not None else "? yd"
            facing = f"{r.facing_error_deg:.0f}°" if r.facing_error_deg is not None else "?"
            (log.info if r.ok else log.warning)("stack %s: %s (%s off, facing %s off, %d moves)",
                                                 name, r.message, where, facing, r.moves)
        self.sig_changed.emit()

    def wowbox_action(self, origin: int, action: str) -> None:
        """`/bb stack|follow|stay|mirror` typed in a client."""
        action = action.strip().lower()
        if action == "stack":
            self.stack_on_me(origin)
        elif action == "follow":
            self.follow.follow()
        elif action == "stay":
            self.follow.stay()
        elif action == "mirror":
            self.follow.toggle_mirror()
        else:
            log.warning("/bb %s: unknown WoWBox action", action)
        self.sig_changed.emit()

    def team_command(self, origin: int, who: str, text: str) -> list[int]:
        """`/bb all|others|to <who> <command>` typed in the client in slot `origin`: type it in the clients meant."""
        if not self.cfg.bridge.commands:
            log.warning("/bb %s %s ignored: bridge.commands is off", who, text)
            return []
        if who == "@wowbox":
            self.wowbox_action(origin, text)
            return []
        slots = self.team.command_slots(who, origin)
        sender = self.team.clients[origin].name if origin in self.team.clients else str(origin)
        if not slots:
            log.warning("/bb from %s: no client matches %r (%s)", sender, who, text)
            return []
        log.info("/bb from %s -> %s %s: %s", sender, who, slots, text)
        self.commands.send(text, slots, origin)
        return slots

    # ------------------------------------------------------------------ identity / slots
    def _wanted_characters(self) -> dict[int, str]:
        return {slot: s.character for slot, s in self.cfg.team.slots.items() if s.character}

    def _on_identity(self) -> None:
        """A client reported its character: put configured characters in their slots, retitle windows."""
        moves = self.team.assign_by_character(self._wanted_characters())
        for name, src, dst in moves:
            log.info("%s is configured for slot %d: moved it there from slot %d", name, dst, src)
        for c in self.team.attached():
            if c.character:
                log.debug("slot %d = %s (%s)", c.slot, c.character, c.class_token)
        self._retitle()
        if moves:
            self.layout.apply()
        self.sig_changed.emit()

    def _retitle(self) -> None:
        if not self.cfg.client.rename_windows:
            return
        for c in self.team.attached():
            if self.windows.title(c.hwnd) != c.window_title:
                self.windows.set_title(c.hwnd, c.window_title)

    def move_slot(self, slot: int, delta: int) -> None:
        """Swap this slot's window with the neighbouring slot's."""
        slots = self.team.slots
        i = slots.index(slot)
        if not 0 <= i + delta < len(slots):
            return
        other = slots[i + delta]
        self.team.swap(slot, other)
        log.info("swapped slots %d and %d", slot, other)
        self._retitle()
        self.layout.apply()
        self.sig_changed.emit()

    def remember_characters(self) -> None:
        """Pin the characters currently in each slot into team.yaml so they land there next time."""
        saved = []
        for c in self.team.attached():
            if c.character:
                self.cfg.team.slots[c.slot].character = c.character
                saved.append(f"{c.slot}={c.character}")
        if not saved:
            log.warning("no character names known yet (is BoxBridge running in the clients?)")
            return
        self.save_config()
        log.info("slots pinned to characters: %s", ", ".join(saved))

    def _on_mem_values(self, slot: int, values: dict) -> None:  # memread thread
        c = self.team.clients.get(slot)
        if c:
            c.mem = values

    def _pids(self) -> dict[int, int]:
        return {c.slot: c.pid for c in self.team.attached() if c.pid}

    def _refresh(self) -> None:
        for slot in self.launcher.refresh():
            self._lost.add(slot)
            self.chatguard.forget(slot)

    # StateSource for the automation engine
    def states(self) -> dict[int, ClientState | None]:
        return {c.slot: c.state for c in self.team.attached()}

    def missing_windows(self) -> list[int]:
        return sorted(self._lost)

    def slots_for(self, target) -> list[int]:
        return self.team.resolve(target, self.router.active_slot())

    # ------------------------------------------------------------------ text
    def label_for(self, slot: int) -> str:
        c = self.team.clients.get(slot)
        if not c:
            return str(slot)
        text = f"{slot} {c.character or c.label}"
        if c.state and not c.state.stale:
            text += f"  HP {c.state.health_pct:.0f}%"
        return text

    def status_text(self) -> str:
        active = self.router.active_slot()
        chat = "PAUSED (typing)" if self.chatguard.paused(active) else "ok"
        auto = "ON" + (" dry-run" if self.engine.dry_run else "") if self.engine.enabled else "OFF"
        if self.engine.paused_reason:
            auto += f" paused: {self.engine.paused_reason}"
        rehooks = getattr(self.input, "rehook_count", 0)
        return (f"Broadcast {'ON' if self.router.enabled else 'OFF'} | active: "
                f"{self.team.clients[active].name if active else '-'} | chat guard: {chat} | automation: {auto}"
                f" | follow: {'ON' if self.follow.active else 'OFF'}{' + mirror' if self.router.mirror else ''}"
                f" | layout: {self.layout.current} | hook re-installs: {rehooks}")

    def _update_overlay(self) -> None:
        if not self.overlay.isVisible() and not self._overlay_timer.isActive():
            return
        active = self.router.active_slot()
        on = self.router.enabled
        typing = self.chatguard.paused(active)
        color = "#e74c3c" if not on else ("#f1c40f" if typing else "#2ecc71")
        state = "OFF" if not on else ("PAUSED - chat" if typing else "ON")
        who = self.team.clients[active].name if active else "-"
        auto = f" | AUTO{' (dry)' if self.engine.dry_run else ''}" if self.engine.enabled else ""
        follow = (" | FOLLOW" if self.follow.active else "") + (" | MIRROR" if self.router.mirror else "")
        self.overlay.set_text(f"<span style='color:{color}'>BROADCAST {state}</span> | {who}{follow}{auto}")
