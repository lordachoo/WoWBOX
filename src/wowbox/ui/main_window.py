"""Control panel."""
from __future__ import annotations

import logging
import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..config import RouteConfig
from ..keys import expand_key_spec
from ..layout import LAYOUT_ORDER

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
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_clients_tab(), "Clients")
        self.tabs.addTab(self._build_routes_tab(), "Routes")
        self.tabs.addTab(self._build_macros_tab(), "Macros")
        self.tabs.addTab(self._build_automation_tab(), "Automation")
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setFont(QFont("Consolas", 9))
        self.tabs.addTab(self.log_view, "Log")
        root.addWidget(self.tabs)
        self.setCentralWidget(central)
        self.status = QLabel()
        self.statusBar().addPermanentWidget(self.status, 1)

        self.c.sig_log.connect(self.log_view.appendPlainText)
        self.timer = QTimer(self, timeout=self.refresh, interval=250)
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
                "Position": (f"{st.map_x * 100:.2f}, {st.map_y * 100:.2f}  {math.degrees(st.facing):.0f}°  {st.fps} fps"
                             if st and st.position_ok and not st.stale else ""),
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
                         ("Down", lambda: self._route_move(1)), ("Apply", self.apply_routes),
                         ("Revert", self.load_routes)):
            buttons.addWidget(QPushButton(text, clicked=fn))
        buttons.addStretch(1)
        hint = QLabel("First matching route wins. A chord matches exactly: list Shift+1 separately from 1. "
                      "Keys not listed go only to the window you're in. Apply = live; Save config = write team.yaml.")
        hint.setWordWrap(True)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self.routes_table)
        lay.addLayout(buttons)
        lay.addWidget(hint)
        return w

    def load_routes(self) -> None:
        t = self.routes_table
        t.setRowCount(0)
        for r in self.c.cfg.routes:
            row = t.rowCount()
            t.insertRow(row)
            to = r.to if isinstance(r.to, str) else ",".join(map(str, r.to))
            for col, text in enumerate((", ".join(r.keys), to, r.note)):
                t.setItem(row, col, _item(text, editable=True))

    def _route_add(self) -> None:
        row = self.routes_table.rowCount()
        self.routes_table.insertRow(row)
        for col, text in enumerate(("", "all", "")):
            self.routes_table.setItem(row, col, _item(text, editable=True))
        self.routes_table.editItem(self.routes_table.item(row, 0))

    def _route_remove(self) -> None:
        rows = sorted({i.row() for i in self.routes_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.routes_table.removeRow(r)

    def _route_move(self, delta: int) -> None:
        t = self.routes_table
        r = t.currentRow()
        if r < 0 or not 0 <= r + delta < t.rowCount():
            return
        texts = [[t.item(row, c).text() if t.item(row, c) else "" for c in range(3)] for row in (r, r + delta)]
        for row, vals in zip((r + delta, r), texts):
            for c, text in enumerate(vals):
                t.setItem(row, c, _item(text, editable=True))
        t.setCurrentCell(r + delta, 0)

    def apply_routes(self) -> None:
        t = self.routes_table
        routes = []
        try:
            for row in range(t.rowCount()):
                keys_text, to_text, note = (t.item(row, c).text().strip() if t.item(row, c) else "" for c in range(3))
                if not keys_text:
                    continue
                keys = [k.strip() for k in keys_text.split(",") if k.strip()]
                for k in keys:
                    expand_key_spec(k)
                to_text = to_text or "active"
                to = [int(x) for x in to_text.replace(" ", "").split(",")] if to_text[0].isdigit() else to_text
                routes.append(RouteConfig(keys=keys, to=to, note=note))
        except Exception as e:
            QMessageBox.warning(self, "Invalid route", f"Row {row + 1}: {e}")
            return
        self.c.set_routes(routes)

    # ------------------------------------------------------------------ macros
    def _build_macros_tab(self) -> QWidget:
        self.macro_table = QTableWidget(0, 4)
        self.macro_table.setHorizontalHeaderLabels(["Macro", "Hotkey", "Status", "Steps"])
        self.macro_table.verticalHeader().setVisible(False)
        self.macro_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.macro_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.macro_table.horizontalHeader().setStretchLastSection(True)
        self.macro_table.cellDoubleClicked.connect(lambda row, _c: self._macro_toggle(row))
        buttons = QHBoxLayout()
        buttons.addWidget(QPushButton("Run / Stop", clicked=lambda: self._macro_toggle(self.macro_table.currentRow())))
        buttons.addWidget(QPushButton("Stop all", clicked=self.c.macros.stop_all))
        buttons.addStretch(1)
        hint = QLabel("Macros live in team.yaml (macros:). Edit there, then Reload config. Press a macro's hotkey in "
                      "any WoW window (or double-click it here) to run it; press again to stop it. Targets can be "
                      "a slot list, all/followers/leader, a character name, class:WARLOCK or role:healer.")
        hint.setWordWrap(True)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self.macro_table)
        lay.addLayout(buttons)
        lay.addWidget(hint)
        return w

    def load_macros(self) -> None:
        t = self.macro_table
        names = list(self.c.macros.macros)
        t.setRowCount(len(names))
        for r, name in enumerate(names):
            m, _steps = self.c.macros.macros[name]
            t.setItem(r, 0, _item(name))
            t.setItem(r, 1, _item(m.key or "-"))
            t.setItem(r, 2, _item(""))
            t.setItem(r, 3, _item(self.c.macros.describe(name)))

    def _macro_toggle(self, row: int) -> None:
        names = list(self.c.macros.macros)
        if 0 <= row < len(names):
            self.c.macros.toggle(names[row])

    def refresh_macros(self) -> None:
        for r, name in enumerate(self.c.macros.macros):
            it = self.macro_table.item(r, 2)
            text = "RUNNING" if self.c.macros.is_running(name) else ""
            if it and it.text() != text:
                it.setText(text)

    # ------------------------------------------------------------------ automation
    def _build_automation_tab(self) -> QWidget:
        self.auto_table = QTableWidget(0, 5)
        self.auto_table.setHorizontalHeaderLabels(["On", "Name", "Trigger", "Actions", "Fired"])
        self.auto_table.verticalHeader().setVisible(False)
        self.auto_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.auto_table.horizontalHeader().setStretchLastSection(True)
        self.auto_table.itemChanged.connect(self._auto_item_changed)
        self.auto_status = QLabel()
        hint = QLabel("Rules live in team.yaml (automation:). Edit there, then Reload config. Dry run logs what "
                      f"would be sent without sending. Kill switch hotkey: {self.c.cfg.hotkeys.automation_kill}.")
        hint.setWordWrap(True)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self.auto_status)
        lay.addWidget(self.auto_table)
        lay.addWidget(hint)
        return w

    def load_automation(self) -> None:
        from ..automation.actions import describe
        t = self.auto_table
        t.blockSignals(True)
        t.setRowCount(len(self.c.engine.rules))
        for r, rt in enumerate(self.c.engine.rules):
            on = _item("")
            on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            on.setCheckState(Qt.Checked if rt.rule.enabled else Qt.Unchecked)
            t.setItem(r, 0, on)
            t.setItem(r, 1, _item(rt.rule.name))
            if rt.rule.chat is not None:
                ch = rt.rule.chat
                trig = f"chat {'/'.join(ch.channel)}"
                if ch.sender:
                    trig += f" from {', '.join(ch.sender)}"
                if ch.contains:
                    trig += f" containing {' or '.join(repr(w) for w in ch.contains)}"
                trig += f" heard by {ch.heard_by}"
                if rt.rule.when is not None:
                    trig += " when " + ", ".join(f"{k}={v}" for k, v in rt.rule.when.model_dump().items())
            elif rt.rule.every is not None:
                trig = f"every {rt.rule.every:g}s"
            else:
                trig = "when " + ", ".join(f"{k}={v}" for k, v in rt.rule.when.model_dump().items())
            if rt.rule.cooldown:
                trig += f" (cooldown {rt.rule.cooldown:g}s)"
            t.setItem(r, 2, _item(trig))
            t.setItem(r, 3, _item(describe(rt.steps)))
            t.setItem(r, 4, _item("0"))
        t.blockSignals(False)

    def _auto_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0 and item.row() < len(self.c.engine.rules):
            self.c.engine.rules[item.row()].rule.enabled = item.checkState() == Qt.Checked

    def refresh_automation(self) -> None:
        e = self.c.engine
        for r, rt in enumerate(e.rules):
            it = self.auto_table.item(r, 4)
            if it and it.text() != str(rt.fire_count):
                it.setText(str(rt.fire_count))
        state = "ON" if e.enabled else "OFF"
        extra = " (dry run)" if e.dry_run else ""
        paused = f" - PAUSED: {e.paused_reason}" if e.paused_reason else ""
        self.auto_status.setText(f"Automation {state}{extra}{paused}")

    # ------------------------------------------------------------------ periodic
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
        self.refresh_clients()
        self.refresh_automation()
        self.refresh_macros()
        self.status.setText(self.c.status_text())

    def quit_app(self) -> None:
        self._quitting = True
        self.close()

    def closeEvent(self, event) -> None:
        self.c.shutdown()
        event.accept()
        QApplication.instance().quit()
