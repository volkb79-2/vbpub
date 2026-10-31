"""Whole-source R1 coverage closure for ``mutation``, ``liveness`` and ``process_tree``.

Every test pins one guarded branch with a specific observable (refusal text,
returned value, raised type) so deleting the branch fails it. Nothing here
touches real docker, cgroups, signals or the network.
"""

from __future__ import annotations

import hashlib
import itertools
from contextlib import contextmanager
import json
import math
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from assay import liveness, process_tree
from assay.adapters.python import PythonAdapter
from assay.runner import CommandResult, execute_command
from conftest import (
    completed_process,
    make_deadline,
    make_lane,
    make_plan,
    GitRepo,
    prepared_snapshot,
    process_tree_evidence_dict,
    timeout_expired,
    zero_resource_limit_evidence_dict,
)

from assay import mutation
from assay.errors import AssayError, Outcome, ReasonCode
from assay.mutation import (
    MutationTarget,
    R2BaselineTimeoutError,
    R2ManifestWriteError,
    collect_mutation_sites,
)

OPERATORS = ("python:compare-swap",)


def _target(path: str, text: str = "x = 1\n") -> MutationTarget:
    return MutationTarget(path=path, text=text, lines=frozenset({1}))


def _adapter(**methods):
    """An adapter exposing exactly the named optional protocol methods."""
    return type(
        "Adapter", (), {"name": "fake", **{k: staticmethod(v) for k, v in methods.items()}}
    )()


# --------------------------------------------------------------------------
# error classes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cls", "outcome", "reason"),
    [
        (R2ManifestWriteError, Outcome.ERROR, ReasonCode.OUTPUT_WRITE_FAILED),
        (R2BaselineTimeoutError, Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT),
    ],
)
def test_r2_error_classes_carry_their_typed_outcome(cls, outcome, reason):
    error = cls("boom")
    assert str(error) == "boom"
    assert error.outcome is outcome
    assert error.reason_code is reason


# --------------------------------------------------------------------------
# collect_mutation_sites context-preparation protocol variants
# --------------------------------------------------------------------------


def _source_protocol(calls, **extra):
    def prepare(stream):
        calls.append(("prepare", list(stream)))
        return "ctx"

    def generate(text, lines, *, operators, limit, context, source_identity):
        calls.append(("generate", source_identity, context))
        return ()

    return _adapter(
        prepare_mutation_context_with_sources=prepare,
        generate_mutation_sites_with_context_for_source=generate,
        **extra,
    )


def test_bounded_target_preparer_is_chosen_when_a_deadline_is_supplied():
    calls = []
    deadlines = []

    def bounded(stream, *, remaining, target_source_identities):
        calls.append(("bounded", sorted(target_source_identities), list(stream)))
        return "ctx"

    adapter = _source_protocol(calls, prepare_mutation_context_with_sources_for_targets_bounded=bounded)
    jobs = collect_mutation_sites(
        [_target("a.py")], adapter=adapter, operators=OPERATORS, limit=5,
        deadline_check=lambda: deadlines.append(1) or 1.0,
    )
    assert jobs == ()
    assert calls[0] == ("bounded", ["a.py"], [("a.py", "x = 1\n")])
    assert deadlines


def test_target_preparer_runs_between_two_deadline_checks_without_bounded_variants():
    calls = []
    events = []

    def preparer(stream, *, target_source_identities):
        events.append("prepare")
        calls.append(sorted(target_source_identities))
        list(stream)
        return "ctx"

    adapter = _source_protocol(calls, prepare_mutation_context_with_sources_for_targets=preparer)
    collect_mutation_sites(
        [_target("a.py")], adapter=adapter, operators=OPERATORS, limit=5,
        deadline_check=lambda: events.append("check") or 1.0,
    )
    assert calls[0] == ["a.py"]
    # check before prepare, and a check immediately after it
    first = events.index("prepare")
    assert events[first - 1] == "check" and events[first + 1] == "check"


def test_identified_sources_dedupe_identical_context_and_reject_malformed_items():
    seen = []

    def prepare(stream):
        seen.extend(stream)
        return "ctx"

    adapter = _adapter(
        prepare_mutation_context_with_sources=prepare,
        generate_mutation_sites_with_context_for_source=lambda *a, **k: (),
    )
    collect_mutation_sites(
        [_target("a.py")], adapter=adapter, operators=OPERATORS, limit=5,
        context_sources=[("ext.py", "same"), ("ext.py", "same"), ("a.py", "stale"), "legacy"],
    )
    assert seen == [("ext.py", "same"), ("external-context:3", "legacy"), ("a.py", "x = 1\n")]

    with pytest.raises(TypeError, match="text or \\(path, text\\)"):
        collect_mutation_sites(
            [_target("a.py")], adapter=adapter, operators=OPERATORS, limit=5,
            context_sources=[42],
        )


@pytest.mark.parametrize("with_deadline", [True, False])
def test_legacy_context_preparer_sees_external_then_target_texts(with_deadline):
    received = []
    checks = []

    def prepare(texts):
        received.extend(texts)
        return "ctx"

    contexts = []

    def generate(text, lines, *, operators, limit, context):
        contexts.append(context)
        return ()

    adapter = _adapter(
        prepare_mutation_context=prepare,
        generate_mutation_sites_with_context=generate,
    )
    collect_mutation_sites(
        [_target("a.py")], adapter=adapter, operators=OPERATORS, limit=5,
        context_sources=[("ext.py", "tuple-text"), "plain-text"],
        deadline_check=(lambda: checks.append(1) or 1.0) if with_deadline else None,
    )
    assert received == ["tuple-text", "plain-text", "x = 1\n"]
    assert contexts == ["ctx"]
    assert bool(checks) is with_deadline


# --------------------------------------------------------------------------
# candidate event buffer
# --------------------------------------------------------------------------


def test_event_buffer_refuses_to_resolve_a_position_twice():
    buffer = mutation._CandidateEventBuffer(3)
    buffer.stage(0, {"a": 1})
    with pytest.raises(ValueError, match="candidate position 0 was already resolved"):
        buffer.stage(0, {"a": 2})
    buffer.resolve_without_event(1)
    with pytest.raises(ValueError, match="candidate position 1 was already resolved"):
        buffer.resolve_without_event(1)
    assert buffer.drain_contiguous() == [{"a": 1}]


# --------------------------------------------------------------------------
# progress_writer open-failure cleanup
# --------------------------------------------------------------------------


def _count_open_fds():
    import os

    return len(os.listdir("/proc/self/fd"))


def test_progress_writer_closes_the_descriptor_when_the_open_guard_raises_oserror(tmp_path):
    def guard(parent_fd, descriptor):
        raise OSError("guard says no")

    before = _count_open_fds()
    with pytest.raises(AssayError, match="cannot open the progress destination") as caught:
        with mutation.progress_writer(tmp_path / "p.jsonl", open_guard=guard):
            pytest.fail("must not yield")
    assert caught.value.reason_code is ReasonCode.OUTPUT_WRITE_FAILED
    assert _count_open_fds() == before


def test_progress_writer_closes_parent_when_the_parent_open_fails_with_oserror(tmp_path, monkeypatch):
    real_open = mutation.os.open
    calls = []

    def flaky(*args, **kwargs):
        calls.append(args[0])
        raise OSError("no parent for you")

    monkeypatch.setattr(mutation.os, "open", flaky)
    with pytest.raises(AssayError, match="no parent for you") as caught:
        with mutation.progress_writer(tmp_path / "p.jsonl"):
            pytest.fail("must not yield")
    monkeypatch.setattr(mutation.os, "open", real_open)
    assert caught.value.reason_code is ReasonCode.OUTPUT_WRITE_FAILED
    assert len(calls) == 1  # only the parent open was attempted


