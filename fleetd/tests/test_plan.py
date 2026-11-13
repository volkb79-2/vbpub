"""Deterministic contract, capacity, routing, and ETA tests for WP2."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import subprocess
from pathlib import Path

import pytest

from fleetd.contracts import ContractError, dump_json
from fleetd.cli import main
from fleetd.plan import (
    _assign_candidates,
    _capacity,
    _host_eta,
    _host_time,
    _number,
    _ordered_mix,
    _parse_other_running,
    _refusal,
    _resolve_memory,
    _resolve_time,
    _reverse_id,
    _time_reference,
    _waves,
    extract_demand,
    parse_candidate_table,
    parse_host_set,
    plan_campaign,
    render_commands,
)


FIXTURES = Path(__file__).parent / "fixtures"
DEMAND = {
    "unit": "candidate",
    "instance_memory": "250m",
    "unit_memory": "120m",
    "unit_memory_high": "600m",
    "unit_seconds_ref": 95,
    "efficient_upper_bound": 2,
}


def _read_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _hosts():
    return _read_fixture("fleet-host-set.valid.json")


def _facts(host_set, host_id):
    return next(host["facts"] for host in host_set["hosts"] if host["id"] == host_id)


def _host(host_set, host_id):
    return next(host for host in host_set["hosts"] if host["id"] == host_id)


def _candidate_table(rows):
    return {"schema": "candidate-table/1", "candidates": rows}


def _cid(label):
    if isinstance(label, str) and len(label) == 64 and all(char in "0123456789abcdef" for char in label):
        return label
    return hashlib.sha256(str(label).encode("utf-8")).hexdigest()


def _candidate(candidate_id, memory, *, source="src/candidate.py", **extra):
    return {"id": _cid(candidate_id), "file": source, "measured_memory_bytes": memory, **extra}


def _resolved_candidate(candidate_id, memory, time_ref_seconds):
    candidate = parse_candidate_table(_candidate_table([_candidate(candidate_id, memory)]))[0]
    return {
        "input": candidate,
        "memory_bytes": memory,
        "time_ref_seconds": time_ref_seconds,
    }


def _plan(rows, hosts=None, demand=None, **kwargs):
    return plan_campaign("r2", _candidate_table(rows), hosts or _hosts(), demand or DEMAND, **kwargs)


def _host_plan(plan, host_id):
    return next(host for host in plan["hosts"] if host["id"] == host_id)


def _assignment_host(host_id, *, cap, budget, average, workers, speed=None, f_h=1.0):
    return {
        "id": host_id,
        "status": "available",
        "cap_bytes": cap,
        "budget_bytes": budget,
        "average_worker_budget_bytes": average,
        "cap_alone_bytes": cap,
        "worker_slots": workers,
        "speed": speed,
        "f_h": f_h,
    }


def _assigned_host(plan, candidate_id):
    candidate_id = _cid(candidate_id)
    return next(host for host in plan["hosts"] if any(item["id"] == candidate_id for item in host["assigned"]))


def _golden_8g_host_set():
    profile = _read_fixture("fleet-plan-8g-hosts.valid.json")
    host_set = _hosts()
    source_hosts = {host["id"]: host for host in host_set["hosts"]}
    hosts = []
    for host_profile in profile["hosts"]:
        source = source_hosts[host_profile["source_fixture_host"]]
        host = copy.deepcopy(source)
        host["id"] = host_profile["id"]
        host["f_h"] = host_profile["f_h"]
        facts = host["facts"]
        facts["cpu"].update(
            online=profile["cpu"]["online"],
            for_lanes=profile["cpu"]["for_lanes"],
            oversubscribe_max=profile["cpu"]["oversubscribe_max"],
            model=host_profile["cpu_model"],
        )
        facts["memory"].update(
            total_bytes=profile["memory"]["total_bytes"],
            available_bytes=profile["memory"]["available_bytes"],
            for_lanes_bytes=profile["memory"]["for_lanes_bytes"],
            overcommit=profile["memory"]["overcommit"],
        )
        facts["control_plane"].update(
            slice="ctl.slice",
            present=False,
            memory_min_bytes=None,
            memory_min_bytes_reason="ctl.slice has not been installed on this host",
            cpu_weight=None,
            cpu_weight_reason="ctl.slice has not been installed on this host",
        )
        facts["speed"] = dict(host_profile["speed"])
        host["other_running"] = []
        hosts.append(host)
    return {"schema": "fleet-host-set/1", "hosts": hosts}


def test_candidate_and_host_fixtures_are_valid_and_sorted():
    candidates = parse_candidate_table(_read_fixture("candidate-table.valid.json"))
    hosts = parse_host_set(_hosts())
    assert len(candidates) == 5
    assert [host.host_id for host in hosts] == ["host-large", "host-small"]
    assert _reverse_id("a") > _reverse_id("b")
    assert _reverse_id("aa") > _reverse_id("a")


def test_plan_resolves_memory_time_and_routes_with_single_core_eta():
    candidate_table = _read_fixture("candidate-table.valid.json")
    plan = plan_campaign("r2", candidate_table, _hosts(), DEMAND)
    assigned = {
        candidate["id"]: candidate
        for host in plan["hosts"]
        for candidate in host["assigned"]
    }
    assert assigned[_cid("candidate-low")]["memory_source"] == "candidate_record"
    assert assigned[_cid("candidate-file-median")]["memory_source"] == "file_median"
    assert assigned[_cid("candidate-lane-median")]["memory_source"] == "lane_median"
    assert assigned[_cid("candidate-file-median")]["memory_bytes"] == 125829120
    assert assigned[_cid("candidate-lane-median")]["memory_bytes"] == 268435456
    assert assigned[_cid("candidate-low")]["time_source"] == "candidate_record"
    assert assigned[_cid("candidate-file-median")]["time_source"] == "file_median"
    assert assigned[_cid("candidate-lane-median")]["time_source"] == "lane_median"
    assert assigned[_cid("candidate-low")]["time_ref_seconds"] == 76.0
    assert plan["time_reference"] == {
        "normalization": "elapsed_seconds * source.single_core / source.baseline_score",
        "host_eta": "time_ref_seconds * baseline_score / host.single_core * f_h",
        "geekbench_major": 6,
        "geekbench_version": "6.4.0",
        "baseline_score": 2500,
    }
    assert _assigned_host(plan, "candidate-big")["id"] == "host-large"
    assert _assigned_host(plan, "candidate-retry")["id"] == "host-large"
    assert _assigned_host(plan, "candidate-retry")["batches"][-1]["kind"] == "retry"
    large = _host_plan(plan, "host-large")
    times = {
        item["id"]: item["time_ref_seconds"] * 2500 / 3000 * 0.7
        for item in large["assigned"]
    }
    shared_times = [
        times[item["id"]] for item in large["assigned"]
        if item["assignment"] == "shared"
    ]
    isolated_times = [
        times[item["id"]] for item in large["assigned"]
        if item["assignment"] in {"alone", "retry"}
    ]
    expected_eta = (
        sum(shared_times) / large["worker_slots"]
        + max(shared_times, default=0.0)
        + sum(isolated_times)
    )
    assert large["eta_seconds"] == pytest.approx(expected_eta)
    assert plan["eta_seconds"] > 0
    assert "eta_reason" not in plan


def test_plan_preserves_special_alone_and_retry_work_on_one_host_and_renders_commands():
    plan = _plan([
        _candidate("big", 14 * 1024**3 + 512 * 1024**2),
        _candidate("retry", 256 * 1024**2, memory_crash_evidence=True),
    ])
    host = _host_plan(plan, "host-large")
    big = next(item for item in host["assigned"] if item["id"] == _cid("big"))
    assert host["cap_bytes"] < big["memory_bytes"] <= host["cap_alone_bytes"]
    assert {batch["kind"] for batch in host["batches"] if "kind" in batch} == {"alone", "retry"}
    assert {item["id"] for item in host["assigned"]} == {_cid("big"), _cid("retry")}
    commands = render_commands(plan)
    assert "if run-gate r2 --jobs 1 --only-candidates" in commands
    assert _cid("big") in commands and _cid("retry") in commands
    assert "BUDGET_EXCEEDED (exit 4)" in commands
    assert "docker" not in commands and "ssh " not in commands


@pytest.mark.parametrize(
    ("first_exit", "second_exit", "expected_exit", "expected_calls"),
    [(0, 0, 0, 2), (4, 4, 0, 2), (2, 4, 2, 1), (1, 4, 1, 1)],
)
def test_rendered_script_runs_batches_serially_and_handles_exit_codes(
    tmp_path, first_exit, second_exit, expected_exit, expected_calls
):
    candidate_ids = [_cid("first batch"), _cid("second batch")]
    plan = {
        "lane": "r2",
        "hosts": [{
            "id": "host-a",
            "status": "available",
            "worker_slots": 2,
            "batches": [
                {"batch": 1, "candidate_ids": [candidate_ids[0]]},
                {"batch": 2, "candidate_ids": [candidate_ids[1]]},
            ],
        }],
    }
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_run_gate = fake_bin / "run-gate"
    fake_run_gate.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "calls=0\n"
        "[ ! -f \"$FLEETD_CALL_COUNT\" ] || calls=$(cat \"$FLEETD_CALL_COUNT\")\n"
        "calls=$((calls + 1))\n"
        "printf '%s\\n' \"$calls\" > \"$FLEETD_CALL_COUNT\"\n"
        "printf '%s\\n' \"$*\" >> \"$FLEETD_CALLS\"\n"
        "case \"$calls\" in\n"
        "  1) exit \"$FLEETD_FIRST_EXIT\" ;;\n"
        "  2) exit \"$FLEETD_SECOND_EXIT\" ;;\n"
        "  *) exit 99 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_run_gate.chmod(0o755)
    call_count = tmp_path / "call-count"
    calls = tmp_path / "calls"
    environment = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "FLEETD_CALL_COUNT": str(call_count),
        "FLEETD_CALLS": str(calls),
        "FLEETD_FIRST_EXIT": str(first_exit),
        "FLEETD_SECOND_EXIT": str(second_exit),
    }
    result = subprocess.run(
        ["/bin/bash", "-c", render_commands(plan)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_exit
    assert int(call_count.read_text(encoding="utf-8")) == expected_calls
    assert len(calls.read_text(encoding="utf-8").splitlines()) == expected_calls
    first_ids = tmp_path / ".assay/fleetd-plan/host-a/batch-001.ids"
    assert first_ids.read_text(encoding="utf-8").strip() == candidate_ids[0]
    second_ids = tmp_path / ".assay/fleetd-plan/host-a/batch-002.ids"
    assert second_ids.exists() is (expected_calls == 2)
    if expected_calls == 2:
        assert second_ids.read_text(encoding="utf-8").strip() == candidate_ids[1]


def test_render_commands_skips_batches_without_candidates():
    candidate_id = _cid("non-empty batch")
    plan = {
        "lane": "r2",
        "hosts": [{
            "id": "host-a",
            "status": "available",
            "worker_slots": 1,
            "batches": [
                {"batch": 0, "candidate_ids": []},
                {"batch": 1, "candidate_ids": [candidate_id]},
            ],
        }],
    }
    commands = render_commands(plan)
    assert "batch-000.ids" not in commands
    assert "batch-001.ids" in commands and candidate_id in commands


def test_physical_cap_routes_a_large_candidate_to_the_bigger_host():
    host_set = _hosts()
    _host(host_set, "host-small")["f_h"] = 0.5
    _host(host_set, "host-large")["f_h"] = 2.0
    plan = _plan([_candidate("large", 8 * 1024**3)], hosts=host_set)
    small = _host_plan(plan, "host-small")
    assert small["cap_bytes"] < 8 * 1024**3
    assert _assigned_host(plan, "large")["id"] == "host-large"


def test_deterministic_outputs_ignore_input_order_and_use_safe_shell_quoting():
    rows = [
        _candidate("z id", 800_000_000, source="src/z.py"),
        _candidate("a'quoted", 200_000_000, source="src/a.py"),
        _candidate("middle", 100_000_000, source="src/m.py"),
    ]
    first = _plan(rows)
    reversed_hosts = _hosts()
    reversed_hosts["hosts"].reverse()
    second = _plan(list(reversed(rows)), reversed_hosts)
    assert dump_json(first) == dump_json(second)
    first["lane"] = "r2 with space"
    second["lane"] = "r2 with space"
    command = render_commands(first)
    assert "'r2 with space'" in command
    assert _cid("z id") in command and _cid("a'quoted") in command
    assert command == render_commands(second)


def test_seeded_property_sweep_enforces_physical_caps_sum_budget_mixes_and_retry_owner():
    generator = random.Random(88017)
    sizes = [80_000_000, 120_000_000, 700_000_000, 2_000_000_000, 5_000_000_000,
             7_000_000_000, 8_000_000_000, 14_000_000_000, 18_000_000_000]
    for sample in range(35):
        rows = [
            _candidate(f"s{sample}-c{index}", generator.choice(sizes), source=f"src/{index % 3}.py")
            for index in range(generator.randint(1, 18))
        ]
        retry_id = f"s{sample}-retry"
        rows.append(_candidate(retry_id, generator.choice(sizes[:-1]), memory_crash_evidence=True))
        plan = _plan(rows)
        charges_by_id = {row["id"]: row["measured_memory_bytes"] for row in rows}
        available = [host for host in plan["hosts"] if host["status"] == "available"]
        assert available
        largest_alone = max(available, key=lambda host: (host["cap_alone_bytes"], tuple(-ord(c) for c in host["id"])))
        retry_host = next(host for host in available if any(item["id"] == _cid(retry_id) for item in host["assigned"]))
        assert retry_host["id"] == largest_alone["id"]
        for host in available:
            for item in host["assigned"]:
                assert item["memory_bytes"] <= host["cap_alone_bytes"]
            for batch in host["batches"]:
                for wave in batch["waves"]:
                    assert wave["expected_charge_bytes"] == sum(charges_by_id[item] for item in wave["candidate_ids"])
                    assert wave["expected_charge_bytes"] <= wave["budget_bytes"]
                    if batch.get("kind") is None:
                        assert len(wave["candidate_ids"]) <= host["worker_slots"]
                    assert wave["mix_holds"] or "mix_flag_reason" in wave
            if not host["mix_holds"]:
                assert host["mix_flagged"]
        small = _host_plan(plan, "host-small")
        for item in plan["hosts"]:
            if item["status"] != "available":
                continue
            for assigned in item["assigned"]:
                assert charges_by_id[assigned["id"]] <= item["cap_alone_bytes"]
        for candidate in rows:
            if candidate["measured_memory_bytes"] > small["cap_bytes"]:
                assigned_hosts = [
                    host["id"] for host in plan["hosts"]
                    if any(item["id"] == candidate["id"] for item in host["assigned"])
                ]
                assert "host-small" not in assigned_hosts


def test_input_derived_budgets_hold_for_varied_hosts_and_output_ignores_input_order():
    """Recompute P, O, B, W and the retry host from the INPUT facts, never from the plan's own figures."""
    demand = {**DEMAND, "efficient_upper_bound": 3}
    instance = 250 * 1024**2
    sizes = [50e6, 120e6, 400e6, 900e6, 1.5e9, 3e9, 5e9, 7e9, 9e9, 15e9]
    saw_alone = saw_refused = False
    for seed in range(120):
        generator = random.Random(4100 + seed)
        host_set = _hosts()
        expected = {}
        for host in host_set["hosts"]:
            facts = host["facts"]
            total = generator.choice([4, 6, 8, 8, 12, 16, 32]) * 1024**3
            facts["memory"].update(
                total_bytes=total, for_lanes_bytes=total, available_bytes=total // 2,
                overcommit=generator.choice([1.0, 1.25, 1.5, 2.0]),
            )
            facts["control_plane"]["memory_min_bytes"] = generator.choice([768, 1024, 2048]) * 1024**2
            facts["cpu"].update(
                online=generator.choice([2, 4, 8]), oversubscribe_max=generator.choice([1.0, 1.25, 1.5]),
            )
            facts["cpu"]["for_lanes"] = float(facts["cpu"]["online"])
            facts["speed"]["single_core"] = generator.choice([1500, 2000, 2500, 3000])
            host["f_h"] = generator.choice([0.5, 1.0, 1.7, 2.0])
            if generator.random() < 0.4:
                host["other_running"] = [{
                    "id": "other", "expected_memory_bytes": generator.choice([1, 2, 3]) * 1024**3,
                    "workers": generator.choice([0, 1, 2]), "containers": 1,
                }]
            other = host["other_running"]
            reserve_default = min(
                2 * 1024**3,
                max(
                    768 * 1024**2,
                    256 * 1024**2 + (total * 6 + 99) // 100,
                ),
            )
            reserve = max(facts["control_plane"]["memory_min_bytes"], reserve_default)
            physical = total - reserve
            overhead = sum(job["expected_memory_bytes"] for job in other) + instance
            slots = min(
                int(facts["cpu"]["for_lanes"] * facts["cpu"]["oversubscribe_max"]) - sum(job["workers"] for job in other),
                demand["efficient_upper_bound"],
            )
            expected[host["id"]] = (physical, overhead, physical * facts["memory"]["overcommit"] - overhead, slots)
        rows = []
        for index in range(generator.randint(1, 30)):
            row = _candidate(f"p{seed}-{index}", int(generator.choice(sizes)), source=f"src/{index % 4}.py")
            if generator.random() < 0.1:
                row["memory_crash_evidence"] = True
            rows.append(row)
        plan = _plan(rows, hosts=host_set, demand=demand)
        available = {host["id"]: host for host in plan["hosts"] if host["status"] == "available"}
        saw_refused = saw_refused or len(available) < len(plan["hosts"])
        placed = set()
        for host_id, host in available.items():
            physical, overhead, budget, slots = expected[host_id]
            assert host["worker_slots"] == slots
            assert host["budget_bytes"] == pytest.approx(budget)
            assert host["cap_alone_bytes"] == physical - overhead
            shared = [item["memory_bytes"] for item in host["assigned"] if item["assignment"] == "shared"]
            for item in host["assigned"]:
                placed.add(item["id"])
                assert item["memory_bytes"] <= physical - overhead
                saw_alone = saw_alone or item["assignment"] == "alone"
            if shared and sum(shared) / len(shared) > budget / slots:
                assert host["mix_flagged"] is True
            for batch in host["batches"]:
                for wave in batch["waves"]:
                    assert wave["expected_charge_bytes"] <= budget
                    if batch.get("kind") is None:
                        assert len(wave["candidate_ids"]) <= slots
        if available:
            largest = max(physical - overhead for physical, overhead, _, _ in (expected[name] for name in available))
            for host_id, host in available.items():
                for item in host["assigned"]:
                    if item["assignment"] == "retry":
                        assert expected[host_id][0] - expected[host_id][1] == largest
        listed = placed | {item["id"] for item in plan["unplaced"]} | {item["id"] for item in plan["terminal"]}
        assert listed == {row["id"] for row in rows}
        assert len(placed) + len(plan["unplaced"]) + len(plan["terminal"]) == len(rows)
        shuffled_hosts = copy.deepcopy(host_set)
        generator.shuffle(shuffled_hosts["hosts"])
        shuffled_rows = list(rows)
        generator.shuffle(shuffled_rows)
        assert dump_json(_plan(shuffled_rows, hosts=shuffled_hosts, demand=demand)) == dump_json(plan)
    assert saw_alone and saw_refused


