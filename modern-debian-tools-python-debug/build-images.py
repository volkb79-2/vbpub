#!/usr/bin/env python3
"""Build modern-debian-tools-python-debug images using config-driven step runner."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
MANAGER_SRC = REPO_ROOT / "release-manager" / "src"

sys.path.insert(0, str(MANAGER_SRC))

from release_manager.step_runner import run_step  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build modern-debian-tools-python-debug images")
    parser.add_argument(
        "--ignore-new-releases",
        action="store_true",
        help=(
            "Continue build even when dynamic pre-check detects newer stable devcontainer "
            "Python/Debian releases on MCR."
        ),
    )
    return parser.parse_args()


def _read_bake_default(var_name: str) -> str | None:
    bake_path = ROOT / "docker-bake.hcl"
    if not bake_path.exists():
        return None
    content = bake_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'variable\s+"{re.escape(var_name)}"\s*\{{[^}}]*?default\s*=\s*"([^"]+)"',
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        return None
    return match.group(1).strip()


def ensure_devcontainers_base_from_bake_defaults() -> None:
    legacy_pinned = os.getenv("DEVCONTAINERS_BASE_STABLE")
    if legacy_pinned and not os.getenv("DEVCONTAINERS_BASE_PINNED"):
        os.environ.setdefault("DEVCONTAINERS_BASE_PINNED", legacy_pinned)

    if os.getenv("DEVCONTAINERS_BASE_PINNED") and os.getenv("DEVCONTAINERS_BASE_DEV"):
        return

    latest_python = _read_bake_default("LATEST_KNOWN_PYTHON")
    latest_debian = _read_bake_default("LATEST_KNOWN_DEBIAN")
    if not latest_python or not latest_debian:
        return

    os.environ.setdefault(
        "DEVCONTAINERS_BASE_PINNED",
        f"mcr.microsoft.com/devcontainers/python:{latest_python}-{latest_debian}",
    )
    os.environ.setdefault(
        "DEVCONTAINERS_BASE_DEV",
        f"mcr.microsoft.com/devcontainers/python:dev-{latest_python}-{latest_debian}",
    )


def main() -> None:
    args = parse_args()

    ensure_devcontainers_base_from_bake_defaults()

    if args.ignore_new_releases:
        os.environ["DEVCONTAINERS_IGNORE_NEW_RELEASES"] = "true"

    config_path = ROOT / "build-push.toml"
    # Ensure package-manifests-versioned directory exists to avoid bake/build failures
    manifests_dir = ROOT / "package-manifests-versioned"
    if not manifests_dir.exists():
        print(f"[INFO] Creating missing manifests directory: {manifests_dir}")
        manifests_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_step(config_path, "build-images", None)
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 3 and exc.cmd and "resolve-devcontainers-release.py" in " ".join(map(str, exc.cmd)):
            if exc.stderr:
                print(exc.stderr, file=sys.stderr, end="" if exc.stderr.endswith("\n") else "\n")
            print(
                "\n[ERROR] Build stopped because newer stable devcontainer release(s) were detected.",
                file=sys.stderr,
            )
            print(
                "[HINT] Update LATEST_KNOWN_PYTHON/LATEST_KNOWN_DEBIAN in docker-bake.hcl "
                "to adopt the new baseline, or continue intentionally with:",
                file=sys.stderr,
            )
            print("[HINT]   ./build-images.py --ignore-new-releases", file=sys.stderr)
            raise SystemExit(3) from None
        print("[ERROR] Build failed. Review command output and log path above.", file=sys.stderr)
        raise SystemExit(exc.returncode) from None


if __name__ == "__main__":
    main()
