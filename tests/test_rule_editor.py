import os

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wowbox.automation import AutomationEngine  # noqa: E402
from wowbox.automation.engine import condition_report  # noqa: E402
from wowbox.bridge.decoder import ClientState  # noqa: E402
from wowbox.config import AppConfig, AutomationRule, load_config  # noqa: E402
from wowbox.macro_store import flat_steps  # noqa: E402
from wowbox.rule_store import automation_path, dump_rules, load_rules_text, migrate_rules  # noqa: E402

RULES = [
    {"name": "healer-drinks", "enabled": False,
     "when": {"slot": 3, "combat": False, "power_pct_lt": 40, "mounted": False}, "do": {"send": "Ctrl+9", "to": [3]},
     "cooldown": "20s"},
    {"name": "rebuff", "every": "25m",
     "do": {"sequence": [{"send": "F5", "to": "all"}, {"wait": "1.5s"}, {"send": "F6", "to": "all"}]}},
    {"name": "heal-on-whisper",
     "chat": {"channel": "whisper", "sender": ["Tinkerdawn", "Gnope"], "contains": ["heal"], "heard_by": "Kunth"},
     "do": {"sequence": [{"type": "/cast [@{sender}] Healing Wave", "to": "Kunth"}]}, "cooldown": "3s"},
    {"name": "chain-if-free",
     "chat": {"channel": ["whisper", "party"], "contains": ["chain"], "heard_by": "Kunth"},
     "when": {"slot": "any", "casting": False, "health_pct_gte": 50.5},
     "do": {"type": "/cast Chain Heal", "to": "Kunth"}},
]

TEAM = """\
# my team
client:
  exe: "C:/WoW/Wow.exe"   # keep me

automation:
  - name: healer-drinks
    when: { slot: 3, combat: false }
    do: { send: "Ctrl+9", to: [3] }

automation_options:
  enabled_on_start: false   # keep me too
  dry_run: true
"""


def rules():
    return [AutomationRule.model_validate(r) for r in RULES]


def key(r: AutomationRule):
    return (r.name, r.enabled, r.when and r.when.model_dump(), r.every, r.chat and r.chat.model_dump(), r.cooldown,
            [s.model_dump() for s in flat_steps(r.do)])


# ---------------------------------------------------------------- storage
def test_rules_round_trip():
    text = dump_rules(rules())
    assert [key(r) for r in load_rules_text(text)] == [key(r) for r in rules()]
    assert "every: 25m" in text and "cooldown: 20s" in text


def test_migrate_keeps_automation_options_and_comments(tmp_path):
    team = tmp_path / "team.yaml"
    team.write_text(TEAM, encoding="utf-8")
    migrate_rules(team, rules())
    new = team.read_text(encoding="utf-8")
    assert "automation: moved to automation.yaml" in new
    assert "# keep me" in new and "# keep me too" in new and "automation_options:" in new
    assert "healer-drinks" not in new
    cfg = load_config(team)
    assert [r.name for r in cfg.automation] == [r["name"] for r in RULES]
    assert cfg.automation_options.dry_run is True and automation_path(team).exists()


# ---------------------------------------------------------------- engine
class Src:
    def __init__(self, st):
        self.st = st

    def states(self):
        return self.st

    def missing_windows(self):
        return []

    def slots_for(self, target):
        return [3]


def test_condition_report_says_why():
    cond = rules()[0].when
    ok, per = condition_report(cond, {3: ClientState(power_pct=62.0)})
    assert not ok and per == [(3, False, "power_pct is 62, needs < 40")]
    ok, per = condition_report(cond, {3: ClientState(power_pct=30.0)})
    assert ok and per == [(3, True, "")]
    ok, per = condition_report(cond, {3: ClientState(combat=True, power_pct=30.0)})
    assert per[0][2] == "combat is yes, needs = no"
    assert condition_report(cond, {3: ClientState(stale=True)})[1][0][2] == "no bridge data"


def test_reload_keeps_counts_and_paused_rules_dont_fire():
    cfg = AppConfig.model_validate({"automation": [RULES[0] | {"enabled": True}],
                                    "automation_options": {"enabled_on_start": True, "dry_run": True}})
    e = AutomationEngine(cfg.automation, cfg.automation_options, None, Src({3: ClientState(power_pct=10)}))
    e.tick(0)
    assert e.rules[0].fire_count == 1
    e.load(cfg.automation)
    assert e.rules[0].fire_count == 1 and e.rules[0].last_fired == 0
    e.paused_rules.add("healer-drinks")
    e.tick(100)
    assert e.rules[0].fire_count == 1
    e.paused_rules.clear()
    e.tick(200)
    assert e.rules[0].fire_count == 2


