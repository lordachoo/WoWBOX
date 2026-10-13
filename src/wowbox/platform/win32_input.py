"""Low-level keyboard hook (own thread + message pump), PostMessage and SendInput."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time

from ..keys import EXTENDED_VKS
from . import _win32 as w
from .base import InputAPI, KeyCallback, KeyEvent

log = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
LLKHF_EXTENDED, LLKHF_INJECTED = 0x01, 0x10
VK_F24 = 0x87
SLOW_KEY_MS = 100  # a key held up this long by our hook is felt everywhere on the PC
WATCHDOG_TAG = 0x57424F58  # "WBOX" in dwExtraInfo: our own liveness probe, always swallowed


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", w.ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", w.ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", w.ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


HOOKPROC = ctypes.WINFUNCTYPE(w.LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
w.user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
w.user32.SetWindowsHookExW.restype = wt.HHOOK
w.user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
w.user32.CallNextHookEx.restype = w.LRESULT
w.user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
w.user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
w.user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
w.user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
w.user32.SendInput.restype = wt.UINT

WM_APP_REHOOK = 0x8001
WM_CHAR = 0x0102


def make_lparam(vk: int, down: bool, *, alt_context: bool = False, repeat: bool = False) -> int:
    lp = 1 | (w.scan_code(vk) << 16)
    if vk in EXTENDED_VKS:
        lp |= 1 << 24
    if alt_context:
        lp |= 1 << 29
    if repeat or not down:
        lp |= 1 << 30  # previous key state: down
    if not down:
        lp |= 1 << 31
    return lp


class Win32Input(InputAPI):
    def __init__(self, watchdog_interval: float = 5.0) -> None:
        self._callback: KeyCallback | None = None
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._hook = None
        self._proc = HOOKPROC(self._on_hook)  # must stay referenced
        self._ready = threading.Event()
        self._watchdog_interval = watchdog_interval
        self._probe_seen = threading.Event()
        self._stop = threading.Event()
        self.rehook_count = 0
        self.on_hook_timeout = lambda: None  # Windows dropped (or nearly dropped) the hook: the diagnostics dump stacks
        # since the last health() call
        self._worst_key_ms = 0.0  # longest our hook took over one key
        self._slow_keys = 0  # keys that took over SLOW_KEY_MS
        self._worst_probe_ms = 0.0  # longest a probe key took to reach the hook
        self._reinstalls = 0

    # -- sending --------------------------------------------------------
    def post_key(self, hwnd: int, vk: int, down: bool, *, sys_key: bool = False, alt_context: bool = False,
                 repeat: bool = False) -> bool:
        if sys_key:
            msg = w.WM_SYSKEYDOWN if down else w.WM_SYSKEYUP
        else:
            msg = w.WM_KEYDOWN if down else w.WM_KEYUP
        ok = bool(w.user32.PostMessageW(hwnd, msg, vk, make_lparam(vk, down, alt_context=alt_context, repeat=repeat)))
        if not ok:
            err = ctypes.get_last_error()
            if err == 5:
                log.error("PostMessage access denied: WoW runs elevated but WoWBox doesn't. Run both at the same level.")
            else:
                log.debug("PostMessage to 0x%X failed: %s", hwnd, err)
        return ok

    def post_char(self, hwnd: int, ch: str) -> bool:
        raw = ch.encode("utf-16-le")  # characters outside the BMP go as two WM_CHARs (surrogate pair)
        ok = True
        for i in range(0, len(raw), 2):
            ok &= bool(w.user32.PostMessageW(hwnd, WM_CHAR, int.from_bytes(raw[i:i + 2], "little"), 1))
        return ok

    def send_input(self, events: list[tuple[int, bool]], extra: int = 0) -> int:
        arr = (INPUT * len(events))()
        for i, (vk, down) in enumerate(events):
            arr[i].type = 1
            ki = arr[i].u.ki
            ki.wVk = vk
            ki.wScan = w.scan_code(vk)
            ki.dwFlags = (0 if down else 0x2) | (0x1 if vk in EXTENDED_VKS else 0)
            ki.dwExtraInfo = extra
        return w.user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))

    # -- hook -------------------------------------------------------------
    def start_hook(self, callback: KeyCallback) -> None:
        if self._thread:
            return
        self._callback = callback
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="kbd-hook", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if self._watchdog_interval > 0:
            threading.Thread(target=self._watchdog, name="kbd-hook-watchdog", daemon=True).start()

    def stop_hook(self) -> None:
        self._stop.set()
        if self._thread and self._tid:
            w.user32.PostThreadMessageW(self._tid, w.WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread = None

    def _install(self) -> None:
        if self._hook:
            w.user32.UnhookWindowsHookEx(self._hook)
        self._hook = w.user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, w.kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            log.error("SetWindowsHookExW failed: %s", ctypes.get_last_error())

    def _run(self) -> None:
        self._tid = w.kernel32.GetCurrentThreadId()
        self._install()
        self._ready.set()
        msg = wt.MSG()
        while w.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP_REHOOK:
                self._install()
                self.rehook_count += 1
                log.warning("keyboard hook reinstalled (Windows dropped it?) count=%d", self.rehook_count)
        if self._hook:
            w.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _on_hook(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0:
            try:
                kb = KBDLLHOOKSTRUCT.from_address(l_param)
                if kb.dwExtraInfo == WATCHDOG_TAG:
                    self._probe_seen.set()
                    return 1
                t0 = time.perf_counter()
                ev = KeyEvent(
                    vk=kb.vkCode, scan=kb.scanCode,
                    down=w_param in (w.WM_KEYDOWN, w.WM_SYSKEYDOWN),
                    injected=bool(kb.flags & LLKHF_INJECTED), extended=bool(kb.flags & LLKHF_EXTENDED),
                    time_ns=time.perf_counter_ns())
                try:
                    if self._callback is not None and self._callback(ev):
                        return 1
                finally:
                    ms = (time.perf_counter() - t0) * 1000
                    self._worst_key_ms = max(self._worst_key_ms, ms)
                    if ms > SLOW_KEY_MS:
                        self._slow_keys += 1
            except Exception:  # never let an exception kill the hook
                log.exception("keyboard hook callback failed")
        return w.user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _watchdog(self) -> None:
        """Windows silently removes LL hooks that time out. Probe with a tagged F24 and reinstall if unseen."""
        while not self._stop.wait(self._watchdog_interval):
            self._probe_seen.clear()
            t0 = time.perf_counter()
            self.send_input([(VK_F24, True), (VK_F24, False)], extra=WATCHDOG_TAG)
            seen = self._probe_seen.wait(1.0)
            if seen:
                self._worst_probe_ms = max(self._worst_probe_ms, (time.perf_counter() - t0) * 1000)
            elif not self._stop.is_set():
                self._reinstalls += 1
                try:
                    self.on_hook_timeout()
                except Exception:
                    log.exception("hook timeout report failed")
                w.user32.PostThreadMessageW(self._tid, WM_APP_REHOOK, 0, 0)

    def health(self) -> str:
        """For the health line: how fast key presses got through our hook (since the last call)."""
        text = (f"worst {self._worst_key_ms:.0f} ms per key, {self._slow_keys} over {SLOW_KEY_MS} ms, "
                f"probe worst {self._worst_probe_ms:.0f} ms, hook reinstalled {self._reinstalls}x")
        self._worst_key_ms = self._worst_probe_ms = 0.0
        self._slow_keys = self._reinstalls = 0
        return text
