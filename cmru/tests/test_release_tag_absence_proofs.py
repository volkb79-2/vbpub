"""Persistence and invalidation rules for exact failed-push absence proofs."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cmru import transaction


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _workspace(tmp_path: Path) -> tuple[Path, transaction.ReleaseWorkspace]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "cmru-test@example.invalid")
    _git(root, "config", "user.name", "cmru test")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "initial")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    return root, transaction.ReleaseWorkspace(
        root, candidate, "cmru/release/absence-proof", "a" * 40,
    )


def _absence_path(root: Path, workspace: transaction.ReleaseWorkspace) -> Path:
    return transaction._scope_dir(root) / f"{transaction._release_token(workspace)}.tag-absent.json"


def test_rel14_sidecars_accept_sha256_object_ids_like_sha1(tmp_path):
    root, workspace = _workspace(tmp_path)
    refs = {"refs/tags/demo-v1": "a" * 64, "refs/tags/demo-v2": "b" * 40}

    transaction.write_release_tag_snapshot(root, workspace, refs)
    assert transaction.read_release_tag_snapshot(root, workspace) == refs

    transaction.write_release_tag_attempts(root, workspace, refs)
    assert transaction.read_release_tag_attempts(root, workspace) == refs


@pytest.mark.parametrize("bad", ["a" * 41, "a" * 63, "A" * 40, "g" * 64, ""])
def test_rel14_sidecars_still_reject_malformed_object_ids(tmp_path, bad):
    root, workspace = _workspace(tmp_path)
    with pytest.raises(RuntimeError):
        transaction.write_release_tag_snapshot(root, workspace, {"refs/tags/demo-v1": bad})


def test_absence_proofs_are_noop_when_unrelated_and_preserve_other_refs_when_cleared(
    tmp_path,
):
    root, workspace = _workspace(tmp_path)
    first = {"refs/tags/demo-v1": "a" * 40}
    second = {"refs/tags/other-v1": "b" * 40}
    transaction.clear_release_tag_absence(root, workspace, {})
    transaction.write_release_tag_attempts(root, workspace, {**first, **second})
    transaction.write_confirmed_absent_release_tag_attempts(
        root, workspace, {**first, **second},
    )

    transaction.clear_release_tag_absence(
        root, workspace, {"refs/tags/unrelated-v1": "c" * 40},
    )
    assert transaction.read_confirmed_absent_release_tag_attempts(root, workspace) == {
        **first, **second,
    }

    transaction.clear_release_tag_absence(root, workspace, first)
    assert transaction.read_confirmed_absent_release_tag_attempts(root, workspace) == second
    assert json.loads(_absence_path(root, workspace).read_text(encoding="utf-8")) == second

    transaction.clear_release_tag_absence(root, workspace, second)
    assert transaction.read_confirmed_absent_release_tag_attempts(root, workspace) == {}
    assert not _absence_path(root, workspace).exists()


@pytest.mark.parametrize(
    "ref, oid, message",
    [
        ("refs/heads/main", "a" * 40, "malformed ref"),
        ("refs/tags/demo-v1", "bad", "malformed ref"),
    ],
)
def test_absence_proof_writer_rejects_malformed_records(tmp_path, ref, oid, message):
    root, workspace = _workspace(tmp_path)

    with pytest.raises(RuntimeError, match=message):
        transaction.write_confirmed_absent_release_tag_attempts(
            root, workspace, {ref: oid},
        )


def test_absence_proof_writer_requires_the_exact_recorded_attempt(tmp_path):
    root, workspace = _workspace(tmp_path)
    ref = "refs/tags/demo-v1"
    transaction.write_release_tag_attempts(root, workspace, {ref: "a" * 40})

    with pytest.raises(RuntimeError, match="does not match its recorded push attempt"):
        transaction.write_confirmed_absent_release_tag_attempts(
            root, workspace, {ref: "b" * 40},
        )


def test_absence_proof_writer_refuses_a_tag_that_still_exists_locally(tmp_path):
    root, workspace = _workspace(tmp_path)
    ref = "refs/tags/demo-v1"
    oid = "a" * 40
    transaction.write_release_tag_attempts(root, workspace, {ref: oid})
    _git(root, "tag", "demo-v1", "HEAD")

    with pytest.raises(RuntimeError, match="still exists in the local repository"):
        transaction.write_confirmed_absent_release_tag_attempts(
            root, workspace, {ref: oid},
        )


def test_absence_proof_reader_reports_unreadable_sidecar_metadata(monkeypatch, tmp_path):
    root, workspace = _workspace(tmp_path)
    path = _absence_path(root, workspace)
    path.parent.mkdir(parents=True)
    real_lstat = Path.lstat

    def unreadable(self):
        if self == path:
            raise PermissionError("permission denied")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", unreadable)
    with pytest.raises(RuntimeError, match="cannot inspect release tag absence record.*permission denied"):
        transaction.read_confirmed_absent_release_tag_attempts(root, workspace)


def test_absence_proof_reader_rejects_nonregular_sidecar(tmp_path):
    root, workspace = _workspace(tmp_path)
    _absence_path(root, workspace).mkdir(parents=True)

    with pytest.raises(RuntimeError, match="absence record is not a regular file"):
        transaction.read_confirmed_absent_release_tag_attempts(root, workspace)


@pytest.mark.parametrize("payload", ["{", "[]", '{"refs/tags/demo-v1":"a"}'])
def test_absence_proof_reader_rejects_unreadable_or_malformed_payloads(tmp_path, payload):
    root, workspace = _workspace(tmp_path)
    path = _absence_path(root, workspace)
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(RuntimeError, match="cannot read release tag absence record|absence record is malformed"):
        transaction.read_confirmed_absent_release_tag_attempts(root, workspace)


@pytest.mark.parametrize(
    "payload",
    [
        '{"refs/heads/main":"' + "a" * 40 + '"}',
        '{"refs/tags/demo-v1":"bad"}',
        '{"refs/tags/demo-v1":"' + "a" * 40 + '"}',
    ],
)
def test_absence_proof_reader_rejects_unmatched_or_invalid_proof_entries(tmp_path, payload):
    root, workspace = _workspace(tmp_path)
    path = _absence_path(root, workspace)
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(RuntimeError, match="absence record is malformed"):
        transaction.read_confirmed_absent_release_tag_attempts(root, workspace)


def test_absence_proof_reader_reports_oserror_while_reading(monkeypatch, tmp_path):
    root, workspace = _workspace(tmp_path)
    path = _absence_path(root, workspace)
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")
    real_read_text = Path.read_text

    def unreadable(self, *args, **kwargs):
        if self == path:
            raise PermissionError("permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(RuntimeError, match="cannot read release tag absence record.*permission denied"):
        transaction.read_confirmed_absent_release_tag_attempts(root, workspace)


def test_absence_proof_reader_rejects_duplicate_json_keys(tmp_path):
    root, workspace = _workspace(tmp_path)
    path = _absence_path(root, workspace)
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"refs/tags/demo-v1":"' + "a" * 40 + '",'
        '"refs/tags/demo-v1":"' + "b" * 40 + '"}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="cannot read release tag absence record.*duplicate JSON"):
        transaction.read_confirmed_absent_release_tag_attempts(root, workspace)
