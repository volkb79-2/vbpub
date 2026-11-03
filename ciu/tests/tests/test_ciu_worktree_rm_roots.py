"""CIU-138: `ciu worktree rm` must clean EVERY committed CIU root of the
checkout (each under its own identity) before deleting it, and preserve the
checkout and the lifecycle record when any root cannot be cleaned.

Real Git worktrees; only the `ciu clean` child (`_clean_in`) is a recording
stand-in, as in the neighbouring lifecycle tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree  # noqa: E402
from tests.test_ciu_worktree_lease import (  # noqa: E402,F401
    _git,
    fake_generate_env,
    tmp_repo,
)


def _commit_nested_roots(checkout: Path, *names: str) -> list[Path]:
    roots = []
    for name in names:
        root = checkout / name
        root.mkdir()
        (root / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
        roots.append(root.resolve())
    assert _git(["add", "-A"], checkout).returncode == 0
    assert _git(["commit", "-m", "nested roots"], checkout).returncode == 0
    return roots


def _setup(tmp_repo, *names):
    record = worktree.create(tmp_repo, "one", base="main")
    nested = _commit_nested_roots(record.git_worktree_path, *names)
    return record, nested


def test_rm_cleans_every_committed_root_before_deleting(
    tmp_repo, fake_generate_env, monkeypatch
):
    record, nested = _setup(tmp_repo, "nested-a", "nested-b")
    calls: list[Path] = []
    monkeypatch.setattr(
        worktree, "_clean_in", lambda root, *, yes: calls.append(root.resolve()) or 0
    )
    removed = worktree.remove(tmp_repo, "one")
    assert set(calls) == {record.ciu_root.resolve(), *nested}
    assert len(calls) == 3
    assert not removed.exists()


def test_a_sibling_root_failure_preserves_checkout_and_records(
    tmp_repo, fake_generate_env, monkeypatch
):
    record, nested = _setup(tmp_repo, "nested-a", "nested-b")
    before = record.record_path.read_text(encoding="utf-8")
    calls: list[Path] = []

    def clean(root, *, yes):
        calls.append(root.resolve())
        return 1 if root.name == "nested-b" else 0

    monkeypatch.setattr(worktree, "_clean_in", clean)
    with pytest.raises(worktree.WorktreeError) as exc:
        worktree.remove(tmp_repo, "one")
    message = str(exc.value)
    assert "nested-b" in message
    assert str(nested[0]) in message.split("Roots already cleaned:")[1]
    assert record.git_worktree_path.exists()
    assert record.record_path.read_text(encoding="utf-8") == before
    # The selected root is cleaned only after every sibling succeeded.
    assert record.ciu_root.resolve() not in calls


def test_an_unreadable_sibling_root_refuses_before_any_deletion(
    tmp_repo, fake_generate_env, monkeypatch
):
    record, _nested = _setup(tmp_repo, "nested-a")
    real_clean = worktree._clean_in
    cleaned: list[Path] = []

    def clean(root, *, yes):
        if root.name == "nested-a":
            return real_clean(root, yes=yes)  # no generated identity there
        cleaned.append(root)
        return 0

    monkeypatch.setattr(worktree, "_clean_in", clean)
    with pytest.raises(worktree.WorktreeError, match="nested-a"):
        worktree.remove(tmp_repo, "one")
    assert record.git_worktree_path.exists()
    assert cleaned == []


def test_a_discovery_failure_refuses_and_keeps_the_checkout(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "one", base="main")
    from ciu import workspace

    def boom(*_args, **_kwargs):
        raise workspace.CiuWorkspaceError("ls-tree broke")

    monkeypatch.setattr(workspace, "discover_committed_roots", boom)
    monkeypatch.setattr(worktree, "_clean_in", lambda *_a, **_k: 0)
    with pytest.raises(worktree.WorktreeError, match="CIU-138.*ls-tree broke"):
        worktree.remove(tmp_repo, "one")
    assert record.git_worktree_path.exists()


def test_a_single_root_checkout_cleans_only_its_own_root(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "one", base="main")
    calls: list[Path] = []
    monkeypatch.setattr(
        worktree, "_clean_in", lambda root, *, yes: calls.append(root.resolve()) or 0
    )
    worktree.remove(tmp_repo, "one")
    assert calls == [record.ciu_root.resolve()]


def test_force_continues_past_a_failed_sibling_clean(
    tmp_repo, fake_generate_env, monkeypatch
):
    record, _nested = _setup(tmp_repo, "nested-a")
    monkeypatch.setattr(
        worktree, "_clean_in",
        lambda root, *, yes: 1 if root.name == "nested-a" else 0,
    )
    removed = worktree.remove(tmp_repo, "one", force=True)
    assert not removed.exists()


def test_force_continues_past_an_unreadable_sibling_root(
    tmp_repo, fake_generate_env, monkeypatch
):
    _setup(tmp_repo, "nested-a")
    real_clean = worktree._clean_in
    monkeypatch.setattr(
        worktree, "_clean_in",
        lambda root, *, yes: real_clean(root, yes=yes) if root.name == "nested-a" else 0,
    )
    removed = worktree.remove(tmp_repo, "one", force=True)
    assert not removed.exists()


def test_a_committed_root_whose_marker_is_gone_refuses(
    tmp_repo, fake_generate_env, monkeypatch
):
    record, _nested = _setup(tmp_repo, "nested-a")
    real_marker = worktree._ciu_root_marker_present
    monkeypatch.setattr(
        worktree, "_ciu_root_marker_present",
        lambda root: False if Path(root).name == "nested-a" else real_marker(root),
    )
    monkeypatch.setattr(worktree, "_clean_in", lambda *_a, **_k: 0)
    with pytest.raises(worktree.WorktreeError, match="has no marker"):
        worktree.remove(tmp_repo, "one")
    assert record.git_worktree_path.exists()


def test_a_sibling_lease_release_failure_refuses(
    tmp_repo, fake_generate_env, monkeypatch
):
    record, _nested = _setup(tmp_repo, "nested-a")
    real_release = worktree.release_own_lease

    def release(root):
        if Path(root).name == "nested-a":
            raise RuntimeError("record unreadable")
        return real_release(root)

    monkeypatch.setattr(worktree, "_clean_in", lambda *_a, **_k: 0)
    monkeypatch.setattr(worktree, "release_own_lease", release)
    with pytest.raises(worktree.WorktreeError, match="sibling root.*record unreadable"):
        worktree.remove(tmp_repo, "one")
    assert record.git_worktree_path.exists()


def test_a_checkout_that_turns_dirty_is_refused_before_any_sibling_clean(
    tmp_repo, fake_generate_env, monkeypatch
):
    record, _nested = _setup(tmp_repo, "nested-a", "nested-b")
    calls: list[Path] = []
    monkeypatch.setattr(
        worktree, "_clean_in", lambda root, *, yes: calls.append(root.resolve()) or 0
    )
    original_plan = worktree._branch_removal_plan

    def dirty_after_inventory(*args, **kwargs):
        # Runs after the initial inventory and before the sibling pass.
        (record.git_worktree_path / "late-change.txt").write_text("x", encoding="utf-8")
        return original_plan(*args, **kwargs)

    monkeypatch.setattr(worktree, "_branch_removal_plan", dirty_after_inventory)
    with pytest.raises(worktree.WorktreeError, match="no sibling root was cleaned"):
        worktree.remove(tmp_repo, "one")
    assert calls == []
    assert record.git_worktree_path.exists()


def test_a_sibling_root_resolving_outside_the_checkout_is_refused(
    tmp_repo, fake_generate_env, tmp_path
):
    record, _nested = _setup(tmp_repo, "nested-a")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    swapped = record.git_worktree_path / "nested-a"
    swapped.rename(record.git_worktree_path / "nested-a.moved")
    swapped.symlink_to(outside)
    with pytest.raises(worktree.WorktreeError, match="resolves outside the checkout"):
        worktree._sibling_committed_roots(record.git_worktree_path, record.ciu_root)
