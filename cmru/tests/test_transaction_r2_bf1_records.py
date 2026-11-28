"""Behaviour witnesses for the release transaction's durable scope sidecars.

Each writer's exact on-disk document, each reader's refusal of a record that
breaks exactly one rule, and the rule that a failed write removes its partial
file instead of masking the original error. Added for the first-release R2
survivor batch (`transaction.py`); no product code changes.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from cmru import transaction
from tests._git_isolation import git_isolation  # noqa: F401

BASE = "a" * 40
BRANCH = "cmru-release-20261128_120000-demo-ab12cd"
pytestmark = pytest.mark.usefixtures("git_isolation")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    result = subprocess.run(["git", "init", "-q", "-b", "main", str(root)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return root


def _workspace(tmp_path: Path, base: str = BASE) -> transaction.ReleaseWorkspace:
    return transaction.ReleaseWorkspace(tmp_path, tmp_path / "candidate", BRANCH, base)


@pytest.fixture
def scope(tmp_path, monkeypatch):
    directory = tmp_path / "scope"
    directory.mkdir()
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: directory)
    monkeypatch.setattr(transaction, "_ensure_scope_dir", lambda _root: directory)
    return directory


def _record_path(scope_dir: Path, workspace, suffix: str) -> Path:
    return scope_dir / f"{transaction._release_token(workspace)}{suffix}"


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fdopen_fails_after_removing(monkeypatch, target: Path, message: str = "simulated write failure") -> None:
    """Simulate a write failure whose partial file is gone when cleanup runs."""
    real_fdopen = os.fdopen

    def failing_fdopen(fd, *args, **kwargs):
        try:
            opened = Path(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:  # pragma: no cover - the descriptor is always open here
            return real_fdopen(fd, *args, **kwargs)
        if opened != target:
            return real_fdopen(fd, *args, **kwargs)
        target.unlink(missing_ok=True)
        os.close(fd)
        raise OSError(message)

    monkeypatch.setattr(transaction.os, "fdopen", failing_fdopen)


# --- write_release_source -----------------------------------------------------


def test_feature_source_record_is_written_in_canonical_sorted_json(tmp_path, scope):
    root = _repo(tmp_path)
    workspace = _workspace(tmp_path)
    transaction.write_release_source(root, workspace, branch="feature/release", commit=BASE)

    path = _record_path(scope, workspace, ".source.json")
    expected = {
        "schema_version": 1,
        "mode": "worktree",
        "source_ref": "refs/heads/feature/release",
        "source_commit": BASE,
    }
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    text = path.read_text(encoding="utf-8")
    assert text == json.dumps(expected, indent=2, sort_keys=True) + "\n"
    order = [text.index(f'"{key}"') for key in ("mode", "schema_version", "source_commit", "source_ref")]
    assert order == sorted(order)


def test_feature_source_write_failure_re_raises_its_own_error(tmp_path, scope, monkeypatch):
    root = _repo(tmp_path)
    workspace = _workspace(tmp_path)
    path = _record_path(scope, workspace, ".source.json")
    _fdopen_fails_after_removing(monkeypatch, path)

    with pytest.raises(OSError, match="simulated write failure"):
        transaction.write_release_source(root, workspace, branch="feature/release", commit=BASE)
    assert not path.exists()


def test_release_source_rejects_a_record_that_breaks_any_single_rule(tmp_path, scope, monkeypatch):
    root = _repo(tmp_path)
    workspace = _workspace(tmp_path)
    valid = {
        "schema_version": 1,
        "mode": "worktree",
        "source_ref": "refs/heads/feature/release",
        "source_commit": BASE,
    }
    cases = {
        "extra-key": {**valid, "extra": 1},
        "schema-version-type": {**valid, "schema_version": True},
        "schema-version-value": {**valid, "schema_version": 2},
        "mode": {**valid, "mode": "origin-main"},
        "source-ref-type": {**valid, "source_ref": 5},
        "source-ref-format": {**valid, "source_ref": "refs/heads/bad name"},
        "source-ref-main": {**valid, "source_ref": "refs/heads/main"},
        "source-commit-type": {**valid, "source_commit": 5},
        "source-commit-format": {**valid, "source_commit": "z" * 40},
        "source-commit-base": {**valid, "source_commit": "b" * 40},
    }
    for name, document in cases.items():
        _write_json(_record_path(scope, workspace, ".source.json"), document)
        with pytest.raises(RuntimeError, match="release source record is malformed"):
            transaction.read_release_source(root, workspace)
        (scope / f"{transaction._release_token(workspace)}.source.json").unlink()

    _write_json(_record_path(scope, workspace, ".source.json"), valid)
    assert transaction.read_release_source(root, workspace) == {
        "mode": "worktree",
        "source_ref": "refs/heads/feature/release",
        "source_commit": BASE,
    }


# --- write_release_version_plan ----------------------------------------------


def test_version_plan_record_is_written_in_canonical_sorted_json(tmp_path, scope):
    workspace = _workspace(tmp_path)
    transaction.write_release_version_plan(tmp_path, workspace, {"demo": "6.2.0", "raw": None})

    expected = {
        "schema_version": 1,
        "transaction": BRANCH,
        "base": BASE,
        "versions": {"demo": "6.2.0", "raw": None},
    }
    path = _record_path(scope, workspace, ".version-plan.json")
    assert path.read_text(encoding="utf-8") == json.dumps(expected, indent=2, sort_keys=True) + "\n"


def test_a_version_plan_write_failure_re_raises_its_own_error(tmp_path, scope, monkeypatch):
    workspace = _workspace(tmp_path)
    path = _record_path(scope, workspace, ".version-plan.json")
    _fdopen_fails_after_removing(monkeypatch, path)

    with pytest.raises(OSError, match="simulated write failure"):
        transaction.write_release_version_plan(tmp_path, workspace, {"demo": "1.0.0"})
    assert not path.exists()


# --- write_release_gates ------------------------------------------------------


def test_gates_record_is_written_in_canonical_sorted_json(tmp_path, scope):
    workspace = _workspace(tmp_path)
    transaction.write_release_gates(tmp_path, workspace, None)

    expected = {
        "schema_version": 1,
        "transaction": BRANCH,
        "base": BASE,
        "gates": {"skipped": False, "reason": None},
    }
    path = _record_path(scope, workspace, ".gates.json")
    assert path.read_text(encoding="utf-8") == json.dumps(expected, indent=2, sort_keys=True) + "\n"


def test_a_gates_write_failure_re_raises_its_own_error(tmp_path, scope, monkeypatch):
    workspace = _workspace(tmp_path)
    path = _record_path(scope, workspace, ".gates.json")
    _fdopen_fails_after_removing(monkeypatch, path)

    with pytest.raises(OSError, match="simulated write failure"):
        transaction.write_release_gates(tmp_path, workspace, "operator: no gate")
    assert not path.exists()


# --- _read_gates_file ---------------------------------------------------------


def test_gates_reader_refuses_a_non_string_base_binding(tmp_path, scope):
    workspace = _workspace(tmp_path)
    path = _record_path(scope, workspace, ".gates.json")
    _write_json(path, {
        "schema_version": 1,
        "transaction": BRANCH,
        "base": 5,
        "gates": {"skipped": False, "reason": None},
    })

    with pytest.raises(RuntimeError, match="release gates record is malformed"):
        transaction._read_gates_file(path, branch=None, base=None)


# --- tag attempt / absence records -------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        {"refs/heads/main": BASE},
        {"refs/tags/demo-v1": 5},
    ],
    ids=["ref-namespace", "object-id-type"],
)
def test_tag_attempt_reader_refuses_one_broken_record(tmp_path, scope, raw):
    workspace = _workspace(tmp_path)
    _write_json(_record_path(scope, workspace, ".tag-attempts.json"), raw)

    with pytest.raises(RuntimeError, match="release tag attempt record is malformed"):
        transaction.read_release_tag_attempts(tmp_path, workspace)


def test_a_tag_attempt_one_sided_change_consults_the_absence_record(tmp_path, scope):
    workspace = _workspace(tmp_path)
    ref = "refs/tags/demo-v1"
    # A prior attempt recorded ``b``, and origin was confirmed to lack exactly
    # that object: a new local tag may now be recorded for the same ref.
    previous = "b" * 40
    _write_json(_record_path(scope, workspace, ".tag-attempts.json"), {ref: previous})
    _write_json(_record_path(scope, workspace, ".tag-absent.json"), {ref: previous})

    transaction.write_release_tag_attempts(tmp_path, workspace, {ref: BASE})

    assert transaction.read_release_tag_attempts(tmp_path, workspace) == {ref: BASE}


def test_clearing_absence_proofs_keeps_an_existing_empty_record_when_nothing_changed(
    tmp_path, scope,
):
    workspace = _workspace(tmp_path)
    path = _record_path(scope, workspace, ".tag-absent.json")
    _write_json(path, {})

    transaction.clear_release_tag_absence(tmp_path, workspace, {"refs/tags/demo-v1": BASE})

    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_clearing_the_last_absence_proof_tolerates_an_already_absent_record(
    tmp_path, scope, monkeypatch,
):
    workspace = _workspace(tmp_path)
    proofs = {"refs/tags/demo-v1": BASE}
    monkeypatch.setattr(
        transaction, "read_confirmed_absent_release_tag_attempts", lambda *_args: dict(proofs),
    )

    transaction.clear_release_tag_absence(tmp_path, workspace, proofs)

    assert not _record_path(scope, workspace, ".tag-absent.json").exists()


@pytest.mark.parametrize(
    "proof",
    [
        {"refs/tags/demo-v1": "b" * 40},
        {"refs/tags/demo-v1": 5},
    ],
    ids=["not-the-recorded-attempt", "object-id-type"],
)
def test_absence_reader_refuses_a_proof_that_breaks_one_rule(tmp_path, scope, proof):
    workspace = _workspace(tmp_path)
    _write_json(_record_path(scope, workspace, ".tag-attempts.json"), {"refs/tags/demo-v1": BASE})
    _write_json(_record_path(scope, workspace, ".tag-absent.json"), proof)

    with pytest.raises(RuntimeError, match="release tag absence record is malformed"):
        transaction.read_confirmed_absent_release_tag_attempts(tmp_path, workspace)


# --- write_release_result ----------------------------------------------------


def test_result_record_keeps_projects_and_canonical_json(tmp_path, scope, monkeypatch):
    # The scope directory is the Git common dir's child, so its parent (the
    # common dir) always exists; the writer must not need more than that.
    workspace = _workspace(tmp_path)
    nested = tmp_path / "git-common" / "cmru-release-scopes"
    nested.parent.mkdir()
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: nested)
    monkeypatch.setattr(transaction, "_ensure_scope_dir", lambda _root: nested)
    transaction.write_release_result(tmp_path, workspace, "zeta", "tag-zeta")
    transaction.write_release_result(tmp_path, workspace, "alpha", "tag-alpha")

    expected = {"zeta": "tag-zeta", "alpha": "tag-alpha"}
    path = nested / f"{transaction._release_token(workspace)}.results.json"
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    text = path.read_text(encoding="utf-8")
    assert text == json.dumps(expected, indent=2, sort_keys=True) + "\n"
    assert text.index('"alpha"') < text.index('"zeta"')


# --- _atomic_write_durable ---------------------------------------------------


def test_atomic_write_failure_after_the_rename_keeps_its_own_error(tmp_path, monkeypatch):
    path = tmp_path / "untagged.json"
    real_replace = transaction.os.replace

    def replace_then_fail(source, destination):
        real_replace(source, destination)
        raise OSError("replace failed")

    monkeypatch.setattr(transaction.os, "replace", replace_then_fail)

    with pytest.raises(OSError, match="replace failed"):
        transaction._atomic_write_durable(path, "{}")
    assert path.is_file()
    assert list(tmp_path.glob(".*.tmp")) == []
