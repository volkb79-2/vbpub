#!/usr/bin/env python3
"""Run CMRU's registered release lanes from an exact, secret-free snapshot."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from xml.etree import ElementTree
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
SECRET_ENV_KEYS = ("GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN")
FACTS_ENV_KEY = "CMRU_ASSAY_BASELINE_FACTS"
EXTRA_MOUNTS_ENV_KEY = "RUN_GATE_EXTRA_MOUNTS"
REF_PROBE_LANE = "ref-probe"
RUN_GATE_MIN_VERSION = "24.0.0"  # First wheel-only SemVer release with the hook/info protections.
_RUN_GATE_MODULE_FILES = ("run_gate.py", "run_gate_admission.py")
# The gate-only variant of installed-wheel: it refuses to run unless it is
# inside this gate's fresh, snapshot-bound manifest. The plain `installed-wheel`
# lane stays runnable directly (it bypasses the secret boundary, as documented).
LIVE_WHEEL_LANE = "installed-wheel-live"
FIRST_RELEASE_ASSAY_LANE = "assay-first-release"
FIRST_RELEASE_R2_FOLLOW_UP_LANE = "assay-first-release-r2"
FIRST_RELEASE_POSTPONEMENT_ID = "FIRST-RELEASE-R2"
# Every directory the gate puts on ``sys.path`` (or whose scripts it runs), so a
# stray top-level module such as ``cmru/src/json.py`` is as visible as a
# package helper.
_GATE_CODE_ROOTS = (
    "cmru/src",
    "cmru/tools",
    "assay/src",
    "assay/analysis/src",
    "assay/tools",
    "libraries/worktree/src",
)
_CODE_SUFFIXES = {".py", ".pyc", ".pyo", ".so", ".pyd"}
_HASH_CHUNK = 200
_RUN_GATE_EXECUTABLE_ENV = "_CMRU_RUN_GATE_EXECUTABLE"
# One-shot marker for the controller's interpreter switch (_ensure_gate_environment).
_GATE_REEXEC_ENV = "_CMRU_RELEASE_GATE_REEXEC"
# Protected values CMRU's release runtime gives a project step. This script cannot import
# cmru before it has chosen its interpreter, so it spells the names; tests pin them to
# transaction.INTERNAL_PYTHON_ENV and the CMRU_RELEASE_GATE_COMMIT that cli.py injects.
_INTERNAL_PYTHON_ENV = "CMRU_INTERNAL_PYTHON"
_GATE_COMMIT_ENV = "CMRU_RELEASE_GATE_COMMIT"


class GateInterrupted(SystemExit):
    def __init__(self, signum: int):
        self.signum = signum
        super().__init__(128 + signum)


@contextmanager
def _gate_signal_handlers():
    """Turn termination into normal unwinding so lane and lease cleanup runs."""
    previous = {}

    def interrupt(signum, _frame):
        for handled in previous:
            signal.signal(handled, signal.SIG_IGN)
        raise GateInterrupted(signum)

    try:
        for handled in (signal.SIGTERM, signal.SIGHUP):
            previous[handled] = signal.getsignal(handled)
            signal.signal(handled, interrupt)
        yield
    finally:
        for handled, handler in previous.items():
            signal.signal(handled, handler)


def _load_components():
    """Load the selected worktree's CMRU, Assay and baseline helper sources."""
    for source in (
        PROJECT_ROOT / "src",
        PROJECT_ROOT / "tools",
        REPOSITORY_ROOT / "assay" / "src",
        # cli_extended is the INSTALLED released wheel (CX-D1), never a source root.
        REPOSITORY_ROOT / "libraries" / "worktree" / "src",
    ):
        sys.path.insert(0, str(source))
    from assay import git as assay_git
    import prepare_assay_baseline

    return assay_git, prepare_assay_baseline


def _installed_launcher(name: str) -> tuple[str, Path, list[str]]:
    """Resolve an installed console script and the Python interpreter it runs under.

    Returns ``(executable, resolved launcher path, interpreter argv)``. The
    interpreter is read from the launcher's own shebang, so it is whatever
    environment the wheel was actually installed into, never a path written
    down in this repository. Every failure is a ``RuntimeError`` naming the
    launcher; nothing is guessed.
    """
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"the registered CMRU gate requires the installed {name} CLI")
    command = Path(executable).resolve(strict=True)
    try:
        with command.open(encoding="utf-8") as stream:
            first_line = stream.readline().strip()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"cannot read the installed {name} launcher: {command}: {exc}") from exc
    if not first_line.startswith("#!"):
        raise RuntimeError(f"installed {name} launcher has no Python shebang: {command}")
    interpreter = shlex.split(first_line[2:])
    if not interpreter:
        raise RuntimeError(f"installed {name} launcher has an empty Python shebang: {command}")
    if interpreter[0] == "/usr/bin/env":
        if len(interpreter) < 2 or interpreter[1].startswith("-"):
            raise RuntimeError(f"installed {name} launcher has an unsupported env shebang: {command}")
        resolved = shutil.which(interpreter[1])
        if resolved is None:
            raise RuntimeError(
                f"cannot resolve the Python interpreter named by {name}: {interpreter[1]}"
            )
        interpreter = [resolved, *interpreter[2:]]
    elif not Path(interpreter[0]).is_absolute():
        raise RuntimeError(f"installed {name} launcher has a non-absolute interpreter: {command}")
    return executable, command, interpreter


def _declared_dependencies() -> list[tuple[str, str]]:
    """``(requirement, import name)`` for each dependency in CMRU's own ``pyproject.toml``.

    The import name is the distribution name normalised the way the wheel's
    top-level package is spelled (``-``/``.`` to ``_``), which is exact for every
    dependency CMRU declares. Deriving the set from the declaration means a
    dependency added later is covered without editing this script.
    """
    path = PROJECT_ROOT / "pyproject.toml"
    try:
        requirements = tomllib.loads(path.read_text(encoding="utf-8"))["project"]["dependencies"]
        declared = []
        for requirement in requirements:
            name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement.strip())
            if name is None:
                raise ValueError(f"unparseable requirement {requirement!r}")
            declared.append((requirement, re.sub(r"[-.]+", "_", name.group(0)).lower()))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(
            f"cannot read CMRU's declared dependencies from {path}: {exc}"
        ) from exc
    return declared


_VERSION_PATTERN = re.compile(
    r"(?P<release>[0-9]+(?:\.[0-9]+)*)"
    r"(?:(?P<pre>a|b|rc)(?P<pre_n>[0-9]+)|\.?(?P<dev>dev)(?P<dev_n>[0-9]+)|\.?(?P<post>post)(?P<post_n>[0-9]+))?"
    r"(?:\+[A-Za-z0-9.]+)?\Z"
)
_SPECIFIER_PATTERN = re.compile(r"\s*(>=|<=|==|!=|>|<)\s*([0-9A-Za-z.+]+)\s*\Z")
_PHASE_RANK = {"dev": 0, "a": 1, "b": 2, "rc": 3, None: 4, "post": 5}


