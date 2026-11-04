"""CP-1: machine facts, the host benchmark reader and the fallback score table.

Oracles are literal expected values. Nothing here touches the live host: every
root is a tmp tree and the benchmark path is a tmp file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from cgprofile import machine, serve, summary, util

MODEL = "AMD EPYC 9634 84-Core Processor"
CLAIM_CANARY = "https://browser.geekbench.com/v7/cpu/4242/claim?key=SECRETKEYCANARY"


def _record(**over: Any) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "schema": "mdt-host-cpu-benchmark/1", "tool": "geekbench", "tool_version": "7.1.0",
        "tool_major": 7, "baseline_score": 2500, "tool_sha256": "a" * 64,
        "measured_at": "2026-11-04T19:10:00Z", "cpu_model": MODEL, "cpus_online": 4,
        "hypervisor": True, "single_core": 2100, "multi_core": 7800, "score_source": "stdout",
        "result_url": "https://browser.geekbench.com/v7/cpu/4242",
        "conditions": {"loadavg1_start": 0.1}, "reason": None,
        "produced_by": "mdt host-setup 1.0",
    }
    rec.update(over)
    return rec


def _entry(**over: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "cpu_model": MODEL, "geekbench_major": 7, "single_core": 1900,
        "source_url": "https://example.test/chart", "retrieved": "2026-11-01",
    }
    entry.update(over)
    return entry


def _write(path: Path, rec: Any) -> str:
    path.write_text(rec if isinstance(rec, str) else json.dumps(rec))
    return str(path)


# ── cpuinfo / affinity / quota / smt ────────────────────────────────────────

def _cpuinfo(path: Path, *, model: str = MODEL, cpus: int = 4, hypervisor: bool = True) -> None:
    flags = "fpu vme hypervisor sse" if hypervisor else "fpu vme sse"
    blocks = [f"processor\t: {i}\nmodel name\t: {model}\nflags\t\t: {flags}\n" for i in range(cpus)]
    (path / "cpuinfo").write_text("\n".join(blocks))


def test_read_cpuinfo_collapses_whitespace_and_counts_processors(tmp_path):
    _cpuinfo(tmp_path, model="AMD   EPYC  9634  84-Core Processor ", cpus=3)
    assert machine.read_cpuinfo(str(tmp_path)) == (MODEL, 3, True)


def test_read_cpuinfo_no_hypervisor_flag(tmp_path):
    _cpuinfo(tmp_path, hypervisor=False)
    assert machine.read_cpuinfo(str(tmp_path)) == (MODEL, 4, False)


def test_read_cpuinfo_missing_file_is_all_unknown(tmp_path):
    assert machine.read_cpuinfo(str(tmp_path)) == (None, None, None)


def test_read_cpuinfo_without_model_line_keeps_the_count(tmp_path):
    (tmp_path / "cpuinfo").write_text("processor\t: 0\nprocessor\t: 1\nmodel name\t: \n")
    assert machine.read_cpuinfo(str(tmp_path)) == (None, 2, False)


def test_read_cpuinfo_first_model_line_wins(tmp_path):
    (tmp_path / "cpuinfo").write_text("processor : 0\nmodel name : A\nprocessor : 1\nmodel name : B\n")
    assert machine.read_cpuinfo(str(tmp_path)) == ("A", 2, False)


def test_read_cpuinfo_file_without_processors_is_unknown_count(tmp_path):
    (tmp_path / "cpuinfo").write_text("something : else\n")
    assert machine.read_cpuinfo(str(tmp_path)) == (None, None, None)


@pytest.mark.parametrize("text,expected", [
    ("0-3", 4), ("0-1,4,6-7", 5), ("2", 1), ("3-1", None), ("a-b", None), ("0,x", None),
])
def test_parse_cpu_list(text, expected):
    assert machine._parse_cpu_list(text) == expected


def test_affinity_reads_cpus_allowed_list(tmp_path):
    (tmp_path / "self").mkdir()
    (tmp_path / "self" / "status").write_text("Name:\tcgpd\nCpus_allowed_list:\t0-2,5\n")
    assert machine.affinity_cpus(str(tmp_path)) == 4


def test_affinity_unknown_when_absent(tmp_path):
    assert machine.affinity_cpus(str(tmp_path)) is None
    (tmp_path / "self").mkdir()
    (tmp_path / "self" / "status").write_text("Name:\tcgpd\n")
    assert machine.affinity_cpus(str(tmp_path)) is None


def _cpu_max(root: Path, rel: str, text: str) -> None:
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "cpu.max").write_text(text)


def test_quota_is_the_tightest_over_the_ancestors(tmp_path):
    _cpu_max(tmp_path, "dev.slice", "400000 100000\n")
    _cpu_max(tmp_path, "dev.slice/gate.scope", "150000 100000\n")
    _cpu_max(tmp_path, "dev.slice/gate.scope/leaf", "max 100000\n")
    assert machine.quota_cpus("/dev.slice/gate.scope/leaf", str(tmp_path)) == 1.5


def test_quota_none_when_no_limit_or_zero_period(tmp_path):
    _cpu_max(tmp_path, "a", "max 100000\n")
    _cpu_max(tmp_path, "a/b", "50000 0\n")
    assert machine.quota_cpus("/a/b", str(tmp_path)) is None
    assert machine.quota_cpus("/missing", str(tmp_path)) is None


def test_cpus_available_intersects_affinity_and_quota(tmp_path):
    (tmp_path / "self").mkdir()
    (tmp_path / "self" / "status").write_text("Cpus_allowed_list:\t0-3\n")
    _cpu_max(tmp_path, "lane", "250000 100000\n")
    assert machine.cpus_available(str(tmp_path), str(tmp_path), "/lane") == 2.5
    assert machine.cpus_available(str(tmp_path), str(tmp_path), None) == 4
    _cpu_max(tmp_path, "wide", "900000 100000\n")
    assert machine.cpus_available(str(tmp_path), str(tmp_path), "/wide") == 4


def test_cpus_available_quota_only_and_unknown(tmp_path):
    _cpu_max(tmp_path, "lane", "200000 100000\n")
    assert machine.cpus_available(str(tmp_path), str(tmp_path), "/lane") == 2
    assert machine.cpus_available(str(tmp_path), str(tmp_path), None) is None


def test_smt_active(tmp_path):
    smt = tmp_path / "devices/system/cpu/smt"
    smt.mkdir(parents=True)
    assert machine.smt_active(str(tmp_path)) is None
    (smt / "active").write_text("1\n")
    assert machine.smt_active(str(tmp_path)) is True
    (smt / "active").write_text("0\n")
    assert machine.smt_active(str(tmp_path)) is False


# ── the host record: validity and the privacy boundary ──────────────────────

def test_load_absent_and_default_path(tmp_path):
    assert machine.load_host_benchmark(str(tmp_path / "none.json")) == ("absent", None)
    # the conftest redirects the default away from the live host
    assert machine.load_host_benchmark() == ("absent", None)


@pytest.mark.parametrize("content", [
    "not json", "[1, 2]", json.dumps({"schema": "other/1"}),
    json.dumps(_record(tool="7zip")), json.dumps(_record(tool_major=5)),
    json.dumps(_record(tool_major=None)),
])
def test_load_invalid(tmp_path, content):
    assert machine.load_host_benchmark(_write(tmp_path / "b.json", content)) == ("invalid", None)


def test_load_valid(tmp_path):
    status, rec = machine.load_host_benchmark(_write(tmp_path / "b.json", _record()))
    assert status == "ok" and rec["single_core"] == 2100


def test_measured_record_gives_the_measured_speed(tmp_path):
    loaded = ("ok", _record())
    speed, reason = machine.resolve_speed(loaded, MODEL, 4, [])
    assert reason is None
    assert speed == {
        "basis": "geekbench7-measured", "geekbench_major": 7, "geekbench_version": "7.1.0",
        "baseline_score": 2500, "single_core": 2100, "multi_core": 7800,
        "cpus_at_measurement": 4, "measured_on": "2026-11-04",
    }


def test_measured_gb6_has_its_own_basis():
    speed, _ = machine.resolve_speed(("ok", _record(tool_major=6, tool_version="6.5.0")), MODEL, 4, [])
    assert speed["basis"] == "geekbench6-measured" and speed["baseline_score"] == 2500


def test_measured_speed_tolerates_sparse_optional_fields():
    rec = _record(multi_core=None, tool_version=None, measured_at="soon", cpus_online=4)
    speed, _ = machine.resolve_speed(("ok", rec), MODEL, 4, [])
    assert speed["multi_core"] is None
    assert speed["geekbench_version"] is None
    assert speed["measured_on"] is None


def test_export_never_carries_url_conditions_or_host_identity():
    speed, _ = machine.resolve_speed(("ok", _record()), MODEL, 4, [])
    text = json.dumps(speed)
    for banned in ("result_url", "geekbench.com", "conditions", "produced_by", "tool_sha256", "claim"):
        assert banned not in text


@pytest.mark.parametrize("field,value", [("cpu_model", "Intel Xeon"), ("cpus_online", 8)])
def test_model_or_cpu_count_mismatch_is_stale_and_uses_the_table(field, value):
    entries = [_entry(single_core=1900)]
    speed, reason = machine.resolve_speed(("ok", _record(**{field: value})), MODEL, 4, entries)
    assert reason == "HOST_MEASUREMENT_STALE"
    assert speed["basis"] == "geekbench7-table"
    assert speed["single_core"] == 1900
    assert speed["multi_core"] is None


def test_stale_without_table_entry_is_null_with_a_reason():
    speed, reason = machine.resolve_speed(("ok", _record(cpus_online=8)), MODEL, 4, [])
    assert speed is None
    assert reason == "HOST_MEASUREMENT_STALE_AND_MODEL_NOT_IN_TABLE"


def test_unverifiable_live_identity_is_stale():
    assert machine.resolve_speed(("ok", _record()), None, 4, [])[1] == "HOST_MEASUREMENT_STALE_AND_MODEL_NOT_IN_TABLE"
    assert machine.resolve_speed(("ok", _record()), MODEL, None, [])[1] == "HOST_MEASUREMENT_STALE_AND_MODEL_NOT_IN_TABLE"


@pytest.mark.parametrize("single", [None, 0, -5, "2100", True, 2_000_000])
def test_incomplete_record_is_not_used(single):
    speed, reason = machine.resolve_speed(("ok", _record(single_core=single)), MODEL, 4, [])
    assert speed is None and reason == "HOST_MEASUREMENT_INCOMPLETE_AND_MODEL_NOT_IN_TABLE"


def test_absent_file_uses_the_table_else_unknown():
    speed, reason = machine.resolve_speed(("absent", None), MODEL, 4, [_entry()])
    assert reason == "NO_HOST_MEASUREMENT" and speed["basis"] == "geekbench7-table"
    assert machine.resolve_speed(("absent", None), MODEL, 4, []) == (None, "NO_HOST_MEASUREMENT_AND_MODEL_NOT_IN_TABLE")
    assert machine.resolve_speed(("absent", None), None, None, [_entry()]) == (
        None, "NO_HOST_MEASUREMENT_AND_MODEL_NOT_IN_TABLE")


def test_invalid_file_has_its_own_reason():
    assert machine.resolve_speed(("invalid", None), MODEL, 4, []) == (
        None, "HOST_MEASUREMENT_INVALID_AND_MODEL_NOT_IN_TABLE")


# ── the table ───────────────────────────────────────────────────────────────

def test_table_prefers_the_stale_records_major_then_the_newest():
    entries = [_entry(geekbench_major=6, single_core=2000), _entry(geekbench_major=7, single_core=2400)]
    stale6 = machine.resolve_speed(("ok", _record(tool_major=6, cpus_online=9)), MODEL, 4, entries)[0]
    assert (stale6["basis"], stale6["single_core"]) == ("geekbench6-table", 2000)
    none = machine.resolve_speed(("absent", None), MODEL, 4, entries)[0]
    assert (none["basis"], none["single_core"]) == ("geekbench7-table", 2400)


def test_table_lookup_is_by_exact_model_after_whitespace_collapse():
    entries = [_entry(cpu_model="AMD  EPYC 9634   84-Core Processor")]
    assert machine.table_speed(MODEL, None, entries)["single_core"] == 1900
    assert machine.table_speed("AMD EPYC 9634", None, entries) is None
    assert machine.table_speed(None, None, entries) is None


def test_table_speed_carries_its_source_not_a_host_identity():
    speed = machine.table_speed(MODEL, None, [_entry()])
    assert speed["source_url"] == "https://example.test/chart"
    assert speed["retrieved"] == "2026-11-01"
    assert speed["measured_on"] is None and speed["cpus_at_measurement"] is None


def test_the_packaged_table_loads_and_every_entry_is_valid():
    path = Path(machine.__file__).parent / "data" / "cgprofile-scores.json"
    doc = json.loads(path.read_text())
    assert doc["schema"] == "cgprofile-scores/1"
    for entry in doc["entries"]:
        assert machine.valid_entry(entry), entry
    keys = [(machine.normalise_model(e["cpu_model"]), e["geekbench_major"]) for e in doc["entries"]]
    assert len(keys) == len(set(keys))
    assert machine.load_scores_table() == doc["entries"]


def test_load_scores_table_skips_invalid_entries_and_refuses_a_foreign_schema():
    good = _entry()
    text = json.dumps({"schema": "cgprofile-scores/1", "entries": [good, _entry(single_core=0)]})
    assert machine.load_scores_table(text) == [good]
    with pytest.raises(ValueError):
        machine.load_scores_table(json.dumps({"schema": "x/1", "entries": []}))
    assert machine.load_scores_table(json.dumps({"schema": "cgprofile-scores/1"})) == []


@pytest.mark.parametrize("over", [
    {"cpu_model": "  "}, {"cpu_model": 5}, {"geekbench_major": 5}, {"single_core": 0},
    {"single_core": 1.5}, {"source_url": "http://insecure"}, {"source_url": None},
    {"retrieved": "yesterday"}, {"retrieved": None},
])
def test_invalid_entries(over):
    assert machine.valid_entry(_entry(**over)) is False


def test_non_dict_entry_is_invalid():
    assert machine.valid_entry("x") is False


def test_normalise_model_rejects_non_strings():
    assert machine.normalise_model(None) is None
    assert machine.normalise_model("") is None


# ── build_machine end to end, and the claim file is never opened ───────────

def test_build_machine_reads_only_the_named_files(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    _cpuinfo(proc)
    state = tmp_path / "state"
    state.mkdir()
    bench = _write(state / "cpu-benchmark.json", _record())
    (state / "cpu-benchmark.claim.json").write_text(json.dumps({"claim_url": CLAIM_CANARY}))
    opened: List[str] = []
    real = util.read_text

    def spy(path: str):
        opened.append(path)
        return real(path)

    monkeypatch.setattr(util, "read_text", spy)
    loaded = machine.load_host_benchmark(bench)
    built = machine.build_machine(
        loaded=loaded, entries=[], proc_root=str(proc), cgroup_root=str(tmp_path / "cg"),
        sys_root=str(tmp_path / "sys"),
    )
    assert built["schema"] == "machine/1"
    assert built["cpu_model"] == MODEL and built["cpus_online"] == 4 and built["hypervisor"] is True
    assert built["speed"]["basis"] == "geekbench7-measured" and built["speed_reason"] is None
    assert not [p for p in opened if "claim" in p]
    assert "SECRETKEYCANARY" not in json.dumps(built)


# ── serve integration ───────────────────────────────────────────────────────

def _server(tmp_path: Path, bench: str, **kw: Any) -> serve.SessionServer:
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True, exist_ok=True)
    _cpuinfo(proc)
    (proc / "self" / "status").write_text("Cpus_allowed_list:\t0-3\n")
    # user nice system idle iowait irq softirq steal guest guest_nice
    (proc / "stat").write_text("cpu  1000 0 500 8000 100 0 50 350 77 0\n")
    cg = tmp_path / "cg"
    cg.mkdir(exist_ok=True)
    return serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), cgroup_root=str(cg), proc_root=str(proc),
        host_benchmark_path=bench, sys_root=str(tmp_path / "sys"), **kw,
    )


def test_host_verb_reports_machine_and_cumulative_steal(tmp_path, monkeypatch):
    monkeypatch.setattr(util, "clk_tck", lambda: 100)
    server = _server(tmp_path, _write(tmp_path / "b.json", _record()))
    host = server.handle_host({})["host"]
    assert host["machine"]["speed"]["single_core"] == 2100
    assert host["machine"]["cpus_available"] == 4
    assert host["steal_seconds"] == 3.5
    # user..steal = 1000+0+500+8000+100+0+50+350; guest is already inside user
    assert host["cpu_seconds_total"] == 100.0


def test_host_verb_without_stat_has_null_counters(tmp_path):
    server = _server(tmp_path, str(tmp_path / "absent.json"))
    (tmp_path / "proc" / "stat").unlink()
    host = server.handle_host({})["host"]
    assert host["steal_seconds"] is None and host["cpu_seconds_total"] is None
    assert host["machine"]["speed_reason"] == "NO_HOST_MEASUREMENT_AND_MODEL_NOT_IN_TABLE"


def test_a_vm_resize_after_the_start_is_stale_until_reload(tmp_path):
    bench = tmp_path / "b.json"
    _write(bench, _record(cpus_online=8))
    server = _server(tmp_path, str(bench))
    assert server._machine()["speed_reason"] == "HOST_MEASUREMENT_STALE_AND_MODEL_NOT_IN_TABLE"
    _write(bench, _record())  # remeasured on the resized host
    assert server._machine()["speed_reason"] == "HOST_MEASUREMENT_STALE_AND_MODEL_NOT_IN_TABLE"
    server.reload_settings()
    assert server._machine()["speed_reason"] is None


def test_lane_machine_applies_the_cgroup_quota(tmp_path):
    server = _server(tmp_path, str(tmp_path / "absent.json"))
    _cpu_max(tmp_path / "cg", "dev.slice/lane.scope", "150000 100000\n")
    assert server._machine("/dev.slice/lane.scope")["cpus_available"] == 1.5


# ── the summary host block ──────────────────────────────────────────────────

def _acc(machine_doc):
    return summary.SummaryAccumulator(
        session="s-1", daemon_name="d", daemon_version="v", scope="container-shared",
        started_at="2026-09-12T10:15:00Z", interval_seconds=1.0, container_id="c",
        cgroup="/a/b.scope", token=None, machine=machine_doc,
    )


def _host(steal: Any, total: Any) -> Dict[str, Any]:
    return {"psi": {}, "loadavg": [1.0, 1.0, 1.0],
            "cpu": {"steal_jiffies": steal, "accounted_jiffies": total}}


def test_summary_host_block_has_the_session_deltas_in_seconds(monkeypatch):
    monkeypatch.setattr(util, "clk_tck", lambda: 100)
    acc = _acc({"schema": "machine/1"})
    for i, (steal, total) in enumerate([(100, 1000), (130, 2000), (150, 3000)]):
        acc.add_sample(cgroup={}, host=_host(steal, total), mono=float(i))
    block = acc.finalize(ended_at="2026-09-12T10:15:02Z")["host"]
    assert block["steal_seconds"] == 0.5     # (150-100)/100
    assert block["cpu_seconds_total"] == 20.0  # (3000-1000)/100
    assert block["machine"] == {"schema": "machine/1"}


def test_summary_steal_is_null_when_the_kernel_does_not_report_it():
    acc = _acc(None)
    acc.add_sample(cgroup={}, host=_host(None, 500), mono=0.0)
    acc.add_sample(cgroup={}, host=_host(None, 900), mono=1.0)
    block = acc.finalize(ended_at="2026-09-12T10:15:01Z")["host"]
    assert block["steal_seconds"] is None
    assert block["cpu_seconds_total"] == 4.0
    assert block["machine"] is None
