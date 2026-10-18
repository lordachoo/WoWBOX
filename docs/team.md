# Your team and targets

[← README](../README.md) · [All docs](README.md)

## Your team: slots, characters, leader

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

## Targets: choosing which characters

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