def test_low_ram_class_threshold_uses_the_smallest_host_average_budget():
    plan = _plan([_candidate("above-small-budget", 7 * 1024**3)])
    assigned = next(
        item
        for host in plan["hosts"]
        for item in host["assigned"]
        if item["id"] == _cid("above-small-budget")
    )
    assert assigned["ram_class"] == "high"


def test_mix_fallback_chooses_the_eligible_host_with_largest_average_budget():
    hosts = [
        _assignment_host("a-small-budget", cap=300, budget=400, average=150, workers=1),
        _assignment_host("z-large-budget", cap=300, budget=600, average=200, workers=2),
    ]
    assignments, _, unplaced, terminal = _assign_candidates(
        [_resolved_candidate("fallback", 250, 1.0)],
        hosts,
        None,
        None,
        None,
    )
    assert not unplaced and not terminal
    assert assignments["a-small-budget"] == []
    assert [item["input"].candidate_id for item in assignments["z-large-budget"]] == [_cid("fallback")]
    assert assignments["z-large-budget"][0]["mix_flag_reason"]


def test_most_constrained_candidate_is_assigned_before_the_longer_flexible_candidate():
    hosts = [
        _assignment_host("a-wide", cap=200, budget=1000, average=500, workers=10),
        _assignment_host("b-narrow", cap=100, budget=100, average=100, workers=1),
    ]
    assignments, _, unplaced, terminal = _assign_candidates(
        [
            _resolved_candidate("flexible-long", 80, 20.0),
            _resolved_candidate("constrained-short", 120, 10.0),
        ],
        hosts,
        None,
        None,
        None,
    )
    assert not unplaced and not terminal
    assert [
        item["input"].candidate_id for item in assignments["a-wide"]
    ] == [_cid("constrained-short"), _cid("flexible-long")]


