import difflib
import shutil
from pathlib import Path

import yaml

from wowbox.config import RouteConfig, load_config
from wowbox.config_text import update_team_yaml

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "team.example.yaml"


def setup(tmp_path):
    p = tmp_path / "team.yaml"
    shutil.copy(EXAMPLE, p)
    return p, p.read_text(encoding="utf-8"), load_config(p)


def changed_lines(before: str, after: str) -> list[str]:
    return [l for l in difflib.ndiff(before.splitlines(), after.splitlines()) if l[:1] in "+-"]


def test_nothing_changed_writes_nothing(tmp_path):
    p, text, cfg = setup(tmp_path)
    assert update_team_yaml(p, cfg) == []
    assert p.read_text(encoding="utf-8") == text and not (tmp_path / "team.yaml.bak").exists()


def test_route_edit_keeps_every_comment(tmp_path):
    p, text, cfg = setup(tmp_path)
    cfg.routes[0] = RouteConfig(keys=["1-9", "0", "-", "="], to="active", note="stay in your window")
    cfg.routes.append(RouteConfig(keys=["F6"], to=[2, 3], note=""))
    assert update_team_yaml(p, cfg) == ["routes"]
    after = p.read_text(encoding="utf-8")
    assert [l for l in text.splitlines() if l.lstrip().startswith("#")] == \
        [l for l in after.splitlines() if l.lstrip().startswith("#")]  # all comment lines, in order
    diff = changed_lines(text, after)
    assert all("keys:" in l or "to:" in l or "note:" in l or "routes" in l or not l[2:].strip() for l in diff), diff
    back = load_config(p)
    assert back.routes[0].to == "active" and back.routes[-1].to == [2, 3]
    assert back.model_dump(exclude={"routes"}) == load_config(tmp_path / "team.yaml.bak").model_dump(
        exclude={"routes"})


def test_leader_and_characters_in_place(tmp_path):
    p, text, cfg = setup(tmp_path)
    cfg.team.leader = 2
    cfg.team.slots[1].character = "Tinkerdawn"
    cfg.team.slots[3].character = "Kunth"
    assert sorted(update_team_yaml(p, cfg)) == ["leader", "slot 1 character", "slot 3 character"]
    after = p.read_text(encoding="utf-8")
    diff = changed_lines(text, after)
    assert len(diff) == 6, diff  # three lines changed, nothing else
    back = load_config(p)
    assert back.team.leader == 2
    assert back.team.slots[1].character == "Tinkerdawn" and back.team.slots[3].character == "Kunth"
    assert back.team.slots[1].label == load_config(tmp_path / "team.yaml.bak").team.slots[1].label
    # change a character that's already set
    cfg.team.slots[1].character = "Someoneelse"
    update_team_yaml(p, cfg)
    assert load_config(p).team.slots[1].character == "Someoneelse"


def test_saving_twice_keeps_the_commented_backup(tmp_path):
    """Seen live: Save config clicked twice replaced team.yaml.bak with the stripped file, losing the comments."""
    p, text, cfg = setup(tmp_path)
    cfg.team.leader = 3
    update_team_yaml(p, cfg)
    update_team_yaml(p, cfg)  # nothing new: no write, the .bak stays the original
    assert (tmp_path / "team.yaml.bak").read_text(encoding="utf-8") == text
    assert "#" in p.read_text(encoding="utf-8")


def test_empty_routes_and_quoting(tmp_path):
    p, _, cfg = setup(tmp_path)
    cfg.routes = [RouteConfig(keys=["Shift+2", "="], to="char:Gnope", note='say "hi": now')]
    update_team_yaml(p, cfg)
    assert load_config(p).routes[0].model_dump() == cfg.routes[0].model_dump()
    cfg.routes = []
    update_team_yaml(p, cfg)
    assert load_config(p).routes == [] and "automation" in yaml.safe_load(p.read_text(encoding="utf-8"))
