"""Shared test helpers (not a test module)."""
from __future__ import annotations

from iobench import devio, testing
from iobench.host import TargetRef
from iobench.testing import GIB, CacheModel, DirScenario, FakeHost, Scenario


def target_ref(host: FakeHost, path: str = "/dev/vda7") -> TargetRef:
    dev = host.devices[path]
    return TargetRef(dev.kind, path, dev.size, devno=dev.devno, partuuid=dev.partuuid,
                     fs_uuid=dev.fs_uuid, inode=dev.inode)


def job(rw="randread", *, off=0, size=GIB, bs=4096, qd=32, rt=30, io=None, metric="iops", hist=False,
        name="j", path="/dev/vda7", rate=None, avg=2000):
    return devio.FioJob(name, path, rw, bs, qd, rt, off, size, io, log_avg_ms=avg, metric=metric, hist=hist,
                        rate_iops=rate, prefix=f"/run/iobench/{name}",
                        exclusive=rw in ("write", "randwrite") and path.startswith("/dev/"))  # a device writer is always exclusive (IOB-BL-06 N-r3-3)


def run_job(host: FakeHost, j: devio.FioJob, path: str = "/dev/vda7") -> devio.FioResult:
    started = host.now()
    if j.writes:
        handle = host.write_range(j.argv(), target_ref(host, path), j.offset, j.size)
    else:
        handle = host.spawn(j.argv())
    return devio.collect(host, j, handle.wait(), started)


def cached_host(*, capacity=265, hit=92000, miss=9800, size=435853320192, **cache_kw) -> FakeHost:
    sc = Scenario(read=DirScenario(iops_max=miss, lat_us=760, lat_hit_us=110),
                  write=DirScenario(iops_max=71584, lat_us=440))
    host = testing.standard_host(sc, target_size=size)
    host.devices["/dev/vda7"].cache = CacheModel(capacity, hit, **cache_kw)
    return host


def fill(host: FakeHost, gib: float, *, bps: float = 1.36e9, path: str = "/dev/vda7"):
    host.scenario.write.bps_cap_seq = bps
    n = int(gib * GIB)
    j = job("write", off=0, size=n, bs=1 << 20, qd=16, rt=int(n / bps) + 10, io=n, metric="bw", name="fill", path=path)
    return run_job(host, j, path)


# -- full-run helpers ---------------------------------------------------------------------------
from iobench import calibrate as cal_mod  # noqa: E402
from iobench import guard, matrix as matrix_mod, stages  # noqa: E402
from iobench.testing import R1002_PARTUUID, R1002_SIZE  # noqa: E402

CREATED = "2026-10-21T03:00:00Z"
READ_MISS = dict(iops_max=9800, lat_us=760, lat_hit_us=110)
WRITE_14 = dict(iops_max=71584, lat_us=440, bps_cap_seq=1.36e9, bps_cap_rand=1.36e9)


def make_host(size: int = R1002_SIZE, *, cache: dict | None = None, read: dict | None = None, write: dict | None = None,
              noise: float = 0.02, **scenario_kw) -> FakeHost:
    sc = Scenario(read=DirScenario(**(read or READ_MISS)), write=DirScenario(**(write or WRITE_14)), noise_cv=noise, **scenario_kw)
    host = testing.standard_host(sc, target_size=size)
    if cache is not None:
        host.devices["/dev/vda7"].cache = CacheModel(**cache)
    host.dynamic["/sys/fs/cgroup/game-server.slice/io.pressure"] = lambda: "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    guard.init_device(host, "/dev/vda7", own=True, expect_size=size, expect_partuuid=R1002_PARTUUID, expect_serial=None, created=CREATED)
    return host


def config(**kw) -> stages.Config:
    base = dict(target="/dev/vda7", tool_version="0.1.0", work_dir="/bench/w", confirm_quiet_window=True,
                protect_cgroup="game-server.slice", abort_psi=40.0)
    base.update(kw)
    return stages.Config(**base)


def calibrate(host: FakeHost, **kw):
    return cal_mod.Calibrator(host, config(**kw)).execute()


def matrix(host: FakeHost, **kw):
    return matrix_mod.Matrixer(host, config(**kw)).execute()


def read_json(host: FakeHost, path: str):
    import json
    return json.loads(host.read_text(path))


INVERSION = dict(capacity_gib=265, hit_iops=92000, write_allocate=True)


def fio_jobs(host: FakeHost) -> list:
    """Every fio job (not ``fio --version``) the Host was asked to run, parsed back from its argv."""
    return [devio.parse_job(a) for a in host.argv_log if a[0] == "fio" and len(a) > 2]
