"""automation.yaml: the automation rules the Automation tab edits, kept next to team.yaml.

Same arrangement as macros.yaml (see macro_store): the editor never rewrites team.yaml. When automation.yaml
exists it is the only source of rules; the first save from the editor moves team.yaml's `automation:` block
over and leaves a pointer comment in its place.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .config import AutomationRule
from .macro_store import flat_steps, fmt_duration, remove_section, step_dict

log = logging.getLogger(__name__)

HEADER = """\
# WoWBox automation rules. The Automation tab edits this file (New / Edit / Duplicate / Delete); you can edit
# it by hand too, then press Reload config. Format: docs/automation.md.
#   trigger: when (a state condition) | every (a timer) | chat (a chat line, optionally plus when)
#   steps:   type (a chat line), send (one key), keys (several), wait, macro (start one)
# Automation as a whole starts OFF and in dry run (toolbar); Ctrl+Alt+K is the kill switch.
"""
POINTER = "# automation: moved to automation.yaml (edited from the Automation tab)\n\n"


def automation_path(team_path: str | Path) -> Path:
    return Path(team_path).with_name("automation.yaml")


def rule_dict(rule: AutomationRule) -> dict:
    d: dict = {"name": rule.name}
    if not rule.enabled:
        d["enabled"] = False
    if rule.chat is not None:
        ch = rule.chat
        c: dict = {"channel": ch.channel[0] if len(ch.channel) == 1 else list(ch.channel)}
        if ch.sender:
            c["sender"] = list(ch.sender)
        if ch.contains:
            c["contains"] = list(ch.contains)
        c["heard_by"] = ch.heard_by
        d["chat"] = c
    if rule.when is not None:
        d["when"] = {"slot": rule.when.slot, **(rule.when.model_extra or {})}
    if rule.every is not None:
        d["every"] = fmt_duration(rule.every)
    steps = [step_dict(s) for s in flat_steps(rule.do)]
    d["do"] = steps[0] if len(steps) == 1 else {"sequence": steps}
    if rule.cooldown:
        d["cooldown"] = fmt_duration(rule.cooldown)
    if rule.queue is not None:
        d["queue"] = rule.queue
    if rule.priority:
        d["priority"] = rule.priority
    if rule.cooldown_per != "rule":
        d["cooldown_per"] = rule.cooldown_per
    if rule.hotkey:
        d["hotkey"] = rule.hotkey
    if rule.panel:
        d["panel"] = True
    return d


def dump_rules(rules: list[AutomationRule]) -> str:
    body = yaml.safe_dump({"automation": [rule_dict(r) for r in rules]}, sort_keys=False, allow_unicode=True,
                          default_flow_style=None, width=140)
    return HEADER + "\n" + body.replace("\n- name:", "\n\n- name:")


def load_rules_text(text: str) -> list[AutomationRule]:
    data = yaml.safe_load(text) or {}
    return [AutomationRule.model_validate(r) for r in (data.get("automation") or [])]


def save_rules(path: str | Path, rules: list[AutomationRule]) -> None:
    path = Path(path)
    text = dump_rules(rules)
    load_rules_text(text)  # never write a file we couldn't read back
    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(text, encoding="utf-8")


def migrate_rules(team_path: str | Path, rules: list[AutomationRule]) -> Path:
    """Create automation.yaml from `rules` and take the `automation:` block out of team.yaml (a .bak is kept)."""
    team_path = Path(team_path)
    target = automation_path(team_path)
    save_rules(target, rules)
    text = team_path.read_text(encoding="utf-8")
    new, found = remove_section(text, "automation", POINTER)
    if found:
        team_path.with_suffix(team_path.suffix + ".bak").write_text(text, encoding="utf-8")
        team_path.write_text(new, encoding="utf-8")
        log.info("automation rules moved from %s to %s", team_path.name, target.name)
    return target
