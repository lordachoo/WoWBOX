"""Shared helpers for the WoWBox Phase 0 spikes.

Throwaway code: everything here is plain ctypes so the spikes have no pywin32
dependency. The real app will put all of this behind ``platform/`` interfaces.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("The spikes must run on Windows, on the machine that runs the WoW clients.")

# Window titles can contain characters the console code page can't encode.
sys.stdout.reconfigure(errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

OUT_DIR = Path(__file__).resolve().parent / "out"

# Window classes used by the 3.3.5a client. Unverified: the spikes print the
# class of every match so we can confirm the real one.
WOW_CLASSES = {"GxWindowClassD3d", "GxWindowClass", "GxWindowClassOpenGl"}
WOW_TITLES = {"World of Warcraft", "WoW1", "WoW2", "WoW3", "WoW4"}

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_CLOSE = 0x0010
WM_QUIT = 0x0012

ULONG_PTR = ctypes.c_size_t
LRESULT = wt.LPARAM


def out_path(name: str) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    return OUT_DIR / name


def set_dpi_aware() -> None:
    """Per-monitor-v2 DPI awareness so all coordinates are physical pixels."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


# --------------------------------------------------------------------------
# Prototypes
# --------------------------------------------------------------------------
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.IsHungAppWindow.argtypes = [wt.HWND]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT
user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = wt.HMONITOR
kernel32.GetCurrentThreadId.restype = wt.DWORD


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------
@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    tid: int
    cls: str
    title: str
    visible: bool
    iconic: bool
    window_rect: tuple[int, int, int, int]  # left, top, right, bottom (screen)
    client_origin: tuple[int, int]  # client (0,0) in screen coords
    client_size: tuple[int, int]

    def describe(self) -> str:
        l, t, r, b = self.window_rect
        return (
            f"hwnd=0x{self.hwnd:X} pid={self.pid} class={self.cls!r} title={self.title!r} "
            f"visible={self.visible} minimized={self.iconic} "
            f"window=({l},{t})-({r},{b}) client_origin={self.client_origin} client_size={self.client_size}"
        )


def window_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid_tid(hwnd: int) -> tuple[int, int]:
    pid = wt.DWORD()
    tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value, tid


def window_info(hwnd: int) -> WindowInfo:
    pid, tid = window_pid_tid(hwnd)
    wr = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    cr = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return WindowInfo(
        hwnd=hwnd,
        pid=pid,
        tid=tid,
        cls=window_class(hwnd),
        title=window_text(hwnd),
        visible=bool(user32.IsWindowVisible(hwnd)),
        iconic=bool(user32.IsIconic(hwnd)),
        window_rect=(wr.left, wr.top, wr.right, wr.bottom),
        client_origin=(pt.x, pt.y),
        client_size=(cr.right - cr.left, cr.bottom - cr.top),
    )


def all_top_level_windows() -> list[int]:
    hwnds: list[int] = []

    @WNDENUMPROC
    def cb(hwnd, _lparam):
        hwnds.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return hwnds


def is_wow_window(hwnd: int) -> bool:
    return window_class(hwnd) in WOW_CLASSES or window_text(hwnd) in WOW_TITLES


def find_wow_windows(title_substring: str | None = None) -> list[WindowInfo]:
    """WoW windows (by class or title), or any window whose title contains ``title_substring``."""
    found = []
    for hwnd in all_top_level_windows():
        if title_substring is not None:
            if title_substring.lower() in window_text(hwnd).lower() and user32.IsWindowVisible(hwnd):
                found.append(window_info(hwnd))
        elif is_wow_window(hwnd):
            found.append(window_info(hwnd))
    return sorted(found, key=lambda w: w.pid)


def print_windows(wins: list[WindowInfo]) -> None:
    if not wins:
        print("  (none)")
    for i, w in enumerate(wins):
        print(f"  [{i}] {w.describe()}")


