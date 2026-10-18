#!/usr/bin/env python3
"""Prepare and verify the exact Run-Gate wheel used by its release gate."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from zipfile import BadZipFile, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
RECEIPT_PATH = PROJECT_ROOT / ".assay" / "run-gate-release-wheel.json"
IMAGE_IDENTITY_PATH = Path("/etc/tester-unified-run-gate-wheel.json")
IMAGE_VERSION_LABEL = "org.vbpub.run-gate.version"
IMAGE_SHA256_LABEL = "org.vbpub.run-gate.wheel-sha256"
CLI_EXTENDED_SHA256_LABEL = "org.vbpub.cli-extended.wheel-sha256"
CLI_EXTENDED_SHA256_ENV = "RUN_GATE_TESTER_CLI_EXTENDED_SHA256"
CLI_EXTENDED_RESOLVE_ENV = "RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE"
BASE_IMAGE_LABEL = "org.vbpub.tester-unified.base-image"
BASE_IMAGE_ENV = "RUN_GATE_TESTER_UNIFIED_BASE_IMAGE"
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]*\Z")
_CANDIDATE_SELFTEST_CODE = "\n".join((
    "import importlib.metadata",
    "from pathlib import Path",
    "import run_gate",
    "import run_gate_admission",
    "distribution = importlib.metadata.distribution('run-gate')",
    "owned = {Path(distribution.locate_file(item)).resolve(strict=True) "
    "for item in (distribution.files or ())}",
    "for module in (run_gate, run_gate_admission):",
    "    path = Path(module.__file__).resolve(strict=True)",
    "    if path not in owned:",
    "        raise SystemExit(f'Run-Gate module loaded outside the installed wheel: {path}')",
    "raise SystemExit(run_gate.main(['selftest']))",
))
STABLE_SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)


class ReleaseCandidateError(RuntimeError):
    """Release inputs or their recorded artifact identities disagree."""


class NoStableReleaseTag(ReleaseCandidateError):
    """No prior stable Run-Gate release exists in this checkout."""


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ReleaseCandidateError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip()[:300]}"
        )
    return result.stdout.strip()


def prepared_release_version(
    changes_path: Path, *, allow_first_release: bool = False,
) -> str:
    """Read the dated version CMRU prepared for this candidate.

    A first stable release has no previous tag to compare against. Only
    :func:`prepare`, after binding CMRU's gate commit to this checkout, passes
    ``allow_first_release``. It then reads origin's ``run-gate-v*`` tags too:
    the first-release path needs origin to show no stable tag, and a checkout
    whose local tags are missing or older than origin's is refused. Ordinary
    calls read only local tags and still require a stable baseline.
    """
    source = REPOSITORY_ROOT / "cmru" / "src"
    source_entry = str(source)
    added_source = source_entry not in sys.path
    if added_source:
        sys.path.insert(0, str(source))
    try:
        try:
            import cmru.release_notes as release_notes
        except ImportError as exc:
            raise ReleaseCandidateError(
                "cannot load the selected checkout's CMRU release-heading parser"
            ) from exc
        parser_path = Path(release_notes.__file__).resolve()
        expected_parser = (source / "cmru" / "release_notes.py").resolve()
        if parser_path != expected_parser:
            raise ReleaseCandidateError(
                "CMRU release-heading parser resolved outside the selected checkout"
            )
    finally:
        if added_source:
            sys.path.remove(source_entry)
    try:
        markdown = changes_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseCandidateError(f"cannot read prepared changelog {changes_path}: {exc}") from exc
    prepared_version = None
    for heading in release_notes.markdown_h2_headings(markdown):
        match = re.fullmatch(r"## \[([^]]+)\] - \d{4}-\d{2}-\d{2}", heading.text)
        if match and STABLE_SEMVER.fullmatch(match.group(1)):
            prepared_version = match.group(1)
            break
    if prepared_version is None:
        raise ReleaseCandidateError(
            f"{changes_path} has no dated stable release section prepared by CMRU"
        )
    if allow_first_release:
        latest = _release_baseline()
        if latest is None:
            return prepared_version
    else:
        latest = _latest_release_tag()
    if _semver_key(prepared_version) <= _semver_key(latest[1]):
        raise ReleaseCandidateError(
            f"prepared Run-Gate version {prepared_version} is not newer than "
            f"the latest release tag {latest[0]}; run this gate inside `cmru release` "
            "after CMRU prepares the next dated changelog section"
        )
    return prepared_version


def _semver_key(version: str) -> tuple[int, int, int]:
    if not STABLE_SEMVER.fullmatch(version):
        raise ReleaseCandidateError(f"invalid stable Run-Gate version {version!r}")
    return tuple(int(part) for part in version.split("."))


def _stable_release_tags(tags: list[str]) -> list[tuple[tuple[int, int, int], str, str]]:
    stable: list[tuple[tuple[int, int, int], str, str]] = []
    for tag in tags:
        match = re.fullmatch(r"run-gate-v([0-9]+\.[0-9]+\.[0-9]+)", tag)
        if match and STABLE_SEMVER.fullmatch(match.group(1)):
            version = match.group(1)
            stable.append((_semver_key(version), tag, version))
    return stable


def _latest_release_tag() -> tuple[str, str]:
    """The newest stable release tag in this checkout (local refs only)."""
    stable = _stable_release_tags(_git("tag", "--list", "run-gate-v*").splitlines())
    if not stable:
        raise NoStableReleaseTag(
            "cannot prove the prepared Run-Gate version is new: no stable "
            "run-gate-vX.Y.Z release tag is available in this checkout"
        )
    _key, tag, version = max(stable)
    return tag, version


_LS_REMOTE_TAG_LINE = re.compile(
    r"([0-9a-f]{40}|[0-9a-f]{64})\trefs/tags/(run-gate-v[^\s^]+)(\^\{\})?"
)


def _origin_release_tags() -> list[str]:
    """Every ``run-gate-v*`` tag name origin advertises; ``[]`` only when proven.

    ``git ls-remote --exit-code`` exits 2 only when origin answered and holds no
    matching ref, which is the one proof that no release exists. Any other
    failure, or output this parser cannot account for, means the inventory
    could not be determined, and that is a refusal, never an empty list.
    """
    argv = [
        "git", "-C", str(REPOSITORY_ROOT), "ls-remote", "--exit-code", "--tags",
        "origin", "refs/tags/run-gate-v*",
    ]
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            argv, check=False, capture_output=True, text=True, env=env, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseCandidateError(
            f"cannot read origin's run-gate-v* release tags: {exc}"
        ) from exc
    if result.returncode == 2 and not result.stdout.strip():
        return []
    if result.returncode:
        raise ReleaseCandidateError(
            "cannot read origin's run-gate-v* release tags (git ls-remote exited "
            f"{result.returncode}): {result.stderr.strip()[:300]}; a checkout without "
            "local release tags cannot be told apart from a first release"
        )
    names: set[str] = set()
    for line_number, line in enumerate(result.stdout.splitlines(), start=1):
        match = _LS_REMOTE_TAG_LINE.fullmatch(line)
        if match is None:
            raise ReleaseCandidateError(
                f"origin's run-gate-v* tag listing is malformed at line {line_number}: {line[:200]!r}"
            )
        names.add(match.group(2))
    if not names:
        raise ReleaseCandidateError(
            "git ls-remote reported run-gate-v* tags on origin but listed none"
        )
    return sorted(names)


def _release_baseline() -> tuple[str, str] | None:
    """The newest stable release, checked against origin; ``None`` = first release.

    ``None`` is returned only when origin has been read and advertises no stable
    ``run-gate-vX.Y.Z`` tag. A local listing that is empty or older than origin's
    is a stale checkout, not a first release, and is refused.
    """
    local = _stable_release_tags(_git("tag", "--list", "run-gate-v*").splitlines())
    origin = _stable_release_tags(_origin_release_tags())
    if origin:
        _key, origin_tag, _version = max(origin)
        if not local or max(origin)[0] > max(local)[0]:
            newest_local = max(local)[1] if local else "none"
            raise ReleaseCandidateError(
                f"origin has stable release tag {origin_tag} but this checkout's newest is "
                f"{newest_local}; fetch the release tags (git fetch --tags origin) and retry"
            )
    if not local:
        return None
    _key, tag, version = max(local)
    return tag, version


def _wheel_identity() -> dict[str, str]:
    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "tester-unified" / "run-gate-wheel-version.py"),
            str(PROJECT_ROOT / "dist"),
            "--json",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ReleaseCandidateError(
            f"cannot identify the built Run-Gate wheel: "
            f"{result.stderr.strip()[:300]}"
        )
    try:
        identity = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseCandidateError("Run-Gate wheel identity output is not JSON") from exc
    if not isinstance(identity, dict) or not all(
        isinstance(identity.get(key), str) and identity[key]
        for key in ("version", "filename", "sha256")
    ):
        raise ReleaseCandidateError("Run-Gate wheel identity is incomplete")
    if not re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]):
        raise ReleaseCandidateError("Run-Gate wheel SHA-256 is malformed")
    return identity


def _image_identity(image_tag: str) -> dict[str, str]:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image_tag],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ReleaseCandidateError(
            f"cannot inspect tester image {image_tag!r}: {result.stderr.strip()[:300]}"
        )
    try:
        labels = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseCandidateError(f"tester image {image_tag!r} returned malformed labels") from exc
    if not isinstance(labels, dict):
        raise ReleaseCandidateError(f"tester image {image_tag!r} has no label object")
    identity = {
        "version": labels.get(IMAGE_VERSION_LABEL),
        "sha256": labels.get(IMAGE_SHA256_LABEL),
    }
    if not all(isinstance(value, str) and value for value in identity.values()):
        raise ReleaseCandidateError(
            f"tester image {image_tag!r} has no Run-Gate version and wheel digest labels"
        )
    return identity


def _image_labels(image_tag: str) -> dict[str, object]:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image_tag],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ReleaseCandidateError(
            f"cannot inspect tester image {image_tag!r}: {result.stderr.strip()[:300]}"
        )
    try:
        labels = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseCandidateError(f"tester image {image_tag!r} returned malformed labels") from exc
    return labels if isinstance(labels, dict) else {}


def _image_cli_extended_sha256(image_tag: str) -> str:
    """The cli-extended wheel digest the built tester image says it installed."""
    value = _image_labels(image_tag).get(CLI_EXTENDED_SHA256_LABEL)
    if not isinstance(value, str) or not _SHA256_HEX.match(value):
        raise ReleaseCandidateError(
            f"tester image {image_tag!r} has no valid {CLI_EXTENDED_SHA256_LABEL} label"
        )
    return value


def _image_base_image(image_tag: str) -> str:
    """The base image reference the built tester image says it was built FROM."""
    value = _image_labels(image_tag).get(BASE_IMAGE_LABEL)
    if not isinstance(value, str) or not _IMAGE_REFERENCE.match(value):
        raise ReleaseCandidateError(
            f"tester image {image_tag!r} has no valid {BASE_IMAGE_LABEL} label"
        )
    return value


def _dockerfile_default(pattern: str, what: str) -> str:
    dockerfile = REPOSITORY_ROOT / "tester-unified" / "Dockerfile"
    try:
        text = dockerfile.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseCandidateError(f"cannot read {dockerfile}: {exc}") from exc
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise ReleaseCandidateError(f"{dockerfile} declares no {what}")
    return match.group(1)


def _dockerfile_cli_extended_pin() -> str:
    """The cli-extended wheel digest ``tester-unified/Dockerfile`` pins by default."""
    return _dockerfile_default(
        r"^ARG CLI_EXTENDED_WHEEL_SHA256=([0-9a-f]{64})$", "pinned cli-extended digest",
    )


def _dockerfile_default_base_image() -> str:
    """The base image ``tester-unified/Dockerfile`` builds FROM by default."""
    return _dockerfile_default(
        r"^ARG TESTER_UNIFIED_BASE_IMAGE=([A-Za-z0-9][A-Za-z0-9._/:@-]*)$",
        "default tester-unified base image",
    )


def _expected_base_image(command: list[str]) -> str:
    """The base the built image must label, from the exact builder command."""
    if "--base-image" in command:
        return command[command.index("--base-image") + 1]
    return _dockerfile_default_base_image()


def _require_base_image_label(image_tag: str, expected: str) -> None:
    observed = _image_base_image(image_tag)
    if observed != expected:
        raise ReleaseCandidateError(
            f"tester image {image_tag!r} was built from base {observed!r}, expected {expected!r}"
        )


def _expected_cli_extended_sha256(command: list[str]) -> str:
    """The digest the built image must label, from the exact builder command.

    A digest passed to the builder (source/local and latest) is the expectation;
    the pinned/default modes install the Dockerfile's pinned wheel.
    """
    if "--cli-extended-wheel-sha256" in command:
        return command[command.index("--cli-extended-wheel-sha256") + 1]
    return _dockerfile_cli_extended_pin()


def _require_cli_extended_label(image_tag: str, expected: str) -> None:
    observed = _image_cli_extended_sha256(image_tag)
    if observed != expected:
        raise ReleaseCandidateError(
            f"tester image {image_tag!r} installed cli-extended wheel sha256 {observed}, "
            f"expected {expected}"
        )


def _require_same_wheel(identity: dict[str, str], expected: dict[str, str], where: str) -> None:
    for field in ("version", "sha256"):
        if identity.get(field) != expected.get(field):
            label = "wheel SHA-256" if field == "sha256" else "version"
            raise ReleaseCandidateError(
                f"{where} Run-Gate {label} {identity.get(field)!r} does not match "
                f"the gated wheel {expected.get(field)!r}"
            )


def _read_receipt(path: Path) -> dict[str, str]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateError(f"cannot read Run-Gate wheel receipt {path}: {exc}") from exc
    required = ("candidate_commit", "version", "filename", "sha256", "image_tag", "image_id")
    if (
        not isinstance(record, dict)
        or type(record.get("schema_version")) is not int
        or record.get("schema_version") != 1
        or any(not isinstance(record.get(key), str) or not record[key] for key in required)
    ):
        raise ReleaseCandidateError(f"Run-Gate wheel receipt {path} is malformed")
    if not re.fullmatch(r"[0-9a-f]{40,64}", record["candidate_commit"]):
        raise ReleaseCandidateError(f"Run-Gate wheel receipt {path} has a malformed commit")
    if not STABLE_SEMVER.fullmatch(record["version"]):
        raise ReleaseCandidateError(f"Run-Gate wheel receipt {path} has a malformed stable version")
    if Path(record["filename"]).name != record["filename"] or "\\" in record["filename"]:
        raise ReleaseCandidateError(f"Run-Gate wheel receipt {path} has a malformed filename")
    if not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
        raise ReleaseCandidateError(f"Run-Gate wheel receipt {path} has a malformed SHA-256")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", record["image_id"]):
        raise ReleaseCandidateError(f"Run-Gate wheel receipt {path} has a malformed image ID")
    return record


def _write_receipt(record: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _build_cli_extended_source_wheel() -> tuple[Path, str]:
    """Build the declared CMRU runtime dependency from this checkout's source.

    This explicit bootstrap path lets the first Run-Gate release build its
    tester image before cli-extended has a published wheel. It does not change
    the normal pinned/latest image modes.
    """
    cmru_pyproject = REPOSITORY_ROOT / "cmru" / "pyproject.toml"
    try:
        document = tomllib.loads(cmru_pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseCandidateError(
            f"cannot read CMRU runtime requirements from {cmru_pyproject}: {exc}"
        ) from exc
    requirement = re.compile(r"cli[-_.]extended\s*>=\s*([0-9]+(?:\.[0-9]+)*)\Z", re.I)
    project = document.get("project")
    dependencies = project.get("dependencies") if isinstance(project, dict) else None
    if not isinstance(dependencies, list):
        raise ReleaseCandidateError(
            f"{cmru_pyproject} has no project.dependencies list"
        )
    floor = next((
        match.group(1)
        for item in dependencies
        if isinstance(item, str) and (match := requirement.fullmatch(item.strip()))
    ), None)
    if floor is None:
        raise ReleaseCandidateError(
            f"{cmru_pyproject} declares no cli-extended>=<floor> runtime dependency"
        )

    version = f"{floor}+bootstrap.source"
    source = REPOSITORY_ROOT / "libraries" / "cli-extended"
    dist = source / "dist"
    epoch = _git("log", "-1", "--format=%ct")
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = epoch
    env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED"] = version
    result = subprocess.run(
        ["cmru", "handler", "wheel-build", "--cwd", str(source)],
        cwd=REPOSITORY_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ReleaseCandidateError(
            "cannot build cli-extended from the selected checkout for the "
            f"first-release tester image: {result.stderr.strip()[:500]}"
        )
    expected = sorted(dist.glob(f"cli_extended-{version}-*.whl"))
    if len(expected) != 1:
        raise ReleaseCandidateError(
            f"expected one cli-extended {version} source wheel under {dist}; found {len(expected)}"
        )
    wheel = expected[0]
    try:
        payload = wheel.read_bytes()
        with ZipFile(wheel) as archive:
            metadata_files = [
                name for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_files) != 1:
                raise ReleaseCandidateError(
                    f"cli-extended source wheel {wheel.name} has {len(metadata_files)} METADATA files"
                )
            metadata = archive.read(metadata_files[0]).decode("utf-8")
    except (OSError, UnicodeError, ValueError, BadZipFile, KeyError) as exc:
        raise ReleaseCandidateError(
            f"cannot verify cli-extended source wheel {wheel}: {exc}"
        ) from exc
    if not re.search(rf"(?m)^Name: cli[-_.]extended\s*$", metadata, re.I):
        raise ReleaseCandidateError(f"{wheel.name} is not a cli-extended wheel")
    observed_version = re.search(r"(?m)^Version: ([^\r\n]+)$", metadata)
    if observed_version is None or observed_version.group(1) != version:
        raise ReleaseCandidateError(
            f"cli-extended source wheel version is "
            f"{observed_version.group(1) if observed_version else '(missing)'!r}, "
            f"expected {version!r}"
        )

    digest = hashlib.sha256(payload).hexdigest()
    local_wheels = REPOSITORY_ROOT / "tester-unified" / "local-wheel"
    local_wheels.mkdir(parents=True, exist_ok=True)
    target = local_wheels / wheel.name
    unrelated = [
        path for path in local_wheels.glob("cli_extended-*.whl")
        if path != target
    ]
    if unrelated:
        raise ReleaseCandidateError(
            "tester-unified/local-wheel contains another cli-extended wheel; "
            "remove the stale bootstrap input before retrying: "
            + ", ".join(path.name for path in unrelated)
        )
    # Write the bytes that were hashed, not a second read of the build output.
    target.write_bytes(payload)
    return target, digest


def _tester_image_options() -> tuple[str, str, str]:
    """Read and validate the tester-image overrides: ``(base, mode, published)``.

    :func:`prepare` calls this before any build, so a malformed override
    refuses before Docker is used. An empty value means "not set".
    """
    base_image = os.environ.get(BASE_IMAGE_ENV, "").strip()
    if base_image and not _IMAGE_REFERENCE.match(base_image):
        raise ReleaseCandidateError(
            f"{BASE_IMAGE_ENV} must be one image reference, not {base_image!r}"
        )
    mode = os.environ.get(CLI_EXTENDED_RESOLVE_ENV, "").strip()
    if mode not in ("", "source", "pinned", "latest"):
        raise ReleaseCandidateError(
            f"{CLI_EXTENDED_RESOLVE_ENV} must be source, pinned or latest"
        )
    published = os.environ.get(CLI_EXTENDED_SHA256_ENV, "").strip()
    if mode == "latest":
        # `latest` follows a mutable pointer, so it is only accepted together with
        # the digest of the wheel that was actually published; the image build
        # refuses any other bytes and labels the digest it verified.
        if not _SHA256_HEX.match(published):
            raise ReleaseCandidateError(
                f"{CLI_EXTENDED_RESOLVE_ENV}=latest requires {CLI_EXTENDED_SHA256_ENV} "
                "to be the published cli-extended wheel's 64-hex SHA-256 (the "
                "CLI_EXTENDED_RELEASED_SHA256 the release-identity check verified)"
            )
    elif published:
        # The builder installs the Dockerfile pin (pinned/default) or the digest of
        # the wheel built here (source); a supplied digest would be silently unused.
        raise ReleaseCandidateError(
            f"{CLI_EXTENDED_SHA256_ENV} is valid only with {CLI_EXTENDED_RESOLVE_ENV}=latest"
        )
    return base_image, mode, published


def _tester_image_build_command(
    image_tag: str, options: tuple[str, str, str] | None = None,
) -> list[str]:
    base_image, cli_extended_mode, published = (
        _tester_image_options() if options is None else options
    )
    command = [
        str(REPOSITORY_ROOT / "tester-unified" / "build-image.sh"),
        "--wheel-dir", str(PROJECT_ROOT / "dist"),
        "--tag", image_tag,
    ]
    if base_image:
        command.extend(["--base-image", base_image])
    if cli_extended_mode == "source":
        _wheel_path, cli_extended_sha256 = _build_cli_extended_source_wheel()
        command.extend([
            "--cli-extended-resolve", "local",
            "--cli-extended-wheel-sha256", cli_extended_sha256,
        ])
    elif cli_extended_mode == "latest":
        command.extend([
            "--cli-extended-resolve", "latest", "--cli-extended-wheel-sha256", published,
        ])
    elif cli_extended_mode == "pinned":
        command.extend(["--cli-extended-resolve", "pinned"])
    return command


def _candidate_wheel_path(receipt: dict[str, str]) -> Path:
    """Resolve and verify the exact wheel named by a prepared receipt."""
    try:
        wheel_dir = (PROJECT_ROOT / "dist").resolve(strict=True)
        wheel = wheel_dir / receipt["filename"]
        resolved = wheel.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ReleaseCandidateError(
            f"cannot read receipted Run-Gate wheel under {PROJECT_ROOT / 'dist'}: {exc}"
        ) from exc
    if not resolved.is_relative_to(wheel_dir) or not resolved.is_file():
        raise ReleaseCandidateError(
            f"receipted Run-Gate wheel is not a regular file under {wheel_dir}"
        )
    try:
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        raise ReleaseCandidateError(
            f"cannot read receipted Run-Gate wheel under {wheel_dir}: {exc}"
        ) from exc
    if digest != receipt["sha256"]:
        raise ReleaseCandidateError(
            f"receipted Run-Gate wheel SHA-256 changed: expected {receipt['sha256']}, "
            f"observed {digest}"
        )
    return resolved


def _candidate_cli(wheel: Path, temp_root: Path) -> Path:
    """Install the candidate wheel offline and return its console script."""
    environment = temp_root / "candidate-venv"
    create = subprocess.run(
        [sys.executable, "-m", "venv", str(environment)],
        cwd=temp_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if create.returncode:
        raise ReleaseCandidateError(
            "cannot create an isolated Run-Gate candidate environment: "
            f"{create.stderr.strip()[:500]}"
        )
    python = environment / "bin" / "python"
    install = subprocess.run(
        [
            str(python), "-m", "pip", "install", "--no-deps", "--no-index",
            "--disable-pip-version-check", str(wheel),
        ],
        cwd=temp_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if install.returncode:
        raise ReleaseCandidateError(
            "cannot install the receipted Run-Gate wheel into its isolated "
            f"candidate environment: {install.stderr.strip()[:500]}"
        )
    executable = environment / "bin" / "run-gate"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ReleaseCandidateError(
            f"the receipted Run-Gate wheel did not install its console script at {executable}"
        )
    return executable


def _verify_receipt_image(receipt: dict[str, str]) -> None:
    """Refuse if the candidate tag no longer names the receipted image."""
    _require_same_wheel(_image_identity(receipt["image_tag"]), receipt, "tester image")
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", receipt["image_tag"]],
        check=False,
        capture_output=True,
        text=True,
    )
    observed = result.stdout.strip()
    if result.returncode or observed != receipt["image_id"]:
        detail = result.stderr.strip() if result.returncode else observed
        raise ReleaseCandidateError(
            f"tester image {receipt['image_tag']!r} changed after preparation: "
            f"expected {receipt['image_id']}, observed {detail}"
        )


def prepare(image_tag: str | None = None) -> dict[str, str]:
    commit = _git("rev-parse", "HEAD")
    gate_commit = os.environ.get("CMRU_RELEASE_GATE_COMMIT")
    if not isinstance(gate_commit, str) or not gate_commit:
        raise ReleaseCandidateError(
            "stable Run-Gate preparation requires CMRU's protected release-gate commit"
        )
    if gate_commit != commit:
        raise ReleaseCandidateError(
            "CMRU release-gate commit does not match the selected Run-Gate checkout: "
            f"expected {gate_commit}, observed {commit}"
        )
    version = prepared_release_version(
        PROJECT_ROOT / "CHANGES.md", allow_first_release=True,
    )
    image_tag = image_tag or f"tester-unified:run-gate-candidate-{commit[:12]}"
    image_options = _tester_image_options()
    epoch = _git("log", "-1", "--format=%ct")
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = epoch
    env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE"] = version
    subprocess.run(
        ["cmru", "handler", "wheel-build", "--cwd", "."],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )
    wheel = _wheel_identity()
    if wheel["version"] != version:
        raise ReleaseCandidateError(
            f"built Run-Gate wheel {wheel['version']} does not match prepared release {version}"
        )
    build_command = _tester_image_build_command(image_tag, image_options)
    subprocess.run(
        build_command,
        cwd=REPOSITORY_ROOT,
        env=env,
        check=True,
    )
    _require_same_wheel(_image_identity(image_tag), wheel, "tester image")
    # The image must name the exact cli-extended bytes and the base this build
    # was bound to before the gate may use it.
    _require_cli_extended_label(image_tag, _expected_cli_extended_sha256(build_command))
    _require_base_image_label(image_tag, _expected_base_image(build_command))
    image_result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image_tag],
        check=False,
        capture_output=True,
        text=True,
    )
    if image_result.returncode:
        raise ReleaseCandidateError(
            f"cannot read tester image ID {image_tag!r}: {image_result.stderr.strip()[:300]}"
        )
    record = {
        "schema_version": 1,
        "candidate_commit": commit,
        "version": version,
        "filename": wheel["filename"],
        "sha256": wheel["sha256"],
        "image_tag": image_tag,
        "image_id": image_result.stdout.strip(),
    }
    if not record["image_id"].startswith("sha256:"):
        raise ReleaseCandidateError(f"tester image ID is malformed: {record['image_id']!r}")
    _write_receipt(record, RECEIPT_PATH)
    print(
        f"prepared Run-Gate {version} wheel {wheel['sha256']} in image "
        f"{image_tag} ({record['image_id']})"
    )
    return record


def verify_built(image_tag: str | None = None, receipt_path: Path = RECEIPT_PATH) -> None:
    receipt = _read_receipt(receipt_path)
    commit = _git("rev-parse", "HEAD")
    if receipt["candidate_commit"] != commit:
        raise ReleaseCandidateError(
            f"wheel receipt is for {receipt['candidate_commit']}, current candidate is {commit}"
        )
    wheel = _wheel_identity()
    _require_same_wheel(wheel, receipt, "rebuilt wheel")
    if wheel["filename"] != receipt["filename"]:
        raise ReleaseCandidateError(
            f"rebuilt wheel filename {wheel['filename']!r} does not match gated wheel "
            f"{receipt['filename']!r}"
        )
    selected_tag = image_tag or receipt["image_tag"]
    _require_same_wheel(_image_identity(selected_tag), receipt, "loaded tester image")
    image_result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", selected_tag],
        check=False,
        capture_output=True,
        text=True,
    )
    if image_result.returncode or image_result.stdout.strip() != receipt["image_id"]:
        detail = image_result.stderr.strip() if image_result.returncode else image_result.stdout.strip()
        raise ReleaseCandidateError(
            f"loaded tester image {selected_tag!r} changed after its gate: "
            f"expected {receipt['image_id']}, observed {detail}"
        )
    print(f"rebuilt Run-Gate wheel matches the gated wheel {receipt['sha256']}")


def verify_running_image(receipt_path: Path) -> None:
    if not receipt_path.is_absolute():
        receipt_path = Path.cwd() / receipt_path
    receipt = _read_receipt(receipt_path)
    commit = _git("rev-parse", "HEAD")
    if receipt["candidate_commit"] != commit:
        raise ReleaseCandidateError(
            f"wheel receipt is for {receipt['candidate_commit']}, running checkout is {commit}"
        )
    try:
        identity = json.loads(IMAGE_IDENTITY_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateError(
            f"tester image has no readable Run-Gate wheel identity: {exc}"
        ) from exc
    if not isinstance(identity, dict):
        raise ReleaseCandidateError("tester image Run-Gate wheel identity is malformed")
    _require_same_wheel(identity, receipt, "running tester image")
    _verify_installed_candidate_wheel(receipt)
    print(f"running tester image contains the gated Run-Gate wheel {receipt['sha256']}")


def _verify_installed_candidate_wheel(receipt: dict[str, str]) -> None:
    """Prove the installed wheel code and CLI are the candidate under test."""
    try:
        version = importlib.metadata.version("run-gate")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ReleaseCandidateError("tester image has no installed Run-Gate distribution") from exc
    if version != receipt["version"]:
        raise ReleaseCandidateError(
            f"installed Run-Gate distribution version {version!r} does not match "
            f"candidate {receipt['version']!r}"
        )
    venv_root = Path(sys.prefix).resolve()
    for module_name in ("run_gate", "run_gate_admission"):
        try:
            module = importlib.import_module(module_name)
            installed_path = Path(module.__file__).resolve(strict=True)
        except (ImportError, OSError, TypeError) as exc:
            raise ReleaseCandidateError(
                f"cannot import installed Run-Gate module {module_name}: {exc}"
            ) from exc
        if not installed_path.is_relative_to(venv_root):
            raise ReleaseCandidateError(
                f"installed Run-Gate module {module_name} resolved outside tester venv: "
                f"{installed_path}"
            )
        source_path = PROJECT_ROOT / f"{module_name}.py"
        try:
            source_bytes = source_path.read_bytes()
            installed_bytes = installed_path.read_bytes()
        except OSError as exc:
            raise ReleaseCandidateError(
                f"cannot compare installed Run-Gate module {module_name}: {exc}"
            ) from exc
        if installed_bytes != source_bytes:
            raise ReleaseCandidateError(
                f"installed Run-Gate module {module_name} differs from the selected "
                "release checkout"
            )
    executable = venv_root / "bin" / "run-gate"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ReleaseCandidateError(f"tester image has no executable installed CLI at {executable}")
    result = subprocess.run(
        [str(executable), "--version"],
        cwd=Path(tempfile.gettempdir()),
        check=False,
        capture_output=True,
        text=True,
    )
    expected = f"run-gate {receipt['version']}"
    if result.returncode or result.stdout.strip() != expected:
        raise ReleaseCandidateError(
            f"installed Run-Gate CLI smoke expected {expected!r}, observed "
            f"{result.stdout.strip()!r} (exit {result.returncode}): "
            f"{result.stderr.strip()[:300]}"
        )


def run_selftest(receipt_path: Path = RECEIPT_PATH) -> int:
    """Run selftest from modules owned by the installed receipted wheel."""
    if not receipt_path.is_absolute():
        receipt_path = PROJECT_ROOT / receipt_path
    receipt = _read_receipt(receipt_path)
    commit = _git("rev-parse", "HEAD")
    if receipt["candidate_commit"] != commit:
        raise ReleaseCandidateError(
            f"wheel receipt is for {receipt['candidate_commit']}, current candidate is {commit}"
        )
    wheel = _candidate_wheel_path(receipt)
    _verify_receipt_image(receipt)
    try:
        container_receipt = receipt_path.resolve().relative_to(
            PROJECT_ROOT.resolve()
        ).as_posix()
    except ValueError as exc:
        raise ReleaseCandidateError(
            "Run-Gate selftest receipt must be inside the selected project checkout"
        ) from exc
    env = os.environ.copy()
    env["RUN_GATE_RELEASE_WHEEL_RECEIPT"] = container_receipt
    env["RUN_GATE_TESTER_UNIFIED_IMAGE"] = receipt["image_id"]
    with tempfile.TemporaryDirectory(prefix="run-gate-candidate-driver-") as directory:
        executable = _candidate_cli(wheel, Path(directory))
        python = executable.parent / "python"
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ReleaseCandidateError(
                f"the candidate environment has no executable Python at {python}"
            )
        try:
            result = subprocess.run(
                [str(python), "-I", "-c", _CANDIDATE_SELFTEST_CODE],
                cwd=PROJECT_ROOT, env=env, check=False,
            )
        except OSError as exc:
            raise ReleaseCandidateError(
                f"cannot start the receipted Run-Gate selftest: {exc}"
            ) from exc
        return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", help="build and receipt the candidate wheel/image")
    prepare_parser.add_argument("--image")
    verify_parser = commands.add_parser("verify", help="compare release build bytes with the gated wheel")
    verify_parser.add_argument("--image")
    verify_parser.add_argument("--receipt", type=Path, default=RECEIPT_PATH)
    running_parser = commands.add_parser("verify-running", help="verify this gate container's baked wheel")
    running_parser.add_argument("--receipt", type=Path, required=True)
    selftest_parser = commands.add_parser("run-selftest", help="run selftest with its receipt-specific image")
    selftest_parser.add_argument("--receipt", type=Path, default=RECEIPT_PATH)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare(args.image)
        elif args.command == "verify":
            verify_built(args.image, args.receipt)
        elif args.command == "run-selftest":
            return run_selftest(args.receipt)
        else:
            verify_running_image(args.receipt)
    except (ReleaseCandidateError, OSError, subprocess.SubprocessError) as exc:
        print(f"run-gate release candidate: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
