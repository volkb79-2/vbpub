"""CR-03 (DR-13): the typed agent-result envelope and its fail-closed reader.

The acceptance criteria are a list of things that must FAIL, so this file is
mostly negatives, and each one is written to fail for the stated reason rather
than incidentally -- every rejection test starts from a record that is valid
except for the one field under test, so a rejection proves that field is
checked rather than proving the fixture is broken.

`_valid()` is that base record. If a test could pass because the fixture was
malformed in some other way, the test is not testing what it claims.

Determinism: pure functions over in-memory documents and tmp_path files. No
clock, no network, no subprocess, no process-global state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom import results
from nyxloom.results import (
    ADMISSIBLE_VERDICTS, AgentResult, DeclineReason, Evidence, RejectionCode,
    ResultIdentity, ResultKind, ResultRejected, Verdict,
)

TASK = "demo-P01-sample"
ATTEMPT = "att-0123456789ab"
RAW = b"the reviewer's raw stdout\n"


def _expect(**over) -> ResultIdentity:
    base = dict(task_id=TASK, attempt_id=ATTEMPT)
    base.update(over)
    return ResultIdentity(**base)


def _evidence(tmp_path: Path | None = None, name: str = "review.log") -> dict:
    if tmp_path is not None:
        (tmp_path / name).write_bytes(RAW)
    return {"path": name, "sha256": results.digest_bytes(RAW), "bytes": len(RAW)}


def _valid(tmp_path: Path | None = None, **over) -> dict:
    """A record that passes every check. Every negative below mutates exactly
    one field of this, so a rejection is attributable."""
    doc = {
        "schema_version": results.SCHEMA_VERSION,
        "kind": "review_independent",
        "verdict": "approved",
        "identity": {"task_id": TASK, "attempt_id": ATTEMPT},
        "evidence": _evidence(tmp_path),
        "summary": "Oracles O1-O3 pass; scope respected.",
    }
    doc.update(over)
    return doc


def _load(doc, tmp_path=None, *, expect=None, kind=ResultKind.REVIEW_INDEPENDENT):
    return results.load_result(
        json.dumps(doc) if isinstance(doc, dict) else doc,
        expect=expect if expect is not None else _expect(),
        kind=kind, evidence_root=tmp_path)


def _rejects(doc, tmp_path=None, **kw) -> RejectionCode:
    with pytest.raises(ResultRejected) as exc:
        _load(doc, tmp_path, **kw)
    return exc.value.code


# ---------------------------------------------------------------------------
# the base record really is valid


def test_a_well_formed_result_loads_with_every_field_preserved(tmp_path):
    """The positive control. Every negative below is only meaningful because
    this passes: a fixture that failed for its own reasons would make all of
    them vacuous."""
    result = _load(_valid(tmp_path), tmp_path)

    assert result.kind is ResultKind.REVIEW_INDEPENDENT
    assert result.verdict is Verdict.APPROVED
    assert result.identity.task_id == TASK
    assert result.identity.attempt_id == ATTEMPT
    assert result.evidence.sha256 == results.digest_bytes(RAW)
    assert result.decline_reason is None
    assert "Oracles" in result.summary


# ---------------------------------------------------------------------------
# acceptance: stale task, wrong attempt, wrong revision, wrong digest


class TestIdentityBinding:
    """"Stale task, wrong attempt, wrong SHA/diff, wrong workflow digest ...
    all fail closed."

    This is the heuristic that `_parse_review_verdict` grew over three
    incidents, replaced by a comparison. A verdict for another attempt is now
    inert because it does not match, not because a rule was written for the
    shape of that particular accident."""

    def test_a_result_about_another_task_is_stale(self, tmp_path):
        doc = _valid(tmp_path, identity={"task_id": "other-P99", "attempt_id": ATTEMPT})
        assert _rejects(doc, tmp_path) is RejectionCode.STALE_TASK

    def test_a_verdict_left_by_a_previous_attempt_is_refused(self, tmp_path):
        """The P59b incident, exactly: attempt #1's REJECTED still on the
        branch when attempt #2 finishes. It re-implemented work that was
        fine. Now it simply does not match."""
        doc = _valid(tmp_path, identity={"task_id": TASK, "attempt_id": "att-ffffffffffff"})
        assert _rejects(doc, tmp_path) is RejectionCode.WRONG_ATTEMPT

    def test_a_result_computed_over_a_different_head_is_refused(self, tmp_path):
        doc = _valid(tmp_path, identity={
            "task_id": TASK, "attempt_id": ATTEMPT, "head_commit": "deadbeef"})
        code = _rejects(doc, tmp_path, expect=_expect(head_commit="cafebabe"))
        assert code is RejectionCode.WRONG_REVISION

    def test_a_result_computed_over_a_different_base_is_refused(self, tmp_path):
        doc = _valid(tmp_path, identity={
            "task_id": TASK, "attempt_id": ATTEMPT, "base_commit": "1111111"})
        code = _rejects(doc, tmp_path, expect=_expect(base_commit="2222222"))
        assert code is RejectionCode.WRONG_REVISION

    def test_a_result_produced_under_a_different_workflow_is_refused(self, tmp_path):
        doc = _valid(tmp_path, identity={
            "task_id": TASK, "attempt_id": ATTEMPT, "workflow_digest": "wf-old"})
        code = _rejects(doc, tmp_path, expect=_expect(workflow_digest="wf-new"))
        assert code is RejectionCode.WRONG_WORKFLOW_DIGEST

    def test_a_result_produced_under_a_different_prompt_is_refused(self, tmp_path):
        doc = _valid(tmp_path, identity={
            "task_id": TASK, "attempt_id": ATTEMPT, "prompt_digest": "p-old"})
        code = _rejects(doc, tmp_path, expect=_expect(prompt_digest="p-new"))
        assert code is RejectionCode.WRONG_WORKFLOW_DIGEST

    def test_an_expectation_the_caller_does_not_hold_is_not_invented(self, tmp_path):
        """The daemon does not always know the head commit before reading the
        result. An unheld expectation must not become a check that passes only
        when both sides are empty -- that would refuse every real result the
        moment an agent started stamping a field the daemon had not learned
        yet. What must never happen is the reverse: an expectation the caller
        DOES hold being skipped."""
        doc = _valid(tmp_path, identity={
            "task_id": TASK, "attempt_id": ATTEMPT, "head_commit": "abc1234"})

        assert _load(doc, tmp_path).identity.head_commit == "abc1234"

        code = _rejects(doc, tmp_path, expect=_expect(head_commit="different"))
        assert code is RejectionCode.WRONG_REVISION


# ---------------------------------------------------------------------------
# acceptance: unknown outcome


class TestVerdictDiscrimination:

    def test_a_verdict_outside_the_vocabulary_is_refused(self, tmp_path):
        assert _rejects(_valid(tmp_path, verdict="looks-fine-to-me"),
                        tmp_path) is RejectionCode.UNKNOWN_OUTCOME

    def test_a_verdict_outside_its_kinds_admissible_set_is_refused(self, tmp_path):
        """The discrimination. `approved` is a real verdict, and an
        implementation claiming it is either a confused agent or a
        mislabelled artifact -- both reasons to stop rather than to guess
        which was meant. Without the per-kind table this record would load
        and an implementation would carry a review's authority."""
        doc = _valid(tmp_path, kind="implement", verdict="approved")
        code = _rejects(doc, tmp_path, kind=ResultKind.IMPLEMENT)
        assert code is RejectionCode.UNKNOWN_OUTCOME

    def test_a_gate_cannot_decline(self, tmp_path):
        """A gate is a program. It passed, it did not, or it could not run --
        it has no capability judgement to offer, and letting one declare a
        decline would put a decline in the promotion path that no agent
        made."""
        doc = _valid(tmp_path, kind="gate", verdict="declined",
                     decline_reason="context-exceeded")
        assert _rejects(doc, tmp_path, kind=ResultKind.GATE) is RejectionCode.UNKNOWN_OUTCOME

    def test_reading_a_result_of_the_wrong_kind_is_refused(self, tmp_path):
        """Asking for a review and being handed an implementation must fail
        rather than succeed on the fields they happen to share."""
        doc = _valid(tmp_path, kind="implement", verdict="done")
        assert _rejects(doc, tmp_path) is RejectionCode.KIND_MISMATCH

    def test_an_unknown_kind_is_refused(self, tmp_path):
        assert _rejects(_valid(tmp_path, kind="vibes"),
                        tmp_path) is RejectionCode.KIND_MISMATCH

    @pytest.mark.parametrize("kind", list(ResultKind), ids=lambda k: k.value)
    def test_every_kind_declares_a_non_empty_admissible_set(self, kind):
        """A kind with no admissible verdicts could never produce a loadable
        result, so it would be dead vocabulary that still passed review."""
        assert ADMISSIBLE_VERDICTS[kind], kind

    def test_every_verdict_is_admissible_somewhere(self):
        """The mirror: a verdict no kind may use is a word in the schema that
        nothing can ever emit."""
        used = set().union(*ADMISSIBLE_VERDICTS.values())
        assert set(Verdict) == used, f"verdicts no kind may use: {set(Verdict) - used}"


