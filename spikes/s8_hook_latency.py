"""S8: keypress -> all clients latency through a Python WH_KEYBOARD_LL hook + worker thread + PostMessage.

The design under test (same as the plan's risk mitigation): the hook callback only timestamps and
queues; a worker thread does the PostMessage fan-out.

    python s8_hook_latency.py                          # synthetic: injects 300 F24 presses, posts to 4 dummy windows
    python s8_hook_latency.py --busy-threads 2         # same, with Python threads hogging the GIL (UI-load stand-in)
    python s8_hook_latency.py --busy-threads 2 --switch-ms 0.5   # does a shorter GIL switch interval help?
    python s8_hook_latency.py --target wow             # post F24 to the real WoW windows (F24 is unbound by default)
    python s8_hook_latency.py --real --seconds 20      # type real keys anywhere for 20 s (posts to dummies)

Paste back the printed tables. Run at least the first two; the --target wow run if clients are up.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import queue
import statistics
import sys
import threading
import time

from _common import (
    LRESULT, ULONG_PTR, WM_KEYDOWN, WM_KEYUP, WM_QUIT, WM_SYSKEYDOWN, banner, find_wow_windows, kernel32,
    make_lparam, print_windows, send_input_keys, user32,
)

WH_KEYBOARD_LL = 13
LLKHF_INJECTED = 0x10
VK_F24 = 0x87
MAGIC = 0x57420000  # dwExtraInfo tag for our injected events; low 16 bits = sequence number


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.DestroyWindow.argtypes = [wt.HWND]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE


def pump_until_quit() -> None:
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


class HookThread(threading.Thread):
    """Owns the LL hook and its message pump. The callback only timestamps + enqueues."""

    def __init__(self, q: queue.SimpleQueue):
        super().__init__(daemon=True, name="hook")
        self.q = q
        self.cb_ns: list[int] = []
        self.ready = threading.Event()
        self.tid = 0
        self._proc = HOOKPROC(self._callback)  # keep a reference or ctypes frees it

    def _callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0:
            t = time.perf_counter_ns()
            kb = KBDLLHOOKSTRUCT.from_address(l_param)
            self.q.put((t, kb.vkCode, w_param, kb.flags, kb.dwExtraInfo))
            self.cb_ns.append(time.perf_counter_ns() - t)
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def run(self) -> None:
        self.tid = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            print(f"SetWindowsHookExW failed: {ctypes.get_last_error()}")
        self.ready.set()
        pump_until_quit()
        user32.UnhookWindowsHookEx(hook)

    def stop(self) -> None:
        user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)


class SinkThread(threading.Thread):
    """Message-only windows that stand in for game clients; pumps (and ignores) what's posted to them."""

    def __init__(self, n: int):
        super().__init__(daemon=True, name="sink")
        self.n = n
        self.hwnds: list[int] = []
        self.ready = threading.Event()
        self.tid = 0

    def run(self) -> None:
        self.tid = kernel32.GetCurrentThreadId()
        hwnd_message = wt.HWND(-3)
        for _ in range(self.n):
            self.hwnds.append(user32.CreateWindowExW(0, "STATIC", "wowbox-sink", 0, 0, 0, 0, 0,
                                                     hwnd_message, None, None, None))
        self.ready.set()
        pump_until_quit()
        for h in self.hwnds:
            user32.DestroyWindow(h)

    def stop(self) -> None:
        user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)


def worker(q: queue.SimpleQueue, targets: list[int], records: list, synthetic: bool, post_vk: int | None) -> None:
    while True:
        item = q.get()
        if item is None:
            return
        t_hook, vk, w_param, flags, extra = item
        if synthetic:
            if (extra & 0xFFFF0000) != MAGIC:
                continue
        elif flags & LLKHF_INJECTED:
            continue
        t_deq = time.perf_counter_ns()
        down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
        out_vk = post_vk or vk
        msg, lp = (WM_KEYDOWN if down else WM_KEYUP), make_lparam(out_vk, down)
        for h in targets:
            user32.PostMessageW(h, msg, out_vk, lp)
        t_done = time.perf_counter_ns()
        records.append({"seq": extra & 0xFFFF, "t_hook": t_hook, "t_deq": t_deq, "t_done": t_done})


