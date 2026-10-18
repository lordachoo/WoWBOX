# The BoxBridge addon and `/bb` commands

[← README](../README.md) · [All docs](README.md)

Copy `addon\BoxBridge` to `<WoW>\Interface\AddOns\BoxBridge` and enable it on the character screen for **every** character. After updating the addon, `/reload` every client. When an update adds a file to the addon (as 1.1 added `Words.lua` and 1.3 `BoxBridgePanel.lua`), **quit and restart** the clients instead: WoW reads an addon's list of files only at start-up.

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
| `/bb rule <name>` | Turn a WoWBox automation rule on or off. The start of its name is enough: `/bb rule holy`. |
| `/bb panel` | Show or hide the party panel on this character (see below). `/bb panel alert` turns the follow warning on or off; `/bb panel reset` puts the panel back in its default place. |
| `/bb` | Turn the strip on or off. |
| `/bb status` | Show the current settings. |
| `/bb size <px>` | Block size, 2–32 (default 8). Bigger copes better with small windows. Also set `bridge.block_size` in `team.yaml` to match. |
| `/bb height <px>` | Override the render height used for pixel snapping. Rarely needed. |
| `/bb hz <n>` | Strip update rate, 1–60 (default 15). |
| `/bb chat` | Stop or resume passing chat lines to WoWBox for this client. |
| `/bb words` | Show the chat filter this client loaded (which words pass, per channel), how many lines it dropped, the watched spells and DoTs, and the target's id (the same on every character on that mob). |
| `/bb test <Name> <text>` | Pretend `<Name>` whispered `<text>`, for testing chat automation. It says so if the chat filter drops it. |

Settings are saved per character.

## The party panel (`/bb panel`)

A small frame on the character you play, showing what your team is doing without looking at WoWBox. Type `/bb panel` once on that character (it's remembered per character); drag it to move it.

```
WoWBox: BROADCAST ON  AUTO  FOLLOW
 Tinkerdawn                       ███ 100%  ██ 82%
 Kunth     -> Tinkerdawn          ███  98%  ██ 64%  Healing Wave  > Gnope ▓▓▓░
*Gnope     -> Tinkerdawn          ███  91%  ██ 40%  Shadow Bolt  > Kobold Miner
 Tinkerbow not following          ███  77%  ██ 90%  drinking
```

- **Second line: your rules** marked *Show on the in-game panel* (`panel: true`), each *ON* in green or *off* in grey. Turn them on and off with `/bb rule <name>` or their hotkey.
- **Title line: WoWBox's own state.** Broadcast on or off, automation (with *dry run*), *rules PAUSED* (a client's bridge can't be read), follow mode, mirroring. It changes when the state does. Until WoWBox has sent it: *no state yet*.
- **Follow:** who each character is following, or *not following* in orange. When someone stops following, the panel waits 2 seconds (follow mode re-follows by itself), then **warns**: a raid warning (*Kunth stopped following*), a sound and a flashing row. No warning when WoWBox's follow mode is off (you told them to stay), or for the dead. `/bb panel alert` turns it off.
- **Casting:** the spell, its target and a cast bar. When an automation types `/cast [@Gnope] Healing Wave` into Kunth, you see *Healing Wave > Gnope* at once. Instant casts show for a moment; failed or interrupted casts say *(failed)*.
- **Health and mana bars,** *DEAD*, *drinking*, a red `*` in combat, and a dimmed row with *far* when they're out of range.

How it gets there:
- **The party's own information** comes from each client's BoxBridge, in hidden addon messages to the party (nothing in chat). So every client needs BoxBridge 1.3 for its follow and cast targets to show; health, mana and casting come from the game for everyone.
- **WoWBox's state** can only reach the game as key presses: WoWBox spells it out on three spare keys BoxBridge binds, **Ctrl+Alt+Shift+F7, F8 and F9**, sent only to clients showing the panel and only when the state changes (15 presses). The panel answers through the bridge with what it got, so presses lost while you were typing there are sent again. Don't bind those keys to anything else. This needs `broadcast.modifier_mode: post` (the default); with the other modes the title line stays at *no state yet*.

## The chat filter (`Words.lua`)

The strip can carry only about two chat lines a second to WoWBox, and BoxBridge keeps just 10 waiting. In a busy raid, raid chat, /say and /yell alone outrun that, so a "paladin buff" could be pushed out before WoWBox ever saw it.

So WoWBox writes the words and channels of your enabled chat rules into `Words.lua` in the addon's folder in the game, and BoxBridge passes on **only lines a chat rule could match**. Everything else is never queued.

- **Where:** `Interface\AddOns\BoxBridge\Words.lua` next to `client.exe` from `team.yaml`. If the addon lives somewhere else, set `bridge.addon_dir`.
- **When it's written:** when WoWBox starts, when you save a rule, and on Reload config. Only when the words or channels change.
- **When a client uses it:** at login or `/reload`. After you change a chat rule's words, type `/bb all reload` when it suits you. Until then, the banner says *Chat filter out of date on …*. To have WoWBox do the `/reload` itself, out of combat, tick *Settings → Chat filter → /reload clients when the word list changes* (`bridge.chat_filter_reload`).
- **Matching:** the same as the rules: any case, the word anywhere in the line. A rule without words takes every line on its channels. `/bb` commands always pass.
- **Long lines:** WoWBox sees 27 bytes of each line. When a word matches, BoxBridge sends it as a number followed by the 25 bytes after it, wherever it is in the line. So "sorry to bother you, could I get paladin buff" still works, and a name or item after the word isn't crowded out by the word itself (see *Arguments* in [Automation](automation.md)).
- **When it's still too busy:** whispers, party lines and `/bb` commands go ahead of other channels, and a full queue throws away the least important line. Dropped lines show in the Activity tab (*BoxBridge dropped 3 chat lines on Kunth*) and in `/bb words`.
- **Off:** untick *Settings → Chat filter → Pass on only lines a chat rule could use* (`bridge.chat_filter: false`). A client without `Words.lua`, or with an older BoxBridge, passes every line, as before.
