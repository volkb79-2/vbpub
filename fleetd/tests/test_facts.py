import json
import shutil
from pathlib import Path

import pytest

from fleetd.contracts import ContractError, dump_json
from fleetd.cli import main
from fleetd.facts import collect_host_facts, load_facts_config


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


def test_facts_collector_reads_only_injected_root_and_emits_design_facts():
    facts = collect_host_facts(FIXTURE_ROOT)
    assert facts["schema"] == "host-facts/1"
    assert facts["boot_id"] == "00112233-4455-6677-8899-aabbccddeeff"
    assert facts["host_now_boottime_s"] == 1234.5
    assert facts["cpu"] == {"model": "Fixture CPU Model", "online": 4, "for_lanes": 4.0, "oversubscribe_max": 1.25}
    assert facts["speed"] == {
        "geekbench_major": 6, "geekbench_version": "6.4.0", "baseline_score": 2500,
        "single_core": 2100, "multi_core": 7800, "basis": "geekbench6-measured",
    }
    assert facts["memory"] == {
        "total_bytes": 8 * 1024**3, "available_bytes": 6 * 1024**3,
        "for_lanes_bytes": 8 * 1024**3, "swap_total_bytes": 4 * 1024**3,
        "swap_free_bytes": 3 * 1024**3, "swap_for_parking_bytes": 3 * 1024**3,
        "overcommit": 1.5,
    }
    assert facts["psi"]["memory"] == {"some_avg10_pct": 1.0, "full_avg10_pct": 0.2}
    assert facts["psi"]["cpu"]["full_avg10_pct"] is None
    assert "full_avg10_pct_reason" in facts["psi"]["cpu"]
    assert facts["cgroup_v2"] == {"mounted": True, "controllers": ["cpu", "cpuset", "io", "memory", "pids"]}
    assert facts["kernel_interfaces"]["cgroup_freeze"] is True
    assert facts["kernel_interfaces"]["memory_reclaim"] is True
    assert facts["kernel_interfaces"]["memory_reclaim_swappiness_arg"] is None
    assert facts["ksm"] == {
        "run": 1, "advisor_mode": "scan-time", "advisor_max_cpu": 8,
        "pages_sharing": 16, "lane_optin_shim": True,
    }
    assert facts["control_plane"] == {
        "slice": "ctl.slice", "present": True,
        "memory_min_bytes": 805306368, "cpu_weight": 10000,
    }
    assert facts["guard"]["enabled"] is True
    assert facts["guard"]["config_digest"].startswith("sha256:")
    assert facts["guard"]["state"] == "idle"
    assert facts["policy"] == {
        "host_role": "dedicated-dev", "psi_refuse_some_avg10": 25.0, "max_lane_containers": 8,
    }
    serialized = dump_json(facts)
    assert "private-field-must-not-export" not in serialized
    assert "result_url" not in serialized
    assert facts == collect_host_facts(FIXTURE_ROOT)


