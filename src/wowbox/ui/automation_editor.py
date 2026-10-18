"""The automation rule editor: name, trigger (state / timer / chat / chat + state), steps, live check."""
from __future__ import annotations

import re
from typing import Callable

from pydantic import ValidationError
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSpinBox, QTableWidget, QVBoxLayout, QWidget,
)

from ..config import CHAT_CHANNELS, AutomationRule
from ..macro_store import fmt_duration
from .macro_editor import TO_HINT, HotkeyEdit, StepTable, explain, macro_rows, rows_to_do, target_text

# (field, label, kind) for the condition table; kind decides the comparisons and values offered
FIELDS = [
    ("health_pct", "health %", "number"),
    ("power_pct", "power % (mana, rage, energy...)", "number"),
    ("target_health_pct", "target's health %", "number"),
    ("combat", "in combat", "bool"),
    ("casting", "casting", "bool"),
    ("channeling", "channeling", "bool"),
    ("mounted", "mounted", "bool"),
    ("dead", "dead", "bool"),
    ("ghost", "ghost", "bool"),
    ("following", "on /follow", "bool"),
    ("resting", "resting (inn / city)", "bool"),
    ("afk", "AFK", "bool"),
    ("typing", "typing in chat", "bool"),
    ("target_exists", "has a target", "bool"),
    ("target_hostile", "target is hostile", "bool"),
    ("target_dead", "target is dead", "bool"),
    ("target_is_player", "target is a player", "bool"),
    ("target_in_combat", "target in combat", "bool"),
    ("target_range", "target's distance, yards (10, 28 or 100 = farther; measured by the game)", "number"),
    ("cant_hit", "can't hit the target: facing the wrong way / too far (last 3 s)", "bool"),
    ("attacking", "auto-attack is on", "bool"),
    ("in_party", "in a party", "bool"),
    ("in_raid", "in a raid", "bool"),
    ("level", "level", "number"),
    ("power_name", "power type (mana, rage, energy, focus, runic)", "text"),
    ("class_token", "class (e.g. WARLOCK)", "text"),
    ("position_ok", "has map coordinates", "bool"),
    ("fps", "frames per second", "number"),
    ("ready", "spell ready (can be cast now; = a spell name, e.g. Avenger's Shield)", "text"),
    ("dot", "DoT needed (castable, and not on the target from you; = a spell name, e.g. Corruption)", "text"),
    ("same_target", "same target as (= a character name or slot, e.g. Tinkerdawn)", "text"),
    ("ally_attacking", "auto-attack is on for (= a character name or slot, e.g. Tinkerdawn)", "text"),
]
FIELD_KIND = {f: k for f, _, k in FIELDS}
OPS = [("=", ""), ("≠", "_ne"), ("<", "_lt"), ("≤", "_lte"), (">", "_gt"), ("≥", "_gte")]
OP_SUFFIX = dict(OPS)
TRIGGERS = [("state", "State: when a condition is true"), ("every", "Timer: every so often"),
            ("chat", "Chat: when a chat line comes in"), ("chat+state", "Chat + state: a chat line, if a condition is true")]
CHANNELS = list(CHAT_CHANNELS) + ["any"]
PLACEHOLDER_HINT = ("In chat rules, {sender}, {message}, {channel} and {listener} are filled in, and from the words "
                    "after the matched one: {who} (who to act on: \"heal gno\" -> Gnope, \"heal\" or \"heal me\" "
                    "-> the sender, \"heal tank\" -> your tank), {arg} and {arg2} (the first and second word) and "
                    "{rest} (all of them, e.g. an item name). {arg|sender}: the sender when there's no word. "
                    "E.g. /cast [@{who}] Healing Wave, or /use {rest}.")


def split_key(key: str) -> tuple[str, str]:
    """'power_pct_lt' -> ('power_pct', '<')"""
    for op, suffix in sorted(OPS, key=lambda o: -len(o[1])):
        if suffix and key.endswith(suffix) and key[: -len(suffix)] in FIELD_KIND:
            return key[: -len(suffix)], op
    return key, "="


