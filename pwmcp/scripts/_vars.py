"""Shared helper — load pwmcp cmru.vars with self-heal.

If cmru.vars is missing or incomplete (a required key is absent),
``resolve-playwright-version.py`` is run as a subprocess (idempotent) to
regenerate it, then the file is reloaded.

Env-wins: any key already present in os.environ is **not** overwritten.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Keys that must be present in cmru.vars for downstream scripts to work.
# PLAYWRIGHT_VERSION and PWMCP_VERSION are backwards-compat aliases for the PyPI variants.
_REQUIRED_KEYS = (
    "PLAYWRIGHT_VERSION_PYPI",
    "PLAYWRIGHT_VERSION_NPM",
    "PLAYWRIGHT_VERSION",
    "PLAYWRIGHT_DISTRO",
    "PWMCP_VERSION_PYPI",
    "PWMCP_VERSION_NPM",
    "PWMCP_VERSION",
)

# Canonical locations relative to this file's position.
# This file lives at pwmcp/scripts/_vars.py  →  parent is pwmcp/scripts/,
# parent.parent is pwmcp/.
_SCRIPTS_DIR = Path(__file__).resolve().parent
_PWMCP_DIR = _SCRIPTS_DIR.parent
_VARS_FILE = _PWMCP_DIR / "cmru.vars"
_RESOLVER = _SCRIPTS_DIR / "resolve-playwright-version.py"


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


def _run_resolver() -> None:
    """Run resolve-playwright-version.py as a subprocess.  Propagates non-zero exit."""
    print("[INFO] cmru.vars missing or incomplete — running resolve-playwright-version.py …",
          file=sys.stderr)
    result = subprocess.run(
        [sys.executable, str(_RESOLVER)],
        check=False,
    )
    if result.returncode != 0:
        print(
            f"[ERROR] resolve-playwright-version.py exited with code {result.returncode}.",
            file=sys.stderr,
        )
        raise SystemExit(result.returncode)


def _is_complete(vars_map: dict[str, str]) -> bool:
    return all(vars_map.get(k) for k in _REQUIRED_KEYS)


def load_vars() -> dict[str, str]:
    """Load cmru.vars, self-healing if absent or incomplete.

    Returns a dict of all KEY→value pairs found in cmru.vars *after* the
    self-heal step (if triggered).  Also applies env-wins: for each key
    already set in os.environ the returned dict reflects the env value, and
    os.environ is left unchanged for keys not present in cmru.vars.

    Side-effect: sets os.environ.setdefault for every key found in the file
    so callers that read os.environ directly continue to work as before.
    """
    # First pass — try reading the existing file.
    if _VARS_FILE.exists():
        vars_map = _parse_vars_file(_VARS_FILE)
        if _is_complete(vars_map):
            _apply_to_env(vars_map)
            return _env_merged(vars_map)

    # File absent or incomplete — regenerate.
    _run_resolver()

    # Second pass — the resolver must have written the file.
    if not _VARS_FILE.exists():
        print(f"[ERROR] {_VARS_FILE} still absent after resolve step.", file=sys.stderr)
        raise SystemExit(1)

    vars_map = _parse_vars_file(_VARS_FILE)
    if not _is_complete(vars_map):
        missing = [k for k in _REQUIRED_KEYS if not vars_map.get(k)]
        print(
            f"[ERROR] cmru.vars still missing required keys after resolve: {missing}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    _apply_to_env(vars_map)
    return _env_merged(vars_map)


def _apply_to_env(vars_map: dict[str, str]) -> None:
    """Set os.environ for any key not already present (env-wins)."""
    for key, value in vars_map.items():
        os.environ.setdefault(key, value)


def _env_merged(vars_map: dict[str, str]) -> dict[str, str]:
    """Return vars_map with env-override applied (env value wins if key already set)."""
    merged = dict(vars_map)
    for key in vars_map:
        if key in os.environ:
            merged[key] = os.environ[key]
    return merged
