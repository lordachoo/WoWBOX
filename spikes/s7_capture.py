"""S7: can we read pixels from clients that are visible-but-unfocused, occluded, or minimized?

In EVERY client first:  /wbs pattern   (draws a 64x16 px colour test pattern in the top-left corner)
Then run once per scenario, with a tag:

    python s7_capture.py --tag visible      # all clients visible, one of them focused
    python s7_capture.py --tag occluded     # cover one or more clients with another window
    python s7_capture.py --tag minimized    # minimize one or more clients
    python s7_capture.py --tag thumbs       # optional: while s6_dwm_thumbs.py is showing them

For each client and each method (mss, dxcam, PrintWindow PW_RENDERFULLCONTENT, GDI BitBlt) it saves
PNGs (native + 8x upscaled) to spikes/out/s7_<tag>/, compares the pattern against the expected
colours, checks that the frame-counter block changes (fresh vs stale frame), and times each method.
Paste back the printed table and spikes/out/s7_<tag>/summary.json; zip the PNGs too if any look odd.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import time
import traceback

import numpy as np
from PIL import Image

from _common import (
    WindowInfo, banner, find_wow_windows, monitor_info, out_path, print_windows, set_dpi_aware, user32,
)

# Must match COLORS in addon/WowboxSpike/WowboxSpike.lua. Block 7 = (counter, 90, 165).
EXPECTED = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (128, 64, 32), (17, 34, 51), (200, 150, 100)]
COUNTER_GB = (90, 165)

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                            ctypes.POINTER(BITMAPINFOHEADER), wt.UINT]

PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2
SRCCOPY = 0x00CC0020


# --------------------------------------------------------------------------
# capture methods: each returns an RGB uint8 array of the region (h, w, 3)
# --------------------------------------------------------------------------
def _gdi_capture(hwnd: int, cw: int, ch: int, w: int, h: int, use_printwindow: bool) -> np.ndarray:
    win_dc = user32.GetDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(win_dc)
    bw, bh = (cw, ch) if use_printwindow else (w, h)
    bmp = gdi32.CreateCompatibleBitmap(win_dc, bw, bh)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        if use_printwindow:
            if not user32.PrintWindow(hwnd, mem_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
                raise RuntimeError("PrintWindow returned 0")
        elif not gdi32.BitBlt(mem_dc, 0, 0, w, h, win_dc, 0, 0, SRCCOPY):
            raise RuntimeError("BitBlt returned 0")
        gdi32.SelectObject(mem_dc, old)
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=bw, biHeight=-bh,
                               biPlanes=1, biBitCount=32, biCompression=0)
        buf = (ctypes.c_ubyte * (bw * bh * 4))()
        if gdi32.GetDIBits(mem_dc, bmp, 0, bh, buf, ctypes.byref(bmi), 0) != bh:
            raise RuntimeError("GetDIBits failed")
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, win_dc)
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(bh, bw, 4)
    return arr[:h, :w, 2::-1].copy()  # BGRA -> RGB


def cap_printwindow(win: WindowInfo, w: int, h: int) -> np.ndarray:
    cw, ch = win.client_size
    return _gdi_capture(win.hwnd, cw, ch, w, h, use_printwindow=True)


def cap_bitblt(win: WindowInfo, w: int, h: int) -> np.ndarray:
    cw, ch = win.client_size
    return _gdi_capture(win.hwnd, cw, ch, w, h, use_printwindow=False)


_mss = None


def cap_mss(win: WindowInfo, w: int, h: int) -> np.ndarray:
    global _mss
    import mss
    if _mss is None:
        _mss = mss.mss()
    x, y = win.client_origin
    shot = _mss.grab({"left": x, "top": y, "width": w, "height": h})
    return np.asarray(shot)[:, :, 2::-1].copy()


_dxcams: dict[int, object] = {}


def _dxcam_for(win: WindowInfo):
    import dxcam
    hmon = user32.MonitorFromWindow(win.hwnd, 2)
    if hmon in _dxcams:
        return _dxcams[hmon]
    mi = monitor_info(win.hwnd).rcMonitor
    mi_size = (mi.right - mi.left, mi.bottom - mi.top)
    fallback = None
    for dev in range(4):
        for out in range(8):
            try:
                cam = dxcam.create(device_idx=dev, output_idx=out, output_color="RGB")
            except Exception:
                continue
            if cam is None:
                continue
            try:
                if int(cam._output.hmonitor or 0) == int(hmon):  # private attr; fall back on size match
                    _dxcams[hmon] = (cam, mi.left, mi.top)
                    return _dxcams[hmon]
            except Exception:
                pass
            if fallback is None and (cam.width, cam.height) == mi_size:
                fallback = cam
    if fallback is None:
        raise RuntimeError("no dxcam output matches this window's monitor")
    _dxcams[hmon] = (fallback, mi.left, mi.top)
    return _dxcams[hmon]


def cap_dxcam(win: WindowInfo, w: int, h: int) -> np.ndarray:
    cam, mx, my = _dxcam_for(win)
    x, y = win.client_origin[0] - mx, win.client_origin[1] - my
    for _ in range(50):
        frame = cam.grab(region=(x, y, x + w, y + h))
        if frame is not None:
            return np.asarray(frame)[:, :, :3].copy()
        time.sleep(0.01)
    raise RuntimeError("dxcam returned no frame (no screen change within 500 ms)")


METHODS = {"mss": cap_mss, "dxcam": cap_dxcam, "printwindow": cap_printwindow, "bitblt": cap_bitblt}


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def analyze(img: np.ndarray, block: int) -> dict:
    res = {"mean_rgb": [round(float(v), 1) for v in img.reshape(-1, 3).mean(axis=0)],
           "all_black": bool(img.max() < 8)}
    for row, name in ((0, "rowA_SetTexture"), (1, "rowB_VertexColor")):
        cy = row * block + block // 2
        if cy >= img.shape[0]:
            continue
        centre_err, edge_err, samples = [], [], []
        for i, exp in enumerate(EXPECTED):
            cx = i * block + block // 2
            if cx >= img.shape[1]:
                break
            got = img[cy, cx].tolist()
            samples.append(got)
            centre_err.append(max(abs(g - e) for g, e in zip(got, exp)))
            for ex in (i * block, i * block + block - 1):  # first/last column of the block
                edge_err.append(max(abs(int(g) - e) for g, e in zip(img[cy, ex], exp)))
        cnt = img[cy, 7 * block + block // 2].tolist() if 7 * block + block // 2 < img.shape[1] else None
        res[name] = {
            "centre_max_err": max(centre_err) if centre_err else None,
            "edge_max_err": max(edge_err) if edge_err else None,
            "pattern_found": bool(centre_err) and max(centre_err) <= 3,
            "samples": samples,
            "counter_px": cnt,
            "counter_gb_ok": bool(cnt) and abs(cnt[1] - COUNTER_GB[0]) <= 3 and abs(cnt[2] - COUNTER_GB[1]) <= 3,
        }
    return res


def save_png(img: np.ndarray, path) -> None:
    im = Image.fromarray(img)
    im.save(path)
    im.resize((img.shape[1] * 8, img.shape[0] * 8), Image.NEAREST).save(str(path).replace(".png", "_x8.png"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--w", type=int, default=64)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--block", type=int, default=8)
    ap.add_argument("--bench", type=int, default=20, help="timed repetitions per method")
    ap.add_argument("--methods", default=",".join(METHODS))
    ap.add_argument("--title", help="capture windows whose title contains this instead of WoW")
    args = ap.parse_args()
    set_dpi_aware()

    wins = find_wow_windows(args.title)
    banner(f"S7 capture, tag={args.tag!r}")
    print_windows(wins)
    if not wins:
        raise SystemExit("No WoW windows found.")
    outdir = out_path(f"s7_{args.tag}")
    outdir.mkdir(exist_ok=True)

    summary = []
    for idx, win in enumerate(wins):
        w, h = min(args.w, win.client_size[0]), min(args.h, win.client_size[1])
        state = f"minimized={win.iconic} focused={user32.GetForegroundWindow() == win.hwnd}"
        for name in args.methods.split(","):
            rec = {"client": idx, "hwnd": f"0x{win.hwnd:X}", "method": name, "state": state}
            try:
                if w <= 0 or h <= 0:
                    raise RuntimeError(f"client area is {win.client_size} (minimized?)")
                fn = METHODS[name]
                img1 = fn(win, w, h)
                time.sleep(0.2)
                img2 = fn(win, w, h)
                t0 = time.perf_counter()
                for _ in range(args.bench):
                    fn(win, w, h)
                rec["ms_per_capture"] = round(1000 * (time.perf_counter() - t0) / max(1, args.bench), 2)
                rec.update(analyze(img1, args.block))
                c1 = rec.get("rowB_VertexColor", {}).get("counter_px")
                c2 = analyze(img2, args.block).get("rowB_VertexColor", {}).get("counter_px")
                rec["counter_changed_in_200ms"] = (c1 is not None and c2 is not None and c1 != c2)
                save_png(img1, outdir / f"client{idx}_{name}.png")
            except Exception as e:  # report and move on: failures are results here
                rec["error"] = f"{type(e).__name__}: {e}"
                if not isinstance(e, (RuntimeError, ImportError)):
                    rec["traceback"] = traceback.format_exc(limit=3)
            summary.append(rec)

    print(f"\n{'client':>6} {'method':<12} {'found A/B':<10} {'err A/B':<10} {'edge A/B':<10} "
          f"{'fresh':<6} {'ms':>7}  notes")
    for r in summary:
        a, b = r.get("rowA_SetTexture", {}), r.get("rowB_VertexColor", {})
        found = f"{int(bool(a.get('pattern_found')))}/{int(bool(b.get('pattern_found')))}"
        err = f"{a.get('centre_max_err')}/{b.get('centre_max_err')}"
        edge = f"{a.get('edge_max_err')}/{b.get('edge_max_err')}"
        notes = r.get("error") or ("ALL BLACK" if r.get("all_black") else f"mean={r.get('mean_rgb')}")
        print(f"{r['client']:>6} {r['method']:<12} {found:<10} {err:<10} {edge:<10} "
              f"{str(r.get('counter_changed_in_200ms', '-')):<6} {r.get('ms_per_capture', '-'):>7}  {notes}")

    (outdir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nPNGs and summary.json in {outdir}")
    print("found = pattern colours matched (<=3 off), err = worst centre error, edge = worst error at block edges"
          " (0 means pixel-exact), fresh = frame counter changed between two captures 200 ms apart.")


if __name__ == "__main__":
    main()