def _version_key(version: str) -> tuple[tuple[int, ...], int, int]:
    """Order a PEP 440 release/pre/dev/post version without third-party code.

    This controller starts under ``-I`` before anything from the worktree (or a
    dependency such as ``packaging``) can be imported, so it carries the small
    ordering it needs. A local segment (``+bootstrap.source``) never changes the
    order. A version it cannot parse is an error, never "satisfied".
    """
    match = _VERSION_PATTERN.match(version.strip())
    if match is None:
        raise ValueError(f"unparseable version {version!r}")
    release = [int(part) for part in match["release"].split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    if match["pre"]:
        return tuple(release), _PHASE_RANK[match["pre"]], int(match["pre_n"])
    if match["dev"]:
        return tuple(release), _PHASE_RANK["dev"], int(match["dev_n"])
    if match["post"]:
        return tuple(release), _PHASE_RANK["post"], int(match["post_n"])
    return tuple(release), _PHASE_RANK[None], 0


def _requirement_distribution(requirement: str) -> tuple[str, list[tuple[str, str]]]:
    """``(distribution name, [(operator, version), ...])`` of one declared requirement."""
    match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)\Z", requirement.strip())
    if match is None:
        raise RuntimeError(f"unparseable requirement {requirement!r}")
    clauses = []
    for clause in filter(None, (part.strip() for part in match.group(2).split(","))):
        parsed = _SPECIFIER_PATTERN.match(clause)
        if parsed is None:
            raise RuntimeError(
                f"requirement {requirement!r} uses a specifier this controller cannot "
                f"evaluate ({clause!r}); it supports >=, >, <=, <, == and != on plain versions"
            )
        clauses.append((parsed.group(1), parsed.group(2)))
    return match.group(1), clauses


