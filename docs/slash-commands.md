# WotLK slash commands for macros and automation

[← README](../README.md) · [All docs](README.md)

A *Type chat line* step (`type:` in YAML) types a line into a character's chat, so anything you could type yourself works in a WoWBox macro or rule. These are the 3.3.5a commands that matter most for boxing. EQWOW's custom spells and items follow the same rules; just use their exact names.

## Targeting and assisting

| Command | Does |
|---|---|
| `/assist Tinkerdawn` | Target whatever Tinkerdawn is targeting. The usual first line of an attack macro. |
| `/target Tinkerdawn` | Target by name. It matches the nearest name that *starts with* the text. |
| `/targetexact Tinkerdawn` | Target by exact name only. |
| `/targetlasttarget` | Go back to the previous target, e.g. after a heal on `{sender}`. |
| `/targetenemy` | Cycle to the next enemy (like Tab). |
| `/cleartarget` | Drop the target. |
| `/focus` / `/clearfocus` | Set the current target as focus / clear it. Then cast with `[@focus]`. |

## Combat

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

## Pets

| Command | Does |
|---|---|
| `/petattack` | Send the pet at your target. |
| `/petfollow` | Call it back. |
| `/petstay` | Keep it where it is. |
| `/petpassive` / `/petdefensive` / `/petaggressive` | Set its stance. |

## Movement and follow

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

## Shaman totems

Since 3.0.2 a single spell drops all four totems from the multi-cast totem bar above the action bar. Click each slot on that bar to choose the Earth, Fire, Water and Air totems for each set.

| Spell | Learned at | Does |
|---|---|---|
| `/cast Call of the Elements` | 30 | Drops totem set 1 |
| `/cast Call of the Ancestors` | 40 | Drops totem set 2 |
| `/cast Call of the Spirits` | 50 | Drops totem set 3 |
| `/cast Totemic Recall` | 30 | Removes your totems and refunds some of their mana |

It costs the mana of all four totems together. Add a *Then wait* of about 1.5 s after it before the shaman's next cast.

## Macro conditionals

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

## Group, chat and utility

| Command | Does |
|---|---|
| `/reload` | Reload the UI (needed after updating the BoxBridge addon). `/bb all reload` does it on every client. |
| `/console maxfps 144` | Set a game setting (see [Follow, stacking and movement mirroring](follow.md) for the frame-rate settings stacking needs). |
| `/invite Gnope`, `/readycheck`, `/roll` | Group basics. |
| `/p`, `/ra`, `/w Name`, `/s`, `/y` | Party, raid, whisper, say, yell. |
| `/afk`, `/dnd` | Set or clear the AFK / Do Not Disturb flags. |
| `/run <lua>` | Run Lua. It can *check* things but can't cast spells or use items: those are protected and need a real key press or a typed `/cast`. |

## Useful `/run` checks

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
Every item in your bags with its bag, slot and ID, for `/use item:12345`, `/equip item:12345` or `/use <bag> <slot>`. Equipping commands are in [Macros](macros.md), *Using items and casting spells*.

## Recipes

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
