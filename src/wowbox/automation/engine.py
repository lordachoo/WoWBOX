"""Rule evaluation, timers and sequences on top of the router.

`tick(now)` is called periodically (and after bridge updates). It never blocks: running sequences
remember which step they're on and when to resume.
"""
from __future__ import annotations

import logging
from collections import deque
import operator
import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..bridge.decoder import STATE_FIELDS, ChatEvent, ClientState
from ..config import SENDER_TEAM, AutomationOptions, AutomationRule, ChatTrigger, Condition, Target
from ..keys import Chord
from .actions import MacroStep, SendStep, Step, TypeStep, WaitStep, describe, flatten, safe_values, substitute

log = logging.getLogger(__name__)

OPS: dict[str, Callable[[object, object], bool]] = {
    "_lt": operator.lt, "_lte": operator.le, "_gt": operator.gt, "_gte": operator.ge, "_ne": operator.ne,
}
CLAIM_RECHECK = 0.05  # a step queued behind another rule's waiting step looks again this soon
RULE_NAME = re.compile(r"^\s*rule (['\"])(.+?)\1")  # "rule 'heal' fired ..." -> heal
CHAT_DEDUPE = 2.0
# a state rule without a cooldown fires again at most this often: a cast that keeps failing (out of range) never
# goes on cooldown in the game, so its condition stays true and would fire on every tick. A cooldown the rule sets
# itself is kept, shorter too (a 500 ms /assist)
STATE_MIN_GAP = 1.0  # the same party/raid line heard by several of your clients fires a rule once


class Sender(Protocol):
    def send_chord(self, chord: Chord, target: Target) -> list[int]: ...

    def type_text(self, text: str, target: Target) -> list[int]: ...


class StateSource(Protocol):
    """What the engine needs from the team."""

    def states(self) -> dict[int, ClientState | None]: ...

    def missing_windows(self) -> list[int]: ...

    def slots_for(self, target: Target) -> list[int]: ...


ARG_VALUES = ("who", "arg", "arg2", "rest")  # from the end of a chat line: a cut can lose them


def _args_used(steps: list[Step]) -> list[str]:
    """Which of {who} {arg} {arg2} {rest} the steps use (also as {x|y})."""
    text = describe(steps)
    return [k for k in ARG_VALUES if re.search(r"\{" + k + r"[}|]", text)]


def _rules(names: list[str]) -> str:
    """"rule 'x'" for one (so the activity feed files it under that rule), else "5 rules (a, b, c, ...)"."""
    if len(names) == 1:
        return f"rule {names[0]!r}"
    shown = ", ".join(names[:3]) + (", ..." if len(names) > 3 else "")
    return f"{len(names)} rules ({shown})"


def _ready(have: tuple[str, ...], want: object) -> bool:
    """`ready: Avenger's Shield`: that watched spell can be cast now (any case; a list: all of them)."""
    names = [want] if isinstance(want, str) else list(want or [])
    return all(str(n).strip().lower() in have for n in names)


def _not_ready(have: tuple[str, ...], want: object) -> bool:
    names = [want] if isinstance(want, str) else list(want or [])
    return not any(str(n).strip().lower() in have for n in names)


def _same_target(mine: int, other: int | None) -> bool:
    """`same_target: Tinkerdawn`: this character's target is the same mob as that one's (both have one)."""
    return bool(mine) and mine == other


def _other_target(mine: int, other: int | None) -> bool:
    """`same_target_ne: Tinkerdawn`: this character has a target, and it isn't that one's (e.g. to /assist again)."""
    return bool(mine) and mine != other


def _ally_attacking(_mine: object, other: ClientState) -> bool:
    """`ally_attacking: Tinkerdawn`: that character's auto-attack is on (so this one only fights when they do)."""
    return other.attacking


def _find_state(states: dict[int, ClientState | None], who: object) -> ClientState | None:
    """The state of the character `who` names: a slot number, or a character name (any case)."""
    if isinstance(who, int) or str(who).strip().isdigit():
        return states.get(int(who))  # type: ignore[arg-type]
    low = str(who).strip().lower()
    return next((st for st in states.values() if st is not None and st.name.lower() == low), None)


