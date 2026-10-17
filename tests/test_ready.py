"""Watched spells (`ready:` conditions), rule on/off from the game (/bb rule, hotkeys) and the panel's rules."""
from pathlib import Path

import numpy as np
import pytest

from wowbox.automation.engine import compile_condition, condition_report
from wowbox.bridge import words
from wowbox.bridge.decoder import ClientState, decode, encode
from wowbox.config import AutomationRule, Condition
from wowbox.panel import encode as panel_bits


def rule(name, when=None, enabled=True, **kw):
    data = {"name": name, "enabled": enabled, "do": {"send": "Ctrl+Shift+F5", "to": "Tinkerdawn"}}
    data.update({"when": when} if when else {} if "chat" in kw else {"every": "1m"})
    data.update(kw)
    return AutomationRule.model_validate(data)


AVENGER = rule("Avenger's Shield on cooldown", {"slot": 1, "ready": "Avenger's Shield", "combat": True},
               hotkey="Ctrl+Alt+A", panel=True)


# ---------------------------------------------------------------- the bridge
def test_ready_bits_ride_in_spare_flag_bits():
    values = {"player_flags": 0b1011, "status_flags": 0b10101, "ready_bits": 0b1010110, "health": 200}
    strip = encode(values, 3, 8)
    img = np.full((strip.shape[0] + 10, strip.shape[1] + 40, 3), 37, dtype=np.uint8)
    img[: strip.shape[0], : strip.shape[1]] = strip
    st = decode(img)
    assert st.ready_bits == 0b1010110
    assert (st.combat, st.resting, st.in_party, st.in_raid) == (True, True, False, True)
    assert (st.mounted, st.dead, st.ghost, st.afk, st.following) == (True, False, True, False, True)


# ---------------------------------------------------------------- Words.lua
def test_spells_come_from_enabled_ready_conditions():
    rules = [AVENGER,
             rule("two", {"slot": 1, "ready": ["avenger's shield", "Holy Shield"]}),
             rule("not", {"slot": 1, "ready_ne": "Hammer of the Righteous"}),
             rule("off", {"slot": 1, "ready": "Consecration"}, enabled=False)]
    assert words.spells_asked(rules) == ["Avenger's Shield", "Holy Shield", "Hammer of the Righteous"]
    f = words.build(rules)
    assert f.spells == ("Avenger's Shield", "Holy Shield", "Hammer of the Righteous")
    assert f.panel == ("Avenger's Shield on cooldown",)


def test_too_many_spells_and_panel_rules_are_cut():
    rules = [rule(f"r{i}", {"slot": 1, "ready": f"Spell {i}"}, panel=True) for i in range(10)]
    f = words.build(rules)
    assert len(f.spells) == words.MAX_SPELLS and len(f.panel) == words.MAX_PANEL_RULES


def test_words_lua_round_trips_the_lists(tmp_path: Path):
    f = words.build([AVENGER, rule("chat", chat={"channel": "party", "contains": ["heal"]})])
    text = words.lua(f, 4)
    assert 'spells = { "Avenger\'s Shield" },' in text and "panel = {" in text
    assert words.parse(text) == f
    plain = words.build([rule("chat", chat={"channel": "party", "contains": ["heal"]})])
    assert "spells" not in words.lua(plain, 4)  # unused: the file (and its version) stays as it was
    seen = {}
    v1, _ = words.sync(tmp_path, plain, seen)
    v2, changed = words.sync(tmp_path, f, seen)
    assert changed and v2 == v1 + 1 and seen[v2].spells == ("Avenger's Shield",)


def test_chat_filter_off_still_carries_the_lists():
    f = words.build([AVENGER], chat=False)
    assert set(f.channels.values()) == {words.ALL} and f.spells == ("Avenger's Shield",)


# ---------------------------------------------------------------- the condition
def test_ready_condition():
    cond = Condition.model_validate({"slot": 1, "ready": "Avenger's Shield", "combat": True})
    preds = compile_condition(cond)
    st = ClientState(combat=True, ready=("avenger's shield",))
    assert condition_report(cond, {1: st}, preds)[0]
    ok, per = condition_report(cond, {1: ClientState(combat=True)}, preds)
    assert not ok and per[0][2] == "Avenger's Shield not ready"
    ne = Condition.model_validate({"slot": 1, "ready_ne": ["Holy Shield"]})
    assert condition_report(ne, {1: st})[0]
    assert not condition_report(ne, {1: ClientState(ready=("holy shield",))})[0]


# ---------------------------------------------------------------- the controller
@pytest.fixture
def c(tmp_path):
    from test_panel import _controller
    c = _controller(tmp_path)
    c._store_rules([AVENGER, rule("Shaman Buff GROUP"), rule("Shaman Buff Tgt")])
    return c


def test_bridge_state_gets_the_ready_names_of_the_list_that_client_loaded(c):
    assert c.words_version and (Path(c.cfg.bridge.addon_dir) / "Words.lua").exists()
    st = ClientState(chat_filter=c.words_version, ready_bits=1)
    c._on_bridge_state(1, st)
    assert st.ready == ("avenger's shield",)
    old = ClientState(chat_filter=99, ready_bits=1)  # a list this WoWBox never wrote
    c._on_bridge_state(1, old)
    assert old.ready == ()


def test_bb_rule_toggles_by_the_start_of_its_name(c):
    c.team_command(1, "@wowbox", "rule aven")
    assert not c.find_rule("Avenger")[0].enabled
    last = c.activity.items()[-1].text
    assert "Avenger's Shield on cooldown" in last and last.endswith("turned OFF")
    c.team_command(1, "@wowbox", "rule AVENGER'S SHIELD ON COOLDOWN")
    assert c.find_rule("aven")[0].enabled
    c.team_command(1, "@wowbox", "rule shaman buff")  # two rules start like that
    assert "2 rules match" in c.activity.items()[-1].text
    c.team_command(1, "@wowbox", "rule nothing")
    assert "no rule matches" in c.activity.items()[-1].text
    assert c.find_rule("buff tgt")[0].name == "Shaman Buff Tgt"  # inside the name, when nothing starts so


def test_rule_hotkey_toggles_and_shows_as_taken(c):
    from wowbox.keys import parse_chord
    assert c.router.hotkey_owner(parse_chord("Ctrl+Alt+A")) == "rule:Avenger's Shield on cooldown"
    assert c.macro_hotkey_owner("Ctrl+Alt+A") == "rule \"Avenger's Shield on cooldown\""
    assert c.macro_hotkey_owner("Ctrl+Alt+A", ignore_rule="Avenger's Shield on cooldown") is None
    c._on_hotkey("rule:Avenger's Shield on cooldown")
    assert not c.find_rule("aven")[0].enabled


def test_panel_flags_carry_the_panel_rules(c):
    assert c.panel_flags()["rule1"] is True
    c.set_rule_enabled("Avenger's Shield on cooldown", False)
    flags = c.panel_flags()
    assert flags["rule1"] is False and panel_bits(flags) >> 6 == 0


def test_words_written_for_spells_even_with_the_chat_filter_off(c):
    c.cfg.bridge.chat_filter = False
    c.sync_chat_filter()
    assert c.words_version
    c._store_rules([rule("plain")])
    assert c.words_version is None
