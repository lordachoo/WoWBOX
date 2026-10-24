# Troubleshooting and known limits

[← README](../README.md) · [All docs](README.md)

## Troubleshooting

| Problem | Fix |
|---|---|
| Keys don't reach other clients | Run WoW and WoWBox at the same privilege level. The log says *access denied* when that's the cause. |
| A character keeps running / key stuck | Ctrl+Alt+Pause (release all). Turning broadcast off also releases everything. |
| Modifier binds don't fire on followers | `broadcast.modifier_mode: focus_swap`, or binds without modifiers. |
| Bridge: `strip colours altered by the game (Vulkan renderer?)` | **The client is in Vulkan mode: switch it to DirectX (DX).** EQWOW's Vulkan renderer brightens the strip until the data is lost; DX draws it exactly. |
| Bridge: `no BoxBridge header` | Addon not enabled for that character, UI hidden (Alt+Z), or something covers the top-left corner. |
| Bridge: `checksum failed` / `blocks visible` | Something is drawn over the strip, the window is too small (`/bb size 12`), or a client still runs the old addon (`/reload`). |
| Bridge stale on a minimized client | Minimized clients don't draw. Use main_thumbs or tabs instead of minimizing. |
| Character in the wrong slot | Move up/down, then Remember characters. |
| A rule or command was "skipped: typing" | That client's chat box was open. Close it and try again. |
| A chat rule fires only sometimes in a raid | `/bb words` in that client: is the word listed for that channel? If not, `/reload` it (the banner says when the filter is out of date). Does the Activity tab show *dropped* lines? Is the rule listening on the channel you typed in (`/raid` vs `/party`)? |
| Chat rules and `/bb` stop working while WoWBox is minimized, and work again once you open it | Windows was slowing WoWBox down in the background (efficiency mode), so it missed the half second each line is shown. WoWBox now opts out of that at start-up; if the Log tab still says *seconds between reads*, check Task Manager for efficiency mode on WoWBox, or whether the PC is busy. |
| `/bb all …` does nothing | Is WoWBox running and attached, and does the Clients tab show live stats for that client? Is the command ≤ 27 bytes? Check the Log tab. |
| Follow mode doesn't re-follow | Does the State column show `following` while a character follows? If not, `/reload` that client for the current addon. Re-follow also waits during combat or casting, and for the window you're in. |
| Mirrored movement does nothing in background windows | That client ignores posted movement keys. Use follow mode without mirroring. |
| Stack says "facing matched only" | No coordinates in that area (many instances), or that client's world map is open. Facing is matched; use follow to get close. |
| Stack ends a degree or two off | That's half a frame of turning at that client's frame rate (±1.5° at 60 fps). Run at 144 fps: `/bb all console maxfps 144`, `/bb all console maxfpsbk 144`, `/bb all console gxVSync 0`. Check the fps in the Position column. See *Frame rate and stacking precision* ([Follow, stacking and movement mirroring](follow.md)). |
| Stack turns the wrong way or strafes instead of turning | A/D aren't bound to turn left/right on that character. Set the `stack.*` keys. |
| Game blurry or stretched | Set the in-game resolution to the window size ([Layouts](configuration.md)). |
| Black screen after arranging | D3D device reset. Arrange at character select, or use Restore frames. |
| Black thumbnails | DWM can't show minimized windows. Don't minimize in main_thumbs. |
| WoWBox freezes, or the whole PC lags while it runs | Restart WoWBox, then send `logs\wowbox.log` and `logs\crash.log` (tray menu: *Open logs folder*). Look for *stuck for*, *keyboard hook didn't answer* and *didn't shut down cleanly*: each has the stack of every thread at that moment. See *Diagnostics* below. |
| Logs | `logs\wowbox.log` (5 MB, 5 old copies kept). The Log tab shows INFO and up. |

### Diagnostics

WoWBox keeps notes so a freeze, crash or slowdown can be explained afterwards. Everything goes to the `logs` folder next to `config` (tray menu: **Open logs folder**):

| What | Where | When |
|---|---|---|
| **Stuck loops**: the UI, the bridge reader, the deferred-send loop and the key sender are checked twice a second. One stuck too long (3 s for the UI, 5 s for the bridge and deferred sends, 30 s for one send) gets every thread's stack logged, then a *moving again after N s* line. | `wowbox.log` (WARNING) | As it happens, and again every 30 s while still stuck |
| **Keyboard hook too slow**: WoWBox's key hook sees every key on the PC, so while it's held up, typing lags everywhere. If a test key doesn't get through within 1 s, every thread's stack is logged before the hook is reinstalled. | `wowbox.log` | As it happens |
| **Bridge dropouts**: one line when a client becomes unreadable, with why (frame counter stuck, or the decode error), the capture method, how long the capture took, and the time since the previous read. Another line when it's readable again. Paused rules get one line per pause, not one per rule. | `wowbox.log` (INFO) | As it happens |
| **Health**: uptime, CPU, memory, handles, GDI/USER objects, stalls, the worst UI delay, bridge reads and capture times per client, dropouts, the worst key-hook delay, and the number of errors and warnings logged. WoWBox warns by itself when GDI/USER objects pass 5,000 (Windows stops at 10,000), when handles pass 10,000, or when memory has grown by 1 GB. | `wowbox.log` (*health:*) | Every 5 minutes, and at exit |
| **Hard crashes, and a fully frozen process**: Python's fault handler writes every thread's stack if WoWBox dies inside a Windows call, or if nothing has run for a minute. | `crash.log` | As it happens |
| **Unclean exits**: a running WoWBox leaves `wowbox-<pid>.running` in the folder. If one is still there at the next start, that WoWBox crashed, froze and was ended, or Windows closed it. The new one logs that, with whatever `crash.log` holds for it, and the Activity tab shows it. | `wowbox.log` | At the next start |
| **Errors nobody caught** (in a button handler or a thread) are logged with their traceback instead of vanishing, and so are Qt's own warnings. | `wowbox.log` (CRITICAL / `qt`) | As it happens |

## Known limits

- **Not yet tested in a live game:** modifier chords under `post`. Stacking has been used in a live game; at 144 fps it gets facing within about a degree. `/bb` commands, typed steps and mirrored movement have worked in a live game. If modifier chords fail on followers, use `modifier_mode: focus_swap`.
- **Mouse input** isn't broadcast or mirrored.
- **Bridge size limits:** the strip carries 54 bytes per frame, which is why chat lines and commands are cut to 27 bytes.
- **Covered windows** fall back to PrintWindow. That's slower, but works.
- **Windows only.**
