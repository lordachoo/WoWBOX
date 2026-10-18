# Automation recipes

[← README](../README.md) · [All docs](README.md) · [Automation](automation.md)

Complete examples to copy. The rule format is in [Automation](automation.md).

## Recipe: heal and buff bot on whisper

A complete example to copy into `team.yaml` under `automation:`. Kunth (shaman) heals and buffs whoever whispers him with a keyword, and replies so they know it worked. Gnope (warlock) answers too. Change the names, spells and keywords to your own.

```yaml
automation:
  # "heal" from a group member: heal them without changing Kunth's target
  - name: heal-on-whisper
    chat:
      channel: [whisper, party]
      sender: [Tinkerdawn, Gnope]          # only these may trigger it; leave out = anyone (careful)
      contains: [heal]
      heard_by: Kunth
    when: { slot: 3, casting: false, dead: false, ghost: false }
    do:
      sequence:
        - { type: "/cast [@{sender}] Healing Wave", to: Kunth }
        - { type: "/w {sender} Healing you!", to: Kunth }
    cooldown: 3s

  # "chain": a different spell for a different word
  - name: chain-heal-on-whisper
    chat: { channel: [whisper, party], sender: [Tinkerdawn, Gnope], contains: [chain], heard_by: Kunth }
    when: { slot: 3, casting: false, dead: false }
    do: { type: "/cast [@{sender}] Chain Heal", to: Kunth }
    cooldown: 5s

  # "buffs": a buff round on the whisperer. One character can only cast once per global cooldown,
  # so its spells need ~1.6 s between them; different characters can cast at the same time.
  - name: buff-bot
    chat:
      channel: whisper
      contains: [buffs, buff me]          # no sender list: anyone may ask (see the warning below)
      heard_by: Kunth
    when: { slot: 3, combat: false, casting: false, dead: false }   # only out of combat
    do:
      sequence:
        - { type: "/w {sender} Buffing you, stand still a few seconds", to: Kunth }
        - { type: "/target {sender}", to: Kunth }        # /target also works on people outside your group
        - { type: "/cast Water Breathing", to: Kunth }
        - { type: "/target {sender}", to: Gnope }
        - { type: "/cast Unending Breath", to: Gnope }   # Gnope casts at the same time as Kunth
        - { wait: 1.6s }
        - { type: "/cast Water Walking", to: Kunth }
        - { wait: 1.6s }
        - { type: "/targetlasttarget", to: [2, 3] }       # back to what they had targeted
    cooldown: 20s                           # one buff round at a time

  # "inv": invite whoever asks (a buff-bot staple)
  - name: invite-on-whisper
    chat: { channel: whisper, contains: [inv], heard_by: Tinkerdawn }
    do: { type: "/invite {sender}", to: Tinkerdawn }
    cooldown: 5s
```

**How it fits together**
- **Who may trigger it (`sender:` / *From*):**

  | You write | Fires for |
  |---|---|
  | `[Tinkerdawn, Gnope]` | those characters only (any case) |
  | `team` | any of **your own** characters: the ones logged in, plus those pinned to slots in `team.yaml` |
  | `[team, Randomfriend]` | your characters and Randomfriend |
  | *(empty / left out)* | **anyone**, strangers included |
  | `all`, `any`, `anyone`, `*` | refused, with a hint to leave it empty instead |

- **`chat:`** decides *which line* fires the rule:
  - `channel` is where it was said;
  - `sender` is who may trigger it;
  - `contains` is the keywords;
  - `heard_by` is the client that received it.
