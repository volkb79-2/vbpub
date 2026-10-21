"""FakeHost and FakeFio (IOB-ARC-03): the in-memory estate every test runs on.

``FakeHost`` implements the ``Host`` protocol over an in-memory filesystem, a
sysfs/procfs tree, a device model (reserved tail, extent map, signatures, an
optional cache) and a fake clock.  ``FakeFio`` turns a parsed fio argv into fio
3.x JSON, rate logs and full latency histograms from a scenario.  The cache
simulates *content* (an LRU over 1 GiB buckets with a page-level filled
fraction, allocate-on-write / allocate-on-read flags, an optional second tier),
never a closed formula: the hit ratio of every job is computed from the
content at that moment and the jobs change it.
"""
from __future__ import annotations

import bisect
from itertools import accumulate
import json
import math
import posixpath
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .devio import HIST_BINS, PERCENTILES, FioJob, bin_value_ns, parse_job
from .extents import Extents
from .host import (TAIL_BYTES, HostError, RunResult, StatInfo, TargetRef, check_argv,
                   check_sysfs_path)

GIB = 1 << 30
PAGE = 4096


class TailViolation(HostError):
    """A writer touched the reserved tail (or the marker writer left it)."""


class FakeCrash(RuntimeError):
    """The scenario's crash-at-stage-N knob fired."""


