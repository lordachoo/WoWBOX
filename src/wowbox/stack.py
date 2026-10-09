"""Stacking: walk followers onto one character's exact spot and facing.

An addon can't move a character, but keys can. BoxBridge reports every client's map position and facing
(GetPlayerMapPosition / GetPlayerFacing); for each follower this module closes the loop:

1. turn (A/D) until it faces the way the anchor faces,
2. move without turning: W/S along its facing, Q/E sideways, one axis at a time, each as a timed key hold,
3. re-measure after every move and repeat until it's within `tolerance_yards`.

Nothing is assumed about speeds: the first move of each kind is a short probe, and every move updates the
measured speed (map units per second) and turn rate. Map units become yards through the measured run speed
(`run_speed`, 7 yd/s unmounted). Timed holds don't suffer from bridge latency (the game applies key down and
up with the same delay), so each correction lands close and the next one is smaller.

The game only applies a key on its next frame, so the smallest move or turn is one frame's worth: at 60 fps
that's 3 degrees or 0.12 yards, at 20 fps 9 degrees and 0.35 yards. BoxBridge reports the client's frame rate;
every key is held for a whole number of frame-times (which the game turns into exactly that many frames of
movement), and the tolerances are never tighter than half a frame allows.

Map coordinates: x runs west to east, y north to south, both 0-1 across the zone map. Zone maps are 3:2,
so one x unit is `map_aspect` times as many yards as one y unit. Facing: 0 = north, counter-clockwise.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .bridge.decoder import ClientState
from .config import StackConfig
from .keys import parse_chord

log = logging.getLogger(__name__)

PROBE_HOLD = 0.25  # first move of a kind, to measure its speed
MIN_LEARN_HOLD = 0.1  # only presses at least this long teach a speed (a frame off is then a small error)
UNTRUSTED_HOLD = 0.6  # longest move while a speed isn't confirmed by a second measurement
MAX_HOLD = 4.0
QUANTUM_SLACK = 0.55  # an error under half a frame's worth of movement can't be corrected (whole frames only)
COARSE_FACING_DEG = 8.0  # facing needed while walking; the exact facing is set at the end
MAX_STEPS = 16
FRESH_FRAMES = 3  # bridge frames to wait after a move, so the reading shows its result (the game only sees a
FRESH_MIN = 0.15  # key-up on its next frame, and a capture may show a frame drawn before that)
FRESH_TIMEOUT = 2.0
SPEED_SMOOTHING = 0.5


class StackError(Exception):
    pass


class Cancelled(StackError):
    pass


def wrap(angle: float) -> float:
    """-pi .. pi"""
    return (angle + math.pi) % (2 * math.pi) - math.pi


def forward_vector(facing: float) -> tuple[float, float]:
    """Unit vector (east, south) a character facing `facing` walks along with W."""
    return -math.sin(facing), -math.cos(facing)


def right_vector(facing: float) -> tuple[float, float]:
    """Unit vector (east, south) for strafing right with E."""
    return math.cos(facing), -math.sin(facing)


def apart(me: ClientState | None, leader: ClientState | None, max_yards: float,
          units_per_yard: float | None, aspect: float = 1.5) -> str | None:
    """Why `me` isn't with `leader`: "in another zone" or "85 yd away". None when they're together, or when
    there's no way to tell (no bridge data, unknown map, or the zone's scale isn't measured yet)."""
    if me is None or leader is None or me.stale or leader.stale:
        return None
    if me.map_id and leader.map_id and (me.map_id, me.map_level) != (leader.map_id, leader.map_level):
        return "in another zone"
    if me.position_ok and leader.position_ok and units_per_yard and me.map_id == leader.map_id:
        yards = math.hypot((me.map_x - leader.map_x) * aspect, me.map_y - leader.map_y) / units_per_yard
        if yards > max_yards:
            return f"{yards:.0f} yd away"
    return None


@dataclass
class StackResult:
    slot: int
    ok: bool
    distance_yards: float | None = None
    facing_error_deg: float | None = None
    moves: int = 0
    message: str = ""
    fps: int = 0


