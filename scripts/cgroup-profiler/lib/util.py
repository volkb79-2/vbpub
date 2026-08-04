"""Small shared helpers: file reads that never raise, unit parsing, formatting.

Every reader here returns a sentinel rather than raising.  A profiler samples
cgroup files that appear and vanish underneath it (a container exits mid-run,
a slice is torn down), and a sampler that dies on ENOENT is useless.  The
distinction that matters downstream is *absent* (``None``) versus *present but
zero*, so nothing here ever substitutes 0 for a missing file.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

# ── cgroup v2 sentinel ──────────────────────────────────────────────────────
# "max" in a limit file means "no limit". We model it as None so that
# min()-style tightest-wins resolution can simply skip it.
MAX = "max"

_SUFFIXES = {
    "": 1,
    "b": 1,
    "k": 1000,
    "kb": 1000,
    "kib": 1024,
    "m": 1000**2,
    "mb": 1000**2,
    "mib": 1024**2,
    "g": 1000**3,
    "gb": 1000**3,
    "gib": 1024**3,
    "t": 1000**4,
    "tb": 1000**4,
    "tib": 1024**4,
}

# systemd-style sizes are binary despite the single-letter suffix (1G == 1GiB),
# which is what every value in host-setup.env means. Keep a separate table so a
# caller can be explicit about which convention it is parsing.
_SUFFIXES_BINARY = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "kib": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "mib": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "gib": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
    "tib": 1024**4,
}

_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*$")


def read_text(path: str) -> Optional[str]:
    """Return file contents stripped of the trailing newline, or None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().rstrip("\n")
    except (OSError, ValueError):
        return None


def read_lines(path: str) -> Optional[List[str]]:
    text = read_text(path)
    if text is None:
        return None
    return [ln for ln in text.split("\n") if ln]


