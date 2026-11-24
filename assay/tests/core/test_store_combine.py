"""P-assay-12: the one store-combine API and its policy order."""

from __future__ import annotations

import ast
import json
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


def _job(source: str) -> mutation.MutantJob:
    raw = source.encode("utf-8")
    start = raw.index(b"True")
    return mutation.MutantJob(
        path="pkg/flags.py",
        original_text=source,
        site=mutation.MutationSite(
            start_byte=start,
            end_byte=start + 4,
            replacement=b"False",
            lineno=source[:start].count("\n") + 1,
            operator="python:bool-const-flip",
            description="True->False",
        ),
    )


def _payload(
    job: mutation.MutantJob,
    *,
    judge: str = _JUDGE,
    bucket: str = "survived",
    **overrides: object,
) -> dict[str, object]:
    payload = _record(job, judge=judge, outcome_bucket=bucket)
    payload.update(mutation_store.candidate_identity_fields(job))
    payload.update(overrides)
    return payload


def _write(store: Path, payload: dict[str, object]) -> None:
    mutation_store._write_mutation_state_record(store, payload)


def _request(
    jobs: tuple[mutation.MutantJob, ...],
    target: Path,
    *,
    carry_over_reason: str | None = None,
    events: list[dict[str, object]] | None = None,
) -> mutation_store.CombineRequest:
    target.mkdir(parents=True, exist_ok=True)
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


def test_mutation_store_record_classes_are_constructed_with_keywords_only() -> None:
    source = Path(mutation_store.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    records = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(
            (
                isinstance(decorator, ast.Name)
                and decorator.id == "record"
            )
            or (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "record"
            )
            for decorator in node.decorator_list
        )
    }
    offenders: list[str] = []
    for path in Path(mutation_store.__file__).parent.rglob("*.py"):
        path_tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(path_tree):
            if not isinstance(call, ast.Call):
                continue
            name = (
                call.func.id
                if isinstance(call.func, ast.Name)
                else call.func.attr
                if isinstance(call.func, ast.Attribute)
                else None
            )
            if name in records and (
                call.args or any(keyword.arg is None for keyword in call.keywords)
            ):
                offenders.append(f"{path.name}:{call.lineno}: {name}")

    assert not offenders, offenders


def _arm_shard(
    store: Path, job: mutation.MutantJob, *, bucket: str = "killed"
) -> None:
    _write(store, _payload(job, bucket=bucket))
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


