import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleetd.contracts import (
    CONTROL_PLANE_SERVICE_UNITS,
    DEFAULT_CONTROL_PLANE_PROFILE_RESERVE_BYTES,
    ContractError,
    dump_json,
)
from fleetd.cli import main
from fleetd.facts import (
    _config_path as _validate_config_path,
    _cpu_list_count,
    _cpu_model,
    _meminfo_values,
    _object,
    _online_cpu_count,
    _parse_systemd_load_state,
    _parse_systemd_memory_min,
    _psi_value,
    _read_integer,
    _read_systemd_load_state,
    _read_systemd_memory_min,
    _rooted,
    _slice_path,
    collect_host_facts,
    load_facts_config,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "facts-root"


def _root(tmp_path):
    target = tmp_path / "root"
    shutil.copytree(FIXTURE_ROOT, target)
    return target


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _config_path(root):
    return root / "etc/fleetd/facts.toml"


def _value(fact):
    return fact["v"]


def test_facts_collector_reads_only_injected_root_and_emits_design_facts():
    statvfs = lambda _path: SimpleNamespace(f_frsize=4096, f_bsize=4096, f_blocks=100, f_bavail=40)
    facts = collect_host_facts(FIXTURE_ROOT, disk_statvfs_reader=statvfs)
    assert facts["schema"] == "host-facts/2"
    assert facts["agent_version"]
    assert facts["collected_utc"].endswith("Z")
    assert _value(facts["boot_id"]) == "00112233-4455-6677-8899-aabbccddeeff"
    assert facts["cpu"] == {"online": {"v": 4}, "planning_cap": {"v": 5.0}, "oversubscribe_max": {"v": 1.25}}
    assert _value(facts["load1"]) == 0.62
    assert _value(facts["load5"]) == 0.70
    assert _value(facts["load15"]) == 0.66
    assert _value(facts["speed"]["v"]["geekbench_major"]) == 6
    assert _value(facts["speed"]["v"]["single_core"]) == 2100
    assert _value(facts["memory"]["total_bytes"]) == 8 * 1024**3
    assert _value(facts["memory"]["available_bytes"]) == 6 * 1024**3
    assert _value(facts["memory"]["host_reserve_bytes"]) == 671088640
    assert _value(facts["memory"]["swap_total_bytes"]) == 4 * 1024**3
    assert _value(facts["memory"]["overcommit"]) == 1.5
    assert _value(facts["psi"]["memory"]["some_avg10_pct"]) == 1.0
    assert _value(facts["psi"]["memory"]["full_avg10_pct"]) == 0.2
    assert _value(facts["psi"]["memory"]["some_avg60_pct"]) == 0.7
    assert _value(facts["psi"]["memory"]["full_avg300_pct"]) == 0.05
    assert _value(facts["psi"]["cpu"]["full_avg10_pct"]) is None
    assert facts["psi"]["cpu"]["full_avg10_pct"]["reason"]
    assert _value(facts["cgroup_v2"]["mounted"]) is True
    assert _value(facts["cgroup_v2"]["controllers"]) == ["cpu", "cpuset", "io", "memory", "pids"]
    assert _value(facts["kernel_interfaces"]["cgroup_freeze"]) is True
    assert _value(facts["kernel_interfaces"]["memory_reclaim"]) is True
    assert _value(facts["kernel_interfaces"]["memory_reclaim_swappiness_arg"]) is None
    assert facts["kernel_interfaces"]["memory_reclaim_swappiness_arg"]["reason"]
    assert _value(facts["ksm"]["run"]) == 1
    assert _value(facts["ksm"]["advisor_mode"]) == "scan-time"
    assert _value(facts["ksm"]["advisor_max_cpu"]) == 8
    assert _value(facts["ksm"]["lane_optin_shim"]) is True
    assert _value(facts["control_plane"]["slice"]) == "ctl.slice"
    assert _value(facts["control_plane"]["present"]) is True
    assert _value(facts["control_plane"]["cpu_weight"]) == 10000
    assert _value(facts["control_plane"]["memory_min_bytes"]) == 805306368
    assert _value(facts["control_plane"]["profile_reserve_bytes"]) == DEFAULT_CONTROL_PLANE_PROFILE_RESERVE_BYTES
    assert _value(facts["control_plane"]["service_memory_min"]["ssh.service"]["memory_min_bytes"]) is None
    assert facts["control_plane"]["service_memory_min"]["ssh.service"]["memory_min_bytes"]["reason"]
    assert _value(facts["gate_slice"]["memory_current_bytes"]) == 209715200
    assert _value(facts["gate_slice"]["memory_warm_bytes"]) == 157286400
    assert _value(facts["gate_slice"]["memory_high_bytes"]) == 7516192768
    assert _value(facts["gate_slice"]["memory_max_bytes"]) == 7516192768
    assert _value(facts["gate_slice"]["cpu_max"]) == {"quota_us": 500000, "period_us": 100000}
    assert _value(facts["disks"])[0]["name"] == {"v": "checkout"}
    assert _value(facts["disks"])[0]["total_bytes"] == {"v": 409600}
    assert _value(facts["disks"])[0]["free_bytes"] == {"v": 163840}
    assert _value(facts["disks"])[0]["floor_bytes"] == {"v": 1073741824}
    assert _value(facts["guard"]["guard_present"]) is True
    assert _value(facts["guard"]["guard_state"]) == "report"
    assert _value(facts["guard"]["config_digest"]).startswith("sha256:")
    assert _value(facts["guard"]["frozen_units"]) is None
    assert facts["guard"]["frozen_units"]["reason"].startswith("not-collected")
    assert _value(facts["policy"]["host_role"]) == "dedicated-dev"
    assert _value(facts["policy"]["max_lane_containers"]) == 8
    assert _value(facts["policy"]["planning_cap"]) == 5
    assert _value(facts["policy"]["max_upload_bytes"]) == 1024**3
    assert facts["queue"]["waiting"]["reason"] == "not-collected: admission book arrives with the host agent"
    assert facts["jobs"]["reason"] == "not-collected"
    assert facts["tools"]["v"] is None and facts["tools"]["reason"]
    serialized = dump_json(facts)
    assert "private-field-must-not-export" not in serialized
    assert "result_url" not in serialized
    repeated = collect_host_facts(FIXTURE_ROOT, disk_statvfs_reader=statvfs)
    assert {key: value for key, value in facts.items() if key != "collected_utc"} == {
        key: value for key, value in repeated.items() if key != "collected_utc"
    }


def test_facts_collect_cli_emits_valid_json_and_config_errors_are_user_facing(capsys, tmp_path):
    assert main(["facts", "collect", "--root", str(FIXTURE_ROOT)]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["schema"] == "host-facts/2"
    assert output.err == ""
    assert main(["facts", "collect", "--root", str(tmp_path / "missing")]) == 2
    assert "required config" in capsys.readouterr().err


def test_profile_reserve_is_host_configured_with_a_fixed_backward_compatible_default(tmp_path):
    root = _root(tmp_path)
    config_path = _config_path(root)
    assert load_facts_config(root).control_plane_profile_reserve_bytes == 768 * 1024**2
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'slice = "ctl.slice"',
            'slice = "ctl.slice"\nprofile_reserve_bytes = 943718400',
        ),
        encoding="utf-8",
    )
    facts = collect_host_facts(root)
    assert _value(facts["control_plane"]["profile_reserve_bytes"]) == 900 * 1024**2


