"""Deterministic boundary contracts for the Layer-1 workspace environment."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.workspace_env import (
    ENV_FILE_NAME,
    WorkspaceEnvError,
    _parse_mountinfo,
    _physical_root_from_mountinfo,
    ensure_workspace_env,
    find_workspace_env,
    load_workspace_env,
    parse_workspace_env,
    resolve_env_root,
)


def _write_env(root: Path, contents: str) -> Path:
    path = root / ENV_FILE_NAME
    path.write_text(contents, encoding="utf-8")
    return path


class TestWorkspaceEnvParsingAndDiscovery:
    def test_parse_accepts_export_quotes_and_shell_comments(self, tmp_path: Path) -> None:
        env_path = _write_env(
            tmp_path,
            '# comment\nexport REPO_ROOT="/repo with spaces" # explanatory\nDOCKER_GID=0\n',
        )

        assert parse_workspace_env(env_path) == {
            "REPO_ROOT": "/repo with spaces",
            "DOCKER_GID": "0",
        }

    @pytest.mark.parametrize("entry", ["not-an-assignment", "A=one two", "=value"])
    def test_parse_rejects_non_shell_assignment_entries(self, tmp_path: Path, entry: str) -> None:
        with pytest.raises(WorkspaceEnvError, match="Invalid ciu.env entry"):
            parse_workspace_env(_write_env(tmp_path, entry + "\n"))

    def test_find_prefers_existing_repo_root_env_then_walks(self, tmp_path: Path, monkeypatch) -> None:
        explicit = tmp_path / "explicit"
        explicit.mkdir()
        expected = _write_env(explicit, "REPO_ROOT=/explicit\n")
        nested = tmp_path / "tree" / "deep"
        nested.mkdir(parents=True)
        _write_env(tmp_path / "tree", "REPO_ROOT=/walked\n")

        monkeypatch.setenv("REPO_ROOT", str(explicit))
        assert find_workspace_env(nested) == expected

        monkeypatch.setenv("REPO_ROOT", str(tmp_path / "missing"))
        assert find_workspace_env(nested) == tmp_path / "tree" / ENV_FILE_NAME

    def test_find_reports_actionable_error_when_absent(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("REPO_ROOT", raising=False)
        with pytest.raises(WorkspaceEnvError, match=r"ciu env generate"):
            find_workspace_env(tmp_path)

    def test_resolve_explicit_root_requires_existing_directory(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing"
        with pytest.raises(WorkspaceEnvError, match="Repository root does not exist"):
            resolve_env_root(tmp_path, missing, "marker")

    def test_resolve_walks_to_marker_or_keeps_start_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        nested = root / "one" / "two"
        nested.mkdir(parents=True)
        (root / "marker").write_text("", encoding="utf-8")
        assert resolve_env_root(nested, None, "marker") == root

        no_marker = tmp_path / "no-marker" / "child"
        no_marker.mkdir(parents=True)
        assert resolve_env_root(no_marker, None, "marker") == no_marker

    def test_load_does_not_clobber_unless_requested(self, tmp_path: Path, monkeypatch) -> None:
        _write_env(tmp_path, "EXISTING=file\nNEW=value\n")
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        monkeypatch.setenv("EXISTING", "process")

        assert load_workspace_env(tmp_path) == {"EXISTING": "file", "NEW": "value"}
        assert os.environ["EXISTING"] == "process"
        assert os.environ["NEW"] == "value"

        load_workspace_env(tmp_path, override=True)
        assert os.environ["EXISTING"] == "file"

    def test_required_keys_accept_zero_but_reject_empty_and_missing(self, monkeypatch) -> None:
        monkeypatch.setenv("ZERO", "0")
        ensure_workspace_env(["ZERO"])

        monkeypatch.setenv("EMPTY", "")
        with pytest.raises(WorkspaceEnvError, match="EMPTY, MISSING"):
            ensure_workspace_env(["EMPTY", "MISSING"])


class TestMountinfoEscapedPaths:
    def test_parse_mountinfo_decodes_proc_path_escapes_once(self) -> None:
        entries = _parse_mountinfo(
            "42 1 0:1 /host/a\\040b /workspaces/a\\040b rw - ext4 /dev/x rw\n"
        )
        assert entries == [(Path("/workspaces/a b"), Path("/host/a b"))]

    def test_escaped_bind_mount_participates_in_longest_prefix_mapping(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text(
            "42 1 0:1 /host/a\\040b /workspaces/a\\040b rw - ext4 /dev/x rw\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)

        assert _physical_root_from_mountinfo(Path("/workspaces/a b/nested")) == Path(
            "/host/a b/nested"
        )
