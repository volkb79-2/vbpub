"""Guard for the MM-MOVE path edits (the stack lives at <vbpub>/mattermost).

The plain-compose fallback binds ABSOLUTE host paths and the ciu defaults name
the provisioning hook by a stack-relative path; a revert to the old
`nyxloom/mattermost` location changes neither a test import nor a unit result,
so these oracles pin them directly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1]
OLD = "nyxloom/mattermost"
# Files that must never name the old location (explanatory comments live elsewhere).
STACK_FILES = [
    "docker-compose.yml", "ciu.compose.yml.j2", "ciu.defaults.toml.j2",
    "hooks/post_compose_provision.py", "tools/mm_reachability.py",
]


def _text(relative: str) -> str:
    return (STACK / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative", STACK_FILES)
def test_stack_files_do_not_name_the_old_location(relative):
    assert OLD not in _text(relative), relative


def test_compose_absolute_paths_stay_inside_the_mattermost_stack_dir():
    """Every absolute host path under a `vbpub` checkout (binds, secret `file:`,
    comment recipes) must be `<...>/vbpub/mattermost/...`, never a sibling dir."""
    found = re.findall(r"/[\w./-]*?/vbpub/([\w.-]+)", _text("docker-compose.yml"))
    assert found, "expected absolute vbpub paths in the pre-rendered fallback"
    assert set(found) == {"mattermost"}, found


def test_compose_bind_mounts_are_the_three_stack_hostdirs():
    binds = re.findall(r"^\s+- (/\S+?/vbpub/\S+?):/", _text("docker-compose.yml"), re.MULTILINE)
    assert sorted(Path(b).name for b in binds) == [
        "vol-mattermost-config", "vol-mattermost-logs", "vol-postgres-data"]
    assert all(Path(b).parent.name == "mattermost" for b in binds), binds


def test_post_compose_hook_path_resolves_inside_the_stack_dir():
    hooks = re.findall(r'^post_compose\s*=\s*\[(.*?)\]', _text("ciu.defaults.toml.j2"), re.MULTILINE)
    assert hooks, "post_compose declaration not found"
    paths = re.findall(r'"([^"]+)"', hooks[0])
    assert paths
    for rel in paths:
        resolved = (STACK / rel).resolve()
        assert STACK in resolved.parents, rel
        assert resolved.is_file(), rel
