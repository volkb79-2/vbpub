"""IOB-PRF: profiles, --budget-minutes and --dry-run."""
import pytest

from iobench import dryrun, plan
from iobench.errors import Refusal

from helpers import INVERSION, calibrate, config, fio_jobs, make_host, read_json

GIB = 1 << 30


def test_prf_01_dry_run_plan_complete_and_writes_nothing():
    host = make_host(cache=INVERSION)
    files, dev = dict(host.files), host.devices["/dev/vda7"]
    written_before = (dev.write_bytes, list(dev.log), dev.written.as_list(), dev.marker)
    out = dryrun.calibrate_plan(host, config(assume_r_est=1.36e9))
    assert out["total_s"] == 3340 and out["profile"] == "standard" and out["r_est_bps"] == 1.36e9
    names = [s["name"] for s in out["stages"]]
    assert names == ["S0", "S1", "fill", "E-cold", "ladder", "ladder-qd", "E2", "fresh-vs-rewrite", "fresh-observation", "rearm",
                     "reference", "observation", "S6", "S7"]
    by = {s["name"]: s for s in out["stages"]}
    assert all("allotted_s" in s for s in out["stages"])
    for name in ("S1", "fill", "E-cold", "ladder", "ladder-qd", "E2", "fresh-vs-rewrite", "fresh-observation", "reference", "observation", "S6"):
        assert by[name]["jobs"], name
        for job in by[name]["jobs"]:
            assert job["argv"][0] == "fio" and "--name=" in " ".join(job["argv"])
    writers = [j for s in out["stages"] for j in s["jobs"] if "--rw=randwrite" in j["argv"] or "--rw=write" in j["argv"]]
    assert writers and all(isinstance(j["written_bytes_bound"], int) and j["written_bytes_bound"] > 0 for j in writers)
    readers = [j for s in out["stages"] for j in s["jobs"] if "--readonly" in j["argv"]]
    assert readers and all(j["written_bytes_bound"] is None for j in readers)
    assert by["S7"]["note"]
    # nothing was written anywhere: no state, no device bytes, no sysfs write, no fio job was started
    assert host.files == files and host.write_log == []
    assert (dev.write_bytes, list(dev.log), dev.written.as_list(), dev.marker) == written_before
    assert fio_jobs(host) == []
    # the matrix has a plan too, after a calibration
    calibrate(host)
    files = dict(host.files)
    mp = dryrun.matrix_plan(host, config())
    assert [s["name"] for s in mp["stages"]] == ["S0", "S3", "S4", "S5"] and mp["total_s"] == 30 + 90 + 480 + 50
    assert host.files == files


def test_prf_01_dry_run_needs_a_rate_that_is_never_invented():
    host = make_host(cache=INVERSION)
    with pytest.raises(Refusal) as err:
        dryrun.calibrate_plan(host, config())
    assert err.value.condition == "dry_run_rate" and "--assume-r-est" in str(err.value)
    calibrate(host)
    again = dryrun.calibrate_plan(host, config())  # now the S1 value is READ from calibration.json
    assert again["r_est_bps"] == pytest.approx(read_json(host, "/var/lib/iobench/calibration.json")["calibration"]["R_est_bps"])
    quick = dryrun.calibrate_plan(host, config(profile="quick"))
    assert quick["total_s"] <= 720 and "E-cold" not in [s["name"] for s in quick["stages"]]


def test_prf_01_budget_minutes_overrides_the_profile():
    host = make_host(cache=INVERSION)
    out = dryrun.calibrate_plan(host, config(assume_r_est=1.36e9, budget_minutes=30))
    assert out["budget_s"] == 1800
    assert out["ws_max_bytes"] == plan.ws_max(435853320192, 1.36e9, 1800)


def test_prf_02_quick_statuses():
    host = make_host(cache=INVERSION)
    out = calibrate(host, profile="quick")
    assert out.status == "complete" and "quick-profile" in out.result["quality"]["limits"]
    stages = out.result["run"]["stages"]
    assert "E-cold" not in stages and "E2" not in stages and "observation" not in stages
    caps = out.result["proposals"]["caps"]["dev_aggregate"]
    for key in ("RIOPS", "WIOPS"):
        assert caps[key]["status"] == "unproven" and "quick_profile" in caps[key]["reasons"]  # reported honestly, not absent
    assert caps["RIOPS"]["value"] > 0
    cal = out.calibration["calibration"]
    assert cal["cache"]["read"]["cache_state"] == "undetermined" and cal["cache"]["read"]["reasons"] == ["quick_profile"]
    assert cal["fresh_extent"] is None and cal["fresh_vs_rewrite"] == {"measured": False, "reason": "quick_profile"}
    assert out.calibration["run"]["budget_s"] == 720
