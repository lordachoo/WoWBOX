# Layouts and configuration reference

[← README](../README.md) · [All docs](README.md)

## Layouts

| Layout | |
|---|---|
| `grid_auto` (default) | Picks the tiling whose cells come closest to 16:9 for your monitor and client count. 3 clients on a super-ultrawide end up side by side. |
| `grid_2x2`, `grid_1x3` | Fixed borderless tiles on `layout.monitor`. |
| `main_thumbs` | One big main client plus live thumbnails of the rest. Click a thumbnail to swap. On a wide monitor (21:9, 32:9) the main client keeps a 16:9 shape on the left and the thumbnails stack on the right; otherwise they sit in a strip along the bottom. `layout.thumb_side` (auto / right / bottom) picks; `layout.thumbs_monitor: 2` puts them on another monitor. |
| `tabs` | One client fills the monitor, and a tab bar switches between them. |

Ctrl+Alt+L cycles the layouts. In main_thumbs and tabs, all clients share one size and position, so swapping only raises a window. Nothing is resized, which avoids a D3D device reset.

**Sharpness:** the game renders at its own resolution (`gxResolution`), and Windows stretches that into the window. If the picture looks blurry or squashed, set the in-game resolution to the window size. Alternatively, set `client.resolution` and `client.set_windowed_cvars: true` so `Config.wtf` is written before launch (a backup is kept).

## Configuration reference

Everything lives in `config\team.yaml`. The exe uses `config\team.yaml` next to `wowbox.exe` instead. `config\team.example.yaml` has every option with comments.

- **Durations** look like `500ms`, `1.5s`, `20s`, `25m` or `1h`.
- **Validate** with `--check-config`.
- **Reload config** re-reads routes, hotkeys, macros, team, follow and automation. Other sections need a restart.
- **Save config** updates the route table, the leader and the slots' `character:` names in `team.yaml` in place. Every other line, comments included, is left alone. The previous version is kept as `.bak`, and nothing is written when nothing changed.

| Section | Key options |
|---|---|
| `client` | `exe`, `args`, `set_windowed_cvars`, `resolution`, `launch_stagger` (3s), `window_timeout` (120s), `rename_windows` |
| `team` | `leader`, `slots: {n: {label, role, character}}` |
| `layout` | `default`, `monitor`, `thumbs_monitor`, `thumb_strip` (0.22), `thumb_side` (auto), `tab_bar_height` |
| `hotkeys` | `toggle_broadcast`, `release_all`, `follow`, `stay`, `stack`, `mirror_movement`, `cycle_layout`, `next_main`, `automation_kill`, `focus_slot: {n: chord}` |
| `broadcast` | `enabled_on_start`, `chatguard`, `chatguard_open_keys`, `chatguard_close_keys`, `modifier_mode` (post / focus_swap / strip), `focus_swap_dwell`, `only_when_wow_focused`, `type_wait_max` (8 s), `type_quiet` (0.35 s) |
| `follow`, `stack` | [Follow, stacking and movement mirroring](follow.md) |
| `routes` | list of `{keys, to, note}` ([Key broadcasting and routes](broadcasting.md)) |
| `bridge` | `enabled`, `hz` (10), `block_size` (8, match `/bb size`), `method` (auto / mss / dxcam / printwindow), `stale_after` (1.5s), `commands` (true) |
| `macros` | [Macros](macros.md). Kept in `config/macros.yaml` once you've saved from the macro editor. |
| `automation` | [Automation](automation.md). Kept in `config/automation.yaml` once you've saved from the rule editor. |
| `automation_options` | `enabled_on_start` (false), `dry_run` (true), `tick_hz` (10), `pause_on_stale_bridge` (true), `chat_queue` (5), `chat_queue_ttl` (60 s), `rule_gap` (1.5 s), `wait_for_casts` (true), `cast_wait_max` (10 s), `skip_dead` (true), `skip_far` (true) |
| `memread` | optional read-only memory reader: `enabled` (false), `hz`, `fields: {name: {address, module, offsets, type}}`. No addresses ship with WoWBox; you supply and verify them. |
