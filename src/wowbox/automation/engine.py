"""Rule evaluation, timers and sequences on top of the router.

`tick(now)` is called periodically (and after bridge updates). It never blocks: running sequences
remember which step they're on and when to resume.
"""
from __future__ import annotations

import logging
import operator
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..bridge.decoder import STATE_FIELDS, ChatEvent, ClientState
from ..config import AutomationOptions, AutomationRule, ChatTrigger, Condition, Target
from ..keys import Chord
from .actions import MacroStep, SendStep, Step, TypeStep, WaitStep, describe, flatten, substitute

log = logging.getLogger(__name__)

OPS: dict[str, Callable[[object, object], bool]] = {
    "_lt": operator.lt, "_lte": operator.le, "_gt": operator.gt, "_gte": operator.ge, "_ne": operator.ne,
}
CHAT_DEDUPE = 2.0  # the same party/raid line heard by several of your clients fires a rule once


class Sender(Protocol):
    def send_chord(self, chord: Chord, target: Target) -> list[int]: ...

    def type_text(self, text: str, target: Target) -> list[int]: ...


class StateSource(Protocol):
    """What the engine needs from the team."""

    def states(self) -> dict[int, ClientState | None]: ...

    def missing_windows(self) -> list[int]: ...

    def slots_for(self, target: Target) -> list[int]: ...


def compile_condition(cond: Condition) -> list[tuple[str, Callable[[object, object], bool], object]]:
    preds = []
    for key, value in (cond.model_extra or {}).items():
        op_fn, fld = operator.eq, key
        for suffix, fn in OPS.items():
            if key.endswith(suffix) and key[: -len(suffix)] in STATE_FIELDS:
                op_fn, fld = fn, key[: -len(suffix)]
                break
        if fld not in STATE_FIELDS:
            raise ValueError(f"unknown condition field {key!r}; fields: {', '.join(sorted(STATE_FIELDS))}")
        preds.append((fld, op_fn, value))
    return preds


OP_WORDS = {operator.eq: "=", operator.ne: "≠", operator.lt: "<", operator.le: "≤", operator.gt: ">",
            operator.ge: "≥"}


def condition_report(cond: Condition, states: dict[int, ClientState | None], preds: list | None = None
                     ) -> tuple[bool, list[tuple[int, bool, str]]]:
    """Whether `cond` matches now, and per character: (slot, matches, why not). `slot: all` needs every
    listed character to match, anything else any one of them."""
    preds = compile_condition(cond) if preds is None else preds
    if isinstance(cond.slot, int):
        slots = [cond.slot]
    elif isinstance(cond.slot, list):
        slots = list(cond.slot)
    else:
        slots = sorted(states)
    per: list[tuple[int, bool, str]] = []
    for slot in slots:
        st = states.get(slot)
        if st is None or st.stale:
            per.append((slot, False, "no bridge data"))
            continue
        failed = next(((fld, fn, val) for fld, fn, val in preds if not fn(getattr(st, fld), val)), None)
        if failed is None:
            per.append((slot, True, ""))
        else:
            fld, fn, val = failed
            per.append((slot, False, f"{fld} is {_show(getattr(st, fld))}, needs {OP_WORDS.get(fn, '?')} {_show(val)}"))
    if not per:
        return False, per
    hits = [ok for _, ok, _ in per]
    return (all(hits) if cond.slot == "all" else any(hits)), per


def _show(v: object) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


@dataclass
class RuleRuntime:
    rule: AutomationRule
    steps: list[Step]
    preds: list = field(default_factory=list)
    last_fired: float | None = None
    next_due: float | None = None
    running_step: int | None = None  # index into steps while a sequence is in flight
    resume_at: float = 0.0
    fire_count: int = 0
    run_steps: list[Step] | None = None  # steps with chat placeholders filled in, for the current run

    @property
    def enabled(self) -> bool:
        return self.rule.enabled


