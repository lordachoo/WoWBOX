from conftest import Clock

from wowbox.keys import parse_chord
from wowbox.router import ChatGuard

ENTER, ESC, SLASH, ONE = (parse_chord(k) for k in ("Enter", "Esc", "/", "1"))


def guard(clock):
    return ChatGuard(True, {ENTER, SLASH}, {ENTER, ESC}, clock=clock)


def test_key_heuristic():
    g = guard(Clock())
    assert not g.paused(1)
    g.on_key(ENTER)
    assert g.paused(1)
    g.on_key(ONE)
    assert g.paused(1)
    g.on_key(ESC)
    assert not g.paused(1)


def test_bridge_typing_pauses_even_without_keys():
    c = Clock()
    g = guard(c)
    g.set_bridge(1, True)  # e.g. clicked into the chat box
    assert g.paused(1)
    assert not g.paused(2)


def test_bridge_corrects_drift_but_not_immediately():
    c = Clock()
    g = guard(c)
    g.on_key(ENTER)  # opened at t=1000
    c.t += 0.1
    g.set_bridge(1, False)  # report from before the chat box appeared: must not close the guard
    assert g.paused(1)
    c.t += 0.5
    g.set_bridge(1, False)  # settled report: chat really is closed (e.g. Enter on empty line)
    assert not g.paused(1)


def test_stale_bridge_ignored():
    c = Clock()
    g = guard(c)
    g.set_bridge(1, True)
    c.t += 5
    assert not g.paused(1)


def test_disabled():
    g = ChatGuard(False, {ENTER}, {ENTER}, clock=Clock())
    g.on_key(ENTER)
    g.set_bridge(1, True)
    assert not g.paused(1)
