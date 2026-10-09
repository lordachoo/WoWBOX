"""Follow mode: followers /follow the leader, and get put back on follow when it breaks.

WoW's /follow breaks all the time: casting, a mount, a door, a jump, being too far away. BoxBridge reports
each client's follow state (AUTOFOLLOW_BEGIN/END), so while follow mode is on, `tick()` re-issues follow
for anyone who's been off it for `refollow_delay`. It holds off while you're moving with the movement keys
(so mirrored movement isn't fought), while a follower casts or fights (unless `refollow_in_combat`), and
for the window you're in (you're steering that one yourself). Failed attempts back off, so a leader who's
out of range doesn't get spammed.

Mirroring copies your movement keys to the followers as well, so they start, stop, turn and jump with you
instead of trailing behind. Positions still drift (different start points, collisions, mouse turning
isn't copied), which is why follow mode puts them back on /follow once you stop.
"""
from __future__ import annotations

import logging
from typing import Callable, Protocol

from .automation.actions import fill, safe_values
from .config import FollowConfig, Target
from .keys import Chord, expand_key_spec, parse_chord
from .team import Team

log = logging.getLogger(__name__)


class FollowSender(Protocol):
    mirror: bool
    mirror_vks: set[int]
    mirror_target: Target
    last_movement: float

    def type_text(self, text: str, target: Target) -> list[int]: ...

    def send_chord(self, chord: Chord, target: Target, check_typing: bool = True) -> list[int]: ...

    def active_slot(self) -> int | None: ...

    def movement_held(self) -> bool: ...

    def set_mirror(self, value: bool) -> None: ...


class FollowManager:
    def __init__(self, cfg: FollowConfig, team: Team, router: FollowSender,
                 on_change: Callable[[], None] = lambda: None) -> None:
        self.team = team
        self.router = router
        self.on_change = on_change
        self.active = False
        self._off_since: dict[int, float] = {}  # slot -> when we first saw it not following
        self._last_try: dict[int, float] = {}
        self._wait: dict[int, float] = {}  # slot -> current retry wait (backs off)
        self.load(cfg)

    def load(self, cfg: FollowConfig) -> None:
        self.cfg = cfg
        self.router.mirror_vks = {c.vk for spec in cfg.movement_keys for c in expand_key_spec(spec)}
        self.router.mirror_target = cfg.who
        if cfg.mirror_on_start:
            self.router.set_mirror(True)

    # ------------------------------------------------------------------ who
    def leader_name(self) -> str | None:
        active = self.router.active_slot()
        for slot in self.team.resolve(self.cfg.leader, active):
            if name := self.team.clients[slot].character:
                return name
        if isinstance(self.cfg.leader, str) and self.cfg.leader.lower() not in ("leader", "active", "all"):
            return self.cfg.leader  # a plain character name
        return None

    def followers(self) -> list[int]:
        active = self.router.active_slot()
        leaders = set(self.team.resolve(self.cfg.leader, active))
        return [s for s in self.team.resolve(self.cfg.who, active) if s not in leaders]

    # ------------------------------------------------------------------ commands
    def follow(self) -> None:
        """Followers target the leader and follow; follow mode ON."""
        leader = self.leader_name()
        if not leader:
            log.warning("follow: don't know the leader's character name yet (is BoxBridge running?)")
            return
        who = self.followers()
        if self.cfg.start_key:
            self.router.send_chord(parse_chord(self.cfg.start_key), who, check_typing=False)
        else:
            for line in self.cfg.start:
                self.router.type_text(self._fill(line, leader), who)
        self._reset()
        self.active = True
        log.info("follow mode ON: slots %s follow %s", who, leader)
        self.on_change()

    def stay(self) -> None:
        """Followers stop where they are; follow mode OFF."""
        self.active = False
        who = self.followers()
        if self.cfg.stop_key:
            self.router.send_chord(parse_chord(self.cfg.stop_key), who, check_typing=False)
        log.info("follow mode OFF: slots %s stay", who)
        self.on_change()

    def set_active(self, value: bool) -> None:
        self.follow() if value else self.stay()

    def toggle_mirror(self) -> None:
        self.router.set_mirror(not self.router.mirror)

    # ------------------------------------------------------------------ re-follow
    def tick(self, now: float) -> None:
        if not self.active or not self.cfg.refollow:
            return
        if self.router.movement_held() or now - self.router.last_movement < self.cfg.refollow_delay:
            self._off_since.clear()  # you're steering: count from when you stop
            return
        leader = None
        active = self.router.active_slot()
        for slot in self.followers():
            st = self.team.clients[slot].state
            if slot == active or st is None or st.stale:
                self._off_since.pop(slot, None)
                continue
            if st.following:
                self._off_since.pop(slot, None)
                self._wait.pop(slot, None)
                continue
            if st.dead or st.ghost or st.typing or st.casting or st.channeling or \
                    (st.combat and not self.cfg.refollow_in_combat):
                self._off_since.pop(slot, None)
                continue
            since = self._off_since.setdefault(slot, now)
            if now - since < self.cfg.refollow_delay:
                continue
            wait = self._wait.get(slot, 0.0)
            if now - self._last_try.get(slot, -1e9) < wait:
                continue
            leader = leader or self.leader_name()
            if not leader:
                return
            self._last_try[slot] = now
            self._wait[slot] = min(self.cfg.retry_max, wait * 2 if wait else self.cfg.retry)
            log.info("re-follow: slot %d (%s) is off follow, following %s again", slot,
                     self.team.clients[slot].name, leader)
            if self.cfg.refollow_key:
                self.router.send_chord(parse_chord(self.cfg.refollow_key), [slot])
            else:
                self.router.type_text(self._fill(self.cfg.refollow_command, leader), [slot])

    @staticmethod
    def _fill(text: str, leader: str) -> str:
        return fill(text, {"leader": safe_values({"sender": leader})["sender"]})  # a name: letters, digits, '-'


    def _reset(self) -> None:
        self._off_since.clear()
        self._last_try.clear()
        self._wait.clear()