def _requirement_satisfied(requirement: str, installed: str) -> bool:
    """Whether *installed* meets every clause of *requirement* (no clause: any version)."""
    _name, clauses = _requirement_distribution(requirement)
    try:
        have = _version_key(installed)
        wanted = [(operator, _version_key(bound)) for operator, bound in clauses]
    except ValueError:
        return False
    checks = {
        ">=": lambda a, b: a >= b, ">": lambda a, b: a > b,
        "<=": lambda a, b: a <= b, "<": lambda a, b: a < b,
        "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    }
    return all(checks[operator](have, bound) for operator, bound in wanted)


def _installed_version(distribution: str) -> str | None:
    """The installed distribution's metadata version, or None when it is not installed."""
    import importlib.metadata

    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _authoritative_interpreter() -> tuple[list[str], str]:
    """The interpreter of the CMRU environment, and a phrase saying where it came from.

    Inside the protected release workflow CMRU states the fact itself: it hands every
    project step ``CMRU_INTERNAL_PYTHON``, the ``sys.executable`` of the running
    ``cmru`` (``transaction.INTERNAL_PYTHON_ENV``; a test pins the spelling). During a
    release the ``cmru`` first on PATH is CMRU's own bound launcher, a ``/bin/sh``
    wrapper that execs that interpreter, so reading its shebang would name the shell,
    not an environment. The value is honoured only together with the protected
    ``CMRU_RELEASE_GATE_COMMIT`` (set by the same release workflow for the same step), so a
    stale variable in an operator's shell cannot choose the interpreter of a manual run.
    An unusable protected value is a refusal, never a reason to look elsewhere.

    Anywhere else (a direct ``run-gate gate-provisional``) the installed ``cmru``
    console script's shebang names the environment it was installed into; a launcher
    that is not a Python console script is refused with that explanation.
    """
    runtime = os.environ.get(_INTERNAL_PYTHON_ENV, "").strip()
    if runtime and os.environ.get(_GATE_COMMIT_ENV, "").strip():
        if not os.path.isabs(runtime) or not os.path.isfile(runtime) or not os.access(runtime, os.X_OK):
            raise RuntimeError(
                f"{_INTERNAL_PYTHON_ENV}={runtime!r}, provided by the CMRU release runtime, "
                "is not an absolute path to an executable file; the release runtime must "
                "report its own interpreter"
            )
        return [runtime], f"provided by the CMRU release runtime ({_INTERNAL_PYTHON_ENV})"
    _executable, launcher, interpreter = _installed_launcher("cmru")
    if "python" not in Path(interpreter[0]).name.lower():
        raise RuntimeError(
            f"the cmru launcher {launcher} is not a Python console script (its shebang runs "
            f"{interpreter[0]}), so it names no Python environment; inside `cmru release` "
            f"CMRU supplies the interpreter itself ({_INTERNAL_PYTHON_ENV}), and for a manual "
            "run the estate environment's own `cmru` must come first on PATH"
        )
    return interpreter, f"named by the installed cmru launcher {launcher}"


def _ensure_gate_environment(argv: list[str]) -> None:
    """Run this controller under the interpreter that holds CMRU's declared dependencies.

    The registered ``gate`` lanes are ``bare-host`` and start ``python3 -I -B
    run_release_gate.py``; ``python3`` there is whatever the host PATH names,
    which need not be the environment the CMRU wheel and its declared
    third-party dependencies (``cli-extended``) were installed into. The
    controller imports CMRU from the exact worktree source and needs only those
    *declared* wheels from the installed environment, so when this interpreter
    cannot import them, or imports one whose INSTALLED distribution version does
    not satisfy the declared requirement (a host with an older ``cli-extended``
    0.2.0 against ``cli-extended>=0.3.0``), the controller is re-executed ONCE
    under the CMRU
    environment's interpreter (see :func:`_authoritative_interpreter`: the
    interpreter CMRU's release runtime reports for itself, or, for a manual
    run, the one named by the installed ``cmru`` console script): the
    environment ``cmru release`` itself requires. Nothing is vendored, no path
    is hardcoded, and the switch is announced on stderr.

    This runs before anything from the worktree is imported (stdlib only; the
    only worktree file read is ``pyproject.toml``, as data, to learn the
    declared dependency names), so the ``-I -B`` ordering that keeps an
    untracked module from running ahead of the source-integrity check is
    preserved: the same script, with the same flags, simply starts again. A
    missing launcher, an interpreter that still cannot import the
    dependencies or still carries a below-floor version (the authoritative
    interpreter is probed for the installed version before the switch), or a
    second failure after the switch is a loud
    ``RuntimeError`` with the remedy, never a degraded run.
    """
    already_switched = os.environ.pop(_GATE_REEXEC_ENV, None) is not None
    declared = _declared_dependencies()
    problems: list[str] = []
    absent: list[tuple[str, str]] = []
    for requirement, module in declared:
        distribution, _clauses = _requirement_distribution(requirement)
        try:
            importable = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            importable = False
        if not importable:
            problems.append(f"cannot import {module}")
            absent.append((requirement, module))
            continue
        # Importable is not the declared floor: the INSTALLED distribution's own
        # metadata version must satisfy the whole declared requirement.
        installed = _installed_version(distribution)
        if installed is None:
            problems.append(
                f"imports {module} but has no installed {distribution} distribution metadata"
            )
            absent.append((requirement, module))
        elif not _requirement_satisfied(requirement, installed):
            problems.append(
                f"has {distribution} {installed} installed, which does not satisfy {requirement}"
            )
            absent.append((requirement, module))
    if not absent:
        return
    missing = ", ".join(module for _requirement, module in absent)
    found = "; ".join(problems)
    remedy = (
        "Install the released cmru wheel together with its declared dependencies "
        f"({', '.join(requirement for requirement, _module in absent)}) into the estate "
        "Python environment (`pip install --no-index --find-links <wheelhouse> cmru`, see "
        "the CMRU README 'Install') and put that environment's bin directory on PATH "
        "before running `run-gate gate` or `run-gate gate-provisional`"
    )
    if already_switched:
        raise RuntimeError(
            f"the interpreter chosen as the CMRU environment's ({sys.executable}) "
            f"still fails the declared dependencies: {found}. {remedy}"
        )
    try:
        interpreter, origin = _authoritative_interpreter()
    except RuntimeError as exc:
        raise RuntimeError(
            f"this interpreter ({sys.executable}) {found}, and no authoritative CMRU "
            f"environment could be determined: {exc}. {remedy}"
        ) from exc
    shown = " ".join(interpreter)
    # The authoritative interpreter must meet the SAME declaration: it imports the
    # dependency and reports the installed distribution version, which is checked here.
    probe_code = "; ".join(
        ["import importlib.metadata as _metadata"]
        + [f"import {module}" for _requirement, module in absent]
        + [
            f"print({_requirement_distribution(requirement)[0]!r} + '=' + "
            f"_metadata.version({_requirement_distribution(requirement)[0]!r}))"
            for requirement, _module in absent
        ]
    )
    try:
        probe = subprocess.run(
            [*interpreter, "-I", "-B", "-c", probe_code],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"cannot run the interpreter {shown} {origin}: {exc}. {remedy}"
        ) from exc
    if probe.returncode != 0:
        detail = (probe.stderr.strip().splitlines() or ["no diagnostic output"])[-1]
        raise RuntimeError(
            f"the interpreter {shown} {origin} cannot import {missing} either "
            f"({detail}). {remedy}"
        )
    reported = dict(
        line.split("=", 1) for line in probe.stdout.splitlines() if "=" in line
    )
    for requirement, _module in absent:
        distribution = _requirement_distribution(requirement)[0]
        version = reported.get(distribution)
        if version is None or not _requirement_satisfied(requirement, version):
            raise RuntimeError(
                f"the interpreter {shown} {origin} has {distribution} "
                f"{version or '(no reported version)'} installed, which does not satisfy "
                f"{requirement}. {remedy}"
            )
    print(
        f"cmru-release-gate: {sys.executable} {found}; running the "
        f"controller under the installed CMRU environment's interpreter "
        f"{shown} ({origin})",
        file=sys.stderr, flush=True,
    )
    os.environ[_GATE_REEXEC_ENV] = "1"
    os.execv(
        interpreter[0],
        [*interpreter, "-I", "-B", str(Path(__file__).resolve()), *argv],
    )


def _check_run_gate_identity(repo_root: Path) -> dict[str, object]:
    """Require and identify the stable Run-Gate judge used by this gate."""
    executable, command, interpreter = _installed_launcher("run-gate")
    try:
        cli_version_result = subprocess.run(
            [executable, "--version"],
            cwd=repo_root / "cmru",
            # The launcher is not verified yet: never hand it publisher tokens.
            env=_lane_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not run installed Run-Gate `--version`: {exc}") from exc
    if cli_version_result.returncode != 0:
        detail = cli_version_result.stderr.strip() or cli_version_result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"cannot read installed Run-Gate version: {detail}")
    cli_version_lines = cli_version_result.stdout.strip().splitlines()
    cli_match = (
        re.fullmatch(
            r"run-gate ((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))",
            cli_version_lines[0],
        )
        if len(cli_version_lines) == 1 else None
    )
    if cli_match is None:
        raw_line = cli_version_lines[0] if len(cli_version_lines) == 1 else ""
        raw_version = (
            raw_line.removeprefix("run-gate ")
            if raw_line.startswith("run-gate ") else ""
        )
        if (
            raw_version
            and re.match(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", raw_version)
            and any(marker in raw_version for marker in (".dev", "+", "-"))
        ):
            raise RuntimeError(
                f"CMRU's release gate requires a stable Run-Gate wheel at "
                f"SemVer {RUN_GATE_MIN_VERSION} or later; installed development/local "
                f"build reports {raw_line!r}"
            )
        raise RuntimeError(
            "installed run-gate --version returned an unrecognized SemVer identity: "
            f"{cli_version_result.stdout.strip()!r}"
        )
    cli_version = cli_match.group(1)

    version_code = """
import hashlib
import json
from importlib.metadata import distribution

dist = distribution("run-gate")
files = dist.files
if not files:
    raise SystemExit("installed Run-Gate distribution has no file manifest")
digest = hashlib.sha256()
for item in sorted(files, key=str):
    path = dist.locate_file(item)
    if not path.is_file():
        raise SystemExit(f"installed Run-Gate file is missing: {item}")
    digest.update(str(item).encode("utf-8", "surrogateescape"))
    digest.update(b"\\0")
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
wheel_sha256 = None
direct_url = dist.read_text("direct_url.json")
if direct_url:
    try:
        direct = json.loads(direct_url)
    except json.JSONDecodeError:
        direct = {}
    archive = direct.get("archive_info") if isinstance(direct, dict) else None
    hashes = archive.get("hashes") if isinstance(archive, dict) else None
    url = direct.get("url") if isinstance(direct, dict) else None
    editable = (direct.get("dir_info") or {}).get("editable", False) if isinstance(direct, dict) else False
    candidate = hashes.get("sha256") if isinstance(hashes, dict) else None
    legacy_hash = archive.get("hash") if isinstance(archive, dict) else None
    if isinstance(legacy_hash, str) and legacy_hash.startswith("sha256="):
        legacy_candidate = legacy_hash.removeprefix("sha256=")
        if candidate is None:
            candidate = legacy_candidate
        elif candidate != legacy_candidate:
            candidate = None
    if (
        not editable
        and isinstance(url, str)
        and url.split("?", 1)[0].split("#", 1)[0].lower().endswith(".whl")
        and isinstance(candidate, str)
        and len(candidate) == 64
        and all(char in "0123456789abcdef" for char in candidate)
    ):
        wheel_sha256 = candidate
payload = hashlib.sha256()
for item in ("run_gate.py", "run_gate_admission.py"):
    path = dist.locate_file(item)
    if not path.is_file():
        raise SystemExit(f"installed Run-Gate module is missing: {item}")
    payload.update(item.encode("utf-8", "surrogateescape"))
    payload.update(b"\\0")
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            payload.update(chunk)
print(json.dumps({
    "version": dist.version,
    "distribution_sha256": digest.hexdigest(),
    "installed_wheel_sha256": wheel_sha256,
    "installed_payload_sha256": payload.hexdigest(),
}))
"""
    try:
        version_result = subprocess.run(
            [*interpreter, "-I", "-B", "-c", version_code],
            cwd=repo_root / "cmru",
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not inspect the installed Run-Gate package version: {exc}") from exc
    if version_result.returncode != 0:
        detail = version_result.stderr.strip() or version_result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"cannot read installed Run-Gate package version: {detail}")
    try:
        package_identity = json.loads(version_result.stdout)
        package_version = package_identity["version"]
        distribution_sha256 = package_identity["distribution_sha256"]
        installed_wheel_sha256 = package_identity["installed_wheel_sha256"]
        installed_payload_sha256 = package_identity["installed_payload_sha256"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(
            "cannot bind installed Run-Gate package identity to its wheel files"
        ) from exc
    package_match = re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)",
        package_version,
    )
    if package_match is None:
        raise RuntimeError(
            "CMRU's release gate requires a stable Run-Gate wheel; "
            f"installed package version is {package_version!r}"
        )
    if cli_version != package_version:
        raise RuntimeError(
            "installed run-gate CLI version does not match its wheel metadata: "
            f"CLI reports {cli_version!r}, package metadata reports {package_version!r}"
        )
    package_key = tuple(int(part) for part in package_match.groups()[:3])
    minimum_key = tuple(int(part) for part in RUN_GATE_MIN_VERSION.split("."))
    if package_key < minimum_key:
        raise RuntimeError(
            f"CMRU's release gate requires Run-Gate {RUN_GATE_MIN_VERSION} or later "
            f"for read-only host hook/info mounts; installed version {package_version} is too old"
        )
    distribution_sha256 = str(distribution_sha256)
    if re.fullmatch(r"[0-9a-f]{64}", distribution_sha256) is None:
        raise RuntimeError("installed Run-Gate distribution hash is malformed")
    if (
        not isinstance(installed_wheel_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", installed_wheel_sha256) is None
    ):
        raise RuntimeError(
            "installed Run-Gate is not proven to come from a non-editable wheel; "
            "install the exact published release wheel"
        )
    if (
        not isinstance(installed_payload_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", installed_payload_sha256) is None
    ):
        raise RuntimeError("installed Run-Gate module hashes are malformed")
    return {
        "version": package_version,
        "executable": str(command),
        "launcher_sha256": hashlib.sha256(command.read_bytes()).hexdigest(),
        "distribution_sha256": distribution_sha256,
        "installed_wheel_sha256": installed_wheel_sha256,
        "installed_payload_sha256": installed_payload_sha256,
    }


def _run_gate_wheel_payload_sha256(wheel_bytes: bytes) -> str:
    """Hash the executable Python modules from a Run-Gate wheel archive."""
    digest = hashlib.sha256()
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
            for name in _RUN_GATE_MODULE_FILES:
                if archive.namelist().count(name) != 1:
                    raise KeyError(name)
                payload = archive.read(name)
                digest.update(name.encode("utf-8", "surrogateescape"))
                digest.update(b"\0")
                digest.update(payload)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise RuntimeError(
            "published Run-Gate wheel is not a readable archive with its executable modules"
        ) from exc
    return digest.hexdigest()


def _check_run_gate_release_artifacts(
    repo_root: Path, identity: Mapping[str, object],
) -> dict[str, object]:
    """Bind the installed wheel and tester image to its exact published release."""
    from cmru.config import load_forge_config
    from cmru.tool_deps import _download_asset, _github_get_json

    config_path = repo_root / "cmru.orchestration.toml"
    forge = load_forge_config(config_path, require_orchestration=True)
    provider = forge.projects.get("run-gate-project")
    if provider is None:
        raise RuntimeError(
            "CMRU cannot resolve the configured run-gate-project release"
        )
    version = identity.get("version")
    if not isinstance(version, str) or re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version,
    ) is None:
        raise RuntimeError(f"installed Run-Gate version is not stable SemVer: {version!r}")
    tag = f"{provider.prefix}{version}"
    try:
        release = _github_get_json(
            f"/repos/{forge.github.owner}/{forge.github.repo}/releases/tags/{tag}",
            timeout=10,
        )
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(
            f"cannot resolve the published Run-Gate release {tag!r}: {exc}"
        ) from exc
    if not isinstance(release, dict):
        raise RuntimeError(f"no published Run-Gate release is available under {tag!r}")
    if release.get("tag_name") != tag:
        raise RuntimeError(
            f"GitHub returned Run-Gate release tag {release.get('tag_name')!r} for {tag!r}"
        )
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise RuntimeError(f"Run-Gate release {tag!r} is not a published stable release")
    expected_asset_name = f"run_gate-{version}-py3-none-any.whl"
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError(f"Run-Gate release {tag!r} has no readable asset list")
    wheel_assets = [
        item for item in assets
        if isinstance(item, dict) and item.get("name") == expected_asset_name
    ]
    checksum_assets = [
        item for item in assets
        if isinstance(item, dict) and item.get("name") == f"{expected_asset_name}.sha256"
    ]
    if len(wheel_assets) != 1:
        raise RuntimeError(
            f"published Run-Gate release {tag!r} must have exactly one asset named "
            f"{expected_asset_name!r} (found {len(wheel_assets)})"
        )
    if len(checksum_assets) != 1:
        raise RuntimeError(
            f"published Run-Gate release {tag!r} must have exactly one checksum asset for "
            f"{expected_asset_name!r} (found {len(checksum_assets)})"
        )
    asset_url = wheel_assets[0].get("browser_download_url")
    sidecar_url = checksum_assets[0].get("browser_download_url")
    if not isinstance(asset_url, str) or not asset_url:
        raise RuntimeError(f"published Run-Gate asset {expected_asset_name!r} has no download URL")
    if not isinstance(sidecar_url, str) or not sidecar_url:
        raise RuntimeError(f"published Run-Gate checksum for {expected_asset_name!r} has no download URL")
    try:
        sidecar_bytes = _download_asset(sidecar_url, timeout=10)
        sidecar_text = sidecar_bytes.decode("utf-8").strip()
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise RuntimeError(
            f"cannot read the published Run-Gate wheel checksum for {tag!r}: {exc}"
        ) from exc
    sidecar_tokens = sidecar_text.split()
    sidecar_sha256 = sidecar_tokens[0].lower() if sidecar_tokens else ""
    if re.fullmatch(r"[0-9a-f]{64}", sidecar_sha256) is None:
        raise RuntimeError(f"published Run-Gate release {tag!r} has no trustworthy wheel digest")
    try:
        wheel_bytes = _download_asset(asset_url, timeout=10)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(f"cannot download the published Run-Gate wheel: {exc}") from exc
    published_wheel_sha256 = hashlib.sha256(wheel_bytes).hexdigest()
    if published_wheel_sha256 != sidecar_sha256:
        raise RuntimeError(
            "published Run-Gate wheel bytes do not match the release checksum sidecar"
        )
    if identity.get("installed_wheel_sha256") != published_wheel_sha256:
        raise RuntimeError(
            "installed Run-Gate wheel does not match the published release asset"
        )
    published_payload_sha256 = _run_gate_wheel_payload_sha256(wheel_bytes)
    if identity.get("installed_payload_sha256") != published_payload_sha256:
        raise RuntimeError(
            "installed Run-Gate executable modules do not match the published wheel bytes"
        )

    return {
        "published_wheel_sha256": published_wheel_sha256,
        "published_payload_sha256": published_payload_sha256,
        **_check_tester_image_identity(
            repo_root, version=version, published_wheel_sha256=published_wheel_sha256,
        ),
    }


def _check_tester_image_identity(
    repo_root: Path, *, version: object, published_wheel_sha256: object,
) -> dict[str, object]:
    """Inspect the image Run-Gate will select and bind its labels to the wheel."""
    config_path = repo_root / "run-gate.root.toml"
    try:
        with config_path.open("rb") as stream:
            run_gate_config = tomllib.load(stream)
        environment = run_gate_config["environments"]["tester-unified"]
        image = environment["image"]
        image_env = environment.get("image_env")
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(
            f"cannot resolve tester-unified image from {config_path}: {exc}"
        ) from exc
    if image_env is not None:
        if not isinstance(image_env, str) or not image_env:
            raise RuntimeError("tester-unified image_env is malformed")
        if image_env in os.environ:
            image = os.environ[image_env]
            if not image:
                raise RuntimeError(
                    f"selected tester-unified image override ${image_env} is empty"
                )
    if not isinstance(image, str) or not image:
        raise RuntimeError("tester-unified image is missing from run-gate.root.toml")
    try:
        inspected = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{json .}}", image],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot inspect tester-unified image {image!r}: {exc}") from exc
    if inspected.returncode != 0:
        detail = inspected.stderr.strip() or inspected.stdout.strip() or "no diagnostic output"
        raise RuntimeError(
            f"cannot inspect tester-unified image {image!r}: {detail[:300]}"
        )
    try:
        image_data = json.loads(inspected.stdout)
        labels = image_data["Config"]["Labels"]
        image_id = image_data["Id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("tester-unified image inspection returned invalid JSON identity") from exc
    if not isinstance(labels, dict) or not isinstance(image_id, str):
        raise RuntimeError("tester-unified image has no readable identity labels")
    image_version = labels.get("org.vbpub.run-gate.version")
    image_wheel_sha256 = labels.get("org.vbpub.run-gate.wheel-sha256")
    if image_version != version or image_wheel_sha256 != published_wheel_sha256:
        raise RuntimeError(
            "tester-unified image does not contain the published Run-Gate wheel: "
            f"version={image_version!r}, sha256={image_wheel_sha256!r}; expected "
            f"version={version!r}, sha256={published_wheel_sha256}"
        )
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise RuntimeError("tester-unified image ID is malformed")
    return {
        "tester_image": image,
        "tester_image_id": image_id,
        "tester_image_wheel_sha256": image_wheel_sha256,
    }


def _current_run_gate_identity(repo_root: Path) -> dict[str, object]:
    identity = _check_run_gate_identity(repo_root)
    return {**identity, **_check_run_gate_release_artifacts(repo_root, identity)}


def _recheck_run_gate_identity(
    repo_root: Path, expected: Mapping[str, object],
) -> dict[str, object]:
    """Recheck the installed judge and selected image without re-downloading the wheel."""
    current = _check_run_gate_identity(repo_root)
    current_core = {
        key: expected.get(key)
        for key in (
            "version", "executable", "launcher_sha256", "distribution_sha256",
            "installed_wheel_sha256", "installed_payload_sha256",
        )
    }
    if current != current_core:
        raise RuntimeError(
            "Run-Gate identity changed during the CMRU release gate; "
            "refusing to use a different installed judge"
        )
    image = _check_tester_image_identity(
        repo_root,
        version=expected.get("version"),
        published_wheel_sha256=expected.get("published_wheel_sha256"),
    )
    expected_image = {
        key: expected.get(key)
        for key in ("tester_image", "tester_image_id", "tester_image_wheel_sha256")
    }
    if image != expected_image:
        raise RuntimeError(
            "tester-unified Run-Gate image identity changed during the CMRU release gate"
        )
    return {
        **current,
        "published_wheel_sha256": expected.get("published_wheel_sha256"),
        "published_payload_sha256": expected.get("published_payload_sha256"),
        **image,
    }


def _preflight_assay_source(repo_root: Path) -> None:
    """Check the source tree before any registered lane can install it."""
    script = PROJECT_ROOT / "tools" / "check_assay_source.py"
    assay_source = repo_root / "assay"
    if script.is_symlink() or not script.is_file():
        raise RuntimeError(f"Assay source preflight is missing or unsafe: {script}")
    try:
        result = subprocess.run(
            # Isolated like the controller itself (-I -B): PYTHONPATH or a
            # user-site module must not shadow anything the check imports.
            [sys.executable, "-I", "-B", str(script), "--assay-source", str(assay_source)],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("could not run the Assay editable-install source preflight") from exc
    if result.returncode != 0 or "CMRU_ASSAY_SOURCE_PREFLIGHT=clean" not in result.stdout.splitlines():
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"Assay editable-install source preflight refused: {detail}")


@contextmanager
def _release_gate_lock(temp_parent: Path) -> Iterator[None]:
    """Serialize snapshot allocation and nested release-gate lanes."""
    lock_path = temp_parent / ".cmru-release-gate.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError("CMRU release-gate lock is not a regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _lane_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in (*SECRET_ENV_KEYS, FACTS_ENV_KEY, EXTRA_MOUNTS_ENV_KEY):
        environment.pop(key, None)
    return environment


def _hardened_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    })
    for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        environment.pop(key, None)
    for key in tuple(environment):
        if key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            environment.pop(key, None)
    return environment


