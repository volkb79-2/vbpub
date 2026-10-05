from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import (
    GitRepo,
    make_deadline,
    make_lane,
    make_plan,
    native_outcome,
    prepared_snapshot,
)
from assay.adapters.python import PythonAdapter
from assay.errors import Outcome, ReasonCode
from assay.mutation import (
    MutationTarget,
    _resource_limit_bucket,
    judge_mutation,
    run_mutation,
)
from assay.resource_limits import (
    CounterDelta,
    ResourceLimitCounters,
    ResourceLimitEvidence,
    ResourceLimitObservationError,
    read_current_cgroup_counters,
)
from assay.runner import execute_command
from assay.verdict import MUTATION_BUCKETS, MutantOutcome, Mutation
from assay.verify import _check_b145_resource_limit_evidence


def _evidence(
    *,
    pids: tuple[int, int] = (0, 0),
    oom_kill: tuple[int, int] = (0, 0),
    oom_group_kill: tuple[int, int] = (0, 0),
) -> ResourceLimitEvidence:
    return ResourceLimitEvidence(
        pids_events_max=CounterDelta.between(*pids),
        memory_events_oom_kill=CounterDelta.between(*oom_kill),
        memory_events_oom_group_kill=CounterDelta.between(*oom_group_kill),
    )


def test_reader_resolves_current_cgroup_from_kernel_mount_records(tmp_path: Path):
    cgroup_dir = tmp_path / "cgroup" / "lane"
    cgroup_dir.mkdir(parents=True)
    (cgroup_dir / "pids.events").write_text("max 3\n", encoding="ascii")
    (cgroup_dir / "memory.events").write_text(
        "oom 1\noom_kill 2\noom_group_kill 0\n", encoding="ascii"
    )
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/worker/lane\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 /worker {tmp_path}/cgroup rw - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )

    assert read_current_cgroup_counters(
        cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
    ) == ResourceLimitCounters(
        pids_max=3, memory_oom_kill=2, memory_oom_group_kill=0
    )


def test_reader_fails_closed_when_a_required_controller_counter_is_missing(
    tmp_path: Path,
):
    cgroup_dir = tmp_path / "cgroup"
    cgroup_dir.mkdir()
    (cgroup_dir / "pids.events").write_text("max 0\n", encoding="ascii")
    (cgroup_dir / "memory.events").write_text("oom_kill 0\n", encoding="ascii")
    cgroup_file = tmp_path / "proc-cgroup"
    cgroup_file.write_text("0::/\n", encoding="utf-8")
    mountinfo_file = tmp_path / "mountinfo"
    mountinfo_file.write_text(
        f"31 23 0:28 / {cgroup_dir} rw - cgroup2 cgroup rw\n",
        encoding="utf-8",
    )

    with pytest.raises(ResourceLimitObservationError, match="oom_group_kill"):
        read_current_cgroup_counters(
            cgroup_file=cgroup_file, mountinfo_file=mountinfo_file
        )


def test_resource_evidence_parser_rejects_forged_counter_arithmetic():
    raw = _evidence(pids=(3, 4)).to_dict()
    raw["pids_events"]["max"]["delta"] = 0

    with pytest.raises(ValueError, match="after minus before"):
        ResourceLimitEvidence.from_dict(raw)


@pytest.mark.parametrize(
    "evidence",
    [
        _evidence(pids=(0, 1)),
        _evidence(oom_kill=(1, 2)),
        _evidence(oom_group_kill=(0, 1)),
    ],
)
def test_any_resource_limit_delta_overrides_a_test_failure(evidence):
    assert _resource_limit_bucket("killed", evidence) == "crashed"
    assert _resource_limit_bucket("survived", evidence) == "crashed"


def test_unchanged_counters_leave_a_real_test_failure_killed():
    assert _resource_limit_bucket("killed", _evidence()) == "killed"


def test_crashed_resource_limited_outcome_makes_r2_an_error():
    outcome = replace(
        native_outcome(
            path="src/mod.py",
            lineno=1,
            start_byte=0,
            end_byte=1,
            replacement_sha256="a" * 64,
            operator="python:compare-swap",
            description="x < y",
        ),
        resource_limit_evidence=_evidence(pids=(4, 5)),
    )
    mutation = Mutation(
        candidate_count=1,
        total=1,
        **{
            name: (outcome,) if name == "crashed" else ()
            for name in MUTATION_BUCKETS
        },
        candidate_ids=(outcome.candidate_id,),
    )
    status = judge_mutation(
        baseline=type("Baseline", (), {"outcome": Outcome.PASS, "reason_code": None})(),
        mutation=mutation,
    )

    assert status == (Outcome.ERROR, ReasonCode.EXEC_FAILED)


def test_model_rejects_a_resource_limited_candidate_as_killed():
    outcome = replace(
        native_outcome(
            path="src/mod.py",
            lineno=1,
            start_byte=0,
            end_byte=1,
            replacement_sha256="a" * 64,
            operator="python:compare-swap",
            description="x < y",
        ),
        resource_limit_evidence=_evidence(oom_kill=(3, 4)),
    )
    with pytest.raises(ValueError, match="must be in crashed"):
        Mutation(
            candidate_count=1,
            total=1,
            **{
                name: (outcome,) if name == "killed" else ()
                for name in MUTATION_BUCKETS
            },
            candidate_ids=(outcome.candidate_id,),
        )


