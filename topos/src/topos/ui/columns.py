"""Column model of the single view: registry, config file, priority planning, cells.

The registry knows how each column id renders and sorts; it knows nothing about
order or priority. Those come from a TOML file (``[[column]]`` tables with ``id``,
``pri`` and optional ``width``/``head``; file order is display order), the same
format the ratatui PoC reads, so one file works in both.

A cell is ``(text, style)``. Three states are always told apart: a number, ``·``
(deliberately not set, for example no limit) and ``n/a`` (the frame has no value).
"""
from __future__ import annotations

import math
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from topos.model import EntityFrame, MetricValue

NAME_MIN = 30
NAME_MAX = 56

UNSET_TEXT = "·"
NA_TEXT = "n/a"

DIM = "bright_black"
NA_STYLE = "italic bright_black"
DEMO_STYLE = "magenta"
_BAND_STYLES = (DIM, "grey70", "yellow", "color(208)", "bold red")

Cell = tuple[str, str]
UNSET = "unset"
NA = "na"


class ColumnConfigError(ValueError):
    """The column file is unreadable or invalid."""


# ── value access ─────────────────────────────────────────────────────


def cn(ef: EntityFrame, key: str) -> float | str:
    """A metric as a float, or ``UNSET`` (unlimited) or ``NA`` (no usable value)."""
    metric = ef.metrics.get(key)
    return _cn(metric)


def _cn(metric: MetricValue | None) -> float | str:
    if metric is None:
        return NA
    if metric.src == "unlimited":
        return UNSET
    value = metric.v
    if value is None:
        return NA
    try:
        number = float(value)
    except (TypeError, ValueError):
        return NA
    return NA if math.isnan(number) else number


def num(ef: EntityFrame, key: str) -> float | None:
    value = cn(ef, key)
    return value if isinstance(value, float) else None


def n0(ef: EntityFrame, key: str) -> float:
    value = num(ef, key)
    return 0.0 if value is None else value


# ── formatting ───────────────────────────────────────────────────────


def fmt_bytes(value: float) -> str:
    units = "BKMGTP"
    x, u = abs(value), 0
    while x >= 1024.0 and u < 5:
        x /= 1024.0
        u += 1
    return f"{x:.0f}{units[u]}" if u == 0 or x >= 10.0 else f"{x:.1f}{units[u]}"


def fmt_count(value: float) -> str:
    if value >= 1e6:
        x, suffix = value / 1e6, "M"
    elif value >= 1e3:
        x, suffix = value / 1e3, "k"
    else:
        x, suffix = value, ""
    return f"{x:.0f}{suffix}" if x >= 10.0 or (not suffix and value >= 10.0) else f"{x:.1f}{suffix}"


def fmt_pct(value: float) -> str:
    return f"{value:.0f}%" if value >= 10.0 else f"{value:.1f}%"


def band(value: float, t: tuple[float, float, float]) -> int:
    """0 zero, 1 low, 2 warn, 3 high, 4 critical."""
    if value <= 0.0:
        return 0
    if value < t[0]:
        return 1
    if value < t[1]:
        return 2
    return 3 if value < t[2] else 4


def band_style(b: int) -> str:
    return _BAND_STYLES[b]


def gauge(fraction: float, width: int) -> str:
    """Left-to-right eighth-block bar, or one vertical block when *width* is 1."""
    fraction = min(1.0, max(0.0, fraction))
    if width <= 1:
        return " ▁▂▃▄▅▆▇█"[round(fraction * 8)]
    partial = " ▏▎▍▌▋▊▉"
    eighths = round(fraction * width * 8)
    out = []
    for i in range(width):
        e = min(8, max(0, eighths - i * 8))
        out.append("█" if e >= 8 else partial[e])
    return "".join(out)


def _unset() -> Cell:
    return (UNSET_TEXT, DIM)


def _na() -> Cell:
    return (NA_TEXT, NA_STYLE)


def _numcell(value: float | str, fmt: Callable[[float], tuple[str, int]]) -> Cell:
    if value == UNSET:
        return _unset()
    if isinstance(value, str):
        return _na()
    text, b = fmt(value)
    return (text, band_style(b))


