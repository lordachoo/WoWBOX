"""Thumbnail strip: a frameless window, kept above WoW, hosting live DWM thumbnails. Click one to make it main."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

from ..platform.base import Rect
from .grid import fit_aspect, thumb_cells
from .zorder import StayWithWoW

log = logging.getLogger(__name__)

DWM_TNP_RECTDESTINATION, DWM_TNP_OPACITY, DWM_TNP_VISIBLE, DWM_TNP_SOURCECLIENTAREAONLY = 0x1, 0x4, 0x8, 0x10
LABEL_H = 20


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [("dwFlags", wt.DWORD), ("rcDestination", wt.RECT), ("rcSource", wt.RECT),
                ("opacity", wt.BYTE), ("fVisible", wt.BOOL), ("fSourceClientAreaOnly", wt.BOOL)]


_dwm = None


def dwm():
    global _dwm
    if _dwm is None:
        _dwm = ctypes.WinDLL("dwmapi")
        _dwm.DwmRegisterThumbnail.argtypes = [wt.HWND, wt.HWND, ctypes.POINTER(wt.HANDLE)]
        _dwm.DwmRegisterThumbnail.restype = ctypes.c_long
        _dwm.DwmUpdateThumbnailProperties.argtypes = [wt.HANDLE, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
        _dwm.DwmUpdateThumbnailProperties.restype = ctypes.c_long
        _dwm.DwmQueryThumbnailSourceSize.argtypes = [wt.HANDLE, ctypes.POINTER(wt.SIZE)]
        _dwm.DwmUnregisterThumbnail.argtypes = [wt.HANDLE]
    return _dwm


class ThumbStrip(QWidget):
    clicked = Signal(int)

    def __init__(self, label_for: Callable[[int], str], is_wow: Callable[[int], bool] = lambda h: False) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("WoWBox thumbnails")
        self.label_for = label_for
        self._entries: list[tuple[int, int]] = []  # (slot, source hwnd)
        self._thumbs: dict[int, int] = {}  # source hwnd -> HTHUMBNAIL
        self._cells: list[tuple[int, Rect]] = []  # (slot, cell in physical px)
        self._size = (0, 0)
        self._z = StayWithWoW(self, is_wow)

    def show_at(self, rect: Rect, entries: list[tuple[int, int]], mover: Callable[[int, Rect], None]) -> None:
        """entries: (slot, hwnd) for the clients to show. rect in physical px."""
        self.show()
        mover(int(self.winId()), rect)
        self._size = (rect.w, rect.h)
        self._sync(entries)
        self._z.start()

    def _sync(self, entries: list[tuple[int, int]]) -> None:
        wanted = {h for _, h in entries}
        for h in list(self._thumbs):
            if h not in wanted:
                dwm().DwmUnregisterThumbnail(self._thumbs.pop(h))
        dest = int(self.winId())
        for _, h in entries:
            if h not in self._thumbs:
                th = wt.HANDLE()
                hr = dwm().DwmRegisterThumbnail(dest, h, ctypes.byref(th))
                if hr == 0:
                    self._thumbs[h] = th.value
                else:
                    log.warning("DwmRegisterThumbnail(0x%X) failed hr=0x%08X", h, hr & 0xFFFFFFFF)
        self._entries = entries
        self._layout()

    def _layout(self) -> None:
        w, h = self._size
        self._cells = []
        for (slot, hwnd), (cell, area) in zip(self._entries, thumb_cells(Rect(0, 0, w, h), len(self._entries),
                                                                            LABEL_H)):
            self._cells.append((slot, cell))
            th = self._thumbs.get(hwnd)
            if not th:
                continue
            size = wt.SIZE()
            dwm().DwmQueryThumbnailSourceSize(th, ctypes.byref(size))
            r = fit_aspect(area, size.cx, size.cy)
            props = DWM_THUMBNAIL_PROPERTIES()
            props.dwFlags = DWM_TNP_RECTDESTINATION | DWM_TNP_VISIBLE | DWM_TNP_OPACITY | DWM_TNP_SOURCECLIENTAREAONLY
            props.rcDestination = wt.RECT(r.x, r.y, r.right, r.bottom)
            props.opacity = 255
            props.fVisible = True
            props.fSourceClientAreaOnly = True
            dwm().DwmUpdateThumbnailProperties(th, ctypes.byref(props))
        self.update()

    def clear(self) -> None:
        for th in self._thumbs.values():
            dwm().DwmUnregisterThumbnail(th)
        self._thumbs.clear()
        self._entries = []
        self._z.stop()
        self.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        dpr = self.devicePixelRatioF()
        self._size = (int(self.width() * dpr), int(self.height() * dpr))
        self._layout()

    def mousePressEvent(self, event) -> None:
        dpr = self.devicePixelRatioF()
        x, y = event.position().x() * dpr, event.position().y() * dpr
        for slot, cell in self._cells:
            if cell.x <= x < cell.right and cell.y <= y < cell.bottom:
                self.clicked.emit(slot)
                return

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(18, 18, 22))
        dpr = self.devicePixelRatioF()
        p.setFont(QFont("Segoe UI", 9))
        for slot, cell in self._cells:
            x, y, w, h = cell.x / dpr, cell.y / dpr, cell.w / dpr, cell.h / dpr
            p.setPen(QColor(70, 70, 80))
            p.drawRect(int(x) + 1, int(y) + 1, int(w) - 2, int(h) - 2)
            p.setPen(QColor(230, 230, 230))
            p.drawText(int(x) + 6, int(y) + 14, self.label_for(slot))
