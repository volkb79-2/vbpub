"""CR-03 (DR-13): assembling a typed result from a judgement plus the facts.

`test_results.py` owns the envelope and its reader. This file owns the SEAM:
the agent-authored judgement, the wrapper that binds it to what the
repository actually says, and what the daemon does with each verdict it can
come back with.

The seam is where the interesting failures live. An agent can write nothing,
write something unreadable, or write a judgement about the wrong attempt --
and none of those may become an approval, a crash, or an absence that looks
like a different absence.

Determinism: real git repos in tmp_path, no clock, no sleeps, no network. The
one subprocess (`git`) is the thing under test in `_head_commit`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from nyxloom import daemon, paths, results, storage, testing, wrapper
from nyxloom.results import DeclineReason, RejectionCode, ResultKind, Verdict

TASK = "demo-P01-sample"
ATTEMPT = "att-0123456789ab"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        capture_output=True, text=True, check=True)


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    return root


def _judgement(**over) -> dict:
    doc = {
        "schema_version": results.SCHEMA_VERSION,
        "kind": "review_independent",
        "verdict": "approved",
        "task_id": TASK,
        "attempt_id": ATTEMPT,
        "summary": "oracles pass",
    }
    doc.update(over)
    return doc


def _load(doc, **kw):
    kw.setdefault("task_id", TASK)
    kw.setdefault("attempt_id", ATTEMPT)
    kw.setdefault("kind", ResultKind.REVIEW_INDEPENDENT)
    return results.load_judgement(json.dumps(doc), **kw)


def _rejects(doc, **kw) -> RejectionCode:
    with pytest.raises(results.ResultRejected) as exc:
        _load(doc, **kw)
    return exc.value.code


# ---------------------------------------------------------------------------
# the agent-authored half


class TestJudgement:
    """What an agent may write, and what it may not.

    The judgement schema deliberately cannot express evidence, commits or
    digests: those are mechanically knowable, so the wrapper stamps them and
    an agent cannot claim a fact the repository disagrees with."""

    def test_a_well_formed_judgement_loads(self):
        j = _load(_judgement())
        assert j.kind is ResultKind.REVIEW_INDEPENDENT
        assert j.verdict is Verdict.APPROVED
        assert j.task_id == TASK and j.attempt_id == ATTEMPT
        assert j.summary == "oracles pass"
        assert j.decline_reason is None

    def test_a_judgement_from_a_previous_attempt_is_refused(self):
        """The P59b staleness case, as a comparison. A judgement left in the
        worktree by an earlier attempt names THAT attempt, so it is inert."""
        assert _rejects(_judgement(attempt_id="att-earlier")) is RejectionCode.WRONG_ATTEMPT

    def test_a_judgement_about_another_task_is_refused(self):
        assert _rejects(_judgement(task_id="other-P99")) is RejectionCode.STALE_TASK

    def test_a_judgement_of_the_wrong_kind_is_refused(self):
        assert _rejects(_judgement(kind="implement", verdict="done")) is (
            RejectionCode.KIND_MISMATCH)

    def test_a_verdict_outside_the_kinds_admissible_set_is_refused(self):
        assert _rejects(_judgement(kind="gate", verdict="approved"),
                        kind=ResultKind.GATE) is RejectionCode.UNKNOWN_OUTCOME

    def test_an_unknown_verdict_is_refused(self):
        assert _rejects(_judgement(verdict="looks-ok")) is RejectionCode.UNKNOWN_OUTCOME

    def test_a_future_schema_version_is_refused_as_a_version_problem(self):
        assert _rejects(_judgement(schema_version=99)) is RejectionCode.UNSUPPORTED_VERSION

    def test_text_that_is_not_json_is_refused(self):
        with pytest.raises(results.ResultRejected) as exc:
            results.load_judgement("VERDICT: APPROVED\n", task_id=TASK,
                                    attempt_id=ATTEMPT,
                                    kind=ResultKind.REVIEW_INDEPENDENT)
        assert exc.value.code is RejectionCode.MALFORMED_DOCUMENT

    def test_a_json_document_that_is_not_an_object_is_refused(self):
        with pytest.raises(results.ResultRejected) as exc:
            results.load_judgement("[]", task_id=TASK, attempt_id=ATTEMPT,
                                    kind=ResultKind.REVIEW_INDEPENDENT)
        assert exc.value.code is RejectionCode.MALFORMED_DOCUMENT

    def test_a_judgement_cannot_claim_evidence_or_a_commit(self):
        """The separation that makes the wrapper's stamp meaningful: if an
        agent could author these, it could bind a verdict to a tree it never
        reviewed."""
        assert _rejects(_judgement(evidence={"path": "x", "sha256": "0" * 64,
                                              "bytes": 1})) is RejectionCode.SCHEMA_VIOLATION
        assert _rejects(_judgement(head_commit="deadbeef")) is RejectionCode.SCHEMA_VIOLATION

    def test_a_valid_decline_carries_its_reason(self):
        j = _load(_judgement(verdict="declined", decline_reason="context-exceeded"))
        assert j.verdict is Verdict.DECLINED
        assert j.decline_reason is DeclineReason.CONTEXT_EXCEEDED

    def test_a_decline_with_no_reason_is_refused(self):
        assert _rejects(_judgement(verdict="declined")) is RejectionCode.MALFORMED_DECLINE

    def test_a_reason_without_a_decline_is_refused(self):
        assert _rejects(_judgement(decline_reason="context-exceeded")) is (
            RejectionCode.MALFORMED_DECLINE)


class TestBinding:

    def test_binding_computes_the_digest_from_the_bytes_not_the_claim(self):
        """A self-reported digest would make the evidence binding a statement
        of intent rather than a check."""
        j = _load(_judgement())
        raw = b"the reviewer's actual output\n"

        record = results.bind_evidence(j, evidence_path="attempt.log",
                                        evidence_bytes=raw, head_commit="abc1234")

        assert record.evidence.sha256 == results.digest_bytes(raw)
        assert record.evidence.bytes_len == len(raw)
        assert record.identity.head_commit == "abc1234"
        assert record.identity.task_id == TASK
        assert record.verdict is Verdict.APPROVED

    def test_binding_carries_the_decline_reason_through(self):
        j = _load(_judgement(verdict="declined", decline_reason="tool-unavailable"))
        record = results.bind_evidence(j, evidence_path="a.log", evidence_bytes=b"x")
        assert record.decline_reason is DeclineReason.TOOL_UNAVAILABLE

    def test_a_failed_result_is_never_a_decline(self):
        """A decline is a claim about capability that only the agent can
        make. Manufacturing one for a leg that simply died would put a
        promotion in the ladder that nobody asked for."""
        record = results.failed_result(
            ResultKind.REVIEW_INDEPENDENT, task_id=TASK, attempt_id=ATTEMPT,
            evidence_path="a.log", evidence_bytes=b"boom\n", detail="no judgement")

        assert record.verdict is Verdict.FAILED
        assert record.decline_reason is None
        assert "no judgement" in record.summary


# ---------------------------------------------------------------------------
# the wrapper's assembly


def _spec(root: Path, attempt_dir: Path, **over) -> wrapper.WrapperSpec:
    kw = dict(
        project="demo", task_id=TASK, attempt_id=ATTEMPT, argv=["true"],
        cwd=str(root), log_path=str(attempt_dir / "attempt.log"),
        receipt_path=str(attempt_dir / "receipt.json"), attempt_dir=str(attempt_dir),
        route_def={}, result_kind=ResultKind.REVIEW_INDEPENDENT.value,
    )
    kw.update(over)
    return wrapper.WrapperSpec(**kw)


def _written(attempt_dir: Path, task_id: str = TASK) -> dict:
    return json.loads((attempt_dir / results.result_filename(task_id)).read_text())


class TestWrapperAssembly:

    def test_a_readable_judgement_becomes_an_evidence_bound_result(self, tmp_path):
        root = _repo(tmp_path)
        attempt_dir = tmp_path / "att"
        attempt_dir.mkdir()
        (root / results.judgement_filename(TASK)).write_text(json.dumps(_judgement()))

        wrapper._write_typed_results(_spec(root, attempt_dir), "agent stdout\n")

        doc = _written(attempt_dir)
        assert doc["verdict"] == "approved"
        assert doc["identity"]["attempt_id"] == ATTEMPT
        assert doc["evidence"]["sha256"] == results.digest_bytes(b"agent stdout\n")
        # the head commit came from git, not from the agent
        assert len(doc["identity"]["head_commit"]) == 40

    def test_a_missing_judgement_becomes_a_failed_result_not_an_absence(self, tmp_path):
        """An absent file and a leg that answered are repaired differently, so
        the absence is recorded rather than left implicit -- otherwise the
        daemon cannot tell "the reviewer never answered" from "no review was
        ever dispatched"."""
        root = _repo(tmp_path)
        attempt_dir = tmp_path / "att"
        attempt_dir.mkdir()

        wrapper._write_typed_results(_spec(root, attempt_dir), "crashed\n")

        doc = _written(attempt_dir)
        assert doc["verdict"] == "failed"
        assert results.judgement_filename(TASK) in doc["summary"]

    def test_an_unreadable_judgement_becomes_a_failed_result_naming_the_code(
            self, tmp_path):
        root = _repo(tmp_path)
        attempt_dir = tmp_path / "att"
        attempt_dir.mkdir()
        (root / results.judgement_filename(TASK)).write_text("not json at all")

        wrapper._write_typed_results(_spec(root, attempt_dir), "log\n")

        doc = _written(attempt_dir)
        assert doc["verdict"] == "failed"
        assert RejectionCode.MALFORMED_DOCUMENT.value in doc["summary"]

    def test_a_judgement_bound_to_another_attempt_becomes_failed_not_approved(
            self, tmp_path):
        """The sharpest case: a real, well-formed APPROVED judgement left by a
        previous attempt. Assembling it as approved would merge unreviewed
        work."""
        root = _repo(tmp_path)
        attempt_dir = tmp_path / "att"
        attempt_dir.mkdir()
        (root / results.judgement_filename(TASK)).write_text(
            json.dumps(_judgement(attempt_id="att-previous")))

        wrapper._write_typed_results(_spec(root, attempt_dir), "log\n")

        doc = _written(attempt_dir)
        assert doc["verdict"] == "failed"
        assert RejectionCode.WRONG_ATTEMPT.value in doc["summary"]

    def test_a_wave_writes_one_result_per_member(self, tmp_path):
        """One attempt, several judged tasks. Per-task documents mean a
        member whose judgement is malformed cannot contaminate a sibling's
        verdict."""
        root = _repo(tmp_path)
        attempt_dir = tmp_path / "att"
        attempt_dir.mkdir()
        (root / results.judgement_filename("t-a")).write_text(
            json.dumps(_judgement(task_id="t-a")))
        (root / results.judgement_filename("t-b")).write_text("garbage")

        wrapper._write_typed_results(
            _spec(root, attempt_dir, result_tasks=["t-a", "t-b"]), "log\n")

        assert _written(attempt_dir, "t-a")["verdict"] == "approved"
        assert _written(attempt_dir, "t-b")["verdict"] == "failed"

    def test_a_leg_with_no_declared_kind_writes_nothing(self, tmp_path):
        """Empty means "this leg has no agent judgement to collect". Writing
        an empty document instead would be indistinguishable from a leg that
        answered nothing."""
        root = _repo(tmp_path)
        attempt_dir = tmp_path / "att"
        attempt_dir.mkdir()

        wrapper._write_typed_results(_spec(root, attempt_dir, result_kind=""), "log\n")

        assert list(attempt_dir.iterdir()) == []

    def test_an_unknown_declared_kind_writes_nothing_and_does_not_raise(self, tmp_path):
        """A spec naming a kind this build does not have is a deployment
        mismatch. It must not take the wrapper down: the process outcome and
        the receipt are still true and still have to be recorded."""
        root = _repo(tmp_path)
        attempt_dir = tmp_path / "att"
        attempt_dir.mkdir()

        wrapper._write_typed_results(
            _spec(root, attempt_dir, result_kind="telepathy"), "log\n")

        assert list(attempt_dir.iterdir()) == []

    def test_the_head_commit_degrades_to_empty_rather_than_lying(self, tmp_path):
        """An unreadable repository must yield NO revision claim, not a false
        one: `_require_identity` skips an expectation the caller does not
        hold, so an empty string is inert while a wrong sha would refuse every
        result."""
        not_a_repo = tmp_path / "bare"
        not_a_repo.mkdir()
        assert wrapper._head_commit(str(not_a_repo)) == ""

        real = _repo(tmp_path)
        assert len(wrapper._head_commit(str(real))) == 40

    def test_a_git_that_cannot_run_at_all_still_degrades(self, tmp_path, monkeypatch):
        def _boom(*_a, **_k):
            raise OSError("git is not installed")

        monkeypatch.setattr(wrapper.subprocess, "run", _boom)
        assert wrapper._head_commit(str(tmp_path)) == ""