def test_longest_candidate_is_assigned_first_when_constraints_are_equal():
    hosts = [_assignment_host("only-host", cap=100, budget=100, average=100, workers=1)]
    assignments, _, unplaced, terminal = _assign_candidates(
        [
            _resolved_candidate("short", 10, 5.0),
            _resolved_candidate("long", 20, 50.0),
        ],
        hosts,
        None,
        None,
        None,
    )
    assert not unplaced and not terminal
    assert [item["input"].candidate_id for item in assignments["only-host"]] == [
        _cid("long"), _cid("short")
    ]


def test_projected_finish_divides_by_each_hosts_worker_slots():
    reference = {
        "geekbench_major": 6,
        "geekbench_version": "6.4.0",
        "baseline_score": 2500,
        "multi_core": 9000,
        "basis": "geekbench6-measured",
    }
    hosts = [
        _assignment_host(
            "a-four-workers", cap=100, budget=500, average=100, workers=4,
            speed={**reference, "single_core": 1000},
        ),
        _assignment_host(
            "b-one-worker", cap=100, budget=100, average=100, workers=1,
            speed={**reference, "single_core": 2500},
        ),
    ]
    assignments, _, unplaced, terminal = _assign_candidates(
        [_resolved_candidate("throughput", 10, 100.0)],
        hosts,
        6,
        "6.4.0",
        2500,
    )
    assert not unplaced and not terminal
    assert [item["input"].candidate_id for item in assignments["a-four-workers"]] == [
        _cid("throughput")
    ]


