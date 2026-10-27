"""Passive collectors: pressure (PSI), /sys/block stat and inflight, cgroup io.stat / io.pressure, settings epoch.

Everything here is a plain read of procfs/sysfs/cgroup files. Paths are rooted in the config so tests can point them
at a temp tree. A missing file yields None/empty, never an exception: a collector must not take a run down.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import time

STAT_FIELDS = (
    "r_ios", "r_merges", "r_sectors", "r_ticks", "w_ios", "w_merges", "w_sectors", "w_ticks",
    "in_flight", "io_ticks", "time_in_queue", "d_ios", "d_merges", "d_sectors", "d_ticks", "f_ios", "f_ticks",
)


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def parse_pressure(text: str | None) -> dict | None:
    """'some avg10=0.00 avg60=0.01 avg300=0.00 total=123' lines -> {'some': {...}, 'full': {...}}."""

    if not text:
        return None
    out: dict = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] not in ("some", "full"):
            continue
        kv = {}
        for item in parts[1:]:
            if "=" in item:
                k, v = item.split("=", 1)
                try:
                    kv[k] = float(v) if k != "total" else int(v)
                except ValueError:
                    pass
        out[parts[0]] = kv
    return out or None


def parse_block_stat(text: str | None) -> dict | None:
    if not text:
        return None
    nums = text.split()
    try:
        values = [int(n) for n in nums]
    except ValueError:
        return None
    return dict(zip(STAT_FIELDS, values))


def parse_io_stat(text: str | None) -> dict:
    """cgroup io.stat: 'MAJ:MIN rbytes=.. wbytes=.. rios=.. wios=.. dbytes=.. dios=..' -> {dev: {key: int}}."""

    out: dict = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if not parts or ":" not in parts[0]:
            continue
        kv = {}
        for item in parts[1:]:
            if "=" in item:
                k, v = item.split("=", 1)
                try:
                    kv[k] = int(v)
                except ValueError:
                    try:
                        kv[k] = float(v)
                    except ValueError:
                        pass
        out[parts[0]] = kv
    return out


VMSTAT_KEYS = ("pswpin", "pswpout", "pgmajfault", "zswpin", "zswpout", "workingset_refault_anon")


def read_vmstat(procfs: str, keys: tuple = VMSTAT_KEYS) -> dict | None:
    """The swap/reclaim counters of /proc/vmstat (pages or events, cumulative): the host-wide signature of a memory-driven stall."""

    text = _read(os.path.join(procfs, "vmstat"))
    if not text:
        return None
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in keys:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out or None


def read_loadavg(procfs: str) -> tuple[float, float, float] | None:
    text = _read(os.path.join(procfs, "loadavg"))
    if not text:
        return None
    try:
        a, b, c = text.split()[:3]
        return float(a), float(b), float(c)
    except ValueError:
        return None


class ContainerResolver:
    """Container name -> cgroup dir. Explicit `container_cgroups` paths win (preferred: config.v2.json also holds container
    environment variables, i.e. secrets, so reading it widens what a compromised service could see). Otherwise the daemon's
    config.v2.json files are parsed once and re-parsed only when their mtime changes; the directory listing and the cgroup
    path discovery are cached for `rescan_s` and a cached path is revalidated with one stat."""

    def __init__(self, cfg, mono=time.monotonic, rescan_s: float = 60.0):
        self.cfg, self.mono, self.rescan_s = cfg, mono, rescan_s
        self._parsed: dict[str, tuple] = {}  # config file -> (mtime_ns, name, container id)
        self._listed_at = -1e18
        self._files: list[str] = []
        self._paths: dict[str, tuple[str | None, float]] = {}

    def _scan(self) -> None:
        now = self.mono()
        if now - self._listed_at < self.rescan_s:
            return
        self._listed_at = now
        self._files = sorted(glob.glob(os.path.join(self.cfg.docker_containers_dir, "*", "config.v2.json")))
        self._parsed = {f: v for f, v in self._parsed.items() if f in self._files}
        for f in self._files:
            try:
                mt = os.stat(f).st_mtime_ns
            except OSError:
                continue
            if f in self._parsed and self._parsed[f][0] == mt:
                continue
            name = None
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and isinstance(data.get("Name"), str):
                    name = data["Name"].lstrip("/")
            except (OSError, ValueError):
                pass
            self._parsed[f] = (mt, name, os.path.basename(os.path.dirname(f)))

    def _discover(self, cid: str) -> str | None:
        root = self.cfg.cgroup_root
        for cand in (f"system.slice/docker-{cid}.scope", f"docker/{cid}", f"docker-{cid}.scope"):
            path = os.path.join(root, cand)
            if os.path.isdir(path):
                return path
        hits = glob.glob(os.path.join(root, "*", f"docker-{cid}.scope")) or glob.glob(os.path.join(root, "**", f"docker-{cid}.scope"), recursive=True)
        return hits[0] if hits else None

    def resolve(self, names) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        todo = []
        for n in names:
            if n in self.cfg.container_cgroups:
                out[n] = self.cfg.container_cgroups[n]
            else:
                todo.append(n)
        if todo:
            self._scan()
            by_name = {v[1]: v[2] for v in self._parsed.values() if v[1]}
            now = self.mono()
            for n in todo:
                cached = self._paths.get(n)
                if cached and cached[0] and os.path.isdir(cached[0]):
                    out[n] = cached[0]
                    continue
                if cached and now - cached[1] < self.rescan_s:
                    out[n] = cached[0]
                    continue
                cid = by_name.get(n)
                path = self._discover(cid) if cid else None
                self._paths[n] = (path, now)
                out[n] = path
        return out


class Collector:
    def __init__(self, cfg, disks, env):
        self.cfg = cfg
        self.disks = disks
        self.env = env
        self.resolver = ContainerResolver(cfg, env.mono)

    # -- single reads -------------------------------------------------------------------------------------
    def system_pressure(self, resource: str = "io") -> dict | None:
        return parse_pressure(_read(os.path.join(self.cfg.procfs_root, "pressure", resource)))

    def device_stat(self, disk) -> dict | None:
        stat = parse_block_stat(_read(os.path.join(disk.sys_path, "stat")))
        inflight = _read(os.path.join(disk.sys_path, "inflight"))
        if stat is not None and inflight:
            try:
                r, w = inflight.split()[:2]
                stat["inflight_r"], stat["inflight_w"] = int(r), int(w)
            except ValueError:
                pass
        return stat

    def cgroup_targets(self) -> dict:
        """{'governed': {path: {...}}, 'protected': {...}, 'containers': {...}} with per-cgroup io.stat/io.pressure."""

        cfg = self.cfg
        targets: dict = {"governed": {}, "protected": {}, "containers": {}}
        for group, paths in (("governed", cfg.governed_cgroups), ("protected", cfg.protected_cgroups)):
            for pattern in paths:
                for path in sorted(glob.glob(pattern)) or [pattern]:
                    targets[group][path] = self._cgroup(path)
        targets["others"] = self.others(targets)
        resolved = self.resolver.resolve(cfg.containers)
        for name, path in resolved.items():
            targets["containers"][name] = self._cgroup(path) if path else None
        return targets

    def others(self, targets: dict) -> dict:
        """'Who else writes': the root cgroup and every top-level cgroup that is neither governed nor protected."""

        root = self.cfg.cgroup_root
        known = {os.path.realpath(p) for g in ("governed", "protected") for p in targets[g]}
        out = {}
        if os.path.exists(os.path.join(root, "io.stat")):
            out["<root>"] = self._cgroup(root)
        for path in sorted(glob.glob(os.path.join(root, "*", "io.stat"))):
            d = os.path.dirname(path)
            if os.path.realpath(d) in known:
                continue
            if os.path.basename(d) in self.cfg.exclude_others:
                continue
            out[os.path.basename(d)] = self._cgroup(d)
        return out

    @staticmethod
    def _cgroup(path: str) -> dict | None:
        io_stat = _read(os.path.join(path, "io.stat"))
        pressure = _read(os.path.join(path, "io.pressure"))
        if io_stat is None and pressure is None:
            return None
        return {"io_stat": parse_io_stat(io_stat), "pressure": parse_pressure(pressure)}

    def snapshot(self) -> dict:
        return {
            "t": self.env.mono(),
            "pressure": self.system_pressure(),
            "pressure_memory": self.system_pressure("memory"),  # memory/swap reclaim is a stall driver the IO PSI alone cannot name
            "vmstat": read_vmstat(self.cfg.procfs_root),
            "loadavg": read_loadavg(self.cfg.procfs_root),
            "devices": {d.kname: self.device_stat(d) for d in self.disks},
            "cgroups": self.cgroup_targets(),
        }

    # -- settings epoch -----------------------------------------------------------------------------------
    def settings(self) -> dict:
        """Read-only view of the knobs whose change can move a passive series. Hashed into `settings_epoch`."""

        out: dict = {"queue": {}, "cgroup": {}}
        for d in self.disks:
            q = os.path.join(d.sys_path, "queue")
            entry = {}
            for key in ("nr_requests", "max_sectors_kb", "rotational", "logical_block_size", "physical_block_size"):
                v = _read(os.path.join(q, key))
                if v is not None:
                    entry[key] = v.strip()
            sched = _read(os.path.join(q, "scheduler"))
            if sched is not None:
                m = re.search(r"\[([^\]]+)\]", sched)
                entry["scheduler"] = m.group(1) if m else sched.strip()
            out["queue"][d.kname] = entry
        root = self.cfg.cgroup_root
        for name in ("io.cost.model", "io.cost.qos"):
            v = _read(os.path.join(root, name))
            if v is not None:
                out["cgroup"][name] = v.strip()
        for pattern in self.cfg.governed_cgroups:
            for path in sorted(glob.glob(pattern)) or [pattern]:
                for name in ("io.max", "io.weight"):
                    v = _read(os.path.join(path, name))
                    if v is not None:
                        out["cgroup"][f"{path}/{name}"] = v.strip()
        return out


def settings_epoch(settings: dict) -> str:
    blob = json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


# ---------------------------------------------------------------------------------------------------------
# deltas between two snapshots
# ---------------------------------------------------------------------------------------------------------


def cgroup_rates(a: dict | None, b: dict | None, dt: float) -> dict | None:
    if not a or not b or dt <= 0:
        return None
    totals = {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0}
    for dev, cur in b["io_stat"].items():
        prev = a["io_stat"].get(dev)
        if not prev:
            continue
        for key in totals:
            totals[key] += max(0, cur.get(key, 0) - prev.get(key, 0))
    return {
        "r_bps": totals["rbytes"] / dt, "w_bps": totals["wbytes"] / dt,
        "r_iops": totals["rios"] / dt, "w_iops": totals["wios"] / dt,
    }


def governed_write_bps(a: dict, b: dict) -> float | None:
    """Sum of the governed cgroups' write rate between two snapshots (None if nothing could be read)."""

    dt = b["t"] - a["t"]
    total, seen = 0.0, False
    for path, cur in b["cgroups"]["governed"].items():
        rates = cgroup_rates(a["cgroups"]["governed"].get(path), cur, dt)
        if rates:
            total += rates["w_bps"]
            seen = True
    return total if seen else None


