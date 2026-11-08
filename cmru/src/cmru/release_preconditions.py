"""Read-only checks that keep known release-gate refusals out of gate time."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Mapping


_BOOTSTRAP_RUN_GATE_VERSION_SEQUENCE = r'''RUN_GATE_VERSION="$(
  git describe --tags --abbrev=0 --match 'run-gate-v*' 2>/dev/null ||
    awk -F'[][]' '/^## \[[0-9]+\.[0-9]+\.[0-9]+\] - / { print $2; exit }' \
      run-gate-project/CHANGES.md
)"
RUN_GATE_VERSION="${RUN_GATE_VERSION#run-gate-v}"
if [[ ! "$RUN_GATE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Run-Gate version must come from a run-gate-v* tag or a stable run-gate-project/CHANGES.md heading; got '$RUN_GATE_VERSION'" >&2
  exit 2
fi
export RUN_GATE_VERSION'''
_BOOTSTRAP_RUN_GATE_LITERAL_PATTERNS = (
    re.compile(r"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE=(?:\"|')?([0-9]+\.[0-9]+\.[0-9]+)"),
    re.compile(r"run_gate-([0-9]+\.[0-9]+\.[0-9]+)-"),
    re.compile(r"cmru release run-gate-project --set-version (\"|')?([0-9]+\.[0-9]+\.[0-9]+)"),
    re.compile(r"\bRun-Gate ([0-9]+\.[0-9]+\.[0-9]+)"),
)
_DOCKER_UNAVAILABLE = (
    "cannot connect to the docker daemon",
    "error during connect",
    "is the docker daemon running",
    "permission denied while trying to connect to the docker daemon",
    "context not found",
)


@dataclass(frozen=True)
class GatePrecondition:
    project: str
    check: str
    status: str
    detail: str
    remedy: str = ""


def _git_blob_text(source_git_root: Path, revision: str, path: str) -> str:
    """Read one tracked file from the exact release source commit."""
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=source_git_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git exited {result.returncode}"
        raise RuntimeError(f"cannot read {path} from release source {revision[:12]}: {detail}")
    return result.stdout


def _load_release_gate(source_git_root: Path, revision: str) -> ModuleType:
    """Load the gate driver from the source revision the release will test."""
    path = "cmru/tools/run_release_gate.py"
    source = _git_blob_text(source_git_root, revision, path)
    module = ModuleType("_cmru_release_gate_preconditions")
    module.__file__ = str(source_git_root / path)
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _prepared_version_error(gate, version: str) -> str | None:
    """Ask the release gate's own postponement validator about this planned version."""
    with tempfile.TemporaryDirectory(prefix="cmru-release-precondition-") as temporary:
        project_root = Path(temporary)
        (project_root / "CHANGES.md").write_text(
            f"## [{version}] - 2000-01-01\n",
            encoding="utf-8",
        )
        try:
            gate._validate_postponement(project_root, "KI-79")
        except RuntimeError as exc:
            return str(exc)
    return None


def _is_docker_unavailable(detail: str) -> bool:
    lowered = detail.lower()
    return any(marker in lowered for marker in _DOCKER_UNAVAILABLE)


def _selected_tester_image_from_source(source_git_root: Path, revision: str) -> str:
    """Read the source snapshot's selected image for a precise rebuild remedy."""
    document = tomllib.loads(
        _git_blob_text(source_git_root, revision, "run-gate.root.toml")
    )
    try:
        environment = document["environments"]["tester-unified"]
        image = environment["image"]
        image_env = environment.get("image_env")
    except (KeyError, TypeError) as exc:
        raise RuntimeError("cannot resolve tester-unified image from run-gate.root.toml") from exc
    if image_env is not None:
        if not isinstance(image_env, str) or not image_env:
            raise RuntimeError("tester-unified image_env is malformed")
        if image_env in os.environ:
            image = os.environ[image_env]
            if not image:
                raise RuntimeError(f"selected tester-unified image override ${image_env} is empty")
    if not isinstance(image, str) or not image:
        raise RuntimeError("tester-unified image is missing from run-gate.root.toml")
    return image


