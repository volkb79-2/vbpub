"""Subprocess contract for the ``python -m ciu`` entrypoint."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_module_entrypoint_delegates_to_cli_help() -> None:
    """``python -m ciu --help`` is a usable CLI, not merely an importable module."""
    project_root = Path(__file__).resolve().parents[2]
    source_root = project_root / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root) + (
        os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else ""
    )

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
    assert "ENVIRONMENT" in result.stdout
    assert result.stderr == ""