def pick_window(hwnd_arg: str | None, title_substring: str | None = None) -> WindowInfo:
    """Resolve --hwnd, or pick among found WoW windows (asks if several)."""
    if hwnd_arg:
        hwnd = int(hwnd_arg, 0)
        if not user32.IsWindow(hwnd):
            raise SystemExit(f"0x{hwnd:X} is not a window")
        return window_info(hwnd)
    wins = find_wow_windows(title_substring)
    print("Candidate windows:")
    print_windows(wins)
    if not wins:
        raise SystemExit("No WoW window found. Start a client first (or pass --hwnd).")
    if len(wins) == 1:
        return wins[0]
    idx = int(input(f"Pick a window index [0-{len(wins) - 1}]: "))
    return wins[idx]


def foreground_desc() -> str:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "<none>"
    return f"0x{hwnd:X} {window_text(hwnd)!r} ({window_class(hwnd)})"


def monitor_info(hwnd: int) -> MONITORINFO:
    hmon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------
VK = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12,
    "PAUSE": 0x13, "ESCAPE": 0x1B, "ESC": 0x1B, "SPACE": 0x20, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "END": 0x23, "HOME": 0x24, "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "INSERT": 0x2D, "DELETE": 0x2E, "SCROLLLOCK": 0x91,
    "LSHIFT": 0xA0, "RSHIFT": 0xA1, "LCTRL": 0xA2, "RCTRL": 0xA3, "LALT": 0xA4, "RALT": 0xA5,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF,
}
VK.update({str(d): 0x30 + d for d in range(10)})
VK.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({f"F{i}": 0x70 + i - 1 for i in range(1, 25)})

EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0xA3, 0xA5}


def vk_from_name(name: str) -> int:
    try:
        return VK[name.upper()]
    except KeyError:
        raise SystemExit(f"Unknown key name {name!r}. Known: {', '.join(sorted(VK))}") from None


def scan_code(vk: int) -> int:
    return user32.MapVirtualKeyW(vk, 0) & 0xFF  # MAPVK_VK_TO_VSC


def make_lparam(vk: int, down: bool, *, alt_context: bool = False, repeat: int = 1) -> int:
    """lParam for WM_(SYS)KEYDOWN/UP: repeat count, scan code, extended, context, previous, transition."""
    lp = (repeat & 0xFFFF) | (scan_code(vk) << 16)
    if vk in EXTENDED_VKS:
        lp |= 1 << 24
    if alt_context:
        lp |= 1 << 29
    if not down:
        lp |= (1 << 30) | (1 << 31)
    return lp


def post_key(hwnd: int, vk: int, down: bool, *, sys_key: bool = False, alt_context: bool = False) -> bool:
    if sys_key:
        msg = WM_SYSKEYDOWN if down else WM_SYSKEYUP
    else:
        msg = WM_KEYDOWN if down else WM_KEYUP
    ok = bool(user32.PostMessageW(hwnd, msg, vk, make_lparam(vk, down, alt_context=alt_context)))
    if not ok:
        err = ctypes.get_last_error()
        hint = " (access denied: is WoW running as admin while this script isn't?)" if err == 5 else ""
        print(f"  !! PostMessageW failed, GetLastError={err}{hint}")
    return ok


def post_press(hwnd: int, vk: int, hold: float = 0.03) -> None:
    post_key(hwnd, vk, True)
    time.sleep(hold)
    post_key(hwnd, vk, False)


# SendInput -----------------------------------------------------------------
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT

KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2


def send_input_keys(events: list[tuple[int, bool]], extra: int = 0) -> int:
    """Inject (vk, down) events into the system input stream (goes to the foreground window)."""
    arr = (INPUT * len(events))()
    for i, (vk, down) in enumerate(events):
        arr[i].type = 1  # INPUT_KEYBOARD
        ki = arr[i].u.ki
        ki.wVk = vk
        ki.wScan = scan_code(vk)
        ki.dwFlags = (0 if down else KEYEVENTF_KEYUP) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0)
        ki.dwExtraInfo = extra
    return user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def force_foreground(hwnd: int) -> bool:
    """SetForegroundWindow with the AttachThreadInput workaround for the foreground lock."""
    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_tid and fg_tid != me and user32.AttachThreadInput(me, fg_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        return bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_tid, False)


# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------
def countdown(seconds: float, what: str) -> None:
    print(f"\n>>> {what}")
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        print(f"    starting in {left:4.1f}s ...", end="\r", flush=True)
        time.sleep(0.1)
    print(" " * 40, end="\r")


def stamp() -> str:
    return time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"


def banner(title: str) -> None:
    print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)
