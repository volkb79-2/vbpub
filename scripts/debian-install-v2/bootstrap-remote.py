#!/usr/bin/env python3
"""
vbpub debian-install v2 — remote bootstrap.

Fetches the runtime files under scripts/debian-install-v2/ with a shallow,
blobless Git clone and a sparse path set, plus the RELEASED cli-extended wheel
(sha256-verified, from the release manifest
`cli-extended-latest/latest.json` or pinned by env), translates a small set of env vars
into v2's strict-JSON config, and runs the installer. This is the v2 equivalent of v1's
scripts/debian-install/bootstrap.sh one-liner, not a continuation of it:
v2's own CLI is strict-JSON only (see debian_install_v2/config.py) and
rejects v1's env var names outright (SWAP_ARCH, SWAP_TOTAL_GB, SWAP_FILES,
USE_PARTITION, ...) — this wrapper is a thin, disposable adapter around
that JSON contract, not a reintroduction of v1's surface. Python 3 and
outbound HTTPS are required. Git is checked during bootstrap preflight before
the wheel is resolved or downloaded; if absent, this wrapper installs it from
the target's configured APT sources. The Git
remote must support partial-clone blob filtering; otherwise the wrapper refuses
rather than silently downloading every repository blob.

This file is an intentional cli-extended exception: it must be fetched and
executed before the target has downloaded cli-extended, so importing that
shared library here would create a bootstrap cycle. Keep this first-stage
adapter stdlib-only; after it fetches the installer tree and the cli-extended
wheel (written beside the entrypoint under its release filename, the only
cli_extended-*.whl kept there), the actual debian-install-v2.py entrypoint
imports the wheel directly (zipimport; no pip) and uses the shared CLI contract.

Usage (root):
  BOOTSTRAP_URL=https://raw.githubusercontent.com/volkb79-2/vbpub/main/scripts/debian-install-v2/bootstrap-remote.py \\
  SWAP_DISK_TOTAL_GB=auto SWAP_PARTITION_COUNT=4 ROOT_RESIZE_POLICY=resize SWAP_POLICY=on \\
  ZSWAP_COMPRESSOR=zstd ZSWAP_POOL_PERCENT=25 \\
  AUTO_REBOOT_AFTER_STAGE1=yes NEVER_REBOOT=no \\
  TELEGRAM_BOT_TOKEN=123:token TELEGRAM_CHAT_ID=456 \\
  python3 -c '
import os, sys, urllib.request
url = os.environ["BOOTSTRAP_URL"]
try:
    with urllib.request.urlopen(url, timeout=60) as response:
        source = response.read()
    if not source.strip():
        raise ValueError("downloaded bootstrap source was empty")
except Exception as exc:
    print(f"debian-install-v2: bootstrap download failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
exec(compile(source, url, "exec"), {"__name__": "__main__", "__file__": url})
  '

Env vars — every name below maps 1:1 to a debian_install_v2.config.Config
field (see debian_install_v2/README.md for defaults/validation of each);
anything else goes through VBPUB_CONFIG_EXTRA_JSON, a raw JSON object
merged in last (wins over the named vars above):

  Bootstrap: BOOTSTRAP_URL in the manual launcher above selects this first
             standalone file; REPO_URL/REPO_BRANCH below select the Git
             repository and branch from which it fetches runtime files. They
             are distinct source choices.
  Fetch:     REPO_URL (default https://github.com/volkb79-2/vbpub; any public
             HTTPS Git remote with partial-clone blob filtering), REPO_BRANCH
             (default main), INSTALL_DIR (default /opt/vbpub-debian-install-v2)
  Disk:      ROOT_RESIZE_POLICY (resize|untouched|grow-only),
             SWAP_POLICY (on|off|untouched)
  Swap:      SWAP_DISK_TOTAL_GB (integer GiB or auto),
             SWAP_PARTITION_COUNT, SWAP_PRIORITY, SWAP_DISCARD,
             PRESERVE_ROOT_SIZE_GB
  zswap:     ZSWAP_COMPRESSOR, ZSWAP_POOL_PERCENT, ZSWAP_ACCEPT_THRESHOLD_PERCENT,
             ZSWAP_SHRINKER_ENABLED (yes/no), VM_SWAPPINESS (0-200), IOCOST_ENABLED
             (yes/no). ZSWAP_ZPOOL is removed: ignored with a warning.
  Docker:    DOCKER_LIVE_RESTORE, DOCKER_LOG_DRIVER, DOCKER_LOG_MAX_SIZE,
             DOCKER_LOG_MAX_FILE, DOCKER_CLEANUP_MAX_AGE_HOURS
  Updates:   APT_AUTO_UPGRADE_MODE (full|security-only|notify-only),
             REBOOT_WINDOW_TIME (HH:MM)
  Stage toggles (yes/no): RUN_USER_CONFIG, RUN_APT_CONFIG,
             RUN_JOURNALD_CONFIG, RUN_DOCKER_INSTALL, RUN_KSM,
             RUN_OOMD_CONFIG, RUN_FSTRIM, RUN_DOCKER_CLEANUP,
             RUN_APT_AUTO_UPGRADE, RUN_AUTO_REBOOT, RUN_ZSWAP_CONFIG,
             RUN_CGROUP2_CONFIG
  Reboot:    AUTO_REBOOT_AFTER_STAGE1, NEVER_REBOOT
  Notify:    NOTIFY_BACKEND (mattermost|telegram|none; unset = infer from the
             credential present, refuse when both are), MATTERMOST_WEBHOOK_URL
             (secret incoming-webhook URL, post-only), NOTIFY_HOST_LABEL
             (free-text label shown first in every Mattermost message)
  Telegram:  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, CREDENTIAL_MODE
             (root-storage|systemd), TELEGRAM_VERBOSE_PROGRESS (yes/no -
             also notify on every internal step, not just the stage-boundary
             start/reboot/resume/success/failure messages sent by default)
  SSH:       CONTROLLER_SSH_PUBKEY - a one-line authorized_keys entry for the
             controller's own ephemeral, per-host bootstrap key. Installed as
             the very first stage1 step (before this key's own registration
             with the provider is even guaranteed to have taken effect) and
             removed again after the install COMPLETES (stage2_done) unless
             RETAIN_CONTROLLER_SSH_KEY=yes (yes|no, default no) keeps it for
             post-reboot controller access. Never removed on a failed install. Leave unset to skip entirely; the
             operator's own persistent access key is never touched by this
             either way.
             Persistent root keys use `ssh_authorized_keys` in
             VBPUB_CONFIG_EXTRA_JSON as an array of public-key lines; these
             are additive and never removed by controller-key cleanup.
  Paths:     STATE_DIR, LOG_DIR, STAGE2_OUTPUT
  Library:   CLI_EXTENDED_WHEEL_URL + CLI_EXTENDED_WHEEL_SHA256 pin one exact
             cli-extended wheel (set both or neither; the URL's last path
             segment must be a cli_extended-*.whl filename). With neither,
             the release manifest at CLI_EXTENDED_LATEST_URL (default
             https://github.com/volkb79-2/vbpub/releases/download/cli-extended-latest/latest.json)
             supplies the wheel's `url` and `sha256`.
             Repository fetching is Git-provider-neutral; this wheel pointer
             remains GitHub-hosted by default unless overridden.

  DRY_RUN=yes    — pass --dry-run through to the installer
  DEBUG_MODE=yes — verbose fetch/translate logging from this wrapper itself

Every yes/no var above is strict: only yes/true/1/on or no/false/0/off are
accepted. v1's three-state "auto" (e.g. AUTO_REBOOT_AFTER_STAGE1=auto) has
no v2 equivalent and is rejected rather than silently guessed — pick yes or
no.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

REPO_URL_DEFAULT = "https://github.com/volkb79-2/vbpub"
REPO_BRANCH_DEFAULT = "main"
INSTALL_DIR_DEFAULT = "/opt/vbpub-debian-install-v2"
SUBTREE = ("scripts", "debian-install-v2")
LATEST_URL_DEFAULT = (
    "https://github.com/volkb79-2/vbpub/releases/download/cli-extended-latest/latest.json"
)
WHEEL_GLOB = "cli_extended-*.whl"
WHEEL_MARKER = "cli_extended/__init__.py"
USER_AGENT = "vbpub-bootstrap-remote"
_SHA256 = re.compile(r"[0-9a-f]{64}")
WHEEL_MAX_BYTES = 16 * 1024 * 1024
MANIFEST_MAX_BYTES = 1024 * 1024
PROFILE_MAX_BYTES = 1024 * 1024
GIT_FETCH_TIMEOUT_S = 600

_TRUE = {"yes", "true", "1", "on"}
_FALSE = {"no", "false", "0", "off"}

_STRING_FIELDS = {
    "ZSWAP_COMPRESSOR": "zswap_compressor",
    "DOCKER_LOG_DRIVER": "docker_log_driver",
    "DOCKER_LOG_MAX_SIZE": "docker_log_max_size",
    "DOCKER_LOG_MAX_FILE": "docker_log_max_file",
    "APT_AUTO_UPGRADE_MODE": "apt_auto_upgrade_mode",
    "REBOOT_WINDOW_TIME": "reboot_window_time",
    "CREDENTIAL_MODE": "credential_mode",
    "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
    "TELEGRAM_CHAT_ID": "telegram_chat_id",
    "NOTIFY_BACKEND": "notify_backend",
    "MATTERMOST_WEBHOOK_URL": "mattermost_webhook_url",
    "NOTIFY_HOST_LABEL": "notify_host_label",
    "STATE_DIR": "state_dir",
    "LOG_DIR": "log_dir",
    "STAGE2_OUTPUT": "stage2_output",
    "CONTROLLER_SSH_PUBKEY": "controller_ssh_pubkey",
}
_INT_FIELDS = {
    "SWAP_DISK_TOTAL_GB": "swap_disk_total_gb",
    "SWAP_PARTITION_COUNT": "swap_partition_count",
    "SWAP_PRIORITY": "swap_priority",
    "PRESERVE_ROOT_SIZE_GB": "preserve_root_size_gb",
    "ZSWAP_POOL_PERCENT": "zswap_pool_percent",
    "ZSWAP_ACCEPT_THRESHOLD_PERCENT": "zswap_accept_threshold_percent",
    "VM_SWAPPINESS": "vm_swappiness",
    "DOCKER_CLEANUP_MAX_AGE_HOURS": "docker_cleanup_max_age_hours",
}
_LEGACY_INT_FIELDS = {"SWAP_FILE_COUNT": "swap_partition_count"}
_POLICY_STRING_FIELDS = {
    "ROOT_RESIZE_POLICY": "root_resize_policy",
    "SWAP_POLICY": "swap_policy",
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
    "ZSWAP_SHRINKER_ENABLED": "zswap_shrinker_enabled",
    "IOCOST_ENABLED": "iocost_enabled",
    "RUN_OOMD_CONFIG": "run_oomd_config",
    "RUN_FSTRIM": "run_fstrim",
    "RUN_DOCKER_CLEANUP": "run_docker_cleanup",
    "RUN_APT_AUTO_UPGRADE": "run_apt_auto_upgrade",
    "APT_UPGRADE_AT_INSTALL": "apt_upgrade_at_install",
    "RUN_AUTO_REBOOT": "run_auto_reboot",
    "RUN_ZSWAP_CONFIG": "run_zswap_config",
    "RUN_CGROUP2_CONFIG": "run_cgroup2_config",
    "TELEGRAM_VERBOSE_PROGRESS": "telegram_verbose_progress",
    "RETAIN_CONTROLLER_SSH_KEY": "retain_controller_ssh_key",
}


class BootstrapError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"bootstrap-remote: {message}")


PROVIDER_FILE_DIR = Path("/root")


def restrict_provider_files(root: Path | None = None) -> None:
    """chmod 0600 every `<root>/custom_script*` file (the provider's script,
    custom_script.output and .output2). Best effort, never raises.

    Called as the VERY FIRST action of main(), before config parsing or
    anything else that can fail (LT-F-r1002-03): the script and its output can
    hold the webhook URL, and an early failure used to leave a 0700 script and
    a 0644 output behind.
    """
    base = PROVIDER_FILE_DIR if root is None else root
    try:
        candidates = sorted(base.glob("custom_script*"))
    except OSError:
        return
    for path in candidates:
        try:
            if path.is_file() and not path.is_symlink():
                path.chmod(0o600)
        except OSError as exc:
            print(f"bootstrap-remote: could not chmod 0600 {path}: {exc}", file=sys.stderr)


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


# Mirrors debian_install_v2.config.OBSOLETE_VARIABLES (v1's env-var names).
# Duplicated rather than imported: this wrapper must run standalone before
# the fetched debian_install_v2 package exists on disk at all -- see the
# module docstring's explicit first-stage bootstrap exception.
# config.py's own OBSOLETE_VARIABLES check only inspects the JSON config
# FILE's keys, never env vars, so an operator setting one of these (the
# likely mistake, since they're literally the v1 names) would otherwise be
# silently ignored here, not "rejected outright" as this file's own
# docstring claims (adversarial review finding).
_V1_OBSOLETE_ENV_VARS = {"SWAP_ARCH", "SWAP_TOTAL_GB", "SWAP_FILES", "USE_PARTITION"}


def build_config() -> dict:
    present_obsolete = sorted(name for name in _V1_OBSOLETE_ENV_VARS if os.environ.get(name))
    if present_obsolete:
        raise BootstrapError(
            f"{', '.join(present_obsolete)} {'is' if len(present_obsolete) == 1 else 'are'} v1 env var name(s) "
            f"with no v2 equivalent -- see this file's docstring for the current names"
        )
    if os.environ.get("ZSWAP_ZPOOL"):
        # Removed knob (absent on 7.x kernels): ignored, never an error.
        print("bootstrap-remote: ZSWAP_ZPOOL is removed and ignored", file=sys.stderr)
    config: dict = {}
    for env_name, field in {**_STRING_FIELDS, **_POLICY_STRING_FIELDS}.items():
        value = os.environ.get(env_name)
        if value:
            config[field] = value
    legacy_values = []
    for env_name, field in _LEGACY_INT_FIELDS.items():
        value = _env_int(env_name)
        if value is not None:
            legacy_values.append((env_name, field, value))
    for env_name, field, value in legacy_values:
        if field in config and config[field] != value:
            raise BootstrapError(f"{env_name} conflicts with the canonical {field} setting")
        config[field] = value
    for env_name, field in _INT_FIELDS.items():
        raw_value = os.environ.get(env_name)
        is_auto = env_name == "SWAP_DISK_TOTAL_GB" and raw_value is not None and raw_value.strip().lower() == "auto"
        value = None if is_auto else _env_int(env_name)
        if is_auto:
            # An explicit `auto` must clear a fixed value inherited from the
            # selected profile; omission would silently retain that profile's
            # number instead of deriving from this VM's disk and RAM.
            config[field] = None
        elif value is not None:
            if field in config and config[field] != value:
                raise BootstrapError(f"{env_name} conflicts with another setting for {field}")
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


def redact_url(url: str) -> str:
    """`url` without userinfo (user[:token]@), for DISPLAY only (logs, errors,
    stage output and notifications); the real URL is still used for the fetch."""
    try:
        parts = urllib.parse.urlsplit(url)
        if "@" not in parts.netloc:
            return url
        host = parts.hostname or ""
        if ":" in host:  # IPv6 literal
            host = f"[{host}]"
        netloc = f"{host}:{parts.port}" if parts.port else host
    except ValueError:
        return "<unparseable url redacted>"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


_SPARSE_PATHS = (
    "/scripts/debian-install-v2/VERSION",
    "/scripts/debian-install-v2/debian-install-v2.py",
    "/scripts/debian-install-v2/debian_install_v2/*.py",
    "/scripts/debian-install-v2/debian_install_v2/vendor/iocost_coef_gen.py",
    "/scripts/debian-install-v2/debian_install_v2/vendor/000*.patch",
    "/scripts/debian-install-v2/tools/iocost_coef_gen.py",
    "/scripts/debian-install-v2/tools/iocost_coef_gen.py.sha256",
    "/scripts/debian-install-v2/profiles/*.jsonc",
)


def _ensure_git(*, debug: bool) -> str:
    git = shutil.which("git")
    if git:
        return git
    apt_get = shutil.which("apt-get")
    if not apt_get:
        raise BootstrapError("git is missing and apt-get is unavailable to install it")
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    for argv in (
        [apt_get, "update", "-qq"],
        [apt_get, "install", "-y", "--no-install-recommends", "git"],
    ):
        if debug:
            print(f"[bootstrap-remote] installing Git: {Path(argv[0]).name} {argv[1]}", file=sys.stderr)
        try:
            completed = subprocess.run(
                argv, check=False, capture_output=True, text=True,
                timeout=GIT_FETCH_TIMEOUT_S, env=env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BootstrapError(f"could not install required Git package: {exc}") from None
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            tail = detail[-1] if detail else f"exit {completed.returncode}"
            raise BootstrapError(f"could not install required Git package: {tail}")
    git = shutil.which("git")
    if not git:
        raise BootstrapError("APT reported Git installed but git is still not on PATH")
    return git


def _validate_git_remote(repo_url: str) -> None:
    try:
        remote = urllib.parse.urlsplit(repo_url)
    except ValueError:
        raise BootstrapError("REPO_URL is not a valid HTTPS Git remote (host unavailable)") from None

    try:
        host = remote.hostname
    except ValueError:
        host = None
    if not host:
        safe_host = "<missing or invalid>"
    elif ":" in host:
        safe_host = f"[{host}]" if re.fullmatch(r"[0-9A-Fa-f:.]+", host) else "<invalid>"
    else:
        try:
            safe_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            safe_host = "<invalid>"
        if not re.fullmatch(r"[A-Za-z0-9.-]+", safe_host):
            safe_host = "<invalid>"
    try:
        port = remote.port
    except ValueError:
        port = None
    if port is not None and safe_host not in ("<missing or invalid>", "<invalid>"):
        safe_host = f"{safe_host}:{port}"

    if (
        remote.scheme != "https" or not remote.hostname or remote.username
        or remote.password or remote.query or remote.fragment
        or any(char.isspace() for char in repo_url)
    ):
        raise BootstrapError(
            "REPO_URL must be a public https:// Git remote without userinfo, "
            f"query, fragment, or whitespace (host: {safe_host})"
        )


def _git_run(git: str, args: list[str], *, what: str, debug: bool) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    try:
        completed = subprocess.run(
            [git, *args], check=False, capture_output=True, text=True,
            timeout=GIT_FETCH_TIMEOUT_S, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BootstrapError(f"Git {what} failed: {exc}") from None
    output = completed.stdout.strip()
    detail = completed.stderr.strip()
    if debug and detail:
        print(f"[bootstrap-remote] git {what}: {detail}", file=sys.stderr)
    if "filtering not recognized by server" in detail.lower():
        raise BootstrapError(
            "Git remote does not support partial-clone blob filtering; refusing "
            "to download the complete repository"
        )
    if completed.returncode:
        tail = (detail or output).splitlines()
        message = tail[-1] if tail else f"exit {completed.returncode}"
        raise BootstrapError(f"Git {what} failed: {message}")
    return output


def preflight_git(repo_url: str, branch: str, *, debug: bool) -> str:
    """Validate the source and ensure Git before bootstrap network fetches."""
    _validate_git_remote(repo_url)
    if not branch or branch.startswith("-") or any(char.isspace() for char in branch):
        raise BootstrapError("REPO_BRANCH must be a non-empty Git branch name without whitespace")
    git = _ensure_git(debug=debug)
    _git_run(git, ["check-ref-format", "--branch", branch], what="branch validation", debug=debug)
    return git


def fetch_subtree(
    repo_url: str,
    branch: str,
    install_dir: Path,
    *,
    debug: bool,
    git_path: str | None = None,
) -> None:
    """Fetch only runtime blobs from a Git remote that supports partial clone."""
    _validate_git_remote(repo_url)
    if not branch or branch.startswith("-") or any(char.isspace() for char in branch):
        raise BootstrapError("REPO_BRANCH must be a non-empty Git branch name without whitespace")
    if install_dir.is_symlink():
        raise BootstrapError(f"refusing symlink install directory: {install_dir}")

    git = git_path or _ensure_git(debug=debug)
    _git_run(git, ["check-ref-format", "--branch", branch], what="branch validation", debug=debug)
    if debug:
        print(
            f"[bootstrap-remote] shallow sparse fetch from {redact_url(repo_url)} "
            f"(branch {branch})",
            file=sys.stderr,
        )

    with tempfile.TemporaryDirectory(prefix="vbpub-debian-install-fetch-") as temporary:
        checkout = Path(temporary) / "source"
        clone_stderr = ""
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        try:
            clone = subprocess.run(
                [
                    git, "clone", "--depth=1", "--filter=blob:none", "--no-checkout",
                    "--single-branch", "--branch", branch, repo_url, str(checkout),
                ],
                check=False, capture_output=True, text=True,
                timeout=GIT_FETCH_TIMEOUT_S, env=env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BootstrapError(f"Git clone failed for {redact_url(repo_url)}: {exc}") from None
        clone_stderr = clone.stderr.strip()
        if "filtering not recognized by server" in clone_stderr.lower():
            raise BootstrapError(
                "Git remote does not support partial-clone blob filtering; refusing "
                "to download the complete repository"
            )
        if clone.returncode:
            tail = (clone_stderr or clone.stdout.strip()).splitlines()
            message = tail[-1] if tail else f"exit {clone.returncode}"
            raise BootstrapError(f"Git clone failed for {redact_url(repo_url)}: {message}")

        _git_run(git, ["-C", str(checkout), "sparse-checkout", "init", "--no-cone"], what="sparse initialization", debug=debug)
        _git_run(
            git,
            ["-C", str(checkout), "sparse-checkout", "set", "--no-cone", *_SPARSE_PATHS],
            what="sparse path selection",
            debug=debug,
        )
        _git_run(git, ["-C", str(checkout), "checkout", "--detach", "FETCH_HEAD"], what="sparse checkout", debug=debug)

        source_root = checkout.joinpath(*SUBTREE)
        entrypoint = source_root / "debian-install-v2.py"
        if not entrypoint.is_file() or entrypoint.is_symlink():
            raise BootstrapError(
                f"Git branch {branch!r} does not contain the required "
                f"{'/'.join((*SUBTREE, 'debian-install-v2.py'))} file"
            )

        install_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        for current, directories, filenames in os.walk(source_root, followlinks=False):
            current_path = Path(current)
            for name in list(directories):
                if (current_path / name).is_symlink():
                    raise BootstrapError(f"refusing symlink in fetched installer tree: {current_path / name}")
            for name in filenames:
                source = current_path / name
                mode = source.lstat().st_mode
                if not stat.S_ISREG(mode):
                    raise BootstrapError(f"refusing non-regular file in fetched installer tree: {source}")
                relative = source.relative_to(source_root)
                target = install_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink():
                    raise BootstrapError(f"refusing symlink install target: {target}")
                shutil.copyfile(source, target)
                target.chmod(mode & 0o777)
                written += 1

    if written == 0:
        raise BootstrapError(
            f"Git branch {branch!r} had no runtime files under "
            f"{'/'.join(SUBTREE)}; wrong REPO_URL/REPO_BRANCH, or the tree moved"
        )
    if debug:
        print(f"[bootstrap-remote] copied {written} sparse runtime files under {install_dir}", file=sys.stderr)


def _require_https(url: str, what: str) -> str:
    """Refuse every scheme but https (http, file, ftp, ...): no exceptions."""
    if urllib.parse.urlparse(url).scheme != "https":
        raise BootstrapError(f"{what} {url!r} must be an https:// URL")
    return url


def _fetch_profile_url(url: str, username: str, password: str) -> str:
    """Fetch JSONC over HTTPS, optionally with origin-scoped Basic Auth."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        raise BootstrapError("profile URL is not a valid HTTPS URL") from None
    if (
        parts.scheme != "https" or not parts.hostname or parts.username
        or parts.password or parts.query or parts.fragment
        or any(char.isspace() for char in url)
    ):
        raise BootstrapError(
            "profile URL must be an https:// URL without userinfo, query, "
            "fragment, or whitespace; supply Basic Auth separately"
        )
    if bool(username) != bool(password) or ":" in username or any(
        ord(char) < 0x20 or ord(char) == 0x7f for char in username + password
    ):
        raise BootstrapError("profile Basic Auth requires a username/password pair without control characters")

    opener_handlers: list[Any] = []
    if username:
        password_manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        password_manager.add_password(None, url, username, password)
        opener_handlers.append(urllib.request.HTTPBasicAuthHandler(password_manager))

    class RejectRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener_handlers.append(RejectRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.build_opener(*opener_handlers).open(request, timeout=60) as response:
            data = response.read(PROFILE_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise BootstrapError(f"profile fetch from {redact_url(url)} failed with HTTP {exc.code}") from None
    except (OSError, http.client.HTTPException) as exc:
        raise BootstrapError(f"profile fetch from {redact_url(url)} failed: {exc}") from None
    if len(data) > PROFILE_MAX_BYTES:
        raise BootstrapError(f"profile at {redact_url(url)} exceeds the {PROFILE_MAX_BYTES}-byte limit")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise BootstrapError(f"profile at {redact_url(url)} is not UTF-8") from None


def _load_profile(install_dir: Path) -> dict:
    kind = os.environ.get("VBPUB_PROFILE_KIND", "")
    if not kind or kind == "none":
        return {}
    sys.path.insert(0, str(install_dir))
    try:
        from debian_install_v2.profiles import (
            load_builtin_profile,
            parse_profile_jsonc,
        )
    except Exception as exc:
        raise BootstrapError(f"could not load the profile validator from fetched source: {exc}") from None

    if kind == "builtin":
        profile_id = os.environ.get("VBPUB_PROFILE_ID", "")
        try:
            return load_builtin_profile(profile_id, profile_dir=install_dir / "profiles")
        except Exception as exc:
            raise BootstrapError(f"could not load released profile {profile_id!r}: {exc}") from None
    if kind == "embedded":
        encoded = os.environ.get("VBPUB_PROFILE_JSONC_B64", "")
        try:
            raw = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise BootstrapError("embedded profile is not valid base64-encoded UTF-8") from None
        try:
            return parse_profile_jsonc(raw, source="embedded profile")
        except Exception as exc:
            raise BootstrapError(f"embedded profile is invalid: {exc}") from None
    if kind == "https":
        url = os.environ.get("VBPUB_PROFILE_URL", "")
        raw = _fetch_profile_url(
            url,
            os.environ.get("VBPUB_PROFILE_AUTH_USERNAME", ""),
            os.environ.get("VBPUB_PROFILE_AUTH_PASSWORD", ""),
        )
        try:
            return parse_profile_jsonc(raw, source=f"profile at {redact_url(url)}")
        except Exception as exc:
            raise BootstrapError(f"remote profile is invalid: {exc}") from None
    raise BootstrapError("VBPUB_PROFILE_KIND must be builtin, embedded, https, or none")


def _discard_bootstrap_secrets() -> None:
    """Drop ISO and host credentials before the installer process is launched."""
    for name in (
        "MATTERMOST_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "CONTROLLER_SSH_PUBKEY", "VBPUB_CONFIG_EXTRA_JSON",
        "VBPUB_PROFILE_JSONC_B64", "VBPUB_PROFILE_AUTH_USERNAME",
        "VBPUB_PROFILE_AUTH_PASSWORD", "CLI_EXTENDED_WHEEL_URL",
        "CLI_EXTENDED_WHEEL_SHA256",
    ):
        os.environ.pop(name, None)


def _download(url: str, what: str, max_bytes: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(max_bytes + 1)
    except (OSError, http.client.HTTPException) as exc:
        # URLError and HTTPError are OSError subclasses, and so is a socket
        # timeout; IncompleteRead is an HTTPException -- all are "could not
        # fetch", one diagnostic.
        raise BootstrapError(f"could not fetch {what} {url}: {exc!r}") from None
    if len(data) > max_bytes:
        raise BootstrapError(f"{what} {url} is larger than the {max_bytes}-byte cap")
    return data


def _manifest_field(manifest: dict, name: str, url: str) -> str:
    value = manifest.get(name)
    if not isinstance(value, str) or not value:
        raise BootstrapError(f"release manifest {url} has no usable {name!r} field")
    return value


def resolve_wheel(*, debug: bool = False) -> tuple[str, str]:
    """Return (wheel url, lowercase sha256) for the cli-extended wheel to install.

    Both CLI_EXTENDED_WHEEL_URL and CLI_EXTENDED_WHEEL_SHA256 pin a wheel;
    with neither, the release manifest's `url` and `sha256` are used.
    """
    pinned_url = os.environ.get("CLI_EXTENDED_WHEEL_URL")
    pinned_sha = os.environ.get("CLI_EXTENDED_WHEEL_SHA256")
    if bool(pinned_url) != bool(pinned_sha):
        raise BootstrapError(
            "CLI_EXTENDED_WHEEL_URL and CLI_EXTENDED_WHEEL_SHA256 must be set together "
            "(or both unset to use the latest release)"
        )
    if pinned_url:
        url, sha256 = _require_https(pinned_url, "CLI_EXTENDED_WHEEL_URL"), pinned_sha
    else:
        latest_url = _require_https(
            os.environ.get("CLI_EXTENDED_LATEST_URL") or LATEST_URL_DEFAULT,
            "CLI_EXTENDED_LATEST_URL",
        )
        if debug:
            print(f"[bootstrap-remote] reading release manifest {latest_url}", file=sys.stderr)
        raw = _download(latest_url, "the cli-extended release manifest", MANIFEST_MAX_BYTES)
        try:
            manifest = json.loads(raw)
        except ValueError as exc:
            raise BootstrapError(f"release manifest {latest_url} is not valid JSON: {exc}") from None
        if not isinstance(manifest, dict):
            raise BootstrapError(f"release manifest {latest_url} must be a JSON object")
        url = _require_https(
            _manifest_field(manifest, "url", latest_url),
            f"the 'url' field of release manifest {latest_url}",
        )
        sha256 = _manifest_field(manifest, "sha256", latest_url)
    sha256 = sha256.strip().lower()
    if not _SHA256.fullmatch(sha256):
        raise BootstrapError(f"cli-extended wheel sha256 {sha256!r} is not 64 hexadecimal digits")
    return url, sha256


def download_wheel(url: str, sha256: str, *, debug: bool) -> tuple[str, bytes]:
    """Download and fully verify the wheel in memory; writes nothing."""
    name = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
    if not (name.startswith("cli_extended-") and name.endswith(".whl")) or "/" in name:
        raise BootstrapError(
            f"cli-extended wheel URL {url} must end in a cli_extended-*.whl filename, got {name!r}"
        )
    if debug:
        print(f"[bootstrap-remote] downloading {url}", file=sys.stderr)
    data = _download(url, "the cli-extended wheel", WHEEL_MAX_BYTES)
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise BootstrapError(
            f"cli-extended wheel sha256 mismatch for {url}: expected {sha256}, got {actual}"
        )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = set(archive.namelist())
    except zipfile.BadZipFile:
        raise BootstrapError(f"cli-extended wheel {url} is not a zip archive") from None
    if WHEEL_MARKER not in members:
        raise BootstrapError(f"cli-extended wheel {url} does not contain {WHEEL_MARKER}")
    return name, data


def write_wheel(name: str, data: bytes, install_dir: Path, *, debug: bool) -> Path:
    """Write the verified wheel atomically, then drop every other cli_extended wheel."""
    install_dir.mkdir(parents=True, exist_ok=True)
    matches = sorted(install_dir.glob(WHEEL_GLOB))
    directories = [str(path) for path in matches if path.is_dir()]
    if directories:
        raise BootstrapError(
            f"refusing to replace cli-extended wheel(s) that are directories: {', '.join(directories)}"
        )
    target = install_dir / name
    temporary = install_dir / f"{name}.tmp"
    temporary.write_bytes(data)
    os.replace(temporary, target)
    for other in matches:
        if other != target:
            other.unlink()
    if debug:
        print(f"[bootstrap-remote] wrote {target}", file=sys.stderr)
    return target


def install_wheel(url: str, sha256: str, install_dir: Path, *, debug: bool) -> Path:
    """Download, verify and write the wheel; keep it as the only cli_extended wheel."""
    name, data = download_wheel(url, sha256, debug=debug)
    return write_wheel(name, data, install_dir, debug=debug)


def main() -> int:
    restrict_provider_files()  # LT-F-r1002-03: first action, before anything can fail
    debug = bool(_env_bool("DEBUG_MODE"))
    repo_url = os.environ.get("REPO_URL", REPO_URL_DEFAULT).rstrip("/")
    branch = os.environ.get("REPO_BRANCH", REPO_BRANCH_DEFAULT)
    install_dir = Path(os.environ.get("INSTALL_DIR", INSTALL_DIR_DEFAULT))

    if os.geteuid() != 0:
        raise BootstrapError("must run as root")

    git_path = preflight_git(repo_url, branch, debug=debug)
    wheel_url, wheel_sha256 = resolve_wheel(debug=debug)
    # The wheel is resolved, downloaded and verified before anything is
    # written to the install dir; a bad pin or digest leaves it untouched.
    wheel_name, wheel_data = download_wheel(wheel_url, wheel_sha256, debug=debug)
    fetch_subtree(repo_url, branch, install_dir, debug=debug, git_path=git_path)
    write_wheel(wheel_name, wheel_data, install_dir, debug=debug)

    entrypoint = install_dir / "debian-install-v2.py"
    if not entrypoint.is_file():
        raise BootstrapError(f"fetch succeeded but {entrypoint} is missing")

    config = _load_profile(install_dir)
    config.update(build_config())
    try:
        from debian_install_v2.config import load_config

        load_config(raw_json=json.dumps(config))
    except Exception as exc:
        raise BootstrapError(f"resolved profile and per-host settings are invalid: {exc}") from None
    _discard_bootstrap_secrets()
    config_path = install_dir / "remote-install-config.json"
    # The file holds the webhook URL / Telegram token: create it 0600 from the
    # first byte (never write_text-then-chmod, which exposes it at the umask).
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)  # a pre-existing file keeps its old mode otherwise
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(json.dumps(config, indent=2) + "\n")
    finally:
        if fd >= 0:
            os.close(fd)
    if debug:
        secret_fields = {
            "mattermost_webhook_url", "telegram_bot_token", "telegram_chat_id",
            "controller_ssh_pubkey", "ssh_authorized_keys",
        }
        redacted = {
            key: ("<redacted>" if key in secret_fields else value)
            for key, value in config.items()
        }
        print(f"[bootstrap-remote] config: {json.dumps(redacted)}", file=sys.stderr)

    argv = [sys.executable, str(entrypoint), "install", "--config", str(config_path), "--yes"]
    if _env_bool("DRY_RUN"):
        argv.append("--dry-run")
    if debug:
        print(f"[bootstrap-remote] exec: {' '.join(argv)}", file=sys.stderr)
    try:
        return subprocess.run(argv, check=False).returncode
    finally:
        # Disposable: the installer's own credential handling
        # (/etc/vbpub/credentials/...) is what stage2 actually relies on —
        # this file was only scaffolding to hand stage1 its config.
        config_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
