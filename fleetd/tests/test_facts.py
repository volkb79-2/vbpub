import json
import shutil
from pathlib import Path

import pytest

from fleetd.contracts import ContractError, dump_json
from fleetd.cli import main
from fleetd.facts import (
    _config_path as _validate_config_path,
    _cpu_list_count,
    _cpu_model,
    _meminfo_values,
    _object,
    _online_cpu_count,
    _psi_value,
    _read_integer,
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
        ('overcommit = 1.5', 'overcommit = 0', "greater than zero"),
        ('oversubscribe_max = 1.25', 'oversubscribe_max = 11', "at most 10"),
        ('psi_refuse_some_avg10 = 25', 'psi_refuse_some_avg10 = 101', "from 0 through 100"),
        ('max_lane_containers = 8', 'max_lane_containers = true', "positive integer"),
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
    config_path.write_text(text, encoding="utf-8")
    (root / "etc/mdt/host-setup.env").unlink()
    facts = collect_host_facts(root)
    assert facts["guard"]["state"] is None
    assert facts["guard"]["state_reason"]
    assert facts["ksm"]["lane_optin_shim"] is None
    assert facts["ksm"]["lane_optin_shim_reason"]
    assert facts["policy"]["host_role"] is None
    assert facts["policy"]["host_role_reason"]
    assert facts["policy"]["max_lane_containers"] is None
    assert facts["policy"]["max_lane_containers_reason"]


def test_host_role_comes_from_host_setup_env_and_invalid_source_is_unknown(tmp_path):
    root = _root(tmp_path)
    role_path = root / "etc/mdt/host-setup.env"
    assert collect_host_facts(root)["policy"]["host_role"] == "dedicated-dev"

    role_path.write_text('HOST_ROLE="shared-prod" # production host\n', encoding="utf-8")
    facts = collect_host_facts(root)
    assert facts["policy"]["host_role"] == "shared-prod"
    assert "host_role_reason" not in facts["policy"]

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
        assert facts["policy"]["host_role"] is None
        assert reason in facts["policy"]["host_role_reason"]

    role_path.unlink()
    facts = collect_host_facts(root)
    assert facts["policy"]["host_role"] is None
    assert "unavailable" in facts["policy"]["host_role_reason"]

    role_path.write_bytes(bytes([255]))
    facts = collect_host_facts(root)
    assert facts["policy"]["host_role"] is None
    assert "not UTF-8" in facts["policy"]["host_role_reason"]


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
    invalid_paths = original.replace(
        '[paths]\ngeekbench_record = "/var/lib/mdt/host-facts/cpu-benchmark.json"\nhost_setup_env = "/etc/mdt/host-setup.env"\nguard_config = "/etc/cgprofile/guard.toml"\nguard_state = "/run/cgprofile/guard-state"\nksm_optin_shim = "/usr/lib/fleetd/ksm-optin.so"\n\n[control_plane]',
        'paths = "invalid"\n\n[control_plane]',
    )
    config_path.write_text(invalid_paths, encoding="utf-8")
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
    assert facts["speed"] is None
    assert reason in facts["speed_reason"]


def test_facts_collector_keeps_unknowns_reasoned_and_does_not_guess(tmp_path):
    root = _root(tmp_path)
    (root / "proc/sys/kernel/random/boot_id").unlink()
    (root / "proc/uptime").write_text("not-uptime", encoding="utf-8")
    (root / "proc/cpuinfo").write_text("processor : 0\n", encoding="utf-8")
    (root / "sys/devices/system/cpu/online").write_text("0-x", encoding="utf-8")
    (root / "proc/pressure/cpu").unlink()
    (root / "proc/pressure/io").write_text("", encoding="utf-8")
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
    assert facts["psi"]["io"]["some_avg10_pct"] is None
    assert facts["cgroup_v2"]["mounted"] is False
    assert facts["cgroup_v2"]["controllers"] is None
    assert facts["control_plane"]["present"] is False
    assert facts["control_plane"]["memory_min_bytes"] is None
    assert facts["kernel_interfaces"]["memory_reclaim"] is None
    assert facts["ksm"]["run"] is None and facts["ksm"]["run_reason"]


@pytest.mark.parametrize("uptime", [None, "", "-1.0 0", "nan 0"])
def test_boottime_is_null_with_a_reason_when_source_is_unreadable_or_invalid(tmp_path, uptime):
    root = _root(tmp_path)
    source = root / "proc/uptime"
    if uptime is None:
        source.unlink()
    else:
        source.write_text(uptime, encoding="utf-8")
    facts = collect_host_facts(root)
    assert facts["host_now_boottime_s"] is None
    assert facts["host_now_boottime_s_reason"]


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


def test_cgroup_interfaces_and_ksm_missing_values_are_not_reported_as_present(tmp_path):
    root = _root(tmp_path)
    control = root / "sys/fs/cgroup/ctl.slice"
    (control / "cgroup.freeze").unlink()
    (control / "memory.reclaim").unlink()
    (root / "sys/kernel/mm/ksm/advisor_mode").unlink()
    (root / "sys/kernel/mm/ksm/advisor_max_cpu").unlink()
    (root / "usr/lib/fleetd/ksm-optin.so").unlink()
    facts = collect_host_facts(root)
    assert facts["kernel_interfaces"]["cgroup_freeze"] is False
    assert facts["kernel_interfaces"]["memory_reclaim"] is False
    assert facts["ksm"]["advisor_mode"] is None
    assert facts["ksm"]["advisor_mode_reason"]
    assert facts["ksm"]["advisor_max_cpu"] is None
    assert facts["ksm"]["lane_optin_shim"] is False


def test_missing_control_plane_slice_does_not_claim_kernel_interfaces(tmp_path):
    root = _root(tmp_path)
    control = root / "sys/fs/cgroup/ctl.slice"
    control.rename(root / "sys/fs/cgroup/moved.slice")
    facts = collect_host_facts(root)
    assert facts["cgroup_v2"]["mounted"] is True
    assert facts["control_plane"]["present"] is False
    assert facts["kernel_interfaces"]["cgroup_freeze"] is None
    assert facts["kernel_interfaces"]["memory_reclaim"] is None
    assert facts["kernel_interfaces"]["cgroup_freeze_reason"]


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


def test_guard_without_dropin_directory_uses_main_config_only(tmp_path):
    root = _root(tmp_path)
    shutil.rmtree(root / "etc/cgprofile/guard.toml.d")
    guard = collect_host_facts(root)["guard"]
    assert guard["enabled"] is True
    assert guard["config_digest"].startswith("sha256:")


def test_guard_invalid_dropin_toml_or_rule_is_reasoned(tmp_path):
    root = _root(tmp_path)
    bad = root / "etc/cgprofile/guard.toml.d/99-bad.toml"
    bad.write_text("enabled = [", encoding="utf-8")
    parse_error = collect_host_facts(root)["guard"]
    assert parse_error["enabled"] is None
    assert "invalid" in parse_error["enabled_reason"]
    bad.write_text("unknown = true\n", encoding="utf-8")
    rule_error = collect_host_facts(root)["guard"]
    assert rule_error["enabled"] is None
    assert "unknown" in rule_error["enabled_reason"]
    assert rule_error["config_digest"].startswith("sha256:")


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
    assert guard["enabled"] is None
    assert guard["config_digest"] is None
    assert "simulated unreadable" in guard["config_digest_reason"]


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


def test_missing_guard_main_and_unconfigured_state_are_reasoned(tmp_path):
    root = _root(tmp_path)
    config = _config_path(root)
    config.write_text(config.read_text(encoding="utf-8").replace('guard_state = "/run/cgprofile/guard-state"\n', ""), encoding="utf-8")
    (root / "etc/cgprofile/guard.toml").unlink()
    guard = collect_host_facts(root)["guard"]
    assert guard["enabled"] is None and guard["enabled_reason"]
    assert guard["config_digest"] is None and guard["config_digest_reason"]
    assert guard["state"] is None
    assert guard["state_reason"] == "guard runtime state source is not configured"