def value_text(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def parse_value(text: str, kind: str):
    t = text.strip()
    if kind == "bool":
        if t.lower() in ("yes", "true", "1", "on"):
            return True
        if t.lower() in ("no", "false", "0", "off"):
            return False
        raise ValueError(f"{t!r}: use yes or no")
    if kind == "number":
        try:
            n = float(t)
        except ValueError:
            raise ValueError(f"{t!r} isn't a number") from None
        return int(n) if n.is_integer() else n
    if not t:
        raise ValueError("needs a value")
    return t


def parse_who(text: str):
    """'any' / 'all' / '3' / '3 (Kunth)' / '[2, 3]' -> Condition.slot"""
    t = text.split("(")[0].strip().lower()
    if t in ("", "any"):
        return "any"
    if t == "all":
        return "all"
    nums = [int(n) for n in re.findall(r"\d+", t)]
    if not nums:
        raise ValueError(f"Who: {text!r} - use any, all, or slot numbers like 3 or [2, 3]")
    return nums[0] if len(nums) == 1 and "[" not in t else nums


def who_text(slot) -> str:
    return target_text(slot) if isinstance(slot, list) else str(slot)


def split_list(text: str) -> list[str] | None:
    items = [x.strip() for x in text.split(",") if x.strip()]
    return items or None


def build_rule(name: str, enabled: bool, cooldown: str, trigger: str, *, who: str = "any",
               conditions: list[dict] | None = None, every: str = "", channels: list[str] | None = None,
               senders: str = "", contains: str = "", heard_by: str = "all", queue: str = "",
               priority: int = 0, cooldown_per_target: bool = False, hotkey: str = "", panel: bool = False,
               steps: list[dict]) -> AutomationRule:
    """Editor values -> AutomationRule. Raises ValueError with a readable message."""
    if not name.strip():
        raise ValueError("Give the rule a name.")
    data: dict = {"name": name.strip(), "enabled": enabled}
    if cooldown.strip():
        data["cooldown"] = cooldown.strip()
    if priority:
        data["priority"] = priority
    if hotkey.strip():
        data["hotkey"] = hotkey.strip()
    if panel:
        data["panel"] = True
    if trigger in ("state", "chat+state"):
        when: dict = {"slot": parse_who(who)}
        for i, c in enumerate(conditions or [], 1):
            field = c["field"]
            kind = FIELD_KIND.get(field, "text")
            if kind == "bool" and c["op"] not in ("=", "≠"):
                raise ValueError(f"Condition {i}: {field} is yes/no, so use = or ≠")
            try:
                when[field + OP_SUFFIX[c["op"]]] = parse_value(c["value"], kind)
            except ValueError as e:
                raise ValueError(f"Condition {i} ({field}): {e}") from None
        if trigger == "state" and len(when) == 1:
            raise ValueError("Add at least one condition (e.g. power % < 40).")
        data["when"] = when
    if trigger == "every":
        if not every.strip():
            raise ValueError("Timer: how often? e.g. 25m or 90s")
        data["every"] = every.strip()
    if trigger in ("chat", "chat+state"):
        if not channels:
            raise ValueError("Chat: tick at least one channel.")
        chat: dict = {"channel": channels, "heard_by": heard_by.strip() or "all"}
        if s := split_list(senders):
            chat["sender"] = s
        if c := split_list(contains):
            chat["contains"] = c
        data["chat"] = chat
        if cooldown_per_target:
            data["cooldown_per"] = "target"
        if (q := queue.strip().lower()) not in ("", "default"):
            if q == "off":
                q = "0"
            if not q.isdigit():
                raise ValueError("Queue: a number of requests (e.g. 5), off, or default.")
            data["queue"] = int(q)
    data["do"], step_rows = rows_to_do(steps)
    try:
        return AutomationRule.model_validate(data)
    except ValidationError as e:
        raise ValueError(explain(e, step_rows, {"when": "Condition", "every": "Timer", "cooldown": "Cooldown",
                                                "chat": "Chat"})) from None


def trigger_of(rule: AutomationRule) -> str:
    if rule.chat is not None:
        return "chat+state" if rule.when is not None else "chat"
    return "every" if rule.every is not None else "state"


class ConditionTable(QWidget):
    """Rows of field / comparison / value."""

    def __init__(self, rows: list[dict]) -> None:
        super().__init__()
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Field", "Is", "Value"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.setColumnWidth(2, 120)
        self.rows = rows
        buttons = QHBoxLayout()
        buttons.addWidget(QPushButton("Add condition", clicked=self.add))
        buttons.addWidget(QPushButton("Remove", clicked=self.remove))
        buttons.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        lay.addLayout(buttons)
        self.render()

    def render(self) -> None:
        t = self.table
        t.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            field = QComboBox()
            for f, label, _ in FIELDS:
                field.addItem(f"{label}   [{f}]", f)
            if row["field"] not in FIELD_KIND:
                field.addItem(row["field"], row["field"])
            field.setCurrentIndex(field.findData(row["field"]))
            field.currentIndexChanged.connect(lambda _i, r=r: self._field_changed(r))
            kind = FIELD_KIND.get(row["field"], "text")
            op = QComboBox()
            for o, _ in OPS if kind == "number" else OPS[:2]:
                op.addItem(o)
            op.setCurrentText(row["op"])
            value = QComboBox()
            value.setEditable(True)
            if kind == "bool":
                value.addItems(["yes", "no"])
            value.setCurrentText(row["value"])
            for col, w in enumerate((field, op, value)):
                t.setCellWidget(r, col, w)

    def read(self) -> list[dict]:
        t = self.table
        self.rows = [{"field": t.cellWidget(r, 0).currentData(), "op": t.cellWidget(r, 1).currentText(),
                      "value": t.cellWidget(r, 2).currentText()} for r in range(t.rowCount())]
        return self.rows

    def _field_changed(self, r: int) -> None:
        self.read()
        row = self.rows[r]
        kind = FIELD_KIND.get(row["field"], "text")
        if kind == "bool":
            row["op"] = row["op"] if row["op"] in ("=", "≠") else "="
            row["value"] = row["value"] if row["value"] in ("yes", "no") else "yes"
        elif kind != "number" and row["op"] not in ("=", "≠"):
            row["op"] = "="
        self.render()

    def add(self) -> None:
        self.read()
        self.rows.append({"field": "power_pct", "op": "<", "value": "40"})
        self.render()

    def remove(self) -> None:
        self.read()
        r = self.table.currentRow()
        if 0 <= r < len(self.rows):
            del self.rows[r]
            self.render()


class RuleEditor(QDialog):
    def __init__(self, parent: QWidget | None, rule: AutomationRule | None, *, targets: list[str],
                 who_choices: list[str], rule_names: list[str],
                 preview: Callable[[AutomationRule], str], on_test: Callable[[AutomationRule, str], str],
                 conflict: Callable[[str], str | None] = lambda key: None) -> None:
        super().__init__(parent)
        self.conflict = conflict
        self.setWindowTitle(f"Edit rule: {rule.name}" if rule else "New automation rule")
        self.resize(940, 720)
        self.other_names = rule_names
        self.preview = preview
        self.on_test = on_test
        self.result_rule: AutomationRule | None = None

        self.name_edit = QLineEdit(rule.name if rule else "")
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(rule.enabled if rule else True)
        self.cooldown = QLineEdit(fmt_duration(rule.cooldown) if rule and rule.cooldown else "")
        self.cooldown.setPlaceholderText("e.g. 20s: the least time between two firings")
        self.trigger = QComboBox()
        for key, label in TRIGGERS:
            self.trigger.addItem(label, key)
        self.trigger.setCurrentIndex(self.trigger.findData(trigger_of(rule) if rule else "state"))
        self.trigger.currentIndexChanged.connect(self._show_trigger)
        top = QFormLayout()
        top.addRow("Name", self.name_edit)
        top.addRow("", self.enabled)
        top.addRow("Trigger", self.trigger)
        top.addRow("Cooldown", self.cooldown)
        self.priority = QSpinBox()
        self.priority.setRange(-100, 100)
        self.priority.setValue(rule.priority if rule else 0)
        self.priority.setToolTip("When rules wait for the same character (its cast or global cooldown), "
                                 "the higher priority goes first. Equal: first come, first served.")
        top.addRow("Priority", self.priority)
        self.hotkey = HotkeyEdit(rule.hotkey or "" if rule else "")
        self.hotkey.setToolTip("Press it (in WoW or WoWBox) to turn this rule on or off. In the game, /bb rule "
                               "<name> does the same.")
        self.hotkey_warning = QLabel()
        self.hotkey_warning.setStyleSheet("color: #c62828;")
        self.hotkey.textChanged.connect(self._check_hotkey)
        key_row = QHBoxLayout()
        key_row.addWidget(self.hotkey, 1)
        key_row.addWidget(QPushButton("Clear", clicked=lambda: self.hotkey.setText("")))
        top.addRow("On/off hotkey", key_row)
        top.addRow("", self.hotkey_warning)
        self.panel = QCheckBox("Show on the in-game panel whether it's on (/bb panel)")
        self.panel.setChecked(bool(rule and rule.panel))
        top.addRow("", self.panel)

        # state
        self.who = QComboBox()
        self.who.setEditable(True)
        self.who.addItems(who_choices)
        when = rule.when if rule and rule.when is not None else None
        self.who.setCurrentText(who_text(when.slot) if when else "any")
        cond_rows = []
        for key, val in ((when.model_extra or {}).items() if when else []):
            field, op = split_key(key)
            cond_rows.append({"field": field, "op": op, "value": value_text(val)})
        if not rule:
            cond_rows = [{"field": "combat", "op": "=", "value": "no"}]
        self.conditions = ConditionTable(cond_rows)
        self.state_box = QGroupBox("When")
        sl = QFormLayout(self.state_box)
        sl.addRow("Who", self.who)
        who_hint = QLabel("any: fires if one of them matches · all: only if every one does · or slots, e.g. 3 or [2, 3]")
        who_hint.setStyleSheet("color: gray;")
        sl.addRow("", who_hint)
        sl.addRow(self.conditions)

        # timer
        self.every = QLineEdit(fmt_duration(rule.every) if rule and rule.every else "")
        self.every.setPlaceholderText("e.g. 25m, 90s, 1h")
        self.timer_box = QGroupBox("Timer")
        QFormLayout(self.timer_box).addRow("Every", self.every)

        # chat
        chat = rule.chat if rule and rule.chat is not None else None
        self.channel_boxes: dict[str, QCheckBox] = {}
        ch_row = QHBoxLayout()
        for ch in CHANNELS:
            cb = QCheckBox(ch)
            cb.setChecked(ch in (chat.channel if chat else ["whisper"]))
            self.channel_boxes[ch] = cb
            ch_row.addWidget(cb)
        ch_row.addStretch(1)
        self.senders = QLineEdit(", ".join(chat.sender) if chat and chat.sender else "")
        self.senders.setPlaceholderText("names, comma separated; team = any of your own characters; "
                                        "empty = anyone (careful)")
        self.contains = QLineEdit(", ".join(chat.contains) if chat and chat.contains else "")
        self.contains.setPlaceholderText("words, comma separated (any one of them, any case); empty = any line")
        self.heard_by = QComboBox()
        self.heard_by.setEditable(True)
        self.heard_by.addItems(targets)
        self.heard_by.setCurrentText(target_text(chat.heard_by) if chat else "all")
        self.chat_box = QGroupBox("Chat line")
        cl = QFormLayout(self.chat_box)
        cl.addRow("Channels", ch_row)
        cl.addRow("From", self.senders)
        cl.addRow("Containing", self.contains)
        cl.addRow("Heard by", self.heard_by)
        self.queue = QComboBox()
        self.queue.setEditable(True)
        self.queue.addItems(["default", "off", "1", "3", "5", "10"])
        q = rule.queue if rule else None
        self.queue.setCurrentText("default" if q is None else "off" if q == 0 else str(q))
        self.queue.setToolTip("Requests that come in while this rule is busy wait their turn, one per person. "
                              "default: automation_options.chat_queue (5). off: they're skipped.")
        cl.addRow("Queue while busy", self.queue)
        self.per_target = QCheckBox("Cooldown per target: healing Gnope doesn't hold up a heal on Kunth")
        self.per_target.setChecked(bool(rule and rule.cooldown_per == "target"))
        self.per_target.setToolTip("The cooldown is kept separately for each {who}. Without it, one cooldown for "
                                   "the whole rule.")
        cl.addRow("", self.per_target)
        self.sample = QLineEdit()
        self.sample.setPlaceholderText("a chat line for Test, e.g. heal on gno pls (empty: the first word in "
                                       "Containing)")
        cl.addRow("Test line", self.sample)

        # steps
        self.steps = StepTable(targets, macro_rows(rule) if rule else None)
        self.table = self.steps.table
        self.steps_hint = QLabel(TO_HINT)
        self.steps_hint.setWordWrap(True)
        self.steps_hint.setStyleSheet("color: gray;")

        # live check + buttons
        self.check = QLabel()
        self.check.setWordWrap(True)
        self.check.setStyleSheet("background: palette(alternate-base); padding: 6px;")
        self.error = QLabel()
        self.error.setStyleSheet("color: #c62828;")
        self.error.setWordWrap(True)
        self.test_out = QLabel()
        self.test_out.setWordWrap(True)
        bottom = QHBoxLayout()
        bottom.addWidget(QPushButton("Test (dry run)", clicked=self.test))
        bottom.addStretch(1)
        save = QPushButton("Save", clicked=self.save)
        save.setDefault(True)
        bottom.addWidget(save)
        bottom.addWidget(QPushButton("Cancel", clicked=self.reject))

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.state_box)
        lay.addWidget(self.timer_box)
        lay.addWidget(self.chat_box)
        lay.addWidget(QLabel("Then do (top to bottom):"))
        lay.addWidget(self.steps, 1)
        lay.addWidget(self.steps_hint)
        lay.addWidget(QLabel("Live check:"))
        lay.addWidget(self.check)
        lay.addWidget(self.test_out)
        lay.addWidget(self.error)
        lay.addLayout(bottom)
        self._show_trigger()
        self._timer = QTimer(self, timeout=self.refresh_check, interval=500)
        self._timer.start()
        self.refresh_check()

    # ------------------------------------------------------------------ view
    def trigger_key(self) -> str:
        return self.trigger.currentData()

    def _show_trigger(self) -> None:
        t = self.trigger_key()
        self.state_box.setVisible(t in ("state", "chat+state"))
        self.state_box.setTitle("And only if" if t == "chat+state" else "When")
        self.timer_box.setVisible(t == "every")
        self.chat_box.setVisible(t in ("chat", "chat+state"))
        self.steps_hint.setText(TO_HINT + (" " + PLACEHOLDER_HINT if t.startswith("chat") else ""))
        self.refresh_check()

    # ------------------------------------------------------------------ result
    def build(self) -> AutomationRule:
        rule = build_rule(
            self.name_edit.text(), self.enabled.isChecked(), self.cooldown.text(), self.trigger_key(),
            who=self.who.currentText(), conditions=self.conditions.read(), every=self.every.text(),
            channels=[ch for ch, cb in self.channel_boxes.items() if cb.isChecked()],
            senders=self.senders.text(), contains=self.contains.text(), heard_by=self.heard_by.currentText(),
            queue=self.queue.currentText(), priority=self.priority.value(),
            cooldown_per_target=self.per_target.isChecked(), hotkey=self.hotkey.text(),
            panel=self.panel.isChecked(), steps=self.steps.read())
        if rule.name in self.other_names:
            raise ValueError(f"There's already a rule called {rule.name!r}.")
        if rule.hotkey and (owner := self.conflict(rule.hotkey)):
            raise ValueError(f"Hotkey {rule.hotkey} is already used by {owner}.")
        return rule

    def _check_hotkey(self, text: str) -> None:
        owner = self.conflict(text) if text else None
        self.hotkey_warning.setText(f"{text} is already used by {owner}. Pick another key." if owner else "")

    def refresh_check(self) -> None:
        try:
            rule = build_rule(
                self.name_edit.text() or "(unnamed)", True, self.cooldown.text(), self.trigger_key(),
                who=self.who.currentText(), conditions=self.conditions.read(), every=self.every.text(),
                channels=[ch for ch, cb in self.channel_boxes.items() if cb.isChecked()],
                senders=self.senders.text(), contains=self.contains.text(),
                heard_by=self.heard_by.currentText(), queue=self.queue.currentText(), priority=self.priority.value(),
            cooldown_per_target=self.per_target.isChecked(), steps=self.steps.read())
        except ValueError as e:
            self.check.setText(f"<span style='color:gray'>Not complete yet: {e}</span>")
            return
        self.check.setText(self.preview(rule))

    def test(self) -> None:
        try:
            rule = self.build()
        except ValueError as e:
            self.error.setText(str(e))
            return
        self.error.setText("")
        self.test_out.setText(self.on_test(rule, self.sample.text()))

    def save(self) -> None:
        try:
            self.result_rule = self.build()
        except ValueError as e:
            self.error.setText(str(e))
            return
        self._timer.stop()
        self.accept()

    def reject(self) -> None:
        self._timer.stop()
        super().reject()
