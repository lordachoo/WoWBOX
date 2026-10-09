"""The stacking controller against a small simulation of WoW movement and the bridge's delay."""
import bisect
import math
import threading

import pytest

from wowbox.bridge.decoder import ClientState
from wowbox.config import StackConfig
from wowbox.keys import VK
from wowbox.stack import Stacker, StackManager, forward_vector, right_vector, wrap

ZONE_W, ZONE_H = 1500.0, 1000.0  # yards; a 3:2 zone map
SPEEDS = {"W": 7.0, "S": 4.5, "Q": 7.0, "E": 7.0}  # yards / s
TURN = math.pi  # rad / s


class World:
    """Characters in yards (east, south). Keys register on the next game frame; the bridge draws at 15 Hz and is
    read `latency` seconds late, like a real client."""

    def __init__(self, fps=60.0, latency=0.05):
        self.t = 0.0
        self.fps = fps
        self.latency = latency
        self.chars: dict[int, dict] = {}
        self.pending: list[tuple[float, int, int, bool]] = []
        self.snaps: list[tuple[float, int, dict[int, ClientState]]] = []
        self.snap_times: list[float] = []
        self.map_id = {}
        self._next_frame = 0.0
        self._next_draw = 0.0
        self.posted = []

    def add(self, slot, east, south, facing, map_id=1):
        self.chars[slot] = {"e": east, "s": south, "f": facing, "held": set()}
        self.map_id[slot] = map_id
        self._draw()

    # what the Stacker sees
    def post(self, slot, vk, down):
        self.posted.append((slot, vk, down))
        self.pending.append((self.t, slot, vk, down))

    def read(self, slot):
        i = bisect.bisect_right(self.snap_times, self.t - self.latency)
        return self.snaps[i - 1 if i else -1][2].get(slot) if self.snaps else None  # nothing old enough: newest

    def clock(self):
        return self.t

    def sleep(self, dt):
        end = self.t + dt
        step = 1 / 240
        while self.t < end:
            self.t = min(end, self.t + step)
            if self.t >= self._next_frame:  # a game frame: apply keys that arrived since the last one
                self._next_frame += 1 / self.fps
                for ev in [p for p in self.pending if p[0] <= self.t]:
                    _, slot, vk, down = ev
                    (self.chars[slot]["held"].add if down else self.chars[slot]["held"].discard)(vk)
                    self.pending.remove(ev)
            for c in self.chars.values():
                self._move(c, step)
            if self.t >= self._next_draw:
                self._next_draw += 1 / 15
                self._draw()

    def _move(self, c, dt):
        held = {k for k in "WSQEAD" if VK[k] in c["held"]}
        if "A" in held:
            c["f"] = (c["f"] + TURN * dt) % (2 * math.pi)
        if "D" in held:
            c["f"] = (c["f"] - TURN * dt) % (2 * math.pi)
        for key, vec in (("W", forward_vector(c["f"])), ("E", right_vector(c["f"]))):
            back = {"W": "S", "E": "Q"}[key]
            sign = (key in held) - (back in held)
            if sign:
                v = SPEEDS[key if sign > 0 else back]
                c["e"] += vec[0] * v * sign * dt
                c["s"] += vec[1] * v * sign * dt

    def _draw(self):
        frame = len(self.snaps) % 256
        self.snap_times.append(self.t)
        self.snaps.append((self.t, frame, {
            slot: ClientState(frame=frame, position_ok=True, map_x=c["e"] / ZONE_W, map_y=c["s"] / ZONE_H,
                              facing=c["f"] % (2 * math.pi), map_id=self.map_id[slot], fps=round(self.fps),
                              updated_at=self.t)
            for slot, c in self.chars.items()}))

    def distance(self, a, b):
        ca, cb = self.chars[a], self.chars[b]
        return math.hypot(ca["e"] - cb["e"], ca["s"] - cb["s"])

    def facing_error_deg(self, a, b):
        return math.degrees(abs(wrap(self.chars[a]["f"] - self.chars[b]["f"])))


def stacker(world, **cfg):
    return Stacker(StackConfig(**cfg), world.read, world.post, clock=world.clock, sleep=world.sleep)


@pytest.mark.parametrize("east,south,facing", [(3, 2, 1.0), (-6, 4, 5.5), (0.8, -12, 3.1), (15, 15, 0.2)])
def test_stacks_onto_anchor(east, south, facing):
    w = World()
    w.add(1, 500, 500, 2.0)
    w.add(2, 500 + east, 500 + south, facing)
    r = stacker(w).stack(2, 1)
    assert r.ok, r.message
    assert w.distance(1, 2) < 0.15 and w.facing_error_deg(1, 2) < 2.0  # 60 fps: half a frame of turning is 1.5 deg
    assert r.distance_yards == pytest.approx(w.distance(1, 2), abs=0.1)
    assert not any(w.chars[2]["held"]) and not w.pending  # every key let go


def test_second_stack_is_quicker_with_learned_speeds():
    w = World()
    w.add(1, 500, 500, 0.5)
    w.add(2, 504, 497, 2.5)
    s = stacker(w)
    first = s.stack(2, 1)
    w.chars[2].update(e=496, s=505, f=4.0)
    t0 = w.t
    second = s.stack(2, 1)
    assert first.ok and second.ok
    assert second.moves <= first.moves and w.t - t0 < 6
    assert w.distance(1, 2) < 0.5


