"""Pixel capture of a client region: mss (screen), dxcam (Desktop Duplication), PrintWindow (works when covered).

Not thread-safe: use one instance per thread (the bridge poller owns one).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import time

import numpy as np

from . import _win32 as w
from .base import CaptureAPI, CaptureMethod, Rect

log = logging.getLogger(__name__)

w.user32.GetDC.argtypes = [wt.HWND]
w.user32.GetDC.restype = wt.HDC
w.user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
w.user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
w.user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
w.user32.MonitorFromWindow.restype = wt.HMONITOR
w.gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
w.gdi32.CreateCompatibleDC.restype = wt.HDC
w.gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
w.gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
w.gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
w.gdi32.SelectObject.restype = wt.HGDIOBJ
w.gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
w.gdi32.DeleteDC.argtypes = [wt.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


w.gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                              ctypes.POINTER(BITMAPINFOHEADER), wt.UINT]

PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2


class Win32Capture(CaptureAPI):
    def __init__(self) -> None:
        self._mss = None
        self._dxcams: dict[int, tuple[object, int, int]] = {}

    def capture(self, hwnd: int, region: Rect, method: CaptureMethod) -> np.ndarray:
        if method == "mss":
            return self._mss_grab(hwnd, region)
        if method == "dxcam":
            return self._dxcam_grab(hwnd, region)
        if method == "printwindow":
            return self._printwindow(hwnd, region)
        raise ValueError(method)

    def close(self) -> None:
        if self._mss is not None:
            self._mss.close()
            self._mss = None
        for cam, _, _ in self._dxcams.values():
            try:
                cam.release()
            except Exception:
                pass
        self._dxcams.clear()

    # ------------------------------------------------------------------
    @staticmethod
    def _client_origin(hwnd: int) -> tuple[int, int]:
        pt = wt.POINT(0, 0)
        w.user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return pt.x, pt.y

    def _mss_grab(self, hwnd: int, region: Rect) -> np.ndarray:
        import mss
        if self._mss is None:
            self._mss = mss.mss()
        ox, oy = self._client_origin(hwnd)
        shot = self._mss.grab({"left": ox + region.x, "top": oy + region.y, "width": region.w, "height": region.h})
        return np.asarray(shot)[:, :, 2::-1].copy()

    def _dxcam_for(self, hwnd: int):
        import dxcam
        hmon = int(w.user32.MonitorFromWindow(hwnd, 2) or 0)
        if hmon in self._dxcams:
            return self._dxcams[hmon]
        mi = w.MONITORINFO()
        mi.cbSize = ctypes.sizeof(w.MONITORINFO)
        w.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        size = (r.right - r.left, r.bottom - r.top)
        fallback = None
        for dev in range(4):
            for out in range(8):
                try:
                    cam = dxcam.create(device_idx=dev, output_idx=out, output_color="RGB")
                except Exception:
                    continue
                if cam is None:
                    continue
                try:
                    if int(cam._output.hmonitor or 0) == hmon:
                        self._dxcams[hmon] = (cam, r.left, r.top)
                        return self._dxcams[hmon]
                except Exception:
                    pass
                if fallback is None and (cam.width, cam.height) == size:
                    fallback = cam
        if fallback is None:
            raise RuntimeError("no dxcam output matches this window's monitor")
        self._dxcams[hmon] = (fallback, r.left, r.top)
        return self._dxcams[hmon]

    def _dxcam_grab(self, hwnd: int, region: Rect) -> np.ndarray:
        cam, mx, my = self._dxcam_for(hwnd)
        ox, oy = self._client_origin(hwnd)
        x, y = ox + region.x - mx, oy + region.y - my
        for _ in range(20):
            frame = cam.grab(region=(x, y, x + region.w, y + region.h))
            if frame is not None:
                return np.asarray(frame)[:, :, :3].copy()
            time.sleep(0.005)
        raise RuntimeError("dxcam: no new frame")

    def _printwindow(self, hwnd: int, region: Rect) -> np.ndarray:
        cr = wt.RECT()
        w.user32.GetClientRect(hwnd, ctypes.byref(cr))
        cw, ch = cr.right - cr.left, cr.bottom - cr.top
        if cw <= 0 or ch <= 0:
            raise RuntimeError("window has no client area (minimized?)")
        win_dc = w.user32.GetDC(hwnd)
        mem_dc = w.gdi32.CreateCompatibleDC(win_dc)
        bmp = w.gdi32.CreateCompatibleBitmap(win_dc, cw, ch)
        old = w.gdi32.SelectObject(mem_dc, bmp)
        try:
            if not w.user32.PrintWindow(hwnd, mem_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
                raise RuntimeError("PrintWindow failed")
            w.gdi32.SelectObject(mem_dc, old)
            bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=cw, biHeight=-ch,
                                   biPlanes=1, biBitCount=32, biCompression=0)
            rows = ch
            buf = (ctypes.c_ubyte * (cw * rows * 4))()
            if w.gdi32.GetDIBits(mem_dc, bmp, 0, rows, buf, ctypes.byref(bmi), 0) == 0:
                raise RuntimeError("GetDIBits failed")
        finally:
            w.gdi32.DeleteObject(bmp)
            w.gdi32.DeleteDC(mem_dc)
            w.user32.ReleaseDC(hwnd, win_dc)
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(rows, cw, 4)
        return arr[region.y:region.y + region.h, region.x:region.x + region.w, 2::-1].copy()
