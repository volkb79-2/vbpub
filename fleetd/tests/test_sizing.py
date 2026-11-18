"""Sizing derivation, per-worker units and the measurement recorder."""

from __future__ import annotations

import json

import pytest

from fleetd.cli import main
from fleetd.contracts import ContractError, validate_lane_demand
from fleetd.measurement import (
    MEASURED_BUCKETS,
    MEASUREMENT_SCHEMA,
    load_measurement,
    measurement_document,
    validate_measurement,
)
from fleetd.sizing import (
    DEFAULT_MARGIN,
    SIZING_SCHEMA,
    TAIL_Z,
    HostSizingInput,
    SizingInput,
    derive_sizing,
    sizing_input_from_plan,
)

_MIB = 1024**2
IMAGE = "sha256:" + "a" * 64


def _cid(index):
    return f"{index:064d}"


def _demand(**overrides):
    demand = {
        "unit": "candidate",
        "instance_memory": "250m",
        "unit_memory": "120m",
        "unit_seconds_ref": 95,
        "efficient_upper_bound": 3,
        "baseline_seconds": 400,
        "worker_memory": "120m",
        "worker_cpu": 1,
        "instance_cpu": 1,
    }
    demand.update(overrides)
    return demand


def _plan(hosts, candidates_per_host, *, demand=None):
    rows = []
    index = 0
    host_rows = []
    for host_id, caps in hosts:
        assigned = []
        for _ in range(candidates_per_host):
            assigned.append({"id": _cid(index), "file": "src/a.py", "memory_bytes": 120 * _MIB})
            index += 1
        host_rows.append({
            "id": host_id,
            "status": "available",
            "cpu_worker_slots": caps["cpu"],
            "budget_bytes": caps["budget"],
            "assigned": assigned,
            "batches": [],
        })
    return {
        "schema": "fleet-plan/1",
        "lane": "r2",
        "demand": validate_lane_demand(_demand(**(demand or {}))),
        "execution": {"schema": "fleet-plan-execution/1", "commit": "b" * 40, "project": "assay"},
        "hosts": host_rows,
        "unplaced": [],
        "terminal": [],
        "eta_seconds": 1.0,
    }


def _sizing(**overrides):
    values = {
        "candidate_count": 100,
        "per_candidate_seconds": 60.0,
        "per_candidate_memory_bytes": 120 * _MIB,
        "basis": "declared",
        "worker_cpu": 1.0,
        "worker_memory_bytes": 120 * _MIB,
        "instance_memory_bytes": 250 * _MIB,
        "instance_cpu": 1.0,
        "max_workers_per_instance": 3,
        "baseline_seconds": 400.0,
        "hosts": (
            HostSizingInput("host-a", cpu_slots=8, memory_budget_bytes=4096 * _MIB, assigned_candidates=50),
            HostSizingInput("host-b", cpu_slots=4, memory_budget_bytes=2048 * _MIB, assigned_candidates=50),
        ),
    }
    values.update(overrides)
    return SizingInput(**values)


# ------------------------------------------------------------------ demand
def test_per_worker_units_are_optional_but_validated_when_declared():
    parsed = validate_lane_demand(_demand())
    assert parsed["worker_memory_bytes"] == 120 * _MIB
    assert parsed["worker_cpu"] == 1.0
    assert parsed["instance_cpu"] == 1.0
    assert validate_lane_demand(_demand(worker_memory="119m"))["worker_memory_bytes"] == 119 * _MIB
    legacy = _demand()
    for key in ("worker_memory", "worker_cpu", "instance_cpu"):
        legacy.pop(key)
    assert "worker_cpu" not in validate_lane_demand(legacy)
    cases = (
        (_demand(worker_memory="0m"), "invalid memory value"),
        (_demand(worker_memory="200m"), "must not exceed unit_memory"),
        (_demand(worker_cpu=0), "must be at least"),
        (_demand(worker_cpu=True), "expected number"),
        (_demand(worker_cpu=10**400), "expected finite number"),
        (_demand(instance_cpu=-1), "must be at least 0"),
        (_demand(instance_cpu="1"), "expected number"),
        (_demand(instance_cpu=10**400), "expected finite number"),
    )
    for demand, message in cases:
        with pytest.raises(ContractError, match=message):
            validate_lane_demand(demand)


