"""O1/O2/O3 — ``evaluate_attestation``'s git-dependent core: equal-or-ancestor
first, then path-scoped staleness, entirely against real ``tmp_path`` git
repositories (DESIGN-GUIDE §10; no committed nested repo).

O1 (verbatim): *an attestation at HEAD and one at an ancestor with
byte-identical declared reviewed paths both produce attested evidence with
verified_by_assay=false and preserve producer, commit, and paths.*
Negative: *requiring exact HEAD rejects the ancestor fixture; marking a
loaded review verified fails the expected artifact.*

O2 (verbatim): *an ancestor whose reviewed path changed renders
NO_MEASUREMENT/STALE_ATTESTATION; a change outside all reviewed paths
remains current; a missing artifact renders NO_MEASUREMENT/MISSING_ATTESTATION.*
Negative: *using commit inequality alone stales the outside-path fixture;
ignoring path changes passes the changed-path fixture.*

O3 (verbatim, this module's slice): *a descendant, unrelated, or malformed
attested commit and a missing reviewed path render ERROR/UNREADABLE_ARTIFACT
(A-110 -- any git.run/GIT_FAILED the ancestry check itself raises for the
unrelated/malformed cases is caught and remapped, never left to propagate);
... no attestation path can create a computed claim.*
Negative: *a plain lexicographic/hash comparison, duplicate collapse,
letting GIT_FAILED propagate uncaught, or claims[] insertion makes one
reject fixture load or produces the wrong complete artifact.*

Mutation evidence for all of the above lives in the package LOG (A-067); this
module is the POSITIVE proof each mutation is checked against.
"""

from __future__ import annotations

import pytest

from assay.attestation import (
    AttestationRecord,
    _check_ancestor_or_equal,
    _check_reviewed_paths_exist,
    evaluate_attestation,
)
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import CLAIM_SOURCES, Claim


def _record(*, attested_commit: str, reviewed_paths: tuple[str, ...]) -> AttestationRecord:
    return AttestationRecord(
        producer="adversarial-review-bot-v3",
        attested_commit=attested_commit,
        reviewed_paths=reviewed_paths,
    )


# --- O1: HEAD and ancestor, byte-identical reviewed paths, both current -----


def test_an_attestation_naming_head_itself_is_current(git_repo):
    git_repo.write("reviewed.py", "x = 1\n")
    head = git_repo.commit_all("add reviewed.py")
    record = _record(attested_commit=head, reviewed_paths=("reviewed.py",))

    evidence = evaluate_attestation(git_repo.path, key="review", head=head, record=record)

    assert evidence.source == "attested"
    assert evidence.key == "review"
    assert evidence.status is Outcome.PASS
    assert evidence.reason_code is None
    assert evidence.verified_by_assay is False
    assert evidence.producer == "adversarial-review-bot-v3"
    assert evidence.attested_commit == head
    assert evidence.reviewed_paths == ("reviewed.py",)


def test_an_ancestor_attestation_with_unchanged_reviewed_paths_is_current(git_repo):
    git_repo.write("reviewed.py", "x = 1\n")
    attested_commit = git_repo.commit_all("add reviewed.py")
    git_repo.write("unrelated.py", "y = 2\n")
    head = git_repo.commit_all("unrelated change")
    record = _record(attested_commit=attested_commit, reviewed_paths=("reviewed.py",))

    evidence = evaluate_attestation(git_repo.path, key="review", head=head, record=record)

    assert attested_commit != head  # proves this is a REAL ancestor, not HEAD
    assert evidence.status is Outcome.PASS
    assert evidence.verified_by_assay is False
    assert evidence.attested_commit == attested_commit
    assert evidence.reviewed_paths == ("reviewed.py",)


def test_requiring_exact_head_would_reject_the_ancestor_fixture(git_repo):
    """O1's own negative, proven directly: the ancestor fixture above is
    current under the real equal-OR-ancestor contract; asserting head equality
    literally would be the wrong (rejected) behavior."""
    git_repo.write("reviewed.py", "x = 1\n")
    attested_commit = git_repo.commit_all("add reviewed.py")
    git_repo.write("unrelated.py", "y = 2\n")
    head = git_repo.commit_all("unrelated change")

    assert attested_commit != head, (
        "an exact-HEAD-only rule would reject this fixture even though it "
        "is current"
    )
    record = _record(attested_commit=attested_commit, reviewed_paths=("reviewed.py",))
    evidence = evaluate_attestation(git_repo.path, key="review", head=head, record=record)
    assert evidence.status is Outcome.PASS


# --- O2: a changed reviewed path stales; a change outside stays current ----