def _git_output(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, env=_hardened_git_environment(),
        capture_output=True, check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise RuntimeError(f"cannot bind gate helper source to HEAD: git {args[0]}: {detail}")
    return result.stdout


def _tracked_gate_code(repo_root: Path) -> dict[str, str]:
    """Map each gate-code path in HEAD to its blob id."""
    listing = _git_output(
        repo_root, "ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", *_GATE_CODE_ROOTS,
    )
    tracked: dict[str, str] = {}
    for entry in listing.split(b"\0"):
        if entry:
            meta, _separator, raw_path = entry.partition(b"\t")
            tracked[os.fsdecode(raw_path)] = meta.split(b" ")[2].decode("ascii")
    return tracked


def _disk_gate_code(repo_root: Path, tracked: Mapping[str, str]):
    """Return ``(untracked_code, symlinks, present_tracked)`` found under the roots."""
    untracked: list[str] = []
    symlinks: list[str] = []
    present: list[str] = []
    for root in _GATE_CODE_ROOTS:
        for current, directories, files in os.walk(repo_root / root, followlinks=False):
            # A private PYTHONPYCACHEPREFIX makes checkout-local bytecode irrelevant.
            directories[:] = [name for name in directories if name != "__pycache__"]
            current_path = Path(current)
            for name in directories:
                if (current_path / name).is_symlink():
                    symlinks.append((current_path / name).relative_to(repo_root).as_posix())
            for name in files:
                path = current_path / name
                relative = path.relative_to(repo_root).as_posix()
                if stat.S_ISLNK(path.lstat().st_mode):
                    symlinks.append(relative)
                elif relative in tracked:
                    present.append(relative)
                elif PurePosixPath(relative).suffix in _CODE_SUFFIXES:
                    untracked.append(relative)
    return untracked, symlinks, present


def _assert_no_untracked_gate_code(repo_root: Path) -> None:
    """Refuse gate code that is not byte-identical to the claimed HEAD.

    The gate claims to judge one exact commit, yet its own helpers are imported
    from the caller's mutable tree. Every file under each import root must
    therefore be (a) tracked in HEAD, or non-code (b) with a working-tree blob
    equal to HEAD's, and (c) free of symlinks. Plain untracked and ignored
    helpers are both refused, as is a tracked helper edited, replaced or deleted
    since HEAD, before anything imports or executes it.
    """
    tracked = _tracked_gate_code(repo_root)
    untracked, symlinks, present = _disk_gate_code(repo_root, tracked)
    modified = sorted(set(tracked) - set(present) - set(symlinks))
    for start in range(0, len(present), _HASH_CHUNK):
        chunk = present[start:start + _HASH_CHUNK]
        digests = _git_output(
            repo_root, "hash-object", "--no-filters", "--", *chunk,
        ).decode("ascii").split()
        if len(digests) != len(chunk):
            raise RuntimeError("cannot bind gate helper source to HEAD: hash-object mismatch")
        modified.extend(
            path for path, digest in zip(chunk, digests) if digest != tracked[path]
        )
    problems = []
    if untracked:
        problems.append("untracked or ignored executable source: " + ", ".join(untracked[:8]))
    if symlinks:
        problems.append("symlinks: " + ", ".join(symlinks[:8]))
    if modified:
        problems.append("tracked source changed from HEAD: " + ", ".join(sorted(modified)[:8]))
    if problems:
        raise RuntimeError(
            "release gate code is not bound to the selected HEAD; " + "; ".join(problems)
        )


@contextmanager
def _private_gate_bytecode_cache():
    """Keep Python from importing or writing checkout-local bytecode caches."""
    previous_prefix = sys.pycache_prefix
    previous_environment = os.environ.get("PYTHONPYCACHEPREFIX")
    with tempfile.TemporaryDirectory(prefix="cmru-release-gate-pycache-") as cache:
        sys.pycache_prefix = cache
        os.environ["PYTHONPYCACHEPREFIX"] = cache
        try:
            yield
        finally:
            sys.pycache_prefix = previous_prefix
            if previous_environment is None:
                os.environ.pop("PYTHONPYCACHEPREFIX", None)
            else:
                os.environ["PYTHONPYCACHEPREFIX"] = previous_environment


def _is_failure_line(line: str) -> bool:
    return line.startswith(("FAILED ", "ERROR "))


def _report_lane_failure(project_root: Path, lane: str, since_ns: int) -> None:
    """Name the failing tests of a failed lane (BG-03).

    A lane's own console output can hide pytest's short-summary lines (assay
    keeps them only in the verdict's ``result_stdout_tail``). Print every
    ``FAILED``/``ERROR`` line from verdicts and junit files written by this
    lane run so the release log always names the test.
    """
    lines: list[str] = []
    for verdict in sorted((project_root / ".assay").glob("verdict-*.json")):
        try:
            if verdict.stat().st_mtime_ns < since_ns:
                continue
            tail = json.loads(verdict.read_text(encoding="utf-8")).get("result_stdout_tail")
        except (OSError, ValueError):
            continue
        if isinstance(tail, str):
            lines.extend(line for line in tail.splitlines() if _is_failure_line(line))
    junit = project_root / "junit-coverage.xml"
    try:
        if junit.stat().st_mtime_ns >= since_ns:
            for case in ElementTree.parse(junit).iter("testcase"):
                if case.find("failure") is not None or case.find("error") is not None:
                    lines.append(f"FAILED {case.get('classname')}::{case.get('name')} (junit)")
    except (OSError, ElementTree.ParseError):
        pass
    if lines:
        print(f"cmru-release-gate: lane {lane!r} failed; failing tests:", file=sys.stderr)
        for line in dict.fromkeys(lines):
            print(f"  {line}", file=sys.stderr)


POSTPONED_MARKER = Path(".assay") / "mutation-postponed-cmru.json"
POSTPONE_REASON = (
    "operator-approved provisional CMRU release (KI-79; the marker's target_version names "
    "which): the R2 mutation lane was skipped and must be run afterwards against the "
    "released tag"
)
FIRST_RELEASE_POSTPONE_REASON = (
    "first CMRU release has no earlier published source baseline; whole-source R0/R1/R3 ran, "
    "and whole-source R2 must be run afterwards with "
    f"`run-gate {FIRST_RELEASE_R2_FOLLOW_UP_LANE}`"
)
#: Version window each postponement id may defer R2 for: ``(lowest, below)``, i.e.
#: ``lowest <= version < below``; an id may list several windows. KI-79: the whole 6.2 patch
#: line (6.2.0 by the 2026-10-14 operator decision; widened 2026-10-21 -- "release more cmru
#: also without prior R2" -- to every 6.2.x) and, by the 2026-10-23 operator decision, the 7.0
#: line (the 7.0.0 release carrying the mandatory `[github] token_source`) and, by the 2026-10-27
#: operator decision (R2 waived for every release of the current release wave), the 7.1 line.
#: Every other version still refuses; moving a window needs a new operator decision and a reviewed change here.
POSTPONEMENT_RELEASE_VERSIONS = {"KI-79": (((6, 2, 0), (6, 3, 0)), ((7, 0, 0), (7, 1, 0)), ((7, 1, 0), (7, 2, 0)))}


def _version_tuple(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def _postponement_window_text(windows: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...]) -> str:
    return " or ".join(
        f">= {'.'.join(map(str, low))} and < {'.'.join(map(str, below))} "
        f"(the {low[0]}.{low[1]} patch line)"
        for low, below in windows
    )
#: What a later whole-source/full mutation follow-up needs before it can run.
#: The marker and the snapshot manifest carry these verbatim so a reader never
#: has to infer them from prose. ``d4-capacity-evidence`` is named, not defined,
#: here: its content is owned by the Run-Gate RG-55 resource-profiling program.
FOLLOW_UP_PREREQUISITES = (
    {
        "id": "release-tag-branch",
        "requirement": (
            "a branch created at the published release tag, so the follow-up judges "
            "the exact tagged source rather than a later main"
        ),
    },
    {
        "id": "cgprofile-observer",
        "requirement": (
            "the CGProfile host observer deployed and reachable "
            "(the follow-up lane declares resource_observer = \"cgprofile\")"
        ),
    },
    {
        "id": "d4-capacity-evidence",
        "requirement": (
            "the D-4 capacity evidence the Run-Gate follow-up lane requires for a "
            "multi-hour R2 run (RG-55 D-4; CMRU does not define or fabricate it)"
        ),
    },
)


def _tracking_id(value: str) -> str:
    """Return a stripped, non-empty postponement tracking id or refuse."""
    tracking_id = value.strip()
    if not tracking_id:
        raise argparse.ArgumentTypeError("the postponement tracking id must be non-empty")
    return tracking_id


def _prepared_release_version(project_root: Path) -> str | None:
    """Read the newest dated release heading prepared by CMRU before the gate."""
    changelog = project_root / "CHANGES.md"
    try:
        markdown = changelog.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read prepared CMRU release changelog: {changelog}: {exc}") from exc
    source_root = PROJECT_ROOT / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    try:
        from cmru.release_notes import markdown_h2_headings
    except ImportError as exc:
        raise RuntimeError("cannot load CMRU's release-heading parser") from exc
    for heading in markdown_h2_headings(markdown):
        match = re.fullmatch(
            r"## \[((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\] - \d{4}-\d{2}-\d{2}",
            heading.text,
        )
        if match is not None:
            return match.group(1)
        # Fail closed: the newest dated heading decides. A prepared heading that is not
        # strict X.Y.Z (``7.2.0rc1``) must not fall through to an older, authorized one.
        if re.match(r"## \[(?!Unreleased\])[^\]]*\] - \d{4}-\d{2}-\d{2}", heading.text):
            raise RuntimeError(
                f"prepared CMRU release heading is not a strict X.Y.Z version: {heading.text!r}"
            )
    return None


def _validate_postponement(
    project_root: Path, tracking_id: str, *, first_release: bool = False,
) -> str:
    """Allow a documented first-release or KI-79 R2 deferral."""
    if first_release:
        if tracking_id != FIRST_RELEASE_POSTPONEMENT_ID:
            raise RuntimeError(
                "a CMRU checkout with no published stable release must use the "
                f"{FIRST_RELEASE_POSTPONEMENT_ID} whole-source R2 follow-up"
            )
        observed_version = _prepared_release_version(project_root)
        if observed_version is None:
            raise RuntimeError(
                "first-release R2 deferral requires a dated, prepared CMRU release heading"
            )
        return observed_version
    windows = POSTPONEMENT_RELEASE_VERSIONS.get(tracking_id)
    if windows is None:
        raise RuntimeError(
            f"mutation postponement {tracking_id!r} is not authorized for a release"
        )
    observed_version = _prepared_release_version(project_root)
    if observed_version is None or not any(
        low <= _version_tuple(observed_version) < below for low, below in windows
    ):
        observed = observed_version or "no prepared release version"
        raise RuntimeError(
            f"mutation postponement {tracking_id} is authorized only for CMRU "
            f"{_postponement_window_text(windows)}; prepared changelog identifies {observed}"
        )
    return observed_version


def _record_postponement(
    project_root: Path, tracking_id: str, *, first_release: bool = False,
) -> tuple[Path, dict[str, object]]:
    """Write the retained marker saying the mutation lane was deferred, and WARN.

    Called only once EVERY required non-mutation lane and the final canary have
    passed: the marker is a success-shaped record and must never exist for a
    gate that did not finish. The marker lives under ``.assay`` so cmru's
    ``evidence_paths`` retain it with the rest of the gate evidence; a full
    gate removes it. Returns the marker path and the structured deferral that
    the snapshot manifest also records.
    """
    marker = project_root / POSTPONED_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    candidate_commit = _git_output(project_root.parent, "rev-parse", "HEAD").decode("ascii").strip()
    target_version = _validate_postponement(
        project_root, tracking_id, first_release=first_release,
    )
    # The standalone `mutation` lane refuses to run without the baseline facts
    # only the aggregate gate prepares, so the documented follow-up is `gate`.
    follow_up_lane = FIRST_RELEASE_R2_FOLLOW_UP_LANE if first_release else "gate"
    reason = FIRST_RELEASE_POSTPONE_REASON if first_release else POSTPONE_REASON
    deferral = {
        "lane": "mutation",
        "tracking_id": tracking_id,
        "reason": reason,
        "follow_up_lane": follow_up_lane,
        "follow_up_prerequisites": [dict(item) for item in FOLLOW_UP_PREREQUISITES],
    }
    record = {
        "schema_version": 3,
        "result": "provisional",
        "lane": "mutation",
        "status": "postponed",
        "tracking_id": tracking_id,
        "candidate_commit": candidate_commit,
        "target_version": target_version,
        "baseline_state": "no-published-stable-release" if first_release else "prior-release-available",
        "follow_up_lane": follow_up_lane,
        "follow_up_prerequisites": deferral["follow_up_prerequisites"],
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason,
    }
    try:
        marker.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except BaseException:
        marker.unlink(missing_ok=True)  # never leave a half-written success-shaped record
        raise
    print(
        f"cmru-release-gate: WARN: PROVISIONAL PASS: every required non-mutation lane and the "
        f"canary passed, but the mutation lane is DEFERRED ({tracking_id}); this is NOT "
        f"an R2 pass. Follow-up lane: {follow_up_lane}. Marker: {marker}",
        file=sys.stderr,
    )
    return marker, deferral


def _invoke_lane(
    repo_root: Path,
    lane: str,
    environment: Mapping[str, str],
    worktree_root: Path | None = None,
) -> int:
    if worktree_root is None:
        worktree_root = repo_root
    started = time.time_ns()
    lane_environment = dict(environment)
    executable = lane_environment.pop(_RUN_GATE_EXECUTABLE_ENV, "run-gate")
    process = subprocess.Popen(
        [executable, "--worktree", str(worktree_root), lane],
        cwd=repo_root / "cmru",
        env=lane_environment,
    )
    try:
        returncode = process.wait()
    except (GateInterrupted, KeyboardInterrupt) as exc:
        signum = exc.signum if isinstance(exc, GateInterrupted) else signal.SIGTERM
        if process.poll() is None:
            try:
                process.send_signal(signum)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=35)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    if returncode:
        _report_lane_failure(worktree_root / "cmru", lane, started)
    return returncode


