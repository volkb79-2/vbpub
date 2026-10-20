#!/usr/bin/env python3
"""Build the pwmcp stack bundle (consumer-facing tar.gz).

Reads PWMCP_VERSION from cmru.vars (written by CMRU's pwmcp prepare phase).
Uses cmru.bundle for the actual archive creation.

Output: pwmcp/dist/pwmcp-<version>.tar.gz
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from pathlib import Path
import tomllib

from cli_extended import RegisteredCli
from cmru.bundle import create_archive, parse_config, run_bundle
from cmru.manifest import build_bundle_manifest, write_manifest
from cmru.release import sha256_file

PWMCP_DIR = Path(__file__).resolve().parent.parent
BUNDLE_TOML = PWMCP_DIR / "bundle.toml"
PROJECT_TOML = PWMCP_DIR / "cmru.toml"
OUTPUT_BUNDLE = PWMCP_DIR / "dist" / "bundle"


def _write_installer_manifest(pwmcp_version: str) -> None:
    config = tomllib.loads(PROJECT_TOML.read_text(encoding="utf-8"))
    wheel_specs = config.get("project", {}).get("installer", {}).get("wheels", [])
    manifest = build_bundle_manifest(
        project="pwmcp",
        tag=f"pwmcp-v{pwmcp_version}",
        bundle_root=OUTPUT_BUNDLE,
    )
    installer_wheels: set[Path] = set()
    for spec in wheel_specs:
        pattern = spec["path"]
        distribution = spec["distribution"]
        matches = sorted(
            path for path in OUTPUT_BUNDLE.glob(pattern)
            if path.is_file() and fnmatch.fnmatch(path.name, Path(pattern).name)
        )
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one bundled wheel for {distribution!r} ({pattern}), got {len(matches)}"
            )
        wheel = matches[0]
        installer_wheels.add(wheel.resolve())
        manifest[distribution] = {
            "wheel": wheel.name,
            "sha256": sha256_file(wheel),
            "size": wheel.stat().st_size,
        }
    unlisted_wheels = sorted(
        path for path in OUTPUT_BUNDLE.glob("wheels/*.whl")
        if path.resolve() not in installer_wheels
    )
    if unlisted_wheels:
        names = ", ".join(path.name for path in unlisted_wheels)
        raise RuntimeError(
            "bundle contains wheel(s) missing from [project.installer.wheels]: " + names
        )
    write_manifest(manifest, OUTPUT_BUNDLE / "manifest.json")

# Strict prepared-coordinate loader and shared script registration (sibling _vars.py).
# `cli_extended` and `cmru` are the INSTALLED packages (CX-D3), not source paths.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _vars import load_vars, run_script, script_cli  # noqa: E402


def _build(_args: argparse.Namespace) -> None:
    load_vars()

    pwmcp_version = os.environ.get("PWMCP_VERSION", "")
    if not pwmcp_version:
        print("[ERROR] PWMCP_VERSION not set in cmru.vars", file=sys.stderr)
        raise SystemExit(1)

    print(f"[INFO] Building bundle for pwmcp-{pwmcp_version}")

    run_bundle(BUNDLE_TOML)
    _write_installer_manifest(pwmcp_version)
    archive = create_archive(parse_config(BUNDLE_TOML))
    print(f"[INFO] Bundle: {archive}")


def build_cli() -> RegisteredCli:
    """Import-safe registration: this script takes no operands."""
    return script_cli(
        "build-bundle.py",
        long_name="stack bundle build",
        description="Build the consumer-facing pwmcp stack bundle into dist/ from the prepared coordinate.",
        handler=_build,
        preview=lambda _args: "would build dist/pwmcp-v<version>.tar.xz from bundle.toml",
    )


def main(argv: list[str] | None = None) -> None:
    run_script(build_cli(), argv)


if __name__ == "__main__":
    main()
