"""CIU-142: `ciu worktree refork` re-records `fork_point_sha` after a
legitimate rebase, so run-gate's derived base (R-35a: merge-base(trunk, HEAD)
must EQUAL the recorded fork point) works again without a hand-passed --base.

Real Git worktrees with a real `git rebase`; only CIU env generation is faked.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, worktree  # noqa: E402
from tests.test_ciu_worktree_lease import (  # noqa: E402,F401
    _git,
    fake_generate_env,
    tmp_repo,
)


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    assert _git(["add", name], repo).returncode == 0
    assert _git(["commit", "-m", name], repo).returncode == 0
    return _git(["rev-parse", "HEAD"], repo).stdout.strip()


def _rebased(tmp_repo):
    """A managed worktree with one own commit, rebased onto a newer main."""
    record = worktree.create(tmp_repo, "one", base="main")
    checkout = record.git_worktree_path
    own = _commit(checkout, "own.txt")
    new_main = _commit(tmp_repo, "later.txt")
    assert _git(["rebase", "main"], checkout).returncode == 0
    head = _git(["rev-parse", "HEAD"], checkout).stdout.strip()
    assert head != own
    return record, new_main, head


def _stored(record) -> dict:
    return json.loads(record.record_path.read_text(encoding="utf-8"))


def test_refork_makes_the_recorded_fork_point_the_merge_base(
    tmp_repo, fake_generate_env
):
    record, new_main, head = _rebased(tmp_repo)
    old = _stored(record)["fork_point_sha"]
    assert old != new_main

    stale = worktree.inspect_instance(tmp_repo, "one")["fork_point"]
    assert stale["status"] == "stale"
    assert stale["remedy"] == "ciu worktree refork one"

    doc = worktree.refork_instance(tmp_repo, "one")
    assert doc["operation"] == "refork"
    assert doc["refork"]["previous_fork_point_sha"] == old
    assert doc["refork"]["fork_point_sha"] == new_main
    assert doc["refork"]["changed"] is True
    stored = _stored(record)
    # R-35a: the recorded fork point now equals merge-base(main, HEAD) and is
    # NOT HEAD (a HEAD-writing implementation would give an empty diff).
    assert stored["fork_point_sha"] == new_main != head
    assert stored["schema_version"] == 6
    assert worktree.inspect_instance(tmp_repo, "one")["fork_point"]["status"] == "current"


def test_refork_is_idempotent(tmp_repo, fake_generate_env):
    record, new_main, _head = _rebased(tmp_repo)
    worktree.refork_instance(tmp_repo, "one")
    before = record.record_path.read_bytes()
    doc = worktree.refork_instance(tmp_repo, "one")
    assert doc["refork"]["changed"] is False
    assert doc["refork"]["previous_fork_point_sha"] == new_main
    assert record.record_path.read_bytes() == before


def test_refork_refuses_a_branch_that_diverged_instead_of_rebased(
    tmp_repo, fake_generate_env
):
    record = worktree.create(tmp_repo, "one", base="main")
    _commit(record.git_worktree_path, "own.txt")
    _commit(tmp_repo, "later.txt")  # main moved; the branch was NOT rebased
    before = record.record_path.read_bytes()
    with pytest.raises(worktree.WorktreeError, match="not an ancestor"):
        worktree.refork_instance(tmp_repo, "one")
    assert record.record_path.read_bytes() == before


def test_refork_refuses_a_dirty_tree(tmp_repo, fake_generate_env):
    record, _new_main, _head = _rebased(tmp_repo)
    (record.git_worktree_path / "scratch.txt").write_text("x", encoding="utf-8")
    before = record.record_path.read_bytes()
    with pytest.raises(worktree.WorktreeError, match="uncommitted changes"):
        worktree.refork_instance(tmp_repo, "one")
    assert record.record_path.read_bytes() == before


def test_refork_refuses_an_unknown_instance_and_a_bad_trunk(
    tmp_repo, fake_generate_env
):
    with pytest.raises(worktree.WorktreeError, match="no managed worktree"):
        worktree.refork_instance(tmp_repo, "nope")
    worktree.create(tmp_repo, "one", base="main")
    with pytest.raises(worktree.WorktreeError, match="does not resolve"):
        worktree.refork_instance(tmp_repo, "one", onto="no-such-ref")


def test_refork_refuses_a_not_ready_record(tmp_repo, fake_generate_env, monkeypatch):
    record = worktree.create(tmp_repo, "one", base="main")
    worktree._mark_recovery(record, "env-generation-failed")
    with pytest.raises(worktree.WorktreeError, match="ensure"):
        worktree.refork_instance(tmp_repo, "one")


def test_refork_explicit_onto_names_the_trunk(tmp_repo, fake_generate_env):
    _record, new_main, _head = _rebased(tmp_repo)
    doc = worktree.refork_instance(tmp_repo, "one", onto="main")
    assert doc["refork"]["trunk"] == "main"
    assert doc["refork"]["fork_point_sha"] == new_main


def test_inspect_reports_unknown_when_the_trunk_does_not_resolve(
    tmp_repo, fake_generate_env, monkeypatch
):
    worktree.create(tmp_repo, "one", base="main")
    _git(["branch", "-m", "main", "renamed"], tmp_repo)
    monkeypatch.setattr(worktree, "_default_branch", lambda _root: None)
    info = worktree.inspect_instance(tmp_repo, "one")["fork_point"]
    assert info["status"] == "unknown"
    assert info["recorded"] is not None


class TestReforkCli:
    def _run(self, capsys, argv):
        code = cli._worktree(argv)
        return code, capsys.readouterr().out

    def test_json_and_human_output(self, tmp_repo, fake_generate_env, capsys):
        _rebased(tmp_repo)
        root = ["--root-folder", str(tmp_repo)]
        code, out = self._run(capsys, ["inspect", "one", *root])
        assert code == 0 and "fork point: stale" in out
        assert "ciu worktree refork one" in out
        code, out = self._run(capsys, ["refork", "one", "--json", *root])
        assert code == 0
        assert json.loads(out)["refork"]["changed"] is True
        code, out = self._run(capsys, ["refork", "one", *root])
        assert code == 0 and "changed: False" in out
        code, out = self._run(capsys, ["inspect", "one", *root])
        assert "fork point: current" in out

    def test_unknown_instance_exits_two(self, tmp_repo, capsys):
        code, _ = self._run(
            capsys, ["refork", "nope", "--root-folder", str(tmp_repo)]
        )
        assert code == 2

    def test_the_verb_is_registered(self):
        from ciu.cli_registry import build_cli

        verbs = {v.name: v for v in build_cli().delegates["worktree"].registered_verbs}
        assert "--onto" in {f for option in verbs["refork"].options for f in option.flags}


def test_inspect_reports_an_unrecorded_fork_point(tmp_repo, fake_generate_env):
    from dataclasses import replace

    record = worktree.create(tmp_repo, "one", base="main")
    worktree._write_instance_record(replace(record, fork_point_sha=None))
    info = worktree.inspect_instance(tmp_repo, "one")["fork_point"]
    assert info["status"] == "unrecorded"
    # An unrecorded (adopt-style) record can still be given a fork point.
    doc = worktree.refork_instance(tmp_repo, "one")
    assert doc["refork"]["previous_fork_point_sha"] is None
    assert doc["refork"]["changed"] is True


def test_inspect_reports_unknown_for_unrelated_histories(tmp_repo, fake_generate_env):
    record = worktree.create(tmp_repo, "one", base="main")
    checkout = record.git_worktree_path
    # Replace the branch's history with an unrelated root commit, keeping the
    # branch name the record claims.
    empty_tree = _git(["hash-object", "-t", "tree", "/dev/null"], checkout).stdout.strip()
    root = _git(["commit-tree", empty_tree, "-m", "island"], checkout).stdout.strip()
    assert _git(["reset", "--hard", root], checkout).returncode == 0
    info = worktree.inspect_instance(tmp_repo, "one")["fork_point"]
    assert info["status"] == "unknown"
    assert info["merge_base"] is None


@pytest.mark.parametrize(
    "failing, message",
    [("status", "could not read git status"), ("merge-base", "could not compute")],
)
def test_refork_reports_a_git_read_failure(
    tmp_repo, fake_generate_env, monkeypatch, failing, message
):
    import subprocess

    worktree.create(tmp_repo, "one", base="main")
    real_git = worktree._git
    state = {"after_ancestor": False}

    def flaky(args, cwd):
        if args[:1] == ["merge-base"] and "--is-ancestor" in args:
            state["after_ancestor"] = True
        hit = (args[:1] == ["status"]) if failing == "status" else (
            args[:1] == ["merge-base"] and "--is-ancestor" not in args
            and state["after_ancestor"]
        )
        if hit:
            return subprocess.CompletedProcess(args, 1, "", "boom")
        return real_git(args, cwd)

    monkeypatch.setattr(worktree, "_git", flaky)
    with pytest.raises(worktree.WorktreeError, match=message):
        worktree.refork_instance(tmp_repo, "one")