def test_a_changed_reviewed_path_renders_stale(git_repo):
    git_repo.write("reviewed.py", "x = 1\n")
    attested_commit = git_repo.commit_all("add reviewed.py")
    git_repo.write("reviewed.py", "x = 2\n")  # the reviewed path itself changes
    head = git_repo.commit_all("change reviewed.py")
    record = _record(attested_commit=attested_commit, reviewed_paths=("reviewed.py",))

    evidence = evaluate_attestation(git_repo.path, key="review", head=head, record=record)

    assert evidence.status is Outcome.NO_MEASUREMENT
    assert evidence.reason_code is ReasonCode.STALE_ATTESTATION
    assert evidence.verified_by_assay is False
    # payload is preserved even though stale -- a consumer can see WHAT changed.
    assert evidence.producer == "adversarial-review-bot-v3"
    assert evidence.attested_commit == attested_commit
    assert evidence.reviewed_paths == ("reviewed.py",)


def test_a_change_outside_every_reviewed_path_remains_current(git_repo):
    git_repo.write("reviewed.py", "x = 1\n")
    git_repo.write("unrelated.py", "y = 1\n")
    attested_commit = git_repo.commit_all("add both files")
    git_repo.write("unrelated.py", "y = 2\n")  # only the UNREVIEWED path changes
    head = git_repo.commit_all("change unrelated.py")
    record = _record(attested_commit=attested_commit, reviewed_paths=("reviewed.py",))

    evidence = evaluate_attestation(git_repo.path, key="review", head=head, record=record)

    assert evidence.status is Outcome.PASS
    assert evidence.reason_code is None


def test_commit_inequality_alone_would_wrongly_stale_the_outside_path_fixture(git_repo):
    """O2's own negative, proven directly against the previous fixture: the
    two commits ARE unequal, so a rule that stales on inequality alone would
    misjudge this exact, legitimately-current case."""
    git_repo.write("reviewed.py", "x = 1\n")
    git_repo.write("unrelated.py", "y = 1\n")
    attested_commit = git_repo.commit_all("add both files")
    git_repo.write("unrelated.py", "y = 2\n")
    head = git_repo.commit_all("change unrelated.py")

    assert attested_commit != head, (
        "a bare commit-inequality rule would stale this fixture even though "
        "no reviewed path changed"
    )
    record = _record(attested_commit=attested_commit, reviewed_paths=("reviewed.py",))
    evidence = evaluate_attestation(git_repo.path, key="review", head=head, record=record)
    assert evidence.status is Outcome.PASS


def test_a_missing_attestation_renders_no_measurement_missing_attestation(git_repo):
    head = git_repo.head()

    evidence = evaluate_attestation(git_repo.path, key="review", head=head, record=None)

    assert evidence.source == "attested"
    assert evidence.key == "review"
    assert evidence.status is Outcome.NO_MEASUREMENT
    assert evidence.reason_code is ReasonCode.MISSING_ATTESTATION
    assert evidence.verified_by_assay is False
    assert evidence.producer is None
    assert evidence.attested_commit is None
    assert evidence.reviewed_paths is None


# --- O3: descendant / unrelated / malformed attested commit ----------------


def test_a_descendant_attested_commit_renders_unreadable_artifact(git_repo):
    head = git_repo.head()
    git_repo.write("later.py", "z = 1\n")
    descendant = git_repo.commit_all("a commit AFTER head")  # attested "reviews the future"
    record = _record(attested_commit=descendant, reviewed_paths=("later.py",))

    evidence = evaluate_attestation(git_repo.path, key="review", head=head, record=record)

    assert evidence.status is Outcome.ERROR
    assert evidence.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert evidence.verified_by_assay is False
    assert evidence.producer is None  # ERROR carries no payload


def test_an_unrelated_attested_commit_renders_unreadable_artifact_not_git_failed(git_repo):
    head = git_repo.head()
    # An orphan branch inside the SAME repo has no common ancestor with main:
    # merge-base exits 1 (verified empirically), so this proves the real
    # GIT_FAILED->UNREADABLE_ARTIFACT remap (A-110), not just "some git
    # command failed".
    git_repo.git("checkout", "-q", "--orphan", "unrelated-history")
    git_repo.write("island.py", "w = 1\n")
    unrelated = git_repo.commit_all("a commit with no shared history")
    git_repo.git("checkout", "-q", "main")
    record = _record(attested_commit=unrelated, reviewed_paths=("island.py",))

    evidence = evaluate_attestation(git_repo.path, key="review", head=head, record=record)

    assert evidence.status is Outcome.ERROR
    assert evidence.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert evidence.verified_by_assay is False