@pytest.mark.parametrize("where", ["parent-open", "parent-guard", "open-guard"])
def test_progress_writer_propagates_non_oserror_failures_and_releases_descriptors(
    tmp_path, monkeypatch, where
):
    real_open = mutation.os.open
    parent_guard = open_guard = None

    def explode(*_args, **_kwargs):
        raise RuntimeError(where)

    if where == "parent-open":
        monkeypatch.setattr(mutation.os, "open", explode)
    elif where == "parent-guard":
        parent_guard = explode
    else:
        open_guard = explode
    before = _count_open_fds()
    with pytest.raises(RuntimeError, match=where):
        with mutation.progress_writer(
            tmp_path / "p.jsonl", parent_guard=parent_guard, open_guard=open_guard
        ):
            pytest.fail("must not yield")
    monkeypatch.setattr(mutation.os, "open", real_open)
    assert _count_open_fds() == before


# --------------------------------------------------------------------------
# state record fixtures
# --------------------------------------------------------------------------


def _job():
    site = mutation.MutationSite(
        start_byte=4,
        end_byte=8,
        replacement=b"False",
        lineno=1,
        operator="python:bool-const-flip",
        description="True->False",
    )
    return mutation.MutantJob(path="src/mod.py", original_text="x = True\n", site=site)


def _record(job=None, **overrides):
    job = _job() if job is None else job
    source = job.original_text.encode()
    payload = {
        "schema_version": mutation.MUTATION_STATE_SCHEMA_VERSION,
        "candidate_id": mutation.candidate_id(job),
        "path": job.path,
        "replacement_sha256": hashlib.sha256(job.site.replacement).hexdigest(),
        "operator": job.site.operator,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "outcome_bucket": "killed",
        "terminal_result": {"outcome": "FAIL", "reason_code": "COMMAND_FAILED", "returncode": 1},
        "judge_sha256": "j" * 64,
        "execution": {"mode": "full"},
        "lineno": job.site.lineno,
        "start_byte": job.site.start_byte,
        "end_byte": job.site.end_byte,
        "description": job.site.description,
        "mutated_file_sha256": hashlib.sha256(job.site.apply(source)).hexdigest(),
        "resource_limit_evidence": zero_resource_limit_evidence_dict(),
        "process_tree_evidence": process_tree_evidence_dict(),
    }
    payload.update(overrides)
    return payload


def _put(root, payload):
    root.mkdir(parents=True, exist_ok=True)
    name = mutation.mutation_state_record_name(payload.get("candidate_id", mutation.candidate_id(_job())))
    (root / name).write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------
# _write_mutation_state_record rollback / cleanup branches
# --------------------------------------------------------------------------


class _GuardOnCall:
    """A commit guard that runs *action* on the n-th call."""

    def __init__(self, n, action):
        self.n, self.action, self.calls = n, action, 0

    def __call__(self):
        self.calls += 1
        if self.calls == self.n:
            self.action()


def _boom():
    raise RuntimeError("guard rejected the commit")


def _dest(root):
    return root / mutation.mutation_state_record_name(mutation.candidate_id(_job()))


def test_failed_commit_with_a_previous_record_restores_it_and_drops_the_backup(tmp_path):
    previous = _record(description="previous")
    _put(tmp_path, previous)
    # guard call 2 runs after the backup link exists and before the replace
    guard = _GuardOnCall(2, _boom)
    with pytest.raises(RuntimeError, match="guard rejected"):
        mutation._write_mutation_state_record(tmp_path, _record(description="new"), commit_guard=guard)
    assert json.loads(_dest(tmp_path).read_text())["description"] == "previous"
    assert [p.name for p in tmp_path.iterdir()] == [_dest(tmp_path).name]


def test_failed_post_publish_commit_tolerates_the_record_vanishing_before_rollback(tmp_path):
    dest = _dest(tmp_path)
    guard = _GuardOnCall(3, lambda: (dest.unlink(), _boom()))
    with pytest.raises(RuntimeError, match="guard rejected"):
        mutation._write_mutation_state_record(tmp_path, _record(), commit_guard=guard)
    assert list(tmp_path.iterdir()) == []


def test_unrecoverable_rollback_is_reported_as_a_state_error(tmp_path):
    dest = _dest(tmp_path)

    def sabotage():
        dest.unlink()
        dest.mkdir()  # unlink() of a directory is an OSError other than FileNotFoundError
        _boom()

    with pytest.raises(mutation.MutationStateError, match="could not roll back") as caught:
        mutation._write_mutation_state_record(tmp_path, _record(), commit_guard=_GuardOnCall(3, sabotage))
    assert isinstance(caught.value.__cause__, OSError)


@pytest.mark.parametrize("failures", [1, 2])
def test_backup_removal_failure_after_acceptance_keeps_the_accepted_record(tmp_path, monkeypatch, failures):
    _put(tmp_path, _record(description="previous"))
    real_unlink = os.unlink
    remaining = [failures]

    def flaky(path, *args, **kwargs):
        if ".state-backup-" in str(path) and remaining[0] > 0:
            remaining[0] -= 1
            raise OSError("cannot unlink backup")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(mutation.os, "unlink", flaky)
    with pytest.raises(OSError, match="cannot unlink backup"):
        mutation._write_mutation_state_record(tmp_path, _record(description="new"))
    monkeypatch.undo()
    assert json.loads(_dest(tmp_path).read_text())["description"] == "new"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".state-backup-")]
    # one failure: the cleanup retry removes it; two: a harmless hard link is left
    assert len(leftovers) == (0 if failures == 1 else 1)


def test_directory_sync_failure_after_backup_removal_keeps_the_accepted_record(tmp_path, monkeypatch):
    _put(tmp_path, _record(description="previous"))
    real_fsync = os.fsync
    calls = []

    def flaky(fd):
        calls.append(fd)
        if len(calls) == 3:  # file, post-replace directory, post-unlink directory
            raise OSError("cannot sync directory")
        return real_fsync(fd)

    monkeypatch.setattr(mutation.os, "fsync", flaky)
    with pytest.raises(OSError, match="cannot sync directory"):
        mutation._write_mutation_state_record(tmp_path, _record(description="new"))
    monkeypatch.undo()
    assert json.loads(_dest(tmp_path).read_text())["description"] == "new"
    assert [p.name for p in tmp_path.iterdir()] == [_dest(tmp_path).name]


# --------------------------------------------------------------------------
# _read_state_record_bytes
# --------------------------------------------------------------------------


@pytest.fixture
def state_fd(tmp_path):
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    yield fd
    os.close(fd)


def _name():
    return mutation.mutation_state_record_name(mutation.candidate_id(_job()))


def test_state_read_refuses_a_symlinked_record(tmp_path, state_fd):
    (tmp_path / "elsewhere").write_text("{}")
    (tmp_path / _name()).symlink_to("elsewhere")
    with pytest.raises(mutation.MutationStateError, match="could not be opened safely"):
        mutation._read_state_record_bytes(tmp_path, mutation.candidate_id(_job()), state_root_fd=state_fd)


def test_state_read_refuses_a_non_regular_record(tmp_path, state_fd):
    os.mkfifo(tmp_path / _name())
    with pytest.raises(mutation.MutationStateError, match="is not a regular file"):
        mutation._read_state_record_bytes(tmp_path, mutation.candidate_id(_job()), state_root_fd=state_fd)


def test_state_read_refuses_an_oversize_record_by_size(tmp_path, state_fd, monkeypatch):
    monkeypatch.setattr(mutation, "MUTATION_STATE_RECORD_LIMIT", 8)
    (tmp_path / _name()).write_bytes(b"x" * 9)
    with pytest.raises(mutation.MutationStateError, match="exceeds the 8-byte limit"):
        mutation._read_state_record_bytes(tmp_path, mutation.candidate_id(_job()), state_root_fd=state_fd)


def test_state_read_refuses_a_record_that_grows_past_the_limit_while_being_read(
    tmp_path, state_fd, monkeypatch
):
    monkeypatch.setattr(mutation, "MUTATION_STATE_RECORD_LIMIT", 8)
    (tmp_path / _name()).write_bytes(b"x" * 20)
    real_fstat = os.fstat
    monkeypatch.setattr(
        mutation.os,
        "fstat",
        lambda fd: SimpleNamespace(st_mode=stat.S_IFREG, st_size=0)
        if fd != state_fd
        else real_fstat(fd),
    )
    with pytest.raises(mutation.MutationStateError, match="exceeds the 8-byte limit"):
        mutation._read_state_record_bytes(tmp_path, mutation.candidate_id(_job()), state_root_fd=state_fd)


