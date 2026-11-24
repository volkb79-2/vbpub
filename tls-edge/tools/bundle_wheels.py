#!/usr/bin/env python3
"""Bundle requirements generation and the single first-party wheel fetcher.

``requirements`` derives the third-party declarations from ciu, cli-extended and
tls-edge-cli, resolves that closure under the estate's upload-age buffer, and
writes one exact-pinned requirements file. ``stage`` builds the tls-edge-cli
wheel and fetches the two GitHub-only first-party wheels through one verified path.
``version-env`` supplies the release version from the committed ``VERSION`` file,
and ``retain`` copies the publisher's version and installer inputs into ``dist/``.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from collections.abc import Mapping
from pathlib import Path

TLS_EDGE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TLS_EDGE_DIR.parent
REQUIREMENTS_OUT = TLS_EDGE_DIR / "requirements-bundle.txt"
CIU_PROJECT = REPO_ROOT / "ciu" / "pyproject.toml"
CLI_EXTENDED_PROJECT = REPO_ROOT / "libraries" / "cli-extended" / "pyproject.toml"
CLI_PROJECT = TLS_EDGE_DIR / "pyproject.toml"
CLI_RUNTIME = TLS_EDGE_DIR / "requirements-runtime.txt"
CLI_CONFIG = TLS_EDGE_DIR / "cmru.toml"
FIRST_PARTY = frozenset({"ciu", "cli-extended", "tls-edge-cli"})
MIN_AGE_DAYS = 14
PLATFORMS = (
    "manylinux2014_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux_2_28_x86_64",
)
_REQUIREMENT_RE = re.compile(r"([A-Za-z0-9_.-]+)(.*)")
_LOWER_BOUND_RE = re.compile(r">=[0-9]+(?:\.[0-9]+)*")
_VERSION_RE = re.compile(r"(?:[0-9]+\.)+[0-9]+")
_WHEEL_RE = re.compile(
    r"([A-Za-z0-9_.-]+)-([0-9][0-9A-Za-z.+!]*)-[^/]+\.whl",
)


def _fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def normal_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _fail(f"cannot read {path}: {exc}")


def _read_requirements(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(f"cannot read {path}: {exc}")
    return [
        line.partition("#")[0].strip()
        for line in text.splitlines()
        if line.partition("#")[0].strip()
    ]


def dependency_sources() -> list[str]:
    """All direct dependency declarations for the three shipped first-party wheels."""
    ciu = _read_toml(CIU_PROJECT)
    cli_extended = _read_toml(CLI_EXTENDED_PROJECT)
    edge = _read_toml(CLI_PROJECT)
    try:
        values = [
            ciu["project"]["dependencies"],
            cli_extended["project"]["dependencies"],
            edge["project"]["optional-dependencies"]["interactive"],
            _read_requirements(CLI_RUNTIME),
        ]
    except (KeyError, TypeError) as exc:
        _fail(f"dependency declaration has an unexpected shape: {exc}")
    result: list[str] = []
    for group in values:
        if not isinstance(group, list) or any(not isinstance(item, str) for item in group):
            _fail("dependency declarations must be arrays of strings")
        result.extend(group)
    return result


def _parse_requirement(raw: str) -> tuple[str, str, str]:
    match = _REQUIREMENT_RE.fullmatch(raw.strip())
    if not match:
        _fail(f"unsupported requirement {raw!r}")
    name, spec = match.groups()
    spec = spec.strip()
    if spec and not _LOWER_BOUND_RE.fullmatch(spec):
        _fail(
            f"requirement {raw!r} uses a constraint form this generator does not merge; "
            "use a single >= floor"
        )
    return name, normal_name(name), spec


def merge_requirements(raw_requirements: list[str]) -> list[str]:
    """Deduplicate by canonical name, retaining the strongest explicit lower bound."""
    merged: dict[str, tuple[str, str]] = {}
    for raw in raw_requirements:
        name, canonical, spec = _parse_requirement(raw)
        if canonical in FIRST_PARTY:
            continue
        existing = merged.get(canonical)
        if existing is None:
            merged[canonical] = (name, spec)
            continue
        old_name, old_spec = existing
        if not spec:
            continue
        if not old_spec:
            merged[canonical] = (old_name, spec)
            continue
        old_version = tuple(int(part) for part in old_spec[2:].split("."))
        new_version = tuple(int(part) for part in spec[2:].split("."))
        if new_version > old_version:
            merged[canonical] = (old_name, spec)
    return [f"{name}{spec}" for canonical, (name, spec) in sorted(merged.items())]


def render_requirements(resolved: Mapping[str, tuple[str, str]]) -> str:
    """Render the exact-pinned third-party closure in stable name order."""
    missing = sorted(
        canonical
        for canonical in {
            _parse_requirement(raw)[1] for raw in merge_requirements(dependency_sources())
        }
        if canonical not in resolved
    )
    if missing:
        _fail("resolved requirements omit direct dependencies: " + ", ".join(missing))
    first_party = sorted(name for name in resolved if name in FIRST_PARTY)
    if first_party:
        _fail("resolved requirements contain first-party wheels: " + ", ".join(first_party))
    lines = [
        "# Generated by tools/bundle_wheels.py requirements. Do not edit by hand.",
        "# Sources: ciu/pyproject.toml, libraries/cli-extended/pyproject.toml,",
        "# tls-edge/pyproject.toml and tls-edge/requirements-runtime.txt.",
        f"# Resolved for CPython 3.13 x86_64 glibc with a {MIN_AGE_DAYS}-day upload buffer.",
        *[
            f"{resolved[canonical][0]}=={resolved[canonical][1]}"
            for canonical in sorted(resolved)
        ],
        "",
    ]
    return "\n".join(lines)


def _wheel_pin(path: Path) -> tuple[str, str, str]:
    """Return (normalised name, spelling, version) from one downloaded wheel."""
    match = _WHEEL_RE.fullmatch(path.name)
    if not match:
        _fail(f"pip download produced an unsupported wheel name: {path.name!r}")
    spelling, version = match.groups()
    if not _VERSION_RE.fullmatch(version):
        _fail(f"pip download produced a non-numeric wheel version: {path.name!r}")
    return normal_name(spelling), spelling, version


def wheel_requirements(paths: list[Path]) -> dict[str, tuple[str, str]]:
    """Convert a resolved wheel download into canonical-name exact pins."""
    result: dict[str, tuple[str, str]] = {}
    for path in sorted(paths):
        canonical, spelling, version = _wheel_pin(path)
        if canonical in result:
            _fail(f"pip download produced duplicate distributions for {canonical}")
        result[canonical] = (spelling, version)
    return result


def resolve_requirements() -> dict[str, tuple[str, str]]:
    """Resolve the third-party closure once under the estate's upload-age buffer."""
    requirements = merge_requirements(dependency_sources())
    with tempfile.TemporaryDirectory(prefix="tls-edge-requirements-") as temp:
        destination = Path(temp)
        command = [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "--index-url",
            "https://pypi.org/simple",
            "--python-version",
            "3.13",
            "--implementation",
            "cp",
            "--abi",
            "cp313",
            "--uploaded-prior-to",
            f"P{MIN_AGE_DAYS}D",
            "--dest",
            str(destination),
            *[f"--platform={platform}" for platform in PLATFORMS],
            *requirements,
        ]
        result = subprocess.run(command, cwd=TLS_EDGE_DIR, check=False)
        if result.returncode:
            _fail(f"resolving the bundle dependency closure failed with exit {result.returncode}")
        wheels = sorted(destination.glob("*.whl"))
        if not wheels:
            _fail("pip download resolved no wheels for the bundle dependency closure")
        return wheel_requirements(wheels)


