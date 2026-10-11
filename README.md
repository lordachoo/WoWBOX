# WoWBox

A multibox controller for the World of Warcraft 3.3.5a (WotLK) client on Windows. One person plays 3–4 characters at once:

- WoWBox launches or attaches the clients and tiles them on your monitor.
- It sends your key presses to the characters you choose, per key.
- It shows each character's health, mana, target and state live.
- It can make specific characters do specific things: macros, team commands, follow, and rules that react to game state or chat.

It works from outside the game. WoWBox never injects code into WoW or modifies it. It sends keys as ordinary window messages and reads a small strip of coloured pixels that its companion addon, **BoxBridge**, draws in the corner of each client.

> **Server rules come first.** Many private servers restrict multiboxing tools, one-key-many-actions macros, or automation that reacts to the game without you. WoWBox gives you the tools. Whether you may use them is your server's call.

**The example team.** The examples in this README use one real team. Put your own names in their place:

| Slot | Character | Class | Role in the examples |
|---|---|---|---|
| 1 | **Tinkerdawn** | Paladin | Leader and tank. The others assist and follow Tinkerdawn. |
| 2 | **Gnope** | Warlock (a Necromancer on the EQWOW server this was built for) | Damage and pet. |
| 3 | **Kunth** | Shaman | Healer and buffer: the heal-on-whisper rules run on Kunth. |

Class targets such as `class:WARLOCK` and `class:SHAMAN` work for any team.

---

## Contents

