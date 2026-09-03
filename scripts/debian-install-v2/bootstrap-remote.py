#!/usr/bin/env python3
"""
vbpub debian-install v2 — remote bootstrap.

Fetches just scripts/debian-install-v2/ from the public volkb79-2/vbpub repo
(a codeload tarball of one branch — no git, no `tar` binary, stdlib only:
urllib + tarfile), translates a small set of env vars into v2's strict-JSON
config, and runs the installer. This is the v2 equivalent of v1's
scripts/debian-install/bootstrap.sh one-liner, not a continuation of it:
v2's own CLI is strict-JSON only (see debian_install_v2/config.py) and
rejects v1's env var names outright (SWAP_ARCH, SWAP_TOTAL_GB, SWAP_FILES,
USE_PARTITION, ...) — this wrapper is a thin, disposable adapter around
that JSON contract, not a reintroduction of v1's surface. Only python3 and
outbound HTTPS are required before this runs; apt/git are never needed to
fetch this wrapper or the code it pulls down (the installer's own stage1
installs git/curl/docker for itself, once it starts).

Usage (root):
  curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/scripts/debian-install-v2/bootstrap-remote.py \\
    | SWAP_DISK_TOTAL_GB=32 SWAP_FILE_COUNT=8 ZSWAP_COMPRESSOR=zstd ZSWAP_POOL_PERCENT=25 \\
      AUTO_REBOOT_AFTER_STAGE1=yes NEVER_REBOOT=no \\
      TELEGRAM_BOT_TOKEN=123:token TELEGRAM_CHAT_ID=456 \\
      python3 -

Env vars — every name below maps 1:1 to a debian_install_v2.config.Config
field (see debian_install_v2/README.md for defaults/validation of each);
anything else goes through VBPUB_CONFIG_EXTRA_JSON, a raw JSON object
merged in last (wins over the named vars above):

  Fetch:     REPO_URL (default https://github.com/volkb79-2/vbpub),
             REPO_BRANCH (default main), INSTALL_DIR (default
             /opt/vbpub-debian-install-v2)
  Swap:      SWAP_DISK_TOTAL_GB, SWAP_FILE_COUNT, SWAP_PRIORITY,
             SWAP_DISCARD, PRESERVE_ROOT_SIZE_GB
  zswap:     ZSWAP_COMPRESSOR, ZSWAP_ZPOOL, ZSWAP_POOL_PERCENT, VM_SWAPPINESS
  Docker:    DOCKER_LIVE_RESTORE, DOCKER_LOG_DRIVER, DOCKER_LOG_MAX_SIZE,
             DOCKER_LOG_MAX_FILE, DOCKER_CLEANUP_MAX_AGE_HOURS
  Updates:   APT_AUTO_UPGRADE_MODE (full|security-only|notify-only),
             REBOOT_WINDOW_TIME (HH:MM)
  Stage toggles (yes/no): RUN_USER_CONFIG, RUN_APT_CONFIG,
             RUN_JOURNALD_CONFIG, RUN_DOCKER_INSTALL, RUN_KSM,
             RUN_OOMD_CONFIG, RUN_FSTRIM, RUN_DOCKER_CLEANUP,
             RUN_APT_AUTO_UPGRADE, RUN_AUTO_REBOOT
  Reboot:    AUTO_REBOOT_AFTER_STAGE1, NEVER_REBOOT
  Telegram:  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, CREDENTIAL_MODE
             (root-storage|systemd)
  Paths:     STATE_DIR, LOG_DIR, STAGE2_OUTPUT

  DRY_RUN=yes    — pass --dry-run through to the installer
  DEBUG_MODE=yes — verbose fetch/translate logging from this wrapper itself

Every yes/no var above is strict: only yes/true/1/on or no/false/0/off are
accepted. v1's three-state "auto" (e.g. AUTO_REBOOT_AFTER_STAGE1=auto) has
no v2 equivalent and is rejected rather than silently guessed — pick yes or
no.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path


REPO_URL_DEFAULT = "https://github.com/volkb79-2/vbpub"
REPO_BRANCH_DEFAULT = "main"
INSTALL_DIR_DEFAULT = "/opt/vbpub-debian-install-v2"
SUBTREE = ("scripts", "debian-install-v2")

_TRUE = {"yes", "true", "1", "on"}
_FALSE = {"no", "false", "0", "off"}

_STRING_FIELDS = {
    "ZSWAP_COMPRESSOR": "zswap_compressor",
    "ZSWAP_ZPOOL": "zswap_zpool",
    "DOCKER_LOG_DRIVER": "docker_log_driver",
    "DOCKER_LOG_MAX_SIZE": "docker_log_max_size",
    "DOCKER_LOG_MAX_FILE": "docker_log_max_file",
    "APT_AUTO_UPGRADE_MODE": "apt_auto_upgrade_mode",
    "REBOOT_WINDOW_TIME": "reboot_window_time",
    "CREDENTIAL_MODE": "credential_mode",
    "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
    "TELEGRAM_CHAT_ID": "telegram_chat_id",
    "STATE_DIR": "state_dir",
    "LOG_DIR": "log_dir",
    "STAGE2_OUTPUT": "stage2_output",
}
_INT_FIELDS = {
    "SWAP_DISK_TOTAL_GB": "swap_disk_total_gb",
    "SWAP_FILE_COUNT": "swap_file_count",
    "SWAP_PRIORITY": "swap_priority",
    "PRESERVE_ROOT_SIZE_GB": "preserve_root_size_gb",
    "ZSWAP_POOL_PERCENT": "zswap_pool_percent",
    "VM_SWAPPINESS": "vm_swappiness",
    "DOCKER_CLEANUP_MAX_AGE_HOURS": "docker_cleanup_max_age_hours",
}
_BOOL_FIELDS = {
    "AUTO_REBOOT_AFTER_STAGE1": "auto_reboot_after_stage1",
    "NEVER_REBOOT": "never_reboot",
    "SWAP_DISCARD": "swap_discard",
    "DOCKER_LIVE_RESTORE": "docker_live_restore",
    "RUN_USER_CONFIG": "run_user_config",
    "RUN_APT_CONFIG": "run_apt_config",
    "RUN_JOURNALD_CONFIG": "run_journald_config",
    "RUN_DOCKER_INSTALL": "run_docker_install",
    "RUN_KSM": "run_ksm",
    "RUN_OOMD_CONFIG": "run_oomd_config",
    "RUN_FSTRIM": "run_fstrim",
    "RUN_DOCKER_CLEANUP": "run_docker_cleanup",
    "RUN_APT_AUTO_UPGRADE": "run_apt_auto_upgrade",
    "RUN_AUTO_REBOOT": "run_auto_reboot",
}


class BootstrapError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"bootstrap-remote: {message}")


def _env_bool(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise BootstrapError(
        f"{name}={value!r} is not yes/no — v1's 'auto' tri-state has no v2 "
        f"equivalent; pick yes or no explicitly"
    )


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        raise BootstrapError(f"{name}={value!r} is not an integer") from None


def build_config() -> dict:
    config: dict = {}
    for env_name, field in _STRING_FIELDS.items():
        value = os.environ.get(env_name)
        if value:
            config[field] = value
    for env_name, field in _INT_FIELDS.items():
        value = _env_int(env_name)
        if value is not None:
            config[field] = value
    for env_name, field in _BOOL_FIELDS.items():
        value = _env_bool(env_name)
        if value is not None:
            config[field] = value
    extra = os.environ.get("VBPUB_CONFIG_EXTRA_JSON")
    if extra:
        try:
            parsed = json.loads(extra)
        except json.JSONDecodeError as exc:
            raise BootstrapError(f"VBPUB_CONFIG_EXTRA_JSON is not valid JSON: {exc}") from None
        if not isinstance(parsed, dict):
            raise BootstrapError("VBPUB_CONFIG_EXTRA_JSON must be a JSON object")
        config.update(parsed)
    return config


def fetch_subtree(repo_url: str, branch: str, install_dir: Path, *, debug: bool) -> None:
    tarball_url = f"{repo_url}/archive/refs/heads/{branch}.tar.gz"
    if debug:
        print(f"[bootstrap-remote] downloading {tarball_url}", file=sys.stderr)
    request = urllib.request.Request(tarball_url, headers={"User-Agent": "vbpub-bootstrap-remote"})
    prefix_len = len(SUBTREE)
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            install_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=response, mode="r|gz") as archive:
                for member in archive:
                    parts = Path(member.name).parts
                    if len(parts) < 1 + prefix_len or parts[1:1 + prefix_len] != SUBTREE:
                        continue
                    if not member.isfile():
                        continue
                    relative = Path(*parts[1 + prefix_len:])
                    target = install_dir / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        continue
                    target.write_bytes(extracted.read())
                    if member.mode & 0o111:
                        target.chmod(target.stat().st_mode | 0o111)
                    written += 1
    except urllib.error.URLError as exc:
        raise BootstrapError(f"could not fetch {tarball_url}: {exc}") from None
    if written == 0:
        raise BootstrapError(
            f"downloaded {tarball_url} but found nothing under {'/'.join(SUBTREE)}/ — "
            f"wrong REPO_URL/REPO_BRANCH, or the subtree moved"
        )
    if debug:
        print(f"[bootstrap-remote] wrote {written} files under {install_dir}", file=sys.stderr)


def main() -> int:
    debug = bool(_env_bool("DEBUG_MODE"))
    repo_url = os.environ.get("REPO_URL", REPO_URL_DEFAULT).rstrip("/")
    branch = os.environ.get("REPO_BRANCH", REPO_BRANCH_DEFAULT)
    install_dir = Path(os.environ.get("INSTALL_DIR", INSTALL_DIR_DEFAULT))

    if os.geteuid() != 0:
        raise BootstrapError("must run as root")

    fetch_subtree(repo_url, branch, install_dir, debug=debug)

    entrypoint = install_dir / "debian-install-v2.py"
    if not entrypoint.is_file():
        raise BootstrapError(f"fetch succeeded but {entrypoint} is missing")

    config = build_config()
    config_path = install_dir / "remote-install-config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.chmod(config_path, 0o600)
    if debug:
        redacted = {key: ("<redacted>" if "token" in key else value) for key, value in config.items()}
        print(f"[bootstrap-remote] config: {json.dumps(redacted)}", file=sys.stderr)

    argv = [sys.executable, str(entrypoint), "--action", "install", "--config", str(config_path)]
    if _env_bool("DRY_RUN"):
        argv.append("--dry-run")
    if debug:
        print(f"[bootstrap-remote] exec: {' '.join(argv)}", file=sys.stderr)
    try:
        return subprocess.run(argv).returncode
    finally:
        # Disposable: the installer's own credential handling
        # (/etc/vbpub/credentials/...) is what stage2 actually relies on —
        # this file was only scaffolding to hand stage1 its config.
        config_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
