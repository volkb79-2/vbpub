"""Tests for ciu.paths and ciu.procutil (CIU v2 P1).

Spec references: S1.4 (to_physical_path mapping), S1.9 (native-host identity),
S7.3 (no sys.exit in helpers).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.paths import is_under, to_physical_path  # noqa: E402
from ciu.procutil import docker, run_cmd  # noqa: E402


# ---------------------------------------------------------------------------
# is_under
# ---------------------------------------------------------------------------


class TestIsUnder:
    def test_direct_child(self, tmp_path: Path) -> None:
        child = tmp_path / "a" / "b"
        assert is_under(child, tmp_path) is True

    def test_equals_root(self, tmp_path: Path) -> None:
        assert is_under(tmp_path, tmp_path) is True

    def test_sibling_not_under(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        other = tmp_path / "other"
        assert is_under(other, root) is False

    def test_parent_not_under_child(self, tmp_path: Path) -> None:
        child = tmp_path / "sub"
        assert is_under(tmp_path, child) is False


# ---------------------------------------------------------------------------
# to_physical_path — remap under repo root (S1.4)
# ---------------------------------------------------------------------------


class TestToPhysicalPathRemap:
    def test_remap_under_repo_root(self, tmp_path: Path) -> None:
        """A path inside repo_root is remapped to physical_root."""
        repo = tmp_path / "repo"
        physical = tmp_path / "host_repo"
        repo.mkdir()
        physical.mkdir()

        logical = repo / "infra" / "redis-core" / ".ciu" / "secrets" / "pw"
        result = to_physical_path(logical, repo_root=repo, physical_root=physical)

        assert result == physical / "infra" / "redis-core" / ".ciu" / "secrets" / "pw"

    def test_remap_string_path(self, tmp_path: Path) -> None:
        """Accepts a string as well as a Path."""
        repo = tmp_path / "repo"
        physical = tmp_path / "host"
        repo.mkdir()
        physical.mkdir()

        logical_str = str(repo / "stack" / "file.txt")
        result = to_physical_path(logical_str, repo_root=repo, physical_root=physical)

        assert result == physical / "stack" / "file.txt"

    def test_remap_preserves_nested_structure(self, tmp_path: Path) -> None:
        repo = tmp_path / "r"
        physical = tmp_path / "p"
        repo.mkdir()
        physical.mkdir()

        deep = repo / "a" / "b" / "c" / "d"
        result = to_physical_path(deep, repo_root=repo, physical_root=physical)
        assert result == physical / "a" / "b" / "c" / "d"


# ---------------------------------------------------------------------------
# to_physical_path — identity when physical == logical (S1.9)
# ---------------------------------------------------------------------------


class TestToPhysicalPathIdentity:
    def test_identity_when_physical_equals_logical(self, tmp_path: Path) -> None:
        """Native host: PHYSICAL_REPO_ROOT == REPO_ROOT → function is identity."""
        repo = tmp_path / "repo"
        repo.mkdir()

        path_in = repo / "some" / "file"
        result = to_physical_path(path_in, repo_root=repo, physical_root=repo)

        assert result == path_in

    def test_identity_same_resolved_path(self, tmp_path: Path) -> None:
        """Resolved identical roots produce identity even with trailing sep differences."""
        repo = tmp_path / "repo"
        repo.mkdir()

        result = to_physical_path(
            repo / "x", repo_root=repo, physical_root=repo
        )
        assert result == repo / "x"


# ---------------------------------------------------------------------------
# to_physical_path — external absolute paths pass through (S1.4)
# ---------------------------------------------------------------------------


class TestToPhysicalPathExternal:
    def test_external_absolute_passes_through(self, tmp_path: Path) -> None:
        """/etc/letsencrypt/… is outside repo_root → returned unchanged."""
        repo = tmp_path / "repo"
        physical = tmp_path / "host"
        repo.mkdir()
        physical.mkdir()

        external = Path("/etc/letsencrypt/live/example.com/fullchain.pem")
        result = to_physical_path(external, repo_root=repo, physical_root=physical)

        assert result == external

    def test_external_arbitrary_absolute(self, tmp_path: Path) -> None:
        """Any absolute path outside repo_root passes through."""
        repo = tmp_path / "repo"
        physical = tmp_path / "host"
        repo.mkdir()
        physical.mkdir()

        # Use a path guaranteed not under tmp_path/repo
        external = Path("/var/run/docker.sock")
        result = to_physical_path(external, repo_root=repo, physical_root=physical)
        assert result == external


# ---------------------------------------------------------------------------
# to_physical_path — missing env raises ValueError naming the var
# ---------------------------------------------------------------------------


class TestToPhysicalPathMissingEnv:
    def test_missing_repo_root_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPO_ROOT", raising=False)
        monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)

        with pytest.raises(ValueError, match="REPO_ROOT"):
            to_physical_path(Path("/some/path"))

    def test_missing_physical_repo_root_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REPO_ROOT", str(tmp_path / "repo"))
        monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)

        with pytest.raises(ValueError, match="PHYSICAL_REPO_ROOT"):
            to_physical_path(Path("/some/path"))

    def test_empty_repo_root_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPO_ROOT", "")
        monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)

        with pytest.raises(ValueError, match="REPO_ROOT"):
            to_physical_path(Path("/some/path"))


# ---------------------------------------------------------------------------
# to_physical_path — explicit args override env
# ---------------------------------------------------------------------------


class TestToPhysicalPathExplicitArgs:
    def test_explicit_args_override_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit repo_root / physical_root are used even when env has different values."""
        monkeypatch.setenv("REPO_ROOT", "/env/repo_root_should_be_ignored")
        monkeypatch.setenv(
            "PHYSICAL_REPO_ROOT", "/env/physical_root_should_be_ignored"
        )

        repo = tmp_path / "explicit_repo"
        physical = tmp_path / "explicit_physical"
        repo.mkdir()
        physical.mkdir()

        path_in = repo / "stack" / "file"
        result = to_physical_path(path_in, repo_root=repo, physical_root=physical)

        assert result == physical / "stack" / "file"

    def test_env_used_when_no_explicit_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "env_repo"
        physical = tmp_path / "env_physical"
        repo.mkdir()
        physical.mkdir()

        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(physical))

        result = to_physical_path(repo / "a" / "b")
        assert result == physical / "a" / "b"


