"""S1: do background 3.3.5a windows accept PostMessage(WM_KEYDOWN/WM_KEYUP)?

In the TARGET client first:  /wbs keys   (or /wbs binds)
Then run this, and during the countdown click a DIFFERENT window so the target is in the background.

    python s1_postmessage.py [--hwnd 0x1234] [--key 1] [--presses 3]
"""
from __future__ import annotations

import argparse
import time

from _common import banner, countdown, foreground_desc, pick_window, post_key, vk_from_name, set_dpi_aware, stamp


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hwnd", help="target window handle (default: pick from found WoW windows)")
    ap.add_argument("--key", default="1", help="key to post (default 1)")
    ap.add_argument("--presses", type=int, default=3)
    ap.add_argument("--hold", type=float, default=0.05, help="seconds between down and up")
    ap.add_argument("--delay", type=float, default=6.0, help="countdown seconds")
    args = ap.parse_args()
    set_dpi_aware()

    target = pick_window(args.hwnd)
    vk = vk_from_name(args.key)
    banner(f"S1 PostMessage test -> {target.describe()}")
    print(f"key={args.key!r} vk=0x{vk:02X}")
    countdown(args.delay, "Click on ANOTHER window now (another client, or Notepad) so the target is in the background.")

    target_fg = foreground_desc().startswith(f"0x{target.hwnd:X} ")
    print(f"[{stamp()}] foreground window: {foreground_desc()}")
    if target_fg:
        print("  WARNING: the target IS the foreground window, so this run doesn't test background delivery.")

    for i in range(1, args.presses + 1):
        print(f"[{stamp()}] press {i}/{args.presses}: WM_KEYDOWN ... WM_KEYUP")
        post_key(target.hwnd, vk, True)
        time.sleep(args.hold)
        post_key(target.hwnd, vk, False)
        time.sleep(1.0)

    print("\nDone. Paste back:")
    print("  1. this console output")
    print("  2. the [WBS] lines from the target's chat (or 'nothing appeared')")
    print("  3. whether the target was visible, partly covered, or minimized")


if __name__ == "__main__":
    main()
