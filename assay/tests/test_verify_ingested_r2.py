"""B046 — the RAW verifier's independent statement about an INGESTED
``judgment.r2``.

**Why this module exists at all.** Before B046 the raw layer said nothing
whatsoever about an ingested document. Every R2 check in ``verify.py`` is
guarded either by an ``isinstance`` test on ``judgment.r2.operators`` or by
the resolved-language rule — and on an ingested document ``operators`` is
absent by contract (A-360), so those guards SKIP rather than pass. A skipped
check and a satisfied one are indistinguishable from a green bar, which is
exactly the shape of hole this project exists to close.

**Every test here is a MUTATED real verdict.** The base document is produced
by an actual run over the committed StrykerJS artifact (the same one
``test_runner_ingested_r2.py`` drives), then one field is broken. Each test
asserts a NAMED failure rather than merely a non-empty list: a checker that
fires with the wrong message is a checker that will be misread, and a test
that only asserts ``failures != []`` cannot tell one checker's output from
another's.

The base document itself is asserted clean first. Without that, every
assertion below could be passing because the document was broken to begin
with.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import GitRepo

from assay import runner
from assay.adapters.javascript import JavaScriptAdapter
from assay.errors import Outcome
from assay.verify import verify_document

from test_runner_ingested_r2 import (  # the same real-artifact harness
    PLACEHOLDER,
    _lane,
    _report_document,
    _seed_repo,
    _stage_report,
)


@pytest.fixture
def ingested_document(git_repo: GitRepo, tmp_path: Path) -> dict:
    document = _report_document()
    document["projectRoot"] = PLACEHOLDER
    staged = _stage_report(tmp_path, document)
    _seed_repo(git_repo, document)
    base = git_repo.git("rev-parse", "HEAD~1").strip()
    verdict = runner.run_lane(
        _lane(git_repo=git_repo, staged=staged, base=base),
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=JavaScriptAdapter(),
        assay_version="0.1.0",
    )
    return json.loads(json.dumps(verdict.to_dict()))


def _failures(document: dict) -> list[str]:
    return verify_document(document)


def _named(document: dict, fragment: str) -> None:
    failures = _failures(document)
    assert failures, "the mutated document was accepted -- the checker is vacuous"
    assert any(fragment in failure for failure in failures), failures


# --------------------------------------------------------------------------
# The control: the unmutated real document is clean
# --------------------------------------------------------------------------


def test_the_real_ingested_verdict_verifies_clean(ingested_document: dict):
    """Without this, every negative below could be passing because the base
    document was already broken."""
    assert _failures(ingested_document) == []


# --------------------------------------------------------------------------
# The four re-derivations, each broken one at a time
# --------------------------------------------------------------------------


def test_a_survived_uncovered_position_with_no_matching_survivor_is_caught(
    ingested_document: dict,
):
    """The forgeable claim this closes: a report asserting it surfaced
    untested lines that its own payload does not contain."""
    document = copy.deepcopy(ingested_document)
    document["judgment"]["r2"]["survived_uncovered"].append(
        {"path": "app/src/format.ts", "lineno": 9999}
    )
    _named(document, "is not a position the payload's own 'survived' bucket")


def test_a_survived_uncovered_position_matching_a_KILLED_mutant_is_caught(
    ingested_document: dict,
):
    """A ``NoCoverage`` mutant maps to ``survived`` and to nothing else, so a
    position that matches only a KILLED mutant would be laundering a kill into
    the worst-survivor list."""
    document = copy.deepcopy(ingested_document)
    killed = document["claims"]
    r2_claim = next(item for item in killed if item["rigor"] == "R2")
    survived = {
        (entry["path"], entry["lineno"])
        for entry in r2_claim["mutation"]["survived"]
    }
    killed_only = next(
        (entry["path"], entry["lineno"])
        for entry in r2_claim["mutation"]["killed"]
        if (entry["path"], entry["lineno"]) not in survived
    )
    document["judgment"]["r2"]["survived_uncovered"].append(
        {"path": killed_only[0], "lineno": killed_only[1]}
    )
    _named(document, "is not a position the payload's own 'survived' bucket")


def test_a_line_without_candidates_that_carries_a_mutant_is_caught(
    ingested_document: dict,
):
    """"No candidate here" and "a mutant starts here" cannot both be true of
    one line in one document."""
    document = copy.deepcopy(ingested_document)
    r2_claim = next(
        item for item in document["claims"] if item["rigor"] == "R2"
    )
    entry = r2_claim["mutation"]["survived"][0]
    document["judgment"]["r2"]["lines_without_candidates"].append(
        {"path": entry["path"], "lineno": entry["lineno"]}
    )
    _named(document, "but the R2 payload records a mutant starting on that")


def test_a_missing_producer_tool_is_caught(ingested_document: dict):
    document = copy.deepcopy(ingested_document)
    del document["judgment"]["r2"]["producer_tool"]
    _named(document, "with no producer_tool")


def test_an_integer_discarded_is_caught_with_the_v10_shape_NAMED(
    ingested_document: dict,
):
    """(B070, schema v11) The migration's own diagnostic. Through v10 this
    field was an integer count; the refusal says what it became and why, so a
    consumer hand-editing a document is not left to infer it."""
    document = copy.deepcopy(ingested_document)
    document["judgment"]["r2"]["discarded"] = 0
    _named(document, "must be an ARRAY of the mutants")


def test_the_A437_reproduction_now_REFUSES_by_name(ingested_document: dict):
    """**B070, and the inversion of the ruling this module used to record.**

    A-437 recorded, as a deliberately accepted gap, that
    ``judgment.r2.discarded = 9999`` on the real 109-mutant ingested document
    verified clean. The reason was a missing quantity rather than a missing
    check: as a bare count, a discarded mutant was outside the document it
    would have to be derived from, and every upper bound that caught the
    inflation (``discarded <= total``, ``<= candidate_count``) equally refused
    the honest high-discard report the field exists to surface — which is why
    DA-R26 rejected one (route 3).

    v11 lists the mutants instead, so the residual
    ``candidate_count - total`` is a real quantity the list must equal. The
    v11 spelling of A-437's forgery is a 9999-entry list, and it is now
    refused with a message naming BOTH numbers.

    The control that keeps this from being the rejected clamp is
    ``test_a_truthful_high_discard_document_is_ACCEPTED`` in
    ``test_runner_ingested_r2.py`` (and the frozen
    ``high-discard-r2-v11-template.json``): 40 discarded mutants beside 48
    attempted, accepted in full.
    """
    document = copy.deepcopy(ingested_document)
    payload = next(
        claim for claim in document["claims"] if claim["rigor"] == "R2"
    )["mutation"]
    assert document["judgment"]["r2"]["discarded"] == []
    assert payload["candidate_count"] == payload["total"]

    document["judgment"]["r2"]["discarded"] = [
        {
            "path": "app/src/format.ts",
            "lineno": 1 + index % 30,
            "start_byte": 100_000 + index * 8,
            "end_byte": 100_004 + index * 8,
            "replacement_sha256": f"{index:064x}",
            "operator": "stryker:Fabricated",
            "description": "a mutant this payload does not contain",
        }
        for index in range(9999)
    ]
    assert 9999 > payload["total"], (
        "the reproduction must inflate BEYOND the whole payload, or it is not "
        "the case A-437 recorded"
    )
    failures = _failures(document)
    assert any(
        "judgment.r2.discarded lists 9999 mutant(s)" in failure
        and "a residual of 0" in failure
        for failure in failures
    ), failures


def test_a_discarded_mutant_that_is_also_in_a_bucket_is_caught(
    ingested_document: dict,
):
    """Disjointness (B070). Without it the arithmetic alone could be satisfied
    by listing one mutant twice — once as caught, once as invalid."""
    document = copy.deepcopy(ingested_document)
    claim = next(item for item in document["claims"] if item["rigor"] == "R2")
    killed = copy.deepcopy(claim["mutation"]["killed"][0])
    document["judgment"]["r2"]["discarded"] = [killed]
    # Pay for the entry so the ARITHMETIC is satisfied and the disjointness
    # rule is the only thing left to catch it -- otherwise this test would
    # pass on the residual message and prove nothing about overlap.
    claim["mutation"]["candidate_count"] += 1
    _named(document, "which the R2 payload also records in one of its five buckets")


def test_a_discarded_mutants_line_may_not_be_reported_as_barren(
    ingested_document: dict,
):
    """The exact converse of ``lines_without_candidates``' own rule (B070):
    the tool DID produce a candidate there, it merely produced an invalid
    one."""
    document = copy.deepcopy(ingested_document)
    claim = next(item for item in document["claims"] if item["rigor"] == "R2")
    barren = document["judgment"]["r2"]["lines_without_candidates"][0]
    document["judgment"]["r2"]["discarded"] = [
        {
            "path": barren["path"],
            "lineno": barren["lineno"],
            "start_byte": 100_000,
            "end_byte": 100_004,
            "replacement_sha256": "b" * 64,
            "operator": "stryker:Fabricated",
            "description": "an invalid mutant on a line reported as barren",
        }
    ]
    claim["mutation"]["candidate_count"] += 1
    _named(document, "judgment.r2.discarded records a mutant starting on that exact line")


def test_an_out_of_order_discarded_list_is_caught_by_the_RAW_layer(
    ingested_document: dict,
):
    """The raw layer's own ordering witness, worded differently from the
    model's for the reason the three position lists already state: asserting
    only that the document is refused would count one witness twice."""
    document = copy.deepcopy(ingested_document)
    claim = next(item for item in document["claims"] if item["rigor"] == "R2")
    document["judgment"]["r2"]["discarded"] = [
        {
            "path": "app/src/format.ts",
            "lineno": 3,
            "start_byte": 200,
            "end_byte": 204,
            "replacement_sha256": "c" * 64,
            "operator": "stryker:Fabricated",
            "description": "the LATER identity, placed first",
        },
        {
            "path": "app/src/format.ts",
            "lineno": 2,
            "start_byte": 100,
            "end_byte": 104,
            "replacement_sha256": "b" * 64,
            "operator": "stryker:Fabricated",
            "description": "the EARLIER identity, placed second",
        },
    ]
    claim["mutation"]["candidate_count"] += 2
    _named(document, "judgment.r2.discarded must be strictly ascending")


def test_an_inflated_discarded_list_still_cannot_move_the_R2_status(
    ingested_document: dict,
):
    """DA-R23's sentence, unchanged by v11: a discarded mutant never enters
    the ``Mutation`` buckets, so the mutation score's denominator is
    unaffected by construction. What changed at v11 is that the forgery is now
    REFUSED as well — it was always powerless, and it is now also visible."""
    document = copy.deepcopy(ingested_document)
    claim = next(item for item in document["claims"] if item["rigor"] == "R2")
    payload = claim["mutation"]
    buckets = ("killed", "survived", "budget_exceeded", "equivalent")
    bucketed = sum(len(payload.get(name, [])) for name in buckets)

    # The structural half: the list is not IN the payload at all, and the
    # payload's own total is the bucket sum with nothing subtracted.
    assert "discarded" not in payload
    assert payload["total"] == bucketed

    # The behavioural half: `judge_mutation` re-derives the status from the
    # buckets (A-379), and it is the SAME status whatever this field holds.
    from assay.mutation import judge_mutation
    from assay.verdict import Mutation, MutantOutcome

    rebuilt = Mutation(
        candidate_count=payload["candidate_count"],
        total=payload["total"],
        **{
            name: tuple(
                MutantOutcome(**{k: v for k, v in entry.items()})
                for entry in payload.get(name, [])
            )
            for name in ("killed", "survived", "crashed", "budget_exceeded", "equivalent")
        },
    )
    baseline = SimpleNamespace(outcome=Outcome.PASS, reason_code=None)
    status, reason = judge_mutation(baseline, rebuilt, fail_under=100.0)
    inflated_status, inflated_reason = judge_mutation(
        baseline, rebuilt, fail_under=100.0, discarded=9999
    )
    assert (status, reason) == (inflated_status, inflated_reason)
    assert status.value == claim["status"]


def test_the_schema_says_what_discarded_still_does_NOT_verify():
    """The three-place discipline ``producer_tool`` carries, carried over to
    B070's own residual: v11 verifies the LISTED half, and the schema must say
    plainly that the UN-LISTED half — candidates a tool drops before reporting
    them at all — is still declared, so a consumer reading the contract alone
    cannot read a green bar as more than it is. DESIGN-GUIDE §11 and
    CONSUMERS' ingested-lane section carry the other two statements."""
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "src"
            / "assay"
            / "schemas"
            / "verdict.schema.json"
        ).read_text()
    )
    described = schema["$defs"]["judgment_r2"]["properties"]["discarded"][
        "description"
    ]
    assert "declared-not-verified" in described
    assert "un-listed half" in described.lower()
    assert "B070" in described
    tool = schema["$defs"]["mutation_producer_tool"]["description"]
    assert "NOT VERIFIED" in tool


