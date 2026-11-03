#!/usr/bin/env python3
"""Stage the bundled ciu and its dependency wheels, one directory per interpreter variant.

Output: tls-edge/vendor/<variant>/*.whl (gitignored), consumed by build-artifact.sh.

* ciu: the newest stable `ciu-v*` GitHub release wheel (sha256 verified), or `--ciu-wheel`.
  Reproducible builds: `--ciu-version X` (or env TLS_EDGE_CIU_VERSION) refuses to stage
  anything but that ciu release; the staged ciu version and sha256 are always printed
  (copy them into the release notes).
* Dependencies: exactly ciu's own `Requires-Dist` (read from the wheel METADATA, so the
  list cannot drift), resolved by `pip download --only-binary=:all:` for each variant's
  CPython tag on x86_64 glibc (`manylinux2014`/`2_17`/`2_28`).
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
    parser.add_argument("--allow-fresh", action="store_true", help="waive the 14-day rule")
    args = parser.parse_args(argv)

    if VENDOR.is_symlink():
        _fail(f"refusing to replace wheel staging symlink {VENDOR}")
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    VENDOR.mkdir()
    tags = variants()

    ciu_dir = VENDOR / ".ciu"
    ciu_dir.mkdir()
    ciu = args.ciu_wheel.resolve() if args.ciu_wheel else _github_ciu_wheel(ciu_dir, args.ciu_version)
    if args.allow_fresh:
        print("[WARN] --allow-fresh: the 14-day freshness rule is WAIVED for this build; "
              "record the reason in the release notes")
    requirements = requirements_of(ciu)
    print(f"[INFO] ciu requirements: {requirements}")

    for name, tag in tags.items():
        target = VENDOR / name
        target.mkdir()
        shutil.copy2(ciu, target / ciu.name)
        result = subprocess.run(download_command(requirements, tag, target), check=False)
        if result.returncode:
            _fail(f"pip download for {name} ({tag}) failed with exit {result.returncode}")
        if not args.allow_fresh:
            for wheel in sorted(target.glob("*.whl")):
                if wheel.name != ciu.name:
                    check_freshness(wheel)
        print(f"[INFO] variant {name}: {sorted(p.name for p in target.glob('*.whl'))}")
    shutil.rmtree(ciu_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
