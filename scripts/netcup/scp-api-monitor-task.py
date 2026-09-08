#!/usr/bin/env python3
"""Monitor a Netcup SCP task until completion.

Uses the SCP API endpoint:
  GET /api/v1/tasks/{uuid}

Auth:
  Requires NETCUP_SCP_API_REFRESH_TOKEN in the environment (or in .env in this folder).

Usage:
  python3 scp-api-monitor-task.py <task_uuid>
  python3 scp-api-monitor-task.py <task_uuid> --poll 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def load_env_file() -> None:
    """Load environment variables from a local .env file (no external deps).

    Search order matches scp-api-install-host.py:
      1) current working directory
      2) directory containing this script (scripts/netcup)
      3) repo root (two levels up from this script)

    .env values take precedence over pre-set environment variables, so a
    stale exported NETCUP_SCP_API_REFRESH_TOKEN in your shell doesn't
    silently shadow a freshly updated .env (matches scp-api-install-host.py's
    behavior). Called immediately below (not just in main()) so the
    settings/DEBUG env-var reads further down see .env's values too.
    """
    script_dir = Path(__file__).resolve().parent
    candidates = [
        Path.cwd() / ".env",
        script_dir / ".env",
        script_dir.parent.parent / ".env",
    ]
    env_file = next((p for p in candidates if p.exists()), None)
    if env_file is None:
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ[key.strip()] = value


load_env_file()


def _flatten_toml(data: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for k, v in data.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            flat.update(_flatten_toml(v, prefix=f"{key}."))
        else:
            flat[key] = v
    return flat


def _load_settings(path: Path, expected_keys: Set[str]) -> Dict[str, Any]:
    """Load a settings TOML file against a closed schema.

    Every key in `expected_keys` must be present, and any key present that is
    NOT in `expected_keys` is also an error (typo/stale-key protection). There
    is no Python-side fallback for any of these values: a missing key is a
    hard error naming it, not a silently-assumed default.
    """
    if not path.exists():
        raise SystemExit(f"ERROR: missing settings file: {path}")
    with path.open("rb") as f:
        data = tomllib.load(f)
    flat = _flatten_toml(data)
    missing = expected_keys - flat.keys()
    unknown = flat.keys() - expected_keys
    errors = []
    if missing:
        errors.append(f"missing required settings: {', '.join(sorted(missing))}")
    if unknown:
        errors.append(f"unknown/unexpected settings: {', '.join(sorted(unknown))}")
    if errors:
        raise SystemExit(f"ERROR: {path} failed validation:\n  - " + "\n  - ".join(errors))
    return flat


_SETTINGS_EXPECTED_KEYS = {"api.base_url", "api.keycloak_url", "monitor.poll_interval"}
SETTINGS_PATH = Path(__file__).resolve().parent / "scp-api-monitor-task.toml"
SETTINGS = _load_settings(SETTINGS_PATH, _SETTINGS_EXPECTED_KEYS)

BASE_URL = SETTINGS["api.base_url"]
KEYCLOAK_URL = SETTINGS["api.keycloak_url"]

# Debug mode: NETCUP_SCP_API_DEBUG env var, OR'd with --debug in main().
DEBUG = os.environ.get("NETCUP_SCP_API_DEBUG", "no").lower() in ("yes", "true", "1")


def log_debug(message: str) -> None:
    """Print debug messages if DEBUG is enabled"""
    if DEBUG:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[DEBUG] {timestamp} {message}", file=sys.stderr)


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


class HTTPStatusError(RuntimeError):
    def __init__(self, status: int, message: str, body: str = ""):
        super().__init__(message)
        self.status = int(status)
        self.body = body


def _http_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Any:
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if q:
            url = url + ("&" if "?" in url else "?") + q

    hdrs: Dict[str, str] = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)

    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, method=method.upper(), headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise HTTPStatusError(int(getattr(e, "code", 0) or 0), f"HTTP {getattr(e, 'code', '?')} for {url}", body)
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error for {url}: {e}")


def get_access_token(refresh_token: str) -> str:
    data = urllib.parse.urlencode(
        {
            "client_id": "scp",
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{KEYCLOAK_URL}/token",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Token request failed: HTTP {e.code}: {body[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Token request network error: {e}")

    token_data = json.loads(raw)
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("No access_token in response")
    return access_token


class NetcupSCPClient:
    def __init__(self, access_token: str, refresh_token: Optional[str] = None):
        self.refresh_token = refresh_token
        self.access_token = access_token

    def refresh_access_token(self) -> None:
        if not self.refresh_token:
            raise RuntimeError("No refresh token available")
        self.access_token = get_access_token(self.refresh_token)

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{BASE_URL}{endpoint}"
        log_debug(f"GET {url} {params or ''}")
        try:
            result = _http_json("GET", url, headers=self._auth_headers(), params=params)
        except HTTPStatusError as e:
            if e.status == 401 and self.refresh_token:
                log_debug("401 Unauthorized; refreshing token and retrying once")
                self.refresh_access_token()
                result = _http_json("GET", url, headers=self._auth_headers(), params=params)
            else:
                raise
        log_debug(f"Response: {json.dumps(_redact(result), indent=2)}")
        return result


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Monitor a Netcup SCP task until completion.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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

Settings (scp-api-monitor-task.toml, next to this script):
  poll_interval and the API base URLs default from there, not a Python
  literal - see that file's comments. --poll overrides it per-run.

Environment Variables:
  NETCUP_SCP_API_REFRESH_TOKEN   Required: Netcup API refresh token (or in .env in this folder).
  NETCUP_SCP_API_DEBUG           Enable verbose request/response logging (yes/true/1); same as --debug.
""",
    )
    p.add_argument("uuid", help="Task UUID (from scp-api-install-host.py's install-image response)")
    p.add_argument("--poll", type=float, default=SETTINGS["monitor.poll_interval"], help="Poll interval seconds (default: from scp-api-monitor-task.toml)")
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
    return p.parse_args()


def main() -> None:
    global DEBUG
    args = parse_args()
    DEBUG = DEBUG or args.debug

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