def busy(stop: threading.Event) -> None:
    x = 0
    while not stop.is_set():
        x += 1


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def table(rows: dict[str, list[float]]) -> None:
    print(f"\n{'stage (microseconds)':<28}{'n':>6}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}{'mean':>9}")
    for name, vals in rows.items():
        if not vals:
            print(f"{name:<28}{0:>6}")
            continue
        print(f"{name:<28}{len(vals):>6}{pct(vals, 50):>9.0f}{pct(vals, 95):>9.0f}{pct(vals, 99):>9.0f}"
              f"{max(vals):>9.0f}{statistics.fmean(vals):>9.0f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real", action="store_true", help="measure your real keypresses instead of injected F24")
    ap.add_argument("--seconds", type=float, default=20.0, help="duration of --real mode")
    ap.add_argument("--presses", type=int, default=300, help="synthetic presses (each = down + up)")
    ap.add_argument("--interval", type=float, default=0.01, help="seconds between synthetic events")
    ap.add_argument("--target", choices=["dummy", "wow"], default="dummy")
    ap.add_argument("--clients", type=int, default=4, help="number of dummy targets")
    ap.add_argument("--busy-threads", type=int, default=0, help="Python threads spinning to contend for the GIL")
    ap.add_argument("--switch-ms", type=float, help="override sys.setswitchinterval (GIL hand-off), in ms")
    args = ap.parse_args()
    if args.switch_ms:
        sys.setswitchinterval(args.switch_ms / 1000)

    banner(f"S8 hook latency  python={sys.version.split()[0]}  switchinterval={sys.getswitchinterval() * 1000:.1f}ms")
    sink = None
    if args.target == "wow":
        wins = find_wow_windows()
        print_windows(wins)
        if not wins:
            raise SystemExit("No WoW windows found.")
        targets = [w.hwnd for w in wins]
        print("Posting F24 to the WoW windows (unbound by default; check it's unbound in your keybinds).")
    else:
        sink = SinkThread(args.clients)
        sink.start()
        sink.ready.wait()
        targets = sink.hwnds
        print(f"Posting to {len(targets)} dummy message-only windows.")

    q: queue.SimpleQueue = queue.SimpleQueue()
    records: list[dict] = []
    hook = HookThread(q)
    hook.start()
    hook.ready.wait()
    synthetic = not args.real
    post_vk = VK_F24 if (args.target == "wow" and not synthetic) else None
    if args.target == "wow" and not synthetic:
        print("Real mode with --target wow: every key you press is forwarded to the clients as F24.")
    wk = threading.Thread(target=worker, args=(q, targets, records, synthetic, post_vk), daemon=True)
    wk.start()

    stop_busy = threading.Event()
    for _ in range(args.busy_threads):
        threading.Thread(target=busy, args=(stop_busy,), daemon=True).start()

    injected: dict[int, int] = {}
    if synthetic:
        print(f"Injecting {args.presses} F24 presses (goes to the focused window; F24 is normally unused)...")
        seq = 0
        for _ in range(args.presses):
            for down in (True, False):
                seq += 1
                injected[seq] = time.perf_counter_ns()
                send_input_keys([(VK_F24, down)], extra=MAGIC | seq)
                time.sleep(args.interval)
        time.sleep(0.5)
    else:
        print(f"Type keys anywhere for {args.seconds:.0f}s ...")
        time.sleep(args.seconds)

    stop_busy.set()
    hook.stop()
    q.put(None)
    wk.join(2)
    if sink:
        sink.stop()

    us = lambda ns: ns / 1000  # noqa: E731
    rows = {
        "hook callback body": [us(v) for v in hook.cb_ns],
        "hook -> worker dequeue": [us(r["t_deq"] - r["t_hook"]) for r in records],
        f"post fan-out ({len(targets)} targets)": [us(r["t_done"] - r["t_deq"]) for r in records],
        "hook -> last post": [us(r["t_done"] - r["t_hook"]) for r in records],
    }
    if synthetic:
        rows["SendInput -> hook"] = [us(r["t_hook"] - injected[r["seq"]]) for r in records if r["seq"] in injected]
        rows["SendInput -> last post"] = [us(r["t_done"] - injected[r["seq"]]) for r in records if r["seq"] in injected]
    table(rows)
    if synthetic:
        seen = {r["seq"] for r in records}
        print(f"\ninjected events: {len(injected)}  seen by hook: {len(seen)}  missing: {len(injected) - len(seen)}")
    over = [v for v in rows["hook -> last post"] if v > 10_000]
    print(f"events over 10 ms (hook -> last post): {len(over)} / {len(records)}")
    print("\nPaste back this output (from each variant you ran).")


if __name__ == "__main__":
    main()
