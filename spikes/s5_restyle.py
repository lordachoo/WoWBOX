"""S5: does stripping the frame + SetWindowPos work cleanly on the D3D9 client (no device reset / black screen)?

Run it twice: once with the client at the character-select screen, once in the world.

    python s5_restyle.py [--hwnd 0x1234]                 # borderless, top-left quadrant, then bottom-right quadrant
    python s5_restyle.py --rect 0,0,1280,720             # explicit first rect (x,y,w,h)
    python s5_restyle.py --restore [--hwnd 0x1234]       # put the original style/position back

Watch the client while it runs: flicker, black frames, stretched/blurry UI, a long freeze = device reset.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import time

from _common import banner, monitor_info, out_path, pick_window, set_dpi_aware, user32, window_info

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_THICKFRAME, WS_SYSMENU = 0x00C00000, 0x00040000, 0x00080000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00020000, 0x00010000
WS_EX_DLGMODALFRAME, WS_EX_WINDOWEDGE, WS_EX_CLIENTEDGE, WS_EX_STATICEDGE = 0x1, 0x100, 0x200, 0x20000
SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_NOOWNERZORDER = 0x4, 0x10, 0x20, 0x200

user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]


def snapshot(hwnd: int, label: str) -> dict:
    w = window_info(hwnd)
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    snap = {"style": style, "exstyle": ex, "window_rect": w.window_rect, "client_origin": w.client_origin,
            "client_size": w.client_size, "hung": bool(user32.IsHungAppWindow(hwnd))}
    print(f"[{label}] style=0x{style:08X} ex=0x{ex:08X} window={w.window_rect} "
          f"client_origin={w.client_origin} client_size={w.client_size} hung={snap['hung']}")
    return snap


def client_brightness(hwnd: int) -> str:
    try:
        import mss
        import numpy as np
    except ImportError:
        return "(mss/numpy not installed; skipped)"
    w = window_info(hwnd)
    (x, y), (cw, ch) = w.client_origin, w.client_size
    if cw <= 0 or ch <= 0:
        return "(no client area)"
    with mss.mss() as sct:
        img = np.asarray(sct.grab({"left": x, "top": y, "width": cw, "height": ch}))[:, :, :3]
    mean = float(img.mean())
    return f"mean={mean:.1f} {'<-- looks BLACK' if mean < 3 else ''}"


def move(hwnd: int, rect: tuple[int, int, int, int]) -> float:
    x, y, cw, ch = rect
    t0 = time.perf_counter()
    user32.SetWindowPos(hwnd, None, x, y, cw, ch, SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_NOOWNERZORDER)
    return 1000 * (time.perf_counter() - t0)


def settle_and_check(hwnd: int, rect: tuple[int, int, int, int], label: str) -> None:
    for t in (0.5, 3.0):
        time.sleep(t)
        snap = snapshot(hwnd, f"{label} +{t}s")
        ok = snap["client_size"] == (rect[2], rect[3]) and snap["client_origin"] == (rect[0], rect[1])
        print(f"    client matches requested {rect}: {ok}; capture {client_brightness(hwnd)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hwnd")
    ap.add_argument("--rect", help="x,y,w,h for the first move (default: top-left quadrant of its monitor)")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()
    set_dpi_aware()

    target = pick_window(args.hwnd)
    hwnd = target.hwnd
    saved = out_path(f"s5_original_{hwnd:X}.json")

    if args.restore:
        orig = json.loads(saved.read_text())
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, orig["style"])
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, orig["exstyle"])
        l, t, r, b = orig["window_rect"]
        user32.SetWindowPos(hwnd, None, l, t, r - l, b - t, SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        snapshot(hwnd, "restored")
        return

    banner(f"S5 restyle -> {target.describe()}")
    before = snapshot(hwnd, "before")
    if not saved.exists():
        saved.write_text(json.dumps(before))
        print(f"original style saved to {saved}")

    work = monitor_info(hwnd).rcWork
    half_w, half_h = (work.right - work.left) // 2, (work.bottom - work.top) // 2
    first = tuple(int(v) for v in args.rect.split(",")) if args.rect else (work.left, work.top, half_w, half_h)
    second = (work.left + half_w, work.top + half_h, half_w, half_h)

    print("\nStep 1: strip caption/thick frame/system menu + edge ex-styles")
    style = before["style"] & ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
    ex = before["exstyle"] & ~(WS_EX_DLGMODALFRAME | WS_EX_WINDOWEDGE | WS_EX_CLIENTEDGE | WS_EX_STATICEDGE)
    user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
    print(f"Step 2: SetWindowPos -> {first}  ({move(hwnd, first):.1f} ms)")
    settle_and_check(hwnd, first, "after move 1")

    print(f"\nStep 3: second layout change -> {second}  ({move(hwnd, second):.1f} ms)")
    settle_and_check(hwnd, second, "after move 2")

    print(f"\nStep 4: back to {first}  ({move(hwnd, first):.1f} ms)")
    settle_and_check(hwnd, first, "after move 3")

    print(f"\nLeft borderless. Restore later with:  python s5_restyle.py --restore --hwnd 0x{hwnd:X}")
    print("Paste back this output plus what you SAW: flicker? black? stretched/blurry UI? freeze? at char-select or in-world?")


if __name__ == "__main__":
    main()
