import os

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wowbox.config import MacroConfig, load_config, save_config  # noqa: E402
from wowbox.keys import VK, Mod  # noqa: E402
from wowbox.macro_store import (  # noqa: E402
    dump_macros, flat_steps, load_macros_text, macros_path, migrate, remove_macros_section,
)

MACROS = [
    {"name": "gnope-assist-and-nuke", "key": "F8", "note": "Gnope assists the tank",
     "do": {"sequence": [{"type": "/assist Tinkerdawn", "to": "Gnope"}, {"wait": "300ms"},
                         {"keys": ["1-9", "0"], "delay": "1.5s", "to": "Gnope"}]}},
    {"name": "portal-stone", "key": "Ctrl+Alt+P", "do": {"type": "/use Portal Stone of Learning", "to": "all"}},
    {"name": "slots", "do": {"sequence": [{"send": "Ctrl+9", "to": [2, 3]}, {"macro": "portal-stone"}]}},
]

TEAM_YAML = """\
# my team, with comments
client:
  exe: "C:/WoW/Wow.exe"   # keep this comment

macros:
  - name: portal-stone
    key: "Ctrl+Alt+P"
    do: { type: "/use Portal Stone of Learning", to: all }

  - name: other
    do: { send: "1", to: all }

# automation comment stays
automation: []
"""


def macros():
    return [MacroConfig.model_validate(m) for m in MACROS]


def same(a: MacroConfig, b: MacroConfig) -> bool:
    return (a.name, a.key, a.note) == (b.name, b.key, b.note) and \
        [s.model_dump() for s in flat_steps(a.do)] == [s.model_dump() for s in flat_steps(b.do)]


# ---------------------------------------------------------------- storage
def test_dump_and_load_round_trip():
    text = dump_macros(macros())
    back = load_macros_text(text)
    assert all(same(a, b) for a, b in zip(macros(), back)) and len(back) == 3
    assert text.startswith("# WoWBox macros.")
    assert "{type: /assist Tinkerdawn, to: Gnope}" in text  # one line per step
    assert "1.5s" in text and "300ms" in text


def test_remove_macros_section_keeps_everything_else():
    new, found = remove_macros_section(TEAM_YAML)
    assert found
    assert "portal-stone" not in new and "other" not in new
    for kept in ("# my team, with comments", "# keep this comment", "# automation comment stays", "automation: []"):
        assert kept in new
    assert "macros: moved to macros.yaml" in new
    assert yaml.safe_load(new)["client"]["exe"] == "C:/WoW/Wow.exe"
    assert remove_macros_section(new) == (new, False)


def test_migrate_and_load_config(tmp_path):
    team = tmp_path / "team.yaml"
    team.write_text(TEAM_YAML, encoding="utf-8")
    assert [m.name for m in load_config(team).macros] == ["portal-stone", "other"]
    migrate(team, load_config(team).macros + [macros()[0]])
    assert macros_path(team).exists() and (tmp_path / "team.yaml.bak").read_text(encoding="utf-8") == TEAM_YAML
    cfg = load_config(team)
    assert [m.name for m in cfg.macros] == ["portal-stone", "other", "gnope-assist-and-nuke"]
    # Save config (the whole-team writer) must not put macros back into team.yaml
    save_config(cfg, team)
    assert "macros" not in yaml.safe_load(team.read_text(encoding="utf-8"))
    assert len(load_config(team).macros) == 3


def test_macros_yaml_wins_over_team_yaml(tmp_path):
    team = tmp_path / "team.yaml"
    team.write_text(TEAM_YAML, encoding="utf-8")
    macros_path(team).write_text(dump_macros(macros()[:1]), encoding="utf-8")
    assert [m.name for m in load_config(team).macros] == ["gnope-assist-and-nuke"]


# ---------------------------------------------------------------- editor
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def editor(qapp, macro=None, conflict=lambda key: None, names=()):
    from wowbox.ui.macro_editor import MacroEditor
    tested = []
    dlg = MacroEditor(None, macro, targets=["all", "followers", "Gnope", "Kunth", "class:WARLOCK"],
                      macro_names=list(names), conflict=conflict, on_test=tested.append)
    return dlg, tested


