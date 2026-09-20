#!/usr/bin/env python3
"""Explore and safely modify the netcup SCP API account/server surface.

First-class exploration, not manual curl+jq: this is exactly what
scp-api-install-host.py used to do ad hoc for imageflavours only (query,
filter, print a numbered list) -- generalized to the rest of the
"pre-install recon" resource set (servers, imageflavours, iso-bootable, disks,
rescuesystem status, snapshots, tasks, metrics, guest-agent status, and
firewall assignment), plus explicitly confirmed actions (ISO attach/detach,
task cancel, rescue-system deactivate, snapshot create/dryrun-check, power
operations, and firewall assignment). Deliberately does NOT expose: disk
format, image setup (server reinstall), snapshot revert, or firewall policy
creation/editing -- those need a dedicated, deliberate flow.

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
  scp-api.py firewall-policies [--query TEXT]
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
  scp-api.py firewall-policies
  scp-api.py firewall 799611 aa:bb:cc:dd:ee:ff get
  scp-api.py firewall 799611 aa:bb:cc:dd:ee:ff set --user-policy-id 12 --active
  scp-api.py power cycle 799611

Verb groups:
  authentication: login
  exploration: servers, server-details, imageflavours, iso-bootable,
                iso-attached, disks, rescuesystem, snapshots, tasks,
                metrics, guest-agent-status, firewall-policies, firewall get
  modification: attach-iso, iso-attached <server_id> detach,
               rescuesystem <server_id> deactivate, snapshots <server_id>
               create, tasks <uuid> cancel, firewall <server_id> [mac] set,
               power {on,off,cycle,reset}

Every subcommand accepts --json for raw machine output; without it, output
is a pretty, optionally-colored table/summary sized for a terminal. Color
auto-detects a TTY and respects NO_COLOR (https://no-color.org/) and
--no-color.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
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


def _mac_address(value: str) -> str:
    if not _MAC_ADDRESS_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a MAC address such as aa:bb:cc:dd:ee:ff")
    return value


def _nonempty_text(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be empty or whitespace")
    return value


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

    if args.uuid is not None and params is not None:
        print("ERROR: task filters are only valid when listing tasks, not with a task UUID", file=sys.stderr)
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


def cmd_firewall_policies(client: NetcupSCPClient, args, pal: _Palette) -> None:
    user_info = _response_dict(_api_call(client.get_user_info), "GET OIDC userinfo")
    user_id = user_info.get("id")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise ResponseShapeError("GET OIDC userinfo: response did not contain a positive integer user id")
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


def cmd_login(args) -> int:
    return run_device_code_login(netcup_scp_client.resolve_env_path())


# --- argument parsing --------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Explore and safely modify the netcup SCP API account/server surface.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument("--json", action="store_true", help="print raw JSON instead of a formatted table/summary")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI color even on a TTY")

    sub = parser.add_subparsers(dest="command", required=True, metavar="VERB")

    def add_subcommand(name: str, help_text: str, description: str = ""):
        command_parser = sub.add_parser(
            name,
            description=description or help_text,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            add_help=False,
        )
        command_parser.add_argument("--help", action="help", help="show this help message and exit")
        # Keep the documented `command --json` spelling working as well as
        # the global `--json command` spelling. SUPPRESS avoids an omitted
        # subcommand option overwriting a global one.
        command_parser.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS,
            help="print raw JSON instead of a formatted table/summary",
        )
        command_parser.add_argument(
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

    add_subcommand(
        "login",
        "OAuth2 device-code login (writes NETCUP_SCP_API_REFRESH_TOKEN to .env)",
        "Obtain the long-lived refresh token through the browser device-code flow.\n\n"
        "Example:\n  ./scp-api.py login",
    )

    add_subcommand(
        "servers",
        "list all known servers",
        "List the account's server inventory.\n\nExample:\n  ./scp-api.py servers",
    )
    p = add_subcommand(
        "server-details",
        "show detailed information for one server",
        "Show the complete API record for one server.\n\nExample:\n  ./scp-api.py server-details 799611",
    )
    p.add_argument("server_id", type=_positive_int, metavar="server_id")

    for name, help_text, description in [
        (
            "imageflavours",
            "list reinstallable OS/image flavours (all servers unless an ID is given)",
            "An image flavour is a server-compatible reinstallable OS/image variant, "
            "for example a Debian 13 UEFI amd64 image. It is not a VM template.\n\n"
            "Example:\n  ./scp-api.py imageflavours --filter debian",
        ),
        (
            "iso-bootable",
            "list available ISO images (all servers unless an ID is given)",
            "ISO images are bootable installer/recovery media exposed by SCP. "
            "Use --filter debian or --filter rescue to narrow the names and descriptions.\n\n"
            "Example:\n  ./scp-api.py iso-bootable --filter rescue",
        ),
    ]:
        p = add_subcommand(name, help_text, description)
        p.add_argument("server_id", nargs="?", type=_positive_int, default=None, metavar="server_id")
        p.add_argument("--filter", type=_nonempty_text, metavar="TEXT", help="case-insensitive text filter across returned fields")

    p = add_subcommand(
        "iso-attached",
        "show attached ISOs for all servers, or one server",
        "Show the ISO currently attached to each server; add the detach action to remove one.\n\n"
        "Example:\n  ./scp-api.py iso-attached 799611",
    )
    p.add_argument("server_id", nargs="?", type=_positive_int, default=None, metavar="server_id")
    add_actions(p, ("detach",), "detach: remove the currently-attached ISO (safe/reversible)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = add_subcommand(
        "attach-iso",
        "attach a bootable or user ISO to one server",
        "Attach an ISO by ID from iso-bootable, or attach a user-uploaded ISO by name. "
        "This changes the server's attached media and always asks for confirmation.\n\n"
        "Example:\n  ./scp-api.py attach-iso 799611 --iso-id 1234",
    )
    p.add_argument("server_id", type=_positive_int, metavar="server_id")
    iso_source = p.add_mutually_exclusive_group(required=True)
    iso_source.add_argument("--iso-id", type=_positive_int, help="ID returned by iso-bootable")
    iso_source.add_argument("--user-iso-name", type=_nonempty_text, metavar="NAME", help="name of an ISO uploaded to the account")
    p.add_argument(
        "--change-boot-device-to-cdrom",
        action="store_true",
        help="also make the virtual CD-ROM the next boot device",
    )
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = add_subcommand(
        "disks",
        "list disks for all servers, or one server",
        "List disk capacity/allocation and storage drivers.\n\n"
        "Example:\n  ./scp-api.py disks 799611",
    )
    p.add_argument("server_id", nargs="?", type=_positive_int, default=None, metavar="server_id")
    add_actions(p, ("supported-drivers",), "supported-drivers: list storage drivers for one server")

    p = add_subcommand(
        "rescuesystem",
        "show rescue-system status for all servers, or one server",
        "Show whether Netcup's provider-managed emergency rescue environment is active; "
        "this is separate from an arbitrary attached ISO. Add deactivate to turn it off.\n\n"
        "Example:\n  ./scp-api.py rescuesystem 799611",
    )
    p.add_argument("server_id", nargs="?", type=_positive_int, default=None, metavar="server_id")
    add_actions(p, ("deactivate",), "deactivate: turn off the rescue system (safe/reversible)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = add_subcommand(
        "snapshots",
        "list snapshots for all servers, or act on one server",
        "List snapshots, or use create/dryrun for one server.\n\n"
        "Example:\n  ./scp-api.py snapshots 799611",
    )
    p.add_argument("server_id", nargs="?", type=_positive_int, default=None, metavar="server_id")
    add_actions(
        p,
        ("create", "dryrun"),
        "create: create a snapshot; dryrun: check whether snapshot creation is possible",
    )
    p.add_argument("--name", default=None, help="optional name for the create action")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = add_subcommand(
        "tasks",
        "list tasks, show one, or cancel one",
        "List tasks, show one by UUID, or use cancel with a UUID.\n\n"
        "Example:\n  ./scp-api.py tasks",
    )
    p.add_argument("uuid", nargs="?", type=_nonempty_text, default=None)
    add_actions(p, ("cancel",), "cancel: cancel a running task (does not undo whatever it already did)")
    p.add_argument(
        "--query", "--filter", dest="query", type=_nonempty_text, metavar="TEXT",
        help="list tasks whose name, UUID, or server fields contain TEXT (API q filter)",
    )
    p.add_argument("--server-id", dest="server_filter_id", type=_positive_int, metavar="ID", help="list tasks for one server")
    p.add_argument(
        "--state",
        choices=_TASK_STATES,
        help="list tasks in one state (ROLLBACK is not supported by the API filter)",
    )
    p.add_argument("--limit", type=_nonnegative_int, help="maximum number of tasks to return")
    p.add_argument("--offset", type=_nonnegative_int, help="number of matching tasks to skip")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = add_subcommand(
        "metrics",
        "show CPU, disk, or network metrics for one server",
        "Return timestamped SCP metrics. The API's hours value is a lookback window, not a sample interval.\n\n"
        "Example:\n  ./scp-api.py metrics 799611 cpu --hours 24",
    )
    p.add_argument("server_id", type=_positive_int, metavar="server_id")
    p.add_argument("metric", choices=tuple(_METRIC_ENDPOINTS), metavar="{cpu,disk,network,network-packet}")
    p.add_argument("--hours", type=_hours, help="look back this many hours (1-1440; API default if omitted)")

    p = add_subcommand(
        "guest-agent-status",
        "show the QEMU guest-agent status for one server",
        "Read whether the guest agent is available; this describes agent reachability, not SSH or bootstrap state.\n\n"
        "Example:\n  ./scp-api.py guest-agent-status 799611",
    )
    p.add_argument("server_id", type=_positive_int, metavar="server_id")

    p = add_subcommand(
        "firewall-policies",
        "list existing firewall policies available to this SCP user",
        "List policy IDs that can be assigned with firewall set. This reads policies; it does not create or edit them.\n\n"
        "Example:\n  ./scp-api.py firewall-policies",
    )
    p.add_argument("--query", "--filter", dest="query", type=_nonempty_text, metavar="TEXT", help="search policy name/description")
    p.add_argument("--limit", type=_nonnegative_int, help="maximum number of policies to return")
    p.add_argument("--offset", type=_nonnegative_int, help="number of matching policies to skip")

    p = add_subcommand(
        "firewall",
        "get or set firewall policy assignments for one interface",
        "The get action reads the firewall attached to an interface MAC. Omit MAC only when the server has exactly "
        "one interface; the CLI resolves that MAC from live server details. The set action replaces its copied/user "
        "policy assignments and requires an explicit --active or --inactive choice; it does not create or edit policies.\n\n"
        "Example:\n  ./scp-api.py firewall 799611 get",
    )
    p.add_argument("server_id", type=_positive_int, metavar="server_id")
    p.add_argument("mac", nargs="?", metavar="mac", help="interface MAC; omitted only when the server has exactly one interface")
    add_actions(p, ("get", "set"), "get: read assignment; set: replace assignment (confirmed)")
    p.add_argument(
        "--consistency-check",
        action="store_true",
        help="with get, ask SCP to compare configured and applied firewall state",
    )
    p.add_argument(
        "--copied-policy-id", dest="copied_policy_ids", type=_positive_int, action="append", default=[], metavar="ID",
        help="repeat for copied policy IDs",
    )
    p.add_argument(
        "--user-policy-id", dest="user_policy_ids", type=_positive_int, action="append", default=[], metavar="ID",
        help="repeat for user policy IDs",
    )
    active = p.add_mutually_exclusive_group()
    active.add_argument("--active", dest="active", action="store_true", help="enable the firewall in the replacement")
    active.add_argument("--inactive", dest="active", action="store_false", help="disable the firewall in the replacement")
    p.set_defaults(active=None)
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt for set")

    p = add_subcommand(
        "power",
        "power on, off, cycle, or reset one server",
        "Control server power state. `off` uses Netcup's POWEROFF option; `cycle` and `reset` use Netcup's "
        "state options. Every action is confirmed unless --yes is supplied.\n\n"
        "Example:\n  ./scp-api.py power cycle 799611",
    )
    add_actions(p, ("on", "off", "cycle", "reset"), "on/off/cycle/reset: selected power operation")
    p.add_argument("server_id", type=_positive_int, metavar="server_id")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

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
