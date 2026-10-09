from wowbox.bridge.decoder import ClientState
from wowbox.commands import ORIGIN_DELAY, ORIGIN_TIMEOUT, TeamCommands


class Rig:
    def __init__(self):
        self.t = 0.0
        self.typed = []
        self.states = {1: ClientState(), 2: ClientState(), 3: ClientState()}
        self.on_sleep = lambda: None
        self.cmd = TeamCommands(self, self.states.get, clock=lambda: self.t, sleep=self.sleep, spawn=lambda fn: fn())

    def type_text(self, text, target):
        self.typed.append((round(self.t, 2), text, target))
        return target

    def sleep(self, dt):
        self.t += dt
        self.on_sleep()


def test_others_at_once_origin_after_a_gap():
    r = Rig()
    r.cmd.send("/reload", [1, 2, 3], origin=2)
    assert r.typed[0] == (0.0, "/reload", [1, 3])
    assert r.typed[1][1:] == ("/reload", [2]) and r.typed[1][0] >= ORIGIN_DELAY


def test_origin_waits_for_its_chat_box_to_close():
    r = Rig()
    r.states[2] = ClientState(typing=True, chat_open=True)

    def close_at_1s():
        if r.t >= 1.0:
            r.states[2] = ClientState()

    r.on_sleep = close_at_1s
    r.cmd.send("/say hi", [1, 2], origin=2)
    assert r.typed[0] == (0.0, "/say hi", [1])
    t, text, target = r.typed[1]
    assert target == [2] and t >= 1.0 + ORIGIN_DELAY


def test_origin_gives_up_if_chat_stays_open():
    r = Rig()
    r.states[2] = ClientState(typing=True)
    r.cmd.send("/reload", [2], origin=2)
    assert r.typed == [] and r.t > ORIGIN_TIMEOUT


def test_only_others():
    r = Rig()
    r.cmd.send("/sit", [1, 3], origin=2)
    assert r.typed == [(0.0, "/sit", [1, 3])]
