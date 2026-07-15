from __future__ import annotations

import hashlib
import json
import math
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ThresholdBand:
    warn: float
    crit: float

    def normalize(self, value: float | int | None) -> float:
        if value is None:
            return 0.0
        sample = float(value)
        if sample <= 0:
            return 0.0
        warn = max(0.0, self.warn)
        crit = max(warn, self.crit)
        if crit == 0:
            return 1.0
        if warn == 0:
            return min(sample / crit, 1.0)
        if crit == warn:
            return 1.0 if sample >= warn else min(sample / warn, 1.0)
        if sample <= warn:
            return min((sample / warn) * 0.5, 0.5)
        return min(0.5 + (((sample - warn) / (crit - warn)) * 0.5), 1.0)


@dataclass(frozen=True)
class DiagnosticsConfig:
    score_weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_SCORE_WEIGHTS))


@dataclass(frozen=True)
class HistoryConfig:
    full_resolution_seconds: int = 14_400
    downsample_interval_seconds: int = 60
    downsample_retention_hours: int = 4
    entity_grace_seconds: float = 30.0

    def capacity_for_interval(self, interval: float) -> int:
        if interval <= 0:
            return 1
        return max(1, math.ceil(self.full_resolution_seconds / interval))

    def entity_grace_frames(self, interval: float) -> int:
        if interval <= 0:
            return 0
        return max(0, math.ceil(self.entity_grace_seconds / interval))


@dataclass(frozen=True)
class RecordConfig:
    flush_every_frames: int = 1
    fsync: bool = False


@dataclass(frozen=True)
class SnapshotConfig:
    dir: Path | None = None
    frames: int = 60
    redact: bool = False


@dataclass(frozen=True)
class NetConfig:
    classes: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def classify_port(self, port: int) -> str | None:
        for name, ports in self.classes.items():
            if port in ports:
                return name
        return None


@dataclass(frozen=True)
class DamonConfig:
    """DAMON monitoring configuration.

    Attributes:
        paddr_enabled: When true and running under the root daemon, the daemon
            starts and owns one whole-host paddr session. Disabled by default.
            The existing paddr_sample_us, paddr_aggr_us, and paddr_update_us
            fields control the interval settings for the daemon-owned paddr
            session when enabled.
    """

    hot_rate: float = 50.0
    warm_rate: float = 5.0
    cold_age: float = 30.0
    idle_age: float = 120.0
    vaddr_sample_us: int = 100_000
    vaddr_aggr_us: int = 2_000_000
    vaddr_update_us: int = 1_000_000
    paddr_sample_us: int = 400_000
    paddr_aggr_us: int = 8_000_000
    paddr_update_us: int = 1_000_000
    max_concurrent_targets: int = 4
    paddr_enabled: bool = False


@dataclass(frozen=True)
class BpfSnapshotConfig:
    """BPF snapshot bridge configuration.

    All fields are disabled by default.
    """

    enabled: bool = False
    root: Path | None = None  # BPF pin root, e.g. /sys/fs/bpf/groop
    interval: float = 30.0  # refresh interval in seconds
    map_name: str = "groop_cgroup_skb"  # pinned map name under root
    state_dir: Path = Path("/run/groop/bpf")  # directory for snapshot.json output


class ProcessConfigError(ValueError):
    """A D-019 process-sampler config field or relationship is invalid."""


@dataclass(frozen=True)
class ProcessConfig:
    """D-019 bounded process candidate/enrichment budget.

    The candidate union is top ``top_cpu`` CPU-hot plus top ``top_io`` I/O-hot
    plus selected/pinned (capped at ``pinned_cap``) plus processes hot within
    the last ``recently_hot_grace_seconds``, capped overall at ``hard_cap``.
    All fields are strictly validated (D-019: "invalid relationships such as a
    hard cap below the selected/pinned allowance fail configuration
    validation").
    """

    top_cpu: int = 20
    top_io: int = 20
    pinned_cap: int = 16
    recently_hot_grace_seconds: float = 60.0
    hard_cap: int = 64

    def __post_init__(self) -> None:
        errors = validate_process_config(self)
        if errors:
            raise ProcessConfigError("; ".join(errors))


