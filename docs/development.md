# Building and development

[← README](../README.md) · [All docs](README.md)

## Building the standalone exe

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

This produces `dist\WoWBox\` containing:
- `wowbox.exe`, a single file with no Python needed;
- `config\`, `addon\`, the README and `docs\`.

The exe keeps its config in `dist\WoWBox\config\team.yaml` and its logs in `dist\WoWBox\logs\`. On first run, it copies the example config if `team.yaml` is missing. dxcam isn't bundled, so the `auto` capture method uses mss and PrintWindow.

Close the exe before rebuilding: Windows locks a running exe.

## Project layout and development

```
addon/BoxBridge/        the WoW addon (Lua): state strip, chat lines, /bb commands
config/                 team.example.yaml (tracked); team.yaml, macros.yaml, automation.yaml (yours, not tracked)
packaging/              build.ps1 + PyInstaller entry point
src/wowbox/
  __main__.py           command line, logging, startup
  app.py                Controller: owns every subsystem, bridges worker threads to the Qt UI
  diagnostics.py        watchdog (stuck threads -> stacks), crash.log, unclean-exit check, health line
  config.py             team.yaml models (pydantic), load/save
  keys.py               key names, chords, ranges
  team.py               slots, clients, target resolution
  launcher.py           launch / attach / track clients
  follow.py             follow mode, re-follow, movement mirroring
  stack.py              stacking: the position/facing control loop
  router/               keyboard hook → route table → sender thread; chat guard
  bridge/               pixel decoder (state + chat + commands) and capture poller
  automation/           steps, macro runner, rule engine
  macro_store.py        macros.yaml: load, save, the one-time move out of team.yaml
  rule_store.py         automation.yaml: the same for automation rules
  config_text.py        Save config: edits team.yaml in place, keeping comments
  commands.py           /bb team commands (the client you typed in goes last)
  panel.py              WoWBox's state on BoxBridge's in-game party panel (three spare keys)
  layout/               grid maths, thumbnail strip, tab bar, layout manager
  platform/             Win32 windows / input / capture (ctypes), and fakes for tests
  memread/              optional read-only memory reader
  ui/                   main window, macro and rule editors, rule list, tray icon
tests/                  pytest suite
```

```powershell
.venv\Scripts\python.exe -m pytest          # tests
.venv\Scripts\python.exe -m wowbox --check-config
```

**What the tests cover:**
- the router, chat guard and decoder (including stretched and irregular strips), automation, chat rules, macros, follow mode, layouts, the launcher and config;
- the real `BoxBridge.lua`, run in Lua 5.1 through `lupa` against a stubbed WoW API, with its output decoded.

They use fake Win32 implementations, so nothing touches real windows.

**Keep the addon and the decoder in sync.** The strip format is documented at the top of `src/wowbox/bridge/decoder.py`. Any change there needs the matching change in `BoxBridge.lua`.
