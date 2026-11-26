import hashlib
import json
from pathlib import Path

import pytest

from fleetd.contracts import (
    CONTROL_PLANE_SERVICE_UNITS,
    ContractError,
    HostFacts,
    WorkRequest,
    _validate_group,
    dump_json,
    load_json,
    parse_memory_bytes,
    validate_host_facts,
    validate_lane_demand,
    validate_work_request,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _fails(call, fragment):
    with pytest.raises(ContractError, match=fragment):
        call()


def _cid(label):
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _v(value):
    return {"v": value}


def _unknown(reason="not-collected by this host-facts source"):
    return {"v": None, "reason": reason}


def _host_facts_2():
    services = {
        unit: {"memory_min_bytes": _v(1024), "load_state": _v("loaded")}
        for unit in CONTROL_PLANE_SERVICE_UNITS
    }
    speed = {
        "geekbench_major": _v(6), "geekbench_version": _v("6.4.0"),
        "baseline_score": _v(2500), "single_core": _v(2100), "multi_core": _v(7800),
        "basis": _v("geekbench6-measured"),
    }
    return {
        "schema": "host-facts/2",
        "agent_version": "1.2.3",
        "collected_utc": "2026-11-26T12:34:56Z",
        "boot_id": _v("00112233-4455-6677-8899-aabbccddeeff"),
        "cpu": {"online": _v(4), "planning_cap": _v(5), "oversubscribe_max": _v(1.25)},
        "load1": _v(0.5), "load5": _v(0.4), "load15": _v(0.3),
        "speed": _v(speed),
        "memory": {
            "total_bytes": _v(8 * 1024**3), "available_bytes": _v(6 * 1024**3),
            "host_reserve_bytes": _v(512 * 1024**2), "swap_total_bytes": _v(0),
            "swap_free_bytes": _v(0), "overcommit": _v(1.5),
        },
        "psi": {
            name: {
                f"{pressure}_avg{window}_pct": _v(0.5)
                for pressure in ("some", "full") for window in (10, 60, 300)
            }
            for name in ("cpu", "memory", "io")
        },
        "gate_slice": {
            "memory_current_bytes": _v(1024), "memory_warm_bytes": _v(512),
            "memory_high_bytes": _v(4096), "memory_max_bytes": _v(8192),
            "cpu_max": _v({"quota_us": 500000, "period_us": 100000}),
        },
        "disks": _v([{
            "name": _v("checkout"), "mount": _v("/work"), "total_bytes": _v(10000),
            "free_bytes": _v(5000), "fs": _v("btrfs"), "floor_bytes": _v(1000),
        }]),
        "cgroup_v2": {"mounted": _v(True), "controllers": _v(["cpu", "io", "memory"])},
        "kernel_interfaces": {
            "cgroup_freeze": _v(True), "memory_reclaim": _v(True),
            "memory_reclaim_swappiness_arg": _unknown(),
        },
        "ksm": {
            "run": _v(1), "advisor_mode": _v("scan-time"), "advisor_max_cpu": _v(8),
            "pages_sharing": _v(16), "lane_optin_shim": _v(True),
        },
        "control_plane": {
            "slice": _v("ctl.slice"), "present": _v(True), "cpu_weight": _v(10000),
            "memory_min_bytes": _v(1024), "profile_reserve_bytes": _v(768 * 1024**2),
            "service_memory_min": services,
        },
        "guard": {
            "guard_present": _v(True), "guard_state": _v("report"),
            "config_digest": _v("sha256:" + "a" * 64), "frozen_units": _unknown(),
        },
        "freeze": {
            "since_utc": _unknown(), "by": _unknown(), "freeze_reason": _unknown(), "queued": _unknown(),
        },
        "policy": {
            "host_role": _v("dedicated-dev"), "psi_refuse_some_avg10": _v(25),
            "max_lane_containers": _v(8), "planning_cap": _v(5), "max_footprint": _v("large"),
            "max_planned_bytes": _v(7 * 1024**3),
            "footprint_charges_bytes": _v({"tiny": 1024, "small": 2048, "medium": 4096, "large": 8192}),
            "priority_classes_by_principal": _v({"test": ["dev", "gate"]}),
            "disk_floors": _v({"checkout": 1000, "docker": 2000}),
            "max_upload_bytes": _v(1024**3),
            "admission": _v({"queue_path": "/run/fleetd/queue", "wait_s": 30, "settle_s": 5}),
        },
        "queue": {
            "planned_cores": _unknown("not-collected: admission book arrives with the host agent"),
            "charged_bytes": _unknown("not-collected: admission book arrives with the host agent"),
            "waiting": _unknown("not-collected: admission book arrives with the host agent"),
        },
        "jobs": _unknown("not-collected"),
        "tools": _unknown("not-collected"),
    }


def _walk_v2_wrappers(value, path=()):
    if isinstance(value, dict):
        if "v" in value and set(value) <= {"v", "reason"}:
            yield path, value
            return
        for key, item in value.items():
            yield from _walk_v2_wrappers(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_v2_wrappers(item, (*path, index))


def _at_path(value, path):
    current = value
    for part in path:
        current = current[part]
    return current


def test_host_facts_fixture_is_typed_and_preserved():
    result = validate_host_facts(_fixture("host-facts.valid.json"))
    assert isinstance(result, HostFacts)
    assert result.to_dict()["schema"] == "host-facts/1"


def test_host_facts_v2_validates_wrapped_values_and_metadata():
    data = _host_facts_2()
    result = validate_host_facts(data)
    assert isinstance(result, HostFacts)
    assert result.to_dict() == data
    assert result.to_dict()["schema"] == "host-facts/2"


def test_host_facts_v2_applies_null_reason_rules_to_every_wrapped_field():
    data = _host_facts_2()
    wrappers = list(_walk_v2_wrappers(data))
    assert len(wrappers) > 60
    for path, _wrapper in wrappers:
        candidate = json.loads(json.dumps(data))
        target = _at_path(candidate, path)
        target["v"] = None
        target.pop("reason", None)
        _fails(lambda candidate=candidate: validate_host_facts(candidate), "null requires reason")

    for path, wrapper in wrappers:
        if wrapper["v"] is None:
            continue
        candidate = json.loads(json.dumps(data))
        target = _at_path(candidate, path)
        target["reason"] = "stale reason beside a value"
        _fails(lambda candidate=candidate: validate_host_facts(candidate), "only allowed when v is null")


def test_host_facts_v2_rejects_invalid_metadata_shapes_and_policy_values():
    mutations = (
        (lambda data: data.update(agent_version="1.2"), "agent_version: expected SemVer"),
        (lambda data: data.update(collected_utc="yesterday"), "expected an RFC 3339 UTC timestamp"),
        (lambda data: data["cpu"].update(extra=_v(1)), "cpu: unknown field.*extra"),
        (lambda data: data["memory"].update(total_bytes={"v": None}), "null requires reason"),
        (lambda data: data["policy"].update(max_footprint=_v("huge")), "expected tiny, small, medium, or large"),
        (lambda data: data["policy"].update(admission=_v({"queue_path": "/queue", "wait_s": 1})), "missing field.*settle_s"),
        (lambda data: data["queue"].update(extra=_v(0)), "unknown field.*extra"),
        (lambda data: data.update(extra=True), "unknown field.*extra"),
    )
    for mutate, message in mutations:
        data = _host_facts_2()
        mutate(data)
        _fails(lambda data=data: validate_host_facts(data), message)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda data: data.update(schema="host-facts/other"), "expected 'host-facts/1' or 'host-facts/2'"),
        (lambda data: data.update(extra=True), "unknown field.*extra"),
        (lambda data: data.pop("policy"), "missing field.*policy"),
        (lambda data: data.update(boot_id=None), "null requires boot_id_reason"),
        (lambda data: (data.update(boot_id=None), data.update(boot_id_reason="  ")), "expected non-empty string"),
        (lambda data: data.update(boot_id_reason="stale"), "only allowed when boot_id is null"),
        (lambda data: data.update(host_now_boottime_s=True), "expected number"),
        (lambda data: data.update(host_now_boottime_s=-1), "must be at least 0"),
        (lambda data: data["cpu"].update(extra=True), "cpu: unknown field.*extra"),
        (lambda data: data["cpu"].update(online=True), "cpu.online: expected integer"),
        (lambda data: data["cpu"].update(online=0), "cpu.online: must be at least 1"),
        (lambda data: data["cpu"].update(for_lanes=0), "cpu.for_lanes: must be at least"),
        (lambda data: data["cpu"].update(oversubscribe_max=0), "oversubscribe_max: must be at least"),
        (lambda data: data["speed"].update(basis="geekbench7-measured"), "speed.basis: must name"),
        (lambda data: data["speed"].update(baseline_score=False), "baseline_score: expected integer"),
        (lambda data: data["speed"].update(single_core=0), "single_core: must be at least 1"),
        (lambda data: data.update(speed=None), "null requires speed_reason"),
        (lambda data: data["memory"].update(available_bytes=None), "null requires available_bytes_reason"),
        (lambda data: data["memory"].update(extra=True), "memory: unknown field.*extra"),
        (lambda data: data["memory"].update(overcommit=0), "overcommit: must be at least"),
        (lambda data: data["psi"]["memory"].update(some_avg10_pct=101), "some_avg10_pct: must be at most 100"),
        (lambda data: data["psi"].update(extra={}), "psi: unknown field.*extra"),
        (lambda data: data["psi"]["io"].update(full_avg10_pct_reason="stale"), "only allowed when full_avg10_pct is null"),
        (lambda data: data["cgroup_v2"].update(mounted=1), "mounted: expected boolean"),
        (lambda data: data["cgroup_v2"].update(controllers="memory"), "controllers: expected array"),
        (lambda data: data["cgroup_v2"].update(controllers=None), "null requires controllers_reason"),
        (lambda data: data["cgroup_v2"].update(controllers=["memory", "memory"]), "controllers: duplicate value"),
        (lambda data: data["kernel_interfaces"].update(cgroup_freeze=None), "null requires cgroup_freeze_reason"),
        (lambda data: data["ksm"].update(run=True), "ksm.run: expected integer"),
        (lambda data: data["control_plane"].update(slice=""), "control_plane.slice: expected non-empty string"),
        (lambda data: data["control_plane"].update(present="yes"), "control_plane.present: expected boolean"),
        (lambda data: data["control_plane"].update(cpu_weight=0), "cpu_weight: must be at least 1"),
        (lambda data: data["control_plane"].update(profile_reserve_bytes=True), "profile_reserve_bytes: expected integer"),
        (
            lambda data: (data["control_plane"].pop("memory_min_bytes"), data["control_plane"].update(memory_min_bytes_reason="stale")),
            "memory_min_bytes_reason: only allowed when memory_min_bytes is null",
        ),
        (lambda data: data["control_plane"].update(service_memory_min={}), "service_memory_min: missing field.*cgprofiled.service"),
        (lambda data: data["guard"].update(enabled=None), "null requires enabled_reason"),
        (lambda data: data["policy"].update(psi_refuse_some_avg10=101), "psi_refuse_some_avg10: must be at most 100"),
        (lambda data: data["policy"].update(max_lane_containers=0), "max_lane_containers: must be at least 1"),
        (lambda data: data["policy"].update(host_role="other"), "host_role: expected 'dedicated-dev' or 'shared-prod'"),
    ],
)
def test_host_facts_rejects_invalid_shapes_and_values(edit, message):
    data = _fixture("host-facts.valid.json")
    edit(data)
    _fails(lambda: validate_host_facts(data), message)


