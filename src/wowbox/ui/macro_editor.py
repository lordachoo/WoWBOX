"""The macro editor: name, hotkey (press it to record), note, and a table of steps."""
from __future__ import annotations

import re
from typing import Callable

from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QVBoxLayout, QWidget,
)

from ..config import ActionConfig, MacroConfig
from ..keys import MODIFIER_VKS, Chord, Mod
from ..macro_store import DEFAULT_DELAY, flat_steps, fmt_duration

# (kind, label, what Value holds)
KINDS = [
    ("type", "Type chat line", "/assist Tinkerdawn   or   /cast [@Tinkerdawn] Healing Wave"),
    ("send", "Press key", "1   or   Ctrl+9"),
    ("keys", "Press keys", "1-9, 0   (comma separated, `Delay` apart)"),
    ("wait", "Wait", "300ms   or   1.5s"),
    ("macro", "Start macro", "name of another macro"),
]
KIND_LABELS = {k: label for k, label, _ in KINDS}
KIND_HINTS = {k: hint for k, _, hint in KINDS}
NO_TARGET = {"wait", "macro"}
_SLOTS = re.compile(r"^\[?\s*\d+(\s*,\s*\d+)*\s*\]?$")
COLS = ["Step", "Value", "To", "Delay"]


def parse_target(text: str):
    """'2' / '[1, 3]' -> slot list; anything else is a target name."""
    text = text.strip()
    if _SLOTS.match(text):
        return [int(n) for n in re.findall(r"\d+", text)]
    return text or "all"


def target_text(target) -> str:
    return "[" + ", ".join(str(s) for s in target) + "]" if isinstance(target, list) else str(target)


def step_row(step: ActionConfig) -> dict:
    """An ActionConfig as the editor's row values."""
    if step.wait is not None:
        return {"kind": "wait", "value": fmt_duration(step.wait), "to": "", "delay": ""}
    if step.macro is not None:
        return {"kind": "macro", "value": step.macro, "to": "", "delay": ""}
    if step.send is not None:
        kind, value = "send", step.send
    elif step.type is not None:
        kind, value = "type", step.type
    else:
        kind, value = "keys", ", ".join(step.keys or [])
    delay = fmt_duration(step.delay) if kind == "keys" else ""
    return {"kind": kind, "value": value, "to": target_text(step.to), "delay": delay}


def row_action(row: dict) -> dict:
    """Editor row values -> the dict an ActionConfig is validated from."""
    kind, value = row["kind"], row["value"].strip()
    if kind == "wait":
        return {"wait": value}
    if kind == "macro":
        return {"macro": value}
    d: dict = {"to": parse_target(row["to"])}
    if kind == "keys":
        d["keys"] = [k.strip() for k in value.split(",") if k.strip()]
        d["delay"] = row["delay"].strip() or fmt_duration(DEFAULT_DELAY)
    else:
        d[kind] = value
    return d


def build_macro(name: str, key: str, note: str, rows: list[dict]) -> MacroConfig:
    """Raises ValueError with a readable message."""
    if not name.strip():
        raise ValueError("Give the macro a name.")
    if not rows:
        raise ValueError("Add at least one step.")
    for i, row in enumerate(rows, 1):
        if not row["value"].strip():
            raise ValueError(f"Step {i}: {KIND_LABELS[row['kind']].lower()} needs a value "
                             f"(e.g. {KIND_HINTS[row['kind']].split('   ')[0]}).")
    steps = [row_action(r) for r in rows]
    data = {"name": name.strip(), "key": key or None, "note": note.strip(),
            "do": steps[0] if len(steps) == 1 else {"sequence": steps}}
    try:
        return MacroConfig.model_validate(data)
    except ValidationError as e:
        raise ValueError(_explain(e, len(steps) == 1)) from None


