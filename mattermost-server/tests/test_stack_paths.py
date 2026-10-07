"""Guard for the project-folder path (the stack lives at <vbpub>/mattermost-server).

The plain-compose fallback binds ABSOLUTE host paths and the ciu defaults name
the provisioning hook by a stack-relative path; a revert to the old
`nyxloom/mattermost` location (MM-MOVE) or to the pre-rename `vbpub/mattermost`
location (MM-RENAME) changes neither a test import nor a unit result, so these
oracles pin them directly.

The ciu STACK/SERVICE name stays `mattermost` (service keys, the secret-store
subpath `.ciu/secrets/mattermost/<name>`, `GEN_LOCAL:mattermost/<name>`,
container and hostname names); only the project FOLDER is `mattermost-server`.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1]
FOLDER = "mattermost-server"
OLD = "nyxloom/mattermost"
# `vbpub/mattermost` NOT followed by a name character or `-`: matches the old
# project path (`/home/vb/volkb79-2/vbpub/mattermost/...`, `<vbpub>/mattermost`)
# but never `vbpub/mattermost-server`, the stack name `mattermost/` or the
# secret-store subpath `.ciu/secrets/mattermost/<name>` (no `vbpub/` before it).
OLD_PROJECT_PATH = re.compile(r"vbpub>?/mattermost(?![\w-])")
# `{worktree}/mattermost` in the lane argv has the same shape.
OLD_LANE_PATH = re.compile(r"\{worktree\}/mattermost(?![\w-])")
# Files that must never name the old location (explanatory comments live elsewhere).
STACK_FILES = [
    "docker-compose.yml", "ciu.compose.yml.j2", "ciu.defaults.toml.j2",
    "hooks/post_compose_provision.py", "tools/mm_reachability.py",
]
# Files that may legitimately say `mattermost/` (stack name) but not the old project path.
PROJECT_FILES = STACK_FILES + [
    "ciu.global.defaults.toml.j2", "README.md", "CONSUMER.md", "run-gate.toml",
]


def _text(relative: str) -> str:
    return (STACK / relative).read_text(encoding="utf-8")


def test_the_project_folder_is_named_mattermost_server():
    assert STACK.name == FOLDER, STACK


@pytest.mark.parametrize("relative", STACK_FILES)
def test_stack_files_do_not_name_the_old_location(relative):
    assert OLD not in _text(relative), relative


@pytest.mark.parametrize("relative", PROJECT_FILES)
def test_stack_files_do_not_name_the_pre_rename_project_path(relative):
    text = _text(relative)
    assert not OLD_PROJECT_PATH.findall(text), relative
    assert not OLD_LANE_PATH.findall(text), relative


def test_old_project_path_pattern_does_not_flag_the_stack_name_or_secret_store():
    ok = [
        "/home/vb/volkb79-2/vbpub/mattermost-server/vol-postgres-data",
        "<vbpub>/mattermost-server",
        ".ciu/secrets/mattermost/postgres_password",
        "GEN_LOCAL:mattermost/admin_password",
        "mattermost-server/.ciu/secrets/mattermost/operator_password",
        "image = mattermost/mattermost-team-edition",
        "{worktree}/mattermost-server && pytest",
    ]
    bad = [
        "/home/vb/volkb79-2/vbpub/mattermost/vol-postgres-data",
        "P=/home/vb/volkb79-2/vbpub/mattermost",
        "`<vbpub>/mattermost`",
        "cd {worktree}/mattermost && pytest",
    ]
    for line in ok:
        assert not OLD_PROJECT_PATH.search(line) and not OLD_LANE_PATH.search(line), line
    for line in bad:
        assert OLD_PROJECT_PATH.search(line) or OLD_LANE_PATH.search(line), line


def test_compose_absolute_paths_stay_inside_the_mattermost_server_dir():
    """Every absolute host path under a `vbpub` checkout (binds, secret `file:`,
    comment recipes) must be `<...>/vbpub/mattermost-server/...`, never a sibling dir."""
    found = re.findall(r"/[\w./-]*?/vbpub/([\w.-]+)", _text("docker-compose.yml"))
    assert found, "expected absolute vbpub paths in the pre-rendered fallback"
    assert set(found) == {FOLDER}, found


def test_compose_bind_mounts_are_the_three_stack_hostdirs():
    binds = re.findall(r"^\s+- (/\S+?/vbpub/\S+?):/", _text("docker-compose.yml"), re.MULTILINE)
    assert sorted(Path(b).name for b in binds) == [
        "vol-mattermost-config", "vol-mattermost-logs", "vol-postgres-data"]
    assert all(Path(b).parent.name == FOLDER for b in binds), binds


def test_post_compose_hook_path_resolves_inside_the_stack_dir():
    hooks = re.findall(r'^post_compose\s*=\s*\[(.*?)\]', _text("ciu.defaults.toml.j2"), re.MULTILINE)
    assert hooks, "post_compose declaration not found"
    paths = re.findall(r'"([^"]+)"', hooks[0])
    assert paths
    for rel in paths:
        resolved = (STACK / rel).resolve()
        assert STACK in resolved.parents, rel
        assert resolved.is_file(), rel