# ---------------------------------------------------------------------------
# the fake CLI writes what a real agent writes


class TestFakeAgentJudgement:
    """The behavioral corpus is only worth anything if its fake produces the
    same artifact a real agent does. These pin that seam directly, because
    the fake itself runs in a forked process the coverage gate cannot see."""

    def test_the_fake_writes_a_judgement_bound_to_the_wrappers_attempt_id(
            self, tmp_path, monkeypatch):
        root = _repo(tmp_path)
        monkeypatch.chdir(root)
        monkeypatch.setenv("NYXLOOM_ATTEMPT_ID", ATTEMPT)

        testing._run_step(TASK, {
            "target_dir": str(root),
            "review_file": "reports/x-REVIEW.md",
            "review_verdict": "APPROVED",
            "review_reason": "all good",
        })

        doc = json.loads((root / results.judgement_filename(TASK)).read_text())
        assert doc["verdict"] == "approved"
        assert doc["attempt_id"] == ATTEMPT
        assert doc["task_id"] == TASK
        # ...and it loads through the REAL reader, which is the point.
        assert _load(doc).verdict is Verdict.APPROVED

    def test_a_rejected_step_writes_a_rejected_judgement(self, tmp_path, monkeypatch):
        root = _repo(tmp_path)
        monkeypatch.chdir(root)
        monkeypatch.setenv("NYXLOOM_ATTEMPT_ID", ATTEMPT)

        testing._run_step(TASK, {
            "target_dir": str(root),
            "review_file": "reports/x-REVIEW.md",
            "review_verdict": "REJECTED",
        })

        assert json.loads(
            (root / results.judgement_filename(TASK)).read_text())["verdict"] == "rejected"

    def test_an_override_lets_a_test_script_a_malformed_judgement(
            self, tmp_path, monkeypatch):
        """The negative cases the corpus needs: a document the reader must
        refuse, produced the same way a broken agent would produce it."""
        root = _repo(tmp_path)
        monkeypatch.chdir(root)
        monkeypatch.setenv("NYXLOOM_ATTEMPT_ID", ATTEMPT)

        testing._run_step(TASK, {
            "target_dir": str(root),
            "review_file": "reports/x-REVIEW.md",
            "judgement_override": "{ this is not json",
        })

        assert (root / results.judgement_filename(TASK)).read_text() == "{ this is not json"

    def test_without_a_wrapper_environment_the_fake_writes_no_judgement(
            self, tmp_path, monkeypatch):
        """A fake invoked outside a wrapper has no attempt to bind to, and a
        judgement bound to nothing would be refused anyway -- writing one
        would only make the failure harder to read."""
        root = _repo(tmp_path)
        monkeypatch.chdir(root)
        monkeypatch.delenv("NYXLOOM_ATTEMPT_ID", raising=False)

        testing._run_step(TASK, {
            "target_dir": str(root),
            "review_file": "reports/x-REVIEW.md",
            "review_verdict": "APPROVED",
        })

        assert not (root / results.judgement_filename(TASK)).exists()

    def test_the_step_serializes_its_judgement_controls(self):
        step = testing.FakeStep(
            review_file="reports/x-REVIEW.md", review_verdict="APPROVED",
            judgement_override="{}", judgement_task_id="t-other")
        d = step.to_dict()
        assert d["judgement_override"] == "{}"
        assert d["judgement_task_id"] == "t-other"