def test_host_facts_rejects_non_object_root():
    _fails(lambda: validate_host_facts(None), "expected object")


def test_host_facts_rejects_missing_reason_for_null_speed_and_unrecognized_speed_shape():
    data = _fixture("host-facts.valid.json")
    data["speed"] = None
    _fails(lambda: validate_host_facts(data), "null requires speed_reason")
    data["speed_reason"] = "not recorded"
    assert validate_host_facts(data).to_dict()["speed"] is None
    data["speed"] = {"geekbench_major": 6}
    data["speed_reason"] = "stale reason"
    _fails(lambda: validate_host_facts(data), "only allowed when speed is null")
    data.pop("speed_reason")
    data["speed"] = {"geekbench_major": 6}
    _fails(lambda: validate_host_facts(data), "speed: missing field")

    data["speed"] = None
    data["speed_reason"] = "not recorded"
    assert validate_host_facts(data).to_dict()["speed"] is None


def test_host_facts_reasoned_nulls_are_valid_at_each_group_boundary():
    data = _fixture("host-facts.valid.json")
    data["cpu"]["model"] = None
    data["cpu"]["model_reason"] = "model unavailable"
    data["memory"]["available_bytes"] = None
    data["memory"]["available_bytes_reason"] = "counter unavailable"
    data["psi"]["memory"]["some_avg10_pct"] = None
    data["psi"]["memory"]["some_avg10_pct_reason"] = "not mounted"
    data["ksm"]["advisor_mode"] = None
    data["ksm"]["advisor_mode_reason"] = "kernel has no advisor"
    data["control_plane"]["memory_min_bytes"] = None
    data["control_plane"]["memory_min_bytes_reason"] = "slice missing"
    data["guard"]["state"] = None
    data["guard"]["state_reason"] = "state file absent"
    data["policy"]["host_role"] = None
    data["policy"]["host_role_reason"] = "host role not configured"
    assert validate_host_facts(data).to_dict()["cpu"]["model"] is None


