#!/usr/bin/env python3
"""Explore and safely modify the netcup SCP API account/server surface.

First-class exploration, not manual curl+jq: this is exactly what
scp-api-install-host.py used to do ad hoc for imageflavours only (query,
filter, print a numbered list) -- generalized to the rest of the
"pre-install recon" resource set (servers, imageflavours, iso-bootable, disks,
rescuesystem status, snapshots, tasks, metrics, guest-agent status, and
firewall assignment), plus explicitly confirmed actions (ISO attach/detach,
task cancel, rescue-system deactivate, snapshot create/dryrun-check, power
operations, firewall policy create/PUT, firewall assignment, and user-ISO
upload). Deliberately does NOT expose: disk format, image setup (server
reinstall), or snapshot revert.

Auth/settings: shares netcup_scp_client.py with scp-api-install-host.py
(same .env-sourced NETCUP_SCP_API_REFRESH_TOKEN, same OAuth2 device-code
`login` support) but its own tiny scp-api.toml for
base_url/keycloak_url (see that file's own comment for why it's not
shared with scp-api-install-host.toml).

Usage:
  scp-api.py login
  scp-api.py servers [--json]
  scp-api.py server-details <server_id> [--json]
  scp-api.py imageflavours [server_id] [--filter TEXT] [--json]
  scp-api.py iso-bootable [server_id] [--filter TEXT] [--json]
  scp-api.py iso-attached [server_id] [detach] [--yes]
  scp-api.py attach-iso <server_id> (--iso-id ID | --user-iso-name NAME) [--yes]
  scp-api.py disks [server_id] [supported-drivers]
  scp-api.py rescuesystem [server_id] [deactivate] [--yes]
  scp-api.py snapshots [server_id] [create|dryrun] [--name NAME] [--yes]
  scp-api.py tasks [uuid] [cancel] [--query TEXT] [--server-id ID]
                  [--state STATE] [--limit N] [--offset N]
  scp-api.py metrics <server_id> {cpu,disk,network,network-packet} [--hours N]
  scp-api.py guest-agent-status <server_id>
  scp-api.py user-iso [upload FILE] [--name KEY] [--multipart]
  scp-api.py firewall-policies [create|put] [policy_id] [--policy-json JSON | --policy-file PATH]
  scp-api.py firewall <server_id> [mac] [get|set] [options]
  scp-api.py power {on,off,cycle,reset} <server_id> [--yes]

Examples:
  scp-api.py login
  scp-api.py servers
  scp-api.py server-details 799611 --json
  scp-api.py imageflavours --filter debian
  scp-api.py iso-bootable --filter rescue
  scp-api.py iso-attached 799611
  scp-api.py attach-iso 799611 --iso-id 1234
  scp-api.py disks 799611 supported-drivers
  scp-api.py rescuesystem 799611
  scp-api.py snapshots 799611
  scp-api.py tasks --state RUNNING --server-id 799611
  scp-api.py metrics 799611 cpu --hours 24
  scp-api.py guest-agent-status 799611
  scp-api.py user-iso
  scp-api.py user-iso upload ./custom.iso --yes
  scp-api.py firewall-policies
  scp-api.py firewall-policies create --policy-file firewall-policy.json --yes
  scp-api.py firewall 799611 aa:bb:cc:dd:ee:ff get
  scp-api.py firewall 799611 aa:bb:cc:dd:ee:ff set --user-policy-id 12 --active
  scp-api.py power cycle 799611

Verb groups:
  authentication: login
  exploration: servers, server-details, imageflavours, iso-bootable,
                iso-attached, disks, rescuesystem, snapshots, tasks,
                metrics, guest-agent-status, user-iso, firewall-policies,
                firewall get
  modification: attach-iso, iso-attached <server_id> detach,
               rescuesystem <server_id> deactivate, snapshots <server_id>
               create, tasks <uuid> cancel, user-iso upload,
               firewall-policies create/put, firewall <server_id> [mac] set,
               power {on,off,cycle,reset}

Every subcommand accepts --json for raw machine output; without it, output
is a pretty, optionally-colored table/summary sized for a terminal. Color
auto-detects a TTY and respects NO_COLOR (https://no-color.org/) and
--no-color.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
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


def _configure() -> None:
    """Load .env + settings and wire netcup_scp_client's module globals.

    Deliberately NOT run at import time (unlike the pre-existing pattern in
    scp-api-install-host.py, which does this eagerly and consequently can't
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
# (screen-clear/color/cursor-move codes), and an embedded newline breaks
# table column alignment across rows, not just cosmetically. --json mode
# is unaffected either way: json.dumps already \u-escapes control bytes.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize(text: str) -> str:
    return _CONTROL_CHARS_RE.sub("", text)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        # One level of nesting flattened for table cells (e.g. imageflavour.image.name)
        return _sanitize(json.dumps(value, separators=(",", ":")))
    return _sanitize(str(value))


def print_table(rows: List[Dict[str, Any]], columns: List[str], pal: _Palette, empty_message: str) -> None:
    rows = _response_rows(rows, "formatted table")
    if not rows:
        print(pal.dim(empty_message))
        return
    cells = [[_stringify(row.get(col)) for col in columns] for row in rows]
    widths = [max(len(columns[i]), *(len(r[i]) for r in cells)) for i in range(len(columns))]
    header = "  ".join(pal.bold(columns[i].ljust(widths[i])) for i in range(len(columns)))
    print(header)
    print(pal.dim("  ".join("-" * widths[i] for i in range(len(columns)))))
    for r in cells:
        print("  ".join(r[i].ljust(widths[i]) for i in range(len(columns))))


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
        eligible.append({"name": name, "id": server_id})

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

def parse_args():
    parser = argparse.ArgumentParser(
        description="Explore and safely modify the netcup SCP API account/server surface.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
        add_help=False,
    )
    help_options = parser.add_argument_group("help")
    help_options.add_argument("--help", action="help", help="show this help message and exit")
    output_options = parser.add_argument_group("output options")
    output_options.add_argument("--json", action="store_true", help="print raw JSON instead of a formatted table/summary")
    output_options.add_argument("--no-color", action="store_true", help="disable ANSI color even on a TTY")

    sub = parser.add_subparsers(dest="command", required=True, title="verbs")

    def add_subcommand(name: str, help_text: str, description: str = ""):
        command_parser = sub.add_parser(
            name,
            description=description or help_text,
            formatter_class=argparse.RawDescriptionHelpFormatter,
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


def main() -> int:
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
        # of a clean message (matches scp-api-install-host.py's own
        # top-level Exception handling in main()).
        print(f"ERROR: {e}", file=sys.stderr)
        if netcup_scp_client.DEBUG:
            import traceback
            traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
