"""The Activity tab: a live dashboard of what rules and macros are doing.

- Characters: each one's state and the last thing a rule or macro sent it.
- Live queue: per character, what's running (rules and macros), waiting (and why) and queued.
- Recent activity: every firing, step, queued request and skip, newest first.
"""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..automation.actions import SendStep, TypeStep, describe
from ..automation.engine import QueueItem, fmt_left
from .settings_tab import summary

CHAR_COLS = ["Character", "State", "Queue", "Last action", "By", "When"]
QUEUE_COLS = ["Character", "State", "Rule / macro", "For", "Detail"]
FEED_COLS = ["Ago", "Time", "Source", "Name", "What", "Characters"]
QUEUE_COLORS = {"running": "#2e7d32", "macro": "#6a1b9a", "waiting": "#ef6c00", "queued": "#1565c0"}
FEED_COLORS = {"fired": "#2e7d32", "queued": "#1565c0", "skipped": "#c62828", "done": "#757575"}
ROW_HEIGHT = 22
# The feed and queue size their columns once per rebuild, not live (ResizeToContents): live sizing re-measures a
# whole column on every cell written, so a full 300-row feed took 10-30 s to rebuild and froze WoWBox.
AGO_SAMPLE = "59m59s ago"
RANK = {"running": 0, "macro": 0, "waiting": 1, "queued": 2}