def test_state_read_reports_an_io_error_as_a_state_error(tmp_path, state_fd, monkeypatch):
    (tmp_path / _name()).write_bytes(b"{}")

    def broken(fd, n):
        raise OSError("EIO")

    monkeypatch.setattr(mutation.os, "read", broken)
    with pytest.raises(mutation.MutationStateError, match="could not be read safely"):
        mutation._read_state_record_bytes(tmp_path, mutation.candidate_id(_job()), state_root_fd=state_fd)


# --------------------------------------------------------------------------
# _terminal_result_matches_bucket
# --------------------------------------------------------------------------


def _tree(status="exited", outliving=0):
    return mutation.ProcessTreeEvidence(
        leader_status=status,
        group_signalled_before_reap=False,
        outliving_descendants=outliving,
        descendants_signalled=0,
        cleanup_seconds=0.0,
        clean=True,
    )


def _terminal(outcome="FAIL", reason="COMMAND_FAILED", returncode=1, bucket="killed", **extra):
    return {
        "terminal_result": {"outcome": outcome, "reason_code": reason, "returncode": returncode},
        "outcome_bucket": bucket,
        **extra,
    }


_DIGEST = "a" * 64
_GOOD_WITNESS = {
    "node_id": "t::n",
    "when": "call",
    "outcome": "failed",
    "session_exit_status": 1,
    "process_exit_status": 1,
}
_OBSERVED_LIMITS = mutation.ResourceLimitEvidence.from_dict(
    zero_resource_limit_evidence_dict()
)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_terminal(reason=5), "reason_code must be a string or null"),
        (_terminal(outcome="BOGUS"), "unknown outcome or reason"),
        (_terminal(reason="NOPE"), "unknown outcome or reason"),
        (_terminal(outcome="PASS", reason="COMMAND_FAILED", returncode=0), "PASS terminal_result requires"),
        (_terminal(outcome="FAIL", reason=None), "FAIL terminal_result requires"),
        (_terminal(outcome="ERROR", reason=None, returncode=1), "ERROR terminal_result requires"),
        (_terminal(forced_bucket="killed"), "forced_bucket must be 'crashed'"),
        (_terminal(equivalence_evidence=[]), "equivalence_evidence has missing or unknown fields"),
        (
            _terminal(equivalence_evidence={"artifact": 1, "baseline_sha256": _DIGEST, "observed_sha256": None}),
            "artifact must be a string",
        ),
        (
            _terminal(equivalence_evidence={"artifact": "a", "baseline_sha256": "xyz", "observed_sha256": None}),
            "baseline digest must be SHA-256",
        ),
        (
            _terminal(equivalence_evidence={"artifact": "a", "baseline_sha256": _DIGEST, "observed_sha256": "xyz"}),
            "observed digest must be SHA-256 or null",
        ),
        (_terminal(kill_signal_evidence=[]), "kill_signal_evidence has missing or unknown fields"),
        (
            _terminal(kill_signal_evidence={"artifact": "k", "present": "yes"}),
            "kill_signal_evidence has invalid field types",
        ),
    ],
)
def test_terminal_matcher_raises_for_malformed_present_evidence(payload, message):
    with pytest.raises(ValueError, match=message):
        mutation._terminal_result_matches_bucket(payload, process_tree_evidence=_tree())


def test_terminal_matcher_refuses_a_budget_terminal_with_a_non_timeout_reason():
    payload = _terminal(outcome="BUDGET_EXCEEDED", reason="COMMAND_FAILED", returncode=None)
    with pytest.raises(ValueError, match="BUDGET_EXCEEDED terminal_result requires a timeout reason"):
        mutation._terminal_result_matches_bucket(payload)


_KILL_OK = {"artifact": "k", "present": True}


@pytest.mark.parametrize(
    ("payload", "kwargs", "expected"),
    [
        ({"outcome_bucket": "killed"}, {}, False),
        (
            _terminal(outcome="BUDGET_EXCEEDED", reason="LANE_TIMEOUT", returncode=None, bucket="budget_exceeded"),
            {"process_tree_evidence": _tree("exited")},
            False,
        ),
        (_terminal(outcome="ERROR", reason=None, returncode=None, bucket="crashed"), {}, True),
        (_terminal(outcome="NO_MEASUREMENT", reason=None, returncode=None, bucket="crashed"), {}, False),
        (_terminal(), {"kill_signal_artifact": "k"}, False),
        (_terminal(kill_signal_evidence={"artifact": "other", "present": True}), {"kill_signal_artifact": "k"}, False),
        (
            _terminal(kill_signal_evidence={"artifact": "k", "present": False}, bucket="crashed"),
            {"kill_signal_artifact": "k"},
            True,
        ),
        (_terminal(kill_signal_evidence=_KILL_OK), {"kill_signal_artifact": "k"}, True),
        (_terminal(kill_signal_evidence=_KILL_OK), {}, False),
        (_terminal(), {"cold_witness": True}, False),
        (_terminal(execution={"mode": "full", "witness": _GOOD_WITNESS}), {"cold_witness": True}, True),
        (_terminal(), {"process_tree_evidence": None}, False),
    ],
)
def test_terminal_matcher_cache_decisions(payload, kwargs, expected):
    kwargs = {
        "process_tree_evidence": _tree(),
        "resource_limit_evidence": _OBSERVED_LIMITS,
        **kwargs,
    }
    assert mutation._terminal_result_matches_bucket(payload, **kwargs) is expected


def test_terminal_matcher_refuses_a_non_call_witness_as_corruption():
    payload = _terminal(
        execution={
            "mode": "full",
            "witness": {**_GOOD_WITNESS, "when": "setup"},
        }
    )

    with pytest.raises(ValueError, match="failed call-phase report"):
        mutation._terminal_result_matches_bucket(
            payload,
            process_tree_evidence=_tree(),
            resource_limit_evidence=_OBSERVED_LIMITS,
            cold_witness=True,
        )


def test_terminal_matcher_maps_a_judgeable_nonexited_leader_to_crashed():
    payload = _terminal(
        outcome="PASS", reason=None, returncode=0, bucket="crashed"
    )
    assert mutation._terminal_result_matches_bucket(
        payload, process_tree_evidence=_tree("timeout-killed")
    )


def test_terminal_matcher_does_not_reuse_a_pass_with_a_signaled_leader_as_survived():
    payload = _terminal(
        outcome="PASS", reason=None, returncode=0, bucket="survived"
    )
    assert not mutation._terminal_result_matches_bucket(
        payload, process_tree_evidence=_tree("signaled")
    )


def test_terminal_matcher_rejects_unobserved_bare_failure_credit():
    payload = _terminal(execution={"mode": "full"})

    assert not mutation._terminal_result_matches_bucket(
        payload, process_tree_evidence=_tree()
    )


def test_terminal_matcher_accepts_unobserved_failure_with_clean_report_receipt():
    payload = _terminal(execution={"mode": "full", "witness": _GOOD_WITNESS})

    assert mutation._terminal_result_matches_bucket(
        payload, process_tree_evidence=_tree()
    )


def test_terminal_matcher_rejects_shell_wrapped_signal_even_with_stale_receipt():
    payload = _terminal(
        returncode=137,
        execution={"mode": "full", "witness": _GOOD_WITNESS},
    )

    assert not mutation._terminal_result_matches_bucket(
        payload, process_tree_evidence=_tree()
    )


# --------------------------------------------------------------------------
# _load_validated_state_record
# --------------------------------------------------------------------------


def _load(root, **kwargs):
    return mutation._load_validated_state_record(root, _job(), judge="j" * 64, **kwargs)


