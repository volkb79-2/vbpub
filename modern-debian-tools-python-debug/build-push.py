#!/usr/bin/env python3
"""Build and push modern-debian-tools-python-debug images.

NOTE: When invoked via cmru release, the built-in oci-image handler replaces this script.
This file remains for manual/local use outside cmru.

Usage:
  ./build-push.py --build        # Resolve env, build images, save state
  ./build-push.py --push         # Load saved state, push images (skips resolver)
  ./build-push.py --rebuild      # Build then push sequentially
                                 # (push/repack publish during build; their push step is a no-op)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(REPO_ROOT / "cmru" / "src"))

from cmru.ghcr import GitHubPackages  # noqa: E402
from cmru.runner import run_step  # noqa: E402

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


def _read_cmru_env_default(var_name: str) -> str | None:
    """Read a default from cmru.build.toml [env]."""
    build_path = ROOT / "cmru.build.toml"
    if not build_path.exists():
        return None
    content = build_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'^\s*{re.escape(var_name)}\s*=\s*"([^"]+)"\s*$',
        re.MULTILINE,
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


def load_cmru_credentials() -> None:
    """Seed GitHub identity from cmru.toml / cmru.secret.toml when running standalone."""
    if (
        os.environ.get("GITHUB_USERNAME")
        and os.environ.get("GITHUB_REPO")
        and os.environ.get("GITHUB_OWNER_TYPE")
        and os.environ.get("GITHUB_PUSH_PAT")
    ):
        return

    repo_root = ROOT.parent
    try:
        import tomllib

        cmru_toml = repo_root / "cmru.toml"
        if cmru_toml.exists():
            with cmru_toml.open("rb") as fh:
                config = tomllib.load(fh)
            github = config.get("github", {})
            if not os.environ.get("GITHUB_USERNAME") and github.get("owner"):
                os.environ["GITHUB_USERNAME"] = str(github["owner"])
            if not os.environ.get("GITHUB_REPO") and github.get("repo"):
                os.environ["GITHUB_REPO"] = str(github["repo"])
            if not os.environ.get("GITHUB_OWNER_TYPE") and github.get("owner_type"):
                os.environ["GITHUB_OWNER_TYPE"] = str(github["owner_type"])

        if not os.environ.get("GITHUB_PUSH_PAT"):
            token = os.environ.get("GITHUB_TOKEN", "")
            if not token:
                secret_toml = repo_root / "cmru.secret.toml"
                if secret_toml.exists():
                    with secret_toml.open("rb") as fh:
                        secret = tomllib.load(fh)
                    token = str(secret.get("github", {}).get("token", ""))
            if token:
                os.environ["GITHUB_PUSH_PAT"] = token
    except Exception:
        pass


def sync_ghcr_package_visibility(package_names: list[str]) -> None:
    """Mirror repo visibility onto GHCR packages that this release just pushed."""
    names = [name.strip() for name in package_names if name and str(name).strip()]
    if not names:
        return

    username = os.environ.get("GITHUB_USERNAME", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    token = os.environ.get("GITHUB_PUSH_PAT", "").strip()
    owner_type = os.environ.get("GITHUB_OWNER_TYPE", "").strip()
    if not username or not repo or not token or not owner_type:
        sys.stderr.write("[WARN] Skipping GHCR visibility sync (missing GitHub identity/token)\n")
        return

    ghcr = GitHubPackages(username, repo, token, owner_type)
    repo_visibility = ghcr.repo_visibility()
    sys.stderr.write(
        f"[INFO] Syncing GHCR package visibility to {repo_visibility}: {', '.join(names)}\n"
    )
    for package_name in names:
        ghcr.mirror_package_visibility(package_name, expected_visibility=repo_visibility)
        sys.stderr.write(
            f"[INFO] Synced {package_name} visibility to {repo_visibility}\n"
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
    load_cmru_credentials()
    release_image_flow = (
        os.getenv("RELEASE_IMAGE_FLOW")
        or _read_cmru_env_default("RELEASE_IMAGE_FLOW")
        or "push"
    ).strip().lower()
    if release_image_flow not in {"load", "push", "repack"}:
        raise SystemExit(
            f"[ERROR] RELEASE_IMAGE_FLOW must be 'load', 'push', or 'repack', got: {release_image_flow!r}"
        )

    # Compute BUILD_DATE first (resolver depends on it). An explicit coordinate
    # is authoritative so a failed release can be retried without inventing a
    # nested counter suffix. Only an implicit date uses the local build counter.
    explicit_build_date = os.getenv("BUILD_DATE")
    if explicit_build_date:
        build_date = explicit_build_date
    else:
        base_date = datetime.now(timezone.utc).strftime("%Y%m%d")
        build_date = compute_build_date_with_counter(base_date)
    os.environ["BUILD_DATE"] = build_date
    sys.stderr.write(f"[INFO] BUILD_DATE={build_date}\n")

    build_timestamp = os.getenv("BUILD_TIMESTAMP") or (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    os.environ["BUILD_TIMESTAMP"] = build_timestamp
    sys.stderr.write(f"[INFO] BUILD_TIMESTAMP={build_timestamp}\n")

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
    env_vars["BUILD_TIMESTAMP"] = build_timestamp
    env_vars["RELEASE_IMAGE_FLOW"] = release_image_flow
    save_build_env(env_vars)

    # Ensure manifests directory
    ensure_manifests_dir()

    # Run build
    sys.stderr.write(
        f"[INFO] Step 3/3: Running release bake flow (RELEASE_IMAGE_FLOW={release_image_flow}) ...\n"
    )
    config_path = ROOT / "cmru.build.toml"
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

    # Post-build manifest extraction: copy the in-image canonical manifest
    # into package-manifests-versioned/ so the committed version matches.
    extract_manifests(build_date, env_vars)


_MANIFEST_PATH = "/usr/local/share/modern-debian-tools-python-debug/manifest.md"


def extract_manifests(build_date: str, env_vars: dict[str, str]) -> None:
    """Extract canonical manifests from freshly built images.

    In repack mode, the release worker exports only the manifest from the
    repacked OCI layout. Other modes use the governed builder to export it from
    the published registry image without loading it into dockerd's image store.
    """
    username = os.environ.get("GITHUB_USERNAME") or env_vars.get("GITHUB_USERNAME", "volkb79-2")
    repo = os.environ.get("GITHUB_REPO") or env_vars.get("GITHUB_REPO", "vbpub")

    # Enumerate bake targets from the bake group.
    BAKE_FILE = ROOT / "docker-bake.hcl"
    try:
        result = subprocess.run(
            ["docker", "buildx", "bake", "-f", str(BAKE_FILE), "all", "--print"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(
            f"[WARN] Failed to enumerate bake targets for manifest extraction: {exc}\n"
        )
        return

    bake = json.loads(result.stdout)
    target_names = ((bake.get("group") or {}).get("all") or {}).get("targets") or []
    targets = bake.get("target") or {}

    manifests_root = ROOT / "package-manifests-versioned"
    release_flow = (env_vars.get("RELEASE_IMAGE_FLOW") or "load").strip().lower()
    repack_work = Path(
        os.environ.get("REPACK_WORK_DIR")
        or _read_cmru_env_default("REPACK_WORK_DIR")
        or "build/repack"
    )
    if not repack_work.is_absolute():
        repack_work = ROOT / repack_work
    extracted: list[str] = []
    failed: list[str] = []

    for name in target_names:
        spec = targets.get(name) or {}
        args = spec.get("args") or {}
        tags = spec.get("tags") or []
        if not tags:
            continue
        first_tag = str(tags[0])

        if release_flow == "repack":
            safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
            manifest_file = repack_work / "manifests" / safe_name / "manifest.md"
            try:
                manifest_content = manifest_file.read_text(encoding="utf-8")
            except OSError as exc:
                sys.stderr.write(
                    f"[WARN] Failed to read repacked manifest {manifest_file}: {exc}\n"
                )
                failed.append(name)
                continue
        else:
            try:
                with tempfile.TemporaryDirectory(prefix="mdt-manifest-") as temp_dir:
                    subprocess.run(
                        [
                            "docker",
                            "buildx",
                            "build",
                            "--file",
                            "scripts/repack-push.Dockerfile",
                            "--target",
                            "manifest",
                            "--build-context",
                            f"repacked=docker-image://{first_tag}",
                            "--output",
                            f"type=local,dest={temp_dir}",
                            ".",
                        ],
                        cwd=str(ROOT),
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=180,
                    )
                    manifest_content = (Path(temp_dir) / "manifest.md").read_text(
                        encoding="utf-8"
                    )
            except (subprocess.CalledProcessError, OSError) as exc:
                sys.stderr.write(
                    f"[WARN] Failed to extract manifest from {first_tag}: {exc}\n"
                )
                failed.append(name)
                continue

        # Derive package name and tag for the output path.
        debian = args.get("DEBIAN_VERSION") or ""
        python = args.get("PYTHON_VERSION") or ""
        install_php = str(args.get("INSTALL_PHP") or "").strip().lower() == "true"
        php_version = str(args.get("PHP_VERSION") or "").strip()
        variant = f"php{php_version}" if install_php and php_version else ""

        # Derive package name from PACKAGE_MANIFEST_SOURCE.
        manifest_source = args.get("PACKAGE_MANIFEST_SOURCE") or ""
        parts = manifest_source.split("/")
        if len(parts) >= 2 and parts[0] == "package-manifests-versioned" and parts[1]:
            package_name = parts[1]
        else:
            is_vsc = any("-vsc-devcontainer" in str(tag) for tag in tags) or name.endswith("-vsc")
            package_name = (
                "modern-debian-tools-python-debug-vsc-devcontainer"
                if is_vsc
                else "modern-debian-tools-python-debug"
            )

        variant_part = f"-{variant}" if variant else ""
        tag_str = f"{debian}-py{python}{variant_part}-{build_date}"
        output_dir = manifests_root / package_name
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{tag_str}.md"

        output_path.write_text(manifest_content, encoding="utf-8")
        extracted.append(str(output_path.relative_to(ROOT)))
        sys.stderr.write(f"[INFO] Extracted manifest: {output_path}\n")

    if extracted:
        sys.stderr.write(
            f"[INFO] Extracted {len(extracted)} manifest(s) to "
            f"package-manifests-versioned/\n"
        )
    if failed:
        sys.stderr.write(
            f"[WARN] Failed to extract {len(failed)} manifest(s): "
            f"{', '.join(failed)}\n"
        )


def do_push() -> None:
    """Push phase: load saved env, push to registry (no resolver re-run)."""
    sys.stderr.write("[INFO] === modern-debian-tools-python-debug: push ===\n")
    sys.stderr.write("[INFO] Step 1/3: Loading saved build environment...\n")

    load_cmru_credentials()

    # Load and apply saved env vars
    env_vars = load_build_env()
    apply_env_to_os(env_vars)

    release_image_flow = (env_vars.get("RELEASE_IMAGE_FLOW") or "load").strip().lower()
    sys.stderr.write(
        f"[INFO] Step 2/3: Running release push flow (RELEASE_IMAGE_FLOW={release_image_flow}) ...\n"
    )
    config_path = ROOT / "cmru.build.toml"
    try:
        run_step(config_path, "push-images", None)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"[ERROR] Push failed. Exit code: {exc.returncode}\n")
        raise SystemExit(exc.returncode) from None

    sys.stderr.write("[INFO] Step 3/3: Syncing GHCR package visibility ...\n")
    # PHP 8.5 is a TAG variant of the base families now, not a separate package name —
    # only two GHCR package families are ever published (see docker-bake.hcl "all" group).
    package_names = [
        name.strip()
        for name in (
            env_vars.get("GHCR_PACKAGE_NAMES")
            or "modern-debian-tools-python-debug,modern-debian-tools-python-debug-vsc-devcontainer"
        ).split(",")
        if name.strip()
    ]
    sync_ghcr_package_visibility(package_names)


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
