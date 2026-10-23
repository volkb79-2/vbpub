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
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from cli_extended import OptionSpec, RegisteredCli
from cmru.ghcr import GitHubPackages

PWMCP_DIR = Path(__file__).resolve().parent
BUILD_CONFIG = PWMCP_DIR / "cmru.toml"
ESTATE_CONFIG = PWMCP_DIR.parent / "cmru.orchestration.toml"

# Strict prepared-coordinate loader and the shared script registration
# (pwmcp/scripts/_vars.py). `cli_extended` and `cmru` are the INSTALLED packages
# (CX-D3): this script runs under CMRU, which brings both; gate lanes keep PYTHONPATH
# on the worktree's own sources only.
sys.path.insert(0, str(PWMCP_DIR / "scripts"))
from _vars import load_vars, run_script, script_cli  # noqa: E402


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
    endpoint: str  # empty: a plain builder (e.g. the docker default), nothing to verify beyond it existing


_ENV_REFERENCE = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)(?::-([^{}]*))?\}$")


def _resolve(value: object) -> str:
    """Expand a whole-value ${NAME:-default} reference the way cmru does at load (the environment
    wins, a set-but-empty value counts as set); anything else is the literal."""
    text = str(value).strip()
    match = _ENV_REFERENCE.fullmatch(text)
    if match is None:
        return text
    actual = os.environ.get(match.group(1))
    return actual.strip() if actual is not None else (match.group(2) or "")


def load_builder_config(path: Path = BUILD_CONFIG, estate: Path = ESTATE_CONFIG) -> BuilderConfig:
    """The Buildx builder and BuildKit endpoint: the process environment, else the project's
    cmru.toml [env] over the estate defaults ([orchestration.defaults.env] in cmru.orchestration.toml),
    the same precedence cmru applies. An empty endpoint means a plain builder, not the managed remote."""
    raw: dict = {}
    if estate.is_file():
        with estate.open("rb") as handle:
            raw.update(tomllib.load(handle).get("orchestration", {}).get("defaults", {}).get("env", {}))
    with path.open("rb") as handle:
        raw.update(tomllib.load(handle).get("env", {}))
    required = ("BUILDX_BUILDER", "BUILDKIT_HOST")
    missing = [key for key in required if key not in raw and key not in os.environ]
    if missing:
        fail(f"{path.name} [env] (or the estate defaults) is missing BuildKit keys: {', '.join(missing)}")
    name, endpoint = (os.environ[k].strip() if k in os.environ else _resolve(raw[k]) for k in required)
    if endpoint and not endpoint.startswith("unix:///"):
        fail(f"{path.name} [env].BUILDKIT_HOST must be empty or an absolute Unix endpoint")
    return BuilderConfig(name=name, endpoint=endpoint)


def _inspect_builder(config: BuilderConfig, *, bootstrap: bool) -> str:
    argv = ["docker", "buildx", "inspect", *([config.name] if config.name else [])]
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


def _builder_args(config: BuilderConfig) -> list[str]:
    return ["--builder", config.name] if config.name else []


def ensure_builder(config: BuilderConfig) -> None:
    """Verify the configured builder exists; with a managed endpoint also that it is exactly that
    remote. Never create a worker here."""
    if not config.endpoint:
        _inspect_builder(config, bootstrap=False)
        log(f"Using Buildx builder {config.name or '<current>'} (no managed BuildKit endpoint configured)")
        return
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
    run(["docker", "buildx", "bake", *_builder_args(builder), "all", "--load"], cwd=PWMCP_DIR)
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
    run(["docker", "buildx", "bake", *_builder_args(builder), "all", "--push"], cwd=PWMCP_DIR)
    package_names = [
        name.strip()
        for name in (os.environ.get("GHCR_PACKAGE_NAMES") or "pwmcp").split(",")
        if name.strip()
    ]
    sync_ghcr_package_visibility(package_names)
    log("Push complete.")


def _handle(args: argparse.Namespace) -> None:
    if args.build:
        do_build()
    else:
        do_push()


def _preview(args: argparse.Namespace) -> str:
    if args.build:
        return "would build the pwmcp images locally through the managed BuildKit builder"
    return "would log in to ghcr.io, push the pwmcp images and sync GHCR package visibility"


def build_cli() -> RegisteredCli:
    """Import-safe registration of the script's grammar (``--build`` | ``--push``)."""
    return script_cli(
        "build-push.py",
        long_name="image build and push",
        description="Build or push the pwmcp-playwright images through the managed BuildKit builder.",
        handler=_handle,
        preview=_preview,
        options=(
            OptionSpec(
                ("--build",),
                "build images locally",
                group="OPERATION",
                parser_kwargs={"action": "store_true"},
                mutually_exclusive_group="operation",
                mutually_exclusive_required=True,
            ),
            OptionSpec(
                ("--push",),
                "log in to GHCR and push the images",
                group="OPERATION",
                parser_kwargs={"action": "store_true"},
                mutually_exclusive_group="operation",
                mutually_exclusive_required=True,
            ),
        ),
        examples=("python3 build-push.py --build", "python3 build-push.py --push"),
    )


def main(argv: list[str] | None = None) -> None:
    run_script(build_cli(), argv)


if __name__ == "__main__":
    main()