def test_an_absent_survived_uncovered_is_caught(ingested_document: dict):
    document = copy.deepcopy(ingested_document)
    del document["judgment"]["r2"]["survived_uncovered"]
    _named(document, "is required on an ingested judgment")


def test_an_absent_lines_without_candidates_is_caught(ingested_document: dict):
    document = copy.deepcopy(ingested_document)
    del document["judgment"]["r2"]["lines_without_candidates"]
    _named(document, "lines_without_candidates is required on an ingested")


def test_an_ingested_judgment_with_no_payload_is_caught(ingested_document: dict):
    document = copy.deepcopy(ingested_document)
    r2_claim = next(
        item for item in document["claims"] if item["rigor"] == "R2"
    )
    del r2_claim["mutation"]
    _named(document, "the ingested facts beside it describe a payload")


# --------------------------------------------------------------------------
# The operator-namespace fork, BOTH directions
# --------------------------------------------------------------------------


def test_a_native_operator_on_an_ingested_document_is_caught(
    ingested_document: dict,
):
    """The half brief 3 flagged as still open: ``verify.py``'s own raw
    ``_check_resolved_language_owns_every_operator`` compared
    ``operator_language(...) != language`` over the payload, which refuses
    every ingested mutant (``operator_language("stryker:X")`` answers
    ``"stryker"``). Fixed as an exact MIRROR rather than as a skip — so the
    ingested branch is not merely silent, it refuses a native operator."""
    document = copy.deepcopy(ingested_document)
    r2_claim = next(
        item for item in document["claims"] if item["rigor"] == "R2"
    )
    r2_claim["mutation"]["survived"][0]["operator"] = "python:compare-swap"
    _named(document, "while judgment.r2.producer is 'ingested'")