# ------------------------------------------------------------------ sizing
def test_sizing_derives_instances_shards_and_budgets_from_the_declared_unit():
    document = derive_sizing(_sizing())
    assert document["schema"] == SIZING_SCHEMA
    assert document["basis"] == "declared"
    assert document["worker_unit"] == {"cpu": 1.0, "memory_bytes": 120 * _MIB}
    assert document["instance_unit"] == {"cpu": 1.0, "memory_bytes": 250 * _MIB}
    host_a, host_b = document["hosts"]
    # 4096 MiB budget, 250 MiB instance, 120 MiB worker -> 32 workers by RAM
    # and 7 by CPU slots; the lane upper bound (3) wins.
    assert host_a["workers_per_instance"] == 3
    assert host_a["instance_memory_bytes"] == 250 * _MIB + 3 * 120 * _MIB
    assert host_a["instance_cpu"] == 4.0
    # 4096 // 610 = 6 instances by RAM, 8 // 4 = 2 by CPU -> 2
    assert host_a["instances"] == 2
    # 2048 MiB holds (2048-250)/120 = 14 workers by RAM and (4-1)/1 = 3 by
    # CPU, so the lane upper bound (3) wins again; 2048 // 610 = 3 instances
    # by RAM but 4 // 4 = 1 by CPU.
    assert host_b["workers_per_instance"] == 3
    assert host_b["instances"] == 1
    assert document["shard_count"] == 3
    assert [shard["host"] for shard in document["shards"]] == ["host-a", "host-a", "host-b"]
    assert sum(shard["candidate_count"] for shard in document["shards"]) == 100
    assert sorted(shard["candidate_count"] for shard in document["shards"]) == [33, 33, 34]
    for shard in document["shards"]:
        assert shard["jobs"] == document["hosts"][0 if shard["host"] == "host-a" else 1]["workers_per_instance"]
        assert shard["budget_seconds"] >= document["minimum_budget_seconds"]
        assert shard["outer_budget_seconds"] == shard["budget_seconds"] + 900
    assert document["campaign_estimate_seconds"] == max(
        shard["estimate_seconds"] for shard in document["shards"]
    )


def test_sizing_honours_the_lane_upper_bound_and_the_stack_charge():
    document = derive_sizing(_sizing(max_workers_per_instance=6))
    assert document["hosts"][0]["workers_per_instance"] == 6
    assert document["hosts"][0]["instance_cpu"] == 7.0   # 1 fixed + 6 workers
    assert document["hosts"][0]["instances"] == 1         # 8 // 7
    assert document["shard_count"] == 2                   # host-b: 4 // 4 = 1


def test_sizing_refuses_a_host_that_cannot_hold_one_worker():
    document = derive_sizing(_sizing(hosts=(
        HostSizingInput("tiny", cpu_slots=0, memory_budget_bytes=100 * _MIB, assigned_candidates=5),
        HostSizingInput("ok", cpu_slots=4, memory_budget_bytes=2048 * _MIB, assigned_candidates=5),
    )))
    assert document["hosts"][0]["status"] == "refused"
    assert "does not fit" in document["hosts"][0]["reason"]
    assert document["hosts"][0]["instances"] == 0
    assert document["shard_count"] == 1
    assert document["shards"][0]["host"] == "ok"


def test_sizing_counts_workers_beside_the_fixed_instance_charge():
    # 640 MiB holds (640 - 250) / 120 = 3 workers beside the instance charge;
    # charging the whole 640 MiB as worker room would claim 5.
    document = derive_sizing(_sizing(max_workers_per_instance=6, hosts=(
        HostSizingInput("lean", cpu_slots=8, memory_budget_bytes=640 * _MIB, assigned_candidates=5),
    )))
    assert document["hosts"][0]["workers_per_instance"] == 3
    assert document["hosts"][0]["instance_memory_bytes"] == 610 * _MIB
    assert document["hosts"][0]["instances"] == 1


def test_sizing_refuses_a_host_with_no_worker_slots_left_beside_the_instance():
    document = derive_sizing(_sizing(hosts=(
        HostSizingInput("narrow", cpu_slots=1, memory_budget_bytes=4096 * _MIB, assigned_candidates=5),
        HostSizingInput("ok", cpu_slots=4, memory_budget_bytes=2048 * _MIB, assigned_candidates=5),
    )))
    assert document["hosts"][0]["status"] == "refused"
    assert "does not fit the host's memory budget" in document["hosts"][0]["reason"]


def test_sizing_refuses_when_no_host_holds_an_instance_at_all():
    with pytest.raises(ContractError, match="no planned host can hold one instance"):
        derive_sizing(_sizing(hosts=(
            HostSizingInput("tiny", cpu_slots=1, memory_budget_bytes=300 * _MIB, assigned_candidates=5),
        )))


def test_sizing_charges_the_baseline_of_an_idle_shard_too():
    document = derive_sizing(_sizing(candidate_count=1, hosts=(
        HostSizingInput("a", cpu_slots=8, memory_budget_bytes=4096 * _MIB, assigned_candidates=1),
        HostSizingInput("b", cpu_slots=8, memory_budget_bytes=4096 * _MIB, assigned_candidates=0),
    )))
    assert document["shard_count"] == 4
    assert document["shards"][0]["candidate_count"] == 1
    empty = [shard for shard in document["shards"] if shard["candidate_count"] == 0]
    assert all(shard["estimate_seconds"] == 400.0 for shard in empty)