def validate_process_config(cfg: ProcessConfig) -> list[str]:
    """Return a list of validation error strings (empty when valid)."""
    errors: list[str] = []
    if cfg.top_cpu < 0:
        errors.append("top_cpu must be >= 0")
    if cfg.top_io < 0:
        errors.append("top_io must be >= 0")
    if cfg.pinned_cap < 0:
        errors.append("pinned_cap must be >= 0")
    if cfg.recently_hot_grace_seconds < 0:
        errors.append("recently_hot_grace_seconds must be >= 0")
    if cfg.hard_cap < 1:
        errors.append("hard_cap must be >= 1")
    if cfg.hard_cap < cfg.pinned_cap:
        errors.append("hard_cap must be >= pinned_cap")
    return errors


@dataclass(frozen=True)
class CiuConfig:
    """CIU stack metadata configuration.

    Attributes:
        known_stacks: Stack directory names used by the inference heuristic
            (e.g. ``"redis-core"``, or the full ``"infra/redis-core"``, which is
            matched on its last path segment). Compose derives its project name
            from the stack directory's *basename* and a compose project can
            never contain ``/``, so only that segment is compared. When a
            container's ``com.docker.compose.project`` matches an entry AND its
            container name is anchored to that project
            (``<project>-<env>-<name>``), it is inferred to be ciu-managed. An
            empty tuple (default) disables inference.
    """

    known_stacks: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroopConfig:
    interval: float = 5.0
    cgroup_root: Path = Path("/sys/fs/cgroup")
    default_view: str = "tree"
    default_column_profile: str = "auto"
    tiers: dict[str, list[str]] = field(default_factory=dict)
    protected_services: tuple[str, ...] = ()
    thresholds: dict[str, Any] = field(default_factory=dict)
    colors: dict[str, Any] = field(default_factory=dict)
    columns: dict[str, Any] = field(default_factory=dict)
    hotkeys: dict[str, Any] = field(default_factory=dict)
    diagnostics: DiagnosticsConfig = field(default_factory=DiagnosticsConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    record: RecordConfig = field(default_factory=RecordConfig)
    snapshots: SnapshotConfig = field(default_factory=SnapshotConfig)
    net: NetConfig = field(default_factory=NetConfig)
    damon: DamonConfig = field(default_factory=DamonConfig)
    ciu: CiuConfig = field(default_factory=CiuConfig)
    bpf_snapshot: BpfSnapshotConfig = field(default_factory=BpfSnapshotConfig)
    processes: ProcessConfig = field(default_factory=ProcessConfig)

    def to_primitive(self) -> dict[str, Any]:
        return {
            "general": {
                "interval": self.interval,
                "cgroup_root": str(self.cgroup_root),
                "default_view": self.default_view,
                "default_column_profile": self.default_column_profile,
            },
            "tiers": {**dict(self.tiers), "protected_services": list(self.protected_services)},
            "thresholds": self.thresholds,
            "colors": self.colors,
            "columns": self.columns,
            "hotkeys": self.hotkeys,
            "diagnostics": {
                "score_weights": dict(self.diagnostics.score_weights),
            },
            "history": {
                "full_resolution_seconds": self.history.full_resolution_seconds,
                "downsample_interval_seconds": self.history.downsample_interval_seconds,
                "downsample_retention_hours": self.history.downsample_retention_hours,
                "entity_grace_seconds": self.history.entity_grace_seconds,
            },
            "record": {
                "flush_every_frames": self.record.flush_every_frames,
                "fsync": self.record.fsync,
            },
            "snapshots": {
                "dir": None if self.snapshots.dir is None else str(self.snapshots.dir),
                "frames": self.snapshots.frames,
                "redact": self.snapshots.redact,
            },
            "net": {
                "classes": {name: list(ports) for name, ports in self.net.classes.items()},
            },
            "damon": {
                "hot_rate": self.damon.hot_rate,
                "warm_rate": self.damon.warm_rate,
                "cold_age": self.damon.cold_age,
                "idle_age": self.damon.idle_age,
                "vaddr_sample_us": self.damon.vaddr_sample_us,
                "vaddr_aggr_us": self.damon.vaddr_aggr_us,
                "vaddr_update_us": self.damon.vaddr_update_us,
                "paddr_sample_us": self.damon.paddr_sample_us,
                "paddr_aggr_us": self.damon.paddr_aggr_us,
                "paddr_update_us": self.damon.paddr_update_us,
                "max_concurrent_targets": self.damon.max_concurrent_targets,
                "paddr_enabled": self.damon.paddr_enabled,
            },
            "ciu": {
                "known_stacks": list(self.ciu.known_stacks),
            },
            "bpf_snapshot": {
                "enabled": self.bpf_snapshot.enabled,
                "root": None if self.bpf_snapshot.root is None else str(self.bpf_snapshot.root),
                "interval": self.bpf_snapshot.interval,
                "map_name": self.bpf_snapshot.map_name,
                "state_dir": str(self.bpf_snapshot.state_dir),
            },
            "processes": {
                "top_cpu": self.processes.top_cpu,
                "top_io": self.processes.top_io,
                "pinned_cap": self.processes.pinned_cap,
                "recently_hot_grace_seconds": self.processes.recently_hot_grace_seconds,
                "hard_cap": self.processes.hard_cap,
            },
        }

    def digest(self) -> str:
        payload = json.dumps(self.to_primitive(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def threshold_band(self, key: str, *, tier: str | None = None, warn: float, crit: float) -> ThresholdBand:
        default_band = ThresholdBand(warn=warn, crit=crit)
        sections: list[object] = []
        if tier:
            sections.append(self.thresholds.get(tier))
        sections.append(self.thresholds.get("default"))
        for section in sections:
            if not isinstance(section, dict):
                continue
            raw = section.get(key)
            if not isinstance(raw, dict):
                continue
            return ThresholdBand(
                warn=_coerce_float(raw.get("warn"), default_band.warn),
                crit=_coerce_float(raw.get("crit"), default_band.crit),
            )
        return default_band


def _default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "groop" / "config.toml"


def _parse_port_list(values: object) -> tuple[int, ...]:
    if not isinstance(values, list):
        return ()
    ports: set[int] = set()
    for value in values:
        if isinstance(value, int):
            if 0 < value < 65536:
                ports.add(value)
            continue
        if isinstance(value, str):
            text = value.strip()
            if "-" in text:
                start_text, _, end_text = text.partition("-")
                try:
                    start = int(start_text)
                    end = int(end_text)
                except ValueError:
                    continue
                if start > end:
                    start, end = end, start
                for port in range(max(1, start), min(65535, end) + 1):
                    ports.add(port)
                continue
            try:
                port = int(text)
            except ValueError:
                continue
            if 0 < port < 65536:
                ports.add(port)
    return tuple(sorted(ports))


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_score_weights(thresholds: object) -> dict[str, float]:
    defaults = dict(_DEFAULT_SCORE_WEIGHTS)
    if not isinstance(thresholds, dict):
        return defaults
    pressure_score = thresholds.get("pressure_score")
    if not isinstance(pressure_score, dict):
        return defaults
    raw_weights = pressure_score.get("weights")
    if not isinstance(raw_weights, dict):
        return defaults
    out = dict(defaults)
    for key, value in raw_weights.items():
        out[str(key)] = _coerce_float(value, out.get(str(key), 0.0))
    return out


_DEFAULT_SCORE_WEIGHTS = {
    "psi_mem_full_avg10": 24.0,
    "psi_mem_some_avg10": 10.0,
    "psi_io_full_avg10": 16.0,
    "psi_io_some_avg10": 6.0,
    "psi_cpu_some_avg10": 4.0,
    "rf_d_per_s": 20.0,
    "rf_f_per_s": 10.0,
    "mem_events_high_per_s": 6.0,
    "mem_events_oom_kill_per_s": 4.0,
    "io_cap_saturation_pct": 0.0,
    "network_loss_pct": 0.0,
}


def load(path: Path | None = None) -> GroopConfig:
    data: dict[str, Any] = {}
    try:
        with (path or _default_path()).open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        pass
    general = data.get("general", {})
    tiers_data = data.get("tiers", {})
    history_data = data.get("history", {})
    record_data = data.get("record", {})
    snapshot_data = data.get("snapshots", {})
    net_data = data.get("net", {})
    damon_data = data.get("damon", {})
    ciu_data = data.get("ciu", {})
    bpf_snapshot_data = data.get("bpf_snapshot", {})
    process_data = data.get("processes", {})
    thresholds = dict(data.get("thresholds", {}) or {})
    tiers = {
        str(name): [str(prefix) for prefix in prefixes]
        for name, prefixes in tiers_data.items()
        if isinstance(prefixes, list) and name != "protected_services"
    }
    net_classes = {
        str(name): _parse_port_list(values)
        for name, values in (net_data.get("classes", {}) or {}).items()
    }
    return GroopConfig(
        interval=float(general.get("interval", 5.0)),
        cgroup_root=Path(general.get("cgroup_root", "/sys/fs/cgroup")),
        default_view=str(general.get("default_view", "tree")),
        default_column_profile=str(general.get("default_column_profile", "auto")),
        tiers=tiers,
        protected_services=tuple(str(v) for v in tiers_data.get("protected_services", ())),
        thresholds=thresholds,
        colors=dict(data.get("colors", {}) or {}),
        columns=dict(data.get("columns", {}) or {}),
        hotkeys=dict(data.get("hotkeys", {}) or {}),
        diagnostics=DiagnosticsConfig(score_weights=_load_score_weights(thresholds)),
        history=HistoryConfig(
            full_resolution_seconds=int(history_data.get("full_resolution_seconds", 14_400)),
            downsample_interval_seconds=int(history_data.get("downsample_interval_seconds", 60)),
            downsample_retention_hours=int(history_data.get("downsample_retention_hours", 4)),
            entity_grace_seconds=float(history_data.get("entity_grace_seconds", 30.0)),
        ),
        record=RecordConfig(
            flush_every_frames=max(1, int(record_data.get("flush_every_frames", 1))),
            fsync=bool(record_data.get("fsync", False)),
        ),
        snapshots=SnapshotConfig(
            dir=Path(snapshot_data["dir"]) if isinstance(snapshot_data.get("dir"), str) else None,
            frames=max(1, int(snapshot_data.get("frames", 60))),
            redact=bool(snapshot_data.get("redact", False)),
        ),
        net=NetConfig(classes=net_classes),
        damon=DamonConfig(
            hot_rate=_coerce_float(damon_data.get("hot_rate"), 50.0),
            warm_rate=_coerce_float(damon_data.get("warm_rate"), 5.0),
            cold_age=_coerce_float(damon_data.get("cold_age"), 30.0),
            idle_age=_coerce_float(damon_data.get("idle_age"), 120.0),
            vaddr_sample_us=max(1, int(_coerce_float(damon_data.get("vaddr_sample_us"), 100_000))),
            vaddr_aggr_us=max(1, int(_coerce_float(damon_data.get("vaddr_aggr_us"), 2_000_000))),
            vaddr_update_us=max(1, int(_coerce_float(damon_data.get("vaddr_update_us"), 1_000_000))),
            paddr_sample_us=max(1, int(_coerce_float(damon_data.get("paddr_sample_us"), 400_000))),
            paddr_aggr_us=max(1, int(_coerce_float(damon_data.get("paddr_aggr_us"), 8_000_000))),
            paddr_update_us=max(1, int(_coerce_float(damon_data.get("paddr_update_us"), 1_000_000))),
            max_concurrent_targets=max(1, int(_coerce_float(damon_data.get("max_concurrent_targets"), 4))),
            paddr_enabled=(
                damon_data["paddr_enabled"]
                if isinstance(damon_data.get("paddr_enabled"), bool)
                else False
            ),
        ),
        ciu=CiuConfig(
            known_stacks=tuple(
                str(s) for s in (ciu_data.get("known_stacks", []) or [])
                if isinstance(s, str)
            ),
        ),
        bpf_snapshot=BpfSnapshotConfig(
            enabled=bool(bpf_snapshot_data.get("enabled", False)),
            root=Path(bpf_snapshot_data["root"]) if isinstance(bpf_snapshot_data.get("root"), str) else None,
            interval=float(bpf_snapshot_data.get("interval", 30.0)),
            map_name=str(bpf_snapshot_data.get("map_name", "groop_cgroup_skb")),
            state_dir=Path(bpf_snapshot_data["state_dir"]) if isinstance(bpf_snapshot_data.get("state_dir"), str) else BpfSnapshotConfig.state_dir,
        ),
        processes=ProcessConfig(
            top_cpu=int(_coerce_float(process_data.get("top_cpu"), 20)),
            top_io=int(_coerce_float(process_data.get("top_io"), 20)),
            pinned_cap=int(_coerce_float(process_data.get("pinned_cap"), 16)),
            recently_hot_grace_seconds=_coerce_float(process_data.get("recently_hot_grace_seconds"), 60.0),
            hard_cap=int(_coerce_float(process_data.get("hard_cap"), 64)),
        ),
    )
