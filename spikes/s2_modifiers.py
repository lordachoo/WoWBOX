"""S2: does a background client see Shift/Ctrl/Alt when modifiers are posted, or does it read the real keyboard?

In the TARGET client first:  /wbs binds   (run a second pass with /wbs keys)
Then run this and click a DIFFERENT window during the countdown (e.g. Notepad; it may receive
the physical Shift you hold in method E).

Before each method a marker key is posted so you can match the chat lines to the methods:
    F13 = 0 baseline: plain '1' posted
    F14 = A: VK_SHIFT posted as WM_KEYDOWN around '1'
    F15 = B: VK_CONTROL posted as WM_KEYDOWN around '1'
    F16 = C: VK_MENU (Alt) posted as WM_SYSKEYDOWN, '1' as WM_SYSKEYDOWN with the Alt context bit
    F17 = D: AttachThreadInput + SetKeyboardState(Shift down), then A again
    F18 = E: YOU physically hold Shift (on the foreground window), the script posts a plain '1'
    F19 = F: focus swap + SendInput Shift+1, then focus back (only with --focus-swap; steals focus briefly)

    python s2_modifiers.py [--hwnd 0x1234] [--focus-swap] [--only 0ABCDEF]
"""
from __future__ import annotations

import argparse
import ctypes
import time

from _common import (
    VK, banner, countdown, force_foreground, foreground_desc, kernel32, pick_window, post_key,
    send_input_keys, set_dpi_aware, stamp, user32,
)

user32.GetKeyboardState.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
user32.SetKeyboardState.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]

VK_SHIFT, VK_CONTROL, VK_MENU, VK_LSHIFT = 0x10, 0x11, 0x12, 0xA0
ONE = VK["1"]
GAP = 0.05


def marker(hwnd: int, n: int) -> None:
    vk = VK[f"F{13 + n}"]
    post_key(hwnd, vk, True)
    time.sleep(GAP)
    post_key(hwnd, vk, False)
    time.sleep(0.3)


def chord_posted(hwnd: int, mod_vk: int) -> None:
    post_key(hwnd, mod_vk, True)
    time.sleep(GAP)
    post_key(hwnd, ONE, True)
    time.sleep(GAP)
    post_key(hwnd, ONE, False)
    time.sleep(GAP)
    post_key(hwnd, mod_vk, False)


def m0(hwnd: int, _tid: int) -> None:
    post_key(hwnd, ONE, True)
    time.sleep(GAP)
    post_key(hwnd, ONE, False)


def ma(hwnd: int, _tid: int) -> None:
    chord_posted(hwnd, VK_SHIFT)


def mb(hwnd: int, _tid: int) -> None:
    chord_posted(hwnd, VK_CONTROL)


def mc(hwnd: int, _tid: int) -> None:
    post_key(hwnd, VK_MENU, True, sys_key=True, alt_context=True)
    time.sleep(GAP)
    post_key(hwnd, ONE, True, sys_key=True, alt_context=True)
    time.sleep(GAP)
    post_key(hwnd, ONE, False, sys_key=True, alt_context=True)
    time.sleep(GAP)
    post_key(hwnd, VK_MENU, False, sys_key=True)


def md(hwnd: int, tid: int) -> None:
    me = kernel32.GetCurrentThreadId()
    if not user32.AttachThreadInput(me, tid, True):
        print(f"  AttachThreadInput failed (err={ctypes.get_last_error()})")
        return
    try:
        state = (ctypes.c_ubyte * 256)()
        user32.GetKeyboardState(state)
        original = bytes(state)
        state[VK_SHIFT] |= 0x80
        state[VK_LSHIFT] |= 0x80
        print(f"  SetKeyboardState ok={bool(user32.SetKeyboardState(state))}")
        chord_posted(hwnd, VK_SHIFT)
        time.sleep(0.3)  # let the client drain its queue while the fake state is set
        restore = (ctypes.c_ubyte * 256).from_buffer_copy(original)
        user32.SetKeyboardState(restore)
    finally:
        user32.AttachThreadInput(me, tid, False)


def me_(hwnd: int, _tid: int) -> None:
    countdown(4, "HOLD SHIFT DOWN NOW on your keyboard (keep holding until told to release)")
    print(f"  [{stamp()}] posting plain '1' while you hold Shift")
    m0(hwnd, _tid)
    time.sleep(0.5)
    print("  >>> release Shift now")
    time.sleep(1.5)


def mf(hwnd: int, _tid: int, dwell: float = 0.05) -> None:
    prev = user32.GetForegroundWindow()
    t0 = time.perf_counter()
    ok = force_foreground(hwnd)
    deadline = t0 + 0.5
    while user32.GetForegroundWindow() != hwnd and time.perf_counter() < deadline:
        time.sleep(0.001)
    t_fg = time.perf_counter()
    got_fg = user32.GetForegroundWindow() == hwnd
    send_input_keys([(VK_SHIFT, True), (ONE, True), (ONE, False), (VK_SHIFT, False)])
    time.sleep(dwell)
    if prev:
        force_foreground(prev)
    t1 = time.perf_counter()
    print(f"  SetForegroundWindow ok={ok} became_foreground={got_fg} "
          f"focus_in={1000 * (t_fg - t0):.1f}ms total_swap={1000 * (t1 - t0):.1f}ms (dwell {1000 * dwell:.0f}ms)")


METHODS = [
    ("0", "baseline: plain '1' posted", m0),
    ("A", "Shift posted as WM_KEYDOWN", ma),
    ("B", "Ctrl posted as WM_KEYDOWN", mb),
    ("C", "Alt posted as WM_SYSKEYDOWN", mc),
    ("D", "AttachThreadInput + SetKeyboardState(Shift) + posted Shift", md),
    ("E", "physical Shift held by you, plain '1' posted", me_),
    ("F", "focus swap + SendInput Shift+1", mf),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hwnd")
    ap.add_argument("--focus-swap", action="store_true", help="include method F (steals focus briefly)")
    ap.add_argument("--only", default="", help="subset of method letters, e.g. 0AE")
    ap.add_argument("--delay", type=float, default=6.0)
    args = ap.parse_args()
    set_dpi_aware()

    target = pick_window(args.hwnd)
    banner(f"S2 modifier test -> {target.describe()}")
    chosen = [m for m in METHODS if (not args.only or m[0] in args.only.upper())]
    if not args.focus_swap:
        chosen = [m for m in chosen if m[0] != "F"]

    countdown(args.delay, "Click on ANOTHER window now (Notepad is ideal) so the target is in the background.")
    print(f"[{stamp()}] foreground window: {foreground_desc()}")

    for letter, desc, fn in chosen:
        n = "0ABCDEF".index(letter)
        print(f"\n[{stamp()}] marker F{13 + n} -> method {letter}: {desc}")
        marker(target.hwnd, n)
        fn(target.hwnd, target.tid)
        time.sleep(2.0)

    print("\nDone. Paste back:")
    print("  1. this console output")
    print("  2. all [WBS] lines from the target's chat (run once with /wbs binds and once with /wbs keys)")


if __name__ == "__main__":
    main()