# ---------------------------------------------------------------- editor
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def editor(qapp, rule=None, names=()):
    from wowbox.ui.automation_editor import RuleEditor
    tested = []
    dlg = RuleEditor(None, rule, targets=["all", "Kunth", "Gnope", "class:SHAMAN"],
                     who_choices=["any", "all", "1 (Tinkerdawn)", "3 (Kunth)"], rule_names=list(names),
                     preview=lambda r: f"preview of {r.name}", on_test=lambda r: tested.append(r) or "tested")
    return dlg, tested


@pytest.mark.parametrize("i", range(len(RULES)))
def test_editor_round_trips_every_kind_of_rule(qapp, i):
    dlg, _ = editor(qapp, rules()[i])
    assert key(dlg.build()) == key(rules()[i])


def test_editor_new_state_rule(qapp):
    dlg, tested = editor(qapp)
    dlg.name_edit.setText("drink")
    dlg.who.setCurrentText("3 (Kunth)")
    c = dlg.conditions
    c.add()  # power_pct < 40 next to the default "combat = no"
    dlg.table.cellWidget(0, 1).setText("Ctrl+9")
    dlg.table.cellWidget(0, 0).setCurrentIndex(1)  # Press key
    dlg.table.cellWidget(0, 1).setText("Ctrl+9")
    dlg.table.cellWidget(0, 2).setCurrentText("Kunth")
    dlg.cooldown.setText("20s")
    r = dlg.build()
    assert r.when.slot == 3 and r.when.model_extra == {"combat": False, "power_pct_lt": 40}
    assert r.cooldown == 20 and flat_steps(r.do)[0].send == "Ctrl+9"
    dlg.refresh_check()
    assert dlg.check.text() == "preview of drink"
    dlg.test()
    assert tested and dlg.test_out.text() == "tested"


def test_editor_switches_trigger(qapp):
    dlg, _ = editor(qapp, rules()[0])
    dlg.trigger.setCurrentIndex(dlg.trigger.findData("every"))
    assert dlg.timer_box.isVisibleTo(dlg) and not dlg.state_box.isVisibleTo(dlg)
    dlg.every.setText("10m")
    r = dlg.build()
    assert r.every == 600 and r.when is None and r.chat is None
    dlg.trigger.setCurrentIndex(dlg.trigger.findData("chat+state"))
    assert dlg.chat_box.isVisibleTo(dlg) and dlg.state_box.title() == "And only if"
    assert "{sender}" in dlg.steps_hint.text()


@pytest.mark.parametrize("setup,message", [
    (lambda d: d.name_edit.setText(""), "name"),
    (lambda d: d.conditions.table.cellWidget(0, 2).setCurrentText("maybe"), "use yes or no"),
    (lambda d: d.conditions.table.cellWidget(1, 2).setCurrentText("lots"), "isn't a number"),
    (lambda d: d.who.setCurrentText("tank"), "Who"),
    (lambda d: d.cooldown.setText("soon"), "Cooldown"),
    (lambda d: d.table.cellWidget(0, 1).setText("Ctrl+Banana"), "Step 1"),
    (lambda d: d.name_edit.setText("taken"), "already a rule"),
])
def test_editor_errors(qapp, setup, message):
    dlg, _ = editor(qapp, rules()[0], names=["taken"])
    setup(dlg)
    with pytest.raises(ValueError, match=message):
        dlg.build()
    dlg.save()
    assert dlg.result_rule is None and dlg.error.text()


def test_chat_editor_needs_a_channel(qapp):
    dlg, _ = editor(qapp, rules()[2])
    for cb in dlg.channel_boxes.values():
        cb.setChecked(False)
    with pytest.raises(ValueError, match="channel"):
        dlg.build()
    dlg.refresh_check()
    assert "Not complete yet" in dlg.check.text()


def test_text_fields_and_unknown_fields(qapp):
    rule = AutomationRule.model_validate({"name": "x", "when": {"slot": "all", "class_token": "WARLOCK",
                                                                "power_name_ne": "rage"}, "do": {"send": "1"}})
    dlg, _ = editor(qapp, rule)
    assert key(dlg.build()) == key(rule)
    assert yaml.safe_load(dump_rules([rule]))["automation"][0]["when"]["slot"] == "all"


def test_editor_from_all_explains_itself(qapp):
    dlg, _ = editor(qapp, rules()[2])
    dlg.senders.setText("all")
    with pytest.raises(ValueError, match="Leave From empty"):
        dlg.build()
    dlg.senders.setText("team, Randomfriend")
    assert dlg.build().chat.sender == ["team", "Randomfriend"]