def _drop(payload, key):
    payload.pop(key)
    return payload


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_record(resource_limit_evidence="nope"), "resource_limit_evidence must be an object"),
        (_record(process_tree_evidence="nope"), "process_tree_evidence must be an object"),
        (_record(process_tree_evidence={"protocol": "x"}), "invalid process-tree evidence"),
        (_record(evidence="nope"), "evidence must be an object or null"),
        (_record(evidence={"command": "r2"}), "evidence has missing or unknown fields"),
        (
            _record(
                evidence={
                    "command": "bogus",
                    "collection_count": 1,
                    "collection_sha256": _DIGEST,
                    "hook_fingerprint_sha256": _DIGEST,
                    "started_count": None,
                    "failed_call_index": None,
                }
            ),
            "invalid mutant evidence",
        ),
        (
            _record(
                outcome_bucket="budget_exceeded",
                terminal_result={"outcome": "BUDGET_EXCEEDED", "reason_code": "LANE_TIMEOUT", "returncode": None},
                process_tree_evidence=process_tree_evidence_dict(leader_status="timeout-killed"),
                evidence=None,
                mutated_file_sha256="not-a-digest",
            ),
            "invalid mutated_file_sha256",
        ),
        (
            _record(
                outcome_bucket="survived",
                terminal_result={"outcome": "PASS", "reason_code": None, "returncode": 0},
                execution={"mode": "witness-cold", "witness": None},
            ),
            None,  # placeholder replaced below (needs cold_witness)
        ),
    ][:-1],
)
def test_load_record_refuses_structural_corruption(tmp_path, payload, message):
    _put(tmp_path, payload)
    with pytest.raises(mutation.MutationStateError, match=message):
        _load(tmp_path)


@pytest.mark.parametrize(
    "resource_observation",
    [
        None,
        {"status": "observed", "reason": "sample present"},
        {"status": "unobserved", "reason": 1},
        {"status": "unobserved", "reason": "  "},
        {"status": "unobserved", "reason": "observer unavailable", "extra": True},
    ],
)
def test_load_record_refuses_malformed_unobserved_resource_marker(
    tmp_path, resource_observation
):
    payload = _record()
    del payload["resource_limit_evidence"]
    payload["resource_observation"] = resource_observation
    _put(tmp_path, payload)
    with pytest.raises(mutation.MutationStateError, match="invalid resource_observation"):
        _load(tmp_path)


def test_load_record_without_process_tree_evidence_is_a_rejected_cache_miss(tmp_path):
    _put(tmp_path, _drop(_record(), "process_tree_evidence"))
    assert _load(tmp_path) == mutation._RECORD_REJECTED


def test_load_record_accepts_well_formed_mutant_evidence(tmp_path):
    evidence = {
        "command": "r2",
        "collection_count": 1,
        "collection_sha256": _DIGEST,
        "hook_fingerprint_sha256": _DIGEST,
        "started_count": None,
        "failed_call_index": None,
    }
    _put(tmp_path, _record(evidence=evidence))
    assert _load(tmp_path)["evidence"] == evidence


def test_cold_witness_survivor_without_evidence_is_corruption(tmp_path):
    _put(
        tmp_path,
        _record(
            outcome_bucket="survived",
            terminal_result={"outcome": "PASS", "reason_code": None, "returncode": 0},
        ),
    )
    with pytest.raises(mutation.MutationStateError, match="missing required evidence"):
        _load(tmp_path, cold_witness=True)


# --------------------------------------------------------------------------
# legacy B145 resource evidence + execution provenance
# --------------------------------------------------------------------------

_DELTA = {"before": 0, "after": 0, "delta": 0}


def _legacy(**changes):
    raw = {
        "cgroup_version": 2,
        "pids_events": {"max": dict(_DELTA)},
        "memory_events": {"oom_kill": dict(_DELTA), "oom_group_kill": dict(_DELTA)},
    }
    raw.update(changes)
    return raw


def test_legacy_resource_evidence_recognition_returns_false_for_other_shapes():
    check = mutation._is_valid_legacy_b145_resource_limit_evidence
    assert check(_legacy()) is True
    assert check({"cgroup_version": 2}) is False
    assert check(_legacy(memory_events={"oom": dict(_DELTA)})) is False


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (_legacy(cgroup_version=1), "requires cgroup_version 2"),
        (_legacy(cgroup_version=True), "requires cgroup_version 2"),
        (_legacy(pids_events={"max": dict(_DELTA), "extra": 1}), "pids_events must contain exactly max"),
        (_legacy(pids_events={"max": {"before": 0}}), "exactly before, after, and delta"),
    ],
)
def test_legacy_resource_evidence_refuses_malformed_counters(raw, message):
    with pytest.raises(ValueError, match=message):
        mutation._is_valid_legacy_b145_resource_limit_evidence(raw)


def test_witness_cold_execution_is_restored_with_its_witness():
    execution = mutation._execution_from_state_record(
        {"execution": {"mode": "witness-cold", "witness": _GOOD_WITNESS}}
    )
    assert execution.mode == "witness-cold"
    assert execution.witness.node_id == "t::n"


# --------------------------------------------------------------------------
# liveness: deferred sweep without termination, native-runner OSError mapping
# --------------------------------------------------------------------------


def test_deferred_sweep_is_cleared_without_signalling_when_termination_is_not_set(monkeypatch):
    signalled = []
    monkeypatch.setattr(liveness, "_killpg_group", lambda pgid, sig: signalled.append((pgid, sig)))
    monkeypatch.setattr(liveness, "_DEFERRED_TERMINATION_SWEEP", True)
    monkeypatch.setattr(liveness, "_LIVE_GROUPS", {111, 222})
    liveness._TERMINATION.clear()
    liveness.unregister_live_group(111)
    assert liveness._DEFERRED_TERMINATION_SWEEP is False
    assert liveness._LIVE_GROUPS == {222}
    assert signalled == []


def test_an_oserror_before_the_child_exists_becomes_a_process_tree_cleanup_refusal(
    tmp_path, monkeypatch
):
    spawned = []

    def failing_open(path, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(liveness, "open", failing_open, raising=False)
    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "events",
        expect_next_event_within_s=5.0,
        popen=lambda *a, **k: spawned.append(a),
    )
    with pytest.raises(AssayError, match="native R2 process-tree cleanup failed") as caught:
        runner(["true"], env={}, cwd=tmp_path, timeout=5.0)
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.EXEC_FAILED
    wrapped = caught.value.__cause__
    assert "native liveness process runner failed: disk full" in str(wrapped)
    assert isinstance(wrapped.__cause__, OSError)
    assert wrapped.__cause__.process_tree_evidence["leader_status"] == "not-started"
    assert spawned == []  # nothing was ever launched, so nothing was unregistered


# --------------------------------------------------------------------------
# process_tree: SIGCHLD disposition parsing and the redacted tail cutoff
# --------------------------------------------------------------------------


def _fake_proc_status(monkeypatch, text=None, error=None):
    real = process_tree.Path.read_text

    def read_text(self, *args, **kwargs):
        if str(self) == "/proc/self/status":
            if error is not None:
                raise error
            return text
        return real(self, *args, **kwargs)

    monkeypatch.setattr(process_tree.Path, "read_text", read_text)


def test_sigchld_probe_reads_the_ignore_mask(monkeypatch):
    _fake_proc_status(monkeypatch, "Name:\tx\nSigIgn:\t0000000000010000\n")
    assert process_tree._sigchld_is_ignored() is True  # bit 16 is SIGCHLD (17)
    _fake_proc_status(monkeypatch, "SigIgn:\t0000000000000000\n")
    assert process_tree._sigchld_is_ignored() is False


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"error": PermissionError("denied")}, "cannot inspect SIGCHLD disposition in procfs: denied"),
        ({"text": "SigIgn:\tnot-hex\n"}, "malformed SigIgn mask"),
        ({"text": "Name:\tx\n"}, "did not report the SigIgn mask"),
    ],
)
def test_sigchld_probe_refuses_unreadable_procfs(monkeypatch, kwargs, message):
    _fake_proc_status(monkeypatch, **kwargs)
    with pytest.raises(process_tree.ProcessTreeError, match=message):
        process_tree._sigchld_is_ignored()


