"""Configuration: one flat TOML table, strict keys, hard ceilings that no config value can exceed."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path

MiB = 1024 * 1024
GiB = 1024 * MiB

# Hard ceilings. A config value above one of these is a configuration ERROR, not silently clamped:
# the worst-case added latency and daily IO in the design note are computed from these numbers.
HARD = {
    "qd_high": 8,
    "read_iops_max": 200.0,
    "sync4k_iops_max": 500.0,
    "region_hard_cap": 2 * GiB,
    "probe_bytes_cap": 256 * MiB,
    "escalation_bytes_cap": 1 * GiB,
    "block_bytes": 1 * MiB,
    "read_samples_per_tick": 64,
    "sync4k_samples": 4096,
    "seq_read_bytes": 128 * MiB,
    "phase_wall_s": 120.0,
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    # --- paths -----------------------------------------------------------------------------------------
    state_dir: str = "/var/lib/iogov"
    scratch_dir: str = "/var/lib/iogov/scratch"
    kill_switch_file: str = "/etc/iogov/disabled"
    sysfs_root: str = "/sys"
    procfs_root: str = "/proc"
    cgroup_root: str = "/sys/fs/cgroup"
    dev_root: str = "/dev"
    docker_containers_dir: str = "/var/lib/docker/containers"
    # --- what is measured ------------------------------------------------------------------------------
    enabled: bool = True
    data_paths: list = field(default_factory=list)  # volumes of the protected workload: devices are derived from these
    devices: list = field(default_factory=list)  # explicit whole-disk nodes (override auto-detection)
    exclude_devices: list = field(default_factory=list)
    governed_cgroups: list = field(default_factory=lambda: ["/sys/fs/cgroup/dev.slice"])
    protected_cgroups: list = field(default_factory=list)
    containers: list = field(default_factory=list)  # container names, resolved to their cgroup scope
    allow_regular_files_as_devices: bool = False  # tests only
    require_write_denied: bool = False  # refuse raw reads if os.access(W_OK) says the service could write the disk
    # --- read probes (tick) ----------------------------------------------------------------------------
    tick_interval_s: float = 300.0  # the timer period; used by `plan` and the budget computation
    read_block_bytes: int = 4096
    read_samples_per_tick: int = 12
    read_max_attempts: int = 36
    witness_reads: int = 2
    witness_keep: int = 16
    read_iops_max: float = 20.0
    max_read_bytes_per_day: int = 128 * MiB
    quiet_probe_s: float = 1.0
    quiet_governed_write_bps: float = float(1 * MiB)
    # --- write bench (every ~6 h) ----------------------------------------------------------------------
    bench_interval_h: float = 6.0
    bench_jitter: float = 0.1
    scratch_fs_allowed: list = field(default_factory=lambda: ["ext4", "xfs"])
    region_default: int = 512 * MiB
    region_hard_cap: int = 1 * GiB
    region_min: int = 16 * MiB
    region_disk_fraction: float = 0.01
    region_free_fraction: float = 0.25
    free_reserve_bytes: int = 2 * GiB
    free_reserve_fraction: float = 0.05
    probe_bytes_cap: int = 64 * MiB
    escalation_every_h: float = 168.0
    escalation_bytes_cap: int = 192 * MiB
    qd_low: int = 1
    qd_high: int = 4
    block_bytes: int = 64 * 1024
    sync_every_bytes: int = 8 * MiB
    write_bps_max: float = float(256 * MiB)
    sync4k_samples: int = 256
    sync4k_iops_max: float = 100.0
    seq_read_bytes: int = 8 * MiB
    seq_read_block: int = 128 * 1024
    read_bps_max: float = float(256 * MiB)
    phase_wall_s: float = 20.0
    max_write_bytes_per_day: int = 1 * GiB
    # --- gating / abort --------------------------------------------------------------------------------
    tick_pressure_some_max: float = 10.0  # % avg10 of /proc/pressure/io
    bench_pressure_some_max: float = 1.0
    bench_pressure_full_max: float = 0.1
    protected_pressure_some_max: float = 1.0
    loadavg_per_cpu_max: float = 1.5
    bench_governed_write_bps_max: float = float(2 * MiB)
    abort_latency_ms: float = 250.0  # a single IO slower than this stops further submissions
    pressure_recheck_every_ops: int = 32
    skip_log_interval_s: float = 3600.0
    backoff_base_s: float = 300.0
    backoff_max_s: float = 3600.0
    # --- covariates, prevention checker (report only) ----------------------------------------------------
    fstrim_stamp_file: str = "/var/lib/systemd/timers/stamp-fstrim.timer"
    fstrim_log_file: str = "/var/lib/iogov/fstrim.log"  # optional: append `fstrim -v` output here to record trimmed bytes
    lvm_conf: str = "/etc/lvm/lvm.conf"
    provider_signal_file: str = "/etc/iogov/provider.json"  # optional JSON {"used_bytes", "total_bytes", "snapshot_created"}
    fill_warn_pct: float = 60.0
    fstrim_max_days: float = 7.0
    snapshot_warn_h: float = 24.0
    provider_used_warn_pct: float = 90.0
    # --- protected-side distress (episodes) and who-else-writes -------------------------------------------
    watch_interval_s: float = 1.0
    episode_some_pct: float = 5.0  # % of wall time the protected cgroup stalled on IO (from PSI `total` deltas)
    episode_full_pct: float = 1.0
    episode_dstate: int = 2  # threads of the protected cgroups in D state
    episode_quiet_s: float = 10.0
    episode_pre_s: int = 30
    episode_max_s: float = 900.0
    episode_max_points: int = 300
    canary_latency_ms: float = 50.0  # a probe read slower than this in a tick is journalled as a `harm` observation
    others_top: int = 8
    # --- recognizer ------------------------------------------------------------------------------------
    window_hours: float = 24.0
    baseline_windows: int = 5
    rebaseline_windows: int = 3
    cusum_k: float = 0.5  # log2 units: ignores drift under ~1.4x
    cusum_h: float = 1.5  # log2 units
    cusum_cap_mult: float = 2.0
    confirm_windows: int = 2
    clear_windows: int = 2
    noise_floor_log2: float = 0.25
    min_samples: dict = field(
        default_factory=lambda: {
            "r_cold": 30,
            "r_witness": 10,
            "w_over_4k": 100,
            "w_fresh_qd1": 1,
            "w_fresh_qd4": 1,
            "r_seq": 1,
        }
    )
    recognize_every_s: float = 3600.0
    lookback_days: int = 120

    def journal_dir(self) -> str:
        return os.path.join(self.state_dir, "journal")

    def state_file(self) -> str:
        return os.path.join(self.state_dir, "state.json")


def _check(cfg: Config) -> Config:
    errors = []
    for key, limit in HARD.items():
        if getattr(cfg, key) > limit:
            errors.append(f"{key}={getattr(cfg, key)} exceeds the hard ceiling {limit}")
    if cfg.qd_low < 1 or cfg.qd_high < cfg.qd_low:
        errors.append("need 1 <= qd_low <= qd_high")
    for key in ("read_block_bytes", "block_bytes", "seq_read_block", "sync_every_bytes"):
        if getattr(cfg, key) % 4096:
            errors.append(f"{key} must be a multiple of 4096")
    if cfg.region_default and cfg.region_min > cfg.region_default:
        errors.append("region_min must not exceed region_default")
    if cfg.region_default > cfg.region_hard_cap:
        errors.append("region_default must not exceed region_hard_cap")
    if cfg.bench_interval_h < 1.0:
        errors.append("bench_interval_h must be >= 1")
    if cfg.read_iops_max <= 0 or cfg.sync4k_iops_max <= 0 or cfg.write_bps_max <= 0 or cfg.read_bps_max <= 0:
        errors.append("rate ceilings must be > 0")
    if not (0 < cfg.cusum_h and 0 < cfg.cusum_k):
        errors.append("cusum_k and cusum_h must be > 0")
    if errors:
        raise ConfigError("; ".join(errors))
    return cfg


def from_mapping(data: dict) -> Config:
    known = {f.name for f in dataclasses.fields(Config)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(unknown)}")
    base = Config()
    values = {}
    for key, value in data.items():
        default = getattr(base, key)
        if isinstance(default, bool) != isinstance(value, bool):
            raise ConfigError(f"{key}: expected {type(default).__name__}, got {type(value).__name__}")
        if isinstance(default, float) and isinstance(value, int):
            value = float(value)
        if isinstance(default, dict):
            merged = dict(default)
            merged.update(value)
            value = merged
        elif not isinstance(value, type(default)):
            raise ConfigError(f"{key}: expected {type(default).__name__}, got {type(value).__name__}")
        values[key] = value
    return _check(dataclasses.replace(base, **values))


def load(path: str | None = None) -> Config:
    """Load a TOML file (default: $IOGOV_CONFIG or /etc/iogov/iogov.toml if present, else built-in defaults)."""

    path = path or os.environ.get("IOGOV_CONFIG") or "/etc/iogov/iogov.toml"
    p = Path(path)
    if not p.exists():
        if path == "/etc/iogov/iogov.toml" and not os.environ.get("IOGOV_CONFIG"):
            return _check(Config())
        raise ConfigError(f"config file not found: {path}")
    try:
        import tomllib
    except ImportError as exc:  # pragma: no cover - python < 3.11
        raise ConfigError("python >= 3.11 (tomllib) is required to read the config file") from exc
    with p.open("rb") as fh:
        return from_mapping(tomllib.load(fh))


def effective(cfg: Config) -> dict:
    return dataclasses.asdict(cfg)
