"""The chat filter for BoxBridge: which channels and words WoWBox's chat rules listen for.

BoxBridge can pass only about two chat lines a second to WoWBox, so in a busy raid most lines must never be
queued. WoWBox writes `Words.lua` into the addon's folder in the game; the addon reads it at login or /reload
and passes on only lines that some chat rule could match. The addon shows the `version` it loaded in its state
frames (6 bits), so WoWBox can tell which clients still have an old list.

Matching mirrors the engine: a line passes when its channel is listed and its text contains one of that
channel's words (any case). A channel listed as "all" passes every line (a rule without words).

It also carries lists for the addon: `spells`, the spells `ready:` conditions ask about (the addon reports which
of them could be cast now), `dots`, the DoTs `dot:` conditions ask about (which should go on the target now), and
`panel`, the rules shown on the in-game panel (`panel: true`).
"""
from __future__ import annotations

import logging
import re
import typing
from dataclasses import dataclass, replace
from pathlib import Path

from ..config import AutomationRule, ChatChannel

log = logging.getLogger(__name__)

CHANNELS: tuple[str, ...] = typing.get_args(ChatChannel)  # whisper, party, raid, guild, officer, say, yell
MAX_SPELLS = 7  # ready bits in the state frame (decoder.MAX_WATCHED_SPELLS)
MAX_DOTS = 14  # DoT bits in the state frame (decoder.MAX_DOTS)
MAX_PANEL_RULES = 8  # bits after WoWBox's flags in the panel keys (panel.py)
MAX_VERSION = 63  # the addon reports it in 6 bits; 0 = no list (it passes everything)
FILE_NAME = "Words.lua"
ALL = "all"
_VERSION_RE = re.compile(r"^\s*version\s*=\s*(\d+)", re.M)


@dataclass(frozen=True)
class ChatFilter:
    channels: dict[str, tuple[str, ...] | str]  # channel -> words (lower case), or "all"
    spells: tuple[str, ...] = ()  # watched spells, as written in the rules
    dots: tuple[str, ...] = ()  # watched DoTs
    panel: tuple[str, ...] = ()  # names of the rules on the in-game panel

    def passes(self, channel: str, text: str) -> bool:
        """What the addon does with a line (for tests and the rule preview)."""
        words = self.channels.get(channel)
        if words is None:
            return False
        if words == ALL:
            return True
        t = ascii_lower(text)
        return any(w in t for w in words)


def ascii_lower(s: str) -> str:
    """Lower case the way Lua's string.lower does in WoW (A-Z only), so both sides agree."""
    return s.translate(_LOWER)


_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


def spells_asked(rules: list[AutomationRule], keys: tuple[str, ...] = ("ready", "ready_ne")) -> list[str]:
    """The spells rules' `ready:` conditions (or other `keys`) ask about, in order (each once, any case). Disabled
    rules count too: turning a rule on in a fight (/bb rule) mustn't change the list the clients loaded."""
    out: list[str] = []
    for r in rules:
        if r.when is None:
            continue
        for key, value in (r.when.model_extra or {}).items():
            if key in keys:
                for name in ([value] if isinstance(value, str) else value or []):
                    name = str(name).strip()
                    if name and ascii_lower(name) not in [ascii_lower(n) for n in out]:
                        out.append(name)
    return out


def build(rules: list[AutomationRule], chat: bool = True) -> ChatFilter:
    """The channels and words of every enabled chat rule, plus the watched spells and the panel's rules.
    chat=False (the chat filter is off): every channel passes every line."""
    spells = spells_asked(rules)
    if len(spells) > MAX_SPELLS:
        log.warning("only %d spells can be watched; ignored: %s", MAX_SPELLS, ", ".join(spells[MAX_SPELLS:]))
    dots = spells_asked(rules, ("dot", "dot_ne"))
    if len(dots) > MAX_DOTS:
        log.warning("only %d DoTs can be watched; ignored: %s", MAX_DOTS, ", ".join(dots[MAX_DOTS:]))
    panel = [r.name for r in rules if r.panel]
    if len(panel) > MAX_PANEL_RULES:
        log.warning("only %d rules fit on the in-game panel; left off: %s", MAX_PANEL_RULES,
                    ", ".join(panel[MAX_PANEL_RULES:]))
    extra = {"spells": tuple(spells[:MAX_SPELLS]), "dots": tuple(dots[:MAX_DOTS]),
             "panel": tuple(panel[:MAX_PANEL_RULES])}
    if not chat:
        return ChatFilter({ch: ALL for ch in CHANNELS}, **extra)
    found: dict[str, set[str] | str] = {}
    for r in rules:
        if not r.enabled or r.chat is None:
            continue
        chans = CHANNELS if "any" in r.chat.channel else [c for c in r.chat.channel if c in CHANNELS]
        words = {ascii_lower(w.strip()) for w in r.chat.contains or [] if w.strip()}
        for ch in chans:
            if not words or found.get(ch) == ALL:
                found[ch] = ALL
            else:
                found.setdefault(ch, set()).update(words)  # type: ignore[union-attr]
    return ChatFilter({ch: v if v == ALL else tuple(sorted(v)) for ch, v in sorted(found.items())}, **extra)


