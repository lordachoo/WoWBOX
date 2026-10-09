from conftest import HWNDS, Rig, make_cfg

from wowbox.keys import VK

ONE, TWO, F1, F2, W = VK["1"], VK["2"], VK["F1"], VK["F2"], VK["W"]
SHIFT, LSHIFT, LCTRL, ENTER, ESC = 0x10, 0xA0, 0xA2, 0x0D, 0x1B


def test_all_route_posts_to_other_clients_and_passes_through(rig):
    assert rig.tap(ONE) is False  # active client gets the physical key
    posted = rig.posted_to()
    assert set(posted) == {2, 3, 4}
    for slot in (2, 3, 4):
        assert posted[slot] == [(ONE, True), (ONE, False)]


def test_unlisted_key_stays_on_active(rig):
    assert rig.tap(W) is False
    assert rig.input.posted == []


def test_followers_excludes_active(rig):
    rig.windows.fg = HWNDS[2]
    assert rig.tap(F1) is True  # the window you're in doesn't get a followers-only key
    assert set(rig.posted_to()) == {1, 3, 4}


def test_explicit_slot_swallows_when_active_not_targeted(rig):
    rig.press(LCTRL, True)
    assert rig.tap(ONE) is True  # Ctrl+1 -> [3]; active is 1 so the physical key is swallowed
    rig.press(LCTRL, False)
    posted = rig.posted_to()
    assert set(posted) == {3}
    # modifiers wrapped around the key in "post" mode
    assert posted[3] == [(0x11, True), (ONE, True), (ONE, False), (0x11, False)]


def test_leader_route_from_follower(rig):
    rig.windows.fg = HWNDS[4]
    assert rig.tap(F2) is True
    assert set(rig.posted_to()) == {1}


def test_shift_chord_matches_exactly(rig):
    rig.press(LSHIFT, True)
    rig.tap(ONE)  # Shift+1 isn't routed -> active only
    assert rig.input.posted == []
    rig.tap(TWO)  # Shift+2 -> all
    rig.press(LSHIFT, False)
    assert rig.posted_to()[2] == [(SHIFT, True), (TWO, True), (TWO, False), (SHIFT, False)]


def test_strip_mode_drops_modifiers():
    cfg = make_cfg(broadcast={"modifier_mode": "strip"})
    r = Rig(cfg)
    r.press(LSHIFT, True)
    r.tap(TWO)
    assert r.posted_to()[2] == [(TWO, True), (TWO, False)]


def test_focus_swap_mode_injects_instead_of_posting():
    r = Rig(make_cfg(broadcast={"modifier_mode": "focus_swap", "focus_swap_dwell": 0}))
    r.press(LSHIFT, True)
    r.tap(TWO)
    r.press(LSHIFT, False)
    assert r.input.posted == []
    assert len(r.input.injected) == 3  # one SendInput per background target
    assert r.windows.fg == HWNDS[1]  # focus restored


def test_keyup_mirrors_targets_even_if_focus_moves(rig):
    rig.press(ONE, True)
    rig.windows.fg = None  # focus left WoW mid-press
    rig.press(ONE, False)
    assert all(p[-1] == (ONE, False) for p in rig.posted_to().values())


def test_autorepeat_reuses_targets(rig):
    rig.press(ONE, True)
    rig.press(ONE, True)
    rig.press(ONE, False)
    assert rig.posted_to()[2] == [(ONE, True), (ONE, True), (ONE, False)]


def test_no_broadcast_when_not_in_wow(rig):
    rig.windows.fg = 0x999  # some other app
    assert rig.tap(ONE) is False
    assert rig.input.posted == []


def test_toggle_hotkey_disables_broadcast_and_is_swallowed(rig):
    scroll = VK["SCROLLLOCK"]
    assert rig.press(scroll, True) is True
    assert rig.press(scroll, False) is True
    assert rig.router.enabled is False
    assert rig.hotkeys == ["toggle_broadcast"]
    rig.input.posted.clear()  # turning broadcast off releases modifiers everywhere
    rig.tap(ONE)
    assert rig.input.posted == []


def test_injected_events_ignored(rig):
    assert rig.input.press(ONE, True, injected=True) is False
    rig.drain()
    assert rig.input.posted == []


def test_chatguard_pauses_broadcast_until_enter_or_escape(rig):
    rig.tap(ENTER)
    rig.tap(ONE)
    assert rig.input.posted == []
    rig.tap(ENTER)  # message sent, chat closed
    rig.tap(ONE)
    assert set(rig.posted_to()) == {2, 3, 4}
    rig.input.posted.clear()
    rig.tap(VK["/"])
    rig.tap(ONE)
    assert rig.input.posted == []
    rig.tap(ESC)
    rig.tap(ONE)
    assert set(rig.posted_to()) == {2, 3, 4}


def test_unchecked_client_excluded_from_all(rig):
    rig.team.clients[3].broadcast = False
    rig.tap(ONE)
    assert set(rig.posted_to()) == {2, 4}


def test_release_all_sends_ups(rig):
    rig.press(ONE, True)
    rig.input.posted.clear()
    rig.router.release_all()
    rig.drain()
    posted = rig.posted_to()
    assert (ONE, False) in posted[2]
    for slot in HWNDS:
        assert (SHIFT, False) in posted[slot]


def test_send_chord_skips_typing_clients(rig):
    from wowbox.bridge.decoder import ClientState
    from wowbox.keys import parse_chord
    rig.team.clients[2].state = ClientState(typing=True)
    sent = rig.router.send_chord(parse_chord("F5"), "all")
    assert sent == [1, 3, 4]


def test_every_hotkey_name_in_config_is_registered():
    """Each HotkeyConfig field must reach the router (stack was missed once)."""
    from wowbox.config import HotkeyConfig
    r = Rig()
    names = set(r.router._hotkeys.values())
    for field in HotkeyConfig.model_fields:
        if field != "focus_slot":
            assert field in names, field
