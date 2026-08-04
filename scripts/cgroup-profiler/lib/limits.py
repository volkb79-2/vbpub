"""Effective-limit resolution: what a cgroup's caps and protections *actually*
are once every ancestor between it and the cgroup2 root is accounted for.

A leaf's own ``memory.max`` is not the answer to "how much memory can this
container use" — a slice three levels up may cap it tighter, and the report's
whole reason to exist is to say *which* ancestor that is
(":func:`effective`'s ``*_by`` fields) rather than make the operator diff
five files by hand.

Caps (memory.max/high/swap.max, cpu.max, io.max, pids.max) and protections
(memory.min/low) are resolved by opposite rules, and conflating them is the
easiest way to get this module wrong:

* A **cap** is tightest-wins over the whole chain — any ancestor can only
  shrink what a descendant may use, never grow it, so the smallest declared
  value anywhere in the chain is what holds. ``util.tightest`` already
  encodes "smallest value, None (unlimited) loses to any real number"; this
  module's job on top of that is to remember *which* cgroup contributed it.
* A **protection** is the opposite: it is a promise the kernel makes to a
  cgroup about memory it will not reclaim under pressure, and the promise
  only holds all the way to the leaf when every level in between also made
  it (``memory_recursiveprot``). Without that mount option the kernel's
  accounting is *not* recursive, and a single unprotected level anywhere in
  the chain — including the leaf redeclaring 0 — throws away everything a
  more generous ancestor promised. That is exactly the silent misconfiguration
  ``effective()`` is built to surface as a warning.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from . import util
from .access import CGROUP_ROOT


@dataclass
class LimitSet:
    """One cgroup's own declared values — nothing inherited, nothing resolved."""

    memory_min: Optional[int] = None
    memory_low: Optional[int] = None
    memory_high: Optional[int] = None
    memory_max: Optional[int] = None
    memory_swap_max: Optional[int] = None
    memory_swap_high: Optional[int] = None
    memory_zswap_max: Optional[int] = None
    memory_zswap_writeback: Optional[bool] = None
    cpu_quota_usec: Optional[int] = None
    cpu_period_usec: Optional[int] = None
    cpu_weight: Optional[int] = None
    io_max: Dict[str, Dict[str, int]] = field(default_factory=dict)
    io_weight: Optional[int] = None
    io_bfq_weight: Optional[int] = None
    pids_max: Optional[int] = None


@dataclass
class Effective:
    """Resolved limits for one cgroup, plus the chain that produced them."""

    cgroup: str
    chain: List[Tuple[str, LimitSet]]
    memory_max: Optional[int] = None
    memory_max_by: Optional[str] = None
    memory_high: Optional[int] = None
    memory_high_by: Optional[str] = None
    memory_swap_max: Optional[int] = None
    memory_swap_max_by: Optional[str] = None
    strict_min: int = 0
    recursive_min: int = 0
    strict_low: int = 0
    recursive_low: int = 0
    protection_mode: str = "strict"
    cpu_cores: Optional[float] = None
    cpu_cores_by: Optional[str] = None
    io_max: Dict[str, Dict[str, int]] = field(default_factory=dict)
    io_max_by: Dict[str, str] = field(default_factory=dict)
    pids_max: Optional[int] = None
    warnings: List[str] = field(default_factory=list)


def _abs_path(root: str, cgroup_path: str) -> str:
    rel = cgroup_path.strip("/")
    return os.path.join(root, rel) if rel else root


def _read_bool(path: str) -> Optional[bool]:
    text = util.read_text(path)
    if text is None:
        return None
    text = text.strip()
    if text == "1":
        return True
    if text == "0":
        return False
    return None


def _default_weight(path: str) -> Optional[int]:
    """The ``default`` value out of a flat-keyed weight file.

    ``io.weight``/``io.bfq.weight`` open with a ``default <n>`` line before
    any per-device overrides; per-device weights are a different concern
    (device-level IO shaping, not "how much of this controller's own budget
    this cgroup gets") and are out of scope for :class:`LimitSet`.
    """
    text = util.read_text(path)
    if not text:
        return None
    parts = text.split("\n", 1)[0].split()
    if len(parts) >= 2 and parts[0] == "default":
        parts = parts[1:]
    try:
        return int(parts[0])
    except (ValueError, IndexError):
        return None


