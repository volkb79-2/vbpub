#!/usr/bin/env python3
"""Stage build and runtime wheels for the offline CMRU installer bundle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

PWMCP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PWMCP_DIR.parent
CONTRACT = PWMCP_DIR / "pwmcp.contract.json"
CLIENT_PROJECT = PWMCP_DIR / "client" / "pyproject.toml"
RUNTIME_REQUIREMENTS = PWMCP_DIR / "client" / "requirements-runtime.txt"
VENDOR = PWMCP_DIR / "vendor"


def _fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def _github_identity() -> tuple[str, str]:
    try:
        with (REPO_ROOT / "cmru.orchestration.toml").open("rb") as stream:
            config = tomllib.load(stream)
        owner = config["github"]["owner"]
        repo = config["github"]["repo"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        _fail(f"cannot read repository identity from cmru.orchestration.toml: {exc}")
    if not isinstance(owner, str) or not isinstance(repo, str):
        _fail("cmru.orchestration.toml [github].owner and .repo must be strings")
    return owner, repo


def _normalized_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _cli_extended_requirement() -> str:
    for line in RUNTIME_REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        stripped = line.partition("#")[0].strip()
        if stripped and _normalized_distribution(re.split(r"[<>=!~; ]", stripped, 1)[0]) == "cli-extended":
            return stripped
    _fail("client/requirements-runtime.txt must declare cli-extended")


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"(\d+(?:\.\d+)*)", value)
    if not match:
        _fail(f"cli-extended release version is not numeric: {value!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def _minimum_version(requirement: str) -> tuple[int, ...]:
    match = re.search(r">=\s*(\d+(?:\.\d+)*)", requirement)
    if not match:
        _fail(f"cli-extended requirement needs an explicit minimum: {requirement!r}")
    return _version_tuple(match.group(1))


def _stage_cli_extended() -> None:
    try:
        from cmru.hosts.github import GitHubReleaseHost
    except ImportError as exc:
        _fail(f"the installed cmru GitHub release adapter is unavailable: {exc}")

    owner, repo = _github_identity()
    requirement = _cli_extended_requirement()
    release = GitHubReleaseHost(owner, repo, os.environ.get("GITHUB_TOKEN", "")).resolve_latest(
        "cli-extended-v", asset_suffix=".whl"
    )
    if not release:
        _fail("no stable cli-extended wheel release is published")
    version = str(release["version"])
    if _version_tuple(version) < _minimum_version(requirement):
        _fail(f"cli-extended {version} is below client requirement {requirement}")
    asset_name = str(release["asset"])
    if not asset_name.endswith(".whl") or "/" in asset_name or "\\" in asset_name:
        _fail(f"CMRU resolved an invalid cli-extended wheel asset name: {asset_name!r}")
    url = str(release["url"])
    if not url.startswith("https://github.com/"):
        _fail(f"CMRU resolved a non-GitHub HTTPS wheel URL: {url!r}")

    destination = VENDOR / asset_name
    request = urllib.request.Request(url, headers={"User-Agent": "pwmcp-wheel-stage"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read()
    except OSError as exc:
        _fail(f"cannot download {asset_name}: {exc}")
    digest = hashlib.sha256(content).hexdigest()
    if digest.lower() != str(release["sha256"]).lower():
        _fail(f"SHA-256 mismatch for {asset_name}")
    destination.write_bytes(content)
    print(f"[INFO] staged {asset_name} sha256={digest}")


def _stage_pypi_wheels() -> None:
    with CLIENT_PROJECT.open("rb") as stream:
        pyproject = tomllib.load(stream)
    build_requirements = pyproject.get("build-system", {}).get("requires", [])
    if not isinstance(build_requirements, list) or not all(
        isinstance(item, str) and item for item in build_requirements
    ):
        _fail("client/pyproject.toml build-system.requires must be a string array")

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    playwright_version = contract.get("playwright", {}).get("python")
    requirements = [
        line.partition("#")[0].strip()
        for line in RUNTIME_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.partition("#")[0].strip()
        and _normalized_distribution(
            re.split(r"[<>=!~; ]", line.partition("#")[0].strip(), 1)[0]
        ) != "cli-extended"
    ]
    expected = [f"playwright=={playwright_version}"]
    if requirements != expected:
        _fail(
            "client/requirements-runtime.txt must pin the exact Playwright version "
            f"from pwmcp.contract.json; expected {expected!r}, got {requirements!r}"
        )

    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--index-url",
        "https://pypi.org/simple",
        "--dest",
        str(VENDOR),
        *build_requirements,
        *requirements,
    ]
    result = subprocess.run(command, cwd=PWMCP_DIR, check=False)
    if result.returncode:
        _fail(f"pip wheel staging failed with exit {result.returncode}")


def main() -> int:
    if VENDOR.is_symlink():
        _fail(f"refusing to replace wheel staging symlink {VENDOR}")
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    VENDOR.mkdir()
    _stage_cli_extended()
    _stage_pypi_wheels()
    print(f"[INFO] staged offline client wheel sources in {VENDOR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