def _bytes_cell(value: float | str, t: tuple[float, float, float]) -> Cell:
    return _numcell(value, lambda v: ("0" if v == 0 else fmt_bytes(v), band(v, t)))


def _count_cell(value: float | str, t: tuple[float, float, float]) -> Cell:
    return _numcell(value, lambda v: ("0" if v == 0 else fmt_count(v), band(v, t)))


def _pct_cell(value: float | str, t: tuple[float, float, float]) -> Cell:
    return _numcell(value, lambda v: ("0" if v == 0 else fmt_pct(v), band(v, t)))


def _cfg_bytes(value: float | str, style: str) -> Cell:
    """A configured limit or protection: 0 or unlimited is 'not set'."""
    if value == NA:
        return _na()
    if isinstance(value, float) and value > 0:
        return (fmt_bytes(value), style)
    return _unset()


def ceiling(ef: EntityFrame) -> float | str:
    high = cn(ef, "mem_high")
    return high if isinstance(high, float) and high > 0 else cn(ef, "mem_max")


def max_psi(ef: EntityFrame, which: str) -> float | None:
    values = [num(ef, f"psi_{r}_{which}_avg10") for r in ("cpu", "mem", "io")]
    present = [v for v in values if v is not None]
    return max(present) if present else None


# ── threshold-free helpers shared with rows and pane ─────────────────


def governance(ef: EntityFrame) -> dict:
    return ef.governance if isinstance(ef.governance, dict) else {}


def gov_summary(ef: EntityFrame) -> dict:
    summary = governance(ef).get("summary")
    return summary if isinstance(summary, dict) else {}


def drifted_limits(ef: EntityFrame) -> list[str]:
    drifted = gov_summary(ef).get("drifted_limits")
    return [str(d) for d in drifted] if isinstance(drifted, list) else []


def has_drift(ef: EntityFrame) -> bool:
    return bool(gov_summary(ef).get("drift"))


def origin_name(ef: EntityFrame) -> str:
    return str(gov_summary(ef).get("origin") or "unset")


_ORIGIN_SHORT = {
    "systemd_unit": "unit",
    "systemd_runtime_dropin": "rt-drop",
    "raw_write": "RAW",
    "docker_default": "docker",
}


def warnings(ef: EntityFrame) -> list[str]:
    """Inconsistencies between set values and effective values."""
    out: list[str] = []

    def positive(key: str) -> float | None:
        v = num(ef, key)
        return v if v is not None and v > 0 else None

    high, mx, low, mn = positive("mem_high"), positive("mem_max"), positive("mem_low"), positive("mem_min")
    if high is not None and mx is not None and high > mx:
        out.append("high>max")
    if low is not None and high is not None and low > high:
        out.append("low>high")
    eff = num(ef, "effective_memory_min")
    if mn is not None and eff is not None and eff < mn:
        out.append(f"min clamped to {fmt_bytes(eff)}")
    if has_drift(ef):
        out.append("drift")
    if num(ef, "io_max_capped") == 1.0 and n0(ef, "io_cap_saturation_pct") > 80.0:
        out.append("io cap saturated")
    if ef.findings:
        n = len(ef.findings)
        out.append(f"{n} finding{'s' if n != 1 else ''}")
    return out


# ── cells ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CellCtx:
    """What a cell needs beyond the entity frame."""

    ef: EntityFrame
    blk: int | None = None
    """Blocked (D-state) tasks owned by this cgroup; None when no source exists."""
    width: int = 0


def _c_cpu(c: CellCtx) -> Cell:
    return _numcell(cn(c.ef, "cpu_pct"), lambda v: (fmt_pct(v), band(v, (20.0, 80.0, 150.0))))


def _c_ram(c: CellCtx) -> Cell:
    ram = cn(c.ef, "ram")
    if not isinstance(ram, float):
        return _na()
    ceil = ceiling(c.ef)
    gw = max(1, c.width - 11) if c.width else 5
    if isinstance(ceil, float) and ceil > 0:
        frac = ram / ceil
        cs, g = fmt_bytes(ceil), gauge(frac, gw)
        b = max(1, band(frac * 100.0, (60.0, 85.0, 95.0)))
    else:
        cs, g, b = UNSET_TEXT, (" " + UNSET_TEXT + " " * (gw - 2))[:gw] if gw > 1 else UNSET_TEXT, 1
    return (f"{fmt_bytes(ram):>5}/{cs:<5}{g}", band_style(b))


