"""Protected-side distress episodes: the missing 'stopping distance' data, recorded as a time series around each episode.

`iogov watch` samples procfs/cgroup files (no probe IO at all) once a second, keeps a short ring of pre-trigger samples and, when
a protected cgroup shows distress (PSI stall share, threads in D state), records the whole window until it has been quiet for
`episode_quiet_s`. Later analysis measures how long distress persisted and how long recovery took, e.g. around an operator
annotation such as "nr_requests 16 -> 8". Distress is an OBSERVATION about the protected workload, never a capacity claim.
"""

from __future__ import annotations

import glob
import os
from collections import deque

from .collectors import ContainerResolver, _read, parse_pressure
from .env import Env, StopRequested
from .gating import kill_switch_reason


class EpisodeDetector:
    def __init__(self, cfg):
        self.cfg = cfg
        pre = max(1, int(cfg.episode_pre_s / max(cfg.watch_interval_s, 0.01)))
        self.ring: deque = deque(maxlen=pre)
        self.active: list | None = None
        self.start_t = 0.0
        self.last_distress = 0.0

    def is_distress(self, some: float, full: float, dstate: int) -> bool:
        c = self.cfg
        return some >= c.episode_some_pct or full >= c.episode_full_pct or dstate >= c.episode_dstate

    def feed(self, t: float, some: float, full: float, dstate: int, sys_some: float) -> dict | None:
        sample = (t, some, full, dstate, sys_some)
        if self.active is None:
            if self.is_distress(some, full, dstate):
                self.active = list(self.ring) + [sample]
                self.start_t = t
                self.last_distress = t
            else:
                self.ring.append(sample)
            return None
        self.active.append(sample)
        if self.is_distress(some, full, dstate):
            self.last_distress = t
        quiet = t - self.last_distress >= self.cfg.episode_quiet_s
        too_long = t - self.start_t >= self.cfg.episode_max_s
        if quiet or too_long:
            return self._close(truncated=too_long and not quiet)
        return None

    def flush(self) -> dict | None:
        return self._close(truncated=True) if self.active is not None else None

    def _close(self, truncated: bool) -> dict:
        samples = self.active or []
        self.active = None
        self.ring.clear()
        start = self.start_t
        peak = max(range(len(samples)), key=lambda i: (samples[i][1], samples[i][2], samples[i][3]))
        step = max(1, len(samples) // self.cfg.episode_max_points)
        keep = sorted(set(range(0, len(samples), step)) | {peak, len(samples) - 1})
        series = [[round(samples[i][0] - start, 2), round(samples[i][1], 2), round(samples[i][2], 2), samples[i][3], round(samples[i][4], 2)]
                  for i in keep]
        return {
            "start": start, "end": samples[-1][0], "duration_s": round(self.last_distress - start, 2),
            "recovery_s": round(samples[-1][0] - self.last_distress, 2), "truncated": truncated,
            "peak_some_pct": round(max(s[1] for s in samples), 2), "peak_full_pct": round(max(s[2] for s in samples), 2),
            "peak_dstate": max(s[3] for s in samples), "series_columns": ["t_rel_s", "some_pct", "full_pct", "dstate", "sys_some_avg10"],
            "series": series,
        }


def dstate_count(procfs: str, cgroup_dirs: list[str]) -> int:
    n = 0
    for d in cgroup_dirs:
        threads = _read(os.path.join(d, "cgroup.threads"))
        for tid in (threads or "").split():
            stat = _read(os.path.join(procfs, tid, "stat"))
            if stat and ")" in stat and stat.rsplit(")", 1)[1].split()[:1] == ["D"]:
                n += 1
    return n


def protected_dirs(cfg, resolver=None) -> list[str]:
    dirs = []
    for pattern in cfg.protected_cgroups:
        dirs += sorted(glob.glob(pattern)) or [pattern]
    resolver = resolver or ContainerResolver(cfg)
    for path in resolver.resolve(cfg.containers).values():
        if path:
            dirs.append(path)
    return dirs


class ProtectedSampler:
    """Stall percentages from PSI `total` deltas of the protected cgroups (max over cgroups) + D-state threads + system avg10."""

    def __init__(self, cfg, env: Env):
        self.cfg, self.env = cfg, env
        self.prev: dict = {}
        self.resolver = ContainerResolver(cfg, env.mono)

    def sample(self) -> tuple[float, float, float, int, float]:
        cfg = self.cfg
        t = self.env.mono()
        some = full = 0.0
        dirs = protected_dirs(cfg, self.resolver)
        for d in dirs:
            p = parse_pressure(_read(os.path.join(d, "io.pressure")))
            if not p:
                continue
            tot = (p.get("some", {}).get("total", 0), p.get("full", {}).get("total", 0))
            old = self.prev.get(d)
            self.prev[d] = (t, tot)
            if old and t > old[0]:
                dt_us = (t - old[0]) * 1e6
                some = max(some, 100.0 * max(0, tot[0] - old[1][0]) / dt_us)
                full = max(full, 100.0 * max(0, tot[1] - old[1][1]) / dt_us)
        sysp = parse_pressure(_read(os.path.join(cfg.procfs_root, "pressure", "io")))
        sys_some = ((sysp or {}).get("some") or {}).get("avg10", 0.0)
        return t, some, full, dstate_count(cfg.procfs_root, dirs), sys_some


def watch(rt, max_seconds: float | None = None) -> int:
    """Resident sampler. Stops on kill switch, SIGTERM, or after max_seconds (tests / one-shot capture). Returns episodes recorded."""

    cfg, env = rt.cfg, rt.env
    sampler, det = ProtectedSampler(cfg, env), EpisodeDetector(cfg)
    restore = env.install_signal_handlers()
    t0 = env.mono()
    count = 0
    epoch = None
    try:
        sampler.sample()  # prime the deltas
        while True:
            env.stop.check()
            if kill_switch_reason(cfg):
                break
            env.sleep(cfg.watch_interval_s)
            ep = det.feed(*sampler.sample())
            if ep:
                count += _emit(rt, ep)
            if max_seconds is not None and env.mono() - t0 >= max_seconds:
                break
    except StopRequested:
        pass
    finally:
        ep = det.flush()
        if ep:
            count += _emit(rt, ep)
        restore()
    return count


def _emit(rt, ep: dict) -> int:
    from .collectors import settings_epoch
    ep["settings_epoch"] = settings_epoch(rt.collector.settings())
    rt.journal.append("harm", kind="episode", evidence=ep)
    return 1