class AutomationEngine:
    def __init__(self, rules: list[AutomationRule], options: AutomationOptions, sender: Sender,
                 source: StateSource, bridge_enabled: bool = True) -> None:
        self.options = options
        self.sender = sender
        self.source = source
        self.bridge_enabled = bridge_enabled
        self.enabled = options.enabled_on_start
        self.dry_run = options.dry_run
        self.paused_reason: str | None = None
        self.on_event: Callable[[str], None] = lambda msg: None
        self.on_macro: Callable[[str], None] = lambda name: log.warning("no macro runner for %r", name)
        self._chat_seen: dict[tuple, float] = {}
        self.rules: list[RuleRuntime] = []
        self.paused_rules: set[str] = set()  # e.g. while a rule is open in the editor
        self.load(rules)

    def load(self, rules: list[AutomationRule]) -> None:
        """(Re)load rules. A rule that keeps its name keeps its fire count and cooldown."""
        old = {rt.rule.name: rt for rt in self.rules} if hasattr(self, "rules") else {}
        runtimes = []
        for r in rules:
            rt = RuleRuntime(r, flatten(r.do), compile_condition(r.when) if r.when else [])
            if prev := old.get(r.name):
                rt.last_fired, rt.fire_count = prev.last_fired, prev.fire_count
                if prev.rule.every == r.every:
                    rt.next_due = prev.next_due
            runtimes.append(rt)
        self.rules = runtimes

    def set_enabled(self, value: bool) -> None:
        self.enabled = value
        if not value:
            for rt in self.rules:
                rt.running_step = None
        self._event(f"automation {'ON' if value else 'OFF'}{' (dry run)' if value and self.dry_run else ''}")

    def kill(self) -> None:
        self.set_enabled(False)

    # ------------------------------------------------------------------
    def _check_pause(self) -> str | None:
        missing = self.source.missing_windows()
        if missing:
            return f"window lost for slot(s) {missing}"
        if self.bridge_enabled and self.options.pause_on_stale_bridge:
            bad = [s for s, st in self.source.states().items() if st is None or st.stale]
            if bad:
                return f"bridge stale/missing for slot(s) {bad}"
        return None

    def tick(self, now: float) -> None:
        if not self.enabled:
            self.paused_reason = None
            return
        reason = self._check_pause()
        if reason != self.paused_reason:
            self.paused_reason = reason
            self._event(f"automation paused: {reason}" if reason else "automation resumed")
        if reason:
            for rt in self.rules:
                rt.running_step = None
            return
        states = self.source.states()
        for rt in self.rules:
            if rt.running_step is not None:
                self._advance(rt, now)
                continue
            if not rt.enabled or rt.rule.chat is not None or rt.rule.name in self.paused_rules:
                continue
            if rt.last_fired is not None and now - rt.last_fired < rt.rule.cooldown:
                continue
            if rt.rule.every is not None:
                if rt.next_due is None:
                    rt.next_due = now + rt.rule.every
                    continue
                if now < rt.next_due:
                    continue
                rt.next_due = now + rt.rule.every
            elif not self._matches(rt, states):
                continue
            self._fire(rt, now)

    def _matches(self, rt: RuleRuntime, states: dict[int, ClientState | None]) -> bool:
        assert rt.rule.when is not None
        return condition_report(rt.rule.when, states, rt.preds)[0]

    # ------------------------------------------------------------------ chat
    def on_chat(self, slot: int, ev: ChatEvent, now: float, listener: str = "") -> None:
        """A client heard a chat line (from its BoxBridge). Fires matching `chat:` rules."""
        if not self.enabled:
            return
        candidates = [rt for rt in self.rules if rt.enabled and rt.rule.chat is not None
                      and rt.rule.name not in self.paused_rules
                      and self._chat_matches(rt.rule.chat, slot, ev)]
        if not candidates:
            return
        if self.paused_reason:
            self._event(f"chat from {ev.sender} ignored: automation paused ({self.paused_reason})")
            return
        self._chat_seen = {k: t for k, t in self._chat_seen.items() if now - t < CHAT_DEDUPE}
        values = {"sender": ev.sender, "message": ev.text, "channel": ev.channel, "listener": listener}
        for rt in candidates:
            key = (rt.rule.name, ev.channel, ev.sender.lower(), ev.text)
            if key in self._chat_seen:
                continue
            self._chat_seen[key] = now
            if rt.rule.when is not None and not self._matches(rt, self.source.states()):
                continue
            if rt.running_step is not None:
                self._event(f"rule {rt.rule.name!r}: still running, chat from {ev.sender} skipped")
                continue
            if rt.last_fired is not None and now - rt.last_fired < rt.rule.cooldown:
                self._event(f"rule {rt.rule.name!r}: on cooldown, chat from {ev.sender} skipped")
                continue
            self._fire(rt, now, substitute(rt.steps, values),
                       f"{ev.channel} from {ev.sender}: {ev.text!r}")

    def _chat_matches(self, trig: ChatTrigger, slot: int, ev: ChatEvent) -> bool:
        if "any" not in trig.channel and ev.channel not in trig.channel:
            return False
        if trig.sender and ev.sender.lower() not in {n.lower() for n in trig.sender}:
            return False
        if trig.contains and not any(w.lower() in ev.text.lower() for w in trig.contains):
            return False
        return slot in self.source.slots_for(trig.heard_by)

    # ------------------------------------------------------------------ running
    def _fire(self, rt: RuleRuntime, now: float, steps: list[Step] | None = None, why: str = "") -> None:
        rt.last_fired = now
        rt.fire_count += 1
        rt.run_steps = steps
        self._event(f"rule {rt.rule.name!r} fired{f' ({why})' if why else ''}: {describe(steps or rt.steps)}")
        rt.running_step = 0
        rt.resume_at = now
        self._advance(rt, now)

    def _advance(self, rt: RuleRuntime, now: float) -> None:
        steps = rt.run_steps if rt.run_steps is not None else rt.steps
        while rt.running_step is not None and now >= rt.resume_at:
            if rt.running_step >= len(steps):
                rt.running_step = None
                rt.run_steps = None
                return
            step = steps[rt.running_step]
            rt.running_step += 1
            if isinstance(step, WaitStep):
                rt.resume_at = now + step.seconds
            elif isinstance(step, MacroStep):
                if self.dry_run:
                    self._event(f"  [dry run] would start macro {step.name!r}")
                else:
                    self._event(f"  started macro {step.name!r}")
                    self.on_macro(step.name)
            elif isinstance(step, TypeStep):
                if self.dry_run:
                    self._event(f"  [dry run] would type {step.text!r} -> {step.to}")
                else:
                    self._event(f"  typed {step.text!r} -> slots {self.sender.type_text(step.text, step.to)}")
            elif isinstance(step, SendStep):
                if self.dry_run:
                    self._event(f"  [dry run] would send {step.chord} -> {step.to}")
                else:
                    slots = self.sender.send_chord(step.chord, step.to)
                    self._event(f"  sent {step.chord} -> slots {slots}")

    def _event(self, msg: str) -> None:
        log.info(msg)
        self.on_event(msg)
