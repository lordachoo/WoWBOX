"""The Automation tab's grouping and search."""
import time

import pytest
from PySide6.QtCore import QSettings, Qt

from wowbox.automation.actions import flatten
from wowbox.automation.grouping import Member, matches, who_acts
from wowbox.config import AutomationRule

MEMBERS = [Member(1, "Tinkerdawn", "Tank", "tank", "PALADIN"), Member(2, "Gnope", "Lock", "dps", "WARLOCK"),
           Member(3, "Kunth", "Shaman", "healer", "SHAMAN"), Member(4, "", "Char4", "dps", "")]


def rule(name="r", to="Kunth", enabled=True, **kw) -> AutomationRule:
    data = {"name": name, "enabled": enabled, "chat": {"contains": ["heal"]},
            "do": {"type": "/cast Healing Wave", "to": to}}
    data.update(kw)
    return AutomationRule.model_validate(data)


def group(to, members=MEMBERS):
    return who_acts(flatten(rule(to=to).do), members, leader=1)


@pytest.mark.parametrize("to", ["Kunth", "class:SHAMAN", "role:healer", "char:kunth", [3]])
def test_ways_of_naming_one_character_share_a_group(to):
    g = group(to)
    assert g.key == "slot:3" and g.title == "3 Kunth · Shaman"


def test_leader_all_and_several():
    assert group("leader").key == "slot:1"
    assert group("all").key == group("followers").key == group("not:Kunth").key == "several"
    assert group([1, 3]).key == "several"
    assert group("role:dps").key == "several"  # two slots have it
    assert group("active").key == "active"


def test_class_nobody_has_yet_gets_its_own_group():
    g = group("class:SHAMAN", [Member(3, "Kunth", role="healer")])  # class not reported yet
    assert g.key == "to:class:shaman" and "Shaman" in g.title
    assert group("Bob").title == "Bob"


def test_mixed_steps_and_macros():
    r = rule(do={"sequence": [{"type": "/cast A", "to": "Kunth"}, {"wait": "1s"},
                                {"type": "/cast B", "to": "class:SHAMAN"}]})
    assert who_acts(flatten(r.do), MEMBERS, 1).key == "slot:3"
    r = rule(do={"sequence": [{"type": "/cast A", "to": "Kunth"}, {"type": "/cast B", "to": "Gnope"}]})
    assert who_acts(flatten(r.do), MEMBERS, 1).key == "several"
    r = rule(do={"macro": "buffs"})
    assert who_acts(flatten(r.do), MEMBERS, 1).key == "macros"


def test_search_needs_every_word():
    assert matches("kings pal", "Paladin buffs", "/cast Blessing of Kings")
    assert not matches("kings shaman", "Paladin buffs", "/cast Blessing of Kings")
    assert matches("", "anything")


# ---------------------------------------------------------------- the widget
class FakeStatus:
    state, last, next, progress, ready = "ready", "", "", None, True


class FakeEngine:
    enabled, dry_run, paused_reason = True, False, ""

    def __init__(self, rules):
        from wowbox.automation.engine import RuleRuntime
        self.rules = [RuleRuntime(r, flatten(r.do)) for r in rules]

    def status(self, rt, now):
        return FakeStatus()


class FakeController:
    def __init__(self, rules):
        self.engine = FakeEngine(rules)
        self.toggled = []

    def rule_members(self):
        return MEMBERS, 1

    def set_rules_enabled(self, names, on):
        self.toggled.append((names, on))


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make(qapp, tmp_path):
    from wowbox.ui.rule_list import RuleList

    def make(rules):
        c = FakeController(rules)
        settings = QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)
        w = RuleList(c, on_edit=lambda n, copy: None, on_delete=lambda n: None, settings=settings)
        w.load()
        return w, c
    return make


RULES = [rule("heal", to="class:SHAMAN"), rule("chain", to="Kunth", enabled=False),
         rule("kings", to="class:PALADIN", do={"type": "/cast Blessing of Kings", "to": "class:PALADIN"}),
         rule("help", to="all")]


def heads(w):
    return {h.data(0, Qt.UserRole + 1): [h.child(i).data(0, Qt.UserRole) for i in range(h.childCount())]
            for h in w.groups}


def test_groups_by_who_acts(make):
    w, _ = make(RULES)
    assert heads(w) == {"slot:1": ["kings"], "slot:3": ["heal", "chain"], "several": ["help"]}
    shaman = next(h for h in w.groups if h.data(0, Qt.UserRole + 1) == "slot:3")
    assert shaman.checkState(0) == Qt.PartiallyChecked and "2 rules, 1 on" in shaman.text(0)
    assert [h.data(0, Qt.UserRole + 1) for h in w.groups] == ["slot:1", "slot:3", "several"]  # slot order


def test_group_by_trigger_on_off_and_none(make):
    w, _ = make(RULES + [rule("timer", chat=None, every="5m")])
    w.group_by.setCurrentText("On/off")
    assert heads(w) == {"on": ["heal", "kings", "help", "timer"], "off": ["chain"]}
    w.group_by.setCurrentText("Trigger")
    assert heads(w) == {"chat": ["heal", "chain", "kings", "help"], "timer": ["timer"]}
    w.group_by.setCurrentText("None")
    assert w.groups == [] and w.tree.topLevelItemCount() == 5
    assert w.settings.value("automation/group_by") == "None"


def test_search_hides_rules_and_empty_groups(make):
    w, _ = make(RULES)
    w.search.setText("blessing kings")
    shown = [n for n, it in w.items.items() if not it.isHidden()]
    assert shown == ["kings"]
    assert [h.isHidden() for h in w.groups] == [False, True, True]
    w.search.setText("")
    assert not any(it.isHidden() for it in w.items.values())


def test_group_box_toggles_the_rules_it_shows(make, qapp):
    w, c = make(RULES)
    shaman = next(h for h in w.groups if h.data(0, Qt.UserRole + 1) == "slot:3")
    shaman.setCheckState(0, Qt.Checked)
    deadline = time.monotonic() + 1
    while not c.toggled and time.monotonic() < deadline:
        qapp.processEvents()
    assert c.toggled == [(["chain"], True)]  # heal was already on


def test_collapsed_groups_are_remembered(make):
    w, _ = make(RULES)
    w.groups[0].setExpanded(False)
    w.load()
    assert [h.isExpanded() for h in w.groups] == [False, True, True]
    w.search.setText("kings")  # search opens everything it shows, without forgetting
    assert w.groups[0].isExpanded()
    w.search.setText("")
    assert not w.groups[0].isExpanded()
