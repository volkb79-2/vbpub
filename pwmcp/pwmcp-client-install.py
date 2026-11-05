#!/usr/bin/env python3
"""Install ONLY the pwmcp client (CLI `pwmcp` + the `pwmcp_client` library) on a consumer host.

For a consumer that talks to a pwmcp server running elsewhere: no docker, no ciu, no compose,
no stack tree. It takes the release bundle (the same tar.xz `get.py` installs), extracts only
`manifest.json` and `wheels/*.whl`, verifies the archive against its published SHA256 and every
wheel against the manifest, builds a private venv from those wheels offline
(`pip install --no-index --require-hashes`: pwmcp-client plus its exact pinned dependencies,
including the Playwright version of that release) and links the `pwmcp` launcher.

    python3 pwmcp-client-install.py --version 1.64.1-r2
    python3 pwmcp-client-install.py --bundle ./pwmcp-v1.64.1-r2.tar.xz --sha256 <hex>

Upgrade = run it again with the new release (releases live side by side; `current` and the
launcher switch atomically). Private repository: --github-token-file FILE or $GITHUB_TOKEN.
Standard library only. The bundled wheels are CPython 3.14 / Linux x86_64 only (see README).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

DEFAULT_REPO = "volkb79-2/vbpub"
TAG_PREFIX = "pwmcp-v"
ASSET_SUFFIX = ".tar.xz"
_RELEASE_RE = re.compile(r"\d+\.\d+\.\d+-r\d+")


def die(message: str) -> "None":
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def info(message: str) -> None:
    print(f"[INFO] {message}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def platform_problem(version_info: tuple = tuple(sys.version_info), machine: str = "") -> str | None:
    """The release wheels are CPython 3.14 / Linux x86_64 only (greenlet cp314, playwright manylinux x86_64)."""
    machine = machine or platform.machine()
    if tuple(version_info[:2]) != (3, 14) or machine not in ("x86_64", "AMD64"):
        return (f"the pwmcp release wheels are built for CPython 3.14 on Linux x86_64 only; this is Python "
                f"{version_info[0]}.{version_info[1]} on {machine} (per-interpreter client wheels: PWMCP-36)")
    return None


def release_tag(version: str) -> str:
    """`1.64.1-r2` or `pwmcp-v1.64.1-r2` -> `pwmcp-v1.64.1-r2`."""
    bare = version.removeprefix(TAG_PREFIX)
    if not _RELEASE_RE.fullmatch(bare):
        die(f"--version must look like 1.64.1-r2 (or {TAG_PREFIX}1.64.1-r2), got {version!r}")
    return TAG_PREFIX + bare


def fetch(url: str, token: str | None, accept: str | None = None) -> bytes:
    headers = {"User-Agent": "pwmcp-client-install"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if accept:
        headers["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
        return response.read()


def download_release_asset(repo: str, tag: str, name: str, token: str | None, fetcher=fetch) -> bytes:
    """One release asset by name: the public download URL, or the API when a token is given (private repo)."""
    if not token:
        return fetcher(f"https://github.com/{repo}/releases/download/{tag}/{name}", None)
    release = json.loads(fetcher(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", token))
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            return fetcher(asset["url"], token, "application/octet-stream")
    die(f"release {tag} has no asset named {name}")


def obtain_bundle(args: argparse.Namespace, workdir: Path, fetcher=fetch) -> tuple[Path, str]:
    """Return (verified bundle path, release tag)."""
    if args.bundle:
        bundle = Path(args.bundle)
        if not bundle.is_file():
            die(f"--bundle {bundle} is not a file")
        expected = args.sha256
        if not expected:
            sidecar = Path(str(bundle) + ".sha256")
            if sidecar.is_file():
                expected = sidecar.read_text(encoding="utf-8").split()[0]
        match = re.fullmatch(rf"({re.escape(TAG_PREFIX)}\d+\.\d+\.\d+-r\d+){re.escape(ASSET_SUFFIX)}", bundle.name)
        if not match:
            die(f"cannot derive the release from the bundle name {bundle.name!r} "
                f"(expected {TAG_PREFIX}<release>{ASSET_SUFFIX})")
        tag = match.group(1)
    else:
        if not args.version:
            die("give --version RELEASE or --bundle FILE")
        tag = release_tag(args.version)
        token = args.token
        name = tag + ASSET_SUFFIX
        bundle = workdir / name
        bundle.write_bytes(download_release_asset(args.repo, tag, name, token, fetcher))
        expected = args.sha256 or download_release_asset(
            args.repo, tag, name + ".sha256", token, fetcher).decode().split()[0]
    if not expected:
        die("no SHA256 to verify the bundle against: pass --sha256 or keep the .sha256 sidecar next to --bundle")
    actual = sha256_file(bundle)
    if actual != expected.lower():
        die(f"bundle SHA256 mismatch for {bundle.name}: expected {expected}, got {actual}")
    info(f"bundle {bundle.name} sha256 verified")
    return bundle, tag


def extract_wheels(bundle: Path, dest: Path) -> dict:
    """Extract manifest.json and wheels/*.whl only (never any other member); return the manifest."""
    dest.mkdir(parents=True)
    manifest_text = None
    with tarfile.open(bundle, "r:*") as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if not member.isfile() or ".." in parts or member.name.startswith("/"):
                continue
            base = parts[-1]
            if base == "manifest.json" and len(parts) <= 2:
                manifest_text = archive.extractfile(member).read().decode("utf-8")
            elif base.endswith(".whl") and len(parts) >= 2 and parts[-2] == "wheels":
                (dest / base).write_bytes(archive.extractfile(member).read())
    if manifest_text is None:
        die("the bundle has no manifest.json")
    return json.loads(manifest_text)


def lock_lines(manifest: dict, wheelhouse: Path) -> list[str]:
    """Verify every wheel against the manifest; emit a hash-locked requirement per wheel."""
    lines: list[str] = []
    found = {path.name: path for path in wheelhouse.glob("*.whl")}
    entries = {k: v for k, v in manifest.items() if isinstance(v, dict) and "wheel" in v and "sha256" in v}
    if "pwmcp-client" not in entries:
        die("the manifest lists no pwmcp-client wheel")
    if set(found) != {v["wheel"] for v in entries.values()}:
        die(f"bundled wheels {sorted(found)} do not match the manifest {sorted(v['wheel'] for v in entries.values())}")
    for distribution, entry in entries.items():
        actual = sha256_file(found[entry["wheel"]])
        if actual != entry["sha256"]:
            die(f"wheel {entry['wheel']} ({distribution}) SHA256 mismatch: manifest {entry['sha256']}, got {actual}")
        version = entry["wheel"][: -len(".whl")].split("-")[1]
        lines.append(f"{distribution}=={version} --hash=sha256:{actual}")
    return lines


def build_venv(release_dir: Path, wheelhouse: Path, lines: list[str], run=subprocess.run) -> Path:
    venv = release_dir / "venv"
    lock = release_dir / "requirements.lock"
    lock.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run([sys.executable, "-m", "venv", str(venv)], check=True)
    py = str(venv / "bin" / "python")
    run([py, "-m", "pip", "install", "--isolated", "--disable-pip-version-check", "--no-index",
         "--find-links", str(wheelhouse), "--require-hashes", "-r", str(lock)], check=True)
    run([py, "-m", "pip", "check", "--isolated", "--disable-pip-version-check"], check=True)
    return venv


def replace_symlink(link: Path, target: Path) -> None:
    """Atomically point `link` at `target` (never clobbers a real file)."""
    if link.exists() and not link.is_symlink():
        die(f"refusing to replace the non-symlink {link}")
    link.parent.mkdir(parents=True, exist_ok=True)
    temp = link.with_name(f".{link.name}.new")
    if temp.is_symlink() or temp.exists():
        temp.unlink()
    temp.symlink_to(target)
    os.replace(temp, link)


def install(args: argparse.Namespace, fetcher=fetch, run=subprocess.run) -> Path:
    root = Path(args.dir).expanduser()
    with tempfile.TemporaryDirectory(prefix="pwmcp-client-install-") as tmp:
        work = Path(tmp)
        bundle, tag = obtain_bundle(args, work, fetcher)
        release_dir = root / "releases" / tag
        if release_dir.exists():
            current = root / "current"
            if current.is_symlink() and current.resolve() == release_dir.resolve():
                die(f"{tag} is already installed and is the current release ({release_dir}); nothing to do "
                    f"(remove that directory first if you really want to rebuild it)")
            info(f"{tag} is already installed at {release_dir}; reinstalling it")
            shutil.rmtree(release_dir)
        wheelhouse = release_dir / "wheelhouse"
        release_dir.mkdir(parents=True)
        try:
            manifest = extract_wheels(bundle, wheelhouse)
            lines = lock_lines(manifest, wheelhouse)
            venv = build_venv(release_dir, wheelhouse, lines, run)
        except BaseException:
            shutil.rmtree(release_dir, ignore_errors=True)
            raise
    launcher = venv / "bin" / "pwmcp"
    replace_symlink(root / "current", release_dir)
    bin_dir = Path(args.bin_dir).expanduser()
    replace_symlink(bin_dir / "pwmcp", root / "current" / "venv" / "bin" / "pwmcp")
    info(f"installed {tag}: launcher {bin_dir / 'pwmcp'} -> {launcher}")
    info("next: pwmcp skills install   (then: pwmcp doctor --contract URL)")
    return release_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install only the pwmcp client (no docker/ciu/stack).")
    parser.add_argument("--version", help="release to install, e.g. 1.64.1-r2 (or pwmcp-v1.64.1-r2)")
    parser.add_argument("--bundle", help="a local release bundle (pwmcp-v<release>.tar.xz) instead of downloading")
    parser.add_argument("--sha256", help="expected SHA256 of the bundle (default: the release .sha256 asset/sidecar)")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub owner/name (default: %(default)s)")
    parser.add_argument("--github-token-file", metavar="FILE", help="file holding a GitHub token (private repository)")
    parser.add_argument("--dir", default="~/.local/share/pwmcp-client", help="install root (default: %(default)s)")
    parser.add_argument("--bin-dir", default="~/.local/bin", help="where the `pwmcp` launcher link goes (default: %(default)s)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    problem = platform_problem()
    if problem:
        die(problem)
    args.token = None
    if args.github_token_file:
        args.token = Path(args.github_token_file).read_text(encoding="utf-8").strip() or None
    args.token = args.token or os.environ.get("GITHUB_TOKEN") or None
    install(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
