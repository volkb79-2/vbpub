"""IOB-CAP: cache capability history, check output, and indirect read hints."""
import io
import json

import pytest
from cli_extended import CliIdentity

from iobench import cache, capability, ceilings, cli, result as res
from iobench.errors import Refusal

from helpers import INVERSION, calibrate, config, make_host

IDENTITY = CliIdentity("iobench", "0.1.0", "cache-proof disk benchmark", "iobench")
SCOPE = {"model": "M", "serial": "S1", "wwn": None, "size_bytes": 1 << 40, "strength": "strong",
         "machine_id_hash": None, "name": "vda"}
IDENT = {"target": {"kind": "device"}, "disk_scope": SCOPE}
DIGEST = ceilings.identity_digest(IDENT)
PATH = "/var/lib/iobench/capability.jsonl"
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
    assert capability.load(host, PATH, DIGEST) == ([row(), row(ts=ceilings.iso(T0 + 1), burst_bytes=2000.0)], [])

    other = "sha256:" + "f" * 64
    with pytest.raises(Refusal) as err:
        capability.append(host, PATH, row(disk_key=other), other)
    assert err.value.condition == "capability_identity_mismatch"
    aside = capability.rotate_foreign(host, PATH, other, "2026-11-07T12:00:00Z")
    assert aside == PATH + ".superseded-2026-11-07T120000Z"
    assert host.exists(aside) and not host.exists(PATH)
    assert capability.rotate_foreign(host, PATH, other, "2026-11-07T12:00:00Z") is None
    assert capability.load(host, "/missing/capability.jsonl", DIGEST) == ([], [])
    assert capability.load(host, aside, DIGEST) == ([row(), row(ts=ceilings.iso(T0 + 1), burst_bytes=2000.0)], [])
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
    assert capability.load(host, PATH, DIGEST) == ([], ["capability_history_corrupt"])
    host.put(PATH, json.dumps({"header": True, "schema": 99, "identity_digest": DIGEST}) + "\n")
    assert capability.load(host, PATH, DIGEST) == ([], ["capability_history_corrupt"])
    host.put(PATH, "")
    with pytest.raises(Refusal) as err:
        capability.append(host, PATH, row(), DIGEST)
    assert err.value.condition == "capability_history_corrupt"


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
    assert capability.load(host, PATH, DIGEST) == ([row()], [])
    host.put(PATH, capability.header_line(DIGEST) + "\n[]\n")
    assert capability.load(host, PATH, DIGEST) == ([], ["capability_history_corrupt"])
    aside = capability.rotate_foreign(host, PATH, DIGEST, "2026-11-07T13:00:00Z")
    assert aside == PATH + ".superseded-2026-11-07T130000Z" and host.exists(aside) and not host.exists(PATH)


def test_cap_01_since_and_latest_summary():
    rows = [row(), row(ts=ceilings.iso(T0 + 1), cliff_bytes=None)]
    assert capability.select_since(rows, None) == rows
    assert capability.select_since(rows, ceilings.iso(T0 + 1)) == rows[1:]
    assert capability.latest_summary([]) == {"count": 0, "latest": None}
    latest = capability.latest_summary(rows)
    assert latest == {"count": 2, "latest": rows[-1]}
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


def test_cap_02_cli_reads_history_and_check_shows_latest_without_writes():
    host = make_host(cache=INVERSION)
    calibration = calibrate(host)
    code, out, err = run_cli(host, "capability", "--json")
    assert code == 0, err
    history = json.loads(out)
    assert history["count"] == 1 and history["latest"]["profile"] == "standard"
    code, out, err = run_cli(host, "capability", "--since", history["latest"]["ts"], "--json")
    assert code == 0 and json.loads(out)["count"] == 1, err
    code, _, err = run_cli(host, "capability", "--since", "invalid", "--json")
    assert code == 2 and "timestamp" in err.lower()
    code, out, _ = run_cli(host, "check", "--json")
    checked = json.loads(out)
    assert checked["capability"]["latest"] == history["latest"]
    baseline_sections = checked["sections"]
    digest = ceilings.identity_digest(calibration.result["identity"])
    capability.append(host, PATH, {**history["latest"], "disk_key": digest, "ts": ceilings.iso(host.now() + 1), "burst_bytes": 1e30}, digest)
    before = list(host.write_log)
    code, out, _ = run_cli(host, "check", "--json")
    after = json.loads(out)
    assert after["capability"]["latest"]["burst_bytes"] == 1e30
    assert after["sections"] == baseline_sections
    assert host.write_log == before


def test_cch_09_repeat_and_delayed_read_evidence_are_advisory():
    host = make_host(cache=INVERSION)
    out = calibrate(host, profile="standard")
    read = out.calibration["calibration"]["cache"]["read"]
    delayed = read["delayed_readback"]
    repeat = read["repeat_pass"]
    assert delayed["idle_s"] == 30 and delayed["region_bytes"] > 0 and delayed["rate_bps"] > 0
    assert repeat["first_bps"] == delayed["rate_bps"] and repeat["second_bps"] > 0
    assert repeat["speedup_ratio"] == pytest.approx(repeat["second_bps"] / repeat["first_bps"])
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
    assert cache.read_cache_hints({"status": "not_measured"}, {"status": "not_measured"}, None)["hint"] == "undetermined"
    assert cache.read_cache_hints({"status": "measured", "rate_bps": float("nan")},
                                  {"status": "measured", "first_bps": 0, "second_bps": 10}, 100)["hint"] == "undetermined"
    assert cache.read_cache_hints(measured, {"status": "not_measured"}, 100)["hint"] == "hit-like"


def test_cch_09_small_region_skips_and_write_proof_is_serialized():
    from iobench import calibrate as cal_mod
    from iobench.cache import TimeProof
    from iobench import plan
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


def test_slo_06_cache_evidence_defaults_and_configured_idle_delay():
    from iobench import plan
    from iobench import calibrate as cal_mod
    from iobench.errors import Refusal
    from iobench.testing import R1002_SIZE

    standard = plan.plan_calibration("standard", R1002_SIZE, 1.36e9)
    thorough = plan.plan_calibration("thorough", R1002_SIZE, 1.36e9)
    assert standard.seconds("cache-evidence") == 60 and standard.total <= standard.budget_s
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

