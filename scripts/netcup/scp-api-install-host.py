#!/usr/bin/env python3
"""
Consume the netcup SCP API to automate the installation of Debian on a server.

netcup SCP API docs (see `netcup-scp-openapi.json`, last synced 2026-09-07 from
upstream version 2026.0902.083309 - re-fetch periodically, the endpoint below
is public/unauthenticated despite the header in this example):
```
curl 'https://www.servercontrolpanel.de/scp-core/api/v1/openapi' -H "Authorization: Bearer ${ACCESS_TOKEN}"
```
As of the 2026-09-07 sync, the diff from the prior stored spec (2025.1230.142745)
was purely additive and non-breaking for the endpoints this script uses: new
`GET /api/v1/servers/{serverId}/guest-agent/status`, new
`GET /api/v1/servers/{serverId}/gpu-driver`, a new `firewallPolicyId` filter on
`GET /api/v1/servers`, and a new `gpuDriverAvailable` field on `Server`.

Get API access / Authentication:

Run `%(prog)s login` - it automates the whole OAuth2 device-code dance below
and writes the resulting refresh token straight into .env. See _run_login()'s
own docstring for the manual curl-by-curl equivalent (useful if you ever
need to debug the flow itself, or the automated version breaks).

Once a refresh token exists (in .env or $NETCUP_SCP_API_REFRESH_TOKEN), this
script handles getting/refreshing short-lived access tokens itself for every
other mode - no further manual steps needed.
"""

import os
import sys
import json
import re
import shlex
import socket
import argparse
import subprocess
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Dict, Any, List, Set
from datetime import datetime
from pathlib import Path

import netcup_scp_client
# _load_env_file/_write_env_file have no call site left in this file's own
# code (load_env_file() below covers the real runtime need) -- they stay
# imported anyway because tests/test_scp_api_install_host.py calls them as
# install_host_mod._load_env_file(...)/._write_env_file(...) directly, the
# same re-export-for-test-access pattern already used for get_access_token/
# NetcupSCPClient/HTTPStatusError. Don't remove as "dead" without checking
# the test file first (caught live, 2026-09-09: removing them broke 5 tests).
from netcup_scp_client import (
    HTTPStatusError,
    NetcupSCPClient,
    _load_env_file,
    _load_settings,
    _redact_for_log,
    _write_env_file,
    get_access_token,
    load_env_file,
    run_device_code_login as _run_login,
)


def _normalize_ssh_public_key(public_key: str) -> str:
    """Normalize an OpenSSH public key for comparison.

    Keeps only the key type and base64 payload (drops comment).
    """
    parts = public_key.strip().split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1]}"
    return public_key.strip()


