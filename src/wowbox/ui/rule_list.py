"""The Automation tab's rule list: rules grouped (by who acts, trigger or on/off) under collapsible headers,
with a search box. A group's checkbox turns all the rules it shows on or off."""
from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QProgressBar, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..automation.grouping import GROUP_BY, group_for, matches
from ..macro_store import fmt_duration

AUTO_COLS = ["On", "Name", "Trigger", "Actions", "Status", "Next", "Last fired", "Fired"]
RULE = Qt.UserRole  # a rule row's rule name
GROUP = Qt.UserRole + 1  # a group header's key


def trigger_text(rule) -> str:
    if rule.chat is not None:
        ch = rule.chat
        trig = f"chat {'/'.join(ch.channel)}"
        if ch.sender:
            trig += f" from {', '.join(ch.sender)}"
        if ch.contains:
            trig += f" containing {' or '.join(repr(w) for w in ch.contains)}"
        trig += f" heard by {ch.heard_by}"
        if rule.when is not None:
            trig += " when " + ", ".join(f"{k}={v}" for k, v in rule.when.model_dump().items())
    elif rule.every is not None:
        trig = f"every {fmt_duration(rule.every)}"
    else:
        trig = "when " + ", ".join(f"{k}={v}" for k, v in rule.when.model_dump().items())
    if rule.cooldown:
        trig += f" (cooldown {fmt_duration(rule.cooldown)})"
    if rule.hotkey:
        trig += f" [on/off: {rule.hotkey}]"
    return trig