def _c_prot(c: CellCtx) -> Cell:
    ef = c.ef
    mn, low = num(ef, "mem_min"), num(ef, "mem_low")
    if mn is not None and mn > 0:
        return (f"m{fmt_bytes(mn)}", "cyan")
    if low is not None and low > 0:
        return (f"l{fmt_bytes(low)}", "cyan")
    if num(ef, "effective_memory_min") is None and mn is None and low is None:
        return _na()
    return _unset()


def _c_stall(c: CellCtx) -> Cell:
    v = max_psi(c.ef, "full")
    return _na() if v is None else _pct_cell(v, (1.0, 5.0, 15.0))


def _c_blk(c: CellCtx) -> Cell:
    if c.blk is None:
        return _na()
    return ("0" if c.blk == 0 else str(c.blk), "magenta" if c.blk else "magenta dim")


def _c_pids(c: CellCtx) -> Cell:
    def f(v: float) -> tuple[str, int]:
        mx = num(c.ef, "pids_max")
        b = max(1, band(v / mx * 100.0, (50.0, 80.0, 95.0))) if mx is not None and mx > 0 else 1
        return (f"{v:.0f}", 0 if v == 0 else b)

    return _numcell(cn(c.ef, "pids_current"), f)


def _c_quota(c: CellCtx) -> Cell:
    q, p = cn(c.ef, "cpu_quota_us"), num(c.ef, "cpu_period_us")
    if isinstance(q, float) and p is not None and p > 0:
        return (f"{q / p * 100.0:.0f}%", "yellow")
    return _na() if q == NA else _unset()


def _c_weight(key: str) -> Callable[[CellCtx], Cell]:
    return lambda c: _numcell(cn(c.ef, key), lambda v: (f"{v:.0f}", 1 if v == 100 else 2))


def _c_cfg(key: str, style: str) -> Callable[[CellCtx], Cell]:
    return lambda c: _cfg_bytes(cn(c.ef, key), style)


def _c_eff_min(c: CellCtx) -> Cell:
    mn, eff = num(c.ef, "mem_min"), num(c.ef, "effective_memory_min")
    clamped = mn is not None and eff is not None and eff < mn
    return _cfg_bytes(cn(c.ef, "effective_memory_min"), "red" if clamped else "cyan")


def _c_origin(c: CellCtx) -> Cell:
    origin = origin_name(c.ef)
    short = _ORIGIN_SHORT.get(origin, UNSET_TEXT if origin in ("unset", "") else origin)
    style = "yellow" if origin == "raw_write" else DIM if short == UNSET_TEXT else "grey70"
    return (short, style)


def _c_drift(c: CellCtx) -> Cell:
    if has_drift(c.ef):
        return (f"▲{max(1, len(drifted_limits(c.ef)))}", "bold yellow")
    return _unset()


def _c_drift_list(c: CellCtx) -> Cell:
    return (",".join(drifted_limits(c.ef)), "yellow") if has_drift(c.ef) else _unset()


def _c_warn(c: CellCtx) -> Cell:
    w = warnings(c.ef)
    return ("; ".join(w), "yellow") if w else _unset()


def _c_pids_max(c: CellCtx) -> Cell:
    v = cn(c.ef, "pids_max")
    if isinstance(v, float):
        return (f"{v:.0f}", "yellow")
    return _na() if v == NA else _unset()


def _c_io_max(c: CellCtx) -> Cell:
    capped = cn(c.ef, "io_max_capped")
    if not isinstance(capped, float):
        return _na()
    if capped != 1.0:
        return _unset()
    sat = num(c.ef, "io_cap_saturation_pct")
    if sat is None:
        return ("cap", "yellow")
    return (f"cap {sat:.0f}%", band_style(band(sat, (50.0, 80.0, 95.0))))