# ---------------------------------------------------------------------------
# acceptance: a valid decline is exactly one typed result, never a failure


class TestCapabilityDecline:
    """"A valid capability decline emits exactly one typed result and is never
    interpreted as contract/environment/provider failure."

    The two facts are different -- "this is beyond me" routes to promotion,
    "I crashed" routes to retry -- and conflating them is what turns a cheap
    band into a toll booth or a silent quality drop."""

    def test_a_valid_decline_loads_as_declined_with_its_reason(self, tmp_path):
        doc = _valid(tmp_path, verdict="declined",
                     decline_reason="contract-referent-missing")
        result = _load(doc, tmp_path)

        assert result.verdict is Verdict.DECLINED
        assert result.decline_reason is DeclineReason.CONTRACT_REFERENT_MISSING
        # ...and it is NOT any of the failure shapes.
        assert result.verdict is not Verdict.FAILED
        assert result.verdict is not Verdict.BLOCKED

    def test_a_decline_naming_no_reason_is_refused(self, tmp_path):
        assert _rejects(_valid(tmp_path, verdict="declined"),
                        tmp_path) is RejectionCode.MALFORMED_DECLINE

    def test_a_decline_naming_a_free_text_reason_is_refused(self, tmp_path):
        """Free text cannot be corroborated by an independent fact, so it
        cannot be told from a lazy decline -- which is the whole reason the
        vocabulary is closed."""
        doc = _valid(tmp_path, verdict="declined",
                     decline_reason="this one looked really hard")
        assert _rejects(doc, tmp_path) is RejectionCode.MALFORMED_DECLINE

    def test_a_decline_reason_on_a_non_declined_result_is_refused(self, tmp_path):
        """A mislabelled record. The safe reading is that the agent meant to
        decline and the verdict is wrong -- exactly the ambiguity that must
        not be resolved by guessing, because one branch merges and the other
        does not."""
        doc = _valid(tmp_path, verdict="approved", decline_reason="context-exceeded")
        assert _rejects(doc, tmp_path) is RejectionCode.MALFORMED_DECLINE

    def test_a_decline_is_refused_rather_than_downgraded(self, tmp_path):
        """A malformed decline must never load AS a failure. If it did, the
        promotion path would silently become the retry path and a route that
        declines sloppily would look like a route that crashes."""
        doc = _valid(tmp_path, verdict="declined", decline_reason="nonsense")
        with pytest.raises(ResultRejected) as exc:
            _load(doc, tmp_path)
        assert exc.value.code is not RejectionCode.UNKNOWN_OUTCOME
        assert "declined" not in str(exc.value).replace("decline_reason", "")


