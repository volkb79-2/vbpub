#!/usr/bin/env python3
"""Compare tester-unified's Run-Gate labels with the installed wheel identity."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

UNKNOWN_EXIT = 125
TIMEOUT_EXIT = 124
STABLE_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Result:
    rc: int
    out: str = ""
    err: str = ""


class Runner(Protocol):
    def run(self, argv: Sequence[str], timeout: float) -> Result: ...


class ProcessRunner:
    """Run bounded, read-only identity probes."""

    def run(self, argv: Sequence[str], timeout: float) -> Result:
        try:
            completed = subprocess.run(
                list(argv), stdin=subprocess.DEVNULL, capture_output=True,
                text=True, check=False, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return Result(TIMEOUT_EXIT, "", "timeout")
        except OSError:
            return Result(127, "", "could not start verifier")
        return Result(completed.returncode, completed.stdout, completed.stderr)


IDENTITY_SCRIPT = r'''
import json, re
from importlib.metadata import distribution

dist = distribution("run-gate")
raw = dist.read_text("direct_url.json")
if not raw:
    raise SystemExit("installed Run-Gate is not proven to come from a wheel")
try:
    direct = json.loads(raw)
except json.JSONDecodeError:
    direct = {}
archive = direct.get("archive_info") if isinstance(direct, dict) else None
url = direct.get("url") if isinstance(direct, dict) else None
editable = (direct.get("dir_info") or {}).get("editable", False) if isinstance(direct, dict) else False
hashes = archive.get("hashes") if isinstance(archive, dict) else None
candidate = hashes.get("sha256") if isinstance(hashes, dict) else None
legacy_hash = archive.get("hash") if isinstance(archive, dict) else None
if isinstance(legacy_hash, str) and legacy_hash.startswith("sha256="):
    legacy_candidate = legacy_hash.removeprefix("sha256=")
    if candidate is None:
        candidate = legacy_candidate
    elif candidate != legacy_candidate:
        candidate = None
if (
    editable
    or not isinstance(url, str)
    or not url.split("?", 1)[0].split("#", 1)[0].lower().endswith(".whl")
    or not isinstance(candidate, str)
    or re.fullmatch(r"[0-9a-f]{64}", candidate) is None
):
    raise SystemExit("installed Run-Gate is not proven to come from a non-editable wheel")
print(json.dumps({"version": dist.version, "wheel_sha256": candidate}))
'''.strip()


PUBLISHED_WHEEL_SCRIPT = r'''
import hashlib, json, re, sys, tomllib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

root = Path(sys.argv[1])
version = sys.argv[2]
try:
    forge = tomllib.loads((root / "cmru.orchestration.toml").read_text(encoding="utf-8"))
    project = tomllib.loads((root / "run-gate-project" / "cmru.toml").read_text(encoding="utf-8"))
    owner = forge["github"]["owner"]
    repository = forge["github"]["repo"]
    prefix = project["project"]["prefix"]
except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
    print("cannot resolve the published Run-Gate release configuration", file=sys.stderr)
    raise SystemExit(1)
if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in (owner, repository, prefix)):
    print("published Run-Gate release configuration is malformed", file=sys.stderr)
    raise SystemExit(1)
tag = prefix + version
wheel_name = f"run_gate-{version}-py3-none-any.whl"

def get_bytes(url, *, api=False):
    headers = {"User-Agent": "vbpub-estate-doctor"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=8) as response:
        if response.status != 200:
            raise OSError("unexpected HTTP response")
        return response.read()

try:
    api_url = f"https://api.github.com/repos/{quote(owner)}/{quote(repository)}/releases/tags/{quote(tag)}"
    release = json.loads(get_bytes(api_url, api=True))
except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError):
    print("published Run-Gate release is not observable", file=sys.stderr)
    raise SystemExit(125)
if not isinstance(release, dict) or release.get("tag_name") != tag or release.get("draft") is not False or release.get("prerelease") is not False:
    print("installed Run-Gate version has no published stable release", file=sys.stderr)
    raise SystemExit(1)
assets = release.get("assets")
if not isinstance(assets, list):
    print("published Run-Gate release has no readable asset list", file=sys.stderr)
    raise SystemExit(1)
wheel_assets = [item for item in assets if isinstance(item, dict) and item.get("name") == wheel_name]
checksum_assets = [item for item in assets if isinstance(item, dict) and item.get("name") == wheel_name + ".sha256"]
if len(wheel_assets) != 1 or len(checksum_assets) != 1:
    print("published Run-Gate release is missing its unique wheel or checksum asset", file=sys.stderr)
    raise SystemExit(1)
wheel_url = wheel_assets[0].get("browser_download_url")
checksum_url = checksum_assets[0].get("browser_download_url")
for url in (wheel_url, checksum_url):
    parsed = urlparse(url) if isinstance(url, str) else None
    if parsed is None or parsed.scheme != "https" or parsed.hostname != "github.com":
        print("published Run-Gate release asset URL is invalid", file=sys.stderr)
        raise SystemExit(1)
try:
    checksum_bytes = get_bytes(checksum_url)
    wheel_bytes = get_bytes(wheel_url)
except (HTTPError, URLError, OSError, TimeoutError):
    print("published Run-Gate wheel or checksum is not observable", file=sys.stderr)
    raise SystemExit(125)
tokens = checksum_bytes.decode("utf-8").strip().split()
sidecar_sha256 = tokens[0].lower() if tokens else ""
wheel_sha256 = hashlib.sha256(wheel_bytes).hexdigest()
if re.fullmatch(r"[0-9a-f]{64}", sidecar_sha256) is None or wheel_sha256 != sidecar_sha256:
    print("published Run-Gate wheel bytes do not match its release checksum", file=sys.stderr)
    raise SystemExit(1)
print(json.dumps({"version": version, "wheel_sha256": wheel_sha256}))
'''.strip()


def _failure(message: str, rc: int = 1) -> Result:
    return Result(rc, message + "\n")


def _run_gate_bin(repo_root: Path, runner_env_path: Path) -> str:
    try:
        lines = runner_env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError("cannot read runner.env for installed Run-Gate selection") from exc
    selected = None
    for line in lines:
        match = re.match(r"\s*(?:export\s+)?RUN_GATE_BIN\s*=\s*(.*)\s*$", line)
        if not match:
            continue
        try:
            words = shlex.split(match.group(1), comments=True)
        except ValueError as exc:
            raise ValueError("runner.env has a malformed RUN_GATE_BIN") from exc
        if len(words) != 1 or not words[0].startswith("/"):
            raise ValueError("runner.env RUN_GATE_BIN must be one absolute path")
        selected = words[0]
    if selected:
        return selected

    run_lane = repo_root / "scripts" / "netcup" / "runner" / "run-lane.sh"
    try:
        source = run_lane.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("cannot resolve the runner's documented Run-Gate launcher") from exc
    match = re.search(r"(?m)^run_gate_bin=\$\{RUN_GATE_BIN:-([^}]+)\}$", source)
    if match is None or not match.group(1).startswith("/"):
        raise ValueError("runner's documented Run-Gate launcher is not an absolute path")
    return match.group(1)


def _local_interpreter(executable: str) -> list[str]:
    try:
        first_line = Path(executable).read_text(encoding="utf-8").splitlines()[0]
        words = shlex.split(first_line.removeprefix("#!"))
    except (OSError, IndexError, ValueError) as exc:
        raise ValueError("installed Run-Gate launcher has no readable Python shebang") from exc
    if not first_line.startswith("#!") or not words:
        raise ValueError("installed Run-Gate launcher has no readable Python shebang")
    if Path(words[0]).name == "env":
        if len(words) < 2:
            raise ValueError("installed Run-Gate launcher has an incomplete env shebang")
        interpreter = shutil.which(words[1])
        if interpreter is None:
            raise ValueError("installed Run-Gate Python interpreter is not on PATH")
        return [interpreter, *words[2:]]
    if not Path(words[0]).is_absolute():
        raise ValueError("installed Run-Gate launcher has a non-absolute interpreter")
    return words


def _remote_identity_bootstrap() -> str:
    identity = repr(IDENTITY_SCRIPT)
    return f'''import shlex, shutil, subprocess, sys
from pathlib import Path
target = Path(sys.argv[1])
try:
    first_line = target.read_text(encoding="utf-8").splitlines()[0]
    words = shlex.split(first_line.removeprefix("#!"))
except (OSError, IndexError, ValueError):
    raise SystemExit("installed Run-Gate launcher has no readable Python shebang")
if not first_line.startswith("#!") or not words:
    raise SystemExit("installed Run-Gate launcher has no readable Python shebang")
if Path(words[0]).name == "env":
    if len(words) < 2 or shutil.which(words[1]) is None:
        raise SystemExit("installed Run-Gate Python interpreter is not on PATH")
    words[0] = shutil.which(words[1])
elif not Path(words[0]).is_absolute():
    raise SystemExit("installed Run-Gate launcher has a non-absolute interpreter")
completed = subprocess.run([*words, "-I", "-B", "-c", {identity}], capture_output=True, text=True, check=False, timeout=8)
if completed.returncode:
    raise SystemExit("installed Run-Gate is not proven to come from a non-editable wheel")
sys.stdout.write(completed.stdout)
'''


def _selected_image(repo_root: Path, env: dict[str, str]) -> str:
    try:
        config = tomllib.loads((repo_root / "run-gate.root.toml").read_text(encoding="utf-8"))
        environment = config["environments"]["tester-unified"]
        image = environment["image"]
        image_env = environment.get("image_env")
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("cannot resolve tester-unified image from run-gate.root.toml") from exc
    if image_env is not None:
        if not isinstance(image_env, str) or not image_env:
            raise ValueError("tester-unified image_env is malformed")
        if image_env in env:
            image = env[image_env]
            if not image:
                raise ValueError(f"selected tester-unified image override ${image_env} is empty")
    if not isinstance(image, str) or not image:
        raise ValueError("tester-unified image is missing from run-gate.root.toml")
    return image


def check_identity(
    runner: Runner,
    repo_root: Path,
    *,
    cockpit: str | None = None,
    runner_env_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> Result:
    """Compare selected image labels with the installed Run-Gate wheel."""
    try:
        image = _selected_image(repo_root, dict(os.environ if env is None else env))
        if cockpit is None:
            executable = shutil.which("run-gate")
            if executable is None:
                return _failure("installed Run-Gate executable is not on PATH")
            version_argv = [executable, "--version"]
            metadata_argv = [*_local_interpreter(executable), "-I", "-B", "-c", IDENTITY_SCRIPT]
        else:
            if not cockpit or runner_env_path is None:
                return _failure("test-host Run-Gate check requires its cockpit and runner.env")
            executable = _run_gate_bin(repo_root, runner_env_path)
            version_argv = ["docker", "exec", "-u", "vscode", cockpit, executable, "--version"]
            metadata_argv = [
                "docker", "exec", "-u", "vscode", cockpit, "python3", "-c",
                _remote_identity_bootstrap(), executable,
            ]
    except ValueError as exc:
        return _failure(str(exc))

    version_result = runner.run(version_argv, timeout=10)
    if version_result.rc in (TIMEOUT_EXIT, UNKNOWN_EXIT):
        return _failure("could not observe installed Run-Gate version", UNKNOWN_EXIT)
    if version_result.rc != 0:
        return _failure("could not run the installed Run-Gate --version probe")
    lines = version_result.out.strip().splitlines()
    match = re.fullmatch(
        r"run-gate ((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))",
        lines[0],
    ) if len(lines) == 1 else None
    if match is None or STABLE_VERSION.fullmatch(match.group(1)) is None:
        return _failure("installed Run-Gate --version is not stable SemVer")
    version = match.group(1)

    identity_result = runner.run(metadata_argv, timeout=10)
    if identity_result.rc in (TIMEOUT_EXIT, UNKNOWN_EXIT):
        return _failure("could not observe installed Run-Gate wheel metadata", UNKNOWN_EXIT)
    if identity_result.rc != 0:
        return _failure("installed Run-Gate is not proven to come from a non-editable wheel")
    try:
        identity = json.loads(identity_result.out)
        package_version = identity["version"]
        wheel_sha256 = identity["wheel_sha256"]
    except (ValueError, KeyError, TypeError):
        return _failure("installed Run-Gate wheel identity is malformed")
    if package_version != version:
        return _failure("installed Run-Gate CLI version does not match its wheel metadata")
    if not isinstance(wheel_sha256, str) or SHA256.fullmatch(wheel_sha256) is None:
        return _failure("installed Run-Gate wheel SHA-256 is malformed")

    published_result = runner.run(
        ["python3", "-I", "-B", "-c", PUBLISHED_WHEEL_SCRIPT, str(repo_root), version],
        timeout=35,
    )
    if published_result.rc in (TIMEOUT_EXIT, UNKNOWN_EXIT):
        return _failure("could not observe the published Run-Gate wheel", UNKNOWN_EXIT)
    if published_result.rc != 0:
        return _failure("could not verify the published Run-Gate wheel")
    try:
        published_identity = json.loads(published_result.out)
        published_version = published_identity["version"]
        published_sha256 = published_identity["wheel_sha256"]
    except (ValueError, KeyError, TypeError):
        return _failure("published Run-Gate wheel identity is malformed")
    if published_version != version or not isinstance(published_sha256, str) or SHA256.fullmatch(published_sha256) is None:
        return _failure("published Run-Gate wheel identity is malformed")
    if wheel_sha256 != published_sha256:
        return _failure("installed Run-Gate wheel does not match the published release asset")

    inspected = runner.run(
        ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image],
        timeout=10,
    )
    if inspected.rc in (TIMEOUT_EXIT, UNKNOWN_EXIT):
        return _failure("could not observe tester-unified image labels", UNKNOWN_EXIT)
    if inspected.rc != 0:
        return _failure("tester-unified image is missing or cannot be inspected")
    try:
        labels = json.loads(inspected.out)
    except (ValueError, TypeError):
        return _failure("tester-unified image labels are malformed")
    if not isinstance(labels, dict):
        return _failure("tester-unified image has no readable labels")
    image_version = labels.get("org.vbpub.run-gate.version")
    image_sha256 = labels.get("org.vbpub.run-gate.wheel-sha256")
    if image_version != version or image_sha256 != published_sha256:
        return _failure(
            "tester-unified image does not contain the published Run-Gate wheel: "
            f"version={image_version!r}, sha256={image_sha256!r}; expected "
            f"version={version!r}, sha256={published_sha256}"
        )
    return Result(0, f"PASS run-gate {version} {published_sha256}\n")


def main(argv: Sequence[str] | None = None, runner: Runner | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cockpit", help="runner cockpit whose Run-Gate image is selected")
    parser.add_argument("--runner-env", type=Path, help="runner.env containing RUN_GATE_BIN")
    args = parser.parse_args(argv)
    result = check_identity(
        runner or ProcessRunner(), Path(__file__).resolve().parents[2],
        cockpit=args.cockpit, runner_env_path=args.runner_env,
    )
    sys.stdout.write(result.out)
    if result.rc != 0 and not result.out:
        sys.stdout.write(result.err or "Run-Gate image identity could not be verified\n")
    return result.rc


if __name__ == "__main__":
    raise SystemExit(main())
