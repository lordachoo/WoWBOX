# WoWBox — Multibox Controller for WotLK 3.3.5a

Project plan and build outline, written to hand to a Claude Code session.

---

## 1. Goal

A single Windows application that:

1. Launches 3–4 copies of the WotLK 3.3.5a client.
2. Arranges them in a grid (2x2 / 1x3), a "main + thumbnails" view, or tabs.
3. Routes keyboard input from the active window to some or all clients according to per-key rules, and pauses automatically while you type in chat.
4. Gives a control panel with per-client status, the leader, and the broadcast state.
5. Reads game state from each client through a small addon that encodes state as pixels (the "pixel bridge").
6. Runs a rules or automation engine (timers, sequences, state-driven actions) on top of the router.

Designed to sit alongside **Jamba 0.7b** in-game, which keeps handling follow, quest sync, vendoring and invites.

## 3. Tech stack (decided: Python)

| Area | Choice |
|---|---|
| Language | Python 3.12+ |
| UI | PySide6 (Qt 6) |
| Win32 | `pywin32` + `ctypes` where pywin32 lacks a binding |
| Config | YAML + `pydantic` models for validation |
| Screen capture | `dxcam` (Desktop Duplication) with `mss` as fallback. **Spike required**, see §5 |
| Hotkeys / hook | `WH_KEYBOARD_LL` through ctypes, on its own thread with a message pump |
| Logging | stdlib `logging` + rotating file handler |
| Tests | `pytest`; all Win32 calls go behind an interface so the logic is testable on any OS |
| Packaging | PyInstaller → single `wowbox.exe` |

If Python's latency or hook reliability proves bad in M0, fall back to C#/.NET 8 + WPF. The architecture below maps one-to-one.

## 4. Repository layout

```
wowbox/
├── pyproject.toml
├── README.md
├── config/
│   └── team.example.yaml
├── addon/
│   └── BoxBridge/                 # WoW 3.3.5a addon (pixel bridge)
│       ├── BoxBridge.toc
│       └── BoxBridge.lua
├── src/wowbox/
│   ├── __main__.py                # entry point
│   ├── config.py                  # pydantic models, YAML load/save
│   ├── platform/                  # ALL Win32 lives here
│   │   ├── base.py                # abstract interfaces (WindowAPI, InputAPI, CaptureAPI)
│   │   ├── win32_windows.py       # find/position/restyle windows, DWM thumbnails
│   │   ├── win32_input.py         # LL keyboard hook, PostMessage sender
│   │   ├── win32_capture.py       # dxcam/mss/PrintWindow capture
│   │   └── fake.py                # fakes for tests
│   ├── launcher.py                # spawn clients, map PID -> HWND -> slot
│   ├── layout/
│   │   ├── grid.py
│   │   ├── main_thumbs.py         # DWM thumbnail host window
│   │   └── tabs.py
│   ├── router/
│   │   ├── rules.py               # per-key routing rules
│   │   ├── router.py              # hook event -> targets -> send
│   │   └── chatguard.py           # chat-aware broadcast pause
│   ├── bridge/
│   │   ├── decoder.py             # pixels -> ClientState
│   │   └── poller.py              # capture loop per client
│   ├── automation/
│   │   ├── engine.py              # rules evaluation, timers, sequences
│   │   └── actions.py
│   ├── memread/                   # Phase 6, optional, read-only
│   │   └── reader.py
│   └── ui/
│       ├── main_window.py         # control panel
│       ├── overlay.py             # optional always-on-top status strip
│       └── tray.py
└── tests/
```

## 5. Phase 0 — Spikes (verify on the real machine before building)

These are assumptions I'm **not certain of**. Each spike is a throwaway script whose output gets pasted back before the real code is written.

