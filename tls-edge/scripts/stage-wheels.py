#!/usr/bin/env python3
"""Stage the bundled ciu and its dependency wheels, one directory per interpreter variant.

Output: tls-edge/vendor/<variant>/*.whl (gitignored), consumed by build-artifact.sh.

* ciu: the newest stable `ciu-v*` GitHub release wheel (sha256 verified), or `--ciu-wheel`.
  Reproducible builds: `--ciu-version X` (or env TLS_EDGE_CIU_VERSION) refuses to stage
  anything but that ciu release; the staged ciu version and sha256 are always printed
  (copy them into the release notes).
* Dependencies: ciu's wheel metadata plus tls-edge CLI runtime and interactive prompt
  requirements, resolved together by `pip download --only-binary=:all:` for each
  variant's CPython tag on x86_64 glibc (`manylinux2014`/`2_17`/`2_28`).
* `tls-edge-cli` is built from this checkout; `cli-extended` is fetched from its latest
  stable GitHub release with SHA-256 verification and a minimum-version check.
* Freshness rule (estate policy): every PyPI wheel must be at least 14 days past its
  upload; `--allow-fresh` waives it (recorded in the output).
"""

from __future__ import annotations

import argparse
import datetime
import email.parser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

TLS_EDGE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TLS_EDGE_DIR.parent
VENDOR = TLS_EDGE_DIR / "vendor"
CLI_PROJECT = TLS_EDGE_DIR / "pyproject.toml"
CLI_RUNTIME = TLS_EDGE_DIR / "requirements-runtime.txt"
MIN_AGE_DAYS = 14
PLATFORMS = ("manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux_2_28_x86_64")


def _fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def variants(config: Path = TLS_EDGE_DIR / "cmru.toml") -> dict[str, str]:
    """{variant name: cpXYZ tag} from [[project.variants]] build_arg = "PYTHON_TAG=cpXYZ"."""
    with config.open("rb") as stream:
        raw = tomllib.load(stream)
    found: dict[str, str] = {}
    for item in raw.get("project", {}).get("variants", []):
        match = re.fullmatch(r"PYTHON_TAG=(cp3\d+)", str(item.get("build_arg", "")))
        if not match:
            _fail(f"variant {item.get('name')!r} needs build_arg = \"PYTHON_TAG=cp3NN\"")
        found[str(item["name"])] = match.group(1)
    if not found:
        _fail("cmru.toml declares no [[project.variants]]")
    return found


def requirements_of(wheel: Path) -> list[str]:
    """ciu's runtime requirements (Requires-Dist without extras) from the wheel METADATA."""
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            _fail(f"{wheel.name}: expected one METADATA file, found {len(names)}")
        metadata = email.parser.Parser().parsestr(archive.read(names[0]).decode("utf-8"))
    return [
        value.split(";")[0].strip()
        for value in metadata.get_all("Requires-Dist", [])
        if "extra ==" not in value
    ]


def download_command(requirements: list[str], tag: str, dest: Path) -> list[str]:
    """pip invocation for one variant: binary-only, target interpreter and platform."""
    command = [
        sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
        "--only-binary=:all:", "--index-url", "https://pypi.org/simple",
        "--python-version", f"{tag[2]}.{tag[3:]}", "--implementation", "cp", "--abi", tag,
        "--dest", str(dest),
    ]
    for platform in PLATFORMS:
        command += ["--platform", platform]
    return [*command, *requirements]


def _wheel_name_version(wheel: Path) -> tuple[str, str]:
    parts = wheel.name[: -len(".whl")].split("-")
    return parts[0], parts[1]


def check_freshness(wheel: Path, now: datetime.datetime | None = None) -> None:
    """Refuse a PyPI wheel uploaded less than MIN_AGE_DAYS ago (the vetting buffer)."""
    name, version = _wheel_name_version(wheel)
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https host
        files = json.load(response)["urls"]
    times = [f["upload_time_iso_8601"] for f in files if f["filename"] == wheel.name]
    if not times:
        _fail(f"PyPI lists no upload time for {wheel.name}")
    uploaded = datetime.datetime.fromisoformat(times[0].replace("Z", "+00:00"))
    age = (now or datetime.datetime.now(datetime.timezone.utc)) - uploaded
    if age < datetime.timedelta(days=MIN_AGE_DAYS):
        _fail(f"{wheel.name} is only {age.days} day(s) old; policy needs {MIN_AGE_DAYS} "
              "(pin an older version in the requirements or pass --allow-fresh)")


