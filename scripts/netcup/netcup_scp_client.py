#!/usr/bin/env python3
"""Shared Netcup SCP API client: OAuth2 device-code login, token refresh,
the authenticated HTTP client, and the small settings/.env helpers both
`install-host.py`, `monitor-task.py`, and `scp-api.py` need.

The frontends use :func:`configure_api` so the API endpoint configuration is
loaded once from ``netcup.toml``. The module-level names remain as a small
compatibility seam for tests and older callers, but frontends no longer copy
the configuration setup independently.

Not a pip-installable package: sits alongside its importers in
scripts/netcup/ and is found via Python's default "importing script's own
directory is on sys.path" behavior, same as any other sibling module.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import tomllib

# --- module-level config, set by each importer before use ----------------

DEBUG = False
DEBUG_RAW = False
DEBUG_LOGGER: logging.Logger | None = None
BASE_URL: Optional[str] = None
KEYCLOAK_URL: Optional[str] = None

API_SETTINGS_PATH = Path(__file__).resolve().parent / "netcup.toml"


def load_api_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the single shared Netcup API configuration file."""
    return _load_settings(path or API_SETTINGS_PATH, {"api.base_url", "api.keycloak_url"})


def configure_api(path: Optional[Path] = None, *, load_environment: bool = True) -> Dict[str, Any]:
    """Configure the shared client from ``netcup.toml`` and the local env."""
    if load_environment:
        load_env_file()
    settings = load_api_settings(path)
    global BASE_URL, KEYCLOAK_URL
    BASE_URL = settings["api.base_url"]
    KEYCLOAK_URL = settings["api.keycloak_url"]
    return settings


def reverse_dns(ip: str) -> Optional[str]:
    """Return the PTR hostname for *ip*, or ``None`` when it has no PTR.

    Reverse DNS is presentation data and must never prevent an API operation;
    callers use the absence of a result to show ``-``.  Keeping the resolver
    here gives the API explorer and installer the same behavior.
    """
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return None


def log_debug(message: str) -> None:
    """Emit debug messages through a CLI logger or legacy stderr fallback."""
    if DEBUG:
        if DEBUG_LOGGER is not None:
            DEBUG_LOGGER.debug(message)
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[DEBUG] {timestamp} {message}", file=sys.stderr)


# --- .env helpers ----------------------------------------------------------

def _load_env_file(path: Path) -> Dict[str, str]:
    """Minimal .env parser (keeps behavior predictable; no shell expansion)."""
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        out[key] = value
    return out


