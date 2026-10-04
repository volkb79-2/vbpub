"""Secret overlay copies stay aligned with the selected source snapshot."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cmru import transaction


def test_central_secret_stays_at_its_owner_while_snapshot_project_secret_is_copied(
    tmp_path,
):
    central_root = tmp_path / "central"
    family_root = tmp_path / "family"
    workspace_root = tmp_path / "candidate"
    (family_root / "project").mkdir(parents=True)
    workspace_root.mkdir()
    (central_root).mkdir()
    (central_root / "cmru.secret.toml").write_text(
        "central credential\n", encoding="utf-8",
    )
    project_config = family_root / "project" / "cmru.toml"
    project_config.write_text("[project]\nid = 'demo'\n", encoding="utf-8")
    project_secret = family_root / "project" / "cmru.secret.toml"
    project_secret.write_text("project credential\n", encoding="utf-8")
    workspace = transaction.ReleaseWorkspace(
        family_root, workspace_root, "cmru/release/test", "a" * 40,
    )

    transaction.copy_secret_overlays(
        central_root, workspace, [project_config],
        candidate_config_paths=[Path("renamed/demo/cmru.toml")],
    )

    assert not (workspace_root / "cmru.secret.toml").exists()
    assert (workspace_root / "renamed/demo/cmru.secret.toml").read_text(
        encoding="utf-8",
    ) == "project credential\n"


def test_secret_overlay_skips_a_missing_project_secret_for_a_valid_snapshot_path(tmp_path):
    family_root = tmp_path / "family"
    workspace_root = tmp_path / "candidate"
    (family_root / "project").mkdir(parents=True)
    workspace_root.mkdir()
    project_config = family_root / "project" / "cmru.toml"
    project_config.write_text("[project]\nid = 'demo'\n", encoding="utf-8")
    workspace = transaction.ReleaseWorkspace(
        family_root, workspace_root, "cmru/release/test", "a" * 40,
    )

    transaction.copy_secret_overlays(
        family_root, workspace, [project_config],
        candidate_config_paths=[Path("project/cmru.toml")],
    )

    assert not (workspace_root / "project/cmru.secret.toml").exists()


def test_secret_overlay_snapshot_paths_must_match_source_config_count(tmp_path):
    root = tmp_path / "repo"
    workspace_root = tmp_path / "candidate"
    root.mkdir()
    workspace_root.mkdir()
    workspace = transaction.ReleaseWorkspace(root, workspace_root, "cmru/release/test", "a" * 40)

    with pytest.raises(RuntimeError, match="must match the source project config paths"):
        transaction.copy_secret_overlays(
            root, workspace, [root / "demo" / "cmru.toml"], candidate_config_paths=[],
        )


def test_secret_overlay_closes_its_source_when_candidate_root_cannot_be_opened(
    monkeypatch, tmp_path,
):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "cmru.secret.toml"
    source.write_text("private\n", encoding="utf-8")
    workspace = transaction.ReleaseWorkspace(
        root, tmp_path / "missing-candidate", "cmru/release/test", "a" * 40,
    )
    opened_source_fds = []
    real_open = os.open

    def record_source_open(path, *args, **kwargs):
        descriptor = real_open(path, *args, **kwargs)
        if Path(path) == source:
            opened_source_fds.append(descriptor)
        return descriptor

    monkeypatch.setattr(transaction.os, "open", record_source_open)

    with pytest.raises(FileNotFoundError):
        transaction.copy_secret_overlays(root, workspace, [])

    assert len(opened_source_fds) == 1
    with pytest.raises(OSError):
        os.fstat(opened_source_fds[0])
    assert source.read_text(encoding="utf-8") == "private\n"


@pytest.mark.parametrize(
    "candidate_path",
    [Path("/outside/cmru.toml"), Path("../demo/cmru.toml"), Path("demo/project.toml")],
)
def test_secret_overlay_rejects_unsafe_snapshot_config_paths(tmp_path, candidate_path):
    root = tmp_path / "repo"
    workspace_root = tmp_path / "candidate"
    project_root = root / "demo"
    project_root.mkdir(parents=True)
    workspace_root.mkdir()
    project_config = project_root / "cmru.toml"
    project_config.write_text("[project]\nid = 'demo'\n", encoding="utf-8")
    (project_root / "cmru.secret.toml").write_text("secret\n", encoding="utf-8")
    workspace = transaction.ReleaseWorkspace(root, workspace_root, "cmru/release/test", "a" * 40)

    with pytest.raises(RuntimeError, match="candidate project config path is unsafe"):
        transaction.copy_secret_overlays(
            root, workspace, [project_config], candidate_config_paths=[candidate_path],
        )