def render_current_requirements() -> str:
    """Resolve once and render the project's generated requirements lock."""
    return render_requirements(resolve_requirements())


def version_env() -> str:
    """The cmru build step's dynamic environment: the committed VERSION as KEY=VALUE."""
    try:
        version = (TLS_EDGE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        _fail(f"cannot read {TLS_EDGE_DIR / 'VERSION'}: {exc}")
    if not version or any(char.isspace() for char in version):
        _fail(f"VERSION must contain one whitespace-free release version, got {version!r}")
    return f"TLS_EDGE_VERSION={version}"


def write_requirements(
    output: Path = REQUIREMENTS_OUT,
    *,
    check: bool = False,
    resolved: Mapping[str, tuple[str, str]] | None = None,
) -> None:
    rendered = render_requirements(resolved if resolved is not None else resolve_requirements())
    try:
        current = output.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None
    if check:
        if current != rendered:
            _fail(f"{output} is stale; run tools/bundle_wheels.py requirements")
        print(f"[INFO] {output} is current")
        return
    output.write_text(rendered, encoding="utf-8")
    print(f"[INFO] wrote {output}")


def _version_tuple(value: str) -> tuple[int, ...]:
    raw = value
    value = re.sub(r"^[A-Za-z0-9_.-]+-v", "", value, count=1)
    value = value.removeprefix("v")
    if not _VERSION_RE.fullmatch(value):
        _fail(f"release version is not numeric: {raw!r}")
    return tuple(int(part) for part in value.split("."))


def _minimum_requirement(distribution: str) -> tuple[int, ...] | None:
    for raw in merge_requirements(dependency_sources()):
        name, _, spec = _parse_requirement(raw)
        if normal_name(name) == normal_name(distribution) and spec:
            return tuple(int(part) for part in spec[2:].split("."))
    return None


def _github_settings() -> tuple[str, str]:
    try:
        with (REPO_ROOT / "cmru.orchestration.toml").open("rb") as stream:
            github = tomllib.load(stream)["github"]
        return str(github["owner"]), str(github["repo"])
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        _fail(f"cannot read GitHub release coordinates: {exc}")


def fetch_github_wheel(
    dest: Path,
    *,
    distribution: str,
    tag_prefix: str,
    pinned: str | None = None,
) -> Path:
    """Fetch one stable GitHub release wheel through the one generic verified path."""
    try:
        from cmru.hosts.github import GitHubReleaseHost
    except ImportError as exc:
        _fail(f"the installed cmru GitHub release adapter is unavailable: {exc}")
    owner, repo = _github_settings()
    release = GitHubReleaseHost(
        owner, repo, os.environ.get("GITHUB_TOKEN", "")
    ).resolve_latest(tag_prefix, asset_suffix=".whl")
    if not release:
        _fail(f"no stable {distribution} wheel release is published")
    version = str(release["version"])
    if pinned and _version_tuple(version) != _version_tuple(pinned):
        _fail(f"latest {distribution} release is {version}, but {pinned} is pinned")
    minimum = _minimum_requirement(distribution)
    if minimum is not None and _version_tuple(version) < minimum:
        _fail(f"{distribution} {version} is below the declared >= floor")
    name = str(release["asset"])
    expected = normal_name(distribution).replace("-", "_")
    if (
        not name.endswith(".whl")
        or "/" in name
        or "\\" in name
        or not name.lower().startswith(expected + "-")
    ):
        _fail(f"CMRU resolved an unexpected {distribution} wheel: {name!r}")
    url = str(release["url"])
    if not url.startswith("https://github.com/"):
        _fail(f"CMRU resolved a non-GitHub HTTPS wheel URL: {url!r}")
    request = urllib.request.Request(url, headers={"User-Agent": "tls-edge-bundle-wheels"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            content = response.read()
    except OSError as exc:
        _fail(f"cannot download {name}: {exc}")
    digest = hashlib.sha256(content).hexdigest()
    if digest != str(release["sha256"]).lower():
        _fail(f"SHA-256 mismatch for {name}")
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / name
    path.write_bytes(content)
    print(f"[INFO] staged {name} ({distribution} {version}, sha256 {digest})")
    return path


def _build_tls_edge_cli(dest: Path) -> Path:
    """Build one tls-edge-cli wheel from this checkout."""
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(dest),
            str(TLS_EDGE_DIR),
        ],
        cwd=TLS_EDGE_DIR,
        check=False,
    )
    if result.returncode:
        _fail(f"building tls-edge-cli wheel failed with exit {result.returncode}")
    wheels = sorted(dest.glob("tls_edge_cli-*.whl"))
    if len(wheels) != 1:
        _fail(f"expected one tls-edge-cli wheel, found {[item.name for item in wheels]}")
    return wheels[0]


def wheel_specs(config: Path = CLI_CONFIG) -> list[tuple[str, str]]:
    raw = _read_toml(config)
    try:
        specs = raw["project"]["installer"]["wheels"]
        result = [(str(item["path"]), str(item["distribution"])) for item in specs]
    except (KeyError, TypeError) as exc:
        _fail(f"cannot read [project.installer.wheels]: {exc}")
    return result


def verify_wheel_inventory(bundle_dir: Path) -> None:
    """Require exactly the declared wheel distributions and no extra wheel."""
    wheels = bundle_dir / "wheels"
    files = sorted(path for path in wheels.glob("*.whl") if path.is_file())
    expected: set[Path] = set()
    for pattern, distribution in wheel_specs():
        matches = [
            path for path in files
            if fnmatch.fnmatchcase(path.name, Path(pattern).name)
        ]
        if len(matches) != 1:
            _fail(
                f"expected exactly one wheel for {distribution!r} ({pattern}); "
                f"found {[path.name for path in matches]}"
            )
        expected.add(matches[0].resolve())
    extras = [path.name for path in files if path.resolve() not in expected]
    if extras:
        _fail("bundle contains wheel(s) absent from [project.installer.wheels]: " + ", ".join(extras))


def reject_bytecode(bundle_dir: Path) -> None:
    bytecode = sorted(
        path.relative_to(bundle_dir).as_posix()
        for path in bundle_dir.rglob("*")
        if (path.is_file() and path.name.endswith(".pyc")) or path.name == "__pycache__"
    )
    if bytecode:
        _fail("bundle contains Python bytecode/cache: " + ", ".join(bytecode))


def stage(
    bundle_dir: Path,
    variant: str,
    python_version: str,
    *,
    ciu_version: str | None = None,
    cli_extended_version: str | None = None,
) -> None:
    bundle_dir = bundle_dir.resolve()
    wheels = bundle_dir / "wheels"
    if not wheels.is_dir():
        _fail(f"bundle wheel directory is absent: {wheels}")
    with tempfile.TemporaryDirectory(prefix="tls-edge-wheel-") as temp:
        temp_dir = Path(temp)
        cli = _build_tls_edge_cli(temp_dir / "tls-edge-cli")
        ciu = fetch_github_wheel(
            temp_dir / "ciu",
            distribution="ciu",
            tag_prefix="ciu-v",
            pinned=ciu_version,
        )
        cli_extended = fetch_github_wheel(
            temp_dir / "cli-extended",
            distribution="cli-extended",
            tag_prefix="cli-extended-v",
            pinned=cli_extended_version,
        )
        for wheel in (cli, ciu, cli_extended):
            shutil.copy2(wheel, wheels / wheel.name)
    verify_wheel_inventory(bundle_dir)
    reject_bytecode(bundle_dir)
    print(f"[INFO] variant {variant} ({python_version}) wheel inventory verified")


def retain(dist_dir: Path) -> None:
    dist_dir.mkdir(parents=True, exist_ok=True)
    for name in ("VERSION", "get.py"):
        shutil.copy2(TLS_EDGE_DIR / name, dist_dir / name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    requirements = subparsers.add_parser("requirements")
    requirements.add_argument("--check", action="store_true")
    requirements.add_argument("--output", type=Path, default=REQUIREMENTS_OUT)
    subparsers.add_parser(
        "version-env",
        help="emit TLS_EDGE_VERSION=<VERSION> for the cmru build step",
    )
    stage_parser = subparsers.add_parser("stage")
    stage_parser.add_argument("bundle_dir", type=Path)
    stage_parser.add_argument("variant")
    stage_parser.add_argument("python_version")
    stage_parser.add_argument("--ciu-version", default=os.environ.get("TLS_EDGE_CIU_VERSION"))
    stage_parser.add_argument(
        "--cli-extended-version", default=os.environ.get("TLS_EDGE_CLI_EXTENDED_VERSION")
    )
    retain_parser = subparsers.add_parser("retain")
    retain_parser.add_argument("dist_dir", type=Path)
    args = parser.parse_args(argv)
    if args.command == "requirements":
        write_requirements(args.output, check=args.check)
    elif args.command == "version-env":
        print(version_env())
    elif args.command == "stage":
        stage(
            args.bundle_dir,
            args.variant,
            args.python_version,
            ciu_version=args.ciu_version,
            cli_extended_version=args.cli_extended_version,
        )
    else:
        retain(args.dist_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