def test_sizing_bounds_instances_by_the_whole_instance_charge_not_the_worker_unit():
    # CPU alone would allow eight instances; the RAM budget holds only six
    # whole instances (4096 MiB / (250 + 3 x 120) MiB), and that is the
    # binding limit.
    document = derive_sizing(_sizing(
        max_workers_per_instance=3,
        hosts=(HostSizingInput("big", cpu_slots=32, memory_budget_bytes=4096 * _MIB, assigned_candidates=10),),
    ))
    assert document["hosts"][0]["workers_per_instance"] == 3
    assert document["hosts"][0]["instance_memory_bytes"] == 610 * _MIB
    assert document["hosts"][0]["instances"] == 6
    assert document["shard_count"] == 6


def test_sizing_budget_applies_the_margin_above_the_minimum():
    document = derive_sizing(
        _sizing(
            candidate_count=1, baseline_seconds=0.0, per_candidate_seconds=1200.0,
            max_workers_per_instance=1, instance_cpu=0.0,
            hosts=(HostSizingInput("a", cpu_slots=4, memory_budget_bytes=4096 * _MIB, assigned_candidates=1),),
        ),
        margin=1.5,
    )
    shard = document["shards"][0]
    assert shard["estimate_seconds"] == 1200.0
    # 1200 s x 1.5 = 1800 s; without the margin the budget would be the
    # estimate itself (1200 s), which a single overrun candidate would break.
    assert shard["budget_seconds"] == 1800
    assert shard["outer_budget_seconds"] == 2700


def test_sizing_budget_divides_the_candidate_time_by_the_worker_count():
    document = derive_sizing(
        _sizing(
            candidate_count=90, baseline_seconds=100.0, per_candidate_seconds=60.0,
            max_workers_per_instance=3, instance_cpu=1.0,
            hosts=(HostSizingInput("a", cpu_slots=4, memory_budget_bytes=700 * _MIB, assigned_candidates=90),),
        ),
        margin=1.0,
        minimum_budget_seconds=60,
    )
    shard = document["shards"][0]
    assert document["hosts"][0]["workers_per_instance"] == 3
    assert shard["jobs"] == 3
    # 90 candidates x 60 s over THREE workers = 1800 s, plus the 100 s baseline.
    # Multiplying by the worker count would give 16300 s; ignoring it, 5500 s.
    assert shard["estimate_seconds"] == 1900.0
    assert shard["budget_seconds"] == 1920


def test_sizing_budget_rounds_up_to_whole_minutes_with_the_margin():
    document = derive_sizing(
        _sizing(
            candidate_count=1, baseline_seconds=0.0, per_candidate_seconds=61.0,
            max_workers_per_instance=1, instance_cpu=0.0,
            hosts=(HostSizingInput("a", cpu_slots=4, memory_budget_bytes=4096 * _MIB, assigned_candidates=1),),
        ),
        margin=1.1,
    )
    shard = document["shards"][0]
    # A single candidate with no baseline is under the 15-minute floor.
    assert shard["budget_seconds"] == 900
    assert document["minimum_budget_seconds"] == 900
    assert shard["estimate_seconds"] == 61.0


def test_sizing_refuses_every_degenerate_input():
    derive_cases = (
        ({"margin": 0.5}, "finite factor of at least 1.0"),
        ({"margin": float("inf")}, "finite factor of at least 1.0"),
        ({"minimum_budget_seconds": 0}, "must be at least 1"),
        ({"startup_allowance_seconds": -1}, "must be at least 0"),
    )
    for kwargs, message in derive_cases:
        with pytest.raises(ContractError, match=message):
            derive_sizing(_sizing(), **kwargs)
    cases = (
        ("basis", "guessed", "expected one of"),
        ("candidate_count", 0, "empty candidate set is nothing to judge"),
        ("candidate_count", "1", "expected an integer"),
        ("per_candidate_seconds", 0.0, "positive"),
        ("per_candidate_seconds", float("nan"), "positive"),
        ("per_candidate_memory_bytes", 0, "must be at least 1"),
        ("worker_cpu", 0.0, "positive"),
        ("worker_cpu", True, "expected a positive number"),
        ("worker_cpu", "1", "expected a positive number"),
        ("worker_memory_bytes", 0, "must be at least 1"),
        ("instance_memory_bytes", 0, "must be at least 1"),
        ("instance_cpu", -1.0, "non-negative finite number"),
        ("instance_cpu", "1", "non-negative number"),
        ("instance_cpu", float("inf"), "non-negative finite number"),
        ("max_workers_per_instance", 0, "must be at least 1"),
        ("baseline_seconds", -1.0, "non-negative finite number"),
        ("baseline_seconds", "1", "non-negative number"),
        ("baseline_seconds", float("nan"), "non-negative finite number"),
        ("hosts", (), "at least one planned host"),
    )
    for field, value, message in cases:
        with pytest.raises(ContractError, match=message):
            derive_sizing(_sizing(**{field: value}))
    with pytest.raises(ContractError, match="cpu_slots"):
        derive_sizing(_sizing(hosts=(HostSizingInput("a", cpu_slots=-1, memory_budget_bytes=1, assigned_candidates=1),)))
    with pytest.raises(ContractError, match="memory_budget_bytes"):
        derive_sizing(_sizing(hosts=(HostSizingInput("a", cpu_slots=1, memory_budget_bytes=True, assigned_candidates=1),)))
    with pytest.raises(ContractError, match="assigned_candidates"):
        derive_sizing(_sizing(hosts=(HostSizingInput("a", cpu_slots=1, memory_budget_bytes=1, assigned_candidates="1"),)))