# ---------------------------------------------------------------------------
# what the daemon does with each verdict


class TestDaemonVerdictMapping:
    """`_typed_verdict` collapses six verdicts into the three words the call
    sites act on. Which one each maps to is a routing decision, so each is
    pinned."""

    def _write(self, project, task_id, attempt_id, verdict, **over):
        raw = b"log\n"
        d = paths.attempt_dir(project, attempt_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "attempt.log").write_bytes(raw)
        record = results.AgentResult(
            kind=ResultKind.REVIEW_INDEPENDENT, verdict=verdict,
            identity=results.ResultIdentity(task_id=task_id, attempt_id=attempt_id),
            evidence=results.Evidence(path="attempt.log",
                                       sha256=results.digest_bytes(raw),
                                       bytes_len=len(raw)),
            summary=over.get("summary", ""),
            decline_reason=over.get("decline_reason"),
        )
        (d / results.result_filename(task_id)).write_text(record.to_json())
        return d

    @pytest.mark.parametrize("verdict, expected, extra", [
        (Verdict.APPROVED, "approved", {}),
        (Verdict.REJECTED, "rejected", {"summary": "boundary is wrong"}),
        # A leg outcome is not a judgement about the WORK: relaunching the
        # review is right, and recording it as a rejection would re-implement
        # work nobody found fault with.
        (Verdict.FAILED, "missing", {}),
        (Verdict.DECLINED, "missing",
         {"decline_reason": DeclineReason.CONTEXT_EXCEEDED}),
    ], ids=["approved", "rejected", "failed", "declined"])
    def test_each_verdict_maps_to_its_routing_word(
            self, tmp_state, sample_project, verdict, expected, extra):
        self._write("demo", TASK, ATTEMPT, verdict, **extra)
        d = daemon.Daemon({"demo": sample_project.root})

        assert d._exit._typed_verdict("demo", TASK, ATTEMPT,
                                 ResultKind.REVIEW_INDEPENDENT)[0] == expected

    def test_no_result_at_all_is_missing_with_a_reason(self, tmp_state, sample_project):
        d = daemon.Daemon({"demo": sample_project.root})
        verdict, why = d._exit._typed_verdict("demo", TASK, "att-never-ran",
                                         ResultKind.REVIEW_INDEPENDENT)
        assert verdict == "missing"
        assert why

    def test_a_refused_result_is_rejected_and_names_the_code(
            self, tmp_state, sample_project):
        """Refused is not absent. Something answered and could not be
        trusted, which must not merge -- and the code travels so an operator
        repairs the right thing."""
        attempt_dir = self._write("demo", TASK, ATTEMPT, Verdict.APPROVED)
        # corrupt the evidence AFTER the digest was recorded
        (attempt_dir / "attempt.log").write_bytes(b"tampered\n")
        d = daemon.Daemon({"demo": sample_project.root})

        verdict, why = d._exit._typed_verdict("demo", TASK, ATTEMPT,
                                         ResultKind.REVIEW_INDEPENDENT)

        assert verdict == "rejected"
        assert RejectionCode.EVIDENCE_DIGEST_MISMATCH.value in why