def compile_condition(cond: Condition) -> list[tuple[str, Callable[[object, object], bool], object]]:
    preds = []
    for key, value in (cond.model_extra or {}).items():
        if key in ("ready", "ready_ne", "dot", "dot_ne", "buff_missing", "buff_missing_ne"):  # names in a list
            fld = {"ready": "ready", "dot": "dots", "buff_missing": "buffs"}[key.removesuffix("_ne")]  # the bridge
            preds.append((fld, _not_ready if key.endswith("_ne") else _ready, value))  # reports
            continue
        if key in ("same_target", "same_target_ne"):  # compared with another character's state (condition_report)
            preds.append(("target_id", _other_target if key.endswith("_ne") else _same_target, value))
            continue
        if key == "ally_attacking":  # another character's auto-attack
            preds.append(("attacking", _ally_attacking, value))
            continue
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


def condition_report(cond: Condition, states: dict[int, ClientState | None], preds: list | None = None,
                     skip_dead: bool = True, far: dict[int, str] | None = None
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
    asks_about_death = any(fld in ("dead", "ghost") for fld, _, _ in preds)
    for slot in slots:
        st = states.get(slot)
        if st is None or st.stale:
            per.append((slot, False, "no bridge data"))
            continue
        if skip_dead and not asks_about_death and (st.dead or st.ghost):
            per.append((slot, False, "dead"))
            continue
        if far and slot in far:
            per.append((slot, False, far[slot]))
            continue
        failed = next(((fld, fn, val) for fld, fn, val in preds if not _holds(st, fld, fn, val, states)), None)
        if failed is None:
            per.append((slot, True, ""))
        else:
            fld, fn, val = failed
            if fn in (_same_target, _other_target):
                other = _find_state(states, val)
                why = ("no target" if not st.target_id else f"no bridge data from {_show(val)}"
                       if other is None or other.stale else f"on {_show(val)}'s target" if fn is _other_target
                       else f"{_show(val)} has no target" if not other.target_id else f"not {_show(val)}'s target")
                per.append((slot, False, why))
            elif fn is _ally_attacking:
                other = _find_state(states, val)
                why = (f"no bridge data from {_show(val)}" if other is None or other.stale
                       else f"{_show(val)} isn't attacking")
                per.append((slot, False, why))
            elif fld == "ready":
                per.append((slot, False, f"{_show(val)} {'not ready' if fn is _ready else 'ready'}"))
            elif fld == "dots":
                why = "not needed (on the target, or not castable)" if fn is _ready else "needed"
                per.append((slot, False, f"{_show(val)} {why}"))
            elif fld == "buffs":
                why = "not needed (on you, or not castable)" if fn is _ready else "needed"
                per.append((slot, False, f"{_show(val)} {why}"))
            else:
                per.append((slot, False, f"{fld} is {_show(getattr(st, fld))}, needs {OP_WORDS.get(fn, '?')} "
                                         f"{_show(val)}"))
    if not per:
        return False, per
    hits = [ok for _, ok, _ in per]
    return (all(hits) if cond.slot == "all" else any(hits)), per


def _holds(st: ClientState, fld: str, fn: Callable, val: object, states: dict[int, ClientState | None]) -> bool:
    if fn in (_same_target, _other_target):
        other = _find_state(states, val)
        return other is not None and not other.stale and fn(st.target_id, other.target_id)
    if fn is _ally_attacking:
        other = _find_state(states, val)
        return other is not None and not other.stale and fn(None, other)
    return fn(getattr(st, fld), val)


def matched_word(trig: ChatTrigger, text: str) -> str | None:
    """The longest of the trigger's words found in the line (lower case), or None."""
    hits = [w.lower() for w in trig.contains if w.lower() in text.lower()]
    return max(hits, key=len) if hits else None


FILLER_WORDS = {"on", "for", "to", "at", "the", "pls", "plz", "please", "now", "thx", "ty", "thanks"}
SELF_WORDS = {"me", "self", "myself", "i"}
UNIT_WORDS = {"target", "focus", "mouseover", "pet", "player", "targettarget"}  # WoW's own words, used as they are
# "shaman buff team" / "... group": one run per person. team = your characters, group = everyone in the party
GROUP_WORDS = {"team": "team", "group": "group", "party": "group"}


def after_word(text: str, word: str | None) -> str:
    """The text after the matched word ("heal on gno pls", "heal" -> "on gno pls"). A word matched inside a longer
    one ("heal" in "healing") counts from the end of that one. No word: the whole line."""
    if not word:
        return text.strip()
    at = text.lower().find(word)
    if at < 0:
        return ""
    rest = text[at + len(word):]
    if rest and not rest[0].isspace():  # in the middle of a word: skip the rest of it
        rest = rest.split(None, 1)[1] if len(rest.split(None, 1)) > 1 else ""
    return rest.strip()


def chat_args(text: str, word: str | None) -> dict[str, str]:
    """{arg} {arg2} {rest}: the words after the matched word."""
    rest = after_word(text, word)
    parts = rest.split()
    return {"rest": rest, "arg": parts[0] if parts else "", "arg2": parts[1] if len(parts) > 1 else ""}


def resolve_who(rest: str, sender: str, names: set[str], aliases: dict[str, str]) -> str:
    """{who}: who "someone" is in "heal <someone>". Filler words are skipped; nothing, or me/self, is the sender;
    target/focus/... stay as they are; then an alias (or role) from team.yaml; then the start of exactly one of
    your characters' names; otherwise the word as typed (someone else in the group)."""
    words = [w.strip(".,!?") for w in rest.split()]
    words = [w for w in words if w and w.lower() not in FILLER_WORDS]
    if not words or words[0].lower() in SELF_WORDS:
        return sender
    w = words[0]
    low = w.lower()
    if low in UNIT_WORDS:
        return low
    if low in aliases:
        return aliases[low]
    if low in GROUP_WORDS:
        return GROUP_WORDS[low]
    exact = [n for n in names if n.lower() == low]
    if exact:
        return exact[0]
    starts = [n for n in names if n.lower().startswith(low)]
    return starts[0] if len(starts) == 1 else w


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
class QueueItem:
    """One line of the live queue view: what a rule is doing, or waiting to do, on some characters."""
    slots: list[int]  # the characters it acts on
    rule: str
    kind: str  # "running", "waiting" (its next step waits for a cast / cooldown / another rule) or "queued"
    who: str = ""  # the chat request it's for
    detail: str = ""
    priority: int = 0
    order: int = 0  # position among the queued requests of its rule (1 = next)
    first: bool = False  # it holds the turn on its characters: it goes before the other waiting rules


@dataclass
class _Request:
    sender: str
    steps: list[Step]
    why: str
    queued_at: float
    target: str = ""  # {who}, lower case: cooldown_per target


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
    pending: deque = field(default_factory=deque)  # chat requests waiting their turn: _Request
    current_sender: str | None = None  # whose request the current run is for (chat rules)
    last_sender: str | None = None  # whose request the last run was for: cooldown still stops their repeats
    wait_reason: str = ""  # why the current step is waiting, for the Status column
    cast_wait_since: float | None = None  # when the current step started waiting for someone's cast
    target_fired: dict[str, float] = field(default_factory=dict)  # cooldown_per target: {who} -> last firing
    current_target: str = ""  # cooldown_per target: who the current run is for

    def cooling(self, now: float, target: str = "") -> float:
        """Seconds of cooldown left (for that target, with cooldown_per target)."""
        r = self.rule
        last = self.target_fired.get(target) if r.cooldown_per == "target" else self.last_fired
        return r.cooldown - (now - last) if last is not None and r.cooldown else 0.0

    def cooling_targets(self, now: float) -> list[tuple[str, float]]:
        return [(t, self.rule.cooldown - (now - at)) for t, at in self.target_fired.items()
                if now - at < self.rule.cooldown]

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
        # (rule name, what happened, characters it went to): for the Activity tab
        self.on_activity: Callable[[str, str, list[int]], None] = lambda name, text, slots: None
        self._ctx = ""  # the rule being worked on, for step lines that don't name it
        self.on_macro: Callable[[str], None] = lambda name: log.warning("no macro runner for %r", name)
        self._chat_seen: dict[tuple, float] = {}
        self.rules: list[RuleRuntime] = []
        self.paused_rules: set[str] = set()  # e.g. while a rule is open in the editor
        self._slot_last: dict[int, tuple[float, str]] = {}  # slot -> (time, rule) of the last step sent there
        self._claims: dict[int, tuple[str, int]] = {}  # slot -> (rule, priority) first in line for it
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
        self._claims.clear()  # the new runtimes wait for nothing yet: an old claim would block that character forever

    def status(self, rt: RuleRuntime, now: float) -> RuleStatus:
        """What a rule is doing and when it fires next, for the Automation tab."""
        r = rt.rule
        last = f"{fmt_left(now - rt.last_fired)} ago" if rt.last_fired is not None else "never"
        if not r.enabled:
            return RuleStatus("disabled", "", None, last)
        if r.name in self.paused_rules:
            return RuleStatus("paused (open in the editor)", "", None, last)
        queued = f" · queued: {', '.join(q.sender for q in rt.pending)}" if rt.pending else ""
        if rt.running_step is not None:
            steps = rt.run_steps if rt.run_steps is not None else rt.steps
            who = f" for {rt.current_sender}" if rt.current_sender else ""
            waiting = f" · waiting: {rt.wait_reason}" if rt.wait_reason else ""
            nxt = f"next: {rt.pending[0].sender}" if rt.pending else ""
            return RuleStatus(f"running step {rt.running_step}/{len(steps)}{who}{waiting}{queued}", nxt, None, last)
        cooling = rt.cooling(now) if r.cooldown_per == "rule" else 0.0
        per_target = ", ".join(f"{t} {fmt_left(left)}" for t, left in rt.cooling_targets(now)) \
            if r.cooldown_per == "target" else ""
        if not self.enabled:
            state = "automation OFF"
        elif why := self.blocked_reason(rt):
            state = f"paused: {why}"
        elif cooling > 0:
            state = f"cooldown {fmt_left(cooling)}"
        else:
            state = "waiting"
        if per_target:
            state += f" · cooldown for {per_target}"
        state += queued
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
        elif r.chat is not None and rt.pending:
            nxt = f"next: {rt.pending[0].sender}" + (f", after the cooldown ({fmt_left(cooling)})" if cooling > 0 else "")
        elif r.chat is not None:
            nxt = "on a matching chat line"
            if r.when is not None:
                ok, _, _ = self._check(rt, self.source.states())
                nxt += " (condition true now)" if ok else " (but the condition is false now)"
        else:
            ok, per, values = self._check(rt, self.source.states())
            ready = ok
            if ok:
                nxt = "now: condition is true" if cooling <= 0 else "after the cooldown: condition is true"
            else:
                far = [f"{self._names([s])} {w}" for s, m, w in per if not m and w.startswith("too far")]
                misses = [w for _, m, w in per if not m]  # "too far" explains a hurt character not being healed
                why = far[0] if far else misses[0] if misses else ""
                nxt = f"when the condition is true ({why})" if why else "when the condition is true"
        return RuleStatus(state, nxt, progress, last, ready)

    def queue_view(self, now: float) -> list[QueueItem]:
        """Everything running, waiting and queued right now, for the queue panel."""
        items: list[QueueItem] = []
        for rt in self.rules:
            name, prio = rt.rule.name, rt.rule.priority
            if rt.running_step is not None:
                steps = rt.run_steps if rt.run_steps is not None else rt.steps
                nxt = next((s for s in steps[rt.running_step:] if isinstance(s, (TypeStep, SendStep))), None)
                slots = sorted(self.source.slots_for(nxt.to)) if nxt else sorted(self._send_slots(rt))
                what = f"step {min(rt.running_step + 1, len(steps))}/{len(steps)}"
                if nxt:
                    what += f": {describe([nxt])}"
                if rt.last_fired is not None:
                    what = f"running {fmt_left(now - rt.last_fired)} · {what}"
                if rt.wait_reason:
                    first = any(self._claims.get(s, ("",))[0] == name for s in slots)
                    items.append(QueueItem(slots, name, "waiting", rt.current_sender or "",
                                           f"{what} · {rt.wait_reason}", prio, first=first))
                else:
                    left = rt.resume_at - now
                    pause = f" · in {fmt_left(left)}" if left > 0.05 else ""
                    items.append(QueueItem(slots, name, "running", rt.current_sender or "", what + pause, prio))
            for i, req in enumerate(rt.pending, 1):
                age = now - req.queued_at
                left = self.options.chat_queue_ttl - age
                items.append(QueueItem(sorted(self._send_slots(rt)), name, "queued", req.sender,
                                       f"#{i} · asked {fmt_left(age)} ago · expires in {fmt_left(left)}", prio, i))
        return items

    def _send_slots(self, rt: RuleRuntime) -> set[int]:
        """The characters a rule's steps go to."""
        slots: set[int] = set()
        for step in rt.steps:
            if isinstance(step, (TypeStep, SendStep)):
                slots.update(self.source.slots_for(step.to))
        return slots

    def set_enabled(self, value: bool) -> None:
        self.enabled = value
        for rt in self.rules:
            if not value:
                rt.running_step = None
                rt.pending.clear()
                self._release(rt)
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
        newly: dict[str, list[str]] = {}  # one line per reason, not one per rule
        for name, why in blocked.items():
            if self.blocked.get(name) != why:
                newly.setdefault(why, []).append(name)
        for why, names in newly.items():
            self._event(f"{_rules(names)} paused: {why}")
        if back := sorted(self.blocked.keys() - blocked.keys()):
            self._event(f"{_rules(back)} resumed")
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
                self._release(rt)
                continue
            if rt.running_step is not None:
                self._advance(rt, now)
                continue
            if not rt.enabled or rt.rule.name in self.paused_rules:
                continue
            if rt.rule.chat is not None:
                self._next_request(rt, now, states)
                continue
            gap = rt.rule.cooldown if rt.rule.every is not None or rt.rule.cooldown > 0 else STATE_MIN_GAP
            if rt.last_fired is not None and now - rt.last_fired < gap:
                continue
            if rt.rule.every is not None:
                if rt.next_due is None:
                    rt.next_due = now + rt.rule.every
                    continue
                if now < rt.next_due:
                    continue
                rt.next_due = now + rt.rule.every
            else:
                ok, _, values = self._check(rt, states)
                if not ok:
                    continue
                self._fire(rt, now, substitute(rt.steps, values) if values else None)
                continue
            self._fire(rt, now)

    def _matches(self, rt: RuleRuntime, states: dict[int, ClientState | None]) -> bool:
        return self._check(rt, states)[0]

    def _check(self, rt: RuleRuntime, states: dict[int, ClientState | None]
               ) -> tuple[bool, list[tuple[int, bool, str]], dict[str, str]]:
        """The rule's condition: (matches, per character, values for {name} {slot} {class} {health} {power}).
        Dead characters and those far from where the rule acts don't count (skip_dead, skip_far)."""
        assert rt.rule.when is not None
        ok, per = condition_report(rt.rule.when, states, rt.preds, self.options.skip_dead, self._far(rt, states))
        return ok, per, self._matched_values(rt, per, states) if ok else {}

    def _still_holds(self, rt: RuleRuntime) -> bool:
        """The condition again when a waiting rule's turn comes, without `ready:`, `dot:` and `buff_missing:`:
        the cast of the rule before it has just put every spell on the global cooldown, so those read "not now" for a
        moment."""
        assert rt.rule.when is not None
        states = self.source.states()
        preds = [p for p in rt.preds if p[0] not in ("ready", "dots", "buffs")]
        return condition_report(rt.rule.when, states, preds, self.options.skip_dead, self._far(rt, states))[0]

    def _far(self, rt: RuleRuntime, states: dict[int, ClientState | None]) -> dict[int, str]:
        """Characters too far from every character the rule acts on: slot -> why (e.g. "in another zone")."""
        apart = getattr(self.source, "too_far_apart", None)
        if not self.options.skip_far or apart is None:
            return {}
        targets = sorted(self._send_slots(rt))
        if not targets:
            return {}
        far = {}
        for s in states:
            if s in targets:
                continue
            whys = [apart(s, t) for t in targets]
            if all(whys):
                far[s] = f"too far from {self._names(targets)}: {whys[0]}"
        return far

    def _matched_values(self, rt: RuleRuntime, per: list[tuple[int, bool, str]],
                        states: dict[int, ClientState | None]) -> dict[str, str]:
        """The character the condition matched, for {name} and friends. Several: the lowest health (or power,
        if the condition is about power), else the first."""
        matched = [s for s, m, _ in per if m and states.get(s) is not None]
        if not matched:
            return {}
        fields = {fld for fld, _, _ in rt.preds}
        if any(f.startswith("health") for f in fields):
            matched.sort(key=lambda s: states[s].health_pct)
        elif any(f.startswith("power") for f in fields):
            matched.sort(key=lambda s: states[s].power_pct)
        s = matched[0]
        st = states[s]
        return {"name": st.name or self._names([s]), "slot": str(s), "class": st.class_token,
                "health": f"{st.health_pct:.0f}", "power": f"{st.power_pct:.0f}"}

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
        base = {"sender": ev.sender, "message": ev.text, "channel": ev.channel, "listener": listener}
        stale = self.stale_slots()
        for rt in candidates:
            if why := self.blocked_reason(rt, stale):
                self._event(f"rule {rt.rule.name!r}: chat from {ev.sender} ignored, paused ({why})")
                continue
            key = (rt.rule.name, ev.channel, ev.sender.lower(), ev.text)
            if key in self._chat_seen:
                continue
            self._chat_seen[key] = now
            values = self.chat_values(rt.rule.chat, ev, listener, base)
            group = values["who"] in ("team", "group") and "who" in _args_used(rt.steps)
            if not group:
                self._dispatch(rt, ev, values, now)
                continue
            members = self._members(values["who"], slot)
            if not members:
                self._event(f"rule {rt.rule.name!r}: chat from {ev.sender} asks for the {values['who']}, "
                            "but nobody is in it")
                continue
            self._event(f"rule {rt.rule.name!r}: {{who}}={values['who']} -> {', '.join(members)}")
            for who in members:  # the first runs now (if it can), the others queue behind it
                self._dispatch(rt, ev, {**values, "who": who}, now, whole_group=True)

    def _members(self, group: str, slot: int) -> list[str]:
        """Who "team" or "group" means: your characters in slot order, or everyone in the party as the game
        names them from the caster's own client (player, party1..party4: the same people seen from any client)."""
        states = self.source.states()
        if group == "team":
            live = [st.name for _, st in sorted(states.items()) if st is not None and not st.stale and st.name]
            if live:
                return list(dict.fromkeys(live))
            names = getattr(self.source, "character_names", None)
            return sorted(names()) if names else []
        st = states.get(slot)
        if st is None:
            return []
        return ["player"] + [f"party{i + 1}" for i, there in enumerate(st.party_exists) if there]

    def _dispatch(self, rt: RuleRuntime, ev: ChatEvent, values: dict, now: float, whole_group: bool = False) -> None:
        """Fire the rule for one request, or queue it if the rule is busy."""
        safe = safe_values(values)
        target = safe["who"].lower()
        steps, why = substitute(rt.steps, values), f"{ev.channel} from {ev.sender}: {ev.text!r}"
        if used := _args_used(rt.steps):
            why += " -> " + ", ".join(f"{{{k}}}={safe[k]!r}" for k in used)
            if ev.truncated:
                why += " (the line was cut off: only its start arrived)"
        busy = ("still running" if rt.running_step is not None else
                "on cooldown" if rt.cooling(now, target) > 0 else
                "others are queued first" if rt.pending else None)
        if busy:
            self._queue(rt, _Request(ev.sender, steps, why, now, target), busy, whole_group)
            return
        if rt.rule.when is not None:
            ok, _, matched = self._check(rt, self.source.states())
            if not ok:
                return
            steps = substitute(steps, matched) if matched else steps
        self._fire(rt, now, steps, why, ev.sender, target)

    def chat_values(self, trig: ChatTrigger, ev: ChatEvent, listener: str = "",
                    base: dict[str, str] | None = None) -> dict[str, str]:
        """The placeholders a chat line fills in: {sender} {message} {channel} {listener} {arg} {arg2} {rest}
        {who}."""
        values = dict(base) if base else {"sender": ev.sender, "message": ev.text, "channel": ev.channel,
                                          "listener": listener}
        args = chat_args(ev.text, matched_word(trig, ev.text) if trig.contains else None)
        names = getattr(self.source, "character_names", None)
        aliases = getattr(self.source, "who_aliases", None)
        values.update(args, who=resolve_who(args["rest"], ev.sender, set(names()) if names else set(),
                                            aliases() if aliases else {}))
        return values

    # ------------------------------------------------------------------ chat request queue
    def queue_limit(self, rt: RuleRuntime) -> int:
        return self.options.chat_queue if rt.rule.queue is None else rt.rule.queue

    def _queue(self, rt: RuleRuntime, req: _Request, busy: str, whole_group: bool = False) -> None:
        name, who = rt.rule.name, req.sender.lower()
        # one place per person; for a rule that acts on {who} ("paladin buff gnope"), per person *and* {who},
        # so one sender can line up buffs for several people
        by_who = "who" in _args_used(rt.steps)

        def same(target: str) -> bool:
            return not by_who or target == req.target

        if who == (rt.current_sender or "").lower() and rt.running_step is not None and same(rt.current_target) \
                or any(q.sender.lower() == who and same(q.target) for q in rt.pending):
            what = f"{req.sender}'s request for {req.target}" if by_who else req.sender
            self._event(f"rule {name!r}: {what} is already being served or queued, skipped")
            return
        if busy == "on cooldown" and (rt.rule.cooldown_per == "target" or by_who):
            if req.target == rt.current_target and who == (rt.last_sender or "").lower():
                self._event(f"rule {name!r}: on cooldown for {req.target}, chat from {req.sender} skipped "
                            "(just served)")
                return
        elif busy == "on cooldown" and who == (rt.last_sender or "").lower():
            self._event(f"rule {name!r}: on cooldown, chat from {req.sender} skipped (they were just served)")
            return
        limit = self.queue_limit(rt)
        if limit == 0 and not whole_group:
            self._event(f"rule {name!r}: {busy}, chat from {req.sender} skipped (queueing is off)")
            return
        if len(rt.pending) >= limit and not whole_group:  # "buff team" always fits in full
            self._event(f"rule {name!r}: queue full ({limit}), chat from {req.sender} skipped")
            return
        rt.pending.append(req)
        what = f"{req.sender} for {req.target}" if by_who else req.sender
        self._event(f"rule {name!r}: {busy}, {what} queued (#{len(rt.pending)})")

    def _next_request(self, rt: RuleRuntime, now: float, states: dict[int, ClientState | None]) -> None:
        """Serve the next queued chat request once the rule is free and off cooldown (cooldown_per target: the
        first one whose target is off cooldown)."""
        per_target = rt.rule.cooldown_per == "target"
        if not per_target and rt.cooling(now) > 0:
            return
        ttl = self.options.chat_queue_ttl
        while rt.pending:
            req = next((q for q in rt.pending if not per_target or now - q.queued_at > ttl
                        or rt.cooling(now, q.target) <= 0), None)
            if req is None:
                return
            rt.pending.remove(req)
            if now - req.queued_at > ttl:
                self._event(f"rule {rt.rule.name!r}: {req.sender}'s request expired after "
                            f"{fmt_left(self.options.chat_queue_ttl)} in the queue")
                continue
            steps = req.steps
            if rt.rule.when is not None:
                ok, _, matched = self._check(rt, states)
                if not ok:
                    self._event(f"rule {rt.rule.name!r}: {req.sender}'s request dropped, the condition is false now")
                    continue
                steps = substitute(steps, matched) if matched else steps
            self._fire(rt, now, steps, f"queued {fmt_left(now - req.queued_at)}, {req.why}", req.sender, req.target)
            return

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
    def _fire(self, rt: RuleRuntime, now: float, steps: list[Step] | None = None, why: str = "",
              sender: str | None = None, target: str = "") -> None:
        rt.last_fired = now
        rt.current_target = target
        if rt.rule.cooldown_per == "target":
            rt.target_fired = {t: at for t, at in rt.target_fired.items() if now - at < rt.rule.cooldown}
            rt.target_fired[target] = now
        rt.fire_count += 1
        rt.run_steps = steps
        rt.current_sender = sender
        rt.last_sender = sender
        self._event(f"rule {rt.rule.name!r} fired{f' ({why})' if why else ''}: {describe(steps or rt.steps)}",
                    sorted(self._send_slots(rt)))
        rt.running_step = 0
        rt.resume_at = now
        self._advance(rt, now)

    def _advance(self, rt: RuleRuntime, now: float) -> None:
        self._ctx = rt.rule.name
        steps = rt.run_steps if rt.run_steps is not None else rt.steps
        while rt.running_step is not None and now >= rt.resume_at:
            if rt.running_step >= len(steps):
                rt.running_step = None
                rt.run_steps = None
                rt.current_sender = None
                self._release(rt)
                return
            step = steps[rt.running_step]
            to = step.to if isinstance(step, (TypeStep, SendStep)) else None
            if to is not None:
                slots = self.source.slots_for(step.to)
                if self.options.skip_dead:
                    alive = [s for s in slots if not self._dead(s)]
                    if not alive and slots:
                        rt.running_step += 1
                        self._event(f"  skipped {describe([step])}: {self._names(slots)} dead", slots)
                        continue
                    if len(alive) < len(slots):
                        to = alive  # leave the dead out
                        slots = alive
                waited = rt.running_step == 0 and rt.last_fired is not None and rt.resume_at > rt.last_fired
                before = {s: self._slot_last.get(s) for s in slots}
                later = self._turn(rt, slots, now)
                if later > now:
                    rt.resume_at = later  # another rule's cast or global cooldown on that character first
                    return
                if waited and rt.rule.when is not None and rt.rule.chat is None and not self._still_holds(rt):
                    # it waited its turn: only go if the condition still holds. A DoT queued behind nine others
                    # must not be cast once the mob is dead (the game then picks the nearest mob for it, and the
                    # pet runs after that one)
                    for s, last in before.items():  # it sent nothing: the next rule needn't wait for it
                        if last is None:
                            self._slot_last.pop(s, None)
                        else:
                            self._slot_last[s] = last
                    rt.running_step = rt.run_steps = rt.current_sender = None
                    self._release(rt)
                    self._event(f"  dropped {describe(steps)}: condition no longer true", slots)
                    return
                if waited:  # the cooldown runs from when it actually goes out, not from when it joined the line:
                    # a DoT that waited 7 s behind others is otherwise off its 6 s cooldown the moment it's cast,
                    # and fires again before the DoT lands
                    rt.last_fired = now
                    if rt.rule.cooldown_per == "target":
                        rt.target_fired[rt.current_target] = now
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
                    self._event(f"  [dry run] would type {step.text!r} -> {to}", slots)
                else:
                    sent = self.sender.type_text(step.text, to)
                    self._event(f"  typed {step.text!r} -> slots {sent}", sent)
            elif isinstance(step, SendStep):
                if self.dry_run:
                    self._event(f"  [dry run] would send {step.chord} -> {to}", slots)
                else:
                    sent = self.sender.send_chord(step.chord, to)
                    self._event(f"  sent {step.chord} -> slots {sent}", sent)

    def _turn(self, rt: RuleRuntime, slots: list[int], now: float) -> float:
        """When this rule's next step may go to `slots`. Rules take turns on a character:
        - a rule already waiting for it goes first, unless this one has a higher priority;
        - rule_gap after another rule's last step there (the global cooldown);
        - and not while it's casting or channeling something this rule didn't start (up to cast_wait_max).
        When it may go now, records it as the last step there and returns `now`.
        A `gcd: false` rule (/assist, /petattack...) skips all of that and isn't recorded: the game has no global
        cooldown for it, so it neither waits for spells nor makes the next spell wait."""
        o = self.options
        me, prio = rt.rule.name, rt.rule.priority
        if not rt.rule.gcd or (o.rule_gap <= 0 and not o.wait_for_casts):
            return now
        later, why = now, ""
        waiting = {r.rule.name for r in self.rules if r.wait_reason}  # a claim only counts while its rule waits
        ahead = [c for s in slots if (c := self._claims.get(s)) and c[0] != me and c[1] >= prio and c[0] in waiting]
        # this rule's own last step doesn't hold up the rest of its sequence, but does hold up a new firing of it
        mine_too = rt.running_step == 0
        others = [last for s in slots if (last := self._slot_last.get(s)) and (mine_too or last[1] != me)]
        latest = max(others, default=None)  # (time, rule) of another rule's last step on these characters
        casting = [s for s in slots if self._casting(s) and (mine_too or self._slot_last.get(s, (0.0, ""))[1] != me)]
        if ahead:
            later, why = now + CLAIM_RECHECK, f"{ahead[0][0]!r} goes first"
        elif o.rule_gap > 0 and latest and latest[0] + o.rule_gap > now:
            later, why = latest[0] + o.rule_gap, f"global cooldown after {latest[1]!r}"
        elif o.wait_for_casts and casting:
            if rt.cast_wait_since is None:
                rt.cast_wait_since = now
            if now - rt.cast_wait_since < o.cast_wait_max:  # after that, go anyway (a stuck flag)
                later, why = now + CLAIM_RECHECK, f"{self._names(casting)} casting"
        if later > now:
            for s in slots:
                c = self._claims.get(s)
                if c is None or (c[0] != me and prio > c[1]):
                    self._claims[s] = (me, prio)  # first in line (or ahead by priority)
            rt.wait_reason = why
            return later
        rt.wait_reason, rt.cast_wait_since = "", None
        for s in slots:
            self._slot_last[s] = (now, me)
            if self._claims.get(s, ("",))[0] == me:
                del self._claims[s]
        return now

    def _release(self, rt: RuleRuntime) -> None:
        """A rule stopped: let go of characters it was waiting for."""
        rt.wait_reason, rt.cast_wait_since = "", None
        for s in [s for s, c in self._claims.items() if c[0] == rt.rule.name]:
            del self._claims[s]

    def _state(self, slot: int) -> ClientState | None:
        st = self.source.states().get(slot)
        return None if st is None or st.stale else st

    def _dead(self, slot: int) -> bool:
        st = self._state(slot)
        return bool(st and (st.dead or st.ghost))

    def _casting(self, slot: int) -> bool:
        st = self._state(slot)
        return bool(st and (st.casting or st.channeling))

    def _names(self, slots: list[int]) -> str:
        names = [st.name if (st := self._state(s)) and st.name else f"slot {s}" for s in slots]
        return ", ".join(names)

    def _event(self, msg: str, slots: list[int] | None = None) -> None:
        log.info(msg)
        self.on_event(msg)
        m = RULE_NAME.search(msg)
        name = m.group(2) if m else self._ctx if msg.startswith("  ") else "(automation)"  # step lines are indented
        text = RULE_NAME.sub("", msg, count=1).strip(" :") if m else msg
        self.on_activity(name, text.strip(), list(slots or []))