# --------------------------------------------------- sizing from a plan
def test_sizing_input_reads_the_plan_and_declares_its_basis():
    plan = _plan((("host-a", {"cpu": 8, "budget": 4096 * _MIB}),), 2)
    sizing = sizing_input_from_plan(plan)
    assert sizing.basis == "declared"
    assert sizing.candidate_count == 2
    assert sizing.per_candidate_seconds == 95
    assert sizing.worker_memory_bytes == 120 * _MIB
    assert sizing.instance_cpu == 1.0
    assert [host.host_id for host in sizing.hosts] == ["host-a"]
    assert sizing.hosts[0].cpu_slots == 8
    assert sizing.hosts[0].memory_budget_bytes == 4096 * _MIB

    document = derive_sizing(sizing)
    assert document["shard_count"] == 2


def test_sizing_input_prefers_a_measurement_over_the_declared_estimate():
    plan = _plan((("host-a", {"cpu": 8, "budget": 4096 * _MIB}),), 2)
    measurement = {
        "schema": MEASUREMENT_SCHEMA,
        "summary": {"elapsed_seconds_median": 61.0, "memory_peak_bytes_median": 200 * _MIB},
    }
    sizing = sizing_input_from_plan(plan, measurement=measurement)
    assert sizing.basis == "measured"
    assert sizing.per_candidate_seconds == 61.0
    assert sizing.per_candidate_memory_bytes == 200 * _MIB
    # An explicit override is DECLARED again: the operator typed it.
    override = sizing_input_from_plan(
        plan, measurement=measurement, per_candidate_seconds=45.0, per_candidate_memory_bytes=100 * _MIB,
    )
    assert override.basis == "declared"
    assert override.per_candidate_seconds == 45.0
    flat = {"elapsed_seconds_median": 61.0, "memory_peak_bytes_median": 200 * _MIB}
    assert sizing_input_from_plan(plan, measurement=flat).basis == "measured"


def test_sizing_input_refuses_missing_units_and_odd_plans():
    plan = _plan((("host-a", {"cpu": 8, "budget": 4096 * _MIB}),), 2)
    with pytest.raises(ContractError, match="expected fleet-plan/1"):
        sizing_input_from_plan({"schema": "other"})
    for key, name in (
        ("worker_memory_bytes", "worker_memory"),
        ("worker_cpu", "worker_cpu"),
        ("instance_cpu", "instance_cpu"),
    ):
        broken = json.loads(json.dumps(plan))
        broken["demand"].pop(key)
        with pytest.raises(ContractError, match=f"demand.{name}"):
            sizing_input_from_plan(broken)
    broken = json.loads(json.dumps(plan))
    del broken["demand"]
    with pytest.raises(ContractError, match="plan.demand"):
        sizing_input_from_plan(broken)
    broken = json.loads(json.dumps(plan))
    broken["hosts"][0]["cpu_worker_slots"] = "8"
    with pytest.raises(ContractError, match="cpu_worker_slots"):
        sizing_input_from_plan(broken)
    broken = json.loads(json.dumps(plan))
    broken["hosts"][0]["budget_bytes"] = True
    with pytest.raises(ContractError, match="budget_bytes"):
        sizing_input_from_plan(broken)
    broken = json.loads(json.dumps(plan))
    broken["hosts"][0]["assigned"] = "nope"
    with pytest.raises(ContractError, match="assigned"):
        sizing_input_from_plan(broken)
    broken = json.loads(json.dumps(plan))
    broken["unplaced"] = "nope"
    with pytest.raises(ContractError, match="unplaced/terminal"):
        sizing_input_from_plan(broken)
    no_hosts = json.loads(json.dumps(plan))
    no_hosts["hosts"][0]["status"] = "refused"
    with pytest.raises(ContractError, match="at least one available host"):
        sizing_input_from_plan(no_hosts)
    with pytest.raises(ContractError, match="elapsed_seconds_median"):
        sizing_input_from_plan(plan, measurement={"summary": {"elapsed_seconds_median": 1.0}})
    with pytest.raises(ContractError, match="measurement.summary"):
        sizing_input_from_plan(plan, measurement={"summary": ["nope"]})
    # Unplaced and terminal candidates are counted: they still need judging.
    counted = json.loads(json.dumps(plan))
    counted["unplaced"] = [{"id": _cid(99), "reason": "too big"}]
    counted["terminal"] = [{"id": _cid(98), "reason": "retry used"}]
    assert sizing_input_from_plan(counted).candidate_count == 4