- **`when:`** adds a condition on that character's current state, e.g. not casting, not dead, out of combat. Any bridge field from the list above can go here.
- **`do:`** is the same step list as a macro. `{sender}` is filled in with the whisperer's name.
- **`[@{sender}]`** casts on the whisperer without changing your target. It works for party and raid members. For anyone else, use `/target {sender}` + `/cast …` + `/targetlasttarget`, as in `buff-bot`.
- **`cooldown:`** stops one person from spamming the rule: the person just served can't trigger it again until the cooldown ends.
- **Requests queue while the rule is busy.** If three people ask for buffs at once, the rule serves them one after another:

  | Time | |
  |---|---|
  | 0 s | Tinkerdawn asks: the rule runs for Tinkerdawn. |
  | 2 s | Gnope asks: queued (#1). |
  | 3 s | Randompug asks: queued (#2). |
  | after Tinkerdawn's run and the cooldown | Gnope is served, then Randompug. |

  - **One place per person:** asking again while you're queued or being served doesn't add a second place.
  - **Limits:** up to `chat_queue` requests (5) wait, each for at most `chat_queue_ttl` (60 s). Older ones are dropped and logged.
  - **The condition is checked again** when a request's turn comes. If the rule has `when:` and it's false then, the request is dropped.
  - **Per rule:** set *Queue while busy* in the editor (`queue:` in YAML) to a number, `off` (`0`, the old skip-it behaviour) or `default`.
  - **The Status column** shows `running step 2/9 for Tinkerdawn · queued: Gnope, Randompug`, and *Next* says who's next.
- **The most specific keyword wins.** Keywords match anywhere in the text, so `heal` is also inside "light heal" and "chain heals". When one rule's matched keyword is part of a longer keyword another rule matched, only the longer one fires:

  | Line | Rules with these keywords: `heal`, `heals`, `light heal`, `chain heal`, `chain heals` |
  |---|---|
  | "light heal pls" | only `light heal` |
  | "chain heals" | only `chain heals` |
  | "heals" | only `heals` |
  | "heal me" | only `heal` |

  Keywords that don't overlap still all fire: a line with both `heal` and `buff` fires a heal rule and a buff rule.

**Turning it on and testing**
1. Paste the rules under `automation:` in `team.yaml`, save, and press **Reload config**. They show up in the **Automation** tab.
2. Click **Automation: ON**. Leave **Dry run** ticked at first.
3. In Kunth's client, fake a whisper:
   ```
   /bb test Tinkerdawn heal me
   /bb test Randompug buffs
   ```
   The Log tab shows `rule 'heal-on-whisper' fired (whisper from Tinkerdawn: 'heal me')`, then `[dry run] would type '/cast [@Tinkerdawn] Healing Wave' -> Kunth`.
4. When the log looks right, untick **Dry run**. Ctrl+Alt+K turns all automation off at once.

**Watch out**
- **Strangers.** A rule without `sender:` reacts to *anyone* who whispers the keyword. For "only my own characters", use `sender: team`. That's what a public buff bot does, but keep `cooldown` up and stay out of combat (`when: {combat: false}`).
- **Server rules.** Many servers forbid unattended bots. A buff bot that answers strangers while you're away is exactly what they look for. Check your server's rules first.
- **Range and line of sight.** WoW's own rules still apply. Out of range shows the usual error on that client, and the rule doesn't retry.
- **Text length.** Only 27 bytes of a line reach WoWBox. With the chat filter, they're the keyword plus the 25 bytes after it, wherever the keyword is in the line, so that's rarely a problem.
- **Channels.** A rule listens only on its channels. In a raid group, `/party` still reaches your subgroup. For lines typed in `/raid`, add `raid` to the rule's channels.
- **Exact names.** Spell names must match the game's exactly, including your server's custom spells.

## Recipe: group ports on a party keyword ("port lavastorm")

Say `port lavastorm` in party from any of your characters. Kunth announces it in party and casts the port, so you don't have to swap windows or dig through the spellbook. One rule per destination, all the same shape. These are the P99 druid group ports up to Velious (Skyfire's is Wind of the North, not a Circle), cast here by the shaman (`class:SHAMAN`). Use `class:DRUID` or a name if your porter is someone else.

```yaml
automation:
  - name: SHAMAN Port - Lavastorm Mountains
    chat:
      channel: [whisper, party]
      sender: [team]                 # only your own characters can port the group
      contains: [port lavastorm]
      heard_by: class:SHAMAN
    do:
      sequence:
        - { type: /p Porting to Lavastorm Mountains, to: "class:SHAMAN" }
        - { type: /cast Circle of Lavastorm, to: "class:SHAMAN" }
    cooldown: 10s

  # Two keywords for one port: either one fires it.
  - name: SHAMAN Port - Great Divide
    chat:
      channel: [whisper, party]
      sender: [team]
      contains: [port gd, port great divide]
      heard_by: class:SHAMAN
    do:
      sequence:
        - { type: /p Porting to the Great Divide, to: "class:SHAMAN" }
        - { type: /cast Circle of Great Divide, to: "class:SHAMAN" }
    cooldown: 10s
```

The full set. Copy the first rule above for each row, changing the name, keyword(s), party text and spell:

| Say in party | Kunth says "Porting to …" | Spell |
|---|---|---|
| `port butcher` | Butcherblock Mountains | Circle of Butcher |
| `port commons` | West Commonlands | Circle of Commons |
| `port karana` | North Karana | Circle of Karana |
| `port surefall` | Surefall Glade | Circle of Surefall Glade |
| `port toxx` | Toxxulia Forest | Circle of Toxxulia |
| `port feerrott` | The Feerrott | Circle of Feerrott |
| `port lavastorm` | Lavastorm Mountains | Circle of Lavastorm |
| `port ro` | South Ro | Circle of Ro |
| `port steamfont` | Steamfont Mountains | Circle of Steamfont |
| `port misty` | Misty Thicket | Circle of Misty |
| `port combines` | the Dreadlands | Circle of the Combines |
| `port gd`, `port great divide` | the Great Divide | Circle of Great Divide |
| `port iceclad` | Iceclad Ocean | Circle of Iceclad |
| `port cobalt` | Cobalt Scar | Circle of Cobalt Scar |
| `port wakening`, `port wl` | the Wakening Lands | Circle of Wakening Lands |
| `port skyfire` | the Skyfire Mountains | Wind of the North |

**How it fits together**
- **`sender: [team]`.** Only your own characters can trigger a port. A stranger in a pickup group typing "port ro" does nothing.
- **`cooldown: 10s`** is what makes it cast once. A party line is heard by every one of your clients, and Kunth's own "Porting to …" goes back out to party too. The rule fires once per line anyway (see *Heard by several clients* in [Chat rules](automation.md#chat-rules-chat)), and the cooldown covers a second "port lavastorm" typed while the cast is still going.
- **The announcement can't set off another port.** "Porting to Lavastorm Mountains" contains "port" but not "port lavastorm": every keyword here is `port` plus a space plus a zone word. Keep that in mind when you add your own. A rule with just `contains: [port]` would fire on Kunth's own announcement.
- **Short keywords match inside longer words.** Keywords match anywhere in the line, so `port ro` also fires on "port rotten…" or "support route". Make one longer (`port south ro`) if that ever bites.
- **`/p` first, then `/cast`.** The party hears where you're going before the cast starts.
- **Grouped in the Automation tab.** With *Group by* set to who acts, they sit together. Type `port` in the search box to see only them, and tick or untick the group header to turn them all on or off.

**Turning it on**
1. Paste the rules into `config/automation.yaml` (or make them in the rule editor). WoWBox reloads the file within a second.
2. `/bb all reload`: the new keywords have to reach the clients' chat filter (*The chat filter* in [the addon doc](addon.md)).
3. Test without moving anyone: tick **Dry run**, then in Kunth's client `/bb test Tinkerdawn port lavastorm` (it acts like a whisper from Tinkerdawn). The log shows `[dry run] would type '/p Porting to Lavastorm Mountains' -> Kunth`.

**Watch out**
- **Spell names are P99's.** Check one in your porter's spellbook. Your server may spell them differently ("Circle of Butcherblock"), and a wrong name only gives the usual "unknown spell" error on that client.
- **In a raid**, `/party` still reaches your subgroup. For lines typed in `/raid`, add `raid` to `channel:`.