def test_output_tail_cutoff_never_starts_inside_a_multibyte_character(tmp_path):
    path = tmp_path / "out"
    path.write_bytes("\u00e9".encode() * 10)  # 20 bytes; the tail window starts mid-character
    with path.open("rb") as stream:
        tail, start = process_tree._read_tail(stream, limit=5)
    assert start == 15
    assert tail == "\u00e9\u00e9"


# --------------------------------------------------------------------------
# run_mutation argument validation (before anything is touched)
# --------------------------------------------------------------------------

_ID_A = "a" * 64
_ID_B = "b" * 64
_FACTS = SimpleNamespace(runtime_fingerprint_sha256="f" * 64)


def _impl_args(**overrides):
    values = {
        "baseline": None,
        "prepared": None,
        "plan": None,
        "deadline": None,
        "targets": (),
        "adapter": None,
        "jobs": 1,
        "max_mutants": 10,
        "operators": ("python:compare-swap",),
        "process_runner": lambda *a, **k: None,
        "clock": lambda: None,
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"cold_witness": "yes"}, "cold_witness must be a boolean"),
        ({"cold_witness": True}, "cold_witness requires declared_plan, r2_facts, and coverage_facts"),
        (
            {"cold_witness": True, "declared_plan": 1, "r2_facts": SimpleNamespace(runtime_fingerprint_sha256=None),
             "coverage_facts": _FACTS},
            "cold_witness requires runtime fingerprints",
        ),
        (
            {"cold_witness": True, "declared_plan": 1, "r2_facts": _FACTS, "coverage_facts": _FACTS,
             "r2_baseline_s": None},
            "finite non-negative R2 baseline duration",
        ),
        (
            {"cold_witness": True, "declared_plan": 1, "r2_facts": _FACTS, "coverage_facts": _FACTS,
             "r2_baseline_s": -1.0},
            "finite non-negative R2 baseline duration",
        ),
        (
            {"cold_witness": True, "declared_plan": 1, "r2_facts": _FACTS, "coverage_facts": _FACTS,
             "r2_baseline_s": math.inf},
            "finite non-negative R2 baseline duration",
        ),
        (
            {"cold_witness": True, "declared_plan": 1, "r2_facts": _FACTS, "coverage_facts": _FACTS,
             "r2_baseline_s": 1.0, "manifest_node_ids": (1,)},
            "manifest_node_ids must contain strings",
        ),
        ({"declared_plan": 1}, "cold-witness facts require cold_witness=True"),
        ({"manifest_node_ids": ("t::n",)}, "cold-witness facts require cold_witness=True"),
        ({"equivalence_ledger_sha256": "xyz"}, "equivalence_ledger_sha256 must be a SHA-256 digest"),
        ({"candidate_selection": {_ID_A}}, "candidate_selection must be a non-empty frozenset"),
        ({"candidate_selection": frozenset()}, "candidate_selection must be a non-empty frozenset"),
        ({"candidate_selection": frozenset({"nope"})}, "must contain SHA-256 candidate IDs"),
        (
            {"candidate_selection": frozenset({_ID_A}), "shard_index": 0, "shard_count": 2},
            "cannot be combined with sharding",
        ),
        (
            {"candidate_selection": frozenset({_ID_A, _ID_B}), "max_mutants": 1},
            "candidate_selection exceeds max_mutants",
        ),
        (
            {"candidate_selection": frozenset({_ID_A}), "expected_selection_sha256": "xyz"},
            "expected_selection_sha256 must be a SHA-256 digest",
        ),
        (
            {"candidate_selection": frozenset({_ID_A})},
            "candidate_selection requires the caller's authoritative state_root",
        ),
        ({"expected_selection_sha256": _ID_A}, "expected_selection_sha256 requires candidate_selection"),
    ],
)
def test_run_mutation_refuses_inconsistent_arguments_up_front(overrides, message):
    with pytest.raises(ValueError, match=message):
        mutation._run_mutation_impl(**_impl_args(**overrides))


# --------------------------------------------------------------------------
# run_mutation: plan binding and candidate selection against the current plan
# --------------------------------------------------------------------------

_TEXT = "def f():\n    a = True\n    b = True\n    return a, b\n"
_TARGETS = (MutationTarget(path="pkg/f.py", text=_TEXT, lines=frozenset({2, 3})),)
_BOOL_OPS = ("python:bool-const-flip",)


def _pass(argv, *, env, cwd, timeout):
    return completed_process(list(argv), returncode=0, stdout="", stderr="")


def _passing_baseline(tmp_path):
    return execute_command(make_lane(argv=("pytest", "-q")), cwd=tmp_path, process_runner=_pass)


def _run_early(tmp_path, *, adapter=None, targets=_TARGETS, **overrides):
    args = _impl_args(
        baseline=_passing_baseline(tmp_path),
        plan=make_plan(make_lane(argv=("pytest", "-q"))),
        deadline=make_deadline(),
        targets=targets,
        adapter=PythonAdapter() if adapter is None else adapter,
        operators=_BOOL_OPS,
        process_runner=_pass,
        clock=lambda: datetime.now(timezone.utc),
    )
    args.update(overrides)
    return mutation._run_mutation_impl(**args)


class _Unsupported:
    name = "unsupported"

    def generate_mutation_sites(self, text, lines, *, operators, limit):
        return mutation.UNSUPPORTED


class _NoSites:
    name = "no-sites"

    def generate_mutation_sites(self, text, lines, *, operators, limit):
        return ()


def test_a_malformed_campaign_plan_digest_is_refused(tmp_path):
    with pytest.raises(mutation.CampaignPlanMismatchError, match="invalid plan_sha256"):
        _run_early(tmp_path, expected_plan_sha256="not-a-digest")


def test_a_campaign_plan_cannot_bind_to_an_adapter_without_discovery(tmp_path):
    with pytest.raises(mutation.CampaignPlanMismatchError, match="cannot discover that plan"):
        _run_early(tmp_path, adapter=_Unsupported(), expected_plan_sha256=_ID_A)


def test_a_selection_against_a_plan_with_no_candidates_is_invalid_input(tmp_path):
    with pytest.raises(
        mutation.InvalidCandidateSelectionError, match=f"absent from the current mutation plan: {_ID_A}"
    ):
        _run_early(
            tmp_path,
            adapter=_NoSites(),
            candidate_selection=frozenset({_ID_A}),
            state_root=tmp_path / "state",
        )


def test_a_selection_digest_that_differs_from_the_plan_is_refused(tmp_path):
    jobs = collect_mutation_sites(_TARGETS, adapter=PythonAdapter(), operators=_BOOL_OPS, limit=5)
    chosen = frozenset({mutation.candidate_id(jobs[0])})
    with pytest.raises(
        mutation.InvalidCandidateSelectionError, match="selection digest differs from the planned selection"
    ):
        _run_early(
            tmp_path,
            candidate_selection=chosen,
            expected_selection_sha256=_ID_B,
            state_root=tmp_path / "state",
        )


# --------------------------------------------------------------------------
# report path resolution outside the repository lexically
# --------------------------------------------------------------------------


def test_report_path_whose_run_directory_is_outside_the_repository_is_refused(tmp_path):
    repo_top = tmp_path / "repo"
    run_cwd = tmp_path / "elsewhere"
    repo_top.mkdir()
    run_cwd.mkdir()
    with pytest.raises(AssayError, match="resolves outside the snapshot repository") as caught:
        mutation._resolve_report_paths(
            SimpleNamespace(sources={"mod.py": object()}),
            run_cwd=run_cwd,
            repo_top=repo_top,
            source_root_paths=(run_cwd,),
            source_root_files=(),
        )
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


# --------------------------------------------------------------------------
# _execute_mutation_jobs: scripted attempts through a fake snapshot substrate
# --------------------------------------------------------------------------

