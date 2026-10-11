"""The Settings tab: the options that tune automation, typing, follow and stacking, editable while you play.

A change applies at once (the running parts read the same config objects). Save writes the changed ones into
team.yaml in place; Revert goes back to what the file says.
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from .. import settings
from ..config import parse_duration
from ..macro_store import fmt_duration

BAD = "QLineEdit, QSpinBox, QDoubleSpinBox { background: #ffcdd2; }"


class SettingsTab(QWidget):
    def __init__(self, controller) -> None:
        super().__init__()
        self.c = controller
        self.widgets: dict[tuple[str, str], QWidget] = {}
        self._loading = False

        body = QWidget()
        cols = QHBoxLayout(body)
        left, right = QVBoxLayout(), QVBoxLayout()
        for n, (title, group) in enumerate(settings.GROUPS):
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
            for s in group:
                w = self._make(s)
                self.widgets[(s.section, s.key)] = w
                w.setToolTip(s.help)
                label = QLabel(s.label)
                label.setToolTip(s.help)
                form.addRow(label, w)
                hint = QLabel(f"<span style='color:gray'>{html.escape(s.help)}</span>")  # its own full-width line: never cut
                hint.setWordWrap(True)
                hint.setContentsMargins(12, 0, 0, 6)
                form.addRow(hint)
            (left if n % 2 == 0 else right).addWidget(box)
        left.addStretch(1)
        right.addStretch(1)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.b_save = QPushButton("Save to team.yaml", clicked=self.save)
        self.b_revert = QPushButton("Revert to team.yaml", clicked=self.revert)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Changes apply <b>at once</b>. Save keeps them after a restart (only those lines of "
                             "team.yaml change; your comments stay)."))
        bar.addStretch(1)
        bar.addWidget(self.b_revert)
        bar.addWidget(self.b_save)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.status)
        lay.addWidget(scroll, 1)
        self.load()

    # ------------------------------------------------------------------ widgets
    def _make(self, s: settings.Setting) -> QWidget:
        model = getattr(self.c.cfg, s.section)
        kind, choices = settings.field_kind(type(model), s.key)
        if kind == "bool":
            w = QCheckBox()
            w.toggled.connect(lambda v, s=s: self._apply(s, v))
        elif kind == "choice":
            w = QComboBox()
            w.addItems(choices)
            w.currentTextChanged.connect(lambda v, s=s: self._apply(s, v))
        elif kind == "int":
            w = QSpinBox()
            w.setRange(0, 1000)
            w.valueChanged.connect(lambda v, s=s: self._apply(s, v))
        elif kind == "float":
            w = QDoubleSpinBox()
            w.setRange(0.0, 10000.0)
            w.setDecimals(2)
            w.setSingleStep(0.1 if type(model).model_fields[s.key].default < 5 else 1.0)
            w.valueChanged.connect(lambda v, s=s: self._apply(s, v))
        else:  # duration, text
            w = QLineEdit()
            w.setFixedWidth(90)
            w.setPlaceholderText("e.g. 1.5s")
            w.editingFinished.connect(lambda s=s, w=w: self._apply(s, w.text().strip()))
        return w

    def load(self) -> None:
        """Show the values in use now (after Reload config, Revert, or a change elsewhere)."""
        self._loading = True
        try:
            for s in settings.ALL:
                w, model = self.widgets[(s.section, s.key)], getattr(self.c.cfg, s.section)
                v = getattr(model, s.key)
                if isinstance(w, QCheckBox):
                    w.setChecked(bool(v))
                elif isinstance(w, QComboBox):
                    w.setCurrentText(str(v))
                elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                    w.setValue(v)
                else:
                    w.setText(settings.show(model, s.key))
                w.setStyleSheet("")
        finally:
            self._loading = False
        self.refresh_status()

    def _apply(self, s: settings.Setting, value: object) -> None:
        if self._loading:
            return
        w = self.widgets[(s.section, s.key)]
        if isinstance(w, QLineEdit):
            try:
                value = parse_duration(value)
            except ValueError as e:
                self._bad(w, f"{s.label}: {e}")
                return
        if problem := self.c.apply_setting(s.section, s.key, value):
            self._bad(w, f"{s.label}: {problem}")
            return
        w.setStyleSheet("")
        w.setToolTip(s.help)
        if isinstance(w, QLineEdit):
            w.setText(fmt_duration(getattr(getattr(self.c.cfg, s.section), s.key)))
        self.refresh_status()

    def _bad(self, w: QWidget, message: str) -> None:
        w.setStyleSheet(BAD)
        w.setToolTip(message)
        self.status.setText(f"<span style='color:#c62828'>Not applied: {html.escape(message)}</span>")

    # ------------------------------------------------------------------ save / revert
    def refresh_status(self) -> None:
        unsaved = self.c.unsaved_settings()
        for (section, key), w in self.widgets.items():
            f = w.font()
            f.setBold(any(u.section == section and u.key == key for u in unsaved))
            w.setFont(f)
        self.b_save.setEnabled(bool(unsaved))
        self.b_revert.setEnabled(bool(unsaved))
        if unsaved:
            self.status.setText("<b>Not saved yet</b> (in use now, lost on restart): "
                                + html.escape(", ".join(u.label for u in unsaved)))
        else:
            self.status.setText("<span style='color:gray'>Everything matches team.yaml.</span>")

    def save(self) -> None:
        try:
            done = self.c.save_settings()
        except Exception as e:
            self.status.setText(f"<span style='color:#c62828'>Not saved: {e}</span>")
            return
        self.refresh_status()
        if done:
            self.status.setText(f"Saved to team.yaml: {', '.join(done)} (the old file is team.yaml.bak).")

    def revert(self) -> None:
        try:
            self.c.revert_settings()
        except Exception as e:
            self.status.setText(f"<span style='color:#c62828'>Couldn't read team.yaml: {e}</span>")
            return
        self.load()


def summary(cfg) -> str:
    """One line of the settings that explain what the queue is doing, for the Activity tab."""
    a, b, f = cfg.automation_options, cfg.broadcast, cfg.follow
    parts = [f"queue {a.chat_queue} per rule" if a.chat_queue else "queueing off",
             f"expires {fmt_duration(a.chat_queue_ttl)}",
             f"rule gap {fmt_duration(a.rule_gap)}" if a.rule_gap else "no rule gap",
             f"waits for casts (max {fmt_duration(a.cast_wait_max)})" if a.wait_for_casts else "doesn't wait for casts",
             "skips dead" if a.skip_dead else "counts the dead",
             f"skips far (> {f.mirror_max_distance_yards:g} yd / other zone)" if a.skip_far else "counts far away",
             f"typed lines wait up to {fmt_duration(b.type_wait_max)}"]
    return " · ".join(parts)
