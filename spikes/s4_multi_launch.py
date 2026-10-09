"""S4: can we start several Wow.exe instances directly? Any prompts, conflicts, or config clobbering?

    python s4_multi_launch.py --exe "C:/Games/WoW335/Wow.exe" [--count 4] [--stagger 3] [--args "-someflag"]

The script:
  * launches N copies (cwd = the WoW folder) and records PID -> first WoW window, with timings,
  * reports any other windows those PIDs open (error dialogs, prompts) and any processes that exit,
  * reports WoW windows that appear under an UNEXPECTED pid (i.e. the exe re-spawned itself),
  * at the end, optionally closes the clients gracefully (WM_CLOSE) and reports whether
    WTF/Config.wtf changed, which matters if every instance writes the same file on exit.

Per-instance config switch: try `--args` with a candidate switch and see whether the clients
pick up a different file. Report what you tried; the plan doesn't assume one exists.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import time
from pathlib import Path

from _common import (
    WM_CLOSE, all_top_level_windows, banner, is_wow_window, set_dpi_aware, user32, window_class, window_info,
    window_pid_tid, window_text,
)


def file_sig(p: Path) -> tuple[float, str] | None:
    if not p.exists():
        return None
    return p.stat().st_mtime, hashlib.sha1(p.read_bytes()).hexdigest()[:12]


def gx_lines(p: Path) -> set[str]:
    if not p.exists():
        return set()
    return {l for l in p.read_text(errors="replace").splitlines() if l.lower().startswith("set gx")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--stagger", type=float, default=3.0, help="seconds between launches")
    ap.add_argument("--args", default="", help="extra command-line args for every client")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()
    set_dpi_aware()

    exe = Path(args.exe)
    cfg = exe.parent / "WTF" / "Config.wtf"
    banner(f"S4 multi-launch: {args.count}x {exe} {args.args}")

    preexisting = {window_pid_tid(h)[0] for h in all_top_level_windows() if is_wow_window(h)}
    if preexisting:
        print(f"Note: WoW already running with PIDs {sorted(preexisting)}; they're ignored.")
    cfg_before, gx_before = file_sig(cfg), gx_lines(cfg)

    launched: dict[int, dict] = {}
    t_start = time.perf_counter()
    for i in range(args.count):
        cmd = [str(exe), *args.args.split()]
        p = subprocess.Popen(cmd, cwd=str(exe.parent))
        launched[p.pid] = {"n": i + 1, "proc": p, "t_launch": time.perf_counter() - t_start,
                           "t_window": None, "hwnd": None, "exit": None, "others": set()}
        print(f"  launched #{i + 1}: pid={p.pid} at t={launched[p.pid]['t_launch']:.1f}s")
        time.sleep(args.stagger)

    stray: dict[int, str] = {}
    deadline = time.perf_counter() + args.timeout
    while time.perf_counter() < deadline:
        now = time.perf_counter() - t_start
        for pid, rec in launched.items():
            if rec["exit"] is None and (code := rec["proc"].poll()) is not None:
                rec["exit"] = (code, now)
                print(f"  pid={pid} (#{rec['n']}) EXITED with code {code} at t={now:.1f}s")
        for h in all_top_level_windows():
            pid, _ = window_pid_tid(h)
            if pid in launched:
                rec = launched[pid]
                if is_wow_window(h) and rec["hwnd"] is None:
                    rec["hwnd"], rec["t_window"] = h, now
                    print(f"  pid={pid} (#{rec['n']}) WoW window 0x{h:X} at t={now:.1f}s")
                elif not is_wow_window(h) and user32.IsWindowVisible(h):
                    rec["others"].add(f"class={window_class(h)!r} title={window_text(h)!r}")
            elif is_wow_window(h) and pid not in preexisting and pid not in stray:
                stray[pid] = f"0x{h:X} {window_text(h)!r}"
                print(f"  WoW window under UNEXPECTED pid={pid}: {stray[pid]}")
        if all(r["hwnd"] or r["exit"] for r in launched.values()):
            break
        time.sleep(0.5)

    banner("Summary")
    for pid, rec in launched.items():
        info = window_info(rec["hwnd"]).describe() if rec["hwnd"] and user32.IsWindow(rec["hwnd"]) else "no window"
        print(f"#{rec['n']} pid={pid} launch_t={rec['t_launch']:.1f}s window_t={rec['t_window']} exit={rec['exit']}")
        print(f"    {info}")
        for o in sorted(rec["others"]):
            print(f"    other visible window: {o}")
    if stray:
        print(f"Stray WoW windows (pid not launched by us): {stray}")

    ans = input("\nLog in on the clients if you want, then press Enter to close all launched clients gracefully,\n"
                "or type 'keep' to leave them running: ").strip().lower()
    if ans != "keep":
        for rec in launched.values():
            if rec["hwnd"] and user32.IsWindow(rec["hwnd"]):
                user32.PostMessageW(rec["hwnd"], WM_CLOSE, 0, 0)
        for rec in launched.values():
            try:
                rec["proc"].wait(timeout=20)
            except subprocess.TimeoutExpired:
                print(f"  #{rec['n']} didn't exit after WM_CLOSE; terminating")
                rec["proc"].terminate()
        cfg_after, gx_after = file_sig(cfg), gx_lines(cfg)
        print(f"\nConfig.wtf before={cfg_before} after={cfg_after} changed={cfg_before != cfg_after}")
        for l in sorted(gx_before - gx_after):
            print(f"  - {l}")
        for l in sorted(gx_after - gx_before):
            print(f"  + {l}")

    print("\nPaste back this whole console output, plus a note of any prompts you saw on screen.")


if __name__ == "__main__":
    main()