def _c_ratio(c: CellCtx) -> Cell:
    return _numcell(cn(c.ef, "ratio"), lambda v: (f"{v:.1f}x", 1))


def _always_na(_: CellCtx) -> Cell:
    return _na()


def _bytes(key: str, t: tuple[float, float, float]) -> Callable[[CellCtx], Cell]:
    return lambda c: _bytes_cell(cn(c.ef, key), t)


def _count(key: str, t: tuple[float, float, float]) -> Callable[[CellCtx], Cell]:
    return lambda c: _count_cell(cn(c.ef, key), t)


def _pct(key: str, t: tuple[float, float, float]) -> Callable[[CellCtx], Cell]:
    return lambda c: _pct_cell(cn(c.ef, key), t)


# ── sort keys ────────────────────────────────────────────────────────


def _sk(key: str) -> Callable[[EntityFrame, int | None], float | None]:
    return lambda ef, blk: num(ef, key)


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    head: str
    width: int
    cell: Callable[[CellCtx], Cell]
    sort: Callable[[EntityFrame, int | None], float | None] | None = None
    left: bool = False
    demo: bool = False


def _spec(head, width, cell, sort=None, *, left=False, demo=False) -> ColumnSpec:
    return ColumnSpec(head, width, cell, sort, left, demo)