def _item(text: str, color: str | None = None, tip: str | None = None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if color:
        it.setForeground(QColor(color))
    it.setToolTip(tip or text)
    return it


class ActivityTab(QWidget):
    def __init__(self, controller, on_settings=lambda: None) -> None:
        super().__init__()
        self.c = controller
        self._queue_sig = None
        self._feed_sig = None
        self._feed_rows: list = []  # the Activity shown in each feed row, for the live Ago column

        # characters
        self.chars = QTableWidget(0, len(CHAR_COLS))
        self.chars.setHorizontalHeaderLabels(CHAR_COLS)
        self.chars.verticalHeader().setVisible(False)
        self.chars.setEditTriggers(QTableWidget.NoEditTriggers)
        ch = self.chars.horizontalHeader()
        ch.setSectionResizeMode(QHeaderView.ResizeToContents)
        ch.setSectionResizeMode(CHAR_COLS.index("Last action"), QHeaderView.Stretch)
        self.chars.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.chars.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # live queue
        self.queue = QTreeWidget()
        self.queue.setHeaderLabels(QUEUE_COLS)
        self.queue.setRootIsDecorated(False)
        self.queue.setUniformRowHeights(True)
        qh = self.queue.header()
        qh.setSectionResizeMode(QHeaderView.Interactive)
        qh.setSectionResizeMode(QUEUE_COLS.index("Detail"), QHeaderView.Stretch)

        # feed
        self.feed = QTableWidget(0, len(FEED_COLS))
        self.feed.setHorizontalHeaderLabels(FEED_COLS)
        self.feed.verticalHeader().setVisible(False)
        self.feed.setEditTriggers(QTableWidget.NoEditTriggers)
        self.feed.setWordWrap(False)
        self.feed.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        fh = self.feed.horizontalHeader()
        fh.setSectionResizeMode(QHeaderView.Interactive)
        fh.setSectionResizeMode(FEED_COLS.index("What"), QHeaderView.Stretch)
        self.show_rules = QCheckBox("Rules", checked=True)
        self.show_macros = QCheckBox("Macros", checked=True)
        self.show_bb = QCheckBox("/bb", checked=True)
        self.show_steps = QCheckBox("Each step", checked=False)
        self.show_queue = QCheckBox("Queue and skips", checked=True)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("<b>What ran</b> (newest first) · show:"))
        for cb in (self.show_rules, self.show_macros, self.show_bb, self.show_steps, self.show_queue):
            cb.toggled.connect(lambda _: self.refresh(force=True))
            filters.addWidget(cb)
        filters.addStretch(1)
        clear = QPushButton("Clear", clicked=self._clear)
        filters.addWidget(clear)

        def section(title: str, *widgets) -> QWidget:
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            if isinstance(title, str):
                lay.addWidget(QLabel(title))
            else:
                lay.addLayout(title)
            for x in widgets:
                lay.addWidget(x)
            return w

        # characters: always exactly as tall as its rows. Below it, side by side: now | what ran
        split = QSplitter(Qt.Horizontal)
        split.addWidget(section("<b>Now</b> · per character, in the order things will go: running (rules and "
                                "macros), waiting (for a cast, the global cooldown or a rule with priority), "
                                "queued chat requests", self.queue))
        split.addWidget(section(filters, self.feed))
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.linkActivated.connect(lambda _: on_settings())
        lay = QVBoxLayout(self)
        lay.addWidget(self.summary)
        lay.addWidget(QLabel("<b>Characters</b> · state, and the last thing a rule or macro sent each one"))
        lay.addWidget(self.chars)
        lay.addWidget(split, 1)

    # ------------------------------------------------------------------ helpers
    def _name(self, slot: int) -> str:
        c = self.c.team.clients.get(slot)
        if c is None:
            return str(slot)
        return c.character or (c.state.name if c.state and c.state.name else c.name)

    def _clear(self) -> None:
        self.c.activity.clear()
        self.refresh(force=True)

    def queue_items(self, now: float) -> list[QueueItem]:
        """The engine's view plus running macros and typed lines waiting for you."""
        c = self.c
        items = c.engine.queue_view(now)
        for name, (i, steps, started) in list(c.macros.progress.items()):
            nxt = next((s for s in steps[i:] if isinstance(s, (TypeStep, SendStep))), None)
            slots = sorted(c.team.resolve(nxt.to, c.router.active_slot())) if nxt else []
            what = f"step {min(i + 1, len(steps))}/{len(steps)}" + (f": {describe([nxt])}" if nxt else "")
            items.append(QueueItem(slots, name, "macro", "", f"{what} · running {fmt_left(now - started)}"))
        for s, n in c.router.waiting().items():
            items.append(QueueItem([s], "(typed line)", "waiting", "",
                                   f"{n} waiting for you to stop typing / pressing keys there"))
        return items

    # ------------------------------------------------------------------ refresh
    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        text = (f"<b>In play:</b> {summary(self.c.cfg)} · <a href='settings'>change in Settings</a>"
                + ("" if self.c.engine.enabled else " · <b>automation is OFF</b>"))
        if self.summary.text() != text:
            self.summary.setText(text)
        items = self.queue_items(now)
        self._refresh_chars(items, now)
        self._refresh_queue(items, force)
        self._refresh_feed(force)

    def _refresh_chars(self, items: list[QueueItem], now: float) -> None:
        c = self.c
        clients = [cl for cl in c.team.clients.values() if cl.attached]
        counts: dict[int, dict[str, int]] = {}
        for it in items:
            for s in it.slots:
                k = "running" if it.kind == "macro" else it.kind
                counts.setdefault(s, {}).setdefault(k, 0)
                counts[s][k] += 1
        t = self.chars
        if t.rowCount() != len(clients):
            t.setRowCount(len(clients))
            t.setFixedHeight(t.horizontalHeader().height() + ROW_HEIGHT * max(1, len(clients)) + 4)
        for r, cl in enumerate(sorted(clients, key=lambda x: x.slot)):
            st = cl.state
            if st is None or st.stale:
                state, color = "no bridge data", "#9e9e9e"
            elif st.dead or st.ghost:
                state, color = "DEAD" if st.dead else "ghost", "#c62828"
            elif st.casting or st.channeling:
                state, color = "casting" if st.casting else "channeling", "#ef6c00"
            elif st.typing or st.chat_open:
                state, color = "typing", "#1565c0"
            else:
                state, color = "ok", "#2e7d32"
            if st is not None and not st.stale and not (st.dead or st.ghost):
                state += f" · HP {st.health_pct:.0f}% · {st.power_name or 'power'} {st.power_pct:.0f}%"
            n = counts.get(cl.slot, {})
            queue = " · ".join(f"{v} {k}" for k, v in n.items()) or "-"
            last = c.activity.last_for(cl.slot)
            cells = [f"{cl.slot} {self._name(cl.slot)}", state, queue,
                     last.text if last else "-", f"{last.source} {last.name}" if last else "",
                     f"{fmt_left(now - last.mono)} ago" if last else ""]
            for col, text in enumerate(cells):
                it = t.item(r, col)
                if it is None or it.text() != text:
                    t.setItem(r, col, _item(text, color if col == 1 else None))

    def _refresh_queue(self, items: list[QueueItem], force: bool) -> None:
        rows: dict[int, list[QueueItem]] = {}
        for it in items:
            for s in it.slots or [0]:
                rows.setdefault(s, []).append(it)
        lines = []
        for s in sorted(rows):
            group = sorted(rows[s], key=lambda i: (RANK[i.kind], not i.first, -i.priority, i.order))
            for k, it in enumerate(group):
                lines.append((self._name(s) if s and k == 0 else "" if s else "(no character)", it))
        sig = [(who, it.rule, it.kind, it.who, it.detail) for who, it in lines]
        if sig == self._queue_sig and not force:
            return
        self._queue_sig = sig
        t = self.queue
        t.setUpdatesEnabled(False)
        t.clear()
        for who, it in lines:
            prio = f" (priority {it.priority})" if it.priority else ""
            row = QTreeWidgetItem([who, it.kind, it.rule + prio, it.who, it.detail])
            row.setForeground(QUEUE_COLS.index("State"), QColor(QUEUE_COLORS[it.kind]))
            row.setToolTip(QUEUE_COLS.index("Detail"), it.detail)
            if who:
                f = row.font(0)
                f.setBold(True)
                row.setFont(0, f)
            t.addTopLevelItem(row)
        if not lines:
            empty = QTreeWidgetItem(["", "", "Nothing running or queued."])
            empty.setForeground(2, QColor("#9e9e9e"))
            t.addTopLevelItem(empty)
        for col in range(len(QUEUE_COLS)):
            if col != QUEUE_COLS.index("Detail"):
                t.resizeColumnToContents(col)
        t.setUpdatesEnabled(True)

    def _shown(self, a) -> bool:
        if a.source == "rule" and not self.show_rules.isChecked():
            return False
        if a.source == "macro" and not self.show_macros.isChecked():
            return False
        if a.source == "/bb" and not self.show_bb.isChecked():
            return False
        if a.kind == "step" and not self.show_steps.isChecked():
            return False
        if a.kind in ("queued", "skipped") and not self.show_queue.isChecked():
            return False
        return True

    def _refresh_feed(self, force: bool) -> None:
        feed = self.c.activity
        t = self.feed
        now = time.monotonic()
        if feed.version != self._feed_sig or force:
            self._feed_sig = feed.version
            items = [a for a in reversed(feed.items()) if self._shown(a)]
            self._feed_rows = items
            t.setUpdatesEnabled(False)
            t.setRowCount(len(items))
            name_col, what_col = FEED_COLS.index("Name"), FEED_COLS.index("What")
            for r, a in enumerate(items):
                color = FEED_COLORS.get(a.kind)
                cells = ["", datetime.fromtimestamp(a.at).strftime("%H:%M:%S"), a.source, a.name, a.text,
                         ", ".join(self._name(s) for s in a.slots)]
                for col, text in enumerate(cells):
                    t.setItem(r, col, _item(text, color if col in (name_col, what_col) else None))
            for col in range(len(FEED_COLS)):
                if col != what_col:
                    t.resizeColumnToContents(col)
            ago = t.fontMetrics().horizontalAdvance(AGO_SAMPLE) + 16  # room for the Ago text to grow
            t.setColumnWidth(0, max(t.columnWidth(0), ago))
            t.setUpdatesEnabled(True)
        for r, a in enumerate(self._feed_rows):  # the Ago column counts up live
            text = f"{fmt_left(now - a.mono)} ago"
            it = t.item(r, 0)
            if it is not None and it.text() != text:
                it.setText(text)