@pytest.mark.parametrize("i", range(len(MACROS)))
def test_editor_shows_and_returns_the_same_macro(qapp, i):
    m = macros()[i]
    dlg, _ = editor(qapp, m)
    assert same(dlg.build(), m)


def test_editor_new_macro_with_steps(qapp):
    dlg, tested = editor(qapp)
    dlg.name_edit.setText("heal-and-bolt")
    dlg.key_edit.record(VK["H"], Mod.CTRL | Mod.ALT)
    assert dlg.key_edit.text() == "Ctrl+Alt+H"
    t = dlg.table
    t.cellWidget(0, 1).setText("/cast [@Tinkerdawn] Healing Wave")
    t.cellWidget(0, 2).setCurrentText("Kunth")
    t.setCurrentCell(0, 1)
    dlg.add_row()
    t.cellWidget(1, 0).setCurrentIndex(3)  # Wait
    t.cellWidget(1, 1).setText("1.5s")
    dlg.add_row()
    t.cellWidget(2, 0).setCurrentIndex(2)  # Press keys
    t.cellWidget(2, 1).setText("1-3, 0")
    t.cellWidget(2, 2).setCurrentText("[2, 3]")
    t.cellWidget(2, 3).setText("1s")
    m = dlg.build()
    steps = flat_steps(m.do)
    assert m.key == "Ctrl+Alt+H"
    assert steps[0].type == "/cast [@Tinkerdawn] Healing Wave" and steps[0].to == "Kunth"
    assert steps[1].wait == 1.5
    assert steps[2].keys == ["1-3", "0"] and steps[2].to == [2, 3] and steps[2].delay == 1.0
    dlg.test()
    assert tested and same(tested[0], m)
    # reorder and remove
    t.setCurrentCell(2, 1)
    dlg.move(-1)
    assert [r["kind"] for r in dlg.read()] == ["type", "keys", "wait"]
    dlg.remove_row()
    assert [r["kind"] for r in dlg.read()] == ["type", "wait"]


@pytest.mark.parametrize("setup,message", [
    (lambda d: d.name_edit.setText(""), "name"),
    (lambda d: d.table.cellWidget(0, 1).setText(""), "Step 1"),
    (lambda d: d.table.cellWidget(0, 0).setCurrentIndex(1) or d.table.cellWidget(0, 1).setText("NotAKey"), "Step 1"),
    (lambda d: d.name_edit.setText("taken"), "already a macro"),
])
def test_editor_errors(qapp, setup, message):
    dlg, _ = editor(qapp, macros()[1], names=["taken"])
    setup(dlg)
    with pytest.raises(ValueError, match=message):
        dlg.build()
    dlg.save()
    assert dlg.result_macro is None and message.split()[0].lower() in dlg.error.text().lower()


def test_editor_hotkey_conflict(qapp):
    dlg, _ = editor(qapp, macros()[1], conflict=lambda key: "the WoWBox stack hotkey" if key == "F12" else None)
    dlg.key_edit.record(VK["F12"], Mod.NONE)
    assert "already used by the WoWBox stack hotkey" in dlg.key_warning.text()
    with pytest.raises(ValueError, match="already used"):
        dlg.build()
    dlg.key_edit.record(VK["LSHIFT"], Mod.SHIFT)  # a bare modifier doesn't replace the hotkey
    assert dlg.key_edit.text() == "F12"


def test_runner_test_run_isnt_listed():
    from wowbox.automation.macros import MacroRunner

    class Sender:
        held_mods = Mod.NONE

        def send_chord(self, chord, target, check_typing=True):
            return [1]

        def type_text(self, text, target):
            return [1]

    r = MacroRunner(macros(), Sender())
    t = r.run_config(macros()[1])
    t.join(2)
    assert r.saved() == ["gnope-assist-and-nuke", "portal-stone", "slots"]


def test_controller_hotkey_owner_words():
    from conftest import Rig, make_cfg
    rig = Rig(make_cfg(macros=MACROS))
    rig.router.set_hotkeys(rig.cfg.hotkeys, rig.cfg.macros)
    from wowbox.keys import parse_chord
    assert rig.router.hotkey_owner(parse_chord("F8")) == "macro:gnope-assist-and-nuke"
    assert rig.router.hotkey_owner(parse_chord("F12")) == "stack"
