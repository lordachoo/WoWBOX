"""Diagnostics: what WoWBox writes down so a freeze, crash or slowdown can be explained afterwards.

- crash.log: if WoWBox dies hard (a crash inside a Windows call) or the whole process is frozen for a minute,
  Python's fault handler writes every thread's stack there.
- Watchdog: the loops that must keep moving (the UI, the bridge reader, the key sender) are checked twice a
  second. One stuck past its limit gets every thread's stack written to wowbox.log, and a line when it moves
  again. Anything else can ask for the stacks too (the keyboard hook does when Windows drops it).
- Errors nobody caught (in a Qt slot or a thread) are logged with their traceback instead of vanishing.
- wowbox-<pid>.running marks a running WoWBox. One left behind at start-up means that WoWBox didn't shut down
  cleanly (it crashed, froze and was ended, or Windows closed it): that's logged, with what crash.log has on it.
- A health line every 5 minutes: CPU, memory, handles, GDI/USER objects, stalls, errors logged, and what each
  part reports (bridge reads and capture times, worst key-hook delay, worst UI delay). Leaks warn on their own.
"""
from __future__ import annotations

import faulthandler
import json
import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

CHECK_EVERY = 0.5  # seconds between watchdog checks
HEALTH_EVERY = 300.0  # seconds between health lines
REPEAT_STUCK = 30.0  # still stuck this much later: log the stacks again
FROZEN_DUMP_AFTER = 60.0  # the watchdog itself can't run this long (the whole process is frozen): crash.log
CRASH_LOG = "crash.log"
CRASH_LOG_MAX = 1_000_000  # bytes: a bigger crash.log is moved to crash.log.1 at start-up
MARKER_GLOB = "wowbox-*.running"
GUI_OBJECTS_WARN = 5000  # GDI or USER objects: Windows stops a process at 10,000 of each
HANDLES_WARN = 10_000
MEMORY_GROWTH_WARN = 1_000_000_000  # private bytes over the first reading (numpy alone reserves ~800 MB up front)


def thread_stacks(first: str | None = None) -> str:
    """Every Python thread's stack, `first` (a thread name) at the top."""
    frames = sys._current_frames()
    names = {t.ident: t.name for t in threading.enumerate()}
    out = []
    for ident in sorted(frames, key=lambda i: (names.get(i) != first, names.get(i, ""))):
        if names.get(ident) == "diagnostics":
            continue  # the watchdog itself, asking
        out.append(f"--- thread {names.get(ident, ident)}:")
        out.append("".join(traceback.format_stack(frames[ident])).rstrip())
    return "\n".join(out)


def _process_counters() -> dict[str, int]:
    if sys.platform != "win32":
        return {}
    from .platform._win32 import process_counters
    return process_counters()


def _process_alive(pid: int) -> bool:
    if sys.platform != "win32":
        return False
    from .platform._win32 import process_alive
    return process_alive(pid)


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.0f} MB"


