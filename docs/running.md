# Running WoWBox

[← README](../README.md) · [All docs](README.md)

```powershell
.\wowbox.bat                       # from source
dist\WoWBox\wowbox.exe             # standalone build ([Building the standalone exe](development.md))
```

| Option | |
|---|---|
| `--config PATH` | use another config file (default: `config\team.yaml`) |
| `--no-attach` | don't touch running clients on start. Press **Attach running** when ready. |
| `--check-config` | validate the config and exit |
| `--log-level DEBUG` | more detail in the log |

On start, WoWBox attaches to every running `Wow.exe` window. It puts each window in its slot (by character name once the bridge reports it), renames the windows, removes their borders and applies the default layout.

## The window

**Top row:**

| Control | |
|---|---|
| **Broadcast ON/OFF** | Master switch for key broadcasting (ScrollLock). Turning it off releases every key. |
| **Follow ON/OFF** | Follow mode (F10 on, F11 off). [Follow, stacking and movement mirroring](follow.md). |
| **Stack** | Everyone walks onto the spot and facing of the window you're in (F12). Press again to cancel. |
| **Mirror movement** | Copy movement keys to followers (Ctrl+Alt+M). |
| **Automation ON/OFF**, **Dry run** | Automation always starts OFF, in dry run. |
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
| **Automation** | Every rule with its trigger and actions, plus live columns: **Status** (waiting, cooldown 8s, running step 2/4, disabled, automation OFF), **Next** (timers get a countdown bar, e.g. *in 12m 05s*; state rules say whether the condition is true now and why not; chat rules wait for a line), **Last fired** and **Fired** (count). **New / Edit / Duplicate / Delete** open the rule editor (double-click edits too). The *On* box enables or disables a rule, and the change is saved at once. **Group by** sorts the rules under collapsible headers: *Who acts* (the character a rule's steps go to: `class:SHAMAN`, `Kunth`, `role:healer` and `[3]` all land under the same one; steps for several characters or `all` go under *Several characters / everyone*), *Trigger* (chat keywords, timers, state), *On/off*, or *None* (a flat list). A header's box turns every rule it shows on or off. **Search** filters as you type: every word must appear in the rule's name, trigger (keywords, senders) or actions (spells), e.g. `kings` or `heal kunth`. The grouping and which groups are collapsed are remembered. |
| **Activity** | The live dashboard of rules, macros and `/bb` commands: each character's state and last action, the live queue, and a feed of recent activity. [Automation](automation.md), *The Activity tab*. |
| **Settings** | The options that tune automation, the typing guard, follow and stacking, each with an explanation. Changes apply at once; **Save to team.yaml** keeps them (only those lines change, comments stay), **Revert** goes back to the file. See [The Settings tab](#the-settings-tab) below. |
| **Log** | Everything WoWBox does: keys typed, rules fired, chat lines seen, `/bb` commands. The same goes to `logs\wowbox.log`. |

A tray icon offers the same switches. Its dot is green while broadcasting and red while not, with a yellow ring while automation is on. Closing the window quits WoWBox.

## The Settings tab

The settings you're most likely to tune while playing, grouped, each with a one-line explanation:

| Group | Settings |
|---|---|
| **Automation: queue and turn-taking** | queue per chat rule, queued request expiry, gap between rules on one character, wait for a cast in progress (and the most it waits) |
| **Automation: who counts** | ignore dead characters, ignore characters too far away, pause rules on an unreadable client |
| **Automation: start-up and speed** | automation ON at start, dry run at start, checks per second |
| **Typing guard** | pause broadcasting while you type, how long typed lines wait for you, quiet time after your last key, broadcast only from WoW windows, how Shift/Ctrl/Alt chords reach other clients |
| **Follow and mirroring** | only mirror to followers who are with you, max distance (also used by *ignore characters too far away*), re-follow and its timing |
| **Stacking** | position and facing tolerance, max distance, timeout, re-stack when you stop moving |

- **Live:** a change applies the moment you make it; the engine, router and stacker read these same values.
- **Wrong input** (e.g. `soon` as a duration) turns the box red and isn't applied. The message says why.
- **Not saved yet:** changed settings are shown in **bold** and listed at the top until you save. They're lost on restart.
- **Save to team.yaml** writes only those lines, in place. A comment at the end of a line stays where it was, and a setting that isn't in the file yet is added at the end of its section. The old file is kept as `team.yaml.bak`.
- **Revert to team.yaml** puts every setting back to what the file says.
- **Reload config** now also re-reads `broadcast` and `automation_options`, so hand edits to them no longer need a restart.
- **The Activity tab** shows the ones that explain the queue on one line at the top (*In play: queue 5 per rule · expires 1m · rule gap 1.5s · …*), with a link to this tab.

## Hotkeys

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
