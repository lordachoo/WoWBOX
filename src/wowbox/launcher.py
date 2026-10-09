"""Spawn / attach clients and keep the PID -> HWND -> slot map current."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from .config import AppConfig
from .platform.base import WindowAPI
from .team import Client, Team

log = logging.getLogger(__name__)

_SET_LINE = re.compile(r'^SET\s+(\w+)\s+"(.*)"\s*$', re.IGNORECASE)


def set_cvars(config_wtf: Path, values: dict[str, str]) -> bool:
    """Set CVars in Config.wtf, keeping every other line. Writes a timestamped backup first.
    Returns True if the file changed."""
    lines = config_wtf.read_text(encoding="utf-8", errors="replace").splitlines() if config_wtf.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        m = _SET_LINE.match(line)
        key = next((k for k in remaining if m and m.group(1).lower() == k.lower()), None)
        if key is not None:
            out.append(f'SET {m.group(1)} "{remaining.pop(key)}"')
        else:
            out.append(line)
    out.extend(f'SET {k} "{v}"' for k, v in remaining.items())
    if out == lines:
        return False
    if config_wtf.exists():
        backup = config_wtf.with_name(f"Config.wtf.wowbox-{time.strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(config_wtf, backup)
        log.info("backed up %s -> %s", config_wtf.name, backup.name)
    config_wtf.parent.mkdir(parents=True, exist_ok=True)
    config_wtf.write_text("\n".join(out) + "\n", encoding="utf-8")
    return True


class Launcher:
    def __init__(self, cfg: AppConfig, team: Team, windows: WindowAPI,
                 spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.team = team
        self.windows = windows
        self._spawn = spawn
        self._sleep = sleep
        self._clock = clock
        self._procs: dict[int, subprocess.Popen] = {}

    # ------------------------------------------------------------------
    def write_windowed_cvars(self) -> None:
        exe = Path(self.cfg.client.exe)
        values = {"gxWindow": "1", "gxMaximize": "0"}
        if self.cfg.client.resolution:
            values["gxResolution"] = self.cfg.client.resolution
        if set_cvars(exe.parent / "WTF" / "Config.wtf", values):
            log.info("Config.wtf updated: %s", values)

    def attach_existing(self) -> list[int]:
        """Assign running WoW windows to empty slots: 'WoWn' titles first, then by PID order."""
        with self.team.lock:
            known = {c.hwnd for c in self.team.clients.values() if c.hwnd}
            free_windows = [h for h in self.windows.list_wow_windows() if h not in known]
            attached = []
            for h in list(free_windows):
                m = re.fullmatch(r"WoW(\d+)(?: .*)?", self.windows.title(h))
                if m and int(m.group(1)) in self.team.clients and not self.team.clients[int(m.group(1))].attached:
                    self._bind(self.team.clients[int(m.group(1))], h)
                    free_windows.remove(h)
                    attached.append(int(m.group(1)))
            free_windows.sort(key=self.windows.pid_of)
            for c in self.team.clients.values():
                if not free_windows:
                    break
                if not c.attached:
                    self._bind(c, free_windows.pop(0))
                    attached.append(c.slot)
            if free_windows:
                log.info("%d WoW window(s) left unassigned (no free slot)", len(free_windows))
            return attached

    def launch_missing(self, progress: Callable[[str], None] = lambda s: None) -> list[int]:
        """Start a client for every slot without a live window. Blocking: run off the UI thread."""
        exe = Path(self.cfg.client.exe)
        if not exe.exists():
            raise FileNotFoundError(f"client exe not found: {exe}")
        if self.cfg.client.set_windowed_cvars:
            self.write_windowed_cvars()
        self.refresh()
        todo = [c for c in self.team.clients.values() if not c.attached]
        started = []
        for i, c in enumerate(todo):
            if i:
                self._sleep(self.cfg.client.launch_stagger)
            progress(f"launching slot {c.slot} ({c.label})")
            proc = self._spawn([str(exe), *self.cfg.client.args], cwd=str(exe.parent))
            self._procs[c.slot] = proc
            c.pid, c.hwnd = proc.pid, None
            started.append(c.slot)
        deadline = self._clock() + self.cfg.client.window_timeout
        pending = set(started)
        while pending and self._clock() < deadline:
            for slot in list(pending):
                c = self.team.clients[slot]
                hwnds = self.windows.windows_for_pid(c.pid) if c.pid else []
                if hwnds:
                    self._bind(c, hwnds[0])
                    pending.discard(slot)
                    progress(f"slot {slot}: window 0x{hwnds[0]:X}")
                elif self._procs[slot].poll() is not None:
                    log.error("slot %d: client exited with code %s before opening a window", slot,
                              self._procs[slot].returncode)
                    pending.discard(slot)
            if pending:
                self._sleep(0.5)
        for slot in pending:
            log.error("slot %d: no window within %.0fs", slot, self.cfg.client.window_timeout)
        return [s for s in started if self.team.clients[s].attached]

    def refresh(self) -> list[int]:
        """Update alive/responding flags; returns slots whose window disappeared."""
        lost = []
        with self.team.lock:
            for c in self.team.clients.values():
                if c.hwnd is None:
                    continue
                if not self.windows.is_alive(c.hwnd):
                    log.warning("slot %d (%s): window gone", c.slot, c.label)
                    c.hwnd, c.pid, c.alive, c.state = None, None, False, None
                    c.character, c.class_token = "", ""
                    lost.append(c.slot)
                else:
                    c.alive = True
                    c.responding = self.windows.is_responding(c.hwnd)
        return lost

    def close_all(self) -> None:
        for c in self.team.attached():
            self.windows.close(c.hwnd)

    def _bind(self, c: Client, hwnd: int) -> None:
        c.hwnd, c.pid, c.alive = hwnd, self.windows.pid_of(hwnd), True
        if self.cfg.client.rename_windows:
            self.windows.set_title(hwnd, f"WoW{c.slot}")
        log.info("slot %d (%s) -> pid %s hwnd 0x%X", c.slot, c.label, c.pid, hwnd)