def test_native_outcome_requires_resource_evidence():
    outcome = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    )

    with pytest.raises(ValueError, match="requires resource_limit_evidence"):
        replace(outcome, resource_limit_evidence=None)


def test_ingested_outcome_cannot_claim_local_resource_evidence():
    with pytest.raises(ValueError, match="ingested MutantOutcome cannot carry"):
        MutantOutcome(
            path="src/mod.py",
            lineno=1,
            start_byte=0,
            end_byte=1,
            replacement_sha256="a" * 64,
            operator="python:compare-swap",
            description="x < y",
            resource_limit_evidence=_evidence(),
        )


def test_raw_verifier_rejects_a_positive_counter_delta_in_killed():
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    entry["resource_limit_evidence"] = _evidence(pids=(0, 1)).to_dict()
    failures: list[str] = []

    _check_b145_resource_limit_evidence("killed", entry, failures)

    assert any(
        "positive cgroup resource-limit counter delta" in item for item in failures
    )


def test_raw_verifier_accepts_a_positive_counter_delta_in_crashed():
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    entry["resource_limit_evidence"] = _evidence(oom_group_kill=(0, 1)).to_dict()
    failures: list[str] = []

    _check_b145_resource_limit_evidence("crashed", entry, failures)

    assert failures == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("delta", 0), ("before", True)],
)
def test_raw_verifier_independently_rejects_invalid_counter_arithmetic(field, value):
    entry = native_outcome(
        path="src/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="x < y",
    ).to_dict()
    counter = entry["resource_limit_evidence"]["pids_events"]["max"]
    counter.update(before=4, after=5, delta=1)
    counter[field] = value
    failures: list[str] = []

    _check_b145_resource_limit_evidence("crashed", entry, failures)

    assert failures
    assert any("pids_events.max" in item for item in failures)


def test_candidate_counter_delta_is_persisted_and_overrides_a_kill(
    tmp_path: Path, monkeypatch
):
    source = "def flags():\n    return True\n"
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", source)
    repo.commit_all("add flags")

    def baseline_runner(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(list(argv), returncode=0)

    baseline = execute_command(
        make_lane(argv=("pytest", "-q")),
        cwd=repo.path,
        process_runner=baseline_runner,
    )
    assert baseline.outcome is Outcome.PASS

    samples = iter(
        (
            ResourceLimitCounters(
                pids_max=0, memory_oom_kill=0, memory_oom_group_kill=0
            ),  # mutation preflight
            ResourceLimitCounters(
                pids_max=0, memory_oom_kill=0, memory_oom_group_kill=0
            ),  # before candidate
            ResourceLimitCounters(
                pids_max=1, memory_oom_kill=0, memory_oom_group_kill=0
            ),  # after candidate
        )
    )
    monkeypatch.setattr(
        "assay.mutation.read_current_cgroup_counters", lambda: next(samples)
    )

    def failing_candidate_runner(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(list(argv), returncode=1)

    state_root = tmp_path / "state"
    progress = tmp_path / "progress.jsonl"
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(make_lane(argv=("pytest", "-q"))),
            deadline=make_deadline(),
            targets=(
                MutationTarget(path="pkg/flags.py", text=source, lines=frozenset({2})),
            ),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=failing_candidate_runner,
            clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
            state_root=state_root,
            progress_artifact=progress,
        )

    assert not isinstance(result, str)
    assert result.killed == ()
    assert len(result.crashed) == result.total == 1
    expected = _evidence(pids=(0, 1)).to_dict()
    state_record, = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in state_root.glob("*.json")
    ]
    progress_records = [
        json.loads(line)
        for line in progress.read_text(encoding="utf-8").splitlines()
    ]
    progress_record, = [
        record for record in progress_records if record.get("event") == "candidate"
    ]
    assert (
        state_record["outcome_bucket"]
        == progress_record["outcome_bucket"]
        == "crashed"
    )
    assert state_record["resource_limit_evidence"] == expected
    assert progress_record["resource_limit_evidence"] == expected


@pytest.mark.skipif(
    os.environ.get("ASSAY_B145_LOW_PIDS_PROBE") != "1",
    reason="requires the dedicated tester-unified --pids-limit acceptance container",
)
def test_low_pids_limit_event_cannot_become_a_kill():
    before = read_current_cgroup_counters()
    code = "\n".join(
        (
            "import errno, os, time",
            "children = []",
            "try:",
            "    for _ in range(64):",
            "        child = os.fork()",
            "        if child == 0:",
            "            time.sleep(1)",
            "            os._exit(0)",
            "        children.append(child)",
            "except OSError as exc:",
            "    if exc.errno != errno.EAGAIN:",
            "        raise",
            "finally:",
            "    for child in children:",
            "        os.waitpid(child, 0)",
        )
    )
    try:
        subprocess.run([sys.executable, "-c", code], check=False, timeout=10)
    except OSError as exc:
        assert exc.errno == errno.EAGAIN
    after = read_current_cgroup_counters()
    evidence = ResourceLimitEvidence.between(before, after)

    assert evidence.pids_events_max.delta > 0
    assert _resource_limit_bucket("killed", evidence) == "crashed"
