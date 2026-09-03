from __future__ import annotations

from dataclasses import dataclass, field, fields
import json
import re
from typing import Any, Literal


SCHEMA_VERSION = 1
OBSOLETE_VARIABLES = {
    "SWAP_ARCH",
    "SWAP_TOTAL_GB",
    "SWAP_FILES",
    "USE_PARTITION",
    "SWAP_PARTITION_SIZE_GB",
    "SWAP_BACKING",
    "BOOTSTRAP_STAGE",
    "NEVER_REBOOT_STAGE2",
}
UNSUPPORTED_V2_SETTINGS = {
    "repo_url", "repo_branch", "clone_dir", "send_sysinfo",
    "run_geekbench", "run_ssh_setup", "run_zswap_validation",
    "telegram_use_forum_topic", "telegram_topic_prefix", "extend_root",
    "pre_shrink_root_extra_gb", "swap_ram_solution",
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    schema_version: int = SCHEMA_VERSION
    fresh_install: bool = True
    log_dir: str = "/var/log/debian-install"
    state_dir: str = "/var/lib/vbpub/bootstrap"
    stage2_output: str = "/root/custom_script.output2"
    auto_reboot_after_stage1: bool = True
    never_reboot: bool = False

    run_user_config: bool = True
    run_apt_config: bool = True
    run_journald_config: bool = True
    run_docker_install: bool = True
    run_ksm: bool = True
    run_oomd_config: bool = True
    run_fstrim: bool = True
    run_docker_cleanup: bool = True
    run_apt_auto_upgrade: bool = True
    run_auto_reboot: bool = True

    swap_disk_total_gb: int = 32
    swap_file_count: int = 8
    swap_priority: int = 10
    swap_discard: bool = True
    preserve_root_size_gb: int = 10
    zswap_compressor: Literal["zstd", "lz4", "lzo-rle"] = "zstd"
    zswap_zpool: Literal["z3fold", "zbud", "zsmalloc"] = "z3fold"
    zswap_pool_percent: int = 25
    # 50, not the kernel's own default of 60 or gstammtisch's 100: anon pages
    # stay the most precious tier even with zswap making reclaim cheap, so
    # this host fleet favors a middle value over "swap early, zswap absorbs
    # it" (100) or the stock default (60). oomd (run_oomd_config) is the
    # safety net that makes ANY of these values safe to run unattended;
    # 5-10 is the documented alternative for latency-sensitive workloads
    # (databases etc.) that want almost no anon reclaim regardless of swap
    # cost, and 100 remains documented for a memory-fungible/zswap-protected
    # host in the gstammtisch mold. See debian_install_v2/README.md.
    vm_swappiness: int = 50
    docker_live_restore: bool = True
    docker_log_driver: str = "json-file"
    docker_log_max_size: str = "50m"
    docker_log_max_file: str = "3"
    docker_cleanup_max_age_hours: int = 240
    apt_auto_upgrade_mode: Literal["full", "security-only", "notify-only"] = "full"
    reboot_window_time: str = "03:00"
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = field(default="", repr=False)
    credential_mode: Literal["root-storage", "systemd"] = "root-storage"


_SIZE_RE = re.compile(r"^[0-9]+$")
_DOCKER_LOG_MAX_SIZE_RE = re.compile(r"^[0-9]+[bkmg]?$", re.IGNORECASE)
_HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
def _reject_obsolete(data: dict[str, Any]) -> None:
    obsolete = sorted(set(data) & OBSOLETE_VARIABLES)
    if obsolete:
        names = ", ".join(obsolete)
        raise ConfigError(f"obsolete v1 setting(s) rejected by v2 clean break: {names}")
    unsupported = sorted({str(key).lower() for key in data} & UNSUPPORTED_V2_SETTINGS)
    if unsupported:
        raise ConfigError(f"setting(s) are not part of minimal v2: {', '.join(unsupported)}")


def _validate(config: Config) -> None:
    if config.schema_version != SCHEMA_VERSION:
        raise ConfigError(
            f"schema_version must be {SCHEMA_VERSION}, got {config.schema_version}"
        )
    boolean_names = [item.name for item in fields(Config) if item.type == "bool" or item.type is bool]
    for name in boolean_names:
        if not isinstance(getattr(config, name), bool):
            raise ConfigError(f"{name} must be a JSON boolean")
    integer_names = [
        "schema_version", "swap_disk_total_gb", "swap_file_count", "swap_priority",
        "preserve_root_size_gb", "zswap_pool_percent", "vm_swappiness",
        "docker_cleanup_max_age_hours",
    ]
    for name in integer_names:
        value = getattr(config, name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{name} must be an integer")
        string_names = [
            "log_dir", "state_dir", "stage2_output",
            "telegram_bot_token", "telegram_chat_id",
            "docker_log_driver", "docker_log_max_size", "docker_log_max_file",
            "reboot_window_time",
        ]
    for name in string_names:
        if not isinstance(getattr(config, name), str):
            raise ConfigError(f"{name} must be a string")
    if config.zswap_compressor not in {"zstd", "lz4", "lzo-rle"}:
        raise ConfigError("zswap_compressor must be zstd, lz4, or lzo-rle")
    if config.zswap_zpool not in {"z3fold", "zbud", "zsmalloc"}:
        raise ConfigError("zswap_zpool must be z3fold, zbud, or zsmalloc")
    if not config.docker_log_driver:
        raise ConfigError("docker_log_driver must not be empty")
    if not _DOCKER_LOG_MAX_SIZE_RE.fullmatch(config.docker_log_max_size):
        raise ConfigError("docker_log_max_size must be a number optionally suffixed b/k/m/g (e.g. '50m')")
    if not config.docker_log_max_file.isdigit() or int(config.docker_log_max_file) < 1:
        raise ConfigError("docker_log_max_file must be a positive integer string (e.g. '3')")
    if not config.fresh_install:
        raise ConfigError("v2 install is restricted to fresh_install=true in this release")
    for name in ("log_dir", "state_dir", "stage2_output"):
        value = getattr(config, name)
        if not value.startswith("/") or value.endswith("/"):
            raise ConfigError(f"{name} must be an absolute path without a trailing slash")
    if not _SIZE_RE.fullmatch(str(config.swap_disk_total_gb)) or not 1 <= config.swap_disk_total_gb <= 10240:
        raise ConfigError("swap_disk_total_gb must be an integer from 1 to 10240")
    if not _SIZE_RE.fullmatch(str(config.swap_file_count)) or not 1 <= config.swap_file_count <= 64:
        raise ConfigError("swap_file_count must be an integer from 1 to 64")
    for name in ("preserve_root_size_gb",):
        value = getattr(config, name)
        if not _SIZE_RE.fullmatch(str(value)) or not 1 <= value <= 10240:
            raise ConfigError(f"{name} must be an integer from 1 to 10240")
    if not 5 <= config.zswap_pool_percent <= 60:
        raise ConfigError("zswap_pool_percent must be from 5 to 60")
    if not 0 <= config.swap_priority <= 32767:
        raise ConfigError("swap_priority must be from 0 to 32767")
    if not 0 <= config.vm_swappiness <= 100:
        raise ConfigError("vm_swappiness must be from 0 to 100")
    if not 1 <= config.docker_cleanup_max_age_hours <= 8760:
        raise ConfigError("docker_cleanup_max_age_hours must be from 1 to 8760")
    if config.apt_auto_upgrade_mode not in {"full", "security-only", "notify-only"}:
        raise ConfigError("apt_auto_upgrade_mode must be full, security-only, or notify-only")
    if not _HHMM_RE.fullmatch(config.reboot_window_time):
        raise ConfigError("reboot_window_time must be 24h HH:MM (e.g. '03:00')")
    if bool(config.telegram_bot_token) != bool(config.telegram_chat_id):
        raise ConfigError("telegram_bot_token and telegram_chat_id must be supplied together")
    if config.credential_mode not in {"root-storage", "systemd"}:
        raise ConfigError("credential_mode must be root-storage or systemd")
    if config.credential_mode == "root-storage" and not (config.state_dir.startswith("/var/lib/") or config.state_dir == "/var/lib/vbpub/bootstrap"):
        raise ConfigError("credential_mode=root-storage requires state_dir under /var/lib")


def load_config(path: str | None = None, raw_json: str | None = None) -> Config:
    if bool(path) == bool(raw_json):
        raise ConfigError("supply exactly one of --config FILE or --config-json JSON")
    try:
        data = json.loads(open(path, encoding="utf-8").read() if path else raw_json or "")
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read configuration as JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("configuration root must be a JSON object")
    _reject_obsolete({str(key).upper() for key in data})
    allowed = {item.name for item in fields(Config)}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"unknown configuration key(s): {', '.join(unknown)}")
    config = Config(**data)
    _validate(config)
    return config