def test_batches_interleave_classes_respect_wave_budget_and_multiplier():
    rows = [
        _candidate("low-slow", 100_000_000, elapsed_seconds=200.0, measured_on_host="host-small"),
        _candidate("low-fast", 120_000_000, elapsed_seconds=10.0, measured_on_host="host-small"),
        _candidate("high-slow", 1_800_000_000, elapsed_seconds=300.0, measured_on_host="host-small"),
        _candidate("high-fast", 1_700_000_000, elapsed_seconds=20.0, measured_on_host="host-small"),
    ]
    plan = _plan(rows, batch_multiplier=0.5)
    host = _assigned_host(plan, "low-slow")
    assert plan["batch_multiplier"] == 0.5
    for batch in host["batches"]:
        ids = batch["candidate_ids"]
        assert len(ids) <= 3 * host["worker_slots"]
        for wave in batch["waves"]:
            assert wave["expected_charge_bytes"] <= host["budget_bytes"]
            assert wave["mix_holds"] or wave.get("mix_flag_reason")
    assert host["ram_low_bytes"] > 0


def test_batch_multiplier_sets_the_candidate_count_of_every_full_batch():
    rows = [_candidate(f"m{index}", 100_000_000, source="src/m.py") for index in range(40)]
    for multiplier in (1.0, 2.5, 4.0):
        plan = _plan(rows, batch_multiplier=multiplier)
        multi_batch_hosts = 0
        for host in plan["hosts"]:
            target = math.ceil(multiplier * host["worker_slots"])
            sizes = [len(batch["candidate_ids"]) for batch in host["batches"]]
            assert all(batch["target_candidate_count"] == target for batch in host["batches"])
            assert all(size == target for size in sizes[:-1])
            assert 1 <= sizes[-1] <= target
            multi_batch_hosts += len(sizes) > 1
        assert multi_batch_hosts


def test_mix_violation_is_flagged_without_turning_missing_eta_into_a_mix_flag():
    rows = [_candidate("a", 11_500_000_000), _candidate("b", 13_500_000_000)]
    plan = _plan(rows)
    host = _assigned_host(plan, "a")
    assert _assigned_host(plan, "b")["id"] == host["id"]
    assert all(item["memory_bytes"] <= host["cap_bytes"] for item in host["assigned"])
    assert host["mix_flagged"] is True
    assert host["mix_holds"] is False
    assert any("mix_flag_reason" in item for item in host["assigned"])

    no_speed = _hosts()
    for host in no_speed["hosts"]:
        host["facts"]["speed"] = None
        host["facts"]["speed_reason"] = "synthetic missing score"
    eta_plan = _plan([_candidate("a", 120_000_000)], hosts=no_speed)
    assigned_host = _assigned_host(eta_plan, "a")
    assert assigned_host["mix_holds"] is True
    assert assigned_host["mix_flagged"] is False
    assert "assignment_reason" in assigned_host["assigned"][0]
    assert eta_plan["eta_seconds"] is None
    assert eta_plan["eta_reason"]
    assert eta_plan["time_reference"]["reason"] == "no host has a Geekbench record"


def test_ram_resolution_declared_low_and_high_and_time_declared_fallback(tmp_path):
    declared = _plan([
        {"id": _cid("low"), "file": "a.py"},
        {"id": _cid("high"), "file": "b.py", "estimated_memory_class": "high"},
    ])
    resolved = {item["id"]: item for host in declared["hosts"] for item in host["assigned"]}
    assert resolved[_cid("low")]["memory_bytes"] == 120 * 1024**2
    assert resolved[_cid("low")]["memory_source"] == "declared_median"
    assert resolved[_cid("high")]["memory_bytes"] == 600 * 1024**2
    assert resolved[_cid("high")]["memory_source"] == "declared_high"
    assert resolved[_cid("low")]["time_source"] == "declared_reference"
    assert resolved[_cid("low")]["time_ref_seconds"] == 95

    with pytest.raises(ContractError, match="high memory class requires demand.unit_memory_high"):
        _plan([{"id": _cid("high"), "file": "b.py", "estimated_memory_class": "high"}], demand={
            "unit": "candidate", "instance_memory": "250m", "unit_memory": "120m",
            "unit_seconds_ref": 95, "efficient_upper_bound": 2,
        })


def test_own_file_and_lane_time_fallbacks_use_exact_geekbench_version(tmp_path):
    rows = [
        {"id": _cid("own"), "file": "same.py", "elapsed_seconds": 100, "measured_on_host": "host-small"},
        {"id": _cid("file"), "file": "same.py"},
        {"id": _cid("lane"), "file": "other.py"},
    ]
    plan = _plan(rows)
    assigned = {item["id"]: item for host in plan["hosts"] for item in host["assigned"]}
    assert assigned[_cid("own")]["time_source"] == "candidate_record"
    assert assigned[_cid("file")]["time_source"] == "file_median"
    assert assigned[_cid("lane")]["time_source"] == "lane_median"
    assert assigned[_cid("file")]["time_ref_seconds"] == 80

    changed = _hosts()
    _facts(changed, "host-large")["speed"]["geekbench_version"] = "6.5.0"
    mismatched = _plan([_candidate("x", 120_000_000)], hosts=changed)
    assert mismatched["eta_seconds"] is None
    assert mismatched["time_reference"]["reason"] == "host Geekbench versions differ; scores are never mixed"


def test_correction_factor_and_single_core_change_eta_but_multicore_does_not():
    rows = [_candidate("one", 120_000_000, elapsed_seconds=100, measured_on_host="host-small")]
    original = _plan(rows)
    changed_factor = _hosts()
    _host(changed_factor, "host-large")["f_h"] = 1.4
    slower = _plan(rows, hosts=changed_factor)
    changed_multi = _hosts()
    _facts(changed_multi, "host-large")["speed"]["multi_core"] = 1
    same_eta = _plan(rows, hosts=changed_multi)
    assert original["eta_seconds"] != slower["eta_seconds"]
    assert original["eta_seconds"] == same_eta["eta_seconds"]
    assert _host_plan(original, "host-large")["cap_bytes"] == _host_plan(slower, "host-large")["cap_bytes"]


def test_host_refusals_have_explicit_reasons_and_null_eta_reasons():
    variants = []
    host_set = _hosts()
    _facts(host_set, "host-small")["policy"]["host_role"] = "shared-prod"
    variants.append((host_set, "shared-prod hosts are not eligible"))

    host_set = _hosts()
    policy = _facts(host_set, "host-small")["policy"]
    policy["host_role"] = None
    policy["host_role_reason"] = "not configured"
    variants.append((host_set, "host role is unknown"))

    host_set = _hosts()
    _facts(host_set, "host-small")["psi"]["memory"]["some_avg10_pct"] = 26
    variants.append((host_set, "memory PSI some avg10 26% exceeds"))

    host_set = _hosts()
    psi = _facts(host_set, "host-small")["psi"]["io"]
    psi["some_avg10_pct"] = None
    psi["some_avg10_pct_reason"] = "fixture unavailable"
    variants.append((host_set, "io PSI some avg10 is unknown"))

    host_set = _hosts()
    _facts(host_set, "host-small")["cgroup_v2"]["mounted"] = False
    variants.append((host_set, "cgroup v2 is not mounted"))

    host_set = _hosts()
    cgroup = _facts(host_set, "host-small")["cgroup_v2"]
    cgroup["controllers"] = None
    cgroup["controllers_reason"] = "not readable"
    variants.append((host_set, "controller list is unknown"))

    host_set = _hosts()
    memory = _facts(host_set, "host-small")["memory"]
    memory["for_lanes_bytes"] = None
    memory["for_lanes_bytes_reason"] = "not readable"
    variants.append((host_set, "physical lane memory is unknown"))

    host_set = _hosts()
    memory = _facts(host_set, "host-small")["memory"]
    memory["total_bytes"] = None
    memory["total_bytes_reason"] = "not readable"
    variants.append((host_set, "MemTotal is unknown for the control-plane memory reserve"))

    host_set = _hosts()
    cpu = _facts(host_set, "host-small")["cpu"]
    cpu["for_lanes"] = None
    cpu["for_lanes_reason"] = "not readable"
    variants.append((host_set, "CPU capacity for lanes is unknown"))

    host_set = _hosts()
    policy = _facts(host_set, "host-small")["policy"]
    policy["max_lane_containers"] = None
    policy["max_lane_containers_reason"] = "not configured"
    variants.append((host_set, "maximum lane container count is unknown"))

    for host_set, reason in variants:
        plan = _plan([_candidate("candidate", 120_000_000)], hosts=host_set)
        refused = _host_plan(plan, "host-small")
        assert refused["status"] == "refused"
        assert reason in refused["reason"]
        assert refused["eta_seconds"] is None
        assert refused["eta_reason"] == refused["reason"]


