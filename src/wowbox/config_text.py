"""Save config without wrecking team.yaml.

team.yaml is hand-written, with comments. The UI changes only a few things in it: the route table, the leader
and the slots' `character:` names. `update_team_yaml` edits just those, in the text, and leaves every other
line (comments included) exactly as it was. Nothing is written when nothing changed.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import yaml

from .config import AppConfig, RouteConfig

log = logging.getLogger(__name__)


def _block(lines: list[str], key: str) -> tuple[int, int] | None:
    """(start, end) line indexes of top-level `key:` and its indented body; comments directly above the next
    top-level key stay with that key."""
    start = next((i for i, l in enumerate(lines) if re.match(rf"{re.escape(key)}:(\s|$)", l)), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines):
        l = lines[end]
        if l.strip() and not l[0].isspace() and not l.startswith("#") and not l.startswith("-"):
            break
        end += 1
    while end - 1 > start and (lines[end - 1].startswith("#") or not lines[end - 1].strip()):
        end -= 1
    return start, end


def _scalar(v) -> str:
    if isinstance(v, str) and re.fullmatch(r"[A-Za-z_][\w:.-]*", v) and v.lower() not in ("yes", "no", "on", "off",
                                                                                       "true", "false", "null"):
        return v
    return json.dumps(v, ensure_ascii=False)


def _text(v: str) -> str:
    """A string as YAML: plain when that reads back the same, quoted otherwise."""
    try:
        if v == v.strip() and yaml.safe_load(f"k: {v}") == {"k": v}:
            return v
    except yaml.YAMLError:
        pass
    return json.dumps(v, ensure_ascii=False)


def routes_text(routes: list[RouteConfig]) -> list[str]:
    out = ["routes:\n"]
    for r in routes:
        to = "[" + ", ".join(str(s) for s in r.to) + "]" if isinstance(r.to, list) else _scalar(r.to)
        out.append(f"  - keys: [{', '.join(json.dumps(k, ensure_ascii=False) for k in r.keys)}]\n")
        out.append(f"    to: {to}\n")
        if r.note:
            out.append(f"    note: {_text(r.note)}\n")
    if not routes:
        out = ["routes: []\n"]
    return out


def _set_leader(lines: list[str], leader: int) -> bool:
    span = _block(lines, "team")
    if span is None:
        return False
    for i in range(span[0] + 1, span[1]):
        if m := re.match(r"^(\s+leader:\s*)\d+(.*)$", lines[i].rstrip("\n")):
            lines[i] = f"{m.group(1)}{leader}{m.group(2)}\n"
            return True
    lines.insert(span[0] + 1, f"  leader: {leader}\n")
    return True


def _set_character(lines: list[str], slot: int, name: str) -> bool:
    """Set `character:` on a slot written in flow style: `    2: { label: "Lock", role: dps }   # comment`."""
    span = _block(lines, "team")
    if span is None:
        return False
    for i in range(span[0] + 1, span[1]):
        m = re.match(rf"^(\s+{slot}:\s*)\{{(.*)\}}(.*)$", lines[i].rstrip("\n"))
        if not m:
            continue
        inner = m.group(2)
        if re.search(r"\bcharacter:", inner):
            inner = re.sub(r"(\bcharacter:\s*)(\"[^\"]*\"|'[^']*'|[^,}]+?)(\s*(,|$))",
                           lambda mm: f"{mm.group(1)}{_scalar(name)}{mm.group(3)}", inner, count=1)
        else:
            inner = inner.rstrip() + f", character: {_scalar(name)} "
        lines[i] = f"{m.group(1)}{{{inner}}}{m.group(3)}\n"
        return True
    return False


def update_team_yaml(path: str | Path, cfg: AppConfig) -> list[str]:
    """Write the UI-editable parts of `cfg` into team.yaml in place. Returns what changed (empty = nothing)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    on_disk = yaml.safe_load(text) or {}
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    changed: list[str] = []
    problems: list[str] = []

    disk_routes = [RouteConfig.model_validate(r).model_dump() for r in on_disk.get("routes") or []]
    if disk_routes != [r.model_dump() for r in cfg.routes]:
        span = _block(lines, "routes")
        new = routes_text(cfg.routes)
        if span:
            lines[span[0]:span[1]] = new
        else:
            lines += ["\n"] + new
        changed.append("routes")

    if (on_disk.get("team") or {}).get("leader") != cfg.team.leader:
        (changed if _set_leader(lines, cfg.team.leader) else problems).append("leader")

    disk_slots = (on_disk.get("team") or {}).get("slots") or {}
    for slot, sc in cfg.team.slots.items():
        if sc.character and (disk_slots.get(slot) or {}).get("character") != sc.character:
            (changed if _set_character(lines, slot, sc.character) else problems).append(f"slot {slot} character")

    if problems:
        log.warning("couldn't update %s in %s in place (unusual layout); edit it by hand", ", ".join(problems),
                    path.name)
    if not changed:
        return []
    new_text = "".join(lines)
    AppConfig.model_validate(yaml.safe_load(new_text) or {})  # never write a file that doesn't load
    path.with_suffix(path.suffix + ".bak").write_text(text, encoding="utf-8")
    path.write_text(new_text, encoding="utf-8")
    return changed