_ECHO_PLAN = make_plan(make_lane(argv=("echo", "hi")))
_PYTEST_PLAN = make_plan(make_lane(argv=("pytest", "-q")))
_SHA_R2 = "1" * 64
_SHA_DECLARED = "2" * 64
_GOOD = {
    "node_id": "t::n",
    "when": "call",
    "outcome": "failed",
    "session_exit_status": 1,
    "process_exit_status": 1,
}


class _FakeSnapshot:
    def __init__(self, root):
        self.root = root
        self.project_root = root
        self.commit = "c" * 40


class _FakePrepared:
    def __init__(self, root):
        self._root = root

    @contextmanager
    def materialize_replacement(self, **_kwargs):
        yield _FakeSnapshot(self._root)


def _result(plan, outcome="FAIL", returncode=1, reason="COMMAND_FAILED", tree="clean"):
    evidence = process_tree_evidence_dict()
    if tree == "outliving":
        evidence.update(outliving_descendants=1, descendants_signalled=1)
    elif tree != "clean":
        evidence = tree
    return CommandResult(
        plan=plan,
        outcome=Outcome(outcome),
        reason_code=None if reason is None else ReasonCode(reason),
        returncode=returncode,
        started="2026-10-28T00:00:00+00:00",
        ended="2026-10-28T00:00:01+00:00",
        process_tree_evidence=evidence,
    )


def _execute(tmp_path, monkeypatch, steps, *, plan=_ECHO_PLAN, jobs_list=None, **kwargs):
    """Drive ``_execute_mutation_jobs`` with ``steps`` as the successive command results.

    A step is a CommandResult or an exception to raise."""
    monkeypatch.setattr(mutation, "_snapshot_left_dirt", lambda *a, **k: None)
    queue = iter(steps)

    def execute_plan(attempt_plan, **_kwargs):
        step = next(queue)
        if isinstance(step, BaseException):
            raise step
        return step

    job_list = [_job()] if jobs_list is None else jobs_list
    params = dict(
        job_list=job_list,
        deadline=make_deadline(),
        jobs=1,
        prepared=_FakePrepared(tmp_path),
        plan=plan,
        process_runner=_pass,
        clock=lambda: datetime.now(timezone.utc),
        write_progress=None,
        execute_plan=execute_plan,
        resolve_run_cwd=lambda root, plan_: root,
        total=len(job_list),
        candidate_count=len(job_list),
    )
    params.update(kwargs)
    return mutation._execute_mutation_jobs(**params)


def test_cold_witness_refuses_a_command_that_cannot_carry_the_receipt_plugin(tmp_path, monkeypatch):
    with pytest.raises(mutation.R2CommandProofError, match="could not install its trusted receipt plugin"):
        _execute(
            tmp_path, monkeypatch, [], jobs_list=[], cold_witness=True, declared_plan=_ECHO_PLAN
        )


def test_an_attempt_that_dies_releases_the_kill_signal_reservation(tmp_path, monkeypatch):
    closed = []
    real = mutation._arm_artifact_reservation

    def tracking(root, artifact, *, limit):
        reservation = real(root, artifact, limit=limit)
        original = reservation.close
        reservation.close = lambda: (closed.append(artifact), original())[1]
        return reservation

    monkeypatch.setattr(mutation, "_arm_artifact_reservation", tracking)
    with pytest.raises(RuntimeError, match="runner exploded"):
        _execute(
            tmp_path, monkeypatch, [RuntimeError("runner exploded")], kill_signal_artifact="kill.txt"
        )
    assert closed == ["kill.txt"]


def _counters_sequence(monkeypatch, *values):
    queue = iter(values)

    def read():
        value = next(queue)
        if isinstance(value, BaseException):
            return None, str(value)
        return value, None

    monkeypatch.setattr(mutation, "_read_candidate_resource_counters_during_campaign", read)


def _zero_counters():
    return mutation.ResourceLimitCounters(
        pids_max=0, memory_oom=0, memory_max=0, memory_oom_kill=0, memory_oom_group_kill=0
    )


def test_zero_window_observer_loss_is_returned_as_unobserved(monkeypatch):
    monkeypatch.setattr(
        mutation,
        "_read_candidate_resource_counters_during_campaign",
        lambda: (None, "observer disappeared at sweep close"),
    )
    assert mutation._zero_window_resource_limit_evidence() == (
        None,
        "observer disappeared at sweep close",
    )


def test_process_tree_converter_preserves_an_already_typed_proof():
    proof = _tree("signaled")
    result = mutation._dataclass_replace(
        _result(_ECHO_PLAN), process_tree_evidence=proof
    )
    assert mutation._process_tree_evidence_from_result(result) is proof


def test_unobserved_candidate_record_without_a_reason_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(mutation, "_read_candidate_resource_counters", _zero_counters)
    monkeypatch.setattr(
        mutation, "_candidate_resource_limit_evidence", lambda *_args: (None, None, ())
    )
    with pytest.raises(
        mutation.MutationStateError,
        match="unobserved candidate resource-limit evidence lacks a reason",
    ):
        _execute(tmp_path, monkeypatch, [_result(_ECHO_PLAN)])


def test_zero_window_observer_loss_emits_one_advisory_for_unstarted_candidates(
    tmp_path, monkeypatch, capsys
):
    class ExpiredDeadline:
        def remaining(self):
            raise AssayError(
                "lane budget expired",
                outcome=Outcome.BUDGET_EXCEEDED,
                reason_code=ReasonCode.LANE_TIMEOUT,
            )

    monkeypatch.setattr(mutation, "_read_candidate_resource_counters", _zero_counters)
    monkeypatch.setattr(
        mutation,
        "_zero_window_resource_limit_evidence",
        lambda: (None, "observer unavailable at sweep close"),
    )
    result, complete = _execute(
        tmp_path,
        monkeypatch,
        [_result(_ECHO_PLAN)],
        deadline=ExpiredDeadline(),
    )
    assert not complete
    assert len(result.budget_exceeded) == 1
    advisories = [
        line
        for line in capsys.readouterr().err.splitlines()
        if "zero-duration resource-limit sample unobserved" in line
    ]
    assert advisories == [
        "Assay advisory: zero-duration resource-limit sample unobserved for unstarted candidates: observer unavailable at sweep close"
    ]


def test_observer_loss_after_a_failed_command_does_not_replace_command_failure(tmp_path, monkeypatch):
    lost = AssayError("observer gone", outcome=Outcome.ERROR, reason_code=ReasonCode.EXEC_FAILED)
    _counters_sequence(monkeypatch, _zero_counters(), lost)
    command_failure = RuntimeError("command failed to run")
    with pytest.raises(RuntimeError, match="command failed to run"):
        _execute(tmp_path, monkeypatch, [command_failure])


def test_a_resource_limit_event_during_a_failed_command_stops_the_claim(tmp_path, monkeypatch):
    zero = _zero_counters()
    _counters_sequence(monkeypatch, zero, zero)
    hit, _changes = mutation.ResourceLimitEvidence.between(zero, zero)
    hit = mutation._dataclass_replace(
        hit, pids_events_max=mutation.CounterDelta(before=0, after=1, delta=1)
    )
    monkeypatch.setattr(
        mutation, "_candidate_resource_limit_evidence", lambda *_a: (hit, None, ())
    )
    command_failure = RuntimeError("command failed to run")
    with pytest.raises(AssayError, match="resource-limit event during native R2 attempt") as caught:
        _execute(tmp_path, monkeypatch, [command_failure])
    assert caught.value.reason_code is ReasonCode.EXEC_FAILED
    assert caught.value.__cause__ is command_failure


def test_surviving_descendants_make_a_completed_candidate_crashed(tmp_path, monkeypatch):
    result, complete = _execute(tmp_path, monkeypatch, [_result(_ECHO_PLAN, tree="outliving")])
    assert [o.path for o in result.crashed] == ["src/mod.py"]
    assert result.killed == ()


def test_judgeable_pass_with_a_non_signaled_nonterminal_leader_is_crashed(
    tmp_path, monkeypatch
):
    result = mutation._dataclass_replace(
        _result(_ECHO_PLAN, outcome="PASS", returncode=0, reason=None),
        process_tree_evidence={
            **process_tree_evidence_dict(),
            "leader_status": "timeout-killed",
        },
    )
    mutation_result, complete = _execute(tmp_path, monkeypatch, [result])
    assert complete
    assert len(mutation_result.crashed) == 1
    assert mutation_result.survived == ()