def check_ciu_pin(resolved: str, pinned: str | None) -> None:
    """Fail when a pin is requested and the newest stable ciu release is another version."""
    if pinned and resolved != pinned.removeprefix("ciu-v").removeprefix("v"):
        _fail(f"newest stable ciu release is {resolved}, but {pinned} is pinned; "
              "bump the pin deliberately or pass --ciu-wheel for that exact wheel")


def _normal_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"(\d+(?:\.\d+)*)", value)
    if not match:
        _fail(f"release version is not numeric: {value!r}")
    return tuple(int(part) for part in value.split("."))


def _minimum_version(requirement: str) -> tuple[int, ...]:
    match = re.search(r">=\s*(\d+(?:\.\d+)*)", requirement)
    if not match:
        _fail(f"requirement needs an explicit minimum version: {requirement!r}")
    return _version_tuple(match.group(1))


def _read_requirements(path: Path) -> list[str]:
    return [
        line.partition("#")[0].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.partition("#")[0].strip()
    ]


def _tls_edge_requirements() -> tuple[list[str], str]:
    """Return CLI runtime + prompt requirements and its cli-extended floor."""
    try:
        pyproject = tomllib.loads(CLI_PROJECT.read_text(encoding="utf-8"))
        runtime = _read_requirements(CLI_RUNTIME)
        interactive = pyproject["project"]["optional-dependencies"]["interactive"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        _fail(f"cannot read tls-edge CLI wheel requirements: {exc}")
    if not isinstance(interactive, list) or not all(isinstance(item, str) for item in interactive):
        _fail("pyproject.toml project.optional-dependencies.interactive must be a string array")
    requirements = [*runtime, *interactive]
    cli_extended = [
        item for item in runtime
        if _normal_name(re.split(r"[<>=!~; ]", item, 1)[0]) == "cli-extended"
    ]
    if len(cli_extended) != 1:
        _fail("requirements-runtime.txt must declare cli-extended exactly once")
    return requirements, cli_extended[0]


def _github_release_wheel(
    dest: Path,
    *,
    distribution: str,
    tag_prefix: str,
    pinned: str | None = None,
    minimum: str | None = None,
) -> Path:
    """Fetch one stable GitHub wheel release and verify its declared SHA-256."""
    try:
        from cmru.hosts.github import GitHubReleaseHost
    except ImportError as exc:
        _fail(f"the installed cmru GitHub release adapter is unavailable: {exc}")
    with (REPO_ROOT / "cmru.orchestration.toml").open("rb") as stream:
        github = tomllib.load(stream)["github"]
    release = GitHubReleaseHost(
        github["owner"], github["repo"], os.environ.get("GITHUB_TOKEN", "")
    ).resolve_latest(tag_prefix, asset_suffix=".whl")
    if not release:
        _fail(f"no stable {distribution} wheel release is published")
    version = str(release["version"])
    if pinned and version != pinned.removeprefix(tag_prefix).removeprefix("v"):
        _fail(f"latest {distribution} release is {version}, but {pinned} is pinned")
    if minimum and _version_tuple(version) < _minimum_version(minimum):
        _fail(f"{distribution} {version} is below requirement {minimum}")
    name = str(release["asset"])
    expected_name = _normal_name(distribution).replace("-", "_")
    if not name.endswith(".whl") or "/" in name or "\\" in name:
        _fail(f"CMRU resolved an invalid {distribution} wheel name: {name!r}")
    if not name.lower().startswith(expected_name.lower() + "-"):
        _fail(f"CMRU resolved an unexpected {distribution} wheel name: {name!r}")
    url = str(release["url"])
    if not url.startswith("https://github.com/"):
        _fail(f"CMRU resolved a non-GitHub HTTPS wheel URL: {url!r}")
    request = urllib.request.Request(url, headers={"User-Agent": "tls-edge-wheel-stage"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            content = response.read()
    except OSError as exc:
        _fail(f"cannot download {name}: {exc}")
    digest = hashlib.sha256(content).hexdigest()
    if digest.lower() != str(release["sha256"]).lower():
        _fail(f"SHA-256 mismatch for {name}")
    path = dest / name
    path.write_bytes(content)
    print(f"[INFO] staged {name} ({distribution} {version}, sha256 {digest})")
    return path


def _build_tls_edge_cli(dest: Path) -> Path:
    """Build the first-party, pure-Python CLI wheel from this checkout."""
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--disable-pip-version-check",
         "--no-deps", "--wheel-dir", str(dest), str(TLS_EDGE_DIR)],
        cwd=TLS_EDGE_DIR,
        check=False,
    )
    if result.returncode:
        _fail(f"building tls-edge-cli wheel failed with exit {result.returncode}")
    wheels = sorted(dest.glob("tls_edge_cli-*.whl"))
    if len(wheels) != 1:
        _fail(f"expected one tls-edge-cli wheel, found {[item.name for item in wheels]}")
    return wheels[0]


def _github_ciu_wheel(dest: Path, pinned: str | None = None) -> Path:
    try:
        from cmru.hosts.github import GitHubReleaseHost
    except ImportError as exc:
        _fail(f"the installed cmru GitHub release adapter is unavailable: {exc}")
    with (REPO_ROOT / "cmru.orchestration.toml").open("rb") as stream:
        github = tomllib.load(stream)["github"]
    release = GitHubReleaseHost(
        github["owner"], github["repo"], os.environ.get("GITHUB_TOKEN", "")
    ).resolve_latest("ciu-v", asset_suffix=".whl")
    if not release:
        _fail("no stable ciu wheel release is published")
    check_ciu_pin(str(release["version"]), pinned)
    name = str(release["asset"])
    url = str(release["url"])
    if not name.endswith(".whl") or "/" in name or not url.startswith("https://github.com/"):
        _fail(f"unexpected ciu release asset {name!r} / {url!r}")
    request = urllib.request.Request(url, headers={"User-Agent": "tls-edge-wheel-stage"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        content = response.read()
    if hashlib.sha256(content).hexdigest() != str(release["sha256"]).lower():
        _fail(f"SHA-256 mismatch for {name}")
    path = dest / name
    path.write_bytes(content)
    print(f"[INFO] staged {name} (ciu {release['version']}, sha256 {release['sha256']})")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ciu-wheel", type=Path, help="use this ciu wheel instead of the release")
    parser.add_argument("--ciu-version", default=os.environ.get("TLS_EDGE_CIU_VERSION"),
                        help="refuse any ciu release other than this version (reproducible builds)")
    parser.add_argument(
        "--cli-extended-version",
        default=os.environ.get("TLS_EDGE_CLI_EXTENDED_VERSION"),
        help="require this cli-extended release version (reproducible builds)",
    )
    parser.add_argument("--allow-fresh", action="store_true", help="waive the 14-day rule")
    args = parser.parse_args(argv)

    if VENDOR.is_symlink():
        _fail(f"refusing to replace wheel staging symlink {VENDOR}")
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    VENDOR.mkdir()
    tags = variants()

    staging = VENDOR / ".staging"
    staging.mkdir()
    ciu_dir = staging / "ciu"
    cli_extended_dir = staging / "cli-extended"
    cli_dir = staging / "tls-edge-cli"
    ciu_dir.mkdir()
    cli_extended_dir.mkdir()
    ciu = args.ciu_wheel.resolve() if args.ciu_wheel else _github_ciu_wheel(ciu_dir, args.ciu_version)
    cli_requirements, cli_extended_requirement = _tls_edge_requirements()
    cli_extended = _github_release_wheel(
        cli_extended_dir,
        distribution="cli-extended",
        tag_prefix="cli-extended-v",
        pinned=args.cli_extended_version,
        minimum=cli_extended_requirement,
    )
    tls_edge_cli = _build_tls_edge_cli(cli_dir)
    if args.allow_fresh:
        print("[WARN] --allow-fresh: the 14-day freshness rule is WAIVED for this build; "
              "record the reason in the release notes")
    cli_extended_name = _normal_name("cli-extended")
    combined_requirements = list(dict.fromkeys(
        [requirement for requirement in requirements_of(ciu)
         if _normal_name(re.split(r"[<>=!~; ]", requirement, 1)[0]) != cli_extended_name]
        + [
            requirement for requirement in cli_requirements
            if _normal_name(re.split(r"[<>=!~; ]", requirement, 1)[0]) != cli_extended_name
        ]
    ))
    print(f"[INFO] merged runtime requirements: {combined_requirements}")

    for name, tag in tags.items():
        target = VENDOR / name
        target.mkdir()
        shutil.copy2(ciu, target / ciu.name)
        shutil.copy2(cli_extended, target / cli_extended.name)
        shutil.copy2(tls_edge_cli, target / tls_edge_cli.name)
        result = subprocess.run(download_command(combined_requirements, tag, target), check=False)
        if result.returncode:
            _fail(f"pip download for {name} ({tag}) failed with exit {result.returncode}")
        if not args.allow_fresh:
            for wheel in sorted(target.glob("*.whl")):
                if wheel.name not in {ciu.name, cli_extended.name, tls_edge_cli.name}:
                    check_freshness(wheel)
        print(f"[INFO] variant {name}: {sorted(p.name for p in target.glob('*.whl'))}")
    shutil.rmtree(staging)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
