"""Project-wide test isolation checks for the PWMCP suite."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parent
_GIT_EXECUTABLE_ENV = "PWMCP_TEST_GIT_EXECUTABLE"


def _git_status_porcelain() -> str:
    git_executable = os.environ.get(_GIT_EXECUTABLE_ENV) or shutil.which("git")
    if git_executable is None:
        raise RuntimeError(
            f"the PWMCP Git-status guard requires git on PATH or {_GIT_EXECUTABLE_ENV}"
        )
    result = subprocess.run(
        [git_executable, "status", "--porcelain", "--untracked-files=all"],
        cwd=_REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.fixture(scope="session", autouse=True)
def git_tree_unchanged() -> Iterator[None]:
    """Fail if the test session leaves Git-visible files in the repository."""
    before = _git_status_porcelain()
    yield
    after = _git_status_porcelain()
    assert after == before, (
        "PWMCP tests changed Git-visible repository state during the session.\n"
        f"Before:\n{before or '<clean>'}\n"
        f"After:\n{after or '<clean>'}"
    )