def device_delta(a: dict | None, b: dict | None, dt: float) -> dict | None:
    if not a or not b or dt <= 0:
        return None
    d = {k: max(0, b[k] - a[k]) for k in ("r_ios", "r_sectors", "r_ticks", "w_ios", "w_sectors", "w_ticks", "io_ticks")}
    return {
        "r_iops": d["r_ios"] / dt,
        "w_iops": d["w_ios"] / dt,
        "r_bps": d["r_sectors"] * 512 / dt,
        "w_bps": d["w_sectors"] * 512 / dt,
        "r_await_ms": (d["r_ticks"] / d["r_ios"]) if d["r_ios"] else None,
        "w_await_ms": (d["w_ticks"] / d["w_ios"]) if d["w_ios"] else None,
        "busy_pct": min(100.0, d["io_ticks"] / (dt * 10.0)),
        "inflight": b.get("in_flight"),
    }


def passive_summary(a: dict, b: dict, top_n: int = 8) -> dict:
    dt = b["t"] - a["t"]
    cg = {}
    for group in ("governed", "protected", "containers"):
        for path, cur in b["cgroups"][group].items():
            prev = a["cgroups"][group].get(path)
            cg[f"{group}:{path}"] = {
                "rates": cgroup_rates(prev, cur, dt),
                "pressure": (cur or {}).get("pressure"),
            }
    others = {}
    root_w = None
    for name, cur in b["cgroups"].get("others", {}).items():
        if name == "<root>":  # the root io.stat is the system TOTAL, not an 'other' writer
            rr = cgroup_rates(a["cgroups"].get("others", {}).get(name), cur, dt)
            root_w = round(rr["w_bps"], 1) if rr else None
            continue
        r = cgroup_rates(a["cgroups"].get("others", {}).get(name), cur, dt)
        if r and (r["w_bps"] or r["r_bps"]):
            others[name] = {"w_bps": round(r["w_bps"], 1), "r_bps": round(r["r_bps"], 1)}
    top = dict(sorted(others.items(), key=lambda kv: -kv[1]["w_bps"])[:top_n])
    return {
        "dt_s": round(dt, 3),
        "others_writing": top,
        "root_total_w_bps": root_w,
        "devices": {k: device_delta(a["devices"].get(k), v, dt) for k, v in b["devices"].items()},
        "pressure": b["pressure"],
        "pressure_memory": b.get("pressure_memory"),
        "vmstat_per_s": vmstat_rates(a.get("vmstat"), b.get("vmstat"), dt),
        "loadavg": b["loadavg"],
        "cgroups": cg,
    }


def vmstat_rates(a: dict | None, b: dict | None, dt: float) -> dict | None:
    """Swap-in/out, major faults and anon refaults per second between two snapshots (None when the counters are unavailable)."""

    if not a or not b or dt <= 0:
        return None
    return {k: round(max(0, b[k] - a[k]) / dt, 2) for k in b if k in a}