def test_host_facts_accepts_profile_reserve_and_reasoned_per_service_memory_min():
    data = _fixture("host-facts.valid.json")
    data["control_plane"]["profile_reserve_bytes"] = 900 * 1024**2
    data["control_plane"]["service_memory_min"] = {
        unit: {
            "memory_min_bytes": 1024 if index == 0 else None,
            **({} if index == 0 else {"memory_min_bytes_reason": "systemd property is unset"}),
        }
        for index, unit in enumerate(CONTROL_PLANE_SERVICE_UNITS)
    }
    validated = validate_host_facts(data).to_dict()
    assert validated["control_plane"]["profile_reserve_bytes"] == 900 * 1024**2
    assert validated["control_plane"]["service_memory_min"]["ssh.service"]["memory_min_bytes"] == 1024


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data["control_plane"].update(service_memory_min={"unknown.service": {"memory_min_bytes": 1}}), "service_memory_min: unknown field.*unknown.service"),
        (lambda data: data["control_plane"].update(service_memory_min={unit: {"memory_min_bytes": None} for unit in CONTROL_PLANE_SERVICE_UNITS}), "null requires memory_min_bytes_reason"),
        (lambda data: data["control_plane"].update(service_memory_min={unit: {"memory_min_bytes": True} for unit in CONTROL_PLANE_SERVICE_UNITS}), "memory_min_bytes: expected integer"),
        (
            lambda data: data["control_plane"].update(service_memory_min={
                unit: {"memory_min_bytes": 1, "load_state": None}
                for unit in CONTROL_PLANE_SERVICE_UNITS
            }),
            "load_state: null requires load_state_reason",
        ),
        (
            lambda data: data["control_plane"].update(service_memory_min={
                unit: {"memory_min_bytes": 1, "load_state": 7}
                for unit in CONTROL_PLANE_SERVICE_UNITS
            }),
            "load_state: expected string",
        ),
    ],
)
def test_host_facts_rejects_invalid_service_memory_min_maps(mutation, message):
    data = _fixture("host-facts.valid.json")
    mutation(data)
    _fails(lambda: validate_host_facts(data), message)


