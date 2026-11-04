"""Oracle for the repository-cleanliness guard in ``pwmcp/conftest.py`` (PWMCP-12..16).

The guard is exercised for real: a throwaway Git repository holds a copy of the conftest and a tiny
test module, and a child pytest runs there. Nothing about git or pytest is mocked.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _testdeps import optional_module, require_binary

CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"

_spec = importlib.util.spec_from_file_location("pwmcp_root_conftest", CONFTEST)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    require_binary("git", "git hosts the repository the guard inspects")
    root = tmp_path / "repo"
    (root / "pwmcp").mkdir(parents=True)
    _git(root, "init", "-q")
    shutil.copy(CONFTEST, root / "pwmcp" / "conftest.py")
    (root / "pwmcp" / ".gitignore").write_text("ignored.out\n__pycache__/\n", encoding="utf-8")
    return root


def _run(root: Path, body: str, *extra: str, env_extra: dict[str, str] | None = None, cwd: Path | None = None):
    project = root / "pwmcp"
    (project / "test_child.py").write_text(body, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in {"PYTEST_ADDOPTS", "PWMCP_ALLOW_NO_GIT_GUARD"}}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", os.devnull,
         "--rootdir", str(project), str(project / "test_child.py"), *extra],
        cwd=cwd or project, env=env, capture_output=True, text=True, timeout=120, check=False,
    )


CLEAN = "def test_ok():\n    assert True\n"


def test_clean_session_passes(repo: Path) -> None:
    result = _run(repo, CLEAN)
    assert result.returncode == 0, result.stdout + result.stderr


def test_new_file_under_pwmcp_fails_with_bounded_report(repo: Path) -> None:
    body = (
        "from pathlib import Path\n"
        "def test_leak():\n"
        "    for i in range(30):\n"
        "        Path(__file__).with_name(f'leak{i:02d}.txt').write_text('x')\n"
    )
    result = _run(repo, body)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Added/changed (30)" in result.stderr
    assert "leak00.txt" in result.stderr
    assert "... and 10 more" in result.stderr
    assert "leak29.txt" not in result.stderr


def test_write_outside_pwmcp_is_not_blamed(repo: Path) -> None:
    body = (
        "from pathlib import Path\n"
        "def test_elsewhere():\n"
        "    Path(__file__).parents[1].joinpath('other.txt').write_text('x')\n"
    )
    result = _run(repo, body)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ignored_paths_are_out_of_scope(repo: Path) -> None:
    body = (
        "from pathlib import Path\n"
        "def test_ignored():\n"
        "    Path(__file__).with_name('ignored.out').write_text('x')\n"
    )
    result = _run(repo, body)
    assert result.returncode == 0, result.stdout + result.stderr


def test_not_a_worktree_gets_one_actionable_message(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    (root / "pwmcp").mkdir(parents=True)
    shutil.copy(CONFTEST, root / "pwmcp" / "conftest.py")
    env = {"GIT_CEILING_DIRECTORIES": str(tmp_path)}
    result = _run(root, CLEAN, env_extra=env)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "not inside a Git worktree" in result.stderr
    assert "PWMCP_ALLOW_NO_GIT_GUARD=1" in result.stderr
    opted_out = _run(root, CLEAN, env_extra={**env, "PWMCP_ALLOW_NO_GIT_GUARD": "1"})
    assert opted_out.returncode == 0, opted_out.stdout + opted_out.stderr
    assert "guard disabled" in opted_out.stderr


def test_missing_git_names_the_override(repo: Path) -> None:
    result = _run(repo, CLEAN, env_extra={"PWMCP_TEST_GIT_EXECUTABLE": str(repo / "no-such-git")})
    assert result.returncode == 4, result.stdout + result.stderr
    assert "cannot be run" in result.stderr


def test_xdist_workers_do_not_trip_the_guard(repo: Path) -> None:
    optional_module("xdist", "pytest-xdist drives the worker-scope oracle")
    body = (
        "import pytest\nfrom pathlib import Path\n"
        "@pytest.mark.parametrize('n', range(6))\n"
        "def test_transient(n):\n"
        "    p = Path(__file__).with_name(f'transient{n}.tmp')\n"
        "    p.write_text('x')\n"
        "    p.unlink()\n"
    )
    result = _run(repo, body, "-n", "2")
    assert result.returncode == 0, result.stdout + result.stderr


def test_snapshot_timeout_is_reported(repo: Path) -> None:
    slow = repo / "slow-git"
    slow.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    slow.chmod(0o755)
    with pytest.raises(guard.GitGuardUnavailable, match="did not finish within 0.2s"):
        guard.snapshot(repo / "pwmcp", git_executable=str(slow), timeout=0.2)


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("fatal: detected dubious ownership in repository at '/x'", "safe.directory"),
        ("fatal: not a git repository (or any parent)", "not inside a Git worktree"),
        ("fatal: something else", "git status failed: fatal: something else"),
    ],
)
def test_failure_classification(stderr: str, expected: str) -> None:
    assert expected in guard.classify_git_failure(stderr)


def test_describe_change_reports_removed_entries() -> None:
    text = guard.describe_change(("?? gone.txt",), ())
    assert "No longer listed (1)" in text
    assert "gone.txt" in text
