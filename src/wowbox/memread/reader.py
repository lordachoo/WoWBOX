"""Read-only process memory reader (optional, off by default).

Reads values at config-supplied addresses/pointer chains with ReadProcessMemory. Opens the process
with PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION only: no writes, no injection, no hiding.
Offsets are never hard-coded; you supply and verify them in team.yaml (memread.fields).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import struct
import sys
import threading
from typing import Callable

from ..config import MemReadConfig, MemReadField

log = logging.getLogger(__name__)

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32 = 0x8, 0x10

_FMT = {"u8": "<B", "u16": "<H", "u32": "<I", "i32": "<i", "u64": "<Q", "float": "<f", "double": "<d"}


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", wt.DWORD), ("hModule", wt.HMODULE), ("szModule", wt.WCHAR * 256),
                ("szExePath", wt.WCHAR * 260)]


class ProcessReader:
    def __init__(self, pid: int) -> None:
        if sys.platform != "win32":
            raise RuntimeError("memread needs Windows")
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32.OpenProcess.restype = wt.HANDLE
        self.k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                               ctypes.POINTER(ctypes.c_size_t)]
        self.k32.IsWow64Process.argtypes = [wt.HANDLE, ctypes.POINTER(wt.BOOL)]
        self.k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        self.k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        self.k32.Module32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        self.k32.Module32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        self.k32.CloseHandle.argtypes = [wt.HANDLE]
        self.pid = pid
        self.handle = self.k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not self.handle:
            raise OSError(f"OpenProcess({pid}) failed: {ctypes.get_last_error()}")
        wow64 = wt.BOOL()
        self.k32.IsWow64Process(self.handle, ctypes.byref(wow64))
        self.ptr_size = 4 if wow64.value else 8
        self._modules: dict[str, int] | None = None

    def close(self) -> None:
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None

    def read(self, address: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        n = ctypes.c_size_t()
        if not self.k32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(n)) \
                or n.value != size:
            raise OSError(f"ReadProcessMemory(0x{address:X}, {size}) failed: {ctypes.get_last_error()}")
        return buf.raw

    def read_ptr(self, address: int) -> int:
        return struct.unpack("<I" if self.ptr_size == 4 else "<Q", self.read(address, self.ptr_size))[0]

    def module_base(self, name: str) -> int:
        if self._modules is None:
            self._modules = {}
            snap = self.k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid)
            if snap and snap != wt.HANDLE(-1).value:
                me = MODULEENTRY32W()
                me.dwSize = ctypes.sizeof(MODULEENTRY32W)
                ok = self.k32.Module32FirstW(snap, ctypes.byref(me))
                while ok:
                    self._modules[me.szModule.lower()] = me.modBaseAddr or 0
                    ok = self.k32.Module32NextW(snap, ctypes.byref(me))
                self.k32.CloseHandle(snap)
        try:
            return self._modules[name.lower()]
        except KeyError:
            raise KeyError(f"module {name!r} not found in pid {self.pid}") from None

    def read_field(self, f: MemReadField) -> object:
        addr = int(f.address, 16)
        if f.module:
            addr += self.module_base(f.module)
        for off in f.offsets:
            addr = self.read_ptr(addr) + int(off, 16)
        fmt = _FMT[f.type]
        return struct.unpack(fmt, self.read(addr, struct.calcsize(fmt)))[0]


class MemReadPoller:
    """Reads all configured fields for every attached client at memread.hz."""

    def __init__(self, cfg: MemReadConfig, get_pids: Callable[[], dict[int, int]],
                 on_values: Callable[[int, dict[str, object]], None]) -> None:
        self.cfg = cfg
        self.get_pids = get_pids
        self.on_values = on_values
        self._readers: dict[int, ProcessReader] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.cfg.enabled or self._thread:
            return
        log.info("memread enabled: %d field(s), read-only", len(self.cfg.fields))
        self._thread = threading.Thread(target=self._run, name="memread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)
            self._thread = None
        for r in self._readers.values():
            r.close()
        self._readers.clear()

    def _run(self) -> None:
        while not self._stop.wait(1.0 / max(0.2, self.cfg.hz)):
            for slot, pid in self.get_pids().items():
                values: dict[str, object] = {}
                try:
                    reader = self._readers.get(pid) or self._readers.setdefault(pid, ProcessReader(pid))
                    for name, f in self.cfg.fields.items():
                        try:
                            values[name] = reader.read_field(f)
                        except Exception as e:
                            values[name] = f"<{e}>"
                except Exception as e:
                    values["error"] = str(e)
                self.on_values(slot, values)
