from __future__ import annotations

from dataclasses import dataclass

from rich.table import Table
from rich.text import Text

from groop.config import GroopConfig
from groop.model import Entity, EntityFrame, Frame, MetricValue
from groop.registry import REGISTRY

PROFILE_ORDER = ("auto", "triage", "memory", "network", "governance", "damon", "wide", "minimal")
SORT_ORDER = ("pressure", "ram", "cpu_pct", "name")

_WIDTH_TIERS = (
    (80, ("name", "ram", "rf_d_per_s", "psi_mem_full_avg10", "cpu_pct")),
    (100, ("anon", "z_pool", "z_eq", "ratio", "rf_z_per_s", "rf_f_per_s")),
    (120, ("swap_disk", "headroom_max_pct", "tier", "pids_current")),
    (160, ("io_r_bps", "io_w_bps", "net_rx_bps", "net_tx_bps", "net_source")),
    (200, ("file", "psi_io_some_avg10", "psi_cpu_some_avg10", "mem_high", "mem_max")),
)

_PROFILE_COLUMNS = {
    "triage": ("name", "pressure", "ram", "cpu_pct", "psi_mem_full_avg10", "psi_io_some_avg10", "rf_d_per_s", "io_r_bps", "io_w_bps", "net_rx_bps", "net_tx_bps", "net_source"),
    "memory": ("name", "ram", "anon", "file", "shmem", "z_pool", "z_eq", "ratio", "swap_disk", "rf_z_per_s", "rf_d_per_s", "rf_f_per_s", "pgscan_per_s", "pgsteal_per_s"),
    "network": ("name", "net_rx_bps", "net_tx_bps", "net_source", "pids_current", "sock"),
    "governance": ("name", "mem_min", "mem_low", "mem_high", "mem_max", "cpu_weight", "governance_origin", "governance_drift", "pids_current"),
    "damon": ("name", "damon_mode", "damon_hot_pct", "damon_warm_pct", "damon_cold_pct", "damon_idle_pct", "damon_sample_age_s"),
    "minimal": ("name", "ram", "rf_d_per_s", "psi_mem_full_avg10", "cpu_pct"),
}

_LABELS = {
    "name": "NAME",
    "tier": "TIER",
    "pressure": "PRESSURE",
    "ram": "RAM",
    "anon": "ANON",
    "file": "FILE",
    "shmem": "SHMEM",
    "z_pool": "Z_POOL",
    "z_eq": "Z_EQ",
    "ratio": "RATIO",
    "swap_disk": "SWAP",
    "rf_z_per_s": "RF_Z/S",
    "rf_d_per_s": "RF_D/S",
    "rf_f_per_s": "RF_F/S",
    "cpu_pct": "CPU%",
    "cpu_weight": "CPU.W",
    "psi_mem_full_avg10": "PSI_MEM",
    "psi_io_some_avg10": "PSI_IO",
    "psi_cpu_some_avg10": "PSI_CPU",
    "io_r_bps": "IO_R",
    "io_w_bps": "IO_W",
    "io_max_capped": "IO_CAP",
    "net_rx_bps": "NET_RX",
    "net_tx_bps": "NET_TX",
    "net_rx_pps": "RX_PPS",
    "net_tx_pps": "TX_PPS",
    "net_source": "NET_SRC",
    "headroom_max_pct": "HEAD%",
    "pids_current": "PIDS",
    "mem_min": "MEM.MIN",
    "mem_low": "MEM.LOW",
    "mem_high": "MEM.HIGH",
    "mem_max": "MEM.MAX",
    "governance_origin": "ORIGIN",
    "governance_drift": "DRIFT",
    "pgscan_per_s": "PGSCAN",
    "pgsteal_per_s": "PGSTEAL",
    "sock": "SOCK",
    "damon_mode": "DAMON",
    "damon_hot_bytes": "HOT_B",
    "damon_warm_bytes": "WARM_B",
    "damon_cold_bytes": "COLD_B",
    "damon_idle_bytes": "IDLE_B",
    "damon_hot_pct": "HOT%",
    "damon_warm_pct": "WARM%",
    "damon_cold_pct": "COLD%",
    "damon_idle_pct": "IDLE%",
    "damon_sample_age_s": "AGE_S",
}


@dataclass(frozen=True)
class RenderedRows:
    table: Table
    row_keys: tuple[str, ...]
    title: str