def _explain(e: ValidationError, single: bool) -> str:
    msgs = []
    for err in e.errors():
        loc = list(err["loc"])
        where = ""
        if loc[:2] == ["do", "sequence"] and len(loc) > 2 and isinstance(loc[2], int):
            where = f"Step {loc[2] + 1}: "
        elif loc[:1] == ["do"] and single:
            where = "Step 1: "
        elif loc:
            where = f"{loc[0]}: "
        msg = err["msg"].removeprefix("Value error, ")
        msgs.append(where + msg)
    return "\n".join(dict.fromkeys(msgs))


def chord_text(vk: int, mods: Mod) -> str | None:
    """A recorded key press as a hotkey string, or None for a bare modifier."""
    if vk in MODIFIER_VKS or vk == 0:
        return None
    return str(Chord(vk, mods))


class HotkeyEdit(QLineEdit):
    """Click, then press the key combination. Backspace/Delete clears."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setReadOnly(True)
        self.setPlaceholderText("click here, then press the keys (e.g. Ctrl+Alt+H)")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        vk = event.nativeVirtualKey()
        m = event.modifiers()
        if vk in (0x08, 0x2E) and not m & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier):
            self.setText("")  # Backspace / Delete on their own clear the hotkey
            return
        self.record(vk, (Mod.CTRL if m & Qt.ControlModifier else Mod.NONE)
                    | (Mod.ALT if m & Qt.AltModifier else Mod.NONE)
                    | (Mod.SHIFT if m & Qt.ShiftModifier else Mod.NONE))

    def record(self, vk: int, mods: Mod) -> None:
        if text := chord_text(vk, mods):
            self.setText(text)

    def focusNextPrevChild(self, nxt: bool) -> bool:  # let Tab be recorded rather than move focus
        return False if self.hasFocus() else super().focusNextPrevChild(nxt)


class MacroEditor(QDialog):
    def __init__(self, parent: QWidget | None, macro: MacroConfig | None, *, targets: list[str],
                 macro_names: list[str], conflict: Callable[[str], str | None],
                 on_test: Callable[[MacroConfig], None]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit macro: {macro.name}" if macro else "New macro")
        self.resize(900, 480)
        self.targets = targets
        self.other_names = macro_names
        self.conflict = conflict
        self.on_test = on_test
        self.result_macro: MacroConfig | None = None

        self.name_edit = QLineEdit(macro.name if macro else "")
        self.key_edit = HotkeyEdit(macro.key or "" if macro else "")
        clear = QPushButton("Clear", clicked=lambda: self.key_edit.setText(""))
        self.key_warning = QLabel()
        self.key_warning.setStyleSheet("color: #c62828;")
        self.key_edit.textChanged.connect(self._check_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(clear)
        self.note_edit = QLineEdit(macro.note if macro else "")
        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Hotkey", key_row)
        form.addRow("", self.key_warning)
        form.addRow("Note", self.note_edit)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.Interactive)
        self.table.setColumnWidth(2, 170)
        self.table.setColumnWidth(3, 70)
        self.rows: list[dict] = [step_row(s) for s in flat_steps(macro.do)] if macro else \
            [{"kind": "type", "value": "", "to": "all", "delay": ""}]

        step_buttons = QHBoxLayout()
        for text, fn in (("Add step", self.add_row), ("Remove", self.remove_row), ("Move up", lambda: self.move(-1)),
                         ("Move down", lambda: self.move(1))):
            step_buttons.addWidget(QPushButton(text, clicked=fn))
        step_buttons.addStretch(1)

        self.error = QLabel()
        self.error.setStyleSheet("color: #c62828;")
        self.error.setWordWrap(True)
        hint = QLabel("To: all, followers (everyone but the window you're in), leader, not:leader, a character "
                      "name, class:WARLOCK, role:healer, or slots like [2, 3]. Two casts on one character need a "
                      "Wait of about 1.5s between them (global cooldown).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")

        bottom = QHBoxLayout()
        bottom.addWidget(QPushButton("Test (run now)", clicked=self.test))
        bottom.addStretch(1)
        save = QPushButton("Save", clicked=self.save)
        save.setDefault(True)
        bottom.addWidget(save)
        bottom.addWidget(QPushButton("Cancel", clicked=self.reject))

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("Steps (run top to bottom):"))
        lay.addWidget(self.table, 1)
        lay.addLayout(step_buttons)
        lay.addWidget(hint)
        lay.addWidget(self.error)
        lay.addLayout(bottom)
        self.render()
        self._check_key(self.key_edit.text())

    # ------------------------------------------------------------------ rows
    def render(self) -> None:
        t = self.table
        t.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            kind = QComboBox()
            for k, label, _ in KINDS:
                kind.addItem(label, k)
            kind.setCurrentIndex([k for k, *_ in KINDS].index(row["kind"]))
            kind.currentIndexChanged.connect(lambda _i, r=r: self._kind_changed(r))
            value = QLineEdit(row["value"])
            value.setPlaceholderText(KIND_HINTS[row["kind"]])
            to = QComboBox()
            to.setEditable(True)
            to.addItems(self.targets)
            to.setCurrentText(row["to"])
            delay = QLineEdit(row["delay"])
            delay.setPlaceholderText(fmt_duration(DEFAULT_DELAY))
            for col, w in enumerate((kind, value, to, delay)):
                t.setCellWidget(r, col, w)
            to.setEnabled(row["kind"] not in NO_TARGET)
            delay.setEnabled(row["kind"] == "keys")

    def read(self) -> list[dict]:
        """The rows as currently shown (widgets -> self.rows)."""
        t = self.table
        rows = []
        for r in range(t.rowCount()):
            rows.append({"kind": t.cellWidget(r, 0).currentData(), "value": t.cellWidget(r, 1).text(),
                         "to": t.cellWidget(r, 2).currentText(), "delay": t.cellWidget(r, 3).text()})
        self.rows = rows
        return rows

    def _kind_changed(self, r: int) -> None:
        self.read()
        if self.rows[r]["kind"] not in NO_TARGET and not self.rows[r]["to"]:
            self.rows[r]["to"] = "all"
        self.render()
        self.table.setCurrentCell(r, 1)

    def add_row(self) -> None:
        self.read()
        r = self.table.currentRow()
        r = len(self.rows) if r < 0 else r + 1
        prev_to = next((row["to"] for row in reversed(self.rows[:r]) if row["to"]), "all")
        self.rows.insert(r, {"kind": "type", "value": "", "to": prev_to, "delay": ""})
        self.render()
        self.table.setCurrentCell(r, 1)
        self.table.cellWidget(r, 1).setFocus()

    def remove_row(self) -> None:
        self.read()
        r = self.table.currentRow()
        if 0 <= r < len(self.rows):
            del self.rows[r]
            self.render()
            self.table.setCurrentCell(min(r, len(self.rows) - 1), 1)

    def move(self, delta: int) -> None:
        self.read()
        r = self.table.currentRow()
        if 0 <= r < len(self.rows) and 0 <= r + delta < len(self.rows):
            self.rows[r], self.rows[r + delta] = self.rows[r + delta], self.rows[r]
            self.render()
            self.table.setCurrentCell(r + delta, 1)

    # ------------------------------------------------------------------ result
    def _check_key(self, text: str) -> None:
        owner = self.conflict(text) if text else None
        self.key_warning.setText(f"{text} is already used by {owner}. Pick another key." if owner else "")

    def build(self) -> MacroConfig:
        m = build_macro(self.name_edit.text(), self.key_edit.text(), self.note_edit.text(), self.read())
        if m.name in self.other_names:
            raise ValueError(f"There's already a macro called {m.name!r}.")
        if m.key and (owner := self.conflict(m.key)):
            raise ValueError(f"Hotkey {m.key} is already used by {owner}.")
        return m

    def test(self) -> None:
        try:
            m = self.build()
        except ValueError as e:
            self.error.setText(str(e))
            return
        self.error.setText("")
        self.on_test(m)

    def save(self) -> None:
        try:
            self.result_macro = self.build()
        except ValueError as e:
            self.error.setText(str(e))
            return
        self.accept()
