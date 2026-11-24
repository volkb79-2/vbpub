"""P-assay-12: the one store-combine API and its policy order."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import GitRepo

from assay import cli, mutation, mutation_store, reuse
from assay.resource_limits import ResourceLimitCounters
from test_flex_r2_campaigns import (
    _STRICT_AB,
    _event,
    _executed,
    _run,
    _seed,
)
from test_mutation_judge_identity import _record

_JUDGE = "d" * 64
_EARLIER = "a" * 64
_NODE = "tests/test_flags.py::test_flags"


@pytest.fixture(autouse=True)
def _stable_resource_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    zero = ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )
    monkeypatch.setattr(mutation, "_read_candidate_resource_counters", lambda: zero)


def _jobs() -> tuple[mutation.MutantJob, mutation.MutantJob]:
    source = "def first():\n    return True\n\ndef second():\n    return True\n"
    raw = source.encode("utf-8")
    first = raw.index(b"True")
    second = raw.index(b"True", first + 4)
    jobs = []
    for start, lineno, description in (
        (first, 2, "first True->False"),
        (second, 5, "second True->False"),
    ):
        jobs.append(
            mutation.MutantJob(
                path="pkg/flags.py",
                original_text=source,
                site=mutation.MutationSite(
                    start_byte=start,
                    end_byte=start + 4,
                    replacement=b"False",
                    lineno=lineno,
                    operator="python:bool-const-flip",
                    description=description,
                ),
            )
        )
    return jobs[0], jobs[1]


def _payload(
    job: mutation.MutantJob,
    *,
    judge: str = _JUDGE,
    bucket: str = "survived",
    **overrides: object,
) -> dict[str, object]:
    return _record(job, judge=judge, outcome_bucket=bucket, **overrides)


def _write(store: Path, payload: dict[str, object]) -> None:
    mutation_store._write_mutation_state_record(store, payload)


def _request(
    jobs: tuple[mutation.MutantJob, ...],
    target: Path,
    *,
    carry_over_reason: str | None = None,
    events: list[dict[str, object]] | None = None,
) -> mutation_store.CombineRequest:
    return mutation_store.CombineRequest(
        jobs=jobs,
        judge=_JUDGE,
        state_root=target,
        state_root_fd=None,
        state_root_guard=None,
        campaign_deadline_sha256=None,
        context=mutation_store.RecordContext(),
        resume=True,
        rejudge_ids=frozenset(),
        rejudge_outcomes=frozenset(),
        resource_sha256=None,
        carry_over_reason=carry_over_reason,
        clock=lambda: datetime(2026, 11, 24, tzinfo=timezone.utc),
        deadline_check=lambda: 1.0,
        write_progress=(
            None if events is None else lambda event: events.append(dict(event))
        ),
        total=len(jobs),
    )


def _arm_shard(store: Path, job: mutation.MutantJob) -> None:
    _write(store, _payload(job, bucket="killed"))
    ids = [mutation.candidate_id(job)]
    mutation_store._write_shard_manifest(
        store,
        state_root_fd=None,
        shard_index=0,
        shard_count=1,
        judge=_JUDGE,
        shard_ids=ids,
    )


def test_same_judge_policy_reads_the_own_store(tmp_path: Path) -> None:
    job, _second = _jobs()
    target = tmp_path / "target"
    own = _payload(job, bucket="killed")
    _write(target, own)

    result = mutation_store.combine((), _request((job,), target))

    assert result.records == (own,)
    assert result.rejected_total == 0
    assert result.hints == ()


def test_combine_without_a_judge_is_empty_only_when_no_store_is_requested(
    tmp_path: Path,
) -> None:
    job, _second = _jobs()
    request = replace(_request((job,), tmp_path / "target"), judge=None, resume=False)

    result = mutation_store.combine((), request)

    assert result.records == ()
    assert result.rejudged_candidate_ids == frozenset()
    assert result.hints == ()
    with pytest.raises(ValueError, match="requires a judge identity"):
        mutation_store.combine(
            (),
            replace(request, resume=True),
        )
    with pytest.raises(ValueError, match="requires a judge identity"):
        mutation_store.combine(
            (mutation_store.StoreSource(path=tmp_path / "store", policy="same-judge"),),
            request,
        )


def test_combine_refuses_store_sources_without_resume(tmp_path: Path) -> None:
    job, _second = _jobs()
    with pytest.raises(ValueError, match="requires resume=True"):
        mutation_store.combine(
            (
                mutation_store.StoreSource(
                    path=tmp_path / "store",
                    policy="same-judge",
                ),
            ),
            replace(
                _request((job,), tmp_path / "target"),
                resume=False,
            ),
        )


def test_strict_shard_policy_proves_then_copies(tmp_path: Path) -> None:
    job, _second = _jobs()
    source = tmp_path / "shard"
    _arm_shard(source, job)
    events: list[dict[str, object]] = []

    result = mutation_store.combine(
        (mutation_store.StoreSource(path=source, policy="strict-shard"),),
        _request((job,), tmp_path / "target", events=events),
    )

    assert [record["outcome_bucket"] for record in result.records] == ["killed"]
    assert [event["event"] for event in events] == ["shard_merge", "carry_over"]


def test_import_unverified_policy_adds_provenance(tmp_path: Path) -> None:
    job, _second = _jobs()
    source = tmp_path / "older"
    _write(source, _payload(job, judge=_EARLIER, bucket="survived"))

    result = mutation_store.combine(
        (
            mutation_store.StoreSource(
                path=source, policy="import-unverified"
            ),
        ),
        _request(
            (job,),
            tmp_path / "target",
            carry_over_reason="the judge change does not affect this source",
        ),
    )

    assert len(result.records) == 1
    record = result.records[0]
    assert record["judge_sha256"] == _EARLIER
    assert mutation_store.record_effective_judge(record) == _JUDGE
    assert result.imported_total == 1
    assert record["provenance"]["kind"] == "imported"


def test_carry_forward_policy_carries_a_survivor(tmp_path: Path) -> None:
    job, _second = _jobs()
    source = tmp_path / "prior"
    _write(source, _payload(job, judge=_EARLIER, bucket="survived"))

    result = mutation_store.combine(
        (
            mutation_store.StoreSource(
                path=source, policy="carry-forward"
            ),
        ),
        _request((job,), tmp_path / "target"),
    )

    assert len(result.records) == 1
    record = result.records[0]
    assert record["provenance"]["kind"] == "carried"
    assert result.imported_total == 1
    assert result.hints == ()


def test_carry_forward_policy_requeues_a_kill_as_a_hint(tmp_path: Path) -> None:
    job, _second = _jobs()
    source = tmp_path / "prior"
    witness = {
        "node_id": _NODE,
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    _write(
        source,
        _payload(
            job,
            judge=_EARLIER,
            bucket="killed",
            execution={"mode": "full", "witness": witness},
        ),
    )

    result = mutation_store.combine(
        (
            mutation_store.StoreSource(
                path=source, policy="carry-forward"
            ),
        ),
        _request((job,), tmp_path / "target"),
    )

    assert result.records == ()
    assert result.hints == (
        mutation_store.Hint(
            candidate_id=mutation.candidate_id(job),
            node_id=_NODE,
            source="store",
            source_ref=str(source),
            from_candidate_id=mutation.candidate_id(job),
            from_judge_sha256=_EARLIER,
            match="exact-id",
        ),
    )


def test_policy_precedence_keeps_own_then_merge_then_shard_then_import(
    tmp_path: Path,
) -> None:
    job, _second = _jobs()
    target = tmp_path / "target"
    own = _payload(job, bucket="survived")
    _write(target, own)
    merge_source = tmp_path / "merge"
    _write(merge_source, _payload(job, bucket="killed"))
    shard_source = tmp_path / "shard"
    _arm_shard(shard_source, job)
    import_source = tmp_path / "import"
    _write(import_source, _payload(job, judge=_EARLIER, bucket="killed"))

    result = mutation_store.combine(
        (
            mutation_store.StoreSource(
                path=merge_source, policy="same-judge"
            ),
            mutation_store.StoreSource(
                path=shard_source, policy="strict-shard"
            ),
            mutation_store.StoreSource(
                path=import_source, policy="import-unverified"
            ),
        ),
        _request(
            (job,),
            target,
            carry_over_reason="precedence test",
        ),
    )

    assert result.records == (own,)


def test_same_judge_precedes_carry_forward_and_hints_do_not_replace_it(
    tmp_path: Path,
) -> None:
    job, _second = _jobs()
    target = tmp_path / "target"
    own = _payload(job, bucket="survived")
    _write(target, own)
    earlier = tmp_path / "earlier"
    witness = {
        "node_id": _NODE,
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    _write(
        earlier,
        _payload(
            job,
            judge=_EARLIER,
            bucket="killed",
            execution={"mode": "full", "witness": witness},
        ),
    )

    result = mutation_store.combine(
        (
            mutation_store.StoreSource(
                path=earlier, policy="carry-forward"
            ),
        ),
        _request((job,), target),
    )

    assert result.records == (own,)
    assert result.hints == ()


def test_store_flag_policies_are_the_capability_contract() -> None:
    assert cli._MUTATION_STORE_POLICIES is mutation_store.STORE_FLAG_POLICIES
    assert cli.capabilities_document()["mutation_store_policies"] == dict(
        mutation_store.STORE_FLAG_POLICIES
    )
    assert cli.capabilities_document()["combine_policies"] == list(
        mutation_store.COMBINE_POLICIES
    )


def test_flex_options_store_sources_keep_policy_and_cli_order(tmp_path: Path) -> None:
    paths = tuple(
        tmp_path / name
        for name in ("a", "b", "shard", "manifest", "import", "forward")
    )
    options = mutation_store.FlexOptions(
        merge_sources=(paths[0], paths[1]),
        merge_manifest=paths[3],
        carry_over_sources=(paths[4],),
        carry_over_reason="provenance",
        carry_forward_sources=(paths[5],),
    )
    assert [
        (source.path, source.policy) for source in options.store_sources()
    ] == [
        (paths[0], "same-judge"),
        (paths[1], "same-judge"),
        (paths[3], "owner-manifest"),
        (paths[4], "import-unverified"),
        (paths[5], "carry-forward"),
    ]
    assert options.reads_other_stores

    shard = mutation_store.FlexOptions(shard_stores=(paths[2],))
    assert shard.store_sources() == (
        mutation_store.StoreSource(path=paths[2], policy="strict-shard"),
    )
    owner = mutation_store.FlexOptions(merge_manifest=paths[3])
    assert owner.store_sources() == (
        mutation_store.StoreSource(path=paths[3], policy="owner-manifest"),
    )
    assert owner.reads_other_stores
    assert not owner.reads_carry_over_stores


def test_combine_source_projection_refuses_untyped_or_multiple_manifests(
    tmp_path: Path,
) -> None:
    path = tmp_path / "store"
    with pytest.raises(ValueError, match="unknown combine policy"):
        mutation_store.combine(
            (mutation_store.StoreSource(path=path, policy="not-a-policy"),),  # type: ignore[arg-type]
            _request((_jobs()[0],), tmp_path / "target"),
        )
    with pytest.raises(ValueError, match="must be Path objects"):
        mutation_store._flex_from_sources(
            [mutation_store.StoreSource(path="store", policy="same-judge")],  # type: ignore[arg-type]
            carry_over_reason=None,
        )
    with pytest.raises(ValueError, match="at most one owner manifest"):
        mutation_store._flex_from_sources(
            [
                mutation_store.StoreSource(
                    path=tmp_path / "one.json",
                    policy="owner-manifest",
                ),
                mutation_store.StoreSource(
                    path=tmp_path / "two.json",
                    policy="owner-manifest",
                ),
            ],
            carry_over_reason=None,
        )


def test_flex_options_refuses_manifest_conflicts_at_library_boundary(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="strict shard merge"):
        mutation_store.FlexOptions(
            merge_manifest=tmp_path / "manifest.json",
            shard_stores=(tmp_path / "shard",),
        )
    with pytest.raises(ValueError, match="candidate selection"):
        mutation_store.FlexOptions(
            merge_manifest=tmp_path / "manifest.json",
            only_candidates=frozenset({mutation.candidate_id(_jobs()[0])}),
        )
    with pytest.raises(ValueError, match="must be a path or None"):
        mutation_store.FlexOptions(
            merge_manifest="manifest.json",  # type: ignore[arg-type]
        )


def test_run_mutation_refuses_manifest_without_resume_or_state_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="merge-manifest requires resume=True"):
        mutation.run_mutation(
            baseline=None,
            prepared=None,
            plan=None,
            deadline=None,
            targets=(),
            adapter=None,
            jobs=1,
            max_mutants=1,
            operators=(),
            process_runner=None,
            clock=None,
            resume=False,
            state_root=tmp_path,
            flex=mutation.FlexOptions(merge_manifest=tmp_path / "manifest.json"),
        )
    with pytest.raises(ValueError, match="authoritative state_root"):
        mutation.run_mutation(
            baseline=None,
            prepared=None,
            plan=None,
            deadline=None,
            targets=(),
            adapter=None,
            jobs=1,
            max_mutants=1,
            operators=(),
            process_runner=None,
            clock=None,
            resume=True,
            state_root=None,
            flex=mutation.FlexOptions(merge_manifest=tmp_path / "manifest.json"),
        )


def test_hint_adapter_projects_to_eligible_witnesses() -> None:
    job, _second = _jobs()
    candidate = mutation.candidate_id(job)
    source = reuse.ReuseSource(
        path=Path("/prior/verdict.json"),
        schema_version=20,
        sha256="b" * 64,
        document={},
        cold_start=False,
        complete_unsharded_native=True,
        outcomes={
            candidate: (
                "killed",
                {
                    "execution": {
                        "mode": "full",
                        "witness": {
                            "node_id": _NODE,
                            "when": "call",
                            "outcome": "failed",
                            "session_exit_status": 1,
                            "process_exit_status": 1,
                        },
                    }
                },
            )
        },
        candidate_ids=frozenset({candidate}),
    )

    hints = reuse.hints_from_verdict(source, sequential_pytest_supported=True)
    assert hints == (
        mutation_store.Hint(
            candidate_id=candidate,
            node_id=_NODE,
            source="verdict",
            source_ref="b" * 64,
            from_candidate_id=candidate,
            from_judge_sha256=None,
            match="exact-id",
        ),
    )
    assert reuse.eligible_witnesses(
        source, sequential_pytest_supported=True
    ) == {candidate: ("b" * 64, _NODE)}
    assert reuse.hints_from_verdict(
        source, sequential_pytest_supported=False
    ) == ()
    assert reuse.hints_from_verdict(
        replace(source, complete_unsharded_native=False),
        sequential_pytest_supported=True,
    ) == ()


def test_a_real_run_is_unchanged_when_store_hints_are_present(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(git_repo, _STRICT_AB, sites=2)
    prior = tmp_path / "prior"
    code, first = _run(git_repo, prior, tmp_path / "first.jsonl")
    assert code == 0
    assert _executed(first) == ["killed", "killed"]
    judge_file = git_repo.path / "tests/judge.sh"
    judge_file.write_text(
        judge_file.read_text(encoding="utf-8") + "# new judge identity\n",
        encoding="utf-8",
    )
    git_repo.commit_all("change judge identity")

    original = mutation_store._carry_forward_records
    seen_hints: list[mutation_store.Hint] = []

    def with_hint(*args: object, **kwargs: object):
        result = original(*args, **kwargs)
        candidate = next(iter(prior.glob("*.json"))).stem
        hint = mutation_store.Hint(
            candidate_id=candidate,
            node_id=_NODE,
            source="store",
            source_ref=str(prior),
            from_candidate_id=candidate,
            from_judge_sha256=_EARLIER,
            match="exact-id",
        )
        seen_hints.append(hint)
        return replace(result, hints=(hint,))

    monkeypatch.setattr(mutation_store, "_carry_forward_records", with_hint)
    code, events = _run(
        git_repo,
        tmp_path / "target",
        tmp_path / "second.jsonl",
        "--carry-forward",
        str(prior),
    )

    assert code == 0
    assert seen_hints
    assert _executed(events) == ["killed", "killed"]
    assert _event(events, "carry_forward")["kills_requeued"] == 2


def test_merge_manifest_progress_event_is_in_the_closed_vocabulary() -> None:
    assert "merge_manifest" in mutation.PROGRESS_EVENTS