def read_limits(abs_path: str) -> LimitSet:
    """Read one cgroup's own controller files. Never raises: a cgroup that
    vanished between discovery and this read comes back as all-``None``,
    same as any other absent file in this package."""
    quota, period = util.parse_cpu_max(util.read_text(os.path.join(abs_path, "cpu.max")))
    return LimitSet(
        memory_min=util.read_int(os.path.join(abs_path, "memory.min")),
        memory_low=util.read_int(os.path.join(abs_path, "memory.low")),
        memory_high=util.read_int(os.path.join(abs_path, "memory.high")),
        memory_max=util.read_int(os.path.join(abs_path, "memory.max")),
        memory_swap_max=util.read_int(os.path.join(abs_path, "memory.swap.max")),
        memory_swap_high=util.read_int(os.path.join(abs_path, "memory.swap.high")),
        memory_zswap_max=util.read_int(os.path.join(abs_path, "memory.zswap.max")),
        memory_zswap_writeback=_read_bool(os.path.join(abs_path, "memory.zswap.writeback")),
        cpu_quota_usec=quota,
        cpu_period_usec=period,
        cpu_weight=util.read_int(os.path.join(abs_path, "cpu.weight")),
        io_max=util.read_flat_keyed(os.path.join(abs_path, "io.max")),
        io_weight=_default_weight(os.path.join(abs_path, "io.weight")),
        io_bfq_weight=_default_weight(os.path.join(abs_path, "io.bfq.weight")),
        pids_max=util.read_int(os.path.join(abs_path, "pids.max")),
    )


def mount_flags(proc_root: str = "/proc") -> Set[str]:
    """The cgroup2 mount's option set, e.g. ``{"rw", "memory_recursiveprot"}``.

    A missing or unparseable ``/proc/mounts`` yields an empty set rather than
    raising — callers must treat that the same as "recursiveprot absent",
    which is the conservative (warn more, not less) reading.
    """
    lines = util.read_lines(os.path.join(proc_root, "mounts")) or []
    for line in lines:
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "cgroup2" and parts[2] == "cgroup2":
            return set(parts[3].split(","))
    return set()


Chain = List[Tuple[str, LimitSet]]


def _tightest_by(
    chain: Chain, getter: Callable[[LimitSet], Optional[int]]
) -> Tuple[Optional[int], Optional[str]]:
    """Smallest declared value over the chain, and which cgroup declared it.

    Walks leaf-first, so an exact tie is attributed to whichever cgroup is
    closest to the leaf — the ancestor a fix is actually likely to touch.
    """
    best_val: Optional[int] = None
    best_cg: Optional[str] = None
    for cg, limits in chain:
        val = getter(limits)
        if val is None:
            continue
        if best_val is None or val < best_val:
            best_val, best_cg = val, cg
    return best_val, best_cg


def _cores(limits: LimitSet) -> Optional[float]:
    if limits.cpu_quota_usec is None or not limits.cpu_period_usec:
        return None
    return limits.cpu_quota_usec / limits.cpu_period_usec


def _merge_io_max(chain: Chain) -> Tuple[Dict[str, Dict[str, int]], Dict[str, str]]:
    """Tightest-wins io.max, per device per field, attributed per device.

    In practice a device's io.max is set at exactly one level of a real
    tree, so per-device attribution is unambiguous; if two ancestors both
    constrain the same device on different fields, the device is credited to
    whichever one contributed the field that was updated last while scanning
    leaf-first — a defensible tie-break, not a claim that only one ancestor
    is involved.
    """
    result: Dict[str, Dict[str, int]] = {}
    by: Dict[str, str] = {}
    for cg, limits in chain:
        for dev, fields in limits.io_max.items():
            merged = result.setdefault(dev, {})
            for key, val in fields.items():
                if key not in merged or val < merged[key]:
                    merged[key] = val
                    by[dev] = cg
    return result, by


def _declared(chain: Chain, attr: str) -> List[int]:
    """Every value actually declared for a protection attribute across the
    chain. A level with no file for it (no memory controller there, or the
    true cgroupfs root, which never has memory.min/low at all) contributes
    nothing — per §1, absent is not zero, so it must not masquerade as an
    explicit "no protection" the way a real 0 legitimately does."""
    out = []
    for _, limits in chain:
        val = getattr(limits, attr)
        if val is not None:
            out.append(val)
    return out


def _strict_protection(declared: List[int]) -> int:
    return min(declared) if declared else 0


def _recursive_binder(chain: Chain, attr: str) -> Tuple[int, Optional[str]]:
    """Smallest *nonzero* declared protection, and who declared it — this is
    the value/ancestor that would hold under ``memory_recursiveprot``."""
    best_val: Optional[int] = None
    best_cg: Optional[str] = None
    for cg, limits in chain:
        val = getattr(limits, attr)
        if val is None or val <= 0:
            continue
        if best_val is None or val < best_val:
            best_val, best_cg = val, cg
    return (best_val if best_val is not None else 0), best_cg


