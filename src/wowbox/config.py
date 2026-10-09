"""team.yaml: pydantic models, load and save."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from .keys import expand_key_spec, parse_chord

Role = Literal["tank", "healer", "dps"]
LayoutName = Literal["grid_auto", "grid_2x2", "grid_1x3", "main_thumbs", "tabs"]
TargetName = Literal["all", "active", "followers", "leader", "none"]
TARGET_NAMES = {"all", "active", "followers", "leader", "none"}


def _check_target(v: Any) -> Any:
    """all | active | followers | leader | none | [slots] | slot | class:WARLOCK | role:healer | char:Name | Name
    | not:<any of these but a list>, e.g. not:leader"""
    if isinstance(v, int):
        return [v]
    if isinstance(v, str):
        kind, sep, arg = v.partition(":")
        if sep and kind.lower() in ("class", "role", "char", "not") and not arg.strip():
            raise ValueError(f"empty target {v!r}")
        if not v.strip():
            raise ValueError("empty target")
    return v


# A bare string that isn't a keyword or class:/role:/char: is taken as a character name.
Target = Annotated[Union[list[int], str], BeforeValidator(_check_target)]

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$")
_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, None: 1.0}


def parse_duration(value: Any) -> float:
    """'1.5s' / '25m' / '200ms' / 3 -> seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    m = _DURATION.match(str(value))
    if not m:
        raise ValueError(f"bad duration {value!r} (use e.g. 500ms, 1.5s, 25m, 1h)")
    return float(m.group(1)) * _UNITS[m.group(2)]


Duration = Annotated[float, BeforeValidator(parse_duration)]


def _check_chord(v: str) -> str:
    parse_chord(v)
    return v


ChordStr = Annotated[str, BeforeValidator(lambda v: _check_chord(str(v)))]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientConfig(_Model):
    exe: str = "C:/Games/WoW335/Wow.exe"
    args: list[str] = []
    set_windowed_cvars: bool = False
    resolution: str | None = None  # gxResolution to write with set_windowed_cvars, e.g. "1280x720"
    launch_stagger: Duration = 3.0
    window_timeout: Duration = 120.0
    rename_windows: bool = True


class SlotConfig(_Model):
    label: str = ""
    role: Role = "dps"
    character: str | None = None  # in-game name (from the bridge); the window showing it is put in this slot


class TeamConfig(_Model):
    leader: int = 1
    slots: dict[int, SlotConfig] = Field(default_factory=lambda: {1: SlotConfig(label="Main", role="tank")})

    @model_validator(mode="after")
    def _leader_in_slots(self) -> "TeamConfig":
        if self.slots and self.leader not in self.slots:
            raise ValueError(f"leader {self.leader} is not one of the slots {sorted(self.slots)}")
        for s in self.slots:
            if not 1 <= s <= 8:
                raise ValueError(f"slot numbers must be 1-8, got {s}")
        return self


class LayoutConfig(_Model):
    default: LayoutName = "grid_auto"
    monitor: int = 1  # 1-based, in Windows' monitor order (primary first)
    thumbs_monitor: int | None = None  # None = strip on the same monitor
    thumb_strip: float = 0.22  # fraction of the monitor height used by the thumbnail strip
    tab_bar_height: int = 28


class HotkeyConfig(_Model):
    toggle_broadcast: ChordStr | None = "ScrollLock"
    release_all: ChordStr | None = "Ctrl+Alt+Pause"
    cycle_layout: ChordStr | None = "Ctrl+Alt+L"
    next_main: ChordStr | None = "Ctrl+Alt+N"
    automation_kill: ChordStr | None = "Ctrl+Alt+K"
    follow: ChordStr | None = "F10"  # followers target the leader and /follow; follow mode ON
    stay: ChordStr | None = "F11"  # followers stop where they are; follow mode OFF
    mirror_movement: ChordStr | None = "Ctrl+Alt+M"  # toggle copying your movement keys to followers
    stack: ChordStr | None = "F12"  # walk everyone onto the spot and facing of the window you're in
    focus_slot: dict[int, ChordStr] = {}  # e.g. {1: "Ctrl+Alt+1"}


