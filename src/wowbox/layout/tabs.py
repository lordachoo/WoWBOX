"""Tab bar: a frameless strip of buttons, one per client, kept above WoW."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ..platform.base import Rect
from .zorder import StayWithWoW


class TabBar(QWidget):
    clicked = Signal(int)

    def __init__(self, label_for: Callable[[int], str], is_wow: Callable[[int], bool] = lambda h: False,
                 follow: Callable[[], bool] = lambda: True) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("WoWBox tabs")
        self.label_for = label_for
        self.setStyleSheet(
            "QWidget { background: #16161a; }"
            "QPushButton { color: #ddd; background: #2a2a32; border: none; padding: 2px 14px; }"
            "QPushButton:checked { background: #3d6fd6; color: white; font-weight: bold; }")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(2)
        self._buttons: dict[int, QPushButton] = {}
        self._z = StayWithWoW(self, is_wow, follow)

    def show_at(self, rect: Rect, slots: list[int], main: int, mover: Callable[[int, Rect], None]) -> None:
        if list(self._buttons) != slots:
            for b in self._buttons.values():
                b.deleteLater()
            self._buttons = {}
            for s in slots:
                b = QPushButton(self.label_for(s))
                b.setCheckable(True)
                b.setFocusPolicy(Qt.NoFocus)
                b.clicked.connect(lambda _=False, s=s: self.clicked.emit(s))
                self._layout.addWidget(b)
                self._buttons[s] = b
        self.set_main(main)
        self.show()
        mover(int(self.winId()), rect)
        self._z.start()  # it stops checking by itself while hidden

    def set_main(self, main: int) -> None:
        for s, b in self._buttons.items():
            b.setChecked(s == main)
            b.setText(self.label_for(s))