def effective(
    cgroup: str, root: str = CGROUP_ROOT, flags: Optional[Set[str]] = None
) -> Effective:
    """Resolve one cgroup's effective limits by walking it up to the root.

    ``flags`` is whatever :func:`mount_flags` returned, or ``None`` if the
    caller has not looked — this function has no ``proc_root`` of its own to
    fall back to, so ``None`` is treated as "unknown, assume the conservative
    (strict) mode" rather than silently reaching for the real ``/proc``.
    """
    chain: Chain = [(anc, read_limits(_abs_path(root, anc))) for anc in util.ancestors(cgroup)]
    flags = flags or set()

    memory_max, memory_max_by = _tightest_by(chain, lambda l: l.memory_max)
    memory_high, memory_high_by = _tightest_by(chain, lambda l: l.memory_high)
    memory_swap_max, memory_swap_max_by = _tightest_by(chain, lambda l: l.memory_swap_max)
    pids_max, _pids_max_by = _tightest_by(chain, lambda l: l.pids_max)
    cpu_cores, cpu_cores_by = _tightest_by(chain, _cores)
    io_max, io_max_by = _merge_io_max(chain)

    strict_min = _strict_protection(_declared(chain, "memory_min"))
    strict_low = _strict_protection(_declared(chain, "memory_low"))
    recursive_min, recursive_min_by = _recursive_binder(chain, "memory_min")
    recursive_low, recursive_low_by = _recursive_binder(chain, "memory_low")

    protection_mode = "recursive" if "memory_recursiveprot" in flags else "strict"

    warnings: List[str] = []
    if protection_mode == "strict":
        if strict_min != recursive_min and recursive_min_by:
            warnings.append(
                f"memory.min: {util.fmt_bytes(recursive_min)} protection declared by "
                f"{recursive_min_by} is discarded without memory_recursiveprot "
                f"(effective memory.min here is {util.fmt_bytes(strict_min)})"
            )
        if strict_low != recursive_low and recursive_low_by:
            warnings.append(
                f"memory.low: {util.fmt_bytes(recursive_low)} protection declared by "
                f"{recursive_low_by} is discarded without memory_recursiveprot "
                f"(effective memory.low here is {util.fmt_bytes(strict_low)})"
            )

    return Effective(
        cgroup=chain[0][0],
        chain=chain,
        memory_max=memory_max,
        memory_max_by=memory_max_by,
        memory_high=memory_high,
        memory_high_by=memory_high_by,
        memory_swap_max=memory_swap_max,
        memory_swap_max_by=memory_swap_max_by,
        strict_min=strict_min,
        recursive_min=recursive_min,
        strict_low=strict_low,
        recursive_low=recursive_low,
        protection_mode=protection_mode,
        cpu_cores=cpu_cores,
        cpu_cores_by=cpu_cores_by,
        io_max=io_max,
        io_max_by=io_max_by,
        pids_max=pids_max,
        warnings=warnings,
    )


def fingerprint(eff: Effective) -> str:
    """A stable hash of the *resolved* facts (not the raw chain), so a caller
    doing drift detection tick to tick only sees a change when something a
    human would actually care about changed — not when an unrelated field on
    a non-binding ancestor happened to move."""
    payload = {
        "memory_max": eff.memory_max,
        "memory_max_by": eff.memory_max_by,
        "memory_high": eff.memory_high,
        "memory_high_by": eff.memory_high_by,
        "memory_swap_max": eff.memory_swap_max,
        "memory_swap_max_by": eff.memory_swap_max_by,
        "strict_min": eff.strict_min,
        "recursive_min": eff.recursive_min,
        "strict_low": eff.strict_low,
        "recursive_low": eff.recursive_low,
        "protection_mode": eff.protection_mode,
        "cpu_cores": eff.cpu_cores,
        "cpu_cores_by": eff.cpu_cores_by,
        "io_max": eff.io_max,
        "io_max_by": eff.io_max_by,
        "pids_max": eff.pids_max,
    }
    blob = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _capline(name: str, value: Optional[int], by: Optional[str]) -> str:
    line = f"{name}: {util.fmt_bytes(value)}"
    return f"{line} (bound by {by})" if by else line


def describe(eff: Effective) -> List[str]:
    """Human-readable lines for the report: the resolved facts first, then
    any warnings — the order a reader would want them in, most-actionable
    last since that is what should linger."""
    lines = [
        _capline("memory.max", eff.memory_max, eff.memory_max_by),
        _capline("memory.high", eff.memory_high, eff.memory_high_by),
        _capline("memory.swap.max", eff.memory_swap_max, eff.memory_swap_max_by),
    ]

    if eff.protection_mode == "strict":
        shown_min, shown_low = eff.strict_min, eff.strict_low
    else:
        shown_min, shown_low = eff.recursive_min, eff.recursive_low
    lines.append(
        f"memory protection ({eff.protection_mode}): "
        f"min={util.fmt_bytes(shown_min)} low={util.fmt_bytes(shown_low)}"
    )

    if eff.cpu_cores is None:
        lines.append("cpu: unlimited")
    else:
        cores_line = f"cpu: {eff.cpu_cores:.2f} cores"
        if eff.cpu_cores_by:
            cores_line += f" (bound by {eff.cpu_cores_by})"
        lines.append(cores_line)

    lines.append(f"pids.max: {eff.pids_max if eff.pids_max is not None else 'max'}")

    for dev, fields in sorted(eff.io_max.items()):
        by = eff.io_max_by.get(dev)
        parts = ", ".join(f"{k}={v}" for k, v in sorted(fields.items()))
        line = f"io.max {dev}: {parts}"
        lines.append(f"{line} (bound by {by})" if by else line)

    lines.extend(eff.warnings)
    return lines
