"""macros.yaml: the macros the Macros tab edits, kept next to team.yaml.

team.yaml is yours, comments and all, so the macro editor never rewrites it. Macros live in their own file
instead. When macros.yaml exists it is the only source of macros; a `macros:` section left in team.yaml is
then ignored. The first save from the editor moves team.yaml's macros over (`migrate`): it writes them to
macros.yaml and replaces team.yaml's `macros:` block with a pointer comment, leaving every other line alone.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .config import ActionConfig, MacroConfig

log = logging.getLogger(__name__)

HEADER = """\
# WoWBox macros. The Macros tab edits this file (New / Edit / Duplicate / Delete); you can edit it by hand too,
# then press Reload config. Format: README section 12.
#   steps: type (a chat line), send (one key), keys (several, `delay` apart), wait, macro (start another)
#   to:    all | followers | leader | not:leader | a character name | class:WARLOCK | role:healer | [slots]
"""
DEFAULT_DELAY = ActionConfig.model_fields["delay"].default


def macros_path(team_path: str | Path) -> Path:
    return Path(team_path).with_name("macros.yaml")


def load_macros(path: str | Path) -> list[MacroConfig]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [MacroConfig.model_validate(m) for m in (data.get("macros") or [])]


def fmt_duration(seconds: float) -> str:
    ms = round(seconds * 1000)
    if ms < 1000 and abs(seconds * 1000 - ms) < 1e-6:
        return f"{ms}ms"
    return f"{seconds:g}s"


def flat_steps(action: ActionConfig) -> list[ActionConfig]:
    """A macro's `do` as a flat list of single steps (nested sequences flattened)."""
    if action.sequence is None:
        return [action]
    out: list[ActionConfig] = []
    for sub in action.sequence:
        out.extend(flat_steps(sub))
    return out


def step_dict(step: ActionConfig) -> dict:
    """One step as it's written to YAML: its kind first, then delay/to only where they mean something."""
    if step.wait is not None:
        return {"wait": fmt_duration(step.wait)}
    if step.macro is not None:
        return {"macro": step.macro}
    if step.send is not None:
        d: dict = {"send": step.send}
    elif step.type is not None:
        d = {"type": step.type}
    elif step.keys is not None:
        d = {"keys": list(step.keys)}
        if step.delay != DEFAULT_DELAY:
            d["delay"] = fmt_duration(step.delay)
    else:
        raise ValueError("a nested sequence isn't a single step")
    d["to"] = step.to
    return d


def macro_dict(m: MacroConfig) -> dict:
    d: dict = {"name": m.name}
    if m.key:
        d["key"] = m.key
    if m.note:
        d["note"] = m.note
    steps = [step_dict(s) for s in flat_steps(m.do)]
    d["do"] = steps[0] if len(steps) == 1 else {"sequence": steps}
    return d


def dump_macros(macros: list[MacroConfig]) -> str:
    body = yaml.safe_dump({"macros": [macro_dict(m) for m in macros]}, sort_keys=False, allow_unicode=True,
                          default_flow_style=None, width=140)
    # a blank line between macros reads better
    body = body.replace("\n- name:", "\n\n- name:")
    return HEADER + "\n" + body


def save_macros(path: str | Path, macros: list[MacroConfig]) -> None:
    path = Path(path)
    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    text = dump_macros(macros)
    load_macros_text(text)  # never write a file we couldn't read back
    path.write_text(text, encoding="utf-8")


def load_macros_text(text: str) -> list[MacroConfig]:
    data = yaml.safe_load(text) or {}
    return [MacroConfig.model_validate(m) for m in (data.get("macros") or [])]


def remove_macros_section(team_text: str) -> tuple[str, bool]:
    """team.yaml text without its top-level `macros:` block (comments elsewhere untouched), plus whether there
    was one. The block runs from the `macros:` line to the next top-level key."""
    lines = team_text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.startswith("macros:")), None)
    if start is None:
        return team_text, False
    end = start + 1
    while end < len(lines):
        l = lines[end]
        if l.strip() and not l[0].isspace() and not l.startswith("#") and not l.startswith("-"):
            break  # the next top-level key
        end += 1
    # leave comments directly above the next key with that key
    while end - 1 > start and lines[end - 1].startswith("#"):
        end -= 1
    pointer = "# macros: moved to macros.yaml (edited from the Macros tab)\n\n"
    return "".join(lines[:start]) + pointer + "".join(lines[end:]), True


def migrate(team_path: str | Path, macros: list[MacroConfig]) -> Path:
    """Create macros.yaml from `macros` and take the `macros:` block out of team.yaml (a .bak is kept)."""
    team_path = Path(team_path)
    target = macros_path(team_path)
    save_macros(target, macros)
    text = team_path.read_text(encoding="utf-8")
    new, found = remove_macros_section(text)
    if found:
        team_path.with_suffix(team_path.suffix + ".bak").write_text(text, encoding="utf-8")
        team_path.write_text(new, encoding="utf-8")
        log.info("macros moved from %s to %s", team_path.name, target.name)
    return target