REGISTRY: dict[str, ColumnSpec] = {
    "name": _spec("NAME", NAME_MIN, lambda c: ("", ""), None, left=True),
    "cpu": _spec("CPU", 5, _c_cpu, _sk("cpu_pct")),
    "thr": _spec("THR", 4, _pct("cpu_throttled_pct", (1.0, 10.0, 30.0)), _sk("cpu_throttled_pct")),
    "ram_ceil": _spec("RAM/CEIL", 17, _c_ram, _sk("ram")),
    "prot": _spec("PROT", 6, _c_prot, _sk("mem_min")),
    "zsw": _spec("ZSW", 5, _bytes("z_pool", (1e8, 1e9, 4e9)), _sk("z_pool")),
    "ratio": _spec("RATIO", 5, _c_ratio, _sk("ratio")),
    "rf_d": _spec("RF_D", 6, _count("rf_d_per_s", (1.0, 50.0, 500.0)), _sk("rf_d_per_s")),
    "rf_z": _spec("RF_Z", 6, _count("rf_z_per_s", (1.0, 50.0, 500.0)), _sk("rf_z_per_s")),
    "rf_f": _spec("RF_F", 6, _count("rf_f_per_s", (10.0, 500.0, 5000.0)), _sk("rf_f_per_s")),
    "psi_m": _spec("PSI_M", 5, _pct("psi_mem_some_avg10", (2.0, 10.0, 30.0)), _sk("psi_mem_some_avg10")),
    "psi_i": _spec("PSI_I", 5, _pct("psi_io_some_avg10", (2.0, 10.0, 30.0)), _sk("psi_io_some_avg10")),
    "psi_c": _spec("PSI_C", 5, _pct("psi_cpu_some_avg10", (2.0, 10.0, 30.0)), _sk("psi_cpu_some_avg10")),
    "psi_mf": _spec("PSI_MF", 6, _pct("psi_mem_full_avg10", (1.0, 5.0, 15.0)), _sk("psi_mem_full_avg10")),
    "psi_if": _spec("PSI_IF", 6, _pct("psi_io_full_avg10", (1.0, 5.0, 15.0)), _sk("psi_io_full_avg10")),
    "psi_cf": _spec("PSI_CF", 6, _pct("psi_cpu_full_avg10", (1.0, 5.0, 15.0)), _sk("psi_cpu_full_avg10")),
    "io_r": _spec("IO_R", 7, _bytes("io_r_bps", (1e6, 2e7, 1e8)), _sk("io_r_bps")),
    "io_w": _spec("IO_W", 7, _bytes("io_w_bps", (1e6, 2e7, 1e8)), _sk("io_w_bps")),
    "iops_r": _spec("IOPS_R", 6, _count("io_r_iops", (100.0, 1000.0, 5000.0)), _sk("io_r_iops")),
    "iops_w": _spec("IOPS_W", 6, _count("io_w_iops", (100.0, 1000.0, 5000.0)), _sk("io_w_iops")),
    "stall": _spec("STALL", 5, _c_stall, lambda ef, blk: max_psi(ef, "full")),
    "io_max": _spec("IO.MAX", 9, _c_io_max, _sk("io_cap_saturation_pct")),
    "iocost": _spec("IOCOST", 6, _always_na),
    "blk": _spec("BLK", 4, _c_blk, lambda ef, blk: None if blk is None else float(blk)),
    "maj": _spec("MAJ", 5, _always_na),
    "net_rx": _spec("NET_RX", 7, _bytes("net_rx_bps", (1e5, 1e7, 1e8)), _sk("net_rx_bps")),
    "net_tx": _spec("NET_TX", 7, _bytes("net_tx_bps", (1e5, 1e7, 1e8)), _sk("net_tx_bps")),
    "pids": _spec("PIDS", 5, _c_pids, _sk("pids_current")),
    "anon": _spec("ANON", 6, _bytes("anon", (1e9, 4e9, 16e9)), _sk("anon")),
    "file": _spec("FILE", 6, _bytes("file", (1e9, 4e9, 16e9)), _sk("file")),
    "shmem": _spec("SHMEM", 5, _bytes("shmem", (1e8, 1e9, 4e9)), _sk("shmem")),
    "swap": _spec("SWAP", 6, _bytes("swap_disk", (1e6, 1e8, 1e9)), _sk("swap_disk")),
    "swap_max": _spec("SWAP.MAX", 8, _always_na),
    "pgscan": _spec("PGSCAN", 6, _count("pgscan_per_s", (100.0, 5000.0, 50000.0)), _sk("pgscan_per_s")),
    "pgsteal": _spec("PGSTEAL", 7, _count("pgsteal_per_s", (100.0, 5000.0, 50000.0)), _sk("pgsteal_per_s")),
    "origin": _spec("ORIGIN", 8, _c_origin, None, left=True),
    "drift": _spec("DRIFT", 5, _c_drift, lambda ef, blk: float(len(drifted_limits(ef)))),
    "cpu_wt": _spec("CPU.W", 6, _c_weight("cpu_weight"), _sk("cpu_weight")),
    "quota": _spec("QUOTA", 6, _c_quota, _sk("cpu_quota_us")),
    "mem_min": _spec("MIN", 7, _c_cfg("mem_min", "cyan"), _sk("mem_min")),
    "mem_low": _spec("LOW", 7, _c_cfg("mem_low", "cyan"), _sk("mem_low")),
    "mem_high": _spec("HIGH", 8, _c_cfg("mem_high", "yellow"), _sk("mem_high")),
    "mem_max": _spec("MAX", 7, _c_cfg("mem_max", "yellow"), _sk("mem_max")),
    "eff_min": _spec("MIN.EFF", 7, _c_eff_min, _sk("effective_memory_min")),
    "io_wt": _spec("IO.W", 5, _c_weight("io_weight"), _sk("io_weight")),
    "pids_max": _spec("PIDS.MAX", 8, _c_pids_max, _sk("pids_max")),
    "drift_list": _spec("DRIFTED", 22, _c_drift_list, lambda ef, blk: float(len(drifted_limits(ef))), left=True),
    "warn": _spec("INCONSISTENCY", 30, _c_warn, lambda ef, blk: float(len(warnings(ef))), left=True),
    "ev_high": _spec("EV_HIGH", 7, _count("mem_events_high_per_s", (1.0, 50.0, 500.0)), _sk("mem_events_high_per_s")),
    "ev_max": _spec("EV_MAX", 6, _count("mem_events_max_per_s", (0.1, 1.0, 10.0)), _sk("mem_events_max_per_s")),
    "ev_oom": _spec("EV_OOM", 6, _count("mem_events_oom_kill_per_s", (0.001, 0.01, 0.1)), _sk("mem_events_oom_kill_per_s")),
}

# ``blk`` is the only column whose source is not the frame: it is n/a until a
# process source (or the demo overlay) provides it.
OVERLAY_COLUMNS = frozenset({"blk"})