| # | Question | Spike |
|---|---|---|
| S1 | Do background 3.3.5a windows accept `PostMessage(WM_KEYDOWN/WM_KEYUP)` with a correct `lParam` (scan code, repeat count)? | Post `1` to a background client and confirm the action fires |
| S2 | **Modifiers:** does the client see Shift/Ctrl/Alt when they're posted to a background window, or does it read the real keyboard state? | Post Shift-down, `1`, Shift-up and see which action bar fires. If posting fails, test alternatives (`SendInput` with a brief focus swap, or avoid modifier binds for broadcast keys) |
| S3 | Exact `Config.wtf` CVars for windowed mode and resolution. I believe they're `gxWindow`, `gxResolution`, and `gxMaximize` | Paste your actual `WTF/Config.wtf` |
| S4 | Can we start multiple instances directly, and is there a per-instance config switch? | Launch 4x `Wow.exe` and note any prompts or conflicts |
| S5 | Does stripping the window frame (`WS_CAPTION`/`WS_THICKFRAME` off) plus `SetWindowPos` work cleanly on the D3D9 client without a device reset or black screen? | Run it on one client |
| S6 | Do `DwmRegisterThumbnail` live thumbnails render the client correctly (not black) when it isn't focused? | Minimal Qt window hosting one thumbnail |
| S7 | **Capture:** can we read pixels from a client that's visible but not focused (dxcam/mss region), and from one that's **occluded or hidden** (`PrintWindow` with `PW_RENDERFULLCONTENT`)? | Capture the top-left 64x8 px of each client and save PNGs |
| S8 | Hook latency: does keypress-to-all-4-clients stay under ~10 ms in Python? | Timestamp in the hook and at each post call |
| S9 | In 3.3.5a, does `Texture:SetTexture(r, g, b, a)` give a solid color? (`SetColorTexture` is 7.x+.) What's the chat edit box frame name: `ChatFrameEditBox` or `ChatFrame1EditBox`? | `/run` tests in game |

**Gate:** M2 and M4 designs get adjusted from S1, S2, S7 and S9 results before implementation.

## 6. Milestones and what I'll build

### M1 — Launcher + Layout
- `team.yaml` defines the client path, slots (1–4), the character label for each slot, the role (tank, healer, dps), and the leader.
- The launcher spawns N clients, waits for each window, and maps PID → HWND → slot. Windows are renamed `WoW1`..`WoW4` for clarity.
- Optionally writes the windowed-mode CVars per S3 results, with a backup of `Config.wtf` first.
- Layouts:
  - **Grid:** 2x2 or 1x3 per monitor, borderless and pixel-exact.
  - **Main + thumbs:** the leader is large; the others are live DWM thumbnails in a strip. Clicking a thumbnail swaps it with the main window.
  - **Tabs:** the active client fills the area and a tab bar switches between them.
- Hotkeys to cycle layouts and swap the main client.
- Multi-monitor aware (thumbnails can go on monitor 2).

### M2 — Input Router
- A low-level keyboard hook, active only while a WoW client or the WoWBox UI has focus.
- Routing rules from YAML:
  ```yaml
  routes:
    - keys: ["1-9", "0", "-", "="]   # rotation bar
      to: all
    - keys: ["F1"]                   # follow macro on followers
      to: followers
    - keys: ["W", "A", "S", "D", "Q", "E", "Space"]
      to: active                     # leader movement only (default)
    - keys: ["Ctrl+1"]
      to: [3]                        # healer only
  ```
- Targets: `all`, `active`, `followers` (everyone except the active window), `leader`, or explicit slot lists.
- **ChatGuard:** Enter or `/` pauses broadcasting until the next Enter or Escape. Phase 4 adds the real chat-box state from the bridge so it can't drift out of sync.
- A global broadcast toggle key, with on-screen and tray indicators.
- Key-up events are mirrored to the same targets as key-down, to prevent stuck keys.
- An emergency "release all keys" hotkey.

### M3 — Control Panel UI
- One row per client: slot, character label, role, PID, alive/responding, and broadcast-target checkboxes.
- Leader selector and layout selector.
- Live route table editor that saves back to YAML.
- Optional always-on-top overlay strip showing the broadcast state, the active client, and ChatGuard status.
- Tray icon with quick toggles.
- Log viewer pane.

### M4 — Pixel Bridge
- **Addon `BoxBridge` (Lua, 3.3.5a API):** draws a strip of solid-color blocks in the top-left corner. Each block's RGB encodes one value. Proposed frame layout (finalize after S9):

  | Block | R | G | B |
  |---|---|---|---|
  | 0 | magic header | magic | frame counter |
  | 1 | health % | power % | power type |
  | 2 | target health % | target exists/hostile/dead flags | combat flag |
  | 3 | chat edit box open | casting / channeling flag | mounted / dead / ghost |
  | 4 | party member 1–2 health % | | |
  | 5+ | reserved (buffs missing, cooldowns, etc.) | | |

  - Updates via `OnUpdate`, throttled (about 10–20 Hz).
  - Toggle with `/boxbridge`. Block size is configurable for UI scale.
