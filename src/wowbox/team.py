"""Runtime model of the team: slots, their windows, leader, and target resolution."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from .bridge.decoder import ClientState
from .config import AppConfig, Target


@dataclass
class Client:
    slot: int
    label: str
    role: str
    pid: int | None = None
    hwnd: int | None = None
    broadcast: bool = True  # include in all/followers broadcasts (UI checkbox)
    alive: bool = False
    responding: bool = True
    state: ClientState | None = None
    mem: dict[str, object] = field(default_factory=dict)
    character: str = ""  # in-game name reported by the bridge
    class_token: str = ""

    # what moves with the window when two slots swap
    BINDING = ("pid", "hwnd", "alive", "responding", "state", "mem", "character", "class_token")

    @property
    def attached(self) -> bool:
        return self.hwnd is not None and self.alive

    @property
    def name(self) -> str:
        who = self.character or self.label
        return f"WoW{self.slot}" + (f" {who}" if who else "")

    @property
    def window_title(self) -> str:
        return f"WoW{self.slot}" + (f" - {self.character}" if self.character else "")


class Team:
    def __init__(self, cfg: AppConfig) -> None:
        self.lock = threading.RLock()
        self.clients: dict[int, Client] = {
            slot: Client(slot, s.label, s.role) for slot, s in sorted(cfg.team.slots.items())}
        self.leader = cfg.team.leader
        self.main_slot = cfg.team.leader  # the big window in main_thumbs / tabs layouts

    def reload(self, cfg: AppConfig) -> None:
        with self.lock:
            old = self.clients
            self.clients = {}
            for slot, s in sorted(cfg.team.slots.items()):
                c = old.get(slot) or Client(slot, s.label, s.role)
                c.label, c.role = s.label, s.role
                self.clients[slot] = c
            self.leader = cfg.team.leader
            if self.main_slot not in self.clients:
                self.main_slot = self.leader

    @property
    def slots(self) -> list[int]:
        return list(self.clients)

    def attached(self) -> list[Client]:
        return [c for c in self.clients.values() if c.attached]

    def slot_for_hwnd(self, hwnd: int | None) -> int | None:
        if not hwnd:
            return None
        for c in self.clients.values():
            if c.hwnd == hwnd:
                return c.slot
        return None

    def hwnd(self, slot: int) -> int | None:
        c = self.clients.get(slot)
        return c.hwnd if c and c.attached else None

    def swap(self, a: int, b: int) -> None:
        """Exchange the windows bound to slots a and b (labels, roles and broadcast ticks stay with the slot)."""
        with self.lock:
            ca, cb = self.clients[a], self.clients[b]
            for attr in Client.BINDING:
                va, vb = getattr(ca, attr), getattr(cb, attr)
                setattr(ca, attr, vb)
                setattr(cb, attr, va)

    def assign_by_character(self, wanted: dict[int, str]) -> list[tuple[str, int, int]]:
        """Move each window whose character matches `wanted[slot]` into that slot.
        Returns (character, from_slot, to_slot) for every move made."""
        moves = []
        with self.lock:
            for slot, name in wanted.items():
                if slot not in self.clients or not name:
                    continue
                if self.clients[slot].character.lower() == name.lower():
                    continue
                src = next((c.slot for c in self.clients.values()
                            if c.character and c.character.lower() == name.lower()), None)
                if src is not None:
                    self.swap(src, slot)
                    moves.append((name, src, slot))
        return moves

    @staticmethod
    def _matches(c: Client, target: str) -> bool:
        """class:WARLOCK / role:healer / char:Name / Name (case-insensitive)."""
        kind, sep, arg = target.partition(":")
        kind, arg = kind.strip().lower(), arg.strip().lower()
        if sep and kind == "class":
            return c.class_token.lower() == arg.replace(" ", "")
        if sep and kind == "role":
            return c.role.lower() == arg
        name = arg if sep and kind == "char" else target.strip().lower()
        return c.character.lower() == name or (not c.character and c.label.lower() == name)

    def command_slots(self, who: str, origin: int) -> list[int]:
        """Who a `/bb all|others|to <who>` command typed in `origin` is for. all/others ignore the
        Broadcast checkboxes (a /reload should reach everyone); anything else is a target as seen from `origin`."""
        attached = [c.slot for c in self.attached()]
        if who.lower() == "all":
            return attached
        if who.lower() == "others":
            return [s for s in attached if s != origin]
        return self.resolve([int(who)] if who.isdigit() else who, origin)

    def resolve(self, target: Target, active: int | None) -> list[int]:
        """Slots a target refers to, given the active (foreground) slot. Only attached clients."""
        if isinstance(target, str) and target[:4].lower() == "not:":
            return sorted(set(self.resolve("all", active)) - set(self.resolve(target[4:].strip(), active)))
        attached = {c.slot for c in self.attached()}
        enabled = {c.slot for c in self.attached() if c.broadcast}
        if isinstance(target, list):
            chosen = set(target) & attached
        elif target == "all":
            chosen = enabled | ({active} & attached if active is not None else set())
        elif target == "active":
            chosen = {active} & attached if active is not None else set()
        elif target == "followers":
            chosen = enabled - {active}
        elif target == "leader":
            chosen = {self.leader} & attached
        elif target == "none":
            chosen = set()
        else:
            chosen = {c.slot for c in self.attached() if self._matches(c, target)}
        return sorted(chosen)