def test_facts_collect_cli_emits_valid_json_and_config_errors_are_user_facing(capsys, tmp_path):
    assert main(["facts", "collect", "--root", str(FIXTURE_ROOT)]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["schema"] == "host-facts/1"
    assert output.err == ""
    assert main(["facts", "collect", "--root", str(tmp_path / "missing")]) == 2
    assert "required config" in capsys.readouterr().err


def test_facts_config_is_required_closed_and_versioned(tmp_path):
    root = _root(tmp_path)
    config_path = _config_path(root)
    config_path.unlink()
    with pytest.raises(ContractError, match="required config"):
        load_facts_config(root)
    config_path.write_text("schema_version = [", encoding="utf-8")
    with pytest.raises(ContractError, match="invalid TOML"):
        load_facts_config(root)
    config_path.write_text("schema_version = 2", encoding="utf-8")
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
        ('slice = "ctl.slice"', 'slice = "../ctl.slice"', "systemd .slice"),
        ('overcommit = 1.5', 'overcommit = 0', "greater than zero"),
        ('oversubscribe_max = 1.25', 'oversubscribe_max = 11', "at most 10"),
        ('psi_refuse_some_avg10 = 25', 'psi_refuse_some_avg10 = 101', "from 0 through 100"),
        ('max_lane_containers = 8', 'max_lane_containers = true', "positive integer"),
        ('host_role = "dedicated-dev"', 'host_role = "  "', "host_role: expected non-empty string"),
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
    text = text.replace('host_role = "dedicated-dev"\n', "")
    text = text.replace("max_lane_containers = 8\n", "")
    config_path.write_text(text, encoding="utf-8")
    facts = collect_host_facts(root)
    assert facts["guard"]["state"] is None
    assert facts["guard"]["state_reason"]
    assert facts["ksm"]["lane_optin_shim"] is None
    assert facts["ksm"]["lane_optin_shim_reason"]
    assert facts["policy"]["host_role"] is None
    assert facts["policy"]["host_role_reason"]
    assert facts["policy"]["max_lane_containers"] is None
    assert facts["policy"]["max_lane_containers_reason"]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing", "host Geekbench record unavailable"),
        ("invalid-json", "invalid JSON"),
        ("wrong-schema", "unsupported schema or tool"),
        ("wrong-major", "unsupported major"),
        ("stale-model", "does not match"),
        ("incomplete", "incomplete"),
    ],
)
def test_geekbench_record_is_read_only_private_and_reasoned(tmp_path, mutation, reason):
    root = _root(tmp_path)
    path = root / "var/lib/mdt/host-facts/cpu-benchmark.json"
    if mutation == "missing":
        path.unlink()
    elif mutation == "invalid-json":
        path.write_text("{", encoding="utf-8")
    else:
        record = _json(path)
        if mutation == "wrong-schema":
            record["schema"] = "machine/1"
        elif mutation == "wrong-major":
            record["tool_major"] = 5
        elif mutation == "stale-model":
            record["cpu_model"] = "Other CPU"
        elif mutation == "incomplete":
            record["single_core"] = 0
        _write_json(path, record)
    facts = collect_host_facts(root)
    assert facts["speed"] is None
    assert reason in facts["speed_reason"]


def test_facts_collector_keeps_unknowns_reasoned_and_does_not_guess(tmp_path):
    root = _root(tmp_path)
    (root / "proc/sys/kernel/random/boot_id").unlink()
    (root / "proc/uptime").write_text("not-uptime", encoding="utf-8")
    (root / "proc/cpuinfo").write_text("processor : 0\n", encoding="utf-8")
    (root / "sys/devices/system/cpu/online").write_text("0-x", encoding="utf-8")
    (root / "proc/pressure/cpu").unlink()
    (root / "proc/pressure/memory").write_text("some avg10=NaN avg60=0 avg300=0 total=0\n", encoding="utf-8")
    (root / "proc/meminfo").write_text("MemTotal: nope\nMemAvailable: 0 kB\nSwapTotal: 0 kB\n", encoding="utf-8")
    (root / "sys/fs/cgroup/cgroup.controllers").unlink()
    (root / "sys/fs/cgroup/ctl.slice").rename(root / "sys/fs/cgroup/renamed.slice")
    (root / "sys/kernel/mm/ksm/run").write_text("bad", encoding="utf-8")
    facts = collect_host_facts(root)
    assert facts["boot_id"] is None and facts["boot_id_reason"]
    assert facts["host_now_boottime_s"] is None and facts["host_now_boottime_s_reason"]
    assert facts["cpu"]["model"] is None and facts["cpu"]["model_reason"]
    assert facts["cpu"]["online"] is None and facts["cpu"]["online_reason"]
    assert facts["memory"]["total_bytes"] is None and facts["memory"]["total_bytes_reason"]
    assert facts["memory"]["available_bytes"] == 0
    assert facts["memory"]["swap_free_bytes"] is None and facts["memory"]["swap_free_bytes_reason"]
    assert facts["psi"]["cpu"]["some_avg10_pct"] is None
    assert facts["psi"]["memory"]["some_avg10_pct"] is None
    assert facts["cgroup_v2"]["mounted"] is False
    assert facts["cgroup_v2"]["controllers"] is None
    assert facts["control_plane"]["present"] is False
    assert facts["control_plane"]["memory_min_bytes"] is None
    assert facts["kernel_interfaces"]["memory_reclaim"] is None
    assert facts["ksm"]["run"] is None and facts["ksm"]["run_reason"]