def _read_public_key_for_identity(identity_file: str) -> str:
    """Read/derive the public key for a private key identity file.

    Prefers an adjacent .pub file (preserves comment), otherwise falls back to
    `ssh-keygen -y -f` to derive the public key material.
    """
    identity_path = Path(identity_file).expanduser()
    if not identity_path.exists():
        raise FileNotFoundError(f"SSH identity file not found: {identity_path}")

    pub_path = identity_path.with_suffix(identity_path.suffix + ".pub")
    if pub_path.exists():
        return pub_path.read_text(encoding="utf-8").strip()

    result = subprocess.run(
        ["ssh-keygen", "-y", "-f", str(identity_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


_PLACEHOLDER_CONTROLLER_FQDN_SUFFIXES = (".invalid", ".example.invalid")

# Same service netcup-api-filter's own shell aliases use for this
# (`alias myip='curl -s ifconfig.me'`, .devcontainer/finalize.post.d/10-netcup-setup.sh) -
# reused here so "automatic" resolves the same way a human checking manually would.
_PUBLIC_IP_SERVICE_URL = "https://ifconfig.me/ip"


def _detect_public_ip(timeout: float = 5.0) -> Optional[str]:
    """Best-effort: this controller's own public/outbound IPv4.

    Returns None on any failure (no network, service down, unexpected body) -
    callers fall back to something else rather than raising.
    """
    try:
        req = urllib.request.Request(_PUBLIC_IP_SERVICE_URL, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ip = resp.read().decode("utf-8", errors="replace").strip()
        if ip.count(".") == 3 and all(part.isdigit() for part in ip.split(".")):
            return ip
    except Exception:
        pass
    return None


def _reverse_dns(ip: str) -> Optional[str]:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return None


def _resolve_controller_fqdn(controller_fqdn: str) -> str:
    """Resolve scp-api-install-host.toml's [ssh] controller_fqdn setting.

    The key this produces a comment for lives in *authorized_keys on the
    installed host* - so what's actually meaningful there is how that host
    sees us: the public IP we'll connect from, reverse-DNS'd if possible.
    Not this controller's own internal/container hostname, which means
    nothing to anyone looking at authorized_keys on a different machine.

    "automatic" therefore: (1) detects our public IP the same way a human
    would (`curl -s ifconfig.me`, see _detect_public_ip), (2) reverse-DNS's
    it; if that PTR lookup comes back empty (common - many ISPs/cloud
    providers don't set one), the bare IP is still a meaningful identifier on
    its own, so that's used instead; (3) only if we couldn't even determine
    our public IP (no network egress) does this fall back to
    socket.getfqdn() - purely local, so it always answers, but it's a
    same-machine-only name if that's not where the install is even reachable
    from, so treat it as a last resort, not the primary answer.
    """
    if controller_fqdn != "automatic":
        return controller_fqdn

    ip = _detect_public_ip()
    if ip:
        hostname = _reverse_dns(ip)
        if hostname:
            print(f"[ssh] controller_fqdn=automatic: {ip} reverse-DNS's to {hostname}")
            return hostname
        print(f"[ssh] controller_fqdn=automatic: no reverse DNS for {ip}; using the IP itself")
        return ip

    local = socket.getfqdn()
    print(
        f"[ssh] controller_fqdn=automatic: could not determine our public IP "
        f"(no network egress?); falling back to local hostname {local}"
    )
    return local


_UNKNOWN_HOST_LABEL = "unknown-host"
_IDENTITY_FILE_LABEL_RE = re.compile(r"[^A-Za-z0-9.\-]")


def _render_identity_file_path(template: str, server_name: Optional[str]) -> str:
    """Render {host}/{date} placeholders in an ssh.identity_file template.

    A literal path with no placeholders passes through unchanged (backward
    compatible with a CLI/env override that doesn't want per-host naming).
    `server_name` is sanitized to safe filename characters; falls back to
    _UNKNOWN_HOST_LABEL if unset, so a template can still render to a
    concrete (if less useful) path outside the interactive-gather flow.
    """
    host_label = _IDENTITY_FILE_LABEL_RE.sub("-", server_name) if server_name else _UNKNOWN_HOST_LABEL
    date_label = datetime.now().strftime("%Y%m%d")
    return template.replace("{host}", host_label).replace("{date}", date_label)


def _ensure_local_identity_file_exists(identity_file: str, controller_fqdn: str) -> None:
    """Generate the local SSH identity key if it doesn't exist yet.

    One keypair per (already-rendered, per-host/per-date) identity_file path
    - see _render_identity_file_path(), called before this - not a single
    shared file reused across every install (confirmed live 2026-09-08: that
    let SSH monitoring for one host silently pick up a key netcup had
    actually registered for a different one). The key's *comment* embeds a
    stable "vbpub-controller-ephemeral" marker (so debian-install-v2's own
    end-of-stage2 authorized_keys cleanup - and any human auditing a host's
    authorized_keys - can identify it unambiguously), the same host/date
    labels as the filename, and `controller_fqdn` (resolved via
    _resolve_controller_fqdn - "automatic" or an explicit value) so anyone
    looking at authorized_keys on a host this key touches can tell who/why
    put it there.
    """
    identity_path = Path(identity_file).expanduser()
    if identity_path.exists():
        return

    resolved_fqdn = _resolve_controller_fqdn(controller_fqdn)
    if not resolved_fqdn or any(
        resolved_fqdn.endswith(suffix) for suffix in _PLACEHOLDER_CONTROLLER_FQDN_SUFFIXES
    ):
        raise SystemExit(
            "ERROR: scp-api-install-host.toml's [ssh] controller_fqdn is still the "
            "placeholder - set it to this controller's own real hostname (or \"automatic\") "
            f"before a new identity key can be generated (would-be key: {identity_path})."
        )

    identity_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"[ssh] No local identity file at {identity_path}; generating one now.")
    subprocess.run(
        [
            "ssh-keygen", "-t", "ed25519", "-N", "",
            "-f", str(identity_path),
            "-C", f"vbpub-controller-ephemeral-{identity_path.name}@{resolved_fqdn}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    print(f"[ssh] Generated new identity: {identity_path}")


def _ensure_netcup_ssh_key_id_for_identity(
    client: "NetcupSCPClient",
    identity_file: str,
    *,
    dry_run: bool = False,
) -> int:
    """Return a netcup sshKeyId matching the given identity file.

    If no matching key exists in the account, create one (unless dry_run).
    """
    user_id = client.get_user_info()["id"]
    public_key = _read_public_key_for_identity(identity_file)
    normalized = _normalize_ssh_public_key(public_key)

    ssh_keys = client.get(f"/api/v1/users/{user_id}/ssh-keys") or []
    for key in ssh_keys:
        existing_key = key.get("key")
        if isinstance(existing_key, str) and _normalize_ssh_public_key(existing_key) == normalized:
            return int(key["id"])

    if dry_run:
        print(f"[dry-run] Would create a new netcup SSH key for identity file: {identity_file}")
        return -1

    # Create a new SSH key in netcup SCP.
    name_base = Path(identity_file).name
    created = client.post(
        f"/api/v1/users/{user_id}/ssh-keys",
        {
            "name": f"{name_base} (vbpub) {datetime.now().strftime('%Y%m%d-%H%M%S')}",
            "key": public_key,
        },
    )
    return int(created["id"])


def _strip_jsonc_comments(text: str) -> str:
    """Strip // and /* */ comments from JSON-with-comments (JSONC).

    This is a small, dependency-free helper so our payload files can include
    human-friendly comments while still parsing as JSON.
    """

    out: List[str] = []
    i = 0
    in_string = False
    string_quote = ""
    escape = False

    while i < len(text):
        ch = text[i]

        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == string_quote:
                in_string = False
                string_quote = ""
            i += 1
            continue

        # Not in string
        if ch in ("\"", "'"):
            in_string = True
            string_quote = ch
            out.append(ch)
            i += 1
            continue

        # Line comment
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            i += 2
            while i < len(text) and text[i] not in ("\n", "\r"):
                i += 1
            continue

        # Block comment
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = i + 2 if i + 1 < len(text) else len(text)
            continue

        out.append(ch)
        i += 1

    return "".join(out)

def _resolve_env_path() -> Path:
    """Which .env file is (or would be) in effect - same search order as
    load_env_file(), but returns a path even when none of the candidates
    exist yet: the canonical scripts/netcup/.env, so `login` has somewhere
    sensible to create it.
    """
    script_dir = Path(__file__).resolve().parent
    candidates = [
        Path.cwd() / ".env",
        script_dir / ".env",
        script_dir.parent.parent / ".env",
    ]
    return next((p for p in candidates if p.exists()), script_dir / ".env")


# Load .env file if it exists
load_env_file()


_SETTINGS_EXPECTED_KEYS = {
    "api.base_url",
    "api.keycloak_url",
    "ssh.identity_file",
    "ssh.user",
    "ssh.controller_fqdn",
    "ssh.poll_interval",
    "ssh.attach_initial_delay",
    "ssh.attach_max_wait_seconds",
    "ssh.stage2_wait_seconds",
}

SETTINGS_PATH = Path(__file__).resolve().parent / "scp-api-install-host.toml"
SETTINGS = _load_settings(SETTINGS_PATH, _SETTINGS_EXPECTED_KEYS)

# Configuration
BASE_URL = SETTINGS["api.base_url"]
KEYCLOAK_URL = SETTINGS["api.keycloak_url"]
# netcup_scp_client's own functions/NetcupSCPClient read these as module
# globals, not parameters (see its own module docstring) -- must be set
# before get_access_token()/_run_login()/NetcupSCPClient(...) are used.
netcup_scp_client.BASE_URL = BASE_URL
netcup_scp_client.KEYCLOAK_URL = KEYCLOAK_URL

# Server configuration
# Example server info from `/servers` API:
#  {
#    "id": 799611,
#    "name": "v2202511209318402047",
#    "disabled": false,
#    "hostname": "r1002.vxxu.de",
#    "nickname": "r1002",
#    "template": {
#      "id": 1357,
#      "name": "RS 1000 G12 Pro"
#    }
#  }

SERVER_NAME = os.environ.get("NETCUP_SCP_API_SERVER_NAME")    # v1001.vxxu.de
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Installation settings (hostname will be set dynamically from server info).
# customScript targets debian-install-v2's remote bootstrap entrypoint
# (scripts/debian-install-v2/bootstrap-remote.py), piped to `python3 -` (not
# `bash` - v2's CLI is a different interpreter/contract than v1's bootstrap.sh).
# `volkb79-2/vbpub` is v2's own documented default org (bootstrap-remote.py's
# REPO_URL_DEFAULT) - not a typo for v1's `volkb79`, a deliberate separate repo.
# AUTO_REBOOT_AFTER_STAGE1/NEVER_REBOOT are passed explicitly because v2 has no
# "auto" tristate (unlike v1) and rejects it outright - yes/no only. Every
# other v2 knob (SWAP_*/ZSWAP_*/DOCKER_*/RUN_* stage toggles) is deliberately
# left unset here so v2's own built-in defaults apply; those are exactly what
# gets tuned by iterating installs against a live host next.
INSTALLATION_CONFIG = {
    "locale": "en_US.UTF-8",
    "timezone": "Europe/Berlin",
    "customScript": (
        "curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/scripts/debian-install-v2/bootstrap-remote.py | "
        "AUTO_REBOOT_AFTER_STAGE1=yes NEVER_REBOOT=no "
        "TELEGRAM_BOT_TOKEN='{{TELEGRAM_BOT_TOKEN}}' TELEGRAM_CHAT_ID='{{TELEGRAM_CHAT_ID}}' "
        "CONTROLLER_SSH_PUBKEY='{{CONTROLLER_SSH_PUBKEY}}' "
        "python3 -"
    ),
    "rootPartitionFullDiskSize": False,
    "sshPasswordAuthentication": False,
    "emailToExecutingUser": True,
}

# Debug mode: NETCUP_SCP_API_DEBUG env var, OR'd with --debug in main().
# log_debug() (imported from netcup_scp_client) reads netcup_scp_client's
# OWN module-level DEBUG, not a local one here -- set that directly.
netcup_scp_client.DEBUG = os.environ.get("NETCUP_SCP_API_DEBUG", "no").lower() in ("yes", "true", "1")


def parse_args():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description="Netcup Server Control Panel - Automated Debian Installation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands (positional, optional; default is the install flow below):
  login               Automate the OAuth2 device-code login flow and write
                       the resulting refresh token to .env. No other flags
                       needed.
  configure           Interactive wizard: resolve the latest Debian UEFI
                       image flavour (and other account/image-level
                       defaults) for $NETCUP_SCP_API_SERVER_NAME and save
                       them as scripts/netcup/default-recipe.jsonc, used as
                       the base for every future interactive-gather install.
  build-customscript  Interactive wizard: build a ready-to-paste customScript
                       snippet (no Netcup API calls) for manual use in a
                       web-hoster's own management UI - covers
                       AUTO_REBOOT_AFTER_STAGE1/NEVER_REBOOT/TELEGRAM_BOT_TOKEN/
                       TELEGRAM_CHAT_ID/CONTROLLER_SSH_PUBKEY only.

Modes (pick one; default is interactive gather+install):
  (no mode flags)     Interactive: gather info for $NETCUP_SCP_API_SERVER_NAME, prompt, install.
  --payload FILE      Direct install from a JSON/JSONC payload file (no gathering).
  --attach-only       SSH-attach + tail bootstrap logs only; no Netcup API calls.
  --poweroff          Power off $NETCUP_SCP_API_SERVER_NAME and exit.

Examples:
  # First-time setup: log in, then build a default recipe:
  %(prog)s login
  %(prog)s configure

  # Preview everything (server lookup, image flavour, payload) without
  # calling any mutating API (install/poweroff/ssh-key-create):
  %(prog)s --payload=target-host.jsonc --dry-run

  # Interactive mode (gather information and prompt for confirmation):
  %(prog)s

  # Direct installation from payload file, then watch it to completion:
  %(prog)s --payload=target-host.jsonc --monitor

  # Non-interactive (e.g. driven by another script/agent) - auto-confirms:
  %(prog)s --payload=target-host.jsonc --yes --monitor

  # Reattach SSH log tailing to an already-installing/installed host:
  %(prog)s --attach-only --ssh-host=1.2.3.4

  # Power off the configured server:
  %(prog)s --poweroff

Settings (scp-api-install-host.toml, next to this script):
  Every operational default (SSH identity path/user, poll interval, attach/
  stage2 wait timeouts, API base URLs) lives there, not in this script - see
  that file's comments. CLI flags below override it per-run; nothing in this
  script falls back to a bare literal if a setting is missing.

Environment Variables (see .env.example):
  NETCUP_SCP_API_REFRESH_TOKEN     Required (not needed for --attach-only): OAuth2 refresh token.
  NETCUP_SCP_API_SERVER_NAME       Required for interactive mode and --poweroff; NOT required
                                   for --payload or --attach-only (no default - must be set).
  NETCUP_SCP_API_SSH_HOST          Default for --ssh-host.
  NETCUP_SCP_API_SSH_USER          Overrides scp-api-install-host.toml's ssh.user for --ssh-user.
  NETCUP_SCP_API_SSH_IDENTITY_FILE Overrides scp-api-install-host.toml's ssh.identity_file for --ssh-identity-file.
  TELEGRAM_BOT_TOKEN               Optional: forwarded into the bootstrap customScript.
  TELEGRAM_CHAT_ID                 Optional: forwarded into the bootstrap customScript.
  NETCUP_SCP_API_DEBUG             Enable verbose request/response logging (yes/true/1); same as --debug.

These can be set in a .env file in the current directory (see scripts/netcup/.env.example).
"""
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("login", "configure", "build-customscript"),
        default=None,
        help="Optional one-shot command; omit for the normal install flow (see Modes above).",
    )
    parser.add_argument(
        "--payload",
        metavar="FILE",
        help="Path to JSON payload file for direct installation (skips interactive gathering)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Resolve and validate everything (server lookup, image flavour, SSH keys, payload "
            "shape) and print what would be sent, but do not call any mutating Netcup API "
            "(install-image POST, poweroff PATCH, ssh-key create POST)."
        ),
    )

    parser.add_argument(
        "--attach-only",
        action="store_true",
        help=(
            "Do not call Netcup APIs. Only SSH-attach to a host and stream stage1/stage2 bootstrap logs, "
            "reconnecting across disconnects/reboots."
        ),
    )
    parser.add_argument(
        "--attach-task-uuid",
        default=None,
        help=(
            "Optional identifier used to name local capture files in --attach-only mode. "
            "Default: a timestamp-based id."
        ),
    )
    parser.add_argument(
        "--simulate-disconnect-seconds",
        type=float,
        default=None,
        help=(
            "Testing aid: in attach mode, intentionally terminate the SSH tail after N seconds to "
            "force the reconnect/reattach loop."
        ),
    )
    parser.add_argument(
        "--attach-initial-delay",
        type=float,
        default=SETTINGS["ssh.attach_initial_delay"],
        help="In --attach-only mode, wait N seconds before the first SSH probe (default: from scp-api-install-host.toml).",
    )
    parser.add_argument(
        "--attach-max-wait-seconds",
        type=float,
        default=SETTINGS["ssh.attach_max_wait_seconds"],
        help="In --attach-only mode, wait up to N seconds for SSH to become usable (default: from scp-api-install-host.toml).",
    )
    parser.add_argument(
        "--stage2-wait-seconds",
        type=float,
        default=SETTINGS["ssh.stage2_wait_seconds"],
        help=(
            "In --attach-only mode, also wait for /var/lib/vbpub/bootstrap/stage2_done "
            "(default: from scp-api-install-host.toml). Set to 0 to disable waiting."
        ),
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="Run non-interactively (auto-confirm prompts). Also enabled automatically when stdin is not a TTY."
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="After starting an installation, poll /api/v1/tasks/{uuid} until finished."
    )

    parser.add_argument(
        "--poweroff",
        action="store_true",
        help="Power off the server via API (state=OFF, stateOption=POWEROFF) and exit."
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=SETTINGS["ssh.poll_interval"],
        help="Polling interval in seconds for --monitor (default: from scp-api-install-host.toml)."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose request/response logging. Same as NETCUP_SCP_API_DEBUG=yes.",
    )

    parser.add_argument(
        "--attach-bootstrap",
        dest="attach_bootstrap",
        action="store_true",
        default=True,
        help="When monitoring, attempt to SSH in once cloud-init starts and tail bootstrap logs. (default: enabled)"
    )
    parser.add_argument(
        "--no-attach-bootstrap",
        dest="attach_bootstrap",
        action="store_false",
        help="Disable SSH attach/bootstrap log tailing while monitoring."
    )
    parser.add_argument(
        "--ssh-host",
        default=os.environ.get("NETCUP_SCP_API_SSH_HOST"),
        help="SSH host/IP to attach to (default: detected server IPv4; override via NETCUP_SCP_API_SSH_HOST)."
    )
    parser.add_argument(
        "--ssh-user",
        default=os.environ.get("NETCUP_SCP_API_SSH_USER", SETTINGS["ssh.user"]),
        help="SSH user for attaching to the freshly installed system (default: from scp-api-install-host.toml; override via NETCUP_SCP_API_SSH_USER)."
    )
    parser.add_argument(
        "--ssh-identity-file",
        default=os.environ.get("NETCUP_SCP_API_SSH_IDENTITY_FILE", SETTINGS["ssh.identity_file"]),
        help=(
            "Path to LOCAL SSH identity file used for attach/monitoring (default: from "
            "scp-api-install-host.toml; override via NETCUP_SCP_API_SSH_IDENTITY_FILE). This is "
            "the ephemeral controller bootstrap key, one per (host, date) - the default is a "
            "'{host}'/'{date}' TEMPLATE rendered automatically for a real install, but "
            "--attach-only requires an explicit, already-resolved path here (it will refuse "
            "an unrendered template rather than silently generate a key that can't match the "
            "host). Not the host-generated per-host production key from bootstrap stage2."
        )
    )
    return parser.parse_args()


def is_noninteractive(args: argparse.Namespace) -> bool:
    # Treat non-tty execution as non-interactive to prevent blocking in automation.
    if getattr(args, "yes", False):
        return True
    try:
        return not sys.stdin.isatty()
    except Exception:
        return True


def _fmt_ts(ts: Optional[str]) -> str:
    if not ts:
        return ""
    return ts


_RE_TELEGRAM_BOT_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")


def _redact_secrets(text: str) -> str:
    # Telegram bot tokens are secrets; avoid printing or persisting them.
    return _RE_TELEGRAM_BOT_TOKEN.sub("***REDACTED_TELEGRAM_BOT_TOKEN***", text)


def _extract_primary_ipv4(server_details: Dict[str, Any]) -> Optional[str]:
    ip_address = None
    if "ipv4Addresses" in server_details and server_details["ipv4Addresses"]:
        ip_address = server_details["ipv4Addresses"][0].get("ip")
    elif "serverLiveInfo" in server_details and "interfaces" in server_details["serverLiveInfo"]:
        interfaces = server_details["serverLiveInfo"]["interfaces"]
        if interfaces and "ipv4Addresses" in interfaces[0] and interfaces[0]["ipv4Addresses"]:
            ip_address = interfaces[0]["ipv4Addresses"][0]
    return ip_address


def _tcp_port_open(host: str, port: int = 22, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_stage2_done(
    *,
    host: str,
    user: str,
    identity_file: Optional[str],
    poll_interval: float,
    max_wait_seconds: float,
    monitor_log_path: Path,
) -> None:
    """Wait for vbpub stage2 completion on the newly installed host.

    Uses a marker file written by bootstrap stage2:
      /var/lib/vbpub/bootstrap/stage2_done

    Handles reboots/disconnects by retrying SSH.
    """

    start = time.monotonic()
    poll = max(1.0, float(poll_interval))

    def _emit(line: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        out = f"[{now}] [stage2-wait] {line}"
        print(out)
        try:
            with open(monitor_log_path, "a", encoding="utf-8") as mf:
                mf.write(out + "\n")
        except Exception:
            pass

    _emit(f"Waiting for stage2 completion marker on {user}@{host} (timeout {max_wait_seconds:.0f}s)")

    last_status = None
    while True:
        if (time.monotonic() - start) > max_wait_seconds:
            raise TimeoutError(f"Timed out waiting for stage2_done after {max_wait_seconds:.0f}s")

        if not _tcp_port_open(host, 22, timeout=2.0):
            time.sleep(poll)
            continue

        cmd = (
            "bash -lc 'set -euo pipefail; "
            "S=/var/lib/vbpub/bootstrap/stage2_done; "
            "if [ -f \"$S\" ]; then echo STAGE2_DONE; else echo STAGE2_NOT_DONE; fi; "
            "if command -v systemctl >/dev/null 2>&1; then "
            "  systemctl is-active vbpub-bootstrap-stage2.service 2>/dev/null || true; "
            "  systemctl show -p ActiveState -p SubState -p Result vbpub-bootstrap-stage2.service 2>/dev/null || true; "
            "fi'"
        )

        cmd_ssh = _build_ssh_cmd_base(host, user, identity_file) + [cmd]
        try:
            r = subprocess.run(cmd_ssh, text=True, capture_output=True, timeout=15)
        except Exception as e:
            status = f"ssh-error: {type(e).__name__}: {e}"
            if status != last_status:
                _emit(status)
                last_status = status
            time.sleep(poll)
            continue

        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        status = f"ssh-exit={r.returncode}"
        if err:
            status += f" err={err.splitlines()[-1]}"

        if out:
            # Keep the output small; it can be multi-line.
            summary = out.splitlines()[:6]
            status += " out=" + " | ".join(summary)

        if status != last_status:
            _emit(status)
            last_status = status

        if r.returncode == 0 and "STAGE2_DONE" in out:
            _emit("Stage2 completion marker present (stage2_done).")
            return

        time.sleep(poll)


_TASK_TERMINAL_STATES = {"FINISHED", "ERROR", "CANCELED", "ROLLBACK"}


def _parse_iso_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        # Handle both `...Z` and `...+00:00` formats.
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _find_active_task_for_server(client: "NetcupSCPClient", server_id: int) -> Optional[Dict[str, Any]]:
    """Best-effort: find the most recent non-terminal task for a server."""
    resp = client.get("/api/v1/tasks", params={"serverId": server_id, "limit": 50})

    # The API is documented as returning a list, but be defensive in case a HAL-ish
    # wrapper appears.
    tasks: Any = resp
    if isinstance(resp, dict):
        embedded = resp.get("_embedded")
        if isinstance(embedded, dict):
            for k in ("tasks", "taskInfoMinimal", "items"):
                if isinstance(embedded.get(k), list):
                    tasks = embedded.get(k)
                    break
        for k in ("tasks", "items", "content"):
            if isinstance(resp.get(k), list):
                tasks = resp.get(k)
                break

    if not isinstance(tasks, list):
        return None

    active: List[Dict[str, Any]] = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        state = t.get("state")
        if isinstance(state, str) and state in _TASK_TERMINAL_STATES:
            continue
        active.append(t)

    if not active:
        return None

    def score(task: Dict[str, Any]) -> tuple:
        name = (task.get("name") or "")
        msg = (task.get("message") or "")
        text = f"{name} {msg}".lower()
        prefers_image = 1 if ("image" in text or "install" in text) else 0
        started = _parse_iso_ts(task.get("startedAt")) or datetime.min
        return (prefers_image, started)

    return max(active, key=score)


def _build_ssh_cmd_base(host: str, user: str, identity_file: Optional[str] = None) -> List[str]:
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=5",
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=3",
        "-o", "LogLevel=ERROR",
    ]
    if identity_file:
        cmd += ["-o", "IdentitiesOnly=yes"]
        cmd += ["-i", identity_file]
    cmd.append(f"{user}@{host}")
    return cmd


def _server_short_name_from_details(server_details: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(server_details, dict):
        return None
    for key in ("nickname", "hostname", "name"):
        val = server_details.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().split(".")[0]
    return None


class _SSHBootstrapFollower:
    def __init__(
        self,
        task_uuid: str,
        host: str,
        user: str,
        identity_file: Optional[str],
        poll_interval: float,
        initial_delay: float = 10.0,
        max_wait_seconds: float = 300.0,
        simulate_disconnect_seconds: Optional[float] = None,
    ) -> None:
        self.task_uuid = task_uuid
        self.host = host
        self.user = user
        self.identity_file = identity_file
        self.poll_interval = max(1.0, float(poll_interval))
        self.initial_delay = max(0.0, float(initial_delay))
        self.max_wait_seconds = max(5.0, float(max_wait_seconds))
        self.simulate_disconnect_seconds = (
            None
            if simulate_disconnect_seconds is None
            else max(0.0, float(simulate_disconnect_seconds))
        )
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen[str]] = None
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.local_log_path = Path.cwd() / f"ssh-tail-{task_uuid}-{ts}.log"
        self.local_stage1_log_path = Path.cwd() / f"ssh-tail-stage1-{task_uuid}-{ts}.log"
        self.local_stage2_log_path = Path.cwd() / f"ssh-tail-stage2-{task_uuid}-{ts}.log"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        banner = f"SSH ATTACH: {self.user}@{self.host}"
        identity_hint = self.identity_file or "(missing identity file)"

        # Always create the local capture file immediately so users have
        # something to inspect even if SSH isn't reachable/auth fails.
        try:
            self.local_log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        def _log(line: str, lf: Optional[Any] = None) -> None:
            print(line)
            if lf is not None:
                try:
                    lf.write(line + "\n")
                    lf.flush()
                except Exception:
                    pass

        with (
            open(self.local_log_path, "a", encoding="utf-8") as lf,
            open(self.local_stage1_log_path, "a", encoding="utf-8") as lf_stage1,
            open(self.local_stage2_log_path, "a", encoding="utf-8") as lf_stage2,
        ):
            _log("=" * 70, lf)
            _log(banner, lf)
            _log("=" * 70, lf)
            _log(f"[attach] Local capture: {self.local_log_path}", lf)
            _log(f"[attach] Stage1 capture: {self.local_stage1_log_path}", lf)
            _log(f"[attach] Stage2 capture: {self.local_stage2_log_path}", lf)
            _log(f"[attach] Identity: {identity_hint}", lf)

            if self.initial_delay > 0:
                _log(f"[attach] Waiting {self.initial_delay:.0f}s before first SSH probe...", lf)
                time.sleep(self.initial_delay)

            def _wait_for_ssh(max_wait: float) -> bool:
                """Wait for SSH to become reachable and usable.

                Returns True if usable, False if timed out or stopped.
                """
                last_reason = None
                start = time.monotonic()
                while not self._stop.is_set():
                    if (time.monotonic() - start) > max_wait:
                        _log(f"[attach] Giving up after {max_wait:.0f}s without SSH becoming usable.", lf)
                        return False
                    if not _tcp_port_open(self.host, 22, timeout=2.0):
                        time.sleep(self.poll_interval)
                        continue

                    cmd_probe = _build_ssh_cmd_base(self.host, self.user, self.identity_file) + ["true"]
                    try:
                        r = subprocess.run(cmd_probe, text=True, capture_output=True, timeout=10)
                        if r.returncode == 0:
                            return True

                        reason = (r.stderr or "").strip().splitlines()[-1:]  # last line only
                        reason_txt = reason[0] if reason else f"ssh exit={r.returncode}"
                        if reason_txt != last_reason:
                            last_reason = reason_txt
                            _log(f"[attach] SSH not ready/auth failed: {reason_txt}", lf)
                            if not self.identity_file:
                                _log(
                                    "[attach] Hint: pass --ssh-identity-file or set NETCUP_SCP_API_SSH_IDENTITY_FILE if this is a key auth issue.",
                                    lf,
                                )
                    except Exception as e:
                        reason_txt = f"{type(e).__name__}: {e}"
                        if reason_txt != last_reason:
                            last_reason = reason_txt
                            _log(f"[attach] SSH probe error: {reason_txt}", lf)

                    time.sleep(self.poll_interval)
                return False

            # First attach: wait up to max_wait_seconds. After reboots/disconnects,
            # keep trying in shorter windows.
            first_attach = True
            while not self._stop.is_set():
                max_wait = self.max_wait_seconds if first_attach else max(60.0, self.max_wait_seconds)
                if not _wait_for_ssh(max_wait=max_wait):
                    return
                first_attach = False
                _log("[attach] SSH reachable; starting remote tail.", lf)

                # Tail both stage1 and stage2 logs in one SSH session.
                # - stage1: /root/custom_script.output (netcup customScript)
                # - stage2: systemd journal for vbpub-bootstrap-stage2.service (survives cloud-init being disabled)
                tail_remote = (
                    "bash -lc 'set -euo pipefail; "
                    "echo "
                    "  \"[attach] Streaming stage1+stage2 logs (one SSH session)\"; "
                    "prefix(){ tag=\"$1\"; while IFS= read -r line; do printf \"[%s] %s\\n\" \"$tag\" \"$line\"; done; }; "
                    "tail_stage1(){ "
                    "  P=/root/custom_script.output; "
                    "  if [ ! -f \"$P\" ]; then "
                    "    echo \"[attach] Waiting for log to appear: $P\"; "
                    "    for i in $(seq 1 300); do [ -f \"$P\" ] && break; sleep 1; done; "
                    "  fi; "
                    "  if [ -f \"$P\" ]; then "
                    "    echo \"[attach] Tailing: $P\"; "
                    "    tail -n 200 -F \"$P\" 2>&1 | prefix stage1; "
                    "  else "
                    "    echo \"[attach] No stage1 log at $P\" | prefix stage1; "
                    "  fi; "
                    "}; "
                    "tail_stage2(){ "
                    "  if ! command -v journalctl >/dev/null 2>&1; then echo \"[attach] journalctl not available\" | prefix stage2; return 0; fi; "
                    "  echo \"[attach] Tailing: journalctl -u vbpub-bootstrap-stage2.service\" | prefix stage2; "
                    "  for i in $(seq 1 600); do journalctl -u vbpub-bootstrap-stage2.service -n 1 --no-pager >/dev/null 2>&1 && break; sleep 1; done; "
                    "  journalctl -u vbpub-bootstrap-stage2.service -n 200 -f --no-pager 2>&1 | prefix stage2; "
                    "}; "
                    "tail_stage1 & tail_stage2 & wait'"
                )

                cmd_tail = _build_ssh_cmd_base(self.host, self.user, self.identity_file) + [tail_remote]
                try:
                    self._proc = subprocess.Popen(
                        cmd_tail,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                except Exception as e:
                    _log(f"[attach] Failed to start SSH tail: {e}", lf)
                    time.sleep(self.poll_interval)
                    continue

                secs_opt = self.simulate_disconnect_seconds
                if secs_opt is not None and secs_opt > 0:
                    proc = self._proc
                    secs = float(secs_opt)

                    def _simulate_disconnect() -> None:
                        time.sleep(secs)
                        if self._stop.is_set():
                            return
                        try:
                            if proc and proc.poll() is None:
                                _log(
                                    f"[attach] Simulating disconnect: terminating SSH tail after {secs:.1f}s",
                                    lf,
                                )
                                proc.terminate()
                        except Exception:
                            pass

                    threading.Thread(target=_simulate_disconnect, daemon=True).start()

                assert self._proc.stdout is not None
                try:
                    for line in self._proc.stdout:
                        if self._stop.is_set():
                            break
                        out_line = _redact_secrets(f"[remote] {line}")
                        sys.stdout.write(out_line)
                        sys.stdout.flush()
                        lf.write(out_line)
                        lf.flush()

                        # Also split into stage-specific files based on the prefixes added
                        # by the remote tail command (sed in tail_remote).
                        if "[stage1]" in out_line:
                            lf_stage1.write(out_line)
                            lf_stage1.flush()
                        elif "[stage2]" in out_line:
                            lf_stage2.write(out_line)
                            lf_stage2.flush()
                except Exception as e:
                    _log(f"[attach] Failed while capturing remote output: {e}", lf)

                try:
                    if self._proc and self._proc.poll() is None:
                        self._proc.terminate()
                except Exception:
                    pass

                if self._stop.is_set():
                    return

                # Most common reason: reboot / network hiccup. Loop and reattach.
                _log("[attach] Remote tail ended (likely reboot/disconnect); reattaching...", lf)
                time.sleep(self.poll_interval)


def monitor_task(
    client: "NetcupSCPClient",
    task_uuid: str,
    poll_interval: float = 5.0,
    *,
    ssh_host: Optional[str] = None,
    ssh_user: str = "root",
    ssh_identity_file: Optional[str] = None,
    attach_bootstrap: bool = True,
) -> Dict[str, Any]:
    """Poll task endpoint until it reaches a terminal state."""
    last_progress = None
    last_state = None
    last_step_states = {}

    follower: Optional[_SSHBootstrapFollower] = None
    attach_started = False

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    monitor_log_path = Path.cwd() / f"task-monitor-{task_uuid}-{ts}.log"

    print("=" * 70)
    print(f"MONITORING TASK {task_uuid}")
    print("=" * 70)
    print(f"Monitor capture: {monitor_log_path}")

    if attach_bootstrap and not ssh_identity_file:
        print(
            "[attach] NOTE: no --ssh-identity-file provided; attempting SSH attach using default ssh identities/agent. "
            "For deterministic behavior, pass --ssh-identity-file (or set NETCUP_SCP_API_SSH_IDENTITY_FILE)."
        )

    # Start attach early (about 10s after installation kickoff) and retry until SSH becomes usable.
    # This avoids relying on SCP step names / Cloudinit timing.
    if attach_bootstrap and not attach_started and ssh_host:
        attach_started = True
        follower = _SSHBootstrapFollower(
            task_uuid=task_uuid,
            host=ssh_host,
            user=ssh_user,
            identity_file=ssh_identity_file,
            poll_interval=poll_interval,
            initial_delay=10.0,
            max_wait_seconds=300.0,
        )
        follower.start()

    while True:
        task = client.get(f"/api/v1/tasks/{task_uuid}")
        state = task.get("state")
        name = task.get("name")
        msg = task.get("message")
        started_at = task.get("startedAt")
        finished_at = task.get("finishedAt")
        progress = None
        tp = task.get("taskProgress") or {}
        if isinstance(tp, dict):
            progress = tp.get("progressInPercent")

        # Steps (optional)
        steps = task.get("steps") or []
        step_lines = []
        cloudinit_seen = False
        if isinstance(steps, list):
            for s in steps:
                sname = s.get("name")
                sstate = s.get("state")
                suuid = s.get("uuid")
                if sname and sstate:
                    if "Cloudinit" in sname and sstate in ("RUNNING", "FINISHED"):
                        cloudinit_seen = True
                    prev = last_step_states.get(suuid)
                    if prev != sstate:
                        last_step_states[suuid] = sstate
                        step_lines.append(f"  - {sstate:12s} {sname}")

        # (Attach is started early outside the polling loop.)

        changed = False
        if state != last_state:
            changed = True
        if progress != last_progress:
            changed = True
        if step_lines:
            changed = True

        if changed:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ptxt = f"{progress:.0f}%" if isinstance(progress, (int, float)) else "?%"
            out_lines: List[str] = []
            out_lines.append(f"[{now}] {state} {ptxt}  {name or ''}".rstrip())
            if msg:
                out_lines.append(f"  message: {msg}")
            if started_at:
                out_lines.append(f"  started:  {_fmt_ts(started_at)}")
            if finished_at:
                out_lines.append(f"  finished: {_fmt_ts(finished_at)}")
            out_lines.extend(step_lines)

            for ln in out_lines:
                print(ln)

            try:
                with open(monitor_log_path, "a", encoding="utf-8") as mf:
                    for ln in out_lines:
                        mf.write(ln + "\n")
            except Exception:
                pass

            last_state = state
            last_progress = progress

        if state in ("FINISHED", "ERROR", "CANCELED", "ROLLBACK"):
            # If the SCP task finished successfully, stage2 may still be running after the reboot.
            # Keep SSH attach alive and explicitly wait for the stage2_done marker.
            if state == "FINISHED" and ssh_host and attach_bootstrap:
                try:
                    _wait_for_stage2_done(
                        host=ssh_host,
                        user=ssh_user,
                        identity_file=ssh_identity_file,
                        poll_interval=poll_interval,
                        max_wait_seconds=60 * 60,  # 60 minutes
                        monitor_log_path=monitor_log_path,
                    )
                    task["vbpub_stage2_done"] = True
                except Exception as e:
                    task["vbpub_stage2_done"] = False
                    task["vbpub_stage2_wait_error"] = str(e)

            if follower:
                follower.stop()
            # Print responseError if present
            resp_err = task.get("responseError")
            if resp_err:
                print("=" * 70)
                print("TASK RESPONSE ERROR")
                print(json.dumps(resp_err, indent=2))
            return task

        # Sleep
        try:
            time.sleep(max(0.5, float(poll_interval)))
        except KeyboardInterrupt:
            print("\nInterrupted; task continues server-side.")
            if follower:
                follower.stop()
            return task


def save_payload_with_comments(
    payload: Dict[str, Any],
    filepath: str,
    server_name: str,
    image_name: str,
    user_id: int,
    ssh_key_names: Optional[List[str]] = None,
    hostname_method: Optional[str] = None,
):
    """Save installation payload to JSONC file with helpful comments"""
    lines: List[str] = ["{"]
    items = list(payload.items())
    for idx, (key, value) in enumerate(items):
        # Add comments for IDs to make them more understandable
        if key == "serverId":
            lines.append(f'  // Server ID for: {server_name}')
        elif key == "hostname":
            if hostname_method:
                lines.append(f'  // Hostname determined by: {hostname_method}')
        elif key == "imageFlavourId":
            lines.append(f'  // Image: {image_name}')
        elif key == "sshKeyIds":
            lines.append(f'  // SSH key IDs for user {user_id}')
            if ssh_key_names:
                for key_name in ssh_key_names:
                    lines.append(f'  //   - {key_name}')

        # Format the actual key-value pair
        if isinstance(value, bool):
            value_str = str(value).lower()
        else:
            value_str = json.dumps(value)
        suffix = "," if idx < len(items) - 1 else ""
        lines.append(f'  "{key}": {value_str}{suffix}')

    lines.append("}")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


DEFAULT_RECIPE_PATH = Path(__file__).resolve().parent / "default-recipe.jsonc"


def save_recipe_with_comments(recipe: Dict[str, Any], filepath: str, image_name: str) -> None:
    """Save a `configure`-produced default recipe to JSONC, with comments.

    Same visual convention as save_payload_with_comments(), for a different
    (server-independent) field set: no serverId/hostname/sshKeyIds here -
    those stay per-run, resolved fresh each install.
    """
    lines: List[str] = [
        "// Default install recipe - generated by `%s configure`." % Path(__file__).name,
        "// Used as the base payload for interactive-gather installs when present;",
        "// delete this file (or re-run `configure`) to reset to built-in defaults.",
        "{",
    ]
    items = list(recipe.items())
    for idx, (key, value) in enumerate(items):
        if key == "imageFlavourId":
            lines.append(f"  // Image: {image_name}")
        if isinstance(value, bool):
            value_str = str(value).lower()
        else:
            value_str = json.dumps(value)
        suffix = "," if idx < len(items) - 1 else ""
        lines.append(f'  "{key}": {value_str}{suffix}')
    lines.append("}")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _load_default_recipe(recipe_path: Optional[Path] = None) -> Dict[str, Any]:
    """The base install config: a `configure`-produced recipe if one exists
    (see save_recipe_with_comments()), else the script's own built-in
    INSTALLATION_CONFIG defaults.

    recipe_path defaults to DEFAULT_RECIPE_PATH looked up at CALL time (not
    bound as a mutable default argument) so tests can monkeypatch the
    module-level constant and have callers like main() pick it up.
    """
    recipe_path = recipe_path or DEFAULT_RECIPE_PATH
    if recipe_path.is_file():
        return json.loads(_strip_jsonc_comments(recipe_path.read_text(encoding="utf-8")))
    return dict(INSTALLATION_CONFIG)


def _run_configure(client: "NetcupSCPClient") -> int:
    """Interactive wizard: resolve account/image-level defaults (currently
    just the latest Debian UEFI image flavour, queried live) and write them
    to DEFAULT_RECIPE_PATH for every future interactive-gather install to
    use as its base - see _load_default_recipe(). Per-run fields (serverId,
    hostname, sshKeyIds) are deliberately never part of this recipe.
    """
    if not SERVER_NAME:
        print(
            "ERROR: missing $NETCUP_SCP_API_SERVER_NAME (needed to query that "
            "server's available image flavours)",
            file=sys.stderr,
        )
        return 1

    print("=" * 70)
    print("CONFIGURE (write a default install recipe)")
    print("=" * 70)

    servers = client.get("/api/v1/servers", params={"name": SERVER_NAME})
    if not servers:
        print(f"ERROR: server '{SERVER_NAME}' not found", file=sys.stderr)
        return 1
    server_id = servers[0]["id"]

    try:
        flavour = _resolve_image_flavour(client, int(server_id), None, interactive=True)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    locale = _prompt_text("Locale", INSTALLATION_CONFIG["locale"])
    timezone = _prompt_text("Timezone", INSTALLATION_CONFIG["timezone"])
    root_full_disk = _prompt_yes_no(
        "Full-disk root partition (rootPartitionFullDiskSize)?",
        INSTALLATION_CONFIG["rootPartitionFullDiskSize"],
    )

    recipe = {
        "locale": locale,
        "timezone": timezone,
        "customScript": INSTALLATION_CONFIG["customScript"],
        "rootPartitionFullDiskSize": root_full_disk,
        "sshPasswordAuthentication": INSTALLATION_CONFIG["sshPasswordAuthentication"],
        "emailToExecutingUser": INSTALLATION_CONFIG["emailToExecutingUser"],
        "imageFlavourId": flavour["id"],
    }
    save_recipe_with_comments(recipe, str(DEFAULT_RECIPE_PATH), flavour["name"])
    print(f"✓ Wrote default recipe to {DEFAULT_RECIPE_PATH}")
    return 0


_CUSTOMSCRIPT_BASE_URL = (
    "https://raw.githubusercontent.com/volkb79-2/vbpub/main/scripts/debian-install-v2/bootstrap-remote.py"
)


def _build_customscript(
    *,
    auto_reboot_after_stage1: bool,
    never_reboot: bool,
    telegram_bot_token: str,
    telegram_chat_id: str,
    controller_pubkey: str,
) -> str:
    """Build a fully-expanded (no {{PLACEHOLDER}} tokens), one-line
    customScript shell command - the same shape as
    INSTALLATION_CONFIG["customScript"], but with every value already
    resolved to a literal. This script's own API-driven install path
    resolves {{PLACEHOLDER}} tokens itself (_expand_payload_placeholders);
    a human pasting this into a web-hoster's own reinstall dialog has no
    such resolution step available, so nothing here can be a placeholder.
    """
    # shlex.quote every operator-supplied value -- _prompt_text() does no
    # validation at all, and unlike the {{PLACEHOLDER}} path (which the
    # operator never directly types free-form shell text into), this
    # wizard's whole point is a value a human just typed going straight
    # into a real shell command that will actually execute as cloud-init
    # on a live host.
    env_parts = [
        f"AUTO_REBOOT_AFTER_STAGE1={'yes' if auto_reboot_after_stage1 else 'no'}",
        f"NEVER_REBOOT={'yes' if never_reboot else 'no'}",
    ]
    if telegram_bot_token:
        env_parts.append(f"TELEGRAM_BOT_TOKEN={shlex.quote(telegram_bot_token)}")
    if telegram_chat_id:
        env_parts.append(f"TELEGRAM_CHAT_ID={shlex.quote(telegram_chat_id)}")
    if controller_pubkey:
        env_parts.append(f"CONTROLLER_SSH_PUBKEY={shlex.quote(controller_pubkey)}")
    return f"curl -fsSL {_CUSTOMSCRIPT_BASE_URL} | " + " ".join(env_parts) + " python3 -"


def _run_build_customscript() -> int:
    """Interactive wizard: build a ready-to-paste customScript snippet for
    manual use in a web-hoster's own management UI (e.g. netcup SCP's
    server-reinstall dialog) - covers only the handful of settings
    INSTALLATION_CONFIG's own customScript template already parameterizes
    today (AUTO_REBOOT_AFTER_STAGE1, NEVER_REBOOT, TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID, CONTROLLER_SSH_PUBKEY) - not the full ~25 env vars
    bootstrap-remote.py documents. For anything else, append your own
    KEY=VALUE pairs (or VBPUB_CONFIG_EXTRA_JSON=...) to the printed snippet
    by hand before the trailing `python3 -`.
    """
    print("=" * 70)
    print("BUILD CUSTOMSCRIPT (for pasting into a web-hoster management UI)")
    print("=" * 70)

    auto_reboot = _prompt_yes_no("Auto-reboot after stage1?", True)
    never_reboot = _prompt_yes_no("Never reboot (overrides the reboot schedule entirely)?", False)
    telegram_bot_token = _prompt_text("Telegram bot token (blank to skip)", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id = _prompt_text("Telegram chat id (blank to skip)", os.environ.get("TELEGRAM_CHAT_ID", ""))

    controller_pubkey = ""
    if _prompt_yes_no("Include a controller SSH key (for early/reliable SSH monitoring access)?", True):
        # Adversarial-review finding, 2026-09-08: this command is explicitly
        # meant to be usable without $NETCUP_SCP_API_SERVER_NAME set (a
        # manual web-UI install may never touch this script's own .env at
        # all) -- silently falling back to SERVER_NAME (possibly None,
        # rendering to the generic "unknown-host" label) would collapse
        # every host built this way, on the same calendar day, onto the
        # SAME keypair -- exactly the failure mode the per-host redesign
        # exists to prevent, and it would happen with no visible warning.
        # Always ask, defaulting to SERVER_NAME only if it's already set.
        host_label = _prompt_text("Target hostname/server name (for a unique per-host key)", SERVER_NAME or "")
        if not host_label:
            print("ERROR: a target hostname is required to generate a per-host key", file=sys.stderr)
            return 1
        identity_template = os.environ.get("NETCUP_SCP_API_SSH_IDENTITY_FILE", SETTINGS["ssh.identity_file"])
        identity_file = _render_identity_file_path(identity_template, host_label)
        _ensure_local_identity_file_exists(identity_file, SETTINGS["ssh.controller_fqdn"])
        controller_pubkey = _read_public_key_for_identity(identity_file)
        print(f"   ✓ Using identity: {identity_file}")

    snippet = _build_customscript(
        auto_reboot_after_stage1=auto_reboot,
        never_reboot=never_reboot,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        controller_pubkey=controller_pubkey,
    )
    print()
    print("Paste this into the customScript field:")
    print("-" * 70)
    print(snippet)
    print("-" * 70)
    return 0


def _prompt_choice(items_desc: List[str], default_index: int, prompt_label: str) -> int:
    """Print a numbered list and prompt for a 1-based selection.

    Returns a 0-based index into items_desc. Enter (empty input) accepts
    default_index. Only call this when the caller has already confirmed the
    run is interactive.
    """
    for i, desc in enumerate(items_desc):
        marker = " (default)" if i == default_index else ""
        print(f"   [{i + 1}] {desc}{marker}")
    while True:
        raw = input(f"{prompt_label} [1-{len(items_desc)}, Enter for default]: ").strip()
        if not raw:
            return default_index
        try:
            choice = int(raw)
        except ValueError:
            print("   Please enter a number.")
            continue
        if 1 <= choice <= len(items_desc):
            return choice - 1
        print(f"   Please enter a number between 1 and {len(items_desc)}.")


def _prompt_text(label: str, default: str) -> str:
    """Prompt for a free-text value; Enter (empty input) accepts default."""
    raw = input(f"{label} [{default}]: ").strip()
    return raw or default


def _prompt_yes_no(label: str, default: bool) -> bool:
    """Prompt for a yes/no value; Enter (empty input) accepts default."""
    hint = "Y/n" if default else "y/N"
    raw = input(f"{label} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _resolve_image_flavour(
    client: "NetcupSCPClient",
    server_id: int,
    preselected: Optional[Dict[str, Any]],
    *,
    interactive: bool,
) -> Dict[str, Any]:
    """Resolve the image flavour to install: use `preselected` as-is if given
    (payload already had `imageFlavourId`), else query and pick.

    Filters to Debian+UEFI images (this tool's whole purpose). Interactive
    runs get a numbered prompt (default: newest); non-interactive runs keep
    auto-selecting newest, matching this tool's prior behavior.

    Returns {"id": int, "name": str}.
    """
    if preselected is not None:
        return preselected

    image_flavours = client.get(f"/api/v1/servers/{server_id}/imageflavours")
    debian_images = [
        img for img in image_flavours
        if "Debian" in img["image"]["name"] and "UEFI" in img["image"]["name"]
    ]
    if not debian_images:
        raise RuntimeError("No Debian UEFI image flavours available for this server")

    print("   Available Debian UEFI images:")
    for img in debian_images:
        print(f"   - ID: {img['id']:3d} | {img['image']['name']}")

    # Sort by version number (extract version from name like "Debian 13.2.0 UEFI amd64").
    debian_images_sorted = sorted(
        debian_images,
        key=lambda img: [int(x) if x.isdigit() else x for x in img["image"]["name"].split() if any(c.isdigit() for c in x)],
        reverse=True,
    )

    if interactive:
        descs = [f"ID {img['id']:3d} | {img['image']['name']}" for img in debian_images_sorted]
        idx = _prompt_choice(descs, default_index=0, prompt_label="   Select image")
        chosen = debian_images_sorted[idx]
    else:
        chosen = debian_images_sorted[0]
        print(f"   ✓ Auto-selected newest (non-interactive): {chosen['image']['name']}")

    print(f"   ✓ Using imageFlavourId {chosen['id']} ({chosen['image']['name']})")
    return {"id": chosen["id"], "name": chosen["image"]["name"]}


def _resolve_ssh_key_ids(
    client: "NetcupSCPClient",
    user_id: int,
    preselected_ids: Optional[List[int]],
    identity_file: Optional[str],
    *,
    interactive: bool,
    dry_run: bool = False,
) -> Optional[List[int]]:
    """Resolve sshKeyIds: use preselected_ids as-is if given (payload already
    had sshKeyIds), else query and pick/derive.

    With an identity file: ensure/derive its netcup key id and merge it with
    every existing key already on the account (so the pre-seeded default key
    stays authorized alongside the deterministic-attach key) - matches this
    tool's prior behavior exactly. Without one: interactive runs get a
    numbered prompt over the account's existing keys; non-interactive runs
    keep the first key, also matching prior behavior.
    """
    if preselected_ids is not None:
        return preselected_ids

    ssh_keys = client.get(f"/api/v1/users/{user_id}/ssh-keys") or []
    if ssh_keys:
        print("   Available SSH keys:")
        for key in ssh_keys:
            print(f"   - ID: {key['id']:3d} | {key.get('name', '')}")
    else:
        print("   ⚠ WARNING: No SSH keys found on this netcup account!")

    if identity_file:
        ssh_key_id = _ensure_netcup_ssh_key_id_for_identity(client, identity_file, dry_run=dry_run)
        if not ssh_keys:
            print(f"   ✓ Using sshKeyId matching identity file: {ssh_key_id}")
            return [ssh_key_id]

        ordered_ids: List[int] = []
        seen_ids: set = set()
        for key in ssh_keys:
            try:
                key_id = int(key["id"])
            except Exception:
                continue
            if key_id not in seen_ids:
                ordered_ids.append(key_id)
                seen_ids.add(key_id)
        if ssh_key_id not in seen_ids:
            ordered_ids.append(ssh_key_id)
            seen_ids.add(ssh_key_id)

        if len(ordered_ids) == 1:
            print(f"   ✓ Using sshKeyId matching identity file: {ordered_ids[0]}")
        else:
            print(f"   ✓ Using SSH Key IDs (existing + identity): {', '.join(str(x) for x in ordered_ids)}")
        return ordered_ids

    if not ssh_keys:
        return None

    if interactive:
        descs = [f"ID {k['id']:3d} | {k.get('name', '')}" for k in ssh_keys]
        idx = _prompt_choice(descs, default_index=0, prompt_label="   Select SSH key")
        chosen = ssh_keys[idx]
    else:
        chosen = ssh_keys[0]
        print(f"   ✓ Auto-selected first key (non-interactive): {chosen.get('name', '')}")

    print(f"   ✓ Using SSH Key ID: {chosen['id']}")
    return [int(chosen["id"])]


def _validate_installation_payload(payload: Dict[str, Any]) -> List[str]:
    """Local, offline structural checks for an install-image payload.

    This cannot catch invalid IDs (those only fail once netcup validates them),
    but it catches missing/mistyped fields before we ever call the API - the
    kind of mistake that otherwise only surfaces as an opaque 422 or 404.

    'imageFlavourId' and 'sshKeyIds' may be absent - install_from_payload()
    resolves those interactively (or auto-selects, non-interactively) when
    they're missing, rather than requiring them upfront.
    """
    errors: List[str] = []

    if "serverId" not in payload and not payload.get("hostname"):
        errors.append("must contain either 'serverId' (int) or a resolvable 'hostname'")
    if "serverId" in payload and not isinstance(payload["serverId"], int):
        errors.append(f"'serverId' must be an int, got {type(payload['serverId']).__name__}")

    if "imageFlavourId" in payload and not isinstance(payload["imageFlavourId"], int):
        errors.append(f"'imageFlavourId' must be an int, got {type(payload['imageFlavourId']).__name__}")

    if "diskName" not in payload:
        errors.append("missing required 'diskName' (e.g. 'vda')")
    elif not isinstance(payload["diskName"], str):
        errors.append(f"'diskName' must be a string, got {type(payload['diskName']).__name__}")

    ssh_key_ids = payload.get("sshKeyIds")
    if ssh_key_ids is not None and not (
        isinstance(ssh_key_ids, list) and all(isinstance(x, int) for x in ssh_key_ids)
    ):
        errors.append("'sshKeyIds' must be a list of ints (or omitted)")

    custom_script = payload.get("customScript")
    if custom_script is not None and not isinstance(custom_script, str):
        errors.append("'customScript' must be a string")

    return errors


def install_from_payload(client: NetcupSCPClient, payload_path: str, args: argparse.Namespace):
    """Install directly from a payload JSON file.

    The payload file may contain placeholders like {{TELEGRAM_BOT_TOKEN}} so it
    can be stored safely. Placeholders are expanded only for the API request.
    """
    print("=" * 70)
    print("DIRECT INSTALLATION MODE" + ("  [DRY RUN]" if getattr(args, "dry_run", False) else ""))
    print("=" * 70)
    print()

    # Load payload
    print(f"Loading payload from: {payload_path}")
    try:
        with open(payload_path, "r", encoding="utf-8") as f:
            raw_payload = f.read()
        installation_payload = json.loads(_strip_jsonc_comments(raw_payload))
    except FileNotFoundError:
        print(f"❌ ERROR: Payload file not found: {payload_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ ERROR: Invalid JSON in payload file: {e}", file=sys.stderr)
        sys.exit(1)

    print("✓ Payload loaded successfully")
    print()

    validation_errors = _validate_installation_payload(installation_payload)
    if validation_errors:
        print("❌ ERROR: payload failed preflight validation:", file=sys.stderr)
        for err in validation_errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    # Resolve server ID.
    server_id = None
    if "serverId" in installation_payload:
        server_id = installation_payload.get("serverId")
    else:
        hostname = installation_payload.get("hostname")
        if hostname:
            servers = client.get("/api/v1/servers", params={"name": hostname})
            if servers:
                server_id = servers[0].get("id")

    if not server_id:
        print("❌ ERROR: Payload must contain 'serverId' (or a resolvable 'hostname')", file=sys.stderr)
        sys.exit(1)

    # Best-effort fetch server details so we can attach via SSH during monitoring.
    server_details = None
    ip_address = None
    try:
        server_details = client.get(f"/api/v1/servers/{server_id}")
        ip_address = _extract_primary_ipv4(server_details)
    except Exception:
        pass

    # Fill in any fields the payload left out by querying the API and, when
    # interactive, letting the operator pick - rather than silently guessing.
    interactive = not is_noninteractive(args)
    if "imageFlavourId" not in installation_payload:
        print("imageFlavourId not set in payload:")
        flavour = _resolve_image_flavour(client, int(server_id), None, interactive=interactive)
        installation_payload["imageFlavourId"] = flavour["id"]
        print()
    if "sshKeyIds" not in installation_payload:
        print("sshKeyIds not set in payload:")
        user_id = client.get_user_info()["id"]
        installation_payload["sshKeyIds"] = _resolve_ssh_key_ids(
            client, user_id, None,
            getattr(args, "ssh_identity_file", None),
            interactive=interactive,
            dry_run=getattr(args, "dry_run", False),
        )
        print()

    # Expand placeholders only for the API request, while keeping the loaded
    # payload safe-to-print/save.
    installation_payload_to_send = _expand_payload_placeholders(installation_payload)

    # Display payload summary.
    print("=" * 70)
    print("PAYLOAD SUMMARY")
    print("=" * 70)
    print(json.dumps(_redact_for_log(installation_payload), indent=2))
    print()

    if getattr(args, "dry_run", False):
        print("=" * 70)
        print(f"[dry-run] Preflight OK. NOT calling POST /api/v1/servers/{server_id}/image.")
        print("=" * 70)
        return

    # Ask for confirmation (unless non-interactive)
    if not is_noninteractive(args):
        print("=" * 70)
        response = input("Do you want to start the installation now? (y/n): ")
        if response.lower() not in ("y", "yes"):
            print("Installation cancelled.")
            return
    else:
        print("=" * 70)
        print("Non-interactive mode: starting installation without prompt")

    # Start installation.
    print()
    print("Starting installation...")
    try:
        result = client.post(f"/api/v1/servers/{server_id}/image", installation_payload_to_send)

        # Avoid leaking secrets (some APIs may include passwords/tokens).
        print(json.dumps(_redact_for_log(result), indent=2))
        print()

        if "uuid" in result:
            task_uuid = result["uuid"]
            print("=" * 70)
            print("✓ Installation started successfully!")
            print("=" * 70)
            print(f"Task UUID: {task_uuid}")
            print()
            print("Monitor progress with:")
            print(f"  python3 scp-api-monitor-task.py {task_uuid}")
            if getattr(args, "monitor", False) or is_noninteractive(args):
                monitor_task(
                    client,
                    task_uuid,
                    poll_interval=args.poll_interval,
                    ssh_host=(getattr(args, "ssh_host", None) or ip_address),
                    ssh_user=args.ssh_user,
                    ssh_identity_file=getattr(args, "ssh_identity_file", None),
                    attach_bootstrap=getattr(args, "attach_bootstrap", True),
                )
    except HTTPStatusError as e:
        print(f"❌ HTTP Error: {e}", file=sys.stderr)
        if getattr(e, "body", ""):
            try:
                error_data = json.loads(e.body)
                print(f"Response: {json.dumps(_redact_for_log(error_data), indent=2)}", file=sys.stderr)
            except Exception:
                print(f"Response: {e.body[:2000]}", file=sys.stderr)
        sys.exit(1)


def _expand_payload_placeholders(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of payload with supported placeholders expanded.

    This is used to keep payloads safe-to-print/save (placeholders), while
    still ensuring the API request sends real values.
    """

    expanded: Dict[str, Any] = json.loads(json.dumps(payload))
    try:
        cs = expanded.get("customScript")
        if isinstance(cs, str) and "{{" in cs and "}}" in cs:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
            server_name = os.environ.get("NETCUP_SCP_API_SERVER_NAME", "")
            controller_pubkey = os.environ.get("CONTROLLER_SSH_PUBKEY", "")

            if "{{TELEGRAM_BOT_TOKEN}}" in cs and not token:
                print("⚠ WARNING: TELEGRAM_BOT_TOKEN not set; notifications will be disabled")
            if "{{TELEGRAM_CHAT_ID}}" in cs and not chat_id:
                print("⚠ WARNING: TELEGRAM_CHAT_ID not set; notifications will be disabled")
            if "{{CONTROLLER_SSH_PUBKEY}}" in cs and not controller_pubkey:
                print(
                    "⚠ WARNING: CONTROLLER_SSH_PUBKEY not set; debian-install-v2 will rely "
                    "solely on netcup's own sshKeyIds injection for SSH access"
                )

            cs = cs.replace("{{TELEGRAM_BOT_TOKEN}}", token)
            cs = cs.replace("{{TELEGRAM_CHAT_ID}}", chat_id)
            cs = cs.replace("{{SERVER_NAME}}", server_name)
            cs = cs.replace("{{CONTROLLER_SSH_PUBKEY}}", controller_pubkey)
            expanded["customScript"] = cs
    except Exception:
        pass

    return expanded


def _refuse_unrendered_attach_only_identity(identity_file: str) -> None:
    """Adversarial-review finding, 2026-09-08: without this check,
    --attach-only with no explicit --ssh-identity-file override would fall
    through to SETTINGS["ssh.identity_file"] (now a per-host TEMPLATE, not a
    static path) still containing literal "{host}"/"{date}" text, and
    _ensure_local_identity_file_exists() would silently generate a brand-new
    keypair at that nonsense path -- one that was never installed on any
    host and can never match. That's exactly the SSH-monitoring failure mode
    (key mismatch) this whole redesign exists to close. Refuse loudly
    instead: attach-only must be told exactly which already-generated key
    to use.
    """
    if "{host}" in identity_file or "{date}" in identity_file:
        raise SystemExit(
            f"ERROR: --attach-only needs an explicit --ssh-identity-file (or "
            f"NETCUP_SCP_API_SSH_IDENTITY_FILE) pointing at the exact key already "
            f"used for this host's install -- the configured default "
            f"({identity_file!r}) is a per-host/per-date TEMPLATE and "
            f"cannot be resolved without knowing which host and date it was "
            f"generated for."
        )


def _build_authenticated_client() -> "NetcupSCPClient":
    """Refresh-token check + access-token fetch + client construction.

    Factored out so both the early `configure` dispatch and the normal flow
    can build a client without duplicating this (previously the only copy
    of) error handling.
    """
    refresh_token = os.environ.get("NETCUP_SCP_API_REFRESH_TOKEN")
    if not refresh_token:
        print("ERROR: missing $NETCUP_SCP_API_REFRESH_TOKEN", file=sys.stderr)
        print("Usage: export NETCUP_SCP_API_REFRESH_TOKEN='your-refresh-token' && python3 <this script>")
        sys.exit(1)
    try:
        access_token = get_access_token(refresh_token)
    except Exception as e:
        print(f"❌ Failed to get access token:  {e}", file=sys.stderr)
        sys.exit(1)
    return NetcupSCPClient(access_token, refresh_token=refresh_token)


def _peek_payload_host_label(payload_path: str) -> Optional[str]:
    """Cheap, local, no-API-call peek at a --payload file's own "hostname"
    (or "serverId", as "netcup<id>") so the per-host SSH identity file gets
    a meaningful label before any network call happens - exactly the fields
    a `configure`/interactive-gather-produced payload already bakes in.
    Returns None (falls back to SERVER_NAME / _UNKNOWN_HOST_LABEL) if the
    file can't be read/parsed or has neither field.
    """
    try:
        with open(payload_path, "r", encoding="utf-8") as f:
            raw = f.read()
        payload = json.loads(_strip_jsonc_comments(raw))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        # Valid JSON but not an object (e.g. a bare array/string/number) -
        # not a real payload; let install_from_payload's own validation
        # produce the real error later instead of raising here.
        return None
    hostname = payload.get("hostname")
    if isinstance(hostname, str) and hostname:
        return hostname
    server_id = payload.get("serverId")
    if isinstance(server_id, int):
        return f"netcup{server_id}"
    return None


def main():
    global args
    args = parse_args()
    netcup_scp_client.DEBUG = netcup_scp_client.DEBUG or getattr(args, "debug", False)

    if getattr(args, "command", None) == "login":
        sys.exit(_run_login(_resolve_env_path()))

    if getattr(args, "command", None) == "build-customscript":
        # Purely local (identity-file generation only) - no Netcup API call,
        # no refresh token needed, unlike configure.
        sys.exit(_run_build_customscript())

    if getattr(args, "command", None) == "configure":
        # Doesn't touch SSH identity at all (just writes a recipe file) -
        # dispatch before the identity-rendering block below so it can't
        # generate a throwaway keypair as a side effect (confirmed live
        # 2026-09-08: running `configure` with $NETCUP_SCP_API_SERVER_NAME
        # unset produced a real "unknown-host"-labeled key for no reason).
        sys.exit(_run_configure(_build_authenticated_client()))

    attach_only = getattr(args, "attach_only", False)
    client: Optional["NetcupSCPClient"] = None
    server_lookup: Optional[List[Dict[str, Any]]] = None
    if getattr(args, "ssh_identity_file", None):
        if not attach_only:
            # A real hostname/server-id label, resolved WITHOUT an extra API
            # call where possible, instead of SERVER_NAME's opaque internal
            # netcup id (confirmed live 2026-09-08: e.g.
            # "v2202511209318406253" gave every generated key an unreadable
            # filename with no way to tell which host it belonged to at a
            # glance - see scp-api-install-host.toml's [ssh] comments).
            host_label = SERVER_NAME
            if args.payload:
                host_label = _peek_payload_host_label(args.payload) or SERVER_NAME
            elif SERVER_NAME:
                # One early, authenticated lookup - reused as step 1 below
                # (via `server_lookup`) so this isn't a second/duplicate
                # call. This lookup is for LABELING ONLY, so any failure
                # (transient HTTP error, malformed response) must not crash
                # main() here - leave server_lookup unset and fall back to
                # the raw SERVER_NAME label; step 1 below will retry the
                # same call for real, going through its own existing
                # HTTPStatusError/KeyError/Exception handling instead of
                # this early, unguarded copy (adversarial-review finding,
                # 2026-09-08).
                client = _build_authenticated_client()
                try:
                    server_lookup = client.get("/api/v1/servers", params={"name": SERVER_NAME})
                    if server_lookup:
                        host_label = server_lookup[0].get("hostname") or f"netcup{server_lookup[0]['id']}"
                except Exception:
                    server_lookup = None
            # Per-host/per-date identity, rendered now that a host label is
            # known - see _render_identity_file_path(). --attach-only
            # intentionally skips rendering: it means to reconnect with an
            # already-known, already-generated key (passed explicitly via
            # --ssh-identity-file / NETCUP_SCP_API_SSH_IDENTITY_FILE), not to
            # silently generate a fresh one that would never match anything
            # on the host it's attaching to.
            args.ssh_identity_file = _render_identity_file_path(args.ssh_identity_file, host_label)
        else:
            _refuse_unrendered_attach_only_identity(args.ssh_identity_file)
        _ensure_local_identity_file_exists(args.ssh_identity_file, SETTINGS["ssh.controller_fqdn"])
        if not attach_only:
            # Consumed by _expand_payload_placeholders() (mirrors the
            # TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID pattern) so debian-install-v2
            # can install this exact key into authorized_keys itself, rather
            # than depending solely on netcup's own account-level sshKeyIds
            # injection actually landing on the host.
            os.environ["CONTROLLER_SSH_PUBKEY"] = _read_public_key_for_identity(args.ssh_identity_file)

    if attach_only:
        if not getattr(args, "ssh_host", None):
            print("ERROR: --attach-only requires --ssh-host (or NETCUP_SCP_API_SSH_HOST)", file=sys.stderr)
            sys.exit(2)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        attach_task_uuid = getattr(args, "attach_task_uuid", None) or f"attach-only-{ts}"
        follower = _SSHBootstrapFollower(
            task_uuid=attach_task_uuid,
            host=getattr(args, "ssh_host"),
            user=args.ssh_user,
            identity_file=getattr(args, "ssh_identity_file", None),
            poll_interval=args.poll_interval,
            initial_delay=args.attach_initial_delay,
            max_wait_seconds=args.attach_max_wait_seconds,
            simulate_disconnect_seconds=getattr(args, "simulate_disconnect_seconds", None),
        )
        follower.start()
        try:
            wait_seconds = float(args.stage2_wait_seconds or 0.0)
            if wait_seconds > 0:
                _wait_for_stage2_done(
                    host=getattr(args, "ssh_host"),
                    user=args.ssh_user,
                    identity_file=getattr(args, "ssh_identity_file", None),
                    poll_interval=args.poll_interval,
                    max_wait_seconds=wait_seconds,
                    monitor_log_path=follower.local_log_path,
                )
            else:
                print("[attach-only] Streaming until Ctrl-C (stage2 wait disabled).")
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            print("\nInterrupted; stopping attach.")
        finally:
            follower.stop()
        return

    client = client or _build_authenticated_client()

    # If payload file is provided, use direct installation mode
    if args.payload:
        install_from_payload(client, args.payload, args)
        return

    # Only the interactive gather+install path (and --poweroff, below) need a
    # configured target server name; --attach-only and --payload do not.
    if not SERVER_NAME:
        print("ERROR: missing $SERVER_NAME (set it in scripts/netcup/.env)", file=sys.stderr)
        print("Hint: not required for --attach-only or --payload modes.", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print(f"Gathering installation information for server:   {SERVER_NAME}")
    print("=" * 70)
    print()

    try:
        # 1. Find server by name (reuse the identity-labeling lookup above,
        # if one already happened, instead of a second/duplicate GET)
        print(f"1. Finding server '{SERVER_NAME}'...")
        servers = server_lookup if server_lookup is not None else client.get(
            "/api/v1/servers", params={"name": SERVER_NAME}
        )

        if not servers:
            print(f"   ❌ ERROR: Server '{SERVER_NAME}' not found!")
            sys.exit(1)

        server_id = servers[0]["id"]
        print(f"   ✓ Server ID: {server_id}")
        print()

        if getattr(args, "poweroff", False):
            print("=" * 70)
            print("POWER OFF SERVER")
            print("=" * 70)
            if getattr(args, "dry_run", False):
                print(f"[dry-run] Would PATCH /api/v1/servers/{server_id} state=OFF stateOption=POWEROFF")
                return
            result = client.patch(
                f"/api/v1/servers/{server_id}",
                {"state": "OFF"},
                params={"stateOption": "POWEROFF"},
            )
            print(json.dumps(_redact_for_log(result), indent=2))
            return

        # 2. Get server details (for disk info and hostname)
        print("2. Getting server details...")
        server_details = client.get(f"/api/v1/servers/{server_id}")
        # Example response:
        # {
        #   "id": 804027,
        #   "name": "v2202511209318406253",
        #   "disabled": false,
        #   "hostname": "v1001.vxxu.de",
        #   "nickname": "v1001.vxxu.de",
        #   "template": {
        #     "id": 1538,
        #     "name": "VPS 1000 G12 Pro"
        #   },
        #   "architecture": "AMD64",
        #   "disksAvailableSpaceInMiB": 7864320,
        #   "firewallFeatureActive": true,
        #   "ipv4Addresses": [
        #     {
        #       "broadcast": "152.53.167.255",
        #       "gateway": "152.53.164.1",
        #       "id": 227115,
        #       "ip": "152.53.166.181",
        #       "netmask": "255.255.252.0"
        #     }
        #   ],
        #   "ipv6Addresses": [
        #     {
        #       "gateway": "fe80::1",
        #       "id": 990975,
        #       "networkPrefix": "2a0a:4cc0:2000:9798::",
        #       "networkPrefixLength": 64
        #     }
        #   ],
        #   "maxCpuCount": 4,
        #   "rescueSystemActive": false,
        #   "serverLiveInfo": {
        #     "autostart": true,
        #     "bootorder": [
        #       "HDD",
        #       "CDROM",
        #       "NETWORK"
        #     ],
        #     "cloudinitAttached": false,
        #     "configChanged": false,
        #     "coresPerSocket": 1,
        #     "cpuCount": 4,
        #     "cpuMaxCount": 4,
        #     "currentServerMemoryInMiB": 8192,
        #     "disks": [
        #       {
        #         "allocationInMiB": 1275,
        #         "capacityInMiB": 524288,
        #         "dev": "vda",
        #         "driver": "virtio"
        #       }
        #     ],
        #     "interfaces": [
        #       {
        #         "driver": "virtio",
        #         "ipv4Addresses": [
        #           "152.53.166.181"
        #         ],
        #         "ipv6LinkLocalAddresses": [
        #           "fe80::6820:60ff:fee8:068f"
        #         ],
        #         "ipv6NetworkPrefixes": [
        #           "2a0a:4cc0:2000:9798::/64"
        #         ],
        #         "mac": "6a:20:60:e8:06:8f",
        #         "mtu": 1500,
        #         "rxMonthlyInMiB": 1838,
        #         "speedInMBits": 2500,
        #         "trafficThrottled": false,
        #         "txMonthlyInMiB": 443,
        #         "vlanId": null,
        #         "vlanInterface": false
        #       }
        #     ],
        #     "keyboardLayout": "en-us",
        #     "latestQemu": true,
        #     "machineType": "pc-i440fx-9.2",
        #     "maxServerMemoryInMiB": 8192,
        #     "nestedGuest": false,
        #     "osOptimization": "LINUX",
        #     "requiredStorageOptimization": "NO",
        #     "sockets": 4,
        #     "state": "RUNNING",
        #     "template": "VPS 1000 G12 Pro",
        #     "uefi": true,
        #     "uptimeInSeconds": 235018
        #   },
        #   "site": {
        #     "city": "Manassas",
        #     "id": 6
        #   },
        #   "snapshotAllowed": true,
        #   "snapshotCount": 1
        # }        

        disk_dev = server_details["serverLiveInfo"]["disks"][0]["dev"]
        disk_capacity_gib = server_details["serverLiveInfo"]["disks"][0]["capacityInMiB"] / 1024
        
        # Get IP address and perform reverse DNS lookup
        # Try to get IPv4 address from either ipv4Addresses or interfaces
        ip_address = _extract_primary_ipv4(server_details)
        hostname_method = None
        
        if ip_address:
            try:
                hostname_result = socket.gethostbyaddr(ip_address)
                hostname = hostname_result[0]  # FQDN from reverse DNS
                hostname_method = f"reverse DNS lookup on {ip_address}"
                print(f"   ✓ IP Address: {ip_address}")
                print(f"   ✓ Hostname (reverse DNS): {hostname}")
            except (socket.herror, socket.gaierror) as e:
                # Fallback to configured hostname if reverse DNS fails
                hostname = server_details.get("hostname", SERVER_NAME)
                hostname_method = f"configured hostname (reverse DNS failed for {ip_address})"
                print(f"   ⚠ Reverse DNS failed for {ip_address}: {e}")
                print(f"   ✓ Using configured hostname: {hostname}")
        else:
            # No IP found, use configured hostname
            hostname = server_details.get("hostname", SERVER_NAME)
            hostname_method = "configured hostname (no IP address found)"
            print(f"   ⚠ No IP address found in server details")
            print(f"   ✓ Using configured hostname: {hostname}")
        
        print(f"   ✓ Primary Disk: {disk_dev} ({disk_capacity_gib:.0f} GiB)")
        print()

        # 3. Resolve image flavour (interactive: numbered prompt; else: newest)
        print("3. Resolving Debian UEFI image flavour...")
        interactive = not is_noninteractive(args)
        flavour = _resolve_image_flavour(client, int(server_id), None, interactive=interactive)
        image_flavour_id = flavour["id"]
        image_flavour_name = flavour["name"]
        print()

        # 4. Get user ID
        print("4. Getting user information...")
        user_info = client.get_user_info()
        user_id = user_info["id"]
        print(f"   ✓ User ID: {user_id}")
        print()

        # 5. Resolve SSH keys (identity-file key merged with existing keys;
        # interactive: numbered prompt over existing keys when no identity file)
        print("5. Resolving SSH keys...")
        ssh_key_ids = _resolve_ssh_key_ids(
            client, user_id, None,
            getattr(args, "ssh_identity_file", None),
            interactive=interactive,
            dry_run=getattr(args, "dry_run", False),
        )
        print()

        # 6. Prepare installation payload
        # Recipe fields go FIRST (defaults only) and the freshly, live-
        # resolved fields below win by coming last -- a recipe's own
        # imageFlavourId (if `configure` was ever run against a stale
        # image catalog) must never override the imageFlavourId this run
        # just resolved live two steps above.
        installation_payload = {
            **_load_default_recipe(),
            "serverId": server_id,  # Include for --payload mode
            "hostname": hostname,  # Use reverse DNS hostname from server info
            "imageFlavourId": image_flavour_id,
            "diskName": disk_dev,
            "sshKeyIds": ssh_key_ids,
        }

        # Expand placeholders only for the API request, while keeping the
        # payload safe-to-print/save.
        installation_payload_to_send = _expand_payload_placeholders(installation_payload)

        # 7. Display summary
        print("=" * 70)
        print("INSTALLATION PARAMETERS SUMMARY")
        print("=" * 70)
        print(json.dumps(_redact_for_log(installation_payload), indent=2))
        print()

        if getattr(args, "dry_run", False):
            print("=" * 70)
            print(
                "[dry-run] NOT saving to target-host.jsonc (would overwrite any existing "
                "file, and any not-yet-created SSH key above is only a placeholder id)."
            )
            print(f"[dry-run] Preflight OK. NOT calling POST /api/v1/servers/{server_id}/image.")
            print("=" * 70)
            return

        # 8. Save payload to file with comments
        save_payload_with_comments(
            installation_payload,
            "target-host.jsonc",
            SERVER_NAME,
            image_flavour_name,
            user_id,
            ssh_key_names=None,
            hostname_method=hostname_method
        )
        print("✓ Installation payload saved to:  target-host.jsonc")
        print()

        # 9. Ask for confirmation (unless non-interactive)
        if not is_noninteractive(args):
            print("=" * 70)
            response = input("Do you want to start the installation now? (y/n): ")
            if response.lower() not in ("y", "yes"):
                print("Installation cancelled.")
                return
        else:
            print("=" * 70)
            print("Non-interactive mode: starting installation without prompt")

        # 10. Start installation
        print()
        print("Starting installation...")
        try:
            result = client.post(f"/api/v1/servers/{server_id}/image", installation_payload_to_send)

            print(json.dumps(_redact_for_log(result), indent=2))
            print()

            if "uuid" in result:
                task_uuid = result["uuid"]
                print("=" * 70)
                print("✓ Installation started successfully!")
                print("=" * 70)
                print(f"Task UUID: {task_uuid}")
                print()
                print("Monitor progress with:")
                print(f"  python3 scp-api-monitor-task.py {task_uuid}")
                if getattr(args, "monitor", False) or is_noninteractive(args):
                    ssh_identity = getattr(args, "ssh_identity_file", None)
                    monitor_task(
                        client,
                        task_uuid,
                        poll_interval=args.poll_interval,
                        ssh_host=(getattr(args, "ssh_host", None) or ip_address),
                        ssh_user=args.ssh_user,
                        ssh_identity_file=ssh_identity,
                        attach_bootstrap=getattr(args, "attach_bootstrap", True),
                    )
        except HTTPStatusError as e:
            status = getattr(e, "status", None)
            if status == 409:
                try:
                    error_data = json.loads(e.body) if getattr(e, "body", "") else None
                except Exception:
                    error_data = None

                code = error_data.get("code") if isinstance(error_data, dict) else None
                if code == "server.lock.error":
                    active_task = _find_active_task_for_server(client, int(server_id))
                    if active_task and isinstance(active_task.get("uuid"), str):
                        task_uuid = active_task["uuid"]
                        print("⚠ Server is locked (installation already running).")
                        print(f"✓ Monitoring existing task instead: {task_uuid}")

                        if getattr(args, "monitor", False) or is_noninteractive(args):
                            ssh_identity = getattr(args, "ssh_identity_file", None)
                            monitor_task(
                                client,
                                task_uuid,
                                poll_interval=args.poll_interval,
                                ssh_host=(getattr(args, "ssh_host", None) or ip_address),
                                ssh_user=args.ssh_user,
                                ssh_identity_file=ssh_identity,
                                attach_bootstrap=getattr(args, "attach_bootstrap", True),
                            )
                            return

                        print("Monitor progress with:")
                        print(f"  python3 scp-api-monitor-task.py {task_uuid}")
                        return
            raise

    except HTTPStatusError as e:
        print(f"❌ HTTP Error: {e}", file=sys.stderr)
        if getattr(e, "body", ""):
            try:
                error_data = json.loads(e.body)
                print(f"Response: {json.dumps(_redact_for_log(error_data), indent=2)}", file=sys.stderr)
            except Exception:
                print(f"Response: {e.body[:2000]}", file=sys.stderr)
        sys.exit(1)
    except KeyError as e:
        print(f"❌ Missing expected field in API response: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}", file=sys.stderr)
        if netcup_scp_client.DEBUG:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