def test_host_facts_accepts_an_absent_service_table_with_a_reason():
    data = _fixture("host-facts.valid.json")
    data["control_plane"]["service_memory_min_reason"] = "systemd was not queried for this root"
    validated = validate_host_facts(data).to_dict()
    assert validated["control_plane"]["service_memory_min_reason"] == (
        "systemd was not queried for this root"
    )


def test_host_facts_rejects_an_absent_table_reason_beside_a_table_or_without_one():
    for mutate in (
        lambda data: data["control_plane"].update(
            service_memory_min_reason="both",
            service_memory_min={unit: {"memory_min_bytes": 1} for unit in CONTROL_PLANE_SERVICE_UNITS},
        ),
        lambda data: data["control_plane"].update(service_memory_min_reason="  "),
        lambda data: data["control_plane"].update(service_memory_min_reason=None),
        lambda data: data["control_plane"].update(service_memory_min_reason=1),
    ):
        data = _fixture("host-facts.valid.json")
        mutate(data)
        _fails(lambda: validate_host_facts(data), "service_memory_min_reason")


def test_host_facts_accepts_nullable_load_state_with_a_reason():
    data = _fixture("host-facts.valid.json")
    data["control_plane"]["service_memory_min"] = {
        unit: {"memory_min_bytes": 1024, "load_state": None, "load_state_reason": "not queried"}
        for unit in CONTROL_PLANE_SERVICE_UNITS
    }
    validated = validate_host_facts(data).to_dict()
    assert validated["control_plane"]["service_memory_min"]["ssh.service"]["load_state"] is None