@pytest.mark.parametrize(
    ("total_gib", "reported_mib", "slice_present", "expected_mib", "source"),
    [
        (8, None, False, 768, "design-default"),
        (8, None, True, 768, "design-default"),
        (8, 0, True, 768, "design-default"),
        (16, 1536, True, 1536, "reported"),
        (64, None, False, 2048, "design-default"),
        (64, 3072, True, 3072, "reported"),
        (64, 3072, False, 2048, "design-default"),
    ],
)
def test_control_plane_reserve_uses_the_design_clamp_and_honors_larger_reports(
    total_gib, reported_mib, slice_present, expected_mib, source
):
    host_set = _hosts()
    facts = _facts(host_set, "host-small")
    total_bytes = total_gib * 1024**3
    facts["memory"].update(total_bytes=total_bytes, for_lanes_bytes=total_bytes)
    control = facts["control_plane"]
    control["present"] = slice_present
    control["memory_min_bytes"] = None if reported_mib is None else reported_mib * 1024**2
    if reported_mib is None:
        control["memory_min_bytes_reason"] = "ctl.slice is absent"
    else:
        control.pop("memory_min_bytes_reason", None)
    if slice_present:
        control["cpu_weight"] = 10000
        control.pop("cpu_weight_reason", None)
    else:
        control["cpu_weight"] = None
        control["cpu_weight_reason"] = "ctl.slice is absent"

    plan = _plan([_candidate("reserve", 120_000_000)], hosts=host_set)
    host = _host_plan(plan, "host-small")
    assert host["status"] == "available"
    assert host["reserve_bytes"] == expected_mib * 1024**2
    assert host["reserve_source"] == source
    assert host["physical_bytes"] == total_bytes - expected_mib * 1024**2