def render_container_table(
    frame: Frame,
    config: GroopConfig,
    *,
    width: int,
    profile: str,
    sort_by: str,
    filter_text: str,
    selected_key: str | None,
) -> RenderedRows:
    columns = resolve_columns(config, width=width, profile=profile)
    entity_frames = _visible_entities(frame, container_only=True, filter_text=filter_text)
    ordered = _sort_rows(entity_frames, sort_by)
    table = _make_table(columns, title=f"CONTAINERS | profile={profile} | sort={sort_by or 'name'}")
    row_keys: list[str] = []
    for entity_frame in ordered:
        row_keys.append(entity_frame.entity.key)
        table.add_row(*_row_cells(columns, entity_frame, selected=entity_frame.entity.key == selected_key))
    if not row_keys:
        table.add_row("no container rows", *[""] * (max(0, len(columns) - 1)))
    return RenderedRows(table=table, row_keys=tuple(row_keys), title=table.title or "")


def resolve_columns(config: GroopConfig, *, width: int, profile: str) -> tuple[str, ...]:
    profile = normalize_profile_name(config, profile)
    if profile == "auto":
        active: list[str] = []
        for minimum, names in _WIDTH_TIERS:
            if width >= minimum:
                active.extend(names)
        return tuple(_dedupe(("name", *active)))
    if profile == "wide":
        all_columns: list[str] = ["name"]
        for _, names in _WIDTH_TIERS:
            all_columns.extend(names)
        return tuple(_dedupe(all_columns))
    configured = _profile_from_config(config, profile)
    if configured is not None:
        return tuple(_dedupe(name for name in configured if _column_supported(name)))
    return tuple(_dedupe(name for name in _PROFILE_COLUMNS.get(profile, _PROFILE_COLUMNS["triage"]) if _column_supported(name)))


def available_profiles(config: GroopConfig) -> tuple[str, ...]:
    ordered = list(PROFILE_ORDER)
    for name in ((config.columns or {}).get("profiles", {}) or {}):
        if name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def normalize_profile_name(config: GroopConfig, profile: str | None) -> str:
    requested = profile or config.default_column_profile
    available = set(available_profiles(config))
    if requested in available:
        return requested
    fallback = config.default_column_profile
    if fallback in available:
        return fallback
    return "auto"


def header_label(column_name: str) -> str:
    label = _LABELS.get(column_name, column_name.upper())
    spec = REGISTRY.get(column_name)
    if spec is None:
        return label
    suffix = {
        "kernel_subtree": "subtree",
        "local_only": "local",
        "child_sum": "agg",
    }.get(spec.branch_policy)
    return label if suffix is None else f"{label}[{suffix}]"


def display_name(entity: Entity) -> str:
    if entity.docker is not None:
        return entity.docker.name
    if not entity.key:
        return "/"
    return entity.key.rsplit("/", 1)[-1]


def format_metric_value(column_name: str, entity_frame: EntityFrame) -> Text:
    if column_name == "name":
        return Text(display_name(entity_frame.entity))
    if column_name == "tier":
        return Text(entity_frame.entity.tier or "-")
    if column_name == "net_source":
        network = entity_frame.network or {}
        label = str(network.get("source_label") or "net:N/A")
        aggregation = str(network.get("aggregation") or "none")
        if aggregation not in {"exact", "none"}:
            label = f"{label}:{aggregation}"
        return Text(label)
    if column_name == "governance_origin":
        summary = (entity_frame.governance or {}).get("summary", {})
        return Text(str(summary.get("origin") or _metric_code(entity_frame.metrics.get(column_name), _GOVERNANCE_ORIGIN)))
    if column_name == "governance_drift":
        summary = (entity_frame.governance or {}).get("summary", {})
        severity = str(summary.get("severity") or _metric_code(entity_frame.metrics.get(column_name), _GOVERNANCE_DRIFT))
        if summary.get("drift") is False and severity == "none":
            return Text("none")
        return Text(severity)
    if column_name == "damon_mode":
        return Text(_metric_code(entity_frame.metrics.get(column_name), _DAMON_MODE))
    metric = entity_frame.metrics.get(column_name)
    return _format_metric(metric, REGISTRY.get(column_name))


def metric_sort_value(column_name: str, entity_frame: EntityFrame) -> tuple[int, float | str]:
    if column_name == "name":
        return (0, display_name(entity_frame.entity).lower())
    if column_name == "tier":
        return (0, entity_frame.entity.tier or "")
    if column_name == "net_source":
        source = (entity_frame.network or {}).get("source_label")
        return (0, str(source or ""))
    metric = entity_frame.metrics.get(column_name)
    if metric is None or metric.v is None:
        return (1, 0.0)
    if isinstance(metric.v, (int, float)):
        return (0, float(metric.v))
    return (0, str(metric.v))


