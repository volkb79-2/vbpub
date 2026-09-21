#!/usr/bin/env python3
"""Inspect and safely operate the Netcup SCP account and its servers.

Start with ``./scp-api.py login`` to obtain the OAuth refresh token and, in an
interactive terminal, select ``v<digits>`` servers for this checkout's local
mutation denylist.  Then use ``status`` for a compact live inventory or
``servers`` and ``server-details`` when you need the raw account records.

The CLI separates read-only exploration from explicitly confirmed
modification.  Server-scoped reads enumerate every server when no
``server_id`` is supplied; mutating actions always require an explicit target.
The API explorer intentionally does not expose destructive disk formatting,
server image installation, or snapshot revert.  ``install-host.py`` owns the
Debian installation workflow and uses this module's shared client.

Exploration (read-only; does not change the account or servers):
  status [server_id]                         parallel live table, addresses, and SSH checks
  servers                                    account server inventory
  server-details server_id                   complete server API record
  imageflavours [server_id] [--filter TEXT]  reinstallable OS/image choices
  iso-bootable [server_id] [--filter TEXT]   provider bootable ISO choices
  iso-attached [server_id]                   currently attached ISO
  disks [server_id]                          disk capacity and drivers
  rescuesystem [server_id]                   provider rescue-system state
  snapshots [server_id]                     snapshot inventory
  tasks [task_uuid] [filters]                task inventory or one task
  metrics server_id metric                   CPU, disk, or network samples
  guest-agent-status server_id               QEMU guest-agent availability
  user-iso                                   account user-ISO inventory
  firewall-policies                          account firewall-policy inventory
  firewall server_id [mac] get               interface firewall assignment

Modification (changes provider state; confirmation is required unless
``--yes`` is supplied):
  attach-iso server_id                       attach provider or uploaded ISO
  iso-attached server_id detach              detach the current ISO
  rescuesystem server_id deactivate           deactivate provider rescue mode
  snapshots server_id create|dryrun           create/check a snapshot
  tasks task_uuid cancel                      cancel a task (use --server-id)
  user-iso upload FILE                       upload an account user ISO
  firewall-policies create|put                create or replace a policy
  firewall server_id [mac] set               replace interface assignments
  power on|off|cycle|reset server_id          control server power state

Authentication:
  login                                      device-code login and denylist wizard

Examples:
  ./scp-api.py status
  ./scp-api.py iso-bootable --filter debian
  ./scp-api.py attach-iso 799611 --iso-id 1234 --yes
  ./scp-api.py firewall 799611 get --consistency-check
  ./scp-api.py power cycle 799611 --yes

Every verb accepts ``--help``.  ``--json`` prints machine-readable output;
otherwise the CLI prints a sanitized table or summary.  Color auto-detects a
TTY and respects ``NO_COLOR`` and ``--no-color``.  The shared
``netcup_scp_client.py`` module handles token refresh, strict local settings,
and the protected-server policy for ``scp-api.py``, ``install-host.py``, and
``monitor-task.py``.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import math
import os
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import netcup_scp_client
from netcup_scp_client import (
    HTTPStatusError,
    NetcupSCPClient,
    _load_settings,
    get_access_token,
    load_env_file,
    run_device_code_login,
)

SETTINGS_PATH = Path(__file__).resolve().parent / "scp-api.toml"
INSTALL_HOST_SETTINGS_PATH = Path(__file__).resolve().parent / "install-host.toml"
DEFAULT_SSH_TIMEOUT_SECONDS = 2.0
STATUS_MAX_WORKERS = 4

# Keep this in sync with install-host.py's closed settings schema.  The
# explorer reads only the SSH values, but validating the complete file keeps a
# typo or stale key from silently changing the install workflow's meaning.
_INSTALL_HOST_SETTINGS_EXPECTED_KEYS = {
    "api.base_url",
    "api.keycloak_url",
    "bootstrap.raw_url_template",
    "bootstrap.repo_url",
    "bootstrap.repo_branch",
    "ssh.identity_file",
    "ssh.user",
    "ssh.controller_fqdn",
    "ssh.poll_interval",
    "ssh.attach_initial_delay",
    "ssh.attach_max_wait_seconds",
    "ssh.stage2_wait_seconds",
}


def _configure() -> None:
    """Load .env + settings and wire netcup_scp_client's module globals.

    Deliberately NOT run at import time (unlike the pre-existing pattern in
    install-host.py, which does this eagerly and consequently can't
    serve --help without a working settings file present -- confirmed live,
    2026-09-09, not fixed there since it's a separate, already-proven
    script). Called from main() after parse_args(), so --help short-
    circuits via argparse's own SystemExit before this ever runs.
    """
    load_env_file()
    # Validate the local safety policy before loading credentials or making
    # any authenticated request.  A typo must never turn protection off.
    try:
        netcup_scp_client.protected_server_policy()
    except ValueError as exc:
        raise SystemExit(f"ERROR: invalid protected-server denylist: {exc}") from exc
    settings = _load_settings(SETTINGS_PATH, {"api.base_url", "api.keycloak_url"})
    netcup_scp_client.BASE_URL = settings["api.base_url"]
    netcup_scp_client.KEYCLOAK_URL = settings["api.keycloak_url"]
    netcup_scp_client.DEBUG = os.environ.get("NETCUP_SCP_API_DEBUG", "no").lower() in ("yes", "true", "1")


# --- presentation layer (this file's own concern -- netcup_scp_client stays pure data) ---

def _color_enabled(no_color_flag: bool) -> bool:
    if no_color_flag or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


class _Palette:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)


# API responses are external input in principle, even against the
# operator's own account: strip C0 controls (incl. \n/\t) and DEL before
# ever printing a value in the pretty (non-JSON) path -- an unsanitized
# ESC sequence in e.g. a hostname would otherwise reach the terminal raw
# (screen-clear/color/cursor-move codes). Ordinary table cells also remove
# newlines so one hostile value cannot break row alignment. The status table's
# explicitly designated reverse-DNS column is the one intentional exception:
# its values are sanitized and rendered as separate physical lines.
# --json mode is unaffected either way: json.dumps already \u-escapes control
# bytes.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_MULTILINE_CONTROL_CHARS_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def _sanitize(text: str) -> str:
    return _CONTROL_CHARS_RE.sub("", text)


def _sanitize_multiline(text: str) -> str:
    return _MULTILINE_CONTROL_CHARS_RE.sub("", text)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        # One level of nesting flattened for table cells (e.g. imageflavour.image.name)
        return _sanitize(json.dumps(value, separators=(",", ":")))
    return _sanitize(str(value))


def _stringify_multiline(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        return _sanitize_multiline(json.dumps(value, separators=(",", ":")))
    return _sanitize_multiline(str(value))


def print_table(
    rows: List[Dict[str, Any]],
    columns: List[str],
    pal: _Palette,
    empty_message: str,
    multiline_columns: set[str] | None = None,
    column_headers: Dict[str, str] | None = None,
) -> None:
    rows = _response_rows(rows, "formatted table")
    if not rows:
        print(pal.dim(empty_message))
        return
    multiline_columns = multiline_columns or set()
    column_headers = column_headers or {}
    headers = [column_headers.get(column, column) for column in columns]
    cells = [
        [
            (_stringify_multiline(row.get(col)) if col in multiline_columns else _stringify(row.get(col))).splitlines() or [""]
            for col in columns
        ]
        for row in rows
    ]
    widths = [
        max(len(headers[i]), *(len(line) for row in cells for line in row[i]))
        for i in range(len(columns))
    ]
    header = "  ".join(pal.bold(headers[i].ljust(widths[i])) for i in range(len(columns)))
    print(header)
    print(pal.dim("  ".join("-" * widths[i] for i in range(len(columns)))))
    for r in cells:
        for line_number in range(max(len(cell) for cell in r)):
            print(
                "  ".join(
                    (r[i][line_number] if line_number < len(r[i]) else "").ljust(widths[i])
                    for i in range(len(columns))
                )
            )


def print_kv(data: Dict[str, Any], pal: _Palette, indent: int = 0) -> None:
    data = _response_dict(data, "formatted object")
    pad = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict) and value:
            print(f"{pad}{pal.bold(key)}:")
            print_kv(value, pal, indent + 1)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            print(f"{pad}{pal.bold(key)}: [{len(value)}]")
            for item in value:
                print_kv(item, pal, indent + 1)
                print()
        else:
            print(f"{pad}{pal.bold(key)}: {_stringify(value)}")


def emit(data: Any, as_json: bool, table_fn=None) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif table_fn is not None:
        table_fn(data)
    else:
        print(json.dumps(data, indent=2, sort_keys=True))


def confirm(prompt: str, yes: bool, pal: _Palette) -> bool:
    if yes:
        return True
    reply = input(f"{pal.yellow('?')} {prompt} [y/N] ")
    return reply.strip().lower() in ("y", "yes")


# --- client bootstrap -------------------------------------------------------

def build_client() -> NetcupSCPClient:
    refresh_token = os.environ.get("NETCUP_SCP_API_REFRESH_TOKEN")
    if not refresh_token:
        print("ERROR: missing $NETCUP_SCP_API_REFRESH_TOKEN -- run: scp-api.py login", file=sys.stderr)
        sys.exit(1)
    try:
        access_token = get_access_token(refresh_token)
    except Exception as e:
        print(f"ERROR: failed to get access token: {e}", file=sys.stderr)
        sys.exit(1)
    return NetcupSCPClient(access_token, refresh_token=refresh_token)


def _api_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except HTTPStatusError as e:
        print(f"ERROR: {e} (HTTP {e.status})", file=sys.stderr)
        if e.body:
            print(e.body[:2000], file=sys.stderr)
        sys.exit(1)


def _guard_server_mutation(client: NetcupSCPClient, server_id: int, operation: str) -> None:
    """Apply the local denylist immediately before a server mutation."""
    if not netcup_scp_client.protected_server_policy_configured():
        return
    endpoint = f"/api/v1/servers/{server_id}"
    details = _response_dict(_api_call(client.get, endpoint), f"GET {endpoint}")
    netcup_scp_client.assert_server_mutation_allowed(server_id, details.get("name"), operation)


def _server_targets(client: NetcupSCPClient, server_id: Any) -> List[Dict[str, Any]]:
    """Return one explicit target or all account servers for exploration."""
    if server_id is not None:
        return [{"id": server_id}]
    servers = _response_rows(_api_call(client.get, "/api/v1/servers"), "GET /api/v1/servers")
    for index, server in enumerate(servers):
        if not isinstance(server.get("id"), int) or isinstance(server.get("id"), bool) or server["id"] <= 0:
            raise ResponseShapeError(f"GET /api/v1/servers: item {index} has no valid positive integer id")
    return servers


def _server_name(server: Dict[str, Any]) -> str:
    return str(
        server.get("name")
        or server.get("nickname")
        or server.get("hostname")
        or server.get("id")
        or ""
    )


def _annotate_server_row(row: Dict[str, Any], server: Dict[str, Any]) -> Dict[str, Any]:
    """Add source-server context to an account-wide result row."""
    annotated = dict(row)
    annotated["serverId"] = server.get("id")
    annotated["serverName"] = _server_name(server)
    annotated["serverHostname"] = server.get("hostname")
    return annotated


def _address_value(value: Any, family: str) -> str | None:
    """Extract a printable address or prefix from one SCP address object."""
    if isinstance(value, str):
        return value or None
    if not isinstance(value, dict):
        return None
    if family == "ipv4":
        address = value.get("ip")
    else:
        address = value.get("ip") or value.get("networkPrefix")
        prefix_length = value.get("networkPrefixLength")
        if isinstance(address, str) and "/" not in address and isinstance(prefix_length, int) and not isinstance(prefix_length, bool):
            address = f"{address}/{prefix_length}"
    return address if isinstance(address, str) and address else None


def _server_address_inventory(server_info: Any) -> Dict[str, Any]:
    """Collect only the addresses declared by the server detail response.

    The SCP server detail (``Server``) has the authoritative
    ``ipv4Addresses``/``ipv6Addresses`` fields.  ``serverLiveInfo.interfaces``
    and ``GET /interfaces`` describe other views of the network, including
    link-local addresses and individual IPv6 rDNS map keys.  Those are useful
    API data, but mixing them into the compact status inventory makes one
    server appear to have duplicate or extra addresses.  Keep this helper
    intentionally narrow so every displayed address is traceable to the
    server detail response.
    """
    addresses = {"ipv4": [], "ipv6": []}
    rdns: Dict[str, str] = {}

    def add_rdns(address: str, value: Any) -> None:
        if isinstance(value, str) and value:
            rdns[address] = value
        elif isinstance(value, dict):
            for key, hostname in value.items():
                if isinstance(key, str) and isinstance(hostname, str) and hostname:
                    rdns[key] = hostname

    def add_address(family: str, value: Any) -> None:
        address = _address_value(value, family)
        if address and address not in addresses[family]:
            addresses[family].append(address)
        if isinstance(value, dict) and address:
            add_rdns(address, value.get("rdns"))

    if isinstance(server_info, dict):
        for family in ("ipv4", "ipv6"):
            field = f"{family}Addresses"
            values = server_info.get(field, [])
            if isinstance(values, list):
                for value in values:
                    add_address(family, value)
    return {"ipv4": addresses["ipv4"], "ipv6": addresses["ipv6"], "rdns": rdns}


def _reverse_dns_summary(inventory: Dict[str, Any]) -> str:
    """Format configured/API or resolver-derived reverse DNS entries."""
    entries: List[tuple[str, str | None, bool]] = []
    rdns = inventory.get("rdns", {})
    for family in ("ipv4", "ipv6"):
        for address in inventory.get(family, []):
            lookup_address = address.split("/", 1)[0]
            hostname = rdns.get(address) or rdns.get(lookup_address)
            # A network prefix is an address allocation, not a host address;
            # asking the local resolver about it can produce an unrelated PTR
            # result and is not useful in this server inventory.
            needs_lookup = hostname is None and "/" not in address
            if needs_lookup:
                try:
                    ipaddress.ip_address(lookup_address)
                except ValueError:
                    needs_lookup = False
                else:
                    pass
            entries.append((address, hostname, needs_lookup))

    lookup_addresses = [address.split("/", 1)[0] for address, _, needs_lookup in entries if needs_lookup]
    if lookup_addresses:
        # Submit every lookup, including duplicate addresses. The operator
        # explicitly wants each displayed address to remain an independent
        # live lookup rather than a hidden cross-row cache.
        with ThreadPoolExecutor(max_workers=min(STATUS_MAX_WORKERS, len(lookup_addresses))) as executor:
            lookup_results = [
                future.result()
                for future in [
                    executor.submit(netcup_scp_client.reverse_dns, address)
                    for address in lookup_addresses
                ]
            ]
    else:
        lookup_results = []

    resolved_index = 0
    formatted_entries: List[str] = []
    seen = set()
    for address, hostname, needs_lookup in entries:
        if needs_lookup:
            hostname = lookup_results[resolved_index]
            resolved_index += 1
        entry = f"{address} -> {hostname or '-'}"
        if entry not in seen:
            formatted_entries.append(entry)
            seen.add(entry)
    return "\n".join(formatted_entries)


def _ssh_probe_hosts(server_info: Dict[str, Any]) -> List[str]:
    """Return exact server-info addresses suitable as SSH destinations.

    ``ipv6Addresses`` normally contains an allocated network prefix.  A
    prefix is deliberately not passed to SSH; only an exact ``ip`` value can
    identify a host.  IPv4 addresses are already host addresses.
    """
    hosts: List[str] = []
    if not isinstance(server_info, dict):
        return hosts

    ipv4_values = server_info.get("ipv4Addresses", [])
    if isinstance(ipv4_values, list):
        for value in ipv4_values:
            address = _address_value(value, "ipv4")
            if not address:
                continue
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                continue
            if isinstance(parsed, ipaddress.IPv4Address) and address not in hosts:
                hosts.append(address)

    ipv6_values = server_info.get("ipv6Addresses", [])
    if isinstance(ipv6_values, list):
        for value in ipv6_values:
            # A ServerIpv6.networkPrefix is an allocation, not an SSH host.
            # Only an explicit ServerIpv6.ip (or an exact string response)
            # identifies a destination.
            if isinstance(value, dict):
                address = value.get("ip")
            else:
                address = value
            if not isinstance(address, str) or not address or "/" in address:
                continue
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                continue
            if isinstance(parsed, ipaddress.IPv6Address) and address not in hosts:
                hosts.append(address)
    return hosts


def _ssh_identity_path(template: str, server: Dict[str, Any]) -> Path:
    """Render the install-host identity template without generating a key."""
    host_label = _IDENTITY_FILE_LABEL_RE.sub("-", _server_name(server)) or "unknown-host"
    date_label = datetime.now(timezone.utc).strftime("%Y%m%d")
    rendered = template.replace("{host}", host_label).replace("{date}", date_label)
    return Path(rendered).expanduser()


_IDENTITY_FILE_LABEL_RE = re.compile(r"[^A-Za-z0-9.\-]")
_SSH_PRIVATE_KEY_MARKERS = (
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN DSA PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----",
)
_SSH_AUTH_FAILURE_MARKERS = (
    "permission denied",
    "authentication failed",
    "publickey",
    "sign_and_send_pubkey",
    "no mutual signature algorithm",
    "too many authentication failures",
)
_SSH_REJECTION_MARKERS = (
    "administratively prohibited",
    "channel open failed",
    "connection closed by",
    "kex_exchange_identification",
    "not allowed",
    "prohibited",
)
_SSH_TRANSPORT_FAILURE_MARKERS = (
    "connection refused",
    "connection timed out",
    "operation timed out",
    "no route to host",
    "network is unreachable",
    "could not resolve hostname",
    "temporary failure in name resolution",
    "connection reset by peer",
    "banner exchange",
)


def _load_ssh_probe_settings() -> tuple[str, str]:
    """Return the configured SSH user and rendered identity template."""
    settings = _load_settings(INSTALL_HOST_SETTINGS_PATH, _INSTALL_HOST_SETTINGS_EXPECTED_KEYS)
    user = os.environ.get("NETCUP_SCP_API_SSH_USER", "").strip() or str(settings["ssh.user"])
    template = os.environ.get("NETCUP_SCP_API_SSH_IDENTITY_FILE", "").strip() or str(
        settings["ssh.identity_file"]
    )
    if not user:
        raise ValueError("install-host.toml [ssh].user is empty")
    if not template:
        raise ValueError("install-host.toml [ssh].identity_file is empty")
    return user, template


def _looks_like_private_key(path: Path) -> bool:
    """Recognize private-key files without invoking ssh-keygen or prompting."""
    try:
        prefix = path.read_text(encoding="utf-8", errors="ignore")[:512]
    except OSError:
        return False
    return any(marker in prefix for marker in _SSH_PRIVATE_KEY_MARKERS)


def _local_private_key_candidates() -> List[Path]:
    """Find recognizable private keys in ~/.ssh once per status invocation."""
    candidates: List[Path] = []
    ssh_dir = Path.home() / ".ssh"
    if ssh_dir.is_dir():
        try:
            entries = sorted(ssh_dir.iterdir(), key=lambda path: path.name)
        except OSError:
            entries = []
        for path in entries:
            if path.is_file() and _looks_like_private_key(path):
                candidates.append(path)
    return candidates


def _ssh_key_matches_server(path: Path, server: Dict[str, Any]) -> bool:
    """Return whether a key filename plausibly names this server."""
    filename = path.name.casefold()
    normalized_filename = re.sub(r"[^a-z0-9]", "", filename)
    labels = {
        str(server.get(field, "")).strip()
        for field in ("name", "hostname", "nickname")
        if isinstance(server.get(field), str) and server.get(field, "").strip()
    }
    hostname = server.get("hostname")
    if isinstance(hostname, str):
        # The first label is the host-specific part. Do not add shared domain
        # components such as `vxxu` or `de`: they would make unrelated keys
        # look server-specific and defeat the priority ordering.
        host_label = hostname.split(".", 1)[0].strip()
        if host_label:
            labels.add(host_label)
    labels.add(_server_name(server))
    for label in labels:
        folded = label.casefold()
        normalized = re.sub(r"[^a-z0-9]", "", folded)
        # Avoid treating a one-character hostname as a meaningful filename
        # match. Netcup names/hostnames are normally much longer, but this
        # guard keeps malformed API data from making every key "preferred".
        if len(normalized) >= 4 and (folded in filename or normalized in normalized_filename):
            return True
    return False


def _ssh_key_candidates(
    server: Dict[str, Any],
    template: str,
    local_keys: List[Path] | None = None,
) -> List[Path]:
    """Find and prioritize private keys for one server.

    Matching server-name/hostname/nickname filenames are tested first. The
    configured identity is next if it was not already in that group; all
    remaining local keys follow. Every key is retained because the status
    output must report all keys that authenticate.
    """
    configured = _ssh_identity_path(template, server)
    candidates = list(local_keys if local_keys is not None else _local_private_key_candidates())
    if configured.is_file() and configured not in candidates:
        candidates.append(configured)

    def priority(path: Path) -> tuple[int, int, str]:
        matches_server = _ssh_key_matches_server(path, server)
        is_configured = path == configured
        if matches_server:
            return (0, 0 if is_configured else 1, path.name.casefold())
        if is_configured:
            return (1, 0, path.name.casefold())
        return (2, 0, path.name.casefold())

    return sorted(candidates, key=priority)


def _ssh_key_label(path: Path) -> str:
    """Use a concise, non-secret name for a status-table key result."""
    return path.name


def _classify_ssh_probe(returncode: int, stderr: str) -> str:
    if returncode == 0:
        return "success"
    message = stderr.casefold()
    if any(marker in message for marker in _SSH_TRANSPORT_FAILURE_MARKERS):
        return "transport"
    if any(marker in message for marker in _SSH_REJECTION_MARKERS):
        return "rejected"
    if any(marker in message for marker in _SSH_AUTH_FAILURE_MARKERS):
        return "auth"
    # An SSH process that reached an unknown non-transport error is safer to
    # classify as reachable-but-not-authenticated than to claim that the
    # service is offline. The explicit transport markers above are the offline
    # verdict.
    return "auth"


def _probe_ssh_service(
    host: str,
    user: str,
    timeout_seconds: float = DEFAULT_SSH_TIMEOUT_SECONDS,
) -> str:
    """Check whether an SSH service responds before trying any key."""
    ssh_host = f"[{host}]" if ":" in host else host
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout_seconds:g}",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "PubkeyAuthentication=no",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "PreferredAuthentications=none",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        f"{user}@{ssh_host}",
        "true",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "transport"
    except FileNotFoundError:
        return "ssh-unavailable"
    except OSError:
        return "transport"
    classification = _classify_ssh_probe(result.returncode, result.stderr or "")
    return "reachable" if classification in ("success", "auth") else classification


def _probe_ssh_key(
    host: str,
    user: str,
    key_path: Path,
    timeout_seconds: float = DEFAULT_SSH_TIMEOUT_SECONDS,
) -> str:
    ssh_host = f"[{host}]" if ":" in host else host
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout_seconds:g}",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-i",
        str(key_path),
        f"{user}@{ssh_host}",
        "true",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "transport"
    except FileNotFoundError:
        return "ssh-unavailable"
    except OSError:
        return "transport"
    return _classify_ssh_probe(result.returncode, result.stderr or "")


def _prepare_ssh_probe_context() -> Dict[str, Any]:
    """Load SSH settings and scan local keys once for a status run."""
    try:
        user, template = _load_ssh_probe_settings()
    except (OSError, ValueError, SystemExit):
        return {"error": True}
    return {
        "user": user,
        "template": template,
        "local_keys": _local_private_key_candidates(),
    }


def _ssh_connection_summary(
    server: Dict[str, Any],
    details: Dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_SSH_TIMEOUT_SECONDS,
    context: Dict[str, Any] | None = None,
) -> str:
    """Probe configured/local keys and summarize SSH reachability.

    A single service probe finds the first reachable address before any key is
    attempted; additional addresses are tried only when the previous one does
    not respond. Every candidate key is then tested on that address. A success
    lists the key names; an open SSH service with no successful key is distinct
    from a transport failure. This is intentionally a status probe, never a
    key-generation path.
    """
    hosts = _ssh_probe_hosts(details)
    if not hosts:
        return "no server IP"
    context = context or _prepare_ssh_probe_context()
    if context.get("error"):
        return "SSH config unavailable"
    user = context["user"]
    template = context["template"]
    identity_server = dict(server)
    identity_server.update(details)
    keys = _ssh_key_candidates(identity_server, template, context.get("local_keys", []))
    if not keys:
        return "no keys found"

    reachable_host = None
    saw_rejection = False
    for host in hosts:
        result = _probe_ssh_service(host, user, timeout_seconds)
        if result == "ssh-unavailable":
            return "SSH client unavailable"
        if result == "reachable":
            reachable_host = host
            break
        if result == "rejected":
            saw_rejection = True
    if reachable_host is None:
        return "rejected" if saw_rejection else "no answer"

    successful: List[str] = []
    saw_reachable_key_attempt = False
    saw_rejection = False
    for key in keys:
        result = _probe_ssh_key(reachable_host, user, key, timeout_seconds)
        if result == "ssh-unavailable":
            return "SSH client unavailable"
        if result == "success":
            saw_reachable_key_attempt = True
            successful.append(_ssh_key_label(key))
        elif result == "rejected":
            saw_rejection = True
        elif result == "auth":
            saw_reachable_key_attempt = True
    if successful:
        return "keys: " + ", ".join(successful)
    if saw_rejection:
        return "rejected"
    if saw_reachable_key_attempt:
        return "no keys match"
    return "no answer"


def _mib_to_gib(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "?"
    return f"{value / 1024:.1f}"


def _server_status_row(
    server: Dict[str, Any],
    details: Dict[str, Any],
    *,
    include_ssh: bool = False,
    ssh_timeout: float = DEFAULT_SSH_TIMEOUT_SECONDS,
    ssh_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build the stable, human-oriented row used by ``status`` and login."""
    record = dict(server)
    record.update(details)
    live_info = record.get("serverLiveInfo")
    if not isinstance(live_info, dict):
        live_info = {}
    inventory = _server_address_inventory(details)

    cpu = live_info.get("cpuCount", live_info.get("cpuMaxCount", record.get("maxCpuCount")))
    memory = live_info.get("currentServerMemoryInMiB", live_info.get("maxServerMemoryInMiB"))
    disks = live_info.get("disks", record.get("disks", []))
    disk_mib = None
    if isinstance(disks, list):
        capacities = [
            disk.get("capacityInMiB")
            for disk in disks
            if isinstance(disk, dict) and isinstance(disk.get("capacityInMiB"), (int, float))
            and not isinstance(disk.get("capacityInMiB"), bool)
        ]
        if capacities:
            disk_mib = sum(capacities)

    return {
        "vname": _server_name(record) or "?",
        "hostname": record.get("hostname") or record.get("nickname") or "?",
        "reverse DNS": _reverse_dns_summary(inventory) or "-",
        "state": live_info.get("state", record.get("state")) or "?",
        "architecture": record.get("architecture") or "?",
        "#CPU": cpu if cpu is not None else "?",
        "RAM GB": _mib_to_gib(memory),
        "disk GB": _mib_to_gib(disk_mib),
        "ssh-connect": (
            _ssh_connection_summary(
                server,
                details,
                timeout_seconds=ssh_timeout,
                context=ssh_context,
            )
            if include_ssh
            else "-"
        ),
    }