def test_golden_cli_plan_for_two_eight_gib_four_core_hosts_and_230_candidates(tmp_path, capsys):
    hosts = _golden_8g_host_set()
    sizes = [
        80 * 1024**2 + (index % 4) * 40 * 1024**2
        for index in range(180)
    ]
    sizes.extend(
        (700 + (index % 5) * 150) * 1024**2
        for index in range(25)
    )
    sizes.extend([5500 * 1024**2] * 24)
    sizes.append(8 * 1024**3)
    candidates = _candidate_table([
        _candidate(f"golden-{index:03d}", memory, source=f"src/module-{index % 8}.py")
        for index, memory in enumerate(sizes)
    ])
    candidate_path = tmp_path / "candidates.json"
    host_path = tmp_path / "hosts.json"
    demand_path = tmp_path / "demand.toml"
    candidate_path.write_text(json.dumps(candidates), encoding="utf-8")
    host_path.write_text(json.dumps(hosts), encoding="utf-8")
    demand_path.write_text(
        "schema_version = 1\n\n[lanes.r2.demand]\nunit = 'candidate'\n"
        "instance_memory = '250m'\nunit_memory = '120m'\n"
        "unit_memory_high = '600m'\nunit_seconds_ref = 95\n"
        "efficient_upper_bound = 3\n",
        encoding="utf-8",
    )

    assert main([
        "plan", "r2", "--candidates", str(candidate_path),
        "--hosts", str(host_path), "--demand", str(demand_path),
    ]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(sizes) == 230
    assert [host["id"] for host in plan["hosts"]] == ["r1002", "v1001"]
    assert all(host["status"] == "available" for host in plan["hosts"])
    assert all(host["worker_slots"] == 3 for host in plan["hosts"])
    assert all(host["reserve_bytes"] == 768 * 1024**2 for host in plan["hosts"])
    assert all(host["reserve_source"] == "design-default" for host in plan["hosts"])
    assert plan["hosts"][0]["speed"]["single_core"] != plan["hosts"][1]["speed"]["single_core"]

    assigned = [item["id"] for host in plan["hosts"] for item in host["assigned"]]
    unplaced = [item["id"] for item in plan["unplaced"]]
    terminal = [item["id"] for item in plan["terminal"]]
    expected_ids = {row["id"] for row in candidates["candidates"]}
    assert len(assigned) == len(set(assigned))
    assert set(assigned) | set(unplaced) | set(terminal) == expected_ids
    assert len(assigned) + len(unplaced) + len(terminal) == len(expected_ids)
    assert len(unplaced) == 1
    assert unplaced[0] == candidates["candidates"][-1]["id"]
    assert all(host["assigned"] for host in plan["hosts"])

    for host in plan["hosts"]:
        batch_ids = []
        for item in host["assigned"]:
            assert item["memory_bytes"] <= host["cap_alone_bytes"]
        for batch in host["batches"]:
            assert batch["target_candidate_count"] == 12
            assert len(batch["candidate_ids"]) <= 12
            batch_ids.extend(batch["candidate_ids"])
            assert all(wave["expected_charge_bytes"] <= host["budget_bytes"] for wave in batch["waves"])
        assert len(batch_ids) == len(set(batch_ids))
        assert set(batch_ids) == {item["id"] for item in host["assigned"]}
        assert host["mix_holds"] or host["mix_flagged"]

    assert 3000 < plan["eta_seconds"] < 10000
    assert plan["eta_seconds"] == max(host["eta_seconds"] for host in plan["hosts"])
    assert plan["execution"]["schema"] == "fleet-plan-execution/1"
    assert len(plan["execution"]["commit"]) == 40
    deterministic_plan = {key: value for key, value in plan.items() if key != "execution"}
    digest = hashlib.sha256(dump_json(deterministic_plan).encode("utf-8")).hexdigest()
    assert digest == "13b036934c44c7f0ddb96061bc78e5b592d913aa73e76085a168a0fef934b18a"


def test_other_work_and_memory_reserves_bound_worker_and_physical_budgets():
    host_set = _hosts()
    _host(host_set, "host-small")["other_running"] = [
        {"id": "other", "expected_memory_bytes": 10_000_000, "workers": 5, "containers": 1}
    ]
    no_workers = _plan([_candidate("candidate", 100_000_000)], hosts=host_set)
    assert _host_plan(no_workers, "host-small")["reason"] == "no worker slots remain after other running jobs"

    host_set = _hosts()
    _host(host_set, "host-small")["other_running"] = [
        {"id": "other", "expected_memory_bytes": 10_000_000, "workers": 0, "containers": 8}
    ]
    no_containers = _plan([_candidate("candidate", 100_000_000)], hosts=host_set)
    assert _host_plan(no_containers, "host-small")["reason"] == "no lane container slots remain after other running jobs"

    host_set = _hosts()
    _host(host_set, "host-small")["other_running"] = [
        {"id": "other", "expected_memory_bytes": 12 * 1024**3, "workers": 0, "containers": 0}
    ]
    overcommitted = _plan([_candidate("candidate", 100_000_000)], hosts=host_set)
    assert "overcommitted RAM budget" in _host_plan(overcommitted, "host-small")["reason"]

    host_set = _hosts()
    physical = 8 * 1024**3 - 768 * 1024**2
    _host(host_set, "host-small")["other_running"] = [
        {"id": "other", "expected_memory_bytes": int(physical * 1.5) - 100_000_000, "workers": 0, "containers": 0}
    ]
    budget_exhausted = _plan([_candidate("candidate", 100_000_000)], hosts=host_set)
    assert "overcommitted RAM budget" in _host_plan(budget_exhausted, "host-small")["reason"]

    host_set = _hosts()
    _host(host_set, "host-small")["other_running"] = [
        {"id": "other", "expected_memory_bytes": physical - 100_000_000, "workers": 0, "containers": 0}
    ]
    physical_exhausted = _plan([_candidate("candidate", 100_000_000)], hosts=host_set)
    assert "physical lane RAM" in _host_plan(physical_exhausted, "host-small")["reason"]


def test_retry_terminal_unplaceable_and_no_available_host_paths():
    terminal = _plan([_candidate("done", 100_000_000, memory_crash_evidence=True, memory_crash_retries=1)])
    assert terminal["terminal"] == [{"id": _cid("done"), "reason": "memory-crash retry was already used; candidate is final"}]

    too_large_retry = _plan([_candidate("retry", 20 * 1024**3, memory_crash_evidence=True)])
    assert too_large_retry["unplaced"] and "largest alone cap" in too_large_retry["unplaced"][0]["reason"]

    too_large = _plan([_candidate("normal", 20 * 1024**3)])
    assert too_large["unplaced"] and "largest physical alone cap" in too_large["unplaced"][0]["reason"]

    no_hosts = _hosts()
    for host in no_hosts["hosts"]:
        host["facts"]["psi"]["memory"]["some_avg10_pct"] = 99
    none_available = _plan([
        _candidate("normal", 100_000_000),
        _candidate("retry", 100_000_000, memory_crash_evidence=True),
    ], hosts=no_hosts)
    assert all(host["status"] == "refused" for host in none_available["hosts"])
    assert {item["id"] for item in none_available["unplaced"]} == {_cid("normal"), _cid("retry")}
    assert none_available["eta_seconds"] == 0.0
    assert "run-gate " not in render_commands(none_available)


def test_overcommit_changes_sum_budget_but_never_physical_alone_cap():
    host_set = _hosts()
    for host in host_set["hosts"]:
        host["facts"]["memory"]["overcommit"] = 0.5
    plan = _plan([
        _candidate("alone-over-budget", 8 * 1024**3),
        _candidate("retry-over-budget", 8 * 1024**3, memory_crash_evidence=True),
    ], hosts=host_set)
    assert {item["id"] for item in plan["unplaced"]} == {_cid("alone-over-budget"), _cid("retry-over-budget")}
    for host in plan["hosts"]:
        if host["status"] == "available":
            assert host["cap_alone_bytes"] > host["budget_bytes"]
            assert all(item["memory_bytes"] <= host["cap_alone_bytes"] for item in host["assigned"])


def test_parse_candidate_contract_errors_are_precise():
    valid_id = _cid("valid")
    with pytest.raises(ContractError, match="expected object"):
        parse_candidate_table(None)
    with pytest.raises(ContractError, match="unknown field.*extra"):
        parse_candidate_table({"schema": "candidate-table/1", "candidates": [], "extra": 1})
    with pytest.raises(ContractError, match="expected 'candidate-table/1'"):
        parse_candidate_table({"schema": "candidate-table/2", "candidates": []})
    with pytest.raises(ContractError, match="expected array"):
        parse_candidate_table({"schema": "candidate-table/1", "candidates": {}})
    with pytest.raises(ContractError, match="missing field.*file"):
        parse_candidate_table(_candidate_table([{"id": valid_id}]))
    with pytest.raises(ContractError, match="duplicate id"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "a"}, {"id": valid_id, "file": "b"}]))
    with pytest.raises(ContractError, match="64-character lowercase SHA-256 candidate id"):
        parse_candidate_table(_candidate_table([{"id": "\x00", "file": "a"}]))
    with pytest.raises(ContractError, match="64-character lowercase SHA-256 candidate id"):
        parse_candidate_table(_candidate_table([{"id": "x\ny", "file": "a"}]))
    with pytest.raises(ContractError, match="64-character lowercase SHA-256 candidate id"):
        parse_candidate_table(_candidate_table([{"id": "A" * 64, "file": "a"}]))
    with pytest.raises(ContractError, match="64-character lowercase SHA-256 candidate id"):
        parse_candidate_table(_candidate_table([{"id": None, "file": "a"}]))
    with pytest.raises(ContractError, match="without control characters"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "src/\x7f.py"}]))
    with pytest.raises(ContractError, match="expected integer"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "a", "measured_memory_bytes": True}]))
    with pytest.raises(ContractError, match="expected low or high"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "a", "estimated_memory_class": []}]))
    with pytest.raises(ContractError, match="requires measured_on_host"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "a", "elapsed_seconds": 2}]))
    with pytest.raises(ContractError, match="must be at least 1"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "a", "measured_memory_bytes": 0}]))
    with pytest.raises(ContractError, match="expected finite number"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "a", "elapsed_seconds": float("inf"), "measured_on_host": "host"}]))
    with pytest.raises(ContractError, match="requires elapsed_seconds"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "a", "measured_on_host": "host"}]))
    with pytest.raises(ContractError, match="retry count cannot exceed 1"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "a", "memory_crash_retries": 2}]))
    with pytest.raises(ContractError, match="must be at least 0"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "a", "memory_crash_retries": -1}]))
    with pytest.raises(ContractError, match="requires memory_crash_evidence"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "a", "memory_crash_retries": 1}]))
    with pytest.raises(ContractError, match="expected boolean"):
        parse_candidate_table(_candidate_table([{"id": valid_id, "file": "a", "memory_crash_evidence": 1}]))


