"""Win32 window management: find, restyle, position, focus, monitors."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging

from . import _win32 as w
from .base import MonitorInfo, Rect, WindowAPI

log = logging.getLogger(__name__)

# WoW's window class, one per renderer: GxWindowClassD3d, GxWindowClassOpenGl, GxWindowClassVulkan, ...
WOW_CLASS_PREFIX = "GxWindowClass"


def is_wow_class(name: str) -> bool:
    return name.startswith(WOW_CLASS_PREFIX)

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_THICKFRAME, WS_SYSMENU = 0x00C00000, 0x00040000, 0x00080000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00020000, 0x00010000
WS_EX_DLGMODALFRAME, WS_EX_WINDOWEDGE, WS_EX_CLIENTEDGE, WS_EX_STATICEDGE = 0x1, 0x100, 0x200, 0x20000
FRAME_STYLES = WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
FRAME_EXSTYLES = WS_EX_DLGMODALFRAME | WS_EX_WINDOWEDGE | WS_EX_CLIENTEDGE | WS_EX_STATICEDGE
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x2, 0x4, 0x10
SWP_FRAMECHANGED, SWP_NOOWNERZORDER = 0x20, 0x200
HWND_TOP = 0
SW_SHOWNOACTIVATE = 4
GA_ROOT = 2


class Win32Windows(WindowAPI):
    def __init__(self) -> None:
        self._orig_styles: dict[int, tuple[int, int]] = {}

    def list_wow_windows(self) -> list[int]:
        return [h for h in w.all_top_level_windows() if is_wow_class(w.window_class(h))]

    def windows_for_pid(self, pid: int) -> list[int]:
        return [h for h in self.list_wow_windows() if w.window_pid(h) == pid]

    def pid_of(self, hwnd: int) -> int:
        return w.window_pid(hwnd)

    def title(self, hwnd: int) -> str:
        return w.window_text(hwnd)

    def set_title(self, hwnd: int, title: str) -> None:
        w.user32.SetWindowTextW(hwnd, title)

    def is_alive(self, hwnd: int) -> bool:
        return bool(hwnd) and bool(w.user32.IsWindow(hwnd))

    def is_responding(self, hwnd: int) -> bool:
        return not w.user32.IsHungAppWindow(hwnd)

    def is_minimized(self, hwnd: int) -> bool:
        return bool(w.user32.IsIconic(hwnd))

    def client_rect(self, hwnd: int) -> Rect:
        cr = wt.RECT()
        w.user32.GetClientRect(hwnd, ctypes.byref(cr))
        pt = wt.POINT(0, 0)
        w.user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return Rect(pt.x, pt.y, cr.right - cr.left, cr.bottom - cr.top)

    def make_borderless(self, hwnd: int) -> None:
        style = w.user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        ex = w.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if not (style & FRAME_STYLES or ex & FRAME_EXSTYLES):
            return
        self._orig_styles.setdefault(hwnd, (style, ex))
        w.user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style & ~FRAME_STYLES)
        w.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex & ~FRAME_EXSTYLES)
        w.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                              SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)

    def restore_frame(self, hwnd: int) -> None:
        orig = self._orig_styles.pop(hwnd, None)
        if orig is None:
            return
        w.user32.SetWindowLongPtrW(hwnd, GWL_STYLE, orig[0])
        w.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, orig[1])
        w.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                              SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)

    def move(self, hwnd: int, rect: Rect, *, to_top: bool = False) -> None:
        flags = SWP_NOACTIVATE | SWP_NOOWNERZORDER | (0 if to_top else SWP_NOZORDER)
        cur = self.client_rect(hwnd)
        if cur == rect and not to_top:
            return
        w.user32.SetWindowPos(hwnd, HWND_TOP, rect.x, rect.y, rect.w, rect.h, flags)

    def restore(self, hwnd: int) -> None:
        if w.user32.IsIconic(hwnd):
            w.user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)

    def foreground(self) -> int | None:
        return w.user32.GetForegroundWindow() or None

    def set_foreground(self, hwnd: int) -> bool:
        """SetForegroundWindow with the AttachThreadInput workaround for the foreground lock."""
        fg = w.user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        me = w.kernel32.GetCurrentThreadId()
        fg_tid = w.user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = bool(fg_tid and fg_tid != me and w.user32.AttachThreadInput(me, fg_tid, True))
        try:
            self.restore(hwnd)
            w.user32.BringWindowToTop(hwnd)
            return bool(w.user32.SetForegroundWindow(hwnd))
        finally:
            if attached:
                w.user32.AttachThreadInput(me, fg_tid, False)

    def monitors(self) -> list[MonitorInfo]:
        found: list[MonitorInfo] = []

        @w.MONITORENUMPROC
        def cb(hmon, _hdc, _rect, _lp):
            mi = w.MONITORINFO()
            mi.cbSize = ctypes.sizeof(w.MONITORINFO)
            w.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
            r, k = mi.rcMonitor, mi.rcWork
            found.append(MonitorInfo(
                rect=Rect(r.left, r.top, r.right - r.left, r.bottom - r.top),
                work=Rect(k.left, k.top, k.right - k.left, k.bottom - k.top),
                primary=bool(mi.dwFlags & 1), handle=int(hmon or 0)))
            return True

        w.user32.EnumDisplayMonitors(None, None, cb, 0)
        return sorted(found, key=lambda m: (not m.primary, m.rect.x, m.rect.y))

    def is_point_visible(self, hwnd: int, x: int, y: int) -> bool:
        hit = w.user32.WindowFromPoint(wt.POINT(x, y))
        if not hit:
            return False
        return hit == hwnd or w.user32.GetAncestor(hit, GA_ROOT) == hwnd

    def close(self, hwnd: int) -> None:
        w.user32.PostMessageW(hwnd, w.WM_CLOSE, 0, 0)