class RuleList(QWidget):
    def __init__(self, controller, on_edit: Callable[[str | None, bool], None], on_delete: Callable[[str], None],
                 hint: str = "", settings: QSettings | None = None) -> None:
        super().__init__()
        self.c = controller
        self.settings = settings or QSettings("WoWBox", "WoWBox")
        self.items: dict[str, QTreeWidgetItem] = {}  # rule name -> its row
        self.groups: list[QTreeWidgetItem] = []
        self._members_seen = None
        self._restoring = False

        self.search = QLineEdit(placeholderText="Search: name, keyword, spell, sender…", clearButtonEnabled=True)
        self.search.textChanged.connect(self.apply_filter)
        self.group_by = QComboBox()
        self.group_by.addItems(GROUP_BY)
        saved = self.settings.value("automation/group_by", GROUP_BY[0])
        self.group_by.setCurrentText(saved if saved in GROUP_BY else GROUP_BY[0])
        self.group_by.currentTextChanged.connect(self._group_by_changed)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(QLabel("Group by"))
        top.addWidget(self.group_by)

        t = self.tree = QTreeWidget()
        t.setColumnCount(len(AUTO_COLS))
        t.setHeaderLabels(AUTO_COLS)
        hh = t.header()
        # Fixed widths, not ResizeToContents: that re-measures the whole column each time a cell changes, and
        # Status / Last fired change every refresh (0.4 s a refresh with ~80 rules, 4 refreshes a second).
        hh.setSectionResizeMode(QHeaderView.Interactive)
        for col, width in (("Name", 220), ("Trigger", 300), ("Actions", 260), ("Status", 170),
                           ("Next", 230), ("Last fired", 90), ("Fired", 55)):  # long text: cut with ...
            hh.setSectionResizeMode(AUTO_COLS.index(col), QHeaderView.Interactive)
            t.setColumnWidth(AUTO_COLS.index(col), width)
        hh.setSectionResizeMode(AUTO_COLS.index("Actions"), QHeaderView.Stretch)
        hh.setStretchLastSection(False)
        t.setTextElideMode(Qt.ElideRight)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setUniformRowHeights(True)
        t.itemChanged.connect(self._item_changed)
        t.itemDoubleClicked.connect(lambda it, col: col and it.data(0, RULE) and on_edit(it.data(0, RULE), False))
        t.itemExpanded.connect(self._save_collapsed)
        t.itemCollapsed.connect(self._save_collapsed)

        self.status = QLabel()
        buttons = QHBoxLayout()
        for text, fn in (("New", lambda: on_edit(None, False)),
                         ("Edit", lambda: (n := self.current_rule()) and on_edit(n, False)),
                         ("Duplicate", lambda: (n := self.current_rule()) and on_edit(n, True)),
                         ("Delete", lambda: (n := self.current_rule()) and on_delete(n))):
            buttons.addWidget(QPushButton(text, clicked=fn))
        buttons.addStretch(1)
        for text, fn in (("Expand all", lambda: self._expand_all(True)),
                         ("Collapse all", lambda: self._expand_all(False))):
            buttons.addWidget(QPushButton(text, clicked=fn))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.status)
        lay.addLayout(top)
        lay.addWidget(t)
        lay.addLayout(buttons)
        if hint:
            h = QLabel(hint, wordWrap=True)
            lay.addWidget(h)

    # ------------------------------------------------------------------ building
    def current_rule(self) -> str | None:
        it = self.tree.currentItem()
        return it.data(0, RULE) if it else None

    def _collapsed(self) -> set[str]:
        v = self.settings.value("automation/collapsed", [])
        return set([v] if isinstance(v, str) else v or [])

    def _mode(self) -> str:
        return self.group_by.currentText()

    def load(self) -> None:
        from ..automation.actions import describe
        t = self.tree
        current = self.current_rule()
        members, leader = self.c.rule_members()
        self._members_seen = members
        by = self._mode()
        t.blockSignals(True)
        self._restoring = True
        t.clear()
        self.items, self.groups = {}, []
        heads: dict[str, QTreeWidgetItem] = {}
        placed = []
        for rt in self.c.engine.rules:
            g = group_for(by, rt.rule, rt.steps, members, leader)
            placed.append((g, rt))
        bold = QFont()
        bold.setBold(True)
        for g in sorted({g.key: g for g, _ in placed if g}.values(), key=lambda g: g.order + (g.title,)):
            head = QTreeWidgetItem()
            head.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            head.setData(0, GROUP, g.key)
            head.setData(0, Qt.UserRole + 2, g.title)
            head.setFont(0, bold)
            head.setCheckState(0, Qt.Unchecked)
            head.setToolTip(0, "Tick or untick to turn every rule shown in this group on or off.")
            t.addTopLevelItem(head)
            head.setFirstColumnSpanned(True)
            heads[g.key] = head
            self.groups.append(head)
        for g, rt in placed:
            r = rt.rule
            it = QTreeWidgetItem()
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if r.enabled else Qt.Unchecked)
            it.setData(0, RULE, r.name)
            trig, acts = trigger_text(r), describe(rt.steps)
            for col, text in (("Name", r.name), ("Trigger", trig), ("Actions", acts)):
                it.setText(AUTO_COLS.index(col), text)
                it.setToolTip(AUTO_COLS.index(col), text)  # the column cuts long text; hover shows all of it
            it.setData(1, Qt.UserRole + 2, " ".join((r.name, trig, acts)))
            (heads[g.key].addChild if g else t.addTopLevelItem)(it)
            self.items[r.name] = it
            if r.every is not None:  # timers get a countdown bar
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setTextVisible(True)
                t.setItemWidget(it, AUTO_COLS.index("Next"), bar)
        collapsed = self._collapsed()
        for head in self.groups:
            head.setExpanded(f"{by}|{head.data(0, GROUP)}" not in collapsed)
        if current in self.items:
            t.setCurrentItem(self.items[current])
        t.resizeColumnToContents(AUTO_COLS.index("On"))  # the rules' checkboxes sit indented under their group
        self._restoring = False
        t.blockSignals(False)
        self.apply_filter()
        self.refresh()

    def _group_by_changed(self, by: str) -> None:
        self.settings.setValue("automation/group_by", by)
        self.load()

    def _update_heads(self) -> None:
        """Each header's text (how many rules, how many on) and checkbox (all on / none / some)."""
        self.tree.blockSignals(True)
        for head in self.groups:
            kids = [head.child(i) for i in range(head.childCount())]
            shown = [k for k in kids if not k.isHidden()]
            on = sum(k.checkState(0) == Qt.Checked for k in shown)
            count = (f"{len(shown)} of {len(kids)} rules shown" if len(shown) != len(kids)
                     else f"{len(kids)} rule{'s' if len(kids) != 1 else ''}")
            head.setText(0, f"{head.data(0, Qt.UserRole + 2)}    {count}, {on} on")
            head.setCheckState(0, Qt.Checked if shown and on == len(shown) else
                               Qt.Unchecked if on == 0 else Qt.PartiallyChecked)
        self.tree.blockSignals(False)

    # ------------------------------------------------------------------ search
    def apply_filter(self) -> None:
        q = self.search.text().strip()
        for it in self.items.values():
            it.setHidden(bool(q) and not matches(q, it.data(1, Qt.UserRole + 2)))
        self._restoring = True
        collapsed = self._collapsed()
        for head in self.groups:
            kids = [head.child(i) for i in range(head.childCount())]
            head.setHidden(not any(not k.isHidden() for k in kids))
            head.setExpanded(bool(q) or f"{self._mode()}|{head.data(0, GROUP)}" not in collapsed)  # search: open
        self._restoring = False
        self._update_heads()

    def _expand_all(self, on: bool) -> None:
        self._restoring = True
        for head in self.groups:
            head.setExpanded(on)
        self._restoring = False
        self._save_collapsed()

    def _save_collapsed(self, *_):
        if self._restoring or self.search.text().strip():  # search opens groups for you; don't remember that
            return
        mode = self._mode()
        keep = {k for k in self._collapsed() if not k.startswith(mode + "|")}
        keep |= {f"{mode}|{h.data(0, GROUP)}" for h in self.groups if not h.isExpanded()}
        self.settings.setValue("automation/collapsed", sorted(keep))

    # ------------------------------------------------------------------ on/off
    def _item_changed(self, item: QTreeWidgetItem, col: int) -> None:
        if col != 0:
            return
        on = item.checkState(0) == Qt.Checked
        rules = {rt.rule.name: rt.rule for rt in self.c.engine.rules}
        if (name := item.data(0, RULE)) is not None:
            names = [name] if name in rules and rules[name].enabled != on else []
        elif item.data(0, GROUP) is not None:
            names = [n for i in range(item.childCount()) if not (k := item.child(i)).isHidden()
                     and (n := k.data(0, RULE)) in rules and rules[n].enabled != on]
        else:
            return
        if names:
            QTimer.singleShot(0, lambda: self.c.set_rules_enabled(names, on))  # it reloads the list: not mid-signal
        else:
            self._update_heads()

    # ------------------------------------------------------------------ live columns
    def refresh(self) -> None:
        e = self.c.engine
        if self.c.rule_members()[0] != self._members_seen:  # a client attached, or its class became known
            self.load()
            return
        now = time.monotonic()
        nxt = AUTO_COLS.index("Next")
        for rt in e.rules:
            it = self.items.get(rt.rule.name)
            if it is None:
                continue
            st = e.status(rt, now)
            for col, text in (("Status", st.state), ("Last fired", st.last), ("Fired", str(rt.fire_count))):
                if it.text(AUTO_COLS.index(col)) != text:
                    it.setText(AUTO_COLS.index(col), text)
            if isinstance(bar := self.tree.itemWidget(it, nxt), QProgressBar):
                bar.setValue(int((st.progress or 0) * 1000))
                bar.setFormat(st.next or st.state)  # e.g. "disabled"
            elif it.text(nxt) != st.next:
                it.setText(nxt, st.next)
            color = ("#9e9e9e" if st.state in ("disabled", "automation OFF") or st.state.startswith("paused")
                     else "#ef6c00" if st.state.startswith(("cooldown", "running")) else
                     "#2e7d32" if st.ready or st.state == "waiting" else None)
            it.setData(AUTO_COLS.index("Status"), Qt.ForegroundRole, QBrush(QColor(color)) if color else None)
        state = "ON" if e.enabled else "OFF"
        extra = " (dry run)" if e.dry_run else ""
        paused = f" - PAUSED: {e.paused_reason}" if e.paused_reason else ""
        self.status.setText(f"Automation {state}{extra}{paused}")
