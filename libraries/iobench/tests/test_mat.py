"""IOB-MAT / IOB-DRN-01 / IOB-SWP-04 / IOB-RDC-01 at stage level: the matrix on a current calibration."""
import json

import pytest

from iobench import devio, plan, sweep, testing
from iobench.errors import Refusal

from helpers import (INVERSION, calibrate, config, fio_jobs, make_host, matrix, read_json)

GIB = 1 << 30
STATE = "/var/lib/iobench"


def sweep_model(p99=lambda k: 200_000.0, drain=10_500_000.0, iops=800.0):
    return lambda k: testing.SweepOutcome(probe_p99_us=p99(k), load_iops=iops, load_fsync_p99_us=90_000.0, drain_bps=drain)


@pytest.fixture
def calibrated():
    host = make_host(cache=INVERSION)
    assert calibrate(host).status == "complete"
    host.sweep_model = sweep_model()
    return host


def test_mat_01_requires_current_calibration():
    # absent
    host = make_host(cache=INVERSION)
    with pytest.raises(Refusal) as err:
        matrix(host)
    assert err.value.condition == "no_calibration" and err.value.exit_code == 2 and "absent" in str(err.value)
    assert host.write_log == [] and not host.exists(f"{STATE}/result.json")
    # corrupt
    host.put(f"{STATE}/calibration.json", "{ truncated")
    with pytest.raises(Refusal, match="corrupt"):
        matrix(host)
    # stale: the disk identity changed after the calibration
    ok = make_host(cache=INVERSION)
    calibrate(ok)
    ok.devices["/dev/vda7"].partuuid = "00000000-0000-0000-0000-00000000dead"
    with pytest.raises(Refusal) as err:
        matrix(ok)
    assert "stale" in str(err.value) or "marker" in str(err.value) or err.value.condition in ("no_calibration", "marker_partuuid_changed")
    # only a failed record exists
    failed = make_host(cache=INVERSION)
    failed.put(f"{STATE}/calibration.last-failed.json", json.dumps({"schema": 1, "schema_minor": 0, "run": {"status": "failed"}}))
    with pytest.raises(Refusal, match="aborted_only"):
        matrix(failed)


def test_mat_02_plan_budgets(calibrated):
    host = calibrated
    t0 = host.now()
    out = matrix(host)
    assert out.status == "complete"
    elapsed = host.now() - t0
    assert 500 <= elapsed <= plan.matrix_seconds(worst=True)  # typical 650 s, worst case 890 s
    assert out.result["run"]["profile"] == "matrix" and out.result["run"]["budget_s"] == 900
    assert list(out.result["run"]["stages"]) == ["S0", "S3", "S4", "S5"] and all(v == "ok" for v in out.result["run"]["stages"].values())
    cells = out.result["evidence"]["sweep"]["cells"]
    assert [c["name"] for c in cells] == ["REF", "dirty_bytes/2", "nr_requests*2", "max_sectors_kb+", "max_sectors_kb-", "REF2"]
    # the knob plan: A B A B cells of 45 s, no S3, typical 220 s, worst case 420 s
    knob_host = make_host(cache=INVERSION)
    calibrate(knob_host)
    knob_host.sweep_model = sweep_model()
    t1 = knob_host.now()
    out2 = matrix(knob_host, knob="max_sectors_kb", candidates=(64, 128), repeat=True)
    assert out2.status == "complete" and knob_host.now() - t1 < plan.knob_plan_seconds(2, True, worst=True) + 60
    assert [c["knobs"]["max_sectors_kb"] for c in out2.result["evidence"]["sweep"]["cells"]] == [64, 128, 64, 128]
    assert "S3" not in out2.result["run"]["stages"] and out2.result["run"]["budget_s"] == 480
    jobs = [j for j in fio_jobs(knob_host) if "-knob-" in j.name]
    assert jobs and {j.runtime_s for j in jobs} == {45}
    assert out2.result["evidence"]["sweep"]["decision"] is not None


def test_mat_03_reuses_calibration(calibrated):
    host = calibrated
    cal = read_json(host, f"{STATE}/calibration.json")
    cal_jobs = len(fio_jobs(host))
    out = matrix(host)
    res = out.result
    for group in ("caps", "iocost_model", "iocost_qos", "notes"):
        assert res["proposals"][group] == cal["calibration"]["proposals"][group]  # it MUST NOT recompute caps or the model
    assert res["run"]["calibration_id"] == cal["calibration"]["id"]
    new = fio_jobs(host)[cal_jobs:]
    assert new and not [j for j in new if j.filename == "/dev/vda7" and j.writes]  # the matrix never writes the target
    ext = cal["calibration"]["extent_written"][0][1]
    assert all(j.end <= ext for j in new if j.filename == "/dev/vda7")