def test_facts_config_is_required_closed_and_versioned(tmp_path):
    root = _root(tmp_path)
    config_path = _config_path(root)
    valid_config = config_path.read_text(encoding="utf-8")
    config_path.unlink()
    with pytest.raises(ContractError, match="required config"):
        load_facts_config(root)
    config_path.write_text("schema_version = [", encoding="utf-8")
    with pytest.raises(ContractError, match="invalid TOML"):
        load_facts_config(root)
    config_path.write_bytes(bytes([255]))
    with pytest.raises(ContractError, match="not UTF-8"):
        load_facts_config(root)
    config_path.write_text(valid_config.replace("schema_version = 1", "schema_version = true"), encoding="utf-8")
    with pytest.raises(ContractError, match="schema_version: expected integer 1"):
        load_facts_config(root)
    config_path.write_text(valid_config.replace("schema_version = 1", "schema_version = 2"), encoding="utf-8")
    with pytest.raises(ContractError, match="schema_version: expected integer 1"):
        load_facts_config(root)
    config_path.write_text("schema_version = 1\nunknown = true", encoding="utf-8")
    with pytest.raises(ContractError, match="unknown key.*unknown"):
        load_facts_config(root)
    config_path.write_text("schema_version = 1", encoding="utf-8")
    with pytest.raises(ContractError, match="missing key.*control_plane"):
        load_facts_config(root)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('geekbench_record = "/var/lib/mdt/host-facts/cpu-benchmark.json"', 'geekbench_record = "relative.json"', "absolute path"),
        ('geekbench_record = "/var/lib/mdt/host-facts/cpu-benchmark.json"', 'geekbench_record = "/var/../escape"', "without '..'"),
        ('host_setup_env = "/etc/mdt/host-setup.env"', 'host_setup_env = "relative.env"', "absolute path"),
        ('slice = "ctl.slice"', 'slice = "../ctl.slice"', "systemd .slice"),
        ('slice = "ctl.slice"', 'slice = "ctl.slice"\nprofile_reserve_bytes = 0', "positive integer bytes"),
        ('slice = "ctl.slice"', 'slice = "ctl.slice"\nprofile_reserve_bytes = true', "positive integer bytes"),
        ('overcommit = 1.5', 'overcommit = 0', "greater than zero"),
        ('oversubscribe_max = 1.25', 'oversubscribe_max = 11', "at most 10"),
        ('psi_refuse_some_avg10 = 25', 'psi_refuse_some_avg10 = 101', "from 0 through 100"),
        ('max_lane_containers = 8', 'max_lane_containers = true', "positive integer"),
        ('host_reserve_bytes = 671088640', 'host_reserve_bytes = true', "positive integer bytes"),
        ('planning_cap = 5', 'planning_cap = false', "finite number"),
        ('max_footprint = "large"', 'max_footprint = "huge"', "tiny, small, medium, or large"),
        ('max_planned_bytes = 7516192768', 'max_planned_bytes = 0', "positive integer bytes"),
        ('tiny = 67108864', 'tiny = true', "positive integer bytes"),
        ('worker = ["dev", "gate", "campaign"]', 'worker = ["unknown"]', "allowed priority classes"),
        ('checkout = 1073741824', 'checkout = 0', "positive integer bytes"),
        ('max_upload_bytes = 1073741824', 'max_upload_bytes = 0', "positive integer bytes"),
        ('queue_path = "/run/fleetd/queue"', 'queue_path = "relative"', "absolute path"),
        ('wait_s = 30', 'wait_s = 0', "greater than zero"),
    ],
)
def test_facts_config_rejects_invalid_values(tmp_path, old, new, message):
    root = _root(tmp_path)
    config_path = _config_path(root)
    config_path.write_text(config_path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    with pytest.raises(ContractError, match=message):
        load_facts_config(root)


def test_facts_config_optional_paths_and_policy_are_explicitly_absent(tmp_path):
    root = _root(tmp_path)
    config_path = _config_path(root)
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('guard_state = "/run/cgprofile/guard-state"\n', "")
    text = text.replace('ksm_optin_shim = "/usr/lib/fleetd/ksm-optin.so"\n', "")
    text = text.replace("max_lane_containers = 8\n", "")
    for line in (
        "host_reserve_bytes = 671088640\n", "planning_cap = 5\n", 'max_footprint = "large"\n',
        "max_planned_bytes = 7516192768\n", "max_upload_bytes = 1073741824\n",
    ):
        text = text.replace(line, "")
    for section in (
        "[policy.footprint_charges_bytes]\ntiny = 67108864\nsmall = 268435456\nmedium = 1073741824\nlarge = 4294967296\n\n",
        '[policy.priority_classes_by_principal]\nworker = ["dev", "gate", "campaign"]\n\n',
        "[policy.disk_floors]\ncheckout = 1073741824\ndocker = 2147483648\n\n",
        '[policy.admission]\nqueue_path = "/run/fleetd/queue"\nwait_s = 30\nsettle_s = 5\n',
    ):
        text = text.replace(section, "")
    config_path.write_text(text, encoding="utf-8")
    (root / "etc/mdt/host-setup.env").unlink()
    facts = collect_host_facts(root)
    assert _value(facts["guard"]["guard_state"]) is None
    assert facts["guard"]["guard_state"]["reason"]
    assert _value(facts["ksm"]["lane_optin_shim"]) is None
    assert facts["ksm"]["lane_optin_shim"]["reason"]
    assert _value(facts["policy"]["host_role"]) is None
    assert facts["policy"]["host_role"]["reason"]
    assert _value(facts["policy"]["max_lane_containers"]) is None
    assert facts["policy"]["max_lane_containers"]["reason"]
    for key in (
        "planning_cap", "max_footprint", "max_planned_bytes", "footprint_charges_bytes",
        "priority_classes_by_principal", "disk_floors", "max_upload_bytes", "admission",
    ):
        assert _value(facts["policy"][key]) is None
        assert facts["policy"][key]["reason"]
    assert _value(facts["memory"]["host_reserve_bytes"]) is None
    assert facts["memory"]["host_reserve_bytes"]["reason"]


def test_host_role_comes_from_host_setup_env_and_invalid_source_is_unknown(tmp_path):
    root = _root(tmp_path)
    role_path = root / "etc/mdt/host-setup.env"
    assert _value(collect_host_facts(root)["policy"]["host_role"]) == "dedicated-dev"

    role_path.write_text('HOST_ROLE="shared-prod" # production host\n', encoding="utf-8")
    facts = collect_host_facts(root)
    assert _value(facts["policy"]["host_role"]) == "shared-prod"
    assert "reason" not in facts["policy"]["host_role"]

    for content, reason in (
        ("HOST_ROLE=dedicated-dev\nHOST_ROLE=shared-prod\n", "appears more than once"),
        ("HOST_ROLE=dedicated\n", "missing or invalid"),
        ("HOST_ROLE=\n", "missing or invalid"),
        ("HOST_ROLE='unterminated\n", "quoting is invalid"),
        ("HOST_ROLE=dedicated dev\n", "unquoted whitespace"),
        ("", "is absent"),
    ):
        role_path.write_text(content, encoding="utf-8")
        facts = collect_host_facts(root)
        assert _value(facts["policy"]["host_role"]) is None
        assert reason in facts["policy"]["host_role"]["reason"]

    role_path.unlink()
    facts = collect_host_facts(root)
    assert _value(facts["policy"]["host_role"]) is None
    assert "unavailable" in facts["policy"]["host_role"]["reason"]

    role_path.write_bytes(bytes([255]))
    facts = collect_host_facts(root)
    assert _value(facts["policy"]["host_role"]) is None
    assert "not UTF-8" in facts["policy"]["host_role"]["reason"]


def test_private_facts_helpers_reject_invalid_contract_edges(tmp_path):
    from fleetd import facts as facts_module

    with pytest.raises(ContractError, match="expected table"):
        _object(None, "example", {"key"})
    with pytest.raises(ContractError, match="unknown key"):
        _object({"extra": 1}, "example", set())
    with pytest.raises(ContractError, match="expected finite number"):
        facts_module._positive_number(True, "number")
    with pytest.raises(ContractError, match="expected finite number"):
        facts_module._positive_number(float("inf"), "number")
    with pytest.raises(ContractError, match="expected finite number"):
        facts_module._positive_number(10**400, "number")
    with pytest.raises(ContractError, match="greater than zero"):
        facts_module._positive_number(0, "number")
    with pytest.raises(ContractError, match="at most"):
        facts_module._positive_number(2, "number", maximum=1)
    with pytest.raises(ContractError, match="expected non-empty absolute path"):
        _validate_config_path("", "path")
    with pytest.raises(ContractError, match="expected non-empty absolute path"):
        _validate_config_path(None, "path")
    with pytest.raises(ContractError, match="absolute path"):
        _validate_config_path("/", "path")
    with pytest.raises(ContractError, match="expected non-empty absolute path"):
        _validate_config_path("/bad\x00path", "path")
    with pytest.raises(ContractError, match="without '..'"):
        _validate_config_path("/a/../b", "path")
    with pytest.raises(ContractError, match="configured path"):
        _rooted(tmp_path, "relative")
    with pytest.raises(ContractError, match="configured path"):
        _rooted(tmp_path, "/a/../b")
    with pytest.raises(ContractError, match="systemd .slice"):
        facts_module._slice_name("../bad.slice", "slice")
    assert _slice_path("-.slice") == Path()

    cpuinfo = tmp_path / "cpuinfo"
    assert _cpu_model(tmp_path)[0] is None
    cpuinfo.write_text("", encoding="utf-8")
    assert "absent" in _cpu_model(tmp_path)[1]
    cpuinfo.write_text("model name :   \n", encoding="utf-8")
    assert "absent" in _cpu_model(tmp_path)[1]
    cpuinfo.write_text("Hardware :  Fixture   ARM  \n", encoding="utf-8")
    assert _cpu_model(tmp_path) == ("Fixture ARM", None)
    assert _cpu_list_count("4") == 1
    cpuinfo.write_bytes(bytes([255]))
    assert "UTF-8" in _cpu_model(tmp_path)[1]
    assert _cpu_list_count("") is None
    assert _cpu_list_count("3-1") is None
    assert _cpu_list_count("0-x") is None
    assert _cpu_list_count("0-1,1-2") is None
    assert _cpu_list_count("0-1,4-5") == 4
    assert _online_cpu_count(tmp_path)[0] is None
    assert _meminfo_values(tmp_path)["MemTotal"][0] is None
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("", encoding="utf-8")
    assert _meminfo_values(tmp_path)["MemTotal"][0] is None
    meminfo.write_text("unrelated line\nMemTotal\nMemTotal: 1 kB\nMemAvailable: 2 kB\nSwapTotal: 3 kB\nSwapFree: 4 kB\n", encoding="utf-8")
    assert _meminfo_values(tmp_path)["SwapFree"][0] == 4096
    integer_file = tmp_path / "integer"
    assert _read_integer(integer_file, "integer")[0] is None
    integer_file.write_text("", encoding="utf-8")
    assert "empty" in _read_integer(integer_file, "integer")[1]
    integer_file.write_text("zero", encoding="utf-8")
    assert "integer" in _read_integer(integer_file, "integer")[1]
    integer_file.write_text("0", encoding="utf-8")
    assert "below" in _read_integer(integer_file, "integer", minimum=1)[1]
    assert _psi_value(None, "some", "fixture")[0] is None
    assert _psi_value("some avg10=bad", "some", "fixture")[0] is None
    assert _psi_value("some avg10=101", "some", "fixture")[0] is None
    assert _psi_value("some avg60=0", "some", "fixture")[0] is None
    assert _psi_value("some avg60=0 avg10=0.5", "some", "fixture") == (0.5, None)


def test_facts_config_rejects_policy_types_and_nonfinite_or_overflow_values(tmp_path):
    root = _root(tmp_path)
    config_path = _config_path(root)
    original = config_path.read_text(encoding="utf-8")
    for old, new, message in (
        ("overcommit = 1.5", "overcommit = true", "expected finite number"),
        ("overcommit = 1.5", "overcommit = inf", "expected finite number"),
        ("oversubscribe_max = 1.25", "oversubscribe_max = " + "9" * 400, "expected finite number"),
        ("psi_refuse_some_avg10 = 25", "psi_refuse_some_avg10 = true", "from 0 through 100"),
        ("psi_refuse_some_avg10 = 25", 'psi_refuse_some_avg10 = "25"', "from 0 through 100"),
        ("psi_refuse_some_avg10 = 25", "psi_refuse_some_avg10 = []", "from 0 through 100"),
        ("psi_refuse_some_avg10 = 25", "psi_refuse_some_avg10 = inf", "from 0 through 100"),
        ("max_lane_containers = 8", "max_lane_containers = 0", "positive integer"),
    ):
        config_path.write_text(original.replace(old, new), encoding="utf-8")
        with pytest.raises(ContractError, match=message):
            load_facts_config(root)
    config_path.write_text(
        'schema_version = 1\npaths = "invalid"\n'
        '[control_plane]\nslice = "ctl.slice"\n'
        '[policy]\novercommit = 1\noversubscribe_max = 1\npsi_refuse_some_avg10 = 25\n',
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="paths: expected table"):
        load_facts_config(root)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing", "host Geekbench record unavailable"),
        ("invalid-json", "invalid JSON"),
        ("empty", "invalid JSON"),
        ("wrong-schema", "unsupported schema or tool"),
        ("wrong-shape", "unsupported schema or tool"),
        ("wrong-tool", "unsupported schema or tool"),
        ("wrong-major", "unsupported major"),
        ("stale-model", "does not match"),
        ("stale-count", "does not match"),
        ("incomplete", "incomplete"),
        ("incomplete-version", "incomplete"),
        ("incomplete-multicore", "incomplete"),
        ("incomplete-baseline", "incomplete"),
    ],
)
def test_geekbench_record_is_read_only_private_and_reasoned(tmp_path, mutation, reason):
    root = _root(tmp_path)
    path = root / "var/lib/mdt/host-facts/cpu-benchmark.json"
    if mutation == "missing":
        path.unlink()
    elif mutation == "invalid-json":
        path.write_text("{", encoding="utf-8")
    elif mutation == "empty":
        path.write_text("", encoding="utf-8")
    else:
        record = _json(path)
        if mutation == "wrong-schema":
            record["schema"] = "machine/1"
        elif mutation == "wrong-shape":
            record = []
        elif mutation == "wrong-tool":
            record["tool"] = "other"
        elif mutation == "wrong-major":
            record["tool_major"] = 5
        elif mutation == "stale-model":
            record["cpu_model"] = "Other CPU"
        elif mutation == "stale-count":
            record["cpus_online"] = 3
        elif mutation == "incomplete":
            record["single_core"] = 0
        elif mutation == "incomplete-version":
            record["tool_version"] = " "
        elif mutation == "incomplete-multicore":
            record["multi_core"] = 0
        elif mutation == "incomplete-baseline":
            record["baseline_score"] = 2300
        _write_json(path, record)
    facts = collect_host_facts(root)
    assert _value(facts["speed"]) is None
    assert reason in facts["speed"]["reason"]


