"""How the Automation tab groups and finds rules: by who carries them out, by trigger, or on/off.

Display only: nothing here changes how a rule fires.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import AutomationRule
from .actions import MacroStep, SendStep, Step, TypeStep

GROUP_BY = ("Who acts", "Trigger", "On/off", "None")


@dataclass(frozen=True)
class Member:
    """A slot as far as grouping cares: who's in it (live from the bridge, else from team.yaml)."""
    slot: int
    character: str = ""
    label: str = ""
    role: str = ""
    class_token: str = ""

    @property
    def name(self) -> str:
        return self.character or self.label or f"slot {self.slot}"

    def matches(self, target: str) -> bool:
        """class:WARLOCK / role:healer / char:Name / Name (case-insensitive), as Team does."""
        kind, sep, arg = target.partition(":")
        kind, arg = kind.strip().lower(), arg.strip().lower()
        if sep and kind == "class":
            return bool(self.class_token) and self.class_token.lower() == arg.replace(" ", "")
        if sep and kind == "role":
            return self.role.lower() == arg
        name = arg if sep and kind == "char" else target.strip().lower()
        return bool(name) and (self.character.lower() == name or (not self.character and self.label.lower() == name))


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    order: tuple


SEVERAL = Group("several", "Several characters / everyone", (3,))
ACTIVE = Group("active", "The window you're in", (2,))
MACROS = Group("macros", "Macros", (4,))
NOTHING = Group("nothing", "Sends nothing", (5,))


def who_acts(steps: list[Step], members: list[Member], leader: int) -> Group:
    """The character a rule's steps go to. class:SHAMAN, Kunth, role:healer and [3] all land on the same one
    when they mean the same slot; steps for more than one character, or for all/followers, are "several"."""
    slots: set[int] = set()
    others: set[str] = set()
    everyone = active = macro = False
    for s in steps:
        if isinstance(s, MacroStep):
            macro = True
        if not isinstance(s, (SendStep, TypeStep)):
            continue
        t = s.to
        if isinstance(t, list):
            slots.update(t)
            continue
        low = t.strip().lower()
        if low in ("all", "followers") or low.startswith("not:"):
            everyone = True
        elif low == "active":
            active = True
        elif low == "leader":
            slots.add(leader)
        elif low != "none":
            hit = [m.slot for m in members if m.matches(t)]
            if hit:
                slots.update(hit)
            else:
                others.add(t.strip())
    if everyone or len(slots) + len(others) + active > 1:
        return SEVERAL
    if slots:
        slot = next(iter(slots))
        m = next((m for m in members if m.slot == slot), Member(slot))
        title = f"{slot} {m.name}" + (f" · {m.class_token.title()}" if m.class_token else "")
        return Group(f"slot:{slot}", title, (0, slot))
    if others:
        o = next(iter(others))
        kind, sep, arg = o.partition(":")
        title = f"{arg.strip().title()} (no {kind.strip()} seen yet)" if sep else o
        return Group("to:" + o.lower(), title, (1, o.lower()))
    if active:
        return ACTIVE
    return MACROS if macro else NOTHING


def by_trigger(rule: AutomationRule) -> Group:
    if rule.chat is not None:
        return Group("chat", "Chat keywords", (0,))
    if rule.every is not None:
        return Group("timer", "Timers", (1,))
    return Group("state", "State (health, combat, …)", (2,))


def by_enabled(rule: AutomationRule) -> Group:
    return Group("on", "On", (0,)) if rule.enabled else Group("off", "Off", (1,))


def group_for(by: str, rule: AutomationRule, steps: list[Step], members: list[Member], leader: int) -> Group | None:
    """The group a rule goes in for the "Group by" choice; None for "None" (a flat list)."""
    if by == "Who acts":
        return who_acts(steps, members, leader)
    if by == "Trigger":
        return by_trigger(rule)
    if by == "On/off":
        return by_enabled(rule)
    return None


def matches(query: str, *texts: str) -> bool:
    """Search: every word of the query appears somewhere in the texts (any case)."""
    hay = " ".join(texts).lower()
    return all(w in hay for w in query.lower().split())
