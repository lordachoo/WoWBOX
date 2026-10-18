# Follow, stacking and movement mirroring

[← README](../README.md) · [All docs](README.md)

WoW's `/follow` breaks constantly: a cast, a mount, a door, a jump, a loading screen. Follow mode keeps followers on it.

- **F10, or the Follow button.** Every follower types `/target Tinkerdawn` and `/follow Tinkerdawn`, where Tinkerdawn is the leader's character. Follow mode turns ON.
- **While ON**, BoxBridge reports who is actually following (`following` in the State column). A follower that has been off follow for `refollow_delay` (1 s) gets `/follow Tinkerdawn` again. That command doesn't retarget, so their target stays. WoWBox holds back:
  - while that follower casts, channels, types or is in combat (with `refollow_in_combat: false`), so casters stand and fight, then catch up;
  - while you press movement keys, and for `refollow_delay` after;
  - for the window you're in, because you're steering that character yourself.

  If re-following keeps failing (leader out of range, say), the waits grow: 2, 4, 8 … up to 15 s.
- **F11, stay.** Taps `S` on the followers, which breaks their follow, and turns follow mode OFF.
- **Ctrl+Alt+M, mirror movement.** Your movement keys (`follow.movement_keys`: W A S D Q E Space) also go to the followers. They start, stop, strafe, turn and jump the moment you do. When you stop, they restack on you (see Stacking below). If stacking is off, they `/follow` again instead.
  - **Stack first.** Mirrored keys only give the same path if everyone starts stacked and faces the same way.
  - **Turn with A/D.** Mouse turning (right-drag) isn't copied.
  - **Obstacles still split them.** Anyone who hits a corner differently drifts, and the next stop fixes it.
  - **Only followers who are with you get your keys** (`follow.mirror_guard`, on by default). A follower is left out while it's **in another zone** or **more than `mirror_max_distance_yards` (40) away** from the character you're steering. Your characters can be spread around the world, and mirroring only moves the ones standing with you. The Log tab says when someone is left out and when they're back. The Position column shows `[in another zone]` or `[85 yd away]` next to anyone who isn't with the leader.
    - The zone check always works: the addon reports the zone's map ID, even where there are no coordinates. The yard check needs the zone's scale, which WoWBox measures while stacking there. It only uses that scale once two measurements agree, so one bad reading (a blocked step, your own keys mixed in) can't make a character standing next to you look far away. Until then, only the zone is checked. The scale is re-measured each time WoWBox starts.
  - **Test first.** Some clients don't take held movement keys while in the background. Try a short walk.
- **Using an in-game macro instead.** Set `follow.start_key` / `follow.refollow_key` to a key bound to an in-game `/follow` macro, and WoWBox presses that key instead of typing.

## Stacking

Mirrored movement only stays together if everyone starts on the same spot, facing the same way. Even 5° of difference becomes 2 yards after a 20-yard run. **Stack (F12, the Stack button, or `/bb stack`)** puts them there:

1. BoxBridge reports every character's map position, facing and frame rate (the **Position** column in the Clients tab).
2. For each follower, WoWBox:
   - turns it with A/D to roughly face the way the anchor faces;
   - moves it without turning: W/S along its facing, Q/E sideways;
   - after every move, waits for a fresh reading and corrects again;
   - finally turns it to the anchor's exact facing (turning on the spot doesn't move it).
3. It usually finishes in about 3 seconds. All followers stack at the same time.