def test_facts_collector_keeps_unknowns_reasoned_and_does_not_guess(tmp_path):
    root = _root(tmp_path)
    (root / "proc/sys/kernel/random/boot_id").unlink()
    (root / "proc/cpuinfo").write_text("processor : 0\n", encoding="utf-8")
    (root / "sys/devices/system/cpu/online").write_text("0-x", encoding="utf-8")
    (root / "proc/loadavg").write_text("broken", encoding="utf-8")
    (root / "proc/pressure/cpu").unlink()
    (root / "proc/pressure/io").write_text("", encoding="utf-8")
    (root / "proc/pressure/memory").write_text("some avg10=NaN avg60=0 avg300=0 total=0\n", encoding="utf-8")
    (root / "proc/meminfo").write_text("MemTotal: nope\nMemAvailable: 0 kB\nSwapTotal: 0 kB\n", encoding="utf-8")
    (root / "sys/fs/cgroup/cgroup.controllers").unlink()
    (root / "sys/fs/cgroup/ctl.slice").rename(root / "sys/fs/cgroup/renamed.slice")
    (root / "sys/kernel/mm/ksm/run").write_text("bad", encoding="utf-8")
    facts = collect_host_facts(root)
    assert _value(facts["boot_id"]) is None and facts["boot_id"]["reason"]
    assert _value(facts["speed"]) is None and facts["speed"]["reason"]
    assert _value(facts["cpu"]["online"]) is None and facts["cpu"]["online"]["reason"]
    assert _value(facts["cpu"]["planning_cap"]) is None and facts["cpu"]["planning_cap"]["reason"]
    for name in ("load1", "load5", "load15"):
        assert _value(facts[name]) is None and facts[name]["reason"]
    assert _value(facts["memory"]["total_bytes"]) is None and facts["memory"]["total_bytes"]["reason"]
    assert _value(facts["memory"]["available_bytes"]) == 0
    assert _value(facts["memory"]["swap_free_bytes"]) is None and facts["memory"]["swap_free_bytes"]["reason"]
    assert _value(facts["psi"]["cpu"]["some_avg10_pct"]) is None
    assert _value(facts["psi"]["memory"]["some_avg10_pct"]) is None
    assert _value(facts["psi"]["io"]["some_avg10_pct"]) is None
    assert _value(facts["cgroup_v2"]["mounted"]) is True
    assert _value(facts["cgroup_v2"]["controllers"]) is None
    assert _value(facts["control_plane"]["present"]) is False
    assert _value(facts["control_plane"]["memory_min_bytes"]) is None
    assert _value(facts["kernel_interfaces"]["memory_reclaim"]) is None
    assert _value(facts["ksm"]["run"]) is None and facts["ksm"]["run"]["reason"]


