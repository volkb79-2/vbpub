"""Regression tests for the vendored devcontainer persistence contract."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "devcontainer.json"
TEMPLATE_TEXT = TEMPLATE.read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "templates"))
import initialize_container_environment as bootstrap  # noqa: E402


MOUNT_RE = re.compile(
    r'^\s*"(?P<spec>source=(?P<source>[^,"]+),target=(?P<target>[^,"]+),'
    r'type=(?P<type>[^,"]+)(?:,[^"]*)?)",?\s*$'
)
PERSISTENCE_PREFIX = "${localEnv:HOME}/mdt--mounted-folders/"


def _template_mounts() -> list[tuple[str, str, str]]:
    mounts = []
    for line in TEMPLATE_TEXT.splitlines():
        match = MOUNT_RE.match(line)
        if match:
            mounts.append((match["spec"], match["source"], match["target"]))
    return mounts


def test_template_contains_source_backed_pi_claudelink_and_opencode_mounts() -> None:
    mounts = {source: target for _, source, target in _template_mounts()}
    expected = {
        f"{PERSISTENCE_PREFIX}.pi": "/home/vscode/.pi",
        f"{PERSISTENCE_PREFIX}.claudelink": "/home/vscode/.claudelink",
        f"{PERSISTENCE_PREFIX}opencode-data": "/home/vscode/.local/share/opencode",
    }

    assert {source: mounts[source] for source in expected} == expected
    for source, target in expected.items():
        spec = f"source={source},target={target},type=bind,consistency=cached"
        assert f'"{spec}"' in TEMPLATE_TEXT


def test_persistent_directory_mounts_remain_alphabetically_ordered() -> None:
    names = [
        source.removeprefix(PERSISTENCE_PREFIX)
        for _, source, _ in _template_mounts()
        if source.startswith(PERSISTENCE_PREFIX)
        and not source.endswith((".claude.json", ".reasonix.toml"))
        and source.removeprefix(PERSISTENCE_PREFIX) != "tmp"
    ]

    assert names == sorted(names)
    assert names[-1] == "opencode-data"


def test_bootstrap_derives_every_active_home_bind_source() -> None:
    host_home = Path("/host/home")
    original_home = bootstrap.HOME
    bootstrap.HOME = host_home
    try:
        derived = [
            path
            for source in bootstrap.host_bind_sources(TEMPLATE)
            if (path := bootstrap.to_home_dir(source)) is not None
        ]
    finally:
        bootstrap.HOME = original_home

    grouped = host_home / bootstrap.PARENT_NAME
    expected = [
        host_home / ".ssh",
        grouped / ".claude",
        grouped / ".claudelink",
        grouped / ".codex",
        grouped / ".config",
        grouped / ".gnupg",
        grouped / ".local",
        grouped / ".minisign",
        grouped / ".openclaw",
        grouped / ".pi",
        grouped / ".reasonix",
        grouped / ".ssh",
        grouped / "opencode-data",
        grouped / ".claude.json",
        grouped / "tmp",
    ]
    assert derived == expected


def test_bootstrap_main_uses_complete_fallback_when_template_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host_home = tmp_path / "host"
    monkeypatch.setattr(bootstrap, "HOME", host_home)
    monkeypatch.setattr(bootstrap, "host_bind_sources", lambda _path: [])
    ensured: list[Path] = []
    monkeypatch.setattr(bootstrap, "ensure", ensured.append)

    assert bootstrap.main() == 0
    assert ensured == bootstrap.fallback_bind_paths()
    assert host_home / bootstrap.PARENT_NAME / ".pi" in ensured
    assert host_home / bootstrap.PARENT_NAME / ".claudelink" in ensured
    assert host_home / bootstrap.PARENT_NAME / ".local" in ensured
    assert host_home / bootstrap.PARENT_NAME / "opencode-data" in ensured


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "templates" / "README.md",
        ROOT / "DEVCONTAINER-LIFECYCLE.md",
        ROOT / "docs" / "CONSUMERS.md",
        ROOT / "USAGE.md",
        ROOT / "README.md",
    ],
)
def test_user_facing_docs_match_persistence_targets(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for target in (
        "/home/vscode/.pi",
        "/home/vscode/.claudelink",
        "/home/vscode/.local/share/opencode",
    ):
        assert target in text, path
    assert "opencode-data" in text, path
    assert ".pi/agent/sessions" in text, path
    assert ".claudelink" in text, path


def test_persistence_docs_expose_migration_recipe_and_no_stale_opencode_path() -> None:
    template_readme = (ROOT / "templates" / "README.md").read_text(encoding="utf-8")
    lifecycle = (ROOT / "DEVCONTAINER-LIFECYCLE.md").read_text(encoding="utf-8")
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    usage = (ROOT / "USAGE.md").read_text(encoding="utf-8")
    top_readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for text in (template_readme, lifecycle, consumers):
        assert "cp -a" in text
    for text in (template_readme, lifecycle, consumers, usage):
        assert ".claudelink" in text
        assert ".pi" in text
        assert ".local/share/opencode" in text
        assert "mdt--mounted-folders/opencode-data" in text
        assert "docker cp" in text
        assert "WAL" in text
    assert "`~/.opencode`" not in template_readme
    assert "`~/tmp`" not in template_readme
    assert "DEVCONTAINER-LIFECYCLE.md#migrating-a-running-devcontainer-before-adopting-the-mounts" in top_readme
    assert "templates/README.md#migrate-existing-pi-claudelink-and-opencode-state-once" in top_readme


def test_running_container_recipe_quiesces_before_copying_sqlite_state() -> None:
    lifecycle = (ROOT / "DEVCONTAINER-LIFECYCLE.md").read_text(encoding="utf-8")

    runbook_heading = "## Migrating a running devcontainer before adopting the mounts"
    stopped_check = 'test "$(docker inspect --format \'{{.State.Running}}\' "$container_name")" = "false"'
    claudelink_copy = 'docker cp "$container_name:/home/vscode/.claudelink/." "$host_state/.claudelink/"'
    assert runbook_heading in lifecycle
    assert "set -eu" in lifecycle
    assert 'docker stop --timeout 30 "$container_name"' in lifecycle
    assert 'docker exec "$container_name" test -d /home/vscode/.claudelink' in lifecycle
    assert stopped_check in lifecycle
    assert claudelink_copy in lifecycle
    assert lifecycle.index("docker stop --timeout 30") < lifecycle.index(stopped_check)
    assert lifecycle.index(stopped_check) < lifecycle.index(claudelink_copy)
    for sidecar in ("nexus.db", "nexus.db-wal", "nexus.db-shm"):
        assert sidecar in lifecycle
    assert "Never copy only" in lifecycle
