"""
CIU v2 deploy_pkg — registry authentication check.

Implements S7.9: read-only parse of ~/.docker/config.json; no subprocess,
no network calls.
"""
from __future__ import annotations

import json
from pathlib import Path

# The Docker Hub canonical auth key stored in config.json
_DOCKER_HUB_KEY = "https://index.docker.io/v1/"


def _normalize_host(registry_url: str) -> str:
    """Strip scheme and path from *registry_url*, returning just the host[:port].

    Special cases:
    - Empty string or 'docker.io' → 'https://index.docker.io/v1/' (the key
      Docker actually stores in config.json for Docker Hub).
    - Any URL that IS 'https://index.docker.io/v1/' → returned as-is.
    """
    url = registry_url.strip()

    # Explicit canonical Docker Hub key
    if url == _DOCKER_HUB_KEY:
        return _DOCKER_HUB_KEY

    # docker.io shorthand
    if not url or url.lower() in ("docker.io", "registry-1.docker.io"):
        return _DOCKER_HUB_KEY

    # Strip scheme
    if "://" in url:
        url = url.split("://", 1)[1]

    # Strip path (anything after the first '/')
    url = url.split("/")[0]

    return url


def check_registry_auth(
    registry_url: str,
    docker_config_path: str | Path | None = None,
) -> bool:
    """Return True iff Docker config has credentials for *registry_url*.

    Credential sources checked (S7.9):
    1. auths[<host>] has a non-empty 'auth' or 'identitytoken' value.
    2. credHelpers[<host>] exists (any value).
    3. A global 'credsStore' key exists in the config (catches store-backed
       credential setups where no per-host auths entry is written).

    Path resolution:
    - *docker_config_path* explicitly given → use that file.
    - Otherwise → ~/.docker/config.json.

    Missing or invalid (non-JSON / non-object) config → False.
    NO subprocess calls; NO network calls.
    """
    if docker_config_path is None:
        config_path = Path.home() / ".docker" / "config.json"
    else:
        config_path = Path(docker_config_path)

    try:
        raw = config_path.read_text(encoding="utf-8")
        cfg = json.loads(raw)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return False

    if not isinstance(cfg, dict):
        return False

    host = _normalize_host(registry_url)

    # 1. auths[host] with non-empty auth/identitytoken
    auths: dict = cfg.get("auths", {})
    if isinstance(auths, dict) and host in auths:
        entry = auths[host]
        if isinstance(entry, dict):
            if entry.get("auth") or entry.get("identitytoken"):
                return True

    # 2. credHelpers[host] present
    cred_helpers: dict = cfg.get("credHelpers", {})
    if isinstance(cred_helpers, dict) and host in cred_helpers:
        return True

    # 3. Global credsStore
    if cfg.get("credsStore"):
        return True

    return False
