#!/usr/bin/env python3
"""
vbpub debian-install v2 — remote bootstrap.

Fetches scripts/debian-install-v2/ from the public volkb79-2/vbpub repo (one
codeload tarball — no git, no `tar` binary, stdlib only: urllib + tarfile) and
the RELEASED cli-extended wheel (sha256-verified, from the release manifest
`cli-extended-latest/latest.json` or pinned by env), translates a small set of env vars
into v2's strict-JSON config, and runs the installer. This is the v2 equivalent of v1's
scripts/debian-install/bootstrap.sh one-liner, not a continuation of it:
v2's own CLI is strict-JSON only (see debian_install_v2/config.py) and
rejects v1's env var names outright (SWAP_ARCH, SWAP_TOTAL_GB, SWAP_FILES,
USE_PARTITION, ...) — this wrapper is a thin, disposable adapter around
that JSON contract, not a reintroduction of v1's surface. Only python3 and
outbound HTTPS are required before this runs; apt/git are never needed to
fetch this wrapper or the code it pulls down (the installer's own stage1
installs git/curl/docker for itself, once it starts).

This file is an intentional cli-extended exception: it must be fetched and
executed before the target has downloaded cli-extended, so importing that
shared library here would create a bootstrap cycle. Keep this first-stage
adapter stdlib-only; after it fetches the installer tree and the cli-extended
wheel (written beside the entrypoint under its release filename, the only
cli_extended-*.whl kept there), the actual debian-install-v2.py entrypoint
imports the wheel directly (zipimport; no pip) and uses the shared CLI contract.

Usage (root):
  BOOTSTRAP_URL=https://raw.githubusercontent.com/volkb79-2/vbpub/main/scripts/debian-install-v2/bootstrap-remote.py \\
  SWAP_DISK_TOTAL_GB=32 SWAP_FILE_COUNT=8 ZSWAP_COMPRESSOR=zstd ZSWAP_POOL_PERCENT=25 \\
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
             standalone file; REPO_URL/REPO_BRANCH below select the archive
             it downloads. They are distinct source choices.
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
             removed again as the last stage2 step - no further controller
             access is needed once the install is done. Leave unset to skip
             entirely; the operator's own persistent access key is never
             touched by this either way.
  Paths:     STATE_DIR, LOG_DIR, STAGE2_OUTPUT
  Library:   CLI_EXTENDED_WHEEL_URL + CLI_EXTENDED_WHEEL_SHA256 pin one exact
             cli-extended wheel (set both or neither; the URL's last path
             segment must be a cli_extended-*.whl filename). With neither,
             the release manifest at CLI_EXTENDED_LATEST_URL (default
             https://github.com/volkb79-2/vbpub/releases/download/cli-extended-latest/latest.json)
             supplies the wheel's `url` and `sha256`.

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
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

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
    "TELEGRAM_VERBOSE_PROGRESS": "telegram_verbose_progress",
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
    request = urllib.request.Request(tarball_url, headers={"User-Agent": USER_AGENT})
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            install_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=response, mode="r|gz") as archive:
                for member in archive:
                    parts = Path(member.name).parts
                    if parts[1:1 + len(SUBTREE)] != SUBTREE or not member.isfile():
                        continue
                    relative_parts = parts[1 + len(SUBTREE):]
                    if not relative_parts or ".." in relative_parts or any(
                        Path(part).is_absolute() for part in relative_parts
                    ):
                        # Tar-slip: a member name embedding ".." after the
                        # matched subtree prefix would otherwise resolve
                        # outside install_dir the moment target.write_bytes()
                        # touches the real filesystem -- this process runs as
                        # root (enforced in main()).
                        raise BootstrapError(f"refusing archive member with an unsafe path: {member.name!r}")
                    target = install_dir.joinpath(*relative_parts)
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
    except tarfile.TarError as exc:
        # A flaky connection on an unattended remote host can truncate the
        # gzip/tar stream mid-download -- tarfile.ReadError and friends are
        # not URLError subclasses, and would otherwise surface as a bare
        # traceback instead of this tool's own diagnostic.
        raise BootstrapError(f"corrupt or truncated download from {tarball_url}: {exc}") from None
    if written == 0:
        raise BootstrapError(
            f"downloaded {tarball_url} but required source tree was empty or missing: "
            f"{'/'.join(SUBTREE)} — wrong REPO_URL/REPO_BRANCH, or the tree moved"
        )
    if debug:
        print(
            f"[bootstrap-remote] wrote {written} installer files under {install_dir}",
            file=sys.stderr,
        )


def _require_https(url: str, what: str) -> str:
    """Refuse every scheme but https (http, file, ftp, ...): no exceptions."""
    if urllib.parse.urlparse(url).scheme != "https":
        raise BootstrapError(f"{what} {url!r} must be an https:// URL")
    return url


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
    debug = bool(_env_bool("DEBUG_MODE"))
    repo_url = os.environ.get("REPO_URL", REPO_URL_DEFAULT).rstrip("/")
    branch = os.environ.get("REPO_BRANCH", REPO_BRANCH_DEFAULT)
    install_dir = Path(os.environ.get("INSTALL_DIR", INSTALL_DIR_DEFAULT))

    if os.geteuid() != 0:
        raise BootstrapError("must run as root")

    wheel_url, wheel_sha256 = resolve_wheel(debug=debug)
    # The wheel is resolved, downloaded and verified before anything is
    # written to the install dir; a bad pin or digest leaves it untouched.
    wheel_name, wheel_data = download_wheel(wheel_url, wheel_sha256, debug=debug)
    fetch_subtree(repo_url, branch, install_dir, debug=debug)
    write_wheel(wheel_name, wheel_data, install_dir, debug=debug)

    entrypoint = install_dir / "debian-install-v2.py"
    if not entrypoint.is_file():
        raise BootstrapError(f"fetch succeeded but {entrypoint} is missing")

    config = build_config()
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
        redacted = {
            key: ("<redacted>" if "token" in key or "webhook" in key else value)
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
