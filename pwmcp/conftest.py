"""Project-wide test isolation checks for the PWMCP suite."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parent


def _git_status_porcelain() -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
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
