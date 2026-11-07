#!/usr/bin/env python3
"""Resolve a fresh, platform-independent runtime lock for the topos daemon."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import Callable, Sequence


NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)")


class LockBuildError(ValueError):
    """The wheel metadata or resolved dependency set cannot form a safe lock."""


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _wheel_tags(path: Path) -> tuple[str, str, str, str, str]:
    if not path.name.endswith(".whl"):
        raise LockBuildError(f"not a wheel: {path.name}")
    parts = path.name[:-4].split("-")
    if len(parts) < 5:
        raise LockBuildError(f"invalid wheel filename: {path.name}")
    tags = tuple(parts[-3:])
    if tags != ("py3", "none", "any"):
        raise LockBuildError(f"wheel {path.name} is not py3-none-any")
    identity = parts[:-3]
    if len(identity) not in (2, 3):
        raise LockBuildError(f"invalid wheel filename: {path.name}")
    return identity[0], identity[1], *tags


def _wheel_metadata(path: Path) -> tuple[str, str, list[str]]:
    filename_name, filename_version, _py, _abi, _platform = _wheel_tags(path)
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_paths = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata_paths) != 1:
                raise LockBuildError(f"wheel {path.name} must contain exactly one .dist-info/METADATA")
            metadata = Parser().parsestr(archive.read(metadata_paths[0]).decode("utf-8"))
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise LockBuildError(f"cannot read wheel {path.name}: {exc}") from None
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version:
        raise LockBuildError(f"wheel {path.name} metadata must declare Name and Version")
    if _canonical_name(filename_name) != _canonical_name(name):
        raise LockBuildError(f"wheel {path.name} filename package does not match metadata Name {name!r}")
    if filename_version != version:
        raise LockBuildError(
            f"wheel {path.name} filename version {filename_version!r} does not match metadata Version {version!r}"
        )
    return name, version, list(metadata.get_all("Requires-Dist", []))


def _dependency_requirements(metadata_values: Sequence[str]) -> list[str]:
    requirements: list[str] = []
    for value in metadata_values:
        match = NAME_RE.match(value)
        if match is None:
            raise LockBuildError(f"cannot read dependency name from METADATA requirement {value!r}")
        if _canonical_name(match.group(1)) != "cli-extended":
            requirements.append(value)
    return requirements


def pip_download(
    requirements: Sequence[str],
    destination: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    python: str = sys.executable,
) -> None:
    """Run pip's aged, wheel-only resolver for the METADATA dependencies."""
    argv = [
        python,
        "-m",
        "pip",
        "download",
        "--only-binary",
        ":all:",
        "--dest",
        str(destination),
        "--uploaded-prior-to",
        "P14D",
        *requirements,
    ]
    try:
        result = run(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise LockBuildError(f"could not run pip download: {exc}") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise LockBuildError(f"pip download failed with exit {result.returncode}: {detail}")


def build_daemon_lock(
    dist: Path,
    *,
    downloader: Callable[[Sequence[str], Path], None] | None = None,
) -> Path:
    """Write and return ``dist/topos-<wheel-version>-daemon.lock``."""
    dist = Path(dist)
    topos_wheels = sorted(dist.glob("topos-*.whl"))
    if len(topos_wheels) != 1:
        raise LockBuildError(f"expected exactly one built topos wheel in {dist}, found {len(topos_wheels)}")
    topos_wheel = topos_wheels[0]
    name, version, metadata_requirements = _wheel_metadata(topos_wheel)
    if _canonical_name(name) != "topos":
        raise LockBuildError(f"built wheel {topos_wheel.name} is named {name!r}, expected 'topos'")
    requirements = _dependency_requirements(metadata_requirements)
    with tempfile.TemporaryDirectory(prefix="topos-daemon-lock-") as temporary:
        destination = Path(temporary)
        if requirements:
            if downloader is None:
                pip_download(requirements, destination)
            else:
                downloader(requirements, destination)
        rows: dict[str, tuple[str, str, str]] = {}
        for wheel in sorted(destination.glob("*.whl")):
            package, package_version, _ = _wheel_metadata(wheel)
            canonical = _canonical_name(package)
            if canonical in {"topos", "cli-extended"}:
                continue
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            prior = rows.get(canonical)
            row = (package.lower().replace("_", "-"), package_version, digest)
            if prior is not None and prior != row:
                raise LockBuildError(f"multiple resolved versions of {package} were downloaded")
            rows[canonical] = row
        if requirements and not rows:
            raise LockBuildError("pip downloaded no third-party wheels for the daemon runtime")
    lock = dist / f"topos-{version}-daemon.lock"
    contents = "".join(
        f"{package}=={package_version} --hash=sha256:{digest}\n"
        for _canonical, (package, package_version, digest) in sorted(rows.items())
    )
    lock.write_text(contents, encoding="utf-8")
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    lock.with_name(lock.name + ".sha256").write_text(f"{digest}  {lock.name}\n", encoding="utf-8")
    return lock


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path(__file__).resolve().parents[1] / "dist")
    args = parser.parse_args(argv)
    try:
        lock = build_daemon_lock(args.dist)
    except LockBuildError as exc:
        parser.error(str(exc))
    print(f"wrote {lock}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