def test_the_ingested_branch_is_reached_at_all(ingested_document: dict):
    """A guard against the whole fork being dead code: flip ``producer`` to
    ``native`` and the OTHER branch must fire on the same operators. If both
    branches were silent, the two tests above would prove nothing."""
    document = copy.deepcopy(ingested_document)
    document["judgment"]["r2"]["producer"] = "native"
    failures = _failures(document)
    assert any(
        "a run cannot apply a catalogue belonging to another language" in failure
        for failure in failures
    ), failures


# --------------------------------------------------------------------------
# `producer` is a CLOSED vocabulary at the raw layer (fix round 1)
#
# Both readers of the field did a bare `== "ingested"` string comparison, so
# every other spelling -- `"Ingested"`, `"INGESTED"`, `"ingsted"` -- routed
# silently to the native branch and skipped every ingested check. The schema
# layer catches the misspelling end to end, so this was never exploitable;
# it was a LAYER-INDEPENDENCE violation, and the property the raw layer exists
# to have is precisely that it does not depend on the schema layer being run.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["Ingested", "INGESTED", "ingsted", "", "native "])
def test_a_producer_outside_the_closed_set_is_NAMED(
    ingested_document: dict, spelling: str
):
    document = copy.deepcopy(ingested_document)
    document["judgment"]["r2"]["producer"] = spelling
    _named(document, "is not one of ['ingested', 'native']")


