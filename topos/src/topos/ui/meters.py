"""Meters area above the tree: only what the frame holds, ``n/a`` for the rest."""
from __future__ import annotations

from collections.abc import Callable, Sequence

from rich.cells import cell_len
from rich.text import Text

from topos.model import Frame

from .columns import (
    DEMO_STYLE,
    DIM,
    NA_STYLE,
    band,
    band_style,
    fmt_bytes,
    fmt_count,
    fmt_pct,
)
from .overlay import Overlay

HEAD = "bold cyan"
PLAIN = "grey70"

Block = list[tuple[int, Text]]
"""Lines with a drop priority (1 is kept longest)."""

_SPARK = "▁▂▃▄▅▆▇█"


def meter_height(rows: int) -> int:
    if rows >= 44:
        return 9
    if rows >= 28:
        return 8
    if rows >= 18:
        return 6
    return 4 if rows >= 12 else 0


def _t(*parts: tuple[str, str]) -> Text:
    out = Text(no_wrap=True)
    for s, style in parts:
        out.append(s, style)
    return out


def _host(frame: Frame, key: str) -> float | None:
    m = frame.host.get(key)
    if m is None or m.v is None or m.src in ("unavail_perm", "unavail_kernel"):
        return None
    try:
        return float(m.v)
    except (TypeError, ValueError):
        return None


def _opt(v: float | None, f: Callable[[float], str]) -> str:
    return "n/a" if v is None else f(v)