# -- cache content model --------------------------------------------------------
class CacheModel:
    """LRU over 1 GiB buckets; each bucket holds a filled fraction in [0, 1].

    One tier by default.  With ``tier2_capacity_gib`` the most recently used
    ``capacity_gib`` of content is tier 1 (rate ``hit_iops``) and the rest of the
    ``tier2_capacity_gib`` total is tier 2 (rate ``tier2_iops``).
    """

    def __init__(self, capacity_gib: float, hit_iops: float, *, write_allocate: bool = True,
                 read_allocate: bool = False, tier2_capacity_gib: float | None = None,
                 tier2_iops: float | None = None, read_alloc_share: float = 1.0) -> None:
        self.cap1 = float(capacity_gib)
        self.total_cap = float(tier2_capacity_gib or capacity_gib)
        self.hit_iops = hit_iops
        self.tier2_iops = tier2_iops or hit_iops
        self.write_allocate = write_allocate
        self.read_allocate = read_allocate
        self.read_alloc_share = read_alloc_share
        self.order: OrderedDict[int, float] = OrderedDict()

    def tiers(self) -> dict[int, tuple[float, float]]:
        remaining = self.cap1
        out: dict[int, tuple[float, float]] = {}
        for b in reversed(self.order):
            f = self.order[b]
            in1 = min(f, remaining)
            remaining -= in1
            out[b] = (in1, f - in1)
        return out

    @staticmethod
    def _buckets(start: int, end: int):
        for b in range(start // GIB, (end - 1) // GIB + 1):
            lo, hi = max(start, b * GIB), min(end, (b + 1) * GIB)
            yield b, (hi - lo) / GIB

    def hit_fractions(self, start: int, end: int) -> tuple[float, float]:
        tiers = self.tiers()
        h1 = h2 = 0.0
        for b, ov in self._buckets(start, end):
            in1, in2 = tiers.get(b, (0.0, 0.0))
            h1 += ov * in1
            h2 += ov * in2
        span = (end - start) / GIB
        return h1 / span, h2 / span

    def _evict(self) -> None:
        excess = sum(self.order.values()) - self.total_cap
        while excess > 1e-9:
            b = next(iter(self.order))
            d = min(self.order[b], excess)
            self.order[b] -= d
            excess -= d
            if self.order[b] <= 1e-12:
                del self.order[b]

    def read(self, start: int, end: int, ios: float) -> None:
        span = (end - start) / GIB
        for b, ov in self._buckets(start, end):
            if self.read_allocate:
                f = self.order.get(b, 0.0)
                share = self.read_alloc_share * ios * ov / span / (GIB / PAGE)
                self.order[b] = 1 - (1 - f) * math.exp(-share)
            if b in self.order:
                self.order.move_to_end(b)
        self._evict()

    def write_seq(self, start: int, end: int) -> None:
        if not self.write_allocate:
            return
        for b, ov in self._buckets(start, end):
            self.order[b] = min(1.0, self.order.get(b, 0.0) + ov)
            self.order.move_to_end(b)
        self._evict()

    def write_random(self, start: int, end: int, ios: float) -> None:
        if not self.write_allocate:
            return
        span = (end - start) / GIB
        for b, ov in self._buckets(start, end):
            f = self.order.get(b, 0.0)
            share = ios * ov / span / (GIB / PAGE)
            self.order[b] = 1 - (1 - f) * math.exp(-share)
            self.order.move_to_end(b)
        self._evict()


# -- scenarios -------------------------------------------------------------------
@dataclass
class Bucket:
    """Token bucket on bytes/s: ``burst_bps`` while credit lasts, then ``sustained_bps``."""

    burst_bps: float
    sustained_bps: float
    burst_s: float
    refill: bool = True

    def __post_init__(self) -> None:
        self.capacity = self.burst_s * (self.burst_bps - self.sustained_bps)
        self.credit = self.capacity

    def step(self, demand: float, dt: float) -> float:
        """Average bytes/s over ``dt`` for the demanded rate; updates the credit."""
        if demand <= self.sustained_bps:
            gained = (self.sustained_bps - demand) * dt if self.refill else 0.0
            self.credit = min(self.capacity, self.credit + gained)
            return demand
        fast = min(demand, self.burst_bps)
        drain = fast - self.sustained_bps
        t_empty = self.credit / drain
        if t_empty >= dt:
            self.credit -= drain * dt
            return fast
        self.credit = 0.0
        return (fast * t_empty + self.sustained_bps * (dt - t_empty)) / dt


@dataclass
class DirScenario:
    """Behaviour of one direction (read or write) of the fake disk."""

    iops_max: float = 100_000.0
    qd_iops: dict[int, float] | None = None
    qd_half: float = 2.0
    c_byte_ns: float = 0.25
    seq_io_ratio: float = 0.2
    iops_cap: float | None = None
    bps_cap_rand: float | None = None
    bps_cap_seq: float | None = None
    lat_us: float = 300.0
    lat_hit_us: float | None = None
    lat_hit2_us: float | None = None
    fresh_penalty: float = 1.0
    fresh_penalty_seq: float = 1.0
    bucket: Bucket | None = None


@dataclass
class Scenario:
    read: DirScenario = field(default_factory=DirScenario)
    write: DirScenario = field(default_factory=lambda: DirScenario(lat_us=440.0))
    ceiling: Callable[[float], float] | None = None
    noise_cv: float = 0.0
    seed: int = 7
    sigma: float = 0.12
    load_latency: Callable[[float], float] = lambda rho: 1 + 9 * rho ** 4
    little_factor: float = 1.0
    zero_read_iops: float = 99_900.0
    # buffered work-dir load (drain / sweep model)
    drain_bps: float = 10_500_000.0
    dirty_peak_bytes: float = 200_000_000.0
    sweep_probe: Callable[[dict[str, str]], float] | None = None
    sweep_load_iops: float = 800.0


def _noise(seed: int, name: str, k: int) -> float:
    h = (seed * 1_000_003 + sum(map(ord, name)) * 7919 + k * 104_729) % 2_147_483_647
    h = (h * 48_271) % 2_147_483_647
    return (h / 2_147_483_647) * 2 - 1


# -- latency distributions -----------------------------------------------------
_BIN_US = [bin_value_ns(i) / 1000.0 for i in range(HIST_BINS)]
_PROFILES: dict[tuple[float, float], list[tuple[int, float]]] = {}


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def mode_profile(latency_us: float, sigma: float) -> list[tuple[int, float]]:
    key = (round(latency_us, 1), sigma)
    if key not in _PROFILES:
        mu = math.log(latency_us)
        lo = bisect.bisect_left(_BIN_US, math.exp(mu - 4 * sigma))
        hi = bisect.bisect_right(_BIN_US, math.exp(mu + 4 * sigma))
        raw = []
        for i in range(lo, hi):
            left = math.log(_BIN_US[i - 1]) if i > 0 and _BIN_US[i - 1] > 0 else mu - 8 * sigma
            right = math.log(_BIN_US[i + 1]) if i + 1 < HIST_BINS else mu + 8 * sigma
            a, b = (left + math.log(max(_BIN_US[i], 1e-9))) / 2, (right + math.log(max(_BIN_US[i], 1e-9))) / 2
            raw.append((i, _phi((b - mu) / sigma) - _phi((a - mu) / sigma)))
        total = sum(w for _, w in raw)
        _PROFILES[key] = [(i, w / total) for i, w in raw if w > 0]
    return _PROFILES[key]


def mixture(components: Sequence[tuple[float, float]], sigma: float) -> dict[int, float]:
    """``[(weight, latency_us), ...]`` -> sparse bin weights."""
    out: dict[int, float] = {}
    for weight, lat in components:
        if weight <= 0:
            continue
        for idx, w in mode_profile(lat, sigma):
            out[idx] = out.get(idx, 0.0) + weight * w
    return out


def percentile_us(weights: dict[int, float], q: float) -> float:
    idxs = sorted(weights)
    cum = list(accumulate(weights[i] for i in idxs))
    k = min(bisect.bisect_left(cum, q / 100.0 * cum[-1]), len(idxs) - 1)
    return _BIN_US[idxs[k]]


# -- devices ----------------------------------------------------------------------
class FakeDevice:
    """A block device or file target with a reserved tail and an extent map."""

    def __init__(self, path: str, size: int, *, kind: str = "device", devno: str | None = None,
                 partuuid: str | None = None, serial: str | None = None, wwn: str | None = None,
                 model: str | None = None, disk: str | None = None, fs_uuid: str | None = None,
                 inode: int | None = None) -> None:
        self.path, self.size, self.kind = path, size, kind
        self.devno, self.partuuid, self.serial, self.wwn, self.model = devno, partuuid, serial, wwn, model
        self.disk, self.fs_uuid, self.inode = disk, fs_uuid, inode
        self.signatures: list[str] = []
        self.written = Extents()
        self.marker: str | None = None
        self.discard_supported = True
        self.cache: CacheModel | None = None
        self.unwritten_reads: list[tuple[int, int]] = []
        self.log: list[tuple[str, int, int]] = []
        self.o_direct = True
        self.write_bytes = 0

    @property
    def tail_start(self) -> int:
        return self.size - TAIL_BYTES

    def check_range(self, offset: int, length: int, kind: str) -> None:
        end = offset + length
        if kind == "marker":
            if offset < self.tail_start or end > self.size:
                raise TailViolation(f"marker write [{offset}, {end}) leaves the reserved tail of {self.path}")
        elif offset < 0 or end > self.tail_start:
            raise TailViolation(f"{kind} [{offset}, {end}) overlaps the reserved tail of {self.path}")
        self.log.append((kind, offset, length))


# -- fio -----------------------------------------------------------------------
@dataclass
class Sim:
    samples: list[tuple[float, float, float]]  # (t, iops, bps)
    io_bytes: float
    runtime: float
    hist: list[tuple[float, dict[int, float]]]
    weights: dict[int, float]
    mean_us: float


class FakeFio:
    def __init__(self, scenario: Scenario) -> None:
        self.s = scenario

    # rate model -------------------------------------------------------------
    @staticmethod
    def _sat(d: DirScenario, job: FioJob) -> float:
        c_rand = max(1e6 / d.iops_max - PAGE * d.c_byte_ns / 1000, 0.01)
        c_io = c_rand * (1 if job.random else d.seq_io_ratio)
        return 1e6 / (c_io + job.bs * d.c_byte_ns / 1000)

    @staticmethod
    def _qd_factor(d: DirScenario, qd: int) -> float:
        if d.qd_iops and qd in d.qd_iops:
            return d.qd_iops[qd] / max(d.qd_iops.values())
        return min(1.0, (qd / (qd + d.qd_half)) / (32 / (32 + d.qd_half)))

    def _service_us(self, d: DirScenario, job: FioJob, h1: float, h2: float) -> float:
        base = d.lat_us + (job.bs - PAGE) * d.c_byte_ns / 1000
        if d.lat_hit_us is None:
            return base
        hit2 = d.lat_hit2_us or d.lat_hit_us
        return h1 * d.lat_hit_us + h2 * hit2 + (1 - h1 - h2) * base

    def _rate(self, d: DirScenario, job: FioJob, h1: float, h2: float, cache: CacheModel | None) -> float:
        if job.iodepth == 1:
            return 1e6 / self._service_us(d, job, h1, h2)
        sat = self._sat(d, job)
        if cache is not None and not job.writes:
            scale = sat / self._sat(d, job.with_(rw="randread", bs=PAGE))
            miss = 1 - h1 - h2
            sat = 1 / (h1 / (cache.hit_iops * scale) + h2 / (cache.tier2_iops * scale) + miss / sat)
        iops = sat * self._qd_factor(d, job.iodepth)
        caps = [iops]
        if d.iops_cap is not None:
            caps.append(d.iops_cap)
        bps_cap = d.bps_cap_rand if job.random else d.bps_cap_seq
        if bps_cap is not None:
            caps.append(bps_cap / job.bs)
        return min(caps)

    # simulation ----------------------------------------------------------------
    def simulate(self, job: FioJob, t0: float, dev: FakeDevice) -> Sim:
        d = self.s.write if job.writes else self.s.read
        dt = job.log_avg_ms / 1000.0
        n = math.ceil(job.runtime_s / dt)
        cache = dev.cache
        fresh = 0.0
        if job.writes:
            fresh = 1 - dev.written.overlap(job.offset, job.end) / job.size
        samples: list[tuple[float, float, float]] = []
        hist: list[tuple[float, dict[int, float]]] = []
        total_w: dict[int, float] = {}
        io_bytes = 0.0
        pos = job.offset
        weighted_mean = 0.0
        for k in range(n):
            lo, hi = k * dt, min((k + 1) * dt, job.runtime_s)
            span = hi - lo
            h1 = h2 = 0.0
            if cache is not None and not job.writes:
                h1, h2 = cache.hit_fractions(job.offset, job.end)
            iops = self._rate(d, job, h1, h2, cache)
            if job.writes and fresh:
                pen = d.fresh_penalty if job.random else d.fresh_penalty_seq
                iops /= 1 + fresh * (pen - 1)
            mult = self.s.ceiling(t0 + hi) if self.s.ceiling else 1.0
            if self.s.noise_cv:
                mult *= 1 + self.s.noise_cv * math.sqrt(3) * _noise(self.s.seed, job.name, k)
            iops *= mult
            rho = 1.0
            if job.rate_iops is not None:
                rho = min(1.0, job.rate_iops / iops)
                iops = min(iops, float(job.rate_iops))
            if d.bucket is not None:
                iops = d.bucket.step(iops * job.bs, span) / job.bs
            bps = iops * job.bs
            end_t = hi
            if job.io_size is not None and io_bytes + bps * span >= job.io_size:
                span = (job.io_size - io_bytes) / bps
                end_t = lo + span
            ios = iops * span
            samples.append((end_t, iops, bps))
            io_bytes += bps * span
            comps = self._components(d, job, h1, h2, iops, rho)
            mix = mixture(comps, self.s.sigma)
            weighted_mean += ios * sum(w * lat for w, lat in comps)
            for idx, w in mix.items():
                total_w[idx] = total_w.get(idx, 0.0) + w * ios
            if job.hist:
                parts = max(1, round(end_t - lo))
                for j in range(parts):
                    hist.append((lo + (j + 1) * (end_t - lo) / parts, {i: w * ios / parts for i, w in mix.items()}))
            self._touch_cache(job, cache, ios, bps * span, pos)
            if job.rw == "write":
                pos += bps * span
            if end_t < hi:
                break
        runtime = samples[-1][0]
        total_ios = sum(w for w in total_w.values())
        return Sim(samples, io_bytes, runtime, hist, total_w, weighted_mean / total_ios)

    def _components(self, d: DirScenario, job: FioJob, h1: float, h2: float, iops: float, rho: float) -> list[tuple[float, float]]:
        base = d.lat_us + (job.bs - PAGE) * d.c_byte_ns / 1000
        if d.lat_hit_us is None or job.writes:
            comps = [(1.0, base)]
        else:
            hit2 = d.lat_hit2_us or d.lat_hit_us
            comps = [(h1, d.lat_hit_us), (h2, hit2), (1 - h1 - h2, base)]
        mean_raw = sum(w * lat for w, lat in comps)
        if job.rate_iops is not None:
            factor = self.s.load_latency(rho)
        elif job.iodepth == 1:
            factor = 1.0
        else:
            factor = job.iodepth / iops * 1e6 * self.s.little_factor / mean_raw
        return [(w, lat * factor) for w, lat in comps]

    @staticmethod
    def _touch_cache(job: FioJob, cache: CacheModel | None, ios: float, nbytes: float, pos: float) -> None:
        if cache is None:
            return
        if job.writes:
            if job.random:
                cache.write_random(job.offset, job.end, ios)
            else:
                cache.write_seq(int(pos), int(min(pos + nbytes, job.end)))
        else:
            cache.read(job.offset, job.end, ios)

    # output formatting ------------------------------------------------------
    def render(self, job: FioJob, sim: Sim) -> tuple[str, str, str | None]:
        """fio JSON, rate log, histogram log (None when not requested)."""
        runtime = max(sim.runtime, 1e-9)
        iops = sim.io_bytes / job.bs / runtime
        pct = {f"{q:.6f}": percentile_us(sim.weights, q) * 1000.0 for q in PERCENTILES}
        body = {"io_bytes": int(sim.io_bytes), "bw_bytes": int(sim.io_bytes / runtime), "bw": int(sim.io_bytes / runtime / 1024),
                "iops": iops, "runtime": int(round(sim.runtime * 1000)),
                "clat_ns": {"mean": sim.mean_us * 1000.0, "N": int(sim.io_bytes / job.bs), "percentile": pct}}
        empty = {"io_bytes": 0, "bw_bytes": 0, "bw": 0, "iops": 0.0, "runtime": 0,
                 "clat_ns": {"mean": 0.0, "N": 0, "percentile": {f"{q:.6f}": 0 for q in PERCENTILES}}}
        doc = {"fio version": "fio-3.36", "jobs": [{"jobname": job.name, "read": empty if job.writes else body,
                                                    "write": body if job.writes else empty}]}
        ddir = 1 if job.writes else 0
        lines = []
        for t, i, b in sim.samples:
            value = int(round(i)) if job.metric == "iops" else int(round(b / 1024))
            lines.append(f"{int(round(t * 1000))}, {value}, {ddir}, {job.bs}, 0")
        hist = None
        if job.hist:
            hl = []
            for t, w in sim.hist:
                bins = [0] * HIST_BINS
                for idx, weight in w.items():
                    bins[idx] = int(round(weight))
                hl.append(f"{int(round(t * 1000))}, {ddir}, {job.bs}, " + ", ".join(map(str, bins)))
            hist = "\n".join(hl) + "\n"
        return json.dumps(doc), "\n".join(lines) + "\n", hist


# -- the host ------------------------------------------------------------------
@dataclass(frozen=True)
class SweepOutcome:
    probe_p99_us: float = 120_000.0
    load_iops: float = 800.0
    load_fsync_p99_us: float = 90_000.0
    drain_bps: float = 10_500_000.0


class FakeHandle:
    def __init__(self, host: "FakeHost", end: float, result: RunResult, sim: "Sim | None" = None) -> None:
        self.host, self.start, self.end = host, host.now(), end
        self.result, self.sim = result, sim
        self.on_finish: Callable[[float], None] | None = None
        self.killed = False
        self.finished = False

    def _finish(self) -> None:
        if not self.finished:
            self.finished = True
            if self.on_finish is not None:
                span = self.end - self.start
                self.on_finish(1.0 if span <= 0 else min(1.0, (self.host.now() - self.start) / span))

    def poll(self) -> int | None:
        if self.killed:
            return -9
        if self.host.now() >= self.end:
            self._finish()
            return self.result.returncode
        return None

    def wait(self) -> RunResult:
        if self.killed:
            return RunResult(-9, "", "killed")
        self.host.advance_to(self.end)
        self._finish()
        return self.result

    def kill(self) -> None:
        if not self.killed and self.poll() is None:
            self.killed = True
            self._finish()


class FakeHost:
    """In-memory Host: filesystem, sysfs/procfs, devices, clock, processes."""

    def __init__(self, scenario: Scenario | None = None, *, start: float = 1_792_550_400.0,
                 lock_registry: dict[str, "FakeHost"] | None = None) -> None:
        self.scenario = scenario or Scenario()
        self.fio = FakeFio(self.scenario)
        self.clock = start
        self.files: dict[str, str] = {}
        self.dirs: set[str] = {"/"}
        self.modes: dict[str, int] = {}
        self.symlinks: dict[str, str] = {}
        self.nlinks: dict[str, int] = {}
        self.devices: dict[str, FakeDevice] = {}
        self.dynamic: dict[str, Callable[[], str]] = {}
        self.mounts: list[tuple[str, str, str, str, str, str]] = []  # (ns, devno, mountpoint, source, fstype, opts)
        self.fs_space: dict[str, list[int]] = {}
        self.containers: list[str] = []
        self.active_units: set[str] = set()
        self.env: dict[str, str] = {}
        self.argv_log: list[list[str]] = []
        self.write_log: list[tuple[str, str]] = []
        self.odirect_unsupported: set[str] = set()
        self.sysfs_reject: set[str] = set()
        self.sysfs_swallow: set[str] = set()
        self.events: list[tuple[float, int, Callable[[], None]]] = []
        self.pending: str | None = None
        self.signals_installed = False
        self.locks = lock_registry if lock_registry is not None else {}
        self.holding: str | None = None
        self.fio_spawns = 0
        self.crash_after: int | None = None
        self.sweep_model: Callable[[dict[str, int]], SweepOutcome] = lambda knobs: SweepOutcome()
        self.work_disk = "vda"
        self.loads: list[tuple[float, float, float]] = []  # (start, end, device bps)
        self.disk_devno = "254:0"
        self._inode = 1000
        self._seq = 0
        self.dynamic["/proc/diskstats"] = self._diskstats
        self.dynamic["/proc/meminfo"] = self._meminfo
        self.dynamic["/proc/self/mountinfo"] = lambda: self._mountinfo("self")
        self.dynamic["/proc/1/mountinfo"] = lambda: self._mountinfo("init")

    # -- clock -------------------------------------------------------------
    def now(self) -> float:
        return self.clock

    def at(self, when: float, fn: Callable[[], None]) -> None:
        self._seq += 1
        self.events.append((when, self._seq, fn))
        self.events.sort(key=lambda e: (e[0], e[1]))

    def advance_to(self, target: float) -> None:
        while self.events and self.events[0][0] <= target:
            when, _, fn = self.events.pop(0)
            self.clock = max(self.clock, when)
            fn()
        self.clock = max(self.clock, target)

    def sleep(self, seconds: float) -> None:
        self.advance_to(self.clock + seconds)

    def token(self) -> str:
        return "a1b2"

    def getenv(self, name: str) -> str | None:
        return self.env.get(name)

    # -- filesystem --------------------------------------------------------
    def _resolve(self, path: str) -> str:
        for link, target in sorted(self.symlinks.items(), key=lambda kv: -len(kv[0])):
            if path == link or path.startswith(link + "/"):
                return self._resolve(target + path[len(link):])
        return path

    def realpath(self, path: str) -> str:
        return self._resolve(path)

    def put(self, path: str, text: str) -> None:
        path = self._resolve(path)
        self.files[path] = text
        d = posixpath.dirname(path)
        while d and d not in self.dirs:
            self.dirs.add(d)
            d = posixpath.dirname(d)

    def read_text(self, path: str) -> str:
        if path in self.dynamic:
            return self.dynamic[path]()
        real = self._resolve(path)
        if real not in self.files:
            raise FileNotFoundError(path)
        return self.files[real]

    def exists(self, path: str) -> bool:
        real = self._resolve(path)
        return real in self.files or real in self.dirs or real in self.devices or path in self.dynamic

    def listdir(self, path: str) -> list[str]:
        real = self._resolve(path).rstrip("/")
        names = set()
        for p in list(self.files) + list(self.dirs) + list(self.devices):
            if p.startswith(real + "/") and p != real:
                names.add(p[len(real) + 1:].split("/", 1)[0])
        if real not in self.dirs and not names:
            raise FileNotFoundError(path)
        return sorted(names)

    def stat(self, path: str) -> StatInfo | None:
        if path in self.symlinks:
            return StatInfo(0, 1, 1, False, False, True)
        real = self._resolve(path)
        if real in self.devices and self.devices[real].kind == "file":
            dev = self.devices[real]
            return StatInfo(dev.size, dev.inode or 0, self.nlinks.get(real, 1), False, True, False)
        if real in self.files:
            return StatInfo(len(self.files[real]), hash(real) & 0xFFFFFF, self.nlinks.get(real, 1), False, True, False)
        if real in self.dirs:
            return StatInfo(4096, 2, 2, True, False, False)
        return None

    def _mount_for(self, path: str) -> str:
        best = "/"
        for mp in self.fs_space:
            if (path == mp or path.startswith(mp.rstrip("/") + "/")) and len(mp) > len(best):
                best = mp
        return best

    def statfs(self, path: str) -> tuple[int, int]:
        total, free = self.fs_space[self._mount_for(path)]
        return total, free

    def consume(self, path: str, nbytes: int) -> None:
        self.fs_space[self._mount_for(path)][1] -= nbytes

    def makedirs(self, path: str) -> None:
        d = path
        while d and d not in self.dirs:
            self.dirs.add(d)
            d = posixpath.dirname(d)

    def probe_odirect(self, directory: str) -> bool:
        return self._mount_for(directory) not in self.odirect_unsupported

    def write_atomic(self, path: str, text: str, mode: int = 0o644) -> None:
        self.put(path, text)
        self.modes[path] = mode

    def append_line(self, path: str, line: str) -> None:
        self.put(path, self.files.get(path, "") + line.rstrip("\n") + "\n")

    def remove(self, path: str) -> None:
        self.files.pop(path, None)

    def write_sysfs(self, path: str, value: str) -> None:
        check_sysfs_path(path)
        if path in self.sysfs_reject:
            raise OSError(f"EINVAL writing {path}")
        self.write_log.append((path, value))
        real = self._resolve(path)
        if real not in self.files:
            raise FileNotFoundError(path)
        if path not in self.sysfs_swallow:
            self.files[real] = value + "\n"

    # -- sysfs / mounts / diskstats builders --------------------------------
    def add_disk(self, name: str, major: int, size: int, *, model: str | None = None, serial: str | None = None,
                 wwn: str | None = None, rotational: str = "1", scheduler: str = "none") -> None:
        base = f"/sys/devices/pci0/block/{name}"
        self.symlinks[f"/sys/class/block/{name}"] = base
        self.symlinks[f"/sys/dev/block/{major}:0"] = base
        self.put(f"{base}/dev", f"{major}:0\n")
        self.put(f"{base}/size", f"{size // 512}\n")
        for knob, value in (("nr_requests", "64"), ("max_sectors_kb", "1280"), ("max_hw_sectors_kb", "1280"),
                            ("rotational", rotational), ("scheduler", f"[{scheduler}] mq-deadline kyber")):
            self.put(f"{base}/queue/{knob}", value + "\n")
        self.put(f"{base}/inflight", "0 0\n")
        if serial is not None:
            self.put(f"{base}/device/serial", serial + "\n")
        if model is not None:
            self.put(f"{base}/device/model", model + "\n")
        if wwn is not None:
            self.put(f"{base}/wwid", wwn + "\n")
        self.disk_devno = f"{major}:0"
        self.work_disk = name

    def add_partition(self, disk: str, name: str, major: int, minor: int, size: int, partuuid: str | None,
                      *, path: str | None = None) -> FakeDevice:
        base = f"/sys/devices/pci0/block/{disk}/{name}"
        self.symlinks[f"/sys/class/block/{name}"] = base
        self.symlinks[f"/sys/dev/block/{major}:{minor}"] = base
        self.put(f"{base}/dev", f"{major}:{minor}\n")
        self.put(f"{base}/size", f"{size // 512}\n")
        self.put(f"{base}/partition", "7\n")
        dev = FakeDevice(path or f"/dev/{name}", size, devno=f"{major}:{minor}", partuuid=partuuid, disk=disk)
        self.devices[dev.path] = dev
        return dev

    def add_holder(self, device: str, holder: str, major: int, minor: int, size: int) -> None:
        base = f"/sys/devices/virtual/block/{holder}"
        self.put(f"{self._resolve('/sys/dev/block/' + str(self.devices[device].devno))}/holders/{holder}", "")
        self.symlinks[f"/sys/dev/block/{major}:{minor}"] = base
        self.symlinks[f"/sys/class/block/{holder}"] = base
        self.put(f"{base}/dev", f"{major}:{minor}\n")
        self.put(f"{base}/size", f"{size // 512}\n")
        self.devices[f"/dev/{holder}"] = FakeDevice(f"/dev/{holder}", size, devno=f"{major}:{minor}")

    def add_mount(self, devno: str, mountpoint: str, source: str, fstype: str = "ext4", options: str = "rw,relatime",
                  ns: str = "both") -> None:
        self.mounts.append((ns, devno, mountpoint, source, fstype, options))

    def _mountinfo(self, ns: str) -> str:
        lines = []
        for i, (mns, devno, mp, source, fstype, opts) in enumerate(self.mounts):
            if mns in ("both", ns):
                lines.append(f"{30 + i} 1 {devno} / {mp} {opts} - {fstype} {source} rw")
        return "\n".join(lines) + "\n"

    def set_fs(self, mountpoint: str, total: int, free: int) -> None:
        self.fs_space[mountpoint] = [total, free]

    def _written_bytes(self) -> float:
        total = 0.0
        for start, end, bps in self.loads:
            total += bps * max(0.0, min(self.clock, end) - start)
        return total

    def _diskstats(self) -> str:
        sectors = int(self._written_bytes() / 512)
        return f" 254 0 {self.work_disk} 100 0 2000 50 300 0 {sectors} 80 0 90 130\n"

    def _meminfo(self) -> str:
        sc = self.scenario
        dirty = 0.0
        for _start, end, _bps in self.loads:
            dirty = sc.dirty_peak_bytes if self.clock <= end else max(
                0.0, sc.dirty_peak_bytes - sc.drain_bps * (self.clock - end))
        return (f"MemTotal:        8126464 kB\nMemAvailable:    8126464 kB\nDirty: {int(dirty / 1024)} kB\n"
                "Writeback:             0 kB\n")

    def knobs(self) -> dict[str, int]:
        q = f"/sys/class/block/{self.work_disk}/queue/"
        return {
            "dirty_bytes": int(self.read_text("/proc/sys/vm/dirty_bytes")),
            "dirty_background_bytes": int(self.read_text("/proc/sys/vm/dirty_background_bytes")),
            "nr_requests": int(self.read_text(q + "nr_requests")),
            "max_sectors_kb": int(self.read_text(q + "max_sectors_kb")),
        }

    # -- processes -----------------------------------------------------------
    def _log(self, argv: Sequence[str]) -> None:
        check_argv(argv)
        self.argv_log.append(list(argv))

    def run(self, argv: Sequence[str], timeout: float | None = None) -> RunResult:
        self._log(argv)
        name = posixpath.basename(argv[0])
        if name == "fio" and list(argv[1:]) == ["--version"]:
            return RunResult(0, "fio-3.36\n", "")
        if name == "blkid":
            return self._blkid(argv[-1])
        if name == "wipefs":
            dev = self.devices[argv[-1]]
            lines = "".join(f"0x438,,,{sig}\n" for sig in dev.signatures)
            return RunResult(0, "# offset,uuid,label,type\n" + lines, "")
        if name == "swapon":
            out = "".join(f"{p} partition 1G\n" for p in self.swap_lines())
            return RunResult(0, out, "")
        if name == "fallocate" and argv[1] == "-l":
            return self._fallocate(argv[3], int(argv[2]))
        if name == "docker":
            return RunResult(0, "".join(c + "\n" for c in self.containers), "")
        if name == "systemctl":
            active = argv[2] in self.active_units
            return RunResult(0 if active else 3, "active\n" if active else "inactive\n", "")
        raise HostError(f"FakeHost.run: unsupported command {list(argv)!r}")

    def swap_lines(self) -> list[str]:
        return [line.split()[0] for line in self.files.get("/proc/swaps", "").splitlines()[1:]]

    def _blkid(self, path: str) -> RunResult:
        dev = self.devices[path]
        out = []
        if dev.partuuid:
            out.append(f"PART_ENTRY_UUID={dev.partuuid}")
        if dev.fs_uuid:
            out.append(f"UUID={dev.fs_uuid}")
        out += [f"TYPE={sig}" for sig in dev.signatures]
        return RunResult(0 if out else 2, "".join(line + "\n" for line in out), "")

    def _fallocate(self, path: str, size: int) -> RunResult:
        mp = self._mount_for(path)
        if self.fs_space[mp][1] < size:
            return RunResult(1, "", "No space left on device")
        self.consume(path, size)
        self._inode += 1
        source = next(e[3] for e in self.mounts if e[2] == mp)
        dev = FakeDevice(path, size, kind="file", inode=self._inode, fs_uuid=self.devices[source].fs_uuid)
        self.devices[path] = dev
        self.dirs.add(posixpath.dirname(path))
        return RunResult(0, "", "")

    def _verify(self, target: TargetRef) -> FakeDevice:
        dev = self.devices.get(target.path)
        if dev is None:
            raise HostError(f"no such target {target.path}")
        if dev.size != target.size_bytes:
            raise HostError(f"size changed: {target.path}")
        if dev.kind == "file":
            if dev.inode != target.inode or dev.fs_uuid != target.fs_uuid:
                raise HostError(f"file identity changed: {target.path}")
        elif dev.devno != target.devno or dev.partuuid != target.partuuid:
            raise HostError(f"device identity changed: {target.path}")
        return dev

    def _crash_check(self) -> None:
        self.fio_spawns += 1
        if self.crash_after is not None and self.fio_spawns > self.crash_after:
            raise FakeCrash(f"scenario crash at fio job {self.fio_spawns}")

    def _launch(self, job: FioJob, dev: FakeDevice) -> FakeHandle:
        sim = self.fio.simulate(job, self.clock, dev)
        out, rate_log, hist_log = self.fio.render(job, sim)
        self.put(job.log_path, rate_log)
        if hist_log is not None:
            self.put(job.hist_path, hist_log)
        return FakeHandle(self, self.clock + sim.runtime, RunResult(0, out, ""), sim)

    def spawn(self, argv: Sequence[str]) -> FakeHandle:
        self._log(argv)
        name = posixpath.basename(argv[0])
        if name == "systemd-run":
            return self._spawn_probe(argv)
        job = parse_job(argv)
        self._crash_check()
        dev = self.devices.get(job.filename)
        if dev is None:
            return self._spawn_workdir(job)
        if job.writes:
            raise HostError("writers of a target must use write_range")
        dev.check_range(job.offset, job.size, "read")
        if dev.written.overlap(job.offset, job.end) < job.size:
            dev.unwritten_reads.append((job.offset, job.size))
        return self._launch(job, dev)

    def write_range(self, argv: Sequence[str], target: TargetRef, offset: int, length: int) -> FakeHandle:
        self._log(argv)
        dev = self._verify(target)
        job = parse_job(argv)
        if job.filename != target.path or job.offset != offset or job.size != length:
            raise HostError("writer extent differs from the extent it was authorised for")
        self._crash_check()
        dev.check_range(offset, length, "fio")

        handle = self._launch(job, dev)
        assert handle.sim is not None

        def finish(fraction: float) -> None:
            done = int(handle.sim.io_bytes * fraction)
            dev.write_bytes += done
            dev.written.add(offset, offset + (length if job.random else min(length, done)))

        handle.on_finish = finish
        return handle

    def discard_range(self, argv: Sequence[str], target: TargetRef, offset: int, length: int) -> RunResult:
        self._log(argv)
        dev = self._verify(target)
        dev.check_range(offset, length, "discard")
        if not dev.discard_supported:
            return RunResult(1, "", "Operation not supported")
        dev.written.remove(offset, offset + length)
        return RunResult(0, "", "")

    def write_marker(self, target: TargetRef, text: str) -> None:
        dev = self._verify(target)
        dev.check_range(dev.tail_start, max(len(text), 1), "marker")
        dev.marker = text

    def read_marker(self, target: TargetRef) -> str:
        return self._verify(target).marker or ""

    def _spawn_workdir(self, job: FioJob) -> FakeHandle:
        sc = self.scenario
        outcome = self.sweep_model(self.knobs())
        n = math.ceil(job.runtime_s / (job.log_avg_ms / 1000))
        bps = outcome.drain_bps if job.fsync is None else outcome.load_iops * job.bs
        iops = bps / job.bs
        start = self.clock
        self.loads.append((start, start + job.runtime_s, outcome.drain_bps if job.fsync is None else 0.0))
        mean = 1e6 / max(iops, 1.0)
        sim = Sim([((k + 1) * job.log_avg_ms / 1000, iops, bps) for k in range(n)], bps * job.runtime_s, float(job.runtime_s),
                  [], mixture([(1.0, outcome.load_fsync_p99_us / 2.0)], sc.sigma), mean)
        out, rate_log, _ = self.fio.render(job, sim)
        self.put(job.log_path, rate_log)
        self.put(job.filename, "x")
        return FakeHandle(self, start + job.runtime_s, RunResult(0, out, ""), sim)

    def _spawn_probe(self, argv: Sequence[str]) -> FakeHandle:
        if not any(a.startswith("RuntimeMaxSec=") for a in argv):
            raise HostError("probe unit without RuntimeMaxSec")
        outpath, duration = argv[-3], float(argv[-2])
        outcome = self.sweep_model(self.knobs())
        n = int(duration / float(argv[-1]))
        low = outcome.probe_p99_us / 10
        lats = [low] * (n - 3) + [outcome.probe_p99_us, outcome.probe_p99_us, outcome.probe_p99_us * 1.5]
        self.put(outpath, "".join(f"{v}\n" for v in lats))
        return FakeHandle(self, self.clock + duration, RunResult(0, "", ""))

    # -- locks, signals --------------------------------------------------------
    def lock(self, path: str) -> bool:
        if path in self.locks and self.locks[path] is not self:
            return False
        self.locks[path] = self
        self.holding = path
        return True

    def unlock(self) -> None:
        if self.holding is not None:
            self.locks.pop(self.holding, None)
            self.holding = None

    def signal_pending(self) -> str | None:
        return self.pending

    def deliver_signal(self, name: str) -> None:
        self.pending = name

    def install_signals(self) -> None:
        self.signals_installed = True

    def devno(self, path: str) -> str | None:
        dev = self.devices.get(path)
        return dev.devno if dev is not None and dev.kind == "device" else None



R1002_SIZE = 435853320192
R1002_PARTUUID = "3e1f7a52-5b6d-4c1e-9a0d-7b2d0c1e0007"
ROOT_PARTUUID = "3e1f7a52-5b6d-4c1e-9a0d-7b2d0c1e0003"


def standard_host(scenario: Scenario | None = None, *, target_size: int = R1002_SIZE,
                  workdir_free: int = 20 * GIB, **kwargs: Any) -> FakeHost:
    """An r1002-shaped estate: root on vda3, a work-dir filesystem on vda5, a clean target vda7."""
    host = FakeHost(scenario, **kwargs)
    host.add_disk("vda", 254, 500 * GIB)
    root = host.add_partition("vda", "vda3", 254, 3, 40 * GIB, ROOT_PARTUUID)
    root.signatures.append("ext4")
    work = host.add_partition("vda", "vda5", 254, 5, 30 * GIB, "3e1f7a52-5b6d-4c1e-9a0d-7b2d0c1e0005")
    work.signatures.append("ext4")
    work.fs_uuid = "22222222-2222-2222-2222-222222222222"
    root.fs_uuid = "11111111-1111-1111-1111-111111111111"
    host.add_partition("vda", "vda7", 254, 7, target_size, R1002_PARTUUID)
    host.devices["/dev/vda"] = FakeDevice("/dev/vda", 500 * GIB, devno="254:0")
    host.add_mount("254:3", "/", "/dev/vda3")
    host.add_mount("254:5", "/bench", "/dev/vda5")
    host.set_fs("/", 40 * GIB, 20 * GIB)
    host.set_fs("/bench", 30 * GIB, workdir_free)
    host.put("/proc/swaps", "Filename Type Size Used Priority\n")
    for name, value in (("dirty_ratio", "20"), ("dirty_background_ratio", "10"), ("dirty_bytes", "0"),
                        ("dirty_background_bytes", "0")):
        host.put(f"/proc/sys/vm/{name}", value + "\n")
    host.makedirs("/bench")
    host.makedirs("/run")
    host.makedirs("/sys/fs/cgroup")
    host.put("/proc/sys/kernel/osrelease", "7.2.9\n")
    return host
