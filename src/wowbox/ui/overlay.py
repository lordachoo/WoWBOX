"""Optional always-on-top, click-through status strip."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QWidget, QHBoxLayout


class Overlay(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.label = QLabel(self)
        self.label.setStyleSheet("QLabel { color: white; background: rgba(10,10,14,170); padding: 3px 10px;"
                                 " border-radius: 6px; font: bold 10pt 'Segoe UI'; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label)

    def set_text(self, html: str) -> None:
        self.label.setText(html)
        self.adjustSize()
        # bottom-right: never over a client's top-left corner, where the bridge strip is drawn
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 8, screen.bottom() - self.height() - 8)
