#!/usr/bin/env python3
"""Build the pwmcp stack bundle (consumer-facing tar.gz).

Reads PWMCP_VERSION from .release-vars (written by resolve-playwright-version.py).
Delegates to release_manager.bundle_builder for the actual archive creation.

Output: pwmcp/dist/pwmcp-<version>.tar.gz
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PWMCP_DIR = Path(__file__).resolve().parent.parent
RELEASE_VARS_FILE = PWMCP_DIR / ".release-vars"
BUNDLE_TOML = PWMCP_DIR / "bundle.toml"

RELEASE_MANAGER_SRC = PWMCP_DIR.parent / "release-manager" / "src"


def load_release_vars(path: Path) -> None:
    if not path.exists():
        print(f"[ERROR] {path} not found — run resolve-playwright-version.py first", file=sys.stderr)
        raise SystemExit(1)
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    load_release_vars(RELEASE_VARS_FILE)

    pwmcp_version = os.environ.get("PWMCP_VERSION", "")
    if not pwmcp_version:
        print("[ERROR] PWMCP_VERSION not set in .release-vars", file=sys.stderr)
        raise SystemExit(1)

    print(f"[INFO] Building bundle for pwmcp-{pwmcp_version}")

    sys.path.insert(0, str(RELEASE_MANAGER_SRC))
    from release_manager.bundle_builder import run_bundle  # noqa: PLC0415

    archive = run_bundle(BUNDLE_TOML)
    print(f"[INFO] Bundle: {archive}")


if __name__ == "__main__":
    main()
