#!/usr/bin/env python3
"""Capacity unavailable to MDT: keys, sizing, protection verification, live probe.

The operator names three numbers: RAM, DISK swap and CPU cores that other services on
the host use and that are therefore unavailable to MDT. Everything else is MDT's. This
module is the single home of

* the renamed-key table and its refusal text (a one-time rename, no alias),
* the derivation of what MDT may use (pure functions, no host access),
* the protection verification over a cgroup tree (verify only),
* the read-only live probe that suggests the three numbers.

It is standard-library only and is installed beside ``mdt-host-check.sh`` so the
installer, the wizard and the check share one implementation. Every function takes
its inputs as arguments (sizes in bytes, a cgroup root path), so tests drive them on
fake trees and fake ``/proc/meminfo`` content.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable, Sequence

KIB = 1024
MIB = KIB * 1024
GIB = MIB * 1024

README_REF = 'See host-setup/README.md "Renamed keys (2026-11)".'

# --------------------------------------------------------------------------- keys

NEW_KEYS = (
    "MDT_EXCLUDED_RAM",
    "MDT_EXCLUDED_SWAP",
    "MDT_EXCLUDED_CPU_CORES",
    "DEV_TIER_EXCLUDED_CPU_CORES",
    "MDT_PROTECTED_UNITS",
    "MDT_HOST_SWAP_MARGIN",
)

# Removed outright, no mapping layer: the installer, the wizard and the check refuse them.
# old key -> (new key, what it means now)
RENAMED_KEYS = {
    "DEV_CPU_RESERVE_CORES": (
        "MDT_EXCLUDED_CPU_CORES",
        "cores that other services on this host use and that are unavailable to MDT",
    ),
    "DEV_SUBSLICE_CPU_RESERVE_CORES": (
        "DEV_TIER_EXCLUDED_CPU_CORES",
        "cores no single tier may use on top of MDT_EXCLUDED_CPU_CORES; 0 is recommended",
    ),
    "DEV_MEMORY_RESERVE": (
        "MDT_EXCLUDED_RAM",
        "RAM that other services on this host use and that is unavailable to MDT",
    ),
}
_OLD_ROOT_DEFAULT = Decimal(1)  # the retired default of DEV_CPU_RESERVE_CORES
_ANY_RESERVE_KEY = re.compile(r"^[A-Z0-9_]*RESERVE[A-Z0-9_]*$")


def _fmt_cores(value: Decimal) -> str:
    value = value.quantize(Decimal("0.1"))
    return str(int(value)) if value == value.to_integral_value() else f"{value:.1f}"


def _migrated_value(old: str, raw: str, values: dict[str, str]) -> str:
    if old == "DEV_SUBSLICE_CPU_RESERVE_CORES":
        try:
            k = Decimal(raw)
            c = Decimal(values["DEV_CPU_RESERVE_CORES"]) if "DEV_CPU_RESERVE_CORES" in values else _OLD_ROOT_DEFAULT
        except InvalidOperation:
            return raw
        return _fmt_cores(max(Decimal(0), k - c))
    return raw


def renamed_key_errors(values: dict[str, str]) -> list[str]:
    """One refusal line per retired key present in ``values`` (empty when clean)."""
    errors: list[str] = []
    for old in sorted(k for k in values if _ANY_RESERVE_KEY.match(k)):
        raw = values[old]
        if old in RENAMED_KEYS:
            new, meaning = RENAMED_KEYS[old]
            migrated = _migrated_value(old, raw, values)
            errors.append(
                f"{old} is no longer read: rename it to {new} ({meaning}; "
                f"your value {raw} → {new}={migrated}). {README_REF}"
            )
        else:
            errors.append(
                f"{old} is no longer read: every *_RESERVE_* key was renamed to the "
                f"MDT_EXCLUDED_* / DEV_TIER_EXCLUDED_* keys (capacity unavailable to MDT); "
                f"remove it. {README_REF}"
            )
    return errors


# ----------------------------------------------------------------- sizes and cores

_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([KMGT]?)i?[Bb]?$", re.IGNORECASE)
_SIZE_MULT = {"": 1, "K": KIB, "M": MIB, "G": GIB, "T": GIB * KIB}


def parse_size_bytes(value: str) -> int | None:
    """systemd-style size to bytes (base 1024); ``None`` for anything else. ``0`` is valid."""
    match = _SIZE_RE.match(value.strip())
    if not match:
        return None
    return int(Decimal(match.group(1)) * _SIZE_MULT[match.group(2).upper()])


def format_size(nbytes: int) -> str:
    nbytes = max(0, int(nbytes))
    if nbytes == 0:
        return "0"
    if nbytes % GIB == 0:
        return f"{nbytes // GIB}G"
    if nbytes % MIB == 0:
        return f"{nbytes // MIB}M"
    if nbytes >= GIB:
        return f"{nbytes / GIB:.2f}G"
    return f"{math.ceil(nbytes / MIB)}M"


def validate_size_or_zero(value: str) -> str | None:
    if parse_size_bytes(value) is None:
        return (
            f"{value!r} is not a size: use a number with an optional K/M/G/T suffix "
            "(e.g. 6500M, 14G) or 0 for none"
        )
    return None


_CORES_RE = re.compile(r"^\d+(\.\d)?$")


def validate_cores(value: str) -> str | None:
    """Cores unavailable to MDT: a non-negative number with at most one decimal place."""
    if not value:
        return "a number of cores is required (0 on a host without other load)"
    if not _CORES_RE.match(value):
        return f"{value!r} is not valid: use a number with at most one decimal place (0, 0.5, 1.5)"
    return None


def parse_cores(value: str) -> Decimal:
    if validate_cores(value):
        raise ValueError(f"invalid core count {value!r}")
    return Decimal(value)


_UNIT_RE = re.compile(r"^[A-Za-z0-9:_.@\\-]+\.(slice|service|scope)$")


def validate_units(value: str) -> str | None:
    """Space-separated systemd unit names; empty means none."""
    for name in value.split():
        if not _UNIT_RE.match(name):
            return (
                f"{name!r} is not a systemd unit name (e.g. wings.slice, docker.service); "
                "separate several with spaces"
            )
    return None


def round_up(value: float, step: float) -> float:
    return math.ceil(value / step - 1e-9) * step


# ---------------------------------------------------------------------- derivation

MIN_RAM_MDT = GIB
MIN_CPU_MDT = Decimal("0.5")


class CapacityError(ValueError):
    """The excluded capacity leaves MDT too little to run."""


@dataclass(frozen=True)
class Capacity:
    """What MDT may use, derived from the host totals minus the excluded capacity."""

    ram_mdt: int  # bytes
    swap_mdt: int  # bytes, may be <= 0 (then MDT gets no disk swap)
    cpu_mdt: Decimal  # cores
    tier_cpu: Decimal  # cores per tier
    warnings: tuple[str, ...] = field(default=())

    @property
    def no_disk_swap(self) -> bool:
        return self.swap_mdt <= 0

    @property
    def dev_cpu_quota_pct(self) -> int:
        return int(self.cpu_mdt * 100)

    @property
    def tier_cpu_quota_pct(self) -> int:
        return int(self.tier_cpu * 100)

    @property
    def swap_mdt_bytes(self) -> int:
        return max(0, self.swap_mdt)


def derive_capacity(
    *,
    mem_total: int,
    swap_total: int,
    nproc: int,
    excluded_ram: int = 0,
    excluded_swap: int = 0,
    excluded_cores: Decimal = Decimal(0),
    tier_excluded_cores: Decimal = Decimal(0),
    host_swap_margin: int = 512 * MIB,
) -> Capacity:
    """RAM_MDT, SWAP_MDT and CPU_MDT; refuses RAM_MDT < 1G and CPU_MDT < 0.5."""
    ram_mdt = mem_total - excluded_ram
    if ram_mdt < MIN_RAM_MDT:
        raise CapacityError(
            f"MemTotal {format_size(mem_total)} minus MDT_EXCLUDED_RAM {format_size(excluded_ram)} "
            f"leaves {format_size(max(0, ram_mdt))} for MDT; at least 1G is required"
        )
    cpu_mdt = Decimal(nproc) - excluded_cores
    if cpu_mdt < MIN_CPU_MDT:
        raise CapacityError(
            f"nproc {nproc} minus MDT_EXCLUDED_CPU_CORES {_fmt_cores(excluded_cores)} "
            f"leaves {_fmt_cores(max(Decimal(0), cpu_mdt))} cores for MDT; at least 0.5 is required"
        )
    tier_cpu = cpu_mdt - tier_excluded_cores
    warnings: list[str] = []
    if tier_cpu < MIN_CPU_MDT:
        warnings.append(
            f"DEV_TIER_EXCLUDED_CPU_CORES={_fmt_cores(tier_excluded_cores)} leaves a tier "
            f"{_fmt_cores(max(Decimal(0), tier_cpu))} cores; the tier quota is floored at 0.5 core"
        )
        tier_cpu = MIN_CPU_MDT
    swap_mdt = swap_total - excluded_swap - host_swap_margin
    if swap_mdt <= 0:
        warnings.append(
            f"SwapTotal {format_size(swap_total)} minus MDT_EXCLUDED_SWAP {format_size(excluded_swap)} "
            f"minus MDT_HOST_SWAP_MARGIN {format_size(host_swap_margin)} is not positive: "
            "MDT gets no disk swap"
        )
    return Capacity(ram_mdt, swap_mdt, cpu_mdt, tier_cpu, tuple(warnings))


def derive_from_config(
    values: dict[str, str], *, mem_total: int, swap_total: int, nproc: int
) -> Capacity:
    """``derive_capacity`` with the six keys read from a parsed host-setup.env."""

    def size(key: str, default: str) -> int:
        raw = values.get(key, default) or default
        parsed = parse_size_bytes(raw)
        if parsed is None:
            raise CapacityError(f"{key}={raw!r} is not a size")
        return parsed

    def cores(key: str) -> Decimal:
        raw = values.get(key, "0") or "0"
        try:
            return parse_cores(raw)
        except ValueError as exc:
            raise CapacityError(f"{key}: {exc}") from exc

    return derive_capacity(
        mem_total=mem_total,
        swap_total=swap_total,
        nproc=nproc,
        excluded_ram=size("MDT_EXCLUDED_RAM", "0"),
        excluded_swap=size("MDT_EXCLUDED_SWAP", "0"),
        excluded_cores=cores("MDT_EXCLUDED_CPU_CORES"),
        tier_excluded_cores=cores("DEV_TIER_EXCLUDED_CPU_CORES"),
        host_swap_margin=size("MDT_HOST_SWAP_MARGIN", "512M"),
    )


def memory_sizing_warnings(values: dict[str, str], cap: Capacity) -> list[str]:
    """WARN (never refuse) when dev.slice's MemoryMax does not bound MDT to RAM_MDT.

    An explicit value wins over the derived proposal, so an existing config can keep a
    DEV_MEMORY_MAX larger than what MDT may use, or none at all while RAM is excluded.
    """
    raw = (values.get("DEV_MEMORY_MAX", "") or "").strip()
    excluded = parse_size_bytes(values.get("MDT_EXCLUDED_RAM", "0") or "0") or 0
    ceiling = parse_size_bytes(raw) if raw not in ("", "-") else None
    if ceiling is not None and ceiling > cap.ram_mdt:
        return [
            f"dev.slice MemoryMax exceeds the RAM MDT may use: DEV_MEMORY_MAX={raw} is above "
            f"RAM_MDT {format_size(cap.ram_mdt)} (MemTotal minus MDT_EXCLUDED_RAM)"
        ]
    if ceiling is None and excluded > 0:
        return [
            f"dev.slice has no MemoryMax (DEV_MEMORY_MAX is empty) while MDT_EXCLUDED_RAM is "
            f"{format_size(excluded)}: the RAM MDT may not use is not bounded on dev.slice; "
            f"set DEV_MEMORY_MAX to at most {format_size(cap.ram_mdt)}"
        ]
    return []


# Tier sizing as percent of RAM_MDT: (MemoryHigh, MemoryMax). The tiers sum to more than
# RAM_MDT on purpose (overcommit); zswap and disk swap absorb the cold part.
DEV_HIGH_PCT = 92
TIER_PCT = {
    "DEV_INTERACTIVE": (45, 60),
    "DEV_BACKGROUND": (50, 70),
    "DEV_GATES": (50, 70),
    "DEV_BUILDKITD": (25, 35),
}


def tier_memory_bytes(ram_mdt: int) -> dict[str, int]:
    """Starting MemoryHigh/Max values (bytes) for dev.slice and the tiers.

    No MemoryLow is proposed anywhere: a child Low below an unset parent Low is ineffective,
    and a dev.slice Low would take reclaim priority over every unprotected host service.
    Soft protection is an open design question (backlog MDT-033)."""
    sizes = {
        "DEV_MEMORY_HIGH": ram_mdt * DEV_HIGH_PCT // 100,
        "DEV_MEMORY_MAX": ram_mdt,
    }
    for prefix, (high, maximum) in TIER_PCT.items():
        sizes[f"{prefix}_MEMORY_HIGH"] = ram_mdt * high // 100
        sizes[f"{prefix}_MEMORY_MAX"] = ram_mdt * maximum // 100
    return sizes


def tier_swap_bytes(swap_mdt: int, cascade_pct: int) -> int:
    return max(0, swap_mdt) * cascade_pct // 100


# --------------------------------------------- HOST_ROLE=dedicated-dev memory (MDT-TESTHOST-MEM)
#
# Not named *_RESERVE_*: every such key is refused by the 2026-11 rename guard above.
# Plain 1G, not derived from the host at install time: the two test hosts measured 1.0-1.1 GB of
# system.slice memory.current (dockerd, containerd, journald, cgpd, page cache), so 1G covers that working
# set. A host whose system.slice is larger must raise DEV_HOST_MEMORY_KEEP; mdt-host-check.sh warns when
# the measured system.slice memory.current exceeds the keep.
DEDICATED_MEMORY_KEEP_DEFAULT = "1G"
DEDICATED_MEMORY_KEEP_MIN = 256 * 1024 * 1024
# A pinned dev.slice MemoryHigh/MemoryMax below this stalls the whole dev tier (every allocation throttled).
DEDICATED_MEMORY_PIN_MIN = 256 * 1024 * 1024
# Host services the dedicated-dev role protects from the dev tier (OOMScoreAdjust drop-in, the
# PSI guard's pressure signals).  docker/containerd are protected too, so a dev-tier OOM victim is
# never the daemon that supervises the containers.
DEDICATED_PROTECTED_UNITS_DEFAULT = (
    "docker.service containerd.service cgpd.service mdt-psi-guard.service "
    "systemd-journald.service systemd-oomd.service"
)
# NEVER in the list: sshd restores the OOM score it started with into every login session it forks, so an
# OOMScoreAdjust drop-in on ssh makes all interactive processes unkillable (review F1). The listener
# already protects itself (-1000). Same for the socket-activated names.
DEDICATED_FORBIDDEN_UNITS = frozenset({"ssh.service", "sshd.service", "ssh.socket", "sshd.socket", "ssh", "sshd"})
DEDICATED_OOM_SCORE_ADJUST = -900


def validate_host_memory_keep(value: str) -> str | None:
    """DEV_HOST_MEMORY_KEEP: a size of at least 256M (K/M/G/T suffix or bytes); 0 and empty are refused."""
    parsed = parse_size_bytes(value) if value.strip() else None
    if parsed is None or parsed < DEDICATED_MEMORY_KEEP_MIN:
        return (
            f"{value!r} is not a valid host memory size: use a size of at least 256M such as 1G "
            "(the RAM+swap kept for the host itself on HOST_ROLE=dedicated-dev; 0 and empty are refused)"
        )
    return None


@dataclass(frozen=True)
class DedicatedMemory:
    """dev.slice memory limits of HOST_ROLE=dedicated-dev, in bytes (``None`` = directive omitted)."""

    keep: int
    ram: int
    swap: int
    high: int | None
    maximum: int | None
    high_source: str
    max_source: str
    low: int | None = None
    low_source: str = "none"
    # dev-gates / dev-background MemoryHigh and MemoryMax (MDT-TESTHOST-MEM-2): dev.slice's own value minus the
    # protected tiers' MemoryLow, so a running cockpit/BuildKit keeps its Low while the load tiers yield first.
    protected: int = 0
    load_high: int | None = None
    load_max: int | None = None
    gates_high_source: str = "none"
    gates_max_source: str = "none"
    background_high_source: str = "none"
    background_max_source: str = "none"
    gates_high: int | None = None
    gates_max: int | None = None
    background_high: int | None = None
    background_max: int | None = None

# The tiers whose MemoryLow dev-gates / dev-background must leave room for.
_PROTECTED_LOW_KEYS = ("DEV_INTERACTIVE_MEMORY_LOW", "DEV_BUILDKITD_MEMORY_LOW")
# Floor of a derived load-tier ceiling (a smaller value would stall every lane).
DEDICATED_LOAD_FLOOR = 1024 ** 3


_TIER_LOW_KEYS = (
    "DEV_INTERACTIVE_MEMORY_LOW", "DEV_BACKGROUND_MEMORY_LOW", "DEV_GATES_MEMORY_LOW",
    "DEV_BUILDKITD_MEMORY_LOW", "DEV_MEMORY_MIN_GUARANTEED_LOW",
)


def dedicated_memory(values: dict[str, str], *, mem_total: int, swap_total: int) -> DedicatedMemory:
    """dev.slice limits for a sole-tenant dev/test host planned for overcommit.

    MemoryMax = MemTotal + SwapTotal - MDT_EXCLUDED_RAM - MDT_EXCLUDED_SWAP - DEV_HOST_MEMORY_KEEP
    (a combined RAM+swap figure; the kernel's memory.max counts RAM (and zswap) only, which physical RAM
    caps, so on these hosts it is NOT reachable and is no leak backstop: the bounds that act are MemoryHigh
    throttling, memory.swap.max, oomd and finally the global OOM killer; see README).
    MemoryHigh = MemTotal - MDT_EXCLUDED_RAM - DEV_HOST_MEMORY_KEEP: the RAM the dev tier may hold; above
    it the kernel reclaims the tier into zswap/swap instead of taking pages from the host.
    An explicit DEV_MEMORY_HIGH / DEV_MEMORY_MAX is a pin and wins per value; `-` omits it.
    """
    raw_keep = (values.get("DEV_HOST_MEMORY_KEEP", "") or DEDICATED_MEMORY_KEEP_DEFAULT).strip()
    error = validate_host_memory_keep(raw_keep)
    if error:
        raise CapacityError(f"DEV_HOST_MEMORY_KEEP: {error}")
    keep = parse_size_bytes(raw_keep) or 0

    def size(key: str) -> int:
        raw = (values.get(key, "0") or "0").strip()
        parsed = parse_size_bytes(raw)
        if parsed is None:
            raise CapacityError(f"{key}={raw!r} is not a size")
        return parsed

    ram = mem_total - size("MDT_EXCLUDED_RAM")
    swap = max(0, swap_total - size("MDT_EXCLUDED_SWAP"))
    high_ceiling = ram - keep
    max_ceiling = ram + swap - keep
    if high_ceiling < MIN_RAM_MDT:
        raise CapacityError(
            f"MemTotal {format_size(mem_total)} minus MDT_EXCLUDED_RAM minus DEV_HOST_MEMORY_KEEP "
            f"{format_size(keep)} leaves {format_size(max(0, high_ceiling))} of RAM for the dev tier; "
            "at least 1G is required (lower DEV_HOST_MEMORY_KEEP)"
        )

    def pick(key: str, derived: int) -> tuple[int | None, str]:
        raw = (values.get(key, "") or "").strip()
        if raw == "-":
            return None, "omitted (-)"
        if raw:
            parsed = parse_size_bytes(raw)
            if parsed is None:
                raise CapacityError(f"{key}={raw!r} is not a size (use a size such as 6G, `-` or 0 to omit)")
            if parsed == 0:  # the shared validators' "0 = none": same as `-`
                return None, "omitted (0)"
            if key != "DEV_MEMORY_LOW" and parsed < DEDICATED_MEMORY_PIN_MIN:
                raise CapacityError(f"{key}={raw!r} is below 256M, which would stall the whole dev tier; use a larger size, `-` or 0")
            return parsed, f"pin {key}={raw}"
        return derived, "derived"

    high, high_source = pick("DEV_MEMORY_HIGH", high_ceiling)
    maximum, max_source = pick("DEV_MEMORY_MAX", max_ceiling)
    if high is not None and maximum is not None and high > maximum:
        raise CapacityError(
            f"dev.slice MemoryHigh {format_size(high)} ({high_source}) exceeds MemoryMax "
            f"{format_size(maximum)} ({max_source}); keep MemoryHigh <= MemoryMax"
        )
    # MemoryLow of a tier only protects its pages when every ancestor carries at least as much
    # (cgroup v2): dev.slice's own Low is therefore the SUM of the tiers' Low values, never more.
    low, low_source = pick("DEV_MEMORY_LOW", 0)
    if low_source == "derived":
        total = 0
        for key in _TIER_LOW_KEYS:
            raw = (values.get(key, "") or "").strip()
            parsed = parse_size_bytes(raw) if raw not in ("", "-") else None
            total += parsed or 0
        low, low_source = (total, "sum of the tier MemoryLow values") if total else (None, "none")
    # Load tiers (gates, background): dev.slice's value minus the protected tiers' MemoryLow, never below 1G
    # and never above dev.slice's own. MemoryHigh is the limit that acts (throttling + reclaim, so the load
    # yields before the protected tiers' Low is touched); MemoryMax is derived the same way for consistency
    # (it stays >= the tier's MemoryHigh) although memory.max is not reachable on these hosts. memory.swap.max
    # stays equal to dev.slice's: swap is one shared pool, a smaller tier cap would only make the load die
    # earlier without returning any RAM. A DEV_{GATES,BACKGROUND}_MEMORY_{HIGH,MAX} pin wins; `-`/0 omit.
    protected = 0
    for key in _PROTECTED_LOW_KEYS:
        raw = (values.get(key, "") or "").strip()
        protected += (parse_size_bytes(raw) or 0) if raw not in ("", "-") else 0

    def derive_load(dev_value: int | None) -> int | None:
        if dev_value is None:
            return None
        return min(dev_value, max(DEDICATED_LOAD_FLOOR, dev_value - protected))

    load_high = derive_load(high)
    load_max = derive_load(maximum)
    if load_high is not None and load_max is not None:
        load_max = max(load_max, load_high)
    resolved: dict[str, tuple[int | None, str]] = {}
    for tier in ("GATES", "BACKGROUND"):
        h, hs = pick(f"DEV_{tier}_MEMORY_HIGH", load_high or 0)
        m, ms = pick(f"DEV_{tier}_MEMORY_MAX", load_max or 0)
        if hs == "derived" and load_high is None:
            h, hs = None, "omitted (dev.slice has no MemoryHigh)"
        if ms == "derived" and load_max is None:
            m, ms = None, "omitted (dev.slice has no MemoryMax)"
        # one side pinned, the other derived: the derived value follows the pin instead of refusing the pair
        if h is not None and m is not None and h > m:
            if hs == "derived":
                h, hs = m, "derived, clamped to the MemoryMax pin"
            elif ms == "derived":
                m, ms = h, "derived, raised to the MemoryHigh pin"
        if h is not None and m is not None and h > m:
            raise CapacityError(f"dev-{tier.lower()}.slice MemoryHigh {format_size(h)} ({hs}) exceeds MemoryMax {format_size(m)} ({ms}); keep MemoryHigh <= MemoryMax")
        resolved[tier] = (h, hs, m, ms)
    g, b = resolved["GATES"], resolved["BACKGROUND"]
    return DedicatedMemory(keep, ram, swap, high, maximum, high_source, max_source, low, low_source,
                           protected, load_high, load_max, g[1], g[3], b[1], b[3], g[0], g[2], b[0], b[2])


def validate_host_protected_units(value: str) -> str | None:
    """DEV_HOST_PROTECTED_UNITS: empty (the default list), `-` (none) or space-separated unit names."""
    if value.strip() in ("", "-"):
        return None
    forbidden = sorted(set(value.split()) & DEDICATED_FORBIDDEN_UNITS)
    if forbidden:
        return (
            f"{' '.join(forbidden)} cannot be protected with OOMScoreAdjust: sshd hands its own score to every login "
            "session it forks, so the whole interactive tier would become unkillable (the sshd listener already "
            "runs at -1000)"
        )
    return validate_units(value)


def dedicated_protected_units(values: dict[str, str]) -> list[str]:
    """The host services of HOST_ROLE=dedicated-dev (empty value = the default list; `-` = none)."""
    raw = (values.get("DEV_HOST_PROTECTED_UNITS", "") or "").strip()
    error = validate_host_protected_units(raw)
    if error:
        raise CapacityError(f"DEV_HOST_PROTECTED_UNITS: {error}")
    if raw == "-":
        return []
    return (raw or DEDICATED_PROTECTED_UNITS_DEFAULT).split()


def summary_line(cap: Capacity) -> str:
    return (
        f"MDT may use: RAM {format_size(cap.ram_mdt)} (overcommitted across tiers), "
        f"swap {format_size(cap.swap_mdt_bytes)}, CPU {_fmt_cores(cap.cpu_mdt)} cores. "
        "Everything else is unavailable to MDT."
    )


# --------------------------------------------------------------- cgroup tree access


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _read_size(path: Path) -> int | None:
    """A cgroup size file: bytes, ``max`` -> None, missing -> None."""
    raw = _read(path)
    if raw is None or raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_weight(path: Path) -> int | None:
    """cpu.weight is a number; io.weight is ``default N`` (plus per-device lines)."""
    raw = _read(path)
    if not raw:
        return None
    first = raw.splitlines()[0].split()
    try:
        return int(first[-1])
    except (ValueError, IndexError):
        return None


def slice_path(cg_root: Path, name: str) -> Path:
    """``a-b-c.slice`` is ``a.slice/a-b.slice/a-b-c.slice`` in cgroupfs."""
    if name == "-.slice":
        return cg_root
    stem = name[: -len(".slice")]
    parts = stem.split("-")
    path = cg_root
    for i in range(len(parts)):
        path = path / ("-".join(parts[: i + 1]) + ".slice")
    return path


def unit_path(cg_root: Path, unit: str) -> Path | None:
    """Locate a unit's cgroup directory; ``None`` when it is not present."""
    if unit.endswith(".slice"):
        path = slice_path(cg_root, unit)
        return path if path.is_dir() else None
    direct = cg_root / "system.slice" / unit
    if direct.is_dir():
        return direct
    for found in sorted(cg_root.glob(f"*/{unit}")) + sorted(cg_root.glob(f"*/*/{unit}")):
        if found.is_dir():
            return found
    return None


