"""System tray icon with quick toggles."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def dot_icon(color: str, ring: str | None = None) -> QIcon:
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    if ring:
        p.setBrush(QColor(ring))
        p.setPen(Qt.NoPen)
        p.drawEllipse(1, 1, 30, 30)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(5, 5, 22, 22)
    p.end()
    return QIcon(pm)


class Tray(QSystemTrayIcon):
    def __init__(self, controller, window) -> None:
        super().__init__()
        self.c = controller
        self.window = window
        menu = QMenu()
        self.act_show = QAction("Show WoWBox", menu, triggered=self._show)
        self.act_broadcast = QAction("Broadcast", menu, checkable=True,
                                     triggered=lambda on: self.c.router.set_enabled(on))
        self.act_auto = QAction("Automation", menu, checkable=True, triggered=lambda on: self.c.engine.set_enabled(on))
        self.act_overlay = QAction("Overlay", menu, checkable=True, triggered=lambda on: self.c.set_overlay(on))
        act_release = QAction("Release all keys", menu, triggered=self.c.router.release_all)
        act_logs = QAction("Open logs folder", menu, triggered=self._open_logs)
        act_logs.setVisible(self.c.diag.log_dir is not None)
        act_quit = QAction("Quit", menu, triggered=self.window.quit_app)
        for a in (self.act_show, None, self.act_broadcast, self.act_auto, self.act_overlay, act_release, None,
                  act_logs, act_quit):
            menu.addSeparator() if a is None else menu.addAction(a)
        self.setContextMenu(menu)
        self._menu = menu
        self.activated.connect(self._on_activated)
        self.refresh()

    def _open_logs(self) -> None:
        import os
        os.startfile(self.c.diag.log_dir)  # noqa: S606 (Windows only, our own folder)

    def _show(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            if self.window.isVisible() and not self.window.isMinimized():
                self.window.hide()
            else:
                self._show()

    def refresh(self) -> None:
        on = self.c.router.enabled
        auto = self.c.engine.enabled
        self.setIcon(dot_icon("#2ecc71" if on else "#e74c3c", "#f1c40f" if auto else None))
        self.setToolTip(f"WoWBox - broadcast {'ON' if on else 'OFF'}, automation {'ON' if auto else 'OFF'}")
        self.act_broadcast.setChecked(on)
        self.act_auto.setChecked(auto)
        self.act_overlay.setChecked(self.c.overlay.isVisible())