# ---------------------------------------------------------------------------
# run_cmd — success
# ---------------------------------------------------------------------------


class TestRunCmdSuccess:
    def test_success_returns_completed_process(self) -> None:
        result = run_cmd(["true"])
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0

    def test_stdout_captured(self) -> None:
        result = run_cmd(["echo", "hello"])
        assert "hello" in result.stdout

    def test_check_true_on_success_no_raise(self) -> None:
        result = run_cmd(["true"], check=True)
        assert result.returncode == 0

    def test_capture_false_does_not_capture(self) -> None:
        result = run_cmd(["true"], capture=False)
        # stdout/stderr are None when capture=False
        assert result.stdout is None
        assert result.stderr is None


# ---------------------------------------------------------------------------
# run_cmd — check=True failure raises CalledProcessError with stderr in message
# ---------------------------------------------------------------------------


class TestRunCmdCheckFailure:
    def test_check_true_raises_on_failure(self) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            run_cmd(["false"], check=True)

    def test_error_message_contains_command(self) -> None:
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            run_cmd(["false"], check=True)
        msg = str(exc_info.value)
        assert "false" in msg

    def test_error_message_contains_returncode(self) -> None:
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            run_cmd(["false"], check=True)
        msg = str(exc_info.value)
        assert "1" in msg

    def test_error_message_contains_stderr(self) -> None:
        """Stderr tail must appear in the raised exception's message."""
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            run_cmd(
                ["sh", "-c", "echo my_error_marker >&2; exit 1"], check=True
            )
        msg = str(exc_info.value)
        assert "my_error_marker" in msg

    def test_check_false_does_not_raise(self) -> None:
        result = run_cmd(["false"], check=False)
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# run_cmd — never raises SystemExit (S7.3)
# ---------------------------------------------------------------------------


class TestRunCmdNoSystemExit:
    def test_no_system_exit_on_failure(self) -> None:
        """run_cmd must never call sys.exit regardless of outcome."""
        try:
            run_cmd(["false"], check=True)
        except SystemExit:
            pytest.fail("run_cmd raised SystemExit — violates S7.3")
        except subprocess.CalledProcessError:
            pass  # expected

    def test_no_system_exit_command_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            run_cmd(["__ciu_nonexistent_binary_xyz__"])
        # Must not have raised SystemExit.


# ---------------------------------------------------------------------------
# docker() — builds argv correctly (no real docker invoked)
# ---------------------------------------------------------------------------


class TestDockerWrapper:
    def test_docker_prepends_docker_to_argv(self) -> None:
        """docker() must call subprocess.run with ['docker', ...] as first arg."""
        captured: list = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            docker(["ps", "--all"])

        assert captured == [["docker", "ps", "--all"]]

    def test_docker_forwards_kwargs(self) -> None:
        """Keyword arguments like check= are forwarded to run_cmd."""
        calls: list = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            docker(["info"], timeout=5.0)

        assert len(calls) == 1
        _cmd, kwargs = calls[0]
        assert kwargs.get("timeout") == 5.0

    def test_docker_check_true_raises_on_nonzero(self) -> None:
        """docker(check=True) propagates CalledProcessError on failure."""

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error output")

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(subprocess.CalledProcessError):
                docker(["nonexistent"], check=True)
