# WoWBox — Phase 0 spikes

Throwaway scripts that answer questions S1–S9 from `wowbox-project-plan.md` §5 on the real machine.
Nothing in M1+ gets built until the results are in. Paste the results into `RESULTS.md` (or straight into the chat).

## Setup

```powershell
cd spikes
python -m venv .venv            # already done on this machine; 3.10+ works for the spikes, the app targets 3.12+
.venv\Scripts\activate
pip install -r requirements.txt
```

- Run the scripts from the **same privilege level as WoW**. If WoW runs as admin and the script doesn't, Windows (UIPI) blocks `PostMessage`. The scripts print "access denied" when that happens.
- Copy `addon/WowboxSpike` to `<WoW>/Interface/AddOns/` and enable it on the character screen. It prints only to your own chat frame and adds `/wbs`:

  | Command | What it does | Used by |
  |---|---|---|
  | `/wbs info` | build, gx/UI CVars, UI scale, `SetTexture(r,g,b,a)` test, chat-frame globals | S3, S9 |
  | `/wbs pattern [height]` | 64×16 px colour test pattern in the top-left corner | S7, S9 |
  | `/wbs keys` | key logger: swallows and prints every key + modifier state. **Esc** turns it off | S1, S2 |
  | `/wbs binds` | override bindings on `1`, `SHIFT-1`, `CTRL-1`, `ALT-1`, `F13`–`F19` that print when fired | S1, S2 |
  | `/wbs chat` | prints chat edit box state changes | S9 |

- Test somewhere safe (a city or inn). Keys posted to a client act like real keypresses.
- Every script takes `--help`. Most take `--hwnd 0x…` to skip the window picker.
- Output files go to `spikes/out/`.

Suggested order: **S3 → S4 → S9 → S1 → S2 → S5 → S6 → S7 → S8**.

---

## S3: Config.wtf CVars

```powershell
python s3_config_wtf.py --exe "C:/Games/WoW335/Wow.exe"
```
**Paste back:** `out/s3_config_sanitized.txt` (`accountName`/`accountList` are redacted). Also say which settings you use in-game for windowed mode (Video options → "Windowed mode", "Maximized").

## S4: multiple instances

Close all clients first.
```powershell
python s4_multi_launch.py --exe "C:/Games/WoW335/Wow.exe" --count 4
```
Watch the screen for prompts. At the end, log into one or two clients if you like, then press Enter: the clients get closed gracefully and the script reports whether `Config.wtf` was rewritten (which matters if 4 clients all write it on exit).

If you know of a per-instance config switch for your client, run again with `--args "<switch>"` and say what happened.

**Paste back:** full console output + any prompts/dialogs you saw.

## S9: in-game API checks (needs the addon)

In one client:
1. `/wbs info` and paste every `[WBS]` line. This answers whether `SetTexture(r,g,b,a)` raises an error, whether `SetColorTexture` exists, and which chat edit box globals exist.
2. `/wbs pattern`: you should see a small strip of coloured squares in the very top-left corner: row 1 red, green, blue, white, brown, dark-navy, tan, plus a flickering 8th square, then an identical row 2. Say whether **both rows** show solid colours. Row 1 uses `SetTexture(r,g,b,a)`; row 2 uses `WHITE8X8` + `SetVertexColor`.
3. `/wbs chat`, then: press Enter (open chat), Esc (close), `/` (open with slash), type and send a message, press `R` (reply, if you have a whisper target), click into the chat box with the mouse. Paste every `CHAT` line. Run it with your normal chat style. If you're curious, also try the other style (Interface → Social → Chat Style: Classic vs IM).

**Paste back:** all `[WBS]` lines + whether each pattern row rendered.

If you'd rather not install the addon, these one-liners answer most of it (each under 255 chars):
```
/run local t=UIParent:CreateTexture() print(pcall(t.SetTexture,t,1,0,0,1), t:GetTexture(), t.SetColorTexture)
/run print(ChatFrameEditBox, ChatFrame1EditBox, ChatEdit_GetActiveWindow, GetCVar("chatStyle"))
```

## S1: background PostMessage

1. Client A: `/wbs keys`. Optionally also do it as a binding test: `/wbs binds` instead.
2. `python s1_postmessage.py` and pick client A.
3. During the countdown, click another window (another client or Notepad).
4. Client A should print `KEY DOWN 1` / `KEY UP 1` three times (or `BIND fired: 1` with binds).

Repeat once with client A **minimized** and once **fully covered**, if you have time.
**Paste back:** console output + the `[WBS]` lines (or "nothing appeared") + the window state for each run.

## S2: modifiers

1. Client A: `/wbs binds`.
2. Open Notepad (method E asks you to hold Shift physically; Notepad catches that harmlessly).
3. `python s2_modifiers.py --focus-swap`. Click Notepad during the countdown and follow the prompts.
4. Run it again with `/wbs keys` instead of `/wbs binds` (Esc in client A afterwards to turn the logger off).

Marker keys tell the methods apart in chat: `F13`=0 baseline, `F14`=A Shift posted, `F15`=B Ctrl posted, `F16`=C Alt via WM_SYSKEYDOWN, `F17`=D SetKeyboardState, `F18`=E your physical Shift, `F19`=F focus swap + SendInput.
**Paste back:** console output + all `[WBS]` lines from both runs.

## S5: borderless restyle

Run twice: once at **character select**, once **in the world**.
```powershell
python s5_restyle.py                       # borderless, then top-left quadrant -> bottom-right -> top-left
python s5_restyle.py --restore --hwnd 0x…  # put it back
```
**Paste back:** console output plus what you **saw**: flicker, black frames, blurry/stretched UI, a freeze or reload (device reset), whether the UI rescaled to the new size.

## S6: DWM thumbnails

```powershell
python s6_dwm_thumbs.py                    # live thumbnails of all WoW windows
python s6_dwm_thumbs.py --title Notepad    # dry run without WoW
```
With the thumbnail window open, try: client unfocused; client fully covered; client minimized; client on monitor 2. For each, say whether the thumbnail is **live** (animation moving), **frozen**, or **black**.
**Paste back:** console output + your observations per state.

## S7: pixel capture

In **every** client: `/wbs pattern`. Then:
```powershell
python s7_capture.py --tag visible     # all clients visible, one focused
python s7_capture.py --tag occluded    # cover one or more clients with another window (say which)
python s7_capture.py --tag minimized   # minimize one or more clients (say which)
```
Each run captures with mss, dxcam, PrintWindow (`PW_RENDERFULLCONTENT`) and GDI BitBlt, compares against the expected colours, and checks freshness via the counter block.
**Paste back:** the printed table and `out/s7_<tag>/summary.json` for each tag. Zip `out/s7_*` if anything looks odd.

If `found` is 0 but the PNG clearly shows the pattern at a different size, the UI-scale maths is off. Run `/wbs pattern <your client's physical height>` (e.g. `/wbs pattern 1080`) and recapture.

## S8: hook latency

Clients not needed for the first two runs.
```powershell
python s8_hook_latency.py                     # injected F24 presses -> 4 dummy targets
python s8_hook_latency.py --busy-threads 2    # same with GIL contention (stand-in for a busy UI)
python s8_hook_latency.py --target wow        # optional: post F24 to the real clients (check F24 is unbound)
python s8_hook_latency.py --real --seconds 20 # optional: your real typing
```
Target: `hook -> last post` p99 under 10 ms, and `missing: 0`.
**Paste back:** each printed table.
