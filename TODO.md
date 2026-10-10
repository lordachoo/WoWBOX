# WoWBox TODO

Features we've talked about and want later, each with a plan.

---

## 1. `{arg}`: a word from the chat line as an argument

**Goal:** "heal Gnope" makes Kunth cast on Gnope, and "heal" alone heals whoever asked.

```yaml
- name: heal-on-request
  chat: { channel: [whisper, party], sender: team, contains: [heal], heard_by: Kunth }
  do: { type: "/cast [@{arg|sender}] Healing Wave", to: Kunth }
  cooldown: 3s
```

| Whisper | Keyword | `{arg}` | `{arg\|sender}` |
|---|---|---|---|
| "heal Gnope" | `heal` | `Gnope` | `Gnope` |
| "light heal kunth pls" | `light heal` | `kunth` | `kunth` |
| "heal" | `heal` | *(empty)* | the sender |
| "heal target" | `heal` | `target` | `target` (Kunth's own target) |

**Plan:**
1. **Find the word** (`automation/engine.py`):
   - `matched_word()` already finds the longest keyword in the line.
   - Add `word_after(text, keyword)`: the first whitespace-separated word after the keyword, or `""`.
   - Put it in the `values` dict in `on_chat` as `arg`, next to `sender`, `message`, `channel` and `listener`.
2. **Fallback syntax** (`automation/actions.py`, `substitute`):
   - Support `{name|fallback}`: use `fallback`'s value when `name` is empty, e.g. `{arg|sender}`.
   - Only one level of fallback is needed.
3. **Safety** (`safe_values`): clean `arg` exactly like `sender`, keeping only letters, digits and `-`. A word can never add a second command.
4. **Editor** (`ui/automation_editor.py`): add `{arg}` and `{arg|sender}` to the placeholder hint under the steps for chat triggers.
5. **Tests** (`tests/test_chat.py`):
   - extraction, including the longest-keyword case;
   - the fallback;
   - cleaning, e.g. "heal Gnope;/run x" → `Gnope`;
   - an empty arg with no fallback;
   - a 27-byte line cut mid-name.
6. **README** (section 13, chat rules): a recipe, plus the limits.

**Limits to document:**
- `[@name]` only reaches party and raid members.
- Unit words (`target`, `focus`, `pet`, `player`) work and are a feature.
- Only the first 27 bytes of the line arrive, so long names may be cut.

**No addon change**, so no `/reload` needed. About an hour of work.

---

## 2. Chat rules: only if the sender is in range

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

## 3. Buff watches: "if X is missing buff Y, cast it"

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

## 4. Heal the group member who's low (state rules)

**Goal:** "if anyone in the group is below 30% health, the shaman heals them."

**Already works for your own characters**, with one rule per character:
```yaml
- name: heal-tinkerdawn-low
  when: { slot: 1, health_pct_lt: 30 }
  do: { type: "/cast [@Tinkerdawn] Healing Wave", to: "class:SHAMAN" }
  cooldown: 3s
```

**What's missing:**
- **Matched-slot placeholders** (`{name}`, `{slot}`) in state rules. With these, one rule with `slot: any` could say `/cast [@{name}] Healing Wave`, filled with the character that matched. If several match, pick the lowest health.
- **Party members who aren't yours.** The addon would report the lowest party member's health and their party index (`party1`–`party4`, via `UnitHealth`/`UnitHealthMax`). New fields: `party_low_pct` and `party_low_unit`. Then: `when: { slot: 3, party_low_pct_lt: 30 }` → `/cast [@{party_low_unit}] Healing Wave` to Kunth. Needs `/reload`.

---

## Smaller ideas

- **Show all:** a button that brings every attached client window to the front.
- **Live stack settings:** tolerance and facing-tolerance boxes in the window, applied without a reload.
- **Lists in *To*:** e.g. `Gnope, Kunth` or `class:WARLOCK, class:SHAMAN`.
- **Per-step "only if party N exists"** in macros, so empty party spots don't cost a *Then wait*.
- **Range check for melee:** the addon reports `target_in_melee` (`CheckInteractDistance("target", 3)`), so a rule can press Interact With Target until the Necro arrives.
