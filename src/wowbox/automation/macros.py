"""Macros: named step sequences you start with a hotkey or the Run button. Press the hotkey again to stop.

Unlike automation rules they run whenever you ask: no enable switch, no dry run, no bridge requirement.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Protocol

from ..config import MacroConfig, Target
from ..keys import Chord, Mod
from .actions import MacroStep, SendStep, Step, TypeStep, WaitStep, describe, flatten

log = logging.getLogger(__name__)

MOD_RELEASE_TIMEOUT = 1.0


class MacroSender(Protocol):
    def send_chord(self, chord: Chord, target: Target, check_typing: bool = True) -> list[int]: ...

    def type_text(self, text: str, target: Target) -> list[int]: ...

    @property
    def held_mods(self) -> Mod: ...


class MacroRunner:
    def __init__(self, macros: list[MacroConfig], sender: MacroSender,
                 on_event: Callable[[str], None] = lambda msg: None) -> None:
        self.sender = sender
        self.on_event = on_event
        self._running: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.macros: dict[str, tuple[MacroConfig, list[Step]]] = {}
        self.load(macros)

    def load(self, macros: list[MacroConfig]) -> None:
        self.macros = {m.name: (m, flatten(m.do)) for m in macros}

    def saved(self) -> list[str]:
        return [n for n in self.macros if n != self.TEST]

    TEST = "(test)"

    def run_config(self, macro: MacroConfig) -> threading.Thread | None:
        """Run a macro that isn't saved yet (the editor's Test button)."""
        self.stop(self.TEST)
        self.macros[self.TEST] = (macro, flatten(macro.do))
        return self.start(self.TEST)

    def describe(self, name: str) -> str:
        return describe(self.macros[name][1])

    def is_running(self, name: str) -> bool:
        return name in self._running

    def toggle(self, name: str) -> None:
        if self.is_running(name):
            self.stop(name)
        else:
            self.start(name)

    def start(self, name: str) -> threading.Thread | None:
        if name not in self.macros:
            log.warning("no macro named %r", name)
            return None
        with self._lock:
            if name in self._running:
                return None
            cancel = threading.Event()
            self._running[name] = cancel
        t = threading.Thread(target=self._run, args=(name, self.macros[name][1], cancel), name=f"macro:{name}",
                             daemon=True)
        t.start()
        return t

    def stop(self, name: str) -> None:
        ev = self._running.get(name)
        if ev:
            ev.set()

    def stop_all(self) -> None:
        for ev in list(self._running.values()):
            ev.set()

    def _run(self, name: str, steps: list[Step], cancel: threading.Event) -> None:
        self._event(f"macro {name!r} started")
        try:
            # Wait for the hotkey's Ctrl/Alt/Shift to be let go, or the first key lands as a chord in this window.
            deadline = time.monotonic() + MOD_RELEASE_TIMEOUT
            while self.sender.held_mods and time.monotonic() < deadline and not cancel.is_set():
                time.sleep(0.01)
            for step in steps:
                if cancel.is_set():
                    self._event(f"macro {name!r} stopped")
                    return
                if isinstance(step, WaitStep):
                    if cancel.wait(step.seconds):
                        self._event(f"macro {name!r} stopped")
                        return
                elif isinstance(step, MacroStep):
                    if step.name == name:
                        log.warning("macro %r can't start itself", name)
                    else:
                        self.start(step.name)
                elif isinstance(step, TypeStep):
                    slots = self.sender.type_text(step.text, step.to)
                    log.debug("macro %s: typed %r -> %s", name, step.text, slots)
                elif isinstance(step, SendStep):
                    slots = self.sender.send_chord(step.chord, step.to, check_typing=False)
                    log.debug("macro %s: %s -> %s", name, step.chord, slots)
            self._event(f"macro {name!r} done")
        except Exception:
            log.exception("macro %r failed", name)
        finally:
            with self._lock:
                self._running.pop(name, None)

    def _event(self, msg: str) -> None:
        log.info(msg)
        self.on_event(msg)