def test_parse_host_contract_edges_and_other_job_ownership():
    with pytest.raises(ContractError, match="unknown field.*extra"):
        parse_host_set({"schema": "fleet-host-set/1", "hosts": [], "extra": True})
    with pytest.raises(ContractError, match="expected 'fleet-host-set/1'"):
        parse_host_set({"schema": "fleet-host-set/2", "hosts": []})
    with pytest.raises(ContractError, match="expected array"):
        parse_host_set({"schema": "fleet-host-set/1", "hosts": None})
    with pytest.raises(ContractError, match="at least one host"):
        parse_host_set({"schema": "fleet-host-set/1", "hosts": []})
    duplicate = _hosts()
    duplicate["hosts"].append(copy.deepcopy(duplicate["hosts"][0]))
    with pytest.raises(ContractError, match="duplicate host id"):
        parse_host_set(duplicate)
    unsafe = _hosts()
    unsafe["hosts"][0]["id"] = "../escape"
    with pytest.raises(ContractError, match="safe host id"):
        parse_host_set(unsafe)
    unsafe = _hosts()
    unsafe["hosts"][0]["id"] = ".."
    with pytest.raises(ContractError, match="dot path segments"):
        parse_host_set(unsafe)
    wrong_factor = _hosts()
    wrong_factor["hosts"][0]["f_h"] = 2.01
    with pytest.raises(ContractError, match="must be at most 2.0"):
        parse_host_set(wrong_factor)
    wrong_factor["hosts"][0]["f_h"] = False
    with pytest.raises(ContractError, match="expected number"):
        parse_host_set(wrong_factor)
    wrong_factor["hosts"][0]["f_h"] = 0.49
    with pytest.raises(ContractError, match="must be at least 0.5"):
        parse_host_set(wrong_factor)
    with pytest.raises(ContractError, match=r"expected array; use \[\]"):
        _parse_other_running(None, "other_running")
    with pytest.raises(ContractError, match="duplicate running job id"):
        _parse_other_running([
            {"id": "x", "expected_memory_bytes": 1, "workers": 0, "containers": 1},
            {"id": "x", "expected_memory_bytes": 1, "workers": 0, "containers": 1},
        ], "other_running")

    job_host_set = _hosts()
    facts = _facts(job_host_set, "host-small")
    facts.pop("jobs_reason")
    facts["jobs"] = [{
        "name": "running-job", "running": True, "exit": None, "exit_reason": "running",
        "progress_mtime_s": None, "progress_mtime_s_reason": "not recorded",
        "candidates_done": 0, "candidates_total": 10,
    }, {
        "name": "finished-job", "running": False, "exit": 0,
        "progress_mtime_s": 1.0, "candidates_done": 10, "candidates_total": 10,
    }]
    with pytest.raises(ContractError, match="missing running host job.*running-job"):
        parse_host_set(job_host_set)
    _host(job_host_set, "host-small")["other_running"] = [{
        "id": "running-job", "expected_memory_bytes": 1, "workers": 0, "containers": 1,
    }]
    parsed = parse_host_set(job_host_set)
    assert parsed[1].other_running[0]["id"] == "running-job"


def test_lane_demand_file_and_number_validation(tmp_path):
    demand_file = tmp_path / "run-gate.toml"
    demand_file.write_text(
        "schema_version = 1\n[lanes.r2.demand]\nunit='candidate'\ninstance_memory='250m'\n"
        "unit_memory='120m'\nunit_memory_high='600m'\nunit_seconds_ref=95\nefficient_upper_bound=2\n",
        encoding="utf-8",
    )
    assert extract_demand(demand_file, "r2") == {
        "unit": "candidate", "instance_memory_bytes": 250 * 1024**2,
        "unit_memory_bytes": 120 * 1024**2, "unit_memory_high_bytes": 600 * 1024**2,
        "unit_seconds_ref": 95.0, "efficient_upper_bound": 2,
    }
    with pytest.raises(ContractError, match=r"has no \[lanes.missing.demand\]"):
        extract_demand(demand_file, "missing")
    demand_file.write_text("[lanes.r2.demand]\nunit = [", encoding="utf-8")
    with pytest.raises(ContractError, match="invalid Run-Gate TOML"):
        extract_demand(demand_file, "r2")
    demand_file.write_text("[lanes.r2]\n", encoding="utf-8")
    with pytest.raises(ContractError, match=r"has no \[lanes.r2.demand\]"):
        extract_demand(demand_file, "r2")
    demand_file.write_text("[lanes.r2]\ndemand = 1\n", encoding="utf-8")
    with pytest.raises(ContractError, match=r"has no \[lanes.r2.demand\]"):
        extract_demand(demand_file, "r2")
    demand_file.write_text("lanes = 1\n", encoding="utf-8")
    with pytest.raises(ContractError, match=r"has no \[lanes.r2.demand\]"):
        extract_demand(demand_file, "r2")
    demand_file.write_bytes(bytes([255]))
    with pytest.raises(ContractError, match="invalid Run-Gate TOML"):
        extract_demand(demand_file, "r2")
    with pytest.raises(ContractError, match="unknown field.*extra"):
        _plan([], demand=DEMAND | {"extra": True})
    for unit in ("worker", "run"):
        with pytest.raises(ContractError, match="demand.unit: fleet plan currently requires 'candidate'"):
            _plan([], demand=DEMAND | {"unit": unit})
    with pytest.raises(ContractError, match="expected finite number"):
        _number(10**400, "number")
    with pytest.raises(ContractError, match="expected number"):
        _number(True, "number")
    with pytest.raises(ContractError, match="expected finite number"):
        _number(float("inf"), "number")
    with pytest.raises(ContractError, match="at least 0.1"):
        _plan([], batch_multiplier=0)
    with pytest.raises(FileNotFoundError):
        extract_demand(tmp_path / "missing.toml", "r2")
    with pytest.raises(ContractError, match="lane: expected non-empty string"):
        plan_campaign("  ", _candidate_table([]), _hosts(), DEMAND)


def test_time_reference_and_host_eta_refuse_incomparable_scores():
    hosts = parse_host_set(_hosts())
    assert _time_reference(hosts) == (6, "6.4.0", 2500, None)
    assert _time_reference(()) == (
        None, None, None, "no eligible host is available for a Geekbench reference"
    )
    row = _host_plan(_plan([_candidate("x", 100_000_000)]), "host-large")
    assert _host_time(row, 76, 6, "6.4.0", 2500) == pytest.approx(76 * 2500 / 3000 * 0.7)
    assert _host_time(row, 76, None, "6.4.0", 2500) is None
    assert _host_time(row, 76, 6, None, 2500) is None
    assert _host_time(row, 76, 6, "6.4.0", None) is None
    assert _host_time(row, 76, 7, "7.0.0", 2500) is None
    assert _host_time(row, 76, 6, "6.5.0", 2500) is None
    assert _host_time({"speed": None}, 76, 6, "6.4.0", 2500) is None
    assert _host_eta(row, [], 6, "6.4.0", 2500) == (0.0, None)
    assert _host_eta(row, [{"time_ref_seconds": 1}], None, None, None)[1] == "common Geekbench reference is unavailable"
    assert _host_eta({"speed": None}, [{"time_ref_seconds": 1}], 6, "6.4.0", 2500)[1] == (
        "host Geekbench score is unavailable or uses a different major/version"
    )

    refused_mismatch = _hosts()
    small_facts = _facts(refused_mismatch, "host-small")
    small_facts["policy"]["host_role"] = "shared-prod"
    small_facts["speed"]["geekbench_major"] = 7
    small_facts["speed"]["geekbench_version"] = "7.0.0"
    small_facts["speed"]["basis"] = "geekbench7-measured"
    eligible_reference = _plan([_candidate("x", 100_000_000)], hosts=refused_mismatch)
    assert eligible_reference["time_reference"]["geekbench_major"] == 6
    assert "reason" not in eligible_reference["time_reference"]
    assert _host_plan(eligible_reference, "host-small")["status"] == "refused"
    assert eligible_reference["eta_seconds"] is not None

    incomparable_source = _hosts()
    source_facts = _facts(incomparable_source, "host-small")
    source_facts["policy"]["host_role"] = "shared-prod"
    source_facts["speed"]["geekbench_major"] = 7
    source_facts["speed"]["geekbench_version"] = "7.0.0"
    source_facts["speed"]["basis"] = "geekbench7-measured"
    ignored_timing = _plan(
        [_candidate("x", 100_000_000, elapsed_seconds=12, measured_on_host="host-small")],
        hosts=incomparable_source,
    )
    ignored_candidate = next(
        item for host in ignored_timing["hosts"] for item in host["assigned"] if item["id"] == _cid("x")
    )
    assert ignored_timing["time_reference"]["geekbench_major"] == 6
    assert ignored_candidate["time_source"] == "declared_reference"
    assert ignored_candidate["time_ref_seconds"] == 95

    no_scores = _hosts()
    for host in no_scores["hosts"]:
        host["facts"]["speed"] = None
        host["facts"]["speed_reason"] = "no score"
    assert _time_reference(parse_host_set(no_scores)) == (None, None, None, "no host has a Geekbench record")
    balanced = _plan([_candidate(f"balanced-{index}", 100_000_000) for index in range(12)], hosts=no_scores)
    assert all(_host_plan(balanced, host_id)["assigned"] for host_id in ("host-small", "host-large"))
    assert all(
        "balanced normalized reference work per worker" in item["assignment_reason"]
        for host in balanced["hosts"]
        for item in host["assigned"]
    )

    no_eligible_hosts = _hosts()
    for host in no_eligible_hosts["hosts"]:
        host["facts"]["policy"]["host_role"] = "shared-prod"
    no_eligible_plan = _plan([_candidate("x", 100_000_000)], hosts=no_eligible_hosts)
    assert no_eligible_plan["time_reference"]["reason"] == (
        "no eligible host is available for a Geekbench reference"
    )
    assert no_eligible_plan["unplaced"] == [
        {"id": _cid("x"), "reason": "no host is available for placement"}
    ]

    for field, expected in (
        ("geekbench_major", "host Geekbench majors differ; scores are never mixed"),
        ("geekbench_version", "host Geekbench versions differ; scores are never mixed"),
        ("baseline_score", "host Geekbench baseline scores differ; ETA is not comparable"),
    ):
        mismatch = _hosts()
        if field == "geekbench_major":
            _facts(mismatch, "host-large")["speed"][field] = 7
            _facts(mismatch, "host-large")["speed"]["basis"] = "geekbench7-measured"
        else:
            _facts(mismatch, "host-large")["speed"][field] = 999 if field == "baseline_score" else "6.5.0"
        assert _time_reference(parse_host_set(mismatch))[3] == expected

    known_reference = _hosts()
    _facts(known_reference, "host-small")["speed"] = None
    _facts(known_reference, "host-small")["speed_reason"] = "unknown score"
    _facts(known_reference, "host-large")["psi"]["memory"]["some_avg10_pct"] = 99
    no_host_score = _plan([_candidate("x", 100_000_000)], hosts=known_reference)
    assert _assigned_host(no_host_score, "x")["eta_reason"] == "common Geekbench reference is unavailable"
    assert no_host_score["eta_seconds"] is None


