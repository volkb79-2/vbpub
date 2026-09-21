#!/usr/bin/env python3
"""Monitor a Netcup SCP task until completion.

Uses the SCP API endpoint:
  GET /api/v1/tasks/{uuid}

Auth:
  Requires NETCUP_SCP_API_REFRESH_TOKEN in the environment (or in .env in this folder).

Usage:
  python3 monitor-task.py <task_uuid>
  python3 monitor-task.py <task_uuid> --poll 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import netcup_scp_client
from netcup_scp_client import (
    NetcupSCPClient,
    _load_settings,
    get_access_token,
    load_env_file,
)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


load_env_file()


_SETTINGS_EXPECTED_KEYS = {"api.base_url", "api.keycloak_url", "monitor.poll_interval"}
SETTINGS_PATH = Path(__file__).resolve().parent / "monitor-task.toml"
SETTINGS = _load_settings(SETTINGS_PATH, _SETTINGS_EXPECTED_KEYS)

netcup_scp_client.BASE_URL = SETTINGS["api.base_url"]
netcup_scp_client.KEYCLOAK_URL = SETTINGS["api.keycloak_url"]

# Debug mode: NETCUP_SCP_API_DEBUG env var, OR'd with --debug in main().
DEBUG = os.environ.get("NETCUP_SCP_API_DEBUG", "no").lower() in ("yes", "true", "1")
netcup_scp_client.DEBUG = DEBUG

# Reuse the same response redaction policy as scp-api.py and install-host.py.
_redact = netcup_scp_client._redact_for_log


class _WideHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Keep the monitor workflow's examples readable in a wide terminal."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("width", 120)
        super().__init__(*args, **kwargs)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Monitor a Netcup SCP task until completion.",
        formatter_class=_WideHelpFormatter,
        epilog="""
Examples:
  # Poll every 5s (default) until FINISHED/ERROR/CANCELED/ROLLBACK:
  %(prog)s 3a27fe8e-e747-4f3b-80b0-f930c0d0db3f

  # Faster polling:
  %(prog)s <uuid> --poll 2

  # Print the full task JSON once and exit (secrets redacted):
  %(prog)s <uuid> --json

  # Same, but include secrets (rootPassword, tokens) - handle with care:
  %(prog)s <uuid> --json --raw

  # Preflight only: confirm NETCUP_SCP_API_REFRESH_TOKEN works and the task
  # exists, print its current state, and exit without entering the poll loop:
  %(prog)s <uuid> --dry-run

Settings (monitor-task.toml, next to this script):
  poll_interval and the API base URLs default from there, not a Python
  literal - see that file's comments. --poll overrides it per-run.

Environment Variables:
  NETCUP_SCP_API_REFRESH_TOKEN   Required: Netcup API refresh token (or in .env in this folder).
  NETCUP_SCP_API_DEBUG           Enable verbose request/response logging (yes/true/1); same as --debug.
""",
        add_help=False,
    )
    p.add_argument("--help", action="help", help="show this help message and exit")
    p.add_argument("uuid", nargs="?", help="Task UUID (from install-host.py's install-image response)")
    p.add_argument("--poll", type=float, default=SETTINGS["monitor.poll_interval"], help="Poll interval seconds (default: from monitor-task.toml)")
    p.add_argument("--json", action="store_true", help="Print full task JSON and exit")
    p.add_argument("--raw", action="store_true", help="With --json: print raw JSON (includes secrets like rootPassword)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Preflight only: obtain an access token and fetch the task once to confirm it "
            "exists, print its current state, then exit without entering the poll loop."
        ),
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose request/response logging. Same as NETCUP_SCP_API_DEBUG=yes.",
    )
    args = p.parse_args()
    if args.uuid is None:
        p.print_help(sys.stderr)
        p.exit(2)
    return args


def main() -> None:
    global DEBUG
    args = parse_args()
    DEBUG = DEBUG or args.debug
    netcup_scp_client.DEBUG = DEBUG

    if not _UUID_RE.match(args.uuid):
        print(f"⚠ WARNING: '{args.uuid}' does not look like a standard UUID; continuing anyway.", file=sys.stderr)

    refresh_token = os.environ.get("NETCUP_SCP_API_REFRESH_TOKEN")
    if not refresh_token:
        print("ERROR: missing NETCUP_SCP_API_REFRESH_TOKEN", file=sys.stderr)
        sys.exit(2)

    try:
        access_token = get_access_token(refresh_token)
    except Exception as e:
        print(f"❌ Failed to get access token: {e}", file=sys.stderr)
        sys.exit(1)
    client = NetcupSCPClient(access_token, refresh_token=refresh_token)

    if args.dry_run:
        print(f"[dry-run] Auth OK. Fetching task once to confirm it exists (no poll loop)...")
        task = client.get(f"/api/v1/tasks/{args.uuid}")
        print(f"[dry-run] OK - task exists. Current state: {task.get('state')}")
        if args.json:
            print(json.dumps(_redact(task) if not args.raw else task, indent=2))
        return

    last_state = None
    last_progress = None

    while True:
        task: Dict[str, Any] = client.get(f"/api/v1/tasks/{args.uuid}")
        if args.json:
            if args.raw:
                print(json.dumps(task, indent=2))
            else:
                print(json.dumps(_redact(task), indent=2))
            return

        state = task.get("state")
        name = task.get("name")
        msg = task.get("message")
        tp = task.get("taskProgress") or {}
        progress = tp.get("progressInPercent") if isinstance(tp, dict) else None

        changed = state != last_state or progress != last_progress
        if changed:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ptxt = f"{progress:.0f}%" if isinstance(progress, (int, float)) else "?%"
            print(f"[{now}] {state} {ptxt}  {name or ''}".rstrip())
            if msg:
                print(f"  message: {msg}")

            last_state = state
            last_progress = progress

        if state in ("FINISHED", "ERROR", "CANCELED", "ROLLBACK"):
            resp_err = task.get("responseError")
            if resp_err:
                print("=" * 70)
                print("TASK RESPONSE ERROR")
                print(json.dumps(resp_err, indent=2))
            return

        time.sleep(max(0.5, float(args.poll)))


if __name__ == "__main__":
    main()