def test_meminfo_missing_duplicate_and_malformed_fields_keep_reasons(tmp_path):
    root = _root(tmp_path)
    (root / "proc/meminfo").write_text(
        "MemTotal: 1 kB\nMemTotal: 2 kB\nMemAvailable: bad\nSwapTotal: 3 kB\nSwapFree: 4 kB\n",
        encoding="utf-8",
    )
    facts = collect_host_facts(root)
    assert facts["memory"]["total_bytes"] is None
    assert "duplicated" in facts["memory"]["total_bytes_reason"]
    assert facts["memory"]["available_bytes"] is None
    assert "malformed" in facts["memory"]["available_bytes_reason"]


def test_control_plane_slice_path_supports_nested_systemd_slices(tmp_path):
    root = _root(tmp_path)
    config_path = _config_path(root)
    text = config_path.read_text(encoding="utf-8").replace('slice = "ctl.slice"', 'slice = "dev-background.slice"')
    config_path.write_text(text, encoding="utf-8")
    parent = root / "sys/fs/cgroup/dev.slice/dev-background.slice"
    parent.mkdir(parents=True)
    (parent / "memory.min").write_text("1048576", encoding="utf-8")
    (parent / "cpu.weight").write_text("200", encoding="utf-8")
    (parent / "cgroup.freeze").write_text("0", encoding="utf-8")
    facts = collect_host_facts(root)
    assert facts["control_plane"]["present"] is True
    assert facts["control_plane"]["memory_min_bytes"] == 1048576
    assert facts["control_plane"]["cpu_weight"] == 200


def test_guard_dropins_override_rules_and_change_digest(tmp_path):
    root = _root(tmp_path)
    before = collect_host_facts(root)["guard"]["config_digest"]
    dropin = root / "etc/cgprofile/guard.toml.d/20-disabled.toml"
    dropin.write_text("enabled = false\n", encoding="utf-8")
    after = collect_host_facts(root)["guard"]
    assert after["enabled"] is False
    assert after["config_digest"] != before


def test_guard_invalid_config_or_missing_main_retains_independent_state(tmp_path):
    root = _root(tmp_path)
    main = root / "etc/cgprofile/guard.toml"
    main.write_text("enabled = [", encoding="utf-8")
    invalid = collect_host_facts(root)["guard"]
    assert invalid["enabled"] is None and invalid["enabled_reason"]
    assert invalid["config_digest"].startswith("sha256:")
    main.unlink()
    missing = collect_host_facts(root)["guard"]
    assert missing["enabled"] is None and missing["enabled_reason"]
    assert missing["config_digest"] is None and missing["config_digest_reason"]
    assert missing["state"] == "idle"


def test_guard_dropin_directory_and_state_source_fail_closed(tmp_path):
    root = _root(tmp_path)
    dropin = root / "etc/cgprofile/guard.toml.d"
    shutil.rmtree(dropin)
    dropin.write_text("not a directory", encoding="utf-8")
    state = root / "run/cgprofile/guard-state"
    state.unlink()
    facts = collect_host_facts(root)
    assert facts["guard"]["enabled"] is None
    assert facts["guard"]["config_digest"] is None
    assert facts["guard"]["state"] is None
    assert facts["guard"]["state_reason"]
