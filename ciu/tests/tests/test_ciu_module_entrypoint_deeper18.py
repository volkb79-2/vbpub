"""Subprocess contract for the ``python -m ciu`` entrypoint."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import conftest as suite_conftest  # noqa: E402


def test_module_entrypoint_delegates_to_cli_help() -> None:
    """``python -m ciu --help`` is a usable CLI, not merely an importable module."""
    project_root = Path(__file__).resolve().parents[2]
    environment = suite_conftest.python_child_env()

    result = subprocess.run(
        [sys.executable, "-m", "ciu", "--help"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "CIU " in result.stdout
    assert "EXPLORATION" in result.stdout and "MODIFICATION" in result.stdout
    assert result.stderr == ""
