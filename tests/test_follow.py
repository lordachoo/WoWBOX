import numpy as np

from conftest import HWNDS, Rig, make_cfg
from wowbox.bridge import BridgePoller
from wowbox.bridge.decoder import ClientState, encode, encode_chat_bytes
from wowbox.config import AppConfig, BridgeConfig
from wowbox.follow import FollowManager
from wowbox.keys import VK
from wowbox.platform.fake import FakeCapture

NAMES = {1: "Tinkerdawn", 2: "Gnope", 3: "Kunth", 4: "Alt"}


class FakeRouter:
    def __init__(self):
        self.typed, self.sent = [], []
        self.mirror, self.mirror_vks, self.mirror_target = False, set(), "followers"
        self.last_movement = -1e9
        self.held = False
        self.active = 1

    def type_text(self, text, target):
        self.typed.append((text, list(target) if isinstance(target, list) else target))
        return target

    def send_chord(self, chord, target, check_typing=True):
        self.sent.append((str(chord), list(target)))
        return target

    def active_slot(self):
        return self.active

    def movement_held(self):
        return self.held

    def set_mirror(self, value):
        self.mirror = value


def follow_rig(**follow):
    r = Rig(make_cfg(follow=follow))
    for slot, name in NAMES.items():
        c = r.team.clients[slot]
        c.character = name
        c.state = ClientState(name=name, following=True)
    router = FakeRouter()
    return r.team, router, FollowManager(r.cfg.follow, r.team, router)


def test_follow_types_target_and_follow_on_everyone_but_the_leader():
    team, router, fm = follow_rig()
    fm.follow()
    assert fm.active
    assert router.typed == [("/target Tinkerdawn", [2, 3, 4]), ("/follow Tinkerdawn", [2, 3, 4])]


def test_follow_with_an_in_game_macro_key():
    team, router, fm = follow_rig(start_key="F6")
    fm.follow()
    assert router.typed == [] and router.sent == [("F6", [2, 3, 4])]


def test_stay_taps_stop_key_and_ends_follow_mode():
    team, router, fm = follow_rig()
    fm.follow()
    fm.stay()
    assert not fm.active and router.sent == [("S", [2, 3, 4])]
    team.clients[2].state = ClientState(following=False)
    fm.tick(0)
    fm.tick(5)
    assert len(router.typed) == 2  # only the two lines from follow()


def test_refollow_after_delay_with_backoff():
    team, router, fm = follow_rig()
    fm.follow()
    router.typed.clear()
    team.clients[2].state = ClientState(following=False)
    fm.tick(10.0)
    fm.tick(10.5)
    assert router.typed == []  # not off long enough
    fm.tick(11.0)
    assert router.typed == [("/follow Tinkerdawn", [2])]
    fm.tick(12.0)
    assert len(router.typed) == 1  # retry wait 2 s
    fm.tick(13.1)
    assert len(router.typed) == 2  # then 4 s
    fm.tick(16.0)
    assert len(router.typed) == 2
    fm.tick(17.2)
    assert len(router.typed) == 3
    team.clients[2].state = ClientState(following=True)  # back on follow: backoff resets
    fm.tick(18)
    team.clients[2].state = ClientState(following=False)
    fm.tick(30)
    fm.tick(31)
    assert len(router.typed) == 4


def test_no_refollow_while_fighting_casting_moving_or_in_that_window():
    team, router, fm = follow_rig()
    fm.follow()
    router.typed.clear()
    for st in (ClientState(combat=True), ClientState(casting=True), ClientState(typing=True),
               ClientState(dead=True), ClientState(stale=True)):
        team.clients[2].state = st
        fm.tick(0)
        fm.tick(5)
    assert router.typed == []
    team.clients[2].state = ClientState()
    router.held = True
    fm.tick(10)
    fm.tick(20)
    router.held = False
    router.last_movement = 20
    fm.tick(20.5)
    assert router.typed == []
    router.active = 2  # you're in Gnope's window, steering it
    fm.tick(30)
    fm.tick(40)
    assert router.typed == []
    router.active = 1
    fm.tick(41)
    fm.tick(42)
    assert router.typed == [("/follow Tinkerdawn", [2])]


