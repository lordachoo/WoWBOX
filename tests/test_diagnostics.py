"""Diagnostics: the watchdog, the health line, unclean-exit detection, and the bridge's dropout lines."""
import json
import logging
import os
import threading

import numpy as np

from wowbox import diagnostics
from wowbox.bridge.decoder import ClientState, encode
from wowbox.bridge.poller import BridgePoller
from wowbox.config import BridgeConfig
from wowbox.diagnostics import Diagnostics
from wowbox.platform.fake import FakeCapture

COUNTERS = {"working_set": 120_000_000, "private": 100_000_000, "handles": 500, "gdi": 100, "user": 80}


def make(tmp_path=None, counters=None):
    t = [0.0]
    d = Diagnostics(tmp_path, clock=lambda: t[0], counters=lambda: dict(counters or COUNTERS))
    return d, t


def test_watchdog_logs_stacks_when_a_loop_stops_and_again_when_it_moves(caplog):
    d, t = make()
    beat = d.heartbeat("bridge reader", 5.0, thread="MainThread")
    caplog.set_level(logging.INFO)
    t[0] = 4.0
    d.check(t[0])
    assert "stuck" not in caplog.text
    t[0] = 6.0
    d.check(t[0])
    assert "bridge reader stuck for 6.0s" in caplog.text and "--- thread MainThread" in caplog.text
    assert "test_watchdog_logs_stacks" in caplog.text  # the stack shows where it is
    caplog.clear()
    t[0] = 7.0
    d.check(t[0])
    assert caplog.text == ""  # reported once, not every check
    t[0] = 36.5
    d.check(t[0])
    assert "still stuck" in caplog.text
    beat()
    d.check(t[0])
    assert "bridge reader moving again after 36.5s" in caplog.text
    assert "stalls: bridge reader 1x (worst 36.5s)" in d.health(t[0])
    assert "stalls: none" in d.health(t[0])  # counted since the last line


def test_watch_ignores_a_loop_that_isnt_running(caplog):
    d, t = make()
    d.heartbeat("deferred sends", 5.0, active=lambda: False)
    t[0] = 100.0
    d.check(t[0])
    assert "stuck" not in caplog.text


def test_health_line_has_counters_reports_and_log_counts(caplog):
    d, t = make()
    d.install()
    try:
        d.report("bridge", lambda: "300 reads")
        d.report("broken", lambda: 1 / 0)
        logging.getLogger("x").warning("careful")
        caplog.set_level(logging.INFO)
        t[0] = 300.0
        line = d.health(t[0])
    finally:
        logging.getLogger().removeHandler(d._log_counter)
    assert "up 5m00s" in line and "memory 120 MB (private 100 MB), handles 500, GDI 100, USER 80" in line
    assert "bridge: 300 reads" in line and "broken: ? (ZeroDivisionError" in line
    assert "log: 0 error(s), 1 warning(s)" in line and line in caplog.text


def test_health_warns_once_about_leaks(caplog):
    d, t = make(counters={**COUNTERS, "gdi": 7000})
    d.health(1.0)
    d.health(2.0)
    assert caplog.text.count("7000 GDI objects") == 1


def test_health_warns_when_memory_keeps_growing(caplog):
    c = dict(COUNTERS)
    d = Diagnostics(None, clock=lambda: 0.0, counters=lambda: dict(c))
    d.health(1.0)  # the baseline
    c["private"] += 900_000_000
    d.health(2.0)
    assert "grew" not in caplog.text
    c["private"] += 200_000_000
    d.health(3.0)
    assert "memory grew from 100 MB to 1200 MB" in caplog.text


def test_periodic_health_from_check():
    d, t = make()
    lines = []
    d.report("x", lambda: lines.append(1) or "ok")
    d.check(299.0)
    d.check(300.0)
    assert lines == [1]