def _run_snapshot_lane(
        repo_root: Path, lane: str, environment: Mapping[str, str], snapshot,
        gate_snapshot, *, after_success=None) -> int:
    _assert_no_untracked_gate_code(repo_root)
    expected_identity = snapshot.run_gate_identity
    lane_environment = dict(environment)
    if expected_identity is not None:
        current_identity = _recheck_run_gate_identity(repo_root, expected_identity)
        if current_identity != expected_identity:
            raise RuntimeError(
                "Run-Gate identity changed during the CMRU release gate; "
                "refusing to run the next lane under a different judge"
            )
        executable_path = expected_identity.get("executable")
        if isinstance(executable_path, str):
            lane_environment[_RUN_GATE_EXECUTABLE_ENV] = executable_path
        image_id = expected_identity.get("tester_image_id")
        if isinstance(image_id, str):
            # Run-Gate reads this override only for an environment that declares
            # `image_env = "RUN_GATE_TESTER_UNIFIED_IMAGE"` (here: cmru-mutation;
            # the root tester-unified declaration lands with the Run-Gate wave).
            # Elsewhere the image-ID recheck around each lane detects a retag.
            lane_environment["RUN_GATE_TESTER_UNIFIED_IMAGE"] = image_id
    # Refresh the host manifest FIRST: prepare_lane seeds the lane's copy from
    # it, and the live-snapshot check accepts only a manifest this recent.
    gate_snapshot.write_snapshot_manifest(snapshot, "running")
    gate_snapshot.prepare_lane(snapshot, lane)
    try:
        result = _invoke_lane(
            repo_root, lane, lane_environment, snapshot.worktree_path,
        )
        if expected_identity is not None:
            current_identity = _recheck_run_gate_identity(repo_root, expected_identity)
            if current_identity != expected_identity:
                raise RuntimeError(
                    "Run-Gate identity changed while a CMRU release lane was "
                    "running; its evidence cannot be combined with the other lanes"
                )
        snapshot.lane_exit_codes[lane] = result
        if result == 0 and after_success is not None:
            after_success(snapshot)
        return result
    except GateInterrupted as exc:
        snapshot.lane_exit_codes[lane] = 128 + exc.signum
        raise
    except BaseException:
        snapshot.lane_exit_codes[lane] = None
        raise
    finally:
        try:
            if lane == REF_PROBE_LANE:
                gate_snapshot.delete_probe_refs(snapshot)
            gate_snapshot.finish_lane(snapshot, lane)
        finally:
            gate_snapshot.write_snapshot_manifest(snapshot, "running")