class BroadcastConfig(_Model):
    enabled_on_start: bool = True
    toggle_key: ChordStr | None = None  # legacy names from the plan; copied into hotkeys
    release_all_key: ChordStr | None = None
    chatguard: bool = True
    chatguard_open_keys: list[ChordStr] = ["Enter", "/"]
    chatguard_close_keys: list[ChordStr] = ["Enter", "Esc"]
    # How chords with Shift/Ctrl/Alt reach background clients (spike S2 decides):
    #   post        post modifier down/up messages around the key
    #   focus_swap  briefly focus each target and SendInput the chord
    #   strip       send the bare key without modifiers
    modifier_mode: Literal["post", "focus_swap", "strip"] = "post"
    focus_swap_dwell: Duration = 0.04
    only_when_wow_focused: bool = True


class RouteConfig(_Model):
    keys: list[str]
    to: Target = "active"
    note: str = ""

    @field_validator("keys")
    @classmethod
    def _keys_parse(cls, v: list[str]) -> list[str]:
        for spec in v:
            expand_key_spec(str(spec))
        return [str(s) for s in v]


class BridgeConfig(_Model):
    enabled: bool = True
    hz: float = 10.0
    block_size: int = 8  # px per block as set by /boxbridge size (the decoder tolerates scaling)
    method: Literal["auto", "mss", "dxcam", "printwindow"] = "auto"
    stale_after: Duration = 1.5
    commands: bool = True  # carry out /bb all|others|to <command> typed in a client


class Condition(_Model):
    """`slot` plus any ClientState field, optionally suffixed _lt/_lte/_gt/_gte/_ne."""
    model_config = ConfigDict(extra="allow")
    slot: int | list[int] | Literal["any", "all"] = "any"


class ActionConfig(_Model):
    send: ChordStr | None = None  # one key press, e.g. "Ctrl+9"
    type: str | None = None  # a chat line, e.g. "/assist Tinkerdawn" (Enter, text, Enter)
    keys: list[str] | None = None  # several presses in order, e.g. ["1-9", "0"], `delay` apart
    delay: Duration = 0.25  # between `keys`
    wait: Duration | None = None
    sequence: list["ActionConfig"] | None = None
    macro: str | None = None  # start a macro by name
    to: Target = "all"

    @field_validator("keys")
    @classmethod
    def _keys_parse(cls, v: list[str] | None) -> list[str] | None:
        for spec in v or []:
            expand_key_spec(str(spec))
        return v

    @model_validator(mode="after")
    def _one_kind(self) -> "ActionConfig":
        kinds = [k for k in ("send", "type", "keys", "wait", "sequence", "macro") if getattr(self, k) is not None]
        if len(kinds) != 1:
            raise ValueError(f"an action needs exactly one of send / type / keys / wait / sequence / macro, "
                             f"got {kinds or 'none'}")
        return self


class MacroConfig(_Model):
    """A named sequence you trigger with a hotkey (from any WoW window) or the Run button."""
    name: str
    key: ChordStr | None = None
    do: ActionConfig
    note: str = ""


ChatChannel = Literal["whisper", "party", "raid", "guild", "officer", "say", "yell"]
CHAT_CHANNELS = ("whisper", "party", "raid", "guild", "officer", "say", "yell")


def _as_list(v: Any) -> Any:
    return [v] if isinstance(v, str) else v


class ChatTrigger(_Model):
    """Fires when a client's BoxBridge reports a chat line that matches."""
    channel: Annotated[list[ChatChannel | Literal["any"]], BeforeValidator(_as_list)] = ["whisper"]
    sender: Annotated[list[str] | None, BeforeValidator(_as_list)] = None  # character names, any case
    contains: Annotated[list[str] | None, BeforeValidator(_as_list)] = None  # any of these words, any case
    heard_by: Target = "all"  # which of your clients must have received it


class AutomationRule(_Model):
    name: str
    enabled: bool = True
    when: Condition | None = None
    every: Duration | None = None
    chat: ChatTrigger | None = None
    do: ActionConfig
    cooldown: Duration = 0.0

    @model_validator(mode="after")
    def _trigger(self) -> "AutomationRule":
        triggers = [t for t in ("when", "every", "chat") if getattr(self, t) is not None]
        if triggers == ["when", "chat"]:  # chat line plus an extra state condition
            return self
        if len(triggers) != 1:
            raise ValueError(f"rule {self.name!r}: give exactly one of `when`, `every` or `chat` "
                             f"(`when` may be added to `chat` as an extra check)")
        return self


