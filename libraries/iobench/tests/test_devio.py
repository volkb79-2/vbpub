"""devio builders, parsers and the allowlist (IOB-ARC-05)."""
import os

import pytest

from iobench import devio
from iobench.devio import FioJob
from iobench.errors import Refusal
from iobench.host import RealHost


def job(**kw):
    base = dict(name="j", filename="/dev/vda7", rw="randread", bs=4096, iodepth=32, runtime_s=30,
                offset=0, size=1 << 30, prefix="/run/iobench/j")
    base.update(kw)
    return FioJob(**base)


@pytest.mark.parametrize("kw", [
    {}, {"rw": "randwrite", "io_size": 1000}, {"rw": "write", "bs": 1 << 20, "metric": "bw"},
    {"hist": True, "iodepth": 1}, {"rate_iops": 500}, {"direct": False, "fsync": 16, "rw": "randwrite"},
])
def test_arc_05_job_argv_round_trips(kw):
    j = job(**kw)
    assert devio.parse_job(j.argv()) == j
    assert devio.is_allowed_argv(j.argv())


def test_arc_05_readers_are_readonly_and_flags():
    assert "--readonly" in job().argv()
    assert "--readonly" not in job(rw="write").argv()
    j = job(rw="randwrite")
    assert j.writes and j.random and j.ddir == "write" and j.end == j.size
    assert not job(rw="read").random and job(rw="read").ddir == "read"
    assert job().with_(bs=8192).bs == 8192
    assert job(hist=True).hist_path.endswith("_clat_hist.1.log")
    assert job(metric="bw").log_path.endswith("_bw.1.log")


def test_iob_bl_28_device_jobs_carry_allow_mounted_write_zero_only():
    guarded = job(rw="randwrite", device_guard=True)
    assert "--allow_mounted_write=0" in guarded.argv()
    assert devio.parse_job(guarded.argv()).device_guard is True
    assert "--allow_mounted_write=0" not in job(rw="randwrite").argv()
    assert devio.parse_job(job(rw="randwrite").argv()).device_guard is False
    assert not devio.is_allowed_argv([*job(rw="randwrite").argv(), "--allow_mounted_write=1"]), "iobench must never let fio permit a mounted write"


def test_iob_bl_28_the_capability_probe_uses_current_fio_output(tmp_path, monkeypatch):
    script = tmp_path / "fio"
    script.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = '--cmdhelp=allow_mounted_write' ]; then\n"
        "  if [ \"$FAKE_FIO_DOCUMENTS\" = 1 ]; then\n"
        "    printf '%20s: help text\\n' allow_mounted_write\n"
        "    exit 0\n"
        "  fi\n"
        "  echo 'No such command: allow_mounted_write' >&2\n"
        "  exit 1\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_FIO_DOCUMENTS", "1")
    devio.require_fio_option(RealHost(), "allow_mounted_write", (3, 39))
    monkeypatch.setenv("FAKE_FIO_DOCUMENTS", "0")
    with pytest.raises(Refusal) as err:
        devio.require_fio_option(RealHost(), "allow_mounted_write", (3, 39))
    assert err.value.condition == "fio_option_unsupported"
    assert "fio 3.39" in str(err.value) and "allow_mounted_write" in str(err.value)


def test_arc_05_bad_rw_and_not_fio():
    with pytest.raises(ValueError):
        job(rw="randrw").argv()
    with pytest.raises(ValueError):
        devio.parse_job(["dd", "--name=x"])


def test_arc_05_builders_allowed_and_forbidden():
    ok = [devio.fio_version_argv(), devio.blkdiscard_argv("/dev/vda7", 0, 4096), devio.blkid_argv("/dev/vda7"),
          devio.wipefs_check_argv("/dev/vda7"), devio.swapon_show_argv(), devio.fallocate_argv("/x", 10),
          devio.punch_hole_argv("/x", 0, 10), devio.docker_ps_argv(), devio.unit_active_argv("game-server"),
          devio.probe_unit_argv("iobench-probe-u", "iobench.slice", 120, __import__("iobench.sweep").sweep.PROBE_SOURCE, ["/d", "/o", "60", "0.5"])]
    for argv in ok:
        assert devio.is_allowed_argv(argv), argv
    bad = [["fstrim", "/"], ["mkfs.ext4", "/dev/x"], ["wipefs", "-a", "/dev/x"], ["mount", "/dev/x", "/m"],
           ["umount", "/m"], ["dd", "of=/dev/x"], ["systemctl", "set-property", "x", "IOReadBandwidthMax=1"],
           ["systemctl", "daemon-reload"], ["blkdiscard", "/dev/x"], ["fallocate", "-d", "x"],
           ["systemd-run", "python3"]]
    for argv in bad:
        assert not devio.is_allowed_argv(argv), argv


