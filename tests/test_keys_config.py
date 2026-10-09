from pathlib import Path

import pytest

from wowbox.config import AppConfig, load_config, parse_duration, save_config
from wowbox.keys import Chord, Mod, expand_key_spec, parse_chord, vk_from_name

ROOT = Path(__file__).resolve().parents[1]


def test_parse_chord():
    assert parse_chord("1") == Chord(0x31)
    assert parse_chord("Ctrl+Alt+Pause") == Chord(0x13, Mod.CTRL | Mod.ALT)
    assert parse_chord("shift+f5") == Chord(0x74, Mod.SHIFT)
    assert parse_chord("-") == Chord(0xBD)
    assert parse_chord("Ctrl+-") == Chord(0xBD, Mod.CTRL)
    assert parse_chord("Esc") == parse_chord("Escape") == Chord(0x1B)
    assert str(parse_chord("alt+ctrl+1")) == "Ctrl+Alt+1"
    with pytest.raises(ValueError):
        parse_chord("Hyper+1")
    with pytest.raises(ValueError):
        vk_from_name("NotAKey")


def test_expand_ranges():
    assert [c.vk for c in expand_key_spec("1-9")] == list(range(0x31, 0x3A))
    assert [c.vk for c in expand_key_spec("F1-F3")] == [0x70, 0x71, 0x72]
    assert expand_key_spec("Ctrl+1-2") == [Chord(0x31, Mod.CTRL), Chord(0x32, Mod.CTRL)]
    assert expand_key_spec("-") == [Chord(0xBD)]


def test_durations():
    assert parse_duration("1.5s") == 1.5
    assert parse_duration("25m") == 1500
    assert parse_duration("200ms") == pytest.approx(0.2)
    assert parse_duration(3) == 3.0
    with pytest.raises(ValueError):
        parse_duration("soon")


def test_example_config_loads_and_roundtrips(tmp_path):
    cfg = load_config(ROOT / "config" / "team.example.yaml")
    assert cfg.team.leader == 1 and len(cfg.team.slots) == 4
    assert cfg.routes[3].to == [3]
    assert cfg.automation[1].every == 1500
    assert cfg.automation[1].do.sequence[1].wait == 1.5
    out = tmp_path / "team.yaml"
    save_config(cfg, out)
    assert load_config(out) == cfg


def test_validation_errors():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"team": {"leader": 5, "slots": {1: {}}}})
    with pytest.raises(ValueError):
        AppConfig.model_validate({"routes": [{"keys": ["Bogus+1"], "to": "all"}]})
    with pytest.raises(ValueError):  # needs exactly one of when/every
        AppConfig.model_validate({"automation": [{"name": "x", "do": {"send": "1"}}]})
    with pytest.raises(ValueError):  # action needs exactly one kind
        AppConfig.model_validate({"automation": [{"name": "x", "every": "1s", "do": {"send": "1", "wait": "1s"}}]})
    assert AppConfig.model_validate({"routes": [{"keys": ["1"], "to": 2}]}).routes[0].to == [2]


def test_legacy_broadcast_keys_map_to_hotkeys():
    cfg = AppConfig.model_validate({"broadcast": {"toggle_key": "F12"}})
    assert cfg.hotkeys.toggle_broadcast == "F12"