@pytest.mark.parametrize(
    ("loadavg", "expected"),
    [
        (None, (None, None, None)),
        ("", (None, None, None)),
        ("-1 0 0", (None, 0.0, 0.0)),
        ("nan 0 0", (None, 0.0, 0.0)),
        ("0.1", (0.1, None, None)),
    ],
)
def test_load_averages_are_null_with_reasons_when_source_is_unreadable_or_invalid(tmp_path, loadavg, expected):
    root = _root(tmp_path)
    source = root / "proc/loadavg"
    if loadavg is None:
        source.unlink()
    else:
        source.write_text(loadavg, encoding="utf-8")
    facts = collect_host_facts(root)
    for name, value in zip(("load1", "load5", "load15"), expected, strict=True):
        assert _value(facts[name]) == value
        assert ("reason" in facts[name]) is (value is None)


@pytest.mark.parametrize(
    ("filename", "fact_name"),
    [
        ("memory.current", "memory_current_bytes"),
        ("memory.current", "memory_warm_bytes"),
        ("memory.stat", "memory_warm_bytes"),
        ("memory.high", "memory_high_bytes"),
        ("memory.max", "memory_max_bytes"),
        ("cpu.max", "cpu_max"),
    ],
)
def test_gate_slice_facts_keep_missing_source_reasons(tmp_path, filename, fact_name):
    root = _root(tmp_path)
    (root / "sys/fs/cgroup/dev.slice/dev-gates.slice" / filename).unlink()
    facts = collect_host_facts(root)
    assert _value(facts["gate_slice"][fact_name]) is None
    assert facts["gate_slice"][fact_name]["reason"]
    if filename == "cpu.max":
        assert _value(facts["cpu"]["planning_cap"]) is None
        assert facts["cpu"]["planning_cap"]["reason"]


def test_gate_slice_warm_memory_is_current_minus_inactive_file_and_never_negative(tmp_path):
    root = _root(tmp_path)
    facts = collect_host_facts(root)
    assert _value(facts["gate_slice"]["memory_warm_bytes"]) == (
        _value(facts["gate_slice"]["memory_current_bytes"]) - 52428800
    )
    (root / "sys/fs/cgroup/dev.slice/dev-gates.slice/memory.current").write_text("1\n", encoding="utf-8")
    facts = collect_host_facts(root)
    assert _value(facts["gate_slice"]["memory_warm_bytes"]) is None
    assert "exceeds" in facts["gate_slice"]["memory_warm_bytes"]["reason"]


def test_missing_gate_slice_config_reports_unknown_instead_of_using_a_slice_default(tmp_path):
    root = _root(tmp_path)
    config_path = _config_path(root)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('[gate_slice]\nslice = "dev-gates.slice"\n\n', ""),
        encoding="utf-8",
    )
    facts = collect_host_facts(root)
    for value in facts["gate_slice"].values():
        assert _value(value) is None
        assert "not-configured" in value["reason"]
    assert _value(facts["cpu"]["planning_cap"]) is None
    assert "not-configured" in facts["cpu"]["planning_cap"]["reason"]