def duration(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{s % 3600 // 60:02d}m"


@dataclass
class _Watch:
    name: str
    limit: float
    age: Callable[[float], float | None]  # how long it's been stuck at `now` (None: it's idle or stopped)
    thread: str | None  # its thread's name, to put that stack first
    stuck_since: float | None = None
    reported_at: float = 0.0
    stalls: int = 0  # since the last health line
    worst: float = 0.0  # longest stall since the last health line


class Beat:
    """A heartbeat a loop calls each time round."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self.last = clock()

    def __call__(self) -> None:
        self.last = self._clock()


class _Counter(logging.Handler):
    """Counts warnings and errors logged, for the health line."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.warnings = 0
        self.errors = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self.errors += 1
        else:
            self.warnings += 1


class Diagnostics:
    def __init__(self, log_dir: Path | None, clock: Callable[[], float] = time.monotonic,
                 counters: Callable[[], dict[str, int]] = _process_counters,
                 health_every: float = HEALTH_EVERY) -> None:
        self.log_dir = log_dir  # None: no files (tests), just the watchdog and health
        self._clock = clock
        self._counters = counters
        self.health_every = health_every
        self._watches: dict[str, _Watch] = {}
        self._reports: dict[str, Callable[[], str | None]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._crash_file = None
        self._marker: Path | None = None
        self._started = clock()
        self._last_health = clock()
        self._cpu = (clock(), self._cpu_seconds())
        self._warned: set[str] = set()
        self._first_private: int | None = None
        self._log_counter = _Counter()
        self.unclean_exit: str | None = None  # what the start-up check found, for the UI

    # ------------------------------------------------------------------ start-up / shut-down
    def install(self) -> None:
        """Crash log, uncaught-error logging, and the check for a WoWBox that didn't shut down cleanly."""
        logging.getLogger().addHandler(self._log_counter)
        sys.excepthook = self._excepthook
        threading.excepthook = self._thread_excepthook
        if self.log_dir is None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        crash = self.log_dir / CRASH_LOG
        try:
            if crash.exists() and crash.stat().st_size > CRASH_LOG_MAX:
                crash.replace(crash.with_suffix(".log.1"))
        except OSError:
            pass
        self._check_last_runs()
        self._crash_file = open(crash, "a", encoding="utf-8")
        self._crash_file.write(f"\n=== WoWBox pid {os.getpid()} started {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self._crash_file.flush()
        faulthandler.enable(self._crash_file, all_threads=True)
        self._marker = self.log_dir / f"wowbox-{os.getpid()}.running"
        self._marker.write_text(json.dumps({"pid": os.getpid(), "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                                            "exe": sys.executable, "argv": sys.argv}), encoding="utf-8")

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="diagnostics", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)
            self._thread = None
        if self._crash_file is not None:
            faulthandler.cancel_dump_traceback_later()

    def clean_exit(self) -> None:
        """WoWBox is shutting down normally: no "didn't shut down cleanly" at the next start."""
        self.stop()
        self.health(self._clock(), final=True)
        if self._marker is not None:
            try:
                self._marker.unlink()
            except OSError:
                pass
            self._marker = None
        if self._crash_file is not None:
            faulthandler.disable()
            self._crash_file.close()
            self._crash_file = None

    def _check_last_runs(self) -> None:
        for marker in sorted(self.log_dir.glob(MARKER_GLOB)):
            try:
                info = json.loads(marker.read_text(encoding="utf-8"))
                pid = int(info.get("pid", 0))
            except (OSError, ValueError, TypeError):
                info, pid = {}, 0
            if pid == os.getpid():
                continue
            if pid and _process_alive(pid):
                log.warning("another WoWBox is already running (pid %d): two of them fight over your keys", pid)
                continue
            what = f"the last WoWBox (pid {pid}, started {info.get('started', '?')}) didn't shut down cleanly"
            self.unclean_exit = what
            crash = self._crash_section(pid)
            log.warning("%s: it crashed, froze and was ended, or Windows closed it. Its last lines are just above "
                        "this start in wowbox.log%s", what,
                        f". crash.log has what it was doing:\n{crash}" if crash else " (nothing in crash.log)")
            try:
                marker.unlink()
            except OSError:
                pass

    def _crash_section(self, pid: int, max_lines: int = 80) -> str:
        """What crash.log holds for that run (after its start line), or ""."""
        try:
            text = (self.log_dir / CRASH_LOG).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        head = f"=== WoWBox pid {pid} started"
        at = text.rfind(head)
        if at < 0:
            return ""
        body = text[at:].split("\n", 1)[1] if "\n" in text[at:] else ""
        body = body.split("\n=== WoWBox pid ", 1)[0].strip()
        lines = body.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines in crash.log)"]
        return "\n".join(lines)

    def _excepthook(self, tp, value, tb) -> None:
        log.critical("uncaught error", exc_info=(tp, value, tb))

    def _thread_excepthook(self, args) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        log.critical("uncaught error in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    # ------------------------------------------------------------------ what to watch
    def watch(self, name: str, limit: float, age: Callable[[float], float | None], thread: str | None = None) -> None:
        """Log every thread's stack when `age(now)` (seconds stuck, or None) goes past `limit`."""
        with self._lock:
            self._watches[name] = _Watch(name, limit, age, thread)

    def heartbeat(self, name: str, limit: float, thread: str | None = None,
                  active: Callable[[], bool] = lambda: True) -> Beat:
        """A loop calls the returned beat each time round; going `limit` seconds without one (while `active()`)
        counts as stuck."""
        beat = Beat(self._clock)
        self.watch(name, limit, lambda now: now - beat.last if active() else None, thread)
        return beat

    def report(self, name: str, fn: Callable[[], str | None]) -> None:
        """Add `name: fn()` to the health line (fn resets whatever it counts "since the last line")."""
        self._reports[name] = fn

    def dump_stacks(self, why: str, first: str | None = None) -> None:
        log.warning("%s. %s\n%s", why, self._counters_text(), thread_stacks(first))

    # ------------------------------------------------------------------ the checks
    def _run(self) -> None:
        armed = -1e9
        while not self._stop.wait(CHECK_EVERY):
            now = self._clock()
            if self._crash_file is not None and now - armed > 10:
                faulthandler.dump_traceback_later(FROZEN_DUMP_AFTER, file=self._crash_file)
                armed = now
            try:
                self.check(now)
            except Exception:
                log.exception("diagnostics check failed")

    def check(self, now: float) -> None:
        with self._lock:
            watches = list(self._watches.values())
        for w in watches:
            age = w.age(now)
            if age is not None and age > w.limit:
                if w.stuck_since is None:
                    w.stuck_since, w.reported_at = now - age, now
                    w.stalls += 1
                    self.dump_stacks(f"{w.name} stuck for {age:.1f}s (should take under {w.limit:g}s)", w.thread)
                elif now - w.reported_at >= REPEAT_STUCK:
                    w.reported_at = now
                    self.dump_stacks(f"{w.name} still stuck, for {duration(now - w.stuck_since)} now", w.thread)
            elif w.stuck_since is not None:
                stalled = now - w.stuck_since
                w.worst = max(w.worst, stalled)
                w.stuck_since = None
                log.warning("%s moving again after %.1fs", w.name, stalled)
        if now - self._last_health >= self.health_every:
            self.health(now)

    @staticmethod
    def _cpu_seconds() -> float:
        t = os.times()
        return t.user + t.system

    def _counters_text(self) -> str:
        c = self._safe_counters()
        if not c:
            return f"threads {threading.active_count()}"
        return (f"memory {_mb(c.get('working_set', 0))} (private {_mb(c.get('private', 0))}), "
                f"handles {c.get('handles', '?')}, GDI {c.get('gdi', '?')}, USER {c.get('user', '?')}, "
                f"threads {threading.active_count()}")

    def _safe_counters(self) -> dict[str, int]:
        try:
            return self._counters()
        except Exception:
            return {}

    def health(self, now: float, final: bool = False) -> str:
        """Log (and return) the health line; warn about anything close to a limit."""
        t0, cpu0 = self._cpu
        cpu = self._cpu_seconds()
        self._cpu = (now, cpu)
        self._last_health = now
        load = (cpu - cpu0) / (now - t0) * 100 if now > t0 else 0.0
        parts = [f"up {duration(now - self._started)}, CPU {load:.1f}%", self._counters_text()]
        stalls = []
        with self._lock:
            watches = list(self._watches.values())
        for w in watches:
            if w.stalls:
                stalls.append(f"{w.name} {w.stalls}x (worst {w.worst:.1f}s)")
            w.stalls, w.worst = 0, 0.0
        parts.append("stalls: " + (", ".join(stalls) if stalls else "none"))
        for name, fn in list(self._reports.items()):
            try:
                text = fn()
            except Exception as e:
                text = f"? ({type(e).__name__}: {e})"
            if text:
                parts.append(f"{name}: {text}")
        lc = self._log_counter
        parts.append(f"log: {lc.errors} error(s), {lc.warnings} warning(s)")
        lc.errors = lc.warnings = 0
        line = ("health at exit: " if final else "health: ") + " | ".join(parts)
        log.info(line)
        self._limits(self._safe_counters())
        return line

    def _limits(self, c: dict[str, int]) -> None:
        def warn(key: str, msg: str) -> None:
            if key not in self._warned:
                self._warned.add(key)
                log.warning(msg)
        for kind in ("gdi", "user"):
            if c.get(kind, 0) > GUI_OBJECTS_WARN:
                warn(kind, f"WoWBox is using {c[kind]} {kind.upper()} objects (Windows stops it at 10,000): "
                           "something is leaking them. Restart WoWBox soon and send wowbox.log")
        if c.get("handles", 0) > HANDLES_WARN:
            warn("handles", f"WoWBox has {c['handles']} handles open: something is leaking them")
        if "private" in c:
            if self._first_private is None:
                self._first_private = c["private"]
            elif c["private"] - self._first_private > MEMORY_GROWTH_WARN:
                warn("memory", f"WoWBox's memory grew from {_mb(self._first_private)} to {_mb(c['private'])} "
                               "since it started: something is growing")