def test_a_misspelled_producer_does_NOT_route_to_the_native_rules(
    ingested_document: dict,
):
    """The defect itself, not merely the new message.

    This document's payload carries ``stryker:``-namespaced operators. Under
    the shipped code a misspelled producer fell through to the NATIVE branch,
    which then reported those operators as a language-catalogue violation --
    a real-looking failure about the wrong thing, while every ingested check
    stayed silent. An unrecognised producer must now take NEITHER branch: the
    only thing said about it is that it is unrecognised.
    """
    document = copy.deepcopy(ingested_document)
    document["judgment"]["r2"]["producer"] = "Ingested"
    failures = _failures(document)
    assert not any(
        "a run cannot apply a catalogue belonging to another language" in failure
        for failure in failures
    ), failures
    assert any("judgment.r2.producer" in failure for failure in failures), failures


def test_a_misspelled_producer_does_not_silently_pass_the_ingested_checks(
    ingested_document: dict,
):
    """The other half: break an ingested fact AND misspell the producer. The
    ingested re-derivations no longer run -- which is correct, since they are
    rules about a producer this document does not name -- so the vocabulary
    failure must be there to carry the document to a red bar on its own."""
    document = copy.deepcopy(ingested_document)
    document["judgment"]["r2"]["producer"] = "INGESTED"
    document["judgment"]["r2"]["discarded"] = 7
    _named(document, "is not one of ['ingested', 'native']")