def test_disk_facts_use_statvfs_mountinfo_and_reason_missing_sources(tmp_path):
    root = _root(tmp_path)
    statvfs = lambda _path: SimpleNamespace(f_frsize=1024, f_bsize=1024, f_blocks=1000, f_bavail=250)
    facts = collect_host_facts(root, disk_statvfs_reader=statvfs)
    disks = _value(facts["disks"])
    assert [item["name"]["v"] for item in disks] == ["checkout", "docker"]
    assert all(item["fs"]["v"] == "ext4" for item in disks)
    assert all(item["total_bytes"]["v"] == 1024000 for item in disks)
    assert all(item["free_bytes"]["v"] == 256000 for item in disks)

    (root / "proc/self/mountinfo").unlink()
    facts = collect_host_facts(root, disk_statvfs_reader=statvfs)
    assert _value(facts["disks"]) is None
    assert "mountinfo" in facts["disks"]["reason"]
    assert _value(facts["cgroup_v2"]["mounted"]) is None
    assert "mountinfo" in facts["cgroup_v2"]["mounted"]["reason"]


def test_disk_facts_report_unknown_when_roots_are_unconfigured_or_unreadable(tmp_path):
    root = _root(tmp_path)
    config_path = _config_path(root)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'checkout_root = "/var/lib/fleetd"\n', "",
        ).replace('docker_root = "/var/lib/docker"\n', ""),
        encoding="utf-8",
    )
    facts = collect_host_facts(root)
    assert _value(facts["disks"]) is None
    assert "not-configured" in facts["disks"]["reason"]

    other_parent = tmp_path / "other"
    other_parent.mkdir()
    root = _root(other_parent)
    def unreadable(_path):
        raise OSError(13, "Permission denied")
    facts = collect_host_facts(root, disk_statvfs_reader=unreadable)
    assert _value(facts["disks"]) is None
    assert "unavailable" in facts["disks"]["reason"]


def test_meminfo_missing_duplicate_and_malformed_fields_keep_reasons(tmp_path):
    root = _root(tmp_path)
    (root / "proc/meminfo").write_text(
        "MemTotal: 1 kB\nMemTotal: 2 kB\nMemAvailable: bad\nSwapTotal: 3 kB\nSwapFree: 4 kB\n",
        encoding="utf-8",
    )
    facts = collect_host_facts(root)
    assert _value(facts["memory"]["total_bytes"]) is None
    assert "duplicated" in facts["memory"]["total_bytes"]["reason"]
    assert _value(facts["memory"]["available_bytes"]) is None
    assert "malformed" in facts["memory"]["available_bytes"]["reason"]


def test_cgroup_interfaces_and_ksm_missing_values_are_not_reported_as_present(tmp_path):
    root = _root(tmp_path)
    control = root / "sys/fs/cgroup/ctl.slice"
    (control / "cgroup.freeze").unlink()
    (control / "memory.reclaim").unlink()
    (root / "sys/kernel/mm/ksm/advisor_mode").unlink()
    (root / "sys/kernel/mm/ksm/advisor_max_cpu").unlink()
    (root / "usr/lib/fleetd/ksm-optin.so").unlink()
    facts = collect_host_facts(root)
    assert _value(facts["kernel_interfaces"]["cgroup_freeze"]) is False
    assert _value(facts["kernel_interfaces"]["memory_reclaim"]) is False
    assert _value(facts["ksm"]["advisor_mode"]) is None
    assert facts["ksm"]["advisor_mode"]["reason"]
    assert _value(facts["ksm"]["advisor_max_cpu"]) is None
    assert _value(facts["ksm"]["lane_optin_shim"]) is False


def test_missing_control_plane_slice_does_not_claim_kernel_interfaces(tmp_path):
    root = _root(tmp_path)
    control = root / "sys/fs/cgroup/ctl.slice"
    control.rename(root / "sys/fs/cgroup/moved.slice")
    facts = collect_host_facts(root)
    assert _value(facts["cgroup_v2"]["mounted"]) is True
    assert _value(facts["control_plane"]["present"]) is False
    assert _value(facts["kernel_interfaces"]["cgroup_freeze"]) is None
    assert _value(facts["kernel_interfaces"]["memory_reclaim"]) is None
    assert facts["kernel_interfaces"]["cgroup_freeze"]["reason"]


def test_control_plane_slice_path_supports_nested_systemd_slices(tmp_path):
    root = _root(tmp_path)
    config_path = _config_path(root)
    text = config_path.read_text(encoding="utf-8").replace('slice = "ctl.slice"', 'slice = "dev-background.slice"')
    config_path.write_text(text, encoding="utf-8")
    parent = root / "sys/fs/cgroup/dev.slice/dev-background.slice"
    parent.mkdir(parents=True)
    (parent / "cpu.weight").write_text("200", encoding="utf-8")
    (parent / "cgroup.freeze").write_text("0", encoding="utf-8")
    facts = collect_host_facts(root)
    assert _value(facts["control_plane"]["present"]) is True
    assert _value(facts["control_plane"]["cpu_weight"]) == 200
    assert _value(facts["control_plane"]["memory_min_bytes"]) is None


def test_service_memory_min_facts_use_the_bounded_unit_list_and_injected_readers(tmp_path):
    root = _root(tmp_path)
    raw_values = {unit: str(index * 1024**2) for index, unit in enumerate(CONTROL_PLANE_SERVICE_UNITS, 1)}
    queried = []
    queried_states = []

    def reader(unit):
        queried.append(unit)
        return _parse_systemd_memory_min(raw_values[unit], unit)

    def load_state_reader(unit):
        queried_states.append(unit)
        return "masked" if unit == CONTROL_PLANE_SERVICE_UNITS[-1] else "loaded", None

    facts = collect_host_facts(
        root, service_memory_min_reader=reader, service_load_state_reader=load_state_reader,
    )
    assert queried == list(CONTROL_PLANE_SERVICE_UNITS)
    assert queried_states == list(CONTROL_PLANE_SERVICE_UNITS)
    assert facts["control_plane"]["service_memory_min"] == {
        unit: {
            "memory_min_bytes": {"v": index * 1024**2},
            "load_state": {"v": "masked" if unit == CONTROL_PLANE_SERVICE_UNITS[-1] else "loaded"},
        }
        for index, unit in enumerate(CONTROL_PLANE_SERVICE_UNITS, 1)
    }


def test_service_memory_min_fact_reasons_survive_a_reader_with_no_reason(tmp_path):
    root = _root(tmp_path)
    facts = collect_host_facts(
        root,
        service_memory_min_reader=lambda _unit: (None, None),
        service_load_state_reader=lambda _unit: (None, None),
    )
    assert facts["control_plane"]["service_memory_min"] == {
        unit: {
            "memory_min_bytes": {"v": None, "reason": f"{unit} MemoryMin is not reported"},
            "load_state": {"v": None, "reason": f"{unit} LoadState is not reported"},
        }
        for unit in CONTROL_PLANE_SERVICE_UNITS
    }


def test_service_load_state_is_absent_when_only_the_memory_min_reader_is_injected(tmp_path):
    """A MemoryMin-only reader cannot prove an optional unit absent, so it says so."""
    root = _root(tmp_path)
    facts = collect_host_facts(root, service_memory_min_reader=lambda _unit: (1024, None))
    for unit in CONTROL_PLANE_SERVICE_UNITS:
        row = facts["control_plane"]["service_memory_min"][unit]
        assert _value(row["memory_min_bytes"]) == 1024
        assert _value(row["load_state"]) is None
        assert "LoadState was not queried" in row["load_state"]["reason"]