# ---------------------------------------------------------------------------
# acceptance: missing evidence


class TestEvidenceBinding:

    def test_evidence_that_does_not_exist_is_refused(self, tmp_path):
        doc = _valid(tmp_path)
        (tmp_path / "review.log").unlink()
        assert _rejects(doc, tmp_path) is RejectionCode.MISSING_EVIDENCE

    def test_evidence_edited_after_the_fact_is_refused(self, tmp_path):
        """Evidence that can be changed afterwards does not support the
        judgement it accompanies. The digest is what makes the raw output
        worth persisting at all."""
        doc = _valid(tmp_path)
        (tmp_path / "review.log").write_bytes(RAW + b"...and one more thing\n")
        assert _rejects(doc, tmp_path) is RejectionCode.EVIDENCE_DIGEST_MISMATCH

    def test_a_record_with_no_evidence_block_is_refused(self, tmp_path):
        doc = _valid(tmp_path)
        del doc["evidence"]
        assert _rejects(doc, tmp_path) is RejectionCode.SCHEMA_VIOLATION

    def test_a_digest_that_is_not_a_sha256_is_refused_by_shape(self, tmp_path):
        doc = _valid(tmp_path)
        doc["evidence"]["sha256"] = "probably-fine"
        assert _rejects(doc, tmp_path) is RejectionCode.SCHEMA_VIOLATION

    def test_the_on_disk_check_is_skipped_only_when_there_is_no_root(self, tmp_path):
        """The wrapper validates a record before it has persisted the file.
        That is the ONLY caller passing no root, and it decides nothing --
        every caller that decides something passes one, so this relaxation
        cannot reach an authority site."""
        doc = _valid(tmp_path)
        (tmp_path / "review.log").unlink()

        assert results.load_result(
            json.dumps(doc), expect=_expect(),
            kind=ResultKind.REVIEW_INDEPENDENT, evidence_root=None).verdict is Verdict.APPROVED

        assert _rejects(doc, tmp_path) is RejectionCode.MISSING_EVIDENCE