More details:
- **No speeds are guessed.** The first move of each kind is a short probe. From then on, WoWBox measures how fast each key moves or turns the character on this map and uses that.
- **The anchor** is the window you're in when you press F12, or the client where you typed `/bb stack`. If you press F12 in WoWBox itself, the leader is the anchor.
- **Mirroring plus stacking is lockstep.** With mirroring on, the followers restack every time you stop moving (`stack.on_stop`). This replaces `/follow` while mirroring, because follow would pull them behind you. Press your movement keys and they move with you. Stop, and any drift is corrected within a second or two.
- **You can always take over.** Pressing a movement key yourself cancels a running stack. So does pressing F12 again.
- **Only nearby followers are stacked.** A follower in another zone, or more than `stack.max_distance_yards` (40) away, is skipped at once ("in another zone" / "too far away … use follow") instead of being walked across the map. Use F10 (follow) to bring it close first.
- **Frame rate sets the precision.** See [Frame rate and stacking precision](#frame-rate-and-stacking-precision) below. In short: **run every client at 144 fps** for facing within 1°.
- **Where it works:** anywhere the client gives map coordinates. That includes the EQ zones on your server (tested with `GetPlayerMapPosition`). In a place without coordinates (many instances: `GetPlayerMapPosition` gives `0 0` there), the Position column stays empty.
  - **Stack in those places matches facing only.** Followers turn to your facing, and the log says "facing matched only (no map position here…)". It can't walk them onto your spot.
  - **To get close first:** use follow (F10) to bring them in, then stay (F11) and stack (F12). Mirrored movement then runs parallel, a yard or two apart instead of exactly on top of you, so drift builds up faster than outdoors. Re-stack often, or use follow for long runs.
  - **The distance guard can't measure there,** so it doesn't block mirroring. Only a different map ID still counts as another zone.
  - **To test a spot:** `/run SetMapToCurrentZone() local x,y=GetPlayerMapPosition("player") print(GetCurrentMapAreaID(), x, y, GetPlayerFacing())`. If x and y print `0 0`, there are no coordinates.
- **Keep the world map closed.** Positions are relative to the map being shown, so while a client has its world map open, that client reports no position.
- **Mounted, walking or slowed:** the measured speed adapts. Tolerances in yards assume the normal 7 yd/s run speed (`stack.run_speed`).
- **Turning keys:** stacking needs keyboard turning. A and D must be bound to turn left and turn right (WoW's default), not to strafe. If yours differ, set `stack.turn_left`, `stack.turn_right`, `stack.forward`, `stack.back`, `stack.strafe_left` and `stack.strafe_right`.

```yaml
stack:
  who: all                  # who gets stacked; the anchor is always left out
  on_stop: true             # while mirroring, re-stack whenever you stop
  on_stop_delay: 400ms
  tolerance_yards: 0.1
  facing_tolerance_deg: 1   # widened automatically to half a frame (see below)
  timeout: 10s
  max_distance_yards: 40    # farther away, or another zone: skipped
  run_speed: 7.0
  map_aspect: 1.5           # zone maps are 3:2
```

### Frame rate and stacking precision

**The limit is the game, not a setting.** WoW applies a held key once per frame. A character therefore turns, and walks, in steps of one frame's worth. Keyboard turning runs at 180° per second, so one step is 180° divided by the frame rate:

| Client fps | One frame of turning | One frame of running | Guaranteed facing | Simulated typical / worst |
|---|---|---|---|---|
| 30 | 6.0° | 0.23 yd | ±3.0° | 1.9° / 3.6° |
| 60 | 3.0° | 0.12 yd | ±1.5° | 1.0° / 2.0° |
| 120 | 1.5° | 0.06 yd | ±0.75° | 0.4° / 1.0° |
| **144** | **1.25°** | **0.05 yd** | **±0.6°** | **0.3° / 0.9°** |

**Why it can't "just keep going until it's under 1°" at 60 fps.** Say a character is 1.6° off. The smallest possible correction is one frame, which is 3°, so it lands 1.4° off the other way. Correcting again goes back to 1.6°. It would swing back and forth forever. So WoWBox stops at whatever whole number of frames gets closest, which is at most half a frame off (±1.5° at 60 fps). Sometimes you'll see 0°: the error just happened to be close to a multiple of 3°. That's luck, not something WoWBox can repeat.

**How WoWBox gets the most out of each frame.** It holds every key for exactly a whole number of frame-times. A hold of *n* frame-times always spans exactly *n* frames, wherever it starts, so every correction lands exactly where it's aimed. The facing tolerance (`stack.facing_tolerance_deg`, default 1°) and the position tolerance (`stack.tolerance_yards`, default 0.1) are widened automatically to half a frame when the frame rate can't do better. Each client's own fps comes from BoxBridge.

**Running at 144 fps (recommended).** Run these once, in any client. They apply to every client, including background windows, and WoW saves them:
```
/bb all console maxfps 144
/bb all console maxfpsbk 144
/bb all console gxVSync 0
```
- `maxfps` caps the frame rate of the window you're playing in.
- `maxfpsbk` caps it for windows in the background. All but one of your clients are always in the background, so this one matters most. WoW keeps background windows slow unless you raise it.
- `gxVSync 0` turns off vsync, which otherwise caps the frame rate at your monitor's refresh rate (often 60). Some clients need a restart before a vsync change takes effect. You can also switch it off in Video options.

Then check the **Position** column in the Clients tab, which shows each client's fps. If a client still shows about 60, vsync or the graphics driver is capping it. If the fps is below 144 and fluctuates, your PC is the limit, because it has to draw every client at that rate. Lower in-game graphics settings help. Anything from 120 fps up gives facing under 1°.

### Who counts as "with you": the distance guard

Your characters don't have to be together. Mirroring and stacking only act on followers who are **with the character you're steering** (for stacking, the one they stack onto). Everyone else is left alone.

```yaml
follow:
  mirror_guard: true               # false = mirror to every follower, wherever they are
  mirror_max_distance_yards: 40
stack:
  max_distance_yards: 40           # farther away, or in another zone: skipped
```

**How "with you" is decided**, each time you press a movement key (mirroring) or start a stack:

1. **Same zone?** BoxBridge sends each client's map ID (`GetCurrentMapAreaID`), even in places without coordinates. A different map ID means *in another zone*, and that follower is left out. This check works from the moment WoWBox starts.
2. **Close enough?** If both have coordinates, WoWBox works out the distance in yards. Map coordinates run 0–1 across each zone's map, and every zone's map is a different size, so WoWBox first needs that zone's **scale**. It gets it by measuring while stacking:
   - Each move teaches it how far a character gets per second, in map units. Divided by the normal run speed (7 yd/s, `stack.run_speed`), that gives map units per yard.
   - Only moves of at least 0.1 s count. On a shorter press, being one frame off is too big an error.
   - Speeds are kept **per character and per zone**, so a mounted or slowed character doesn't throw off the others.
   - **One bad reading is ignored.** A reading more than 2× off the current estimate is set aside. It only replaces the estimate if the next reading agrees, which is what happens when someone really mounts up or gets slowed. A blocked step, a stale reading, or your own mirrored keys reaching the character during a measurement can't spoil the scale.
   - **The scale is trusted after two readings agree.** Until then, the guard only checks the zone, and stacking moves in short steps (at most 0.6 s).
3. **Can't tell?** With no bridge data, an unknown map, or a zone not measured yet, the follower is treated as *with you*. The guard only leaves someone out when it knows they're elsewhere.

The scale is re-measured after every WoWBox start: one or two stacks in a zone are enough. Until then, only "in another zone" can leave someone out.

**What you'll see**
- **Clients tab, Position column:** `[in another zone]` or `[85 yd away]` next to anyone who isn't with the leader.
- **Log tab, mirroring:** a line when someone is left out, and one when they're back:
  ```
  mirroring: WoW2 Gnope left out (in another zone)
  mirroring: WoW3 Kunth left out (52 yd away)
  mirroring: WoW3 Kunth is with you again
  ```
- **Log tab, stacking:** stack results for followers elsewhere, skipped straight away rather than walked across the map:
  ```
  stack WoW2 Gnope: in another zone than the anchor (? yd off, ...)
  stack WoW3 Kunth: too far away (52 yd, max 40 yd): use follow (...)
  ```
  Press **F10** (follow) to bring them over, then stack.

**If someone standing next to you is left out**
- **"in another zone":** the two clients report different map IDs. A client with its **world map open** reports the map it's showing, so close it. Moving between sub-zones and floors (e.g. dungeon levels) can also change the map ID.
- **A distance far larger than real, e.g. "1840 yd away":** the zone scale is off. That shouldn't happen any more. If it does, restart WoWBox, which clears the measured speeds, and send the `stack …` lines from the log.
- **To switch the guard off:** `follow.mirror_guard: false`, or raise `mirror_max_distance_yards` / `stack.max_distance_yards`. Then Reload config.

## Follow settings

```yaml
follow:
  leader: leader                    # who to follow: the leader slot, or a character name
  who: not:leader                   # who follows
  start: ["/target {leader}", "/follow {leader}"]
  refollow: true
  refollow_command: "/follow {leader}"
  refollow_delay: 1s
  refollow_in_combat: false
  retry: 2s                         # first wait after a failed re-follow; doubles up to retry_max
  retry_max: 15s
  stop_key: "S"
  movement_keys: ["W", "A", "S", "D", "Q", "E", "Space"]
  mirror_on_start: false
  mirror_guard: true                # only mirror to followers in your zone, within:
  mirror_max_distance_yards: 40
```
