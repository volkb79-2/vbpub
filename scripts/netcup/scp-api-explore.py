#!/usr/bin/env python3
"""Read-only(-ish) exploration of the netcup SCP API account/server surface.

First-class exploration, not manual curl+jq: this is exactly what
scp-api-install-host.py used to do ad hoc for imageflavours only (query,
filter, print a numbered list) -- generalized to the rest of the
"pre-install recon" resource set (servers, imageflavours, isoimages, disks,
rescuesystem status, snapshots, tasks), plus a handful of paired SAFE
mutating actions (task cancel, ISO detach, rescue-system deactivate,
snapshot create/dryrun-check) named explicitly because they're reversible/
non-destructive. Deliberately does NOT expose: disk format, image setup
(server reinstall), or snapshot revert -- those are genuinely destructive
and belong in a dedicated, deliberate flow, not a recon tool's convenience
actions.

Auth/settings: shares netcup_scp_client.py with scp-api-install-host.py
(same .env-sourced NETCUP_SCP_API_REFRESH_TOKEN, same OAuth2 device-code
`login` support) but its own tiny scp-api-explore.toml for
base_url/keycloak_url (see that file's own comment for why it's not
shared with scp-api-install-host.toml).

Usage:
  scp-api-explore.py login                          # same device-code flow as scp-api-install-host.py
  scp-api-explore.py servers [--json]
  scp-api-explore.py servers <id> [--json]
  scp-api-explore.py imageflavours <id> [--json]
  scp-api-explore.py isoimages <id> [--json]
  scp-api-explore.py iso <id> [--json] [--detach [--yes]]
  scp-api-explore.py disks <id> [--json] [--supported-drivers]
  scp-api-explore.py rescuesystem <id> [--json] [--deactivate [--yes]]
  scp-api-explore.py snapshots <id> [--json] [--create [--yes]] [--dryrun]
  scp-api-explore.py tasks [uuid] [--json] [--cancel [--yes]]

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

SETTINGS_PATH = Path(__file__).resolve().parent / "scp-api-explore.toml"


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
        print("ERROR: missing $NETCUP_SCP_API_REFRESH_TOKEN -- run: scp-api-explore.py login", file=sys.stderr)
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


# --- subcommands -------------------------------------------------------------

def cmd_servers(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.server_id is None:
        servers = _api_call(client.get, "/api/v1/servers")
        emit(servers, args.json, lambda d: print_table(
            d, ["id", "hostname", "nickname", "name", "disabled"], pal, "no servers on this account"
        ))
        return
    server = _api_call(client.get, f"/api/v1/servers/{args.server_id}")
    emit(server, args.json, lambda d: print_kv(d, pal))


def cmd_imageflavours(client: NetcupSCPClient, args, pal: _Palette) -> None:
    flavours = _api_call(client.get, f"/api/v1/servers/{args.server_id}/imageflavours")

    def table(rows):
        flat = [{"id": r.get("id"), "name": (r.get("image") or {}).get("name"), "alias": r.get("alias")} for r in rows]
        print_table(flat, ["id", "name", "alias"], pal, "no image flavours available for this server")

    emit(flavours, args.json, table)


def cmd_isoimages(client: NetcupSCPClient, args, pal: _Palette) -> None:
    images = _api_call(client.get, f"/api/v1/servers/{args.server_id}/isoimages")
    emit(images, args.json, lambda d: print_table(
        d, ["id", "name", "description", "architecture"], pal, "no ISO images available for this server"
    ))


def cmd_iso(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.detach:
        if not confirm(f"Detach the ISO currently attached to server {args.server_id}?", args.yes, pal):
            print("aborted")
            return
        _api_call(client.delete, f"/api/v1/servers/{args.server_id}/iso")
        print(pal.green("detached"))
        return
    attached = _api_call(client.get, f"/api/v1/servers/{args.server_id}/iso")
    emit(attached, args.json, lambda d: print_kv(d, pal) if d else print(pal.dim("no ISO currently attached")))


def cmd_disks(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.supported_drivers:
        drivers = _api_call(client.get, f"/api/v1/servers/{args.server_id}/disks/supported-drivers")
        emit(drivers, args.json, lambda d: print(", ".join(d) if d else pal.dim("none reported")))
        return
    disks = _api_call(client.get, f"/api/v1/servers/{args.server_id}/disks")
    emit(disks, args.json, lambda d: print_table(
        d, ["name", "capacityInMiB", "allocationInMiB", "storageDriver"], pal, "no disks reported"
    ))


def cmd_rescuesystem(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.deactivate:
        if not confirm(f"Deactivate the rescue system for server {args.server_id}?", args.yes, pal):
            print("aborted")
            return
        _api_call(client.delete, f"/api/v1/servers/{args.server_id}/rescuesystem")
        print(pal.green("deactivated"))
        return
    status = _api_call(client.get, f"/api/v1/servers/{args.server_id}/rescuesystem")
    emit(status, args.json, lambda d: print_kv(d, pal))


def cmd_snapshots(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.dryrun:
        result = _api_call(client.post, f"/api/v1/servers/{args.server_id}/snapshots:dryrun", {})
        emit(result, args.json, lambda d: print_kv(d, pal))
        return
    if args.create:
        if not confirm(f"Create a new snapshot of server {args.server_id}?", args.yes, pal):
            print("aborted")
            return
        # name is REQUIRED by the API (ServerSnapshotCreate schema) -- an
        # omitted --name previously sent {} and always failed server-side
        # AFTER the confirm prompt (confirmed against the OpenAPI spec,
        # 2026-09-09). Default to a timestamp rather than forcing --name
        # on every call.
        name = args.name or f"vbpub-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        result = _api_call(client.post, f"/api/v1/servers/{args.server_id}/snapshots", {"name": name})
        emit(result, args.json, lambda d: print_kv(d, pal))
        return
    snapshots = _api_call(client.get, f"/api/v1/servers/{args.server_id}/snapshots")
    emit(snapshots, args.json, lambda d: print_table(
        d, ["uuid", "name", "state", "online", "creationTime"], pal, "no snapshots for this server"
    ))


def cmd_tasks(client: NetcupSCPClient, args, pal: _Palette) -> None:
    if args.cancel and args.uuid is None:
        print("ERROR: --cancel requires a task uuid", file=sys.stderr)
        sys.exit(2)
    if args.uuid is None:
        tasks = _api_call(client.get, "/api/v1/tasks")
        emit(tasks, args.json, lambda d: print_table(
            d, ["uuid", "name", "state", "startedAt", "finishedAt"], pal, "no tasks found"
        ))
        return
    if args.cancel:
        if not confirm(f"Cancel task {args.uuid}?", args.yes, pal):
            print("aborted")
            return
        _api_call(client.put, f"/api/v1/tasks/{args.uuid}:cancel")
        print(pal.green("cancel requested"))
        return
    task = _api_call(client.get, f"/api/v1/tasks/{args.uuid}")
    emit(task, args.json, lambda d: print_kv(d, pal))


def cmd_login(args) -> int:
    return run_device_code_login(Path(__file__).resolve().parent / ".env")


# --- argument parsing --------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Explore the netcup SCP API account/server surface (read-only + a few safe paired actions).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--json", action="store_true", help="print raw JSON instead of a formatted table/summary")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI color even on a TTY")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="OAuth2 device-code login (writes NETCUP_SCP_API_REFRESH_TOKEN to .env)")

    p = sub.add_parser("servers", help="list servers, or show one server's detail")
    p.add_argument("server_id", nargs="?", type=int, default=None)

    for name, help_text in [
        ("imageflavours", "list image flavours available for a server"),
        ("isoimages", "list ISO images available for a server"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("server_id", type=int)

    p = sub.add_parser("iso", help="show (or detach) the ISO attached to a server")
    p.add_argument("server_id", type=int)
    p.add_argument("--detach", action="store_true", help="detach the currently-attached ISO (safe/reversible)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = sub.add_parser("disks", help="list a server's disks (or its supported storage drivers)")
    p.add_argument("server_id", type=int)
    p.add_argument("--supported-drivers", action="store_true")

    p = sub.add_parser("rescuesystem", help="show (or deactivate) a server's rescue-system status")
    p.add_argument("server_id", type=int)
    p.add_argument("--deactivate", action="store_true", help="deactivate the rescue system (safe/reversible)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = sub.add_parser("snapshots", help="list, create, or dry-run-check snapshots for a server")
    p.add_argument("server_id", type=int)
    p.add_argument("--create", action="store_true", help="create a new snapshot (additive, does not touch existing ones)")
    p.add_argument("--dryrun", action="store_true", help="check whether creating a snapshot is currently possible")
    p.add_argument("--name", default=None, help="optional name for --create")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = sub.add_parser("tasks", help="list tasks, show one, or cancel one")
    p.add_argument("uuid", nargs="?", default=None)
    p.add_argument("--cancel", action="store_true", help="cancel a running task (does not undo whatever it already did)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    return parser.parse_args()


def main() -> int:
    args = parse_args()  # --help/-h exits here, before _configure() ever runs
    _configure()

    if args.command == "login":
        return cmd_login(args)

    pal = _Palette(_color_enabled(args.no_color))
    client = build_client()

    dispatch = {
        "servers": cmd_servers,
        "imageflavours": cmd_imageflavours,
        "isoimages": cmd_isoimages,
        "iso": cmd_iso,
        "disks": cmd_disks,
        "rescuesystem": cmd_rescuesystem,
        "snapshots": cmd_snapshots,
        "tasks": cmd_tasks,
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