def test_host_refusal_arithmetic_helpers_and_wave_invariant_error():
    parsed = parse_host_set(_hosts())
    host = parsed[0]
    assert _refusal(host, 0, 0, 0) is None
    capacity = _capacity(host, {"efficient_upper_bound": 2, "instance_memory_bytes": 250 * 1024**2})
    assert capacity["status"] == "available"
    assert capacity["worker_slots"] == 2
    assert capacity["campaign_slots"] == 1
    assert "reason" not in capacity
    no_workers = _capacity(host, {"efficient_upper_bound": 0, "instance_memory_bytes": 250 * 1024**2})
    assert no_workers["reason"] == "no usable worker slots remain after host policy limits"

    bad_host = copy.deepcopy(host)
    facts = bad_host.facts
    facts["memory"]["overcommit"] = -1
    assert "overcommit factor is invalid" in _refusal(bad_host, 0, 0, 0)

    reserve_host = copy.deepcopy(host)
    reserve_host.facts["memory"]["for_lanes_bytes"] = 1
    assert "reserve leaves no physical RAM" in _refusal(reserve_host, 0, 0, 0)

    high_host = dict(capacity)
    high_host.update({"worker_slots": 1, "budget_bytes": 0, "average_worker_budget_bytes": 0, "slot_worker_capacities": [1]})
    with pytest.raises(ContractError, match="cannot fit a worker wave"):
        _waves([{"input": type("Input", (), {"candidate_id": "x"})(), "memory_bytes": 1}], high_host)

    sparse_host = {
        "worker_slots": 1,
        "budget_bytes": 10,
        "average_worker_budget_bytes": 10,
        "slot_worker_capacities": [0, 1],
    }
    sparse_wave = _waves(
        [{"input": type("Input", (), {"candidate_id": "x"})(), "memory_bytes": 1}],
        sparse_host,
    )[0]
    assert sparse_wave["slot_batches"] == [
        {"slot": 1, "worker_capacity": 1, "candidate_ids": ["x"]}
    ]

    mixes = [
        {"ram_class": "high", "memory_bytes": 10, "time_ref_seconds": 50, "input": type("Input", (), {"candidate_id": "h"})()},
        {"ram_class": "low", "memory_bytes": 1, "time_ref_seconds": 10, "input": type("Input", (), {"candidate_id": "l"})()},
        {"ram_class": "low", "memory_bytes": 1, "time_ref_seconds": 5, "input": type("Input", (), {"candidate_id": "f"})()},
    ]
    assert [item["input"].candidate_id for item in _ordered_mix(mixes)] == ["h", "f", "l"]


def test_missing_measurement_host_is_ignored_for_time_and_reported():
    absent_source = _plan([_candidate("x", 100_000_000, elapsed_seconds=1, measured_on_host="absent")])
    ignored = next(item for host in absent_source["hosts"] for item in host["assigned"])
    assert ignored["time_source"] == "declared_reference"
    assert "measurement host 'absent' is absent" in ignored["time_reason"]

    host_set = _hosts()
    _facts(host_set, "host-small")["speed"] = None
    _facts(host_set, "host-small")["speed_reason"] = "missing record"
    _facts(host_set, "host-large")["psi"]["memory"]["some_avg10_pct"] = 99
    result = _plan([_candidate("x", 100_000_000, elapsed_seconds=2, measured_on_host="host-small")], hosts=host_set)
    assigned = {item["id"]: item for host in result["hosts"] for item in host["assigned"]}[_cid("x")]
    assert assigned["time_source"] == "declared_reference"
    assert result["eta_reason"]

    host_set = _hosts()
    _facts(host_set, "host-small")["speed"] = None
    _facts(host_set, "host-small")["speed_reason"] = "missing record"
    result = _plan(
        [_candidate("x", 100_000_000, elapsed_seconds=2, measured_on_host="host-small")],
        hosts=host_set,
    )
    assigned = {item["id"]: item for host in result["hosts"] for item in host["assigned"]}[_cid("x")]
    assert assigned["time_source"] == "declared_reference"
    assert "measurement host 'host-small' has no matching Geekbench reference" in assigned["time_reason"]


def test_plan_cli_json_commands_and_errors(tmp_path, capsys):
    demand_path = tmp_path / "run-gate.toml"
    demand_path.write_text(
        "schema_version = 1\n[lanes.r2.demand]\nunit='candidate'\ninstance_memory='250m'\n"
        "unit_memory='120m'\nunit_memory_high='600m'\nunit_seconds_ref=95\nefficient_upper_bound=2\n",
        encoding="utf-8",
    )
    candidates_path = FIXTURES / "candidate-table.valid.json"
    hosts_path = FIXTURES / "fleet-host-set.valid.json"
    args = [
        "plan", "r2", "--candidates", str(candidates_path), "--hosts", str(hosts_path),
        "--demand", str(demand_path),
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "fleet-plan/1"
    assert main(args + ["--commands"]) == 0
    commands = capsys.readouterr().out
    assert commands.startswith("set -euo pipefail\n# host ")
    assert "if run-gate r2 --jobs " in commands and "--only-candidates" in commands
    assert "wave-" not in commands and "slot-" not in commands
    assert commands.count("if run-gate r2 --jobs ") == sum(
        len(host["batches"]) for host in result["hosts"] if host["status"] == "available"
    )
    assert main(args + ["--batch-multiplier", "0"]) == 2
    assert "batch_multiplier: must be at least 0.1" in capsys.readouterr().err
