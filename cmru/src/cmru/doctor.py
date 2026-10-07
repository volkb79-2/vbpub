"""``cmru doctor``: read-only environment checks (AC-20).

Every check is read-only, never prints a secret VALUE (only which variable is
present), and gives a remedy when it does not pass. The library appends the
automatic ``skills`` check. Process and filesystem access goes through the small
seams below so tests can use fakes.
"""
from __future__ import annotations

import contextlib
import importlib.metadata
import io
import os
import re
import shutil
import subprocess
from pathlib import Path

from cli_extended import CheckResult, DoctorCheck

CREDENTIAL_VARIABLES = ("GITHUB_PUSH_PAT", "GITHUB_TOKEN")
IMAGE_VARIABLES = ("CMRU_TESTER_UNIFIED_IMAGE", "CMRU_WHEEL_BUILDER_IMAGE")
_PROBE_TIMEOUT_SECONDS = 10


def _run(argv: list[str], timeout: float = _PROBE_TIMEOUT_SECONDS):
    """Run one probe command; the single subprocess seam of this module."""
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False,
    )


def _which(name: str) -> str | None:
    return shutil.which(name)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def check_git(runtime=None, args=None) -> CheckResult:
    """``git`` is on PATH; its version is printed."""
    if _which("git") is None:
        return CheckResult(
            "fail", "git is not on PATH",
            remedy="install git and make sure it is on PATH",
        )
    try:
        completed = _run(["git", "--version"])
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(
            "fail", f"git --version failed: {type(exc).__name__}",
            remedy="reinstall git",
        )
    version = _first_line(completed.stdout)
    if completed.returncode != 0 or not version:
        return CheckResult(
            "fail", "git --version did not report a version", remedy="reinstall git",
        )
    return CheckResult("ok", version, details={"version": version})


def check_docker(runtime=None, args=None) -> CheckResult:
    """The docker CLI is on PATH and the daemon answers ``docker version``."""
    if _which("docker") is None:
        return CheckResult(
            "fail", "docker CLI is not on PATH",
            remedy="install the docker CLI, or run cmru where docker is available",
        )
    try:
        completed = _run(
            ["docker", "version", "--format", "{{.Server.Version}}"], timeout=5,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            "fail", "docker daemon did not answer within 5 seconds",
            remedy="start the docker daemon or check DOCKER_HOST",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(
            "fail", f"docker version failed: {type(exc).__name__}",
            remedy="start the docker daemon or check DOCKER_HOST",
        )
    server = _first_line(completed.stdout)
    if completed.returncode != 0 or not server:
        return CheckResult(
            "fail", "docker daemon is not reachable",
            remedy="start the docker daemon, check DOCKER_HOST and socket permissions",
        )
    return CheckResult(
        "ok", f"docker daemon {server} reachable", details={"server_version": server},
    )


def _load_nearest_config():
    """Return ``(path, ForgeConfig)`` for the nearest cmru config, or ``None``.

    The real loaders report problems by printing a diagnostic and raising
    ``SystemExit``; that output is captured so the check can fold it into its
    own one-line result.
    """
    from cmru.config import (
        ORCHESTRATION_CONFIG_FILENAME,
        PROJECT_CONFIG_FILENAME,
        load_forge_config,
    )

    # Same precedence as the real discovery (resolve_invocation_context): the
    # nearest orchestration file anywhere above wins over a nearer project file.
    current = Path.cwd().resolve()
    found = None
    for name in (ORCHESTRATION_CONFIG_FILENAME, PROJECT_CONFIG_FILENAME):
        for directory in (current, *current.parents):
            if (directory / name).is_file():
                found = directory / name
                break
        if found is not None:
            break
    if found is None:
        return None
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured), contextlib.redirect_stdout(captured):
            forge = load_forge_config(found)
    except SystemExit:
        message = ""
        for line in captured.getvalue().splitlines():
            if line.startswith("[ERROR]"):
                message = line[len("[ERROR]"):].strip()
        raise ValueError(message or "the configuration is invalid") from None
    return found, forge


def check_config(runtime=None, args=None) -> CheckResult:
    """The nearest ``cmru.toml`` / orchestration file loads with the real loaders."""
    try:
        loaded = _load_nearest_config()
    except ValueError as exc:
        return CheckResult(
            "fail", f"config does not load: {exc}"[:300].replace("\n", " "),
            remedy="fix the reported problem; 'cmru init' scaffolds a valid config",
        )
    if loaded is None:
        return CheckResult("skip", "no cmru config here")
    path, forge = loaded
    return CheckResult(
        "ok", f"{path.name} loads ({len(forge.projects)} project(s))",
        details={"path": str(path), "projects": sorted(forge.projects)},
    )


def check_credentials(runtime=None, args=None) -> CheckResult:
    """A GitHub token variable is present; reports WHICH one, never its value."""
    for name in CREDENTIAL_VARIABLES:
        if os.environ.get(name, "").strip():
            return CheckResult(
                "ok", f"{name} is set", details={"variable": name},
            )
    return CheckResult(
        "warn",
        "no GitHub token: " + " or ".join(CREDENTIAL_VARIABLES) + " is unset",
        remedy=(
            "export " + " or ".join(CREDENTIAL_VARIABLES)
            + " before publishing or resolving private repositories; "
            "local-only verbs do not need it"
        ),
    )


