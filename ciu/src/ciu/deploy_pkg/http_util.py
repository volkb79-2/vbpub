"""
CIU v2 deploy_pkg — HTTP utilities.

Single implementation for all health/selftest fetches (S7.7, S7.9).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def http_get_json(
    url: str,
    *,
    timeout: float = 10,
    user_agent: str = "CIU-HealthCheck/2.0",
) -> tuple[bool, dict | str]:
    """Perform an HTTP GET and parse the response as JSON.

    Returns:
        (True, parsed_dict)  on success with valid JSON body.
        (False, error_str)   on any HTTP error, network failure, timeout,
                             or non-JSON response body.
    """
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return False, f"URLError: {exc.reason}"
    except TimeoutError:
        return False, f"Timeout after {timeout}s"
    except OSError as exc:
        # Catches socket.timeout (Python < 3.11) and other OS-level errors
        return False, f"Error: {exc}"

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        return False, f"Non-JSON response: {exc}"

    if not isinstance(parsed, dict):
        return False, f"Expected JSON object, got {type(parsed).__name__}"

    return True, parsed
