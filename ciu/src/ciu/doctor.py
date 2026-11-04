"""``ciu doctor``: the environment checks behind the library's shared doctor verb (AC-20).

Every check is read-only and bounded: it runs a version/status probe, never a deploy.
The library adds ``--check NAME``, ``--json``, the exit status (1 only for ``fail``) and
the automatic ``skills`` check.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from cli_extended import CheckResult, DoctorCheck

_PROBE_TIMEOUT_SECONDS = 20


def _probe(argv: list[str]) -> tuple[int, str]:
    """Run one bounded probe; ``(returncode, first output line)``."""
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(argv)} timed out after {_PROBE_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return 127, str(exc)
    text = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode, (text.splitlines() or [""])[0]


def _docker(runtime, args) -> CheckResult:
    path = shutil.which("docker")
    if path is None:
        return CheckResult("fail", "docker CLI not found on PATH",
                           remedy="install docker or add it to PATH")
    code, line = _probe(["docker", "version", "--format", "{{.Server.Version}}"])
    if code != 0:
        return CheckResult("fail", "docker daemon is not reachable",
                           remedy="start the daemon or check DOCKER_HOST and socket permissions",
                           details={"path": path}, lines=(line[:200],) if line else ())
    return CheckResult("ok", f"docker daemon {line}", details={"path": path, "server": line})


def _compose(runtime, args) -> CheckResult:
    code, line = _probe(["docker", "compose", "version", "--short"])
    if code != 0:
        return CheckResult("fail", "docker compose plugin is not available",
                           remedy="install the docker compose v2 plugin",
                           lines=(line[:200],) if line else ())
    return CheckResult("ok", f"docker compose {line}", details={"version": line})


def _buildx(runtime, args) -> CheckResult:
    code, line = _probe(["docker", "buildx", "version"])
    if code != 0:
        return CheckResult("warn", "docker buildx is not available (`ciu bake` needs it)",
                           remedy="install the docker buildx plugin",
                           lines=(line[:200],) if line else ())
    return CheckResult("ok", line or "docker buildx available")


def _git(runtime, args) -> CheckResult:
    path = shutil.which("git")
    if path is None:
        return CheckResult("fail", "git not found on PATH", remedy="install git")
    return CheckResult("ok", "git found", details={"path": path})


def _ssh(runtime, args) -> CheckResult:
    missing = [name for name in ("ssh", "ssh-keygen", "ssh-keyscan") if shutil.which(name) is None]
    if missing:
        return CheckResult("warn", f"missing for remote verbs: {', '.join(missing)}",
                           remedy="install the OpenSSH client (needed by `ciu host/ssh/up --host`)",
                           details={"missing": missing})
    return CheckResult("ok", "OpenSSH client tools found")


def _repo_root(runtime, args) -> CheckResult:
    """Is the working directory inside a CIU-enabled repository?"""
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "ciu.global.defaults.toml.j2").is_file():
            return CheckResult("ok", f"CIU root: {candidate}", details={"root": str(candidate)})
    return CheckResult("warn", "not inside a CIU-enabled repository",
                       remedy="run from a repo holding ciu.global.defaults.toml.j2, "
                              "or pass --root-folder, or `ciu init`")


def doctor_checks() -> list[DoctorCheck]:
    return [
        DoctorCheck("docker", "docker CLI and daemon are reachable", _docker),
        DoctorCheck("compose", "docker compose v2 plugin is available", _compose),
        DoctorCheck("buildx", "docker buildx plugin is available", _buildx),
        DoctorCheck("git", "git is installed", _git),
        DoctorCheck("ssh", "OpenSSH client tools for the remote verbs are installed", _ssh),
        DoctorCheck("repo-root", "the working directory is inside a CIU-enabled repository",
                    _repo_root),
    ]
