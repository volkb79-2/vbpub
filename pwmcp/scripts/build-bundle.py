#!/usr/bin/env python3
"""Build the pwmcp stack bundle (consumer-facing tar.gz).

Reads PWMCP_VERSION from cmru.vars (written by resolve-playwright-version.py).
Delegates to release_manager.bundle_builder for the actual archive creation.

Output: pwmcp/dist/pwmcp-<version>.tar.gz
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PWMCP_DIR = Path(__file__).resolve().parent.parent
BUNDLE_TOML = PWMCP_DIR / "bundle.toml"

CIU_FORGE_SRC = PWMCP_DIR.parent / "cmru" / "src"

# Shared self-healing vars loader (sibling _vars.py in pwmcp/scripts/).
sys.path.insert(0, str(Path(__file__).resolve().parent))  # ensure script dir wins for _vars
from _vars import load_vars  # noqa: E402


def main() -> None:
    load_vars()

    pwmcp_version = os.environ.get("PWMCP_VERSION", "")
    if not pwmcp_version:
        print("[ERROR] PWMCP_VERSION not set in cmru.vars", file=sys.stderr)
        raise SystemExit(1)

    print(f"[INFO] Building bundle for pwmcp-{pwmcp_version}")

    sys.path.insert(0, str(CIU_FORGE_SRC))
    from cmru.bundle import run_bundle  # noqa: PLC0415

    archive = run_bundle(BUNDLE_TOML)
    print(f"[INFO] Bundle: {archive}")


if __name__ == "__main__":
    main()