def test_default_facts_root_uses_systemd_reader_without_touching_a_host(monkeypatch):
    from fleetd import facts as facts_module

    monkeypatch.setattr(facts_module, "DEFAULT_ROOT", FIXTURE_ROOT)
    queried = []

    def fake_reader(unit):
        queried.append(unit)
        return CONTROL_PLANE_SERVICE_UNITS.index(unit) + 1, None

    monkeypatch.setattr(facts_module, "_read_systemd_memory_min", fake_reader)

    def fake_load_state(unit):
        return ("not-found" if unit == CONTROL_PLANE_SERVICE_UNITS[-1] else "loaded"), None

    monkeypatch.setattr(facts_module, "_read_systemd_load_state", fake_load_state)
    facts = collect_host_facts(FIXTURE_ROOT)
    assert queried == list(CONTROL_PLANE_SERVICE_UNITS)
    assert _value(facts["control_plane"]["service_memory_min"][CONTROL_PLANE_SERVICE_UNITS[-1]]["load_state"]) == "not-found"
    assert [
        _value(facts["control_plane"]["service_memory_min"][unit]["memory_min_bytes"])
        for unit in CONTROL_PLANE_SERVICE_UNITS
    ] == list(range(1, len(CONTROL_PLANE_SERVICE_UNITS) + 1))


@pytest.mark.parametrize(
    ("raw", "expected", "reason"),
    [
        ("12345", 12345, None),
        ("MemoryMin=67890", 67890, None),
        ("0", None, "zero or unset"),
        ("infinity", None, "infinity"),
        ("", None, "unset"),
        ("MemoryLow=10", None, "not a MemoryMin property"),
        ("12\n13", None, "not one value"),
        ("not-bytes", None, "non-negative integer"),
    ],
)
def test_systemd_memory_min_parser_marks_zero_infinity_and_unset_as_not_reported(raw, expected, reason):
    value, error = _parse_systemd_memory_min(raw, "ssh.service")
    assert value == expected
    if reason is None:
        assert error is None
    else:
        assert reason in error


@pytest.mark.parametrize(
    ("raw", "expected", "reason"),
    [
        ("", None, "LoadState is unset"),
        (" =Loaded", None, "not a LoadState property"),
        ("LoadState=", None, "LoadState is unset"),
        ("loadstate masked", None, "not one value"),
        ("loaded\rnot-found", None, "not one value"),
        ("LoadState=masked", "masked", None),
        ("not-found", "not-found", None),
        ("LoadState=not-found", "not-found", None),
    ],
)
def test_systemd_load_state_parser_rejects_foreign_properties_and_empty_values(raw, expected, reason):
    value, error = _parse_systemd_load_state(raw, "mdt-psi-guard.service")
    assert value == expected
    if reason is None:
        assert error is None
    else:
        assert reason in error


def test_systemd_memory_min_reader_uses_systemctl_show_and_reports_failures(monkeypatch):
    from fleetd import facts as facts_module

    calls = []

    def successful(command, **kwargs):
        calls.append((command, kwargs))
        return type("Completed", (), {"returncode": 0, "stdout": "1048576\n"})()

    monkeypatch.setattr(facts_module.subprocess, "run", successful)
    assert _read_systemd_memory_min("ssh.service") == (1048576, None)
    assert calls[0][0] == ["systemctl", "show", "-p", "MemoryMin", "--value", "--no-pager", "ssh.service"]
    assert calls[0][1]["timeout"] == 5

    monkeypatch.setattr(
        facts_module.subprocess, "run",
        lambda *_args, **_kwargs: type("Completed", (), {"returncode": 1, "stdout": ""})(),
    )
    value, reason = _read_systemd_memory_min("ssh.service")
    assert value is None and "exited 1" in reason

    def timeout(*_args, **_kwargs):
        raise facts_module.subprocess.TimeoutExpired("systemctl", 5)

    monkeypatch.setattr(facts_module.subprocess, "run", timeout)
    value, reason = _read_systemd_memory_min("ssh.service")
    assert value is None and "timed out" in reason

    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(facts_module.subprocess, "run", unavailable)
    value, reason = _read_systemd_memory_min("ssh.service")
    assert value is None and "unavailable" in reason


def test_systemd_load_state_reader_uses_systemctl_show_and_reports_failures(monkeypatch):
    from fleetd import facts as facts_module

    calls = []

    def successful(command, **kwargs):
        calls.append((command, kwargs))
        return type("Completed", (), {"returncode": 0, "stdout": "loaded\n"})()

    monkeypatch.setattr(facts_module.subprocess, "run", successful)
    assert _read_systemd_load_state("ssh.service") == ("loaded", None)
    assert calls[0][0] == ["systemctl", "show", "-p", "LoadState", "--value", "--no-pager", "ssh.service"]
    assert calls[0][1]["timeout"] == 5

    monkeypatch.setattr(
        facts_module.subprocess, "run",
        lambda *_args, **_kwargs: type("Completed", (), {"returncode": 3, "stdout": ""})(),
    )
    value, reason = _read_systemd_load_state("ssh.service")
    assert value is None and "exited 3" in reason

    def timeout(*_args, **_kwargs):
        raise facts_module.subprocess.TimeoutExpired("systemctl", 5)

    monkeypatch.setattr(facts_module.subprocess, "run", timeout)
    value, reason = _read_systemd_load_state("ssh.service")
    assert value is None and "timed out" in reason

    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(facts_module.subprocess, "run", unavailable)
    value, reason = _read_systemd_load_state("ssh.service")
    assert value is None and "unavailable" in reason


def test_guard_dropins_change_the_reported_config_digest(tmp_path):
    root = _root(tmp_path)
    before = _value(collect_host_facts(root)["guard"]["config_digest"])
    dropin = root / "etc/cgprofile/guard.toml.d/20-disabled.toml"
    dropin.write_text("enabled = false\n", encoding="utf-8")
    after = collect_host_facts(root)["guard"]
    assert _value(after["guard_present"]) is True
    assert _value(after["config_digest"]) != before


def test_guard_without_dropin_directory_uses_main_config_only(tmp_path):
    root = _root(tmp_path)
    shutil.rmtree(root / "etc/cgprofile/guard.toml.d")
    guard = collect_host_facts(root)["guard"]
    assert _value(guard["guard_present"]) is True
    assert _value(guard["config_digest"]) == "sha256:37f357c9df40c3755c983fd24dcf4f6401ceaac640b0be16cf0b93c74d3bc128"


def test_guard_invalid_dropin_toml_is_reasoned_and_rule_fields_are_ignored(tmp_path):
    root = _root(tmp_path)
    bad = root / "etc/cgprofile/guard.toml.d/99-bad.toml"
    bad.write_text("enabled = [", encoding="utf-8")
    parse_error = collect_host_facts(root)["guard"]
    assert _value(parse_error["guard_present"]) is True
    bad.write_text("unknown = true\n", encoding="utf-8")
    ignored_rule = collect_host_facts(root)["guard"]
    assert _value(ignored_rule["config_digest"]).startswith("sha256:")


