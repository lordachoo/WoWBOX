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
from ..config import SENDER_TEAM, AutomationOptions, AutomationRule, ChatTrigger, Condition, Target
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


def matched_word(trig: ChatTrigger, text: str) -> str | None:
    """The longest of the trigger's words found in the line (lower case), or None."""
    hits = [w.lower() for w in trig.contains if w.lower() in text.lower()]
    return max(hits, key=len) if hits else None


def fmt_left(seconds: float) -> str:
    """12 -> '12s', 725 -> '12m 05s', 3725 -> '1h 02m'"""
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


@dataclass
class RuleStatus:
    state: str  # what the rule is doing now, e.g. "waiting", "cooldown 8s", "running step 2/4"
    next: str  # when it fires next, in words
    progress: float | None = None  # timers: 0..1 of the way to the next firing
    last: str = ""  # "2m 10s ago"
    ready: bool = False  # it would fire now if automation were on (state rule condition true)


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
        self.paused_reason: str | None = None  # summary of what's paused, for the window's banner
        self.blocked: dict[str, str] = {}  # rule name -> why it's paused right now
        self._gone: list[int] = []
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

    def status(self, rt: RuleRuntime, now: float) -> RuleStatus:
        """What a rule is doing and when it fires next, for the Automation tab."""
        r = rt.rule
        last = f"{fmt_left(now - rt.last_fired)} ago" if rt.last_fired is not None else "never"
        if not r.enabled:
            return RuleStatus("disabled", "", None, last)
        if r.name in self.paused_rules:
            return RuleStatus("paused (open in the editor)", "", None, last)
        if rt.running_step is not None:
            steps = rt.run_steps if rt.run_steps is not None else rt.steps
            return RuleStatus(f"running step {rt.running_step}/{len(steps)}", "", None, last)
        cooling = r.cooldown - (now - rt.last_fired) if rt.last_fired is not None and r.cooldown else 0.0
        if not self.enabled:
            state = "automation OFF"
        elif why := self.blocked_reason(rt):
            state = f"paused: {why}"
        elif cooling > 0:
            state = f"cooldown {fmt_left(cooling)}"
        else:
            state = "waiting"
        ready = False
        progress = None
        if r.every is not None:
            if rt.next_due is None or not self.enabled:
                nxt = f"{fmt_left(r.every)} after automation is turned on" if not self.enabled else \
                    f"in {fmt_left(r.every)}"
            else:
                left = rt.next_due - now
                nxt = f"in {fmt_left(left)}" if left > 0 else "now"
                progress = min(1.0, max(0.0, 1 - left / r.every))
        elif r.chat is not None:
            nxt = "on a matching chat line"
            if r.when is not None:
                ok, _ = condition_report(r.when, self.source.states(), rt.preds)
                nxt += " (condition true now)" if ok else " (but the condition is false now)"
        else:
            ok, per = condition_report(r.when, self.source.states(), rt.preds)
            ready = ok
            if ok:
                nxt = "now: condition is true" if cooling <= 0 else "after the cooldown: condition is true"
            else:
                why = next((w for _, m, w in per if not m), "")
                nxt = f"when the condition is true ({why})" if why else "when the condition is true"
        return RuleStatus(state, nxt, progress, last, ready)

    def set_enabled(self, value: bool) -> None:
        self.enabled = value
        for rt in self.rules:
            if not value:
                rt.running_step = None
            else:
                rt.next_due = None  # timers start counting when automation is turned on, never fire at once
        self._event(f"automation {'ON' if value else 'OFF'}{' (dry run)' if value and self.dry_run else ''}")

    def kill(self) -> None:
        self.set_enabled(False)

    # ------------------------------------------------------------------
    def stale_slots(self) -> list[int]:
        """Clients whose bridge can't be read (minimized, covered, addon off). With pause_on_stale_bridge, rules
        that send to or check one of these wait: WoWBox can't see whether you're typing there."""
        if not (self.bridge_enabled and self.options.pause_on_stale_bridge):
            return []
        return sorted(s for s, st in self.source.states().items() if st is None or st.stale)

    def involved_slots(self, rt: RuleRuntime) -> set[int]:
        """The clients a rule checks or sends to (a macro step may send anywhere: all of them)."""
        r = rt.rule
        slots: set[int] = set()
        if r.when is not None:
            if isinstance(r.when.slot, int):
                slots.add(r.when.slot)
            elif isinstance(r.when.slot, list):
                slots.update(r.when.slot)
        for step in rt.steps:
            if isinstance(step, (TypeStep, SendStep)):
                slots.update(self.source.slots_for(step.to))
            elif isinstance(step, MacroStep):
                slots.update(self.source.states())
        return slots

    def blocked_reason(self, rt: RuleRuntime, stale: list[int] | None = None) -> str | None:
        stale = self.stale_slots() if stale is None else stale
        bad = sorted(set(stale) & self.involved_slots(rt)) if stale else []
        return f"can't read the bridge of slot(s) {bad}" if bad else None

    def _update_blocked(self) -> list[int]:
        """Work out which rules are paused now, and log the changes."""
        stale = self.stale_slots()
        blocked = {}
        for rt in self.rules:
            if rt.enabled and (why := self.blocked_reason(rt, stale)):
                blocked[rt.rule.name] = why
        for name, why in blocked.items():
            if self.blocked.get(name) != why:
                self._event(f"rule {name!r} paused: {why}")
        for name in self.blocked.keys() - blocked.keys():
            self._event(f"rule {name!r} resumed")
        self.blocked = blocked
        gone = self.source.missing_windows()
        if gone != self._gone:
            if set(gone) - set(self._gone):
                self._event(f"window closed for slot(s) {sorted(set(gone) - set(self._gone))}: "
                            "rules leave it out and keep running for the others")
            self._gone = gone
        self.paused_reason = (f"{len(blocked)} rule(s) paused: can't read the bridge of slot(s) {stale}"
                              if blocked else None)
        return stale

    def tick(self, now: float) -> None:
        if not self.enabled:
            self.paused_reason = None
            self.blocked = {}
            return
        self._update_blocked()
        states = self.source.states()
        for rt in self.rules:
            if rt.rule.name in self.blocked:
                rt.running_step = None
                continue
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
        matching = [rt for rt in self.rules if rt.enabled and rt.rule.chat is not None
                    and rt.rule.name not in self.paused_rules and self._line_matches(rt.rule.chat, ev)]
        candidates = [rt for rt in self.most_specific(matching, ev.text)
                      if slot in self.source.slots_for(rt.rule.chat.heard_by)]
        if not candidates:
            return
        self._chat_seen = {k: t for k, t in self._chat_seen.items() if now - t < CHAT_DEDUPE}
        values = {"sender": ev.sender, "message": ev.text, "channel": ev.channel, "listener": listener}
        stale = self.stale_slots()
        for rt in candidates:
            if why := self.blocked_reason(rt, stale):
                self._event(f"rule {rt.rule.name!r}: chat from {ev.sender} ignored, paused ({why})")
                continue
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

    def _sender_ok(self, allowed: list[str], sender: str) -> bool:
        names = {n.lower() for n in allowed}
        if SENDER_TEAM in names and sender.lower() in self._team_names():
            return True
        return sender.lower() in names - {SENDER_TEAM}

    def _team_names(self) -> set[str]:
        """Your own characters' names (lower case), for `sender: team`."""
        fn = getattr(self.source, "character_names", None)
        return {n.lower() for n in fn()} if fn else set()

    def _line_matches(self, trig: ChatTrigger, ev: ChatEvent) -> bool:
        """Channel, sender and words match (who heard it is checked separately)."""
        if "any" not in trig.channel and ev.channel not in trig.channel:
            return False
        if trig.sender and not self._sender_ok(trig.sender, ev.sender):
            return False
        return not trig.contains or matched_word(trig, ev.text) is not None

    @staticmethod
    def most_specific(rules: list[RuleRuntime], text: str) -> list[RuleRuntime]:
        """Most specific keyword wins: drop a rule whose matched word is part of a longer word another rule
        matched, so "light heal" doesn't also fire the "heal" rule. Rules without words are kept."""
        words = {rt.rule.name: matched_word(rt.rule.chat, text) for rt in rules}
        return [rt for rt in rules
                if (w := words[rt.rule.name]) is None
                or not any(o is not None and len(o) > len(w) and w in o for o in words.values())]

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
