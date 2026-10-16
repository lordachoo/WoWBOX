"""Applies layouts to the attached clients. Main (Qt) thread only."""
from __future__ import annotations

import logging
from typing import Protocol

from ..config import LayoutConfig
from ..platform.base import MonitorInfo, Rect, WindowAPI
from ..team import Team
from .grid import LAYOUT_GRIDS, auto_grid, plan_grid, split_main_strip, split_tabs

log = logging.getLogger(__name__)

LAYOUT_ORDER = ["grid_auto", "grid_2x2", "grid_1x3", "main_thumbs", "tabs"]


class ThumbHost(Protocol):
    def show_at(self, rect: Rect, entries: list[tuple[int, int]], mover) -> None: ...
    def clear(self) -> None: ...


class TabHost(Protocol):
    def show_at(self, rect: Rect, slots: list[int], main: int, mover) -> None: ...
    def set_main(self, main: int) -> None: ...
    def hide(self) -> None: ...


class LayoutManager:
    def __init__(self, cfg: LayoutConfig, team: Team, windows: WindowAPI,
                 thumbs: ThumbHost | None = None, tabs: TabHost | None = None) -> None:
        self.cfg = cfg
        self.team = team
        self.windows = windows
        self.thumbs = thumbs
        self.tabs = tabs
        self.current = cfg.default
        self.main_area: Rect | None = None

    def monitor(self, index: int | None) -> MonitorInfo:
        mons = self.windows.monitors()
        if not index or index < 1:
            return mons[0]
        return mons[min(index, len(mons)) - 1]

    def _mover(self, hwnd: int, rect: Rect) -> None:
        self.windows.move(hwnd, rect, to_top=True)

    def _prepare(self, hwnd: int) -> None:
        self.windows.restore(hwnd)
        self.windows.make_borderless(hwnd)

    def apply(self, name: str | None = None) -> None:
        if name:
            self.current = name
        clients = self.team.attached()
        work = self.monitor(self.cfg.monitor).work
        log.info("layout %s on %d client(s)", self.current, len(clients))
        if self.current in LAYOUT_GRIDS or self.current == "grid_auto":
            if self.thumbs:
                self.thumbs.clear()
            if self.tabs:
                self.tabs.hide()
            cols, rows = LAYOUT_GRIDS.get(self.current) or auto_grid(work, len(clients))
            for slot, rect in plan_grid([c.slot for c in clients], work, cols, rows).items():
                h = self.team.hwnd(slot)
                self._prepare(h)
                self.windows.move(h, rect)
            self.main_area = None
            return

        if self.team.main_slot not in [c.slot for c in clients] and clients:
            self.team.main_slot = clients[0].slot
        if self.current == "main_thumbs":
            self.main_area, strip = self._main_strip()
            if self.tabs:
                self.tabs.hide()
        else:  # tabs
            bar, self.main_area = split_tabs(work, self.cfg.tab_bar_height)
            if self.thumbs:
                self.thumbs.clear()
        for c in clients:
            self._prepare(c.hwnd)
            self.windows.move(c.hwnd, self.main_area)
        self.set_main(self.team.main_slot, focus=True)
        if self.current == "main_thumbs" and self.thumbs:
            self.thumbs.show_at(strip, self._thumb_entries(), self._mover)
        elif self.current == "tabs" and self.tabs:
            self.tabs.show_at(bar, [c.slot for c in clients], self.team.main_slot, self._mover)

    def _main_strip(self) -> tuple[Rect, Rect]:
        """main_thumbs: (main area, thumbnail strip)."""
        work = self.monitor(self.cfg.monitor).work
        strip_work = self.monitor(self.cfg.thumbs_monitor).work if self.cfg.thumbs_monitor else None
        return split_main_strip(work, self.cfg.thumb_strip, None if strip_work == work else strip_work,
                                self.cfg.thumb_side)

    def _thumb_entries(self) -> list[tuple[int, int]]:
        return [(c.slot, c.hwnd) for c in self.team.attached() if c.slot != self.team.main_slot]

    def cycle(self) -> str:
        i = LAYOUT_ORDER.index(self.current) if self.current in LAYOUT_ORDER else -1
        self.apply(LAYOUT_ORDER[(i + 1) % len(LAYOUT_ORDER)])
        return self.current

    def set_main(self, slot: int, focus: bool = True) -> None:
        h = self.team.hwnd(slot)
        if h is None:
            return
        self.team.main_slot = slot
        if self.main_area is not None:
            self.windows.move(h, self.main_area, to_top=True)
        if focus:
            self.windows.set_foreground(h)
        if self.current == "main_thumbs" and self.thumbs and self.main_area is not None:
            self.thumbs.show_at(self._main_strip()[1], self._thumb_entries(), self._mover)
        if self.current == "tabs" and self.tabs:
            self.tabs.set_main(slot)

    def next_main(self) -> None:
        slots = [c.slot for c in self.team.attached()]
        if not slots:
            return
        i = slots.index(self.team.main_slot) if self.team.main_slot in slots else -1
        self.set_main(slots[(i + 1) % len(slots)])

    def restore_frames(self) -> None:
        for c in self.team.attached():
            self.windows.restore_frame(c.hwnd)
        if self.thumbs:
            self.thumbs.clear()
        if self.tabs:
            self.tabs.hide()
