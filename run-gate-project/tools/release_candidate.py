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
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
RECEIPT_PATH = PROJECT_ROOT / ".assay" / "run-gate-release-wheel.json"
IMAGE_IDENTITY_PATH = Path("/etc/tester-unified-run-gate-wheel.json")
IMAGE_VERSION_LABEL = "org.vbpub.run-gate.version"
IMAGE_SHA256_LABEL = "org.vbpub.run-gate.wheel-sha256"
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


def prepared_release_version(changes_path: Path) -> str:
    """Read the dated version CMRU prepared for this candidate."""
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


def _latest_release_tag() -> tuple[str, str]:
    tags = _git("tag", "--list", "run-gate-v*").splitlines()
    stable: list[tuple[tuple[int, int, int], str, str]] = []
    for tag in tags:
        match = re.fullmatch(r"run-gate-v([0-9]+\.[0-9]+\.[0-9]+)", tag)
        if match and STABLE_SEMVER.fullmatch(match.group(1)):
            version = match.group(1)
            stable.append((_semver_key(version), tag, version))
    if not stable:
        raise ReleaseCandidateError(
            "cannot prove the prepared Run-Gate version is new: no stable "
            "run-gate-vX.Y.Z release tag is available in this checkout"
        )
    _key, tag, version = max(stable)
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
    version = prepared_release_version(PROJECT_ROOT / "CHANGES.md")
    image_tag = image_tag or f"tester-unified:run-gate-candidate-{commit[:12]}"
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
    subprocess.run(
        [
            str(REPOSITORY_ROOT / "tester-unified" / "build-image.sh"),
            "--wheel-dir", str(PROJECT_ROOT / "dist"),
            "--tag", image_tag,
        ],
        cwd=REPOSITORY_ROOT,
        env=env,
        check=True,
    )
    _require_same_wheel(_image_identity(image_tag), wheel, "tester image")
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
