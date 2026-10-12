"""Controller: owns every subsystem and bridges worker threads to the Qt main thread."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from pydantic import ValidationError
from PySide6.QtCore import QObject, QTimer, Signal

from . import settings
from .activity import ActivityFeed
from .automation import AutomationEngine
from .automation.actions import flatten
from .automation.engine import RuleRuntime, compile_condition
from .automation.macros import MacroRunner
from .commands import TeamCommands
from .config import AutomationRule, MacroConfig
from .keys import parse_chord
from .macro_store import fmt_duration, macros_path, migrate, save_macros
from .rule_store import automation_path, migrate_rules, save_rules
from .follow import FollowManager
from .stack import Stacker, StackManager, StackResult, apart
from .bridge import BridgePoller, ChatEvent, ClientState
from .bridge import words
from .config import AppConfig, RouteConfig, load_config
from .config_text import set_settings, update_team_yaml
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
        self.activity = ActivityFeed()
        self.engine.on_activity = lambda name, text, slots: self.activity.add("rule", name, text, slots)
        self.macros.on_activity = lambda name, text, slots: self.activity.add("macro", name, text, slots)
        self.memread = MemReadPoller(cfg.memread, self._pids, self._on_mem_values)
        self.stacker = Stacker(cfg.stack, self._client_state, self._post_raw)
        self.commands = TeamCommands(self.router, self._client_state)
        self.router.mirror_allowed = self._mirror_allowed
        self.apart: dict[int, str | None] = {}  # slot -> why it was left out of mirroring last time
        self.stack = StackManager(cfg.stack, self.stacker, lambda anchor: self.team.resolve(self.cfg.stack.who, anchor),
                                  on_done=self.sig_stack_done.emit, boost=self.bridge.boost,
                                  moving_since=lambda: self.router.last_movement)
        self._apply_stack_cfg()
        self.overlay = Overlay()
        # the chat filter (Words.lua) BoxBridge loads: its version, or None when there isn't one
        self.words_version: int | None = None
        self.words_problem: str | None = None  # why it couldn't be written
        self._words_checked = 0.0
        self._reload_asked: dict[int, float] = {}  # slot -> when WoWBox last typed /reload for the filter
        self._dropped_seen: dict[int, int] = {}  # slot -> the addon's dropped-lines counter (mod 16) last seen
        self.dropped: dict[int, int] = {}  # slot -> chat lines its BoxBridge dropped since WoWBox started

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
        self.sync_chat_filter()
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

    # ------------------------------------------------------------------ settings (the Settings tab)
    def apply_setting(self, section: str, key: str, raw: object) -> str | None:
        """Change one setting now. The running parts read these same objects, so it applies at once.
        Returns a problem (the value is then left as it was), or None."""
        model = getattr(self.cfg, section)
        try:
            new = type(model).model_validate({**model.model_dump(), key: raw})
        except ValidationError as e:
            return "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
        setattr(model, key, getattr(new, key))
        if section == "stack":
            self.stacker.load(model)
            self.stack.cfg = model
            self._apply_stack_cfg()
        elif section == "broadcast" and key == "chatguard":
            self.chatguard.enabled = model.chatguard
        elif section == "bridge" and key == "chat_filter":
            self.sync_chat_filter()
        elif section == "automation_options" and key == "tick_hz":
            self._auto_timer.setInterval(int(1000 / max(1.0, model.tick_hz)))
        log.info("setting %s.%s = %s", section, key, settings.show(model, key))
        self.sig_changed.emit()
        return None

    def unsaved_settings(self) -> list[settings.Setting]:
        """Settings whose value now differs from team.yaml."""
        try:
            disk = load_config(self.cfg_path)
        except Exception:
            return list(settings.ALL)
        return [s for s in settings.ALL
                if settings.show(getattr(self.cfg, s.section), s.key) != settings.show(getattr(disk, s.section), s.key)]

    def save_settings(self) -> list[str]:
        """Write the changed settings into team.yaml, in place (comments stay). Returns what was written."""
        changes = {(s.section, s.key): settings.show(getattr(self.cfg, s.section), s.key)
                   for s in self.unsaved_settings()}
        done = set_settings(self.cfg_path, changes) if changes else []
        if done:
            log.info("settings saved to %s: %s", self.cfg_path.name, ", ".join(done))
        return done

    def revert_settings(self) -> None:
        """Put every setting back to what team.yaml says."""
        disk = load_config(self.cfg_path)
        for s in self.unsaved_settings():
            self.apply_setting(s.section, s.key, getattr(getattr(disk, s.section), s.key))

    def save_config(self) -> None:
        """Write the route table, leader and slot characters into team.yaml, in place: the rest of the file,
        comments included, stays as you wrote it."""
        try:
            changed = update_team_yaml(self.cfg_path, self.cfg)
        except Exception as e:
            log.error("config not saved: %s", e)
            return
        if changed:
            log.info("saved %s to %s (previous version kept as .bak)", ", ".join(changed), self.cfg_path.name)
        else:
            log.info("nothing to save: %s already matches", self.cfg_path.name)

    def reload_config(self) -> None:
        try:
            cfg = load_config(self.cfg_path)
        except Exception as e:
            log.error("config not reloaded: %s", e)
            return
        self.cfg.routes = cfg.routes
        for section in ("automation_options", "broadcast"):  # the running parts hold these objects: update in place
            live, new = getattr(self.cfg, section), getattr(cfg, section)
            for k in type(new).model_fields:
                setattr(live, k, getattr(new, k))
        self.chatguard.enabled = self.cfg.broadcast.chatguard
        self._auto_timer.setInterval(int(1000 / max(1.0, self.cfg.automation_options.tick_hz)))
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
        self.sync_chat_filter()
        log.info("config reloaded (routes, hotkeys, macros, team, follow, stack, broadcast, automation). "
                 "Restart for other sections.")
        self.sig_changed.emit()
        if hasattr(self, "window"):
            self.window.load_routes()
            self.window.load_automation()
            self.window.load_macros()
            self.window.settings_tab.load()

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
        if not st.stale:
            self._count_dropped(slot, st.chat_dropped)
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

    # ------------------------------------------------------------------ macros (the editor)
    def macro_hotkey_owner(self, key: str, ignore_macro: str | None = None) -> str | None:
        """Who already uses hotkey `key` (another macro or a WoWBox hotkey), or None."""
        try:
            owner = self.router.hotkey_owner(parse_chord(key))
        except ValueError:
            return None
        if owner is None or owner == f"macro:{ignore_macro}":
            return None
        return f"macro {owner.split(':', 1)[1]!r}" if owner.startswith("macro:") else f"the WoWBox {owner} hotkey"

    def target_choices(self) -> list[str]:
        out = ["all", "followers", "leader", "not:leader", "active"]
        names = [c.character for c in self.team.clients.values() if c.character]
        names += [s.character for s in self.cfg.team.slots.values() if s.character]
        classes = sorted({c.class_token for c in self.team.clients.values() if c.class_token})
        out += names + [f"class:{c}" for c in classes] + ["role:tank", "role:healer", "role:dps"]
        return list(dict.fromkeys(out))

    def save_macro(self, old_name: str | None, macro: MacroConfig) -> None:
        """Add (old_name None) or replace a macro, write macros.yaml, and use it right away."""
        macros = list(self.cfg.macros)
        names = [m.name for m in macros]
        if macro.name != old_name and macro.name in names:
            raise ValueError(f"There's already a macro called {macro.name!r}.")
        if old_name in names:
            macros[names.index(old_name)] = macro
        else:
            macros.append(macro)
        self._store_macros(macros)
        log.info("macro %r saved", macro.name)

    def delete_macro(self, name: str) -> None:
        self.macros.stop(name)
        self._store_macros([m for m in self.cfg.macros if m.name != name])
        log.info("macro %r deleted", name)

    def _store_macros(self, macros: list[MacroConfig]) -> None:
        path = macros_path(self.cfg_path)
        if path.exists():
            save_macros(path, macros)
        else:
            migrate(self.cfg_path, macros)  # first edit: macros move out of team.yaml
            log.info("macros are now kept in %s (team.yaml is left as you wrote it)", path)
        self.cfg.macros = macros
        self.router.set_hotkeys(self.cfg.hotkeys, macros)
        self.macros.load(macros)
        if hasattr(self, "window"):
            self.window.load_macros()
        self.sig_changed.emit()

    # ------------------------------------------------------------------ automation rules (the editor)
    def save_rule(self, old_name: str | None, rule: AutomationRule) -> None:
        """Add (old_name None) or replace a rule, write automation.yaml, and use it right away."""
        rules = list(self.cfg.automation)
        names = [r.name for r in rules]
        if rule.name != old_name and rule.name in names:
            raise ValueError(f"There's already a rule called {rule.name!r}.")
        if old_name in names:
            rules[names.index(old_name)] = rule
        else:
            rules.append(rule)
        self._store_rules(rules)
        log.info("automation rule %r saved", rule.name)

    def delete_rule(self, name: str) -> None:
        self._store_rules([r for r in self.cfg.automation if r.name != name])
        log.info("automation rule %r deleted", name)

    def set_rule_enabled(self, name: str, on: bool) -> None:
        self._store_rules([r.model_copy(update={"enabled": on}) if r.name == name else r
                           for r in self.cfg.automation])
        log.info("automation rule %r %s", name, "enabled" if on else "disabled")

    def _store_rules(self, rules: list[AutomationRule]) -> None:
        path = automation_path(self.cfg_path)
        if path.exists():
            save_rules(path, rules)
        else:
            migrate_rules(self.cfg_path, rules)  # first edit: rules move out of team.yaml
            log.info("automation rules are now kept in %s (team.yaml is left as you wrote it)", path)
        self.cfg.automation = rules
        self.engine.load(rules)
        self.sync_chat_filter()
        if hasattr(self, "window"):
            self.window.load_automation()
        self.sig_changed.emit()

    def who_choices(self) -> list[str]:
        return ["any", "all"] + [f"{c.slot} ({c.name})" for c in self.team.attached()]

    def rule_preview(self, rule: AutomationRule) -> str:
        """The editor's live check: what this rule would do right now, in words (HTML)."""
        names = {c.slot: c.name for c in self.team.clients.values()}
        lines = []
        if rule.chat is not None:
            ch = rule.chat
            heard = [names.get(s, str(s)) for s in self.team.resolve(ch.heard_by, self.router.active_slot())]
            if ch.sender:
                named = [n for n in ch.sender if n.lower() != "team"]
                parts = [] if len(named) == len(ch.sender) else                     [f"your characters ({', '.join(sorted(self.character_names())) or 'none known yet'})"]
                src = "from " + ", ".join(parts + named)
            else:
                src = "from <b>anyone</b>"
            words = f" containing {' or '.join(repr(w) for w in ch.contains)}" if ch.contains else ""
            lines.append(f"Fires on a {'/'.join(ch.channel)} line {src}{words}, heard by "
                         f"{', '.join(heard) if heard else '<b>nobody</b> (check Heard by)'}.")
        if rule.when is not None:
            rt = RuleRuntime(rule, flatten(rule.do), compile_condition(rule.when))
            ok, per, values = self.engine._check(rt, self.states())  # same rules as the engine: dead, too far
            who = " · ".join(f"{names.get(s, s)}: " + ("<span style='color:#2e7d32'>matches</span>" if m
                                                          else f"no ({why})") for s, m, why in per)
            head = "<b style='color:#2e7d32'>Condition is TRUE now</b>" if ok else "<b>Condition is false now</b>"
            lines.append(f"{head}{' (the chat line must also come in)' if rule.chat else ''}: {who or 'no characters'}")
            if ok and values:
                lines.append(f"{{name}} would be <b>{values['name']}</b> (health {values['health']}%).")
        if rule.every is not None:
            lines.append(f"Fires every {fmt_duration(rule.every)}, the first time {fmt_duration(rule.every)} after "
                         f"automation is turned on.")
        if rule.cooldown:
            lines.append(f"At most once every {fmt_duration(rule.cooldown)}.")
        e = self.engine
        if not rule.enabled:
            lines.append("This rule is <b>disabled</b>: it never fires until you tick Enabled.")
        if not e.enabled:
            lines.append("Automation is <b>OFF</b> (toolbar): nothing fires while it's off.")
        elif e.dry_run:
            lines.append("Automation is ON in <b>dry run</b>: firings are only logged.")
        else:
            lines.append("Automation is ON and <b>live</b>: a firing really sends the steps.")
        return "<br>".join(lines)

    def test_rule(self, rule: AutomationRule) -> str:
        """The editor's Test: what one firing would send, without sending anything (also logged)."""
        from .automation.actions import MacroStep, SendStep, TypeStep, WaitStep, flatten, substitute
        steps = flatten(rule.do)
        if rule.chat is not None:
            ch = rule.chat
            first = (ch.sender or ["Someone"])[0]
            if first.lower() == "team":
                slot_cfg = self.cfg.team.slots.get(self.team.leader)
                first = (self.team.clients[self.team.leader].character or (slot_cfg and slot_cfg.character)
                         or next(iter(sorted(self.character_names())), "Yourleader"))
            sample = {"sender": first, "message": (ch.contains or ["hello"])[0],
                      "channel": ch.channel[0], "listener": self.team.clients[self.team.leader].name}
            steps = substitute(steps, sample)
            intro = f"Test: a {sample['channel']} from {sample['sender']} saying {sample['message']!r} would do:"
        else:
            intro = "Test: one firing would do:"
        out = [intro]
        names = {c.slot: c.name for c in self.team.clients.values()}
        for st in steps:
            if isinstance(st, WaitStep):
                out.append(f"  wait {fmt_duration(st.seconds)}")
            elif isinstance(st, MacroStep):
                out.append(f"  start macro {st.name!r}")
            else:
                slots = self.team.resolve(st.to, self.router.active_slot())
                who = ", ".join(names.get(s, str(s)) for s in slots) or "NOBODY (no character matches To)"
                what = f"type {st.text!r}" if isinstance(st, TypeStep) else f"press {st.chord}"
                out.append(f"  {what} -> {who}")
        out.append("(nothing was sent)")
        for line in out:
            log.info("[rule %s] %s", rule.name, line)
        return "<br>".join(line.replace("<", "&lt;").replace(">", "&gt;").replace("  ", "&nbsp;&nbsp;")
                           for line in out)

    # ------------------------------------------------------------------ stacking
    def _client_state(self, slot: int) -> ClientState | None:
        c = self.team.clients.get(slot)
        return c.state if c and c.attached else None

    def _post_raw(self, slot: int, vk: int, down: bool) -> None:  # stack threads: plain key, no modifiers
        if hwnd := self.team.hwnd(slot):
            self.input.post_key(hwnd, vk, down)

    def where_apart(self, slot: int, leader: int | None) -> str | None:
        """Why `slot` isn't with `leader` ("in another zone", "85 yd away"), or None."""
        if leader is None or slot == leader:
            return None
        lead = self._client_state(leader)
        return apart(self._client_state(slot), lead, self.cfg.follow.mirror_max_distance_yards,
                     self.stacker.units_per_yard(lead) if lead else None, self.cfg.stack.map_aspect)

    def _mirror_allowed(self, leader: int | None, slot: int) -> bool:  # keyboard hook thread: keep it quick
        if not self.cfg.follow.mirror_guard:
            return True
        why = self.where_apart(slot, leader)
        if why != self.apart.get(slot):
            self.apart[slot] = why
            name = self.team.clients[slot].name
            if why:
                log.info("mirroring: %s left out (%s)", name, why)
            else:
                log.info("mirroring: %s is with you again", name)
        return why is None

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
            self.activity.add("/bb", "stack", f"{name}: {r.message} ({where} off, facing {facing} off, "
                              f"{r.moves} moves)", [r.slot], kind="done" if r.ok else "skipped")
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
        sender = self.team.clients[origin].name if origin in self.team.clients else str(origin)
        if not self.cfg.bridge.commands:
            log.warning("/bb %s %s ignored: bridge.commands is off", who, text)
            self.activity.add("/bb", f"from {sender}", f"{who} {text}: ignored, bridge.commands is off", kind="skipped")
            return []
        if who == "@wowbox":
            self.activity.add("/bb", f"from {sender}", f"{text} (WoWBox action)", [origin], kind="fired")
            self.wowbox_action(origin, text)
            return []
        slots = self.team.command_slots(who, origin)
        if not slots:
            log.warning("/bb from %s: no client matches %r (%s)", sender, who, text)
            self.activity.add("/bb", f"from {sender}", f"{who} {text}: no client matches {who!r}", kind="skipped")
            return []
        log.info("/bb from %s -> %s %s: %s", sender, who, slots, text)
        self.activity.add("/bb", f"from {sender}", f"{who}: {text}", slots, kind="fired")
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
        self._check_chat_filter(time.monotonic())

    # ------------------------------------------------------------------ chat filter (Words.lua for BoxBridge)
    def addon_folder(self) -> Path:
        return Path(self.cfg.bridge.addon_dir) if self.cfg.bridge.addon_dir else words.addon_dir(self.cfg.client.exe)

    def sync_chat_filter(self) -> None:
        """Write Words.lua from the chat rules (only when they changed what it says)."""
        self._words_checked = time.monotonic()
        if not (self.cfg.bridge.enabled and self.cfg.bridge.chat_filter):
            self.words_version, self.words_problem = None, None
            return
        folder = self.addon_folder()
        if not folder.is_dir():
            problem = f"BoxBridge isn't installed at {folder} (set bridge.addon_dir in team.yaml)"
            if problem != self.words_problem:
                log.warning("chat filter not written: %s", problem)
            self.words_version, self.words_problem = None, problem
            return
        try:
            version, changed = words.sync(folder, words.build(self.cfg.automation))
        except OSError as e:
            if str(e) != self.words_problem:
                log.warning("chat filter not written: %s", e)
            self.words_version, self.words_problem = None, str(e)
            return
        self.words_version, self.words_problem = version, None
        if changed:
            log.info("chat filter written: %s (version %d). Clients pick it up on /reload.",
                     folder / words.FILE_NAME, version)
            self.activity.add("/bb", "chat filter", f"Words.lua version {version} written: /reload clients to use it",
                              kind="info")
        self.sig_changed.emit()

    def chat_filter_stale(self) -> list[int]:
        """Clients whose BoxBridge hasn't loaded the current Words.lua (they need a /reload)."""
        if self.words_version is None:
            return []
        return [c.slot for c in self.team.attached()
                if c.state and not c.state.stale and c.state.chat_filter != self.words_version]

    def _check_chat_filter(self, now: float) -> None:
        stale = self.chat_filter_stale()
        if stale and now - self._words_checked > 10:
            self.sync_chat_filter()  # the file may have been replaced (a fresh copy of the addon)
            stale = self.chat_filter_stale()
        if not (stale and self.cfg.bridge.chat_filter_reload):
            return
        for slot in stale:
            st = self.team.clients[slot].state
            if st.combat or st.dead or st.ghost or now - self._reload_asked.get(slot, -1e9) < 60:
                continue
            self._reload_asked[slot] = now
            log.info("%s: /reload to load the new chat filter", self.label_for(slot))
            self.activity.add("/bb", "chat filter", "/reload to load the new Words.lua", [slot], kind="fired")
            self.router.type_text("/reload", [slot])

    def _count_dropped(self, slot: int, counter: int) -> None:  # bridge thread
        last = self._dropped_seen.get(slot)
        self._dropped_seen[slot] = counter
        if last is None or counter == last:
            return
        n = (counter - last) % 16
        self.dropped[slot] = self.dropped.get(slot, 0) + n
        c = self.team.clients.get(slot)
        who = c.name if c else str(slot)
        log.warning("BoxBridge on %s dropped %d chat line(s): its queue was full", who, n)
        self.activity.add("/bb", "chat dropped", f"BoxBridge dropped {n} chat line(s) on {who}: queue full",
                          [slot], kind="skipped")

    # StateSource for the automation engine
    def states(self) -> dict[int, ClientState | None]:
        return {c.slot: c.state for c in self.team.attached()}

    def missing_windows(self) -> list[int]:
        return sorted(self._lost)

    def too_far_apart(self, slot: int, other: int) -> str | None:
        """Why `slot` isn't near `other` (another zone, too far), or None (near, or can't tell)."""
        return self.where_apart(slot, other)

    def character_names(self) -> set[str]:
        """Your own characters: the ones logged in now, plus those pinned to slots (for chat `sender: team`)."""
        names = {c.character for c in self.team.clients.values() if c.character}
        return names | {s.character for s in self.cfg.team.slots.values() if s.character}

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
            auto += f" ({self.engine.paused_reason})"
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
