"""Layout geometry (pure functions, physical pixels)."""
from __future__ import annotations

import math

from ..platform.base import Rect

LAYOUT_GRIDS = {"grid_2x2": (2, 2), "grid_1x3": (3, 1)}  # (cols, rows)
TARGET_ASPECT = 16 / 9


def auto_grid(area: Rect, n: int) -> tuple[int, int]:
    """(cols, rows) whose cells come closest to 16:9 for n clients; fewer empty cells breaks ties."""
    n = max(1, n)
    best = None
    for cols in range(1, n + 1):
        rows = -(-n // cols)
        aspect = (area.w / cols) / (area.h / rows)
        score = (round(abs(math.log(aspect / TARGET_ASPECT)), 3), cols * rows - n)
        if best is None or score < best[0]:
            best = (score, (cols, rows))
    return best[1]


def grid_rects(area: Rect, cols: int, rows: int) -> list[Rect]:
    """Cells left-to-right, top-to-bottom. The last column/row absorbs rounding so cells tile exactly."""
    cells = []
    for r in range(rows):
        y0 = area.y + area.h * r // rows
        y1 = area.y + area.h * (r + 1) // rows
        for c in range(cols):
            x0 = area.x + area.w * c // cols
            x1 = area.x + area.w * (c + 1) // cols
            cells.append(Rect(x0, y0, x1 - x0, y1 - y0))
    return cells


def plan_grid(slots: list[int], area: Rect, cols: int, rows: int) -> dict[int, Rect]:
    """Assign slots (in order) to cells; extra slots share the last cell."""
    cells = grid_rects(area, cols, rows)
    return {s: cells[min(i, len(cells) - 1)] for i, s in enumerate(slots)}


WIDE_ASPECT = 2.0  # thumb_side auto: monitors wider than this (21:9, 32:9) get the thumbnails on the right
MIN_COLUMN = 240  # thumbnails on the right: the narrowest column worth having


def split_main_strip(work: Rect, strip_frac: float, strip_work: Rect | None = None,
                     side: str = "bottom") -> tuple[Rect, Rect]:
    """(main area, thumbnail strip). With strip_work (another monitor) the main area is the whole `work`.
    side right (or auto on a wide monitor): a 16:9 main area on the left, the strip is the column to its right."""
    if strip_work is not None:
        return work, strip_work
    if side == "auto":
        side = "right" if work.w / max(1, work.h) > WIDE_ASPECT else "bottom"
    if side == "right" and work.w - MIN_COLUMN > 0:
        main_w = min(int(work.h * TARGET_ASPECT), work.w - MIN_COLUMN)
        return Rect(work.x, work.y, main_w, work.h), Rect(work.x + main_w, work.y, work.w - main_w, work.h)
    strip_h = max(80, int(work.h * strip_frac))
    return Rect(work.x, work.y, work.w, work.h - strip_h), Rect(work.x, work.bottom - strip_h, work.w, strip_h)


def split_tabs(work: Rect, bar_h: int) -> tuple[Rect, Rect]:
    """(tab bar, main area)."""
    return Rect(work.x, work.y, work.w, bar_h), Rect(work.x, work.y + bar_h, work.w, work.h - bar_h)


def thumb_cells(strip: Rect, n: int, label_h: int = 20, pad: int = 4) -> list[tuple[Rect, Rect]]:
    """Cells inside the strip (coordinates relative to the strip): (cell, thumbnail area below the label)."""
    if n <= 0:
        return []
    cols = _thumb_cols(strip, n, label_h, pad)
    rows = -(-n // cols)
    out = []
    for cell in grid_rects(Rect(0, 0, strip.w, strip.h), cols, rows)[:n]:
        thumb = Rect(cell.x + pad, cell.y + label_h + pad, max(1, cell.w - 2 * pad),
                     max(1, cell.h - label_h - 2 * pad))
        out.append((cell, thumb))
    return out


def _thumb_cols(strip: Rect, n: int, label_h: int, pad: int) -> int:
    """The column count that gives the biggest 16:9 thumbnails (fewer empty cells breaks ties): one row in a wide
    strip, one column in a tall one."""
    best = None
    for cols in range(1, n + 1):
        rows = -(-n // cols)
        w = strip.w / cols - 2 * pad
        h = strip.h / rows - label_h - 2 * pad
        scale = min(w / TARGET_ASPECT, h) if w > 0 and h > 0 else 0
        score = (round(scale), -(cols * rows - n))
        if best is None or score > best[0]:
            best = (score, cols)
    return best[1]


def fit_aspect(area: Rect, src_w: int, src_h: int) -> Rect:
    """Largest rect with the source aspect ratio centred in area."""
    if src_w <= 0 or src_h <= 0:
        return area
    scale = min(area.w / src_w, area.h / src_h)
    w, h = max(1, int(src_w * scale)), max(1, int(src_h * scale))
    return Rect(area.x + (area.w - w) // 2, area.y + (area.h - h) // 2, w, h)
