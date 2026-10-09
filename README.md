# WoWBox

A multibox controller for the World of Warcraft 3.3.5a (WotLK) client on Windows. One person plays 3–4 characters at once:

- WoWBox launches or attaches the clients and tiles them on your monitor.
- It sends your key presses to the characters you choose, per key.
- It shows each character's health, mana, target and state live.
- It can make specific characters do specific things: macros, team commands, follow, and rules that react to game state or chat.

It works from outside the game. WoWBox never injects code into WoW or modifies it. It sends keys as ordinary window messages and reads a small strip of coloured pixels that its companion addon, **BoxBridge**, draws in the corner of each client.

> **Server rules come first.** Many private servers restrict multiboxing tools, one-key-many-actions macros, or automation that reacts to the game without you. WoWBox gives you the tools. Whether you may use them is your server's call.

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
14. [Layouts](#14-layouts)
15. [Configuration reference](#15-configuration-reference)
16. [Building the standalone exe](#16-building-the-standalone-exe)
17. [Project layout and development](#17-project-layout-and-development)
18. [Troubleshooting](#18-troubleshooting)
19. [Known limits](#19-known-limits)

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
| **Stacking** | One key walks every follower onto your exact spot and facing (within about half a yard and a few degrees), using positions from BoxBridge. |
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
cd C:\Users\exx\Downloads\WOWBOX
powershell -ExecutionPolicy Bypass -File setup.ps1     # once: Python 3.12 venv + dependencies + config\team.yaml
```

1. Copy `addon\BoxBridge` into `<WoW>\Interface\AddOns\` and enable it for every character.
2. Edit `config\team.yaml`: set `client.exe`, your slots, and your characters.
3. Start your clients and log in, then run `.\wowbox.bat`. Or build and use `dist\WoWBox\wowbox.exe` (section 16).
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
dist\WoWBox\wowbox.exe             # standalone build (section 16)
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

**Tabs:**

| Tab | Shows |
|---|---|
| **Clients** | One row per slot: character and class, label, role, PID, window, status, Broadcast checkbox (untick to leave a client out of broadcasts), HP, power, target, state flags (`combat casting TYPING following …`), and bridge health. Buttons: **Move up/down** swaps slots, **Remember characters** pins the current characters into `team.yaml`. |
| **Routes** | The route table, editable live. Save config writes it back. |
| **Macros** | Your macros with their hotkeys, whether they're running, and their steps. Double-click or Run/Stop. |
| **Automation** | Rules, their triggers and actions, enable checkboxes, and how often each has fired. |
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
| F12 | **stack**: everyone walks onto the spot and facing of the window you're in |
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
- **How it gets there:** BoxBridge shows the command on its strip for half a second, WoWBox reads it, and types `Enter, command, Enter` into each target client. That takes about a quarter to half a second.
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
  - **Test first.** Some clients don't take held movement keys while in the background. Try a short walk.
- **Using an in-game macro instead.** Set `follow.start_key` / `follow.refollow_key` to a key bound to an in-game `/follow` macro, and WoWBox presses that key instead of typing.

### Stacking

Mirrored movement only stays together if everyone starts on the same spot, facing the same way. Even 5° of difference becomes 2 yards after a 20-yard run. **Stack (F12, the Stack button, or `/bb stack`)** puts them there:

1. BoxBridge reports every character's map position, facing and frame rate (the **Position** column in the Clients tab).
2. For each follower, WoWBox:
   - turns it with A/D to face the way the anchor faces;
   - moves it without turning: W/S along its facing, Q/E sideways;
   - after every move, waits for a fresh reading and corrects again.
3. It usually finishes in 1–5 seconds, within about **half a yard and 3°**. All followers stack at the same time.

More details:
- **No speeds are guessed.** The first move of each kind is a short probe. From then on, WoWBox measures how fast each key moves or turns the character on this map and uses that.
- **The anchor** is the window you're in when you press F12, or the client where you typed `/bb stack`. If you press F12 in WoWBox itself, the leader is the anchor.
- **Mirroring plus stacking is lockstep.** With mirroring on, the followers restack every time you stop moving (`stack.on_stop`). This replaces `/follow` while mirroring, because follow would pull them behind you. Press your movement keys and they move with you. Stop, and any drift is corrected within a second or two.
- **You can always take over.** Pressing a movement key yourself cancels a running stack. So does pressing F12 again.
- **Frame rate matters.** WoW only notices a key on its next frame, so a character can't turn less than one frame's worth. At 60 fps that's 3°. At 20 fps it's 9°, and stacking widens its tolerance to match. Background WoW windows often run at a reduced frame rate, so raise it on every client once:
  ```
  /bb all console maxfpsbk 60
  ```
  Check the frame rates in the Position column.
- **Where it works:** anywhere the client gives map coordinates. That includes the EQ zones on your server (tested with `GetPlayerMapPosition`). In a place without coordinates, the Position column stays empty and stacking reports "no map position". Use follow there.
- **Keep the world map closed.** Positions are relative to the map being shown, so while a client has its world map open, that client reports no position.
- **Mounted, walking or slowed:** the measured speed adapts. Tolerances in yards assume the normal 7 yd/s run speed (`stack.run_speed`).
- **Turning keys:** stacking needs keyboard turning. A and D must be bound to turn left and turn right (WoW's default), not to strafe. If yours differ, set `stack.turn_left`, `stack.turn_right`, `stack.forward`, `stack.back`, `stack.strafe_left` and `stack.strafe_right`.

```yaml
stack:
  who: all                  # who gets stacked; the anchor is always left out
  on_stop: true             # while mirroring, re-stack whenever you stop
  on_stop_delay: 400ms
  tolerance_yards: 0.5
  facing_tolerance_deg: 3   # widened automatically for low frame rates
  timeout: 10s
  run_speed: 7.0
  map_aspect: 1.5           # zone maps are 3:2
```

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
```

## 12. Macros

Broadcasting sends the *same* key everywhere. A macro sends *specific* things to *specific* characters when you press its hotkey in any WoW window, or double-click it in the Macros tab. Pressing the hotkey again stops a macro that's still running.

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
- **Editing.** Edit `team.yaml`, then press **Reload config**.
- **Tip.** For something you press constantly, an in-game macro bound to a key plus a route (`keys: ["F6"], to: Gnope`) is faster than typing into chat.

## 13. Automation

Rules that act without a keypress. Automation always starts **OFF** and in **dry run**: dry run logs what it would send and sends nothing. Turn it on with the button, untick Dry run when you trust it, and kill it with Ctrl+Alt+K.

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
      sender: [Tinkerdawn, Gnope]   # only these (any case). Leave out = anyone
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

### Common to all rules

- `cooldown:` is the minimum time between firings.
- `enabled: false` keeps a rule in the file but switched off.
- A rule that's still running its sequence won't start again.
- **Safety:**
  - Automation pauses itself when any client's bridge goes stale or a window disappears (`pause_on_stale_bridge`).
  - Key presses skip clients that are typing.
  - Every firing is logged.

## 14. Layouts

| Layout | |
|---|---|
| `grid_auto` (default) | Picks the tiling whose cells come closest to 16:9 for your monitor and client count. 3 clients on a super-ultrawide end up side by side. |
| `grid_2x2`, `grid_1x3` | Fixed borderless tiles on `layout.monitor`. |
| `main_thumbs` | The main client fills the monitor except for a strip of live thumbnails. Click a thumbnail to swap. `layout.thumbs_monitor: 2` puts the strip on another monitor. |
| `tabs` | One client fills the monitor, and a tab bar switches between them. |

Ctrl+Alt+L cycles the layouts. In main_thumbs and tabs, all clients share one size and position, so swapping only raises a window. Nothing is resized, which avoids a D3D device reset.

**Sharpness:** the game renders at its own resolution (`gxResolution`), and Windows stretches that into the window. If the picture looks blurry or squashed, set the in-game resolution to the window size. Alternatively, set `client.resolution` and `client.set_windowed_cvars: true` so `Config.wtf` is written before launch (a backup is kept).

## 15. Configuration reference

Everything lives in `config\team.yaml`. The exe uses `config\team.yaml` next to `wowbox.exe` instead. `config\team.example.yaml` has every option with comments.

- **Durations** look like `500ms`, `1.5s`, `20s`, `25m` or `1h`.
- **Validate** with `--check-config`.
- **Reload config** re-reads routes, hotkeys, macros, team, follow and automation. Other sections need a restart.
- **Save config** writes the file back. The previous version is kept as `.bak`, but YAML comments are lost.

| Section | Key options |
|---|---|
| `client` | `exe`, `args`, `set_windowed_cvars`, `resolution`, `launch_stagger` (3s), `window_timeout` (120s), `rename_windows` |
| `team` | `leader`, `slots: {n: {label, role, character}}` |
| `layout` | `default`, `monitor`, `thumbs_monitor`, `thumb_strip` (0.22), `tab_bar_height` |
| `hotkeys` | `toggle_broadcast`, `release_all`, `follow`, `stay`, `stack`, `mirror_movement`, `cycle_layout`, `next_main`, `automation_kill`, `focus_slot: {n: chord}` |
| `broadcast` | `enabled_on_start`, `chatguard`, `chatguard_open_keys`, `chatguard_close_keys`, `modifier_mode` (post / focus_swap / strip), `focus_swap_dwell`, `only_when_wow_focused` |
| `follow`, `stack` | section 11 |
| `routes` | list of `{keys, to, note}` (section 9) |
| `bridge` | `enabled`, `hz` (10), `block_size` (8, match `/bb size`), `method` (auto / mss / dxcam / printwindow), `stale_after` (1.5s), `commands` (true) |
| `macros` | section 12 |
| `automation` | section 13 |
| `automation_options` | `enabled_on_start` (false), `dry_run` (true), `tick_hz` (10), `pause_on_stale_bridge` (true) |
| `memread` | optional read-only memory reader: `enabled` (false), `hz`, `fields: {name: {address, module, offsets, type}}`. No addresses ship with WoWBox; you supply and verify them. |
| `ui` | `overlay: true` shows the overlay on start |

## 16. Building the standalone exe

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

This produces `dist\WoWBox\` containing:
- `wowbox.exe`, a single file with no Python needed;
- `config\`, `addon\` and this README.

The exe keeps its config in `dist\WoWBox\config\team.yaml` and its logs in `dist\WoWBox\logs\`. On first run, it copies the example config if `team.yaml` is missing. dxcam isn't bundled, so the `auto` capture method uses mss and PrintWindow.

Close the exe before rebuilding: Windows locks a running exe.

## 17. Project layout and development

```
addon/BoxBridge/        the WoW addon (Lua): state strip, chat lines, /bb commands
config/                 team.example.yaml (tracked), team.yaml (yours, not tracked)
packaging/              build.ps1 + PyInstaller entry point
spikes/                 Phase 0 experiments (see spikes/README.md and RESULTS.md)
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
  layout/               grid maths, thumbnail strip, tab bar, layout manager
  platform/             Win32 windows / input / capture (ctypes), and fakes for tests
  memread/              optional read-only memory reader
  ui/                   main window, tray icon, overlay
tests/                  pytest suite
wowbox-project-plan.md  the original design plan
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

## 18. Troubleshooting

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
| Stack fails with "no map position" | No coordinates in that area, or that client's world map is open. |
| Stack ends a few degrees off, or slowly | Low background frame rate: `/bb all console maxfpsbk 60`, and check fps in the Position column. |
| Stack turns the wrong way or strafes instead of turning | A/D aren't bound to turn left/right on that character. Set the `stack.*` keys. |
| Game blurry or stretched | Set the in-game resolution to the window size (section 14). |
| Black screen after arranging | D3D device reset. Arrange at character select, or use Restore frames. |
| Black thumbnails | DWM can't show minimized windows. Don't minimize in main_thumbs. |
| Logs | `logs\wowbox.log` (rotating). The Log tab shows INFO and up. |

## 19. Known limits

- **Not yet tested in a live game:** stacking, and modifier chords under `post`. Stacking was tested against a simulation of WoW movement at 10–60 fps. `/bb` commands, typed steps and mirrored movement have worked in a live game. If modifier chords fail on followers, use `modifier_mode: focus_swap`.
- **Mouse input** isn't broadcast or mirrored.
- **Bridge size limits:** the strip carries 54 bytes per frame, which is why chat lines and commands are cut to 27 bytes.
- **Covered windows** fall back to PrintWindow. That's slower, but works.
- **Windows only.**
