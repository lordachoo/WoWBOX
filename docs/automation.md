# Automation

[← README](../README.md) · [All docs](README.md)

Rules that act without a keypress. Automation always starts **OFF** and in **dry run**: dry run logs what it would send and sends nothing. Turn it on with the button, untick Dry run when you trust it, and kill it with Ctrl+Alt+K.

## The rule editor

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

## State rules: `when:`

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
- **`ready: <spell>`:** that spell could be cast now: known, off its cooldown *and* the global cooldown, with enough mana or rage, and for a spell cast at your target, the target in range (*spell ready* in the editor's field list). `ready_ne` is the opposite; a list (`ready: [Holy Shield, Consecration]`) needs all of them. See *Keep a spell on cooldown* below.
- **`dot: <spell>`:** that DoT should go on the target now: castable (as for `ready:`), and not on the target from *you*, or with less than 2 seconds left (*DoT needed* in the editor). See *Keep DoTs up* below.
- **`target_range: <yards>`:** how far the target is, measured by the game in steps: 10, 28, or 100 (farther, or no target); e.g. `target_range_lte: 10`. An older BoxBridge reports 999 (unknown). See *Fight only the tank's mob* below.
- **`cant_hit: true`:** the game just refused a swing or cast at the current target: *You are facing the wrong way!*, *You are too far away!*, *Target needs to be in front of you.* or *Out of range.* It stays true for 3 seconds, and only while the target stays the same. Needs BoxBridge 1.5. See *Running into melee* below.
- **`same_target: <character>`:** this character's target is the same mob as that character's (a name or a slot number; *same target as* in the editor). `same_target_ne:` is the opposite: it has a target, but not that one's. See *Fight only the tank's mob* below.
- **Who matched: `{name}`.** When the condition matches, the steps can use the character it matched:

  | Placeholder | Filled with |
  |---|---|
  | `{name}` | the matched character's name, e.g. `Gnope` |
  | `{slot}` | its slot number |
  | `{class}` | its class, e.g. `WARLOCK` |
  | `{health}`, `{power}` | its health and power % (for messages like `/p {name} is at {health}%`) |

  If several characters match, it's the one with the lowest health (or lowest power, if the condition is about power), otherwise the first. Example: heal whoever is low, in one rule:
  ```yaml
  - name: heal-whoever-is-low
    when: { slot: any, health_pct_lt: 50 }
    do: { type: "/cast [@{name}] Healing Wave", to: "class:SHAMAN" }
    cooldown: 3s
  ```
  The same placeholders work in a chat rule that also has a `when:`.
- **Only characters near the action count** (`skip_far`, on by default). A character in another zone, or farther than `follow.mirror_max_distance_yards` (40 yd) from every character the rule acts on, is ignored by that rule's condition. Someone hurt in another zone doesn't make Kunth chain-heal forever. The Next column says so, e.g. *Gnope too far from Kunth: in another zone*. Inside instances without map coordinates only the zone is compared.
- **Dead characters don't count either** (`skip_dead`), unless the condition asks about `dead` itself.

## Keep a spell on cooldown: `ready:`

Example: keep the tank's **Holy Shield** up in big fights, turned on and off from inside the game. Rather than guessing with a timer, the rule asks the bridge whether the spell is ready:

```yaml
  - name: HOLY SHIELD UP
    enabled: false                       # off until a big fight
    when: { slot: 1, ready: Holy Shield, combat: true, target_hostile: true, casting: false }
    do: { type: /cast Holy Shield, to: "class:PALADIN" }
    cooldown: 2s
    hotkey: Ctrl+Alt+A                   # press it to turn the rule on or off
    panel: true                          # the in-game panel shows HOLY SHIELD UP ON / off
```

In the editor: Trigger *State: when a condition is true*, Who `1` (the tank's slot), conditions *spell ready* = `Holy Shield`, *in combat* = yes, *target is hostile* = yes, *casting* = no; one step typing `/cast Holy Shield` to `class:PALADIN`; *On/off hotkey* Ctrl+Alt+A; tick *Show on the in-game panel*.

**Using it:**
1. Save the rule, then `/bb all reload` once so the clients load the spell list (the banner reminds you).
2. Check on the tank with `/bb words`: it lists `watched spell: Holy Shield (ready)`, and *not ready* right after you cast it.
3. Make sure automation is **ON** and **Dry run** is unticked (the panel's title shows **AUTO** in blue). Automation always starts OFF, in dry run, when WoWBox starts.
4. In a big fight press **Ctrl+Alt+A**, or type **`/bb rule holy`**. The panel shows *HOLY SHIELD UP ON*. From then on, whenever you're in combat with a hostile target and Holy Shield is ready, WoWBox casts it. Press or type it again to turn it off.

How it works:
- **How it knows:** WoWBox writes the spells your rules ask about into `Words.lua` (`spells`, up to 7; rules that are off count too, so turning one on in a fight needs no `/reload`), and BoxBridge reports which of them could be cast now (known, off its cooldown and the global cooldown, enough mana, target in range for spells cast at it), about 10 times a second. The range check matters: a shot from too close fails without going on cooldown, so without it the spell would stay "ready" and the rule would fire over and over. After adding or changing a `ready:` spell, `/bb all reload`. Until the client has loaded the new list, the condition is false.
- **The spell's name** as the game spells it; any case.
- **Typing or a key:** `/cast Holy Shield` works, but briefly opens the chat box in the window you're playing. If that gets in the way, put the spell on an action bar slot with a keybind (e.g. Ctrl+Shift+F5) and have the step `send:` that key instead. Either way the step waits while you're holding keys there.
- **On and off from the game:** the rule's **On/off hotkey** (`hotkey:`) or `/bb rule <name>`, where the start of the name is enough. With *Show on the in-game panel* (`panel: true`) the panel's second line says whether it's on.
- **Why it isn't firing:** the rule's *Next* column in the Automation tab says which condition isn't true, e.g. *Holy Shield not ready* or *combat is no*.
- The same works for any "keep it rolling" spell: Avenger's Shield, Hammer of the Righteous, a hunter's Kill Shot when it lights up.

## Keep DoTs up: `dot:`

A DoT has no cooldown, so `ready:` would recast it every few seconds. `dot:` asks whether *your* DoT is on the target instead. Example: the warlock (slot 2) joins the tank's fight and keeps his DoTs up:

```yaml
  - name: NEC assist                      # the tank's mob is fighting: target it too
    when: { slot: 1, combat: true, target_hostile: true, target_in_combat: true }
    do: { type: /assist Tinkerdawn, to: "class:WARLOCK" }
    cooldown: 5s

  - name: NEC corruption
    when: { slot: 2, target_hostile: true, target_in_combat: true, same_target: Tinkerdawn, dot: Corruption,
            casting: false, channeling: false }
    do: { type: /cast Corruption, to: "class:WARLOCK" }
    cooldown: 6s
    priority: 5
```

- **One rule per DoT.** `priority:` decides which goes first when several are needed (the rules take turns, 1.5 s apart: `rule_gap`).
- **Mine only:** another warlock's Corruption on the mob doesn't count. A DoT with less than 2 seconds left counts as gone, so it's renewed as it runs out.
- **The debuff must have the spell's name.** If a spell's debuff is called something else on your server, the DoT never looks "on" and the rule recasts it every cooldown; `/bb words` (with a target) shows each watched DoT as *on your target*, *NEEDED* or *not castable now*.
- **Up to 14 DoTs**, written into `Words.lua` (`dots`) like the watched spells; after adding one, `/bb all reload`.

## Fight only the tank's mob: `same_target:`

`/assist` can leave a character on the wrong mob: one the tank only clicked or tabbed to, or an old target when the assist didn't take (the tank had no target at that moment). That mob is "hostile" and soon "in combat" too, so `target_hostile` and `target_in_combat` can't tell it from the tank's, and a pet sent at it brings the whole pack back. Every BoxBridge (1.4 or newer) shows which mob its target is, so WoWBox can compare:

```yaml
  - name: NEC re-assist                   # on a fighting mob that isn't the tank's: take the tank's again
    when: { slot: 2, target_hostile: true, target_in_combat: true, same_target_ne: Tinkerdawn }
    do: { type: /assist Tinkerdawn, to: "class:WARLOCK" }
    cooldown: 3s

  - name: NEC pet attack                  # the pet only goes for the tank's mob, and only when it's close
    when: { slot: 2, target_hostile: true, target_dead: false, target_in_combat: true, same_target: Tinkerdawn,
            target_range_lte: 10 }
    do: { type: /petattack, to: "class:WARLOCK" }
    cooldown: 5s
```

- **Add it to every rule that attacks** (pet, shots, DoTs). The Next column says why one waits: *not Tinkerdawn's target*, *Tinkerdawn has no target*, *no target*.
- **Guard the casts themselves: `/cast [harm,nodead] Corruption`** (quote it in YAML: `type: '/cast [harm,nodead] Corruption'`). Cast with no live hostile target, a spell makes the game pick the nearest mob, and the pet runs after it; with `[harm,nodead]` the game casts nothing instead. Send the pet with plain `/petattack`: it does nothing without a target, and on EQWOW `/petattack [harm,nodead]` was ignored and left the pet idle.
- **A rule that waited its turn is checked again.** When ten DoT rules fire together they go out one by one (`rule_gap`). Each state rule's condition is checked again when its turn comes, and dropped if it no longer holds (the mob died meanwhile); the Activity tab says *dropped ...: condition no longer true*.
- **`target_dead: false`:** a corpse stays hostile, and "in combat" for a few seconds, and the tank often keeps it targeted. Without this the rules keep casting at it.
- **Distance: `target_range`.** The game measures how far the target really is, in steps: `10` (within about 10 yards), `28` (about 28), `100` (farther, or no target). `target_range_lte: 10` keeps the pet home unless the mob is close. Don't use `ready:` as a range check for this: the game's spell range check doesn't know about every aura (on EQWOW an aura that shortens a warlock's spell range still reports *in range* at full range); `/bb words` shows the distance and what the game says.
- **Running into melee: `target_range_gt: 10` and `cant_hit`.** The game can't tell an addon anything closer than about 10 yards, and a character inside 10 yards may still be too far to swing, or facing away. So run in on either signal, with two rules that press Interact With Target (see *Running a character into melee range* in [slash commands](slash-commands.md)). It runs to the target and turns to face it:

  ```yaml
    - name: NEC run in                    # the tank's mob is out of reach
      when: { slot: 2, target_hostile: true, target_dead: false, target_in_combat: true, same_target: Tinkerdawn,
              target_range_gt: 10 }
      do:
        sequence:
          - { send: Ctrl+Shift+I, to: "class:WARLOCK" }
          - { wait: 750ms }
          - { type: /startattack, to: "class:WARLOCK" }
      cooldown: 5s
      priority: 20

    - name: NEC run in - can't hit        # close, but the game says facing the wrong way / too far away
      when: { slot: 2, target_hostile: true, target_dead: false, same_target: Tinkerdawn, cant_hit: true }
      do: { send: Ctrl+Shift+I, to: "class:WARLOCK" }
      cooldown: 2s
      priority: 20
  ```

  Add `cant_hit: false` to the DoT rules too: starting a cast stops Interact's run, and the casts would only fail again. `cant_hit` needs something to fail first: `/startattack` on, or a cast. The game's wording varies ("Out of range" or "Out of range.", custom servers' own text), so BoxBridge matches the words *out of range*, *too far away*, *facing the wrong way* and *front of you* / *infront* (EQWOW says "Target needs to be infront of you") anywhere in the error; `/bb words` shows the last game error and whether it counted. Don't make the run-in depend on `following: true`: Interact takes the character off follow, so it would run in only on the first pull after each `/follow`.
- **Every client needs BoxBridge 1.4** (1.5 for `cant_hit`). An older one reports no target, so `same_target` is never true there and those rules wait. `/bb words` shows the target's id: two characters on the same mob show the same number.

## Timers: `every:`

```yaml
  - name: rebuff
    every: 25m
    do: { sequence: [ { send: "F5", to: all }, { wait: 1.5s }, { send: "F6", to: all } ] }
```

## Chat rules: `chat:`

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
- **Arguments:** what comes after the matched word, so one rule can act on someone or something named in the line (see *Arguments: "heal gno", "disenchant all …"* below):

  | Placeholder | From "heal on gno pls" | From "disenchant all Runecloth Bracers" (word `disenchant all`) |
  |---|---|---|
  | `{who}` | `Gnope` (who to act on) | |
  | `{arg}` | `on` (the first word) | `Runecloth` |
  | `{arg2}` | `gno` (the second word) | `Bracers` |
  | `{rest}` | `on gno pls` (all of them) | `Runecloth Bracers` |

  `{x|y}` falls back to `y` when `x` is empty: `[@{arg|sender}]`.
- **Placeholders can't inject commands.** Names (`{sender}`, `{who}`, `{arg}`, `{arg2}`) keep only letters, digits and `-`. Other text loses control characters and WoW escape codes, and can't start a slash command; `{rest}` also loses `[`, `]` and `;`, so it can't add conditions or alternatives to a `/cast` or `/use`. Still, use `sender:` to ignore strangers.
- **Heard by several clients.** A party or raid line all your clients hear fires a rule once.
- **Limits.** 27 bytes of each line come through. When one of the chat filter's words matches, BoxBridge sends the word as a number and then the 25 bytes *after* it, so a name or item name after the word gets the room (needs the current BoxBridge, and WoWBox puts the word back). Without a chat filter, it's the first 27 bytes. Senders get 20 bytes. The addon shows each line for 0.5 s, so a burst of 10 lines takes about 5 s, and status updates pause meanwhile. That's why BoxBridge passes on only lines your rules could match: see *The chat filter* in [The BoxBridge addon and /bb commands](addon.md).
- **New words need a `/reload`.** A new or changed chat rule's words reach the clients' chat filter on their next `/reload` (`/bb all reload`). The banner tells you when one is out of date.
- **Testing.** `/bb test Tinkerdawn heal me` in Kunth's client acts like a whisper from Tinkerdawn.

## Arguments: "heal gno", "disenchant all …"

`{who}` works out who "someone" is in a line like "heal &lt;someone&gt;", from the words after the matched one:

1. Filler words are skipped: on, for, to, at, pls, plz, please, now, thx, ty, thanks.
2. Nothing left, or `me` / `self` / `myself` / `i`: the **sender**.
3. `target`, `focus`, `mouseover`, `pet`, `player`, `targettarget`: used as they are, so `[@target]` is the caster's own target.
4. A **role**: `tank`, `healer` or `dps` mean the character of the only slot with that role in `team.yaml`.
5. An **alias** from `team.aliases` in `team.yaml` (these win over roles):
   ```yaml
   team:
     aliases: { mt: Tinkerdawn, lock: Gnope }
   ```
6. One of your characters' names, or the **start of exactly one**: "gno" is Gnope.
7. Anything else, as typed: someone else in your group.

```yaml
  - name: heal-on-request
    chat: { channel: [whisper, party], sender: team, contains: heal, heard_by: Kunth }
    do: { type: "/cast [@{who}] Healing Wave", to: Kunth }
    cooldown: 3s
    cooldown_per: target      # healing Gnope doesn't hold up a heal on Tinkerdawn
```

| Line | `{who}` |
|---|---|
| "heal", "heal me pls" | the sender |
| "heal gno", "heal on Gnope" | Gnope |
| "heal tank" | Tinkerdawn (the slot with role tank) |
| "heal target" | `target`: Kunth's own target |
| "heal Bob" | Bob |

- **`cooldown_per: target`** (*Cooldown per target* in the editor) keeps the cooldown separately for each `{who}`. A request for someone still on cooldown waits in the queue; requests for others go first.
- **Item names:** `{rest}` is everything after the word: `/use {rest}`. 25 bytes of it arrive, so very long names are cut; the log and the editor's Test say so.
- **Test it in the editor:** type a line in *Test line* (e.g. `heal on gno pls`) and press *Test*: it shows what `{who}` and the others became and the exact line it would type, without sending anything. The log also says what a firing understood, e.g. `whisper from Kunth: 'heal on gno' -> {who}='Gnope'`.

## Recipes

Complete rule sets to copy, in [Automation recipes](automation-recipes.md):

- [Heal and buff bot on whisper](automation-recipes.md#recipe-heal-and-buff-bot-on-whisper): Kunth heals and buffs whoever asks.
- [Group ports on a party keyword](automation-recipes.md#recipe-group-ports-on-a-party-keyword-port-lavastorm): "port lavastorm" in party, and Kunth announces it and casts it.

Recipes for anti-AFK, running in to melee and totems are in [WotLK slash commands for macros and automation](slash-commands.md).

## Watching the rules run

The **Automation** tab updates four times a second:

| Column | Shows |
|---|---|
| **Status** | `waiting` (green), `cooldown 8s` or `running step 2/4` (orange), `disabled`, `automation OFF`, `paused: …` (grey) |
| **Next** | **Timers:** a countdown bar filling up to the next firing, e.g. *in 12m 05s*. **State rules:** *now: condition is true*, or *when the condition is true (power_pct is 62, needs < 40)*. **Chat rules:** *on a matching chat line*. |
| **Last fired** | how long ago, e.g. *2m 10s ago*, or *never* |
| **Fired** | how many times since WoWBox started |

Long triggers and actions are cut off with "…". Hover over them to see all of it.

## The Activity tab

A live dashboard of everything rules and macros do. It updates four times a second while it's open.

- **Characters:** one row per client.
  - **State:** `ok` with HP and power, `casting`, `channeling`, `typing`, `DEAD`, or `no bridge data`.
  - **Queue:** how many things are running, waiting or queued for that character.
  - **Last action:** the last line or key a rule or macro sent it, which rule or macro sent it, and how long ago.
- **Now** (left, below the characters): per character, in the order things will go. Running and waiting rows say how long the rule has been running, e.g. *running 3s · step 2/9: …*

  | State | Means | Detail shows |
  |---|---|---|
  | **running** (green) | a rule going through its steps | the next step, and how long until it, e.g. *step 3/9: type '/cast Might' -> Kunth · in 1s* |
  | **macro** (purple) | a macro running | its next step and how long it's been running |
  | **waiting** (orange) | a step held back | why: *Kunth casting*, *global cooldown after 'buffs'*, or *'totems' goes first* (priority) |
  | **queued** (blue) | a chat request waiting for its rule to be free | its place, how long ago it was asked and when it expires, e.g. *#2 · asked 4s ago · expires in 56s* |

  The rule holding a character's turn is listed first among the waiting ones, and a rule's priority is shown next to its name. Typed lines waiting for you to stop typing in a window are listed too.
- **What ran** (right, beside *Now*): the last 300 events, newest first, with a live **Ago** column (*4s ago*, *2m 10s ago*) and the time.
  - **What's in it:** rules firing (with the chat line that triggered them, the steps and the characters they act on), macros starting (with their steps) and finishing, requests queued, and anything skipped or expired.
  - **`/bb` too:** every team command (who typed it, to whom, what, e.g. *from Tinkerdawn · all: /reload*), `/bb stack/follow/stay/mirror`, and how each character's stack ended (*stacked*, *too far away*, *facing matched only*). A `/bb` command also counts as that character's last action in the Characters table.
  - **Each step** (off by default) adds every single line or key sent and to whom.
  - **Colours:** fired is green, queued blue, skipped red, done grey.
  - **Filters:** tick boxes show or hide **Rules**, **Macros**, **/bb**, **Each step** and **Queue and skips**. **Clear** empties the list. Hover over a cut-off line to read all of it. **Timers start when automation is turned on:** a timer never fires the moment you switch automation on, even if it came due while automation was off.

## Common to all rules

- `cooldown:` is the minimum time between firings. A state rule fires again at most once a second even without one, so a cast that keeps failing can't flood a client.
- `enabled: false` keeps a rule in the file but switched off.
- **Editing `automation.yaml` by hand** while WoWBox runs is safe: WoWBox notices within a second and loads it (the Activity tab says *automation.yaml changed on disk*), and it never saves over a newer file. If your edit has an error, WoWBox says so in the Log tab and won't save rules until it's fixed.
- A rule that's still running its sequence won't start again (chat rules queue the request instead).
- **Two rules on one character take turns.** When a step from one rule wants a character that another rule just used, it waits for both of these:
  - **The global cooldown:** `rule_gap` (1.5 s) after the other rule's last step there.
  - **The current cast:** the cast or channel in progress (a 2.5 s Chain Heal, Drain Soul) has finished, as read from the bridge. A stuck flag holds it for at most `cast_wait_max` (10 s).

  A totem or heal request in the middle of a chain-heal loop goes out as soon as the current Chain Heal lands. The loop then continues after it. A rule's own steps never wait for its own casts: its *Then wait* gaps handle that. Set `rule_gap: 0` and `wait_for_casts: false` to turn this off.
- **On/off hotkey** (`hotkey:`, the editor's *On/off hotkey*): a key that turns the rule on or off while WoW or WoWBox has focus. **`/bb rule <name>`** in the game does the same for any rule (the start of its name is enough; if it fits several, nothing changes and the Activity tab says which).
- **Show on the in-game panel** (`panel: true`): the party panel's second line shows the rule and whether it's on (up to 8 rules).
- **Priority** (`priority:`, the editor's *Priority* box, default 0) decides who goes first when several rules wait for the same character: higher first, equal = first come, first served. Give totems `priority: 10` and they jump ahead of a waiting heal. It doesn't interrupt a cast already in progress.
- **Dead characters are left alone** (`skip_dead`, on by default):
  - A dead character or ghost never matches a condition, so `health_pct < 70` doesn't fire forever for a dead team.
  - Steps aren't sent to dead characters; a step whose characters are all dead is skipped.
  - A condition that asks about death itself (`dead = yes`, e.g. a resurrect rule) still sees them.
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