def run_release_gate(
    repo_root: Path,
    *,
    temp_parent: Path = Path("/tmp"),
    postpone_mutation: str | None = None,
) -> int:
    with _private_gate_bytecode_cache():
        return _run_release_gate_with_private_cache(
            repo_root, temp_parent=temp_parent,
            postpone_mutation=postpone_mutation,
        )


def _run_release_gate_with_private_cache(
    repo_root: Path,
    *,
    temp_parent: Path = Path("/tmp"),
    postpone_mutation: str | None = None,
) -> int:
    if postpone_mutation is not None:
        postpone_mutation = _tracking_id(postpone_mutation)
    repo_root = repo_root.resolve(strict=True)
    project_root = repo_root / "cmru"
    if not project_root.is_dir() or project_root.is_symlink():
        raise RuntimeError(f"CMRU project directory is missing or unsafe: {project_root}")
    if PROJECT_ROOT.resolve() != project_root.resolve():
        raise RuntimeError(
            f"release gate helper is not running from the selected worktree: {PROJECT_ROOT}"
        )
    _assert_no_untracked_gate_code(repo_root)
    _preflight_assay_source(repo_root)
    assay_git, baseline = _load_components()
    run_gate_identity = _current_run_gate_identity(repo_root)
    if assay_git.repo_top(project_root).resolve() != repo_root:
        raise RuntimeError("selected CMRU project does not belong to the requested worktree")
    temp_parent = temp_parent.resolve(strict=True)
    if temp_parent == repo_root or temp_parent.is_relative_to(repo_root):
        raise RuntimeError("private CMRU gate lock directory is inside the judged worktree")
    with _release_gate_lock(temp_parent), _gate_signal_handlers():
        _assert_no_untracked_gate_code(repo_root)
        from cmru import gate_snapshot

        # Every input check runs BEFORE the snapshot is opened or any evidence
        # is touched: an invocation refused here leaves the previous run's
        # commit-bound marker and manifest exactly as they were.
        plan = _plan_gate(repo_root, project_root, baseline, postpone_mutation)
        snapshot = gate_snapshot.open_gate_snapshot(repo_root)
        state = "incomplete"
        started = False
        try:
            if snapshot.run_gate_identity and snapshot.run_gate_identity != run_gate_identity:
                raise RuntimeError(
                    "resumed CMRU gate snapshot was judged by a different Run-Gate identity; "
                    "remove the finished snapshot and start a fresh gate"
                )
            snapshot.run_gate_identity = run_gate_identity
            # A valid new gate starts here. Whatever an earlier run left behind
            # (success-shaped marker or manifest field) is stale from now on.
            snapshot.deferred_lanes = []
            (project_root / POSTPONED_MARKER).unlink(missing_ok=True)
            started = True
            gate_snapshot.plant_source_canary(snapshot)
            gate_snapshot.write_snapshot_manifest(snapshot, "running")
            try:
                result = _run_locked_gate(
                    repo_root,
                    project_root,
                    baseline,
                    assay_git,
                    snapshot,
                    plan,
                )
            except (GateInterrupted, KeyboardInterrupt):
                state = "interrupted"
                raise
            except BaseException:
                state = "failed"
                raise
            if result != 0:
                state = "failed"
            elif snapshot.deferred_lanes:
                state = "provisional"  # exit 0, but a required lane was deferred
            else:
                state = "passed"
            return result
        finally:
            # Success is decided only after cleanup succeeded: `state` is a
            # claim until _finish_gate_snapshot has run.
            _finish_gate_snapshot(gate_snapshot, snapshot, project_root, state, started)


