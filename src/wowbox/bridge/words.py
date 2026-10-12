"""The chat filter for BoxBridge: which channels and words WoWBox's chat rules listen for.

BoxBridge can pass only about two chat lines a second to WoWBox, so in a busy raid most lines must never be
queued. WoWBox writes `Words.lua` into the addon's folder in the game; the addon reads it at login or /reload
and passes on only lines that some chat rule could match. The addon shows the `version` it loaded in its state
frames (6 bits), so WoWBox can tell which clients still have an old list.

Matching mirrors the engine: a line passes when its channel is listed and its text contains one of that
channel's words (any case). A channel listed as "all" passes every line (a rule without words).
"""
from __future__ import annotations

import logging
import re
import typing
from dataclasses import dataclass
from pathlib import Path

from ..config import AutomationRule, ChatChannel

log = logging.getLogger(__name__)

CHANNELS: tuple[str, ...] = typing.get_args(ChatChannel)  # whisper, party, raid, guild, officer, say, yell
MAX_VERSION = 63  # the addon reports it in 6 bits; 0 = no list (it passes everything)
FILE_NAME = "Words.lua"
ALL = "all"
_VERSION_RE = re.compile(r"^\s*version\s*=\s*(\d+)", re.M)


@dataclass(frozen=True)
class ChatFilter:
    channels: dict[str, tuple[str, ...] | str]  # channel -> words (lower case), or "all"

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


def build(rules: list[AutomationRule]) -> ChatFilter:
    """The channels and words of every enabled chat rule."""
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
    return ChatFilter({ch: v if v == ALL else tuple(sorted(v)) for ch, v in sorted(found.items())})


def _lua_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


def body(f: ChatFilter) -> str:
    """The channels part of Words.lua (what decides whether the version must change)."""
    lines = []
    for ch, words in f.channels.items():
        value = _lua_string(ALL) if words == ALL else "{ " + ", ".join(_lua_string(w) for w in words) + " }"
        lines.append(f"    {ch} = {value},")
    return "  channels = {\n" + "\n".join(lines) + ("\n" if lines else "") + "  },\n"


def lua(f: ChatFilter, version: int) -> str:
    return ("-- Written by WoWBox from your chat rules (automation). Don't edit: it's rewritten when they change.\n"
            "-- BoxBridge reads it at login or /reload and passes WoWBox only chat lines these words could match.\n"
            "BoxBridgeWords = {\n"
            f"  version = {version},\n"
            + body(f) + "}\n")


def file_version(text: str) -> int:
    m = _VERSION_RE.search(text)
    return int(m.group(1)) if m else 0


def addon_dir(exe: str) -> Path:
    """Where BoxBridge is installed for the game at `exe` (client.exe in team.yaml)."""
    return Path(exe).parent / "Interface" / "AddOns" / "BoxBridge"


def sync(folder: Path, f: ChatFilter) -> tuple[int, bool]:
    """Make folder/Words.lua describe `f`. Returns (its version, whether it was rewritten).
    The version only changes when the channels or words do. Raises OSError if it can't be written."""
    path = folder / FILE_NAME
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    version = file_version(old)
    if version and old == lua(f, version):
        return version, False
    version = version % MAX_VERSION + 1
    path.write_text(lua(f, version), encoding="utf-8", newline="\n")
    return version, True
