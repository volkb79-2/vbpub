"""Focused regression coverage for workspace environment parsing."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.workspace_env import parse_workspace_env


def test_parse_ignores_comment_only_export_line(tmp_path: Path) -> None:
    env_path = tmp_path / "ciu.env"
    env_path.write_text(
        "BEFORE=first\nexport # explanatory comment\nAFTER=last\n",
        encoding="utf-8",
    )

    assert parse_workspace_env(env_path) == {"BEFORE": "first", "AFTER": "last"}
