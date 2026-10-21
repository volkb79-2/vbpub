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
                        rate_iops=rate, prefix=f"/run/iobench/{name}")


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