# ---------------------------------------------------------------------------
# versioning and document shape


class TestVersionAndShape:

    def test_a_future_version_is_refused_as_a_version_problem(self, tmp_path):
        """Not as a schema violation. A record from a newer writer may fail
        this reader's schema only because the reader is old, and reporting
        that as a schema violation sends an operator to fix a document that
        is correct."""
        doc = _valid(tmp_path, schema_version=results.SCHEMA_VERSION + 1)
        assert _rejects(doc, tmp_path) is RejectionCode.UNSUPPORTED_VERSION

    def test_a_missing_version_is_refused(self, tmp_path):
        doc = _valid(tmp_path)
        del doc["schema_version"]
        assert _rejects(doc, tmp_path) is RejectionCode.UNSUPPORTED_VERSION

    def test_text_that_is_not_json_is_refused(self, tmp_path):
        assert _rejects("VERDICT: APPROVED\n", tmp_path) is RejectionCode.MALFORMED_DOCUMENT

    def test_a_json_document_that_is_not_an_object_is_refused(self, tmp_path):
        assert _rejects("[1, 2, 3]", tmp_path) is RejectionCode.MALFORMED_DOCUMENT

    def test_unknown_top_level_fields_are_refused(self, tmp_path):
        """Closed by `additionalProperties: false`, so a field an agent
        invented cannot be silently ignored -- an ignored field is one the
        agent believed was being read."""
        doc = _valid(tmp_path, verdict_confidence="high")
        assert _rejects(doc, tmp_path) is RejectionCode.SCHEMA_VIOLATION

    def test_details_stay_open_because_they_carry_no_authority(self, tmp_path):
        """The one deliberately-open field. A new detail must not require a
        schema bump, and it is safe precisely because nothing routes on it."""
        doc = _valid(tmp_path, details={"reject_class": "fixable", "findings": 3})
        assert _load(doc, tmp_path).details["reject_class"] == "fixable"


# ---------------------------------------------------------------------------
# enum/schema consistency -- the generated-consistency check


