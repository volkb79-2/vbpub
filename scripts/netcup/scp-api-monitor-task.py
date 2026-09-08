#!/usr/bin/env python3
"""Monitor a Netcup SCP task until completion.

Uses the SCP API endpoint:
  GET /api/v1/tasks/{uuid}

Auth:
  Requires NETCUP_REFRESH_TOKEN in the environment (or in .env in this folder).

Usage:
  python3 monitor-task.py <task_uuid>
  python3 monitor-task.py <task_uuid> --poll 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests

BASE_URL = "https://www.servercontrolpanel.de/scp-core"
KEYCLOAK_URL = "https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect"


_SENSITIVE_DICT_KEYS = {
    "access_token",
    "refresh_token",
    "rootPassword",
    "password",
    "token",
    "authorization",
    "Authorization",
    "cloudInitResultBase64Encoded",
}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if k in _SENSITIVE_DICT_KEYS:
                out[k] = "***REDACTED***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def load_env_file() -> None:
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def get_access_token(refresh_token: str) -> str:
    response = requests.post(
        f"{KEYCLOAK_URL}/token",
        data={
            "client_id": "scp",
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    response.raise_for_status()
    token_data = response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("No access_token in response")
    return access_token


class NetcupSCPClient:
    def __init__(self, access_token: str, refresh_token: Optional[str] = None):
        self.refresh_token = refresh_token
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def refresh_access_token(self) -> None:
        if not self.refresh_token:
            raise RuntimeError("No refresh token available")
        access_token = get_access_token(self.refresh_token)
        self.session.headers.update({"Authorization": f"Bearer {access_token}"})

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{BASE_URL}{endpoint}"
        r = self.session.get(url, params=params, timeout=30)
        if r.status_code == 401 and self.refresh_token:
            self.refresh_access_token()
            r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Monitor a Netcup SCP task")
    p.add_argument("uuid", help="Task UUID")
    p.add_argument("--poll", type=float, default=5.0, help="Poll interval seconds (default: 5)")
    p.add_argument("--json", action="store_true", help="Print full task JSON and exit")
    p.add_argument("--raw", action="store_true", help="With --json: print raw JSON (includes secrets like rootPassword)")
    return p.parse_args()


def main() -> None:
    load_env_file()
    args = parse_args()

    refresh_token = os.environ.get("NETCUP_REFRESH_TOKEN")
    if not refresh_token:
        print("ERROR: missing NETCUP_REFRESH_TOKEN", file=sys.stderr)
        sys.exit(2)

    access_token = get_access_token(refresh_token)
    client = NetcupSCPClient(access_token, refresh_token=refresh_token)

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
