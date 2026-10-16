from conftest import HWNDS, Rig

from wowbox.bridge import BridgePoller, encode
from wowbox.config import AppConfig, BridgeConfig
from wowbox.launcher import Launcher, set_cvars
from wowbox.layout import LayoutManager
from wowbox.layout.grid import grid_rects, plan_grid, split_main_strip, thumb_cells
from wowbox.platform.base import Rect
from wowbox.platform.fake import FakeCapture, FakeWindows
from wowbox.team import Team

WORK = Rect(0, 0, 1920, 1040)


def test_grid_tiles_exactly():
    cells = grid_rects(Rect(0, 0, 1921, 1041), 2, 2)
    assert cells[0] == Rect(0, 0, 960, 520)
    assert cells[3].right == 1921 and cells[3].bottom == 1041
    assert sum(c.w * c.h for c in cells) == 1921 * 1041


def test_plan_grid_extra_slots_share_last_cell():
    plan = plan_grid([1, 2, 3, 4], WORK, 3, 1)
    assert plan[3] == plan[4]


def test_main_strip_split():
    main, strip = split_main_strip(WORK, 0.25)
    assert main.h + strip.h == WORK.h and strip.y == main.bottom
    main2, strip2 = split_main_strip(WORK, 0.25, Rect(1920, 0, 1920, 1040))
    assert main2 == WORK and strip2.x == 1920
    assert len(thumb_cells(strip, 3)) == 3


def test_wide_monitor_puts_a_16_9_main_left_and_thumbs_right():
    wide = Rect(0, 0, 3440, 1400)  # 21:9
    for side in ("auto", "right"):
        main, strip = split_main_strip(wide, 0.22, side=side)
        assert main == Rect(0, 0, 2488, 1400) and strip == Rect(2488, 0, 952, 1400)
    main, strip = split_main_strip(wide, 0.22, side="bottom")
    assert main.w == 3440 and strip.y == main.bottom
    assert split_main_strip(WORK, 0.22, side="auto")[0].w == 1920  # a 16:9 monitor keeps the strip at the bottom
    cells = thumb_cells(strip, 3)  # a wide strip: one row
    assert len({c.y for c, _ in cells}) == 1
    cells = thumb_cells(split_main_strip(wide, 0.22, side="right")[1], 3)  # a tall column: one per row
    assert len({c.x for c, _ in cells}) == 1 and len(cells) == 3
    super_wide = Rect(0, 0, 5120, 1400)  # 32:9: a wide column, 2 across fits better for 4
    main, strip = split_main_strip(super_wide, 0.22, side="auto")
    assert main.w == 2488 and len({c.x for c, _ in thumb_cells(strip, 4)}) == 2


class FakeThumbs:
    def __init__(self):
        self.shown = None

    def show_at(self, rect, entries, mover):
        self.shown = (rect, entries)

    def clear(self):
        self.shown = None


def test_layout_manager_grid_and_main_thumbs():
    r = Rig()
    thumbs = FakeThumbs()
    lm = LayoutManager(r.cfg.layout, r.team, r.windows, thumbs=thumbs)
    lm.apply("grid_2x2")
    rects = {s: r.windows.windows[h].rect for s, h in HWNDS.items()}
    assert len(set(rects.values())) == 4
    assert all(w.borderless for w in r.windows.windows.values())

    lm.apply("main_thumbs")
    assert len({w.rect for w in r.windows.windows.values()}) == 1  # all stacked in the main area
    assert [s for s, _ in thumbs.shown[1]] == [2, 3, 4]
    lm.set_main(3)
    assert r.windows.fg == HWNDS[3]
    assert [s for s, _ in thumbs.shown[1]] == [1, 2, 4]
    lm.next_main()
    assert r.team.main_slot == 4
    assert lm.cycle() == "tabs"