class TestSchemaAndCodeAgree:
    """The two copies of every vocabulary must not drift.

    A value added to the Python enum but not the schema is refused at
    runtime by a schema nobody remembered to update; the reverse is a value
    the schema advertises and the reader cannot parse. Both look like agent
    bugs and are not."""

    def _schema_enum(self, *path) -> set[str]:
        node = results._schema()["properties"]
        for key in path:
            node = node[key]
        return set(node["enum"])

    def test_kinds_agree(self):
        assert self._schema_enum("kind") == {k.value for k in ResultKind}

    def test_verdicts_agree(self):
        assert self._schema_enum("verdict") == {v.value for v in Verdict}

    def test_decline_reasons_agree(self):
        assert self._schema_enum("decline_reason") == {r.value for r in DeclineReason}

    def test_every_kind_has_an_admissibility_entry(self):
        """A kind the schema accepts but the table does not cover would raise
        KeyError inside the reader -- a crash where a refusal was meant."""
        assert set(ADMISSIBLE_VERDICTS) == set(ResultKind)

    def test_rejection_codes_are_unique_values(self):
        values = [c.value for c in RejectionCode]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# round-trip and rendering


class TestRoundTripAndRendering:

    def test_a_result_serializes_to_something_that_loads_back_identically(self, tmp_path):
        original = _load(_valid(tmp_path, details={"oracles": ["O1"]}), tmp_path)
        reloaded = _load(json.loads(original.to_json()), tmp_path)
        assert reloaded == original

    def test_serialization_is_byte_stable(self, tmp_path):
        """This document is committed to a branch and read by humans in a
        diff. A serializer whose key order varied would make every rewrite
        look like a change."""
        result = _load(_valid(tmp_path), tmp_path)
        assert result.to_json() == result.to_json()
        assert json.loads(result.to_json())["kind"] == "review_independent"

    def test_a_declined_result_round_trips_its_reason(self, tmp_path):
        doc = _valid(tmp_path, verdict="declined", decline_reason="context-exceeded")
        result = _load(doc, tmp_path)
        assert _load(json.loads(result.to_json()), tmp_path).decline_reason is (
            DeclineReason.CONTEXT_EXCEEDED)

    def test_the_markdown_rendering_names_the_verdict_task_and_evidence(self, tmp_path):
        rendered = results.render_markdown(_load(_valid(tmp_path), tmp_path))
        assert "APPROVED" in rendered
        assert TASK in rendered
        assert ATTEMPT in rendered
        assert "review.log" in rendered

    def test_a_declined_rendering_says_why(self, tmp_path):
        doc = _valid(tmp_path, verdict="declined", decline_reason="tool-unavailable")
        rendered = results.render_markdown(_load(doc, tmp_path))
        assert "tool-unavailable" in rendered

    def test_rendering_a_result_with_no_summary_still_produces_a_document(self, tmp_path):
        doc = _valid(tmp_path)
        del doc["summary"]
        rendered = results.render_markdown(_load(doc, tmp_path))
        assert rendered.startswith("# review_independent result: APPROVED")

    def test_optional_identity_fields_appear_only_when_present(self, tmp_path):
        bare = results.render_markdown(_load(_valid(tmp_path), tmp_path))
        assert "- base:" not in bare and "- head:" not in bare

        doc = _valid(tmp_path, identity={
            "task_id": TASK, "attempt_id": ATTEMPT,
            "base_commit": "1111111", "head_commit": "2222222"})
        full = results.render_markdown(_load(doc, tmp_path))
        assert "- base: `1111111`" in full and "- head: `2222222`" in full


# ---------------------------------------------------------------------------
# the constructed-in-code path (the wrapper's side)