# ------------------------------------------------------------- measurement
def test_measurement_records_each_candidate_and_summarizes_judged_ones():
    ids = [_cid(index) for index in range(6)]
    records = [
        {"candidate_id": ids[0], "outcome_bucket": "killed", "elapsed_seconds": 60.0, "memory_peak_bytes": 100 * _MIB},
        {"candidate_id": ids[1], "outcome_bucket": "survived", "elapsed_seconds": 90.0, "memory_peak_bytes": None},
        {"candidate_id": ids[2], "outcome_bucket": "budget_exceeded", "elapsed_seconds": 30.0, "memory_peak_bytes": 300 * _MIB},
        {"candidate_id": ids[3], "outcome_bucket": "hung", "elapsed_seconds": -1, "memory_peak_bytes": 0},
        {"candidate_id": ids[5], "outcome_bucket": "not-a-bucket", "elapsed_seconds": True},
        {"candidate_id": "f" * 64, "outcome_bucket": "killed", "elapsed_seconds": 1.0},
    ]
    document = measurement_document(
        campaign="c", lane="r2", commit="b" * 40, image_digest=IMAGE,
        planned_ids=ids, records=records, baseline_seconds=400.0,
    )
    assert document["schema"] == MEASUREMENT_SCHEMA
    assert document["baseline_seconds"] == 400.0
    rows = {row["candidate_id"]: row for row in document["candidates"]}
    assert rows[ids[0]]["elapsed_seconds"] == 60.0
    assert rows[ids[1]]["memory_peak_bytes"] is None
    assert rows[ids[3]]["elapsed_seconds"] is None
    assert rows[ids[3]]["memory_peak_bytes"] is None
    assert rows[ids[4]]["outcome_bucket"] is None
    # An unknown bucket is recorded as None, never folded into a real one.
    assert rows[ids[5]] == {
        "candidate_id": ids[5], "outcome_bucket": None,
        "elapsed_seconds": None, "memory_peak_bytes": None,
    }
    summary = document["summary"]
    assert summary["measured_candidates"] == 2
    assert summary["elapsed_seconds_median"] == 75.0
    assert summary["elapsed_seconds_min"] == 60.0
    assert summary["elapsed_seconds_max"] == 90.0
    # Only the killed candidate carried a usable memory peak.
    assert summary["memory_peak_bytes_median"] == 100 * _MIB
    assert summary["memory_peak_bytes_max"] == 100 * _MIB
    assert summary["memory_measured_candidates"] == 1
    # The foreign record ("f"*64 is not a planned candidate) contributes
    # nothing at all, so one killed candidate is counted.
    assert summary["buckets"] == {
        "killed": 1, "survived": 1, "budget_exceeded": 1, "hung": 1,
        "crashed": 0, "equivalent": 0,
    }
    assert MEASURED_BUCKETS == ("killed", "survived")


def test_measurement_with_no_measurement_is_all_null_never_zero():
    document = measurement_document(
        campaign="c", lane="r2", commit="b" * 40, image_digest=None,
        planned_ids=[_cid(0)], records=[], baseline_seconds=None,
    )
    assert document["candidates"][0] == {
        "candidate_id": _cid(0), "outcome_bucket": None,
        "elapsed_seconds": None, "memory_peak_bytes": None,
    }
    assert document["summary"]["elapsed_seconds_median"] is None
    assert document["summary"]["memory_peak_bytes_median"] is None
    assert document["summary"]["elapsed_seconds_min"] is None
    assert document["summary"]["elapsed_seconds_max"] is None
    assert document["summary"]["memory_peak_bytes_max"] is None
    assert document["baseline_seconds"] is None
    with pytest.raises(ContractError, match="planned_ids"):
        measurement_document(
            campaign="c", lane="r2", commit="b" * 40, image_digest=None,
            planned_ids=["nope"], records=[],
        )