def _write_env_file(path: Path, updates: Dict[str, str]) -> None:
    """Update/add keys in a .env file while preserving unknown lines."""
    existing_lines: List[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    remaining = dict(updates)
    new_lines: List[str] = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                new_lines.append(f"{key}={remaining.pop(key)}")
                continue
        new_lines.append(raw_line)
    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    # The file contains a long-lived OAuth refresh token.  Enforce this on
    # both newly-created and pre-existing files; relying on the process umask
    # leaves a common 0644 file readable by every local user.
    os.chmod(path, 0o600)


def resolve_env_path() -> Path:
    """Return the existing .env selected by the shared search order.

    If no candidate exists yet, return the canonical file next to the Netcup
    tools so the login command has a deterministic, credential-safe destination.
    """
    script_dir = Path(__file__).resolve().parent
    candidates = [
        Path.cwd() / ".env",
        script_dir / ".env",
        script_dir.parent.parent / ".env",
    ]
    return next((p for p in candidates if p.exists()), script_dir / ".env")


def load_env_file() -> None:
    """Load environment variables from a local .env file (no external deps).

    Search order:
      1) current working directory
      2) directory containing this module (scripts/netcup)
      3) repo root (two levels up from this module)

    This makes it safe to run either script from repo root while keeping
    the canonical .env next to the netcup tooling.
    """
    env_file = resolve_env_path()
    if not env_file.exists():
        return

    try:
        with env_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    value = value.strip()

                    # If the value is unquoted, allow trailing inline comments.
                    # Example: SERVER_NAME=v1001  # prod
                    if value and not (value.startswith('"') or value.startswith("'")):
                        if "#" in value:
                            value = value.split("#", 1)[0].rstrip()

                    # Remove surrounding quotes if present.
                    if (len(value) >= 2) and (
                        (value.startswith('"') and value.endswith('"'))
                        or (value.startswith("'") and value.endswith("'"))
                    ):
                        value = value[1:-1]

                    # In this repo, `.env` is the primary configuration source for the
                    # installer. Prefer it over pre-set environment variables to keep
                    # runs deterministic (CLI flags are the intended override channel).
                    key = key.strip()
                    os.environ[key] = value
    except OSError:
        # Best-effort only; the caller will error out later if required vars are missing.
        return


# --- local protected-server policy -----------------------------------------

PROTECTED_SERVER_NAME_RE = re.compile(r"^v[0-9]+$")
PROTECTED_SERVER_NAMES_ENV = "NETCUP_SCP_API_PROTECTED_SERVERS"
PROTECTED_SERVER_IDS_ENV = "NETCUP_SCP_API_PROTECTED_SERVER_IDS"


class ProtectedServerError(RuntimeError):
    """A local safety policy refuses or cannot safely authorize a mutation."""


def is_protectable_server_name(value: Any) -> bool:
    """Return whether *value* is an SCP internal server name we can select.

    The login wizard intentionally only offers the stable-looking SCP names
    such as ``v2202503209318326780``.  Hostnames and nicknames are not a safe
    substitute: operators commonly change them during an installation.
    """
    return isinstance(value, str) and PROTECTED_SERVER_NAME_RE.fullmatch(value) is not None


def _parse_protected_csv(raw: str, *, kind: str) -> List[str]:
    """Parse a strict comma-separated policy value from the environment."""
    if not raw.strip():
        return []
    values = [item.strip() for item in raw.split(",")]
    if any(not item for item in values):
        raise ValueError(f"{kind} contains an empty item; remove stray commas")
    if kind == "protected server names":
        invalid = [item for item in values if not is_protectable_server_name(item)]
        if invalid:
            raise ValueError(
                f"protected server names must match v<digits>; invalid value(s): {', '.join(invalid)}"
            )
    else:
        invalid = [item for item in values if not item.isdigit() or int(item) <= 0]
        if invalid:
            raise ValueError(
                f"protected server IDs must be positive integers; invalid value(s): {', '.join(invalid)}"
            )
    duplicates = sorted({item for item in values if values.count(item) > 1})
    if duplicates:
        raise ValueError(f"{kind} contains duplicate value(s): {', '.join(duplicates)}")
    return values


def protected_server_policy() -> Tuple[Set[str], Set[int]]:
    """Read and validate the local protected-server denylist.

    An empty policy is disabled.  A non-empty policy is deliberately strict:
    configuration errors are raised before a command can reach the API.
    """
    names = set(_parse_protected_csv(
        os.environ.get(PROTECTED_SERVER_NAMES_ENV, ""),
        kind="protected server names",
    ))
    ids = {
        int(value)
        for value in _parse_protected_csv(
            os.environ.get(PROTECTED_SERVER_IDS_ENV, ""),
            kind="protected server IDs",
        )
    }
    if not names and not ids:
        return set(), set()
    return names, ids


def protected_server_policy_configured() -> bool:
    """Whether either denylist setting contains a configured value."""
    names, ids = protected_server_policy()
    return bool(names or ids)


def write_protected_server_policy(path: Path, names: List[str], ids: List[int]) -> None:
    """Persist a validated denylist and its login-selected server IDs."""
    clean_names = list(dict.fromkeys(names))
    clean_ids = list(dict.fromkeys(ids))
    invalid_names = [name for name in clean_names if not is_protectable_server_name(name)]
    if invalid_names:
        raise ValueError(f"protected server names must match v<digits>; invalid value(s): {', '.join(invalid_names)}")
    if any(not isinstance(server_id, int) or isinstance(server_id, bool) or server_id <= 0 for server_id in clean_ids):
        raise ValueError("protected server IDs must be positive integers")
    _write_env_file(path, {
        PROTECTED_SERVER_NAMES_ENV: ",".join(clean_names),
        PROTECTED_SERVER_IDS_ENV: ",".join(str(server_id) for server_id in clean_ids),
    })


def assert_server_mutation_allowed(server_id: Any, server_name: Any, operation: str) -> None:
    """Refuse a mutation targeting a protected or indeterminate server.

    The ID companion set is authoritative for servers selected by the login
    wizard, so a rename remains protected.  When names are configured without
    a matching ID, a missing/malformed response name is indeterminate and is
    refused rather than silently treated as an unprotected server.
    """
    names, ids = protected_server_policy()
    if not names and not ids:
        return
    if not isinstance(server_id, int) or isinstance(server_id, bool) or server_id <= 0:
        raise ProtectedServerError(
            f"refusing {operation}: API returned an invalid server ID while the local protected-server policy is active"
        )
    if server_id in ids:
        shown_name = server_name if isinstance(server_name, str) and server_name else "<unnamed>"
        raise ProtectedServerError(
            f"refusing {operation}: server {shown_name!r} (id {server_id}) is protected by the local denylist"
        )
    if names:
        if not isinstance(server_name, str) or not server_name.strip():
            raise ProtectedServerError(
                f"refusing {operation}: could not determine the server name for id {server_id} "
                "while the local protected-server policy is active"
            )
        if server_name in names:
            raise ProtectedServerError(
                f"refusing {operation}: server {server_name!r} (id {server_id}) is protected by the local denylist"
            )
# --- settings (TOML) --------------------------------------------------------

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

    Every key in `expected_keys` must be present, and any key present that
    is NOT in `expected_keys` is also an error (typo/stale-key protection).
    There is no Python-side fallback for any of these values: a missing key
    is a hard error naming it, not a silently-assumed default.
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


# --- redaction (for debug logging) -----------------------------------------

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


def _redact_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for k, v in value.items():
            if k in _SENSITIVE_DICT_KEYS:
                redacted[k] = "***REDACTED***"
            elif k == "customScript" and isinstance(v, str):
                redacted[k] = f"***REDACTED customScript (len={len(v)})***"
            else:
                redacted[k] = _redact_for_log(v)
        return redacted
    if isinstance(value, list):
        return [_redact_for_log(v) for v in value]
    return value


def _debug_value(value: Any) -> Any:
    """Apply the shared client's raw-debug opt-out only when explicitly set."""
    return value if DEBUG_RAW else _redact_for_log(value)


# --- low-level HTTP ----------------------------------------------------------

class HTTPStatusError(RuntimeError):
    def __init__(self, status: int, message: str, body: str = ""):
        super().__init__(message)
        self.status = int(status)
        self.body = body


def _upload_presigned_stream(url: str, stream, size: int, *, timeout: float = 300.0) -> Dict[str, str]:
    """PUT a file stream to an S3 presigned URL without adding API auth."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError("presigned upload URL is not an absolute HTTP(S) URL")
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed.netloc, timeout=timeout)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    try:
        connection.putrequest("PUT", target)
        connection.putheader("Content-Length", str(size))
        connection.putheader("Content-Type", "application/octet-stream")
        connection.endheaders()
        remaining = size
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError("local ISO changed or ended before the declared upload size")
            connection.send(chunk)
            remaining -= len(chunk)
        response = connection.getresponse()
        body = response.read(2000).decode("utf-8", errors="replace")
        if not 200 <= response.status < 300:
            raise HTTPStatusError(response.status, f"presigned upload failed with HTTP {response.status}", body)
        return {key.lower(): value for key, value in response.getheaders()}
    finally:
        connection.close()


def upload_file_to_presigned_url(url: str, path: Path, *, offset: int = 0, size: Optional[int] = None) -> Dict[str, str]:
    """Stream all or one part of a local file to an unauthenticated presigned URL."""
    file_size = path.stat().st_size
    if size is None:
        size = file_size - offset
    if offset < 0 or size < 0 or offset + size > file_size:
        raise ValueError("presigned upload range is outside the local file")
    with path.open("rb") as stream:
        stream.seek(offset)
        return _upload_presigned_stream(url, stream, size)


def _http_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
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
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON response from {method.upper()} {url}: {e}") from e


# --- OAuth2 device-code login + token refresh -------------------------------

def get_access_token(refresh_token: str) -> str:
    """Get fresh access token using refresh token. Uses module-level
    KEYCLOAK_URL -- set it before calling."""
    log_debug("Refreshing access token...")

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

    try:
        token_data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Token response was not valid JSON: {e}") from e
    if not isinstance(token_data, dict):
        raise RuntimeError("Token response was not a JSON object")
    access_token = token_data.get("access_token")

    if not isinstance(access_token, str) or not access_token.strip():
        raise ValueError("No access token in response")

    log_debug(f"Access token obtained (expires in {token_data.get('expires_in', 'unknown')} seconds)")
    return access_token


def run_device_code_login(env_path: Path) -> int:
    """Automate the OAuth2 device-code grant and write the resulting
    refresh token into env_path (NETCUP_SCP_API_REFRESH_TOKEN). Uses
    module-level KEYCLOAK_URL -- set it before calling.

    Equivalent manual flow, for reference / debugging if this ever breaks:

    1. Get a device_code and activate it
    ```
    curl -X POST 'https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/auth/device' \\
      -d "client_id=scp" \\
      -d 'scope=offline_access openid' | jq
    ```
    1.2. extract link in "verification_uri_complete", open it, login with SCP credentials, confirm grant access
    1.3. extract the "device_code" : e.g. "BqCuANW2nKFwCtdf5HcbYRIEZ_RrklqiSF40r9AQH0k"

    2. Use activated `device-token` to get long-term `refresh_token`
    ```bash
    device_code=<device-code-from-first-curl-reply>
    curl -X POST 'https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/token' \\
      -d 'grant_type=urn:ietf:params:oauth:grant-type:device_code' \\
      -d "device_code=$device_code" \\
      -d 'client_id=scp' | jq
    ```

    Notes:
    - Use access token within the next 300 seconds to access the API. See
      get_access_token() for how this script refreshes it as needed.
    - The offline refresh token can be used multiple times and does not
      expire as long as it is used at least once every 30 days.
    - If the refresh token is leaked or no longer needed it could be
      revoked. Forgotten refresh tokens can be revoked in the Account
      Console: https://www.servercontrolpanel.de/realms/scp/account
    """
    print("=" * 70)
    print("LOGIN (netcup SCP OAuth2 device-code flow)")
    print("=" * 70)

    device_data = urllib.parse.urlencode(
        {"client_id": "scp", "scope": "offline_access openid"}
    ).encode("utf-8")
    device_req = urllib.request.Request(
        f"{KEYCLOAK_URL}/auth/device",
        data=device_data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(device_req, timeout=30) as resp:
            device = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"❌ ERROR: could not start device login: {e}", file=sys.stderr)
        return 1

    if not isinstance(device, dict):
        print("❌ ERROR: device-code response was not a JSON object", file=sys.stderr)
        return 1
    device_code = device.get("device_code")
    if not isinstance(device_code, str) or not device_code.strip():
        print(f"❌ ERROR: device-code response had no device_code: {device}", file=sys.stderr)
        return 1
    try:
        interval = max(1, int(device.get("interval", 5)))
        expires_in = int(device.get("expires_in", 600))
    except (TypeError, ValueError) as e:
        print(f"❌ ERROR: device-code response had invalid timing values: {e}", file=sys.stderr)
        return 1
    if expires_in <= 0:
        print("❌ ERROR: device-code response had non-positive expires_in", file=sys.stderr)
        return 1
    verification_url = device.get("verification_uri_complete") or device.get("verification_uri")
    if not isinstance(verification_url, str) or not verification_url.strip():
        print("❌ ERROR: device-code response had no verification URL", file=sys.stderr)
        return 1

    print(f"1. Open this URL and log in with your SCP credentials:\n   {verification_url}")
    if not device.get("verification_uri_complete") and device.get("user_code"):
        print(f"   Enter code: {device['user_code']}")
    print("2. Waiting for you to complete login (Ctrl-C to cancel)...")

    deadline = time.monotonic() + expires_in
    try:
        while time.monotonic() < deadline:
            time.sleep(interval)
            token_req = urllib.request.Request(
                f"{KEYCLOAK_URL}/token",
                data=urllib.parse.urlencode(
                    {
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": "scp",
                    }
                ).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                with urllib.request.urlopen(token_req, timeout=30) as resp:
                    token_response = json.loads(resp.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as e:
                try:
                    body = json.loads(e.read().decode("utf-8", errors="replace") or "{}")
                except json.JSONDecodeError:
                    # Non-JSON error body (e.g. an HTML gateway-error page) -
                    # still a clean failure, not a crash.
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                error = body.get("error")
                if error == "authorization_pending":
                    continue
                if error == "slow_down":
                    interval += 5
                    continue
                print(f"❌ ERROR: {error or e}: {body.get('error_description', '')}", file=sys.stderr)
                return 1
            except urllib.error.URLError as e:
                print(f"❌ ERROR: token poll network error: {e}", file=sys.stderr)
                return 1
            except json.JSONDecodeError as e:
                print(f"❌ ERROR: token response was not valid JSON: {e}", file=sys.stderr)
                return 1

            if not isinstance(token_response, dict):
                print("❌ ERROR: token response was not a JSON object", file=sys.stderr)
                return 1
            refresh_token = token_response.get("refresh_token")
            if not isinstance(refresh_token, str) or not refresh_token.strip():
                print("❌ ERROR: token response had no refresh_token", file=sys.stderr)
                return 1
            _write_env_file(env_path, {"NETCUP_SCP_API_REFRESH_TOKEN": refresh_token})
            print(f"✓ Logged in. Wrote NETCUP_SCP_API_REFRESH_TOKEN to {env_path}")
            return 0
    except KeyboardInterrupt:
        print("\nLogin cancelled.", file=sys.stderr)
        return 130

    print("❌ ERROR: login timed out waiting for browser confirmation", file=sys.stderr)
    return 1


# --- the client itself -------------------------------------------------------

class NetcupSCPClient:
    def __init__(self, access_token: str, refresh_token: Optional[str] = None):
        self.base_url = BASE_URL
        self.refresh_token = refresh_token
        self.access_token = access_token

    def refresh_access_token(self) -> None:
        if not self.refresh_token:
            raise RuntimeError("No refresh token available for access token refresh")
        new_access_token = get_access_token(self.refresh_token)
        self.access_token = new_access_token

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """Make GET request to API"""
        url = f"{self.base_url}{endpoint}"
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
        log_debug(f"Response: {json.dumps(_debug_value(result), indent=2)}")
        return result

    def post(self, endpoint: str, data: Optional[Any] = None) -> Any:
        """Make POST request to API"""
        url = f"{self.base_url}{endpoint}"
        log_debug(f"POST {url}")
        log_debug(f"Payload: {json.dumps(_debug_value(data), indent=2)}")
        try:
            result = _http_json("POST", url, headers=self._auth_headers(), json_body=data)
        except HTTPStatusError as e:
            if e.status == 401 and self.refresh_token:
                log_debug("401 Unauthorized; refreshing token and retrying once")
                self.refresh_access_token()
                result = _http_json("POST", url, headers=self._auth_headers(), json_body=data)
            else:
                raise
        log_debug(f"Response: {json.dumps(_debug_value(result), indent=2)}")
        return result

    def patch(self, endpoint: str, data: Dict, params: Optional[Dict] = None) -> Any:
        """Make PATCH request to API."""
        url = f"{self.base_url}{endpoint}"
        log_debug(f"PATCH {url} {params or ''}")
        log_debug(f"Payload: {json.dumps(_debug_value(data), indent=2)}")
        headers = self._auth_headers()
        headers["Content-Type"] = "application/merge-patch+json"
        try:
            result = _http_json("PATCH", url, headers=headers, params=params, json_body=data)
        except HTTPStatusError as e:
            if e.status == 401 and self.refresh_token:
                log_debug("401 Unauthorized; refreshing token and retrying once")
                self.refresh_access_token()
                headers = self._auth_headers()
                headers["Content-Type"] = "application/merge-patch+json"
                result = _http_json("PATCH", url, headers=headers, params=params, json_body=data)
            else:
                raise
        log_debug(f"Response: {json.dumps(_debug_value(result), indent=2)}")
        return result

    def put(self, endpoint: str, data: Optional[Any] = None, params: Optional[Dict] = None) -> Any:
        """Make PUT request to API (e.g. tasks:cancel, interface/vlan updates)."""
        url = f"{self.base_url}{endpoint}"
        log_debug(f"PUT {url} {params or ''}")
        if data is not None:
            log_debug(f"Payload: {json.dumps(_debug_value(data), indent=2)}")
        try:
            result = _http_json("PUT", url, headers=self._auth_headers(), params=params, json_body=data)
        except HTTPStatusError as e:
            if e.status == 401 and self.refresh_token:
                log_debug("401 Unauthorized; refreshing token and retrying once")
                self.refresh_access_token()
                result = _http_json("PUT", url, headers=self._auth_headers(), params=params, json_body=data)
            else:
                raise
        log_debug(f"Response: {json.dumps(_debug_value(result), indent=2)}")
        return result

    def delete(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """Make DELETE request to API (e.g. detach an ISO, deactivate rescue system)."""
        url = f"{self.base_url}{endpoint}"
        log_debug(f"DELETE {url} {params or ''}")
        try:
            result = _http_json("DELETE", url, headers=self._auth_headers(), params=params)
        except HTTPStatusError as e:
            if e.status == 401 and self.refresh_token:
                log_debug("401 Unauthorized; refreshing token and retrying once")
                self.refresh_access_token()
                result = _http_json("DELETE", url, headers=self._auth_headers(), params=params)
            else:
                raise
        log_debug(f"Response: {json.dumps(_debug_value(result), indent=2)}")
        return result

    def upload_file(self, url: str, path: Path, *, offset: int = 0, size: Optional[int] = None) -> Dict[str, str]:
        """Upload a whole file or part to an SCP-provided presigned URL.

        Presigned object-storage URLs must not receive the SCP bearer token;
        this deliberately bypasses the authenticated API request helpers.
        """
        log_debug(f"PUT presigned upload {url.split('?', 1)[0]} offset={offset} size={size}")
        return upload_file_to_presigned_url(url, path, offset=offset, size=size)

    def get_user_info(self) -> Dict:
        """Get user information from OIDC userinfo endpoint"""
        url = f"{KEYCLOAK_URL}/userinfo"
        log_debug(f"GET {url}")
        try:
            result = _http_json("GET", url, headers=self._auth_headers())
        except HTTPStatusError as e:
            if e.status == 401 and self.refresh_token:
                log_debug("401 Unauthorized; refreshing token and retrying once")
                self.refresh_access_token()
                result = _http_json("GET", url, headers=self._auth_headers())
            else:
                raise
        log_debug(f"Response: {json.dumps(_debug_value(result), indent=2)}")
        return result
