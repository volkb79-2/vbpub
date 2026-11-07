"""IOB-CAP: cache capability history, check output, and indirect read hints."""
import io
import json

import pytest
from cli_extended import CliIdentity

from iobench import cache, capability, ceilings, cli, result as res
from iobench.errors import Refusal
from iobench import calibrate as cal_mod
from iobench import plan
from iobench.testing import GIB

from helpers import INVERSION, calibrate, config, fio_jobs, make_host

IDENTITY = CliIdentity("iobench", "0.1.0", "cache-proof disk benchmark", "iobench")
SCOPE = {"model": "M", "serial": "S1", "wwn": None, "size_bytes": 1 << 40, "strength": "strong",
         "machine_id_hash": None, "name": "vda"}
IDENT = {"target": {"kind": "device"}, "disk_scope": SCOPE}
DIGEST = ceilings.identity_digest(IDENT)
PATH = "/var/lib/iobench/capability.jsonl"
STATE = "/var/lib/iobench"
T0 = 1_792_550_400.0


def row(**kw):
    return {"schema": capability.SCHEMA, "ts": ceilings.iso(T0), "disk_key": DIGEST, "kind": "raw", "profile": "standard",
            "source": "benchmark", "burst_bytes": 1000.0, "knee_s": 10.0, "post_cliff_bps": 100.0,
            "pre_cliff_bps": 200.0, "cliff_bytes": 1 << 30, "cold_region_bps": 90.0,
            "write_burst_seen": None, "confidence": "medium", "note": None, **kw}