def test_measurement_validation_is_strict(tmp_path):
    good = measurement_document(
        campaign="c", lane="r2", commit="b" * 40, image_digest=IMAGE,
        planned_ids=[_cid(0)],
        records=[{"candidate_id": _cid(0), "outcome_bucket": "killed", "elapsed_seconds": 10.0}],
    )
    path = tmp_path / "m.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    assert load_measurement(path) == good
    assert validate_measurement(good) == good
    cases = (
        ({"schema": "other/1"}, "expected fleet-r2-measurement/1"),
        ({"schema": MEASUREMENT_SCHEMA, "campaign": "", "lane": "r2", "commit": "c",
         "summary": {"elapsed_seconds_median": 1, "memory_peak_bytes_median": 2}}, "campaign"),
        ({"schema": MEASUREMENT_SCHEMA, "campaign": "c", "lane": "", "commit": "c",
         "summary": {"elapsed_seconds_median": 1, "memory_peak_bytes_median": 2}}, "lane"),
        ({"schema": MEASUREMENT_SCHEMA, "campaign": "c", "lane": "r2", "commit": "",
         "summary": {"elapsed_seconds_median": 1, "memory_peak_bytes_median": 2}}, "commit"),
        ({"schema": MEASUREMENT_SCHEMA, "campaign": "c", "lane": "r2", "commit": "c"}, "summary"),
        ({"schema": MEASUREMENT_SCHEMA, "campaign": "c", "lane": "r2", "commit": "c",
         "summary": {"memory_peak_bytes_median": 2}}, "elapsed_seconds_median"),
        ({"schema": MEASUREMENT_SCHEMA, "campaign": "c", "lane": "r2", "commit": "c",
         "summary": {"elapsed_seconds_median": 0, "memory_peak_bytes_median": 2}},
         "elapsed_seconds_median"),
        ({"schema": MEASUREMENT_SCHEMA, "campaign": "c", "lane": "r2", "commit": "c",
         "summary": {"elapsed_seconds_median": True, "memory_peak_bytes_median": 2}},
         "elapsed_seconds_median"),
        ({"schema": MEASUREMENT_SCHEMA, "campaign": "c", "lane": "r2", "commit": "c",
         "summary": {"elapsed_seconds_median": float("inf"), "memory_peak_bytes_median": 2}},
         "elapsed_seconds_median"),
        (["nope"], "expected a JSON object"),
    )
    for document, message in cases:
        with pytest.raises(ContractError, match=message):
            validate_measurement(document)
    with pytest.raises(ContractError, match="cannot be read"):
        load_measurement(tmp_path / "absent.json")
    with pytest.raises(ContractError, match="not valid JSON"):
        (tmp_path / "bad.json").write_text("{", encoding="utf-8")
        load_measurement(tmp_path / "bad.json")