def _server_enrichment(client: NetcupSCPClient, server_id: int) -> Dict[str, Any]:
    """Fetch the server detail used by status output."""
    details_endpoint = f"/api/v1/servers/{server_id}"
    return _response_dict(_api_call(client.get, details_endpoint), f"GET {details_endpoint}")


def _status_row_for_server(
    client: NetcupSCPClient,
    server: Dict[str, Any],
    ssh_timeout: float,
    ssh_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Fetch and format one server; callers preserve the target order."""
    details = _server_enrichment(client, server["id"])
    return _server_status_row(
        server,
        details,
        include_ssh=True,
        ssh_timeout=ssh_timeout,
        ssh_context=ssh_context,
    )


def cmd_status(client: NetcupSCPClient, args, pal: _Palette) -> None:
    """Print a compact live status table for one or all account servers."""
    targets = _server_targets(client, args.server_id)
    rows: List[Dict[str, Any]] = []
    if targets:
        ssh_timeout = getattr(args, "ssh_timeout", DEFAULT_SSH_TIMEOUT_SECONDS)
        ssh_context = _prepare_ssh_probe_context()
        worker_count = min(STATUS_MAX_WORKERS, len(targets))
        if worker_count == 1:
            rows = [_status_row_for_server(client, targets[0], ssh_timeout, ssh_context)]
        else:
            # The API client uses immutable request state for reads. Keep the
            # pool bounded for provider rate limits, but retain input order in
            # the rendered table by consuming futures in target order.
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [
                    executor.submit(
                        _status_row_for_server,
                        client,
                        server,
                        ssh_timeout,
                        ssh_context,
                    )
                    for server in targets
                ]
                rows = [future.result() for future in futures]
    columns = [
        "vname",
        "hostname",
        "state",
        "architecture",
        "#CPU",
        "RAM GB",
        "disk GB",
        "ssh-connect",
        "reverse DNS",
    ]
    emit(
        rows,
        args.json,
        lambda data: print_table(
            data,
            columns,
            pal,
            "no servers on this account",
            multiline_columns={"reverse DNS"},
            column_headers={"architecture": "Arch", "RAM GB": "RAM", "disk GB": "disk"},
        ),
    )


def _filter_rows(rows: List[Dict[str, Any]], term: str | None) -> List[Dict[str, Any]]:
    if not term:
        return rows
    needle = term.casefold()
    return [row for row in rows if needle in _stringify(row).casefold()]


def _image_flavour_name(row: Dict[str, Any]) -> str:
    image = row.get("image")
    if image is None:
        return ""
    if not isinstance(image, dict):
        raise ResponseShapeError("imageflavours: an image field was not a JSON object")
    return _stringify(image.get("name"))


def _require_server_id(args, action: str) -> int:
    if args.server_id is None:
        print(f"ERROR: {action} requires a server ID", file=sys.stderr)
        raise SystemExit(2)
    return args.server_id


_TASK_STATES = (
    "PENDING",
    "RUNNING",
    "FINISHED",
    "ERROR",
    "WAITING_FOR_CANCEL",
    "CANCELED",
)
_METRIC_ENDPOINTS = {
    "cpu": "cpu",
    "disk": "disk",
    "network": "network",
    "network-packet": "network/packet",
}
_MAC_ADDRESS_RE = re.compile(r"^[a-fA-F0-9]{2}(?::[a-fA-F0-9]{2}){5}$")
_PORT_VALUE_RE = re.compile(r"^(?:0|[1-9][0-9]{0,4})(?:-(?:0|[1-9][0-9]{0,4}))?$")
_POLICY_FIELDS = {"name", "description", "rules"}
_RULE_FIELDS = {
    "description",
    "direction",
    "protocol",
    "action",
    "sources",
    "sourcePorts",
    "destinations",
    "destinationPorts",
}
_RULE_DIRECTIONS = {"INGRESS", "EGRESS"}
_RULE_PROTOCOLS = {"TCP", "UDP", "ICMP", "ICMPv6"}
_RULE_ACTIONS = {"ACCEPT", "DROP"}


class ResponseShapeError(RuntimeError):
    """The API returned valid JSON with an unusable shape."""


def _response_dict(value: Any, operation: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ResponseShapeError(
            f"{operation}: expected a JSON object, got {type(value).__name__}"
        )
    return value


def _response_rows(value: Any, operation: str) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise ResponseShapeError(
            f"{operation}: expected a JSON array, got {type(value).__name__}"
        )
    bad = next((index for index, row in enumerate(value) if not isinstance(row, dict)), None)
    if bad is not None:
        raise ResponseShapeError(f"{operation}: array item {bad} is not a JSON object")
    return value


def _response_strings(value: Any, operation: str) -> List[str]:
    if not isinstance(value, list):
        raise ResponseShapeError(
            f"{operation}: expected a JSON array, got {type(value).__name__}"
        )
    bad = next((index for index, item in enumerate(value) if not isinstance(item, str)), None)
    if bad is not None:
        raise ResponseShapeError(f"{operation}: array item {bad} is not a string")
    return value


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be an integer") from e
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be a number") from e
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


def _nonnegative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be an integer") from e
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def _hours(value: str) -> int:
    number = _nonnegative_int(value)
    if number == 0 or number > 1440:
        raise argparse.ArgumentTypeError("must be between 1 and 1440 hours")
    return number


def _part_size_mib(value: str) -> int:
    number = _positive_int(value)
    if number < 5:
        raise argparse.ArgumentTypeError("must be at least 5 MiB for multipart S3 uploads")
    return number


def _mac_address(value: str) -> str:
    if not _MAC_ADDRESS_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a MAC address such as aa:bb:cc:dd:ee:ff")
    return value


def _nonempty_text(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be empty or whitespace")
    return value


def _policy_validation_error(path: str, message: str) -> None:
    raise ValueError(f"firewall policy {path}: {message}")


def _validate_policy_text(
    value: Any,
    path: str,
    *,
    max_length: int,
    allow_none: bool = False,
    require_nonempty: bool = True,
) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str):
        _policy_validation_error(path, "must be a string")
    if require_nonempty and not value.strip():
        _policy_validation_error(path, "must not be empty or whitespace")
    if len(value) > max_length:
        _policy_validation_error(path, f"must be at most {max_length} characters")


def _validate_policy_addresses(value: Any, path: str) -> set[int]:
    if value is None:
        return set()
    if not isinstance(value, list):
        _policy_validation_error(path, "must be an array of IP addresses or networks")
    if all(isinstance(item, str) for item in value) and len(value) != len(set(value)):
        _policy_validation_error(path, "must not contain duplicate addresses")
    families: set[int] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            _policy_validation_error(f"{path}[{index}]", "must be a non-empty IP address or network")
        try:
            try:
                parsed = ipaddress.ip_address(item)
            except ValueError:
                parsed = ipaddress.ip_network(item, strict=False)
        except ValueError as e:
            _policy_validation_error(f"{path}[{index}]", f"is not a valid IP address or network: {e}")
        families.add(parsed.version)
    return families


def _validate_policy_ports(value: Any, path: str) -> None:
    if value is None:
        return  # SCP documents null as “any port”; omission has the same effect.
    if not isinstance(value, str) or not _PORT_VALUE_RE.fullmatch(value):
        _policy_validation_error(path, "must be a port number or range from 0 to 65535")
    parts = value.split("-")
    numbers = [int(part) for part in parts]
    if any(number > 65535 for number in numbers):
        _policy_validation_error(path, "must be a port number or range from 0 to 65535")
    if len(numbers) == 2 and numbers[0] > numbers[1]:
        _policy_validation_error(path, "must have its lower port first")


def _validate_firewall_policy(value: Any) -> Dict[str, Any]:
    """Validate the FirewallPolicySave request shape before POST/PUT.

    This intentionally validates the request schema locally, including the
    documented IP/port constraints, without silently dropping unknown fields.
    The SCP API remains the authority for provider-specific semantic checks.
    """
    if not isinstance(value, dict):
        _policy_validation_error("$", "must be a JSON object")
    unknown = set(value) - _POLICY_FIELDS
    if unknown:
        _policy_validation_error("$", f"unknown field(s): {', '.join(sorted(unknown))}")
    if "name" not in value:
        _policy_validation_error("$.name", "is required")
    _validate_policy_text(value["name"], "$.name", max_length=255)
    if "description" in value:
        _validate_policy_text(value["description"], "$.description", max_length=255, require_nonempty=False)
    if "rules" not in value:
        return value
    rules = value["rules"]
    if not isinstance(rules, list):
        _policy_validation_error("$.rules", "must be an array")
    if len(rules) > 500:
        _policy_validation_error("$.rules", "may contain at most 500 rules")
    for index, rule in enumerate(rules):
        path = f"$.rules[{index}]"
        if not isinstance(rule, dict):
            _policy_validation_error(path, "must be a JSON object")
        unknown = set(rule) - _RULE_FIELDS
        if unknown:
            _policy_validation_error(path, f"unknown field(s): {', '.join(sorted(unknown))}")
        for required in ("direction", "protocol", "action"):
            if required not in rule:
                _policy_validation_error(f"{path}.{required}", "is required")
        if not isinstance(rule["direction"], str) or rule["direction"] not in _RULE_DIRECTIONS:
            _policy_validation_error(f"{path}.direction", f"must be one of {sorted(_RULE_DIRECTIONS)}")
        if not isinstance(rule["protocol"], str) or rule["protocol"] not in _RULE_PROTOCOLS:
            _policy_validation_error(f"{path}.protocol", f"must be one of {sorted(_RULE_PROTOCOLS)}")
        if not isinstance(rule["action"], str) or rule["action"] not in _RULE_ACTIONS:
            _policy_validation_error(f"{path}.action", f"must be one of {sorted(_RULE_ACTIONS)}")
        if "description" in rule:
            _validate_policy_text(
                rule["description"], f"{path}.description", max_length=255,
                allow_none=True, require_nonempty=False,
            )
        source_families = set()
        destination_families = set()
        if "sources" in rule:
            source_families = _validate_policy_addresses(rule["sources"], f"{path}.sources")
        if "destinations" in rule:
            destination_families = _validate_policy_addresses(rule["destinations"], f"{path}.destinations")
        if len(rule.get("sources") or []) > 1 and len(rule.get("destinations") or []) > 1:
            _policy_validation_error(path, "multiple sources and multiple destinations cannot be combined")
        if len(source_families) > 1 and rule.get("destinations"):
            _policy_validation_error(path, "mixed IPv4/IPv6 sources require any destination")
        if len(destination_families) > 1 and rule.get("sources"):
            _policy_validation_error(path, "mixed IPv4/IPv6 destinations require any source")
        for field in ("sourcePorts", "destinationPorts"):
            if field in rule:
                _validate_policy_ports(rule[field], f"{path}.{field}")
    return value


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant {value!r} is not allowed")


def _load_firewall_policy(args) -> Dict[str, Any]:
    if args.policy_json is None and args.policy_file is None:
        raise ValueError("firewall policy input: provide exactly one of --policy-json or --policy-file")
    if args.policy_json is not None:
        source = "--policy-json"
        raw = args.policy_json
    else:
        source = f"--policy-file {args.policy_file}"
        try:
            raw = Path(args.policy_file).read_text(encoding="utf-8")
        except OSError as e:
            raise ValueError(f"{source}: cannot read file: {e}") from e
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"{source}: invalid JSON: {e}") from e
    return _validate_firewall_policy(value)


# --- subcommands -------------------------------------------------------------

def cmd_servers(client: NetcupSCPClient, args, pal: _Palette) -> None:
    servers = _response_rows(_api_call(client.get, "/api/v1/servers"), "GET /api/v1/servers")
    emit(servers, args.json, lambda d: print_table(
        d, ["id", "hostname", "nickname", "name", "disabled"], pal, "no servers on this account"
    ))


def cmd_server_details(client: NetcupSCPClient, args, pal: _Palette) -> None:
    server = _response_dict(
        _api_call(client.get, f"/api/v1/servers/{args.server_id}"),
        f"GET /api/v1/servers/{args.server_id}",
    )
    emit(server, args.json, lambda d: print_kv(d, pal))


def cmd_imageflavours(client: NetcupSCPClient, args, pal: _Palette) -> None:
    targets = _server_targets(client, args.server_id)
    aggregate = args.server_id is None
    flavours: List[Dict[str, Any]] = []
    for server in targets:
        result = _api_call(client.get, f"/api/v1/servers/{server['id']}/imageflavours")
        rows = _response_rows(result, f"GET /api/v1/servers/{server['id']}/imageflavours")
        flavours.extend(_annotate_server_row(row, server) if aggregate else row for row in rows)
    flavours = _filter_rows(flavours, getattr(args, "filter", None))

    def table(rows):
        flat = [
            {
                "serverId": r.get("serverId"),
                "serverName": r.get("serverName"),
                "id": r.get("id"),
                "name": _image_flavour_name(r),
                "alias": r.get("alias"),
            }
            for r in rows
        ]
        columns = ["serverId", "serverName", "id", "name", "alias"] if aggregate else ["id", "name", "alias"]
        scope = "on this account" if aggregate else "for this server"
        print_table(flat, columns, pal, f"no image flavours available {scope}")

    emit(flavours, args.json, table)


def cmd_iso_bootable(client: NetcupSCPClient, args, pal: _Palette) -> None:
    targets = _server_targets(client, args.server_id)
    aggregate = args.server_id is None
    images: List[Dict[str, Any]] = []
    for server in targets:
        result = _api_call(client.get, f"/api/v1/servers/{server['id']}/isoimages")
        rows = _response_rows(result, f"GET /api/v1/servers/{server['id']}/isoimages")
        images.extend(_annotate_server_row(row, server) if aggregate else row for row in rows)
    images = _filter_rows(images, getattr(args, "filter", None))
    columns = ["serverId", "serverName", "id", "name", "description", "architecture"] if aggregate else ["id", "name", "description", "architecture"]
    scope = "on this account" if aggregate else "for this server"
    emit(images, args.json, lambda d: print_table(d, columns, pal, f"no ISO images available {scope}"))


def cmd_attached_iso(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.action == "detach":
        server_id = _require_server_id(args, "detach")
        _guard_server_mutation(client, server_id, "detach ISO")
        if not confirm(f"Detach the ISO currently attached to server {args.server_id}?", args.yes, pal):
            print("aborted")
            return
        _api_call(client.delete, f"/api/v1/servers/{server_id}/iso")
        print(pal.green("detached"))
        return
    if args.server_id is not None:
        attached = _response_dict(
            _api_call(client.get, f"/api/v1/servers/{args.server_id}/iso"),
            f"GET /api/v1/servers/{args.server_id}/iso",
        )
        emit(attached, args.json, lambda d: print_kv(d, pal) if d else print(pal.dim("no ISO currently attached")))
        return
    rows = []
    for server in _server_targets(client, None):
        row = _response_dict(
            _api_call(client.get, f"/api/v1/servers/{server['id']}/iso"),
            f"GET /api/v1/servers/{server['id']}/iso",
        )
        rows.append(_annotate_server_row(row, server))
    emit(rows, args.json, lambda d: print_table(
        d, ["serverId", "serverName", "isoAttached", "iso"], pal, "no servers on this account"
    ))


def cmd_attach_iso(client: NetcupSCPClient, args, pal: _Palette) -> None:
    data: Dict[str, Any] = {}
    if args.iso_id is not None:
        data["isoId"] = args.iso_id
    if args.user_iso_name is not None:
        data["userIsoName"] = args.user_iso_name
    if args.change_boot_device_to_cdrom:
        data["changeBootDeviceToCdrom"] = True

    _guard_server_mutation(client, args.server_id, "attach ISO")
    if not confirm(f"Attach the selected ISO to server {args.server_id}?", args.yes, pal):
        print("aborted")
        return
    result = _response_dict(
        _api_call(client.post, f"/api/v1/servers/{args.server_id}/iso", data),
        f"POST /api/v1/servers/{args.server_id}/iso",
    )
    emit(result, args.json, lambda d: print_kv(d, pal) if isinstance(d, dict) and d else print(pal.green("attach requested")))


def cmd_disks(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.action == "supported-drivers":
        server_id = _require_server_id(args, "supported-drivers")
        drivers = _response_strings(
            _api_call(client.get, f"/api/v1/servers/{server_id}/disks/supported-drivers"),
            f"GET /api/v1/servers/{server_id}/disks/supported-drivers",
        )
        emit(drivers, args.json, lambda d: print(", ".join(d) if d else pal.dim("none reported")))
        return
    if args.server_id is not None:
        disks = _response_rows(
            _api_call(client.get, f"/api/v1/servers/{args.server_id}/disks"),
            f"GET /api/v1/servers/{args.server_id}/disks",
        )
        emit(disks, args.json, lambda d: print_table(
            d, ["name", "capacityInMiB", "allocationInMiB", "storageDriver"], pal, "no disks reported"
        ))
        return
    rows = []
    for server in _server_targets(client, None):
        disks = _response_rows(
            _api_call(client.get, f"/api/v1/servers/{server['id']}/disks"),
            f"GET /api/v1/servers/{server['id']}/disks",
        )
        rows.extend(_annotate_server_row(row, server) for row in disks)
    emit(rows, args.json, lambda d: print_table(
        d, ["serverId", "serverName", "name", "capacityInMiB", "allocationInMiB", "storageDriver"], pal, "no disks reported"
    ))


def cmd_rescuesystem(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.action == "deactivate":
        server_id = _require_server_id(args, "deactivate")
        _guard_server_mutation(client, server_id, "deactivate rescue system")
        if not confirm(f"Deactivate the rescue system for server {args.server_id}?", args.yes, pal):
            print("aborted")
            return
        _api_call(client.delete, f"/api/v1/servers/{server_id}/rescuesystem")
        print(pal.green("deactivated"))
        return
    if args.server_id is not None:
        status = _response_dict(
            _api_call(client.get, f"/api/v1/servers/{args.server_id}/rescuesystem"),
            f"GET /api/v1/servers/{args.server_id}/rescuesystem",
        )
        emit(status, args.json, lambda d: print_kv(d, pal))
        return
    rows = []
    for server in _server_targets(client, None):
        status = _response_dict(
            _api_call(client.get, f"/api/v1/servers/{server['id']}/rescuesystem"),
            f"GET /api/v1/servers/{server['id']}/rescuesystem",
        )
        rows.append(_annotate_server_row(status, server))
    emit(rows, args.json, lambda d: print_table(
        d, ["serverId", "serverName", "active"], pal, "no servers on this account"
    ))


def cmd_snapshots(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.action == "dryrun":
        server_id = _require_server_id(args, "dryrun")
        result = _response_dict(
            _api_call(client.post, f"/api/v1/servers/{server_id}/snapshots:dryrun", {}),
            f"POST /api/v1/servers/{server_id}/snapshots:dryrun",
        )
        emit(result, args.json, lambda d: print_kv(d, pal))
        return
    if args.action == "create":
        server_id = _require_server_id(args, "create")
        _guard_server_mutation(client, server_id, "create snapshot")
        if not confirm(f"Create a new snapshot of server {args.server_id}?", args.yes, pal):
            print("aborted")
            return
        # name is REQUIRED by the API (ServerSnapshotCreate schema) -- an
        # omitted --name previously sent {} and always failed server-side
        # AFTER the confirm prompt (confirmed against the OpenAPI spec,
        # 2026-09-09). Default to a timestamp rather than forcing --name
        # on every call.
        name = args.name or f"vbpub-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        result = _response_dict(
            _api_call(client.post, f"/api/v1/servers/{server_id}/snapshots", {"name": name}),
            f"POST /api/v1/servers/{server_id}/snapshots",
        )
        emit(result, args.json, lambda d: print_kv(d, pal))
        return
    if args.server_id is not None:
        snapshots = _response_rows(
            _api_call(client.get, f"/api/v1/servers/{args.server_id}/snapshots"),
            f"GET /api/v1/servers/{args.server_id}/snapshots",
        )
        emit(snapshots, args.json, lambda d: print_table(
            d, ["uuid", "name", "state", "online", "creationTime"], pal, "no snapshots for this server"
        ))
        return
    rows = []
    for server in _server_targets(client, None):
        snapshots = _response_rows(
            _api_call(client.get, f"/api/v1/servers/{server['id']}/snapshots"),
            f"GET /api/v1/servers/{server['id']}/snapshots",
        )
        rows.extend(_annotate_server_row(row, server) for row in snapshots)
    emit(rows, args.json, lambda d: print_table(
        d, ["serverId", "serverName", "uuid", "name", "state", "online", "creationTime"], pal,
        "no snapshots on this account"
    ))


def cmd_tasks(client: NetcupSCPClient, args, pal: _Palette) -> None:
    server_filter_id = getattr(args, "server_filter_id", None)
    params = {}
    if getattr(args, "query", None):
        params["q"] = args.query
    if getattr(args, "server_filter_id", None) is not None:
        params["serverId"] = args.server_filter_id
    if getattr(args, "state", None):
        params["state"] = args.state
    if getattr(args, "limit", None) is not None:
        params["limit"] = args.limit
    if getattr(args, "offset", None) is not None:
        params["offset"] = args.offset
    params = params or None

    if args.uuid is not None:
        other_filters = any(
            getattr(args, name, None) is not None
            for name in ("query", "state", "limit", "offset")
        )
        if other_filters or (server_filter_id is not None and args.action != "cancel"):
            print("ERROR: task filters are only valid when listing tasks, except --server-id with cancel", file=sys.stderr)
            raise SystemExit(2)
    if args.action == "cancel" and args.uuid is None:
        print("ERROR: cancel requires a task uuid", file=sys.stderr)
        sys.exit(2)
    if args.uuid is None:
        tasks = _response_rows(_api_call(client.get, "/api/v1/tasks", params=params), "GET /api/v1/tasks")
        emit(tasks, args.json, lambda d: print_table(
            d, ["uuid", "name", "state", "startedAt", "finishedAt"], pal, "no tasks found"
        ))
        return
    if args.action == "cancel":
        protected = netcup_scp_client.protected_server_policy_configured()
        if protected and server_filter_id is None:
            print(
                "ERROR: task cancellation requires --server-id when the protected-server denylist is configured; "
                "the API task record does not identify its server reliably",
                file=sys.stderr,
            )
            raise SystemExit(2)
        if server_filter_id is not None:
            _guard_server_mutation(client, server_filter_id, "cancel task")
            verification = _response_rows(
                _api_call(
                    client.get,
                    "/api/v1/tasks",
                    params={"q": args.uuid, "serverId": server_filter_id},
                ),
                "GET /api/v1/tasks task/server verification",
            )
            if not any(row.get("uuid") == args.uuid for row in verification):
                raise netcup_scp_client.ProtectedServerError(
                    f"refusing cancel task: API did not verify task {args.uuid!r} belongs to server {server_filter_id}"
                )
        if not confirm(f"Cancel task {args.uuid}?", args.yes, pal):
            print("aborted")
            return
        _api_call(client.put, f"/api/v1/tasks/{args.uuid}:cancel")
        print(pal.green("cancel requested"))
        return
    task = _response_dict(_api_call(client.get, f"/api/v1/tasks/{args.uuid}"), f"GET /api/v1/tasks/{args.uuid}")
    emit(task, args.json, lambda d: print_kv(d, pal))


def cmd_metrics(client: NetcupSCPClient, args, pal: _Palette) -> None:
    endpoint = _METRIC_ENDPOINTS[args.metric]
    params = {"hours": args.hours} if args.hours is not None else None
    result = _response_dict(
        _api_call(client.get, f"/api/v1/servers/{args.server_id}/metrics/{endpoint}", params=params),
        f"GET /api/v1/servers/{args.server_id}/metrics/{endpoint}",
    )
    emit(result, args.json)


def cmd_guest_agent_status(client: NetcupSCPClient, args, pal: _Palette) -> None:
    result = _response_dict(
        _api_call(client.get, f"/api/v1/servers/{args.server_id}/guest-agent/status"),
        f"GET /api/v1/servers/{args.server_id}/guest-agent/status",
    )
    emit(result, args.json, lambda d: print_kv(d, pal))


def _scp_user_id(client: NetcupSCPClient) -> int:
    user_info = _response_dict(_api_call(client.get_user_info), "GET OIDC userinfo")
    user_id = user_info.get("id")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise ResponseShapeError("GET OIDC userinfo: response did not contain a positive integer user id")
    return user_id


def _firewall_policy_input_args(args) -> Dict[str, Any]:
    return _load_firewall_policy(args)


def cmd_firewall_policy_write(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.query is not None or args.limit is not None or args.offset is not None:
        print("ERROR: policy list filters cannot be combined with create or put", file=sys.stderr)
        raise SystemExit(2)
    policy = _firewall_policy_input_args(args)
    if not policy.get("rules"):
        print("WARNING: policy contains no rules; it will not match any traffic by itself", file=sys.stderr)
    if args.action == "put":
        if args.policy_id is None:
            print("ERROR: firewall-policies put requires a policy_id", file=sys.stderr)
            raise SystemExit(2)
        prompt = f"Update firewall policy {args.policy_id} ({policy['name']!r})?"
    else:
        if args.policy_id is not None:
            print("ERROR: firewall-policies create does not take a policy_id", file=sys.stderr)
            raise SystemExit(2)
        prompt = f"Create firewall policy {policy['name']!r}?"
    if not confirm(prompt, args.yes, pal):
        print("aborted")
        return
    user_id = _scp_user_id(client)
    if args.action == "put":
        endpoint = f"/api/v1/users/{user_id}/firewall-policies/{args.policy_id}"
        method = client.put
        method_args = (endpoint, policy)
        operation = f"PUT {endpoint}"
    else:
        endpoint = f"/api/v1/users/{user_id}/firewall-policies"
        method = client.post
        method_args = (endpoint, policy)
        operation = f"POST {endpoint}"
    result = _response_dict(_api_call(method, *method_args), operation)
    emit(result, args.json, lambda d: print_kv(d, pal) if d else print(pal.green("firewall policy request accepted")))


def _user_iso_endpoint(user_id: int, key: str) -> str:
    return f"/api/v1/users/{user_id}/isos/{urllib.parse.quote(key, safe='')}"


def _user_iso_key(args, path: Path) -> str:
    key = args.name or path.name
    if not isinstance(key, str) or not key.strip():
        raise ValueError("user ISO name must not be empty")
    return key


def _user_iso_file(args) -> tuple[Path, int, str]:
    if args.file is None:
        raise ValueError("user-iso upload requires a local FILE")
    path = Path(args.file)
    try:
        if not path.is_file():
            raise ValueError(f"ISO file {path} is not a regular file")
        size = path.stat().st_size
    except OSError as e:
        raise ValueError(f"cannot inspect ISO file {path}: {e}") from e
    if size <= 0:
        raise ValueError(f"ISO file {path} is empty")
    return path, size, _user_iso_key(args, path)


def cmd_user_iso(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.action == "upload":
        path, size, key = _user_iso_file(args)
        if not confirm(f"Upload {path} as user ISO {key!r} ({size} bytes)?", args.yes, pal):
            print("aborted")
            return
        user_id = _scp_user_id(client)
        multipart = args.multipart
        endpoint = _user_iso_endpoint(user_id, key)
        prepared = _response_dict(
            _api_call(client.post, f"{endpoint}?multipart={'true' if multipart else 'false'}", None),
            f"POST {endpoint}",
        )
        if multipart:
            upload_id = prepared.get("uploadId")
            if not isinstance(upload_id, str) or not upload_id.strip():
                raise ResponseShapeError(f"POST {endpoint}: multipart response had no uploadId")
            part_size = (args.part_size_mib or 64) * 1024 * 1024
            parts = []
            offset = 0
            part_number = 1
            while offset < size:
                length = min(part_size, size - offset)
                part_endpoint = f"{endpoint}/{urllib.parse.quote(upload_id, safe='')}/parts/{part_number}"
                part_result = _response_dict(
                    _api_call(client.get, part_endpoint),
                    f"GET {part_endpoint}",
                )
                upload_url = part_result.get("url")
                if not isinstance(upload_url, str) or not upload_url.strip():
                    raise ResponseShapeError(f"GET {part_endpoint}: response had no upload URL")
                headers = _api_call(client.upload_file, upload_url, path, offset=offset, size=length)
                etag = headers.get("etag") if isinstance(headers, dict) else None
                if not isinstance(etag, str) or not etag.strip():
                    raise ResponseShapeError(f"PUT part {part_number}: upload response had no ETag")
                parts.append({"ETag": etag, "partNumber": part_number})
                offset += length
                part_number += 1
            complete_endpoint = f"{endpoint}/{urllib.parse.quote(upload_id, safe='')}"
            _api_call(client.put, complete_endpoint, parts)
            summary = {"key": key, "sizeInB": size, "multipart": True, "parts": len(parts)}
        else:
            upload_url = prepared.get("presignedUrl")
            if not isinstance(upload_url, str) or not upload_url.strip():
                raise ResponseShapeError(f"POST {endpoint}: response had no presignedUrl")
            _api_call(client.upload_file, upload_url, path)
            summary = {"key": key, "sizeInB": size, "multipart": False}
        emit(summary, args.json, lambda d: print_kv(d, pal))
        return

    if (getattr(args, "file", None) is not None
            or getattr(args, "name", None) is not None
            or getattr(args, "multipart", False)
            or getattr(args, "part_size_mib", None) is not None
            or getattr(args, "yes", False)):
        print("ERROR: user-iso upload options require the explicit upload action", file=sys.stderr)
        raise SystemExit(2)
    user_id = _scp_user_id(client)
    result = _response_rows(
        _api_call(client.get, f"/api/v1/users/{user_id}/isos"),
        f"GET /api/v1/users/{user_id}/isos",
    )
    emit(result, args.json, lambda d: print_table(
        d, ["key", "lastModified", "sizeInB"], pal, "no user ISOs uploaded"
    ))


def cmd_firewall_policies(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if getattr(args, "action", None) in ("create", "put"):
        return cmd_firewall_policy_write(client, args, pal)
    if (getattr(args, "policy_id", None) is not None
            or getattr(args, "policy_json", None) is not None
            or getattr(args, "policy_file", None) is not None
            or getattr(args, "yes", False)):
        print("ERROR: policy write options require the create or put action", file=sys.stderr)
        raise SystemExit(2)
    user_id = _scp_user_id(client)
    params = {}
    if args.query:
        params["q"] = args.query
    if args.limit is not None:
        params["limit"] = args.limit
    if args.offset is not None:
        params["offset"] = args.offset
    result = _response_rows(
        _api_call(client.get, f"/api/v1/users/{user_id}/firewall-policies", params=params or None),
        f"GET /api/v1/users/{user_id}/firewall-policies",
    )

    def table(rows):
        flat = [
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "description": row.get("description"),
                "rules": len(row.get("rules") or []) if isinstance(row.get("rules"), list) else "",
            }
            for row in rows
            if isinstance(row, dict)
        ]
        print_table(flat, ["id", "name", "description", "rules"], pal, "no firewall policies found")

    emit(result, args.json, table)


def _resolve_firewall_mac(client: NetcupSCPClient, server_id: int) -> str:
    endpoint = f"/api/v1/servers/{server_id}"
    details = _response_dict(_api_call(client.get, endpoint), f"GET {endpoint}")
    live_info = details.get("serverLiveInfo")
    if isinstance(live_info, dict) and "interfaces" in live_info:
        interfaces = live_info["interfaces"]
    elif "interfaces" in details:
        # Accept the older/condensed shape as well, but do not invent a MAC
        # when the server response is only partially populated.
        interfaces = details["interfaces"]
    else:
        raise ResponseShapeError(
            f"GET {endpoint}: response did not contain serverLiveInfo.interfaces; "
            "specify the interface MAC explicitly or retry with live server details"
        )
    if not isinstance(interfaces, list):
        raise ResponseShapeError(f"GET {endpoint}: interfaces is not a JSON array")

    macs = []
    for index, interface in enumerate(interfaces):
        if not isinstance(interface, dict):
            raise ResponseShapeError(f"GET {endpoint}: interface {index} is not a JSON object")
        mac = interface.get("mac")
        if not isinstance(mac, str) or not _MAC_ADDRESS_RE.fullmatch(mac):
            raise ResponseShapeError(f"GET {endpoint}: interface {index} has no valid MAC address")
        if mac not in macs:
            macs.append(mac)

    if len(macs) == 1:
        return macs[0]
    if not macs:
        raise ResponseShapeError(f"GET {endpoint}: no interfaces with a MAC address were returned")
    print(
        f"ERROR: server {server_id} has multiple interfaces; specify one MAC explicitly: {', '.join(macs)}",
        file=sys.stderr,
    )
    raise SystemExit(2)


def cmd_firewall(client: NetcupSCPClient, args, pal: _Palette) -> None:
    action = args.action or "get"
    copied_policy_ids = getattr(args, "copied_policy_ids", [])
    user_policy_ids = getattr(args, "user_policy_ids", [])
    active = getattr(args, "active", None)
    if action == "get":
        if copied_policy_ids or user_policy_ids or active is not None:
            print("ERROR: firewall set options require the explicit set action", file=sys.stderr)
            raise SystemExit(2)
        mac = args.mac or _resolve_firewall_mac(client, args.server_id)
        endpoint = f"/api/v1/servers/{args.server_id}/interfaces/{mac}/firewall"
        params = {"consistencyCheck": True} if args.consistency_check else None
        result = _response_dict(_api_call(client.get, endpoint, params=params), f"GET {endpoint}")
        emit(result, args.json, lambda d: print_kv(d, pal) if d else print(pal.dim("no firewall assignment reported")))
        return

    if active is None:
        print("ERROR: firewall set requires either --active or --inactive", file=sys.stderr)
        raise SystemExit(2)
    mac = args.mac or _resolve_firewall_mac(client, args.server_id)
    endpoint = f"/api/v1/servers/{args.server_id}/interfaces/{mac}/firewall"
    data = {
        "copiedPolicies": [{"id": policy_id} for policy_id in copied_policy_ids],
        "userPolicies": [{"id": policy_id} for policy_id in user_policy_ids],
        "active": active,
    }
    _guard_server_mutation(client, args.server_id, "set firewall assignment")
    if not confirm(
        f"Replace firewall policy assignments on {args.server_id}/{mac}?",
        args.yes,
        pal,
    ):
        print("aborted")
        return
    result = _response_dict(_api_call(client.put, endpoint, data), f"PUT {endpoint}")
    emit(result, args.json, lambda d: print_kv(d, pal) if isinstance(d, dict) and d else print(pal.green("firewall update requested")))


_POWER_ACTIONS = {
    "on": ("ON", None, "Power on"),
    "off": ("OFF", "POWEROFF", "Power off"),
    "cycle": ("ON", "POWERCYCLE", "Power-cycle"),
    "reset": ("ON", "RESET", "Reset"),
}


def cmd_power(client: NetcupSCPClient, args, pal: _Palette) -> None:
    state, state_option, label = _POWER_ACTIONS[args.action]
    _guard_server_mutation(client, args.server_id, f"{label.lower()} server")
    if not confirm(f"{label} server {args.server_id}?", args.yes, pal):
        print("aborted")
        return
    params = {"stateOption": state_option} if state_option else None
    result = _response_dict(_api_call(
        client.patch,
        f"/api/v1/servers/{args.server_id}",
        {"state": state},
        params=params,
    ), f"PATCH /api/v1/servers/{args.server_id}")
    emit(result, args.json, lambda d: print_kv(d, pal) if d else print(pal.green(f"{label.lower()} requested")))


def _configure_protected_servers(env_path: Path, client: NetcupSCPClient) -> int:
    """Offer an interactive, local denylist selection after successful login."""
    try:
        result = _response_rows(
            _api_call(client.get, "/api/v1/servers"),
            "GET /api/v1/servers",
        )
    except Exception as exc:
        print(
            f"WARNING: login succeeded, but protected-server selection could not read the server list: {exc}",
            file=sys.stderr,
        )
        return 1

    eligible: List[Dict[str, Any]] = []
    for index, server in enumerate(result):
        name = server.get("name")
        if not netcup_scp_client.is_protectable_server_name(name):
            continue
        server_id = server.get("id")
        if not isinstance(server_id, int) or isinstance(server_id, bool) or server_id <= 0:
            print(
                f"ERROR: GET /api/v1/servers: v-name {name!r} at item {index} has no valid positive integer id",
                file=sys.stderr,
            )
            return 1
        enriched = {"name": name, "id": server_id}
        try:
            enriched["details"] = _response_dict(
                client.get(f"/api/v1/servers/{server_id}"),
                f"GET /api/v1/servers/{server_id}",
            )
        except Exception as exc:
            # Login must still let the operator protect a server when its
            # optional detail response is temporarily unavailable.
            # The missing enrichment is explicit in the prompt instead of
            # being mistaken for an empty address set.
            print(
                f"WARNING: could not load IP/rDNS details for {name} (id {server_id}): {exc}",
                file=sys.stderr,
            )
            enriched["details"] = enriched.get("details", {})
        eligible.append(enriched)

    existing_names, existing_ids = netcup_scp_client.protected_server_policy()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "Login succeeded. Protected-server selection was skipped because this is not an interactive terminal. "
            "Run `scp-api.py login` from a terminal, or set NETCUP_SCP_API_PROTECTED_SERVERS to comma-separated "
            "v<digits> names in .env.",
        )
        return 0
    if not eligible:
        print("Login succeeded. No servers with an SCP v<digits> name are available for the local denylist.")
        return 0

    print("\nLocal protected-server denylist")
    print("This is a local safety guard, not a Netcup-side lock.")
    print("Select servers whose mutating API operations this checkout must refuse:")
    for number, server in enumerate(eligible, start=1):
        marker = " [already protected]" if server["name"] in existing_names else ""
        print(f"  {number}. {server['name']} (id {server['id']}){marker}")
        summary = _server_status_row(server, server["details"])
        inventory = _server_address_inventory(server["details"])
        print(f"     IPv4: {', '.join(inventory['ipv4']) or '-'}")
        print(f"     IPv6: {', '.join(inventory['ipv6']) or '-'}")
        reverse_dns_entries = summary["reverse DNS"].splitlines() or ["-"]
        print(f"     reverse DNS: {reverse_dns_entries[0]}")
        for entry in reverse_dns_entries[1:]:
            print(f"                  {entry}")

    while True:
        try:
            answer = input("Add servers to the local protected-server denylist? [numbers, all, or Enter for none] ")
        except EOFError:
            print("\nNo selection made; existing protected-server denylist unchanged.")
            return 0
        answer = answer.strip()
        if not answer:
            print("Protected-server denylist unchanged.")
            return 0
        if answer.lower() == "all":
            selected = eligible
            break
        tokens = [token.strip() for token in answer.split(",")]
        if not tokens or any(not token.isdigit() for token in tokens):
            print("ERROR: enter comma-separated list numbers such as 1,3, or `all`.", file=sys.stderr)
            continue
        numbers = [int(token) for token in tokens]
        if any(number < 1 or number > len(eligible) for number in numbers):
            print(f"ERROR: choose numbers from 1 to {len(eligible)}.", file=sys.stderr)
            continue
        selected = [eligible[number - 1] for number in dict.fromkeys(numbers)]
        break

    names = list(existing_names)
    ids = list(existing_ids)
    for server in selected:
        if server["name"] not in names:
            names.append(server["name"])
        if server["id"] not in ids:
            ids.append(server["id"])
    try:
        netcup_scp_client.write_protected_server_policy(env_path, names, ids)
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not save protected-server denylist: {exc}", file=sys.stderr)
        return 1
    print(f"Protected {len(selected)} server(s) locally in {env_path} (mode 0600).")
    return 0


def cmd_login(args) -> int:
    env_path = netcup_scp_client.resolve_env_path()
    result = run_device_code_login(env_path)
    if result != 0:
        return result
    # The login helper has just written the token to this same .env. Reload it
    # before creating the client used to populate the optional denylist.
    load_env_file()
    try:
        client = build_client()
        return _configure_protected_servers(env_path, client)
    except netcup_scp_client.ProtectedServerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


# --- argument parsing --------------------------------------------------------


class _WideHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Keep command examples readable in the intended wide terminal view."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("width", 120)
        super().__init__(*args, **kwargs)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Inspect and safely operate the Netcup SCP account and its servers.",
        formatter_class=_WideHelpFormatter,
        epilog=__doc__,
        add_help=False,
    )
    help_options = parser.add_argument_group("help")
    help_options.add_argument("--help", action="help", help="show this help message and exit")
    output_options = parser.add_argument_group("output options")
    output_options.add_argument("--json", action="store_true", help="print raw JSON instead of a formatted table/summary")
    output_options.add_argument("--no-color", action="store_true", help="disable ANSI color even on a TTY")

    sub = parser.add_subparsers(
        dest="command",
        required=True,
        title="verbs (exploration and modification; see the grouped map below)",
        metavar="VERB",
    )

    def add_subcommand(name: str, help_text: str, description: str = ""):
        command_parser = sub.add_parser(
            name,
            help=help_text,
            description=description or help_text,
            formatter_class=_WideHelpFormatter,
            add_help=False,
        )
        help_options = command_parser.add_argument_group("help")
        help_options.add_argument("--help", action="help", help="show this help message and exit")
        # Keep the documented `command --json` spelling working as well as
        # the global `--json command` spelling. SUPPRESS avoids an omitted
        # subcommand option overwriting a global one.
        output_options = command_parser.add_argument_group("output options")
        output_options.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS,
            help="print raw JSON instead of a formatted table/summary",
        )
        output_options.add_argument(
            "--no-color", action="store_true", default=argparse.SUPPRESS,
            help="disable ANSI color even on a TTY",
        )
        return command_parser

    def add_actions(command_parser, choices, help_text: str):
        action_group = command_parser.add_argument_group("actions")
        action_group.add_argument(
            "action",
            nargs="?",
            choices=choices,
            metavar="{" + ",".join(choices) + "}",
            help=help_text,
        )

    def add_confirmation(command_parser, help_text="skip the confirmation prompt"):
        confirmation = command_parser.add_argument_group("confirmation")
        confirmation.add_argument("--yes", action="store_true", help=help_text)

    add_subcommand(
        "login",
        "OAuth2 login and optional protected-server selection",
        "Obtain the long-lived refresh token through the browser device-code flow. "
        "Afterwards, an interactive terminal offers servers named v<digits> for the local protected-server denylist. "
        "The denylist is enforced by mutating commands in this checkout; it is not a provider-side lock.\n\n"
        "Examples:\n  ./scp-api.py login",
    )

    add_subcommand(
        "servers",
        "list all known servers",
        "List the account's server inventory.\n\nExamples:\n  ./scp-api.py servers",
    )
    p = add_subcommand(
        "status",
        "show compact live status for all servers or one server",
        "Fetch each server detail and show vname, hostname, run state, architecture, "
        "CPU count, RAM, disk capacity, SSH authentication result, and a final "
        "multiline reverse-DNS column containing only the detail response's "
        "ipv4Addresses/ipv6Addresses. The SSH column tests every recognizable "
        "private key in ~/.ssh plus the configured install-host identity without "
        "creating a key. It first performs one SSH service probe per address, "
        "then tests keys in server-name/hostname filename order. The default "
        "probe timeout is 2 seconds. It reports successful key names, `no keys "
        "match`, `rejected`, or `no answer`. `no keys match` means the service "
        "answered but every tested key failed public-key authentication; "
        "`rejected` means the SSH endpoint refused the session itself; "
        "`no answer` means no address responded. "
        "With no ID, every account server is queried using a bounded four-worker pool. "
        "Reverse-DNS lookups are concurrent but are not cached across duplicate addresses.\n\n"
        "Examples:\n"
        "  ./scp-api.py status\n"
        "  ./scp-api.py status 799611 --json",
    )
    p.add_argument(
        "server_id", nargs="?", type=_positive_int, default=None, metavar="server_id",
        help="Netcup SCP server ID; omit to show all account servers",
    )
    p.add_argument(
        "--ssh-timeout",
        type=_positive_float,
        default=DEFAULT_SSH_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="per-address SSH service/key timeout (default: 2 seconds)",
    )
    p = add_subcommand(
        "server-details",
        "show detailed information for one server",
        "Show the complete API record for one server.\n\nExamples:\n  ./scp-api.py server-details 799611",
    )
    p.add_argument("server_id", type=_positive_int, metavar="server_id", help="Netcup SCP server ID")

    for name, help_text, description in [
        (
            "imageflavours",
            "list reinstallable OS/image flavours (all servers unless an ID is given)",
            "An image flavour is a server-compatible reinstallable OS/image variant, "
            "for example a Debian 13 UEFI amd64 image. It is not a VM template.\n\n"
            "Examples:\n  ./scp-api.py imageflavours --filter debian",
        ),
        (
            "iso-bootable",
            "list available ISO images (all servers unless an ID is given)",
            "ISO images are bootable installer/recovery media exposed by SCP. "
            "Use --filter debian or --filter rescue to narrow the names and descriptions.\n\n"
            "Examples:\n  ./scp-api.py iso-bootable --filter rescue",
        ),
    ]:
        p = add_subcommand(name, help_text, description)
        p.add_argument(
            "server_id", nargs="?", type=_positive_int, default=None, metavar="server_id",
            help="Netcup SCP server ID; omit to list choices across the account",
        )
        selection = p.add_argument_group("selection")
        selection.add_argument("--filter", type=_nonempty_text, metavar="TEXT", help="case-insensitive text filter across returned fields")

    p = add_subcommand(
        "iso-attached",
        "show attached ISOs for all servers, or one server",
        "Show the ISO currently attached to each server; add the detach action to remove one.\n\n"
        "Examples:\n"
        "  ./scp-api.py iso-attached 799611\n"
        "  ./scp-api.py iso-attached 799611 detach --yes",
    )
    p.add_argument(
        "server_id", nargs="?", type=_positive_int, default=None, metavar="server_id",
        help="Netcup SCP server ID; omit to inspect every server",
    )
    add_actions(p, ("detach",), "detach: remove the currently-attached ISO (safe/reversible)")
    add_confirmation(p)

    p = add_subcommand(
        "attach-iso",
        "attach a bootable or user ISO to one server",
        "Attach an ISO by ID from iso-bootable, or attach a user-uploaded ISO by name. "
        "This changes the server's attached media and always asks for confirmation.\n\n"
        "Examples:\n"
        "  ./scp-api.py attach-iso 799611 --iso-id 1234 --yes\n"
        "  ./scp-api.py attach-iso 799611 --user-iso-name custom.iso "
        "--change-boot-device-to-cdrom --yes",
    )
    p.add_argument("server_id", type=_positive_int, metavar="server_id", help="Netcup SCP server ID")
    iso_source = p.add_argument_group("ISO source").add_mutually_exclusive_group(required=True)
    iso_source.add_argument("--iso-id", type=_positive_int, help="ID returned by iso-bootable")
    iso_source.add_argument("--user-iso-name", type=_nonempty_text, metavar="NAME", help="name of an ISO uploaded to the account")
    boot_options = p.add_argument_group("boot options")
    boot_options.add_argument(
        "--change-boot-device-to-cdrom",
        action="store_true",
        help="also make the virtual CD-ROM the next boot device",
    )
    add_confirmation(p)

    p = add_subcommand(
        "disks",
        "list disks for all servers, or one server",
        "List disk capacity/allocation and storage drivers.\n\n"
        "Examples:\n"
        "  ./scp-api.py disks 799611\n"
        "  ./scp-api.py disks 799611 supported-drivers",
    )
    p.add_argument(
        "server_id", nargs="?", type=_positive_int, default=None, metavar="server_id",
        help="Netcup SCP server ID; omit to list disks for every server",
    )
    add_actions(p, ("supported-drivers",), "supported-drivers: list storage drivers for one server")

    p = add_subcommand(
        "rescuesystem",
        "show rescue-system status for all servers, or one server",
        "Show whether Netcup's provider-managed emergency rescue environment is active; "
        "this is separate from an arbitrary attached ISO. Add deactivate to turn it off.\n\n"
        "Examples:\n"
        "  ./scp-api.py rescuesystem 799611\n"
        "  ./scp-api.py rescuesystem 799611 deactivate --yes",
    )
    p.add_argument(
        "server_id", nargs="?", type=_positive_int, default=None, metavar="server_id",
        help="Netcup SCP server ID; omit to inspect every server",
    )
    add_actions(p, ("deactivate",), "deactivate: turn off the rescue system (safe/reversible)")
    add_confirmation(p)

    p = add_subcommand(
        "snapshots",
        "list snapshots for all servers, or act on one server",
        "List snapshots, or use create/dryrun for one server.\n\n"
        "Examples:\n"
        "  ./scp-api.py snapshots 799611\n"
        "  ./scp-api.py snapshots 799611 dryrun\n"
        "  ./scp-api.py snapshots 799611 create --name before-upgrade --yes",
    )
    p.add_argument(
        "server_id", nargs="?", type=_positive_int, default=None, metavar="server_id",
        help="Netcup SCP server ID; omit to list snapshots for every server",
    )
    add_actions(
        p,
        ("create", "dryrun"),
        "create: create a snapshot; dryrun: check whether snapshot creation is possible",
    )
    snapshot_options = p.add_argument_group("snapshot options")
    snapshot_options.add_argument("--name", default=None, help="optional name for the create action")
    add_confirmation(p)

    p = add_subcommand(
        "tasks",
        "list tasks, show one, or cancel one",
        "List tasks, show one by UUID, or use cancel with a UUID.\n\n"
        "Examples:\n"
        "  ./scp-api.py tasks --state RUNNING\n"
        "  ./scp-api.py tasks TASK_UUID\n"
        "  ./scp-api.py tasks TASK_UUID cancel --server-id 799611 --yes",
    )
    p.add_argument(
        "uuid", nargs="?", type=_nonempty_text, default=None, metavar="task_uuid",
        help="task UUID; omit to list tasks",
    )
    add_actions(p, ("cancel",), "cancel: cancel a running task (does not undo whatever it already did)")
    task_filters = p.add_argument_group("list filters")
    task_filters.add_argument(
        "--query", "--filter", dest="query", type=_nonempty_text, metavar="TEXT",
        help="list tasks whose name, UUID, or server fields contain TEXT (API q filter)",
    )
    task_filters.add_argument(
        "--server-id", dest="server_filter_id", type=_positive_int, metavar="ID",
        help="list tasks for one server; required with cancel when the protected-server denylist is configured",
    )
    task_filters.add_argument(
        "--state",
        choices=_TASK_STATES,
        help="list tasks in one state (ROLLBACK is not supported by the API filter)",
    )
    task_filters.add_argument("--limit", type=_nonnegative_int, help="maximum number of tasks to return")
    task_filters.add_argument("--offset", type=_nonnegative_int, help="number of matching tasks to skip")
    add_confirmation(p)

    p = add_subcommand(
        "metrics",
        "show CPU, disk, or network metrics for one server",
        "Return timestamped SCP metrics. The API's hours value is a lookback window, not a sample interval.\n\n"
        "Examples:\n  ./scp-api.py metrics 799611 cpu --hours 24",
    )
    p.add_argument("server_id", type=_positive_int, metavar="server_id", help="Netcup SCP server ID")
    p.add_argument(
        "metric", choices=tuple(_METRIC_ENDPOINTS), metavar="{cpu,disk,network,network-packet}",
        help="metric series to return",
    )
    metrics_options = p.add_argument_group("metric options")
    metrics_options.add_argument("--hours", type=_hours, help="look back this many hours (1-1440; API default if omitted)")

    p = add_subcommand(
        "guest-agent-status",
        "show the QEMU guest-agent status for one server",
        "Read whether the guest agent is available; this describes agent reachability, not SSH or bootstrap state.\n\n"
        "Examples:\n  ./scp-api.py guest-agent-status 799611",
    )
    p.add_argument("server_id", type=_positive_int, metavar="server_id", help="Netcup SCP server ID")

    p = add_subcommand(
        "user-iso",
        "list account user ISOs or upload one",
        "List account-level user ISO objects, or upload a local ISO through SCP's "
        "presigned single-part or multipart object-storage flow. The upload action "
        "does not attach or boot the ISO; use attach-iso and power cycle afterward.\n\n"
        "Examples:\n"
        "  ./scp-api.py user-iso\n"
        "  ./scp-api.py user-iso upload ./custom.iso --name custom.iso --yes\n"
        "  ./scp-api.py attach-iso 799611 --user-iso-name custom.iso --yes\n"
        "  ./scp-api.py power cycle 799611 --yes",
    )
    add_actions(p, ("upload",), "upload: upload one local ISO (confirmed)")
    p.add_argument(
        "file", nargs="?", metavar="FILE",
        help="local ISO path; required with upload, omitted when listing",
    )
    upload_options = p.add_argument_group("upload options")
    upload_options.add_argument("--name", type=_nonempty_text, metavar="KEY", help="object name; defaults to the local filename")
    upload_options.add_argument("--multipart", action="store_true", help="use multipart upload for large ISOs")
    upload_options.add_argument("--part-size-mib", type=_part_size_mib, default=None, metavar="N", help="multipart part size (default: 64; minimum: 5)")
    add_confirmation(p, "skip the upload confirmation prompt")

    p = add_subcommand(
        "firewall-policies",
        "list, create, or PUT firewall policies for this SCP user",
        "List policy IDs, or create/update a FirewallPolicySave request after strict local JSON validation. "
        "`put` updates an existing policy definition; `firewall SERVER set` separately assigns policy IDs to an interface.\n\n"
        "Examples:\n"
        "  ./scp-api.py firewall-policies\n"
        "  ./scp-api.py firewall-policies create --policy-json '{\"name\":\"ssh\",\"rules\":[]}'\n"
        "  ./scp-api.py firewall-policies put 12 --policy-file firewall-policy.json\n"
        "  ./scp-api.py firewall 799611 set --user-policy-id 12 --active --yes",
    )
    add_actions(p, ("create", "put"), "create: POST a new policy; put: PUT an existing policy definition")
    p.add_argument(
        "policy_id", nargs="?", type=_positive_int, metavar="policy_id",
        help="existing policy ID; required with put, unused for list/create",
    )
    policy_input = p.add_argument_group("policy input for create/put").add_mutually_exclusive_group()
    policy_input.add_argument("--policy-json", metavar="JSON", help="validated FirewallPolicySave JSON object")
    policy_input.add_argument("--policy-file", metavar="PATH", help="file containing validated FirewallPolicySave JSON")
    policy_filters = p.add_argument_group("list filters")
    policy_filters.add_argument("--query", "--filter", dest="query", type=_nonempty_text, metavar="TEXT", help="search policy name/description")
    policy_filters.add_argument("--limit", type=_nonnegative_int, help="maximum number of policies to return")
    policy_filters.add_argument("--offset", type=_nonnegative_int, help="number of matching policies to skip")
    add_confirmation(p, "skip the create/PUT confirmation prompt")

    p = add_subcommand(
        "firewall",
        "get or set firewall policy assignments for one interface",
        "The get action reads the firewall attached to an interface MAC. Omit MAC only when the server has exactly "
        "one interface; the CLI resolves that MAC from live server details. The set action replaces its copied/user "
        "policy assignments and requires an explicit --active or --inactive choice; it does not create or edit policies.\n\n"
        "Examples:\n"
        "  ./scp-api.py firewall 799611 get\n"
        "  ./scp-api.py firewall 799611 set --user-policy-id 12 --active --yes",
    )
    p.add_argument("server_id", type=_positive_int, metavar="server_id", help="Netcup SCP server ID")
    p.add_argument("mac", nargs="?", metavar="mac", help="interface MAC; omitted only when the server has exactly one interface")
    add_actions(p, ("get", "set"), "get: read assignment; set: replace assignment (confirmed)")
    firewall_read = p.add_argument_group("get options")
    firewall_read.add_argument(
        "--consistency-check",
        action="store_true",
        help="with get, ask SCP to compare configured and applied firewall state",
    )
    firewall_set = p.add_argument_group("set options")
    firewall_set.add_argument(
        "--copied-policy-id", dest="copied_policy_ids", type=_positive_int, action="append", default=[], metavar="ID",
        help="repeat for copied policy IDs",
    )
    firewall_set.add_argument(
        "--user-policy-id", dest="user_policy_ids", type=_positive_int, action="append", default=[], metavar="ID",
        help="repeat for user policy IDs",
    )
    active = firewall_set.add_mutually_exclusive_group()
    active.add_argument("--active", dest="active", action="store_true", help="enable the firewall in the replacement")
    active.add_argument("--inactive", dest="active", action="store_false", help="disable the firewall in the replacement")
    p.set_defaults(active=None)
    add_confirmation(p, "skip the confirmation prompt for set")

    p = add_subcommand(
        "power",
        "power on, off, cycle, or reset one server",
        "Control server power state. `off` uses Netcup's POWEROFF option; `cycle` and `reset` use Netcup's "
        "state options. Every action is confirmed unless --yes is supplied.\n\n"
        "Examples:\n"
        "  ./scp-api.py power cycle 799611 --yes\n"
        "  ./scp-api.py power on 799611 --yes",
    )
    add_actions(p, ("on", "off", "cycle", "reset"), "on/off/cycle/reset: selected power operation")
    p.add_argument("server_id", type=_positive_int, metavar="server_id", help="Netcup SCP server ID")
    add_confirmation(p)

    # With no command the most useful response is the top-level usage, not
    # argparse's implementation detail about a required subparser. Print the
    # full help so a bare invocation is a useful discovery command.
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        parser.exit(2)

    args = parser.parse_args()

    # `mac` is optional for the one-interface convenience form. Because it
    # precedes the positional action, argparse initially sees
    # `firewall SERVER get` as mac="get". Normalize that unambiguous spelling
    # here while retaining the explicit `firewall SERVER MAC get` form.
    if args.command == "firewall":
        if args.mac in ("get", "set") and args.action is None:
            args.action = args.mac
            args.mac = None
        if args.mac is not None:
            try:
                args.mac = _mac_address(args.mac)
            except argparse.ArgumentTypeError as e:
                parser.error(f"firewall MAC: {e}")

    return args


def _main() -> int:
    args = parse_args()  # --help exits here, before _configure() ever runs
    # Validate local write/upload inputs before loading settings, refreshing a
    # token, or contacting the API. This makes a typo in a policy/file a local
    # error rather than an authenticated request followed by a provider error.
    try:
        if args.command == "firewall-policies" and args.action in ("create", "put"):
            _load_firewall_policy(args)
            if args.action == "put" and args.policy_id is None:
                raise ValueError("firewall-policies put requires a policy_id")
        elif args.command == "user-iso" and args.action == "upload":
            _user_iso_file(args)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    _configure()

    if args.command == "login":
        return cmd_login(args)

    pal = _Palette(_color_enabled(args.no_color))
    client = build_client()

    dispatch = {
        "servers": cmd_servers,
        "status": cmd_status,
        "server-details": cmd_server_details,
        "imageflavours": cmd_imageflavours,
        "iso-bootable": cmd_iso_bootable,
        "iso-attached": cmd_attached_iso,
        "attach-iso": cmd_attach_iso,
        "disks": cmd_disks,
        "rescuesystem": cmd_rescuesystem,
        "snapshots": cmd_snapshots,
        "tasks": cmd_tasks,
        "metrics": cmd_metrics,
        "guest-agent-status": cmd_guest_agent_status,
        "user-iso": cmd_user_iso,
        "firewall-policies": cmd_firewall_policies,
        "firewall": cmd_firewall,
        "power": cmd_power,
    }
    try:
        dispatch[args.command](client, args, pal)
    except Exception as e:
        # e.g. a bare RuntimeError from _http_json on a network/DNS
        # failure -- _api_call() only catches HTTPStatusError, so anything
        # else reaching here would otherwise dump a raw traceback instead
        # of a clean message (matches install-host.py's own
        # top-level Exception handling in main()).
        print(f"ERROR: {e}", file=sys.stderr)
        if netcup_scp_client.DEBUG:
            import traceback
            traceback.print_exc()
        return 1
    return 0


def main() -> int:
    """Run the CLI and turn an intentional Ctrl-C into a clean exit."""
    try:
        return _main()
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
