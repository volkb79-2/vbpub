#!/usr/bin/env python3
"""Explore and safely modify the netcup SCP API account/server surface.

First-class exploration, not manual curl+jq: this is exactly what
scp-api-install-host.py used to do ad hoc for imageflavours only (query,
filter, print a numbered list) -- generalized to the rest of the
"pre-install recon" resource set (servers, imageflavours, iso-bootable, disks,
rescuesystem status, snapshots, tasks), plus a handful of paired SAFE
mutating actions (task cancel, ISO detach, rescue-system deactivate,
snapshot create/dryrun-check) named explicitly because they're reversible/
non-destructive. Deliberately does NOT expose: disk format, image setup
(server reinstall), or snapshot revert -- those are genuinely destructive
and belong in a dedicated, deliberate flow, not a recon tool's convenience
actions.

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
  scp-api.py disks [server_id] [supported-drivers]
  scp-api.py rescuesystem [server_id] [deactivate] [--yes]
  scp-api.py snapshots [server_id] [create|dryrun] [--name NAME] [--yes]
  scp-api.py tasks [uuid] [cancel] [--yes]
  scp-api.py {power-on,power-off,power-cycle,reset} <server_id> [--yes]

Examples:
  scp-api.py login
  scp-api.py servers
  scp-api.py server-details 799611 --json
  scp-api.py imageflavours --filter debian
  scp-api.py iso-bootable --filter rescue
  scp-api.py iso-attached 799611
  scp-api.py disks 799611 supported-drivers
  scp-api.py rescuesystem 799611
  scp-api.py snapshots 799611
  scp-api.py tasks
  scp-api.py power-cycle 799611

Verb groups:
  authentication: login
  exploration: servers, server-details, imageflavours, iso-bootable,
                iso-attached, disks, rescuesystem, snapshots, tasks
  modification: iso-attached <server_id> detach, rescuesystem <server_id>
               deactivate, snapshots <server_id> create, tasks <uuid> cancel,
               power-on, power-off, power-cycle, reset

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
    servers = _api_call(client.get, "/api/v1/servers")
    return servers if isinstance(servers, list) else []


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


def _require_server_id(args, action: str) -> int:
    if args.server_id is None:
        print(f"ERROR: {action} requires a server ID", file=sys.stderr)
        raise SystemExit(2)
    return args.server_id


# --- subcommands -------------------------------------------------------------

def cmd_servers(client: NetcupSCPClient, args, pal: _Palette) -> None:
    servers = _api_call(client.get, "/api/v1/servers")
    emit(servers, args.json, lambda d: print_table(
        d, ["id", "hostname", "nickname", "name", "disabled"], pal, "no servers on this account"
    ))


def cmd_server_details(client: NetcupSCPClient, args, pal: _Palette) -> None:
    server = _api_call(client.get, f"/api/v1/servers/{args.server_id}")
    emit(server, args.json, lambda d: print_kv(d, pal))


def cmd_imageflavours(client: NetcupSCPClient, args, pal: _Palette) -> None:
    targets = _server_targets(client, args.server_id)
    aggregate = args.server_id is None
    flavours: List[Dict[str, Any]] = []
    for server in targets:
        result = _api_call(client.get, f"/api/v1/servers/{server['id']}/imageflavours")
        rows = result if isinstance(result, list) else []
        flavours.extend(_annotate_server_row(row, server) if aggregate else row for row in rows)
    flavours = _filter_rows(flavours, getattr(args, "filter", None))

    def table(rows):
        flat = [
            {
                "serverId": r.get("serverId"),
                "serverName": r.get("serverName"),
                "id": r.get("id"),
                "name": (r.get("image") or {}).get("name"),
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
        rows = result if isinstance(result, list) else []
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
        attached = _api_call(client.get, f"/api/v1/servers/{args.server_id}/iso")
        emit(attached, args.json, lambda d: print_kv(d, pal) if d else print(pal.dim("no ISO currently attached")))
        return
    rows = []
    for server in _server_targets(client, None):
        attached = _api_call(client.get, f"/api/v1/servers/{server['id']}/iso")
        row = attached if isinstance(attached, dict) else {}
        rows.append(_annotate_server_row(row, server))
    emit(rows, args.json, lambda d: print_table(
        d, ["serverId", "serverName", "isoAttached", "iso"], pal, "no servers on this account"
    ))


def cmd_disks(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.action == "supported-drivers":
        server_id = _require_server_id(args, "supported-drivers")
        drivers = _api_call(client.get, f"/api/v1/servers/{server_id}/disks/supported-drivers")
        emit(drivers, args.json, lambda d: print(", ".join(d) if d else pal.dim("none reported")))
        return
    if args.server_id is not None:
        disks = _api_call(client.get, f"/api/v1/servers/{args.server_id}/disks")
        emit(disks, args.json, lambda d: print_table(
            d, ["name", "capacityInMiB", "allocationInMiB", "storageDriver"], pal, "no disks reported"
        ))
        return
    rows = []
    for server in _server_targets(client, None):
        disks = _api_call(client.get, f"/api/v1/servers/{server['id']}/disks")
        rows.extend(_annotate_server_row(row, server) for row in disks if isinstance(row, dict))
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
        status = _api_call(client.get, f"/api/v1/servers/{args.server_id}/rescuesystem")
        emit(status, args.json, lambda d: print_kv(d, pal))
        return
    rows = []
    for server in _server_targets(client, None):
        status = _api_call(client.get, f"/api/v1/servers/{server['id']}/rescuesystem")
        rows.append(_annotate_server_row(status if isinstance(status, dict) else {}, server))
    emit(rows, args.json, lambda d: print_table(
        d, ["serverId", "serverName", "active"], pal, "no servers on this account"
    ))


def cmd_snapshots(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.action == "dryrun":
        server_id = _require_server_id(args, "dryrun")
        result = _api_call(client.post, f"/api/v1/servers/{server_id}/snapshots:dryrun", {})
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
        result = _api_call(client.post, f"/api/v1/servers/{server_id}/snapshots", {"name": name})
        emit(result, args.json, lambda d: print_kv(d, pal))
        return
    if args.server_id is not None:
        snapshots = _api_call(client.get, f"/api/v1/servers/{args.server_id}/snapshots")
        emit(snapshots, args.json, lambda d: print_table(
            d, ["uuid", "name", "state", "online", "creationTime"], pal, "no snapshots for this server"
        ))
        return
    rows = []
    for server in _server_targets(client, None):
        snapshots = _api_call(client.get, f"/api/v1/servers/{server['id']}/snapshots")
        rows.extend(_annotate_server_row(row, server) for row in snapshots if isinstance(row, dict))
    emit(rows, args.json, lambda d: print_table(
        d, ["serverId", "serverName", "uuid", "name", "state", "online", "creationTime"], pal,
        "no snapshots on this account"
    ))


def cmd_tasks(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.action == "cancel" and args.uuid is None:
        print("ERROR: cancel requires a task uuid", file=sys.stderr)
        sys.exit(2)
    if args.uuid is None:
        tasks = _api_call(client.get, "/api/v1/tasks")
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
    task = _api_call(client.get, f"/api/v1/tasks/{args.uuid}")
    emit(task, args.json, lambda d: print_kv(d, pal))


_POWER_ACTIONS = {
    "power-on": ("ON", None, "Power on"),
    "power-off": ("OFF", "POWEROFF", "Power off"),
    "power-cycle": ("ON", "POWERCYCLE", "Power-cycle"),
    "reset": ("ON", "RESET", "Reset"),
}


def cmd_power(client: NetcupSCPClient, args, pal: _Palette) -> None:
    state, state_option, label = _POWER_ACTIONS[args.command]
    if not confirm(f"{label} server {args.server_id}?", args.yes, pal):
        print("aborted")
        return
    params = {"stateOption": state_option} if state_option else None
    result = _api_call(
        client.patch,
        f"/api/v1/servers/{args.server_id}",
        {"state": state},
        params=params,
    )
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
    p.add_argument("server_id", type=int, metavar="server_id")

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
        p.add_argument("server_id", nargs="?", type=int, default=None, metavar="server_id")
        p.add_argument("--filter", metavar="TEXT", help="case-insensitive text filter across returned fields")

    p = add_subcommand(
        "iso-attached",
        "show attached ISOs for all servers, or one server",
        "Show the ISO currently attached to each server; add the detach action to remove one.\n\n"
        "Example:\n  ./scp-api.py iso-attached 799611",
    )
    p.add_argument("server_id", nargs="?", type=int, default=None, metavar="server_id")
    add_actions(p, ("detach",), "detach: remove the currently-attached ISO (safe/reversible)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = add_subcommand(
        "disks",
        "list disks for all servers, or one server",
        "List disk capacity/allocation and storage drivers.\n\n"
        "Example:\n  ./scp-api.py disks 799611",
    )
    p.add_argument("server_id", nargs="?", type=int, default=None, metavar="server_id")
    add_actions(p, ("supported-drivers",), "supported-drivers: list storage drivers for one server")

    p = add_subcommand(
        "rescuesystem",
        "show rescue-system status for all servers, or one server",
        "Show whether the rescue system is active; add deactivate to turn it off.\n\n"
        "Example:\n  ./scp-api.py rescuesystem 799611",
    )
    p.add_argument("server_id", nargs="?", type=int, default=None, metavar="server_id")
    add_actions(p, ("deactivate",), "deactivate: turn off the rescue system (safe/reversible)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = add_subcommand(
        "snapshots",
        "list snapshots for all servers, or act on one server",
        "List snapshots, or use create/dryrun for one server.\n\n"
        "Example:\n  ./scp-api.py snapshots 799611",
    )
    p.add_argument("server_id", nargs="?", type=int, default=None, metavar="server_id")
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
    p.add_argument("uuid", nargs="?", default=None)
    add_actions(p, ("cancel",), "cancel: cancel a running task (does not undo whatever it already did)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    for command, description in [
        ("power-on", "Power on one server.\n\nExample:\n  ./scp-api.py power-on 799611"),
        ("power-off", "Power off one server.\n\nExample:\n  ./scp-api.py power-off 799611"),
        ("power-cycle", "Power-cycle one server.\n\nExample:\n  ./scp-api.py power-cycle 799611"),
        ("reset", "Reset one server.\n\nExample:\n  ./scp-api.py reset 799611"),
    ]:
        p = add_subcommand(command, description)
        p.add_argument("server_id", type=int, metavar="server_id")
        p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    # With no command the most useful response is the top-level usage, not
    # argparse's implementation detail about a required subparser. Print the
    # full help so a bare invocation is a useful discovery command.
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        parser.exit(2)

    return parser.parse_args()


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
        "disks": cmd_disks,
        "rescuesystem": cmd_rescuesystem,
        "snapshots": cmd_snapshots,
        "tasks": cmd_tasks,
        "power-on": cmd_power,
        "power-off": cmd_power,
        "power-cycle": cmd_power,
        "reset": cmd_power,
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
