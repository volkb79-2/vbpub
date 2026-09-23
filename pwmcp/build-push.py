#!/usr/bin/env python3
"""Build and push the pwmcp-playwright Docker image through managed BuildKit.

Usage:
  python3 build-push.py --build   # Build images locally through mdt-managed
  python3 build-push.py --push    # Login to GHCR and push images

Reads the complete Playwright/PWMCP release coordinate, including the pinned
base-image manifest digest, from cmru.vars (written by
scripts/resolve-playwright-version.py). CMRU's prepare phase is the sole
writer; this script refuses an absent or incomplete prepared coordinate.

The wrapper verifies the host-managed remote builder declared in ``cmru.toml``;
it never creates an ephemeral Docker-container worker. Credentials for push
(from the CMRU environment or explicitly exported):
  GITHUB_USERNAME
  GITHUB_PUSH_PAT
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

PWMCP_DIR = Path(__file__).resolve().parent
BUILD_CONFIG = PWMCP_DIR / "cmru.toml"

# Strict prepared-coordinate loader (pwmcp/scripts/_vars.py).
sys.path.insert(0, str(PWMCP_DIR / "scripts"))
from _vars import load_vars  # noqa: E402
sys.path.insert(0, str(PWMCP_DIR.parent / "cmru" / "src"))
from cmru.ghcr import GitHubPackages  # noqa: E402


def log(msg: str) -> None:
    print(f"[INFO] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def sync_ghcr_package_visibility(package_names: list[str]) -> None:
    """Mirror repo visibility onto GHCR packages that this release just pushed."""
    names = [name.strip() for name in package_names if name and str(name).strip()]
    if not names:
        return

    username = os.environ.get("GITHUB_USERNAME", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    token = os.environ.get("GITHUB_PUSH_PAT", "").strip()
    owner_type = os.environ.get("GITHUB_OWNER_TYPE", "").strip()
    missing = [
        name for name, value in {
            "GITHUB_USERNAME": username,
            "GITHUB_REPO": repo,
            "GITHUB_PUSH_PAT": token,
            "GITHUB_OWNER_TYPE": owner_type,
        }.items() if not value
    ]
    if missing:
        fail(
            "GHCR visibility sync requires " + ", ".join(missing) +
            "; run through CMRU or export the release identity explicitly"
        )

    ghcr = GitHubPackages(username, repo, token, owner_type)
    repo_visibility = ghcr.repo_visibility()
    log(f"Mirroring GHCR package visibility to {repo_visibility}: {', '.join(names)}")
    for package_name in names:
        ghcr.mirror_package_visibility(package_name, expected_visibility=repo_visibility)
        log(f"Synced {package_name} visibility to {repo_visibility}")


def run(argv: list[str], cwd: Path | None = None) -> None:
    log(f"$ {' '.join(argv)}")
    subprocess.run(argv, check=True, cwd=str(cwd or PWMCP_DIR))


@dataclass(frozen=True)
class BuilderConfig:
    name: str
    endpoint: str


def load_builder_config(path: Path = BUILD_CONFIG) -> BuilderConfig:
    with path.open("rb") as handle:
        raw = tomllib.load(handle).get("env", {})
    required = ("BUILDX_BUILDER", "BUILDKIT_HOST")
    missing = [key for key in required if key not in raw]
    if missing:
        fail(f"{path.name} [env] is missing managed BuildKit keys: {', '.join(missing)}")
    name = str(raw["BUILDX_BUILDER"]).strip()
    endpoint = str(raw["BUILDKIT_HOST"]).strip()
    if not name:
        fail(f"{path.name} [env].BUILDX_BUILDER must be non-empty")
    if not endpoint.startswith("unix:///"):
        fail(f"{path.name} [env].BUILDKIT_HOST must be an absolute Unix endpoint")
    return BuilderConfig(name=name, endpoint=endpoint)


def _inspect_builder(config: BuilderConfig, *, bootstrap: bool) -> str:
    argv = ["docker", "buildx", "inspect", config.name]
    if bootstrap:
        argv.append("--bootstrap")
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        phase = "bootstrap" if bootstrap else "registration"
        detail = (result.stderr or result.stdout or "command failed").strip()
        fail(
            f"managed BuildKit builder {config.name!r} failed {phase} verification at "
            f"{config.endpoint}: {detail[:400]}"
        )
    return result.stdout


def _assert_remote_builder(config: BuilderConfig, output: str) -> None:
    driver = ""
    endpoints: list[str] = []
    for line in output.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.strip()
        if key.strip() == "Driver":
            driver = value
        elif key.strip() == "Endpoint" and value:
            endpoints.append(value)
    if driver != "remote":
        fail(
            f"builder {config.name!r} is not the managed remote builder: "
            f"driver={driver or '<missing>'!r}; expected driver='remote'"
        )
    if endpoints != [config.endpoint]:
        actual = ", ".join(endpoints) or "<missing>"
        fail(
            f"builder {config.name!r} endpoint is not the configured managed socket: "
            f"got {actual!r}, expected {config.endpoint!r}"
        )


def ensure_builder(config: BuilderConfig) -> None:
    """Verify the durable host-managed remote; never create a worker here."""
    _assert_remote_builder(config, _inspect_builder(config, bootstrap=False))
    _assert_remote_builder(config, _inspect_builder(config, bootstrap=True))
    log(f"Using host-managed BuildKit remote {config.name} at {config.endpoint}")


def do_build() -> None:
    load_vars()
    builder = load_builder_config()
    ensure_builder(builder)
    pw_version = os.environ.get("PLAYWRIGHT_VERSION", "?")
    pwmcp_version = os.environ.get("PWMCP_VERSION", "?")
    log(
        f"Building pwmcp coordinated release "
        f"PLAYWRIGHT_VERSION={pw_version} PWMCP_VERSION={pwmcp_version}"
    )
    run(["docker", "buildx", "bake", "--builder", builder.name, "all", "--load"], cwd=PWMCP_DIR)
    log("Build complete.")


def do_push() -> None:
    load_vars()
    builder = load_builder_config()
    ensure_builder(builder)

    username = os.environ.get("GITHUB_USERNAME", "")
    pat = os.environ.get("GITHUB_PUSH_PAT", "")
    if not username or not pat:
        fail("GITHUB_USERNAME and GITHUB_PUSH_PAT are required for push; run through CMRU or export both")

    log(f"Logging in to ghcr.io as {username}")
    proc = subprocess.run(
        ["docker", "login", "ghcr.io", "-u", username, "--password-stdin"],
        input=pat.encode(), check=True,
    )
    del proc

    pw_version = os.environ.get("PLAYWRIGHT_VERSION", "?")
    pwmcp_version = os.environ.get("PWMCP_VERSION", "?")
    log(
        f"Pushing pwmcp coordinated release "
        f"PLAYWRIGHT_VERSION={pw_version} PWMCP_VERSION={pwmcp_version}"
    )
    run(["docker", "buildx", "bake", "--builder", builder.name, "all", "--push"], cwd=PWMCP_DIR)
    package_names = [
        name.strip()
        for name in (os.environ.get("GHCR_PACKAGE_NAMES") or "pwmcp").split(",")
        if name.strip()
    ]
    sync_ghcr_package_visibility(package_names)
    log("Push complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/push pwmcp images")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true", help="Build images locally")
    group.add_argument("--push", action="store_true", help="Push images to GHCR")
    args = parser.parse_args()

    if args.build:
        do_build()
    else:
        do_push()


if __name__ == "__main__":
    main()