def _children(path: Path) -> list[Path]:
    try:
        return sorted(p for p in path.iterdir() if p.is_dir())
    except OSError:
        return []


def _own_min(path: Path) -> int:
    return _read_size(path / "memory.min") or 0


def effective_memory_min(cg_root: Path, path: Path) -> int:
    """memory.min as the kernel can honour it.

    A child's protection is capped by every ancestor below the root: when the siblings'
    memory.min sum exceeds the parent's, the kernel scales every sibling down in
    proportion (and a parent with memory.min 0 protects nothing below it).
    """
    eff = float(_own_min(path))
    node = path
    while node != cg_root and node.parent != node:
        parent = node.parent
        if parent == cg_root:
            break
        siblings = sum(_own_min(c) for c in _children(parent))
        parent_min = _own_min(parent)
        if siblings > parent_min:
            eff *= parent_min / siblings if siblings else 0.0
        node = parent
    return int(eff)


@dataclass
class UnitFacts:
    unit: str
    path: Path | None
    memory_min: int = 0
    effective_min: int = 0
    memory_current: int = 0
    swap_current: int = 0
    cpu_weight: int | None = None
    io_weight: int | None = None
    children: list[tuple[str, int]] = field(default_factory=list)


def _top_level_only(cg_root: Path, units: Sequence[str]) -> list[tuple[str, Path | None]]:
    """Drop units that live inside another named unit (their protection is counted once)."""
    located = [(u, unit_path(cg_root, u)) for u in units]
    paths = [p for _, p in located if p is not None]
    result = []
    for unit, path in located:
        if path is not None and any(o != path and o in path.parents for o in paths):
            continue
        result.append((unit, path))
    return result


