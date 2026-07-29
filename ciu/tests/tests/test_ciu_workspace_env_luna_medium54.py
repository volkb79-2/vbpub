"""Luna-medium 54 mountinfo malformed-row contract."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.workspace_env import _parse_mountinfo  # noqa: E402


def test_malformed_mountinfo_rows_are_ignored() -> None:
    text = "\n".join(
        [
            "",
            "   ",
            "1 2 0:1 / - overlay overlay rw",
            "3 1 0:1 /physical/project /workspaces/project rw - ext4 /dev/x rw",
        ]
    )

    assert _parse_mountinfo(text) == [
        (Path("/workspaces/project"), Path("/physical/project"))
    ]
