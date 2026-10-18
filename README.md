# WoWBox

A multibox controller for the World of Warcraft 3.3.5a (WotLK) client on Windows. One person plays 3–4 characters at once:

- WoWBox launches or attaches the clients and tiles them on your monitor.
- It sends your key presses to the characters you choose, per key.
- It shows each character's health, mana, target and state live.
- It can make specific characters do specific things: macros, team commands, follow, and rules that react to game state or chat.

It works from outside the game. WoWBox never injects code into WoW or modifies it. It sends keys as ordinary window messages and reads a small strip of coloured pixels that its companion addon, **BoxBridge**, draws in the corner of each client.

> **Server rules come first.** Many private servers restrict multiboxing tools, one-key-many-actions macros, or automation that reacts to the game without you. WoWBox gives you the tools. Whether you may use them is your server's call.

**The example team.** The examples in this README and in `docs/` use one real team. Put your own names in their place:

| Slot | Character | Class | Role in the examples |
|---|---|---|---|
| 1 | **Tinkerdawn** | Paladin | Leader and tank. The others assist and follow Tinkerdawn. |
| 2 | **Gnope** | Warlock (a Necromancer on the EQWOW server this was built for) | Damage and pet. |
| 3 | **Kunth** | Shaman | Healer and buffer: the heal-on-whisper rules run on Kunth. |

Class targets such as `class:WARLOCK` and `class:SHAMAN` work for any team.

---

## Contents

In this file:

1. [What it does](#1-what-it-does)
2. [How it works](#2-how-it-works)
3. [Quick start](#3-quick-start)
4. [Installing](#4-installing)

In `docs/`:

- [The BoxBridge addon and `/bb` commands](docs/addon.md)
- [Running WoWBox](docs/running.md)
- [Your team and targets](docs/team.md)
- [Key broadcasting and in-game team commands](docs/broadcasting.md)
- [Follow, stacking and movement mirroring](docs/follow.md)
- [Macros](docs/macros.md)
- [Automation](docs/automation.md)
  - [Automation recipes](docs/automation-recipes.md): heal/buff bot, group ports
- [WotLK slash commands for macros and automation](docs/slash-commands.md)
- [Layouts and configuration reference](docs/configuration.md)
- [Building and development](docs/development.md)
- [Troubleshooting and known limits](docs/troubleshooting.md)

---

## 1. What it does

| Feature | In short |
|---|---|
| **Launch and attach** | Starts missing clients or adopts ones already running. Retitles each window `WoW1 - Tinkerdawn`. |
| **Layouts** | Borderless tiling (auto, 2×2, 1×3), a main window with live thumbnails, or tabs. |
| **Key broadcasting** | Per-key routes: `1-9` to everyone, `F1` to followers, `Ctrl+1` only to the healer. Pauses automatically while you type in chat. |
| **Live status** | HP, power, target, combat, casting, typing, follow state, character name and class for every client, via BoxBridge. |
| **Character-aware** | Characters are recognised by name, so the warlock lands in its slot whatever order you start the clients in. Targets can be a name, `class:WARLOCK` or `role:healer`. |
| **Team commands** | Type `/bb all reload` in any client and every client does `/reload`. Also `/bb others …` and `/bb to Gnope …`. |
| **Follow mode** | One key puts everyone on `/follow`. WoWBox re-follows anyone whose follow breaks, but not mid-cast or mid-fight. |
| **Stacking** | One key walks every follower onto your exact spot and facing (typically within 0.1 yd and 1°), using positions from BoxBridge. |
| **Movement mirroring** | Optionally copies your W/A/S/D/Q/E/Space to the followers, so they move in lockstep with you. They re-stack every time you stop. |
| **Macros** | A hotkey runs a sequence: type `/assist Tinkerdawn` on Gnope, then press 1–9 and 0 a second apart. |
| **Automation** | Rules triggered by state ("healer mana < 40 % and out of combat → drink"), by timers, or by chat ("Tinkerdawn whispers *heal* → Kunth heals him"). Off and in dry run by default. |
| **Safety switches** | Broadcast toggle, release-all-keys panic button, automation kill switch, chat guard. |

## 2. How it works

```
 your keyboard ──► low-level keyboard hook (WoWBox)
                      │  route table: which clients get this key?
                      ▼
                 PostMessage WM_KEYDOWN/UP/CHAR ──► WoW windows (in the background)

 WoW client ──► BoxBridge addon draws 28 coloured blocks top-left
                      │  (state: HP, power, target, flags, name, class — or a chat line / team command)
                      ▼
                 WoWBox captures those pixels ~10×/s ──► decoder ──► Clients tab, follow, automation
```

- **Sending keys.** A Windows low-level keyboard hook sees your physical keys. For each key, the route table picks the target clients. WoWBox posts the key to them as window messages, so they don't need focus. A watchdog re-installs the hook if Windows ever drops it.
- **Typing chat lines** (`/assist X`, `/follow X`, `/reload`): WoWBox posts Enter, then the text as characters, then Enter. The text arrives as typed characters, so it never sets off a keybind.
- **The pixel bridge.** BoxBridge draws a strip of 28 small squares in the top-left corner. Each square carries 2 bytes in its red and green values. The blue value alternates, so WoWBox can find every square however the game stretches its picture. A checksum rejects torn or covered frames. Besides the normal status frame, the same strip briefly carries chat lines and `/bb` team commands.
- **Capturing pixels.** WoWBox grabs the corner of each window with `mss` when the window is visible on screen, and with `PrintWindow` when it's covered. dxcam is an optional third method.
- **Nothing inside the game process.** No DLLs, no memory writes. An optional, read-only memory reader exists, but it's off by default and ships without addresses.

## 3. Quick start

```powershell
cd WOWBOX                                              # wherever you cloned or unpacked it
powershell -ExecutionPolicy Bypass -File setup.ps1     # once: Python 3.12 venv + dependencies + config\team.yaml
```

1. Copy `addon\BoxBridge` into `<WoW>\Interface\AddOns\` and enable it for every character.
2. Edit `config\team.yaml`: set `client.exe`, your slots, and your characters.
3. Start your clients and log in, then run `.\wowbox.bat`. Or build and use `dist\WoWBox\wowbox.exe` ([Building the standalone exe](docs/development.md)).
4. Check the **Clients** tab: each row should show its character name and live HP.
5. Try it:
   - Press `1` in the leader's window: everyone presses 1.
   - Press **F10**: everyone follows the leader.
   - Type `/bb all reload` in any client: every client reloads its UI.

## 4. Installing

**Requirements:**
- Windows 10/11 and a 3.3.5a client.
- Python 3.12. If you have [`uv`](https://docs.astral.sh/uv/), `setup.ps1` uses it and downloads 3.12 itself. Otherwise it needs `py -3.12` (python.org installer).

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

`setup.ps1` does three things:
- creates `.venv`;
- installs WoWBox with its dependencies (PySide6, pydantic, PyYAML, numpy, mss, plus dxcam and OpenCV for the optional capture method) and the dev tools (pytest, Pillow, lupa, pyinstaller);
- copies `config\team.example.yaml` to `config\team.yaml` if you don't have one yet.

**Privilege level matters.** WoW and WoWBox must run at the same level: both normal, or both as administrator. Windows blocks messages from a lower-privileged process to a higher one. When this happens, the log says *access denied*.

---

## License

MIT. See [LICENSE](LICENSE).