def read_unit(cg_root: Path, unit: str, path: Path | None) -> UnitFacts:
    facts = UnitFacts(unit, path)
    if path is None:
        return facts
    facts.memory_min = _own_min(path)
    facts.effective_min = effective_memory_min(cg_root, path)
    facts.memory_current = _read_size(path / "memory.current") or 0
    facts.swap_current = _read_size(path / "memory.swap.current") or 0
    facts.cpu_weight = _read_weight(path / "cpu.weight")
    facts.io_weight = _read_weight(path / "io.weight")
    facts.children = [(c.name, _own_min(c)) for c in _children(path) if (c / "memory.min").exists()]
    return facts


WEIGHT_FACTOR = 4


def verify_protection(
    cg_root: Path,
    units: Sequence[str],
    excluded_ram: int,
    *,
    dev_slice: str = "dev.slice",
) -> list[str]:
    """WARN lines about the named units' protection; empty when everything holds.

    Verify only: nothing is written. The production units are operator-owned.
    """
    warnings: list[str] = []
    if not units:
        return warnings
    named = _top_level_only(cg_root, units)
    total_eff = 0
    for unit, path in named:
        if path is None:
            warnings.append(f"protected unit {unit} has no cgroup under {cg_root}; it is not running or the name is wrong")
            continue
        facts = read_unit(cg_root, unit, path)
        total_eff += facts.effective_min
        kids = sum(m for _, m in facts.children)
        if facts.children and kids > facts.memory_min:
            warnings.append(
                f"{unit}: the children's memory.min sum {format_size(kids)} exceeds the parent's "
                f"{format_size(facts.memory_min)}, so the kernel scales the children down "
                f"(effective {format_size(facts.effective_min)} of the unit)"
            )
    if excluded_ram > 0 and total_eff < excluded_ram:
        warnings.append(
            f"effective memory.min summed over MDT_PROTECTED_UNITS is {format_size(total_eff)}, "
            f"below MDT_EXCLUDED_RAM {format_size(excluded_ram)}: that RAM is not protected"
        )
    dev_path = slice_path(cg_root, dev_slice)
    dev_cpu = _read_weight(dev_path / "cpu.weight")
    dev_io = _read_weight(dev_path / "io.weight")
    for unit, path in named:
        if path is None:
            continue
        for label, mine, ref in (
            ("CPUWeight", _read_weight(path / "cpu.weight"), dev_cpu),
            ("IOWeight", _read_weight(path / "io.weight"), dev_io),
        ):
            if mine is not None and ref is not None and mine < WEIGHT_FACTOR * ref:
                warnings.append(
                    f"{unit}: {label} {mine} is below {WEIGHT_FACTOR}x that of {dev_slice} ({ref})"
                )
    return warnings