def test_mat_04_recalibration_triggers(calibrated):
    host = calibrated
    # identity change -> refused (check exit 3 / stale)
    # recalibrate_recommended: the 12 h median of the ceiling history moved > 25 % in two consecutive windows -> warns and proceeds
    from iobench import ceilings
    cal = read_json(host, f"{STATE}/calibration.json")
    digest = ceilings.identity_digest(cal["identity"])
    plateau = cal["calibration"]["plateau_bps"]["write/rand4k"]
    now = host.now()
    for hours in (1, 3, 14, 20):
        ceilings.append(host, f"{STATE}/ceilings.jsonl", {"ts": ceilings.iso(now - hours * 3600), "dir": "write", "workload": "rand4k",
                                                           "plateau_bps": plateau * 0.5, "duration_s": 600, "source": "governor"}, digest)
    out = matrix(host)
    assert out.status == "complete" and "recalibrate_recommended" in out.result["run"]["warnings"]


def test_drn_01_drain_rate_and_consistency(calibrated):
    host = calibrated
    out = matrix(host)
    drain = out.result["evidence"]["drain"]
    assert drain["r_drain_bps"] == pytest.approx(10_500_000, rel=0.02) and drain["seconds"] == 60
    assert drain["consistency"]["verdict"] == "drain_below_ladder"  # 10 MB/s against a ladder write rate in the GB/s
    wb = out.result["proposals"]["writeback"]
    assert "drain_below_ladder" in wb["dirty_bytes"]["reasons"] and "drain_below_ladder" in wb["max_sectors_kb"]["reasons"]
    # consistent drain
    ok = make_host(cache=INVERSION)
    calibrate(ok)
    ladder_bps = read_json(ok, f"{STATE}/calibration.json")["calibration"]["cap_basis"]["bps"]["write"]["value"]
    ok.sweep_model = sweep_model(drain=ladder_bps)
    out2 = matrix(ok)
    assert out2.result["evidence"]["drain"]["consistency"]["consistent"]
    assert "drain_below_ladder" not in out2.result["proposals"]["writeback"]["dirty_bytes"].get("reasons", [])
    # the load: a buffered writer plus a 4 KiB random-write fsync=16 job, both in the work dir, volume bounded
    s3 = [j for j in fio_jobs(ok) if "-s3-" in j.name]
    assert len(s3) == 2 and all(not j.direct and j.filename.startswith("/bench/w/") for j in s3)
    assert {j.fsync for j in s3} == {None, 16} and all(j.io_size is not None for j in s3)


def test_swp_04_only_allowed_knobs(calibrated):
    host = calibrated
    before = len(host.write_log)
    matrix(host)
    paths = {p for p, _ in host.write_log[before:]}
    allowed = {"/proc/sys/vm/dirty_bytes", "/proc/sys/vm/dirty_background_bytes", "/sys/class/block/vda/queue/nr_requests",
               "/sys/class/block/vda/queue/max_sectors_kb", "/proc/sys/vm/dirty_ratio", "/proc/sys/vm/dirty_background_ratio"}
    assert paths <= allowed and paths
    assert not any("io.max" in p for p in paths)
    # every knob is back at its original value afterwards
    assert host.read_text("/proc/sys/vm/dirty_bytes") == "0\n" and host.read_text("/sys/class/block/vda/queue/max_sectors_kb") == "1280\n"
    assert host.read_text("/sys/class/block/vda/queue/nr_requests") == "64\n"
    # IOB-SAF-02 (review A-B2): the coupled ratio knobs come back too, not only the bytes knobs the matrix wrote
    assert host.read_text("/proc/sys/vm/dirty_ratio") == "20\n" and host.read_text("/proc/sys/vm/dirty_background_ratio") == "10\n"
    assert host.read_text("/proc/sys/vm/dirty_background_bytes") == "0\n"
    assert not host.exists("/run/iobench/restore.json")


def test_rdc_01_read_ratio(calibrated):
    host = calibrated
    # the read throughput follows max_sectors_kb: 64 KB requests read 0.27 GB/s against 1.3 GB/s at the hardware maximum
    real_spawn = host.spawn

    def spawn(argv):
        job = devio.parse_job(argv) if argv[0] == "fio" and len(argv) > 2 else None
        if job is not None and "-rdc-" in job.name and job.rw == "read" and job.direct:
            kb = int(host.read_text("/sys/class/block/vda/queue/max_sectors_kb"))
            host.scenario.read.bps_cap_seq = 1.3e9 * min(1.0, kb / 1280 * 3.2)
        return real_spawn(argv)

    host.spawn = spawn
    out = matrix(host)
    rc = out.result["evidence"]["read_cost"]
    assert rc["native_bps"] > 0 and len(rc["candidates"]) >= 1
    ratios = {c["max_sectors_kb"]: c["read_ratio"] for c in rc["candidates"]}
    assert all(0 < r <= 1.05 for r in ratios.values()) and ratios[64] == pytest.approx(0.16, abs=0.03) and ratios[1280] == pytest.approx(1.0, abs=0.03)
    assert rc["buffered"]["ratio"] > 0
    jobs = [j for j in fio_jobs(host) if "-rdc-" in j.name]
    direct = [j for j in jobs if j.direct]
    assert direct and all(j.bs == 1 << 20 and j.iodepth == 4 and j.runtime_s == sweep.RDC_CELL_S for j in direct)
    assert sweep.rdc_seconds(3) == 50