def test_unclean_exit_is_found_at_the_next_start(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(diagnostics, "_process_alive", lambda pid: False)
    (tmp_path / "wowbox-4242.running").write_text(json.dumps({"pid": 4242, "started": "2026-10-11 20:39:21"}))
    (tmp_path / "crash.log").write_text("\n=== WoWBox pid 4242 started 2026-10-11 20:39:21\n"
                                        "Fatal Python error: Segmentation fault\n  File \"x.py\", line 1\n"
                                        "\n=== WoWBox pid 5000 started 2026-10-12 08:00:00\n")
    d, t = make(tmp_path)
    try:
        d.install()
        assert "pid 4242, started 2026-10-11 20:39:21) didn't shut down cleanly" in caplog.text
        assert "Segmentation fault" in caplog.text and "pid 5000" not in caplog.text.split("crash.log has")[1]
        assert d.unclean_exit and not (tmp_path / "wowbox-4242.running").exists()
        assert (tmp_path / f"wowbox-{os.getpid()}.running").exists()
    finally:
        d.clean_exit()
        logging.getLogger().removeHandler(d._log_counter)
    assert not list(tmp_path.glob("*.running"))  # a clean exit leaves nothing for the next start
    assert f"pid {os.getpid()} started" in (tmp_path / "crash.log").read_text()


def test_a_still_running_wowbox_is_not_a_crash(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(diagnostics, "_process_alive", lambda pid: True)
    (tmp_path / "wowbox-4242.running").write_text(json.dumps({"pid": 4242}))
    d, t = make(tmp_path)
    try:
        d.install()
    finally:
        d.clean_exit()
        logging.getLogger().removeHandler(d._log_counter)
    assert "already running (pid 4242)" in caplog.text and d.unclean_exit is None


def test_uncaught_thread_error_is_logged(caplog):
    d, t = make()
    old = threading.excepthook
    d.install()
    try:
        th = threading.Thread(target=lambda: 1 / 0, name="boom")
        th.start()
        th.join()
    finally:
        threading.excepthook = old
        logging.getLogger().removeHandler(d._log_counter)
    assert "uncaught error in thread boom" in caplog.text and "ZeroDivisionError" in caplog.text


# ---------------------------------------------------------------------- bridge dropouts
def _rig():
    from test_layout_launcher import HWNDS, Rig
    r = Rig()
    cap = FakeCapture()
    for i, h in enumerate(HWNDS.values()):
        cap.images[h] = _strip(1)
    return r, cap, HWNDS


def _strip(frame):
    img = encode({"health": 255, "level": 70}, frame=frame, block=6)
    full = np.zeros((100, 400, 3), dtype="uint8")
    full[:6, :img.shape[1]] = img
    return full


def test_bridge_logs_why_a_client_went_unreadable_and_when_it_came_back(caplog):
    caplog.set_level(logging.INFO)
    r, cap, hwnds = _rig()
    t = [0.0]
    poller = BridgePoller(BridgeConfig(stale_after=1.0), r.team, r.windows, lambda: cap, clock=lambda: t[0])
    poller.poll_once(cap)
    t[0] = 2.0
    poller.poll_once(cap)  # the same frame for 2 s
    assert "WoW1 C1 unreadable: the strip decodes but its frame counter is stuck at 1 for 2.0s" in caplog.text
    assert "Capture mss took" in caplog.text and "2.00s since the previous read" in caplog.text
    caplog.clear()
    t[0] = 3.0
    poller.poll_once(cap)
    assert "unreadable" not in caplog.text  # said once
    cap.images[hwnds[1]] = cap.images[hwnds[2]] = _strip(2)
    t[0] = 4.5
    poller.poll_once(cap)
    assert "WoW1 C1 readable again after 2.5s" in caplog.text
    cap.images[hwnds[1]] = _strip(3)
    cap.images[hwnds[2]] = np.zeros((100, 400, 3), dtype="uint8")  # addon gone: no header
    t[0] = 5.6
    poller.poll_once(cap)
    assert "WoW1 C1 unreadable" not in caplog.text.split("readable again")[1]
    assert "WoW2 C2 unreadable: DecodeError" in caplog.text and "last good frame" in caplog.text
    line = poller.health()
    assert "5 reads, worst gap 2.00s" in line and "dropout(s)" in line and "mss" in line
    assert "0 reads" in poller.health()  # reset after each report


def test_engine_logs_one_line_per_pause_not_one_per_rule():
    from test_automation import DRINK, engine
    rules = [{"name": f"r{i}", "every": "10s", "do": {"send": "F1", "to": "leader"}} for i in range(5)]
    e, sender, src = engine([DRINK, *rules])
    events = []
    e.on_event = events.append
    src.st[3] = ClientState(power_pct=10)
    src.st[1] = ClientState(stale=True)
    e.tick(0)
    paused = [m for m in events if "paused" in m]
    assert paused == ["5 rules (r0, r1, r2, ...) paused: can't read the bridge of slot(s) [1]"]
    src.st[1] = ClientState()
    e.tick(1)
    assert [m for m in events if "resumed" in m] == ["5 rules (r0, r1, r2, ...) resumed"]