- **Decoder** in the app: capture the region, validate the header and frame counter, decode into a `ClientState` dataclass, and flag "stale" if the frame counter stops changing.
- The capture method is chosen per S7: visible-region capture for the grid layout, `PrintWindow` for hidden or thumbnailed clients if it works.
- The control panel shows live HP, mana, target and combat state per client.
- ChatGuard switches to using bridge block 3 as its main source.

### M5 — Automation Engine
- Rules in YAML, evaluated on each bridge tick:
  ```yaml
  automation:
    - name: healer-drinks-out-of-combat
      when: { slot: 3, combat: false, power_pct_lt: 40 }
      do:   { send: "Ctrl+9", to: [3] }
      cooldown: 20s
    - name: rebuff-timer
      every: 25m
      do:   { sequence: [{ send: "F5", to: all }, { wait: 1.5s }, { send: "F6", to: all }] }
  ```
- Primitives: `send`, `sequence`, `wait`, conditions on any `ClientState` field, cooldowns, `every` timers, and enable/disable per rule.
- **Safety:**
  - Global automation kill switch hotkey.
  - Automation auto-pauses when a client's bridge goes stale or any window loses its HWND.
  - Dry-run mode logs what *would* be sent without sending it.
  - Everything is visible in the UI log.

### M6 — Read-Only Memory Reader (optional, off by default)
- Read player and target state with `ReadProcessMemory` as a cross-check for, or alternative to, the pixel bridge.
- Offsets are config-driven, never hard-coded, and sourced and verified by you.
- **Read-only, no injection, no evasion.** Disabled unless `memread.enabled: true`.

### M7 — Packaging & Docs
- PyInstaller build to `wowbox.exe`.
- README: install, addon install, first-run walkthrough, sample `team.yaml`, troubleshooting (stuck keys, black thumbnails, capture failures).

## 7. Example `team.yaml`

```yaml
client:
  exe: "C:/Games/WoW335/Wow.exe"
  set_windowed_cvars: true        # per S3 results
team:
  leader: 1
  slots:
    1: { label: "Tankadin",  role: tank }
    2: { label: "Shammy1",   role: dps }
    3: { label: "Shammy2",   role: healer }
    4: { label: "Mage",      role: dps }
layout:
  default: grid_2x2
  monitor: 1
  thumbs_monitor: 2
broadcast:
  toggle_key: "ScrollLock"
  release_all_key: "Ctrl+Alt+Pause"
  chatguard: true
routes: []        # see M2
automation: []    # see M5
```

## 8. Testing strategy

- All Win32 access goes through `platform/base.py` interfaces. Router, rules, ChatGuard, decoder and automation logic get unit tests against `platform/fake.py` on any OS.
- The decoder is tested against PNG fixtures captured during S7 and M4.
- A manual test checklist for each milestone runs on the real 4-client setup.
- **No parsing code gets written against guessed formats.** Real captures, `Config.wtf` contents and in-game `/run` output are collected first.

## 9. Risks / unknowns

| Risk | Mitigation |
|---|---|
| Modifiers don't work through `PostMessage` (S2) | Use modifier-free broadcast binds, or a focus-swap approach for modifier keys |
| Background or occluded capture returns black (S7) | Grid layout keeps everything visible; for thumbnails mode, briefly read from the visible thumbnail region |
| Python hook latency or dropped hooks (Windows silently unhooks slow LL hooks) | Keep the hook callback minimal and queue to a worker thread; fall back to C# if needed |
| Server-side detection or admin bans | Your call. The tool doesn't hide itself |
| D3D9 window restyle causes device reset | Restyle before login or at the character screen, and test in S5 |

## 10. Kickoff prompt for the code session

> Build WoWBox per `wowbox-project-plan.md`. Start with Phase 0: write each spike (S1–S9) as a standalone script under `spikes/` with clear instructions on what to run and what output to paste back. Don't build M1+ until I've reported spike results. Python 3.12, PySide6, pywin32. All Win32 calls behind `platform/` interfaces. Respect the boundaries in §2.
