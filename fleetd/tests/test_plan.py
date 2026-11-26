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

from fleetd.contracts import (
    CONTROL_PLANE_SERVICE_MEMORY_MIN_CEILING_FACTOR,
    CONTROL_PLANE_SERVICE_MEMORY_MIN_CEILINGS,
    CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS,
    CONTROL_PLANE_SERVICE_MEMORY_MIN_TOTAL_CEILING_BYTES,
    CONTROL_PLANE_SERVICE_OPTIONAL_UNITS,
    CONTROL_PLANE_SERVICE_UNITS,
    DEFAULT_CONTROL_PLANE_PROFILE_RESERVE_BYTES,
    ContractError,
    dump_json,
)
from fleetd.cli import main
from fleetd.facts import _parse_systemd_memory_min
from host_facts_v2 import host_set_v2, write_host_set_v2
from fleetd.plan import (
    _assign_candidates,
    _capacity,
    _control_plane_reserve,
    _fact_value,
    _host_eta,
    _host_time,
    _number,
    _ordered_mix,
    _parse_other_running,
    _refusal,
    _resolve_memory,
    _resolve_time,
    _reverse_id,
    _speed,
    _time_reference,
    _waves,
    extract_demand,
    parse_candidate_table,
    parse_host_set,
    plan_campaign,
    render_commands,
    replan_pending,
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
    return host_set_v2(_read_fixture("fleet-host-set.valid.json"))


def _facts(host_set, host_id):
    return next(host["facts"] for host in host_set["hosts"] if host["id"] == host_id)


def _value(facts, group, name):
    return facts[group][name]["v"]


def _set_field(table, name, value, reason="test source unavailable"):
    table[name] = {"v": None, "reason": reason} if value is None else {"v": value}


def _set_value(facts, group, name, value, reason="test source unavailable"):
    _set_field(facts[group], name, value, reason)


def _set_top_value(facts, name, value, reason="test source unavailable"):
    facts[name] = {"v": None, "reason": reason} if value is None else {"v": value}


def _set_speed(facts, value, reason="test score unavailable"):
    if value is None:
        facts["speed"] = {"v": None, "reason": reason}
    else:
        facts["speed"] = {"v": {key: {"v": item} for key, item in value.items()}}


def _update_speed(facts, **values):
    speed = facts["speed"]["v"]
    for key, value in values.items():
        speed[key] = {"v": value}


def _set_service_memory_mins(control_plane, values, load_states=None):
    control_plane["service_memory_min"] = {}
    load_states = load_states or ["loaded"] * len(CONTROL_PLANE_SERVICE_UNITS)
    for unit, value, load_state in zip(CONTROL_PLANE_SERVICE_UNITS, values, load_states, strict=True):
        row = {
            "memory_min_bytes": {"v": value} if value is not None else {
                "v": None, "reason": "systemd did not report a positive MemoryMin",
            },
            "load_state": {"v": load_state} if load_state is not None else {
                "v": None, "reason": "systemd did not report a load state",
            },
        }
        control_plane["service_memory_min"][unit] = row


# The documented per-service floors, in MiB, in CONTROL_PLANE_SERVICE_UNITS order.
FLOOR_MIB = [10, 210, 92, 24, 4, 5]


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
        _set_value(facts, "cpu", "online", profile["cpu"]["online"])
        _set_value(facts, "cpu", "oversubscribe_max", profile["cpu"]["oversubscribe_max"])
        _set_value(facts, "memory", "total_bytes", profile["memory"]["total_bytes"])
        _set_value(facts, "memory", "available_bytes", profile["memory"]["available_bytes"])
        _set_value(facts, "memory", "overcommit", profile["memory"]["overcommit"])
        _set_value(facts, "control_plane", "slice", "ctl.slice")
        _set_value(facts, "control_plane", "present", False)
        _set_value(facts, "control_plane", "memory_min_bytes", None, "ctl.slice has not been installed on this host")
        _set_value(facts, "control_plane", "cpu_weight", None, "ctl.slice has not been installed on this host")
        _set_speed(facts, dict(host_profile["speed"]))
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


def test_host_facts_v2_keeps_the_legacy_plan_capacity_and_assignment():
    """Pinned pre-migration /1 capacity figures for equivalent /2 inputs."""
    legacy = _read_fixture("fleet-host-set.valid.json")
    migrated = host_set_v2(legacy)
    plan = _plan([_candidate("same-placement", 120_000_000)], hosts=migrated)

    expected = {
        "host-small": (7_784_628_224, 11_414_798_336, 7_522_484_224),
        "host-large": (16_374_562_816, 24_299_700_224, 16_112_418_816),
    }
    for host_id, (physical, budget, cap_alone) in expected.items():
        host = _host_plan(plan, host_id)
        assert host["status"] == "available"
        assert host["physical_bytes"] == physical
        assert host["budget_bytes"] == budget
        assert host["cap_alone_bytes"] == cap_alone
        assert host["cpu_worker_slots"] == 5
        assert host["worker_slots"] == 2
    assert _assigned_host(plan, "same-placement")["id"] == "host-large"


def test_plan_preserves_special_alone_and_retry_work_on_one_host_and_renders_commands():
    plan = _plan([
        _candidate("big", 15 * 1024**3),
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
            overcommit = generator.choice([1.0, 1.25, 1.5, 2.0])
            _set_value(facts, "memory", "total_bytes", total)
            _set_value(facts, "memory", "available_bytes", total // 2)
            _set_value(facts, "memory", "overcommit", overcommit)
            control = facts["control_plane"]
            _set_value(facts, "control_plane", "memory_min_bytes", generator.choice([0, 768, 1024, 2048]) * 1024**2)
            _set_value(facts, "control_plane", "profile_reserve_bytes", generator.choice([768, 896, 1024]) * 1024**2)
            # Per-service facts stay inside each unit's documented ceiling, so
            # every generated host is plannable; the recomputation below still
            # derives the reserve from the INPUT facts, never from the plan.
            service_values = [
                generator.choice([
                    None,
                    CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS[unit] // 2,
                    CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS[unit],
                    CONTROL_PLANE_SERVICE_MEMORY_MIN_CEILINGS[unit],
                ])
                for unit in CONTROL_PLANE_SERVICE_UNITS
            ]
            _set_service_memory_mins(control, service_values)
            online = generator.choice([2, 4, 8])
            oversubscribe = generator.choice([1.0, 1.25, 1.5])
            _set_value(facts, "cpu", "online", online)
            _set_value(facts, "cpu", "oversubscribe_max", oversubscribe)
            _update_speed(facts, single_core=generator.choice([1500, 2000, 2500, 3000]))
            host["f_h"] = generator.choice([0.5, 1.0, 1.7, 2.0])
            if generator.random() < 0.4:
                host["other_running"] = [{
                    "id": "other", "expected_memory_bytes": generator.choice([1, 2, 3]) * 1024**3,
                    "workers": generator.choice([0, 1, 2]), "containers": 1,
                }]
            other = host["other_running"]
            # Every row is positive-or-missing and within its ceiling, and every
            # load state is "loaded", so a missing value means that unit's
            # documented default; the profile constant is never used here.
            reserve = sum(
                value if value is not None else CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS[unit]
                for value, unit in zip(service_values, CONTROL_PLANE_SERVICE_UNITS, strict=True)
            )
            physical = total - reserve
            overhead = sum(job["expected_memory_bytes"] for job in other) + instance
            slots = min(
                int(online * oversubscribe) - sum(job["workers"] for job in other),
                demand["efficient_upper_bound"],
            )
            expected[host["id"]] = (physical, overhead, physical * overcommit - overhead, slots)
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
        _set_speed(host["facts"], None, "synthetic missing score")
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
    _update_speed(_facts(changed, "host-large"), geekbench_version="6.5.0")
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
    _update_speed(_facts(changed_multi, "host-large"), multi_core=1)
    same_eta = _plan(rows, hosts=changed_multi)
    assert original["eta_seconds"] != slower["eta_seconds"]
    assert original["eta_seconds"] == same_eta["eta_seconds"]
    assert _host_plan(original, "host-large")["cap_bytes"] == _host_plan(slower, "host-large")["cap_bytes"]


def test_host_refusals_have_explicit_reasons_and_null_eta_reasons():
    variants = []
    host_set = _hosts()
    _set_value(_facts(host_set, "host-small"), "policy", "host_role", "shared-prod")
    variants.append((host_set, "shared-prod hosts are not eligible"))

    host_set = _hosts()
    _set_value(_facts(host_set, "host-small"), "policy", "host_role", None, "not configured")
    variants.append((host_set, "policy.host_role: not configured"))

    host_set = _hosts()
    _set_top_value(_facts(host_set, "host-small")["psi"]["memory"], "some_avg10_pct", 26)
    variants.append((host_set, "memory PSI some avg10 26% exceeds"))

    host_set = _hosts()
    psi = _facts(host_set, "host-small")["psi"]["io"]
    psi["some_avg10_pct"] = {"v": None, "reason": "fixture unavailable"}
    variants.append((host_set, "psi.io.some_avg10_pct: fixture unavailable"))

    host_set = _hosts()
    _set_top_value(_facts(host_set, "host-small")["cgroup_v2"], "mounted", False)
    variants.append((host_set, "cgroup v2 is not mounted"))

    host_set = _hosts()
    cgroup = _facts(host_set, "host-small")["cgroup_v2"]
    cgroup["controllers"] = {"v": None, "reason": "not readable"}
    variants.append((host_set, "cgroup_v2.controllers: not readable"))

    host_set = _hosts()
    _set_value(_facts(host_set, "host-small"), "memory", "total_bytes", None, "not readable")
    variants.append((host_set, "memory.total_bytes: not readable"))

    # An unknown raw MemTotal is now an explicit planning refusal.
    host_set = _hosts()
    _set_value(_facts(host_set, "host-small"), "memory", "total_bytes", None, "not readable")
    plan = _plan([_candidate("candidate", 120_000_000)], hosts=host_set)
    assert _host_plan(plan, "host-small")["status"] == "refused"
    assert _host_plan(plan, "host-small")["reason"] == "memory.total_bytes: not readable"

    host_set = _hosts()
    _set_value(_facts(host_set, "host-small"), "cpu", "online", None, "not readable")
    variants.append((host_set, "cpu.online: not readable"))

    host_set = _hosts()
    _set_value(_facts(host_set, "host-small"), "policy", "max_lane_containers", None, "not configured")
    variants.append((host_set, "policy.max_lane_containers: not configured"))

    for host_set, reason in variants:
        plan = _plan([_candidate("candidate", 120_000_000)], hosts=host_set)
        refused = _host_plan(plan, "host-small")
        assert refused["status"] == "refused"
        assert reason in refused["reason"]
        assert refused["eta_seconds"] is None
        assert refused["eta_reason"] == refused["reason"]


def test_fact_value_and_partial_speed_keep_unknown_reasons_explicit():
    with pytest.raises(ContractError, match="speed: expected host-facts/2 value wrapper"):
        _fact_value(None, "speed")
    with pytest.raises(ContractError, match="speed: expected host-facts/2 value wrapper"):
        _fact_value({"reason": "missing wrapper"}, "speed")
    assert _fact_value({"v": None, "reason": "not collected"}, "speed") == (None, "not collected")

    host = parse_host_set(_hosts())[0]
    host.facts["speed"] = {
        "v": {
            "geekbench_major": {"v": 6}, "geekbench_version": {"v": "6.4.0"},
            "baseline_score": {"v": None, "reason": "no score"}, "single_core": {"v": 2100},
            "multi_core": {"v": 7800}, "basis": {"v": "geekbench6-measured"},
        },
    }
    assert _speed(host) is None


def test_parse_host_set_refuses_unknown_running_job_state_and_id():
    host_set = _hosts()
    facts = _facts(host_set, "host-small")
    job = {
        "id": {"v": "running-job"}, "state": {"v": None, "reason": "state unavailable"},
        "requested_by": {"v": None, "reason": "unknown"}, "resources": {"v": None, "reason": "unknown"},
        "started_utc": {"v": None, "reason": "unknown"}, "progress_done": {"v": 0},
        "progress_total": {"v": 1}, "progress_unit": {"v": "candidate"},
        "last_progress_utc": {"v": None, "reason": "unknown"},
    }
    facts["jobs"] = {"v": [job]}
    with pytest.raises(ContractError, match=r"jobs\[\]\.state: state unavailable"):
        parse_host_set(host_set)

    job["state"] = {"v": "running"}
    job["id"] = {"v": None, "reason": "identifier unavailable"}
    with pytest.raises(ContractError, match=r"jobs\[\]\.id: identifier unavailable"):
        parse_host_set(host_set)


@pytest.mark.parametrize(
    ("group", "name", "value", "reason"),
    [
        ("policy", "host_role", None, "policy.host_role: role source unavailable"),
        ("policy", "psi_refuse_some_avg10", None, "policy.psi_refuse_some_avg10: threshold unavailable"),
        ("cgroup_v2", "mounted", None, "cgroup_v2.mounted: mount source unavailable"),
        ("cgroup_v2", "controllers", None, "cgroup_v2.controllers: controller source unavailable"),
        ("memory", "total_bytes", None, "memory.total_bytes: memory source unavailable"),
        ("cpu", "online", None, "cpu.online: cpu source unavailable"),
        ("cpu", "oversubscribe_max", None, "cpu.oversubscribe_max: cpu policy unavailable"),
        ("policy", "max_lane_containers", None, "policy.max_lane_containers: container policy unavailable"),
        ("memory", "overcommit", None, "memory.overcommit: overcommit policy unavailable"),
    ],
)
def test_refusal_names_each_required_unknown_fact(group, name, value, reason):
    host = parse_host_set(_hosts())[0]
    _set_value(host.facts, group, name, value, reason.split(": ", 1)[1])
    assert _refusal(host, 0, 0, 0) == reason


def test_refusal_names_psi_thresholds_and_exhausted_capacity_reasons():
    host = parse_host_set(_hosts())[0]
    host.facts["psi"]["memory"]["some_avg10_pct"] = {"v": 26}
    assert _refusal(host, 0, 0, 0) == "memory PSI some avg10 26% exceeds host refusal threshold 25%"

    host = parse_host_set(_hosts())[0]
    host.facts["psi"]["io"]["some_avg10_pct"] = {"v": 26}
    assert _refusal(host, 0, 0, 0) == "io PSI some avg10 26% exceeds host refusal threshold 25%"

    host = parse_host_set(_hosts())[0]
    host.facts["psi"]["io"]["some_avg10_pct"] = {"v": None, "reason": "io PSI unavailable"}
    assert _refusal(host, 0, 0, 0) == "psi.io.some_avg10_pct: io PSI unavailable"

    host = parse_host_set(_hosts())[0]
    host.facts["cpu"]["oversubscribe_max"] = {"v": 1}
    assert _refusal(host, 0, 4, 0) == "no worker slots remain after other running jobs"

    host = parse_host_set(_hosts())[0]
    assert _refusal(host, 0, 0, 8) == "no lane container slots remain after other running jobs"

    host = parse_host_set(_hosts())[0]
    assert _refusal(host, 10**20, 0, 0) == "other running work consumes the host's overcommitted RAM budget"


def test_refusal_reports_profile_reserve_reason_when_per_service_collection_failed():
    host = parse_host_set(_hosts())[0]
    control = host.facts["control_plane"]
    control["profile_reserve_bytes"] = {"v": None, "reason": "profile reserve not configured"}
    for row in control["service_memory_min"].values():
        row["memory_min_bytes"] = {"v": None, "reason": "query failed"}
        row["load_state"] = {"v": None, "reason": "query failed"}
    assert _refusal(host, 0, 0, 0) == (
        "control_plane.profile_reserve_bytes: profile reserve not configured"
    )


@pytest.mark.parametrize(
    ("values_mib", "load_states", "expected_mib", "source", "missing_units", "sources"),
    [
        # Every service reports a real (bounded) floor: their sum, nothing added.
        ([20, 300, 150, 40, 10, 12], None, 532, "service-memory-min", [], ["reported"] * 6),

        # A declared optional unit is absent: it promises nothing, so it adds 0.
        (
            [FLOOR_MIB[0], FLOOR_MIB[1], FLOOR_MIB[2], FLOOR_MIB[3], FLOOR_MIB[4], None],
            ["loaded", "loaded", "loaded", "loaded", "loaded", "not-found"],
            sum(FLOOR_MIB[:5]), "per-service-default", [CONTROL_PLANE_SERVICE_UNITS[5]],
            ["reported"] * 5 + ["absent-optional"],
        ),
        # A masked optional unit is absent too.
        (
            [None] * 5 + [None], ["loaded"] * 5 + ["masked"],
            sum(FLOOR_MIB[:5]), "per-service-default", list(CONTROL_PLANE_SERVICE_UNITS),
            ["default"] * 5 + ["absent-optional"],
        ),
        # A mandatory service with no value uses its documented default, not 0.
        (
            [20, None, 92, 24, 4, 5], None,
            20 + 210 + 92 + 24 + 4 + 5, "per-service-default", [CONTROL_PLANE_SERVICE_UNITS[1]],
            ["reported", "default"] + ["reported"] * 4,
        ),
        # An optional unit with an UNKNOWN load state is not assumed absent.
        (
            [10, 210, 92, 24, 4, None], ["loaded"] * 5 + [None],
            sum(FLOOR_MIB), "per-service-default", [CONTROL_PLANE_SERVICE_UNITS[5]],
            ["reported"] * 5 + ["default"],
        ),
        # No per-service fact at all: the only remaining use of the constant.
        (None, None, 768, "profile-constant", list(CONTROL_PLANE_SERVICE_UNITS), []),
    ],
)
def test_control_plane_reserve_resolves_one_term_per_service_with_provenance(
    values_mib, load_states, expected_mib, source, missing_units, sources
):
    host_set = _hosts()
    facts = _facts(host_set, "host-small")
    control = facts["control_plane"]
    _set_field(control, "profile_reserve_bytes", 768 * 1024**2)
    if values_mib is not None:
        _set_service_memory_mins(
            control,
            [None if value is None else value * 1024**2 for value in values_mib],
            load_states,
        )

    plan = _plan([_candidate("reserve", 120_000_000)], hosts=host_set)
    host = _host_plan(plan, "host-small")
    assert host["status"] == "available"
    assert host["reserve_bytes"] == expected_mib * 1024**2
    assert host["reserve_source"] == source
    assert host["reserve_missing_services"] == missing_units
    assert [row["source"] for row in host.get("reserve_services", [])] == sources
    if sources:
        assert [row["unit"] for row in host["reserve_services"]] == list(CONTROL_PLANE_SERVICE_UNITS)
        assert sum(row["bytes"] for row in host["reserve_services"]) == host["reserve_bytes"]


def test_the_reserve_never_drops_below_what_was_actually_reported():
    """The per-service fallback replaces a missing fact; it never shrinks a reported one."""
    facts = _facts(_hosts(), "host-small")
    control = facts["control_plane"]
    reported = {unit: floor * 1024**2 for unit, floor in zip(CONTROL_PLANE_SERVICE_UNITS, FLOOR_MIB, strict=True)}
    _set_service_memory_mins(control, list(reported.values()))
    reserve = _control_plane_reserve(facts)
    assert reserve.bytes == sum(reported.values())
    assert reserve.source == "service-memory-min"
    assert all(row["source"] == "reported" for row in reserve.services)

    # An absent optional unit is the only way a term may become zero, and it is
    # named as absent rather than silently dropped.
    control["service_memory_min"]["mdt-psi-guard.service"] = {
        "memory_min_bytes": {"v": None, "reason": "unit is absent"},
        "load_state": {"v": "not-found"},
    }
    absent = _control_plane_reserve(facts)
    assert absent.bytes == sum(reported.values()) - reported["mdt-psi-guard.service"]
    assert absent.source == "per-service-default"
    assert [row for row in absent.services if row["source"] == "absent-optional"] == [
        {"unit": "mdt-psi-guard.service", "source": "absent-optional", "bytes": 0}
    ]


@pytest.mark.parametrize("total_bytes", [1, 2**60])
def test_reserve_does_not_change_with_tiny_or_huge_ram(total_bytes):
    facts = _facts(_hosts(), "host-small")
    _set_value(facts, "memory", "total_bytes", total_bytes)
    control = facts["control_plane"]
    _set_field(control, "profile_reserve_bytes", 900 * 1024**2)
    # Per-service facts are present but none reports a value: every term is that
    # service's documented floor, and the total still ignores installed RAM.
    _set_service_memory_mins(control, [None] * len(CONTROL_PLANE_SERVICE_UNITS))
    reserve = _control_plane_reserve(facts)
    assert reserve.bytes == sum(FLOOR_MIB) * 1024**2
    assert reserve.source == "per-service-default"
    assert reserve.missing == CONTROL_PLANE_SERVICE_UNITS

    # With no per-service fact at all, the fixed profile constant still does not
    # move with installed RAM.
    del control["service_memory_min"]
    assert _control_plane_reserve(facts).bytes == 900 * 1024**2


def test_legacy_parent_slice_memory_min_is_ignored_by_service_sum():
    host_set = _hosts()
    facts = _facts(host_set, "host-small")
    control = facts["control_plane"]
    _set_field(control, "present", False)
    _set_field(control, "memory_min_bytes", 7 * 1024**3)
    _set_service_memory_mins(control, [floor * 1024**2 for floor in FLOOR_MIB])
    plan = _plan([_candidate("reserve", 120_000_000)], hosts=host_set)
    host = _host_plan(plan, "host-small")
    assert host["reserve_bytes"] == sum(FLOOR_MIB) * 1024**2
    assert host["reserve_source"] == "service-memory-min"
    assert host["reserve_missing_services"] == []


def test_infinity_memory_min_is_not_reported_and_uses_that_service_default():
    host_set = _hosts()
    control = _facts(host_set, "host-small")["control_plane"]
    parsed_value, reason = _parse_systemd_memory_min("infinity", CONTROL_PLANE_SERVICE_UNITS[0])
    assert parsed_value is None and "infinity" in reason
    values = [floor * 1024**2 for floor in FLOOR_MIB]
    values[0] = None
    _set_service_memory_mins(control, values)
    control["service_memory_min"][CONTROL_PLANE_SERVICE_UNITS[0]]["memory_min_bytes"] = {
        "v": None, "reason": reason,
    }
    plan = _plan([_candidate("infinity", 120_000_000)], hosts=host_set)
    host = _host_plan(plan, "host-small")
    assert host["reserve_bytes"] == sum(FLOOR_MIB) * 1024**2
    assert host["reserve_source"] == "per-service-default"
    assert host["reserve_missing_services"] == [CONTROL_PLANE_SERVICE_UNITS[0]]


def test_profile_fallback_varies_by_host_profile_without_cross_host_pooling():
    host_set = _hosts()
    _set_field(_facts(host_set, "host-small")["control_plane"], "profile_reserve_bytes", 768 * 1024**2)
    _set_field(_facts(host_set, "host-large")["control_plane"], "profile_reserve_bytes", 1024 * 1024**2)
    plan = _plan([_candidate("reserve", 120_000_000)], hosts=host_set)
    assert _host_plan(plan, "host-small")["reserve_bytes"] == 768 * 1024**2
    assert _host_plan(plan, "host-large")["reserve_bytes"] == 1024 * 1024**2
    assert all(host["reserve_source"] == "profile-constant" for host in plan["hosts"])


def test_placement_caps_and_refusal_move_by_the_reserve_difference_only():
    host_set = _hosts()
    small = _facts(host_set, "host-small")
    large = _facts(host_set, "host-large")
    _set_value(large, "policy", "host_role", "shared-prod")
    _set_value(small, "memory", "total_bytes", 8 * 1024**3)
    _set_field(small["control_plane"], "profile_reserve_bytes", 768 * 1024**2)
    # Real reported floors (each within its documented ceiling) that sum to
    # 600 MiB, so this host's reserve differs from the 768 MiB constant by 168 MiB.
    reported_mib = [40, 300, 200, 50, 8, 2]
    assert sum(reported_mib) == 600
    _set_service_memory_mins(
        small["control_plane"], [value * 1024**2 for value in reported_mib],
    )

    demand = {**DEMAND, "efficient_upper_bound": 1}
    edge_candidate = _candidate("edge", 7300 * 1024**2)
    service_plan = _plan([edge_candidate], hosts=host_set, demand=demand)
    service_host = _host_plan(service_plan, "host-small")

    fallback_set = copy.deepcopy(host_set)
    fallback_facts = _facts(fallback_set, "host-small")
    # No per-service fact at all is the only remaining use of the constant, so
    # the fallback direction of this difference is still exercised.
    _set_service_memory_mins(
        fallback_facts["control_plane"],
        [None] * len(CONTROL_PLANE_SERVICE_UNITS),
        [None] * len(CONTROL_PLANE_SERVICE_UNITS),
    )
    fallback_plan = _plan([edge_candidate], hosts=fallback_set, demand=demand)
    fallback_host = _host_plan(fallback_plan, "host-small")
    reserve_delta = 768 * 1024**2 - 600 * 1024**2
    assert service_host["reserve_bytes"] == 600 * 1024**2
    assert service_host["reserve_source"] == "service-memory-min"
    assert fallback_host["reserve_bytes"] == 768 * 1024**2
    assert fallback_host["reserve_source"] == "profile-constant"
    assert service_host["physical_bytes"] - fallback_host["physical_bytes"] == reserve_delta
    assert service_host["budget_bytes"] - fallback_host["budget_bytes"] == reserve_delta * 1.5
    assert service_host["cap_alone_bytes"] - fallback_host["cap_alone_bytes"] == reserve_delta
    assert service_host["cap_bytes"] - fallback_host["cap_bytes"] == reserve_delta
    assert [item["id"] for item in service_host["assigned"]] == [_cid("edge")]
    assert _host_plan(fallback_plan, "host-small")["assigned"] == []

    refusal_set = _hosts()
    refusal_facts = _facts(refusal_set, "host-small")
    _set_value(refusal_facts, "memory", "total_bytes", 600 * 1024**2)
    refusal_control = refusal_facts["control_plane"]
    refusal_mib = [10, 210, 92, 24, 4, 5]
    assert sum(refusal_mib) == 345
    _set_service_memory_mins(refusal_control, [value * 1024**2 for value in refusal_mib])
    service_input = next(host for host in parse_host_set(refusal_set) if host.host_id == "host-small")
    assert _refusal(service_input, 0, 0, 0) is None
    # Only the no-per-service-fact case still uses the 768 MiB constant.
    _set_service_memory_mins(
        refusal_control,
        [None] * len(CONTROL_PLANE_SERVICE_UNITS),
        [None] * len(CONTROL_PLANE_SERVICE_UNITS),
    )
    fallback_input = next(host for host in parse_host_set(refusal_set) if host.host_id == "host-small")
    assert "reserve leaves no physical RAM" in _refusal(fallback_input, 0, 0, 0)


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
    assert all(host["reserve_source"] == "profile-constant" for host in plan["hosts"])
    assert all(host["reserve_missing_services"] == list(CONTROL_PLANE_SERVICE_UNITS) for host in plan["hosts"])
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
    if "execution" in plan:
        assert plan["execution"]["schema"] == "fleet-plan-execution/1"
        assert len(plan["execution"]["commit"]) == 40
    deterministic_plan = {key: value for key, value in plan.items() if key != "execution"}
    digest = hashlib.sha256(dump_json(deterministic_plan).encode("utf-8")).hexdigest()
    assert digest == "fa3d5ddbe8a2df95e74b467944382c9ce09abadd28492bb3bb76ed4a59185edd"


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
        _set_top_value(host["facts"]["psi"]["memory"], "some_avg10_pct", 99)
    none_available = _plan([
        _candidate("normal", 100_000_000),
        _candidate("retry", 100_000_000, memory_crash_evidence=True),
    ], hosts=no_hosts)
    assert all(host["status"] == "refused" for host in none_available["hosts"])
    assert {item["id"] for item in none_available["unplaced"]} == {_cid("normal"), _cid("retry")}
    assert none_available["eta_seconds"] == 0.0
    assert "run-gate " not in render_commands(none_available)


def test_replan_pending_reuses_placement_rules_and_refuses_bad_inputs():
    rows = [
        _candidate("replan-low", 120 * 1024**2),
        _candidate("replan-retry", 130 * 1024**2, memory_crash_evidence=True),
    ]
    plan = plan_campaign("r2", _candidate_table(rows), _hosts(), DEMAND)
    host = next(row for row in plan["hosts"] if row["status"] == "available" and row["assigned"])
    low_id = _cid("replan-low")
    retry_id = _cid("replan-retry")
    batches, unplaceable = replan_pending(plan, [low_id], host["id"])
    assert [candidate_id for batch in batches for candidate_id in batch["candidate_ids"]] == [low_id]
    assert unplaceable == []
    assert replan_pending(plan, [], host["id"]) == ([], [])
    retry_batches, _ = replan_pending(plan, [retry_id], host["id"])
    assert retry_batches[0]["kind"] == "retry"
    assert retry_batches[0]["waves"][0]["retry_reason"] == "memory-crash retry must run alone with --jobs 1"

    with pytest.raises(ContractError, match="invalid candidate id"):
        replan_pending(plan, ["invalid"], host["id"])
    with pytest.raises(ContractError, match="not assigned"):
        replan_pending(plan, ["f" * 64], host["id"])
    blocked = copy.deepcopy(plan)
    next(row for row in blocked["hosts"] if row["id"] == host["id"])["status"] = "blocked"
    with pytest.raises(ContractError, match="not available"):
        replan_pending(blocked, [low_id], host["id"])
    missing_facts = copy.deepcopy(plan)
    missing_host = next(row for row in missing_facts["hosts"] if row["id"] == host["id"])
    del missing_host["cap_bytes"]
    with pytest.raises(ContractError, match="missing planner capacity facts"):
        replan_pending(missing_facts, [low_id], host["id"])
    impossible = copy.deepcopy(plan)
    impossible_host = next(row for row in impossible["hosts"] if row["id"] == host["id"])
    candidate = next(row for row in impossible_host["assigned"] if row["id"] == low_id)
    candidate["memory_bytes"] = impossible_host["cap_alone_bytes"] + 1
    only_batches, only_unplaceable = replan_pending(impossible, [low_id], host["id"])
    assert only_batches == []
    assert [row["id"] for row in only_unplaceable] == [low_id]
    assert "requires" in only_unplaceable[0]["reason"]

    without_multiplier = copy.deepcopy(plan)
    without_multiplier.pop("batch_multiplier", None)
    assert replan_pending(without_multiplier, [low_id], host["id"])[0]


def test_replan_pending_places_what_fits_and_returns_the_unplaceable_ids_with_reasons():
    rows = [_candidate(f"replan-part-{index}", 120 * 1024**2) for index in range(7)]
    rows.append(_candidate("replan-part-huge", 120 * 1024**2))
    plan = plan_campaign("r2", _candidate_table(rows), _hosts(), DEMAND)
    host = next(row for row in plan["hosts"] if row["id"] == "host-small")
    ids = [_cid(f"replan-part-{index}") for index in range(7)]
    huge_id = _cid("replan-part-huge")
    for row in plan["hosts"]:
        for candidate in row["assigned"]:
            if candidate["id"] == huge_id:
                candidate["memory_bytes"] = 9 * 1024**3
    batches, unplaceable = replan_pending(plan, [*ids, huge_id], host["id"])
    placed = sorted(candidate_id for batch in batches for candidate_id in batch["candidate_ids"])
    assert placed == sorted(ids)
    assert [row["id"] for row in unplaceable] == [huge_id]
    assert "requires" in unplaceable[0]["reason"] or "needs" in unplaceable[0]["reason"]


def test_overcommit_changes_sum_budget_but_never_physical_alone_cap():
    host_set = _hosts()
    for host in host_set["hosts"]:
        _set_value(host["facts"], "memory", "overcommit", 0.5)
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
    legacy = _read_fixture("fleet-host-set.valid.json")
    with pytest.raises(ContractError, match="expected host-facts/2; host-facts/1 is not read"):
        parse_host_set(legacy)
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
    facts["jobs"] = {"v": [{
        "id": {"v": "running-job"}, "state": {"v": "running"},
        "requested_by": {"v": None, "reason": "unknown"},
        "resources": {"v": None, "reason": "unknown"},
        "started_utc": {"v": None, "reason": "unknown"},
        "progress_done": {"v": 0}, "progress_total": {"v": 10},
        "progress_unit": {"v": "candidate"},
        "last_progress_utc": {"v": None, "reason": "unknown"},
    }, {
        "id": {"v": "finished-job"}, "state": {"v": "complete"},
        "requested_by": {"v": None, "reason": "unknown"},
        "resources": {"v": None, "reason": "unknown"},
        "started_utc": {"v": None, "reason": "unknown"},
        "progress_done": {"v": 10}, "progress_total": {"v": 10},
        "progress_unit": {"v": "candidate"},
        "last_progress_utc": {"v": None, "reason": "unknown"},
    }]}
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
    _set_value(small_facts, "policy", "host_role", "shared-prod")
    _update_speed(small_facts, geekbench_major=7, geekbench_version="7.0.0", basis="geekbench7-measured")
    eligible_reference = _plan([_candidate("x", 100_000_000)], hosts=refused_mismatch)
    assert eligible_reference["time_reference"]["geekbench_major"] == 6
    assert "reason" not in eligible_reference["time_reference"]
    assert _host_plan(eligible_reference, "host-small")["status"] == "refused"
    assert eligible_reference["eta_seconds"] is not None

    incomparable_source = _hosts()
    source_facts = _facts(incomparable_source, "host-small")
    _set_value(source_facts, "policy", "host_role", "shared-prod")
    _update_speed(source_facts, geekbench_major=7, geekbench_version="7.0.0", basis="geekbench7-measured")
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
        _set_speed(host["facts"], None, "no score")
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
        _set_value(host["facts"], "policy", "host_role", "shared-prod")
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
            _update_speed(_facts(mismatch, "host-large"), geekbench_major=7, basis="geekbench7-measured")
        else:
            _update_speed(_facts(mismatch, "host-large"), **{
                field: 999 if field == "baseline_score" else "6.5.0",
            })
        assert _time_reference(parse_host_set(mismatch))[3] == expected

    known_reference = _hosts()
    _set_speed(_facts(known_reference, "host-small"), None, "unknown score")
    _set_top_value(_facts(known_reference, "host-large")["psi"]["memory"], "some_avg10_pct", 99)
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
    _set_value(facts, "memory", "overcommit", -1)
    assert "overcommit factor is invalid" in _refusal(bad_host, 0, 0, 0)

    reserve_host = copy.deepcopy(host)
    _set_value(reserve_host.facts, "memory", "total_bytes", 1)
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
    _set_speed(_facts(host_set, "host-small"), None, "missing record")
    _set_top_value(_facts(host_set, "host-large")["psi"]["memory"], "some_avg10_pct", 99)
    result = _plan([_candidate("x", 100_000_000, elapsed_seconds=2, measured_on_host="host-small")], hosts=host_set)
    assigned = {item["id"]: item for host in result["hosts"] for item in host["assigned"]}[_cid("x")]
    assert assigned["time_source"] == "declared_reference"
    assert result["eta_reason"]

    host_set = _hosts()
    _set_speed(_facts(host_set, "host-small"), None, "missing record")
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
    hosts_path = write_host_set_v2(FIXTURES / "fleet-host-set.valid.json", tmp_path / "hosts-v2.json")
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


@pytest.mark.parametrize("bad", [True, False, 0, -5, 1.5e9, float("inf"), float("nan"), "512", None, [1], {"x": 1}])
def test_reserve_treats_every_non_positive_integer_service_value_as_not_reported(bad):
    # The contract validator already rejects most of these; the planner must not
    # depend on that, because a service value is a promise only when it is a real
    # positive integer (bool is an int subclass and must not count as 1 byte).
    facts = _facts(_hosts(), "host-small")
    control = facts["control_plane"]
    _set_service_memory_mins(control, [floor * 1024**2 for floor in FLOOR_MIB])
    control["service_memory_min"][CONTROL_PLANE_SERVICE_UNITS[2]]["memory_min_bytes"] = (
        {"v": None, "reason": "no positive report"} if bad is None else {"v": bad}
    )
    _set_field(control, "profile_reserve_bytes", 900 * 1024**2)
    reserve = _control_plane_reserve(facts)
    assert reserve.source == "per-service-default"
    assert reserve.missing == (CONTROL_PLANE_SERVICE_UNITS[2],)
    # The unusable row contributes that service's documented default, never zero.
    assert reserve.bytes == sum(FLOOR_MIB) * 1024**2
    assert reserve.services[2] == {
        "unit": CONTROL_PLANE_SERVICE_UNITS[2], "source": "default",
        "bytes": CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS[CONTROL_PLANE_SERVICE_UNITS[2]],
    }


@pytest.mark.parametrize("table", [None, [], "x", {}])
def test_reserve_uses_the_profile_constant_only_when_no_per_service_fact_exists(table):
    facts = _facts(_hosts(), "host-small")
    control = facts["control_plane"]
    control["service_memory_min"] = table
    _set_field(control, "profile_reserve_bytes", 800 * 1024**2)
    reserve = _control_plane_reserve(facts)
    assert (reserve.bytes, reserve.source, reserve.missing, reserve.services) == (
        800 * 1024**2, "profile-constant", CONTROL_PLANE_SERVICE_UNITS, (),
    )


def test_reserve_refuses_an_implausible_reported_value_instead_of_summing_it():
    """A reported value above its documented sane ceiling is refused with a reason."""
    host_set = _hosts()
    control = _facts(host_set, "host-small")["control_plane"]
    _set_service_memory_mins(control, [floor * 1024**2 for floor in FLOOR_MIB])
    unit = "docker.service"
    over = CONTROL_PLANE_SERVICE_MEMORY_MIN_CEILINGS[unit] + 1
    control["service_memory_min"][unit]["memory_min_bytes"] = {"v": over}
    plan = _plan([_candidate("x", 120_000_000)], hosts=host_set)
    refused = _host_plan(plan, "host-small")
    assert refused["status"] == "refused"
    assert unit in refused["reason"] and str(over) in refused["reason"]
    assert "per-service ceiling" in refused["reason"]
    # The refused plan still reports the reserve and names the reason, so the
    # evidence says what was refused rather than silently clamping.
    assert refused["reserve_reason"] == refused["reason"]
    # The term is named as a default (the reported value is not trusted), the
    # source no longer claims every term was reported, and the total therefore
    # says 345 MiB, not the 975 MiB the bad report would imply.
    assert refused["reserve_source"] == "per-service-default"
    assert refused["reserve_missing_services"] == [unit]
    assert {row["unit"]: row["source"] for row in refused["reserve_services"]}[unit] == "default"
    assert refused["reserve_bytes"] == sum(FLOOR_MIB) * 1024**2


def test_reserve_refuses_a_total_above_twice_the_documented_floors():
    """Every value individually plausible, the sum still absurd: refuse, do not sum."""
    facts = _facts(_hosts(), "host-small")
    control = facts["control_plane"]
    at_bound = [
        CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS[unit] * 2
        for unit in CONTROL_PLANE_SERVICE_UNITS
    ]
    assert sum(at_bound) == CONTROL_PLANE_SERVICE_MEMORY_MIN_TOTAL_CEILING_BYTES
    _set_service_memory_mins(control, at_bound)
    reserve = _control_plane_reserve(facts)
    # At exactly the bound the sum is still accepted and every term is the report.
    assert reserve.reason is None
    assert reserve.bytes == CONTROL_PLANE_SERVICE_MEMORY_MIN_TOTAL_CEILING_BYTES
    assert all(row["source"] == "reported" for row in reserve.services)

    # One byte more, each value still inside its own per-service ceiling: the
    # aggregate bound refuses instead of summing a drifted table.
    over = list(at_bound)
    over[0] += 1
    assert over[0] <= CONTROL_PLANE_SERVICE_MEMORY_MIN_CEILINGS[CONTROL_PLANE_SERVICE_UNITS[0]]
    _set_service_memory_mins(control, over)
    refused = _control_plane_reserve(facts)
    assert refused.reason is not None
    assert "twice the documented" in refused.reason and "FD-007b" in refused.reason
    assert refused.bytes == sum(over)


def test_a_partial_per_service_table_still_resolves_per_service_terms():
    """Any per-service fact means per-service terms; only NO fact uses the constant."""
    for kept in ("ssh.service", "docker.service"):
        facts = _facts(_hosts(), "host-small")
        control = facts["control_plane"]
        _set_field(control, "profile_reserve_bytes", 800 * 1024**2)
        control["service_memory_min"] = {
            kept: {
                "memory_min_bytes": {"v": CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS[kept]},
                "load_state": {"v": "loaded"},
            },
        }
        reserve = _control_plane_reserve(facts)
        assert reserve.source == "per-service-default"
        # The one present unit reports its floor; the five absent rows are
        # missing facts and contribute their documented defaults.
        assert reserve.bytes == sum(FLOOR_MIB) * 1024**2
        assert reserve.missing == tuple(
            unit for unit in CONTROL_PLANE_SERVICE_UNITS if unit != kept
        )
        assert {row["unit"]: row["source"] for row in reserve.services}[kept] == "reported"
        assert all(
            row["source"] == "default"
            for row in reserve.services
            if row["unit"] != kept
        )


def test_reserve_falls_back_per_service_when_the_table_has_rows_with_a_load_state_only():
    """A queried load state is a fact: the host answered, so defaults are per service."""
    facts = _facts(_hosts(), "host-small")
    control = facts["control_plane"]
    control["service_memory_min"] = {
        unit: {"memory_min_bytes": None, "load_state": "loaded"}
        for unit in CONTROL_PLANE_SERVICE_UNITS
    }
    _set_field(control, "profile_reserve_bytes", 800 * 1024**2)
    reserve = _control_plane_reserve(facts)
    assert reserve.source == "per-service-default"
    assert reserve.bytes == sum(FLOOR_MIB) * 1024**2
    assert reserve.missing == CONTROL_PLANE_SERVICE_UNITS
    assert [row["source"] for row in reserve.services] == ["default"] * 6


@pytest.mark.parametrize(
    "row",
    [
        "x",
        {"memory_min_bytes": None},
        {"memory_min_bytes": None, "load_state": None},
        {"memory_min_bytes": 0, "load_state": None},
        {"memory_min_bytes": -1, "load_state": 7},
        {"memory_min_bytes": True},
    ],
)
def test_reserve_keeps_the_profile_constant_when_every_row_is_a_failed_query(row):
    """The dangerous direction: systemd could not be asked, so six defaults
    (345 MiB) must not silently replace the 768 MiB constant."""
    facts = _facts(_hosts(), "host-small")
    control = facts["control_plane"]
    control["service_memory_min"] = {unit: row for unit in CONTROL_PLANE_SERVICE_UNITS}
    _set_field(control, "profile_reserve_bytes", 800 * 1024**2)
    reserve = _control_plane_reserve(facts)
    assert (reserve.bytes, reserve.source, reserve.services) == (
        800 * 1024**2, "profile-constant", (),
    )
    assert reserve.bytes > sum(FLOOR_MIB) * 1024**2


def test_one_answering_service_makes_the_other_failed_rows_per_service_defaults():
    facts = _facts(_hosts(), "host-small")
    control = facts["control_plane"]
    control["service_memory_min"] = {
        unit: {"memory_min_bytes": None} for unit in CONTROL_PLANE_SERVICE_UNITS
    }
    control["service_memory_min"]["docker.service"] = {
        "memory_min_bytes": None, "load_state": "loaded",
    }
    reserve = _control_plane_reserve(facts)
    assert reserve.source == "per-service-default"
    assert reserve.bytes == sum(FLOOR_MIB) * 1024**2