# --------------------------------------------------------------------------
# Array ORDER at the raw layer (fix round 1)
#
# Draft 2020-12 cannot express array order, so three shipped v9 field
# descriptions say it "is checked by the model and the raw verifier". The
# model half was real; the raw half did not exist -- this module's only
# ordering check was `unsafe_symlink_omissions`'. Three v9 fields therefore
# promised two witnesses and had one.
#
# Each test below asserts the RAW checker's own wording, which is deliberately
# different from the model's ("strictly ascending ... is not before" vs
# "must be ascending ..., got"). Asserting merely that the document is refused
# would prove nothing: the model refuses it too, and that is the single
# witness these tests exist to stop being single.
# --------------------------------------------------------------------------


def test_an_out_of_order_survived_uncovered_is_caught_by_the_RAW_layer(
    ingested_document: dict,
):
    document = copy.deepcopy(ingested_document)
    positions = document["judgment"]["r2"]["survived_uncovered"]
    assert len(positions) >= 2, "the real fixture must carry enough to reorder"
    positions[0], positions[1] = positions[1], positions[0]
    _named(document, "judgment.r2.survived_uncovered must be strictly ascending")


def test_an_out_of_order_lines_without_candidates_is_caught_by_the_RAW_layer(
    ingested_document: dict,
):
    document = copy.deepcopy(ingested_document)
    positions = document["judgment"]["r2"]["lines_without_candidates"]
    assert len(positions) >= 2, "the real fixture must carry enough to reorder"
    positions[0], positions[1] = positions[1], positions[0]
    _named(
        document, "judgment.r2.lines_without_candidates must be strictly ascending"
    )


def test_a_DUPLICATE_position_is_caught_by_the_RAW_layer(ingested_document: dict):
    """Strictly ascending, not merely non-descending: a repeated position is
    the same line claimed twice, which is what A-381's deduplication to
    distinct positions exists to prevent."""
    document = copy.deepcopy(ingested_document)
    positions = document["judgment"]["r2"]["survived_uncovered"]
    positions.insert(1, copy.deepcopy(positions[0]))
    _named(document, "judgment.r2.survived_uncovered must be strictly ascending")


def test_an_out_of_order_link_paths_is_caught_by_the_RAW_layer(
    ingested_document: dict,
):
    """``snapshot_policy.link_paths`` carries the same promise and had the
    same gap. Checked BEFORE the ``selection`` fork (A-366): under
    ``selection = "repository"`` -- which this document declares -- the
    function returns early, so a check placed after the fork would have been
    dead for exactly the selection most lanes use."""
    document = copy.deepcopy(ingested_document)
    assert document["snapshot_policy"]["selection"] == "repository"
    document["snapshot_policy"]["link_paths"] = ["b/two", "a/one"]
    _named(
        document,
        "snapshot_policy.link_paths must be strictly ascending by UTF-8 bytes",
    )


def test_ascending_link_paths_are_accepted_by_the_RAW_layer(
    ingested_document: dict,
):
    """The control. Without it the test above would pass just as well against
    a checker that refused every ``link_paths`` list it was ever shown."""
    document = copy.deepcopy(ingested_document)
    document["snapshot_policy"]["link_paths"] = ["a/one", "b/two"]
    assert not any(
        "snapshot_policy.link_paths" in failure for failure in _failures(document)
    )