def test_a_result_built_in_code_serializes_to_a_loadable_document(tmp_path):
    """The wrapper constructs rather than parses. Its output must satisfy the
    same reader the daemon uses -- if the two could disagree, every attempt
    would be a coin flip resolved after the work was already done."""
    (tmp_path / "impl.log").write_bytes(RAW)
    built = AgentResult(
        kind=ResultKind.IMPLEMENT,
        verdict=Verdict.DONE,
        identity=ResultIdentity(task_id=TASK, attempt_id=ATTEMPT, head_commit="abc1234"),
        evidence=Evidence(path="impl.log", sha256=results.digest_bytes(RAW),
                          bytes_len=len(RAW)),
        summary="Implemented the bounded change.",
    )

    reloaded = results.load_result(
        built.to_json(), expect=_expect(head_commit="abc1234"),
        kind=ResultKind.IMPLEMENT, evidence_root=tmp_path)

    assert reloaded.verdict is Verdict.DONE
    assert reloaded.identity.head_commit == "abc1234"
    assert reloaded.evidence.bytes_len == len(RAW)


# ---------------------------------------------------------------------------
# what happens if the two vocabularies DO drift


class TestDriftFailsClosedRatherThanCrashing:
    """`TestSchemaAndCodeAgree` prevents the schema and the enums from
    drifting. These prove what happens if that prevention ever fails.

    The distinction is not academic. The schema runs first, so in a drifted
    build it would ADMIT a value the reader cannot parse -- and an unhandled
    `ValueError` out of `ResultKind(...)` would surface as a crash in the
    reconcile pass rather than as a refusal of one result. A crash takes the
    whole project down; a refusal takes one attempt down. The reader keeps
    its own enum guards for exactly this, and they are only reachable here.
    """

    def _permissive_schema(self, monkeypatch, field: str, extra: str) -> None:
        """Widen ONE enum in the schema, leaving the Python enums alone --
        the drifted build, simulated at the one seam that can produce it."""
        real = results._schema()
        widened = json.loads(json.dumps(real))
        widened["properties"][field]["enum"].append(extra)
        # The decline conditional would reject the widened verdict for its own
        # reasons; drop it so the test isolates the enum drift under test.
        widened.pop("allOf", None)
        monkeypatch.setattr(results, "_schema", lambda: widened)

    def test_a_kind_the_schema_admits_and_the_reader_cannot_parse_is_refused(
            self, tmp_path, monkeypatch):
        self._permissive_schema(monkeypatch, "kind", "telepathy")
        doc = _valid(tmp_path, kind="telepathy")
        assert _rejects(doc, tmp_path) is RejectionCode.KIND_MISMATCH

    def test_a_verdict_the_schema_admits_and_the_reader_cannot_parse_is_refused(
            self, tmp_path, monkeypatch):
        self._permissive_schema(monkeypatch, "verdict", "probably")
        doc = _valid(tmp_path, verdict="probably")
        assert _rejects(doc, tmp_path) is RejectionCode.UNKNOWN_OUTCOME

    def test_a_decline_reason_the_schema_admits_and_the_reader_cannot_parse_is_refused(
            self, tmp_path, monkeypatch):
        self._permissive_schema(monkeypatch, "decline_reason", "mercury-retrograde")
        doc = _valid(tmp_path, verdict="declined", decline_reason="mercury-retrograde")
        assert _rejects(doc, tmp_path) is RejectionCode.MALFORMED_DECLINE

    def test_a_reason_on_a_non_declined_result_is_refused_by_the_reader_too(
            self, tmp_path, monkeypatch):
        """The schema's conditional normally catches this. With the
        conditional dropped, the reader's own check is what refuses -- so the
        rule holds even in a build whose schema forgot it."""
        self._permissive_schema(monkeypatch, "decline_reason", "unused")
        doc = _valid(tmp_path, verdict="approved", decline_reason="context-exceeded")
        assert _rejects(doc, tmp_path) is RejectionCode.MALFORMED_DECLINE


def test_an_already_decoded_document_is_accepted_without_a_round_trip(tmp_path):
    """The wrapper builds a dict and validates it before writing. Forcing it
    through `json.dumps` first would mean the thing validated and the thing
    written were only equal by construction -- which is the assumption the
    validation exists to remove."""
    doc = _valid(tmp_path)

    result = results.load_result(
        doc, expect=_expect(), kind=ResultKind.REVIEW_INDEPENDENT,
        evidence_root=tmp_path)

    assert result.verdict is Verdict.APPROVED