# --------------------------------------------------------------------- CLI
def test_sizing_cli_derives_the_document_from_a_plan_file(tmp_path, capsys):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan((("host-a", {"cpu": 8, "budget": 4096 * _MIB}),), 4)), encoding="utf-8")
    assert main(["sizing", str(plan_path), "--minimum-budget", "20m", "--startup-allowance", "1h"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["minimum_budget_seconds"] == 1200
    assert document["startup_allowance_seconds"] == 3600
    assert document["basis"] == "declared"


def test_sizing_cli_accepts_measured_and_per_candidate_overrides(tmp_path, capsys):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan((("host-a", {"cpu": 8, "budget": 4096 * _MIB}),), 4)), encoding="utf-8")
    assert main([
        "sizing", str(plan_path), "--per-candidate-seconds", "45",
        "--per-candidate-memory", "200m",
    ]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["per_candidate_seconds"] == 45.0
    assert document["per_candidate_memory_bytes"] == 200 * _MIB
    assert document["basis"] == "declared"


def test_sizing_cli_joins_a_measurement_and_rejects_bad_durations(tmp_path, capsys):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan((("host-a", {"cpu": 8, "budget": 4096 * _MIB}),), 4)), encoding="utf-8")
    measurement = measurement_document(
        campaign="c", lane="r2", commit="b" * 40, image_digest=IMAGE,
        planned_ids=[_cid(0)],
        records=[{"candidate_id": _cid(0), "outcome_bucket": "killed", "elapsed_seconds": 30.0,
                  "memory_peak_bytes": 90 * _MIB}],
    )
    measurement_path = tmp_path / "measurement.json"
    measurement_path.write_text(json.dumps(measurement), encoding="utf-8")
    assert main(["sizing", str(plan_path), "--measurement", str(measurement_path)]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["basis"] == "measured"
    assert document["per_candidate_seconds"] == 30.0
    for argv, message in (
        (["--minimum-budget", "15"], "--minimum-budget"),
        (["--startup-allowance", "-5m"], "--startup-allowance"),
        (["--per-candidate-memory", "nope"], "--per-candidate-memory"),
    ):
        assert main(["sizing", str(plan_path), *argv]) == 2
        assert message in capsys.readouterr().err


# --------------------------------------------- FD-031: mean-based tail budgets
def _dispersed_measurement(count=40):
    """A heavy-tailed finished campaign: most candidates are quick, a few are not.

    30 killed at 10 s, 4 survived at 100 s, 3 budget_exceeded and 3 hung at
    their 600 s bound; the median over killed and survived is 10 s, the real
    sum is 4300 s.
    """
    ids = [_cid(index) for index in range(count)]
    plan = (
        [("killed", 10.0)] * 30 + [("survived", 100.0)] * 4
        + [("budget_exceeded", 600.0)] * 3 + [("hung", 600.0)] * 3
    )
    records = [
        {"candidate_id": ids[index], "outcome_bucket": bucket, "elapsed_seconds": seconds,
         "memory_peak_bytes": 100 * _MIB}
        for index, (bucket, seconds) in enumerate(plan)
    ]
    return measurement_document(
        campaign="c", lane="r2", commit="b" * 40, image_digest=IMAGE,
        planned_ids=ids, records=records,
    )


def _one_worker_sizing(measurement, candidate_count, **overrides):
    """One host, one instance, one worker: the shard IS the campaign."""
    plan = _plan(
        (("host-a", {"cpu": 1, "budget": 4096 * _MIB}),), candidate_count,
        demand={"baseline_seconds": 0, "instance_cpu": 0, "efficient_upper_bound": 1},
    )
    sizing = sizing_input_from_plan(plan, measurement=measurement, **overrides)
    assert sizing.candidate_count == candidate_count
    return sizing


def test_measurement_summary_adds_the_per_candidate_sum_fields_without_a_new_schema():
    document = _dispersed_measurement()
    assert document["schema"] == "fleet-r2-measurement/1"
    summary = document["summary"]
    # The existing median fields stay exactly as they were.
    assert summary["elapsed_seconds_median"] == 10.0
    assert summary["measured_candidates"] == 34
    # The additive fields: every candidate that occupied a worker (killed,
    # survived, budget_exceeded, hung), the bound counted for the last two.
    assert summary["cost_candidates"] == 40
    assert summary["cost_seconds_sum"] == 4300.0
    assert summary["cost_seconds_mean"] == 107.5
    assert summary["cost_seconds_max"] == 600.0
    assert summary["cost_seconds_stdev"] == pytest.approx(
        (sum((value - 107.5) ** 2 for value in [10.0] * 30 + [100.0] * 4 + [600.0] * 6) / 40) ** 0.5,
        rel=1e-4,
    )
    empty = measurement_document(
        campaign="c", lane="r2", commit="b" * 40, image_digest=None,
        planned_ids=[_cid(0)],
        records=[{"candidate_id": _cid(0), "outcome_bucket": "budget_exceeded", "elapsed_seconds": None}],
    )["summary"]
    assert empty["cost_candidates"] == 0
    assert empty["cost_seconds_sum"] is None and empty["cost_seconds_mean"] is None
    assert empty["cost_seconds_stdev"] is None and empty["cost_seconds_max"] is None


def test_a_dispersed_measurement_yields_a_budget_at_or_above_the_measured_tail():
    measurement = _dispersed_measurement()
    sizing = _one_worker_sizing(measurement, 40)
    assert sizing.basis == "measured"
    assert sizing.per_candidate_seconds == 107.5
    document = derive_sizing(sizing)
    shard = document["shards"][0]
    # The campaign's own measured sum is 4300 s; the median-based budget
    # (40 x 10 s) would have been the 900 s floor and stopped the shard early.
    assert shard["estimate_seconds"] == 4300.0
    assert shard["budget_seconds"] >= 4300
    assert document["seconds_statistic"] == "mean"
    # A smaller shard of the same lane is covered at its own 95th percentile:
    # n x mean + z x stdev x sqrt(n).
    stdev = measurement["summary"]["cost_seconds_stdev"]
    for count in (4, 10, 25):
        small = derive_sizing(_one_worker_sizing(measurement, count))["shards"][0]
        tail = count * 107.5 + TAIL_Z * stdev * count ** 0.5
        assert small["budget_seconds"] >= tail, count
    assert document["margin_source"] == "derived"
    assert document["margin"] == DEFAULT_MARGIN
    assert shard["margin"] > DEFAULT_MARGIN


def test_the_documented_margin_override_replaces_the_derived_tail_margin():
    measurement = _dispersed_measurement()
    document = derive_sizing(_one_worker_sizing(measurement, 40), margin=1.0)
    shard = document["shards"][0]
    assert document["margin_source"] == "explicit"
    assert document["margin"] == 1.0 and shard["margin"] == 1.0
    assert shard["budget_seconds"] == 4320  # 4300 s rounded up to whole minutes
    wide = derive_sizing(_one_worker_sizing(measurement, 40), margin=2.0)["shards"][0]
    assert wide["budget_seconds"] == 8640  # 8600 s rounded up


def test_a_measurement_without_dispersion_keeps_the_fixed_default_margin():
    flat = measurement_document(
        campaign="c", lane="r2", commit="b" * 40, image_digest=IMAGE,
        planned_ids=[_cid(0), _cid(1)],
        records=[
            {"candidate_id": _cid(0), "outcome_bucket": "killed", "elapsed_seconds": 60.0, "memory_peak_bytes": 100 * _MIB},
            {"candidate_id": _cid(1), "outcome_bucket": "killed", "elapsed_seconds": 60.0, "memory_peak_bytes": 100 * _MIB},
        ],
    )
    assert flat["summary"]["cost_seconds_stdev"] == 0.0
    document = derive_sizing(_one_worker_sizing(flat, 100))
    assert document["margin_source"] == "derived"
    assert document["shards"][0]["margin"] == DEFAULT_MARGIN
    declared = derive_sizing(_sizing())
    assert declared["seconds_statistic"] == "declared"
    assert declared["margin_source"] == "default"
    assert all(shard["margin"] == DEFAULT_MARGIN for shard in declared["shards"])


def test_a_measurement_written_before_the_sum_fields_still_sizes_from_its_median():
    plan = _plan((("host-a", {"cpu": 8, "budget": 4096 * _MIB}),), 2)
    old = {
        "schema": MEASUREMENT_SCHEMA, "campaign": "c", "lane": "r2", "commit": "b" * 40,
        "summary": {"elapsed_seconds_median": 61.0, "memory_peak_bytes_median": 200 * _MIB},
    }
    assert validate_measurement(old) == old
    sizing = sizing_input_from_plan(plan, measurement=old)
    assert sizing.per_candidate_seconds == 61.0
    assert sizing.seconds_statistic == "median"
    assert derive_sizing(sizing)["seconds_statistic"] == "median"
    # A per-candidate override is a declared number again, whatever the document says.
    declared = sizing_input_from_plan(plan, measurement=_dispersed_measurement(), per_candidate_seconds=45.0)
    assert declared.seconds_statistic == "declared"
    assert declared.dispersion == 0.0


def test_the_new_measurement_fields_are_validated_when_present():
    base = _dispersed_measurement()
    assert validate_measurement(base) == base
    for key, bad in (
        ("cost_seconds_mean", 0),
        ("cost_seconds_mean", True),
        ("cost_seconds_stdev", -1.0),
        ("cost_seconds_stdev", float("inf")),
        ("cost_seconds_sum", "x"),
        ("cost_candidates", -1),
        ("cost_candidates", 1.5),
    ):
        broken = json.loads(json.dumps(base))
        broken["summary"][key] = bad
        with pytest.raises(ContractError, match=key):
            validate_measurement(broken)
    nulls = json.loads(json.dumps(base))
    for key in ("cost_seconds_mean", "cost_seconds_stdev", "cost_seconds_sum", "cost_seconds_max"):
        nulls["summary"][key] = None
    assert validate_measurement(nulls) == nulls


def test_sizing_refuses_a_bad_statistic_or_dispersion():
    for field, value, message in (
        ("seconds_statistic", "p95", "expected one of"),
        ("dispersion", -0.1, "non-negative"),
        ("dispersion", float("nan"), "non-negative"),
        ("dispersion", True, "non-negative"),
    ):
        with pytest.raises(ContractError, match=message):
            derive_sizing(_sizing(**{field: value}))


def test_sizing_cli_derives_the_tail_margin_and_honours_an_explicit_one(tmp_path, capsys):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan((("host-a", {"cpu": 8, "budget": 4096 * _MIB}),), 4)), encoding="utf-8")
    measurement_path = tmp_path / "measurement.json"
    measurement_path.write_text(json.dumps(_dispersed_measurement()), encoding="utf-8")
    assert main(["sizing", str(plan_path), "--measurement", str(measurement_path)]) == 0
    derived = json.loads(capsys.readouterr().out)
    assert derived["margin_source"] == "derived" and derived["seconds_statistic"] == "mean"
    assert main(["sizing", str(plan_path), "--measurement", str(measurement_path), "--margin", "1.3"]) == 0
    explicit = json.loads(capsys.readouterr().out)
    assert explicit["margin_source"] == "explicit" and explicit["margin"] == 1.3