1. [What it does](#1-what-it-does)
2. [How it works](#2-how-it-works)
3. [Quick start](#3-quick-start)
4. [Installing](#4-installing)
5. [The BoxBridge addon and `/bb` commands](#5-the-boxbridge-addon-and-bb-commands)
6. [Running WoWBox](#6-running-wowbox)
7. [Your team: slots, characters, leader](#7-your-team-slots-characters-leader)
8. [Targets: choosing which characters](#8-targets-choosing-which-characters)
9. [Key broadcasting and routes](#9-key-broadcasting-and-routes)
10. [Team commands from inside the game](#10-team-commands-from-inside-the-game)
11. [Follow, stacking and movement mirroring](#11-follow-stacking-and-movement-mirroring)
12. [Macros](#12-macros)
13. [Automation](#13-automation)
14. [WotLK slash commands for macros and automation](#14-wotlk-slash-commands-for-macros-and-automation)
15. [Layouts](#15-layouts)
16. [Configuration reference](#16-configuration-reference)
17. [Building the standalone exe](#17-building-the-standalone-exe)
18. [Project layout and development](#18-project-layout-and-development)
19. [Troubleshooting](#19-troubleshooting)
20. [Known limits](#20-known-limits)

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
| **Safety switches** | Broadcast toggle, release-all-keys panic button, automation kill switch, chat guard, an always-on-top status overlay. |

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
3. Start your clients and log in, then run `.\wowbox.bat`. Or build and use `dist\WoWBox\wowbox.exe` (section 17).
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

## 5. The BoxBridge addon and `/bb` commands

Copy `addon\BoxBridge` to `<WoW>\Interface\AddOns\BoxBridge` and enable it on the character screen for **every** character. After updating the addon, `/reload` every client.

A thin strip of coloured squares appears in the top-left corner. That's the bridge: leave it visible, and don't cover it with other UI. WoWBox works without the bridge, but then you lose:
- live status;
- character recognition;
- chat-aware pausing;
- follow mode's re-follow;
- `/bb` team commands;
- state and chat automation.

All in-game commands (`/bb` is short for `/boxbridge`):

| Command | Does |
|---|---|
| `/bb all <command>` | Every client runs `<command>`, this one included. The leading `/` is optional. `/bb all reload` |
| `/bb cmd <command>` | Same as `all`. |
| `/bb others <command>` | Every client except this one. `/bb others follow Tinkerdawn` |
| `/bb to <who> <command>` | Only `<who>`: a character name, `class:X`, `role:X`, `leader`, or a slot number. `/bb to Gnope assist Tinkerdawn` |
| `/bb stack` | Everyone walks onto *your* spot and facing (the client you typed it in). |
| `/bb follow`, `/bb stay` | Follow mode on / off. |
| `/bb mirror` | Movement mirroring on / off. |
| `/bb` | Turn the strip on or off. |
| `/bb status` | Show the current settings. |
| `/bb size <px>` | Block size, 2–32 (default 8). Bigger copes better with small windows. Also set `bridge.block_size` in `team.yaml` to match. |
| `/bb height <px>` | Override the render height used for pixel snapping. Rarely needed. |
| `/bb hz <n>` | Strip update rate, 1–60 (default 15). |
| `/bb chat` | Stop or resume passing chat lines to WoWBox for this client. |
| `/bb test <Name> <text>` | Pretend `<Name>` whispered `<text>`, for testing chat automation. |

Settings are saved per character.

## 6. Running WoWBox

```powershell
.\wowbox.bat                       # from source
dist\WoWBox\wowbox.exe             # standalone build (section 17)
```

| Option | |
|---|---|
| `--config PATH` | use another config file (default: `config\team.yaml`) |
| `--no-attach` | don't touch running clients on start. Press **Attach running** when ready. |
| `--check-config` | validate the config and exit |
| `--log-level DEBUG` | more detail in the log |

On start, WoWBox attaches to every running `Wow.exe` window. It puts each window in its slot (by character name once the bridge reports it), renames the windows, removes their borders and applies the default layout.

### The window

**Top row:**

| Control | |
|---|---|
| **Broadcast ON/OFF** | Master switch for key broadcasting (ScrollLock). Turning it off releases every key. |
| **Follow ON/OFF** | Follow mode (F10 on, F11 off). Section 11. |
| **Stack** | Everyone walks onto the spot and facing of the window you're in (F12). Press again to cancel. |
| **Mirror movement** | Copy movement keys to followers (Ctrl+Alt+M). |
| **Automation ON/OFF**, **Dry run** | Automation always starts OFF, in dry run. |
| **Overlay** | Small always-on-top status line: broadcast, active character, follow, automation. |
| **Release keys** | Key-up for everything on every client (Ctrl+Alt+Pause). |

**Second row:** Launch missing, Attach running, Arrange, the Layout and Leader pickers, Restore frames (puts window borders back), Reload config, Save config.

- **Reload config** re-reads `team.yaml` (and `macros.yaml`) after you've edited them by hand.
- **Save config** writes what you changed *in the app* (the route table, the leader and the slots' characters) into `team.yaml`, **in place**. Only those lines change. The rest of the file, comments included, stays as you wrote it. If nothing changed, nothing is written. The previous version is kept as `team.yaml.bak`.

**Tabs:**

| Tab | Shows |
|---|---|
| **Clients** | One row per slot: character and class, label, role, PID, window, status, Broadcast checkbox (untick to leave a client out of broadcasts), HP, power, target, state flags (`combat casting TYPING following …`), and bridge health. Buttons: **Move up/down** swaps slots, **Remember characters** pins the current characters into `team.yaml`. |
| **Routes** | The route table. A change takes effect as soon as you finish editing a cell. A bad row turns red and isn't used. **Save config** keeps the changes after a restart. |
| **Macros** | Your macros with their hotkeys, whether they're running, and their steps. **New / Edit / Duplicate / Delete** open the macro editor (double-click edits too). Run/Stop, Stop all. |
| **Automation** | Every rule with its trigger and actions, plus live columns: **Status** (waiting, cooldown 8s, running step 2/4, disabled, automation OFF), **Next** (timers get a countdown bar, e.g. *in 12m 05s*; state rules say whether the condition is true now and why not; chat rules wait for a line), **Last fired** and **Fired** (count). **New / Edit / Duplicate / Delete** open the rule editor (double-click edits too). The *On* box enables or disables a rule, and the change is saved at once. |
| **Log** | Everything WoWBox does: keys typed, rules fired, chat lines seen, `/bb` commands. The same goes to `logs\wowbox.log`. |

A tray icon offers the same switches. Its dot is green while broadcasting and red while not, with a yellow ring while automation is on. Closing the window quits WoWBox.

### Hotkeys

Hotkeys work while a WoW client or WoWBox has focus. Change them under `hotkeys:`.

| Default | Action |
|---|---|
| ScrollLock | broadcast on/off |
| Ctrl+Alt+Pause | release all keys (stuck-key panic button) |
| F10 | **follow**: followers target the leader and `/follow`; follow mode ON |
| F11 | **stay**: followers stop; follow mode OFF |
| F12 | **stack**: everyone walks onto the spot and facing of the window you're in. WoWBox takes F12 over from any in-game F12 binding; change `hotkeys.stack` if you need that binding. |
| Ctrl+Alt+M | movement mirroring on/off |
| Ctrl+Alt+L | next layout |
| Ctrl+Alt+N | next client becomes main (main_thumbs / tabs) |
| Ctrl+Alt+1..4 | bring that client to the front |
| Ctrl+Alt+K | automation kill switch |
| *(yours)* | each macro's `key:` |

Hotkeys and macro keys are swallowed: the game never sees them. Macro keys pass through as normal text while your chat box is open.

## 7. Your team: slots, characters, leader

```yaml
team:
  leader: 1
  slots:
    1: { label: "Tank",   role: tank,   character: Tinkerdawn }
    2: { label: "Lock",   role: dps,    character: Gnope }
    3: { label: "Shaman", role: healer, character: Kunth }
```

- **Slots** (1–8) are positions. `WoW1` is slot 1, and layouts place windows by slot.
- **Characters.** BoxBridge reports each client's character name and class. If a slot names a `character:`, the window showing that character is moved into the slot, whatever order the clients started in. To set this up without typing: arrange the rows with **Move up/down**, then press **Remember characters**.
- **Leader.** Set it in the toolbar or with `team.leader`. It's who `leader` targets point at and who followers follow.
- **Labels and roles.** Labels are display names. Roles (`tank` / `healer` / `dps`) can be used as targets (`role:healer`).

## 8. Targets: choosing which characters

Routes, macros, automation, `/bb to` and follow settings all use the same target language:

| Target | Means |
|---|---|
| `all` | every client with Broadcast ticked, plus the window you're in |
| `active` | only the window you're in |
| `followers` | everyone except the window you're in |
| `leader` | the leader slot |
| `none` | nobody (swallow the key) |
| `[2, 3]` or `2` | those slots |
| `Gnope` or `char:Gnope` | that character (any case) |
| `class:WARLOCK` | every client playing that class (WoW's class token: WARRIOR, PALADIN, HUNTER, ROGUE, PRIEST, DEATHKNIGHT, SHAMAN, MAGE, WARLOCK, DRUID) |
| `role:healer` | every slot with that role |
| `not:<target>` | everyone except that target: `not:leader`, `not:Gnope`, `not:class:WARLOCK` |

Names and classes come from the bridge. Until a client's bridge has reported, a name target can't find it.

## 9. Key broadcasting and routes

**Changing a route.** In the **Routes** tab, edit a cell and press Enter. It takes effect at once (the line under the table says *Applied*). Click **Save config** to keep it after a restart. You can also edit `routes:` in `team.yaml` and press **Reload config**.

**Turning a key's broadcasting off.** Set its *To* to **`active`**, so the key only goes to the window you're in, or remove the route. Don't use `none` for this: `none` means *nobody* gets the key, not even the window you're in, so the key stops working altogether. ScrollLock switches all broadcasting off at once.

```yaml
routes:
  - keys: ["1-9", "0", "-", "="]   # ranges and lists
    to: all
    note: rotation bar
  - keys: ["F1"]
    to: followers
  - keys: ["W", "A", "S", "D", "Q", "E", "Space"]
    to: active                     # movement stays in the window you're in
  - keys: ["Ctrl+1"]
    to: [3]
```

- **First match wins.** Chords match exactly: `Shift+1` is not `1`, so list it separately.
- **Unlisted keys** go only to the window you're in, just as without WoWBox.
- **Leaving out your own window.** If a route doesn't include the window you're in (`followers`, `[3]`), that window doesn't get the key either.
- **Key-ups follow key-downs.** A key-up always goes to the clients that got the key-down, so a key can't stick when focus changes mid-press. Holding a key repeats it to the same clients.
- **Key names:** `1`–`0`, `A`–`Z`, `F1`–`F24`, `Numpad0`–`9`, `Space`, `Enter`, `Esc`, `Tab`, `Pause`, `ScrollLock`, `- = / [ ] ; ' , . \``. Chords look like `Ctrl+1`, `Alt+F2` or `Ctrl+Alt+Pause`. Ranges look like `1-9`, `F1-F4` or `Ctrl+1-3`.

**Chat guard.** Typing in chat must not reach the other clients, so broadcasting pauses while you type. Pressing Enter or `/` pauses it, and Enter or Esc resumes it. With the bridge running, WoWBox also sees when any edit box has focus, including mail, the auction house and whisper replies, and corrects itself if the key guess drifts.

**Modifiers** (`broadcast.modifier_mode`). Background clients don't always honour posted Shift/Ctrl/Alt.
- `post` (default): sends modifier key messages around the key.
- `focus_swap`: briefly focuses each target and types the chord for real. Reliable, but the windows flicker.
- `strip`: sends the bare key.

If modifier binds don't fire on followers, try `focus_swap`, or use modifier-free binds for broadcast keys.

## 10. Team commands from inside the game

Type a `/bb` command in any client's chat, and WoWBox types that command into the clients you named:

```
/bb all reload                    → every client: /reload
/bb all /follow Tinkerdawn        → every client follows Tinkerdawn (Tinkerdawn just fails harmlessly)
/bb others follow Tinkerdawn      → everyone except the client you typed it in
/bb to Gnope assist Tinkerdawn    → only Gnope: /assist Tinkerdawn
/bb to class:SHAMAN cast Ghost Wolf
/bb to 3 sit
/bb to leader say ready
```

- **Any slash command** works: `/reload`, `/follow`, `/assist`, `/target`, `/cast`, `/use`, `/sit`, `/dance`, `/say`, `/console …`, and slash commands from other addons. The `/` is optional.
- **Who:** `all` and `others` reach every attached client, even ones with Broadcast unticked. `to <who>` takes any target from section 8 except lists. Targets are seen from the client you typed in, so `/bb to followers …` means everyone but you.
- **How it gets there:** BoxBridge shows the command on its strip for half a second, WoWBox reads it, and types `Enter, command, Enter` into each target client. That takes about a quarter to half a second. The client you typed the command in gets it last, about 0.35 s after its chat box has closed, so your own Enter key can't get mixed into it.
- **Length limit:** a command can be up to 27 bytes, slash included. That covers `/assist Tinkerdawn` and `/cast Shadow Bolt`. For longer things, use a WoWBox macro (section 12) or an in-game macro.
- **Protected spells** (`/cast`, `/target`) work, because they are really typed into the chat box, just as if you typed them.
- **Turning it off:** set `bridge.commands: false` to ignore all `/bb` team commands.

## 11. Follow, stacking and movement mirroring

WoW's `/follow` breaks constantly: a cast, a mount, a door, a jump, a loading screen. Follow mode keeps followers on it.

- **F10, or the Follow button.** Every follower types `/target Tinkerdawn` and `/follow Tinkerdawn`, where Tinkerdawn is the leader's character. Follow mode turns ON.
- **While ON**, BoxBridge reports who is actually following (`following` in the State column). A follower that has been off follow for `refollow_delay` (1 s) gets `/follow Tinkerdawn` again. That command doesn't retarget, so their target stays. WoWBox holds back:
  - while that follower casts, channels, types or is in combat (with `refollow_in_combat: false`), so casters stand and fight, then catch up;
  - while you press movement keys, and for `refollow_delay` after;
  - for the window you're in, because you're steering that character yourself.

  If re-following keeps failing (leader out of range, say), the waits grow: 2, 4, 8 … up to 15 s.
- **F11, stay.** Taps `S` on the followers, which breaks their follow, and turns follow mode OFF.
- **Ctrl+Alt+M, mirror movement.** Your movement keys (`follow.movement_keys`: W A S D Q E Space) also go to the followers. They start, stop, strafe, turn and jump the moment you do. When you stop, they restack on you (see Stacking below). If stacking is off, they `/follow` again instead.
  - **Stack first.** Mirrored keys only give the same path if everyone starts stacked and faces the same way.
  - **Turn with A/D.** Mouse turning (right-drag) isn't copied.
  - **Obstacles still split them.** Anyone who hits a corner differently drifts, and the next stop fixes it.
  - **Only followers who are with you get your keys** (`follow.mirror_guard`, on by default). A follower is left out while it's **in another zone** or **more than `mirror_max_distance_yards` (40) away** from the character you're steering. Your characters can be spread around the world, and mirroring only moves the ones standing with you. The Log tab says when someone is left out and when they're back. The Position column shows `[in another zone]` or `[85 yd away]` next to anyone who isn't with the leader.
    - The zone check always works: the addon reports the zone's map ID, even where there are no coordinates. The yard check needs the zone's scale, which WoWBox measures while stacking there. It only uses that scale once two measurements agree, so one bad reading (a blocked step, your own keys mixed in) can't make a character standing next to you look far away. Until then, only the zone is checked. The scale is re-measured each time WoWBox starts.
  - **Test first.** Some clients don't take held movement keys while in the background. Try a short walk.
- **Using an in-game macro instead.** Set `follow.start_key` / `follow.refollow_key` to a key bound to an in-game `/follow` macro, and WoWBox presses that key instead of typing.

### Stacking

Mirrored movement only stays together if everyone starts on the same spot, facing the same way. Even 5° of difference becomes 2 yards after a 20-yard run. **Stack (F12, the Stack button, or `/bb stack`)** puts them there:

1. BoxBridge reports every character's map position, facing and frame rate (the **Position** column in the Clients tab).
2. For each follower, WoWBox:
   - turns it with A/D to roughly face the way the anchor faces;
   - moves it without turning: W/S along its facing, Q/E sideways;
   - after every move, waits for a fresh reading and corrects again;
   - finally turns it to the anchor's exact facing (turning on the spot doesn't move it).
3. It usually finishes in about 3 seconds. All followers stack at the same time.

More details:
- **No speeds are guessed.** The first move of each kind is a short probe. From then on, WoWBox measures how fast each key moves or turns the character on this map and uses that.
- **The anchor** is the window you're in when you press F12, or the client where you typed `/bb stack`. If you press F12 in WoWBox itself, the leader is the anchor.
- **Mirroring plus stacking is lockstep.** With mirroring on, the followers restack every time you stop moving (`stack.on_stop`). This replaces `/follow` while mirroring, because follow would pull them behind you. Press your movement keys and they move with you. Stop, and any drift is corrected within a second or two.
- **You can always take over.** Pressing a movement key yourself cancels a running stack. So does pressing F12 again.
- **Only nearby followers are stacked.** A follower in another zone, or more than `stack.max_distance_yards` (40) away, is skipped at once ("in another zone" / "too far away … use follow") instead of being walked across the map. Use F10 (follow) to bring it close first.
- **Frame rate sets the precision.** See [Frame rate and stacking precision](#frame-rate-and-stacking-precision) below. In short: **run every client at 144 fps** for facing within 1°.
- **Where it works:** anywhere the client gives map coordinates. That includes the EQ zones on your server (tested with `GetPlayerMapPosition`). In a place without coordinates (many instances: `GetPlayerMapPosition` gives `0 0` there), the Position column stays empty.
  - **Stack in those places matches facing only.** Followers turn to your facing, and the log says "facing matched only (no map position here…)". It can't walk them onto your spot.
  - **To get close first:** use follow (F10) to bring them in, then stay (F11) and stack (F12). Mirrored movement then runs parallel, a yard or two apart instead of exactly on top of you, so drift builds up faster than outdoors. Re-stack often, or use follow for long runs.
  - **The distance guard can't measure there,** so it doesn't block mirroring. Only a different map ID still counts as another zone.
  - **To test a spot:** `/run SetMapToCurrentZone() local x,y=GetPlayerMapPosition("player") print(GetCurrentMapAreaID(), x, y, GetPlayerFacing())`. If x and y print `0 0`, there are no coordinates.
- **Keep the world map closed.** Positions are relative to the map being shown, so while a client has its world map open, that client reports no position.
- **Mounted, walking or slowed:** the measured speed adapts. Tolerances in yards assume the normal 7 yd/s run speed (`stack.run_speed`).
- **Turning keys:** stacking needs keyboard turning. A and D must be bound to turn left and turn right (WoW's default), not to strafe. If yours differ, set `stack.turn_left`, `stack.turn_right`, `stack.forward`, `stack.back`, `stack.strafe_left` and `stack.strafe_right`.

```yaml
stack:
  who: all                  # who gets stacked; the anchor is always left out
  on_stop: true             # while mirroring, re-stack whenever you stop
  on_stop_delay: 400ms
  tolerance_yards: 0.1
  facing_tolerance_deg: 1   # widened automatically to half a frame (see below)
  timeout: 10s
  max_distance_yards: 40    # farther away, or another zone: skipped
  run_speed: 7.0
  map_aspect: 1.5           # zone maps are 3:2
```

#### Frame rate and stacking precision

**The limit is the game, not a setting.** WoW applies a held key once per frame. A character therefore turns, and walks, in steps of one frame's worth. Keyboard turning runs at 180° per second, so one step is 180° divided by the frame rate:

| Client fps | One frame of turning | One frame of running | Guaranteed facing | Simulated typical / worst |
|---|---|---|---|---|
| 30 | 6.0° | 0.23 yd | ±3.0° | 1.9° / 3.6° |
| 60 | 3.0° | 0.12 yd | ±1.5° | 1.0° / 2.0° |
| 120 | 1.5° | 0.06 yd | ±0.75° | 0.4° / 1.0° |
| **144** | **1.25°** | **0.05 yd** | **±0.6°** | **0.3° / 0.9°** |

**Why it can't "just keep going until it's under 1°" at 60 fps.** Say a character is 1.6° off. The smallest possible correction is one frame, which is 3°, so it lands 1.4° off the other way. Correcting again goes back to 1.6°. It would swing back and forth forever. So WoWBox stops at whatever whole number of frames gets closest, which is at most half a frame off (±1.5° at 60 fps). Sometimes you'll see 0°: the error just happened to be close to a multiple of 3°. That's luck, not something WoWBox can repeat.

**How WoWBox gets the most out of each frame.** It holds every key for exactly a whole number of frame-times. A hold of *n* frame-times always spans exactly *n* frames, wherever it starts, so every correction lands exactly where it's aimed. The facing tolerance (`stack.facing_tolerance_deg`, default 1°) and the position tolerance (`stack.tolerance_yards`, default 0.1) are widened automatically to half a frame when the frame rate can't do better. Each client's own fps comes from BoxBridge.

**Running at 144 fps (recommended).** Run these once, in any client. They apply to every client, including background windows, and WoW saves them:
```
/bb all console maxfps 144
/bb all console maxfpsbk 144
/bb all console gxVSync 0
```
- `maxfps` caps the frame rate of the window you're playing in.
- `maxfpsbk` caps it for windows in the background. All but one of your clients are always in the background, so this one matters most. WoW keeps background windows slow unless you raise it.
- `gxVSync 0` turns off vsync, which otherwise caps the frame rate at your monitor's refresh rate (often 60). Some clients need a restart before a vsync change takes effect. You can also switch it off in Video options.

Then check the **Position** column in the Clients tab, which shows each client's fps. If a client still shows about 60, vsync or the graphics driver is capping it. If the fps is below 144 and fluctuates, your PC is the limit, because it has to draw every client at that rate. Lower in-game graphics settings help. Anything from 120 fps up gives facing under 1°.

#### Who counts as "with you": the distance guard

Your characters don't have to be together. Mirroring and stacking only act on followers who are **with the character you're steering** (for stacking, the one they stack onto). Everyone else is left alone.

```yaml
follow:
  mirror_guard: true               # false = mirror to every follower, wherever they are
  mirror_max_distance_yards: 40
stack:
  max_distance_yards: 40           # farther away, or in another zone: skipped
```

**How "with you" is decided**, each time you press a movement key (mirroring) or start a stack:

1. **Same zone?** BoxBridge sends each client's map ID (`GetCurrentMapAreaID`), even in places without coordinates. A different map ID means *in another zone*, and that follower is left out. This check works from the moment WoWBox starts.
2. **Close enough?** If both have coordinates, WoWBox works out the distance in yards. Map coordinates run 0–1 across each zone's map, and every zone's map is a different size, so WoWBox first needs that zone's **scale**. It gets it by measuring while stacking:
   - Each move teaches it how far a character gets per second, in map units. Divided by the normal run speed (7 yd/s, `stack.run_speed`), that gives map units per yard.
   - Only moves of at least 0.1 s count. On a shorter press, being one frame off is too big an error.
   - Speeds are kept **per character and per zone**, so a mounted or slowed character doesn't throw off the others.
   - **One bad reading is ignored.** A reading more than 2× off the current estimate is set aside. It only replaces the estimate if the next reading agrees, which is what happens when someone really mounts up or gets slowed. A blocked step, a stale reading, or your own mirrored keys reaching the character during a measurement can't spoil the scale.
   - **The scale is trusted after two readings agree.** Until then, the guard only checks the zone, and stacking moves in short steps (at most 0.6 s).
3. **Can't tell?** With no bridge data, an unknown map, or a zone not measured yet, the follower is treated as *with you*. The guard only leaves someone out when it knows they're elsewhere.

The scale is re-measured after every WoWBox start: one or two stacks in a zone are enough. Until then, only "in another zone" can leave someone out.

**What you'll see**
- **Clients tab, Position column:** `[in another zone]` or `[85 yd away]` next to anyone who isn't with the leader.
- **Log tab, mirroring:** a line when someone is left out, and one when they're back:
  ```
  mirroring: WoW2 Gnope left out (in another zone)
  mirroring: WoW3 Kunth left out (52 yd away)
  mirroring: WoW3 Kunth is with you again
  ```
- **Log tab, stacking:** stack results for followers elsewhere, skipped straight away rather than walked across the map:
  ```
  stack WoW2 Gnope: in another zone than the anchor (? yd off, ...)
  stack WoW3 Kunth: too far away (52 yd, max 40 yd): use follow (...)
  ```
  Press **F10** (follow) to bring them over, then stack.

**If someone standing next to you is left out**
- **"in another zone":** the two clients report different map IDs. A client with its **world map open** reports the map it's showing, so close it. Moving between sub-zones and floors (e.g. dungeon levels) can also change the map ID.
- **A distance far larger than real, e.g. "1840 yd away":** the zone scale is off. That shouldn't happen any more. If it does, restart WoWBox, which clears the measured speeds, and send the `stack …` lines from the log.
- **To switch the guard off:** `follow.mirror_guard: false`, or raise `mirror_max_distance_yards` / `stack.max_distance_yards`. Then Reload config.

### Follow settings

```yaml
follow:
  leader: leader                    # who to follow: the leader slot, or a character name
  who: not:leader                   # who follows
  start: ["/target {leader}", "/follow {leader}"]
  refollow: true
  refollow_command: "/follow {leader}"
  refollow_delay: 1s
  refollow_in_combat: false
  retry: 2s                         # first wait after a failed re-follow; doubles up to retry_max
  retry_max: 15s
  stop_key: "S"
  movement_keys: ["W", "A", "S", "D", "Q", "E", "Space"]
  mirror_on_start: false
  mirror_guard: true                # only mirror to followers in your zone, within:
  mirror_max_distance_yards: 40
```

## 12. Macros

Broadcasting sends the *same* key everywhere. A macro sends *specific* things to *specific* characters when you press its hotkey in any WoW window, or press Run in the Macros tab. Pressing the hotkey again stops a macro that's still running.

### The macro editor

In the **Macros** tab, use **New**, **Edit** (or double-click a macro), **Duplicate** or **Delete**.

- **Name** and **Note:** free text. Names must be unique.
- **Hotkey:** click the field, then press the combination (e.g. Ctrl+Alt+H). Backspace or Clear removes it. If the combination already belongs to another macro or a WoWBox hotkey, the editor says so and won't save. While the editor is open, WoWBox hotkeys are paused, so recording F12 doesn't also stack your team.
- **Steps:** one row per step, run top to bottom. **Add step** inserts below the selected row and copies its *To* and *Then wait*. **Remove**, **Move up** and **Move down** do what they say. The columns are:
  - **Step:** what kind of step it is. *Type chat line*, *Press key*, *Press keys* (comma-separated, *Delay* apart), *Wait*, or *Start macro*.
  - **Value:** the chat line, key, key list, duration (`300ms`, `1.5s`) or macro name.
  - **To:** pick from your characters, `all`, `followers`, `leader`, `not:leader`, `class:…`, `role:…`, or type anything from section 8. For slots, type something like `[2, 3]`.
  - **Key gap:** *Press keys* rows only. The pause between those keys (default 250ms).
  - **Then wait:** a pause *after* this step, on any row. Put `1.6s` after each cast so the next one isn't blocked by the global cooldown. A *Wait* row does the same, so use whichever reads better. When you reopen a macro, a wait that follows a step is shown in that step's *Then wait*.
- **Test (run now):** runs the macro as it stands in the editor, without saving.
- **Save:** checks everything first. Problems, such as a bad key name or an empty step, show in red with the step number. A saved macro works immediately, with no Reload needed.

The editor saves to **`config/macros.yaml`**, next to `team.yaml`. The first time you save, your existing macros move over from `team.yaml`. WoWBox removes only the `macros:` block from `team.yaml`, leaves a comment in its place, and keeps a `team.yaml.bak`. Everything else in `team.yaml`, comments included, stays as you wrote it. Once `macros.yaml` exists, it's the only place macros are read from. You can still edit it by hand, then press **Reload config**.

The format is the same whether the macros are in `macros.yaml` or `team.yaml`:

```yaml
macros:
  - name: gnope-assist-and-nuke
    key: "F8"
    do:
      sequence:
        - { type: "/assist Tinkerdawn", to: Gnope }        # typed into Gnope's chat only
        - { wait: 300ms }
        - { keys: ["1-9", "0"], delay: 1.5s, to: Gnope }   # 1, 2, … 9, 0, 1.5 s apart
  - name: everyone-assist-tank
    key: "F9"
    do:
      sequence:
        - { type: "/assist Tinkerdawn", to: followers }
        - { wait: 300ms }
        - { send: "1", to: all }
```

| Step | Does |
|---|---|
| `send: "Ctrl+9"` | one key press |
| `keys: ["1-9", "0"]`, `delay: 1.5s` | several presses in order, `delay` apart (default 0.25 s) |
| `type: "/assist X"` | Enter, the text, Enter, in each target's chat |
| `wait: 500ms` | pause |
| `sequence: [...]` | steps in order |
| `macro: other-name` | start another macro (it runs alongside this one) |

Every step except `wait`, `sequence` and `macro` takes `to:` (default `all`).

- **Held modifiers.** A macro waits until you've released Ctrl/Alt/Shift before its first step, so a `Ctrl+Alt+…` hotkey doesn't turn `1` into `Ctrl+Alt+1`.
- **Editing by hand.** Edit `macros.yaml` (or `team.yaml`, before the first editor save), then press **Reload config**.
- **Tip.** For something you press constantly, an in-game macro bound to a key plus a route (`keys: ["F6"], to: Gnope`) is faster than typing into chat.

### Example: send the pets (Ctrl+1, no in-game macro needed)

Pressing **Ctrl+1** in any WoW window makes the warlock and the shaman assist the tank and send their pets. Nothing is needed on the characters themselves: no in-game macro, no action bar slot, no key binding. WoWBox types the commands.

**In the macro editor** (Macros tab → **New**):
1. **Name:** `attack with pets`.
2. **Hotkey:** click the field and press **Ctrl+1**.
3. Four steps, all *Type chat line*:

   | Step | Value | To |
   |---|---|---|
   | Type chat line | `/assist Tinkerdawn` | `class:WARLOCK` |
   | Type chat line | `/assist Tinkerdawn` | `class:SHAMAN` |
   | Type chat line | `/petattack` | `class:WARLOCK` |
   | Type chat line | `/petattack` | `class:SHAMAN` |

4. **Test**, then **Save**.

The same macro in `config/macros.yaml`:

```yaml
- name: attack with pets
  key: Ctrl+1
  do:
    sequence:
    - {type: /assist Tinkerdawn, to: 'class:WARLOCK'}
    - {type: /assist Tinkerdawn, to: 'class:SHAMAN'}
    - {type: /petattack, to: 'class:WARLOCK'}
    - {type: /petattack, to: 'class:SHAMAN'}
```

**Why it's built this way**
- **Class targets instead of slots.** It finds the warlock and the shaman by class (read live from BoxBridge), whichever slot they're in. A class that isn't logged in is simply skipped. `[2, 3]` works too, but breaks if the slots change.
- **One class per row.** *To* takes one target, so each command appears once per class. Both assists come first, so both characters have a target before their pets go.
- **Typed instead of a key press.** `/petattack` is a slash command, so it costs no bar space. It also avoids posting Ctrl+1 to background windows, the one key type not yet confirmed to work there.
- **Ctrl+1 never reaches the game.** WoWBox takes the hotkey, so the game doesn't see it. If you had a route for `Ctrl+1`, remove it: the macro hotkey wins anyway, but two definitions get confusing. WoWBox waits until you let go of Ctrl, then the four lines take about a second.
- **More pet commands** work the same way: `/petfollow`, `/petstay`, `/petpassive`, `/petdefensive`, `/petaggressive`, and pet spells with `/cast`, e.g. `/cast Torment`.

### Example: buff the whole group (Ctrl+2)

The paladin casts Blessing of Kings on everyone in the group, itself included, with the global cooldown between casts. `[@player]` is the caster itself, and `[@party1]`–`[@party4]` are the others in its group. `exists` skips empty party spots, so the same macro works in any group, whoever is in it and in whatever order.

| Step | Value | To | Then wait |
|---|---|---|---|
| Type chat line | `/cast [@player] Blessing of Kings` | `class:PALADIN` | `1.6s` |
| Type chat line | `/cast [@party1,exists] Blessing of Kings` | `class:PALADIN` | `1.6s` |
| Type chat line | `/cast [@party2,exists] Blessing of Kings` | `class:PALADIN` | `1.6s` |
| Type chat line | `/cast [@party3,exists] Blessing of Kings` | `class:PALADIN` | `1.6s` |
| Type chat line | `/cast [@party4,exists] Blessing of Kings` | `class:PALADIN` | |

- **Several buffers at once.** Different characters can cast at the same time. For a druid and a shaman in one macro (e.g. Ctrl+3), put the druid's `Mark of the Wild` row and the shaman's self buff (`/cast Water Shield`) next to each other. Give only the second of each pair a *Then wait*, so both cast, then both wait.
- **Fewer casts:** `/cast Gift of the Wild` (druid) and the *Greater Blessings* (paladin) buff the group, or the class, in one cast, but need reagents.
- **Empty party spots:** WoWBox still types the line for a spot that's empty (it doesn't know who's in the group). The game then skips the cast because of `exists`, so nothing is cast. Only that row's *Then wait* is spent. To avoid even that, use one row per character by name (`/cast [@Gnope] Blessing of Kings`).
- **Not in combat:** add `nocombat` to the brackets, e.g. `[@party1,exists,nocombat]`.
- **Range** is about 30 yards, so stack first (F12).
- **Spell names** must match the game's exactly, including your server's custom spells.
- **If casts fail** with "Spell is not ready yet", raise *Then wait* a little.

### Using items and casting spells

A `type:` step types any chat line into the chosen characters' chat. WoW handles it exactly as if you typed it, so `/use` and `/cast` work, protected spells included.

```yaml
macros:
  - name: portal-stone                      # every character uses the same item
    key: "Ctrl+Alt+P"
    do: { type: "/use Portal Stone of Learning", to: all }

  - name: heal-and-bolt                     # different characters, different spells
    key: "Ctrl+Alt+H"
    do:
      sequence:
        - { type: "/cast [@Tinkerdawn] Healing Wave", to: Kunth }   # heal the tank, target unchanged
        - { type: "/assist Tinkerdawn", to: Gnope }
        - { type: "/cast Shadow Bolt", to: Gnope }

  - name: buff-round                        # several spells on one character: mind the global cooldown
    key: "Ctrl+Alt+B"
    do:
      sequence:
        - { type: "/cast [@Tinkerdawn] Water Shield", to: Kunth }
        - { wait: 1.6s }
        - { type: "/cast [@Gnope] Water Breathing", to: Kunth }
```

Useful chat commands:

| Line | Does |
|---|---|
| `/use Portal Stone of Learning` | use an item by name |
| `/use item:12345` | use an item by ID (short enough for `/bb`) |
| `/use 0 1` | use whatever is in backpack slot 1 |
| `/cast Shadow Bolt` | cast on your current target |
| `/cast Healing Wave(Rank 3)` | a specific rank |
| `/cast [@Tinkerdawn] Healing Wave` | cast on a named character without changing target (`[target=Tinkerdawn]` also works) |
| `/cast [@focus] Fear` | cast on your focus |
| `/castsequence Corruption, Curse of Agony, Shadow Bolt` | the next spell in the list each time it runs |
| `/stopcasting` | interrupt your own cast |

- **Global cooldown.** Two casts on the same character need about 1.5 s between them (`wait:`), or the second fails with "Spell is not ready yet".
- **Names must match the game's exactly,** including the server's custom spell and item names. Case doesn't matter.
- **The same lines work as team commands,** within 27 characters: `/bb all use item:12345`, `/bb to Gnope cast Shadow Bolt`, `/bb to class:SHAMAN cast Ghost Wolf`.
- **Typed lines or keys?** Typing a line takes about 0.2 s. Use typed lines for situational things: portals, mounts, buff rounds, a heal on a whisper. For your fighting rotation, use action bar keys with broadcasting, which is instant.

**To find an item's ID**, list your bags. Each line shows the bag, the slot, the item ID and the item:
```
/run for b=0,4 do for s=1,GetContainerNumSlots(b) do local l=GetContainerItemLink(b,s) if l then print(b,s,l:match("item:(%d+)"),l) end end end
```
`0 3 19019 [Thunderfury]` means bag 0, slot 3, ID 19019: use `/use item:19019`, or `/use 0 3` for that bag slot. For gear you're wearing (slots 1–19):
```
/run for i=1,19 do local l=GetInventoryItemLink("player",i) if l then print(i,l:match("item:(%d+)"),l) end end
```

**Equipping (a right-click on armor or a weapon):**

| Line | Does |
|---|---|
| `/equip Thunderfury`, `/equip item:19019` | Equip by name or ID, into the item's usual slot. |
| `/equipslot 17 Some Shield` | Into a given slot: 16 main hand, 17 off hand, 13/14 trinkets, 11/12 rings. |
| `/use item:19019` or `/use 0 3` | Same as a right-click in your bags: armor and weapons get equipped. |
| `/use 13` | Use what's **equipped** in slot 13, e.g. an on-use trinket. |
| `/equipset Tank` | Put on a saved Equipment Manager set. |

Armor can't be changed in combat; weapons and shields can.

More slash commands for typed steps (targeting, `/startattack`, pets, totems, conditionals, `/run` checks): section 14.

## 13. Automation

Rules that act without a keypress. Automation always starts **OFF** and in **dry run**: dry run logs what it would send and sends nothing. Turn it on with the button, untick Dry run when you trust it, and kill it with Ctrl+Alt+K.

### The rule editor

In the **Automation** tab, use **New**, **Edit** (or double-click a rule), **Duplicate** or **Delete**.

- **Name, Enabled, Cooldown.** The cooldown is the least time between two firings, e.g. `20s`.
- **Trigger.** Pick one:
  - **State:** fires when a condition is true.
    - **Who:** `any` fires if one of them matches, `all` only if every one does. Or pick a character or slots, e.g. `3 (Kunth)` or `[2, 3]`.
    - **Conditions:** rows of *Field* (health %, power %, in combat, casting, mounted, on /follow, has a target…), *Is* (`=`, `≠`, `<`, `≤`, `>`, `≥`) and *Value*. Yes/no fields only offer `=`/`≠` and *yes*/*no*. All rows must hold.
  - **Timer:** every `25m`, `90s`, `1h`…
  - **Chat:** a chat line comes in. Pick the channels, *From*, *Containing* (words, or empty for any line) and *Heard by*.
    - **From** takes character names (any case), `team` for any of your own characters, or both, e.g. `team, Randomfriend`. Empty means anyone, strangers included.
    - `all`, `any`, `anyone` and `*` are refused with an explanation. *From* doesn't take the *To* keywords: a keyword there would be read as a name, and the rule would never fire.
  - **Chat + state:** a chat line, but only if a condition is true too, e.g. "only if Kunth isn't casting".
- **Then do:** the same step table as the macro editor (*Type chat line*, *Press key*, *Press keys*, *Wait*, *Start macro*, with *To* and *Then wait*). In chat rules, `{sender}`, `{message}`, `{channel}` and `{listener}` are filled in.
- **Live check:** updates twice a second while you edit. It shows in plain words whether the rule would fire *right now*, using the characters' live state, and if not, why not. For example: `Condition is false now: Kunth: no (power_pct is 62, needs < 40)`. For chat rules it spells out what it listens for and on whose client. It also says whether automation is off, in dry run, or live.
- **Test (dry run):** shows, and logs, exactly what one firing would send and to whom, e.g. `type '/use Conjured Water' -> Kunth`. Nothing is sent. Chat rules are tested with a sample line from the first listed sender.
- **Save** checks everything first. Problems show in red, e.g. `Condition 2 (power_pct): 'lots' isn't a number` or `Step 1: unknown key name`. A saved rule is in use immediately.
- **Safe editing:** a rule never fires while its editor is open. Saving keeps its fire count and cooldown.

Rules are saved in **`config/automation.yaml`**, the same way macros go to `macros.yaml`. The first time you save, your rules move over from `team.yaml`. WoWBox removes only the `automation:` block, leaves a comment in its place, and keeps a `team.yaml.bak`. `automation_options:` and everything else stay in `team.yaml` as you wrote them. Comments that were *inside* the old `automation:` block don't carry over. Once `automation.yaml` exists, it's the only place rules are read from. You can still edit it by hand and press **Reload config**. The format is the same as below.

### State rules: `when:`

```yaml
automation:
  - name: healer-drinks-out-of-combat
    when: { slot: 3, combat: false, power_pct_lt: 40, mounted: false, dead: false }
    do:   { send: "Ctrl+9", to: [3] }
    cooldown: 20s
```

- **`slot`:** a number, a list, `any` (the default) or `all`.
- **Every other key** is a bridge field. Add `_lt`, `_lte`, `_gt`, `_gte` or `_ne` to compare.
- **Fields:** `health_pct`, `power_pct`, `power_type`, `target_exists`, `target_hostile`, `target_dead`, `target_is_player`, `target_in_combat`, `target_health_pct`, `combat`, `resting`, `in_party`, `in_raid`, `typing`, `chat_open`, `casting`, `channeling`, `mounted`, `dead`, `ghost`, `afk`, `following`, `level`, `position_ok`, `map_x`, `map_y`, `facing`, `map_id`, `fps`.

### Timers: `every:`

```yaml
  - name: rebuff
    every: 25m
    do: { sequence: [ { send: "F5", to: all }, { wait: 1.5s }, { send: "F6", to: all } ] }
```

### Chat rules: `chat:`

BoxBridge passes every whisper, party, raid, guild, officer, say and yell line its client hears to WoWBox.

```yaml
  - name: kunth-heals-on-whisper
    chat:
      channel: whisper              # or [party, raid], or any. Default: whisper
      sender: [Tinkerdawn, Gnope]   # only these (any case); team = any of your characters. Leave out = anyone
      contains: [heal, help]        # any of these words (any case). Leave out = any line
      heard_by: Kunth               # which of your clients must receive it. Default: all
    when: { slot: 3, casting: false }   # optional extra state check
    do:
      sequence:
        - { type: "/target {sender}", to: Kunth }
        - { wait: 200ms }
        - { send: "3", to: Kunth }
        - { type: "/targetlasttarget", to: Kunth }
    cooldown: 3s
```

- **Placeholders** work in `type:` text and in `to:`:
  - `{sender}`: who sent the line;
  - `{message}`: the text;
  - `{channel}`: whisper, party, and so on;
  - `{listener}`: which of your characters heard it.

  `to: "{sender}"` targets the sender's own window, if the sender is one of yours.
- **Placeholders can't inject commands.** Names keep only letters, digits and `-`. Message text loses control characters and WoW escape codes, and can't start a slash command. Still, use `sender:` to ignore strangers.
- **Heard by several clients.** A party or raid line all your clients hear fires a rule once.
- **Limits.** The first 27 bytes of each line come through, which is plenty for keywords. Senders get 20 bytes. The addon shows each line for 0.5 s, so a burst of 10 lines takes about 5 s, and status updates pause meanwhile.
- **Testing.** `/bb test Tinkerdawn heal me` in Kunth's client acts like a whisper from Tinkerdawn.

### Recipe: heal and buff bot on whisper

A complete example to copy into `team.yaml` under `automation:`. Kunth (shaman) heals and buffs whoever whispers him with a keyword, and replies so they know it worked. Gnope (warlock) answers too. Change the names, spells and keywords to your own.

```yaml
automation:
  # "heal" from a group member: heal them without changing Kunth's target
  - name: heal-on-whisper
    chat:
      channel: [whisper, party]
      sender: [Tinkerdawn, Gnope]          # only these may trigger it; leave out = anyone (careful)
      contains: [heal]
      heard_by: Kunth
    when: { slot: 3, casting: false, dead: false, ghost: false }
    do:
      sequence:
        - { type: "/cast [@{sender}] Healing Wave", to: Kunth }
        - { type: "/w {sender} Healing you!", to: Kunth }
    cooldown: 3s

  # "chain": a different spell for a different word
  - name: chain-heal-on-whisper
    chat: { channel: [whisper, party], sender: [Tinkerdawn, Gnope], contains: [chain], heard_by: Kunth }
    when: { slot: 3, casting: false, dead: false }
    do: { type: "/cast [@{sender}] Chain Heal", to: Kunth }
    cooldown: 5s

  # "buffs": a buff round on the whisperer. One character can only cast once per global cooldown,
  # so its spells need ~1.6 s between them; different characters can cast at the same time.
  - name: buff-bot
    chat:
      channel: whisper
      contains: [buffs, buff me]          # no sender list: anyone may ask (see the warning below)
      heard_by: Kunth
    when: { slot: 3, combat: false, casting: false, dead: false }   # only out of combat
    do:
      sequence:
        - { type: "/w {sender} Buffing you, stand still a few seconds", to: Kunth }
        - { type: "/target {sender}", to: Kunth }        # /target also works on people outside your group
        - { type: "/cast Water Breathing", to: Kunth }
        - { type: "/target {sender}", to: Gnope }
        - { type: "/cast Unending Breath", to: Gnope }   # Gnope casts at the same time as Kunth
        - { wait: 1.6s }
        - { type: "/cast Water Walking", to: Kunth }
        - { wait: 1.6s }
        - { type: "/targetlasttarget", to: [2, 3] }       # back to what they had targeted
    cooldown: 20s                           # one buff round at a time

  # "inv": invite whoever asks (a buff-bot staple)
  - name: invite-on-whisper
    chat: { channel: whisper, contains: [inv], heard_by: Tinkerdawn }
    do: { type: "/invite {sender}", to: Tinkerdawn }
    cooldown: 5s
```

**How it fits together**
- **Who may trigger it (`sender:` / *From*):**

  | You write | Fires for |
  |---|---|
  | `[Tinkerdawn, Gnope]` | those characters only (any case) |
  | `team` | any of **your own** characters: the ones logged in, plus those pinned to slots in `team.yaml` |
  | `[team, Randomfriend]` | your characters and Randomfriend |
  | *(empty / left out)* | **anyone**, strangers included |
  | `all`, `any`, `anyone`, `*` | refused, with a hint to leave it empty instead |

- **`chat:`** decides *which line* fires the rule:
  - `channel` is where it was said;
  - `sender` is who may trigger it;
  - `contains` is the keywords;
  - `heard_by` is the client that received it.
- **`when:`** adds a condition on that character's current state, e.g. not casting, not dead, out of combat. Any bridge field from the list above can go here.
- **`do:`** is the same step list as a macro. `{sender}` is filled in with the whisperer's name.
- **`[@{sender}]`** casts on the whisperer without changing your target. It works for party and raid members. For anyone else, use `/target {sender}` + `/cast …` + `/targetlasttarget`, as in `buff-bot`.
- **`cooldown:`** stops one person from spamming the rule: the person just served can't trigger it again until the cooldown ends.
- **Requests queue while the rule is busy.** If three people ask for buffs at once, the rule serves them one after another:

  | Time | |
  |---|---|
  | 0 s | Tinkerdawn asks: the rule runs for Tinkerdawn. |
  | 2 s | Gnope asks: queued (#1). |
  | 3 s | Randompug asks: queued (#2). |
  | after Tinkerdawn's run and the cooldown | Gnope is served, then Randompug. |

  - **One place per person:** asking again while you're queued or being served doesn't add a second place.
  - **Limits:** up to `chat_queue` requests (5) wait, each for at most `chat_queue_ttl` (60 s). Older ones are dropped and logged.
  - **The condition is checked again** when a request's turn comes. If the rule has `when:` and it's false then, the request is dropped.
  - **Per rule:** set *Queue while busy* in the editor (`queue:` in YAML) to a number, `off` (`0`, the old skip-it behaviour) or `default`.
  - **The Status column** shows `running step 2/9 for Tinkerdawn · queued: Gnope, Randompug`, and *Next* says who's next.
- **The most specific keyword wins.** Keywords match anywhere in the text, so `heal` is also inside "light heal" and "chain heals". When one rule's matched keyword is part of a longer keyword another rule matched, only the longer one fires:

  | Line | Rules with these keywords: `heal`, `heals`, `light heal`, `chain heal`, `chain heals` |
  |---|---|
  | "light heal pls" | only `light heal` |
  | "chain heals" | only `chain heals` |
  | "heals" | only `heals` |
  | "heal me" | only `heal` |

  Keywords that don't overlap still all fire: a line with both `heal` and `buff` fires a heal rule and a buff rule.

**Turning it on and testing**
1. Paste the rules under `automation:` in `team.yaml`, save, and press **Reload config**. They show up in the **Automation** tab.
2. Click **Automation: ON**. Leave **Dry run** ticked at first.
3. In Kunth's client, fake a whisper:
   ```
   /bb test Tinkerdawn heal me
   /bb test Randompug buffs
   ```
   The Log tab shows `rule 'heal-on-whisper' fired (whisper from Tinkerdawn: 'heal me')`, then `[dry run] would type '/cast [@Tinkerdawn] Healing Wave' -> Kunth`.
4. When the log looks right, untick **Dry run**. Ctrl+Alt+K turns all automation off at once.

**Watch out**
- **Strangers.** A rule without `sender:` reacts to *anyone* who whispers the keyword. For "only my own characters", use `sender: team`. That's what a public buff bot does, but keep `cooldown` up and stay out of combat (`when: {combat: false}`).
- **Server rules.** Many servers forbid unattended bots. A buff bot that answers strangers while you're away is exactly what they look for. Check your server's rules first.
- **Range and line of sight.** WoW's own rules still apply. Out of range shows the usual error on that client, and the rule doesn't retry.
- **Text length.** Only the first 27 characters of a whisper reach WoWBox, so keywords should come early in the message.
- **Exact names.** Spell names must match the game's exactly, including your server's custom spells.

Recipes for anti-AFK, running in to melee and totems are in section 14.

### Watching the rules run

The **Automation** tab updates four times a second:

| Column | Shows |
|---|---|
| **Status** | `waiting` (green), `cooldown 8s` or `running step 2/4` (orange), `disabled`, `automation OFF`, `paused: …` (grey) |
| **Next** | **Timers:** a countdown bar filling up to the next firing, e.g. *in 12m 05s*. **State rules:** *now: condition is true*, or *when the condition is true (power_pct is 62, needs < 40)*. **Chat rules:** *on a matching chat line*. |
| **Last fired** | how long ago, e.g. *2m 10s ago*, or *never* |
| **Fired** | how many times since WoWBox started |

Long triggers and actions are cut off with "…". Hover over them to see all of it. **Timers start when automation is turned on:** a timer never fires the moment you switch automation on, even if it came due while automation was off.

### Common to all rules

- `cooldown:` is the minimum time between firings.
- `enabled: false` keeps a rule in the file but switched off.
- A rule that's still running its sequence won't start again (chat rules queue the request instead).
- **Two rules on one character take turns.** Steps from *different* rules sent to the same character are kept at least `rule_gap` (1.5 s, the global cooldown) apart. A heal whispered in the middle of a buff round goes out 1.5 s after the last buff cast, and the rest of the buffs move back to make room, so neither fails with "Spell is not ready yet". The first rule to wait for a character goes first. Steps within one rule keep their own *Then wait* gaps. Set `rule_gap: 0` to turn this off.
- **Safety:**
  - **Pausing is per rule.** With `pause_on_stale_bridge` (on by default), a client whose bridge can't be read (minimized, covered, addon off) pauses only the rules that check or send to it, because WoWBox can't tell whether you're typing there. Every other rule keeps running.
  - **A closed window** pauses nothing: targets like `all` simply leave it out.
  - **The banner** under the toolbar shows from any tab when rules are paused, when Dry run is on (rules log but send nothing), or when a window was closed.
  - **Nothing gets mixed into what you're typing.** A typed line (or an automation key press) waits:
    - while that client's chat box is open, so it doesn't land in your half-typed message or send it;
    - while you're pressing keys in the window you're in (holding W to run, or within `type_quiet`, 0.35 s, of your last key), so no `www/cast Heal`.

    Waiting lines go out as soon as you stop, in order and with the same gaps between a rule's steps. The banner shows what's waiting.
  - **After `type_wait_max` (8 s):** a client that's still typing is skipped, and the log says so. If you're only holding a key (running a long way), the line goes anyway, and your key presses in that window are held back for the ~0.2 s it takes to type it.
  - **Your own hotkey macros** don't wait for key presses: you pressed the hotkey, so the keys go at once.
  - Every firing is logged.

## 14. WotLK slash commands for macros and automation

A *Type chat line* step (`type:` in YAML) types a line into a character's chat, so anything you could type yourself works in a WoWBox macro or rule. These are the 3.3.5a commands that matter most for boxing. EQWOW's custom spells and items follow the same rules; just use their exact names.

### Targeting and assisting

| Command | Does |
|---|---|
| `/assist Tinkerdawn` | Target whatever Tinkerdawn is targeting. The usual first line of an attack macro. |
| `/target Tinkerdawn` | Target by name. It matches the nearest name that *starts with* the text. |
| `/targetexact Tinkerdawn` | Target by exact name only. |
| `/targetlasttarget` | Go back to the previous target, e.g. after a heal on `{sender}`. |
| `/targetenemy` | Cycle to the next enemy (like Tab). |
| `/cleartarget` | Drop the target. |
| `/focus` / `/clearfocus` | Set the current target as focus / clear it. Then cast with `[@focus]`. |

### Combat

| Command | Does |
|---|---|
| `/startattack` | Turn melee auto-attack **on**. If it's already on, nothing happens, so it's safe to repeat. |
| `/stopattack` | Turn auto-attack off. |
| `/cast Attack` | **Toggles** auto-attack: pressing it twice turns it off. Use `/startattack` instead. |
| `/cast Shadow Bolt` | Cast on the current target. |
| `/cast [@Tinkerdawn] Healing Wave` | Cast on a character without changing target. `[target=Tinkerdawn]` is the older spelling. |
| `/cast Healing Wave(Rank 3)` | A specific rank. |
| `/castsequence reset=combat Corruption, Curse of Agony, Shadow Bolt` | The next spell in the list each time it runs; starts over after combat (`reset=target`, `reset=10` for seconds). |
| `/stopcasting` | Interrupt your own cast. |
| `/use Portal Stone of Learning`, `/use item:12345`, `/use 13` | Use an item by name, by ID, or by equipment slot (13 and 14 are trinkets). |
| `/cancelaura Ghost Wolf` | Remove a buff or form from yourself. |
| `/dismount` | Get off the mount. |

`/startattack` doesn't move the character. It only swings once the target is in melee range. To run in, use Interact With Target (below).

### Pets

| Command | Does |
|---|---|
| `/petattack` | Send the pet at your target. |
| `/petfollow` | Call it back. |
| `/petstay` | Keep it where it is. |
| `/petpassive` / `/petdefensive` / `/petaggressive` | Set its stance. |

### Movement and follow

| Command or key | Does |
|---|---|
| `/follow Tinkerdawn` (`/fol`) | Follow a friendly player. Breaks on any movement key. Doesn't work on mobs. |
| **Interact With Target** key | With Click-to-Move on, runs to the target and starts melee (see below). |
| `Space` (a *Press key* step) | Jump. Clears the AFK flag. |

**Running a character into melee range:**
1. On that character, turn on Interface → Mouse → **Click-to-Move**, or type `/console autoInteract 1`. To check: `/run print(GetCVar("autoInteract"))` should print `1`.
2. Bind Key Bindings → Targeting → **Interact With Target** to a spare key, e.g. `Ctrl+Shift+I`.
3. In WoWBox: `/assist Tinkerdawn`, then *Press key* `Ctrl+Shift+I`. In the Value box, type the key name with no quotes: `Ctrl+Shift+I`.

Interact always goes to melee range; there's no way to stop at 20 yards. A character on `/follow` may be pulled back toward the leader, so break follow first (Stay, F11). Starting a cast stops the run, so give the Interact step a *Then wait* long enough to arrive, 3–4 s.

### Shaman totems

Since 3.0.2 a single spell drops all four totems from the multi-cast totem bar above the action bar. Click each slot on that bar to choose the Earth, Fire, Water and Air totems for each set.

| Spell | Learned at | Does |
|---|---|---|
| `/cast Call of the Elements` | 30 | Drops totem set 1 |
| `/cast Call of the Ancestors` | 40 | Drops totem set 2 |
| `/cast Call of the Spirits` | 50 | Drops totem set 3 |
| `/cast Totemic Recall` | 30 | Removes your totems and refunds some of their mana |

It costs the mana of all four totems together. Add a *Then wait* of about 1.5 s after it before the shaman's next cast.

### Macro conditionals

Square brackets make a line act only in some situations. Separate conditions with commas (all must be true); several bracket groups mean "the first group that fits".

| Conditional | True when |
|---|---|
| `[@party1]`, `[@player]`, `[@pet]`, `[@focus]`, `[@Tinkerdawn]` | Not a test: it picks who the spell goes on. |
| `exists` / `noexists` | That unit is there (e.g. a party spot is filled). |
| `harm` / `help` | The unit is an enemy / a friend. |
| `dead` / `nodead` | The unit is dead / alive. |
| `combat` / `nocombat` | **You** are in combat / not. |
| `pet` / `nopet` | You have a pet out / not. |
| `mounted`, `swimming`, `flying`, `indoors`, `outdoors`, `stealth` | What it says. |
| `channeling` / `nochanneling` | You're channeling a spell / not. |
| `mod:shift`, `mod:ctrl`, `mod:alt` | That modifier is held while the macro runs. |

Examples:
```
/cast [@party1,exists,nodead] Renew
/cast [nopet] Summon Imp
/petattack [@target,harm,nodead]
/cast [help] Flash Heal; [harm] Smite
```

**There's no buff conditional in 3.3.5:** `[nobuff:X]` doesn't exist. For buffs, use a timer rule that recasts a little before the buff runs out (e.g. `every: 29m` for a 30-minute buff).

### Group, chat and utility

| Command | Does |
|---|---|
| `/reload` | Reload the UI (needed after updating the BoxBridge addon). `/bb all reload` does it on every client. |
| `/console maxfps 144` | Set a game setting (see section 11 for the frame-rate settings stacking needs). |
| `/invite Gnope`, `/readycheck`, `/roll` | Group basics. |
| `/p`, `/ra`, `/w Name`, `/s`, `/y` | Party, raid, whisper, say, yell. |
| `/afk`, `/dnd` | Set or clear the AFK / Do Not Disturb flags. |
| `/run <lua>` | Run Lua. It can *check* things but can't cast spells or use items: those are protected and need a real key press or a typed `/cast`. |

### Useful `/run` checks

```
/run SetMapToCurrentZone() local x,y=GetPlayerMapPosition("player") print(GetCurrentMapAreaID(), x, y, GetPlayerFacing())
```
Map ID, position and facing. `0 0` for x and y means this place has no coordinates (many instances), so stacking can only match facing there.

```
/run print(UnitIsAFK("player"))
```
`1` if this character is flagged AFK.

```
/run for i=1,40 do local n=UnitBuff("player",i) if n then print(i,n) end end
```
Lists the buffs on this character with their exact names, to copy into `/cast` lines.

```
/run for b=0,4 do for s=1,GetContainerNumSlots(b) do local l=GetContainerItemLink(b,s) if l then print(b,s,l:match("item:(%d+)"),l) end end end
```
Every item in your bags with its bag, slot and ID, for `/use item:12345`, `/equip item:12345` or `/use <bag> <slot>`. Equipping commands are in section 12, *Using items and casting spells*.

### Recipes

**Necro runs in and dots (one hotkey):**

| Step | Value | To | Then wait |
|---|---|---|---|
| Type chat line | `/assist Tinkerdawn` | Gnope | |
| Press key | `Ctrl+Shift+I` (Interact With Target) | Gnope | `3s` |
| Type chat line | `/startattack` | Gnope | |
| Type chat line | `/cast Corruption` | Gnope | `1.6s` |
| Type chat line | `/cast Curse of Agony` | Gnope | |

**Pets, totems and attack together (Ctrl+1):**

| Step | Value | To |
|---|---|---|
| Type chat line | `/assist Tinkerdawn` | `class:WARLOCK` |
| Type chat line | `/petattack` | `class:WARLOCK` |
| Type chat line | `/assist Tinkerdawn` | `class:SHAMAN` |
| Type chat line | `/petattack` | `class:SHAMAN` |
| Type chat line | `/cast Call of the Elements` | `class:SHAMAN` |

**Anti-AFK rule (Automation tab):** one rule per character, so only the AFK one jumps.

```yaml
- name: kunth-anti-afk
  when: { slot: 3, afk: true }
  do: { send: "Space", to: Kunth }
  cooldown: 30s
```
In the editor: trigger *State*, Who `3 (Kunth)`, condition `AFK = yes`, step *Press key* `Space` to `Kunth`, cooldown `30s`. Without a cooldown the rule fires several times before the jump registers. A simpler version without the flag: a timer `every: 4m` that presses `Space` on `followers`. Check your server's rules on anti-AFK automation first.

**What can't be done with slash commands:**
- **Cast based on a buff:** there's no `[nobuff]`, and `/run` can't cast. Use a timer.
- **Follow a mob:** `/follow` only works on friendly players. Use Interact With Target.
- **Stop at a range:** Interact runs all the way to melee; nothing stops at a chosen distance.
- **Mouse clicks:** WoWBox sends keys and typed lines only.

## 15. Layouts

| Layout | |
|---|---|
| `grid_auto` (default) | Picks the tiling whose cells come closest to 16:9 for your monitor and client count. 3 clients on a super-ultrawide end up side by side. |
| `grid_2x2`, `grid_1x3` | Fixed borderless tiles on `layout.monitor`. |
| `main_thumbs` | The main client fills the monitor except for a strip of live thumbnails. Click a thumbnail to swap. `layout.thumbs_monitor: 2` puts the strip on another monitor. |
| `tabs` | One client fills the monitor, and a tab bar switches between them. |

Ctrl+Alt+L cycles the layouts. In main_thumbs and tabs, all clients share one size and position, so swapping only raises a window. Nothing is resized, which avoids a D3D device reset.

**Sharpness:** the game renders at its own resolution (`gxResolution`), and Windows stretches that into the window. If the picture looks blurry or squashed, set the in-game resolution to the window size. Alternatively, set `client.resolution` and `client.set_windowed_cvars: true` so `Config.wtf` is written before launch (a backup is kept).

## 16. Configuration reference

Everything lives in `config\team.yaml`. The exe uses `config\team.yaml` next to `wowbox.exe` instead. `config\team.example.yaml` has every option with comments.

- **Durations** look like `500ms`, `1.5s`, `20s`, `25m` or `1h`.
- **Validate** with `--check-config`.
- **Reload config** re-reads routes, hotkeys, macros, team, follow and automation. Other sections need a restart.
- **Save config** updates the route table, the leader and the slots' `character:` names in `team.yaml` in place. Every other line, comments included, is left alone. The previous version is kept as `.bak`, and nothing is written when nothing changed.

| Section | Key options |
|---|---|
| `client` | `exe`, `args`, `set_windowed_cvars`, `resolution`, `launch_stagger` (3s), `window_timeout` (120s), `rename_windows` |
| `team` | `leader`, `slots: {n: {label, role, character}}` |
| `layout` | `default`, `monitor`, `thumbs_monitor`, `thumb_strip` (0.22), `tab_bar_height` |
| `hotkeys` | `toggle_broadcast`, `release_all`, `follow`, `stay`, `stack`, `mirror_movement`, `cycle_layout`, `next_main`, `automation_kill`, `focus_slot: {n: chord}` |
| `broadcast` | `enabled_on_start`, `chatguard`, `chatguard_open_keys`, `chatguard_close_keys`, `modifier_mode` (post / focus_swap / strip), `focus_swap_dwell`, `only_when_wow_focused`, `type_wait_max` (8 s), `type_quiet` (0.35 s) |
| `follow`, `stack` | section 11 |
| `routes` | list of `{keys, to, note}` (section 9) |
| `bridge` | `enabled`, `hz` (10), `block_size` (8, match `/bb size`), `method` (auto / mss / dxcam / printwindow), `stale_after` (1.5s), `commands` (true) |
| `macros` | section 12. Kept in `config/macros.yaml` once you've saved from the macro editor. |
| `automation` | section 13. Kept in `config/automation.yaml` once you've saved from the rule editor. |
| `automation_options` | `enabled_on_start` (false), `dry_run` (true), `tick_hz` (10), `pause_on_stale_bridge` (true), `chat_queue` (5), `chat_queue_ttl` (60 s), `rule_gap` (1.5 s) |
| `memread` | optional read-only memory reader: `enabled` (false), `hz`, `fields: {name: {address, module, offsets, type}}`. No addresses ship with WoWBox; you supply and verify them. |
| `ui` | `overlay: true` shows the overlay on start |

## 17. Building the standalone exe

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

This produces `dist\WoWBox\` containing:
- `wowbox.exe`, a single file with no Python needed;
- `config\`, `addon\` and this README.

The exe keeps its config in `dist\WoWBox\config\team.yaml` and its logs in `dist\WoWBox\logs\`. On first run, it copies the example config if `team.yaml` is missing. dxcam isn't bundled, so the `auto` capture method uses mss and PrintWindow.

Close the exe before rebuilding: Windows locks a running exe.

## 18. Project layout and development

```
addon/BoxBridge/        the WoW addon (Lua): state strip, chat lines, /bb commands
config/                 team.example.yaml (tracked); team.yaml, macros.yaml, automation.yaml (yours, not tracked)
packaging/              build.ps1 + PyInstaller entry point
src/wowbox/
  __main__.py           command line, logging, startup
  app.py                Controller: owns every subsystem, bridges worker threads to the Qt UI
  config.py             team.yaml models (pydantic), load/save
  keys.py               key names, chords, ranges
  team.py               slots, clients, target resolution
  launcher.py           launch / attach / track clients
  follow.py             follow mode, re-follow, movement mirroring
  stack.py              stacking: the position/facing control loop
  router/               keyboard hook → route table → sender thread; chat guard
  bridge/               pixel decoder (state + chat + commands) and capture poller
  automation/           steps, macro runner, rule engine
  macro_store.py        macros.yaml: load, save, the one-time move out of team.yaml
  rule_store.py         automation.yaml: the same for automation rules
  config_text.py        Save config: edits team.yaml in place, keeping comments
  commands.py           /bb team commands (the client you typed in goes last)
  layout/               grid maths, thumbnail strip, tab bar, layout manager
  platform/             Win32 windows / input / capture (ctypes), and fakes for tests
  memread/              optional read-only memory reader
  ui/                   main window, macro and rule editors, tray icon, overlay
tests/                  pytest suite
```

```powershell
.venv\Scripts\python.exe -m pytest          # tests
.venv\Scripts\python.exe -m wowbox --check-config
```

**What the tests cover:**
- the router, chat guard and decoder (including stretched and irregular strips), automation, chat rules, macros, follow mode, layouts, the launcher and config;
- the real `BoxBridge.lua`, run in Lua 5.1 through `lupa` against a stubbed WoW API, with its output decoded.

They use fake Win32 implementations, so nothing touches real windows.

**Keep the addon and the decoder in sync.** The strip format is documented at the top of `src/wowbox/bridge/decoder.py`. Any change there needs the matching change in `BoxBridge.lua`.

## 19. Troubleshooting

| Problem | Fix |
|---|---|
| Keys don't reach other clients | Run WoW and WoWBox at the same privilege level. The log says *access denied* when that's the cause. |
| A character keeps running / key stuck | Ctrl+Alt+Pause (release all). Turning broadcast off also releases everything. |
| Modifier binds don't fire on followers | `broadcast.modifier_mode: focus_swap`, or binds without modifiers. |
| Bridge: `no BoxBridge header` | Addon not enabled for that character, UI hidden (Alt+Z), or something covers the top-left corner. |
| Bridge: `checksum failed` / `blocks visible` | Something is drawn over the strip, the window is too small (`/bb size 12`), or a client still runs the old addon (`/reload`). |
| Bridge stale on a minimized client | Minimized clients don't draw. Use main_thumbs or tabs instead of minimizing. |
| Character in the wrong slot | Move up/down, then Remember characters. |
| A rule or command was "skipped: typing" | That client's chat box was open. Close it and try again. |
| `/bb all …` does nothing | Is WoWBox running and attached, and does the Clients tab show live stats for that client? Is the command ≤ 27 bytes? Check the Log tab. |
| Follow mode doesn't re-follow | Does the State column show `following` while a character follows? If not, `/reload` that client for the current addon. Re-follow also waits during combat or casting, and for the window you're in. |
| Mirrored movement does nothing in background windows | That client ignores posted movement keys. Use follow mode without mirroring. |
| Stack says "facing matched only" | No coordinates in that area (many instances), or that client's world map is open. Facing is matched; use follow to get close. |
| Stack ends a degree or two off | That's half a frame of turning at that client's frame rate (±1.5° at 60 fps). Run at 144 fps: `/bb all console maxfps 144`, `/bb all console maxfpsbk 144`, `/bb all console gxVSync 0`. Check the fps in the Position column. See *Frame rate and stacking precision* (section 11). |
| Stack turns the wrong way or strafes instead of turning | A/D aren't bound to turn left/right on that character. Set the `stack.*` keys. |
| Game blurry or stretched | Set the in-game resolution to the window size (section 15). |
| Black screen after arranging | D3D device reset. Arrange at character select, or use Restore frames. |
| Black thumbnails | DWM can't show minimized windows. Don't minimize in main_thumbs. |
| Logs | `logs\wowbox.log` (rotating). The Log tab shows INFO and up. |

## 20. Known limits

- **Not yet tested in a live game:** modifier chords under `post`. Stacking has been used in a live game; at 144 fps it gets facing within about a degree. `/bb` commands, typed steps and mirrored movement have worked in a live game. If modifier chords fail on followers, use `modifier_mode: focus_swap`.
- **Mouse input** isn't broadcast or mirrored.
- **Bridge size limits:** the strip carries 54 bytes per frame, which is why chat lines and commands are cut to 27 bytes.
- **Covered windows** fall back to PrintWindow. That's slower, but works.
- **Windows only.**

## License

MIT. See [LICENSE](LICENSE).