class Stacker:
    """The control loop for one follower at a time. Thread-safe enough to run one call per follower."""

    def __init__(self, cfg: StackConfig, read: Callable[[int], ClientState | None],
                 post: Callable[[int, int, bool], None],
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.read = read
        self.post = post
        self.clock = clock
        self.sleep = sleep
        self._speeds: dict[tuple, float] = {}  # (slot, map_id, level, key) -> map units / s
        self._samples: dict[tuple, int] = {}  # measurements that agreed with the estimate
        self._pending: dict[tuple, float] = {}  # a measurement that didn't fit, waiting for a second opinion
        self._turn_rate: float | None = None  # rad / s
        self.load(cfg)

    def load(self, cfg: StackConfig) -> None:
        self.cfg = cfg
        self.keys = {name: parse_chord(getattr(cfg, name)).vk
                     for name in ("forward", "back", "strafe_left", "strafe_right", "turn_left", "turn_right")}

    # ------------------------------------------------------------------ helpers
    def _pose(self, slot: int) -> ClientState:
        st = self.read(slot)
        if st is None or st.stale:
            raise StackError("no bridge data")
        if not st.position_ok:
            raise StackError("no map position here (an instance without coordinates, or the world map is open)")
        return st

    def _fresh(self, slot: int, cancel: threading.Event) -> ClientState:
        """Wait until the bridge shows a frame drawn after the last move, then return it."""
        st = self.read(slot)
        last = st.frame if st else None
        changes = 0
        start = self.clock()
        while True:
            if cancel.is_set():
                raise Cancelled("cancelled")
            st = self.read(slot)
            if st is not None and st.frame != last:
                changes += 1
                last = st.frame
            now = self.clock()
            if changes >= FRESH_FRAMES and now - start >= FRESH_MIN:
                return self._pose(slot)
            if now - start > FRESH_TIMEOUT:
                raise StackError("bridge stopped updating")
            self.sleep(0.01)

    def _hold(self, slot: int, key: str, seconds: float, cancel: threading.Event) -> None:
        vk = self.keys[key]
        self.post(slot, vk, True)
        try:
            end = self.clock() + seconds
            while (left := end - self.clock()) > 0:
                if cancel.is_set():
                    raise Cancelled("cancelled")
                self.sleep(min(left, 0.01))
        finally:
            self.post(slot, vk, False)

    @staticmethod
    def _fps(st: ClientState) -> float:
        return float(st.fps) if st.fps else 60.0

    def turn_rate(self) -> float:
        return self._turn_rate or math.radians(self.cfg.turn_rate_deg)

    def facing_tolerance(self, st: ClientState) -> float:
        """The configured tolerance, or half a frame of turning if that's coarser (nothing finer is possible)."""
        return max(math.radians(self.cfg.facing_tolerance_deg), QUANTUM_SLACK * self.turn_rate() / self._fps(st))

    def distance_tolerance(self, slot: int, st: ClientState) -> float | None:
        """In map units, or None while the speed is unknown."""
        upy = self.units_per_yard(st, slot)
        if upy is None:
            return None
        return max(self.cfg.tolerance_yards * upy, QUANTUM_SLACK * self._speed(slot, st, "forward") / self._fps(st))

    # Speeds are measured per character (a mounted or slowed one moves differently) and per map (each zone map
    # has its own scale). One bad measurement - the character was blocked, a reading was stale, or your own
    # mirrored keys moved it at the same time - must not poison the estimate: a value more than 2x off the
    # current one is set aside, and only replaces it if the next measurement agrees with it. A speed is
    # *trusted* once two measurements agree; only trusted speeds are used to turn map distances into yards for
    # the distance guards, and moves stay short while a speed isn't trusted yet.
    def _key(self, slot: int, st: ClientState, key: str) -> tuple:
        return slot, st.map_id, st.map_level, key

    def _speed(self, slot: int, st: ClientState, key: str) -> float | None:
        if (v := self._speeds.get(self._key(slot, st, key))) is not None:
            return v
        fwd = next((v for k in ("forward", "strafe_left", "strafe_right")
                    if (v := self._speeds.get(self._key(slot, st, k))) is not None), None)
        if fwd is None:
            return None
        return fwd * (4.5 / 7.0) if key == "back" else fwd  # backpedal is slower; strafing runs at full speed

    def _trusted(self, slot: int, st: ClientState, key: str) -> bool:
        return self._samples.get(self._key(slot, st, key), 0) >= 2

    def _learn(self, slot: int, st: ClientState, key: str, speed: float) -> None:
        k = self._key(slot, st, key)
        old = self._speeds.get(k)
        if old is None:
            self._speeds[k], self._samples[k] = speed, 1
            return
        if not 0.5 <= speed / old <= 2.0:
            pending = self._pending.pop(k, None)
            if pending is not None and 0.5 <= speed / pending <= 2.0:  # twice in a row: the speed really changed
                self._speeds[k], self._samples[k] = (speed + pending) / 2, 2
                log.info("stack: slot %d %s speed re-measured (mounted, slowed, or a bad first reading)", slot, key)
            else:
                self._pending[k] = speed
            return
        self._pending.pop(k, None)
        self._speeds[k] = old + (speed - old) * SPEED_SMOOTHING
        self._samples[k] = self._samples.get(k, 0) + 1

    def units_per_yard(self, st: ClientState, slot: int | None = None) -> float | None:
        """Map units per yard. With `slot`: from that character's own speed (trusted or not, for stacking it).
        Without: the zone's scale from trusted measurements of any character (for the distance guards),
        or None if nothing is trusted on this map yet."""
        if slot is not None:
            fwd = self._speed(slot, st, "forward")
            return fwd / self.cfg.run_speed if fwd else None
        trusted = sorted(v for k, v in self._speeds.items()
                         if k[1:3] == (st.map_id, st.map_level) and k[3] != "back" and self._samples.get(k, 0) >= 2)
        return trusted[len(trusted) // 2] / self.cfg.run_speed if trusted else None

    def _too_far(self, me: ClientState, anchor: ClientState) -> str | None:
        upy = self.units_per_yard(me)  # trusted zone scale only
        if upy is None:
            return None
        yards = math.hypot(*self._offset(me, anchor)) / upy
        return f"{yards:.0f} yd" if yards > self.cfg.max_distance_yards else None

    def _offset(self, me: ClientState, anchor: ClientState) -> tuple[float, float]:
        """Anchor minus me, in map units with x stretched to the same scale as y."""
        return (anchor.map_x - me.map_x) * self.cfg.map_aspect, anchor.map_y - me.map_y

    def _press_frames(self, slot: int, key: str, frames: int, me: ClientState, cancel: threading.Event) -> float:
        """Hold `key` for exactly `frames` game frames. A hold of n frame-times always spans exactly n frame
        boundaries, wherever it starts, so the game applies exactly n frames of movement. Returns the seconds held."""
        seconds = min(MAX_HOLD, frames / self._fps(me))
        self._hold(slot, key, seconds, cancel)
        return seconds

    # ------------------------------------------------------------------ turning
    def face(self, slot: int, target: float, cancel: threading.Event, me: ClientState | None = None,
             tolerance: float | None = None) -> ClientState:
        """Turn to `target` until within `tolerance` (default: the fine facing tolerance), or until the rest is
        less than half a frame of turning."""
        me = me or self._fresh(slot, cancel)
        for _ in range(10):
            diff = wrap(target - me.facing)
            if abs(diff) <= (tolerance if tolerance is not None else self.facing_tolerance(me)):
                return me
            frames = round(abs(diff) / self.turn_rate() * self._fps(me))
            if frames == 0:
                return me  # as close as whole frames get
            key = "turn_left" if diff > 0 else "turn_right"  # turning left raises the facing angle
            held = self._press_frames(slot, key, frames, me, cancel)
            after = self._fresh(slot, cancel)
            turned = abs(wrap(after.facing - me.facing))
            if turned > 0 and held >= MIN_LEARN_HOLD:
                measured = turned / held
                self._turn_rate = measured if self._turn_rate is None else \
                    self._turn_rate + (measured - self._turn_rate) * SPEED_SMOOTHING
            me = after
        return me

    # ------------------------------------------------------------------ the whole thing
    def stack(self, slot: int, anchor: int, cancel: threading.Event | None = None) -> StackResult:
        cancel = cancel or threading.Event()
        deadline = self.clock() + self.cfg.timeout
        moves = 0
        coarse = math.radians(COARSE_FACING_DEG)
        try:
            me, target = self._pose(slot), self._pose(anchor)
            if (me.map_id, me.map_level) != (target.map_id, target.map_level):
                raise StackError("in another zone than the anchor")
            if me.following:  # /follow would fight us: any movement key breaks it
                self._press_frames(slot, "back", 1, me, cancel)
            # 1. roughly face the anchor's way: moves are measured along the actual facing, so rough is enough
            me = self.face(slot, target.facing, cancel, tolerance=coarse)
            # 2. walk onto the spot, one axis at a time, in whole frames
            for _ in range(MAX_STEPS):  # `me` is always a reading taken after the last move
                if self.clock() > deadline:
                    raise StackError("timed out")
                target = self._pose(anchor)
                dx, dy = self._offset(me, target)
                if why := self._too_far(me, target):
                    raise StackError(f"too far away ({why}, max {self.cfg.max_distance_yards:g} yd): use follow")
                tol = self.distance_tolerance(slot, me)
                if tol is not None and math.hypot(dx, dy) <= tol:
                    break
                if abs(wrap(target.facing - me.facing)) > 2 * coarse:
                    me = self.face(slot, target.facing, cancel, me, tolerance=coarse)
                    continue
                fx, fy = forward_vector(me.facing)
                rx, ry = right_vector(me.facing)
                along, side = dx * fx + dy * fy, dx * rx + dy * ry
                moves_possible = []
                for comp, axis, pos_key, neg_key in ((along, (fx, fy), "forward", "back"),
                                                     (side, (rx, ry), "strafe_right", "strafe_left")):
                    key = pos_key if comp > 0 else neg_key
                    speed = self._speed(slot, me, key)
                    frames = round(PROBE_HOLD * self._fps(me)) if speed is None else \
                        round(abs(comp) / speed * self._fps(me))
                    if speed is not None and not self._trusted(slot, me, key):  # not sure yet: short steps
                        frames = min(frames, round(UNTRUSTED_HOLD * self._fps(me)))
                    moves_possible.append((frames, comp, axis, key))
                frames, comp, axis, key = max(moves_possible, key=lambda m: (m[0], abs(m[1])))
                if frames == 0:
                    break  # both axes are within half a frame of movement: as close as whole frames get
                held = self._press_frames(slot, key, frames, me, cancel)
                moves += 1
                after = self._fresh(slot, cancel)
                ex, ey = (after.map_x - me.map_x) * self.cfg.map_aspect, after.map_y - me.map_y
                moved = (ex * axis[0] + ey * axis[1]) * (1 if comp > 0 else -1)
                if moved > 0 and held >= MIN_LEARN_HOLD:  # one frame off on a short press is a big error
                    self._learn(slot, me, key, moved / held)
                me = after
            else:
                raise StackError("didn't get there in %d moves" % MAX_STEPS)
            # 3. fine facing last: turning on the spot doesn't move you
            me = self.face(slot, self._pose(anchor).facing, cancel, me)
            return self._result(slot, True, me, self._pose(anchor), moves, "stacked")
        except StackError as e:
            try:
                me, target = self._pose(slot), self._pose(anchor)
            except StackError:
                return StackResult(slot, False, moves=moves, message=str(e))
            return self._result(slot, False, me, target, moves, str(e))

    def _result(self, slot, ok, me, target, moves, message) -> StackResult:
        upy = self.units_per_yard(me, slot)
        dist = math.hypot(*self._offset(me, target)) / upy if upy else None
        err = math.degrees(abs(wrap(target.facing - me.facing)))
        return StackResult(slot, ok, dist, err, moves, message, fps=me.fps)


class StackManager:
    """Runs a Stacker for every follower at once, each on its own thread."""

    def __init__(self, cfg: StackConfig, stacker: Stacker, followers: Callable[[int], list[int]],
                 on_done: Callable[[list[StackResult]], None] = lambda results: None,
                 boost: Callable[[float], None] = lambda seconds: None,
                 moving_since: Callable[[], float] = lambda: -1e9) -> None:
        self.cfg = cfg
        self.stacker = stacker
        self.followers = followers
        self.on_done = on_done
        self.boost = boost
        self.moving_since = moving_since  # last time you pressed a movement key: you take over, we stop
        self._cancel: threading.Event | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._cancel is not None

    def cancel(self) -> None:
        with self._lock:
            if self._cancel:
                self._cancel.set()

    def stack_on(self, anchor: int) -> bool:
        """Stack everyone in `cfg.who` on the character in slot `anchor`. Returns False if already running."""
        with self._lock:
            if self._cancel is not None:
                return False
            cancel = self._cancel = threading.Event()
        slots = [s for s in self.followers(anchor) if s != anchor]
        if not slots:
            with self._lock:
                self._cancel = None
            log.info("stack: nobody to stack on slot %d", anchor)
            return True
        log.info("stack: slots %s onto slot %d", slots, anchor)
        self.boost(self.cfg.timeout + 1)
        started = time.monotonic()
        results: list[StackResult] = []

        def watch() -> None:  # you started moving: hand control back to you
            while not cancel.wait(0.05):
                if self.moving_since() > started:
                    cancel.set()

        def one(slot: int) -> None:
            results.append(self.stacker.stack(slot, anchor, cancel))

        threads = [threading.Thread(target=one, args=(s,), name=f"stack:{s}", daemon=True) for s in slots]

        def run() -> None:
            watcher = threading.Thread(target=watch, name="stack-watch", daemon=True)
            watcher.start()
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            with self._lock:
                self._cancel = None
            cancel.set()  # stops the watcher
            self.boost(0)
            self.on_done(sorted(results, key=lambda r: r.slot))

        threading.Thread(target=run, name="stack", daemon=True).start()
        return True
