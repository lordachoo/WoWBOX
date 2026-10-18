# Key broadcasting and in-game team commands

[← README](../README.md) · [All docs](README.md)

## Key broadcasting and routes

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

## Team commands from inside the game

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
- **Who:** `all` and `others` reach every attached client, even ones with Broadcast unticked. `to <who>` takes any target from [Targets: choosing which characters](team.md) except lists. Targets are seen from the client you typed in, so `/bb to followers …` means everyone but you.
- **How it gets there:** BoxBridge shows the command on its strip for half a second, WoWBox reads it, and types `Enter, command, Enter` into each target client. That takes about a quarter to half a second. The client you typed the command in gets it last, about 0.35 s after its chat box has closed, so your own Enter key can't get mixed into it.
- **Length limit:** a command can be up to 27 bytes, slash included. That covers `/assist Tinkerdawn` and `/cast Shadow Bolt`. For longer things, use a WoWBox macro ([Macros](macros.md)) or an in-game macro.
- **Protected spells** (`/cast`, `/target`) work, because they are really typed into the chat box, just as if you typed them.
- **Turning it off:** set `bridge.commands: false` to ignore all `/bb` team commands.