def _lua_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


def body(f: ChatFilter) -> str:
    """The channels part of Words.lua (what decides whether the version must change)."""
    lines = []
    for ch, words in f.channels.items():
        value = _lua_string(ALL) if words == ALL else "{ " + ", ".join(_lua_string(w) for w in words) + " }"
        lines.append(f"    {ch} = {value},")
    out = "  channels = {\n" + "\n".join(lines) + ("\n" if lines else "") + "  },\n"
    for key in ("spells", "dots", "panel"):  # only when used: an unchanged file keeps its version
        if names := getattr(f, key):
            out += f"  {key} = {{ " + ", ".join(_lua_string(n) for n in names) + " },\n"
    return out


def lua(f: ChatFilter, version: int) -> str:
    return ("-- Written by WoWBox from your chat rules (automation). Don't edit: it's rewritten when they change.\n"
            "-- BoxBridge reads it at login or /reload and passes WoWBox only chat lines these words could match.\n"
            "BoxBridgeWords = {\n"
            f"  version = {version},\n"
            + body(f) + "}\n")


_CHANNEL_RE = re.compile(r"^\s*(\w+)\s*=\s*(\{.*?\}|\"(?:\\.|[^\"\\])*\")\s*,\s*$", re.M)
_STRING_RE = re.compile(r"\"((?:\\.|[^\"\\])*)\"")
_UNESCAPE = {"n": "\n", "r": "\r"}


def parse(text: str) -> ChatFilter | None:
    """The filter a Words.lua describes (what a client that loaded it has), or None if it isn't one."""
    if "BoxBridgeWords" not in text or "channels" not in text:
        return None
    channels: dict[str, tuple[str, ...] | str] = {}
    block = text.split("channels", 1)[1].split("\n  },", 1)[0]
    for ch, value in _CHANNEL_RE.findall(block):
        found = _strings(value)
        channels[ch] = ALL if not value.startswith("{") and found == (ALL,) else found
    extra = {key: _strings(m.group(1)) for key in ("spells", "dots", "panel")
             if (m := re.search(rf"^\s*{key}\s*=\s*(\{{.*?\}})\s*,\s*$", text, re.M))}
    return ChatFilter(channels, **extra)


def _strings(value: str) -> tuple[str, ...]:
    return tuple(re.sub(r"\\(.)", lambda m: _UNESCAPE.get(m.group(1), m.group(1)), s)
                 for s in _STRING_RE.findall(value))


def with_keyword(f: ChatFilter | None, ev):
    """A chat line whose matched word the addon sent as its number (ev.keyword), with the word put back: the
    text then starts with it. None if `f` (the filter the client loaded) doesn't have that word."""
    found = f.channels.get(ev.channel) if f else None
    if not ev.keyword:
        return ev
    if not isinstance(found, tuple) or not 1 <= ev.keyword <= len(found):
        return None
    word = found[ev.keyword - 1]
    return replace(ev, text=word + ev.text, length=len(word.encode("utf-8")) + ev.length, keyword=0)


def file_version(text: str) -> int:
    m = _VERSION_RE.search(text)
    return int(m.group(1)) if m else 0


def addon_dir(exe: str) -> Path:
    """Where BoxBridge is installed for the game at `exe` (client.exe in team.yaml)."""
    return Path(exe).parent / "Interface" / "AddOns" / "BoxBridge"


def sync(folder: Path, f: ChatFilter, seen: dict[int, ChatFilter] | None = None) -> tuple[int, bool]:
    """Make folder/Words.lua describe `f`. Returns (its version, whether it was rewritten).
    The version only changes when the channels or words do. Raises OSError if it can't be written.
    seen: version -> filter, for every version the file has had (clients may still have an older one loaded)."""
    path = folder / FILE_NAME
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    version = file_version(old)
    seen = {} if seen is None else seen
    if version and (was := parse(old)) is not None:
        seen[version] = was  # what a client that loaded it before has
    if version and old == lua(f, version):
        return version, False
    version = version % MAX_VERSION + 1
    path.write_text(lua(f, version), encoding="utf-8", newline="\n")
    seen[version] = f
    return version, True
