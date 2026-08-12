"""Load the prepared pwmcp release coordinate from ``cmru.vars``.

CMRU's explicit ``prepare`` phase owns the resolver and the generated file.
Build/publish phases consume that exact coordinate; they never silently invoke
network discovery or replace it with an environment value.
"""
from __future__ import annotations

import os
from pathlib import Path

# Keys that must be present in cmru.vars for downstream scripts to work.
_REQUIRED_KEYS = (
    "PLAYWRIGHT_VERSION",
    "PLAYWRIGHT_DISTRO",
    "PLAYWRIGHT_MCP_VERSION",
    "CHROME_DEVTOOLS_MCP_VERSION",
    "MCP_PROXY_VERSION",
    "LIGHTHOUSE_VERSION",
    "PWMCP_VERSION",
)

# Canonical locations relative to this file's position.
# This file lives at pwmcp/scripts/_vars.py  →  parent is pwmcp/scripts/,
# parent.parent is pwmcp/.
_SCRIPTS_DIR = Path(__file__).resolve().parent
_PWMCP_DIR = _SCRIPTS_DIR.parent
_VARS_FILE = _PWMCP_DIR / "cmru.vars"


def _parse_vars_file(path: Path) -> dict[str, str]:
    """Return KEY→value pairs from a KEY=VALUE env file (comments + blank lines skipped)."""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result


def _is_complete(vars_map: dict[str, str]) -> bool:
    return all(vars_map.get(k) for k in _REQUIRED_KEYS)


def load_vars() -> dict[str, str]:
    """Load the exact prepared coordinate or fail with an actionable remedy.

    The file is intentionally authoritative over shell values.  If it is
    absent or malformed, start a new CMRU release transaction (or run the
    project's explicit prepare command) rather than discovering a newer
    upstream halfway through a build or push.
    """
    if not _VARS_FILE.exists():
        print(
            f"[ERROR] {_VARS_FILE} is absent; run CMRU's pwmcp prepare phase first.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    vars_map = _parse_vars_file(_VARS_FILE)
    if not _is_complete(vars_map):
        missing = [k for k in _REQUIRED_KEYS if not vars_map.get(k)]
        print(
            f"[ERROR] cmru.vars is missing required prepared keys: {missing}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    _apply_to_env(vars_map)
    return dict(vars_map)


def _apply_to_env(vars_map: dict[str, str]) -> None:
    """Apply the prepared coordinate as the authoritative process environment."""
    for key, value in vars_map.items():
        os.environ[key] = value