def _uptime(s: float) -> str:
    d, h, m = int(s // 86400), int(s % 86400 // 3600), int(s % 3600 // 60)
    return f"{d}d{h}h" if d else f"{h}h{m:02d}m"


def spark(values: Sequence[float], width: int, floor: float) -> str:
    if not values:
        return ""
    top = max(floor, max(values))
    tail = list(values)[-width:]
    return "".join(_SPARK[min(7, max(0, round(v / top * 7)))] for v in tail)


def stacked_bar(width: int, segments: Sequence[tuple[float, str]], total: float) -> Text:
    out = Text(no_wrap=True)
    acc, prev = 0.0, 0
    for value, color in segments:
        acc += max(0.0, value)
        upto = round(min(1.0, acc / max(total, 1e-9)) * width)
        if upto > prev:
            out.append("█" * (upto - prev), color)
            prev = upto
    if prev < width:
        out.append("·" * (width - prev), DIM)
    return out


def _top_level_sum(frame: Frame, key: str) -> float | None:
    values = []
    for ef in frame.entities.values():
        parent = ef.entity.parent
        if ef.entity.key and (parent is None or parent == "" or parent not in frame.entities):
            m = ef.metrics.get(key)
            if m is not None and m.v is not None:
                values.append(float(m.v))
    return sum(values) if values else None


def _cpu_block(frame: Frame, each: int, overlay: Overlay | None) -> Block:
    busy = _top_level_sum(frame, "cpu_pct")
    cores = None if busy is None else busy / 100.0
    lines: Block = [
        (1, _t(("CPU ", HEAD),
               (f"{_opt(cores, lambda c: f'{c:.1f} cores')} busy  ", PLAIN),
               (f"load {_opt(_host(frame, 'host_load1'), lambda v: f'{v:.2f}')} "
                f"{_opt(_host(frame, 'host_load5'), lambda v: f'{v:.2f}')} "
                f"{_opt(_host(frame, 'host_load15'), lambda v: f'{v:.2f}')}  "
                f"up {_opt(_host(frame, 'host_uptime_s'), _uptime)}", PLAIN)))
    ]
    cpus = overlay.per_cpu(frame) if overlay is not None else None
    if cpus is None:
        lines.append((3, _t(("per-CPU ", PLAIN), ("n/a", NA_STYLE), (" (no per-CPU source)", DIM))))
        lines.append((2, _t(("run ", PLAIN), ("n/a", NA_STYLE), ("  blocked ", PLAIN), ("n/a", NA_STYLE))))
        return lines
    tag = overlay.label if overlay is not None else ""
    lines.append((3, _t((f"per-CPU {tag}({len(cpus)}) ", DEMO_STYLE), ("us", "green"), (" sy", "red"), (" io", "blue"), (" st", "magenta"))))
    bw = max(6, min(22, (each - 12) // 2))
    for r in range(0, len(cpus), 2):
        line = Text(no_wrap=True)
        for k, c in enumerate(cpus[r:r + 2]):
            line.append(f"{r + k:>2}", PLAIN)
            line.append("[", DIM)
            line.append_text(stacked_bar(bw, list(zip(c, ("green", "red", "blue", "magenta"))), 1.0))
            line.append("]", DIM)
            line.append(f"{sum(c) * 100:>3.0f}% ", DEMO_STYLE)
        lines.append((4 + r // 4, line))
    counts = overlay.counts(frame) if overlay is not None else None
    if counts is not None:
        lines.append((2, _t((f"run {counts[0]}  D-state {counts[1]} {tag}", DEMO_STYLE))))
    return lines


def _mem_block(frame: Frame, each: int) -> Block:
    total, avail = _host(frame, "host_mem_total"), _host(frame, "host_mem_available")
    anon, file_, shmem = (_top_level_sum(frame, k) for k in ("anon", "file", "shmem"))
    pool = _host(frame, "host_zswap_pool")
    lines: Block = []
    if total is None:
        lines.append((1, _t(("MEM ", HEAD), ("n/a", NA_STYLE))))
    else:
        used = None if avail is None else max(0.0, total - avail)
        lines.append((1, _t(("MEM ", HEAD), (f"{_opt(used, fmt_bytes)} used of {fmt_bytes(total)}  avail {_opt(avail, fmt_bytes)}", PLAIN))))
        bar = _t(("[", DIM))
        bar.append_text(stacked_bar(max(8, each - 2), [(anon or 0.0, "green"), (file_ or 0.0, "blue"), (shmem or 0.0, "yellow"), (pool or 0.0, "magenta")], total))
        bar.append("]", DIM)
        lines.append((1, bar))
        legend = _t(
            ("■", "green"), (f"anon {_opt(anon, fmt_bytes)} ", PLAIN), ("■", "blue"), (f"file {_opt(file_, fmt_bytes)} ", PLAIN),
            ("■", "yellow"), (f"shm {_opt(shmem, fmt_bytes)} ", PLAIN), ("■", "magenta"), (f"zswap {_opt(pool, fmt_bytes)} ", PLAIN),
            (f"·free {_opt(avail, fmt_bytes)}", PLAIN),
        )
        lines.append((3, legend))
    stored, ratio = _host(frame, "host_zswap_stored"), _host(frame, "host_zswap_ratio")
    pct = None if pool is None or not total else pool / total * 100.0
    lines.append((2, _t(("ZSWAP ", HEAD), (
        f"{_opt(pool, fmt_bytes)} ← {_opt(stored, fmt_bytes)}  ratio {_opt(ratio, lambda r: f'{r:.2f}x')}  "
        f"pool {_opt(pct, lambda p: f'{p:.1f}%')} of {_opt(_host(frame, 'host_zswap_max_pool_percent'), lambda v: f'{v:.0f}')}%", PLAIN))))
    lines.append((5, _t(("  in/out/wb/rej ", PLAIN), ("n/a", NA_STYLE), (" (no zswap rate source)", DIM))))
    st, sf = _host(frame, "host_swap_total"), _host(frame, "host_swap_free")
    used = None if st is None or sf is None else st - sf
    pct = None if used is None or not st else used / st * 100.0
    lines.append((2, _t(("SWAP ", HEAD), (
        f"{_opt(used, fmt_bytes)} of {_opt(st, fmt_bytes)} used ({_opt(pct, fmt_pct)})  cached {_opt(_host(frame, 'host_swapcached'), fmt_bytes)}", PLAIN))))
    return lines


def _io_block(frame: Frame, trend: Callable[[str], list[float]]) -> Block:
    lines: Block = [(2, _t(("PSI ", HEAD), ("avg10 some / full   trend", PLAIN)))]
    for label, res, has_full in (("cpu", "cpu", False), ("mem", "mem", True), ("io ", "io", True)):
        some = _host(frame, f"host_psi_{res}_some_avg10")
        full = _host(frame, f"host_psi_{res}_full_avg10") if has_full else None
        st = band_style(band(some or 0.0, (2.0, 10.0, 30.0)))
        line = _t((f"{label} ", PLAIN), (f"{_opt(some, fmt_pct):>6}", NA_STYLE if some is None else st), (" / ", PLAIN))
        line.append(f"{('' if not has_full else _opt(full, fmt_pct)):<6}", band_style(band(full or 0.0, (1.0, 5.0, 15.0))))
        line.append(spark(trend(f"host_psi_{res}_some_avg10"), 20, 5.0), st)
        lines.append((2, line))
    meta = frame.host_meta or {}
    for dev in list(meta.get("block_devices") or [])[:3]:
        if not isinstance(dev, dict):
            continue
        g = dev.get
        lines.append((3, _t(("IO ", HEAD), (
            f"{g('name', '?')} r {_opt(g('read_bps'), fmt_bytes)} w {_opt(g('write_bps'), fmt_bytes)}  "
            f"{_opt(g('read_iops'), fmt_count)}/{_opt(g('write_iops'), fmt_count)} iops  await n/a util n/a", PLAIN))))
    vrate = _host(frame, "host_io_cost_vrate_pct")
    if vrate is not None:
        lines.append((4, _t(("IO.COST ", HEAD), (f"vrate {vrate:.0f}%", PLAIN))))
    arc = _host(frame, "host_zfs_arc_size")
    if arc is not None:
        lines.append((5, _t(("ARC ", HEAD), (f"{fmt_bytes(arc)} of {_opt(_host(frame, 'host_zfs_arc_max'), fmt_bytes)}  hit {_opt(_host(frame, 'host_zfs_arc_hit_ratio'), lambda v: f'{v:.2f}')}", PLAIN))))
    gpus = _host(frame, "host_gpu_count")
    if gpus:
        lines.append((5, _t(("GPU ", HEAD), (f"{gpus:.0f}x busy {_opt(_host(frame, 'host_gpu_busy_pct'), fmt_pct)}  vram {_opt(_host(frame, 'host_gpu_vram_used'), fmt_bytes)}/{_opt(_host(frame, 'host_gpu_vram_total'), fmt_bytes)}", PLAIN))))
    for dev in list(meta.get("net_devices") or [])[:2]:
        if isinstance(dev, dict):
            lines.append((3, _t(("NET ", HEAD), (f"{dev.get('name', '?')} rx {_opt(dev.get('rx_bps'), fmt_bytes)} tx {_opt(dev.get('tx_bps'), fmt_bytes)}", PLAIN))))
    return lines


def _fit(block: Block, n: int) -> list[Text]:
    block = list(block)
    while len(block) > n:
        worst = max(p for p, _ in block)
        idx = max(i for i, (p, _) in enumerate(block) if p == worst)
        del block[idx]
    return [t for _, t in block]


def render_meters(
    frame: Frame,
    *,
    width: int,
    height: int,
    trend: Callable[[str], list[float]],
    overlay: Overlay | None = None,
) -> Text:
    """The meters as one multi-line ``Text`` of at most *height* lines."""
    if height <= 0 or width <= 0:
        return Text("")
    ncols = 3 if width >= 150 else 2 if width >= 100 else 1
    each = max(10, width // ncols - 2)
    cpu, mem, io = _cpu_block(frame, each, overlay), _mem_block(frame, each), _io_block(frame, trend)
    sets = [cpu, mem, io] if ncols == 3 else [cpu, mem + io] if ncols == 2 else [cpu + mem + io]
    cols = [_fit(s, height) for s in sets]
    out = Text(no_wrap=True, overflow="crop")
    for i in range(height):
        for j, col in enumerate(cols):
            cell = col[i] if i < len(col) else Text("")
            cell = cell.copy()
            cell.truncate(each, overflow="crop")
            if j < len(cols) - 1:
                cell.append(" " * max(0, each + 2 - cell_len(cell.plain)))
            out.append_text(cell)
        if i < height - 1:
            out.append("\n")
    return out


def host_verdict(frame: Frame, config) -> str:
    """``OK``/``WARN``/``CRIT`` from the host PSI gauges against the configured bands."""
    full = config.threshold_band("psi_full_avg10", warn=1.0, crit=2.0)
    some = config.threshold_band("psi_some_avg10", warn=5.0, crit=15.0)
    watched = (
        ("host_psi_mem_full_avg10", full), ("host_psi_io_full_avg10", full),
        ("host_psi_mem_some_avg10", some), ("host_psi_io_some_avg10", some), ("host_psi_cpu_some_avg10", some),
    )
    verdict = "OK"
    for name, bands in watched:
        v = _host(frame, name)
        if v is None:
            continue
        if v >= bands.crit:
            return "CRIT"
        if v >= bands.warn:
            verdict = "WARN"
    return verdict


def unprivileged_count(frame: Frame) -> int:
    total = sum(1 for m in frame.host.values() if m.src == "unavail_perm")
    for ef in frame.entities.values():
        total += sum(1 for m in ef.metrics.values() if m.src == "unavail_perm")
    return total
