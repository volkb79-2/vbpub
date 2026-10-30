"""devio builders, parsers and the allowlist (IOB-ARC-05)."""
import pytest

from iobench import devio
from iobench.devio import FioJob


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
    text = '{"jobs": [{"read": {"iops": 10.0, "bw_bytes": 40960, "io_bytes": 409600, "runtime": 10000, "clat_ns": {"mean": 3000.0, "percentile": {"50.000000": 2000}}}}]}'
    st = devio.parse_fio_json(text, "read")
    assert st.iops == 10 and st.clat_mean_us == 3 and st.pct_us["50.000000"] == 2 and st.runtime_s == 10
    series = devio.parse_rate_log("2000, 100, 0, 4096, 0\n4000, 200, 0, 4096, 0\n", "iops")
    assert series == [(2.0, 100.0), (4.0, 200.0)]
    assert devio.parse_rate_log("1000, 4, 1, 4096, 0\n", "bw") == [(1.0, 4096.0)]
    assert devio.parse_hist_log("1000, 0, 4096, 1, 2, 3\n") == [(1.0, [1, 2, 3])]


def test_arc_05_bin_values():
    assert devio.bin_value_ns(5) == 5.0
    assert devio.bin_value_ns(127) == 127.0
    assert devio.bin_value_ns(128) == 128 + 0.5 * 2
    assert devio.bin_value_us(devio.HIST_BINS - 1) > 1e6
    assert devio.tail_start(1 << 30) == (1 << 30) - 1048576