def read_int(path: str) -> Optional[int]:
    """Read a single-integer cgroup file. ``max`` reads back as None."""
    text = read_text(path)
    if text is None:
        return None
    text = text.strip()
    if text == MAX or text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def read_kv(path: str) -> Dict[str, int]:
    """Parse a ``key value`` per line file (memory.stat, memory.events, ...).

    Non-integer values are skipped rather than failing the whole read: a newer
    kernel adding a non-numeric field must not blind us to every other field.
    """
    out: Dict[str, int] = {}
    lines = read_lines(path)
    if not lines:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            out[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return out


def read_flat_keyed(path: str) -> Dict[str, Dict[str, int]]:
    """Parse cgroup v2 "flat keyed" files such as ``io.stat`` / ``io.max``.

    ``8:16 rbytes=1024 wbytes=0 rios=3 wios=0`` becomes
    ``{"8:16": {"rbytes": 1024, "wbytes": 0, ...}}``.  Values of ``max`` are
    dropped (absent == unlimited), matching :func:`read_int`.
    """
    out: Dict[str, Dict[str, int]] = {}
    lines = read_lines(path)
    if not lines:
        return out
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        fields: Dict[str, int] = {}
        for kv in parts[1:]:
            if "=" not in kv:
                continue
            key, _, val = kv.partition("=")
            if val == MAX:
                continue
            try:
                fields[key] = int(val)
            except ValueError:
                continue
        out[parts[0]] = fields
    return out


def read_pressure(path: str) -> Dict[str, float]:
    """Parse a PSI file into ``{"some_avg10": .., "full_total": ..}``.

    ``total`` is microseconds of stall and is the only field worth
    differentiating over time; the avg* fields are the kernel's own decaying
    averages and are recorded as-is.
    """
    out: Dict[str, float] = {}
    lines = read_lines(path)
    if not lines:
        return out
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        kind = parts[0]  # "some" | "full"
        for kv in parts[1:]:
            key, _, val = kv.partition("=")
            try:
                out[f"{kind}_{key}"] = float(val)
            except ValueError:
                continue
    return out


def read_proc_meminfo(path: str = "/proc/meminfo") -> Dict[str, int]:
    """/proc/meminfo in **bytes** (the file is in kB for all but a few keys)."""
    out: Dict[str, int] = {}
    lines = read_lines(path)
    if not lines:
        return out
    for line in lines:
        key, _, rest = line.partition(":")
        rest = rest.strip()
        if not rest:
            continue
        parts = rest.split()
        try:
            value = int(parts[0])
        except ValueError:
            continue
        if len(parts) > 1 and parts[1].lower() == "kb":
            value *= 1024
        out[key.strip()] = value
    return out


def parse_size(text: str, binary: bool = True) -> Optional[int]:
    """Parse ``2G`` / ``512M`` / ``1.5GiB`` / ``max`` into bytes.

    ``binary=True`` (the default) follows systemd's convention where ``1G``
    means 1 GiB — that is what every size in ``host-setup.env`` means.
    """
    if text is None:
        return None
    text = text.strip()
    if not text or text == MAX:
        return None
    match = _SIZE_RE.match(text)
    if not match:
        return None
    number, suffix = match.groups()
    table = _SUFFIXES_BINARY if binary else _SUFFIXES
    factor = table.get(suffix.lower())
    if factor is None:
        return None
    return int(float(number) * factor)


def fmt_bytes(value: Optional[int], places: int = 1) -> str:
    """Human size using binary units, with ``max`` for None."""
    if value is None:
        return "max"
    negative = value < 0
    value = abs(value)
    for unit, factor in (
        ("T", 1024**4),
        ("G", 1024**3),
        ("M", 1024**2),
        ("K", 1024),
    ):
        if value >= factor:
            out = f"{value / factor:.{places}f}{unit}"
            return f"-{out}" if negative else out
    return f"-{value}B" if negative else f"{value}B"


def fmt_rate(value: Optional[float], unit: str = "/s", places: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:.{places}f}{unit}"


def tightest(values: Iterable[Optional[int]]) -> Optional[int]:
    """Smallest non-None value, or None when every value is unlimited."""
    present = [v for v in values if v is not None]
    return min(present) if present else None


def loosest(values: Iterable[Optional[int]]) -> Optional[int]:
    """Largest value, where None (unlimited) dominates everything."""
    values = list(values)
    if any(v is None for v in values):
        return None
    return max(values) if values else None


def ancestors(cgroup_path: str) -> List[str]:
    """Every cgroup from ``cgroup_path`` up to and including the root ``/``.

    Paths are cgroup-relative (leading ``/``, no ``/sys/fs/cgroup`` prefix),
    ordered leaf-first so index 0 is always the cgroup itself.
    """
    path = "/" + cgroup_path.strip("/")
    out = [path]
    while path != "/":
        path = os.path.dirname(path) or "/"
        out.append(path)
    return out


def relpath_of(cgroup_path: str, root: str) -> str:
    """``cgroup_path`` expressed relative to ``root``, or the input unchanged."""
    root = "/" + root.strip("/")
    path = "/" + cgroup_path.strip("/")
    if root == "/":
        return path
    if path == root:
        return "/"
    if path.startswith(root + "/"):
        return path[len(root):]
    return path


def short_label(cgroup_path: str, keep: int = 2) -> str:
    """A compact display label: the last ``keep`` path components.

    Docker scope names are 64 hex characters and destroy any chart legend, so
    ``docker-<64hex>.scope`` is abbreviated to ``docker-<12hex>…``.
    """
    parts = [p for p in cgroup_path.strip("/").split("/") if p]
    if not parts:
        return "/"
    parts = parts[-keep:]
    out = []
    for part in parts:
        match = re.match(r"^(docker|crio|containerd)-([0-9a-f]{12})[0-9a-f]*\.scope$", part)
        if match:
            out.append(f"{match.group(1)}-{match.group(2)}…")
        else:
            out.append(part)
    return "/".join(out)


def dev_from_majmin(majmin: str) -> str:
    """``254:0`` → a device name when resolvable, else the input unchanged."""
    try:
        major, minor = majmin.split(":")
    except ValueError:
        return majmin
    link = f"/sys/dev/block/{major}:{minor}"
    try:
        return os.path.basename(os.path.realpath(link))
    except OSError:
        return majmin


def rate(prev: Optional[float], cur: Optional[float], dt: float) -> Optional[float]:
    """Per-second rate between two monotonic counter samples.

    Returns None when either sample is missing, when ``dt`` is not positive, or
    when the counter went backwards — a counter reset (cgroup recreated, task
    migrated) must surface as "unknown", never as a huge negative spike.
    """
    if prev is None or cur is None or dt <= 0:
        return None
    if cur < prev:
        return None
    return (cur - prev) / dt


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_cpu_max(text: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """``cpu.max`` → ``(quota_usec, period_usec)``; quota None when ``max``."""
    if not text:
        return (None, None)
    parts = text.split()
    if not parts:
        return (None, None)
    quota = None if parts[0] == MAX else _safe_int(parts[0])
    period = _safe_int(parts[1]) if len(parts) > 1 else 100000
    return (quota, period)


def _safe_int(text: str) -> Optional[int]:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None
