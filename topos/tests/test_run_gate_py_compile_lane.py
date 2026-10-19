"""The py-compile lane must not fail on Python files the branch deleted."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

LANE_FILE = Path(__file__).resolve().parents[1] / "run-gate.toml"


def _lane_script() -> str:
    lane = tomllib.loads(LANE_FILE.read_text(encoding="utf-8"))["lanes"]["py-compile"]
    return lane["argv"][2]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _run_lane(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", _lane_script().replace("{worktree}", str(repo))],
        capture_output=True, text=True, check=False,
    )


def _repo_with_a_deleted_python_file(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "gone.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "kept.py").write_text("y = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "gone.py").unlink()
    (repo / "kept.py").write_text("y = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "delete one, change one")
    return repo


def test_a_deleted_python_file_does_not_fail_the_lane(tmp_path):
    result = _run_lane(_repo_with_a_deleted_python_file(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "py-compile: OK" in result.stdout


def test_a_changed_python_file_with_a_syntax_error_still_fails_the_lane(tmp_path):
    repo = _repo_with_a_deleted_python_file(tmp_path)
    (repo / "kept.py").write_text("def broken(:\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "break it")
    result = _run_lane(repo)
    assert result.returncode != 0
    assert "py-compile: FAILED" in result.stderr