@pytest.mark.parametrize(
    ("kind", "current_source", "prior_source"),
    [
        ("exact-id", "def flag():\n    return True\n", "def flag():\n    return True\n"),
        (
            "locus",
            "def flag():\n    return True\n",
            "def flag():\n    return True\n\nOTHER = 1\n",
        ),
        (
            "window",
            (
                "def flag():\n"
                "    marker = 1\n"
                "    padding = 0\n"
                "    padding = 1\n"
                "    padding = 2\n"
                "    padding = 3\n"
                "    return True\n"
                "    padding = 4\n"
            ),
            (
                "def flag():\n"
                "    marker = 2\n"
                "    padding = 0\n"
                "    padding = 1\n"
                "    padding = 2\n"
                "    padding = 3\n"
                "    return True\n"
                "    padding = 4\n"
            ),
        ),
    ],
)
def test_carry_forward_kill_hint_names_the_prior_candidate(
    tmp_path: Path,
    kind: str,
    current_source: str,
    prior_source: str,
) -> None:
    current = _job(current_source)
    prior = _job(prior_source)
    witness = {
        "node_id": _NODE,
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    fields = mutation_store.candidate_identity_fields(prior)
    fields.update(mutation_store._locus_fields_for_job(prior))
    source = tmp_path / "prior"
    _write(
        source,
        _payload(
            prior,
            judge=_EARLIER,
            bucket="killed",
            execution={"mode": "full", "witness": witness},
            **fields,
        ),
    )

    result = mutation_store.combine(
        (mutation_store.StoreSource(path=source, policy="carry-forward"),),
        _request((current,), tmp_path / "target"),
    )

    assert result.records == ()
    assert result.hints == (
        mutation_store.Hint(
            candidate_id=mutation.candidate_id(current),
            node_id=_NODE,
            source="store",
            source_ref=str(source),
            from_candidate_id=mutation.candidate_id(prior),
            from_judge_sha256=_EARLIER,
            match=kind,
        ),
    )


def test_carry_forward_kill_hint_keeps_a_chained_prior_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_source = "def flag():\n    return True\n"
    current = _job(current_source)
    prior = _job(current_source + "\nOTHER = 1\n")
    origin = _job(current_source + "\nORIGIN = 1\n")
    candidate = mutation.candidate_id(current)
    witness = {
        "node_id": _NODE,
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    source = tmp_path / "prior"
    source.mkdir()
    payload = {
        "candidate_id": mutation.candidate_id(prior),
        "judge_sha256": _EARLIER,
        "outcome_bucket": "killed",
        "provenance": {
            "kind": "carried",
            "from_candidate_id": mutation.candidate_id(origin),
            "from_judge_sha256": _EARLIER,
            "match": "locus",
            "reason": mutation_store.CARRY_FORWARD_REASON,
        },
    }
    match = mutation_store._CarryForwardMatch(
        kind="locus",
        source=source,
        payload=payload,
        effective_judge=_EARLIER,
    )
    checked = {
        **payload,
        "candidate_id": candidate,
        "execution": {"mode": "full", "witness": witness},
    }
    monkeypatch.setattr(
        mutation_store,
        "_carry_forward_match_candidates",
        lambda *_args, **_kwargs: ({candidate: (match,)}, 0, 0, 1),
    )
    monkeypatch.setattr(
        mutation_store,
        "_validate_carry_forward_match",
        lambda *_args, **_kwargs: checked,
    )

    result = mutation_store.combine(
        (mutation_store.StoreSource(path=source, policy="carry-forward"),),
        _request((current,), tmp_path / "target"),
    )

    assert result.hints == (
        mutation_store.Hint(
            candidate_id=candidate,
            node_id=_NODE,
            source="store",
            source_ref=str(source),
            from_candidate_id=mutation.candidate_id(origin),
            from_judge_sha256=_EARLIER,
            match="locus",
        ),
    )


def test_policy_precedence_keeps_own_then_merge_then_shard_then_import(
    tmp_path: Path,
) -> None:
    job, _second = _jobs()
    own = _payload(job, bucket="survived")
    merge_against_own_source = tmp_path / "merge-against-own"
    _write(
        merge_against_own_source,
        _payload(job, bucket="killed"),
    )
    merge_source = tmp_path / "merge"
    merge_record = _payload(job, bucket="survived")
    _write(merge_source, merge_record)
    shard_source = tmp_path / "shard"
    shard_record = _payload(job, bucket="survived")
    _arm_shard(shard_source, job, bucket="survived")
    import_source = tmp_path / "import"
    import_record = _payload(job, judge=_EARLIER, bucket="survived")
    _write(import_source, import_record)
    merge = mutation_store.StoreSource(path=merge_source, policy="same-judge")
    shard = mutation_store.StoreSource(path=shard_source, policy="strict-shard")
    imported = mutation_store.StoreSource(
        path=import_source, policy="import-unverified"
    )

    own_target = tmp_path / "own-target"
    _write(own_target, own)
    result = mutation_store.combine(
        (
            mutation_store.StoreSource(
                path=merge_against_own_source, policy="same-judge"
            ),
            shard,
            imported,
        ),
        _request((job,), own_target, carry_over_reason="precedence test"),
    )
    assert result.records == (own,)

    merge_target = tmp_path / "merge-target"
    merge_events: list[dict[str, object]] = []
    result = mutation_store.combine(
        (merge, shard, imported),
        _request(
            (job,), merge_target, carry_over_reason="precedence test", events=merge_events
        ),
    )
    assert result.records == (merge_record,)
    assert "provenance" not in result.records[0]
    merge_carry = next(
        event for event in merge_events if event.get("event") == "carry_over"
    )
    assert (merge_carry["copied_total"], merge_carry["imported_total"]) == (1, 0)

    shard_target = tmp_path / "shard-target"
    shard_events: list[dict[str, object]] = []
    result = mutation_store.combine(
        (shard, imported),
        _request(
            (job,), shard_target, carry_over_reason="precedence test", events=shard_events
        ),
    )
    assert result.records == (shard_record,)
    assert "provenance" not in result.records[0]
    shard_carry = next(
        event for event in shard_events if event.get("event") == "carry_over"
    )
    assert (shard_carry["copied_total"], shard_carry["imported_total"]) == (1, 0)

    import_target = tmp_path / "import-target"
    result = mutation_store.combine(
        (imported,),
        _request((job,), import_target, carry_over_reason="precedence test"),
    )
    assert result.records[0]["candidate_id"] == mutation.candidate_id(job)
    assert result.records[0]["provenance"]["kind"] == "imported"
    assert result.imported_total == 1


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


@pytest.mark.parametrize(
    "conflict",
    [
        {"shard_index": 0, "shard_count": 1},
        {"candidate_selection": frozenset({"c" * 64})},
    ],
)
def test_run_mutation_refuses_a_manifest_inside_a_slice(
    tmp_path: Path, conflict: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="merge_manifest is the whole-lane merge"):
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
            state_root=tmp_path,
            flex=mutation.FlexOptions(merge_manifest=tmp_path / "manifest.json"),
            **conflict,
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


def _zero_timings(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                0.0
                if (
                    key.endswith("_seconds")
                    or (
                        key == "phase_seconds"
                        and isinstance(item, dict)
                    )
                )
                and isinstance(item, (int, float))
                and not isinstance(item, bool)
                else (
                    _zero_timings(item)
                    if key != "phase_seconds"
                    else {
                        phase: (
                            0.0
                            if isinstance(seconds, (int, float))
                            and not isinstance(seconds, bool)
                            else seconds
                        )
                        for phase, seconds in item.items()
                    }
                )
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_zero_timings(item) for item in value]
    return value


def _first_differences(
    left: object,
    right: object,
    path: tuple[object, ...] = (),
) -> list[tuple[tuple[object, ...], object, object]]:
    if isinstance(left, dict) and isinstance(right, dict):
        differences = []
        for key in sorted(left.keys() | right.keys(), key=str):
            if key not in left or key not in right:
                differences.append((path + (key,), left.get(key), right.get(key)))
            else:
                differences.extend(
                    _first_differences(left[key], right[key], path + (key,))
                )
        return differences
    if isinstance(left, list) and isinstance(right, list):
        differences = []
        for index in range(max(len(left), len(right))):
            if index >= len(left) or index >= len(right):
                differences.append(
                    (
                        path + (index,),
                        None if index >= len(left) else left[index],
                        None if index >= len(right) else right[index],
                    )
                )
            else:
                differences.extend(
                    _first_differences(left[index], right[index], path + (index,))
                )
        return differences
    return [] if left == right else [(path, left, right)]


def _verdict_semantics(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))

    def without_timing(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: without_timing(item)
                for key, item in value.items()
                if key not in {"started", "ended"}
                and not key.endswith("_at")
                and not key.endswith("_seconds")
            }
        if isinstance(value, list):
            return [without_timing(item) for item in value]
        return value

    return {
        "outcome": document["outcome"],
        "claims": without_timing(document["claims"]),
    }


def test_a_real_run_is_unchanged_when_store_hints_are_present(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(git_repo, _STRICT_AB, sites=2)
    git_repo.write(".gitignore", ".assay/\n")
    git_repo.commit_all("ignore carry-forward stores")
    prior = git_repo.path / ".assay" / "prior"
    code, first = _run(git_repo, prior, tmp_path / "first.jsonl")
    assert code == 0
    assert _executed(first) == ["killed", "killed"]
    prior_hints = tuple(
        mutation_store.Hint(
            candidate_id=record["candidate_id"],
            node_id=_NODE,
            source="store",
            source_ref=str(prior),
            from_candidate_id=record["candidate_id"],
            from_judge_sha256=record["judge_sha256"],
            match="exact-id",
        )
        for path in sorted(prior.glob("*.json"))
        for record in (json.loads(path.read_text(encoding="utf-8")),)
    )
    git_repo.write("tests/judge.sh", _STRICT_AB + "# new judge identity\n")
    git_repo.commit_all("change judge identity")

    original_carry = mutation_store._carry_forward_records
    original_write = mutation_store._write_mutation_state_record
    seen_hints: list[mutation_store.Hint] = []
    hints_enabled = False

    def deterministic_write(
        state_root: Path,
        payload: dict[str, object],
        **kwargs: object,
    ) -> None:
        stable = _zero_timings(payload)
        assert isinstance(stable, dict)
        original_write(state_root, stable, **kwargs)

    def carry_with_optional_hints(*args: object, **kwargs: object):
        result = original_carry(*args, **kwargs)
        if hints_enabled:
            seen_hints.extend(prior_hints)
            return replace(result, hints=(*result.hints, *prior_hints))
        return replace(result, hints=())

    monkeypatch.setattr(mutation_store, "_write_mutation_state_record", deterministic_write)
    monkeypatch.setattr(
        mutation_store, "_carry_forward_records", carry_with_optional_hints
    )
    no_hint_target = tmp_path / "without-hints"
    no_hint_verdict = tmp_path / "without-hints-verdict.json"
    code, no_hint_events = _run(
        git_repo,
        no_hint_target,
        tmp_path / "without-hints.jsonl",
        "--carry-forward",
        str(prior),
        verdict=no_hint_verdict,
    )
    assert code == 0
    assert _executed(no_hint_events) == ["killed", "killed"]
    assert _event(no_hint_events, "carry_forward")["kills_requeued"] == 2

    hints_enabled = True
    hinted_target = tmp_path / "with-hints"
    hinted_verdict = tmp_path / "with-hints-verdict.json"
    code, hinted_events = _run(
        git_repo,
        hinted_target,
        tmp_path / "with-hints.jsonl",
        "--carry-forward",
        str(prior),
        verdict=hinted_verdict,
    )
    assert code == 0
    assert len(seen_hints) == 2
    assert [
        (event.get("candidate_id"), event.get("outcome_bucket"))
        for event in no_hint_events
        if event.get("event") == "candidate"
    ] == [
        (event.get("candidate_id"), event.get("outcome_bucket"))
        for event in hinted_events
        if event.get("event") == "candidate"
    ]
    no_hint_records = {
        path.name: path.read_bytes() for path in sorted(no_hint_target.glob("*.json"))
    }
    hinted_records = {
        path.name: path.read_bytes() for path in sorted(hinted_target.glob("*.json"))
    }
    assert len(no_hint_records) == 2
    record_differences = [
        difference
        for name in sorted(no_hint_records)
        for difference in _first_differences(
            json.loads(no_hint_records[name]),
            json.loads(hinted_records[name]),
            (name,),
        )
    ]
    assert not record_differences, record_differences
    assert no_hint_records == hinted_records
    assert _verdict_semantics(no_hint_verdict) == _verdict_semantics(hinted_verdict)


def test_merge_manifest_progress_event_is_in_the_closed_vocabulary() -> None:
    assert "merge_manifest" in mutation.PROGRESS_EVENTS
