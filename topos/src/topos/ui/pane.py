"""Side pane: the config chain of the cursor row with effective values, origin, drift, findings."""
from __future__ import annotations

from rich.text import Text

from topos.model import EntityFrame

from .columns import (
    DEMO_STYLE,
    DIM,
    NA,
    REGISTRY,
    UNSET,
    UNSET_TEXT,
    CellCtx,
    cn,
    fmt_bytes,
    fmt_count,
    fmt_pct,
    governance,
    warnings,
)
from .meters import HEAD, PLAIN
from .overlay import ProcRow

_ORDER = ("cpu_weight", "io_weight", "mem_min", "mem_low", "mem_high", "mem_max")


def _val(name: str, value: float | None, src: str | None) -> str:
    if src == "unlimited":
        return f"{UNSET_TEXT} (unset)"
    if value is None:
        return "n/a"
    return f"{value:.0f}" if name.endswith("weight") else fmt_bytes(float(value))


def _g(ef: EntityFrame, key: str, fmt) -> str:
    v = cn(ef, key)
    if v == UNSET:
        return UNSET_TEXT
    if v == NA or isinstance(v, str):
        return "n/a"
    return fmt(v)


def _line(*parts: tuple[str, str]) -> Text:
    out = Text()
    for s, style in parts:
        out.append(s, style)
    return out


def _kv(k: str, v: str) -> Text:
    return _line((f"{k:<9}", DIM), (v, PLAIN))


def pane_lines(ef: EntityFrame, *, procs: list[ProcRow] | None = None, blk: int | None = None, overlay_label: str = "") -> list[Text]:
    e = ef.entity
    out: list[Text] = [_line((e.key or "/ (host root)", "bold"))]
    out.append(_kv("kind", f"{e.kind}  tier {e.tier or UNSET_TEXT}  protected {'yes' if e.is_protected else 'no'}"))
    if e.docker is not None:
        out.append(_kv("docker", f"{e.docker.name}  {e.docker.image}"))
    if e.ciu is not None and e.ciu.stack:
        out.append(_kv("ciu", e.ciu.stack))
    gov = governance(ef)
    summary = gov.get("summary") if isinstance(gov.get("summary"), dict) else {}
    if summary.get("unit"):
        out.append(_kv("unit", str(summary["unit"])))
    out.append(Text(""))
    out.append(_line(("CONFIG CHAIN  live  <- set (origin)", HEAD)))
    limits = gov.get("limits") if isinstance(gov.get("limits"), dict) else {}
    for name in _ORDER:
        lim = limits.get(name)
        if not isinstance(lim, dict):
            continue
        live = _val(name, lim.get("live_value"), lim.get("live_src"))
        rec = lim.get("recorded_value")
        set_part = f" <- {_val(name, rec, None)}" if rec is not None else ""
        origin = lim.get("recorded_origin") or lim.get("origin") or ""
        drift = bool(lim.get("drift"))
        out.append(_line((f"{name:<9} ", DIM), (f"{live}{set_part}  ({origin})", "yellow" if drift else PLAIN),
                         (" DRIFT", "bold red") if drift else ("", "")))
        if drift:
            out.append(_line((f"  {lim.get('reason', '')}", "yellow")))
    eff = gov.get("effective_memory_min") if isinstance(gov.get("effective_memory_min"), dict) else {}
    out.append(_line(("EFFECTIVE memory.min", HEAD)))
    out.append(_line((str(eff.get("reason") or "n/a"), PLAIN)))
    path = eff.get("path")
    if isinstance(path, list) and path:
        chain = []
        for p in path:
            if isinstance(p, dict):
                key = str(p.get("key") or "")
                v = p.get("value")
                chain.append(f"{key.rsplit('/', 1)[-1] or '/'}={'n/a' if v is None else fmt_bytes(float(v))}")
        out.append(_line((f"chain: {' > '.join(chain)}", DIM)))
    for w in warnings(ef):
        out.append(_line((f"! {w}", "yellow")))
    for f in ef.findings:
        out.append(_line((f"[{f.severity}] {f.message}", "yellow")))
        if f.remedy:
            out.append(_line((f"  fix: {f.remedy}", DIM)))
    out.append(Text(""))
    out.append(_line(("USE", HEAD)))
    quota = REGISTRY["quota"].cell(CellCtx(ef))[0]
    out.append(_kv("cpu", f"{_g(ef, 'cpu_pct', fmt_pct)}  throttled {_g(ef, 'cpu_throttled_pct', fmt_pct)}  quota {quota}"))
    out.append(_kv("memory", f"ram {_g(ef, 'ram', fmt_bytes)}  anon {_g(ef, 'anon', fmt_bytes)}  file {_g(ef, 'file', fmt_bytes)}"))
    out.append(_kv("", f"shmem {_g(ef, 'shmem', fmt_bytes)}  swap {_g(ef, 'swap_disk', fmt_bytes)}  zswap {_g(ef, 'z_pool', fmt_bytes)}"))
    out.append(_kv("headroom", f"to high {_g(ef, 'headroom_high_pct', fmt_pct)}  to max {_g(ef, 'headroom_max_pct', fmt_pct)}"))
    out.append(_kv("psi", "c {}/{}  m {}/{}  i {}/{}".format(*(_g(ef, f"psi_{r}_{w}_avg10", fmt_pct) for r in ("cpu", "mem", "io") for w in ("some", "full")))))
    out.append(_kv("io", f"r {_g(ef, 'io_r_bps', fmt_bytes)}  w {_g(ef, 'io_w_bps', fmt_bytes)}  iops {_g(ef, 'io_r_iops', fmt_count)}/{_g(ef, 'io_w_iops', fmt_count)}"))
    label = (ef.network or {}).get("source_label") if isinstance(ef.network, dict) else None
    out.append(_kv("net", f"rx {_g(ef, 'net_rx_bps', fmt_bytes)}  tx {_g(ef, 'net_tx_bps', fmt_bytes)}  ({label or 'n/a'})"))
    out.append(_kv("pids", f"{_g(ef, 'pids_current', lambda v: f'{v:.0f}')} of {_g(ef, 'pids_max', lambda v: f'{v:.0f}')}"))
    out.append(_kv("reclaim", f"scan {_g(ef, 'pgscan_per_s', fmt_count)}/s  steal {_g(ef, 'pgsteal_per_s', fmt_count)}/s  rf d/f {_g(ef, 'rf_d_per_s', fmt_count)}/{_g(ef, 'rf_f_per_s', fmt_count)}"))
    out.append(_kv("events", f"high {_g(ef, 'mem_events_high_per_s', fmt_count)}/s max {_g(ef, 'mem_events_max_per_s', fmt_count)}/s oom {_g(ef, 'mem_events_oom_kill_per_s', fmt_count)}/s"))
    if procs:
        out.append(Text(""))
        out.append(_line((f"PROCESSES ({overlay_label}), D-state {blk or 0}", DEMO_STYLE)))
        for p in procs[:6]:
            out.append(_line((f"  {p.state} {p.comm:<14} {p.pid:>6} {fmt_pct(p.cpu)}", DEMO_STYLE)))
    return out


def pane_text(lines: list[Text]) -> Text:
    out = Text()
    for i, line in enumerate(lines):
        if i:
            out.append("\n")
        out.append_text(line)
    return out

