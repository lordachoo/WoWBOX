"""Pixels -> ClientState. Must stay in sync with addon/BoxBridge/BoxBridge.lua.

The addon draws a row of square blocks at the client's top-left corner. Each block carries two data
bytes in R and G; B is a marker that alternates between 224 (even blocks) and 32 (odd blocks), so every
block boundary is a visible edge even when two blocks hold the same data. The decoder finds blocks by
those edges rather than assuming a width, so it works however the game stretches its picture to the window.

Byte stream (2 bytes per block):

     0  0xB0 magic           1  0x0E magic           2  frame counter
     3  health 0-255         4  power 0-255          5  power type (bits 0-2); bits 3-7: DoTs 10-14 needed
     6  target health        7  target flags (bits 0-4); bits 5-7: DoTs 1-3 needed
     8  player flags (bits 0-3); bits 4-7: watched spells 1-4 ready
     9  input flags (bits 0-1); bits 2-7: DoTs 4-9 needed         10  cast flags (bits 0-1); bits 2-7: version of the chat filter loaded (Words.lua, 0 = none)
    11  status flags (bits 0-4); bits 5-7: watched spells 5-7 ready
    12  party1 health       13  party2 health       14  party3 health    15  party4 health
    16  level               17  party-exists bits (0-3); bits 4-7: chat lines dropped (queue full), mod 16
    18-37  character name, UTF-8, zero padded (20 bytes)
    38-40  target id: a 24-bit hash of the target's GUID, high byte first (0 = no target, or an older addon)
    41  class (CLASS_IDS, bits 0-3); bits 4-5: target distance (1: within ~10 yd, 2: ~28 yd, 3: farther / none; 0: older addon); bit 6: can't hit the target (facing / too far, last 3 s); bit 7: auto-attack on
    42-44  map x, 0-16777215 = 0.0-1.0 (GetPlayerMapPosition; high byte first)
    45-47  map y, same
    48-49  facing, 0-65535 = 0-2pi radians (GetPlayerFacing: 0 = north, counter-clockwise)
    50-51  map area id (GetCurrentMapAreaID)
    52  bit 0: position valid, bits 1-4: map dungeon level; bits 5-7: watched buffs 1-3 needed
    53  frames per second (GetFramerate, max 127); bit 7: watched buff 4 needed
    54  sum(bytes 0..53) % 256                      55  xor(bytes 0..53)

Watched spells are the `spells` list in Words.lua (the spells `ready:` conditions ask about): a bit is set while
that spell could be cast now, off its cooldown and the global cooldown. Older addons leave them 0.
DoTs are the `dots` list (what `dot:` conditions ask about): a bit is set while that spell should go on the target
now: castable, and not on the target from you (or about to run out).
Buffs are the `buffs` list (what `buff_missing:` conditions ask about): a bit is set while that buff should be cast
on yourself now: castable, and not on you (or about to run out). Before BoxBridge 1.7, byte 52 bits 5-7 were the
dungeon level's high bits and byte 53 the full fps: WoWBox only reads buff bits through the list the client loaded.

Older addons sent 0x0B as the second magic byte with a 24-byte name, a 12-byte class token in 42-53 and
no position; those frames still decode. 0x0D (16-bit positions) is rejected with a hint to /reload.

Chat frames use the same strip with 0x0C as the second magic byte. The addon shows one for each chat line
it hears, held for about half a second, between normal state frames:

     0  0xB0 magic           1  0x0C magic           2  frame counter (shared with state frames)
     3  event sequence       4  channel (CHAT_CHANNELS)  5  full message length in bytes (max 255)
     6-25   sender, UTF-8, zero padded (20 bytes)
    26-52   message, UTF-8, zero padded (27 bytes; longer messages are cut)
    53  input flags right now (same bits as byte 9 of a state frame)
    54  sum % 256                                   55  xor
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, fields

import numpy as np

MAGIC = (0xB0, 0x0B)
KIND_STATE_V1, KIND_CHAT, KIND_STATE_V2, KIND_STATE = 0x0B, 0x0C, 0x0D, 0x0E
CLASS_IDS = {"WARRIOR": 1, "PALADIN": 2, "HUNTER": 3, "ROGUE": 4, "PRIEST": 5, "DEATHKNIGHT": 6, "SHAMAN": 7,
             "MAGE": 8, "WARLOCK": 9, "DRUID": 11}
CLASS_TOKENS = {v: k for k, v in CLASS_IDS.items()}
POS_VALID = 1
U24_MAX = 0xFFFFFF
MARK_EVEN, MARK_ODD = 224, 32
NAME_BYTES_V1 = slice(18, 42)
NAME_BYTES = slice(18, 38)
TARGET_ID_BYTES = 38
CLASS_BYTES = slice(42, 54)
CHAT_SENDER_BYTES = slice(6, 26)
CHAT_TEXT_BYTES = slice(26, 53)
CHAT_INPUT_BYTE = 53
CHAT_KEYWORD = 1  # first text byte: the next one is the matched word's number in Words.lua, then the text after it
CHAT_AFTER_WORD = CHAT_TEXT_BYTES.stop - CHAT_TEXT_BYTES.start - 2  # bytes of text after a matched word
NUM_BYTES = 56
MAX_WATCHED_SPELLS = 7
MAX_DOTS = 14
MAX_BUFFS = 4
RANGE_UNKNOWN = 999  # an older addon: no distance, so `target_range_lte` is never true
TARGET_RANGES = (RANGE_UNKNOWN, 10, 28, 100)  # yards, by the 2 bits in byte 41 (100: farther, or no target)
NUM_BLOCKS = NUM_BYTES // 2

# flag bits
T_EXISTS, T_HOSTILE, T_DEAD, T_PLAYER, T_COMBAT = 1, 2, 4, 8, 16
P_COMBAT, P_RESTING, P_PARTY, P_RAID = 1, 2, 4, 8
I_TYPING, I_CHAT = 1, 2
C_CASTING, C_CHANNELING = 1, 2
S_MOUNTED, S_DEAD, S_GHOST, S_AFK, S_FOLLOWING = 1, 2, 4, 8, 16

POWER_TYPES = {0: "mana", 1: "rage", 2: "focus", 3: "energy", 6: "runic"}
# channel byte -> name; order matches the addon's CHANNELS table
CHAT_CHANNELS = {1: "whisper", 2: "party", 3: "raid", 4: "guild", 5: "officer", 6: "say", 7: "yell",
                 8: "command"}  # command: you typed /bb all|others|to; sender = who it's for, text = the command


@dataclass(frozen=True)
class ChatEvent:
    channel: str
    sender: str
    text: str
    seq: int = 0
    length: int = 0  # full length in bytes; more than len(text) when the addon had to cut it
    frame: int = 0
    typing: bool = False  # the client's input flags while the line is shown: the chat frame
    chat_open: bool = False  # replaces state frames for a while, so it carries these itself
    # 1-based number of the matched word in the channel's Words.lua list, 0 = none. Then `text` is only what came
    # after the word, and `length` its full length; WoWBox puts the word back (Controller._with_keyword)
    keyword: int = 0

    @property
    def truncated(self) -> bool:
        return self.length > len(self.text.encode("utf-8"))


@dataclass
class ClientState:
    frame: int = 0
    health_pct: float = 0.0
    power_pct: float = 0.0
    power_type: int = 0
    target_exists: bool = False
    target_hostile: bool = False
    target_dead: bool = False
    target_is_player: bool = False
    target_in_combat: bool = False
    target_health_pct: float = 0.0
    combat: bool = False
    resting: bool = False
    in_party: bool = False
    in_raid: bool = False
    typing: bool = False  # any edit box has keyboard focus
    chat_open: bool = False
    casting: bool = False
    channeling: bool = False
    mounted: bool = False
    dead: bool = False
    ghost: bool = False
    afk: bool = False
    following: bool = False  # on /follow (AUTOFOLLOW_BEGIN .. AUTOFOLLOW_END)
    position_ok: bool = False  # map_x/map_y/facing/map_id are valid
    map_x: float = 0.0  # 0-1 across the current zone map, west to east
    map_y: float = 0.0  # 0-1, north to south
    facing: float = 0.0  # radians, 0 = north, counter-clockwise
    map_id: int = 0
    map_level: int = 0
    fps: int = 0  # the client's frame rate: keys only register once per frame
    chat_filter: int = 0  # version of Words.lua the addon loaded (0: none, or an older addon: every line passes)
    chat_dropped: int = 0  # chat lines the addon threw away because its queue was full, mod 16
    ready_bits: int = 0  # bit i: watched spell i+1 (Words.lua `spells`) can be cast now
    ready: tuple[str, ...] = ()  # those spells by name, lower case (filled in by WoWBox from the list the client loaded)
    dot_bits: int = 0  # bit i: DoT i+1 (Words.lua `dots`) should go on the target now
    dots: tuple[str, ...] = ()  # those DoTs by name, lower case
    buff_bits: int = 0  # bit i: buff i+1 (Words.lua `buffs`) should be cast on yourself now
    buffs: tuple[str, ...] = ()  # those buffs by name, lower case
    target_range: int = RANGE_UNKNOWN  # yards to the target, measured by the game: 10, 28, 100 (farther / none)
    attacking: bool = False  # auto-attack is on (PLAYER_ENTER_COMBAT): fighting on purpose, not just chased
    cant_hit: bool = False  # the game just said "facing the wrong way" / "too far away" / "out of range" (last 3 s, same target)
    target_id: int = 0  # which mob the target is (the same in every client); 0: no target, or an older addon
    level: int = 0
    party_health_pct: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    party_exists: tuple[bool, bool, bool, bool] = (False, False, False, False)
    name: str = ""
    class_token: str = ""
    stale: bool = False
    updated_at: float = field(default_factory=time.monotonic)

    @property
    def power_name(self) -> str:
        return POWER_TYPES.get(self.power_type, str(self.power_type))

    def summary(self) -> str:
        if self.stale:
            return "stale"
        t = f"T {self.target_health_pct:.0f}%" if self.target_exists else "no target"
        flags = [n for n, v in (("combat", self.combat), ("casting", self.casting or self.channeling),
                                ("typing", self.typing), ("mounted", self.mounted), ("following", self.following), ("dead", self.dead or self.ghost))
                 if v]
        return (f"{self.name or '?'} {self.class_token.title()} | HP {self.health_pct:.0f}% "
                f"{self.power_name[:4]} {self.power_pct:.0f}% | {t} | {' '.join(flags)}")


STATE_FIELDS = {f.name for f in fields(ClientState)} | {"power_name"}


class DecodeError(ValueError):
    pass


def checksum(values: list[int] | bytes) -> tuple[int, int]:
    s, x = 0, 0
    for v in values:
        s += v
        x ^= v
    return s % 256, x


def _close(px: np.ndarray, rgb: tuple[int, int, int], tol: int) -> bool:
    return all(abs(int(px[i]) - rgb[i]) <= tol for i in range(3))


def _runs(row: np.ndarray, tol: int) -> list[tuple[int, int]]:
    """Split a pixel row into (start, length) runs of near-constant colour."""
    px = row.astype(np.int16)
    n = px.shape[0]
    runs, start = [], 0
    while start < n:  # a numpy step per run, not one per pixel (this ran ~30,000 times a second)
        end = start + 1
        while end < n:  # look ahead a block or two at a time: runs are short
            far = np.abs(px[end:end + 64] - px[start]).max(axis=1) > tol
            if far.any():
                end += int(far.argmax())
                break
            end += 64
        end = min(end, n)
        runs.append((start, end - start))
        start = end
    return runs


def _header(px: np.ndarray, tol: int) -> tuple[int, int, int] | None:
    for kind in (KIND_STATE, KIND_CHAT, KIND_STATE_V1, KIND_STATE_V2):
        if _close(px, (MAGIC[0], kind, MARK_EVEN), tol):
            return MAGIC[0], kind, MARK_EVEN
    return None


def decode(img: np.ndarray, *, tol: int = 4) -> ClientState:
    """A state frame. Raises DecodeError for anything else, chat frames included."""
    st = decode_frame(img, tol=tol)
    if not isinstance(st, ClientState):
        raise DecodeError("chat frame, not a state frame")
    return st


def decode_frame(img: np.ndarray, *, tol: int = 4) -> ClientState | ChatEvent:
    """img: RGB uint8 (h, w, 3) starting at the client's top-left pixel."""
    if img.ndim != 3 or img.shape[0] < 1 or img.shape[1] < NUM_BLOCKS:
        raise DecodeError("image too small")
    probe_x = 1 if img.shape[1] > 1 else 0
    header = next((h for y in range(min(2, img.shape[0])) if (h := _header(img[y, probe_x], tol))), None)
    if header is None:
        raise DecodeError("no BoxBridge header (addon off, UI hidden, or wrong capture region)")
    height = 0
    while height < img.shape[0] and _close(img[height, probe_x], header, tol):
        height += 1
    row = img[max(0, height // 2) if height else 1]

    runs = _runs(row, tol)
    header_len = runs[0][1]
    min_len = max(2, int(header_len * 0.35)) if header_len >= 3 else 1  # shorter runs are blended edge pixels
    blocks = [row[start + length // 2] for start, length in runs if length >= min_len]
    if len(blocks) < NUM_BLOCKS:
        raise DecodeError(f"only {len(blocks)} of {NUM_BLOCKS} blocks visible (strip covered or cut off?)")
    data = []
    for i, px in enumerate(blocks[:NUM_BLOCKS]):
        mark = MARK_EVEN if i % 2 == 0 else MARK_ODD
        if abs(int(px[2]) - mark) > 40:
            raise DecodeError(f"block {i}: marker {int(px[2])} != {mark} (blocks merged or split)")
        data += [int(px[0]), int(px[1])]
    s, x = checksum(data[:-2])
    if (s, x) != (data[-2], data[-1]):
        raise DecodeError(f"checksum failed ({len(blocks)} blocks, header {header_len}x{height}px)")
    if data[1] == KIND_CHAT:
        return _to_chat(bytes(data))
    if data[1] == KIND_STATE_V2:
        raise DecodeError("old BoxBridge version in this client: /reload it")
    return _to_state(bytes(data))


def _pct(v: int) -> float:
    return round(v * 100.0 / 255.0, 1)


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="ignore")  # "ignore": a cut may split a character


def _to_chat(d: bytes) -> ChatEvent:
    raw, keyword = d[CHAT_TEXT_BYTES], 0
    if raw[0] == CHAT_KEYWORD:
        raw, keyword = raw[2:], raw[1]
    return ChatEvent(channel=CHAT_CHANNELS.get(d[4], f"channel{d[4]}"), sender=_text(d[CHAT_SENDER_BYTES]),
                     text=_text(raw), seq=d[3], length=d[5], frame=d[2], keyword=keyword,
                     typing=bool(d[CHAT_INPUT_BYTE] & I_TYPING), chat_open=bool(d[CHAT_INPUT_BYTE] & I_CHAT))


def _to_state(d: bytes) -> ClientState:
    tf, pf, inp, cast, st, pbits = d[7], d[8], d[9], d[10], d[11], d[17]
    return ClientState(
        frame=d[2],
        health_pct=_pct(d[3]), power_pct=_pct(d[4]), power_type=d[5] & 7,
        target_health_pct=_pct(d[6]),
        target_exists=bool(tf & T_EXISTS), target_hostile=bool(tf & T_HOSTILE), target_dead=bool(tf & T_DEAD),
        target_is_player=bool(tf & T_PLAYER), target_in_combat=bool(tf & T_COMBAT),
        combat=bool(pf & P_COMBAT), resting=bool(pf & P_RESTING), in_party=bool(pf & P_PARTY),
        in_raid=bool(pf & P_RAID),
        typing=bool(inp & I_TYPING), chat_open=bool(inp & I_CHAT),
        casting=bool(cast & C_CASTING), channeling=bool(cast & C_CHANNELING), chat_filter=cast >> 2,
        mounted=bool(st & S_MOUNTED), dead=bool(st & S_DEAD), ghost=bool(st & S_GHOST), afk=bool(st & S_AFK),
        following=bool(st & S_FOLLOWING),
        party_health_pct=(_pct(d[12]), _pct(d[13]), _pct(d[14]), _pct(d[15])),
        level=d[16],
        party_exists=tuple(bool(pbits & (1 << i)) for i in range(4)),  # type: ignore[arg-type]
        chat_dropped=pbits >> 4,
        ready_bits=(pf >> 4) | (st >> 5) << 4,
        dot_bits=(tf >> 5) | (inp >> 2) << 3 | (d[5] >> 3) << 9,
        name=_text(d[NAME_BYTES_V1 if d[1] == KIND_STATE_V1 else NAME_BYTES]),
        target_id=0 if d[1] == KIND_STATE_V1 else _u24(d, TARGET_ID_BYTES),
        class_token=_text(d[CLASS_BYTES]) if d[1] == KIND_STATE_V1 else CLASS_TOKENS.get(d[41] & 15, ""),
        target_range=RANGE_UNKNOWN if d[1] == KIND_STATE_V1 else TARGET_RANGES[d[41] >> 4 & 3],
        cant_hit=d[1] != KIND_STATE_V1 and bool(d[41] & 64),
        attacking=d[1] != KIND_STATE_V1 and bool(d[41] & 128),
        **({} if d[1] == KIND_STATE_V1 else _position(d)),
    )


def _u16(d: bytes, i: int) -> int:
    return d[i] * 256 + d[i + 1]


def _u24(d: bytes, i: int) -> int:
    return (d[i] << 16) + (d[i + 1] << 8) + d[i + 2]


def _position(d: bytes) -> dict:
    return dict(position_ok=bool(d[52] & POS_VALID), map_x=_u24(d, 42) / U24_MAX, map_y=_u24(d, 45) / U24_MAX,
                facing=_u16(d, 48) / 65536.0 * 2 * math.pi, map_id=_u16(d, 50), map_level=d[52] >> 1 & 15,
                fps=d[53] & 127, buff_bits=d[52] >> 5 | (d[53] >> 7) << 3)


def encode_bytes(values: dict, frame: int) -> list[int]:
    """Reference encoder (mirrors the Lua addon)."""
    v = values
    party = list(v.get("party", [0, 0, 0, 0]))
    data = [MAGIC[0], KIND_STATE, frame % 256,
            v.get("health", 0), v.get("power", 0), v.get("power_type", 0) | (v.get("dot_bits", 0) >> 9 & 31) << 3,
            v.get("target_health", 0), v.get("target_flags", 0) | (v.get("dot_bits", 0) & 7) << 5,
            v.get("player_flags", 0) | (v.get("ready_bits", 0) & 15) << 4,
            v.get("input_flags", 0) | (v.get("dot_bits", 0) >> 3 & 63) << 2,
            v.get("cast_flags", 0) | (v.get("chat_filter", 0) << 2),
            v.get("status_flags", 0) | (v.get("ready_bits", 0) >> 4 & 7) << 5,
            *party[:4], v.get("level", 0), v.get("party_bits", 0) | (v.get("chat_dropped", 0) % 16) << 4]
    data += list(v.get("name", "").encode("utf-8")[:20].ljust(20, bytes(1)))
    tid = v.get("target_id", 0)
    data += [tid >> 16 & 255, tid >> 8 & 255, tid & 255]
    data.append(CLASS_IDS.get(v.get("class", ""), 0) | v.get("range_code", 0) << 4 | (64 if v.get("cant_hit") else 0)
                | (128 if v.get("attacking") else 0))
    pos = v.get("pos")  # (x, y, facing, map_id, level) or None
    if pos:
        x, y, facing, map_id, level = pos
        for n in (round(x * U24_MAX), round(y * U24_MAX)):
            data += [n >> 16, (n >> 8) & 255, n & 255]
        fx = round(facing / (2 * math.pi) * 65536) % 65536
        data += [fx >> 8, fx & 255, map_id >> 8, map_id & 255, POS_VALID | (level & 15) << 1]
    else:
        data += [0] * 11
    buffs = v.get("buff_bits", 0)
    data[52] |= (buffs & 7) << 5
    data.append(min(127, v.get("fps", 60)) | (buffs >> 3 & 1) << 7)
    data += list(checksum(data))
    assert len(data) == NUM_BYTES
    return data


def encode_chat_bytes(channel: str, sender: str, text: str, seq: int, frame: int, input_flags: int = 0,
                      keyword: int = 0) -> list[int]:
    """Reference encoder for chat frames (mirrors the Lua addon). keyword: `text` is what follows word number
    `keyword` of the channel's list."""
    code = next(k for k, v in CHAT_CHANNELS.items() if v == channel)
    raw = text.encode("utf-8")
    data = [MAGIC[0], KIND_CHAT, frame % 256, seq % 256, code, min(len(raw), 255)]
    if keyword:
        raw = bytes([CHAT_KEYWORD, keyword]) + raw
    data += list(sender.encode("utf-8")[:20].ljust(20, b"\0"))
    data += list(raw[:27].ljust(27, b"\0"))
    data.append(input_flags)
    data += list(checksum(data))
    assert len(data) == NUM_BYTES
    return data


def encode(values: dict, frame: int, block: int, data: list[int] | None = None) -> np.ndarray:
    """The strip as the addon draws it, `block` px per block (`data` overrides, e.g. a chat frame)."""
    data = data or encode_bytes(values, frame)
    img = np.zeros((block, block * NUM_BLOCKS, 3), dtype=np.uint8)
    for i in range(NUM_BLOCKS):
        img[:, i * block:(i + 1) * block] = (data[2 * i], data[2 * i + 1], MARK_EVEN if i % 2 == 0 else MARK_ODD)
    return img