def _visible_entities(frame: Frame, *, container_only: bool, filter_text: str) -> list[EntityFrame]:
    needle = filter_text.lower().strip()
    rows: list[EntityFrame] = []
    for entity_frame in frame.entities.values():
        if container_only and entity_frame.entity.docker is None:
            continue
        if needle:
            haystacks = (display_name(entity_frame.entity).lower(), entity_frame.entity.key.lower())
            if not any(needle in haystack for haystack in haystacks):
                continue
        rows.append(entity_frame)
    return rows


def _sort_rows(entity_frames: list[EntityFrame], sort_by: str) -> list[EntityFrame]:
    if sort_by == "name":
        return sorted(entity_frames, key=lambda entity_frame: display_name(entity_frame.entity).lower())
    return sorted(
        entity_frames,
        key=lambda entity_frame: metric_sort_value(sort_by, entity_frame),
        reverse=True,
    )


def _make_table(columns: tuple[str, ...], *, title: str) -> Table:
    table = Table(title=title, box=None, expand=True, show_lines=False, pad_edge=False)
    for column_name in columns:
        justify = "left" if column_name in {"name", "tier", "net_source", "governance_origin", "governance_drift", "damon_mode"} else "right"
        no_wrap = column_name != "name"
        table.add_column(header_label(column_name), justify=justify, no_wrap=no_wrap, overflow="fold")
    return table


def _row_cells(columns: tuple[str, ...], entity_frame: EntityFrame, *, selected: bool) -> list[Text]:
    cells = [format_metric_value(column_name, entity_frame) for column_name in columns]
    if not cells:
        return cells
    name_cell = cells[0]
    marker = ">" if selected else " "
    cells[0] = Text.assemble((f"{marker} ", "bold cyan" if selected else ""), name_cell)
    return cells


def _format_metric(metric: MetricValue | None, spec) -> Text:
    if metric is not None and metric.src == "unlimited":
        return Text("max", style="yellow")
    if metric is None or metric.v is None:
        style = "dim" if metric is None or metric.src in {"unavail_perm", "unavail_kernel"} else ""
        return Text("-", style=style)
    value = metric.v
    if spec is None:
        return Text(str(value))
    if spec.unit in {"bytes", "bytes/s"}:
        suffix = "/s" if spec.unit == "bytes/s" else ""
        return Text(f"{_fmt_bytes(float(value))}{suffix}")
    if spec.unit == "%":
        return Text(f"{float(value):.1f}%")
    if spec.unit == "/s":
        return Text(f"{float(value):.1f}/s")
    if spec.unit == "ratio":
        return Text(f"{float(value):.1f}x")
    if metric is not None and spec.name == "damon_sample_age_s":
        return Text(f"{float(value):.1f}s")
    if spec.unit == "us":
        return Text(str(int(value)))
    if isinstance(value, float):
        return Text(f"{value:.1f}")
    return Text(str(value))


def _profile_from_config(config: GroopConfig, profile: str) -> tuple[str, ...] | None:
    profiles = ((config.columns or {}).get("profiles", {}) or {})
    selected = profiles.get(profile)
    if not isinstance(selected, dict):
        return None
    names = selected.get("list")
    if not isinstance(names, list):
        return None
    return tuple(str(name) for name in names)


def _column_supported(name: str) -> bool:
    return name in _LABELS and (name in REGISTRY or name in {"name", "tier", "net_source", "governance_origin", "governance_drift"} or name == "sock")


def _dedupe(names) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name not in seen and _column_supported(name):
            seen.add(name)
            out.append(name)
    return out


def _fmt_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    scaled = value
    unit = units[0]
    for unit in units:
        if abs(scaled) < 1024 or unit == units[-1]:
            break
        scaled /= 1024.0
    if unit == "B":
        return f"{int(scaled)}{unit}"
    return f"{scaled:.1f}{unit}"


_GOVERNANCE_ORIGIN = {
    0: "unset",
    1: "docker_default",
    2: "systemd_unit",
    3: "systemd_runtime_dropin",
    4: "raw_write",
}

_GOVERNANCE_DRIFT = {
    0: "none",
    1: "warn",
    2: "red",
}

_DAMON_MODE = {
    1: "vaddr",
    2: "paddr",
}


def _metric_code(metric: MetricValue | None, mapping: dict[int, str]) -> str:
    if metric is None or metric.v is None:
        return "-"
    return mapping.get(int(metric.v), str(metric.v))