def test_judgeable_pass_with_a_signaled_leader_is_crashed(tmp_path, monkeypatch):
    result = mutation._dataclass_replace(
        _result(_ECHO_PLAN, outcome="PASS", returncode=0, reason=None),
        process_tree_evidence={
            **process_tree_evidence_dict(),
            "leader_status": "signaled",
        },
    )
    mutation_result, complete = _execute(tmp_path, monkeypatch, [result])
    assert complete
    assert mutation_result.survived == ()
    assert len(mutation_result.crashed) == 1


def test_a_non_mapping_process_tree_proof_never_reaches_a_recorded_bucket(tmp_path, monkeypatch):
    # The command runner's own None check passes a non-Mapping proof through; classification
    # then proceeds without tree facts, and sweep assembly refuses the unproven candidate.
    with pytest.raises(AssertionError):
        _execute(tmp_path, monkeypatch, [_result(_ECHO_PLAN, tree=["not", "a", "mapping"])])


@pytest.mark.parametrize(
    ("classifier", "kwargs", "message"),
    [
        ("_bucket_for_run", {}, "equivalent candidate lacks a declared equivalence artifact"),
        (
            "_equivalence_bucket",
            {"equivalence_artifact": "eq.txt", "baseline_equivalence": b"baseline"},
            "equivalent candidate lacks matching declared artifact evidence",
        ),
    ],
)
def test_state_records_refuse_an_equivalent_bucket_without_matching_evidence(
    tmp_path, monkeypatch, classifier, kwargs, message
):
    monkeypatch.setattr(mutation, classifier, lambda *a, **k: "equivalent")
    with pytest.raises(mutation.MutationStateError, match=message):
        _execute(
            tmp_path,
            monkeypatch,
            [_result(_ECHO_PLAN)],
            state_root=tmp_path / "state",
            judge="j" * 64,
            **kwargs,
        )


def test_state_records_refuse_an_equivalence_artifact_without_baseline_bytes(tmp_path, monkeypatch):
    # a crashed candidate never consults the baseline, so the record writer is what refuses
    with pytest.raises(mutation.MutationStateError, match="lacks its baseline bytes"):
        _execute(
            tmp_path,
            monkeypatch,
            [_result(_ECHO_PLAN, tree="outliving")],
            state_root=tmp_path / "state",
            judge="j" * 64,
            equivalence_artifact="eq.txt",
        )


# --- cold-witness orchestration with scripted receipts and proofs ------------


class _Receipts:
    """Scripted stand-ins for the receipt/proof helpers ``_run_one`` consults."""

    def __init__(self, monkeypatch):
        self.facts = {}
        self.cold_proof = None
        self.survivor = False
        self.declared_failure = False
        self.replay_witness = None
        self.counter = itertools.count()
        owner = self

        class Capture:
            write_fd = -1

            def __init__(self):
                self.token = next(owner.counter)

            def finish(self):
                return {"token": self.token, "witness_node_id": "t::n"}

        monkeypatch.setattr(mutation, "ReceiptCapture", Capture)
        monkeypatch.setattr(mutation, "supports_sequential_pytest", lambda *a, **k: True)
        monkeypatch.setattr(
            mutation,
            "_inject_witness_plugin",
            lambda plan, **_k: SimpleNamespace(active=True, plan=plan),
        )
        monkeypatch.setattr(mutation, "_make_witness_attempt_plan", lambda plan, **_k: plan)
        monkeypatch.setattr(mutation, "receipt_facts", lambda receipt: self.facts.get(receipt["token"]))
        monkeypatch.setattr(mutation, "cold_witness_from_receipt", lambda *a, **k: self.cold_proof)
        monkeypatch.setattr(mutation, "survivor_proof_ok", lambda *a, **k: self.survivor)
        monkeypatch.setattr(mutation, "declared_failure_proof_ok", lambda *a, **k: self.declared_failure)
        monkeypatch.setattr(mutation, "_replay_witness_from_receipt", lambda *a, **k: self.replay_witness)


def _facts(sha, duplicates=0):
    return SimpleNamespace(
        duplicates=duplicates, collection_sha256=sha, collection_count=3, hook_fingerprint_sha256="3" * 64
    )


def _cold_run(tmp_path, monkeypatch, receipts, steps, **kwargs):
    return _execute(
        tmp_path,
        monkeypatch,
        steps,
        plan=_PYTEST_PLAN,
        cold_witness=True,
        declared_plan=_PYTEST_PLAN,
        r2_facts=_facts(_SHA_R2),
        coverage_facts=_facts(_SHA_DECLARED),
        manifest_node_ids=("t::n",),
        **kwargs,
    )


def _only(result):
    outcomes = [o for name in mutation.MUTATION_BUCKETS for o in getattr(result, name)]
    assert len(outcomes) == 1
    return outcomes[0]


def _bucket_of(result):
    return next(name for name in mutation.MUTATION_BUCKETS if getattr(result, name))


def test_cold_signalled_command_is_crashed_and_keeps_only_matching_r2_evidence(tmp_path, monkeypatch):
    receipts = _Receipts(monkeypatch)
    receipts.facts[0] = _facts("9" * 64)  # collection does not match the R2 receipt
    result, _ = _cold_run(tmp_path, monkeypatch, receipts, [_result(_PYTEST_PLAN, returncode=-9)])
    assert _bucket_of(result) == "crashed"
    assert _only(result).evidence is None


def test_cold_failure_with_a_proof_but_dirty_process_tree_is_not_a_witnessed_kill(tmp_path, monkeypatch):
    receipts = _Receipts(monkeypatch)
    receipts.cold_proof = (_GOOD, 1, 0)
    receipts.facts[0] = _facts(_SHA_R2)
    result, _ = _cold_run(
        tmp_path, monkeypatch, receipts, [_result(_PYTEST_PLAN, tree="outliving")]
    )
    assert _bucket_of(result) == "crashed"
    assert _only(result).execution.mode == "full"
    assert _only(result).evidence is not None


def test_cold_failure_proof_does_not_override_a_non_kill_classifier_result(
    tmp_path, monkeypatch
):
    receipts = _Receipts(monkeypatch)
    receipts.cold_proof = (_GOOD, 1, 0)
    receipts.facts[0] = _facts(_SHA_R2)
    monkeypatch.setattr(
        mutation,
        "_equivalence_bucket",
        lambda *_args, **_kwargs: "equivalent",
    )
    result, _ = _cold_run(
        tmp_path,
        monkeypatch,
        receipts,
        [_result(_PYTEST_PLAN)],
        equivalence_artifact="eq.txt",
        baseline_equivalence=b"baseline",
    )
    assert _bucket_of(result) == "equivalent"
    assert _only(result).execution.mode == "full"
    assert _only(result).evidence is not None


def test_cold_survivor_with_a_proven_receipt_is_final_without_the_declared_rerun(tmp_path, monkeypatch):
    receipts = _Receipts(monkeypatch)
    receipts.survivor = True
    receipts.facts[0] = _facts(_SHA_R2)
    result, _ = _cold_run(
        tmp_path, monkeypatch, receipts, [_result(_PYTEST_PLAN, outcome="PASS", returncode=0, reason=None)]
    )
    assert _bucket_of(result) == "survived"
    assert _only(result).evidence.command == "r2"


def test_cold_error_outcome_is_final_and_crashed(tmp_path, monkeypatch):
    receipts = _Receipts(monkeypatch)
    result, _ = _cold_run(
        tmp_path,
        monkeypatch,
        receipts,
        [_result(_PYTEST_PLAN, outcome="ERROR", returncode=None, reason="EXEC_FAILED")],
    )
    assert _bucket_of(result) == "crashed"


