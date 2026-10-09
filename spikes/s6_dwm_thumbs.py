"""S6: do DwmRegisterThumbnail live thumbnails render the client (not black) when it isn't focused?

    python s6_dwm_thumbs.py                    # thumbnails of every WoW window found
    python s6_dwm_thumbs.py --title Notepad    # dry run against any window title (no WoW needed)

A Qt window shows one live thumbnail per client. Then try each state and note what the thumbnail shows:
  1. client visible, but another window focused
  2. client fully covered by another window
  3. client minimized
  4. client moved to another monitor (if you have one)
Look for motion (idle animation, login-screen fire/clouds) to confirm the thumbnail is live, not frozen.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from _common import WindowInfo, find_wow_windows, print_windows, user32

dwmapi = ctypes.WinDLL("dwmapi")

DWM_TNP_RECTDESTINATION, DWM_TNP_RECTSOURCE, DWM_TNP_OPACITY = 0x1, 0x2, 0x4
DWM_TNP_VISIBLE, DWM_TNP_SOURCECLIENTAREAONLY = 0x8, 0x10


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [("dwFlags", wt.DWORD), ("rcDestination", wt.RECT), ("rcSource", wt.RECT),
                ("opacity", wt.BYTE), ("fVisible", wt.BOOL), ("fSourceClientAreaOnly", wt.BOOL)]


dwmapi.DwmRegisterThumbnail.argtypes = [wt.HWND, wt.HWND, ctypes.POINTER(wt.HANDLE)]
dwmapi.DwmRegisterThumbnail.restype = ctypes.c_long
dwmapi.DwmUpdateThumbnailProperties.argtypes = [wt.HANDLE, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
dwmapi.DwmUpdateThumbnailProperties.restype = ctypes.c_long
dwmapi.DwmQueryThumbnailSourceSize.argtypes = [wt.HANDLE, ctypes.POINTER(wt.SIZE)]
dwmapi.DwmQueryThumbnailSourceSize.restype = ctypes.c_long
dwmapi.DwmUnregisterThumbnail.argtypes = [wt.HANDLE]

LABEL_H = 22  # logical px


class ThumbHost(QWidget):
    def __init__(self, sources: list[WindowInfo]):
        super().__init__()
        self.sources = sources
        self.thumbs: list[int | None] = []
        self.setWindowTitle("WoWBox S6 - DWM thumbnails")
        self.resize(1200, 700)
        self.status: list[str] = ["" for _ in sources]
        timer = QTimer(self)
        timer.timeout.connect(self.poll_state)
        timer.start(1000)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.thumbs:
            dest = int(self.winId())
            for s in self.sources:
                h = wt.HANDLE()
                t0 = time.perf_counter()
                hr = dwmapi.DwmRegisterThumbnail(dest, s.hwnd, ctypes.byref(h))
                print(f"DwmRegisterThumbnail(0x{s.hwnd:X}) hr=0x{hr & 0xFFFFFFFF:08X} "
                      f"in {1000 * (time.perf_counter() - t0):.2f} ms")
                self.thumbs.append(h.value if hr == 0 else None)
        self.layout_thumbs()

    def cells(self):
        n = max(1, len(self.sources))
        cols = 1 if n == 1 else 2
        rows = (n + cols - 1) // cols
        cw, ch = self.width() // cols, self.height() // rows
        for i in range(n):
            yield i, (i % cols) * cw, (i // cols) * ch, cw, ch

    def layout_thumbs(self):
        dpr = self.devicePixelRatioF()
        for i, x, y, cw, ch in self.cells():
            thumb = self.thumbs[i] if i < len(self.thumbs) else None
            if not thumb:
                continue
            size = wt.SIZE()
            dwmapi.DwmQueryThumbnailSourceSize(thumb, ctypes.byref(size))
            avail_w, avail_h = cw - 8, ch - LABEL_H - 8
            if size.cx > 0 and size.cy > 0:
                scale = min(avail_w / size.cx, avail_h / size.cy)
                tw, th = int(size.cx * scale), int(size.cy * scale)
            else:
                tw, th = avail_w, avail_h
            props = DWM_THUMBNAIL_PROPERTIES()
            props.dwFlags = DWM_TNP_RECTDESTINATION | DWM_TNP_VISIBLE | DWM_TNP_OPACITY | DWM_TNP_SOURCECLIENTAREAONLY
            props.rcDestination = wt.RECT(int((x + 4) * dpr), int((y + LABEL_H + 4) * dpr),
                                          int((x + 4 + tw) * dpr), int((y + LABEL_H + 4 + th) * dpr))
            props.opacity = 255
            props.fVisible = True
            props.fSourceClientAreaOnly = True
            hr = dwmapi.DwmUpdateThumbnailProperties(thumb, ctypes.byref(props))
            if hr != 0:
                print(f"DwmUpdateThumbnailProperties hr=0x{hr & 0xFFFFFFFF:08X}")
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.thumbs:
            self.layout_thumbs()

    def poll_state(self):
        fg = user32.GetForegroundWindow()
        for i, s in enumerate(self.sources):
            alive = bool(user32.IsWindow(s.hwnd))
            self.status[i] = (f"0x{s.hwnd:X} pid={s.pid}  alive={alive} "
                              f"minimized={bool(user32.IsIconic(s.hwnd))} focused={fg == s.hwnd}")
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 48))
        p.setPen(QColor(230, 230, 230))
        for i, x, y, cw, ch in self.cells():
            p.drawRect(x + 2, y + 2, cw - 4, ch - 4)
            if i < len(self.status):
                p.drawText(x + 8, y + 16, self.status[i])

    def closeEvent(self, event):
        for t in self.thumbs:
            if t:
                dwmapi.DwmUnregisterThumbnail(t)
        super().closeEvent(event)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", help="use windows whose title contains this instead of WoW windows")
    args = ap.parse_args()

    app = QApplication(sys.argv)  # Qt sets per-monitor DPI awareness itself
    sources = find_wow_windows(args.title)
    print("Sources:")
    print_windows(sources)
    if not sources:
        raise SystemExit("No source windows found.")
    host = ThumbHost(sources)
    host.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