def test_arc_05_parse_outputs():
    text = '{"jobs": [{"read": {"iops": 10.0, "bw_bytes": 40960, "io_bytes": 409600, "runtime": 10000, "lat_ns": {"mean": 7000.0}, "clat_ns": {"mean": 3000.0, "percentile": {"50.000000": 2000}}}}]}'
    st = devio.parse_fio_json(text, "read")
    assert st.iops == 10 and st.clat_mean_us == 3 and st.lat_mean_us == 7 and st.pct_us["50.000000"] == 2 and st.runtime_s == 10
    submitted = '{"jobs": [{"write": {"iops": 1600.0, "bw_bytes": 6553600, "io_bytes": 4096000, "runtime": 10000, "slat_ns": {"mean": 621500.0}, "clat_ns": {"mean": 3500.0, "percentile": {"50.000000": 3200}}}}]}'
    st = devio.parse_fio_json(submitted, "write")
    assert st.slat_mean_us == 621.5 and st.lat_mean_us == 625.0 and st.clat_mean_us == 3.5
    clat_only = '{"fio version": "fio-3.36", "jobs": [{"read": {"iops": 10.0, "bw_bytes": 40960, "io_bytes": 409600, "runtime": 10000, "clat_ns": {"mean": 3000.0, "percentile": {"50.000000": 2000}}}}]}'
    missing = devio.parse_fio_json(clat_only, "read")
    assert missing.lat_mean_us is None and missing.slat_mean_us is None and missing.fio_version == "fio-3.36"
    series = devio.parse_rate_log("2000, 100, 0, 4096, 0\n4000, 200, 0, 4096, 0\n", "iops")
    assert series == [(2.0, 100.0), (4.0, 200.0)]
    assert devio.parse_rate_log("1000, 4, 1, 4096, 0\n", "bw") == [(1.0, 4096.0)]
    assert devio.parse_hist_log("1000, 0, 4096, 1, 2, 3\n") == [(1.0, [1, 2, 3])]


def test_arc_05_fake_fio_reports_distinct_completion_submission_and_total_latency():
    from helpers import job, run_job
    from iobench.testing import DirScenario, Scenario, standard_host

    host = standard_host(Scenario(write=DirScenario(iops_max=2600, qd_iops={1: 1600}, lat_us=3.5, slat_us=621.5)))
    host.devices["/dev/vda7"].written.add(0, 1 << 30)
    result = run_job(host, job("randwrite", size=1 << 30, qd=1, rt=10, name="slat-replay"))
    assert result.stats.clat_mean_us == pytest.approx(3.5)
    assert result.stats.slat_mean_us == pytest.approx(621.5)
    assert result.stats.lat_mean_us == pytest.approx(625.0)


def test_arc_05_fio_version_indicates_when_latency_fields_are_expected():
    assert not devio.fio_version_reports_latency(None)
    assert not devio.fio_version_reports_latency("unknown")
    assert not devio.fio_version_reports_latency("fio-3.29")
    assert devio.fio_version_reports_latency("fio-3.30")
    assert devio.fio_version_reports_latency("fio-3.36-12-gabcd")


def test_arc_05_bin_values():
    assert devio.bin_value_ns(5) == 5.0
    assert devio.bin_value_ns(127) == 127.0
    assert devio.bin_value_ns(128) == 128 + 0.5 * 2
    assert devio.bin_value_us(devio.HIST_BINS - 1) > 1e6
    assert devio.tail_start(1 << 30) == (1 << 30) - 1048576
