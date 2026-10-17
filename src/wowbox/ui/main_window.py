"""Control panel."""
from __future__ import annotations

import logging
import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget,
)

from ..config import RouteConfig
from ..keys import expand_key_spec
from ..layout import LAYOUT_ORDER
from .activity_tab import ActivityTab
from .rule_list import RuleList
from .settings_tab import SettingsTab

log = logging.getLogger(__name__)

CLIENT_COLS = ["Slot", "Character", "Label", "Role", "PID", "Window", "Status", "Broadcast", "HP", "Power", "Target",
               "State", "Position", "Bridge"]


def _item(text: str, editable: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    if not editable:
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    return it


class MainWindow(QMainWindow):
    def __init__(self, controller) -> None:
        super().__init__()
        self.c = controller
        self.setWindowTitle(f"WoWBox - {controller.cfg_path.name}")
        self.resize(1180, 640)
        self._quitting = False

        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(self._build_toolbar())
        self.banner = QLabel(wordWrap=True)  # automation paused / dry run / closed windows: seen from any tab
        self.banner.setTextFormat(Qt.TextFormat.RichText)
        self.banner.hide()
        root.addWidget(self.banner)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_clients_tab(), "Clients")
        self.tabs.addTab(self._build_routes_tab(), "Routes")
        self.tabs.addTab(self._build_macros_tab(), "Macros")
        self.tabs.addTab(self._build_automation_tab(), "Automation")
        self.activity_tab = ActivityTab(self.c, on_settings=lambda: self.tabs.setCurrentWidget(self.settings_tab))
        self.tabs.addTab(self.activity_tab, "Activity")
        self.settings_tab = SettingsTab(self.c)
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.currentChanged.connect(
            lambda _: self.tabs.currentWidget() is self.settings_tab and self.settings_tab.load())
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setFont(QFont("Consolas", 9))
        self.tabs.addTab(self.log_view, "Log")
        root.addWidget(self.tabs)
        self.setCentralWidget(central)
        self.status = QLabel()
        self.statusBar().addPermanentWidget(self.status, 1)

        self.c.sig_log.connect(self.log_view.appendPlainText)
        self.timer = QTimer(self, timeout=self._safe_refresh, interval=250)
        self.timer.start()
        self.load_routes()
        self.load_automation()
        self.load_macros()
        self.refresh()

    # ------------------------------------------------------------------ toolbar
    def _build_toolbar(self) -> QVBoxLayout:
        rows = QVBoxLayout()
        b_launch = QPushButton("Launch missing", clicked=self.c.launch)
        b_launch.setToolTip("Start a client for every slot that has no window (uses client.exe from the config)")
        b_attach = QPushButton("Attach running", clicked=self.c.attach)
        b_attach.setToolTip("Assign already-running WoW windows to empty slots")
        b_arrange = QPushButton("Arrange", clicked=lambda: self.c.layout.apply())
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(LAYOUT_ORDER)
        self.layout_combo.setCurrentText(self.c.layout.current)
        self.layout_combo.textActivated.connect(lambda name: self.c.layout.apply(name))
        self.leader_combo = QComboBox()
        for s in self.c.team.slots:
            self.leader_combo.addItem(str(s), s)
        self.leader_combo.setCurrentText(str(self.c.team.leader))
        self.leader_combo.currentIndexChanged.connect(lambda _i: self.c.set_leader(self.leader_combo.currentData()))

        self.b_broadcast = QPushButton(clicked=lambda: self.c.router.set_enabled(not self.c.router.enabled))
        self.b_broadcast.setMinimumWidth(150)
        self.b_auto = QPushButton(clicked=lambda: self.c.engine.set_enabled(not self.c.engine.enabled))
        self.b_auto.setMinimumWidth(130)
        self.cb_dry = QCheckBox("Dry run")
        self.cb_dry.setChecked(self.c.engine.dry_run)
        self.cb_dry.toggled.connect(self.c.set_dry_run)
        self.cb_overlay = QCheckBox("Overlay")
        self.cb_overlay.toggled.connect(self.c.set_overlay)
        b_release = QPushButton("Release keys", clicked=self.c.router.release_all)
        hk = self.c.cfg.hotkeys
        self.b_follow = QPushButton(clicked=lambda: self.c.follow.set_active(not self.c.follow.active))
        self.b_follow.setToolTip(f"Followers target the leader and /follow, and get put back on follow when it "
                                 f"breaks [{hk.follow}]. Click again (or [{hk.stay}]) to make them stay.")
        self.b_follow.setMinimumWidth(120)
        self.b_stack = QPushButton(f"Stack [{hk.stack}]", clicked=lambda: self.c.stack_on_me())
        self.b_stack.setToolTip("Walk everyone onto the exact spot and facing of the window you're in (or the "
                                "leader). Press again to cancel. Needs BoxBridge positions.")
        self.cb_mirror = QCheckBox(f"Mirror movement [{hk.mirror_movement}]")
        self.cb_mirror.setToolTip("Copy your movement keys (follow.movement_keys) to the followers too")
        self.cb_mirror.toggled.connect(self.c.router.set_mirror)
        b_frames = QPushButton("Restore frames", clicked=self.c.layout.restore_frames)
        b_frames.setToolTip("Put the normal window borders back on all clients")
        b_reload = QPushButton("Reload config", clicked=self.c.reload_config)
        b_save = QPushButton("Save config", clicked=self.c.save_config)

        for group in ((self.b_broadcast, self.b_follow, self.b_stack, self.cb_mirror, self.b_auto, self.cb_dry, self.cb_overlay,
                       b_release),
                      (b_launch, b_attach, b_arrange, QLabel("Layout:"), self.layout_combo, QLabel("Leader:"),
                       self.leader_combo, b_frames, b_reload, b_save)):
            bar = QHBoxLayout()
            for w in group:
                bar.addWidget(w)
            bar.addStretch(1)
            rows.addLayout(bar)
        return rows

    # ------------------------------------------------------------------ clients
    def _build_clients_tab(self) -> QWidget:
        self.clients_table = QTableWidget(0, len(CLIENT_COLS))
        self.clients_table.setHorizontalHeaderLabels(CLIENT_COLS)
        self.clients_table.verticalHeader().setVisible(False)
        self.clients_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.clients_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.clients_table.horizontalHeader().setStretchLastSection(True)
        self.clients_table.cellDoubleClicked.connect(self._client_double_clicked)
        self.clients_table.itemChanged.connect(self._client_item_changed)
        self._client_rows: list[int] = []
        hint = QLabel("Double-click a row to focus that client. Untick Broadcast to leave a client out of "
                      "all/followers broadcasts. Wrong character in a slot? Select it and use Move up/down, then "
                      "Remember characters so each character always lands in its slot (needs BoxBridge in-game).")
        hint.setWordWrap(True)
        buttons = QHBoxLayout()
        for text, fn in (("Move up", lambda: self._move_selected(-1)), ("Move down", lambda: self._move_selected(1)),
                         ("Remember characters", self.c.remember_characters)):
            buttons.addWidget(QPushButton(text, clicked=fn))
        buttons.addStretch(1)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self.clients_table)
        lay.addLayout(buttons)
        lay.addWidget(hint)
        return w

    def _move_selected(self, delta: int) -> None:
        row = self.clients_table.currentRow()
        if 0 <= row < len(self._client_rows):
            self.c.move_slot(self._client_rows[row], delta)
            new_row = row + delta
            if 0 <= new_row < len(self._client_rows):
                self.clients_table.selectRow(new_row)

    def _client_double_clicked(self, row: int, _col: int) -> None:
        if row < len(self._client_rows):
            self.c.layout.set_main(self._client_rows[row])

    def _client_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == CLIENT_COLS.index("Broadcast") and item.row() < len(self._client_rows):
            slot = self._client_rows[item.row()]
            self.c.team.clients[slot].broadcast = item.checkState() == Qt.Checked

    def refresh_clients(self) -> None:
        t = self.clients_table
        clients = list(self.c.team.clients.values())
        t.blockSignals(True)
        if [c.slot for c in clients] != self._client_rows:
            t.setRowCount(len(clients))
            self._client_rows = [c.slot for c in clients]
            for r, c in enumerate(clients):
                bc = _item("")
                bc.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                t.setItem(r, CLIENT_COLS.index("Broadcast"), bc)
        active = self.c.router.active_slot()
        for r, c in enumerate(clients):
            st = c.state
            if not c.attached:
                status = "not running"
            elif not c.responding:
                status = "NOT RESPONDING"
            else:
                status = "active" if c.slot == active else "ok"
            lead = " (leader)" if c.slot == self.c.team.leader else ""
            wanted = self.c.cfg.team.slots[c.slot].character if c.slot in self.c.cfg.team.slots else None
            if c.character:
                who = f"{c.character} ({c.class_token.title()})" if c.class_token else c.character
                if wanted and wanted.lower() != c.character.lower():
                    who += f"  [slot wants {wanted}]"
            else:
                who = f"(waiting for {wanted})" if wanted else ""
            values = {
                "Slot": f"{c.slot}{lead}", "Character": who, "Label": c.label, "Role": c.role, "PID": str(c.pid or ""),
                "Window": f"0x{c.hwnd:X}" if c.hwnd else "", "Status": status,
                "HP": f"{st.health_pct:.0f}%" if st and not st.stale else "",
                "Power": f"{st.power_pct:.0f}% {st.power_name}" if st and not st.stale else "",
                "Target": ((f"{st.target_health_pct:.0f}%" + (" hostile" if st.target_hostile else "")
                            + (" dead" if st.target_dead else "")) if st and st.target_exists and not st.stale
                           else ""),
                "State": self._flags(st),
                "Position": ((f"{st.map_x * 100:.2f}, {st.map_y * 100:.2f}  {math.degrees(st.facing):.0f}°  "
                              f"{st.fps} fps" if st and st.position_ok and not st.stale else "")
                             + (f"  [{away}]" if (away := self.c.where_apart(c.slot, self.c.team.leader)) else "")),
                "Bridge": self._bridge_text(c.slot),
            }
            for col, text in values.items():
                ci = CLIENT_COLS.index(col)
                it = t.item(r, ci)
                if it is None:
                    t.setItem(r, ci, _item(text))
                elif it.text() != text:
                    it.setText(text)
            bc = t.item(r, CLIENT_COLS.index("Broadcast"))
            bc.setCheckState(Qt.Checked if c.broadcast else Qt.Unchecked)
            if c.slot == active:
                color = QColor("#43a047")
            elif not c.attached:
                color = QColor("#888")
            elif not c.responding:
                color = QColor("#e53935")
            else:
                color = t.palette().text().color()
            t.item(r, CLIENT_COLS.index("Status")).setForeground(color)
        t.blockSignals(False)

    @staticmethod
    def _flags(st) -> str:
        if st is None:
            return ""
        if st.stale:
            return "STALE"
        flags = [n for n, v in (("combat", st.combat), ("casting", st.casting), ("channel", st.channeling),
                                ("TYPING", st.typing), ("mounted", st.mounted), ("following", st.following), ("dead", st.dead),
                                ("ghost", st.ghost), ("afk", st.afk)) if v]
        return " ".join(flags) or "idle"

    def _bridge_text(self, slot: int) -> str:
        if not self.c.cfg.bridge.enabled:
            return "off"
        err = self.c.bridge.last_error.get(slot)
        method = self.c.bridge.last_method.get(slot, "")
        return f"{method}: {err}" if err else method

    # ------------------------------------------------------------------ routes
    def _build_routes_tab(self) -> QWidget:
        self.routes_table = QTableWidget(0, 3)
        self.routes_table.setHorizontalHeaderLabels(["Keys (comma separated, ranges ok: 1-9, F1-F4)",
                                                     "To (all / active / followers / leader / none / 1,3)", "Note"])
        self.routes_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.routes_table.verticalHeader().setVisible(False)
        buttons = QHBoxLayout()
        for text, fn in (("Add", self._route_add), ("Remove", self._route_remove), ("Up", lambda: self._route_move(-1)),
                         ("Down", lambda: self._route_move(1)), ("Revert", self._route_revert)):
            buttons.addWidget(QPushButton(text, clicked=fn))
        buttons.addStretch(1)
        self.routes_table.itemChanged.connect(lambda _item: self.apply_routes())
        self.routes_status = QLabel()
        self.routes_status.setWordWrap(True)
        hint = QLabel("Changes take effect as soon as you finish editing a cell. Save config writes them to "
                      "team.yaml (only the routes part; the rest of the file and its comments stay). "
                      "First matching route wins. A chord matches exactly: list Shift+1 separately from 1. Keys "
                      "not listed go only to the window you're in. To: all, active (only the window you're in), "
                      "followers, leader, none (nobody, the key is blocked), or slots like 1,3.")
        hint.setWordWrap(True)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self.routes_table)
        lay.addLayout(buttons)
        lay.addWidget(self.routes_status)
        lay.addWidget(hint)
        return w

    def load_routes(self) -> None:
        t = self.routes_table
        t.blockSignals(True)
        t.setRowCount(0)
        for r in self.c.cfg.routes:
            row = t.rowCount()
            t.insertRow(row)
            to = r.to if isinstance(r.to, str) else ",".join(map(str, r.to))
            for col, text in enumerate((", ".join(r.keys), to, r.note)):
                t.setItem(row, col, _item(text, editable=True))
        t.blockSignals(False)
        if hasattr(self, "routes_status"):
            self.routes_status.setText("")

    def _route_revert(self) -> None:
        self.load_routes()

    def _route_add(self) -> None:
        row = self.routes_table.rowCount()
        self.routes_table.blockSignals(True)
        self.routes_table.insertRow(row)
        for col, text in enumerate(("", "all", "")):
            self.routes_table.setItem(row, col, _item(text, editable=True))
        self.routes_table.blockSignals(False)
        self.routes_table.editItem(self.routes_table.item(row, 0))

    def _route_remove(self) -> None:
        rows = sorted({i.row() for i in self.routes_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.routes_table.removeRow(r)
        self.apply_routes()

    def _route_move(self, delta: int) -> None:
        t = self.routes_table
        r = t.currentRow()
        if r < 0 or not 0 <= r + delta < t.rowCount():
            return
        texts = [[t.item(row, c).text() if t.item(row, c) else "" for c in range(3)] for row in (r, r + delta)]
        t.blockSignals(True)
        for row, vals in zip((r + delta, r), texts):
            for c, text in enumerate(vals):
                t.setItem(row, c, _item(text, editable=True))
        t.blockSignals(False)
        t.setCurrentCell(r + delta, 0)
        self.apply_routes()

    def apply_routes(self) -> None:
        """Use the table's routes now, if every row is valid; otherwise mark the bad row and keep the old ones."""
        t = self.routes_table
        routes = []
        bad = None
        for row in range(t.rowCount()):
            keys_text, to_text, note = (t.item(row, c).text().strip() if t.item(row, c) else "" for c in range(3))
            if not keys_text:
                continue  # an empty row (just added): ignored until it has keys
            try:
                keys = [k.strip() for k in keys_text.split(",") if k.strip()]
                for k in keys:
                    expand_key_spec(k)
                to_text = to_text or "active"
                to = [int(x) for x in to_text.replace(" ", "").split(",")] if to_text[0].isdigit() else to_text
                routes.append(RouteConfig(keys=keys, to=to, note=note))
            except Exception as e:
                bad = (row, str(e).splitlines()[-1] if str(e) else type(e).__name__)
                break
        t.blockSignals(True)
        for row in range(t.rowCount()):
            for c in range(3):
                if it := t.item(row, c):
                    it.setBackground(QColor("#ffcdd2") if bad and row == bad[0] else QColor(0, 0, 0, 0))
        t.blockSignals(False)
        if bad:
            self.routes_status.setText(f"<span style='color:#c62828'>Row {bad[0] + 1}: {bad[1]}. "
                                       f"Not applied: the previous routes are still in use.</span>")
            return
        if [r.model_dump() for r in routes] == [r.model_dump() for r in self.c.cfg.routes]:
            return
        self.c.set_routes(routes)
        self.routes_status.setText(f"Applied: {len(routes)} route(s) in use now. Save config to keep them "
                                   f"after a restart.")

    # ------------------------------------------------------------------ macros
    def _build_macros_tab(self) -> QWidget:
        self.macro_table = QTableWidget(0, 4)
        self.macro_table.setHorizontalHeaderLabels(["Macro", "Hotkey", "Status", "Steps"])
        self.macro_table.verticalHeader().setVisible(False)
        self.macro_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.macro_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.macro_table.horizontalHeader().setStretchLastSection(True)
        self.macro_table.cellDoubleClicked.connect(lambda row, _c: self._macro_edit(row))
        buttons = QHBoxLayout()
        row = self.macro_table.currentRow
        for text, fn in (("New", lambda: self._macro_edit(None)), ("Edit", lambda: self._macro_edit(row())),
                         ("Duplicate", lambda: self._macro_edit(row(), copy=True)),
                         ("Delete", lambda: self._macro_delete(row()))):
            buttons.addWidget(QPushButton(text, clicked=fn))
        buttons.addSpacing(20)
        buttons.addWidget(QPushButton("Run / Stop", clicked=lambda: self._macro_toggle(row())))
        buttons.addWidget(QPushButton("Stop all", clicked=self.c.macros.stop_all))
        buttons.addStretch(1)
        hint = QLabel("Double-click a macro to edit it. Press its hotkey in any WoW window to run it; press again "
                      "to stop it. Macros are saved in config/macros.yaml (you can edit that by hand too, then "
                      "Reload config).")
        hint.setWordWrap(True)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self.macro_table)
        lay.addLayout(buttons)
        lay.addWidget(hint)
        return w

    def load_macros(self) -> None:
        t = self.macro_table
        names = self.c.macros.saved()
        t.setRowCount(len(names))
        for r, name in enumerate(names):
            m, _steps = self.c.macros.macros[name]
            t.setItem(r, 0, _item(name))
            t.setItem(r, 1, _item(m.key or "-"))
            t.setItem(r, 2, _item(""))
            t.setItem(r, 3, _item(self.c.macros.describe(name)))

    def _macro_toggle(self, row: int) -> None:
        names = self.c.macros.saved()
        if 0 <= row < len(names):
            self.c.macros.toggle(names[row])

    def _macro_edit(self, row: int | None, copy: bool = False) -> None:
        from .macro_editor import MacroEditor
        names = self.c.macros.saved()
        macro = None
        if row is not None:
            if not 0 <= row < len(names):
                return
            macro = self.c.macros.macros[names[row]][0]
        source = macro.name if macro else None
        if macro and copy:
            new_name = f"{macro.name}-copy"
            while new_name in names:
                new_name += "-copy"
            macro = macro.model_copy(update={"name": new_name, "key": None})
        old_name = None if copy else source
        dlg = MacroEditor(self, macro, targets=self.c.target_choices(),
                          macro_names=[n for n in names if n != old_name],
                          conflict=lambda key: self.c.macro_hotkey_owner(key, ignore_macro=old_name),
                          on_test=self.c.macros.run_config)
        if copy:
            dlg.setWindowTitle(f"New macro (copy of {source})")
        self.c.router.hotkeys_paused = True  # so recording a hotkey doesn't also fire it
        try:
            accepted = dlg.exec()
        finally:
            self.c.router.hotkeys_paused = False
            self.c.macros.stop(self.c.macros.TEST)
        if accepted and dlg.result_macro:
            try:
                self.c.save_macro(old_name, dlg.result_macro)
            except Exception as e:
                QMessageBox.warning(self, "Macro not saved", str(e))

    def _macro_delete(self, row: int) -> None:
        names = self.c.macros.saved()
        if not 0 <= row < len(names):
            return
        if QMessageBox.question(self, "Delete macro", f"Delete the macro {names[row]!r}?") == QMessageBox.Yes:
            self.c.delete_macro(names[row])

    def refresh_macros(self) -> None:
        for r, name in enumerate(self.c.macros.saved()):
            it = self.macro_table.item(r, 2)
            text = "RUNNING" if self.c.macros.is_running(name) else ""
            if it and it.text() != text:
                it.setText(text)

    # ------------------------------------------------------------------ automation
    def _build_automation_tab(self) -> QWidget:
        hint = ("Double-click a rule to edit it; the On box enables or disables it, and a group's box all the "
                "rules it shows. Rules are saved in config/automation.yaml. Automation as a whole is switched on "
                "in the toolbar and starts in dry run (logs only). Kill switch hotkey: "
                + str(self.c.cfg.hotkeys.automation_kill) + ".")
        self.rule_list = RuleList(self.c, on_edit=self._rule_edit, on_delete=self._rule_delete, hint=hint)
        return self.rule_list

    def load_automation(self) -> None:
        self.rule_list.load()

    def _rule_edit(self, name: str | None, copy: bool = False) -> None:
        from .automation_editor import RuleEditor
        rules = [rt.rule for rt in self.c.engine.rules]
        names = [r.name for r in rules]
        rule = None
        if name is not None:
            if name not in names:
                return
            rule = rules[names.index(name)]
        source = rule.name if rule else None
        if rule and copy:
            new_name = f"{rule.name}-copy"
            while new_name in names:
                new_name += "-copy"
            rule = rule.model_copy(update={"name": new_name})
        old_name = None if copy else source
        dlg = RuleEditor(self, rule, targets=self.c.target_choices(), who_choices=self.c.who_choices(),
                         rule_names=[n for n in names if n != old_name], preview=self.c.rule_preview,
                         on_test=self.c.test_rule)
        if copy:
            dlg.setWindowTitle(f"New rule (copy of {source})")
        if old_name:
            self.c.engine.paused_rules.add(old_name)  # a half-edited rule must not fire
        try:
            accepted = dlg.exec()
        finally:
            self.c.engine.paused_rules.discard(old_name)
        if accepted and dlg.result_rule:
            try:
                self.c.save_rule(old_name, dlg.result_rule)
            except Exception as e:
                QMessageBox.warning(self, "Rule not saved", str(e))

    def _rule_delete(self, name: str) -> None:
        if name not in {rt.rule.name for rt in self.c.engine.rules}:
            return
        if QMessageBox.question(self, "Delete rule", f"Delete the rule {name!r}?") == QMessageBox.Yes:
            self.c.delete_rule(name)

    def refresh_automation(self) -> None:
        self.rule_list.refresh()

    def banner_lines(self) -> tuple[list[str], bool]:
        """What the banner says, and whether any of it is serious (rules not running)."""
        e, c = self.c.engine, self.c
        name = lambda s: c.team.clients[s].name if s in c.team.clients else str(s)  # noqa: E731
        lines, serious = [], False
        if e.enabled and e.blocked:
            stale = e.stale_slots()
            lines.append(f"<b>Automation: {len(e.blocked)} rule(s) PAUSED</b> ({', '.join(sorted(e.blocked))}). "
                         f"Can't read the bridge of {', '.join(name(s) for s in stale)} (minimized, covered, or "
                         "the addon is off), so rules that check or send to it wait. The other rules keep running.")
            serious = True
        if e.enabled and e.dry_run:
            lines.append("<b>Automation is in DRY RUN</b>: rules only log what they would do and send nothing. "
                         "Untick Dry run to make them act.")
            serious = True
        if held := c.router.waiting():
            lines.append("Waiting for you to stop typing or pressing keys: " + ", ".join(
                f"{name(s)} ({n} queued)" for s, n in sorted(held.items())) + ". It goes out when you stop.")
        stale = c.chat_filter_stale()
        none = [s for s in stale if c.team.clients[s].state.chat_filter == 0]
        if old := [s for s in stale if s not in none]:
            lines.append(f"<b>Chat filter out of date</b> on {', '.join(name(s) for s in old)}: your chat rules "
                         "changed, and BoxBridge loads the new word list on <b>/reload</b> (type "
                         "<code>/bb all reload</code>). Until then those clients pass on the old list's lines.")
        if none:
            lines.append(f"<b>No chat filter loaded</b> on {', '.join(name(s) for s in none)}, so they pass on every "
                         "chat line. If you just updated BoxBridge, <b>quit and restart</b> those WoW clients once: "
                         "WoW reads an addon's list of files only at start-up, so a /reload can't load the new "
                         "Words.lua. Otherwise copy the new addon/BoxBridge folder into the game.")
        if gone := c.missing_windows():
            lines.append(f"Window closed: {', '.join(name(s) for s in gone)}. Left out of broadcasting and rules; "
                         "Attach or Launch brings it back.")
        return lines, serious

    def refresh_banner(self) -> None:
        lines, serious = self.banner_lines()
        text = "<br>".join(lines)
        if text != self.banner.text():
            self.banner.setText(text)
            self.banner.setStyleSheet("padding: 6px; border-radius: 4px; " + (
                "background: #ffe0b2; color: #6d2c00; border: 1px solid #ef6c00;" if serious else
                "background: #eceff1; color: #37474f; border: 1px solid #b0bec5;"))
        self.banner.setVisible(bool(lines))

    # ------------------------------------------------------------------ periodic
    def _safe_refresh(self) -> None:
        """The 4-times-a-second refresh. A bug in it is logged once, not every 250 ms."""
        try:
            self.refresh()
            self._refresh_error = None
        except Exception as e:
            key = f"{type(e).__name__}: {e}"
            if key != getattr(self, "_refresh_error", None):
                self._refresh_error = key
                log.exception("window refresh failed (shown once until it changes)")

    def refresh(self) -> None:
        on = self.c.router.enabled
        self.b_broadcast.setText(f"Broadcast: {'ON' if on else 'OFF'}  [{self.c.cfg.hotkeys.toggle_broadcast}]")
        self.b_broadcast.setStyleSheet(f"background: {'#2e7d32' if on else '#c62828'}; color: white; font-weight: bold;")
        auto = self.c.engine.enabled
        self.b_auto.setText(f"Automation: {'ON' if auto else 'OFF'}")
        self.b_auto.setStyleSheet("background: #f9a825; font-weight: bold;" if auto else "")
        following = self.c.follow.active
        stacking = self.c.stack.running
        self.b_stack.setText("Stacking... (cancel)" if stacking else f"Stack [{self.c.cfg.hotkeys.stack}]")
        self.b_stack.setStyleSheet("background: #6a1b9a; color: white; font-weight: bold;" if stacking else "")
        self.b_follow.setText(f"Follow: {'ON' if following else 'OFF'}")
        self.b_follow.setStyleSheet("background: #1565c0; color: white; font-weight: bold;" if following else "")
        for cb, value in ((self.cb_overlay, self.c.overlay.isVisible()), (self.cb_dry, self.c.engine.dry_run),
                          (self.cb_mirror, self.c.router.mirror)):
            if cb.isChecked() != value:
                cb.blockSignals(True)
                cb.setChecked(value)
                cb.blockSignals(False)
        if self.layout_combo.currentText() != self.c.layout.current:
            self.layout_combo.setCurrentText(self.c.layout.current)
        self.refresh_banner()
        self.refresh_clients()
        self.refresh_automation()
        if self.tabs.currentWidget() is self.activity_tab:
            self.activity_tab.refresh()
        self.refresh_macros()
        self.status.setText(self.c.status_text())

    def quit_app(self) -> None:
        self._quitting = True
        self.close()

    def closeEvent(self, event) -> None:
        self.c.shutdown()
        event.accept()
        QApplication.instance().quit()