def test_set_cvars(tmp_path):
    cfg = tmp_path / "WTF" / "Config.wtf"
    cfg.parent.mkdir()
    cfg.write_text('SET realmList "x"\nSET gxWindow "0"\nSET gxResolution "1920x1080"\n')
    assert set_cvars(cfg, {"gxWindow": "1", "gxMaximize": "0"})
    text = cfg.read_text()
    assert 'SET gxWindow "1"' in text and 'SET gxMaximize "0"' in text and 'SET realmList "x"' in text
    assert len(list(tmp_path.glob("WTF/Config.wtf.wowbox-*.bak"))) == 1
    assert not set_cvars(cfg, {"gxWindow": "1"})


def test_attach_existing_prefers_titles_then_pid_order():
    cfg = AppConfig.model_validate({"team": {"leader": 1, "slots": {1: {}, 2: {}, 3: {}}}})
    team = Team(cfg)
    wins = FakeWindows()
    wins.add(0xA, pid=50)
    wins.add(0xB, pid=20, title="WoW3")
    wins.add(0xC, pid=10)
    Launcher(cfg, team, wins).attach_existing()
    assert team.clients[3].hwnd == 0xB
    assert team.clients[1].hwnd == 0xC and team.clients[2].hwnd == 0xA
    assert wins.title(0xC) == "WoW1"


def test_launch_missing_spawns_and_binds(tmp_path):
    exe = tmp_path / "Wow.exe"
    exe.write_text("")
    cfg = AppConfig.model_validate({"client": {"exe": str(exe), "launch_stagger": 0},
                                    "team": {"leader": 1, "slots": {1: {}, 2: {}}}})
    team = Team(cfg)
    wins = FakeWindows()

    class Proc:
        n = 0

        def __init__(self, cmd, cwd):
            Proc.n += 1
            self.pid = 100 + Proc.n
            self.returncode = None
            wins.add(0x1000 + Proc.n, pid=self.pid)

        def poll(self):
            return None

    started = Launcher(cfg, team, wins, spawn=Proc, sleep=lambda s: None).launch_missing()
    assert started == [1, 2]
    assert team.clients[2].pid == 102


def test_bridge_poller_decodes_and_goes_stale():
    r = Rig()
    t = [0.0]
    cap = FakeCapture()
    for i, h in enumerate(HWNDS.values()):
        img = encode({"health": 255, "level": 70 + i}, frame=1, block=6)
        full = __import__("numpy").zeros((100, 400, 3), dtype="uint8")
        full[:6, :img.shape[1]] = img
        cap.images[h] = full
    poller = BridgePoller(BridgeConfig(stale_after=1.0), r.team, r.windows, lambda: cap, clock=lambda: t[0])
    poller.poll_once(cap)
    assert r.team.clients[2].state.level == 71 and not r.team.clients[2].state.stale
    t[0] = 2.0
    poller.poll_once(cap)  # same frame counter for 2 s -> stale
    assert r.team.clients[1].state.stale
    r.windows.windows[HWNDS[3]].visible_points = False
    poller.poll_once(cap)
    assert poller.last_method[3] == "printwindow" and poller.last_method[1] == "mss"


def test_swap_and_assign_by_character():
    r = Rig()
    names = {1: "Healy", 2: "Tanky", 3: "Zappy", 4: ""}
    for slot, n in names.items():
        r.team.clients[slot].character = n
    r.team.clients[3].broadcast = False
    moves = r.team.assign_by_character({1: "tanky", 3: "Healy"})
    assert r.team.clients[1].character == "Tanky" and r.team.clients[1].hwnd == HWNDS[2]
    assert r.team.clients[3].character == "Healy" and r.team.clients[3].hwnd == HWNDS[1]
    assert r.team.clients[2].character == "Zappy"
    assert r.team.clients[3].broadcast is False  # per-slot settings stay with the slot
    assert [m[2] for m in moves] == [1, 3]
    assert r.team.assign_by_character({1: "Tanky", 3: "Healy"}) == []  # already in place
    assert r.team.clients[1].window_title == "WoW1 - Tanky"


def test_thumbnails_and_tabs_are_not_always_on_top():
    """They stay above WoW only: another program in front covers them."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from wowbox.layout.main_thumbs import ThumbStrip
    from wowbox.layout.tabs import TabBar
    for w in (ThumbStrip(str, lambda h: True), TabBar(str, lambda h: True)):
        assert not w.windowFlags() & Qt.WindowStaysOnTopHint
