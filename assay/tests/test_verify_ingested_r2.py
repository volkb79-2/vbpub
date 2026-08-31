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

import pytest
from conftest import GitRepo

from assay import runner
from assay.adapters.javascript import JavaScriptAdapter
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


def test_a_negative_discarded_count_is_caught(ingested_document: dict):
    document = copy.deepcopy(ingested_document)
    document["judgment"]["r2"]["discarded"] = -1
    _named(document, "cannot be negative")


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
    document["judgment"]["r2"]["discarded"] = -1
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