def test_facing_wraps_the_short_way():
    w = World()
    w.add(1, 500, 500, 0.05)
    w.add(2, 500, 500.3, 6.2)  # 0.13 rad apart across north
    s = stacker(w)
    s.face(2, 0.05, threading.Event())
    assert w.facing_error_deg(1, 2) < 3.0
    first = next(vk for _, vk, down in w.posted if down)
    assert first == VK["A"]  # a little left, not almost a full circle right
    assert sum(down for *_, down in w.posted) <= 3


def test_lower_background_fps_still_converges():
    w = World(fps=30, latency=0.08)
    w.add(1, 500, 500, 1.0)
    w.add(2, 507, 503, 4.0)
    r = stacker(w).stack(2, 1)
    assert r.ok, r.message
    assert w.distance(1, 2) < 0.25 and w.facing_error_deg(1, 2) < 4.0  # 30 fps: 3 deg a frame


def test_different_map_and_no_position():
    w = World()
    w.add(1, 500, 500, 0)
    w.add(2, 503, 500, 0, map_id=2)
    assert "another zone" in stacker(w).stack(2, 1).message
    w2 = World()
    w2.add(1, 500, 500, 0)
    w2.add(2, 503, 500, 0)
    s = stacker(w2)
    real_read = w2.read
    w2.read = lambda slot: (st := real_read(slot)) and ClientState(**{**st.__dict__, "position_ok": slot != 2})
    s.read = w2.read
    r = s.stack(2, 1)
    assert not r.ok and "no map position" in r.message


def test_cancel_lets_go_of_keys():
    w = World()
    w.add(1, 500, 500, 0)
    w.add(2, 540, 500, 0)
    cancel = threading.Event()
    s = stacker(w)
    real_sleep = w.sleep

    def sleep(dt):
        real_sleep(dt)
        if w.t > 1.0:
            cancel.set()

    s.sleep = sleep
    r = s.stack(2, 1, cancel)
    assert not r.ok and r.message == "cancelled"
    w.sleep(0.1)
    assert not w.chars[2]["held"]


def test_manager_runs_followers_and_reports():
    w = World()
    w.add(1, 500, 500, 1.0)
    w.add(2, 502, 501, 2.0)
    w.add(3, 497, 503, 5.0)
    lock = threading.Lock()

    def read(slot):  # threads share the simulated world: let one advance it at a time
        with lock:
            return w.read(slot)

    def sleep(dt):
        with lock:
            w.sleep(dt)

    s = Stacker(StackConfig(), read, w.post, clock=w.clock, sleep=sleep)
    done = threading.Event()
    results = []
    m = StackManager(StackConfig(), s, followers=lambda anchor: [1, 2, 3],
                     on_done=lambda rs: (results.extend(rs), done.set()))
    assert m.stack_on(1)
    assert not m.stack_on(1)  # already running
    assert done.wait(30)
    assert [r.slot for r in results] == [2, 3] and all(r.ok for r in results), results
    assert w.distance(1, 2) < 0.5 and w.distance(1, 3) < 0.5
    assert not m.running


def test_high_fps_gets_sub_degree():
    w = World(fps=120, latency=0.08)
    w.add(1, 500, 500, 4.0)
    w.add(2, 505, 492, 0.7)
    r = stacker(w).stack(2, 1)
    assert r.ok and r.fps == 120
    assert w.distance(1, 2) < 0.1 and w.facing_error_deg(1, 2) <= 1.0


def test_restack_when_close_but_not_within_tolerance():
    """Reported: stacking again did 0 moves while 0.38 yd off. Off by more than half a frame -> it moves."""
    w = World()
    w.add(1, 500, 500, 1.0)
    w.add(2, 506, 503, 3.0)
    s = stacker(w)
    s.stack(2, 1)
    w.chars[2]["e"] += 0.38
    w.sleep(0.3)
    r = s.stack(2, 1)
    assert r.ok and r.moves >= 1 and w.distance(1, 2) < 0.15


def test_apart():
    from wowbox.stack import apart
    lead = ClientState(position_ok=True, map_x=0.5, map_y=0.5, map_id=10)
    near = ClientState(position_ok=True, map_x=0.51, map_y=0.5, map_id=10)
    upy = 0.01 / 7.5  # map units per yard: 0.01 x-units * 1.5 aspect = 0.015 units = ~11 yd here
    assert apart(near, lead, 40, upy) is None
    far = ClientState(position_ok=True, map_x=0.6, map_y=0.5, map_id=10)
    assert apart(far, lead, 40, upy) == "112 yd away"
    assert apart(far, lead, 40, None) is None  # zone scale not measured yet: can't tell
    other = ClientState(position_ok=True, map_x=0.5, map_y=0.5, map_id=11)
    assert apart(other, lead, 40, upy) == "in another zone"
    no_coords = ClientState(position_ok=False, map_id=11)
    assert apart(no_coords, lead, 40, upy) == "in another zone"  # map id still tells, without coordinates
    assert apart(ClientState(map_id=0), lead, 40, upy) is None  # unknown map: can't tell
    assert apart(ClientState(stale=True, map_id=11), lead, 40, upy) is None
    assert apart(near, None, 40, upy) is None


def test_stack_refuses_followers_too_far_away():
    w = World()
    w.add(1, 500, 500, 0)
    w.add(2, 503, 500, 0)
    s = stacker(w, max_distance_yards=40)
    assert s.stack(2, 1).ok  # learns this zone's scale
    w.chars[2]["e"] = 600  # 100 yd away
    w.sleep(0.3)
    t0 = w.t
    r = s.stack(2, 1)
    assert not r.ok and "too far away" in r.message and w.t - t0 < 1.0