def run_cli(host, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = cli.build_cli(IDENTITY, host_factory=lambda: host).run(argv=list(argv), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_cap_01_append_only_schema_and_disk_binding():
    host = make_host(cache=INVERSION)
    host.makedirs("/var/lib/iobench")
    capability.append(host, PATH, row(), DIGEST)
    capability.append(host, PATH, row(ts=ceilings.iso(T0 + 1), burst_bytes=2000.0), DIGEST)
    lines = host.read_text(PATH).splitlines()
    assert json.loads(lines[0]) == {"header": True, "schema": capability.SCHEMA, "identity_digest": DIGEST}
    assert [json.loads(line)["burst_bytes"] for line in lines[1:]] == [1000.0, 2000.0]
    assert all(json.loads(line)["kind"] == "raw" for line in lines[1:])
    assert capability.load(host, PATH, DIGEST) == ([row(), row(ts=ceilings.iso(T0 + 1), burst_bytes=2000.0)], [], 0)

    other = "sha256:" + "f" * 64
    with pytest.raises(Refusal) as err:
        capability.append(host, PATH, row(disk_key=other), other)
    assert err.value.condition == "capability_identity_mismatch"
    aside = capability.rotate_foreign(host, PATH, other, "2026-11-07T12:00:00Z")
    assert aside == PATH + ".superseded-2026-11-07T120000Z"
    assert host.exists(aside) and not host.exists(PATH)
    assert capability.rotate_foreign(host, PATH, other, "2026-11-07T12:00:00Z") is None
    assert capability.load(host, "/missing/capability.jsonl", DIGEST) == ([], [], 0)
    assert capability.load(host, aside, DIGEST) == ([row(), row(ts=ceilings.iso(T0 + 1), burst_bytes=2000.0)], [], 0)
    assert capability.load(host, aside, other) == ([], ["capability_history_identity_differs"], 0)
    host.put(PATH, "broken header\n")
    corrupt_aside = capability.rotate_foreign(host, PATH, DIGEST, "2026-11-07T12:01:00Z")
    assert corrupt_aside == PATH + ".superseded-2026-11-07T120100Z"
    assert host.read_text(corrupt_aside) == "broken header\n" and not host.exists(PATH)


def test_cap_01_rejects_invalid_rows_and_unreadable_headers():
    host = make_host(cache=INVERSION)
    invalid = (
        (row(schema=2), "capability_schema"),
        (row(schema=True), "capability_schema"),
        (row(ts="yesterday"), "capability_timestamp"),
        (row(disk_key="sha256:other"), "capability_identity"),
        (row(kind="unknown"), "capability_kind"),
        (row(kind="probe", source="benchmark"), "capability_kind"),
        (row(kind="raw", source="probe"), "capability_kind"),
        (row(source="oracle"), "capability_source"),
        (row(profile="full"), "capability_profile"),
        (row(burst_bytes=float("nan")), "capability_number"),
        (row(knee_s=True), "capability_number"),
        (row(post_cliff_bps=-1), "capability_number"),
        (row(write_burst_seen="yes"), "capability_burst_seen"),
        (row(confidence="certain"), "capability_confidence"),
        (row(note=17), "capability_note"),
    )
    for bad, condition in invalid:
        with pytest.raises(Refusal) as err:
            capability.validate(bad, DIGEST)
        assert err.value.condition == condition, bad

    host.put(PATH, "not json\n")
    assert capability.load(host, PATH, DIGEST) == ([], ["capability_history_corrupt"], 0)
    host.put(PATH, json.dumps({"header": False, "schema": capability.SCHEMA, "identity_digest": DIGEST}) + "\n")
    assert capability.load(host, PATH, DIGEST) == ([], ["capability_history_corrupt"], 0)
    host.put(PATH, json.dumps({"header": True, "schema": True, "identity_digest": DIGEST}) + "\n")
    assert capability.load(host, PATH, DIGEST) == ([], ["capability_history_corrupt"], 0)
    host.put(PATH, json.dumps({"header": True, "schema": 99, "identity_digest": DIGEST}) + "\n")
    assert capability.load(host, PATH, DIGEST) == ([], ["capability_history_unsupported_schema:99"], 0)
    with pytest.raises(Refusal) as err:
        capability.append(host, PATH, row(), DIGEST)
    assert err.value.condition == "capability_history_unsupported_schema"
    assert capability.rotate_foreign(host, PATH, DIGEST, "2026-11-07T12:00:30Z") is None
    assert host.exists(PATH)
    host.put(PATH, "")
    with pytest.raises(Refusal) as err:
        capability.append(host, PATH, row(), DIGEST)
    assert err.value.condition == "capability_history_corrupt"

    host.put(PATH, capability.header_line(DIGEST) + "\n")
    original_read = host.read_text

    def unreadable(path):
        if path == PATH:
            raise OSError("permission denied")
        return original_read(path)

    host.read_text = unreadable
    assert capability.load(host, PATH, DIGEST) == ([], ["capability_history_corrupt"], 0)


def test_cap_01_source_kind_pairs_missing_fields_and_corrupt_rows():
    for kind, source in (("raw", "benchmark"), ("file", "benchmark"), ("probe", "probe"), ("file", "governor")):
        capability.validate(row(kind=kind, source=source), DIGEST)
    with pytest.raises(Refusal) as err:
        capability.validate({key: value for key, value in row().items() if key != "pre_cliff_bps"}, DIGEST)
    assert err.value.condition == "capability_fields"
    for field in ("burst_bytes", "knee_s"):
        capability.validate(row(**{field: 0}), DIGEST)
    for field in ("post_cliff_bps", "pre_cliff_bps", "cliff_bytes", "cold_region_bps"):
        with pytest.raises(Refusal) as err:
            capability.validate(row(**{field: 0}), DIGEST)
        assert err.value.condition == "capability_number"

def test_cap_01_load_skips_blank_lines_and_rejects_non_object_rows():
    host = make_host(cache=INVERSION)
    host.put(PATH, capability.header_line(DIGEST) + "\n\n" + json.dumps(row()) + "\n")
    assert capability.load(host, PATH, DIGEST) == ([row()], [], 0)
    host.put(PATH, capability.header_line(DIGEST) + "\n" + json.dumps(row()) + "\n[]\n{" + '"schema":1')
    rows, warnings, skipped = capability.load(host, PATH, DIGEST)
    assert rows == [row()] and warnings == ["capability_history_rows_skipped:2"] and skipped == 2
    capability.append(host, PATH, row(ts=ceilings.iso(T0 + 1)), DIGEST)
    rows, warnings, skipped = capability.load(host, PATH, DIGEST)
    assert rows == [row(), row(ts=ceilings.iso(T0 + 1))] and warnings == ["capability_history_rows_skipped:2"] and skipped == 2
    host.put(PATH, json.dumps({"header": True, "schema": 1, "identity_digest": DIGEST}) + "\n" + json.dumps(row()))
    capability.append(host, PATH, row(ts=ceilings.iso(T0 + 2)), DIGEST)
    rows, warnings, skipped = capability.load(host, PATH, DIGEST)
    assert rows == [row(), row(ts=ceilings.iso(T0 + 2))] and warnings == [] and skipped == 0
    host.put(PATH, capability.header_line(DIGEST) + "\n[]\n")
    assert capability.load(host, PATH, DIGEST) == ([], ["capability_history_rows_skipped:1"], 1)
    aside = capability.rotate_foreign(host, PATH, DIGEST, "2026-11-07T13:00:00Z")
    assert aside is None and host.exists(PATH)


def test_cap_01_since_and_latest_summary():
    rows = [row(), row(ts=ceilings.iso(T0 + 1), cliff_bytes=None)]
    assert capability.select_since(rows, None) == rows
    assert capability.select_since(rows, ceilings.iso(T0 + 1)) == rows[1:]
    assert capability.latest_summary([]) == {
        "count": 0, "latest": {"raw": None, "file": None, "probe": None},
        "by_kind": {kind: {"count": 0, "latest": None} for kind in ("raw", "file", "probe")}, "skipped_rows": 0,
    }
    latest = capability.latest_summary(rows, skipped_rows=2)
    assert latest["count"] == 2 and latest["latest"] == {"raw": rows[-1], "file": None, "probe": None}
    assert latest["by_kind"]["raw"] == {"count": 2, "latest": rows[-1]}
    assert latest["skipped_rows"] == 2
    out_of_order = [row(ts=ceilings.iso(T0 + 5)), row(ts=ceilings.iso(T0 + 1))]
    assert capability.latest_summary(out_of_order)["latest"]["raw"] == out_of_order[-1]
    with pytest.raises(Refusal) as err:
        capability.select_since(rows, "not-a-timestamp")
    assert err.value.condition == "capability_since"


def test_cap_01_observation_extraction_keeps_only_measured_facts():
    identity = {"disk_scope": {"key": DIGEST}}
    vq_cal = {"veryquick": {"write_burst_seen": True}, "cache": {"write": {"time_proof": None}, "read": {"e_cold": None}},
              "ladder": {"cliff_bytes": None}}
    vq = capability.run_observation(vq_cal, identity, "veryquick", "raw", ceilings.iso(T0))
    assert vq["write_burst_seen"] is True and all(vq[name] is None for name in capability.NUMERIC_FIELDS)
    assert vq["confidence"] == "low" and "numeric" in vq["note"]

    cal = {"cache": {"write": {"time_proof": {"burst_bytes": 500, "knee_s": 10, "post_cliff_bps": 120,
                                             "pre_cliff_bps": 170, "proven": True}},
                      "read": {"e_cold": {"R_cold_iops": 100, "class": "miss-like"}}},
           "ladder": {"cliff_bytes": 1 << 30}}
    raw = capability.run_observation(cal, identity, "standard", "raw", ceilings.iso(T0))
    assert raw["burst_bytes"] == 500 and raw["post_cliff_bps"] == 120 and raw["pre_cliff_bps"] == 170
    assert raw["cliff_bytes"] == 1 << 30 and raw["cold_region_bps"] == 409600 and raw["confidence"] == "high"
    file_row = capability.run_observation(cal, identity, "standard", "file", ceilings.iso(T0))
    assert file_row["confidence"] == "medium"

    fallback = {"cache": {"write": {"time_proof": None}, "read": {"e_cold": None}},
                "ladder": {"cliff_bytes": None, "rungs": [
                    {"dir": "write", "state": "flat", "ws_bytes": 2, "bs": 4, "burst_units": 2, "knee_s": 2, "steady_iops": 10},
                    {"dir": "read", "state": "steady", "ws_bytes": 3, "bs": 4, "burst_units": 0, "knee_s": 0, "steady_iops": 10}]}}
    estimated = capability.run_observation(fallback, identity, "quick", "file", ceilings.iso(T0))
    assert estimated["burst_bytes"] == 8 and estimated["post_cliff_bps"] == 40 and estimated["pre_cliff_bps"] == 44
    assert estimated["confidence"] == "medium" and estimated["cliff_bytes"] is None and estimated["cold_region_bps"] is None
    no_write = capability.run_observation({"cache": {"write": {"time_proof": None}, "read": {"e_cold": None}},
                                           "ladder": {"cliff_bytes": None, "rungs": []}}, identity, "quick", "file", ceilings.iso(T0))
    assert no_write["confidence"] == "low" and no_write["burst_bytes"] is None
    assert no_write["note"] == "no numeric burst or cache fields were measurable in this run"


def test_cap_02_cli_reads_history_and_check_shows_latest_without_writes():
    host = make_host(cache=INVERSION)
    calibration = calibrate(host)
    code, out, err = run_cli(host, "capability", "--json")
    assert code == 0, err
    history = json.loads(out)
    assert history["count"] == 1 and history["latest"]["raw"]["profile"] == "standard"
    code, out, err = run_cli(host, "capability", "--since", history["latest"]["raw"]["ts"], "--kind", "raw", "--json")
    assert code == 0 and json.loads(out)["count"] == 1 and set(json.loads(out)["by_kind"]) == {"raw"}, err
    code, _, err = run_cli(host, "capability", "--since", "invalid", "--json")
    assert code == 2 and "timestamp" in err.lower()
    code, out, _ = run_cli(host, "check", "--json")
    checked = json.loads(out)
    assert checked["capability"]["by_kind"]["raw"]["latest"] == history["latest"]["raw"]
    baseline_sections = checked["sections"]
    baseline_limits = checked["limits"]
    baseline_warnings = checked["warnings"]
    code, baseline_proposals, _ = run_cli(host, "show", "--proposals", "--json")
    assert code == 0
    digest = ceilings.identity_digest(calibration.result["identity"])
    capability.append(host, PATH, {**history["latest"]["raw"], "disk_key": digest, "ts": ceilings.iso(host.now() + 1), "burst_bytes": 1e30}, digest)
    capability.append(host, PATH, {**history["latest"]["raw"], "disk_key": digest, "kind": "file",
                                   "ts": ceilings.iso(host.now() + 2), "burst_bytes": 2e30}, digest)
    before = list(host.write_log)
    code, out, _ = run_cli(host, "check", "--json")
    after = json.loads(out)
    assert after["capability"]["by_kind"]["raw"]["latest"]["burst_bytes"] == 1e30
    assert after["capability"]["by_kind"]["file"]["latest"]["burst_bytes"] == 2e30
    assert after["sections"] == baseline_sections and after["limits"] == baseline_limits and after["warnings"] == baseline_warnings
    code, proposals_after, _ = run_cli(host, "show", "--proposals", "--json")
    assert code == 0 and proposals_after == baseline_proposals
    code, file_only, _ = run_cli(host, "capability", "--kind", "file", "--json")
    assert code == 0 and json.loads(file_only)["count"] == 1 and set(json.loads(file_only)["by_kind"]) == {"file"}
    code, _, err = run_cli(host, "capability", "--kind", "bogus", "--json")
    assert code == 2 and "invalid choice" in err
    assert host.write_log == before


def test_cap_02_foreign_history_is_reported_by_check_and_capability():
    host = make_host(cache=INVERSION)
    calibrate(host)
    other = "sha256:" + "e" * 64
    host.put(PATH, capability.header_line(other) + "\n" + json.dumps(row(disk_key=other)) + "\n")
    code, checked, _ = run_cli(host, "check", "--json")
    check_doc = json.loads(checked)
    assert code in (0, 7) and "capability_history_identity_differs" in check_doc["warnings"]
    assert check_doc["capability"]["count"] == 0 and check_doc["capability"]["skipped_rows"] == 0
    code, observed, _ = run_cli(host, "capability", "--json")
    cap_doc = json.loads(observed)
    assert code == 0 and "capability_history_identity_differs" in cap_doc["warnings"]
    assert cap_doc["count"] == 0 and cap_doc["observations"] == []


def test_cch_09_repeat_and_delayed_read_evidence_are_advisory(monkeypatch):
    host = make_host(cache=INVERSION)
    original_render = host.fio.render
    job_rates = {}

    def render(job, sim):
        output, rate_log, hist_log = original_render(job, sim)
        forced = None
        if job.name.endswith("-ecold"):
            forced = 500_000_000.0  # QD32 reference, deliberately much higher than the QD1 reference
        elif job.name.endswith("-ecold-qd1"):
            forced = 100_000_000.0
        elif job.name.endswith("-cache-readback"):
            forced = 121_000_000.0
        elif job.name.endswith("-cache-repeat"):
            forced = 130_000_000.0
        if forced is not None:
            doc = json.loads(output)
            body = doc["jobs"][0][job.ddir]
            body["bw_bytes"] = int(forced)
            body["iops"] = forced / job.bs
            output = json.dumps(doc)
            job_rates[job.name] = float(body["bw_bytes"])
            if job.name.endswith("-ecold"):
                lines = []
                for line in rate_log.splitlines():
                    parts = [part.strip() for part in line.split(",")]
                    parts[1] = str(forced / job.bs)
                    lines.append(", ".join(parts))
                rate_log = "\n".join(lines) + "\n"
        return output, rate_log, hist_log

    monkeypatch.setattr(host.fio, "render", render)
    out = calibrate(host, profile="standard")
    read = out.calibration["calibration"]["cache"]["read"]
    delayed = read["delayed_readback"]
    repeat = read["repeat_pass"]
    assert delayed["idle_s"] == 30 and delayed["region_bytes"] > 0 and delayed["rate_bps"] > 0
    assert repeat["first_bps"] == delayed["rate_bps"] and repeat["second_bps"] > 0
    assert repeat["speedup_ratio"] == pytest.approx(repeat["second_bps"] / repeat["first_bps"])
    cold = read["e_cold"]
    qd1_cold_bps = cold["R_cold_qd1_bps"]
    qd32_cold_bps = cold["R_cold_iops"] * 4096
    assert qd1_cold_bps == 100_000_000 and qd32_cold_bps == pytest.approx(500_000_000)
    assert delayed["rate_bps"] > 1.2 * qd1_cold_bps and delayed["rate_bps"] < 1.2 * qd32_cold_bps
    assert out.result["quality"]["cache_state"]["delayed_readback"] == "hit-like"
    jobs = [job for job in fio_jobs(host) if job.name.endswith(("cache-readback", "cache-repeat"))]
    assert len(jobs) == 2 and jobs[0].name.endswith("cache-readback") and jobs[1].name.endswith("cache-repeat")
    assert repeat["first_bps"] == job_rates[jobs[0].name]
    assert repeat["second_bps"] == job_rates[jobs[1].name] and repeat["second_bps"] != repeat["first_bps"]
    assert out.result["quality"]["cache_state"]["hint"] in ("hit-like", "miss-like", "mixed", "undetermined")
    assert out.result["quality"]["plausibility"]["cache_state"] == read["cache_state"]
    assert out.result["proposals"]["iocost_model"] == out.calibration["calibration"]["proposals"]["iocost_model"]


def test_cch_09_read_hints_cover_hit_miss_conflict_and_missing_cells():
    measured = {"status": "measured", "rate_bps": 121}
    fast = {"status": "measured", "first_bps": 100, "second_bps": 130}
    slow = {"status": "measured", "first_bps": 100, "second_bps": 100}
    assert cache.read_cache_hints(measured, fast, 100)["hint"] == "hit-like"
    assert cache.read_cache_hints(measured, slow, 100)["hint"] == "mixed"
    assert cache.read_cache_hints(measured, {"status": "not_measured"}, 100)["hint"] == "hit-like"
    assert cache.read_cache_hints({"status": "measured", "rate_bps": 90}, slow, 100)["hint"] == "miss-like"
    assert cache.read_cache_hints(measured, slow, 100)["delayed_readback"] == "hit-like"
    assert cache.read_cache_hints(measured, slow, 120)["delayed_readback"] == "miss-like"
    # The caller supplies the QD1 cold rate. A QD1 delayed rate must not be
    # labelled miss-like only because a separate QD32 reference is higher.
    qd1_delayed = {"status": "measured", "rate_bps": 121}
    qd1_cold, qd32_cold = 100.0, 1000.0
    assert qd1_delayed["rate_bps"] < 1.2 * qd32_cold
    assert cache.read_cache_hints(qd1_delayed, {"status": "not_measured"}, qd1_cold)["delayed_readback"] == "hit-like"
    assert cache.read_cache_hints({"status": "not_measured"}, {"status": "not_measured"}, None)["hint"] == "undetermined"
    assert cache.read_cache_hints({"status": "not_measured"}, {"status": "not_measured"}, None)["status"] == "not_measured"
    assert cache.read_cache_hints({"status": "measured", "rate_bps": float("nan")},
                                  {"status": "measured", "first_bps": 0, "second_bps": 10}, 100)["hint"] == "undetermined"
    assert cache.read_cache_hints(measured, {"status": "not_measured"}, 100)["hint"] == "hit-like"


def test_cch_09_small_region_skips_and_write_proof_is_serialized():
    from iobench.cache import TimeProof
    from iobench.series import Reading
    from iobench.stages import StageSkipped
    from iobench.testing import R1002_SIZE

    run = cal_mod.Calibrator(make_host(cache=INVERSION), config())
    run.proof = {}
    disabled = cal_mod.Calibrator(make_host(cache=INVERSION), config(cache_idle_delay_s=0))
    disabled_alloc = plan.plan_calibration("standard", R1002_SIZE, 1.36e9, cache_idle_delay_s=0)
    disabled.cache_evidence_stage(disabled_alloc)
    assert disabled.stages["cache-evidence"] == "skipped: disabled"
    assert disabled.delayed_readback == {"status": "not_measured", "reason": "disabled"}
    run.fill_info = {"bytes": (1 << 20) - 1}
    with pytest.raises(StageSkipped, match="less than 1 MiB"):
        run.cache_evidence(0)

    run.proof["rewrite"] = TimeProof(True, None, 10, 2, 500, 5000)
    run.obs["write"] = Reading("steady", 100, 100, 2, 0, 10, False, 0.1, 8)
    doc = run.write_time_proof_doc()
    assert doc["post_cliff_bps"] == 409600 and doc["pre_cliff_bps"] == 409850
    run.proof["rewrite"] = TimeProof(False, "short_observation", 10, 0, 500, 0)
    assert run.write_time_proof_doc()["pre_cliff_bps"] is None
    run.obs.clear()
    doc = run.write_time_proof_doc()
    assert doc["post_cliff_bps"] is None and doc["pre_cliff_bps"] is None


def test_cch_09_idle_wait_honours_a_first_signal_within_one_tick():
    from iobench.errors import Aborted

    host = make_host(cache=INVERSION)
    run = cal_mod.Calibrator(host, config())
    run.fill_info = {"bytes": GIB}
    started = host.now()
    host.at(started + 0.5, lambda: host.deliver_signal("SIGTERM"))
    with pytest.raises(Aborted, match="signal SIGTERM"):
        run.cache_evidence(30)
    assert host.now() - started <= 1
    assert not any(job.name.endswith("cache-readback") for job in fio_jobs(host))


def test_cap_02_capability_validation_precedes_core_writes_and_store_failure_is_advisory(monkeypatch):
    host = make_host(cache=INVERSION)
    original_validate = capability.validate
    ceiling_path = f"{STATE}/ceilings.jsonl"
    foreign_digest = "sha256:" + "a" * 64
    foreign_history = ceilings.header_line(foreign_digest) + "\n"
    host.put(ceiling_path, foreign_history)

    def validate_before_writes(row, digest):
        assert not host.exists(f"{STATE}/result.json")
        assert not host.exists(f"{STATE}/calibration.json")
        assert host.read_text(ceiling_path) == foreign_history
        original_validate(row, digest)

    def fail_append(*args, **kwargs):
        raise OSError("capability store full")

    monkeypatch.setattr(capability, "validate", validate_before_writes)
    monkeypatch.setattr(capability, "append", fail_append)
    out = calibrate(host)
    assert out.status == "complete"
    expected = "capability_not_recorded:OSError"
    assert expected in out.calibration["quality"]["warnings"]
    assert expected in out.result["quality"]["warnings"]
    assert host.exists(f"{STATE}/result.json") and host.exists(f"{STATE}/calibration.json")
    assert any(path.startswith(ceiling_path + ".superseded-") for path in host.files)


def test_cap_02_invalid_capability_observation_does_not_abort_a_complete_run(monkeypatch):
    host = make_host(cache=INVERSION)
    original = capability.run_observation

    def invalid_observation(*args, **kwargs):
        observed = original(*args, **kwargs)
        observed["cliff_bytes"] = 0
        return observed

    monkeypatch.setattr(capability, "run_observation", invalid_observation)
    out = calibrate(host)
    expected = "capability_not_recorded:capability_number"
    assert out.status == "complete" and expected in out.calibration["quality"]["warnings"]
    assert host.exists(f"{STATE}/result.json") and host.exists(f"{STATE}/calibration.json")


def test_cap_02_a_failed_result_write_never_leaves_a_final_capability_row(monkeypatch):
    host = make_host(cache=INVERSION)
    real_write_json = res.write_json

    def fail_result_write(write_host, path, value):
        if path == f"{STATE}/result.json":
            raise OSError("injected result write failure")
        return real_write_json(write_host, path, value)

    monkeypatch.setattr(res, "write_json", fail_result_write)
    with pytest.raises(OSError, match="injected result write failure"):
        calibrate(host)
    assert not host.exists(PATH)


def test_cap_02_foreign_capability_rotation_failure_keeps_core_result(monkeypatch):
    host = make_host(cache=INVERSION)

    def fail_rotation(*args, **kwargs):
        raise OSError("capability history unavailable")

    monkeypatch.setattr(capability, "rotate_foreign", fail_rotation)
    out = calibrate(host)
    expected = "capability_not_recorded:OSError"
    assert out.status == "complete" and expected in out.result["quality"]["warnings"]
    assert host.exists(f"{STATE}/result.json") and host.exists(f"{STATE}/calibration.json")
    assert not host.exists(PATH)


def test_cap_02_warning_rewrite_failure_does_not_remove_completed_records(monkeypatch):
    host = make_host(cache=INVERSION)
    real_write_json = res.write_json
    writes = {}

    def fail_warning_rewrite(write_host, path, value):
        writes[path] = writes.get(path, 0) + 1
        if path in (f"{STATE}/result.json", f"{STATE}/calibration.json") and writes[path] > 1:
            raise OSError("state filesystem filled after final records")
        return real_write_json(write_host, path, value)

    def fail_append(*args, **kwargs):
        raise OSError("capability history unavailable")

    monkeypatch.setattr(res, "write_json", fail_warning_rewrite)
    monkeypatch.setattr(capability, "append", fail_append)
    out = calibrate(host)
    assert out.status == "complete" and "capability_not_recorded:OSError" in out.result["quality"]["warnings"]
    assert host.exists(f"{STATE}/result.json") and host.exists(f"{STATE}/calibration.json")
    assert not host.exists(PATH)


def test_slo_06_cache_evidence_defaults_and_configured_idle_delay():
    from iobench import plan
    from iobench import calibrate as cal_mod
    from iobench.errors import Refusal
    from iobench.testing import R1002_SIZE

    standard = plan.plan_calibration("standard", R1002_SIZE, 1.36e9)
    thorough = plan.plan_calibration("thorough", R1002_SIZE, 1.36e9)
    assert standard.seconds("cache-evidence") == 60 and standard.total <= standard.budget_s
    assert standard.stage("cache-evidence").status == "planned"
    assert thorough.seconds("cache-evidence") == 90 and thorough.total <= thorough.budget_s
    configured = plan.plan_calibration("standard", R1002_SIZE, 1.36e9, cache_idle_delay_s=15)
    assert configured.seconds("cache-evidence") == 45
    assert plan.plan_calibration("standard", R1002_SIZE, 1.36e9, cache_idle_delay_s=230).total == standard.budget_s
    assert plan.plan_calibration("thorough", R1002_SIZE, 1.36e9, cache_idle_delay_s=290).total == thorough.budget_s
    disabled = plan.plan_calibration("standard", R1002_SIZE, 1.36e9, cache_idle_delay_s=0)
    assert "cache-evidence" not in [stage.name for stage in disabled.stages]
    assert "cache-evidence" not in [stage.name for stage in plan.plan_calibration("quick", R1002_SIZE, 1.36e9).stages]
    for value in (-1, 231, 301, float("nan"), float("inf"), "30", True):
        with pytest.raises(Refusal):
            plan.plan_calibration("standard", R1002_SIZE, 1.36e9, cache_idle_delay_s=value)
    with pytest.raises(Refusal):
        plan.plan_calibration("thorough", R1002_SIZE, 1.36e9, cache_idle_delay_s=291)
    with pytest.raises(Refusal):
        plan.plan_calibration("quick", R1002_SIZE, 1.36e9, cache_idle_delay_s=5)
    host = make_host(cache=INVERSION)
    before = list(host.argv_log)
    with pytest.raises(Refusal):
        cal_mod.Calibrator(host, config(cache_idle_delay_s=231))
    assert host.argv_log == before  # invalid delay refused before the calibrator probes the host


def test_slo_06_cache_evidence_is_explicitly_budget_skipped_for_large_geometry():
    from iobench import guard

    small = plan.plan_calibration("standard", 435853320192, 1.36e9)
    large_size = 64 * 1024 * GIB
    large = plan.plan_calibration("thorough", large_size, 2e9)
    assert small.stage("cache-evidence").status == "planned"
    stage = large.stage("cache-evidence")
    assert stage.status == "skipped:budget" and stage.allotted_s == 0
    assert large.as_dict()["stages"][4]["status"] == "skipped:budget"

    host = make_host(size=large_size, cache=INVERSION)
    run = cal_mod.Calibrator(host, config(profile="thorough"))
    run.target = guard.load_target(host, "/dev/vda7")
    run.r_est = 2e9
    alloc = run.make_plan()
    run.fill_info = {"bytes": GIB}
    run.cache_evidence_stage(alloc)
    assert run.stages["cache-evidence"] == "skipped: budget"
    assert run.delayed_readback["status"] == "skipped:budget"
    assert run.repeat_pass["status"] == "skipped:budget"
    code, output, error = run_cli(host, "calibrate", "--target", "/dev/vda7", "--work-dir", "/bench/w", "--dry-run",
                                  "--profile", "thorough", "--assume-r-est", "2e9", "--json")
    assert code == 0, error
    plan_doc = json.loads(output)
    planned = next(stage for stage in plan_doc["stages"] if stage["name"] == "cache-evidence")
    assert planned["status"] == "skipped:budget" and planned["allotted_s"] == 0 and planned["jobs"] == []

