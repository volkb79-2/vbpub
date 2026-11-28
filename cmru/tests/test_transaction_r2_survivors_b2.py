"""Caller-visible witnesses for the second transaction R2 survivor batch."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cmru import transaction
from tests._git_isolation import git_isolation, isolate_git  # noqa: F401

BASE = "a" * 40
BRANCH = "cmru-release-20261128_120000-demo-ab12cd"
pytestmark = pytest.mark.usefixtures("git_isolation")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "source.txt").write_text("release source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "source.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "initial"], check=True,
    )
    return root


def _workspace(root: Path) -> transaction.ReleaseWorkspace:
    return transaction.ReleaseWorkspace(root, root / "candidate", BRANCH, BASE)


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
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_local_tag_listing_failure_is_a_runtime_error(tmp_path):
    outside_repository = tmp_path / "not-a-repository"
    outside_repository.mkdir()

    with pytest.raises(RuntimeError, match="cannot inspect local release tags"):
        transaction.list_local_tag_refs(outside_repository)


def test_first_and_repeated_tag_attempts_are_recorded(tmp_path, scope):
    root = _repo(tmp_path)
    workspace = _workspace(root)
    attempts = {"refs/tags/demo-v1": BASE}

    transaction.write_release_tag_attempts(root, workspace, attempts)
    transaction.write_release_tag_attempts(root, workspace, attempts)

    assert transaction.read_release_tag_attempts(root, workspace) == attempts


def test_absence_confirmation_is_written_only_for_an_attempt_without_a_local_tag(
    tmp_path, scope,
):
    root = _repo(tmp_path)
    workspace = _workspace(root)
    ref = "refs/tags/demo-v1"
    transaction.write_release_tag_attempts(root, workspace, {ref: BASE})

    transaction.write_confirmed_absent_release_tag_attempts(root, workspace, {ref: BASE})

    assert transaction.read_confirmed_absent_release_tag_attempts(root, workspace) == {
        ref: BASE,
    }
    local_oid = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(root), "tag", "demo-v1", local_oid], check=True)
    with pytest.raises(RuntimeError, match="still exists in the local repository"):
        transaction.write_confirmed_absent_release_tag_attempts(root, workspace, {ref: BASE})


def test_absence_reader_rejects_an_invalid_tag_ref(tmp_path, scope):
    root = _repo(tmp_path)
    workspace = _workspace(root)
    _write_json(_record_path(scope, workspace, ".tag-attempts.json"), {})
    _write_json(
        _record_path(scope, workspace, ".tag-absent.json"),
        {"refs/tags/bad name": BASE},
    )

    with pytest.raises(RuntimeError, match="release tag absence record is malformed"):
        transaction.read_confirmed_absent_release_tag_attempts(root, workspace)


def test_clearing_an_unrelated_absence_proof_leaves_its_file_untouched(tmp_path, scope):
    root = _repo(tmp_path)
    workspace = _workspace(root)
    ref = "refs/tags/kept-v1"
    transaction.write_release_tag_attempts(root, workspace, {ref: BASE})
    transaction.write_confirmed_absent_release_tag_attempts(root, workspace, {ref: BASE})
    path = _record_path(scope, workspace, ".tag-absent.json")
    hard_link = tmp_path / "same-original-record"
    hard_link.hardlink_to(path)

    transaction.clear_release_tag_absence(root, workspace, {"refs/tags/other-v1": BASE})

    assert path.read_bytes() == hard_link.read_bytes()
    assert path.stat().st_ino == hard_link.stat().st_ino
    assert transaction.read_confirmed_absent_release_tag_attempts(root, workspace) == {
        ref: BASE,
    }


@pytest.mark.parametrize(
    "ref",
    ["refs/heads/trailing/", "refs/heads/trailing.", "refs/heads/double..dot"],
    ids=["trailing-slash", "trailing-dot", "double-dot"],
)
def test_remote_ref_parser_rejects_git_invalid_names(ref):
    with pytest.raises(RuntimeError, match="origin branch lookup returned a malformed ref"):
        transaction.parse_ls_remote_refs(
            f"{BASE}\t{ref}\n", namespace="refs/heads/",
            description="origin branch lookup",
        )


def test_disallowed_remote_transport_is_a_runtime_refusal_with_git_diagnostic(
    tmp_path, monkeypatch,
):
    root = _repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", f"file://{tmp_path / 'absent.git'}"],
        check=True,
    )
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "https")

    with pytest.raises(RuntimeError, match="cannot inspect origin branch refs/heads/feature/demo") as error:
        transaction._origin_branch_oid(root, "feature/demo")

    assert "no diagnostic output" not in str(error.value)


def test_failed_origin_branch_fetch_keeps_the_runtime_error_contract(
    tmp_path, monkeypatch,
):
    root = _repo(tmp_path)
    oid = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    config = root / ".git" / "config"
    with config.open("a", encoding="utf-8") as stream:
        stream.write(f'\n[remote "origin"]\n\turl = file://{tmp_path / "origin.git"}\n')
    # The real fetch subprocess is rejected by Git's protocol policy before
    # Git starts a transport helper; the test then checks what CMRU returns.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "https")
    monkeypatch.setattr(transaction, "_origin_branch_oid", lambda *_a, **_k: oid)

    with pytest.raises(RuntimeError, match="could not fetch origin/feature/demo"):
        transaction.fetch_origin_branch(root, "feature/demo")
