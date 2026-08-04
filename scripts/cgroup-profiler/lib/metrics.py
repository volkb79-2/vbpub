"""Per-tick readers: turn one cgroup / one pid / the host into a sample record.

Everything here is a pure transform of files that exist at the instant of the
call — no caching, no state, no knowledge of the previous tick.  The *_rates
helpers are the only functions that see two ticks at once, and they take the
already-sampled group dicts (not raw paths), so they work identically whether
the samples came from the real tree or a synthetic one in a test.

Two things this module must never do, because the whole package leans on it:
substitute 0 for a file that is not there (:mod:`lib.util` already encodes
"absent" as ``None`` — that is preserved end to end, not laundered into a
number), and let a rate go negative or spike when a counter is reset by a
cgroup being recreated (``util.rate`` already refuses that; nothing here may
work around it by computing a delta by hand).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Set, Tuple

from . import util

GROUPS: tuple[str, ...] = ("mem", "memstat", "memev", "psi", "cpu", "io", "pids", "cgstat")


def sample_cgroup(abs_path: str, groups: Optional[Set[str]] = None) -> Dict[str, Any]:
    """One tick of one cgroup's controller files, keyed by group name.

    A vanished cgroup (the common case: a container exits mid-run) reads back
    as ``{}`` in full, not as a dict of groups full of ``None`` — there is
    nothing here to report, and pretending otherwise would let a stale sample
    look like a real zero-activity tick downstream.  ``psi`` is one requested
    group but produces three keys (``psi_mem``/``psi_cpu``/``psi_io``) because
    the report treats memory/cpu/io pressure as three separate panels.
    """
    if not os.path.isdir(abs_path):
        return {}
    wanted = set(GROUPS) if groups is None else (set(groups) & set(GROUPS))
    out: Dict[str, Any] = {}

    def path(name: str) -> str:
        return os.path.join(abs_path, name)

    if "mem" in wanted:
        out["mem"] = {
            "current": util.read_int(path("memory.current")),
            "peak": util.read_int(path("memory.peak")),
            "swap_current": util.read_int(path("memory.swap.current")),
            "swap_peak": util.read_int(path("memory.swap.peak")),
            "zswap_current": util.read_int(path("memory.zswap.current")),
        }
    if "memstat" in wanted:
        out["memstat"] = util.read_kv(path("memory.stat"))
    if "memev" in wanted:
        # memory.events is the hierarchical (self + descendants) counter; a
        # profiler samples every cgroup of interest individually, and using
        # the hierarchical file would double-count a breach at both a parent
        # and the child that caused it. memory.events.local is this cgroup's
        # own count only, which is what "did *this* level breach" needs.
        out["memev"] = util.read_kv(path("memory.events.local"))
    if "psi" in wanted:
        out["psi_mem"] = util.read_pressure(path("memory.pressure"))
        out["psi_cpu"] = util.read_pressure(path("cpu.pressure"))
        out["psi_io"] = util.read_pressure(path("io.pressure"))
    if "cpu" in wanted:
        out["cpu"] = util.read_kv(path("cpu.stat"))
    if "io" in wanted:
        out["io"] = util.read_flat_keyed(path("io.stat"))
    if "pids" in wanted:
        out["pids"] = {
            "current": util.read_int(path("pids.current")),
            "peak": util.read_int(path("pids.peak")),
        }
    if "cgstat" in wanted:
        out["cgstat"] = util.read_kv(path("cgroup.stat"))
    return out


def sample_proc(pid: int, proc_root: str = "/proc") -> Dict[str, Any]:
    """One tick of one process's memory/cpu/io counters, ``{}`` if it is gone.

    ``proc_root`` has no counterpart in the public contract (a pid is process-
    namespace bound, not something a bind-mount root can redirect) — it exists
    purely as a test seam so the suite never has to read the real ``/proc``;
    every real caller uses the default.

    ``utime``/``stime`` come back in microseconds, not clock ticks, so they
    sit on the same footing as every other ``*_usec`` counter in this package
    (cgroup ``cpu.stat`` in particular) and can be compared or summed with it
    without a unit conversion downstream.
    """
    base = os.path.join(proc_root, str(pid))
    if not os.path.isdir(base):
        return {}
    status = util.read_proc_meminfo(os.path.join(base, "status"))
    io = util.read_proc_meminfo(os.path.join(base, "io"))
    utime, stime = _proc_cpu_usec(os.path.join(base, "stat"))
    return {
        "rss_anon": status.get("RssAnon"),
        "rss_file": status.get("RssFile"),
        "swap": status.get("VmSwap"),
        "utime": utime,
        "stime": stime,
        "read_bytes": io.get("read_bytes"),
        "write_bytes": io.get("write_bytes"),
    }


def _proc_cpu_usec(stat_path: str) -> Tuple[Optional[int], Optional[int]]:
    """``(utime, stime)`` in microseconds from ``/proc/<pid>/stat``.

    ``comm`` (field 2) is attacker-and-kernel controlled and may itself
    contain spaces or parentheses, so the only safe parse is to split on the
    *last* ``)`` and count fields from there, per ``proc(5)``.
    """
    text = util.read_text(stat_path)
    if not text:
        return (None, None)
    close = text.rfind(")")
    if close == -1:
        return (None, None)
    fields = text[close + 1 :].split()
    if len(fields) < 13:
        return (None, None)
    try:
        ticks_utime = int(fields[11])
        ticks_stime = int(fields[12])
    except ValueError:
        return (None, None)
    try:
        hz = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):
        hz = 100
    per_tick = 1_000_000 / hz
    return (int(ticks_utime * per_tick), int(ticks_stime * per_tick))


def sample_host(proc_root: str = "/proc", sys_root: str = "/sys") -> Dict[str, Any]:
    """One tick of host-wide memory/swap/pressure/KSM/zswap/CPU state."""
    pressure_dir = os.path.join(proc_root, "pressure")
    return {
        "meminfo": util.read_proc_meminfo(os.path.join(proc_root, "meminfo")),
        "vmstat": util.read_kv(os.path.join(proc_root, "vmstat")),
        "psi": {
            "memory": util.read_pressure(os.path.join(pressure_dir, "memory")),
            "cpu": util.read_pressure(os.path.join(pressure_dir, "cpu")),
            "io": util.read_pressure(os.path.join(pressure_dir, "io")),
        },
        "ksm": {
            "pages_sharing": util.read_int(os.path.join(sys_root, "kernel/mm/ksm/pages_sharing")),
            "general_profit": util.read_int(os.path.join(sys_root, "kernel/mm/ksm/general_profit")),
        },
        "zswap": {
            "max_pool_percent": util.read_int(
                os.path.join(sys_root, "module/zswap/parameters/max_pool_percent")
            ),
            "compressor": util.read_text(
                os.path.join(sys_root, "module/zswap/parameters/compressor")
            ),
        },
        "loadavg": _loadavg(proc_root),
        "cpu": _cpu_jiffies(proc_root),
    }


def _loadavg(proc_root: str) -> Optional[list]:
    text = util.read_text(os.path.join(proc_root, "loadavg"))
    if not text:
        return None
    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        return [float(parts[0]), float(parts[1]), float(parts[2])]
    except ValueError:
        return None


def _cpu_jiffies(proc_root: str) -> Dict[str, Optional[int]]:
    """Host-wide jiffy counters from the ``cpu `` (aggregate) line of /proc/stat.

    ``idle`` folds in ``iowait``: a CPU stalled on I/O is not doing work any
    more than one sitting fully idle is, and every top-alike tool's "%idle"
    matches that convention — using the raw ``idle`` field alone would read
    as busier than the host actually is whenever storage is the bottleneck.
    """
    text = util.read_text(os.path.join(proc_root, "stat"))
    if not text:
        return {"total_jiffies": None, "idle_jiffies": None}
    for line in text.split("\n"):
        if line.startswith("cpu "):
            parts = line.split()[1:]
            try:
                values = [int(p) for p in parts]
            except ValueError:
                return {"total_jiffies": None, "idle_jiffies": None}
            if not values:
                return {"total_jiffies": None, "idle_jiffies": None}
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return {"total_jiffies": sum(values), "idle_jiffies": idle}
    return {"total_jiffies": None, "idle_jiffies": None}


def cpu_cores_used(prev: Dict, cur: Dict, dt: float) -> Optional[float]:
    """Cores consumed between two ``cpu`` group readings (``usage_usec`` sums
    every CPU, so a delta of one CPU-second in one wall second is 1.0)."""
    rate = util.rate(
        prev.get("usage_usec") if prev else None,
        cur.get("usage_usec") if cur else None,
        dt,
    )
    return None if rate is None else rate / 1_000_000


def io_rates(prev: Dict, cur: Dict, dt: float) -> Dict[str, Dict[str, Optional[float]]]:
    """Per-device, per-field rates between two ``io`` group readings.

    Iterates the *current* tick's devices only: a device that vanished (its
    block device detached, or the cgroup lost access) has nothing to report a
    rate for, and carrying its last-known fields forward would be exactly the
    "stale sample dressed as current" bug this module exists to avoid.
    """
    cur = cur or {}
    prev = prev or {}
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for dev, fields in cur.items():
        prev_fields = prev.get(dev, {})
        out[dev] = {
            f"{key}_per_s": util.rate(prev_fields.get(key), value, dt)
            for key, value in fields.items()
        }
    return out


_MEMSTAT_RATE_FIELDS = (
    "pgfault",
    "pgmajfault",
    "pgscan",
    "pgsteal",
    "pswpin",
    "pswpout",
    "zswpin",
    "zswpout",
    "zswpwb",
    "workingset_refault_anon",
    "workingset_refault_file",
)


def memstat_rates(prev: Dict, cur: Dict, dt: float) -> Dict[str, Optional[float]]:
    """Per-second rates for every ``memstat`` counter, plus the zswap/disk/file
    refault split.

    The split (``rfz``/``rfd``/``rff``) reproduces
    ``/usr/local/sbin/soulmask-zswap-monitor.py`` byte for byte so the two
    tools read side by side: ``rfz/s = Δzswpin/Δt`` (pages served from the
    compressed pool, microsecond-scale), ``rff/s = Δworkingset_refault_file/Δt``
    (file-cache refaults, always a real read), and ``rfd/s =
    max(0, Δworkingset_refault_anon − Δzswpin)/Δt`` — the anon refaults zswpin
    can't account for, i.e. pages that went all the way to the real swap
    device. The two component rates are computed with :func:`util.rate` (not
    a hand-rolled delta) so that a reset in *either* counter makes ``rfd``
    ``None`` rather than silently blending a fresh counter's small delta
    against a stale one's huge one.
    """
    prev = prev or {}
    cur = cur or {}
    out: Dict[str, Optional[float]] = {
        f"{name}_per_s": util.rate(prev.get(name), cur.get(name), dt)
        for name in _MEMSTAT_RATE_FIELDS
    }

    zswpin_rate = util.rate(prev.get("zswpin"), cur.get("zswpin"), dt)
    anon_rate = util.rate(
        prev.get("workingset_refault_anon"), cur.get("workingset_refault_anon"), dt
    )
    file_rate = util.rate(
        prev.get("workingset_refault_file"), cur.get("workingset_refault_file"), dt
    )

    out["rfz_per_s"] = zswpin_rate
    out["rff_per_s"] = file_rate
    if zswpin_rate is None or anon_rate is None:
        out["rfd_per_s"] = None
    else:
        out["rfd_per_s"] = max(0.0, anon_rate - zswpin_rate)
    return out