def _declared_images(forge) -> dict[str, str]:
    """The tester/wheel-builder image values the loaded config declares."""
    declared: dict[str, str] = {}
    scopes = [forge.env, *(project.env for project in forge.projects.values())]
    for scope in scopes:
        for name in IMAGE_VARIABLES:
            value = (scope or {}).get(name)
            if isinstance(value, str) and value.strip():
                declared.setdefault(name, value.strip())
    return declared


def check_images(runtime=None, args=None) -> CheckResult:
    """The declared tester-unified / wheel-builder images exist locally."""
    try:
        loaded = _load_nearest_config()
    except ValueError:
        return CheckResult("skip", "config does not load; see the config check")
    if loaded is None:
        return CheckResult("skip", "no cmru config here")
    declared = _declared_images(loaded[1])
    if not declared:
        return CheckResult("skip", "the config declares no tester or builder image")
    if _which("docker") is None:
        return CheckResult("skip", "docker is unavailable; cannot inspect images")
    missing: list[str] = []
    for name, image in sorted(declared.items()):
        try:
            completed = _run(["docker", "image", "inspect", image], timeout=10)
        except (OSError, subprocess.SubprocessError):
            return CheckResult("skip", "docker is unavailable; cannot inspect images")
        if completed.returncode != 0:
            err = (completed.stderr or "").lower()
            if "no such image" in err or "no such object" in err:
                missing.append(f"{name}={image}")
            else:
                return CheckResult("skip", "docker is unavailable; cannot inspect images")
    details = {"images": declared, "missing": missing}
    if missing:
        return CheckResult(
            "fail", "image(s) not present locally: " + ", ".join(missing),
            remedy="build or pull the image(s) (for example 'docker pull IMAGE')",
            details=details,
        )
    return CheckResult(
        "ok", f"{len(declared)} declared image(s) present", details=details,
    )


_REQUIREMENT = re.compile(r"^\s*cli[-_.]extended\s*([<>=!~].*?)?\s*(?:;.*)?$", re.IGNORECASE)
_FLOOR = re.compile(r">=\s*([0-9]+(?:\.[0-9]+)*)")


def _version_tuple(text: str) -> tuple[int, ...] | None:
    match = re.match(r"[0-9]+(?:\.[0-9]+)*", text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def check_cli_extended(runtime=None, args=None) -> CheckResult:
    """The installed cli-extended satisfies the floor cmru's metadata declares."""
    try:
        installed = importlib.metadata.version("cli-extended")
    except importlib.metadata.PackageNotFoundError:
        return CheckResult(
            "fail", "cli-extended is not installed",
            remedy="install the cli-extended wheel (see the cmru README, Install)",
        )
    try:
        requirements = importlib.metadata.requires("cmru") or []
    except importlib.metadata.PackageNotFoundError:
        return CheckResult(
            "warn", f"cli-extended {installed}; cmru has no distribution metadata",
            remedy="install cmru as a wheel (see the README, Install)",
            details={"installed": installed},
        )
    spec = None
    for requirement in requirements:
        if "extra ==" in requirement:
            continue
        match = _REQUIREMENT.match(requirement)
        if match is not None:
            spec = (match.group(1) or "").strip()
            break
    if spec is None:
        return CheckResult(
            "warn", f"cli-extended {installed}; cmru metadata declares no floor",
            remedy="reinstall the cmru wheel, which declares cli-extended>=FLOOR",
            details={"installed": installed},
        )
    floor_match = _FLOOR.search(spec)
    have = _version_tuple(installed)
    if floor_match is None or have is None:
        return CheckResult(
            "warn", f"cli-extended {installed}; cannot evaluate '{spec}'",
            details={"installed": installed, "requirement": spec},
        )
    floor = _version_tuple(floor_match.group(1))
    width = max(len(floor), len(have))
    pad = lambda value: value + (0,) * (width - len(value))
    details = {"installed": installed, "requirement": spec}
    if pad(have) < pad(floor):
        return CheckResult(
            "fail", f"cli-extended {installed} is below the declared floor {spec}",
            remedy=f"install a cli-extended wheel satisfying {spec}",
            details=details,
        )
    return CheckResult(
        "ok", f"cli-extended {installed} satisfies {spec}", details=details,
    )


def doctor_checks() -> tuple[DoctorCheck, ...]:
    """The six cmru checks (the library adds ``skills``)."""
    return (
        DoctorCheck("git", "git is on PATH", check_git),
        DoctorCheck("docker", "the docker CLI is installed and the daemon reachable", check_docker),
        DoctorCheck("config", "the nearest cmru config loads", check_config),
        DoctorCheck("credentials", "a GitHub token variable is set (value never shown)", check_credentials),
        DoctorCheck("images", "declared tester and builder images exist locally", check_images),
        DoctorCheck("cli-extended", "the installed cli-extended meets cmru's floor", check_cli_extended),
    )
