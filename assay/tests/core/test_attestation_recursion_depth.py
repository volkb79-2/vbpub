"""B072 — ``parse_attestation`` on a pathologically deep, well-formed,
well-under-bound JSON document.

The identical gap `f0126b35` closed in ``adjudication.py``'s
``evaluate_provenance``, duplicated into a second module: ``json.loads`` is
CPython's recursive-descent parser, and at the real C-stack boundary it
raises ``RecursionError`` — a ``RuntimeError`` subclass, **not** a
``ValueError`` — which the original ``except (json.JSONDecodeError,
ValueError)`` clause did not catch.

Attestation documents are produced entirely OUTSIDE assay (the caller's own
harness, on the host, before the container starts), so this is not reachable
only by an adversary: a truncated-then-repeated write under disk pressure, a
buggy producer, or a shape assay has not seen yet can all reach this depth.
Uncaught, it crashed the whole ``assay run`` process instead of producing the
judged ``ERROR``/``UNREADABLE_ARTIFACT`` refusal a consumer can act on.

The sibling module for the original finding is
``test_adjudication_provenance_parse.py``
(``test_a_pathologically_deep_document_renders_unreadable_artifact_not_a_raise``);
this module mirrors it and then walks the SAME document out through the real
consumer path, because the acceptance criterion is what a consumer sees, not
what the bare function returns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assay import attestation
from assay.attestation import (
    MAX_ATTESTATION_BYTES,
    load_attestation_file,
    load_attested_evidence,
    parse_attestation,
)
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import EvidenceDeclaration

#: 100,000-deep nested array = 200,000 bytes, comfortably under
#: ``MAX_ATTESTATION_BYTES`` (1 MiB) — so the size bound
#: ``load_attestation_file`` already applies does NOT exclude a real document
#: at this depth; only the parser's own handling has to survive it.
_DEPTH = 100_000
_PATHOLOGICAL = "[" * _DEPTH + "]" * _DEPTH


def _remaining() -> float:
    return 60.0


def test_the_fixture_is_genuinely_inside_the_size_bound():
    """Guards the premise: if this ever grew past the bound, the tests below
    would be proving the BOUND's refusal, not the parser's."""
    assert len(_PATHOLOGICAL.encode("utf-8")) < MAX_ATTESTATION_BYTES


def test_a_pathologically_deep_document_is_unreadable_not_a_raise():
    with pytest.raises(AssayError) as excinfo:
        parse_attestation(_PATHOLOGICAL, source_name="repro")
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_it_is_a_RecursionError_that_is_being_caught(monkeypatch: pytest.MonkeyPatch):
    """Pins the WHY, not just the what: a future CPython that raised some
    other exception here would leave the fix silently inert, and the depth
    fixture alone could not tell the difference."""

    def raise_recursion_error(*args, **kwargs):
        raise RecursionError("injected: maximum recursion depth exceeded")

    monkeypatch.setattr(attestation.json, "loads", raise_recursion_error)
    with pytest.raises(AssayError) as excinfo:
        parse_attestation("{}", source_name="repro")
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert isinstance(excinfo.value.__cause__, RecursionError)


# --- the real consumer path ----------------------------------------------------


def test_it_is_unreadable_through_load_attestation_file(tmp_path: Path):
    (tmp_path / "attestations").mkdir()
    (tmp_path / "attestations" / "review.json").write_text(
        _PATHOLOGICAL, encoding="utf-8"
    )

    with pytest.raises(AssayError) as excinfo:
        load_attestation_file(tmp_path, attestation_dir="attestations", key="review")

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_it_becomes_an_evidence_result_through_load_attested_evidence(
    git_repo, tmp_path: Path
):
    """``load_attested_evidence`` catches ``AssayError`` and stages it as one
    evidence item's own refusal — a ``RecursionError`` is not an
    ``AssayError``, so before the fix it escaped this whole loader and, as
    the backlog measured, every caller above it up to ``cli.py``."""
    (tmp_path / "attestations").mkdir()
    (tmp_path / "attestations" / "review.json").write_text(
        _PATHOLOGICAL, encoding="utf-8"
    )

    results = load_attested_evidence(
        git_repo.path,
        head=git_repo.head(),
        declared=(EvidenceDeclaration(source="attested", key="review"),),
        project_root=tmp_path,
        attestation_dir="attestations",
        remaining=_remaining,
    )

    assert [item.key for item in results] == ["review"]
    assert results[0].status is Outcome.ERROR
    assert results[0].reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_a_well_formed_attestation_at_the_same_site_is_unaffected(tmp_path: Path):
    """The control: widening the ``except`` tuple must not change what a
    legible document does."""
    record = parse_attestation(
        json.dumps(
            {
                "producer": "adversarial-review-bot-v3",
                "attested_commit": "0" * 40,
                "reviewed_paths": ["src/a.py"],
            }
        ),
        source_name="ok",
    )
    assert record.producer == "adversarial-review-bot-v3"
    assert record.reviewed_paths == ("src/a.py",)