def test_refollow_in_combat_option():
    team, router, fm = follow_rig(refollow_in_combat=True)
    fm.follow()
    router.typed.clear()
    team.clients[3].state = ClientState(combat=True)
    fm.tick(0)
    fm.tick(1)
    assert router.typed == [("/follow Tinkerdawn", [3])]


def test_unknown_leader_name_does_nothing():
    team, router, fm = follow_rig()
    team.clients[1].character = ""
    fm.follow()
    assert not fm.active and router.typed == []


def test_not_target():
    r = Rig()
    assert r.team.resolve("not:leader", 1) == [2, 3, 4]
    r.team.clients[3].character = "Kunth"
    assert r.team.resolve("not:Kunth", 1) == [1, 2, 4]
    assert AppConfig.model_validate({"follow": {"who": "not:leader"}}).follow.who == "not:leader"


def test_mirror_copies_movement_keys_to_followers():
    r = Rig()
    r.router.mirror_vks = {VK["W"]}
    r.press(VK["W"])
    assert set(r.posted_to()) == set()  # W normally stays in the window you're in
    r.press(VK["W"], False)
    r.router.set_mirror(True)
    assert not r.press(VK["W"])  # not swallowed: the leader moves too
    assert r.router.movement_held()
    r.press(VK["W"], False)
    assert not r.router.movement_held()
    posted = r.posted_to()
    assert set(posted) == {2, 3, 4} and posted[2] == [(VK["W"], True), (VK["W"], False)]


def test_chat_frame_clears_stale_typing_flag():
    """`/bb test` typed in Kunth's window: the last state frame says typing, the chat frame says not."""
    r = Rig()
    cap = FakeCapture()
    for h in HWNDS.values():
        full = np.zeros((100, 400, 3), dtype=np.uint8)
        strip = encode({"health": 255, "input_flags": 3}, 1, 6)
        full[:6, :strip.shape[1]] = strip
        cap.images[h] = full
    seen = []
    poller = BridgePoller(BridgeConfig(), r.team, r.windows, lambda: cap, clock=lambda: 0.0,
                          on_state=lambda s, st: seen.append((s, st.typing)))
    poller.poll_once(cap)
    assert r.team.clients[3].state.typing
    strip = encode({}, 2, 6, data=encode_chat_bytes("whisper", "Tinkerdawn", "heal", 1, 2, input_flags=0))
    cap.images[HWNDS[3]][:6, :strip.shape[1]] = strip
    poller.poll_once(cap)
    assert not r.team.clients[3].state.typing and (3, False) in seen


def test_command_slots():
    r = Rig()
    r.team.clients[2].character = "Gnope"
    r.team.clients[3].broadcast = False
    assert r.team.command_slots("all", 2) == [1, 2, 3, 4]  # broadcast checkbox doesn't matter
    assert r.team.command_slots("others", 2) == [1, 3, 4]
    assert r.team.command_slots("gnope", 1) == [2]
    assert r.team.command_slots("3", 1) == [3]
    assert r.team.command_slots("leader", 2) == [1]
    assert r.team.command_slots("Nobody", 1) == []


def test_mirroring_stacks_when_you_stop_instead_of_refollowing():
    team, router, fm = follow_rig()
    stacked = []
    fm.stack_on_stop, fm.stack_delay = stacked.append, 0.4
    fm.follow()
    router.typed.clear()
    router.mirror = True
    team.clients[2].state = ClientState(following=False)
    router.held, router.last_movement = True, 10.0
    fm.tick(10.2)
    assert stacked == []  # still moving
    router.held = False
    fm.tick(10.3)
    assert stacked == []  # not stopped long enough
    fm.tick(10.5)
    assert stacked == [1]  # onto the window you're in
    fm.tick(12)
    assert stacked == [1] and router.typed == []  # once per stop, and no /follow while mirroring
    router.last_movement = 15.0
    fm.tick(16)
    assert stacked == [1, 1]
