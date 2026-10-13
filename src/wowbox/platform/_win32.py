"""ctypes prototypes shared by the win32_* modules. Import only on Windows."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
LRESULT = wt.LPARAM

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_CLOSE, WM_QUIT = 0x0010, 0x0012

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def _proto(fn, argtypes, restype=None):
    fn.argtypes = argtypes
    if restype is not None:
        fn.restype = restype


_proto(user32.EnumWindows, [WNDENUMPROC, wt.LPARAM])
_proto(user32.EnumDisplayMonitors, [wt.HDC, ctypes.POINTER(wt.RECT), MONITORENUMPROC, wt.LPARAM])
_proto(user32.GetMonitorInfoW, [wt.HMONITOR, ctypes.POINTER(MONITORINFO)])
_proto(user32.GetClassNameW, [wt.HWND, wt.LPWSTR, ctypes.c_int])
_proto(user32.GetWindowTextW, [wt.HWND, wt.LPWSTR, ctypes.c_int])
_proto(user32.GetWindowTextLengthW, [wt.HWND])
_proto(user32.SetWindowTextW, [wt.HWND, wt.LPCWSTR])
_proto(user32.GetWindowThreadProcessId, [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD)
_proto(user32.IsWindow, [wt.HWND])
_proto(user32.IsWindowVisible, [wt.HWND])
_proto(user32.IsIconic, [wt.HWND])
_proto(user32.IsHungAppWindow, [wt.HWND])
_proto(user32.GetWindowRect, [wt.HWND, ctypes.POINTER(wt.RECT)])
_proto(user32.GetClientRect, [wt.HWND, ctypes.POINTER(wt.RECT)])
_proto(user32.ClientToScreen, [wt.HWND, ctypes.POINTER(wt.POINT)])
_proto(user32.PostMessageW, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL)
_proto(user32.MapVirtualKeyW, [wt.UINT, wt.UINT], wt.UINT)
_proto(user32.GetForegroundWindow, [], wt.HWND)
_proto(user32.SetForegroundWindow, [wt.HWND])
_proto(user32.BringWindowToTop, [wt.HWND])
_proto(user32.ShowWindow, [wt.HWND, ctypes.c_int])
_proto(user32.AttachThreadInput, [wt.DWORD, wt.DWORD, wt.BOOL])
_proto(user32.GetWindowLongPtrW, [wt.HWND, ctypes.c_int], ctypes.c_ssize_t)
_proto(user32.SetWindowLongPtrW, [wt.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t)
_proto(user32.SetWindowPos, [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT])
_proto(user32.WindowFromPoint, [wt.POINT], wt.HWND)
_proto(user32.GetAncestor, [wt.HWND, wt.UINT], wt.HWND)
_proto(kernel32.GetCurrentThreadId, [], wt.DWORD)
_proto(kernel32.GetModuleHandleW, [wt.LPCWSTR], wt.HMODULE)
_proto(kernel32.OpenProcess, [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE)
_proto(kernel32.CloseHandle, [wt.HANDLE])
_proto(kernel32.GetExitCodeProcess, [wt.HANDLE, ctypes.POINTER(wt.DWORD)])


def window_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def all_top_level_windows() -> list[int]:
    hwnds: list[int] = []

    @WNDENUMPROC
    def cb(hwnd, _lparam):
        hwnds.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return hwnds


def scan_code(vk: int) -> int:
    return user32.MapVirtualKeyW(vk, 0) & 0xFF


class _PowerThrottlingState(ctypes.Structure):
    _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]


def disable_power_throttling() -> bool:
    """Opt out of Windows' efficiency mode for this process. While WoWBox's window is minimized or hidden,
    Windows 11 otherwise slows its threads down, and the bridge then misses chat lines (each is on screen
    for only half a second). Returns whether Windows accepted it (older Windows: False, nothing to do)."""
    process_power_throttling = 4
    execution_speed, ignore_timer_resolution = 0x1, 0x4
    state = _PowerThrottlingState(1, execution_speed | ignore_timer_resolution, 0)  # controlled, and off
    fn = getattr(kernel32, "SetProcessInformation", None)
    if fn is None:
        return False
    fn.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    fn.restype = wt.BOOL
    return bool(fn(kernel32.GetCurrentProcess(), process_power_throttling, ctypes.byref(state),
                   ctypes.sizeof(state)))


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


_proto(kernel32.GetCurrentProcess, [], wt.HANDLE)
_proto(kernel32.K32GetProcessMemoryInfo, [wt.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wt.DWORD], wt.BOOL)
_proto(kernel32.GetProcessHandleCount, [wt.HANDLE, ctypes.POINTER(wt.DWORD)], wt.BOOL)
_proto(user32.GetGuiResources, [wt.HANDLE, wt.DWORD], wt.DWORD)


def process_counters() -> dict[str, int]:
    """This process's memory (bytes), kernel handles, and GDI/USER objects (Windows allows 10,000 of each)."""
    me = kernel32.GetCurrentProcess()
    out: dict[str, int] = {}
    mem = _ProcessMemoryCounters()
    mem.cb = ctypes.sizeof(mem)
    if kernel32.K32GetProcessMemoryInfo(me, ctypes.byref(mem), mem.cb):
        out.update(working_set=mem.WorkingSetSize, peak_working_set=mem.PeakWorkingSetSize,
                   private=mem.PagefileUsage)
    handles = wt.DWORD()
    if kernel32.GetProcessHandleCount(me, ctypes.byref(handles)):
        out["handles"] = handles.value
    out["gdi"] = user32.GetGuiResources(me, 0)
    out["user"] = user32.GetGuiResources(me, 1)
    return out


def process_alive(pid: int) -> bool:
    """Whether a process with this id is still running."""
    query_limited_information, still_active = 0x1000, 259
    h = kernel32.OpenProcess(query_limited_information, False, pid)
    if not h:
        return False
    try:
        code = wt.DWORD()
        return bool(kernel32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == still_active
    finally:
        kernel32.CloseHandle(h)