class AutomationOptions(_Model):
    enabled_on_start: bool = False
    dry_run: bool = True
    tick_hz: float = 10.0
    pause_on_stale_bridge: bool = True


class MemReadField(_Model):
    address: str  # hex; base address, relative to module base if `module` is set
    module: str | None = None
    offsets: list[str] = []  # pointer chain applied after reading `address`
    type: Literal["u8", "u16", "u32", "i32", "u64", "float", "double"] = "u32"


class MemReadConfig(_Model):
    enabled: bool = False
    hz: float = 5.0
    fields: dict[str, MemReadField] = {}


class FollowConfig(_Model):
    """Follow mode: everyone follows the leader, and WoWBox puts them back on follow when it breaks."""
    leader: Target = "leader"  # who to follow (usually the leader slot)
    who: Target = "not:leader"  # who follows
    start: list[str] = ["/target {leader}", "/follow {leader}"]  # typed when you press `follow`
    start_key: ChordStr | None = None  # or press this (an in-game /follow macro) instead of typing `start`
    refollow: bool = True  # re-issue follow when a follower's bridge says it stopped following
    refollow_command: str = "/follow {leader}"  # doesn't change their target
    refollow_key: ChordStr | None = None  # or press this instead
    refollow_delay: Duration = 1.0  # how long a follower must be off follow (and you not moving) first
    refollow_in_combat: bool = False  # off: casters stay put in a fight and follow again after it
    retry: Duration = 2.0  # wait between attempts on one follower; doubles while it keeps failing
    retry_max: Duration = 15.0
    stop_key: ChordStr | None = "S"  # `stay` taps this on followers (any movement breaks /follow)
    mirror_on_start: bool = False
    movement_keys: list[str] = ["W", "A", "S", "D", "Q", "E", "Space"]  # copied to followers while mirroring

    @field_validator("movement_keys")
    @classmethod
    def _keys_parse(cls, v: list[str]) -> list[str]:
        for spec in v:
            expand_key_spec(str(spec))
        return v


class StackConfig(_Model):
    """Stacking: walk followers onto one character's exact spot and facing, using BoxBridge positions."""
    who: Target = "all"  # who gets stacked (the character they stack on is always left out)
    on_stop: bool = True  # while mirroring movement: re-stack every time you stop moving
    on_stop_delay: Duration = 0.4  # how long you must have stopped first
    tolerance_yards: float = 0.5
    facing_tolerance_deg: float = 3.0
    timeout: Duration = 10.0  # give up on a follower after this long
    run_speed: float = 7.0  # yards/s, to turn the measured speed into yards (unmounted run)
    turn_rate_deg: float = 180.0  # first guess for keyboard turning; measured as it goes
    map_aspect: float = 1.5  # zone map width/height in yards per map unit (WoW zone maps are 3:2)
    forward: ChordStr = "W"
    back: ChordStr = "S"
    strafe_left: ChordStr = "Q"
    strafe_right: ChordStr = "E"
    turn_left: ChordStr = "A"
    turn_right: ChordStr = "D"


class AppConfig(_Model):
    client: ClientConfig = ClientConfig()
    team: TeamConfig = TeamConfig()
    layout: LayoutConfig = LayoutConfig()
    hotkeys: HotkeyConfig = HotkeyConfig()
    broadcast: BroadcastConfig = BroadcastConfig()
    follow: FollowConfig = FollowConfig()
    stack: StackConfig = StackConfig()
    routes: list[RouteConfig] = []
    bridge: BridgeConfig = BridgeConfig()
    macros: list[MacroConfig] = []
    automation: list[AutomationRule] = []
    automation_options: AutomationOptions = AutomationOptions()
    memread: MemReadConfig = MemReadConfig()
    ui: dict[str, Any] = {}

    @model_validator(mode="after")
    def _legacy_keys(self) -> "AppConfig":
        if self.broadcast.toggle_key:
            self.hotkeys.toggle_broadcast = self.broadcast.toggle_key
        if self.broadcast.release_all_key:
            self.hotkeys.release_all = self.broadcast.release_all_key
        return self


def load_config(path: str | Path) -> AppConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


def save_config(cfg: AppConfig, path: str | Path) -> None:
    """Writes the whole config back (YAML comments are not preserved; a .bak copy is kept)."""
    path = Path(path)
    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    data = cfg.model_dump(mode="json", exclude_none=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