@pytest.mark.parametrize(("literal", "kind"), [('"true"', "str"), ("1", "int")])
def test_guard_non_boolean_enabled_reports_the_type_found(tmp_path, literal, kind):
    root = _root(tmp_path)
    (root / "etc/cgprofile/guard.toml").write_text(f"enabled = {literal}\n", encoding="utf-8")
    guard = collect_host_facts(root)["guard"]
    assert _value(guard["guard_present"]) is True
    assert _value(guard["config_digest"]).startswith("sha256:")
    assert _value(guard["guard_state"]) == "report"


def test_guard_missing_top_level_enabled_is_reasoned(tmp_path):
    root = _root(tmp_path)
    (root / "etc/cgprofile/guard.toml").write_text('mode = "report"\n', encoding="utf-8")
    guard = collect_host_facts(root)["guard"]
    assert _value(guard["guard_present"]) is True
    assert _value(guard["config_digest"]).startswith("sha256:")


def test_guard_dropin_read_error_refuses_a_partial_digest(tmp_path, monkeypatch):
    from fleetd import facts as facts_module

    root = _root(tmp_path)
    dropin = root / "etc/cgprofile/guard.toml.d/01-unreadable.toml"
    dropin.write_text("enabled = false\n", encoding="utf-8")
    real_read_bytes = facts_module._read_bytes

    def unreadable(path, description):
        if path == dropin:
            return None, "simulated unreadable drop-in"
        return real_read_bytes(path, description)

    monkeypatch.setattr(facts_module, "_read_bytes", unreadable)
    guard = collect_host_facts(root)["guard"]
    assert _value(guard["config_digest"]) is None
    assert "simulated unreadable" in guard["config_digest"]["reason"]


def test_guard_invalid_config_or_missing_main_retains_independent_state(tmp_path):
    root = _root(tmp_path)
    main = root / "etc/cgprofile/guard.toml"
    main.write_text("enabled = [", encoding="utf-8")
    invalid = collect_host_facts(root)["guard"]
    assert _value(invalid["guard_present"]) is True
    assert _value(invalid["config_digest"]).startswith("sha256:")
    main.unlink()
    missing = collect_host_facts(root)["guard"]
    assert _value(missing["guard_present"]) is False
    assert _value(missing["config_digest"]) is None and missing["config_digest"]["reason"]
    assert _value(missing["guard_state"]) == "report"
    assert missing["frozen_units"]["reason"] == "thrashguard-absent"


def test_guard_dropin_directory_and_state_source_fail_closed(tmp_path):
    root = _root(tmp_path)
    dropin = root / "etc/cgprofile/guard.toml.d"
    shutil.rmtree(dropin)
    dropin.write_text("not a directory", encoding="utf-8")
    state = root / "run/cgprofile/guard-state"
    state.unlink()
    facts = collect_host_facts(root)
    assert _value(facts["guard"]["guard_present"]) is True
    assert _value(facts["guard"]["config_digest"]) is None
    assert _value(facts["guard"]["guard_state"]) is None
    assert facts["guard"]["guard_state"]["reason"]


def test_missing_guard_main_and_unconfigured_state_are_reasoned(tmp_path):
    root = _root(tmp_path)
    config = _config_path(root)
    config.write_text(config.read_text(encoding="utf-8").replace('guard_state = "/run/cgprofile/guard-state"\n', ""), encoding="utf-8")
    (root / "etc/cgprofile/guard.toml").unlink()
    guard = collect_host_facts(root)["guard"]
    assert _value(guard["guard_present"]) is False
    assert _value(guard["config_digest"]) is None and guard["config_digest"]["reason"]
    assert _value(guard["guard_state"]) is None
    assert guard["guard_state"]["reason"] == "guard runtime state source is not configured"
    assert guard["frozen_units"]["reason"] == "thrashguard-absent"


def test_policy_map_helpers_validate_tables_and_preserve_configured_values():
    from fleetd import facts as facts_module

    assert facts_module._positive_bytes_map({"tiny": 64}, "charges", allowed={"tiny"}) == {"tiny": 64}
    with pytest.raises(ContractError, match="charges: expected table"):
        facts_module._positive_bytes_map([], "charges")
    with pytest.raises(ContractError, match="expected non-empty keys"):
        facts_module._positive_bytes_map({"": 64}, "charges")
    with pytest.raises(ContractError, match="unknown key 'large'"):
        facts_module._positive_bytes_map({"large": 64}, "charges", allowed={"tiny"})
    with pytest.raises(ContractError, match="expected positive integer bytes"):
        facts_module._positive_bytes_map({"tiny": True}, "charges")

    assert facts_module._priority_map({"worker": ["dev", "gate"]}, "priorities") == {
        "worker": ["dev", "gate"],
    }
    with pytest.raises(ContractError, match="priorities: expected table"):
        facts_module._priority_map([], "priorities")
    with pytest.raises(ContractError, match="expected non-empty principal names"):
        facts_module._priority_map({" ": []}, "priorities")
    with pytest.raises(ContractError, match="expected an array of allowed priority classes"):
        facts_module._priority_map({"worker": "dev"}, "priorities")
    with pytest.raises(ContractError, match="expected an array of allowed priority classes"):
        facts_module._priority_map({"worker": ["unknown"]}, "priorities")
    with pytest.raises(ContractError, match="duplicate priority class"):
        facts_module._priority_map({"worker": ["dev", "dev"]}, "priorities")

    admission = facts_module._admission_config(
        {"queue_path": "/run/fleetd/queue", "wait_s": 30, "settle_s": 5}, "admission",
    )
    assert admission == {"queue_path": "/run/fleetd/queue", "wait_s": 30.0, "settle_s": 5.0}
    with pytest.raises(ContractError, match="admission: expected table"):
        facts_module._admission_config(None, "admission")
    with pytest.raises(ContractError, match="admission.wait_s: must be greater than zero"):
        facts_module._admission_config(
            {"queue_path": "/run/fleetd/queue", "wait_s": 0, "settle_s": 5}, "admission",
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.2.3", "1.2.3"),
        ("1.2.3.dev4", "1.2.3-dev.4"),
        ("1.2.3.post5", "1.2.3-post.5"),
        ("1.2.3.a6", "1.2.3-alpha.6"),
        ("1.2.3.b7", "1.2.3-beta.7"),
        ("1.2.3.rc8+host.1", "1.2.3-rc.8+host.1"),
    ],
)
def test_agent_version_metadata_is_converted_to_semver(monkeypatch, raw, expected):
    from fleetd import facts as facts_module

    monkeypatch.setattr(facts_module.importlib.metadata, "version", lambda _name: raw)
    assert facts_module._agent_version() == expected


def test_agent_version_refuses_missing_or_unrepresentable_distribution_metadata(monkeypatch):
    from fleetd import facts as facts_module

    def missing(_name):
        raise facts_module.importlib.metadata.PackageNotFoundError("fleetd")

    monkeypatch.setattr(facts_module.importlib.metadata, "version", missing)
    with pytest.raises(ContractError, match="agent version metadata is unavailable"):
        facts_module._agent_version()
    monkeypatch.setattr(facts_module.importlib.metadata, "version", lambda _name: "1.2")
    with pytest.raises(ContractError, match="cannot be represented as SemVer"):
        facts_module._agent_version()