def test_a_malformed_attested_commit_ref_renders_unreadable_artifact_not_git_failed(git_repo):
    head = git_repo.head()
    record = _record(
        attested_commit="not-a-real-ref-anywhere-zzz", reviewed_paths=("README.md",)
    )

    evidence = evaluate_attestation(git_repo.path, key="review", head=head, record=record)

    assert evidence.status is Outcome.ERROR
    assert evidence.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert evidence.verified_by_assay is False


def test_letting_git_failed_propagate_uncaught_would_be_the_bug(git_repo):
    """A-110's own negative, made concrete: proves the malformed-ref case
    above does NOT raise at all (an uncaught GIT_FAILED would propagate as an
    exception here instead of returning a value)."""
    head = git_repo.head()
    record = _record(
        attested_commit="not-a-real-ref-anywhere-zzz", reviewed_paths=("README.md",)
    )

    # If GIT_FAILED were left to propagate, this call would raise AssayError
    # instead of returning -- pytest.raises used here NEGATIVELY, i.e. proving
    # it does NOT raise, by simply calling it and reading the return value.
    evidence = evaluate_attestation(git_repo.path, key="review", head=head, record=record)
    assert evidence.reason_code is not ReasonCode.GIT_FAILED
    assert evidence.reason_code is ReasonCode.UNREADABLE_ARTIFACT


# --- O3: a reviewed path that never existed at the attested commit ---------


def test_a_reviewed_path_missing_at_the_attested_commit_renders_unreadable_artifact(git_repo):
    attested_commit = git_repo.head()  # "README.md" exists, "ghost.py" never did
    record = _record(attested_commit=attested_commit, reviewed_paths=("ghost.py",))

    evidence = evaluate_attestation(
        git_repo.path, key="review", head=attested_commit, record=record
    )

    assert evidence.status is Outcome.ERROR
    assert evidence.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert evidence.verified_by_assay is False


def test_one_missing_reviewed_path_among_several_still_renders_unreadable_artifact(git_repo):
    git_repo.write("real.py", "x = 1\n")
    attested_commit = git_repo.commit_all("add real.py")
    record = _record(
        attested_commit=attested_commit, reviewed_paths=("real.py", "ghost.py")
    )

    evidence = evaluate_attestation(
        git_repo.path, key="review", head=attested_commit, record=record
    )

    assert evidence.status is Outcome.ERROR
    assert evidence.reason_code is ReasonCode.UNREADABLE_ARTIFACT


# --- A-110's own remap, pinned INDEPENDENTLY of evaluate_attestation's outer
# --- catch: the ancestry/path-existence checks themselves never let a raw
# --- GIT_FAILED escape, even called directly.


def test_check_ancestor_or_equal_itself_remaps_a_malformed_ref_not_git_failed(git_repo):
    head = git_repo.head()

    with pytest.raises(AssayError) as excinfo:
        _check_ancestor_or_equal(git_repo.path, "not-a-real-ref-anywhere-zzz", head)

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_check_ancestor_or_equal_itself_remaps_an_unrelated_history_not_git_failed(git_repo):
    head = git_repo.head()
    git_repo.git("checkout", "-q", "--orphan", "unrelated-history-2")
    git_repo.write("island2.py", "w = 1\n")
    unrelated = git_repo.commit_all("no shared history")
    git_repo.git("checkout", "-q", "main")

    with pytest.raises(AssayError) as excinfo:
        _check_ancestor_or_equal(git_repo.path, unrelated, head)

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_check_ancestor_or_equal_itself_rejects_a_descendant_with_no_exception(git_repo):
    head = git_repo.head()
    git_repo.write("later.py", "z = 1\n")
    descendant = git_repo.commit_all("after head")

    with pytest.raises(AssayError) as excinfo:
        _check_ancestor_or_equal(git_repo.path, descendant, head)

    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_check_reviewed_paths_exist_itself_remaps_a_missing_path_not_git_failed(git_repo):
    head = git_repo.head()

    with pytest.raises(AssayError) as excinfo:
        _check_reviewed_paths_exist(git_repo.path, head, ("ghost.py",))

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


# --- O3: no attestation path can create a computed claim -------------------


def test_claim_source_is_closed_to_computed_only():
    assert CLAIM_SOURCES == ("computed",)
    with pytest.raises(ValueError, match="source"):
        Claim(rigor="R1", source="attested", status=Outcome.PASS, verified_by_assay=False)


def test_attestation_module_never_imports_claim():
    import assay.attestation as attestation_module

    assert not hasattr(attestation_module, "Claim"), (
        "assay.attestation must never import Claim -- attested evidence is "
        "structurally incapable of becoming a computed claim"
    )