def test_declared_rerun_signalled_command_is_crashed(tmp_path, monkeypatch):
    receipts = _Receipts(monkeypatch)
    receipts.facts[1] = _facts("9" * 64)  # declared receipt collection mismatches the coverage facts
    steps = [_result(_PYTEST_PLAN), _result(_PYTEST_PLAN, returncode=-9)]  # unproven cold failure, then declared
    result, _ = _cold_run(tmp_path, monkeypatch, receipts, steps)
    assert _bucket_of(result) == "crashed"
    assert _only(result).evidence is None


def test_declared_rerun_with_a_non_command_outcome_keeps_its_evidence_and_bucket(tmp_path, monkeypatch):
    receipts = _Receipts(monkeypatch)
    receipts.facts[1] = _facts(_SHA_DECLARED)
    steps = [
        _result(_PYTEST_PLAN),
        _result(_PYTEST_PLAN, outcome="ERROR", returncode=None, reason="EXEC_FAILED"),
    ]
    result, _ = _cold_run(tmp_path, monkeypatch, receipts, steps)
    assert _bucket_of(result) == "crashed"
    assert _only(result).evidence.command == "declared"


def test_declared_failure_without_proof_is_crashed_and_carries_no_witness(tmp_path, monkeypatch):
    receipts = _Receipts(monkeypatch)
    receipts.facts[1] = _facts(_SHA_DECLARED)
    receipts.declared_failure = False
    result, _ = _cold_run(tmp_path, monkeypatch, receipts, [_result(_PYTEST_PLAN), _result(_PYTEST_PLAN)])
    assert _bucket_of(result) == "crashed"
    assert _only(result).execution.witness is None


@pytest.mark.parametrize("facts", ["good", "duplicated", "missing"])
def test_witness_prefix_replay_attaches_cold_evidence_only_from_clean_receipts(tmp_path, monkeypatch, facts):
    receipts = _Receipts(monkeypatch)
    receipts.replay_witness = dict(_GOOD)
    if facts == "good":
        receipts.facts[0] = _facts(_SHA_R2)
    elif facts == "duplicated":
        receipts.facts[0] = _facts(_SHA_R2, duplicates=1)
    reuse = {mutation.candidate_id(_job()): ("a" * 64, "t::n")}
    result, _ = _cold_run(
        tmp_path, monkeypatch, receipts, [_result(_PYTEST_PLAN)], reuse_witnesses=reuse
    )
    killed = _only(result)
    assert _bucket_of(result) == "killed"
    assert killed.execution.mode == "witness-prefix"
    assert (killed.evidence is not None) is (facts == "good")


def test_witness_prefix_replay_without_a_clean_process_tree_falls_back(
    tmp_path, monkeypatch
):
    receipts = _Receipts(monkeypatch)
    receipts.replay_witness = dict(_GOOD)
    receipts.survivor = True
    reuse = {mutation.candidate_id(_job()): ("a" * 64, "t::n")}

    result, _ = _cold_run(
        tmp_path,
        monkeypatch,
        receipts,
        [
            _result(_PYTEST_PLAN, tree="outliving"),
            _result(_PYTEST_PLAN, outcome="PASS", returncode=0, reason=None),
        ],
        reuse_witnesses=reuse,
    )

    assert _bucket_of(result) == "survived"
    assert _only(result).execution.mode == "full"


@pytest.mark.parametrize("replay_behavior", ["returns", "raises"])
def test_witness_prefix_limit_fallback_only_follows_a_completed_replay(
    tmp_path, monkeypatch, capsys, replay_behavior
):
    zero = _zero_counters()
    after_hit = mutation._dataclass_replace(zero, pids_max=1)
    _counters_sequence(monkeypatch, zero, after_hit, zero, zero)
    monkeypatch.setattr(mutation, "_read_candidate_resource_counters", _zero_counters)
    closed = []
    real_reserve = mutation._arm_artifact_reservation

    def tracking_reservation(root, artifact, *, limit):
        reservation = real_reserve(root, artifact, limit=limit)
        original_close = reservation.close
        reservation.close = lambda: (closed.append(artifact), original_close())[1]
        return reservation

    monkeypatch.setattr(mutation, "_arm_artifact_reservation", tracking_reservation)
    reuse = {mutation.candidate_id(_job()): ("a" * 64, "t::n")}
    replay_step = (
        RuntimeError("replay command failed")
        if replay_behavior == "raises"
        else _result(_PYTEST_PLAN)
    )

    if replay_behavior == "raises":
        with pytest.raises(RuntimeError, match="replay command failed"):
            _execute(
                tmp_path,
                monkeypatch,
                [replay_step],
                plan=_PYTEST_PLAN,
                reuse_witnesses=reuse,
                kill_signal_artifact="kill.txt",
            )
        assert closed == ["kill.txt"]
        assert "falling back to the full command" not in capsys.readouterr().err
        return

    result, complete = _execute(
        tmp_path,
        monkeypatch,
        [
            replay_step,
            _result(_PYTEST_PLAN, outcome="PASS", returncode=0, reason=None),
        ],
        plan=_PYTEST_PLAN,
        reuse_witnesses=reuse,
        kill_signal_artifact="kill.txt",
    )

    assert complete
    assert _bucket_of(result) == "survived"
    assert _only(result).execution.mode == "full"
    assert closed == ["kill.txt", "kill.txt"]
    assert any(
        "native R2 witness-prefix replay" in line
        and "falling back to the full command" in line
        for line in capsys.readouterr().err.splitlines()
    )


# --------------------------------------------------------------------------
# resume merge on an incomplete (deadline-cut) campaign
# --------------------------------------------------------------------------


def test_resumed_records_merge_into_a_campaign_that_a_deadline_cut_short(tmp_path):
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/f.py", _TEXT)
    repo.commit_all("seed")
    state_root = tmp_path / "state"
    lane = make_lane(argv=("pytest", "-q"))

    def sweep(runner, *, progress=None):
        scratch = tmp_path / f"scratch-{len(list(tmp_path.iterdir()))}"
        scratch.mkdir()
        baseline = execute_command(lane, cwd=repo.path, process_runner=_pass)
        with prepared_snapshot(repo, scratch_root=scratch) as prepared:
            return mutation.run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=make_plan(lane),
                deadline=make_deadline(),
                targets=_TARGETS,
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=10,
                operators=_BOOL_OPS,
                process_runner=runner,
                clock=lambda: datetime.now(timezone.utc),
                state_root=state_root,
                resume=True,
                progress_artifact=progress,
            )

    def candidate_runner(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return _pass(argv, env=env, cwd=cwd, timeout=timeout)
        return completed_process(list(argv), returncode=0)

    first = sweep(candidate_runner)
    assert first.total == 2
    records = sorted(p for p in state_root.iterdir() if not p.name.startswith("."))
    assert len(records) == 2
    records[0].unlink()  # one candidate must re-execute on the next sweep

    def cut_short(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return _pass(argv, env=env, cwd=cwd, timeout=timeout)
        raise timeout_expired(list(argv), timeout)

    progress = tmp_path / "progress.jsonl"
    second = sweep(cut_short, progress=progress)
    assert len(second.budget_exceeded) == 1
    assert len(second.survived) == 1  # the resumed record is kept beside the cut-short candidate
    events = [json.loads(line)["event"] for line in progress.read_text().splitlines()]
    assert "resume_merged" in events


def test_close_if_open_closes_a_descriptor_and_tolerates_a_failed_open():
    import os

    from assay import mutation

    read_end, write_end = os.pipe()
    try:
        mutation._close_if_open(write_end)
        with pytest.raises(OSError):
            os.fstat(write_end)
        mutation._close_if_open(None)  # nothing was opened: nothing to close
    finally:
        os.close(read_end)


def test_unregister_started_group_forgets_a_started_group_and_ignores_a_failed_spawn(monkeypatch):
    from types import SimpleNamespace

    from assay import liveness

    forgotten = []
    monkeypatch.setattr(liveness, "unregister_live_group", forgotten.append)
    liveness._unregister_started_group(SimpleNamespace(pid=4321))
    liveness._unregister_started_group(None)
    assert forgotten == [4321]
