"""IOB-BL-11/13/14/16: the refusals and cleanups that used to be tracebacks or leaks."""
import pytest

from iobench import host as host_mod
from iobench.errors import Refusal, RunFailed

from iobench.matrix import Matrixer

from helpers import calibrate, config, matrix
from test_mat import calibrated  # noqa: F401  (fixture)
from test_tgt import file_host


def test_bl16_a_vanished_diskstats_row_fails_the_run_not_a_stop_iteration(calibrated):
    host = calibrated
    host.dynamic["/proc/diskstats"] = lambda: "   8  0 sdz 1 0 8 0 0 0 0 0 0 0 0\n"
    out = matrix(host)
    assert out.status == "failed" and "no row for" in out.message
    m = Matrixer(host, config())
    m.workdir = type("W", (), {"disk_name": "sda"})()
    with pytest.raises(RunFailed, match="no row for sda"):
        m.written_bytes()


def test_bl11_a_file_target_on_a_filesystem_without_a_block_device_is_refused_before_any_job():
    host = file_host()
    host.mounts = [(ns, "0:99" if mp == "/bench" else dev, mp, src, "overlay" if mp == "/bench" else fs, opts)
                   for ns, dev, mp, src, fs, opts in host.mounts]
    before = len(host.argv_log)
    with pytest.raises(Refusal) as err:
        calibrate(host, target="/bench/target.img", work_dir="/bench/w", installer=True)
    assert err.value.condition == "target_fs_disk_unknown"
    assert not any(a[0] == "fio" and len(a) > 2 for a in host.argv_log[before:])
    assert not host.exists("/run/iobench/restore.json")  # the lock/journal were not left behind


class _Popen:
    def __init__(self, argv, **kw):
        self.pid, self.returncode = 4242, 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def test_bl14_kill_closes_the_temporary_files(monkeypatch):
    monkeypatch.setattr(host_mod.subprocess, "Popen", _Popen)
    monkeypatch.setattr(host_mod, "_PRCTL", object())
    handle = host_mod._ProcHandle(["true"])
    out, err = handle._out, handle._err
    handle.kill()
    assert out.closed and err.closed
    handle.kill()  # idempotent
