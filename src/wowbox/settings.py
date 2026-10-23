"""The settings shown in the Settings tab: which ones, in what groups, with what explanation.

Values live in the config models (AppConfig sections). The running parts read those same objects, so a value
changed here applies at once; `Controller.save_settings` writes the changed ones into team.yaml in place.
"""
from __future__ import annotations

import typing
from dataclasses import dataclass

from pydantic import BaseModel
from pydantic.functional_validators import BeforeValidator

from .config import parse_duration
from .macro_store import fmt_duration


@dataclass(frozen=True)
class Setting:
    section: str  # AppConfig attribute, e.g. "automation_options"
    key: str
    label: str
    help: str


GROUPS: list[tuple[str, list[Setting]]] = [
    ("Layout", [
        Setting("layout", "thumbs_follow_wow", "Thumbnails come up with WoW",
                "Clicking into WoW brings the thumbnail strip (or tab bar) back above other programs. Off: it stays "
                "where it is, so a program you put over it stays on top; Arrange brings it up."),
    ]),
    ("Automation: queue and turn-taking", [
        Setting("automation_options", "chat_queue", "Queue per chat rule",
                "Requests that come in while a chat rule is busy wait their turn, one per person, up to this many. "
                "0 = skip them. A rule's own Queue setting overrides this."),
        Setting("automation_options", "chat_queue_ttl", "Queued request expires after",
                "A request still waiting after this long is dropped (e.g. 60s)."),
        Setting("automation_options", "rule_gap", "Gap between rules on one character",
                "Steps from different rules on the same character stay at least this far apart: the global "
                "cooldown (1.5s). 0 = off."),
        Setting("automation_options", "wait_for_casts", "Wait for a cast in progress",
                "Another rule's step also waits until the character's current cast or channel has finished."),
        Setting("automation_options", "cast_wait_max", "...but at most",
                "A stuck casting flag holds a step for no longer than this."),
    ]),
    ("Automation: who counts", [
        Setting("automation_options", "skip_dead", "Ignore dead characters",
                "Dead characters and ghosts don't match conditions (like health < 70) and get no steps."),
        Setting("automation_options", "skip_far", "Ignore characters too far away",
                "A character in another zone, or farther than Follow > Max distance from every character a rule "
                "acts on, doesn't count for that rule's condition."),
        Setting("automation_options", "pause_on_stale_bridge", "Pause rules on an unreadable client",
                "A rule waits while a client it checks or sends to can't be read (minimized, covered, addon off)."),
    ]),
    ("Automation: start-up and speed", [
        Setting("automation_options", "enabled_on_start", "Automation ON at start",
                "Off: automation always starts OFF and you turn it on in the toolbar."),
        Setting("automation_options", "dry_run", "Dry run at start",
                "Start in dry run (rules log what they'd do and send nothing). The toolbar box switches it now."),
        Setting("automation_options", "tick_hz", "Checks per second",
                "How often rules are checked (10 is plenty)."),
    ]),
    ("Chat filter (BoxBridge)", [
        Setting("bridge", "chat_filter", "Pass on only lines a chat rule could use",
                "WoWBox writes your chat rules' words into BoxBridge's Words.lua; the addon then drops every other "
                "line, so in a busy raid the ones you need aren't pushed out. Clients load it on /reload."),
        Setting("bridge", "chat_filter_reload", "/reload clients when the word list changes",
                "Out of combat only. Off: the banner tells you to /reload (or type /bb all reload)."),
    ]),
    ("Typing guard", [
        Setting("broadcast", "chatguard", "Pause broadcasting while you type",
                "Keys aren't copied to other clients while your chat box is open."),
        Setting("broadcast", "type_wait_max", "Typed lines wait for you up to",
                "A rule's or macro's typed line waits while you're typing or pressing keys in that client. After "
                "this long: skipped if the chat box is still open, sent anyway if you're only holding keys."),
        Setting("broadcast", "type_quiet", "Quiet time after your last key",
                "The window you're in counts as free this long after your last key press."),
        Setting("broadcast", "only_when_wow_focused", "Broadcast only from WoW windows",
                "Keys pressed in other programs are never broadcast."),
        Setting("broadcast", "modifier_mode", "How Shift/Ctrl/Alt chords reach other clients",
                "post: send the modifier with the key (default). focus_swap: briefly focus each client. "
                "strip: send the bare key."),
    ]),
    ("Follow and mirroring", [
        Setting("follow", "mirror_guard", "Only mirror to followers who are with you",
                "Followers in another zone or farther than Max distance don't get mirrored movement."),
        Setting("follow", "mirror_max_distance_yards", "Max distance (yards)",
                "Used by mirroring and by Ignore characters too far away."),
        Setting("follow", "refollow", "Put followers back on follow",
                "When a follower's /follow breaks, WoWBox follows again (needs BoxBridge)."),
        Setting("follow", "refollow_delay", "Re-follow after",
                "How long a follower must be off follow (and you not moving) first."),
        Setting("follow", "refollow_in_combat", "Re-follow in combat",
                "Off: casters stay put in a fight and follow again after it."),
        Setting("follow", "retry", "Retry every", "Wait between attempts on one follower; doubles while it fails."),
        Setting("follow", "retry_max", "...at most every", "The longest wait between attempts."),
    ]),
    ("Stacking", [
        Setting("stack", "tolerance_yards", "Close enough (yards)",
                "How close to the spot counts as stacked (0.1). Can't beat one frame of movement."),
        Setting("stack", "facing_tolerance_deg", "Facing close enough (degrees)",
                "Can't beat half a frame of turning: 1.5 at 60 fps, 0.6 at 144 fps."),
        Setting("stack", "max_distance_yards", "Max distance (yards)",
                "Followers farther than this (or in another zone) are skipped: use follow."),
        Setting("stack", "timeout", "Give up on a follower after", "e.g. 10s"),
        Setting("stack", "on_stop", "Re-stack when you stop moving",
                "While mirroring movement: stack every time you stop."),
        Setting("stack", "on_stop_delay", "...after you've stopped for", "e.g. 400ms"),
    ]),
]

ALL = [s for _, group in GROUPS for s in group]


def field_kind(model: type[BaseModel], key: str) -> tuple[str, list[str]]:
    """'bool', 'int', 'float', 'duration' or 'choice' (with its choices)."""
    info = model.model_fields[key]
    if any(isinstance(m, BeforeValidator) and m.func is parse_duration for m in info.metadata):
        return "duration", []
    ann = info.annotation
    if typing.get_origin(ann) is typing.Literal:
        return "choice", [str(a) for a in typing.get_args(ann)]
    if ann is bool:
        return "bool", []
    if ann is int:
        return "int", []
    if ann is float:
        return "float", []
    return "text", []


def show(model: BaseModel, key: str) -> str:
    """A value as the Settings tab and team.yaml show it: 1.5s, 40, true."""
    kind, _ = field_kind(type(model), key)
    v = getattr(model, key)
    if kind == "duration":
        return fmt_duration(v)
    if kind == "bool":
        return "true" if v else "false"
    if kind == "float":
        return f"{v:g}"
    return str(v)
