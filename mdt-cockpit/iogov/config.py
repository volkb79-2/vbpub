"""Configuration: one flat TOML table, strict keys, hard ceilings that no config value can exceed."""

from __future__ import annotations

import dataclasses
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import safeio

MiB = 1024 * 1024
GiB = 1024 * MiB
MAX_CONFIG_BYTES = 1 * MiB

# Keys that existed in an earlier release and were removed. They are IGNORED with a warning (listed in `plan` and `config`) for one release,
# so an upgraded host with an old config file keeps running; an unknown NEW key is still an error.
DEPRECATED_KEYS = {
    "lookback_days": "removed 2026-10-27: the recognizer keeps persisted per-lane aggregates, there is no sliding lookback",
    "recognize_every_s": "removed 2026-10-27: windows are closed by every run; there is no separate recognizer schedule",
}

# Hard limits. Every safety-relevant value is CLAMPED into [HARD_MIN, HARD_MAX] when the config is loaded (the clamp is recorded in
# `Config.clamped` and printed by `plan`/`config`): a typo or a hostile config file can never exceed the budgets in the design note.
HARD_MAX = {
    "qd_high": 8, "read_iops_max": 200.0, "sync4k_iops_max": 500.0, "region_hard_cap": 2 * GiB, "region_default": 2 * GiB,
    "probe_bytes_cap": 256 * MiB, "escalation_bytes_cap": 1 * GiB, "block_bytes": 1 * MiB, "seq_read_block": 1 * MiB,
    "read_samples_per_tick": 64, "read_max_attempts": 128, "witness_reads": 16, "witness_keep": 64, "sync4k_samples": 4096,
    "seq_read_bytes": 128 * MiB, "phase_wall_s": 120.0,
    "max_read_bytes_per_day": 256 * MiB, "max_write_bytes_per_day": 2 * GiB,
    "write_bps_max": float(512 * MiB), "read_bps_max": float(512 * MiB), "sync_every_bytes": 8 * MiB,
    "abort_latency_ms": 1000.0, "pressure_recheck_every_ops": 256, "flush_limit_s": 10.0,
    "tick_pressure_some_max": 25.0, "bench_pressure_some_max": 5.0, "bench_pressure_full_max": 1.0,
    "protected_pressure_some_max": 5.0, "loadavg_per_cpu_max": 4.0, "bench_governed_write_bps_max": float(16 * MiB),
    "quiet_governed_write_bps": float(16 * MiB), "bench_max_failures": 20, "backoff_max_s": 86400.0, "skip_passive_interval_s": 3600.0,
}
HARD_MIN = {
    "qd_low": 1, "qd_high": 1, "block_bytes": 4096, "seq_read_block": 4096, "sync_every_bytes": 4096,
    "read_samples_per_tick": 1, "read_max_attempts": 1, "sync4k_samples": 1, "pressure_recheck_every_ops": 1,
    "abort_latency_ms": 1.0, "phase_wall_s": 1.0, "flush_limit_s": 0.05, "read_iops_max": 0.1, "sync4k_iops_max": 0.1,
    "write_bps_max": 4096.0, "read_bps_max": 4096.0, "seq_read_bytes": 4096, "bench_max_failures": 1,
    "max_read_bytes_per_day": 0, "max_write_bytes_per_day": 0, "bench_interval_h": 1.0, "tick_interval_s": 30.0,
    "skip_passive_interval_s": 60.0,
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
    allow_root: bool = False  # running as root is refused unless this (or --allow-root) says so; root also needs a root-owned 0700 scratch dir
    container_cgroups: dict = field(default_factory=dict)  # name -> cgroup dir: explicit paths, preferred over reading config.v2.json
    exclude_others: list = field(default_factory=lambda: ["iogov-probe.slice"])  # never reported as "other writers"
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
    flush_limit_s: float = 2.0  # a flush slower than this stops the run (slow_flush)
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
    bench_max_failures: int = 5  # consecutive crashed benches before the bench lane disables itself (until `annotate --baseline-reset`)
    journal_max_age_days: float = 400.0
    journal_max_bytes: int = 512 * MiB
    # --- recognizer ------------------------------------------------------------------------------------
    window_hours: float = 24.0
    baseline_windows: int = 5
    rebaseline_windows: int = 3
    cusum_k: float = 0.5  # log2 units: ignores drift under ~1.4x
    cusum_h: float = 2.0  # log2 units
    cusum_cap_mult: float = 2.0
    confirm_windows: int = 3
    max_gap_windows: int = 1  # windows missing between two bad windows break the "consecutive" streak
    prior_sigma_log2: float = 0.5  # shrinkage prior for the baseline noise estimate
    prior_weight: float = 5.0
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
    skip_passive_interval_s: float = 900.0  # while a tick is gated or backing off, at most one passive-on-skip record (and gate read) per this interval
    clamped: list = field(default_factory=list)  # filled by from_mapping: what was clamped
    deprecated: list = field(default_factory=list)  # filled by from_mapping: removed keys that were present and ignored

    def journal_dir(self) -> str:
        return os.path.join(self.state_dir, "journal")

    def state_file(self) -> str:
        return os.path.join(self.state_dir, "state.json")


def _check(cfg: Config) -> Config:
    changes = {}
    clamped = []
    for key, limit in HARD_MAX.items():
        v = getattr(cfg, key)
        if v > limit:
            changes[key] = limit
            clamped.append(f"{key}: {v} -> {limit} (hard maximum)")
    for key, limit in HARD_MIN.items():
        v = changes.get(key, getattr(cfg, key))
        if v < limit:
            changes[key] = limit
            clamped.append(f"{key}: {v} -> {limit} (hard minimum)")
    for key in ("block_bytes", "seq_read_block", "sync_every_bytes"):
        v = changes.get(key, getattr(cfg, key))
        if v % 4096:
            changes[key] = max(4096, (v // 4096) * 4096)
            clamped.append(f"{key}: {v} -> {changes[key]} (4096 alignment)")
    qd_low = changes.get("qd_low", cfg.qd_low)
    if changes.get("qd_high", cfg.qd_high) < qd_low:
        changes["qd_high"] = qd_low
        clamped.append(f"qd_high raised to qd_low {qd_low}")
    if changes.get("region_default", cfg.region_default) > changes.get("region_hard_cap", cfg.region_hard_cap):
        changes["region_default"] = changes.get("region_hard_cap", cfg.region_hard_cap)
        clamped.append("region_default clamped to region_hard_cap")
    if cfg.region_default and cfg.region_min > changes.get("region_default", cfg.region_default):
        changes["region_min"] = changes.get("region_default", cfg.region_default)
        clamped.append("region_min lowered to region_default")
    if not (0 < cfg.cusum_h and 0 < cfg.cusum_k):
        raise ConfigError("cusum_k and cusum_h must be > 0")
    if changes or clamped:
        cfg = dataclasses.replace(cfg, clamped=list(cfg.clamped) + clamped, **changes)
    return cfg


def from_mapping(data: dict) -> Config:
    known = {f.name for f in dataclasses.fields(Config)}
    deprecated = [f"{k}: ignored ({DEPRECATED_KEYS[k]})" for k in sorted(data) if k in DEPRECATED_KEYS]
    data = {k: v for k, v in data.items() if k not in DEPRECATED_KEYS}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(unknown)}")
    base = Config()
    values = {"deprecated": deprecated}
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
    # The config path may be a symlink (a managed-config layout), but only a ROOT-owned one whose whole chain (every hop, every directory
    # on the way, the final file's directory) is root-owned and not group/other-writable; the target must then pass every check a plain
    # file must pass. Anything else that is not a regular file (FIFO, device node, directory) is refused before any open.
    try:
        lst = os.lstat(path)
    except OSError as exc:
        raise ConfigError(f"{path}: {exc.strerror or exc}") from None
    target = _resolve_config_link(path) if stat.S_ISLNK(lst.st_mode) else path
    try:
        safeio.refuse_non_regular(target)
    except safeio.UnsafePath as exc:
        raise ConfigError(str(exc)) from None
    st = os.stat(target)
    if st.st_mode & 0o022 or st.st_uid not in (0, os.geteuid()):
        raise ConfigError(f"{path}: refusing a config file that is writable by group/others or owned by another user")
    if st.st_size > MAX_CONFIG_BYTES:
        raise ConfigError(f"{path}: {st.st_size} bytes exceeds the {MAX_CONFIG_BYTES} byte limit")
    text = safeio.read_text(target, MAX_CONFIG_BYTES, follow=False)
    if text is None:
        raise ConfigError(f"{path}: cannot be read")
    try:
        return from_mapping(tomllib.loads(text))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def _trusted_dir(d: str) -> str | None:
    """None when `d` is a directory owned by root that nobody but root can replace entries in, else the reason.
    Other-writable is never trusted. Group-writable is trusted only with the sticky bit (entries can then be replaced only by their
    owner: `/nix/store` 1775 root:nixbld qualifies, Debian's `/usr/local` 2775 root:staff does not, since any staff member could swap a
    root-owned link there)."""

    try:
        st = os.stat(d)
    except OSError as exc:
        return exc.strerror or str(exc)
    if not stat.S_ISDIR(st.st_mode):
        return "not a directory"
    if st.st_uid != 0:
        return f"owned by uid {st.st_uid}, not root"
    if st.st_mode & 0o002:
        return f"other writable (mode {oct(st.st_mode & 0o7777)})"
    if st.st_mode & 0o020 and not st.st_mode & stat.S_ISVTX:
        return f"group writable without the sticky bit (mode {oct(st.st_mode & 0o7777)}): entries can be replaced by group members"
    return None


def _ancestors(d: str) -> list[str]:
    """Every directory from `d` (a REAL path: symlinked directories already resolved) up to the root."""

    out = []
    while True:
        out.append(d)
        parent = os.path.dirname(d)
        if parent == d:
            return out
        d = parent


MAX_CONFIG_LINK_HOPS = 8


def _resolve_config_link(path: str, max_hops: int = MAX_CONFIG_LINK_HOPS) -> str:
    """Follow a chain of root-owned symlinks by hand. Each hop is placed in its REAL directory first (symlinked directories resolved by the
    kernel's rules, so what is checked is what will be opened); the hop must be root-owned, and every directory above it and above the
    final file must be trusted (`_trusted_dir`): a root-owned link into a service-writable directory is NOT a trusted config. A cycle or a
    chain longer than `max_hops` is refused."""

    cur = os.path.abspath(path)
    for _ in range(max_hops + 1):
        parent_real = os.path.realpath(os.path.dirname(cur))
        cur = os.path.join(parent_real, os.path.basename(cur))
        try:
            lst = os.lstat(cur)
        except OSError as exc:
            raise ConfigError(f"{path}: {cur}: {exc.strerror or exc}") from None
        if stat.S_ISLNK(lst.st_mode) and lst.st_uid != 0:
            raise ConfigError(f"{path}: symlink {cur} must be root-owned (is uid {lst.st_uid})")
        for d in _ancestors(parent_real):
            why = _trusted_dir(d)
            if why:
                raise ConfigError(f"{path}: directory {d} on the symlink chain is not trusted ({why})")
        if not stat.S_ISLNK(lst.st_mode):
            return cur
        cur = os.path.normpath(os.path.join(parent_real, os.readlink(cur)))
    raise ConfigError(f"{path}: more than {max_hops} symlink hops (a loop, or a chain nobody should trust)")


def effective(cfg: Config) -> dict:
    return dataclasses.asdict(cfg)