def test_host_facts_accepts_null_cgroup_controllers_with_reason():
    data = _fixture("host-facts.valid.json")
    data["cgroup_v2"]["controllers"] = None
    data["cgroup_v2"]["controllers_reason"] = "mount is absent"
    assert validate_host_facts(data).to_dict()["cgroup_v2"]["controllers"] is None


def test_host_facts_rejects_controller_reason_when_controllers_are_present():
    data = _fixture("host-facts.valid.json")
    data["cgroup_v2"]["controllers_reason"] = "stale"
    _fails(lambda: validate_host_facts(data), "controllers_reason: only allowed when controllers is null")


def test_host_facts_accepts_job_and_tool_inventory_and_rejects_bad_records():
    data = _fixture("host-facts.valid.json")
    data.pop("jobs_reason")
    data.pop("tools_reason")
    data["jobs"] = [{
        "name": "campaign.host.s1.b1.digest",
        "running": True,
        "exit": None,
        "exit_reason": "job is still running",
        "progress_mtime_s": 123.0,
        "candidates_done": 2,
        "candidates_total": 4,
    }]
    data["tools"] = {"run_gate": ["25.6.0"], "images": {"tester-unified:local": "sha256:fixture"}}
    assert validate_host_facts(data).to_dict()["jobs"][0]["candidates_done"] == 2
    data["jobs"].append(dict(data["jobs"][0]))
    _fails(lambda: validate_host_facts(data), "duplicate job name")
    data["jobs"] = [{
        "name": "x", "running": False, "exit": 0, "progress_mtime_s": None,
        "progress_mtime_s_reason": "no progress file", "candidates_done": 3, "candidates_total": 2,
    }]
    _fails(lambda: validate_host_facts(data), "cannot exceed candidates_total")
    data["jobs"] = "none"
    _fails(lambda: validate_host_facts(data), "jobs: expected array")
    data["jobs"] = None
    data["jobs_reason"] = "not available"
    data["tools"] = {"run_gate": [], "images": []}
    _fails(lambda: validate_host_facts(data), "tools.images: expected object")


def test_private_group_validator_rejects_unknown_contract_type_and_nonfinite_numbers():
    _fails(lambda: _validate_group({"x": 1}, "example", {"x": ("odd", None, None)}), "unsupported contract type")
    data = _fixture("host-facts.valid.json")
    data["host_now_boottime_s"] = float("inf")
    _fails(lambda: validate_host_facts(data), "expected finite number")


def test_demand_parser_accepts_design_units_and_rejects_shadowing_values():
    demand = _fixture("work-request.valid.json")["demand"]
    parsed = validate_lane_demand(demand)
    assert parsed["unit_memory_bytes"] == 120 * 1024**2
    assert parsed["unit_memory_high_bytes"] == 600 * 1024**2
    without_high = dict(demand)
    without_high.pop("unit_memory_high")
    assert "unit_memory_high_bytes" not in validate_lane_demand(without_high)
    with_baseline = {**demand, "baseline_seconds": 400}
    assert validate_lane_demand(with_baseline)["baseline_seconds"] == 400
    _fails(lambda: validate_lane_demand({**demand, "unknown": 1}), "unknown field.*unknown")
    missing = dict(demand)
    del missing["unit"]
    _fails(lambda: validate_lane_demand(missing), "missing field.*unit")
    for changed, message in (
        ({**demand, "unit": "host"}, "expected candidate, worker, or run"),
        ({**demand, "instance_memory": "0m"}, "invalid memory value"),
        ({**demand, "unit_memory_high": "1m"}, "must be at least unit_memory"),
        ({**demand, "baseline_seconds": True}, "expected number"),
        ({**demand, "baseline_seconds": -1}, "must be at least 0"),
        ({**demand, "baseline_seconds": 10**400}, "expected finite number"),
        ({**demand, "unit_seconds_ref": True}, "expected number"),
        ({**demand, "unit_seconds_ref": 0}, "must be at least"),
        # An integer beyond float range is a contract error, not an OverflowError.
        ({**demand, "unit_seconds_ref": 10**400}, "expected finite number"),
        ({**demand, "efficient_upper_bound": False}, "expected integer"),
        ({**demand, "efficient_upper_bound": 0}, "must be at least 1"),
    ):
        _fails(lambda changed=changed: validate_lane_demand(changed), message)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 1), ("2", 2), ("3b", 3), ("4K", 4096), ("5kb", 5120), ("6KiB", 6144),
     ("7m", 7 * 1024**2), ("8mb", 8 * 1024**2), ("9MiB", 9 * 1024**2),
     ("10g", 10 * 1024**3), ("11gb", 11 * 1024**3), ("12GiB", 12 * 1024**3)],
)
def test_memory_units_are_binary_and_deterministic(value, expected):
    assert parse_memory_bytes(value, "memory") == expected