def _finish_gate_snapshot(gate_snapshot, snapshot, project_root: Path, state: str, started: bool) -> None:
    """Clean up the snapshot, then persist the final evidence.

    Both cleanup steps always run. A cleanup failure after a claimed success
    (``passed`` / ``provisional``) turns the run into ``failed``: the marker is
    removed, ``deferred_lanes`` is cleared and the failure is re-raised so the
    gate returns non-zero. No success-shaped record can survive it.
    """
    errors: list[BaseException] = []
    steps = (
        (gate_snapshot.remove_source_canary, gate_snapshot.close_gate_snapshot)
        if started else (gate_snapshot.close_gate_snapshot,)
    )
    for step in steps:
        try:
            step(snapshot)
        except BaseException as exc:  # noqa: BLE001 - every step must run, then we re-raise
            errors.append(exc)
    if started:
        if errors and state in ("passed", "provisional"):
            state = "failed"
        marker = project_root / POSTPONED_MARKER
        if state != "provisional":
            marker.unlink(missing_ok=True)
            snapshot.deferred_lanes = []
        try:
            gate_snapshot.write_snapshot_manifest(snapshot, state)
        except BaseException as exc:  # noqa: BLE001
            marker.unlink(missing_ok=True)  # no marker without its manifest
            errors.append(exc)
    if errors:
        raise errors[0]


