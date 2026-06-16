#!/usr/bin/env python3
"""Build and push modern-debian-tools-python-debug images.

Usage:
  ./build-push.py --build        # Resolve env, build images, save state
  ./build-push.py --push         # Load saved state, push images (skips resolver)
  ./build-push.py --rebuild      # Build then push sequentially
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(REPO_ROOT / "ciu-forge" / "src"))

from ciu_forge.runner import run_step  # noqa: E402

BUILD_ENV_FILE = ROOT / ".build-env.json"
RESOLVER_SCRIPT = ROOT / "scripts" / "resolve-devcontainers-release.py"
COUNTER_DIR = ROOT / "logs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and/or push modern-debian-tools-python-debug images"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true", help="Resolve env and build images locally")
    mode.add_argument("--push", action="store_true", help="Push previously built images to registry")
    mode.add_argument(
        "--rebuild", action="store_true", help="Build then push sequentially"
    )
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
    """Read a variable default from docker-bake.hcl."""
    bake_path = ROOT / "docker-bake.hcl"
    if not bake_path.exists():
        return None
    content = bake_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'variable\s+"{re.escape(var_name)}"\s*\{{[^}}]*?default\s*=\s*"([^"]+)"',
        re.DOTALL,
    )
    match = pattern.search(content)
    return match.group(1).strip() if match else None


def ensure_devcontainers_base_from_bake_defaults() -> None:
    """Set DEVCONTAINERS_BASE_PINNED/DEV from bake defaults if not in env."""
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


def run_resolver() -> dict[str, str]:
    """Run resolve-devcontainers-release.py and return KEY=VALUE pairs as a dict.

    Handles exit code 3 (newer releases detected) with a clean error message.
    """
    if not RESOLVER_SCRIPT.exists():
        raise FileNotFoundError(f"Resolver script not found: {RESOLVER_SCRIPT}")

    result = subprocess.run(
        [sys.executable, str(RESOLVER_SCRIPT)],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,  # capture KEY=VALUE output for parsing
        # stderr inherits parent → streams live to terminal
    )

    if result.returncode == 3:
        sys.stderr.write(
            "\n[ERROR] Build stopped because newer stable devcontainer release(s) were detected.\n"
        )
        sys.stderr.write(
            "[HINT] Update LATEST_KNOWN_PYTHON/LATEST_KNOWN_DEBIAN in docker-bake.hcl "
            "to adopt the new baseline, or continue intentionally with:\n"
        )
        sys.stderr.write("[HINT]   ./build-push.py --build --ignore-new-releases\n")
        raise SystemExit(3)

    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, RESOLVER_SCRIPT)

    env_vars: dict[str, str] = {}
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            sys.stderr.write(f"[WARN] Ignoring malformed resolver output: {line}\n")
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            sys.stderr.write(f"[WARN] Ignoring empty key from resolver: {line}\n")
            continue
        env_vars[key] = value

    return env_vars


def save_build_env(env_vars: dict[str, str]) -> None:
    """Save resolved env vars to .build-env.json with a timestamp."""
    data = {
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "vars": env_vars,
    }
    BUILD_ENV_FILE.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    sys.stderr.write(f"[INFO] Saved build environment to {BUILD_ENV_FILE}\n")


def load_build_env() -> dict[str, str]:
    """Load resolved env vars from .build-env.json."""
    if not BUILD_ENV_FILE.exists():
        sys.stderr.write(
            f"[ERROR] No build environment found at {BUILD_ENV_FILE}.\n"
        )
        sys.stderr.write(
            "[HINT] Run './build-push.py --build' first to create the build state.\n"
        )
        raise SystemExit(1)

    data = json.loads(BUILD_ENV_FILE.read_text(encoding="utf-8"))
    resolved_at = data.get("resolved_at", "unknown")
    sys.stderr.write(
        f"[INFO] Loaded build environment from {BUILD_ENV_FILE} "
        f"(resolved at {resolved_at})\n"
    )
    return data.get("vars", {})


def apply_env_to_os(env_vars: dict[str, str]) -> None:
    """Set resolved env vars into os.environ so step_runner can inject them via bake_set_vars."""
    for key, value in env_vars.items():
        os.environ[key] = value


def compute_build_date_with_counter(base_date: str) -> str:
    """Apply -N build counter and return the final BUILD_DATE.

    Counter file: logs/build-counter-{base_date}.txt

    - First build of the day: no suffix (e.g., 20260604)
    - Subsequent builds: -2, -3, ... suffix
    """
    COUNTER_DIR.mkdir(parents=True, exist_ok=True)
    counter_file = COUNTER_DIR / f"build-counter-{base_date}.txt"

    if counter_file.exists():
        counter_raw = counter_file.read_text(encoding="utf-8").strip()
    else:
        counter_raw = "0"

    if not counter_raw.isdigit():
        raise ValueError(f"Invalid build counter value in {counter_file}: {counter_raw}")

    counter = int(counter_raw)
    if counter == 0:
        # First build of the day — write next counter, return bare date
        counter_file.write_text("2", encoding="utf-8")
        return base_date

    # Nth build — append -{counter} suffix (starts at -2)
    build_date = f"{base_date}-{counter}"
    counter_file.write_text(str(counter + 1), encoding="utf-8")
    return build_date


def ensure_manifests_dir() -> None:
    """Create package-manifests-versioned directory if missing."""
    manifests_dir = ROOT / "package-manifests-versioned"
    if not manifests_dir.exists():
        sys.stderr.write(f"[INFO] Creating missing manifests directory: {manifests_dir}\n")
        manifests_dir.mkdir(parents=True, exist_ok=True)


def do_build(ignore_new_releases: bool) -> None:
    """Build phase: resolve env, compute counter, build images."""
    sys.stderr.write("[INFO] === modern-debian-tools-python-debug: build ===\n")

    ensure_devcontainers_base_from_bake_defaults()

    # Compute BUILD_DATE first (resolver depends on it)
    base_date = os.getenv("BUILD_DATE") or datetime.now(timezone.utc).strftime("%Y%m%d")
    build_date = compute_build_date_with_counter(base_date)
    os.environ["BUILD_DATE"] = build_date
    sys.stderr.write(f"[INFO] BUILD_DATE={build_date}\n")

    sys.stderr.write(
        "[INFO] Step 1/3: Resolving environment "
        "(MCR check, tool versions, CIU coordinates)...\n"
    )

    if ignore_new_releases:
        os.environ["DEVCONTAINERS_IGNORE_NEW_RELEASES"] = "true"

    # Run the resolver
    env_vars = run_resolver()

    # Apply resolved vars to environment
    apply_env_to_os(env_vars)

    # Include BUILD_DATE in saved state
    env_vars["BUILD_DATE"] = build_date
    save_build_env(env_vars)

    # Ensure manifests directory
    ensure_manifests_dir()

    # Run build
    sys.stderr.write("[INFO] Step 3/3: Running docker buildx bake --load ...\n")
    config_path = ROOT / "build-push.toml"
    try:
        run_step(config_path, "build-images", None)
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 3:
            sys.stderr.write(
                "\n[ERROR] Build stopped due to new devcontainer release detection.\n"
            )
            raise SystemExit(3) from None
        sys.stderr.write(f"[ERROR] Build failed. Exit code: {exc.returncode}\n")
        raise SystemExit(exc.returncode) from None


def do_push() -> None:
    """Push phase: load saved env, push to registry (no resolver re-run)."""
    sys.stderr.write("[INFO] === modern-debian-tools-python-debug: push ===\n")
    sys.stderr.write("[INFO] Step 1/2: Loading saved build environment...\n")

    # Load and apply saved env vars
    env_vars = load_build_env()
    apply_env_to_os(env_vars)

    sys.stderr.write(
        f"[INFO] Step 2/2: Running docker buildx bake --push ...\n"
    )
    config_path = ROOT / "build-push.toml"
    try:
        run_step(config_path, "push-images", None)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"[ERROR] Push failed. Exit code: {exc.returncode}\n")
        raise SystemExit(exc.returncode) from None


def main() -> None:
    args = parse_args()

    if args.build:
        do_build(args.ignore_new_releases)
    elif args.push:
        do_push()
    elif args.rebuild:
        do_build(args.ignore_new_releases)
        do_push()


if __name__ == "__main__":
    main()