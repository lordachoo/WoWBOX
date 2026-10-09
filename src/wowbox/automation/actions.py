"""Automation actions flattened into steps: send a chord, type a chat line, wait, or start a macro."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..config import ActionConfig, Target
from ..keys import Chord, expand_key_spec, parse_chord


@dataclass(frozen=True)
class SendStep:
    chord: Chord
    to: Target


@dataclass(frozen=True)
class TypeStep:
    text: str
    to: Target


@dataclass(frozen=True)
class WaitStep:
    seconds: float


@dataclass(frozen=True)
class MacroStep:
    name: str


Step = SendStep | TypeStep | WaitStep | MacroStep

PLACEHOLDER = re.compile(r"\{(\w+)\}")
_NAME_UNSAFE = re.compile(r"[^\w\-]", re.UNICODE)
_TEXT_UNSAFE = re.compile(r"[\x00-\x1f\x7f|]")


def safe_values(values: dict[str, str]) -> dict[str, str]:
    """Make chat-supplied values safe to type: a name keeps only letters, digits and '-'; any other text
    loses control characters and WoW escape codes and can't start a slash command."""
    out = {}
    for k, v in values.items():
        if k in ("sender", "listener"):
            out[k] = _NAME_UNSAFE.sub("", v)
        else:
            out[k] = _TEXT_UNSAFE.sub("", v).lstrip(" /")
    return out


def fill(text: str, values: dict[str, str]) -> str:
    """Replace {sender}, {message}, ... ; unknown placeholders are left as they are."""
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def substitute(steps: list[Step], values: dict[str, str]) -> list[Step]:
    values = safe_values(values)
    out: list[Step] = []
    for s in steps:
        if isinstance(s, TypeStep):
            s = replace(s, text=fill(s.text, values), to=fill(s.to, values) if isinstance(s.to, str) else s.to)
        elif isinstance(s, SendStep) and isinstance(s.to, str):
            s = replace(s, to=fill(s.to, values))
        out.append(s)
    return out


def flatten(action: ActionConfig) -> list[Step]:
    if action.send is not None:
        return [SendStep(parse_chord(action.send), action.to)]
    if action.type is not None:
        return [TypeStep(action.type, action.to)]
    if action.keys is not None:
        steps: list[Step] = []
        for spec in action.keys:
            for chord in expand_key_spec(spec):
                if steps and action.delay > 0:
                    steps.append(WaitStep(action.delay))
                steps.append(SendStep(chord, action.to))
        return steps
    if action.wait is not None:
        return [WaitStep(action.wait)]
    if action.macro is not None:
        return [MacroStep(action.macro)]
    steps: list[Step] = []
    for sub in action.sequence or []:
        steps.extend(flatten(sub))
    return steps


def describe(steps: list[Step]) -> str:
    parts = []
    for s in steps:
        if isinstance(s, SendStep):
            parts.append(f"send {s.chord} -> {s.to}")
        elif isinstance(s, TypeStep):
            parts.append(f"type {s.text!r} -> {s.to}")
        elif isinstance(s, MacroStep):
            parts.append(f"macro {s.name}")
        else:
            parts.append(f"wait {s.seconds:g}s")
    return "; ".join(parts)