def _plan_gate(repo_root: Path, project_root: Path, baseline, postpone_mutation: str | None) -> dict:
    """Resolve and validate everything a new gate needs, touching no evidence."""
    _assert_no_untracked_gate_code(repo_root)
    auth = baseline._repository_git_auth(repo_root)
    latest_stable_tag, remote_tags = baseline.published_release_state(
        repo_root, git_auth=auth,
    )
    first_release = latest_stable_tag is None
    if first_release:
        if postpone_mutation is None:
            raise RuntimeError(
                "origin has no published stable CMRU release tag, so the changed-source "
                "mutation baseline does not exist; release through `gate-provisional` to "
                "run whole-source R0/R1/R3 and record the required whole-source R2 follow-up"
            )
        if postpone_mutation != "KI-79":
            raise RuntimeError(
                "origin has no published stable CMRU release tag; only the release workflow's "
                "provisional R2 deferral can select the whole-source first-release gate"
            )
        expected_commit = _git_output(repo_root, "rev-parse", "HEAD").decode("ascii").strip()
        protected_commit = os.environ.get("CMRU_RELEASE_GATE_COMMIT", "")
        if protected_commit != expected_commit:
            raise RuntimeError(
                "whole-source first-release qualification is permitted only inside CMRU's "
                "protected release transaction with CMRU_RELEASE_GATE_COMMIT equal to HEAD"
            )
        effective_postponement = FIRST_RELEASE_POSTPONEMENT_ID
        _validate_postponement(
            project_root, effective_postponement, first_release=True,
        )
        assay_lane = FIRST_RELEASE_ASSAY_LANE
    else:
        effective_postponement = postpone_mutation
        assay_lane = "assay"
        if postpone_mutation is not None:
            _validate_postponement(project_root, postpone_mutation)
    return {
        "auth": auth,
        "remote_tags": remote_tags,
        "first_release": first_release,
        "effective_postponement": effective_postponement,
        "assay_lane": assay_lane,
    }


def _run_locked_gate(
    repo_root: Path,
    project_root: Path,
    baseline,
    assay_git,
    snapshot,
    plan: dict,
) -> int:
    _assert_no_untracked_gate_code(repo_root)
    auth = plan["auth"]
    remote_tags = plan["remote_tags"]
    first_release = plan["first_release"]
    effective_postponement = plan["effective_postponement"]
    assay_lane = plan["assay_lane"]
    lane_environment = _lane_environment()
    from cmru import gate_snapshot

    # Live detector probe first: a lane that writes a protected ref from inside
    # the real tester container must be seen by the gate, or no later lane's
    # silence about refs means anything.
    result = _run_snapshot_lane(
        repo_root, REF_PROBE_LANE, lane_environment, snapshot, gate_snapshot,
        after_success=gate_snapshot.verify_ref_probe_detected,
    )
    if result:
        return result
    for lane in (LIVE_WHEEL_LANE, assay_lane, "coverage"):
        result = _run_snapshot_lane(
            repo_root, lane, lane_environment, snapshot, gate_snapshot,
        )
        if result:
            return result

    if effective_postponement is None:
        _assert_no_untracked_gate_code(repo_root)
        gate_snapshot.assert_source_head(snapshot)
        with gate_snapshot.host_git_hooks_disabled(repo_root):
            facts = baseline.build_facts(
                repo_root,
                project_root,
                git_auth=auth,
                assay_git=assay_git,
                remote_tag_commits=remote_tags,
            )
        facts_json = json.dumps(facts, separators=(",", ":"), sort_keys=True)
        if auth.token and auth.token in facts_json:
            raise RuntimeError("remote baseline facts unexpectedly contain publisher credentials")
        mutation_environment = dict(lane_environment)
        mutation_environment[FACTS_ENV_KEY] = facts_json
        result = _run_snapshot_lane(
            repo_root, "mutation", mutation_environment, snapshot, gate_snapshot,
        )
        if result:
            return result

    result = _run_snapshot_lane(
        repo_root, "canary", lane_environment, snapshot, gate_snapshot,
    )
    if result:
        return result
    gate_snapshot.assert_source_head(snapshot)
    if effective_postponement is not None:
        # Provisional gate: ONLY the mutation lane (and its remote-facts
        # preparation) was skipped. The deferral is persisted now, after every
        # required lane and the canary succeeded, and it is the last thing that
        # can fail before the zero exit.
        _marker, deferral = _record_postponement(
            project_root, effective_postponement, first_release=first_release,
        )
        snapshot.deferred_lanes = [deferral]
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument(
        "--postpone-mutation",
        metavar="TRACKING-ID",
        type=_tracking_id,
        default=None,
        help=(
            "skip ONLY the mutation lane (PROVISIONAL gate) and record the "
            "postponement under TRACKING-ID in .assay/mutation-postponed-cmru.json"
        ),
    )
    args = parser.parse_args(argv)
    try:
        return run_release_gate(args.worktree, postpone_mutation=args.postpone_mutation)
    except Exception as exc:
        print(f"cmru-release-gate: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        _ensure_gate_environment(sys.argv[1:])
    except RuntimeError as exc:
        print(f"cmru-release-gate: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(main())
