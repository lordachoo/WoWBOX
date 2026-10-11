import os
import shutil
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wowbox import settings  # noqa: E402
from wowbox.config import AppConfig, load_config  # noqa: E402

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=["team.example.yaml", "team.yaml"])
def controller(request, tmp_path, qapp):
    """A Controller on a copy of the example config, and of your own team.yaml when there is one."""
    src = ROOT / "config" / request.param
    if not src.exists():
        pytest.skip(f"no {request.param}")
    shutil.copy(src, tmp_path / "team.yaml")
    from wowbox.app import Controller
    from wowbox.platform.fake import FakeCapture, FakeInput, FakeWindows
    return Controller(tmp_path / "team.yaml", load_config(tmp_path / "team.yaml"), FakeWindows(), FakeInput(),
                      lambda: FakeCapture())


def test_every_setting_exists_and_shows():
    cfg = AppConfig()
    for s in settings.ALL:
        model = getattr(cfg, s.section)
        assert s.key in type(model).model_fields, s
        assert settings.show(model, s.key)


def test_apply_is_live_and_bad_values_are_refused(controller):
    c = controller
    assert c.apply_setting("automation_options", "chat_queue", 2) is None
    assert c.engine.options.chat_queue == 2  # the engine reads the same object
    assert c.apply_setting("automation_options", "rule_gap", 2.5) is None and c.engine.options.rule_gap == 2.5
    assert c.apply_setting("broadcast", "type_quiet", 0.5) is None and c.router.cfg.type_quiet == 0.5
    assert c.apply_setting("broadcast", "chatguard", False) is None and c.chatguard.enabled is False
    assert c.apply_setting("stack", "tolerance_yards", 0.3) is None and c.stacker.cfg.tolerance_yards == 0.3
    assert c.apply_setting("automation_options", "chat_queue", 999)  # out of range: a message back
    assert c.engine.options.chat_queue == 2
    assert {s.key for s in c.unsaved_settings()} >= {"chat_queue", "rule_gap", "type_quiet", "chatguard",
                                                     "tolerance_yards"}


def test_save_writes_only_those_lines_and_keeps_comments(controller):
    c = controller
    before = c.cfg_path.read_text(encoding="utf-8")
    c.apply_setting("automation_options", "rule_gap", 2.0)
    c.apply_setting("follow", "mirror_max_distance_yards", 55.0)
    done = c.save_settings()
    assert set(done) == {"automation_options.rule_gap", "follow.mirror_max_distance_yards"}
    after = c.cfg_path.read_text(encoding="utf-8")
    import difflib
    diff = [l for l in difflib.ndiff(before.splitlines(), after.splitlines()) if l[:2] in ("- ", "+ ")]
    removed, added = [l[2:] for l in diff if l[:2] == "- "], [l[2:] for l in diff if l[:2] == "+ "]
    assert all("mirror_max_distance_yards" in l or "rule_gap" in l for l in removed + added)  # only those lines
    assert any("mirror_max_distance_yards: 55" in l for l in added) and any("rule_gap: 2s" in l for l in added)
    assert [l for l in before.splitlines() if l.lstrip().startswith("#")] == \
        [l for l in after.splitlines() if l.lstrip().startswith("#")]  # every comment line kept
    disk = load_config(c.cfg_path)
    assert disk.automation_options.rule_gap == 2.0 and disk.follow.mirror_max_distance_yards == 55.0
    assert c.unsaved_settings() == []
    assert yaml.safe_load(after)  # still valid


def test_revert_goes_back_to_the_file(controller):
    c = controller
    was = c.cfg.automation_options.chat_queue
    c.apply_setting("automation_options", "chat_queue", was + 1)
    c.revert_settings()
    assert c.cfg.automation_options.chat_queue == was and c.unsaved_settings() == []


def test_settings_tab_widgets_apply_and_flag_bad_input(controller):
    from wowbox.ui.settings_tab import SettingsTab, summary
    c = controller
    tab = SettingsTab(c)
    tab.widgets[("automation_options", "chat_queue")].setValue(3)
    assert c.engine.options.chat_queue == 3 and tab.b_save.isEnabled()
    w = tab.widgets[("automation_options", "rule_gap")]
    w.setText("soon")
    w.editingFinished.emit()
    assert "Not applied" in tab.status.text() and c.engine.options.rule_gap != 0
    w.setText("2s")
    w.editingFinished.emit()
    assert c.engine.options.rule_gap == 2.0 and w.text() == "2s"
    tab.widgets[("broadcast", "modifier_mode")].setCurrentText("strip")
    assert c.cfg.broadcast.modifier_mode == "strip"
    tab.save()
    assert "Saved to team.yaml" in tab.status.text() and not tab.b_save.isEnabled()
    assert "queue 3 per rule" in summary(c.cfg) and "rule gap 2s" in summary(c.cfg)


def test_reload_config_picks_up_automation_options_in_place(controller):
    c = controller
    opts = c.engine.options
    text = c.cfg_path.read_text(encoding="utf-8")
    from wowbox.config_text import set_settings
    set_settings(c.cfg_path, {("automation_options", "chat_queue"): "7"})
    c.reload_config()
    assert c.engine.options is opts and opts.chat_queue == 7  # same object, new value
    c.cfg_path.write_text(text, encoding="utf-8")