# ------------------------------------------------------------------------- probe


@dataclass
class ProbeResult:
    ram: int
    ram_protected: bool
    swap: int
    cpu_cores: float
    rows: list[dict]
    warnings: list[str]


def _cpu_usage_usec(path: Path) -> int | None:
    raw = _read(path / "cpu.stat")
    if raw is None:
        return None
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "usage_usec":
            return int(parts[1])
    return None


def _p95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


def probe_units(
    cg_root: Path,
    units: Sequence[str],
    *,
    seconds: float = 600,
    interval: float = 5,
    sleep: Callable[[float], None] = lambda s: None,
    clock: Callable[[], float] | None = None,
    on_sample: Callable[[int], None] | None = None,
) -> ProbeResult:
    """Sample the named units' cgroups and suggest the three numbers. Read-only.

    ``sleep``/``clock`` are injected so a test runs the window instantly; ``on_sample``
    lets a test mutate the fake tree between samples. Rounding: RAM up to 256M, swap up
    to 512M, CPU up to 0.5 core.
    """
    named = _top_level_only(cg_root, units)
    samples = max(1, int(seconds // interval) + 1) if seconds > 0 else 1
    cur: dict[str, list[float]] = {u: [] for u, _ in named}
    swap_max: dict[str, int] = {u: 0 for u, _ in named}
    cpu_rate: dict[str, list[float]] = {u: [] for u, _ in named}
    last_usage: dict[str, tuple[float, int]] = {}
    now = 0.0
    for i in range(samples):
        if on_sample:
            on_sample(i)
        now = clock() if clock else i * interval
        for unit, path in named:
            if path is None:
                continue
            cur[unit].append(float(_read_size(path / "memory.current") or 0))
            swap_max[unit] = max(swap_max[unit], _read_size(path / "memory.swap.current") or 0)
            usage = _cpu_usage_usec(path)
            if usage is not None:
                if unit in last_usage:
                    t0, u0 = last_usage[unit]
                    if now > t0:
                        cpu_rate[unit].append((usage - u0) / 1e6 / (now - t0))
                last_usage[unit] = (now, usage)
        if i < samples - 1:
            sleep(interval)

    rows: list[dict] = []
    warnings: list[str] = []
    ram_total = 0
    swap_total = 0
    cpu_total = 0.0
    all_protected = True
    for unit, path in named:
        if path is None:
            warnings.append(f"{unit}: no cgroup found under {cg_root}; skipped")
            continue
        facts = read_unit(cg_root, unit, path)
        protected = facts.effective_min > 0
        ram = facts.effective_min if protected else int(_p95(cur[unit]))
        if not protected:
            all_protected = False
        cpu = _p95(cpu_rate[unit])
        swap = swap_max[unit]
        ram_total += ram
        swap_total += swap
        cpu_total += cpu
        rows.append({
            "unit": unit,
            "memory_min": facts.memory_min,
            "effective_min": facts.effective_min,
            "memory_current_p95": int(_p95(cur[unit])),
            "swap_max": swap,
            "cpu_p95": cpu,
            "cpu_weight": facts.cpu_weight,
            "io_weight": facts.io_weight,
            "protected": protected,
            "stat": _memory_stat(path),
        })
    return ProbeResult(
        ram=int(round_up(ram_total, 256 * MIB)),
        ram_protected=all_protected and bool(rows),
        swap=int(round_up(swap_total, 512 * MIB)),
        cpu_cores=round_up(cpu_total, 0.5) if cpu_total > 0 else 0.0,
        rows=rows,
        warnings=warnings,
    )


def _memory_stat(path: Path) -> dict[str, int]:
    raw = _read(path / "memory.stat") or ""
    out = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("anon", "file") and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def format_probe(result: ProbeResult, excluded_ram: int = 0, cg_root: Path | None = None,
                 units: Sequence[str] = ()) -> str:
    lines = [
        "unit                      memory.min  mem p95     swap max    cpu p95   weights(cpu/io)  protected",
    ]
    for r in result.rows:
        lines.append(
            f"{r['unit']:<25} {format_size(r['effective_min']):<11} {format_size(r['memory_current_p95']):<11} "
            f"{format_size(r['swap_max']):<11} {r['cpu_p95']:<9.2f} {r['cpu_weight'] or '-'}/{r['io_weight'] or '-'}"
            f"{'':<10} {'yes' if r['protected'] else 'NO (no memory.min)'}"
        )
    ram_note = "" if result.ram_protected else "  (p95 of memory.current for unprotected units: flagged unprotected)"
    lines += [
        "",
        "Suggested numbers (nothing is applied):",
        f"  MDT_EXCLUDED_RAM={format_size(result.ram)}{ram_note}",
        f"  MDT_EXCLUDED_SWAP={format_size(result.swap)}",
        f"  MDT_EXCLUDED_CPU_CORES={_fmt_cores(Decimal(str(result.cpu_cores)))}",
    ]
    for w in result.warnings:
        lines.append(f"WARN {w}")
    if cg_root is not None and units:
        for w in verify_protection(cg_root, list(units), excluded_ram or result.ram):
            lines.append(f"WARN {w}")
    return "\n".join(lines)


# ------------------------------------------------------------- protection drop-ins

DROPIN_NAME = "50-mdt-protection.conf"


class ProtectionRefused(ValueError):
    """The protection plan cannot be made safely (e.g. no RAM figure to mirror)."""


# plan: unit -> property -> (current value as shown to the operator, new value written)
ProtectionPlan = dict[str, dict[str, tuple[str, str]]]


def plan_protection(
    cg_root: Path,
    units: Sequence[str],
    excluded_ram: int,
    *,
    dev_slice: str = "dev.slice",
    notes: list[str] | None = None,
) -> ProtectionPlan:
    """Per named unit: the MemoryMin, CPUWeight, IOWeight that would INCREASE. Reads cgroups only.

    Protection is only ever raised: the drop-in overrides the operator's own unit file, so a
    value lower than the unit's current one would silently remove production protection.

    * ``excluded_ram`` must be > 0 (``ProtectionRefused`` otherwise); a MemoryMin of 0 is never planned.
    * MemoryMin mirrors MDT_EXCLUDED_RAM, split across the named units in proportion to their
      current memory (an even split when none is readable), then raised to at least the unit's
      own current memory.min.
    * The weights are 4x the dev.slice weight (default 100), capped at 10000, never below the
      unit's own current weight.
    * A property that would not increase is left out; a unit with nothing to raise is left out.
    * A unit without a readable cgroup (not running) cannot be compared and is skipped with a note.
    """
    if excluded_ram <= 0:
        raise ProtectionRefused(
            "MDT_EXCLUDED_RAM is 0 or unreadable: there is no RAM figure to mirror, and writing "
            "MemoryMin=0 would remove existing protection. Set MDT_EXCLUDED_RAM first."
        )
    named = _top_level_only(cg_root, units)
    currents = {}
    for u, path in named:
        currents[u] = (_read_size(path / "memory.current") or 0) if path else 0
    total = sum(currents.values())
    dev_path = slice_path(cg_root, dev_slice)
    cpu_ref = _read_weight(dev_path / "cpu.weight") or 100
    io_ref = _read_weight(dev_path / "io.weight") or 100
    plan: ProtectionPlan = {}
    for u, path in named:
        if path is None:
            if notes is not None:
                notes.append(f"{u}: no cgroup found (not running?); its current values are unknown, skipped")
            continue
        share = currents[u] / total if total else 1 / max(1, len(named))
        wanted_min = int(excluded_ram * share)
        old_min = _own_min(path)
        props: dict[str, tuple[str, str]] = {}
        if wanted_min > old_min:
            props["MemoryMin"] = (format_size(old_min), format_size(wanted_min))
        for prop, filename, ref in (("CPUWeight", "cpu.weight", cpu_ref), ("IOWeight", "io.weight", io_ref)):
            old = _read_weight(path / filename)
            wanted = min(10000, WEIGHT_FACTOR * ref)
            if old is not None and wanted > old:
                props[prop] = (str(old), str(wanted))
        if props:
            plan[u] = props
        elif notes is not None:
            notes.append(f"{u}: nothing would increase; left untouched")
    return plan


def write_protection_dropins(plan: ProtectionPlan, systemd_dir: Path) -> list[Path]:
    """Write ``<unit>.d/50-mdt-protection.conf`` for exactly the planned units (new values only)."""
    written = []
    for unit, change in sorted(plan.items()):
        props = {k: new for k, (_old, new) in change.items()}
        directory = systemd_dir / f"{unit}.d"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / DROPIN_NAME
        section = "Slice" if unit.endswith(".slice") else "Service" if unit.endswith(".service") else "Scope"
        body = "# Written by mdt host-setup install.sh --apply-protection; MDT_PROTECTED_UNITS.\n"
        body += f"[{section}]\n" + "".join(f"{k}={v}\n" for k, v in props.items())
        target.write_text(body)
        written.append(target)
    return written


# --------------------------------------------------------------------------- CLI


def _meminfo_bytes(path: Path) -> tuple[int, int]:
    total = swap = 0
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "MemTotal:":
            total = int(parts[1]) * KIB
        elif len(parts) >= 2 and parts[0] == "SwapTotal:":
            swap = int(parts[1]) * KIB
    return total, swap


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            v = match.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            values[match.group(1)] = v
    return values


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="capacity unavailable to MDT: refuse old keys, verify protection")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refuse-renamed", help="exit 2 with the rename message when the env file has a retired key")
    r.add_argument("env", type=Path)
    v = sub.add_parser("verify-protection", help="print WARN lines for the protected units (verify only)")
    v.add_argument("env", type=Path)
    v.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"))
    a = sub.add_parser("apply-protection", help="opt-in: write MemoryMin/weight drop-ins for MDT_PROTECTED_UNITS")
    a.add_argument("env", type=Path)
    a.add_argument("--systemd-dir", type=Path, default=Path("/etc/systemd/system"))
    a.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"))
    a.add_argument("--yes", action="store_true", help="skip the confirmation prompt (tests)")
    c = sub.add_parser("check-config", help="print WARN lines: no disk swap for MDT, DEV_MEMORY_MAX vs RAM_MDT")
    c.add_argument("env", type=Path)
    c.add_argument("--meminfo", type=Path, default=Path("/proc/meminfo"))
    c.add_argument("--nproc", type=int, required=True)
    m = sub.add_parser("dedicated-memory", help="HOST_ROLE=dedicated-dev: print the dev.slice memory limits as KEY=VALUE lines")
    m.add_argument("env", type=Path)
    m.add_argument("--meminfo", type=Path, default=Path("/proc/meminfo"))
    d = sub.add_parser("derive", help="print the derived MDT capacity as KEY=VALUE lines")
    d.add_argument("env", type=Path)
    d.add_argument("--meminfo", type=Path, default=Path("/proc/meminfo"))
    d.add_argument("--nproc", type=int, required=True)
    args = ap.parse_args(argv)
    values = _parse_env(args.env)
    if args.cmd == "refuse-renamed":
        errors = renamed_key_errors(values)
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 2 if errors else 0
    if args.cmd == "verify-protection":
        units = values.get("MDT_PROTECTED_UNITS", "").split()
        excl = parse_size_bytes(values.get("MDT_EXCLUDED_RAM", "0") or "0") or 0
        warns = verify_protection(args.cgroup_root, units, excl)
        for w in warns:
            print(f"WARN {w}")
        return 0
    if args.cmd == "apply-protection":
        units = values.get("MDT_PROTECTED_UNITS", "").split()
        if not units:
            print("ERROR: MDT_PROTECTED_UNITS is empty; there is nothing to protect", file=sys.stderr)
            return 2
        bad = validate_units(" ".join(units))
        if bad:
            print(f"ERROR: MDT_PROTECTED_UNITS: {bad}", file=sys.stderr)
            return 2
        raw_ram = values.get("MDT_EXCLUDED_RAM", "0") or "0"
        excl = parse_size_bytes(raw_ram)
        if excl is None:
            print(f"ERROR: MDT_EXCLUDED_RAM: {validate_size_or_zero(raw_ram)}", file=sys.stderr)
            return 2
        notes: list[str] = []
        try:
            plan = plan_protection(args.cgroup_root, units, excl, notes=notes)
        except ProtectionRefused as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        for note in notes:
            print(f"note: {note}")
        if not plan:
            print("Nothing would increase; nothing was written.")
            return 0
        print("Drop-ins to write (only for the named units; protection is only raised; current -> new):")
        for unit, change in plan.items():
            print(f"  {args.systemd_dir}/{unit}.d/{DROPIN_NAME}: "
                  + " ".join(f"{k}: {old} -> {new}" for k, (old, new) in change.items()))
        if not args.yes:
            try:
                answer = input("Write these drop-ins? [y/N] ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                print("Not confirmed; nothing was written.")
                return 1
        for path in write_protection_dropins(plan, args.systemd_dir):
            print(f"wrote {path}")
        return 0
    total, swap = _meminfo_bytes(args.meminfo)
    dedicated = values.get("HOST_ROLE", "") == "dedicated-dev"
    if args.cmd == "dedicated-memory":
        try:
            mem = dedicated_memory(values, mem_total=total, swap_total=swap)
            protected = dedicated_protected_units(values)
        except CapacityError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"DEDICATED_MEM_KEEP={mem.keep}")
        print(f"DEDICATED_MEM_HIGH={'' if mem.high is None else mem.high}")
        print(f"DEDICATED_MEM_MAX={'' if mem.maximum is None else mem.maximum}")
        print(f"DEDICATED_MEM_LOW={'' if mem.low is None else mem.low}")
        print(f"DEDICATED_MEM_LOW_SOURCE={mem.low_source}")
        print(f"DEDICATED_MEM_HIGH_SOURCE={mem.high_source}")
        print(f"DEDICATED_MEM_MAX_SOURCE={mem.max_source}")
        for _tier, _vals in (("GATES", (mem.gates_high, mem.gates_max, mem.gates_high_source, mem.gates_max_source)),
                             ("BACKGROUND", (mem.background_high, mem.background_max, mem.background_high_source, mem.background_max_source))):
            print(f"DEDICATED_{_tier}_HIGH={'' if _vals[0] is None else _vals[0]}")
            print(f"DEDICATED_{_tier}_MAX={'' if _vals[1] is None else _vals[1]}")
            print(f"DEDICATED_{_tier}_HIGH_SOURCE={_vals[2]}")
            print(f"DEDICATED_{_tier}_MAX_SOURCE={_vals[3]}")
        print(f"DEDICATED_PROTECTED_UNITS={' '.join(protected)}")
        return 0
    try:
        cap = derive_from_config(values, mem_total=total, swap_total=swap, nproc=args.nproc)
    except CapacityError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    sizing = () if dedicated else tuple(memory_sizing_warnings(values, cap))
    if args.cmd == "check-config":
        for w in cap.warnings + sizing:
            print(f"WARN {w}")
        return 0
    print(f"MDT_RAM_BYTES={cap.ram_mdt}")
    print(f"MDT_SWAP_BYTES={cap.swap_mdt_bytes}")
    print(f"MDT_NO_DISK_SWAP={1 if cap.no_disk_swap else 0}")
    print(f"MDT_DEV_CPU_QUOTA={cap.dev_cpu_quota_pct}%")
    print(f"MDT_TIER_CPU_QUOTA={cap.tier_cpu_quota_pct}%")
    for w in cap.warnings + sizing:
        print(f"WARN: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
