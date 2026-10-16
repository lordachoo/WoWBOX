# WoWBox TODO

Features we've talked about and want later, each with a plan.

---

## 1. Chat rules: only if the sender is in range

**Goal:** "heal" from someone in another zone or out of range doesn't fire the rule or start its cooldown.

**Plan:**
1. **Addon** (`BoxBridge.lua`): on each chat line, check `UnitInRange(sender)` (party and raid, about 40 yd, works in instances). Optionally use `IsSpellInRange(spell, sender)`. Send the result as a spare bit in the chat frame's flags byte (byte 53).
2. **Decoder:** add `ChatEvent.in_range: bool | None`. `None` means unknown, e.g. the sender isn't in the group.
3. **Config and engine:**
   - Add `ChatTrigger.sender_in_range: bool = False`.
   - When it's set and the line says out of range or unknown, log "skipped: out of range" and don't fire.
4. **Editor:** a checkbox, *"Only if the sender is in range of the listener"*.
5. **Tests and README.**

Needs `/reload` on every client.

---

## 2. Buff watches: "if X is missing buff Y, cast it"

**Goal:** recast a buff when it actually drops, not on a timer.

**Plan:**
1. **Config:** `bridge.watch_buffs: [Demon Armor, Water Shield, ...]`, up to about 8. WoWBox sends the list to each client with `/bb watch ...` at start.
2. **Addon:**
   - For each watched name, check `UnitBuff("player", name)` and `UnitBuff("pet", name)`.
   - Pack the results as bits into a state frame. There are spare bits in byte 52 or a new frame kind; check the room left.
3. **Decoder:** `ClientState.buffs_missing` and `pet_buffs_missing`, as sets of names.
4. **Conditions:** `missing_buff: Demon Armor` and `pet_missing_buff: ...`, as new fields in the editor.
5. **Example rule:** when Gnope is missing Demon Armor and is out of combat → `/cast Demon Armor` on Gnope, with a 10s cooldown.

Needs `/reload` on every client.

---

## 3. "Who matched": act on the character that's low

**Goal:** one rule, "if anyone is below 30% health, the shaman heals *that* character".

**Today:** it takes one rule per character (`slot: 1` → `/cast [@Tinkerdawn] …`). A workaround is `slot: any, health_pct_lt: 70` → `/cast [@Tinkerdawn] Chain Heal`, since Chain Heal jumps to the injured. But the first heal always lands on the tank.

### Part A: placeholders for the matched character (your own characters, no addon change)

**Partly done:**
- `{name}`, `{slot}`, `{class}`, `{health}` and `{power}` work in state rules, and in chat rules with a `when:`.
- When several characters match, the lowest health (or power) is picked.
- Characters too far away or dead don't match (`skip_far`, `skip_dead`).

**Still to do:** the `pick:` option, `cooldown_per: character` and `to: matched`.

```yaml
- name: heal-whoever-is-low
  when: { slot: any, health_pct_lt: 30 }
  pick: lowest_health                # which one, if several match
  do: { type: "/cast [@{name}] Healing Wave", to: Kunth }
  cooldown: 3s
  cooldown_per: character            # healing Gnope doesn't block healing Tinkerdawn
```

**Placeholders:** in state rules (and chat rules that have a `when:`), when the condition matches:

| Placeholder | Filled with |
|---|---|
| `{name}` | the matched character's name, e.g. `Gnope` |
| `{slot}` | its slot number |
| `{class}` | its class, e.g. `WARLOCK` |
| `{health}` / `{power}` | its health / power %, for messages like `/p Gnope at {health}%` |

**`to: matched`:** a new target keyword that sends to the matched character's own window. This makes the anti-AFK rule a single rule: `when: { slot: any, afk: true }` → `send: Space, to: matched`.

**`pick:`** decides which character to use when several match:

| Value | Picks |
|---|---|
| `lowest_health` | the lowest health %. This is the default when the condition uses `health_pct`. |
| `lowest_power` | the lowest power %. |
| `first` | the lowest slot number. This is the default otherwise. |
| `all` | every matched character: the steps run once per character, in turn. |

**`cooldown_per: character`:** keeps a separate cooldown per matched character. Without it, the cooldown is per rule, as now. Chat rules already have `cooldown_per: target` (per `{who}`); reuse `RuleRuntime.target_fired` and `cooling()`.

**Plan:**
1. **Engine** (`automation/engine.py`):
   - `condition_report` already returns, per slot, whether it matched. Use it to collect the matched slots.
   - Choose one (or all) by `pick`.
   - Build the values: name from the client's character, class, health and power.
   - Fill the steps with `substitute(steps, values)`, as chat rules already do (`rt.run_steps`).
2. **Cooldowns:** for `cooldown_per: character`, keep `last_fired` per (rule, slot) in a dict on `RuleRuntime`. A slot on cooldown is left out before picking, so the next-lowest character gets healed instead.
3. **`to: matched`:** resolve it in the step's `to` before sending. Store the matched slot with the running step, and pass `[slot]` as the target.
4. **Safety:** values go through `safe_values`. Names keep only letters, digits and `-`.
5. **Config** (`config.py`): add `pick` and `cooldown_per` to `AutomationRule`, with validation (`pick` only with a `when:`).
6. **Editor** (`ui/automation_editor.py`):
   - a *Pick* dropdown and a *Cooldown per character* checkbox in the state box;
   - the placeholder hint for state triggers;
   - `matched` in the *To* choices.
7. **Status column:** show who it would act on, e.g. *now: Gnope (health 24%)*.
8. **Tests:**
   - picking lowest, first and all;
   - per-character cooldowns;
   - `to: matched`;
   - a placeholder with no match;
   - a dead character (not picked, and add `dead: false` in the examples).
9. **README:** a recipe, and update the anti-AFK example to a single rule.

### Part B: party members who aren't yours (addon change)

The bridge only carries each client's *own* health, so a party member who isn't one of your characters is invisible. Plan:
1. **Addon:**
   - Scan `player` and `party1`–`party4` with `UnitHealth`/`UnitHealthMax`, skipping dead and disconnected units (`UnitIsDeadOrGhost`, `UnitIsConnected`).
   - Optionally use `UnitInRange` to skip anyone out of range.
   - Send the lowest one's health % (1 byte) and unit index (0 = player, 1–4 = party1–4, 1 byte) in the state frame. Check the room left; it may need a new frame kind.
2. **Decoder:** add `party_low_pct` and `party_low_unit` (`"party2"`).
3. **Conditions and placeholders:**
   - `party_low_pct_lt: 30` as a condition on the healer's slot;
   - `{party_low}` as a placeholder: `/cast [@{party_low}] Healing Wave`.
4. **Raids** (later): the same over `raid1`–`raid40`, with a "lowest in my subgroup" option.

Part B needs `/reload` on every client. Part A doesn't.

---

## Smaller ideas

- **Show all:** a button that brings every attached client window to the front.
- **Live stack settings:** tolerance and facing-tolerance boxes in the window, applied without a reload.
- **Lists in *To*:** e.g. `Gnope, Kunth` or `class:WARLOCK, class:SHAMAN`.
- **Per-step "only if party N exists"** in macros, so empty party spots don't cost a *Then wait*.
- **Range check for melee:** the addon reports `target_in_melee` (`CheckInteractDistance("target", 3)`), so a rule can press Interact With Target until the Necro arrives.
