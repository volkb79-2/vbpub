"""Tests for scripts/mdt-perms - the `mdt` group contract checker/fixer.

The group name is injectable (MDT_PERMS_GROUP), so the contract is exercised with
the caller's own primary group instead of requiring a real `mdt` group.
"""
from __future__ import annotations

import grp
import os
import stat
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parent / "mdt-perms"
GROUP = grp.getgrgid(os.getgid()).gr_name


def _run(action: str, *paths: Path, group: str = GROUP) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), action, *map(str, paths)],
        env={**os.environ, "MDT_PERMS_GROUP": group},
        capture_output=True, text=True, check=False,
    )


def _compliant_tree(root: Path) -> None:
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "tool").write_text("x", encoding="utf-8")
    (root / "link").symlink_to("bin/tool")
    for directory in (root, root / "bin"):
        directory.chmod(0o2775)
    (root / "bin" / "tool").chmod(0o775)


def _other_group() -> str | None:
    for gid in os.getgroups():
        if gid != os.getgid():
            return grp.getgrgid(gid).gr_name
    return None


def test_a_compliant_tree_passes_check(tmp_path: Path) -> None:
    root = tmp_path / "mdt"
    _compliant_tree(root)
    result = _run("check", root)
    assert result.returncode == 0, result.stderr


def test_a_file_without_group_write_is_a_violation_and_fix_repairs_only_it(tmp_path: Path) -> None:
    root = tmp_path / "mdt"
    _compliant_tree(root)
    good = root / "bin" / "tool"
    bad = root / "bin" / "data.json"
    bad.write_text("{}", encoding="utf-8")
    bad.chmod(0o644)
    before = good.stat().st_ctime_ns

    check = _run("check", root)
    assert check.returncode == 1 and "file-mode" in check.stderr and "data.json" in check.stderr

    fixed = _run("fix", root)
    assert fixed.returncode == 0, fixed.stderr
    assert stat.S_IMODE(bad.stat().st_mode) & 0o020
    # A compliant entry is not touched at all: on overlayfs even a no-op chmod
    # would copy it up into a new layer.
    assert good.stat().st_ctime_ns == before


def test_a_directory_without_setgid_or_group_rwx_is_a_violation(tmp_path: Path) -> None:
    root = tmp_path / "mdt"
    _compliant_tree(root)
    (root / "bin").chmod(0o755)

    check = _run("check", root)
    assert check.returncode == 1 and "dir-mode" in check.stderr

    assert _run("fix", root).returncode == 0
    assert stat.S_IMODE((root / "bin").stat().st_mode) == 0o2775


def test_symlinks_are_excluded_from_every_predicate(tmp_path: Path) -> None:
    root = tmp_path / "mdt"
    _compliant_tree(root)
    (root / "dangling").symlink_to("/nonexistent")
    assert _run("check", root).returncode == 0


def test_a_wrong_group_is_a_violation_and_fix_assigns_the_group(tmp_path: Path) -> None:
    other = _other_group()
    if other is None:
        pytest.skip("needs a second group the user belongs to")
    root = tmp_path / "mdt"
    _compliant_tree(root)
    os.chown(root / "bin" / "tool", -1, grp.getgrnam(other).gr_gid)

    check = _run("check", root)
    assert check.returncode == 1 and "group" in check.stderr and "tool" in check.stderr

    assert _run("fix", root).returncode == 0
    assert (root / "bin" / "tool").stat().st_gid == os.getgid()


def test_a_missing_path_is_an_error_not_a_pass(tmp_path: Path) -> None:
    result = _run("check", tmp_path / "does-not-exist")
    assert result.returncode != 0
    assert "no such path" in result.stderr


def test_an_unknown_action_is_a_usage_error(tmp_path: Path) -> None:
    assert _run("chown", tmp_path).returncode == 2