@pytest.mark.parametrize("value", [True, False, 0, -1, 2.5, None, "", "0b", "1.5g", "1t"])
def test_memory_parser_rejects_invalid_values(value):
    _fails(lambda: parse_memory_bytes(value, "memory"), ".*")


def test_work_request_validates_identity_demand_and_candidate_sets():
    data = _fixture("work-request.valid.json")
    assert isinstance(validate_work_request(data), WorkRequest)
    data["carry_over"] = {"source": "prior-store", "reason": "same judge"}
    data.pop("carry_over_reason")
    assert validate_work_request(data).to_dict()["carry_over"]["source"] == "prior-store"


def test_work_request_requires_array_candidate_lists():
    data = _fixture("work-request.valid.json")
    data["planned_ids"] = "candidate-a"
    _fails(lambda: validate_work_request(data), "planned_ids: expected array")


def test_work_request_rejects_non_digest_candidate_ids():
    data = _fixture("work-request.valid.json")
    data["planned_ids"][0] = "candidate-a"
    _fails(
        lambda: validate_work_request(data),
        r"work-request/1\.planned_ids\[0\]: expected a 64-character lowercase SHA-256 candidate id",
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data.update(schema="work-request/2"), "expected 'work-request/1'"),
        (lambda data: data.update(extra=True), "unknown field.*extra"),
        (lambda data: data.update(campaign_id=""), "campaign_id: expected non-empty string"),
        (lambda data: data.update(commit="abcd"), "full lowercase Git object ID"),
        (lambda data: data.update(image_digest="sha256:bad"), "64 lowercase hex digits"),
        (lambda data: data.update(floors=[]), "floors: expected object"),
        (lambda data: data.update(floors={"assay": 12}), "floors.assay: expected string"),
        (lambda data: data.update(planned_ids=[_cid("same"), _cid("same")]), "duplicate value"),
        (lambda data: data.update(pending_ids=[_cid("not-planned")]), "every pending ID"),
        (lambda data: (data.update(carry_over=None), data.pop("carry_over_reason")), "null requires carry_over_reason"),
        (lambda data: (data.update(carry_over=None), data.update(carry_over_reason="absent"), data.update(campaign_id="")), "campaign_id: expected non-empty string"),
        (lambda data: (data.update(carry_over={"source": "x", "reason": "prior"}), data.update(carry_over_reason="stale")), "only allowed when carry_over is null"),
        (lambda data: (data.update(carry_over={"source": "x"}), data.pop("carry_over_reason")), "work-request/1.carry_over: missing field.*reason"),
    ],
)
def test_work_request_rejects_invalid_contract(change, message):
    data = _fixture("work-request.valid.json")
    change(data)
    _fails(lambda: validate_work_request(data), message)


def test_json_loader_rejects_duplicate_keys_and_reports_syntax_location(tmp_path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    _fails(lambda: load_json(duplicate), "duplicate JSON object key: 'a'")
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{bad", encoding="utf-8")
    _fails(lambda: load_json(malformed), "invalid JSON at line 1 column")
    invalid_encoding = tmp_path / "not-utf8.json"
    invalid_encoding.write_bytes(bytes([255]))
    _fails(lambda: load_json(invalid_encoding), "JSON is not UTF-8")


def test_json_renderer_sorts_keys_and_ends_with_newline():
    assert dump_json({"z": 1, "a": 2}) == '{\n  "a": 2,\n  "z": 1\n}\n'
