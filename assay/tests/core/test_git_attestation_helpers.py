"""P26/A-211 — the four narrow attestation helpers own *exit-code and raw-output
interpretation*, and the frozen contract §4 makes every "anything else" a TYPED
Git failure rather than a quiet answer.

Reviewer addition: the locked packet drives these helpers only along their
successful and record-level paths, so every one of §4's own "anything else is
typed Git failure" clauses — `is_ancestor`'s non-0/1 exit, `path_is_current`'s
non-0/1 exit, and all five of `tree_entry_kind`'s raw-output rejections — was
unproven. Breaking any of them left the whole suite green, which is exactly the
vacuity A-067 exists to catch.

The distinction is not cosmetic. Each of these guards separates "Git told us the
answer" from "Git could not answer": collapsing them would let an unresolvable
or hostile identity read as an ordinary ``False``/``None`` and so render a
*judged* attestation terminal (``STALE_ATTESTATION`` or a missing path) instead
of ``ERROR``/``UNREADABLE_ARTIFACT``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import GitRepo

from assay import git
from assay.errors import AssayError, Outcome, ReasonCode

#: a well-formed 40-hex OID that exists in no repository
ABSENT_OID = "0" * 39 + "1"


def _remaining() -> float:
    return 60.0


def _assert_git_failed(caught: pytest.ExceptionInfo[AssayError]) -> None:
    assert (caught.value.outcome, caught.value.reason_code) == (
        Outcome.ERROR,
        ReasonCode.GIT_FAILED,
    )


# --- real-git exit-code discrimination ---------------------------------------


def test_is_ancestor_raises_rather_than_reporting_a_missing_commit_as_not_ancestor(
    git_repo: GitRepo,
):
    """§4.2: exit 0 is true, 1 is false, "anything else is typed Git failure".

    ``merge-base --is-ancestor`` exits >1 for an unresolvable ref. Reading that
    as a plain ``False`` would let an attestation naming a commit this
    repository does not have be reported as a legitimate "not an ancestor"
    judgement instead of an unreadable artifact.
    """
    head = git_repo.head()

    with pytest.raises(AssayError) as caught:
        git.is_ancestor(git_repo.path, ABSENT_OID, head, remaining=_remaining)

    _assert_git_failed(caught)
    # the honest answers still work, so the guard is not simply swallowing
    assert git.is_ancestor(git_repo.path, head, head, remaining=_remaining) is True


def test_path_is_current_raises_rather_than_reporting_a_bad_rev_as_changed(
    git_repo: GitRepo,
):
    """§4.4: exit 0 true, 1 false, anything else typed failure. A ``diff``
    against an unresolvable revision exits >1; reading that as ``False``
    would render a payload-preserving ``STALE_ATTESTATION`` — a *judged*
    staleness — for evidence Git could not evaluate at all."""
    git_repo.write("reviewed.py", "x = 1\n")
    head = git_repo.commit_all("add reviewed.py")

    with pytest.raises(AssayError) as caught:
        git.path_is_current(
            git_repo.path, ABSENT_OID, head, "reviewed.py", remaining=_remaining
        )

    _assert_git_failed(caught)
    assert (
        git.path_is_current(
            git_repo.path, head, head, "reviewed.py", remaining=_remaining
        )
        is True
    )


def test_tree_entry_kind_raises_rather_than_reporting_a_bad_commit_as_absent(
    git_repo: GitRepo,
):
    """§4.3: empty output is absence (``None``); a non-zero exit is a typed
    failure. Collapsing them would turn "this commit does not exist" into
    "the reviewed path is missing at the attested commit"."""
    git_repo.write("reviewed.py", "x = 1\n")
    head = git_repo.commit_all("add reviewed.py")

    with pytest.raises(AssayError) as caught:
        git.tree_entry_kind(git_repo.path, ABSENT_OID, "reviewed.py", remaining=_remaining)

    _assert_git_failed(caught)
    assert (
        git.tree_entry_kind(git_repo.path, head, "reviewed.py", remaining=_remaining)
        == "blob"
    )
    assert (
        git.tree_entry_kind(git_repo.path, head, "no/such/path", remaining=_remaining)
        is None
    ), "genuine absence stays None"


def test_tree_entry_kind_refuses_a_gitlink_that_is_neither_blob_nor_tree(
    git_repo: GitRepo,
):
    """§4.3 accepts EXACTLY ``blob`` or ``tree``. A gitlink (mode 160000)
    reports type ``commit``: a real, committable third kind. Accepting it would
    let a reviewed path resolve to a submodule pointer whose contents this
    repository never stores, so ``diff`` could never observe a change beneath
    it — a permanently, silently "current" attestation."""
    git_repo.write("keep.py", "x = 1\n")
    git_repo.commit_all("seed")
    git.run(
        git_repo.path,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{ABSENT_OID},vendor/sub",
    )
    head = git.run(git_repo.path, "commit-tree", "-m", "gitlink",
                   git.run(git_repo.path, "write-tree").strip()).strip()

    kind_of_blob = git.tree_entry_kind(
        git_repo.path, head, "keep.py", remaining=_remaining
    )
    assert kind_of_blob == "blob", "the ordinary entry still resolves"

    with pytest.raises(AssayError) as caught:
        git.tree_entry_kind(git_repo.path, head, "vendor/sub", remaining=_remaining)

    _assert_git_failed(caught)


# --- raw ls-tree output rejections (git itself will not emit these) ----------


@pytest.mark.parametrize(
    ("name", "stdout"),
    [
        (
            "two records for one exact query",
            b"100644 blob " + b"a" * 40 + b"\tsrc/api.py\x00"
            b"100644 blob " + b"b" * 40 + b"\tsrc/other.py\x00",
        ),
        ("no TAB separator", b"100644 blob " + b"a" * 40 + b" src/api.py\x00"),
        (
            "a path that is not the exact query",
            b"100644 blob " + b"a" * 40 + b"\tsrc/other.py\x00",
        ),
        ("malformed metadata", b"100644 blob\tsrc/api.py\x00"),
        ("an unsupported object type", b"160000 commit " + b"a" * 40 + b"\tsrc/api.py\x00"),
    ],
)
def test_tree_entry_kind_rejects_every_malformed_raw_record(
    name: str, stdout: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """§4.3's raw-record contract, driven directly at the boundary because a
    healthy ``git`` will not produce these shapes.

    The path-mismatch row is the load-bearing one: without it, ``ls-tree``
    answering about a DIFFERENT path than the one queried would be accepted as
    the reviewed path's own kind — the identity confusion ``--literal-pathspecs``
    exists to prevent, unverified.
    """
    monkeypatch.setattr(git, "_run_raw", lambda *a, **k: (0, stdout, b""))

    with pytest.raises(AssayError) as caught:
        git.tree_entry_kind(tmp_path, "a" * 40, "src/api.py", remaining=_remaining)

    _assert_git_failed(caught)


def test_tree_entry_kind_accepts_the_one_well_formed_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The positive control for the parametrized rejections above: the same
    injection shape, with the record git really does emit, must succeed — so
    the rejections are proved to discriminate rather than to refuse
    everything."""
    stdout = b"040000 tree " + b"a" * 40 + b"\tsrc/api.py\x00"
    monkeypatch.setattr(git, "_run_raw", lambda *a, **k: (0, stdout, b""))

    assert (
        git.tree_entry_kind(tmp_path, "a" * 40, "src/api.py", remaining=_remaining)
        == "tree"
    )