def _materialize_gate_identity_config(
    source_git_root: Path, revision: str, destination: Path,
) -> None:
    """Give the gate preflight only the tracked config files from its source commit."""
    orchestration = _git_blob_text(source_git_root, revision, "cmru.orchestration.toml")
    run_gate_root = _git_blob_text(source_git_root, revision, "run-gate.root.toml")
    try:
        document = tomllib.loads(orchestration)
        projects = document["orchestration"]["project"]
    except (KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError("cannot resolve projects from cmru.orchestration.toml") from exc
    if not isinstance(projects, dict) or not projects:
        raise RuntimeError("cmru.orchestration.toml has no project configs")

    destination.mkdir(parents=True, exist_ok=True)
    (destination / "cmru.orchestration.toml").write_text(orchestration, encoding="utf-8")
    (destination / "run-gate.root.toml").write_text(run_gate_root, encoding="utf-8")
    for project_name, entry in projects.items():
        if not isinstance(entry, dict):
            raise RuntimeError(f"orchestration project {project_name!r} is malformed")
        config_value = entry.get("config")
        if not isinstance(config_value, str) or not config_value.strip():
            raise RuntimeError(f"orchestration project {project_name!r} has no config path")
        config_path = Path(config_value)
        if (
            config_path.is_absolute() or ".." in config_path.parts
            or config_path.name != "cmru.toml"
        ):
            raise RuntimeError(
                f"orchestration project {project_name!r} has an unsafe config path"
            )
        content = _git_blob_text(source_git_root, revision, config_path.as_posix())
        target = destination / config_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (destination / "cmru").mkdir(exist_ok=True)


def _run_gate_release_identity_preflight(
    _repo_root: Path, source_git_root: Path, revision: str,
) -> GatePrecondition:
    """Use the release gate's installed-wheel and selected-image preflight."""
    version = None
    image = None
    try:
        image = _selected_tester_image_from_source(source_git_root, revision)
        gate = _load_release_gate(source_git_root, revision)
        with tempfile.TemporaryDirectory(prefix="cmru-gate-identity-") as temporary:
            identity_root = Path(temporary)
            _materialize_gate_identity_config(source_git_root, revision, identity_root)
            checked = gate._current_run_gate_identity(identity_root)
        version = checked.get("version")
        if not isinstance(version, str) or not version:
            raise RuntimeError("Run-Gate identity preflight did not report its version")
        wheel_sha256 = checked.get("published_wheel_sha256")
        if (
            not isinstance(wheel_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", wheel_sha256) is None
        ):
            raise RuntimeError(
                "Run-Gate identity preflight did not report its published wheel SHA-256"
            )
        image = checked.get("tester_image", image)
    except Exception as exc:
        detail = str(exc)
        if version is None:
            version_match = re.search(r"expected version=['\"]([^'\"]+)", detail)
            if version_match is not None:
                version = version_match.group(1)
        if image is None:
            image_match = re.search(r"tester-unified image ['\"]([^'\"]+)", detail)
            if image_match is not None:
                image = image_match.group(1)
        if _is_docker_unavailable(detail):
            return GatePrecondition(
                "cmru", "Run-Gate image identity", "SKIPPED",
                f"Docker is unavailable; the selected tester image was not inspected ({detail}).",
            )
        if version and image and "tester-unified image" in detail:
            remedy = (
                f"Rebuild {image} from the published Run-Gate {version} wheel with "
                f"`tester-unified/build-image.sh --run-gate-published {version} --tag {image}`, "
                "then rerun the dry-run."
            )
        elif "run-gate.root.toml" in detail or "cmru.orchestration.toml" in detail:
            remedy = (
                "Repair the Run-Gate or CMRU orchestration config in the selected release "
                "commit, then rerun the dry-run."
            )
        elif "cannot resolve the published Run-Gate release" in detail or "cannot download" in detail:
            remedy = "Restore access to the published Run-Gate release and its wheel assets, then rerun the dry-run."
        elif any(
            marker in detail.lower()
            for marker in (
                "run-gate command is missing",
                "could not locate installed run-gate",
                "installed run-gate",
                "installed run-gate package",
            )
        ):
            remedy = (
                "Install a stable, non-editable Run-Gate release wheel that meets CMRU's "
                "declared minimum and put its `run-gate` CLI on PATH, then rerun."
            )
        else:
            remedy = "Resolve the Run-Gate preflight error shown above, then rerun the dry-run."
        return GatePrecondition(
            "cmru", "Run-Gate image identity", "FAIL", detail, remedy,
        )
    image = checked.get("tester_image", image)
    return GatePrecondition(
        "cmru", "Run-Gate image identity", "PASS",
        f"Run-Gate {version} wheel sha256 {wheel_sha256} matches {image}.",
    )


def check_run_gate_image_precondition(
    repo_root: Path, source_git_root: Path, revision: str,
) -> GatePrecondition:
    """Inspect the selected image through the exact release-gate identity check."""
    if shutil.which("docker") is None:
        return GatePrecondition(
            "cmru", "Run-Gate image identity", "SKIPPED",
            "Docker CLI is unavailable; the selected tester image was not inspected.",
        )
    return _run_gate_release_identity_preflight(repo_root, source_git_root, revision)


def _normalise_documented_code(text: str) -> str:
    """Compare command text without Markdown fenced-block indentation."""
    return "\n".join(line.strip() for line in text.splitlines())


def _bootstrap_literal_run_gate_versions(bootstrap: str) -> set[str]:
    versions: set[str] = set()
    for pattern in _BOOTSTRAP_RUN_GATE_LITERAL_PATTERNS:
        for match in pattern.finditer(bootstrap):
            version = next(
                group
                for group in match.groups()
                if group and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", group)
            )
            versions.add(version)
    return versions


def bootstrap_run_gate_version_error(bootstrap: str) -> str | None:
    """Require one documented derivation and no literal Run-Gate version."""
    literal = _bootstrap_literal_run_gate_versions(bootstrap)
    if literal:
        return (
            "docs/BOOTSTRAP-FROM-ZERO.md contains literal Run-Gate version(s) "
            f"{', '.join(sorted(literal))}; replace every release-version occurrence "
            "with `$RUN_GATE_VERSION`."
        )
    normalised = _normalise_documented_code(bootstrap)
    sequence = _normalise_documented_code(_BOOTSTRAP_RUN_GATE_VERSION_SEQUENCE)
    if normalised.count(sequence) != 1:
        return (
            "docs/BOOTSTRAP-FROM-ZERO.md must contain exactly one documented "
            "RUN_GATE_VERSION derivation using `git describe --tags --abbrev=0 "
            "--match 'run-gate-v*'` with the stable run-gate-project/CHANGES.md fallback."
        )
    return None


def _bootstrap_version_precondition(
    source_git_root: Path, revision: str,
) -> GatePrecondition:
    try:
        bootstrap = _git_blob_text(source_git_root, revision, "docs/BOOTSTRAP-FROM-ZERO.md")
        error = bootstrap_run_gate_version_error(bootstrap)
    except Exception as exc:
        return GatePrecondition(
            "cmru", "Bootstrap Run-Gate version", "FAIL", str(exc),
            "Restore a readable bootstrap document, then rerun the dry-run.",
        )
    if error is not None:
        return GatePrecondition(
            "cmru", "Bootstrap Run-Gate version", "FAIL", error,
            "Keep the single RUN_GATE_VERSION derivation and replace every literal "
            "Run-Gate release version with `$RUN_GATE_VERSION`, then rerun.",
        )
    return GatePrecondition(
        "cmru", "Bootstrap Run-Gate version", "PASS",
        "docs/BOOTSTRAP-FROM-ZERO.md derives RUN_GATE_VERSION once from the newest "
        "run-gate tag, with the stable CHANGES.md heading as the from-zero fallback.",
    )


def run_tests_uses_provisional_gate(config_text: str) -> bool:
    """True when ``[steps.run-tests]`` in a cmru.toml runs ``gate-provisional``.

    Only that lane carries the KI-79 R2 postponement, so only a release that
    configures it needs the waiver-window check.
    """
    document = tomllib.loads(config_text)
    commands = document.get("steps", {}).get("run-tests", {}).get("commands", [])
    return any("gate-provisional" in command.get("argv", []) for command in commands)


def evaluate_release_gate_preconditions(
    repo_root: Path,
    source_git_root: Path,
    revision: str,
    planned_versions: Mapping[str, str],
    *,
    gate_provisional: bool = True,
) -> list[GatePrecondition]:
    """Evaluate CMRU's three release-gate preconditions for the planned release.

    ``gate_provisional`` says whether cmru.toml's run-tests step runs the
    provisional gate; the KI-79 window only binds then.
    """
    version = planned_versions.get("cmru")
    if version is None:
        return []
    if not gate_provisional:
        return [
            GatePrecondition(
                "cmru", "KI-79 waiver window", "SKIPPED",
                "cmru.toml's run-tests step does not run `gate-provisional`; the full "
                "gate is configured, so no R2 postponement is used.",
            ),
            check_run_gate_image_precondition(repo_root, source_git_root, revision),
            _bootstrap_version_precondition(source_git_root, revision),
        ]
    try:
        gate = _load_release_gate(source_git_root, revision)
        waiver_error = _prepared_version_error(gate, version)
        if waiver_error is None:
            waiver = GatePrecondition(
                "cmru", "KI-79 waiver window", "PASS",
                f"planned version {version} is accepted by the release gate's KI-79 validator.",
            )
        else:
            waiver = GatePrecondition(
                "cmru", "KI-79 waiver window", "FAIL", waiver_error,
                "Change `cmru/cmru.toml`'s run-tests command from `run-gate gate-provisional` "
                "to `run-gate gate` (the full lane, no postponement; the check is then "
                "SKIPPED), or obtain a recorded operator decision to widen KI-79.",
            )
    except Exception as exc:
        waiver = GatePrecondition(
            "cmru", "KI-79 waiver window", "FAIL", str(exc),
            "Repair the release gate source in the selected release commit, then rerun the dry-run.",
        )
    return [
        waiver,
        check_run_gate_image_precondition(repo_root, source_git_root, revision),
        _bootstrap_version_precondition(source_git_root, revision),
    ]


def format_gate_precondition_report(rows: list[GatePrecondition]) -> str:
    """Render non-all-pass results with one actionable remedy per failure."""
    lines = ["Project  Check                       Status   Detail"]
    for row in rows:
        lines.append(f"{row.project:<8} {row.check:<29} {row.status:<8} {row.detail}")
        if row.status == "FAIL":
            lines.append(f"         Remedy: {row.remedy}")
    return "\n".join(lines)