def test_psi_window_parser_reports_missing_malformed_and_out_of_range_values():
    from fleetd import facts as facts_module

    assert facts_module._psi_window_value(None, "some", 10, "fixture") == (
        None, "fixture has no some line",
    )
    assert facts_module._psi_window_value("some avg10", "some", 10, "fixture") == (
        None, "fixture some avg10 is absent",
    )
    assert facts_module._psi_window_value("some avg10=bad", "some", 10, "fixture") == (
        None, "fixture some avg10 is malformed",
    )
    assert facts_module._psi_window_value("some avg10=101", "some", 10, "fixture") == (
        None, "fixture some avg10 is outside 0 through 100",
    )
    assert facts_module._psi_window_value("some", "some", 10, "fixture") == (
        None, "fixture some avg10 is absent",
    )
    assert facts_module._psi_window_value("some avg60=0 avg10=0.5", "some", 10, "fixture") == (
        0.5, None,
    )


def test_cgroup_source_parsers_keep_missing_and_malformed_values_unknown(tmp_path):
    from fleetd import facts as facts_module

    limit = tmp_path / "memory.max"
    assert facts_module._read_cgroup_limit(limit, "memory.max")[1]
    limit.write_text("max\n", encoding="utf-8")
    assert facts_module._read_cgroup_limit(limit, "memory.max") == ("max", None)
    limit.write_text("bad\n", encoding="utf-8")
    assert facts_module._read_cgroup_limit(limit, "memory.max")[1] == (
        "memory.max is not a non-negative integer or max"
    )

    cpu_max = tmp_path / "cpu.max"
    assert facts_module._read_cpu_max(cpu_max, "cpu.max")[1]
    cpu_max.write_text("max 100000\n", encoding="utf-8")
    assert facts_module._read_cpu_max(cpu_max, "cpu.max") == (
        {"quota_us": "max", "period_us": 100000}, None,
    )
    cpu_max.write_text("100000\n", encoding="utf-8")
    assert facts_module._read_cpu_max(cpu_max, "cpu.max")[1] == "cpu.max must contain quota and period"
    cpu_max.write_text("0 100000\n", encoding="utf-8")
    assert facts_module._read_cpu_max(cpu_max, "cpu.max")[1] == "cpu.max quota is malformed"
    cpu_max.write_text("100000 0\n", encoding="utf-8")
    assert facts_module._read_cpu_max(cpu_max, "cpu.max")[1] == "cpu.max period is malformed"

    memory_stat = tmp_path / "memory.stat"
    assert facts_module._read_memory_stat(memory_stat, "inactive_file")[1]
    memory_stat.write_text("inactive_file nope\n", encoding="utf-8")
    assert facts_module._read_memory_stat(memory_stat, "inactive_file")[1] == (
        "gate-slice memory.stat inactive_file is malformed"
    )
    memory_stat.write_text("inactive_file 4\ninactive_file 5\n", encoding="utf-8")
    assert facts_module._read_memory_stat(memory_stat, "inactive_file")[1] == (
        "gate-slice memory.stat inactive_file is absent or duplicated"
    )
    memory_stat.write_text("inactive_file 4\n", encoding="utf-8")
    assert facts_module._read_memory_stat(memory_stat, "inactive_file") == (4, None)


def test_gate_slice_planning_cap_reports_unavailable_cpu_inputs_and_handles_unlimited_quota(tmp_path):
    from fleetd import facts as facts_module

    root = _root(tmp_path)
    config = load_facts_config(root)
    slice_path = root / "sys/fs/cgroup" / _slice_path(config.gate_slice)
    (slice_path / "cpu.max").write_text("max 100000\n", encoding="utf-8")
    gate, cap = facts_module._gate_slice_facts(root, config, 4)
    assert _value(gate["cpu_max"]) == {"quota_us": "max", "period_us": 100000}
    assert _value(cap) == 5.0
    gate, cap = facts_module._gate_slice_facts(root, config, None)
    assert _value(gate["cpu_max"]) == {"quota_us": "max", "period_us": 100000}
    assert _value(cap) is None
    assert cap["reason"] == "online CPU count is unavailable"


def test_mountinfo_decoding_and_disk_collection_failures_keep_reasons(tmp_path):
    from fleetd import facts as facts_module

    root = _root(tmp_path)
    mountinfo = root / "proc/self/mountinfo"
    mountinfo.write_text("not a mount row\n", encoding="utf-8")
    mounts, reason = facts_module._mountinfo_fields(root)
    assert mounts == []
    assert reason == "/proc/self/mountinfo has no parseable mount rows"
    disk = facts_module._collect_disks(root, load_facts_config(root))
    assert _value(disk) is None
    assert disk["reason"] == "/proc/self/mountinfo has no parseable mount rows"
    mountinfo.write_text(
        "1 0 8:1 / /mnt\\040data rw - ext4 /dev/test rw\n"
        "broken row\n",
        encoding="utf-8",
    )
    mounts, reason = facts_module._mountinfo_fields(root)
    assert reason is None
    assert mounts == [("/mnt data", "ext4")]
    assert facts_module._filesystem_for_path("/mnt data/child", mounts) == "ext4"
    assert facts_module._filesystem_for_path("/outside", mounts) is None
    assert facts_module._filesystem_for_path("/outside", [("/", "rootfs")]) == "rootfs"

    disk = facts_module._collect_disks(root, load_facts_config(root))
    assert _value(disk) is None
    assert disk["reason"] == "filesystem type for /var/lib/fleetd is absent from /proc/self/mountinfo"


def test_disk_collection_uses_block_size_fallback_and_preserves_unknown_floor_reason(tmp_path):
    root = _root(tmp_path)
    config_path = _config_path(root)
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace(
        "[policy.disk_floors]\ncheckout = 1073741824\ndocker = 2147483648\n\n", "",
    )
    config_path.write_text(config_text, encoding="utf-8")
    facts = collect_host_facts(
        root,
        disk_statvfs_reader=lambda _path: SimpleNamespace(f_frsize=0, f_bsize=512, f_blocks=10, f_bavail=4),
    )
    assert _value(facts["disks"])[0]["total_bytes"] == {"v": 5120}
    assert _value(facts["disks"])[0]["free_bytes"] == {"v": 2048}
    assert _value(facts["disks"])[0]["floor_bytes"]["v"] is None
    assert facts["disks"]["v"][0]["floor_bytes"]["reason"] == "not-configured: policy.disk_floors.checkout"


def test_guard_state_outside_contract_vocabulary_is_unknown_with_its_reason(tmp_path):
    root = _root(tmp_path)
    state = root / "run/cgprofile/guard-state"
    state.write_text("armed\n", encoding="utf-8")
    guard = collect_host_facts(root)["guard"]
    assert _value(guard["guard_state"]) is None
    assert guard["guard_state"]["reason"] == (
        "guard runtime state 'armed' is outside off, report, enforce, or unknown"
    )
