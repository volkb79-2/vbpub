"""Tests for scripts/mdt-perms - the `mdt` group contract checker/fixer.

The shipped script pins the group `mdt`; the tests run a COPY with that one line
substituted, so the contract is exercised with the caller's own primary group
instead of requiring a real `mdt` group.
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


_COPIES: dict[tuple[str, str], Path] = {}


def _script_for(group: str, directory: Path) -> Path:
    """A COPY of the shipped script whose fixed `GROUP="mdt"` line names `group`.

    The shipped binary honours no environment override (an ambient variable must not
    change which group it enforces), so the tests substitute the line in a copy.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.count('\nGROUP="mdt"\n') == 1, "the shipped script must pin GROUP=\"mdt\" exactly once"
    copy = directory / f"mdt-perms-{group}"
    copy.write_text(text.replace('\nGROUP="mdt"\n', f'\nGROUP="{group}"\n'), encoding="utf-8")
    copy.chmod(0o755)
    return copy


def _run(action: str, *paths: Path, group: str = GROUP) -> subprocess.CompletedProcess:
    script = _script_for(group, paths[0].parent)
    return subprocess.run(
        [str(script), action, *map(str, paths)],
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


def test_a_wrong_group_is_a_violation(tmp_path: Path) -> None:
    """Needs no second group membership: the tree is owned by the caller's group and the
    contract is checked against a DIFFERENT group name (`root` exists everywhere)."""
    root = tmp_path / "mdt"
    _compliant_tree(root)
    assert _run("check", root).returncode == 0, "control: the same tree passes for its own group"

    check = _run("check", root, group="root")

    assert check.returncode == 1
    assert "group" in check.stderr and "tool" in check.stderr


def test_fix_assigns_the_group_to_an_offender(tmp_path: Path) -> None:
    other = _other_group()
    if other is None:
        pytest.skip("chgrp to a second group needs the user to belong to one (the image build runs as root)")
    root = tmp_path / "mdt"
    _compliant_tree(root)
    os.chown(root / "bin" / "tool", -1, grp.getgrnam(other).gr_gid)

    assert _run("check", root).returncode == 1

    assert _run("fix", root).returncode == 0
    assert (root / "bin" / "tool").stat().st_gid == os.getgid()


def test_a_missing_path_is_an_error_not_a_pass(tmp_path: Path) -> None:
    result = _run("check", tmp_path / "does-not-exist")
    assert result.returncode != 0
    assert "no such path" in result.stderr


def test_an_unknown_action_is_a_usage_error(tmp_path: Path) -> None:
    assert _run("chown", tmp_path).returncode == 2


def test_a_group_readable_requirement_a_0600_file_is_a_violation_and_fix_makes_it_rw(tmp_path: Path) -> None:
    """PWMCP's get.py creates its lock 0600: owner-only is unreadable to a remapped uid."""
    root = tmp_path / "mdt"
    _compliant_tree(root)
    lock = root / "bin" / ".lock"
    lock.write_text("", encoding="utf-8")
    lock.chmod(0o620)  # what a g+w-only fix turns a 0600 file into: writable, still unreadable to the group

    check = _run("check", root)
    assert check.returncode == 1 and "file-mode" in check.stderr and ".lock" in check.stderr

    assert _run("fix", root).returncode == 0
    assert stat.S_IMODE(lock.stat().st_mode) == 0o660

    lock.chmod(0o600)
    assert _run("fix", root).returncode == 0
    assert stat.S_IMODE(lock.stat().st_mode) == 0o660


def test_the_shipped_script_ignores_the_environment_group_override(tmp_path: Path) -> None:
    root = tmp_path / "mdt"
    _compliant_tree(root)
    result = subprocess.run(
        [str(SCRIPT), "check", str(root)],
        env={**os.environ, "MDT_PERMS_GROUP": GROUP},
        capture_output=True, text=True, check=False,
    )
    # the tree is group GROUP; the shipped script enforces `mdt` whatever the variable says
    assert result.returncode != 0


def test_a_group_that_does_not_exist_is_an_error_not_a_pass(tmp_path: Path) -> None:
    """`find -group nosuch` fails; swallowed inside a command substitution that read as
    'no violations' (the shipped script, run where no `mdt` group exists, passed)."""
    root = tmp_path / "mdt"
    _compliant_tree(root)

    result = subprocess.run([str(SCRIPT), "check", str(root)], capture_output=True, text=True, check=False)

    assert result.returncode == 2
    assert "does not exist" in result.stderr
