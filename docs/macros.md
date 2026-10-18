# Macros

[← README](../README.md) · [All docs](README.md)

Broadcasting sends the *same* key everywhere. A macro sends *specific* things to *specific* characters when you press its hotkey in any WoW window, or press Run in the Macros tab. Pressing the hotkey again stops a macro that's still running.

## The macro editor

In the **Macros** tab, use **New**, **Edit** (or double-click a macro), **Duplicate** or **Delete**.

- **Name** and **Note:** free text. Names must be unique.
- **Hotkey:** click the field, then press the combination (e.g. Ctrl+Alt+H). Backspace or Clear removes it. If the combination already belongs to another macro or a WoWBox hotkey, the editor says so and won't save. While the editor is open, WoWBox hotkeys are paused, so recording F12 doesn't also stack your team.
- **Steps:** one row per step, run top to bottom. **Add step** inserts below the selected row and copies its *To* and *Then wait*. **Remove**, **Move up** and **Move down** do what they say. The columns are:
  - **Step:** what kind of step it is. *Type chat line*, *Press key*, *Press keys* (comma-separated, *Delay* apart), *Wait*, or *Start macro*.
  - **Value:** the chat line, key, key list, duration (`300ms`, `1.5s`) or macro name.
  - **To:** pick from your characters, `all`, `followers`, `leader`, `not:leader`, `class:…`, `role:…`, or type anything from [Targets: choosing which characters](team.md). For slots, type something like `[2, 3]`.
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

## Example: send the pets (Ctrl+1, no in-game macro needed)

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

## Example: buff the whole group (Ctrl+2)

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

## Using items and casting spells

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

More slash commands for typed steps (targeting, `/startattack`, pets, totems, conditionals, `/run` checks): [WotLK slash commands for macros and automation](slash-commands.md).
