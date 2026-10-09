import pytest

from wowbox.automation import AutomationEngine
from wowbox.bridge.decoder import ClientState
from wowbox.config import AppConfig


class FakeSender:
    def __init__(self):
        self.sent = []

    def send_chord(self, chord, target):
        self.sent.append((str(chord), target))
        return [1]


class Source:
    def __init__(self):
        self.st = {1: ClientState(), 3: ClientState(power_pct=80)}
        self.missing = []

    def states(self):
        return self.st

    def missing_windows(self):
        return self.missing


def engine(rules, **opts):
    cfg = AppConfig.model_validate({"automation": rules,
                                    "automation_options": {"enabled_on_start": True, "dry_run": False, **opts}})
    sender, src = FakeSender(), Source()
    return AutomationEngine(cfg.automation, cfg.automation_options, sender, src), sender, src


DRINK = {"name": "drink", "when": {"slot": 3, "combat": False, "power_pct_lt": 40},
         "do": {"send": "Ctrl+9", "to": [3]}, "cooldown": "20s"}


def test_condition_and_cooldown():
    e, sender, src = engine([DRINK])
    e.tick(0)
    assert sender.sent == []
    src.st[3] = ClientState(power_pct=30)
    e.tick(1)
    assert sender.sent == [("Ctrl+9", [3])]
    e.tick(5)  # cooldown
    assert len(sender.sent) == 1
    e.tick(22)
    assert len(sender.sent) == 2
    src.st[3] = ClientState(power_pct=30, combat=True)
    e.tick(50)
    assert len(sender.sent) == 2


def test_every_and_sequence_with_wait():
    rule = {"name": "buff", "every": "10s",
            "do": {"sequence": [{"send": "F5", "to": "all"}, {"wait": "1.5s"}, {"send": "F6", "to": "all"}]}}
    e, sender, _ = engine([rule])
    e.tick(0)  # arms the timer
    e.tick(9.9)
    assert sender.sent == []
    e.tick(10)
    assert sender.sent == [("F5", "all")]
    e.tick(11)
    assert len(sender.sent) == 1
    e.tick(11.6)
    assert sender.sent[-1] == ("F6", "all")
    e.tick(20.1)
    assert sender.sent[-1] == ("F5", "all")


def test_dry_run_sends_nothing():
    e, sender, src = engine([DRINK], dry_run=True)
    src.st[3] = ClientState(power_pct=10)
    events = []
    e.on_event = events.append
    e.tick(0)
    assert sender.sent == []
    assert any("dry run" in m for m in events)


def test_pauses_on_stale_bridge_and_lost_window():
    e, sender, src = engine([DRINK])
    src.st[3] = ClientState(power_pct=10)
    src.st[1] = ClientState(stale=True)
    e.tick(0)
    assert sender.sent == [] and "stale" in e.paused_reason
    src.st[1] = ClientState()
    src.missing = [2]
    e.tick(1)
    assert sender.sent == [] and "window lost" in e.paused_reason
    src.missing = []
    e.tick(2)
    assert e.paused_reason is None and len(sender.sent) == 1


def test_kill_switch():
    e, sender, src = engine([DRINK])
    src.st[3] = ClientState(power_pct=10)
    e.kill()
    e.tick(0)
    assert sender.sent == []


def test_disabled_rule_and_any_slot():
    rule = {"name": "low hp anywhere", "when": {"health_pct_lt": 30}, "do": {"send": "F9", "to": "leader"}}
    e, sender, src = engine([rule, {**DRINK, "enabled": False}])
    src.st[1] = ClientState(health_pct=20)
    src.st[3] = ClientState(power_pct=1)
    e.tick(0)
    assert sender.sent == [("F9", "leader")]


def test_unknown_field_rejected():
    with pytest.raises(ValueError, match="unknown condition field"):
        engine([{"name": "x", "when": {"mana_lt": 3}, "do": {"send": "1"}}])