# ── config file ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Column:
    id: str
    head: str
    width: int
    pri: int

    @property
    def spec(self) -> ColumnSpec:
        return REGISTRY[self.id]


def default_columns_text() -> str:
    return resources.files("topos").joinpath("assets/columns.toml").read_text(encoding="utf-8")


def parse_columns(text: str, *, origin: str = "columns") -> list[Column]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ColumnConfigError(f"{origin}: syntax error: {exc}") from exc
    entries = data.get("column")
    if not isinstance(entries, list) or not entries:
        raise ColumnConfigError(f"{origin}: no [[column]] tables")
    out: list[Column] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ColumnConfigError(f"{origin}: column {index} is not a table")
        for key in entry:
            if key not in {"id", "pri", "width", "head"}:
                raise ColumnConfigError(f"{origin}: column {index}: unknown key {key!r}")
        ident = entry.get("id")
        if not isinstance(ident, str):
            raise ColumnConfigError(f"{origin}: column {index} has no id")
        spec = REGISTRY.get(ident)
        if spec is None:
            raise ColumnConfigError(f"{origin}: unknown column id {ident!r}; known: {', '.join(REGISTRY)}")
        if ident in seen:
            raise ColumnConfigError(f"{origin}: column {ident!r} listed twice")
        seen.add(ident)
        pri = entry.get("pri")
        if isinstance(pri, bool) or not isinstance(pri, int) or pri < 1:
            raise ColumnConfigError(f"{origin}: column {ident!r} needs an integer pri >= 1")
        width = entry.get("width", spec.width)
        if isinstance(width, bool) or not isinstance(width, int) or width < 1:
            raise ColumnConfigError(f"{origin}: column {ident!r} width must be a positive integer")
        head = entry.get("head", spec.head)
        if not isinstance(head, str) or not head:
            raise ColumnConfigError(f"{origin}: column {ident!r} head must be a non-empty string")
        out.append(Column(ident, head, width, pri))
    if out[0].id != "name":
        raise ColumnConfigError(f"{origin}: the first column must be 'name'")
    return out


def load_columns(path: Path | None) -> list[Column]:
    """The columns from *path*, or the shipped file when *path* is None."""
    if path is None:
        return parse_columns(default_columns_text(), origin="shipped columns.toml")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ColumnConfigError(f"{path}: cannot read: {exc}") from exc
    return parse_columns(text, origin=str(path))


@dataclass(frozen=True, slots=True)
class Plan:
    columns: tuple[Column, ...]
    widths: tuple[int, ...]
    """Display width per kept column (name takes the leftover)."""
    dropped: int
    total: int


def plan(columns: list[Column], available: int) -> Plan:
    """Keep columns by priority while they fit; survivors keep file order.

    The first column that does not fit ends the plan, so a lower-priority column
    never jumps ahead of a higher-priority one. ``name`` is always kept and takes
    what is left (up to ``NAME_MAX``). Nothing is ever cut off at the right edge.
    """
    name_min = min(columns[0].width, available) if columns else 0
    order = sorted(range(len(columns)), key=lambda i: (columns[i].pri, i))
    keep = [False] * len(columns)
    used = name_min
    for i in order:
        if columns[i].id == "name":
            keep[i] = True
        elif used + 1 + columns[i].width <= available:
            used += 1 + columns[i].width
            keep[i] = True
        else:
            break
    name_w = min(name_min + max(0, available - used), max(NAME_MAX, name_min))
    kept = tuple(c for c, k in zip(columns, keep) if k)
    widths = tuple(name_w if c.id == "name" else c.width for c in kept)
    return Plan(kept, widths, len(columns) - len(kept), len(columns))


SORTABLE_NAME = "name"


def sort_value(column_id: str, ef: EntityFrame, blk: int | None) -> float | None:
    spec = REGISTRY.get(column_id)
    if spec is None or spec.sort is None:
        return None
    return spec.sort(ef, blk)


def is_sortable(column_id: str) -> bool:
    spec = REGISTRY.get(column_id)
    return column_id == SORTABLE_NAME or (spec is not None and spec.sort is not None)
