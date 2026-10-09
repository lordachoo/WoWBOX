"""Key names, virtual-key codes and chords ("Ctrl+1"). Pure Python, no Win32."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntFlag


class Mod(IntFlag):
    NONE = 0
    SHIFT = 1
    CTRL = 2
    ALT = 4


VK: dict[str, int] = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D, "SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12,
    "PAUSE": 0x13, "CAPSLOCK": 0x14, "ESCAPE": 0x1B, "ESC": 0x1B, "SPACE": 0x20, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "END": 0x23, "HOME": 0x24, "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "INSERT": 0x2D, "DELETE": 0x2E, "NUMLOCK": 0x90, "SCROLLLOCK": 0x91,
    "LSHIFT": 0xA0, "RSHIFT": 0xA1, "LCTRL": 0xA2, "RCTRL": 0xA3, "LALT": 0xA4, "RALT": 0xA5,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
    "NUMPADMULTIPLY": 0x6A, "NUMPADPLUS": 0x6B, "NUMPADMINUS": 0x6D, "NUMPADDECIMAL": 0x6E, "NUMPADDIVIDE": 0x6F,
}
VK.update({str(d): 0x30 + d for d in range(10)})
VK.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({f"F{i}": 0x70 + i - 1 for i in range(1, 25)})
VK.update({f"NUMPAD{d}": 0x60 + d for d in range(10)})

ALIASES = {"CONTROL": "CTRL", "ESCAPE": "ESC", "RETURN": "ENTER", "SCROLL": "SCROLLLOCK", "BREAK": "PAUSE",
           "PGUP": "PAGEUP", "PGDN": "PAGEDOWN", "DEL": "DELETE", "INS": "INSERT", "MINUS": "-", "EQUALS": "=",
           "SLASH": "/", "PLUS": "="}

_NAMES: dict[int, str] = {}
for _name, _vk in VK.items():
    _NAMES.setdefault(_vk, _name)
_NAMES[0x0D] = "ENTER"
_NAMES[0x1B] = "ESC"

MODIFIER_VKS: dict[int, Mod] = {
    0x10: Mod.SHIFT, 0xA0: Mod.SHIFT, 0xA1: Mod.SHIFT,
    0x11: Mod.CTRL, 0xA2: Mod.CTRL, 0xA3: Mod.CTRL,
    0x12: Mod.ALT, 0xA4: Mod.ALT, 0xA5: Mod.ALT,
}
# Generic VK posted for each modifier.
MOD_VK = {Mod.SHIFT: 0x10, Mod.CTRL: 0x11, Mod.ALT: 0x12}
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x6F, 0x90, 0xA3, 0xA5}

_MOD_WORDS = {"CTRL": Mod.CTRL, "CONTROL": Mod.CTRL, "SHIFT": Mod.SHIFT, "ALT": Mod.ALT}


def vk_from_name(name: str) -> int:
    key = name.strip().upper()
    key = ALIASES.get(key, key)
    if key == "ESC":
        return 0x1B
    try:
        return VK[key]
    except KeyError:
        raise ValueError(f"unknown key name {name!r}") from None


def vk_name(vk: int) -> str:
    return _NAMES.get(vk, f"VK_{vk:02X}")


@dataclass(frozen=True, order=True)
class Chord:
    vk: int
    mods: Mod = Mod.NONE

    def __str__(self) -> str:
        parts = [w for w, m in (("Ctrl", Mod.CTRL), ("Alt", Mod.ALT), ("Shift", Mod.SHIFT)) if self.mods & m]
        name = vk_name(self.vk)
        parts.append(name.title() if len(name) > 1 and not name.startswith(("F", "NUMPAD")) else name)
        return "+".join(parts)


def parse_chord(text: str) -> Chord:
    """'Ctrl+Alt+Pause' -> Chord(vk=0x13, mods=CTRL|ALT). A lone '+' or '-' is a key."""
    text = text.strip()
    if not text:
        raise ValueError("empty key")
    parts = [text] if text in ("+", "-") else re.split(r"\+(?=.)", text)
    mods = Mod.NONE
    for word in parts[:-1]:
        try:
            mods |= _MOD_WORDS[word.strip().upper()]
        except KeyError:
            raise ValueError(f"unknown modifier {word!r} in {text!r}") from None
    return Chord(vk_from_name(parts[-1]), mods)


_RANGE = re.compile(r"^(?:(?P<pre>[A-Za-z+]*\+))?(?P<a>F?\d+|[A-Za-z])-(?P<b>F?\d+|[A-Za-z])$")


def expand_key_spec(spec: str) -> list[Chord]:
    """'1-9' / 'F1-F4' / 'A-D' / 'Ctrl+1-3' expand to several chords; anything else is one chord."""
    m = _RANGE.match(spec.strip())
    if m:
        pre, a, b = m.group("pre") or "", m.group("a").upper(), m.group("b").upper()
        if a.startswith("F") and b.startswith("F") and a[1:].isdigit() and b[1:].isdigit():
            names = [f"F{i}" for i in range(int(a[1:]), int(b[1:]) + 1)]
        elif a.isdigit() and b.isdigit() and len(a) == len(b) == 1:
            names = [str(i) for i in range(int(a), int(b) + 1)]
        elif a.isalpha() and b.isalpha() and len(a) == len(b) == 1:
            names = [chr(c) for c in range(ord(a), ord(b) + 1)]
        else:
            names = []
        if names:
            return [parse_chord(pre + n) for n in names]
    return [parse_chord(spec)]
