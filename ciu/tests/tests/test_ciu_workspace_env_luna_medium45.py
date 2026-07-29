"""Luna-medium 45 workspace discovery and mountinfo contracts."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.workspace_env import (  # noqa: E402
    _parse_mountinfo,
    _physical_root_from_mountinfo,
    detect_standalone_root,
)


def test_unreadable_standalone_default_is_skipped_and_ancestor_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ancestor = tmp_path / "workspace"
    nested = ancestor / "project" / "src"
    nested.mkdir(parents=True)
    defaults_name = "ciu.global.defaults.toml.j2"
    unreadable = nested.parent / defaults_name
    unreadable.write_text("standalone_root = true\n", encoding="utf-8")
    (ancestor / defaults_name).write_text(
        "standalone_root = true\n", encoding="utf-8"
    )

    original_read_text = Path.read_text

    def read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == unreadable:
            raise PermissionError("synthetic unreadable defaults")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    assert detect_standalone_root(nested) == ancestor


def test_malformed_short_and_root_mountinfo_rows_are_ignored(tmp_path: Path) -> None:
    text = "\n".join(
        [
            "malformed row",
            "1 2 0:1 / /too-short",
            "2 1 0:1 / / rw - overlay overlay rw",
            "3 1 0:1 /physical/project /workspaces/project rw - ext4 /dev/x rw",
        ]
    )

    entries = _parse_mountinfo(text)

    assert entries == [
        (Path("/workspaces/project"), Path("/physical/project"))
    ]


def test_root_mount_entry_cannot_win_physical_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "1 0 0:1 /host/rootfs / rw - overlay overlay rw\n", encoding="utf-8"
    )
    monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)

    assert _physical_root_from_mountinfo(Path("/workspaces/project")) is None
