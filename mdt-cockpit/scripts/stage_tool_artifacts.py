#!/usr/bin/env python3
"""Pre-stage external tool artifacts before docker bake runs.

This module resolves effective tool versions and downloads all required release
artifacts locally. Any failure raises an exception so callers can fail fast
before docker buildx bake starts.
"""
from __future__ import annotations

import argparse
from mdt_cli import MdtArgumentParser
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STAGE_ROOT = PROJECT_ROOT / "build" / "tool-artifacts-staging"
DOWNLOADS_DIR = STAGE_ROOT / "downloads"
METADATA_PATH = STAGE_ROOT / "metadata.json"
VERSIONS_ENV_PATH = STAGE_ROOT / "tool-versions.env"

USER_AGENT = "mdt-cockpit/stage-tool-artifacts"
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}
DEFAULT_RETRIES = 3
DOWNLOAD_TIMEOUT_SECONDS = 90
AWSCLI_VERSION_RE = re.compile(r"aws-cli/(?P<version>[0-9][^\s]+)")
CLAUDE_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.+-]+)?$")
NPM_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.+-]+)?$")

ANTIGRAVITY_MANIFEST_LINUX_AMD64_URL = (
    "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/"
    "linux_amd64.json"
)


class StageError(RuntimeError):
    """Raised when artifact staging fails."""


@dataclass(frozen=True)
class StagedArtifact:
    tool: str
    version: str
    source_url: str
    final_url: str
    file_name: str
    sha256: str
    kind: str
    verification: str


@dataclass(frozen=True)
class StagingResult:
    stage_root: Path
    metadata_path: Path
    versions_env_path: Path
    resolved_versions: dict[str, str]


def _log(message: str) -> None:
    print(f"[INFO] {message}", file=os.sys.stderr)


def _token() -> str:
    return (os.getenv("GITHUB_PUSH_PAT") or os.getenv("GITHUB_TOKEN") or "").strip()


def _github_api_headers() -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if extra:
        headers.update(extra)
    return headers


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _git_out(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _artifact_cache_dir() -> Path:
    """Return a local cache shared by all worktrees of this repository.

    Default: `artifacts/cache/tool-artifacts` of THIS project in the main checkout (the git common
    dir's parent), so every release worktree reuses one cache. `artifacts/` is gitignored and holds
    only local, regenerable data; nothing is written under `.git`. A repository without a normal
    main checkout (bare, or a common dir that is not named `.git`) uses the project's own
    `artifacts/cache`. `MDT_ARTIFACT_CACHE_DIR` overrides everything.
    """
    configured = os.getenv("MDT_ARTIFACT_CACHE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    common = Path(_git_out("rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (PROJECT_ROOT / common).resolve()
    if common.name != ".git":
        return PROJECT_ROOT / "artifacts" / "cache" / "tool-artifacts"
    try:
        toplevel = Path(_git_out("rev-parse", "--show-toplevel")).resolve()
        relative = PROJECT_ROOT.resolve().relative_to(toplevel)
    except (subprocess.CalledProcessError, ValueError):
        return PROJECT_ROOT / "artifacts" / "cache" / "tool-artifacts"
    return common.parent / relative / "artifacts" / "cache" / "tool-artifacts"


def _cache_entry(url: str) -> tuple[Path, Path]:
    entry = _artifact_cache_dir() / "by-url" / hashlib.sha256(url.encode()).hexdigest()
    return entry / "payload", entry / "metadata.json"


def _cacheable_download(url: str, destination: Path) -> bool:
    """Cache immutable release assets, never mutable discovery documents."""
    return (
        destination not in {STAGE_ROOT / "tmp-text-response", STAGE_ROOT / "tmp-json-response"}
        and "/latest" not in url
        and not url.endswith("linux_amd64.json")
    )


def _restore_cached_download(url: str, destination: Path) -> str | None:
    payload, metadata_path = _cache_entry(url)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected, final_url = str(metadata["sha256"]), str(metadata["final_url"])
    except (OSError, KeyError, ValueError, TypeError):
        return None
    if not payload.is_file() or _sha256(payload) != expected:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(payload, destination)
    _log(f"Reusing verified cached artifact: {destination.name}")
    return final_url


def _store_cached_download(url: str, final_url: str, source: Path) -> None:
    payload, metadata_path = _cache_entry(url)
    payload.parent.mkdir(parents=True, exist_ok=True)
    temporary = payload.with_suffix(".part")
    shutil.copyfile(source, temporary)
    temporary.replace(payload)
    metadata_path.write_text(
        json.dumps({"final_url": final_url, "sha256": _sha256(source)}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _download(
    url: str,
    destination: Path,
    *,
    headers: dict[str, str] | None = None,
    not_found_ok: bool = False,
) -> str | None:
    """Download `url` to `destination`, returning the final (post-redirect) URL.

    If `not_found_ok` is set and the server returns HTTP 404, returns None instead
    of raising — for optional companion assets (e.g. checksum files) that upstream
    projects sometimes rename or drop between releases.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    cacheable = _cacheable_download(url, destination)
    if cacheable:
        restored = _restore_cached_download(url, destination)
        if restored is not None:
            return restored
    last_exc: Exception | None = None
    request_headers = _request_headers(headers)

    for attempt in range(1, DEFAULT_RETRIES + 1):
        req = urllib.request.Request(url, headers=request_headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                final_url = response.geturl()
                temp_path = destination.with_suffix(destination.suffix + ".part")
                with temp_path.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
                temp_path.replace(destination)
                if cacheable:
                    _store_cached_download(url, final_url, destination)
                return final_url
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and not_found_ok:
                return None
            last_exc = exc
            is_retryable = exc.code in TRANSIENT_HTTP_CODES
            if is_retryable and attempt < DEFAULT_RETRIES:
                delay = min(2 ** (attempt - 1), 5)
                _log(
                    f"Transient HTTP {exc.code} for {url}; retrying in {delay}s "
                    f"({attempt}/{DEFAULT_RETRIES})"
                )
                time.sleep(delay)
                continue
            raise StageError(f"Download failed for {url}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < DEFAULT_RETRIES:
                delay = min(2 ** (attempt - 1), 5)
                _log(
                    f"Network error for {url}: {exc.reason}; retrying in {delay}s "
                    f"({attempt}/{DEFAULT_RETRIES})"
                )
                time.sleep(delay)
                continue
            raise StageError(f"Network error while downloading {url}: {exc.reason}") from exc

    raise StageError(f"Download failed for {url}: {last_exc}")


def _urlopen_with_retry(
    req: urllib.request.Request,
    *,
    timeout: int,
    label: str,
) -> object:
    """Open a URL with the same transient retry policy as downloads."""
    last_exc: Exception | None = None
    for attempt in range(1, DEFAULT_RETRIES + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in TRANSIENT_HTTP_CODES and attempt < DEFAULT_RETRIES:
                delay = min(2 ** (attempt - 1), 5)
                _log(f"Transient HTTP {exc.code} for {label}; retrying in {delay}s ({attempt}/{DEFAULT_RETRIES})")
                time.sleep(delay)
                continue
            raise StageError(f"{label} failed: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < DEFAULT_RETRIES:
                delay = min(2 ** (attempt - 1), 5)
                _log(f"Network error for {label}: {exc.reason}; retrying in {delay}s ({attempt}/{DEFAULT_RETRIES})")
                time.sleep(delay)
                continue
            raise StageError(f"{label} network error: {exc.reason}") from exc

    raise StageError(f"{label} failed: {last_exc}")


def _download_first_available(destination: Path, urls: Iterable[str]) -> tuple[str, str]:
    errors: list[str] = []
    for url in urls:
        try:
            final_url = _download(url, destination)
            return url, final_url
        except StageError as exc:
            errors.append(str(exc))

    rendered_errors = "\n".join(f"  - {entry}" for entry in errors)
    raise StageError(
        "Failed to download from all candidate URLs:\n"
        f"{rendered_errors}"
    )


def _download_text(url: str, *, headers: dict[str, str] | None = None) -> str:
    payload_path = STAGE_ROOT / "tmp-text-response"
    _download(url, payload_path, headers=headers)
    try:
        return payload_path.read_text(encoding="utf-8")
    finally:
        payload_path.unlink(missing_ok=True)


def _fetch_json(url: str, *, headers: dict[str, str] | None = None) -> dict:
    payload_path = STAGE_ROOT / "tmp-json-response"
    final_url = _download(url, payload_path, headers=headers)
    del final_url
    try:
        return json.loads(payload_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StageError(f"Invalid JSON payload received from {url}") from exc
    finally:
        payload_path.unlink(missing_ok=True)


def _resolve_latest_release(repo: str) -> str:
    latest_url = f"https://github.com/{repo}/releases/latest"
    try:
        req = urllib.request.Request(latest_url, headers=_request_headers(), method="GET")
        with _urlopen_with_retry(req, timeout=30, label=f"{repo} latest release lookup") as response:
            redirect_target = response.geturl()
        path = urllib.parse.urlparse(redirect_target).path
        candidate = Path(path).name.strip().removeprefix("v")
        if candidate:
            return candidate
    except Exception as exc:  # noqa: BLE001
        _log(f"Falling back to GitHub API for {repo} latest release lookup: {exc}")

    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    payload = _fetch_json(api_url, headers=_github_api_headers())
    tag_name = str(payload.get("tag_name") or "").strip().removeprefix("v")
    if not tag_name:
        raise StageError(f"Unable to determine latest release tag for {repo}")
    return tag_name


def _resolve_version(requested: str | None, repo: str) -> str:
    value = (requested or "").strip()
    if value and value != "latest":
        return value
    return _resolve_latest_release(repo)


# gdu and dust are pinned, not "latest": estate freshness rule = the newest release that is at least
# 14 days old when pinned (gdu 5.38.0 released 2026-10-05, dust 1.2.6 released 2026-09-16; pinned 2026-10-21).
# Bumping needs a deliberate edit here, never a silent float.
GDU_PINNED_VERSION = "5.38.0"
DUST_PINNED_VERSION = "1.2.6"
# dust publishes no checksum file or signature (assets are bare tarballs), so its sha256 is pinned here per
# version. It was computed over the downloaded asset and equals the digest GitHub itself records for it
# (`gh api repos/bootandy/dust/releases/tags/v1.2.6`). A new dust version without an entry here fails staging.
DUST_PINNED_SHA256 = {
    "1.2.6": "cbf2cb5c9d6227e46fc4b29c03dc43b8632020dfc5cf269d263d6e29c399e044",
}


def _resolve_pinned_version(requested: str | None, default: str, label: str) -> str:
    value = (requested or "").strip()
    if value == "latest":
        raise StageError(
            f"{label}: 'latest' is not accepted; it is pinned to a release at least 14 days old. "
            "Set an explicit version or edit the pin in stage_tool_artifacts.py"
        )
    return value or default


def _resolve_npm_version(requested: str | None, package_name: str) -> str:
    value = (requested or "").strip()
    if value and value != "latest":
        return value

    payload = _fetch_json(f"https://registry.npmjs.org/{package_name}/latest")
    version = str(payload.get("version") or "").strip()
    if not version:
        raise StageError(f"Unable to determine latest npm version for {package_name}")
    if not NPM_VERSION_RE.fullmatch(version):
        _log(f"npm registry returned non-semver version for {package_name}: {version}")
    return version


def _resolve_pypi_version(requested: str | None, package_name: str) -> str:
    value = (requested or "").strip()
    if value and value != "latest":
        return value

    payload = _fetch_json(f"https://pypi.org/pypi/{package_name}/json")
    version = str(payload.get("info", {}).get("version") or "").strip()
    if not version:
        raise StageError(f"Unable to determine latest PyPI version for {package_name}")
    return version


def _normalize_codex_version(value: str) -> str:
    normalized = value.strip().removeprefix("rust-v").removeprefix("v")
    if not normalized:
        raise StageError(f"Invalid Codex release version: {value!r}")
    return normalized


def _resolve_codex_version(requested: str | None) -> str:
    value = (requested or "").strip()
    if value and value != "latest":
        return _normalize_codex_version(value)

    latest_tag = _resolve_latest_release("openai/codex")
    return _normalize_codex_version(latest_tag)


CLAUDE_CODE_NPM_PACKAGE = "@anthropic-ai/claude-code"


def install_enabled(name: str) -> bool:
    """An INSTALL_* build switch: unset means enabled. The single definition shared with
    resolve-devcontainers-release.py (the in-image installer cannot import it, and a test pins
    that its own `is_enabled` agrees on every value)."""
    value = os.getenv(name)
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _resolve_pinned_npm_version(requested: str | None, package: str, variable: str) -> str:
    """The exact version of an npm-installed, user-updatable tool the image ships and records.

    Such a tool follows upstream (no freshness window): the npm `latest` dist-tag, or the
    explicit pin when one is given. The EXACT version is returned and recorded in
    tool-versions.env and the image manifest; a pin must be a published release.
    """
    value = (requested or "").strip()
    if not value or value == "latest":
        return _resolve_npm_version(None, package)
    if not CLAUDE_VERSION_RE.fullmatch(value):
        raise StageError(f"Unsupported {variable} value: {value!r}")
    try:
        document = _fetch_json(f"https://registry.npmjs.org/{package}/{value}")
    except StageError as exc:
        raise StageError(f"{variable} {value} is not a published {package} release ({exc})") from exc
    if document.get("version") != value:
        raise StageError(f"{variable} {value} is not a published {package} release")
    return value


def _resolve_claude_code_version(requested: str | None) -> str:
    return _resolve_pinned_npm_version(requested, CLAUDE_CODE_NPM_PACKAGE, "CLAUDE_CODE_VERSION")


def _tar_contains_binary(archive: Path, binary_name: str) -> bool:
    with tarfile.open(archive, mode="r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            if Path(member.name).name == binary_name:
                return True
    return False


def _zip_contains_binary(archive: Path, binary_name: str) -> bool:
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            if Path(name).name == binary_name:
                return True
    return False


def _zip_contains_member(archive: Path, member_path: str) -> bool:
    with zipfile.ZipFile(archive) as zf:
        return member_path in zf.namelist()


def _extract_awscli_version_from_archive(archive: Path) -> str | None:
    """Extract concrete AWS CLI version from a downloaded archive.

    Preferred strategy is executing the bundled binary from the unpacked
    archive and parsing the `aws-cli/X.Y.Z` prefix from its --version output.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="awscli-version-") as tmp_dir:
            extract_root = Path(tmp_dir)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extract_root)

            aws_binary = extract_root / "aws" / "dist" / "aws"
            if not aws_binary.exists():
                return None

            aws_binary.chmod(0o755)
            result = subprocess.run(
                [str(aws_binary), "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            output = " ".join(
                part.strip() for part in (result.stdout, result.stderr) if part.strip()
            )
            if not output:
                return None
            match = AWSCLI_VERSION_RE.search(output)
            if match:
                return match.group("version")
    except Exception as exc:  # noqa: BLE001
        _log(f"Failed to extract AWS CLI version by executing bundled binary: {exc}")

    return None


def _extract_awscli_version_from_url(url: str) -> str | None:
    match = re.search(r"awscli-exe-linux-x86_64-(?P<version>[0-9][0-9a-zA-Z.+-]*)\.zip", url)
    if not match:
        return None
    return match.group("version")


def _parse_b2_sha256(hash_file: Path) -> str:
    for raw_line in hash_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() == "sha256":
            return parts[1]
    raise StageError("Unable to parse sha256 from b2 hash file")


def _parse_hashicorp_sha256(sums_file: Path, expected_file_name: str) -> str:
    for raw_line in sums_file.read_text(encoding="utf-8").splitlines():
        parts = raw_line.split()
        if len(parts) < 2:
            continue
        checksum, name = parts[0], parts[1]
        if name.lstrip("*") == expected_file_name:
            return checksum.lower()
    raise StageError(f"Unable to find checksum entry for {expected_file_name}")


def _parse_github_asset_sha256(release: dict, expected_asset_name: str) -> str:
    """Return the GitHub-computed digest for one immutable release asset."""
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise StageError("GitHub release payload missing 'assets' list")

    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != expected_asset_name:
            continue
        digest = str(asset.get("digest") or "").strip().lower()
        if not digest.startswith("sha256:"):
            raise StageError(
                f"GitHub release asset {expected_asset_name} has no sha256 digest"
            )
        checksum = digest.removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise StageError(
                f"GitHub release asset {expected_asset_name} has invalid digest {digest!r}"
            )
        return checksum

    raise StageError(f"GitHub release does not contain asset {expected_asset_name}")


def _parse_antigravity_manifest(payload: dict) -> tuple[str, str, str]:
    version = str(payload.get("version") or "").strip()
    url = str(payload.get("url") or "").strip()
    sha512 = str(payload.get("sha512") or "").strip().lower()

    if not version:
        raise StageError("Antigravity manifest missing version")
    if not url.startswith("https://"):
        raise StageError("Antigravity manifest missing secure download URL")
    if not re.fullmatch(r"[0-9a-f]{128}", sha512):
        raise StageError("Antigravity manifest has invalid sha512 digest")

    return version, url, sha512


def _record_artifact(
    records: list[StagedArtifact],
    *,
    tool: str,
    version: str,
    source_url: str,
    final_url: str,
    path: Path,
    kind: str,
    verification: str,
) -> None:
    records.append(
        StagedArtifact(
            tool=tool,
            version=version,
            source_url=source_url,
            final_url=final_url,
            file_name=path.name,
            sha256=_sha256(path),
            kind=kind,
            verification=verification,
        )
    )


def _stage_b2(version: str, records: list[StagedArtifact]) -> None:
    binary_path = DOWNLOADS_DIR / f"b2-{version}-linux"
    hash_path = DOWNLOADS_DIR / f"b2-{version}-linux_hashes.txt"

    binary_url = f"https://github.com/Backblaze/B2_Command_Line_Tool/releases/download/v{version}/b2-linux"
    hash_url = (
        "https://github.com/Backblaze/B2_Command_Line_Tool/"
        f"releases/download/v{version}/b2-linux_hashes.txt"
    )

    binary_final_url = _download(binary_url, binary_path)
    hash_final_url = _download(hash_url, hash_path)

    expected_sha256 = _parse_b2_sha256(hash_path)
    actual_sha256 = _sha256(binary_path)
    if actual_sha256 != expected_sha256:
        raise StageError(
            f"B2 checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
        )

    _record_artifact(
        records,
        tool="b2",
        version=version,
        source_url=binary_url,
        final_url=binary_final_url,
        path=binary_path,
        kind="binary",
        verification="sha256 from b2-linux_hashes.txt",
    )
    _record_artifact(
        records,
        tool="b2-hashes",
        version=version,
        source_url=hash_url,
        final_url=hash_final_url,
        path=hash_path,
        kind="checksum-file",
        verification="downloaded",
    )


def _stage_tarball_tool(
    *,
    tool: str,
    version: str,
    destination: Path,
    urls: list[str],
    expected_binary: str,
    archive_kind: str,
    expected_companion_binaries: tuple[str, ...] = (),
    records: list[StagedArtifact],
) -> None:
    source_url, final_url = _download_first_available(destination, urls)
    if not _tar_contains_binary(destination, expected_binary):
        raise StageError(
            f"Downloaded {tool} archive does not contain expected binary '{expected_binary}'"
        )
    for companion in expected_companion_binaries:
        if not _tar_contains_binary(destination, companion):
            raise StageError(
                f"Downloaded {tool} archive does not contain expected companion "
                f"binary '{companion}'"
            )

    verified_binaries = (expected_binary, *expected_companion_binaries)
    _record_artifact(
        records,
        tool=tool,
        version=version,
        source_url=source_url,
        final_url=final_url,
        path=destination,
        kind=archive_kind,
        verification="archive contains " + ", ".join(verified_binaries),
    )


def _stage_source_tarball(
    *,
    tool: str,
    version: str,
    destination: Path,
    urls: list[str],
    records: list[StagedArtifact],
) -> None:
    source_url, final_url = _download_first_available(destination, urls)
    _record_artifact(
        records,
        tool=tool,
        version=version,
        source_url=source_url,
        final_url=final_url,
        path=destination,
        kind="source-tarball",
        verification="GitHub source archive for build-time compilation",
    )


def _stage_neovim(version: str, records: list[StagedArtifact]) -> None:
    archive_name = "nvim-linux-x86_64.tar.gz"
    base_url = f"https://github.com/neovim/neovim/releases/download/v{version}"
    archive_url = f"{base_url}/{archive_name}"
    archive_path = DOWNLOADS_DIR / f"nvim-{version}.tar.gz"

    archive_final_url = _download(archive_url, archive_path)
    if not _tar_contains_binary(archive_path, "nvim"):
        raise StageError("Downloaded Neovim archive does not contain nvim binary")

    _record_artifact(
        records,
        tool="nvim",
        version=version,
        source_url=archive_url,
        final_url=archive_final_url,
        path=archive_path,
        kind="tar.gz",
        verification="archive contains nvim",
    )


def _stage_nvchad(version: str, records: list[StagedArtifact]) -> None:
    archive_name = f"v{version}.tar.gz"
    archive_path = DOWNLOADS_DIR / f"nvchad-{version}.tar.gz"
    urls = [
        f"https://github.com/NvChad/NvChad/archive/refs/tags/{archive_name}",
        f"https://github.com/NvChad/NvChad/archive/refs/tags/{version}.tar.gz",
    ]

    _stage_source_tarball(
        tool="nvchad",
        version=version,
        destination=archive_path,
        urls=urls,
        records=records,
    )


def _stage_zip_tool(
    *,
    tool: str,
    version: str,
    destination: Path,
    url: str,
    expected_binary: str,
    verification: str,
    records: list[StagedArtifact],
) -> None:
    final_url = _download(url, destination)
    if not _zip_contains_binary(destination, expected_binary):
        raise StageError(
            f"Downloaded {tool} archive does not contain expected binary '{expected_binary}'"
        )

    _record_artifact(
        records,
        tool=tool,
        version=version,
        source_url=url,
        final_url=final_url,
        path=destination,
        kind="zip",
        verification=verification,
    )


def _stage_antigravity(requested: str, records: list[StagedArtifact]) -> str:
    requested_normalized = (requested or "").strip()
    if requested_normalized and requested_normalized != "latest":
        _log(
            "ANTIGRAVITY_VERSION currently supports only 'latest'; "
            f"ignoring requested value {requested_normalized!r}"
        )

    manifest_path = DOWNLOADS_DIR / "antigravity-linux-amd64-manifest.json"
    manifest_final_url = _download(ANTIGRAVITY_MANIFEST_LINUX_AMD64_URL, manifest_path)
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StageError("Invalid Antigravity manifest JSON payload") from exc

    version, archive_url, expected_sha512 = _parse_antigravity_manifest(manifest_payload)
    archive_path = DOWNLOADS_DIR / f"antigravity-{version}.tar.gz"
    archive_final_url = _download(archive_url, archive_path)

    actual_sha512 = _sha512(archive_path)
    if expected_sha512 != actual_sha512:
        raise StageError(
            f"Antigravity checksum mismatch: expected {expected_sha512}, got {actual_sha512}"
        )

    if not _tar_contains_binary(archive_path, "antigravity"):
        raise StageError("Downloaded Antigravity archive does not contain antigravity binary")

    _record_artifact(
        records,
        tool="antigravity",
        version=version,
        source_url=archive_url,
        final_url=archive_final_url,
        path=archive_path,
        kind="tar.gz",
        verification="sha512 from linux_amd64 manifest + archive contains antigravity",
    )
    _record_artifact(
        records,
        tool="antigravity-manifest",
        version=version,
        source_url=ANTIGRAVITY_MANIFEST_LINUX_AMD64_URL,
        final_url=manifest_final_url,
        path=manifest_path,
        kind="manifest",
        verification="downloaded",
    )

    return version


def _stage_crane(version: str, records: list[StagedArtifact]) -> None:
    archive_name = "go-containerregistry_Linux_x86_64.tar.gz"
    base_url = (
        "https://github.com/google/go-containerregistry/releases/download/"
        f"v{version}"
    )
    archive_url = f"{base_url}/{archive_name}"
    checksums_url = f"{base_url}/checksums.txt"
    archive_path = DOWNLOADS_DIR / f"crane-{version}.tar.gz"
    checksums_path = DOWNLOADS_DIR / f"crane-{version}-checksums.txt"

    checksums_final_url = _download(checksums_url, checksums_path)
    archive_final_url = _download(archive_url, archive_path)
    expected_sha256 = _parse_hashicorp_sha256(checksums_path, archive_name)
    actual_sha256 = _sha256(archive_path)
    if expected_sha256 != actual_sha256:
        raise StageError(
            f"crane checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    if not _tar_contains_binary(archive_path, "crane"):
        raise StageError("Downloaded go-containerregistry archive does not contain crane")

    _record_artifact(
        records,
        tool="crane",
        version=version,
        source_url=archive_url,
        final_url=archive_final_url,
        path=archive_path,
        kind="tar.gz",
        verification="sha256 from upstream checksums.txt + archive contains crane",
    )
    _record_artifact(
        records,
        tool="crane-checksums",
        version=version,
        source_url=checksums_url,
        final_url=checksums_final_url,
        path=checksums_path,
        kind="checksum-file",
        verification="downloaded",
    )


def _stage_regctl(version: str, records: list[StagedArtifact]) -> None:
    asset_name = "regctl-linux-amd64"
    base_url = f"https://github.com/regclient/regclient/releases/download/v{version}"
    binary_url = f"{base_url}/{asset_name}"
    binary_path = DOWNLOADS_DIR / f"regctl-{version}-linux-amd64"
    checksum_path = DOWNLOADS_DIR / f"regctl-{version}.sha256"

    release_url = (
        "https://api.github.com/repos/regclient/regclient/releases/tags/"
        f"v{version}"
    )
    release = _fetch_json(release_url, headers=_github_api_headers())
    expected_sha256 = _parse_github_asset_sha256(release, asset_name)
    binary_final_url = _download(binary_url, binary_path)
    actual_sha256 = _sha256(binary_path)
    if expected_sha256 != actual_sha256:
        raise StageError(
            f"regctl checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
        )

    # regclient does not publish a checksum-file release asset. Preserve the
    # GitHub-computed asset digest as a sidecar so the Dockerfile can repeat the
    # check without network access after the staging gate has succeeded.
    checksum_path.write_text(
        f"{expected_sha256}  {binary_path.name}\n",
        encoding="utf-8",
    )

    _record_artifact(
        records,
        tool="regctl",
        version=version,
        source_url=binary_url,
        final_url=binary_final_url,
        path=binary_path,
        kind="binary",
        verification="sha256 from GitHub release asset digest",
    )


def _stage_gdu(version: str, records: list[StagedArtifact]) -> None:
    # Static linux/amd64 build; upstream publishes sha256sums.txt (+ a detached .asc signature).
    asset_name = "gdu_linux_amd64_static.tgz"
    base_url = f"https://github.com/dundee/gdu/releases/download/v{version}"
    archive_url = f"{base_url}/{asset_name}"
    sums_url = f"{base_url}/sha256sums.txt"
    archive_path = DOWNLOADS_DIR / f"gdu-{version}.tgz"
    sums_path = DOWNLOADS_DIR / f"gdu-{version}-sha256sums.txt"

    sums_final_url = _download(sums_url, sums_path)
    archive_final_url = _download(archive_url, archive_path)
    expected_sha256 = _parse_hashicorp_sha256(sums_path, asset_name)
    actual_sha256 = _sha256(archive_path)
    if expected_sha256 != actual_sha256:
        raise StageError(f"gdu checksum mismatch: expected {expected_sha256}, got {actual_sha256}")
    if not _tar_contains_binary(archive_path, "gdu_linux_amd64_static"):
        raise StageError("Downloaded gdu archive does not contain gdu_linux_amd64_static")

    _record_artifact(
        records,
        tool="gdu",
        version=version,
        source_url=archive_url,
        final_url=archive_final_url,
        path=archive_path,
        kind="tar.gz",
        verification="sha256 from upstream sha256sums.txt + archive contains gdu_linux_amd64_static",
    )
    _record_artifact(
        records,
        tool="gdu-checksums",
        version=version,
        source_url=sums_url,
        final_url=sums_final_url,
        path=sums_path,
        kind="checksum-file",
        verification="downloaded",
    )


def _stage_dust(version: str, records: list[StagedArtifact]) -> None:
    # Static (musl) linux/amd64 build. Upstream publishes no checksum file, so the sha256 is pinned in
    # DUST_PINNED_SHA256 and written to an offline sidecar for the Dockerfile to repeat the check.
    expected_sha256 = DUST_PINNED_SHA256.get(version)
    if expected_sha256 is None:
        raise StageError(
            f"dust {version} has no pinned sha256 (upstream publishes no checksum file); "
            "add the verified digest to DUST_PINNED_SHA256 in stage_tool_artifacts.py"
        )
    asset_name = f"dust-v{version}-x86_64-unknown-linux-musl.tar.gz"
    archive_url = f"https://github.com/bootandy/dust/releases/download/v{version}/{asset_name}"
    archive_path = DOWNLOADS_DIR / f"dust-{version}.tar.gz"
    sidecar_path = DOWNLOADS_DIR / f"dust-{version}.sha256"

    archive_final_url = _download(archive_url, archive_path)
    actual_sha256 = _sha256(archive_path)
    if expected_sha256 != actual_sha256:
        raise StageError(f"dust checksum mismatch: expected {expected_sha256}, got {actual_sha256}")
    if not _tar_contains_binary(archive_path, "dust"):
        raise StageError("Downloaded dust archive does not contain dust")
    sidecar_path.write_text(f"{expected_sha256}  {archive_path.name}\n", encoding="utf-8")

    _record_artifact(
        records,
        tool="dust",
        version=version,
        source_url=archive_url,
        final_url=archive_final_url,
        path=archive_path,
        kind="tar.gz",
        verification="sha256 pinned in stage_tool_artifacts.py (upstream publishes no checksum) + archive contains dust",
    )


def _stage_tools(resolved: dict[str, str]) -> list[StagedArtifact]:
    records: list[StagedArtifact] = []

    resolved["ANTIGRAVITY_VER"] = _stage_antigravity(resolved["ANTIGRAVITY_VER"], records)

    _stage_b2(resolved["B2_VER"], records)
    _stage_crane(resolved["CRANE_VER"], records)
    _stage_regctl(resolved["REGCTL_VER"], records)
    _stage_gdu(resolved["GDU_VER"], records)
    _stage_dust(resolved["DUST_VER"], records)

    _stage_tarball_tool(
        tool="bat",
        version=resolved["BAT_VER"],
        destination=DOWNLOADS_DIR / f"bat-{resolved['BAT_VER']}.tar.gz",
        urls=[
            f"https://github.com/sharkdp/bat/releases/download/v{resolved['BAT_VER']}/bat-v{resolved['BAT_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/sharkdp/bat/releases/download/v{resolved['BAT_VER']}/bat-{resolved['BAT_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/sharkdp/bat/releases/download/v{resolved['BAT_VER']}/bat-v{resolved['BAT_VER']}-x86_64-unknown-linux-musl.tar.gz",
            f"https://github.com/sharkdp/bat/releases/download/v{resolved['BAT_VER']}/bat-{resolved['BAT_VER']}-x86_64-unknown-linux-musl.tar.gz",
        ],
        expected_binary="bat",
        archive_kind="tar.gz",
        records=records,
    )

    _stage_tarball_tool(
        tool="delta",
        version=resolved["DELTA_VER"],
        destination=DOWNLOADS_DIR / f"delta-{resolved['DELTA_VER']}.tar.gz",
        urls=[
            f"https://github.com/dandavison/delta/releases/download/{resolved['DELTA_VER']}/delta-{resolved['DELTA_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/dandavison/delta/releases/download/v{resolved['DELTA_VER']}/delta-{resolved['DELTA_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/dandavison/delta/releases/download/{resolved['DELTA_VER']}/delta-{resolved['DELTA_VER']}-x86_64-unknown-linux-musl.tar.gz",
            f"https://github.com/dandavison/delta/releases/download/v{resolved['DELTA_VER']}/delta-{resolved['DELTA_VER']}-x86_64-unknown-linux-musl.tar.gz",
        ],
        expected_binary="delta",
        archive_kind="tar.gz",
        records=records,
    )

    _stage_tarball_tool(
        tool="fd",
        version=resolved["FD_VER"],
        destination=DOWNLOADS_DIR / f"fd-{resolved['FD_VER']}.tar.gz",
        urls=[
            f"https://github.com/sharkdp/fd/releases/download/v{resolved['FD_VER']}/fd-v{resolved['FD_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/sharkdp/fd/releases/download/v{resolved['FD_VER']}/fd-{resolved['FD_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/sharkdp/fd/releases/download/v{resolved['FD_VER']}/fd-v{resolved['FD_VER']}-x86_64-unknown-linux-musl.tar.gz",
            f"https://github.com/sharkdp/fd/releases/download/v{resolved['FD_VER']}/fd-{resolved['FD_VER']}-x86_64-unknown-linux-musl.tar.gz",
        ],
        expected_binary="fd",
        archive_kind="tar.gz",
        records=records,
    )

    _stage_tarball_tool(
        tool="rga",
        version=resolved["RGA_VER"],
        destination=DOWNLOADS_DIR / f"rga-{resolved['RGA_VER']}.tar.gz",
        urls=[
            f"https://github.com/phiresky/ripgrep-all/releases/download/{resolved['RGA_VER']}/ripgrep_all-v{resolved['RGA_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/phiresky/ripgrep-all/releases/download/v{resolved['RGA_VER']}/ripgrep_all-v{resolved['RGA_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/phiresky/ripgrep-all/releases/download/{resolved['RGA_VER']}/ripgrep_all-v{resolved['RGA_VER']}-x86_64-unknown-linux-musl.tar.gz",
            f"https://github.com/phiresky/ripgrep-all/releases/download/v{resolved['RGA_VER']}/ripgrep_all-v{resolved['RGA_VER']}-x86_64-unknown-linux-musl.tar.gz",
            f"https://github.com/phiresky/ripgrep-all/releases/download/{resolved['RGA_VER']}/ripgrep_all-{resolved['RGA_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/phiresky/ripgrep-all/releases/download/v{resolved['RGA_VER']}/ripgrep_all-{resolved['RGA_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/phiresky/ripgrep-all/releases/download/{resolved['RGA_VER']}/ripgrep_all-{resolved['RGA_VER']}-x86_64-unknown-linux-musl.tar.gz",
            f"https://github.com/phiresky/ripgrep-all/releases/download/v{resolved['RGA_VER']}/ripgrep_all-{resolved['RGA_VER']}-x86_64-unknown-linux-musl.tar.gz",
        ],
        expected_binary="rga",
        expected_companion_binaries=("rga-fzf", "rga-fzf-open", "rga-preproc"),
        archive_kind="tar.gz",
        records=records,
    )

    _stage_tarball_tool(
        tool="ripgrep",
        version=resolved["RIPGREP_VER"],
        destination=DOWNLOADS_DIR / f"ripgrep-{resolved['RIPGREP_VER']}.tar.gz",
        urls=[
            f"https://github.com/BurntSushi/ripgrep/releases/download/{resolved['RIPGREP_VER']}/ripgrep-{resolved['RIPGREP_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/BurntSushi/ripgrep/releases/download/v{resolved['RIPGREP_VER']}/ripgrep-{resolved['RIPGREP_VER']}-x86_64-unknown-linux-gnu.tar.gz",
            f"https://github.com/BurntSushi/ripgrep/releases/download/{resolved['RIPGREP_VER']}/ripgrep-{resolved['RIPGREP_VER']}-x86_64-unknown-linux-musl.tar.gz",
            f"https://github.com/BurntSushi/ripgrep/releases/download/v{resolved['RIPGREP_VER']}/ripgrep-{resolved['RIPGREP_VER']}-x86_64-unknown-linux-musl.tar.gz",
        ],
        expected_binary="rg",
        archive_kind="tar.gz",
        records=records,
    )

    shellcheck_path = DOWNLOADS_DIR / f"shellcheck-{resolved['SHELLCHECK_VER']}.tar.xz"
    shellcheck_url = (
        "https://github.com/koalaman/shellcheck/releases/download/"
        f"v{resolved['SHELLCHECK_VER']}/shellcheck-v{resolved['SHELLCHECK_VER']}.linux.x86_64.tar.xz"
    )
    shellcheck_final_url = _download(shellcheck_url, shellcheck_path)
    if not _tar_contains_binary(shellcheck_path, "shellcheck"):
        raise StageError("Downloaded shellcheck archive does not contain shellcheck binary")
    _record_artifact(
        records,
        tool="shellcheck",
        version=resolved["SHELLCHECK_VER"],
        source_url=shellcheck_url,
        final_url=shellcheck_final_url,
        path=shellcheck_path,
        kind="tar.xz",
        verification="archive contains shellcheck",
    )

    _stage_tarball_tool(
        tool="fzf",
        version=resolved["FZF_VER"],
        destination=DOWNLOADS_DIR / f"fzf-{resolved['FZF_VER']}.tar.gz",
        urls=[
            f"https://github.com/junegunn/fzf/releases/download/v{resolved['FZF_VER']}/fzf-{resolved['FZF_VER']}-linux_amd64.tar.gz",
        ],
        expected_binary="fzf",
        archive_kind="tar.gz",
        records=records,
    )

    _stage_zip_tool(
        tool="lnav",
        version=resolved["LNAV_VER"],
        destination=DOWNLOADS_DIR / f"lnav-{resolved['LNAV_VER']}-linux-musl-x86_64.zip",
        url=(
            "https://github.com/tstack/lnav/releases/download/"
            f"v{resolved['LNAV_VER']}/lnav-{resolved['LNAV_VER']}-linux-musl-x86_64.zip"
        ),
        expected_binary="lnav",
        verification="archive contains lnav",
        records=records,
    )

    yq_path = DOWNLOADS_DIR / f"yq-{resolved['YQ_VER']}-linux_amd64"
    yq_url = f"https://github.com/mikefarah/yq/releases/download/v{resolved['YQ_VER']}/yq_linux_amd64"
    yq_final_url = _download(yq_url, yq_path)
    _record_artifact(
        records,
        tool="yq",
        version=resolved["YQ_VER"],
        source_url=yq_url,
        final_url=yq_final_url,
        path=yq_path,
        kind="binary",
        verification="downloaded",
    )

    _stage_tarball_tool(
        tool="gh",
        version=resolved["GH_VER"],
        destination=DOWNLOADS_DIR / f"gh-{resolved['GH_VER']}.tar.gz",
        urls=[
            f"https://github.com/cli/cli/releases/download/v{resolved['GH_VER']}/gh_{resolved['GH_VER']}_linux_amd64.tar.gz",
        ],
        expected_binary="gh",
        archive_kind="tar.gz",
        records=records,
    )

    _stage_neovim(resolved["NVIM_VER"], records)
    _stage_nvchad(resolved["NVCHAD_VER"], records)

    _stage_source_tarball(
        tool="htop",
        version=resolved["HTOP_VER"],
        destination=DOWNLOADS_DIR / f"htop-{resolved['HTOP_VER']}.tar.gz",
        urls=[
            f"https://github.com/htop-dev/htop/archive/refs/tags/{resolved['HTOP_VER']}.tar.gz",
            f"https://github.com/htop-dev/htop/archive/refs/tags/v{resolved['HTOP_VER']}.tar.gz",
        ],
        records=records,
    )

    # grpcurl — gRPC inspection client (SkyWalking/OTel speak gRPC). Published by
    # fullstorydev/grpcurl as a tarball containing the `grpcurl` binary at top level.
    _stage_tarball_tool(
        tool="grpcurl",
        version=resolved["GRPCURL_VER"],
        destination=DOWNLOADS_DIR / f"grpcurl-{resolved['GRPCURL_VER']}.tar.gz",
        urls=[
            f"https://github.com/fullstorydev/grpcurl/releases/download/v{resolved['GRPCURL_VER']}/grpcurl_{resolved['GRPCURL_VER']}_linux_x86_64.tar.gz",
        ],
        expected_binary="grpcurl",
        archive_kind="tar.gz",
        records=records,
    )

    consul_zip_name = f"consul_{resolved['CONSUL_VER']}_linux_amd64.zip"
    consul_zip_path = DOWNLOADS_DIR / consul_zip_name
    consul_sums_path = DOWNLOADS_DIR / f"consul_{resolved['CONSUL_VER']}_SHA256SUMS"
    consul_sums_url = f"https://releases.hashicorp.com/consul/{resolved['CONSUL_VER']}/consul_{resolved['CONSUL_VER']}_SHA256SUMS"
    consul_zip_url = f"https://releases.hashicorp.com/consul/{resolved['CONSUL_VER']}/{consul_zip_name}"
    consul_sums_final = _download(consul_sums_url, consul_sums_path)
    consul_zip_final = _download(consul_zip_url, consul_zip_path)
    consul_expected_sha = _parse_hashicorp_sha256(consul_sums_path, consul_zip_name)
    consul_actual_sha = _sha256(consul_zip_path)
    if consul_expected_sha != consul_actual_sha:
        raise StageError(
            f"Consul checksum mismatch: expected {consul_expected_sha}, got {consul_actual_sha}"
        )
    if not _zip_contains_binary(consul_zip_path, "consul"):
        raise StageError("Downloaded consul zip does not contain consul binary")
    _record_artifact(
        records,
        tool="consul",
        version=resolved["CONSUL_VER"],
        source_url=consul_zip_url,
        final_url=consul_zip_final,
        path=consul_zip_path,
        kind="zip",
        verification="sha256 from hashicorp sums + archive contains consul",
    )
    _record_artifact(
        records,
        tool="consul-sha256sums",
        version=resolved["CONSUL_VER"],
        source_url=consul_sums_url,
        final_url=consul_sums_final,
        path=consul_sums_path,
        kind="checksum-file",
        verification="downloaded",
    )

    vault_zip_name = f"vault_{resolved['VAULT_VER']}_linux_amd64.zip"
    vault_zip_path = DOWNLOADS_DIR / vault_zip_name
    vault_sums_path = DOWNLOADS_DIR / f"vault_{resolved['VAULT_VER']}_SHA256SUMS"
    vault_sums_url = f"https://releases.hashicorp.com/vault/{resolved['VAULT_VER']}/vault_{resolved['VAULT_VER']}_SHA256SUMS"
    vault_zip_url = f"https://releases.hashicorp.com/vault/{resolved['VAULT_VER']}/{vault_zip_name}"
    vault_sums_final = _download(vault_sums_url, vault_sums_path)
    vault_zip_final = _download(vault_zip_url, vault_zip_path)
    vault_expected_sha = _parse_hashicorp_sha256(vault_sums_path, vault_zip_name)
    vault_actual_sha = _sha256(vault_zip_path)
    if vault_expected_sha != vault_actual_sha:
        raise StageError(
            f"Vault checksum mismatch: expected {vault_expected_sha}, got {vault_actual_sha}"
        )
    if not _zip_contains_binary(vault_zip_path, "vault"):
        raise StageError("Downloaded vault zip does not contain vault binary")
    _record_artifact(
        records,
        tool="vault",
        version=resolved["VAULT_VER"],
        source_url=vault_zip_url,
        final_url=vault_zip_final,
        path=vault_zip_path,
        kind="zip",
        verification="sha256 from hashicorp sums + archive contains vault",
    )
    _record_artifact(
        records,
        tool="vault-sha256sums",
        version=resolved["VAULT_VER"],
        source_url=vault_sums_url,
        final_url=vault_sums_final,
        path=vault_sums_path,
        kind="checksum-file",
        verification="downloaded",
    )

    # lazydocker — terminal UI for Docker (jesseduffield/lazydocker).
    # Asset: lazydocker_<ver>_Linux_<arch>.tar.gz + checksums.txt (space-space BSD format).
    # Arch mapping mirrors the Dockerfile case statement: x86_64 → x86_64, aarch64 → arm64.
    lazydocker_ver = resolved["LAZYDOCKER_VER"]
    lazydocker_arch = "x86_64"  # staging always runs on the build host (amd64)
    lazydocker_archive_name = f"lazydocker_{lazydocker_ver}_Linux_{lazydocker_arch}.tar.gz"
    lazydocker_archive_path = DOWNLOADS_DIR / f"lazydocker-{lazydocker_ver}.tar.gz"
    lazydocker_sums_path = DOWNLOADS_DIR / f"lazydocker-{lazydocker_ver}-checksums.txt"
    lazydocker_base_url = f"https://github.com/jesseduffield/lazydocker/releases/download/v{lazydocker_ver}"
    lazydocker_sums_url = f"{lazydocker_base_url}/checksums.txt"
    lazydocker_archive_url = f"{lazydocker_base_url}/{lazydocker_archive_name}"
    _download(lazydocker_sums_url, lazydocker_sums_path)
    lazydocker_archive_final_url = _download(lazydocker_archive_url, lazydocker_archive_path)
    lazydocker_expected_sha = _parse_hashicorp_sha256(lazydocker_sums_path, lazydocker_archive_name)
    lazydocker_actual_sha = _sha256(lazydocker_archive_path)
    if lazydocker_expected_sha != lazydocker_actual_sha:
        raise StageError(
            f"lazydocker checksum mismatch: expected {lazydocker_expected_sha}, "
            f"got {lazydocker_actual_sha}"
        )
    if not _tar_contains_binary(lazydocker_archive_path, "lazydocker"):
        raise StageError("Downloaded lazydocker archive does not contain lazydocker binary")
    _record_artifact(
        records,
        tool="lazydocker",
        version=lazydocker_ver,
        source_url=lazydocker_archive_url,
        final_url=lazydocker_archive_final_url,
        path=lazydocker_archive_path,
        kind="tar.gz",
        verification="sha256 from checksums.txt + archive contains lazydocker",
    )
    _record_artifact(
        records,
        tool="lazydocker-checksums",
        version=lazydocker_ver,
        source_url=lazydocker_sums_url,
        final_url=lazydocker_sums_url,
        path=lazydocker_sums_path,
        kind="checksum-file",
        verification="downloaded",
    )

    # dive — Docker image layer explorer (wagoodman/dive).
    # Asset: dive_<ver>_linux_<arch>.deb + dive_<ver>_checksums.txt (space-space format).
    # Arch: dpkg --print-architecture style (amd64 / arm64) matches the deb filename directly.
    dive_ver = resolved["DIVE_VER"]
    dive_arch = "amd64"  # staging always runs on the build host (amd64)
    dive_deb_name = f"dive_{dive_ver}_linux_{dive_arch}.deb"
    dive_deb_path = DOWNLOADS_DIR / dive_deb_name
    dive_sums_path = DOWNLOADS_DIR / f"dive-{dive_ver}-checksums.txt"
    dive_base_url = f"https://github.com/wagoodman/dive/releases/download/v{dive_ver}"
    dive_sums_url = f"{dive_base_url}/dive_{dive_ver}_checksums.txt"
    dive_deb_url = f"{dive_base_url}/{dive_deb_name}"
    _download(dive_sums_url, dive_sums_path)
    dive_deb_final_url = _download(dive_deb_url, dive_deb_path)
    dive_expected_sha = _parse_hashicorp_sha256(dive_sums_path, dive_deb_name)
    dive_actual_sha = _sha256(dive_deb_path)
    if dive_expected_sha != dive_actual_sha:
        raise StageError(
            f"dive checksum mismatch: expected {dive_expected_sha}, got {dive_actual_sha}"
        )
    _record_artifact(
        records,
        tool="dive",
        version=dive_ver,
        source_url=dive_deb_url,
        final_url=dive_deb_final_url,
        path=dive_deb_path,
        kind="deb",
        verification="sha256 from dive_<ver>_checksums.txt + dpkg deb",
    )
    _record_artifact(
        records,
        tool="dive-checksums",
        version=dive_ver,
        source_url=dive_sums_url,
        final_url=dive_sums_url,
        path=dive_sums_path,
        kind="checksum-file",
        verification="downloaded",
    )

    # syft — SBOM / software composition analysis (anchore/syft).
    # Asset: syft_<ver>_linux_<arch>.tar.gz + syft_<ver>_checksums.txt (space-space format).
    # Replaces the insecure `get.anchore.io | sh` pipe-to-shell installer.
    syft_ver = resolved["SYFT_VER"]
    syft_arch = "amd64"  # staging always runs on the build host (amd64)
    syft_archive_name = f"syft_{syft_ver}_linux_{syft_arch}.tar.gz"
    syft_archive_path = DOWNLOADS_DIR / f"syft-{syft_ver}.tar.gz"
    syft_sums_path = DOWNLOADS_DIR / f"syft-{syft_ver}-checksums.txt"
    syft_base_url = f"https://github.com/anchore/syft/releases/download/v{syft_ver}"
    syft_sums_url = f"{syft_base_url}/syft_{syft_ver}_checksums.txt"
    syft_archive_url = f"{syft_base_url}/{syft_archive_name}"
    _download(syft_sums_url, syft_sums_path)
    syft_archive_final_url = _download(syft_archive_url, syft_archive_path)
    syft_expected_sha = _parse_hashicorp_sha256(syft_sums_path, syft_archive_name)
    syft_actual_sha = _sha256(syft_archive_path)
    if syft_expected_sha != syft_actual_sha:
        raise StageError(
            f"syft checksum mismatch: expected {syft_expected_sha}, got {syft_actual_sha}"
        )
    if not _tar_contains_binary(syft_archive_path, "syft"):
        raise StageError("Downloaded syft archive does not contain syft binary")
    _record_artifact(
        records,
        tool="syft",
        version=syft_ver,
        source_url=syft_archive_url,
        final_url=syft_archive_final_url,
        path=syft_archive_path,
        kind="tar.gz",
        verification="sha256 from syft_<ver>_checksums.txt + archive contains syft",
    )
    _record_artifact(
        records,
        tool="syft-checksums",
        version=syft_ver,
        source_url=syft_sums_url,
        final_url=syft_sums_url,
        path=syft_sums_path,
        kind="checksum-file",
        verification="downloaded",
    )

    # dtop — container metrics TUI (amir20/dtop).
    # Asset: dtop-<rust-triple>.tar.gz + sha256.sum (space-asterisk BSD format, multi-file).
    # Arch mapping: x86_64 → x86_64-unknown-linux-gnu, aarch64 → aarch64-unknown-linux-gnu.
    # The sha256.sum file uses the `*filename` convention (leading asterisk); handled by
    # _parse_hashicorp_sha256 which already strips the `*` prefix.
    # Replaces the insecure `dtop-installer.sh | sh` pipe-to-shell installer.
    dtop_ver = resolved["DTOP_VER"]
    dtop_rust_triple = "x86_64-unknown-linux-gnu"  # staging always runs on the build host (amd64)
    dtop_archive_name = f"dtop-{dtop_rust_triple}.tar.gz"
    dtop_archive_path = DOWNLOADS_DIR / f"dtop-{dtop_ver}.tar.gz"
    dtop_sums_path = DOWNLOADS_DIR / f"dtop-{dtop_ver}-sha256.sum"
    dtop_base_url = f"https://github.com/amir20/dtop/releases/download/v{dtop_ver}"
    dtop_sums_url = f"{dtop_base_url}/sha256.sum"
    dtop_archive_url = f"{dtop_base_url}/{dtop_archive_name}"
    _download(dtop_sums_url, dtop_sums_path)
    dtop_archive_final_url = _download(dtop_archive_url, dtop_archive_path)
    dtop_expected_sha = _parse_hashicorp_sha256(dtop_sums_path, dtop_archive_name)
    dtop_actual_sha = _sha256(dtop_archive_path)
    if dtop_expected_sha != dtop_actual_sha:
        raise StageError(
            f"dtop checksum mismatch: expected {dtop_expected_sha}, got {dtop_actual_sha}"
        )
    if not _tar_contains_binary(dtop_archive_path, "dtop"):
        raise StageError("Downloaded dtop archive does not contain dtop binary")
    _record_artifact(
        records,
        tool="dtop",
        version=dtop_ver,
        source_url=dtop_archive_url,
        final_url=dtop_archive_final_url,
        path=dtop_archive_path,
        kind="tar.gz",
        verification="sha256 from sha256.sum + archive contains dtop",
    )
    _record_artifact(
        records,
        tool="dtop-sha256sums",
        version=dtop_ver,
        source_url=dtop_sums_url,
        final_url=dtop_sums_url,
        path=dtop_sums_path,
        kind="checksum-file",
        verification="downloaded",
    )

    # glances — cross-platform system monitor (PyPI: glances).
    # NOTE: glances is intentionally NOT pre-staged as a wheel set.
    # Rationale: glances has a large transitive dependency closure (psutil, ujson, and many
    # optional extras) whose exact wheel set is Python-version- and platform-specific.
    # Pre-staging would require resolving and downloading 20+ wheels for the exact target
    # Python/glibc combination, which the staging script (running on the host) cannot
    # reliably do without a matching Python environment. The Dockerfile installs glances
    # via `pip install --no-cache-dir glances==<ver>` at build time with the version pinned
    # from tool-versions.env; the version is resolved + recorded here for provenance.
    # Write a marker file so the metadata path field references a real file.
    glances_marker_path = DOWNLOADS_DIR / f"glances-{resolved['GLANCES_VER']}.pypi-marker"
    glances_marker_path.write_text(
        f"glances=={resolved['GLANCES_VER']}\n", encoding="utf-8"
    )
    _record_artifact(
        records,
        tool="glances",
        version=resolved["GLANCES_VER"],
        source_url=f"https://pypi.org/pypi/glances/{resolved['GLANCES_VER']}/json",
        final_url=f"https://pypi.org/pypi/glances/{resolved['GLANCES_VER']}/json",
        path=glances_marker_path,
        kind="pypi-network",
        verification="version pinned from PyPI; installed at build time with pip",
    )

    # hadolint — Dockerfile linter (hadolint/hadolint).
    # Asset: hadolint-linux-x86_64 (static binary, no archive).
    # No version number in the asset filename (the release tag is the version anchor).
    # Arch mapping: x86_64 → linux-x86_64, aarch64 → linux-arm64.
    #
    # Checksum layout changed as of v2.15.0 (2026-07-30): releases up to v2.14.0 published
    # a per-asset "<name>.sha256" sidecar; v2.15.0+ instead publishes one combined
    # "checksums.sha256" covering every platform. Try the combined file first (what
    # "latest" now serves) and fall back to the old sidecar so a pinned older
    # HADOLINT_VERSION still resolves.
    hadolint_ver = resolved["HADOLINT_VER"]
    hadolint_bin_name = "hadolint-linux-x86_64"  # staging always runs on the build host (amd64)
    hadolint_bin_path = DOWNLOADS_DIR / f"hadolint-{hadolint_ver}"
    hadolint_base_url = f"https://github.com/hadolint/hadolint/releases/download/v{hadolint_ver}"
    hadolint_bin_url = f"{hadolint_base_url}/{hadolint_bin_name}"

    # Local filename is stable regardless of which upstream layout backs it — the
    # Dockerfile's install step (require_file "${DOWNLOADS_DIR}/hadolint-${HADOLINT_VER}.sha256")
    # hardcodes this name, same contract every other staged tool follows.
    hadolint_sha256_path = DOWNLOADS_DIR / f"hadolint-{hadolint_ver}.sha256"
    hadolint_sha256_url = f"{hadolint_base_url}/checksums.sha256"
    hadolint_checksum_final_url = _download(hadolint_sha256_url, hadolint_sha256_path, not_found_ok=True)
    if hadolint_checksum_final_url is None:
        _log(
            f"No combined checksums.sha256 for hadolint v{hadolint_ver}; "
            "falling back to per-asset sidecar checksum"
        )
        hadolint_sha256_url = f"{hadolint_base_url}/{hadolint_bin_name}.sha256"
        _download(hadolint_sha256_url, hadolint_sha256_path)

    hadolint_bin_final_url = _download(hadolint_bin_url, hadolint_bin_path)
    hadolint_expected_sha = _parse_hashicorp_sha256(hadolint_sha256_path, hadolint_bin_name)
    hadolint_actual_sha = _sha256(hadolint_bin_path)
    if hadolint_expected_sha != hadolint_actual_sha:
        raise StageError(
            f"hadolint checksum mismatch: expected {hadolint_expected_sha}, "
            f"got {hadolint_actual_sha}"
        )
    _record_artifact(
        records,
        tool="hadolint",
        version=hadolint_ver,
        source_url=hadolint_bin_url,
        final_url=hadolint_bin_final_url,
        path=hadolint_bin_path,
        kind="binary",
        verification="sha256 from checksums.sha256 (or per-asset sidecar fallback)",
    )
    _record_artifact(
        records,
        tool="hadolint-sha256",
        version=hadolint_ver,
        source_url=hadolint_sha256_url,
        final_url=hadolint_sha256_url,
        path=hadolint_sha256_path,
        kind="checksum-file",
        verification="downloaded",
    )

    # grype — vulnerability scanner (anchore/grype).
    # Asset: grype_<ver>_linux_amd64.tar.gz + grype_<ver>_checksums.txt (space-space format).
    # Arch: dpkg --print-architecture style (amd64 / arm64) matches upstream names directly.
    grype_ver = resolved["GRYPE_VER"]
    grype_arch = "amd64"  # staging always runs on the build host (amd64)
    grype_archive_name = f"grype_{grype_ver}_linux_{grype_arch}.tar.gz"
    grype_archive_path = DOWNLOADS_DIR / f"grype-{grype_ver}.tar.gz"
    grype_sums_path = DOWNLOADS_DIR / f"grype-{grype_ver}-checksums.txt"
    grype_base_url = f"https://github.com/anchore/grype/releases/download/v{grype_ver}"
    grype_sums_url = f"{grype_base_url}/grype_{grype_ver}_checksums.txt"
    grype_archive_url = f"{grype_base_url}/{grype_archive_name}"
    _download(grype_sums_url, grype_sums_path)
    grype_archive_final_url = _download(grype_archive_url, grype_archive_path)
    # grype is saved under a renamed local path; extract hash and reform the check line
    # against the local filename so sha256sum -c doesn't look for the upstream name.
    grype_expected_sha = _parse_hashicorp_sha256(grype_sums_path, grype_archive_name)
    grype_actual_sha = _sha256(grype_archive_path)
    if grype_expected_sha != grype_actual_sha:
        raise StageError(
            f"grype checksum mismatch: expected {grype_expected_sha}, "
            f"got {grype_actual_sha}"
        )
    if not _tar_contains_binary(grype_archive_path, "grype"):
        raise StageError("Downloaded grype archive does not contain grype binary")
    _record_artifact(
        records,
        tool="grype",
        version=grype_ver,
        source_url=grype_archive_url,
        final_url=grype_archive_final_url,
        path=grype_archive_path,
        kind="tar.gz",
        verification="sha256 from grype_<ver>_checksums.txt + archive contains grype",
    )
    _record_artifact(
        records,
        tool="grype-checksums",
        version=grype_ver,
        source_url=grype_sums_url,
        final_url=grype_sums_url,
        path=grype_sums_path,
        kind="checksum-file",
        verification="downloaded",
    )

    # cdebug — container debugging tool (iximiuz/cdebug).
    # Asset: cdebug_linux_amd64.tar.gz + checksums.txt (space-space format, no version in names).
    # NOTE: cdebug asset filenames have NO version embedded (cdebug_linux_amd64.tar.gz).
    # The checksums.txt is saved locally with a versioned name to avoid collisions across
    # builds; the extract-hash-and-reform pattern is used because the local archive path
    # is also saved with a version prefix while the checksums.txt lists the bare asset name.
    cdebug_ver = resolved["CDEBUG_VER"]
    cdebug_arch = "amd64"  # staging always runs on the build host (amd64)
    cdebug_archive_upstream_name = f"cdebug_linux_{cdebug_arch}.tar.gz"
    cdebug_archive_path = DOWNLOADS_DIR / f"cdebug-{cdebug_ver}.tar.gz"
    cdebug_sums_path = DOWNLOADS_DIR / f"cdebug-{cdebug_ver}-checksums.txt"
    cdebug_base_url = f"https://github.com/iximiuz/cdebug/releases/download/v{cdebug_ver}"
    cdebug_sums_url = f"{cdebug_base_url}/checksums.txt"
    cdebug_archive_url = f"{cdebug_base_url}/{cdebug_archive_upstream_name}"
    _download(cdebug_sums_url, cdebug_sums_path)
    cdebug_archive_final_url = _download(cdebug_archive_url, cdebug_archive_path)
    # cdebug is saved under a version-prefixed local path while checksums.txt lists the
    # bare upstream name; extract hash and reform check line against the local filename.
    cdebug_expected_sha = _parse_hashicorp_sha256(cdebug_sums_path, cdebug_archive_upstream_name)
    cdebug_actual_sha = _sha256(cdebug_archive_path)
    if cdebug_expected_sha != cdebug_actual_sha:
        raise StageError(
            f"cdebug checksum mismatch: expected {cdebug_expected_sha}, "
            f"got {cdebug_actual_sha}"
        )
    if not _tar_contains_binary(cdebug_archive_path, "cdebug"):
        raise StageError("Downloaded cdebug archive does not contain cdebug binary")
    _record_artifact(
        records,
        tool="cdebug",
        version=cdebug_ver,
        source_url=cdebug_archive_url,
        final_url=cdebug_archive_final_url,
        path=cdebug_archive_path,
        kind="tar.gz",
        verification="sha256 from checksums.txt + archive contains cdebug",
    )
    _record_artifact(
        records,
        tool="cdebug-checksums",
        version=cdebug_ver,
        source_url=cdebug_sums_url,
        final_url=cdebug_sums_url,
        path=cdebug_sums_path,
        kind="checksum-file",
        verification="downloaded",
    )

    aws_requested = resolved["AWSCLI_VER"]
    if aws_requested == "latest":
        aws_url = "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
    else:
        aws_url = f"https://awscli.amazonaws.com/awscli-exe-linux-x86_64-{aws_requested}.zip"

    aws_zip_path = DOWNLOADS_DIR / f"awscli-{aws_requested}.zip"
    aws_final_url = _download(aws_url, aws_zip_path)
    if not _zip_contains_member(aws_zip_path, "aws/install"):
        raise StageError("Downloaded awscli archive does not contain aws/install")

    detected_aws_version = _extract_awscli_version_from_archive(aws_zip_path)
    if not detected_aws_version:
        detected_aws_version = _extract_awscli_version_from_url(aws_final_url)

    if detected_aws_version:
        if aws_requested != "latest" and aws_requested != detected_aws_version:
            _log(
                "Requested AWSCLI version "
                f"{aws_requested} differs from archive-reported version {detected_aws_version}; "
                "using archive-reported version for metadata and build args"
            )
        resolved["AWSCLI_VER"] = detected_aws_version
        concrete_path = DOWNLOADS_DIR / f"awscli-{detected_aws_version}.zip"
        if concrete_path != aws_zip_path:
            concrete_path.unlink(missing_ok=True)
            aws_zip_path.replace(concrete_path)
            aws_zip_path = concrete_path
    elif aws_requested != "latest":
        resolved["AWSCLI_VER"] = aws_requested
    else:
        _log(
            "Unable to determine concrete AWS CLI version from downloaded archive; "
            "keeping AWSCLI_VER=latest"
        )

    _record_artifact(
        records,
        tool="awscli",
        version=resolved["AWSCLI_VER"],
        source_url=aws_url,
        final_url=aws_final_url,
        path=aws_zip_path,
        kind="zip",
        verification="archive contains aws/install",
    )

    return records


# copilot, openclaw and claudelink are NOT user-updatable the way the R16 tools are, so they obey the
# estate's 14-day rule, and the rule is enforced by `cmru versions`, not by a resolver of our own: the
# newest version at least `age_window_days` old is recorded by `cmru versions resolve` in
# cmru.toml and read back here through `cmru versions check`.
AI_CLI_PROJECT = PROJECT_ROOT
AI_CLI_TARGETS = {
    "COPILOT_VER": ("npm.copilot", "INSTALL_COPILOT", "COPILOT_VERSION"),
    "OPENCLAW_VER": ("npm.openclaw", "INSTALL_OPENCLAW", "OPENCLAW_VERSION"),
    "CLAUDELINK_VER": ("npm.claudelink", "INSTALL_CLAUDELINK", "CLAUDELINK_VERSION"),
}
# aider is the documented exception: an exact commit of upstream main (aider-main-pin.toml), because its
# newest vetted release cannot run on this image's Python 3.14.
AIDER_PIN_FILE = PROJECT_ROOT / "aider-main-pin.toml"
AIDER_ENV_PINS = ("AIDER_VERSION", "AIDER_COMMIT")
# A record older than two age windows (2 x 14 days) is stale: the rule exists so that a newly published
# release is not adopted before it has aged; a record left to rot defeats the other half (picking up fixes).
RECORD_MAX_AGE_DAYS = 28
RESOLVE_ADVICE = (
    "run `cmru versions resolve` in mdt-cockpit and commit the result "
    "(cmru.toml, package-lock.json, constraints/, versions/)"
)


def _load_version_records(path: Path | None = None) -> dict[str, dict]:
    """The `[versions.targets]` tables of cmru.toml (the committed record; no network, no registry)."""
    import tomllib

    path = path or AI_CLI_PROJECT / "cmru.toml"
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))["versions"]["targets"]
    except (OSError, ValueError, KeyError) as exc:
        raise StageError(f"cannot read the version records in {path}: {exc}; {RESOLVE_ADVICE}") from exc


def _record_age_days(resolved_at: object, now: datetime | None = None) -> float:
    try:
        stamp = datetime.strptime(str(resolved_at), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise StageError(f"unreadable resolved_at {resolved_at!r} in a cmru version record; {RESOLVE_ADVICE}") from exc
    return ((now or datetime.now(timezone.utc)) - stamp).total_seconds() / 86400


def _estate_window_days() -> int:
    """age_window_days from the estate's cmru.orchestration.toml (read, never defaulted)."""
    import tomllib

    path = PROJECT_ROOT.parent / "cmru.orchestration.toml"
    try:
        days = tomllib.loads(path.read_text(encoding="utf-8"))["versions"]["age_window_days"]
    except (OSError, ValueError, KeyError) as exc:
        raise StageError(f"cannot read versions.age_window_days from {path}: {exc}") from exc
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise StageError(f"{path}: versions.age_window_days must be a positive integer, got {days!r}")
    return days


def _aider_main_pin(path: Path | None = None, now: datetime | None = None, window_days: int | None = None) -> dict[str, str]:
    """{AIDER_COMMIT, AIDER_VER} from the tracked pin file; an unpinned or malformed file fails staging."""
    import tomllib

    path = path or AIDER_PIN_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StageError(f"aider needs {path.name} (an exact commit of aider main): {exc}") from exc
    commit, version, before = data.get("commit"), data.get("version"), data.get("deps_before")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise StageError(f"{path.name}: `commit` must be a full 40-hex commit sha, never a branch like `main`")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+_\-]*", version) or commit[:9] not in version:
        raise StageError(f"{path.name}: `version` must be the version that commit builds (it carries `g{commit[:9]}`)")
    if not isinstance(before, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", before):
        raise StageError(f"{path.name}: `deps_before` must be a UTC timestamp like 2026-10-05T00:00:00Z (aider's PyPI dependencies are held to it)")
    # Hand-written, so more than a shape check: a future date would let dependencies float, and a date inside the
    # estate window would admit dependencies younger than the 14-day rule allows.
    moment = _parse_utc(before, f"{path.name} deps_before")
    window = window_days if window_days is not None else _estate_window_days()
    newest_allowed = (now or datetime.now(timezone.utc)) - timedelta(days=window)
    if moment > newest_allowed:
        raise StageError(
            f"{path.name}: deps_before {before} is newer than {newest_allowed.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"(staging time minus the estate's {window}-day window); a future or too-young date lets aider's "
            "dependencies float inside the window"
        )
    return {"AIDER_COMMIT": commit, "AIDER_VER": version, "AIDER_BEFORE": before}


def _vetted_ai_cli_versions(load=_load_version_records, now: datetime | None = None) -> dict[str, str]:
    """{COPILOT_VER: ..., ...} read from the committed `cmru versions` record; any other state fails staging.

    Staging reads the record OFFLINE (`cmru versions check` queries every target of the project, including
    rate-limited OCI registries, so a build would fail on an unrelated 429). `missing`, `unresolved` (no
    `resolved` table) and `stale` (older than RECORD_MAX_AGE_DAYS) all fail with a message naming
    `cmru versions resolve`; whether a newer version is already eligible is `cmru versions check`'s job.
    Environment pins are refused, not ignored: cmru's own override (`version` + `reason` on the target) is the
    one reasoned way to pin.
    """
    names = [env_name for _t, _i, env_name in AI_CLI_TARGETS.values()] + list(AIDER_ENV_PINS)
    pins = sorted(name for name in names if (os.getenv(name) or "").strip())
    if pins:
        raise StageError(
            f"{', '.join(pins)} cannot pin a version any more: copilot, openclaw and claudelink are vetted by "
            "`cmru versions` (aider is pinned in aider-main-pin.toml). Set `version = \"<x>\"` and a "
            f"`reason = \"<why>\"` on the target in cmru.toml (cmru's reasoned override), then {RESOLVE_ADVICE}"
        )
    records = load()
    vetted: dict[str, str] = {}
    for env_key, (target, install_switch, _env_name) in AI_CLI_TARGETS.items():
        if not install_enabled(install_switch):
            continue
        declaration = records.get(target)
        if not isinstance(declaration, dict):
            raise StageError(f"cmru.toml declares no version target {target!r}; declare it, then {RESOLVE_ADVICE}")
        resolved = declaration.get("resolved")
        version = resolved.get("version") if isinstance(resolved, dict) else None
        if not isinstance(version, str) or not version:
            raise StageError(f"{target} is unresolved (no recorded version): {RESOLVE_ADVICE}")
        age = _record_age_days(resolved.get("resolved_at"), now)
        if age > RECORD_MAX_AGE_DAYS:
            raise StageError(f"{target} was resolved {age:.0f} days ago (limit {RECORD_MAX_AGE_DAYS}): {RESOLVE_ADVICE}")
        prefix = env_key[: -len("_VER")]
        vetted[env_key] = version
        vetted[f"{prefix}_BEFORE"], reason, waived = _check_record(target, declaration, resolved, version)
        if reason:
            vetted[f"{prefix}_REASON"] = reason
        if waived:
            vetted[f"{prefix}_WAIVED"] = "true"
    if install_enabled("INSTALL_AIDER"):
        # `now` is forwarded so one injected clock governs the record-age check and the aider window. The
        # window is read from the estate file rather than derived from the records' own cutoff because
        # the records are absent when aider is the only enabled tool.
        vetted.update(_aider_main_pin(now=now))
    return vetted


def _parse_utc(value: object, what: str) -> datetime:
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise StageError(f"{what}: unreadable timestamp {value!r}; {RESOLVE_ADVICE}") from exc


def _check_record(target: str, declaration: dict, resolved: dict, version: str) -> tuple[str, str, bool]:
    """Cross-check one record; returns (the `npm --before` cutoff, the override reason or "", whether the window is waived).

    * resolved.version must equal the source's version (a hand-edited record is caught);
    * without an override the release must be at least as old as the age cutoff;
    * a declared `version` override must equal the resolved value (an override edited without re-resolving
      fails) and carries a reason, which is carried into tool-versions.env and the image manifest;
    * the cutoff handed to npm is the record's own age cutoff, so transitive dependencies are judged by the
      rule that judged the package. EXCEPTION: an override released AFTER the age cutoff cannot be installed
      under that cutoff, so `--before` becomes its release date and the 14-day window is WAIVED for that tool's
      dependencies too (returned `waived`, recorded as <TOOL>_WAIVED and shown in the manifest).
    """
    source = (resolved.get("sources") or {}).get("npm") if isinstance(resolved.get("sources"), dict) else None
    if not isinstance(source, dict) or source.get("version") != version:
        raise StageError(f"{target}: resolved.version {version!r} does not equal the source's version; {RESOLVE_ADVICE}")
    cutoff_text = resolved.get("age_cutoff")
    cutoff = _parse_utc(cutoff_text, f"{target} age_cutoff")
    released = _parse_utc(source.get("released_at"), f"{target} released_at")
    override = declaration.get("version")
    reason = ""
    waived = False
    if override is not None:
        reason = declaration.get("reason")
        if override != version:
            raise StageError(f"{target}: the declared override {override!r} differs from the resolved {version!r}; {RESOLVE_ADVICE}")
        if not isinstance(reason, str) or not reason.strip():
            raise StageError(f"{target}: an override needs a reason; {RESOLVE_ADVICE}")
        reason = reason.strip()
        if released > cutoff:
            cutoff_text = source["released_at"]
            waived = True
    elif released > cutoff:
        raise StageError(f"{target}: {version} was released {source['released_at']}, after the age cutoff {cutoff_text}; {RESOLVE_ADVICE}")
    return str(cutoff_text), reason, waived


def _resolve_versions() -> dict[str, str]:
    resolved = {
        "CODEX_VER": _resolve_codex_version(os.getenv("CODEX_VERSION")),
        "ANTIGRAVITY_VER": (os.getenv("ANTIGRAVITY_VERSION") or "latest").strip() or "latest",
        "REASONIX_VER": _resolve_pinned_npm_version(os.getenv("REASONIX_VERSION"), "reasonix", "REASONIX_VERSION"),
        "OPENCODE_VER": _resolve_pinned_npm_version(os.getenv("OPENCODE_VERSION"), "opencode-ai", "OPENCODE_VERSION"),
        "PI_VER": _resolve_pinned_npm_version(os.getenv("PI_VERSION"), "@earendil-works/pi-coding-agent", "PI_VERSION"),
        "DTOP_VER": _resolve_version(os.getenv("DTOP_VERSION"), "amir20/dtop"),
        "LAZYDOCKER_VER": _resolve_version(os.getenv("LAZYDOCKER_VERSION"), "jesseduffield/lazydocker"),
        "GLANCES_VER": _resolve_pypi_version(os.getenv("GLANCES_VERSION"), "glances"),
        "DIVE_VER": _resolve_version(os.getenv("DIVE_VERSION"), "wagoodman/dive"),
        "SYFT_VER": _resolve_version(os.getenv("SYFT_VERSION"), "anchore/syft"),
        "HADOLINT_VER": _resolve_version(os.getenv("HADOLINT_VERSION"), "hadolint/hadolint"),
        "GRYPE_VER": _resolve_version(os.getenv("GRYPE_VERSION"), "anchore/grype"),
        "CDEBUG_VER": _resolve_version(os.getenv("CDEBUG_VERSION"), "iximiuz/cdebug"),
        "CRANE_VER": _resolve_version(
            os.getenv("CRANE_VERSION"), "google/go-containerregistry"
        ),
        "REGCTL_VER": _resolve_version(os.getenv("REGCTL_VERSION"), "regclient/regclient"),
        "B2_VER": _resolve_version(os.getenv("B2_VERSION"), "Backblaze/B2_Command_Line_Tool"),
        "GDU_VER": _resolve_pinned_version(os.getenv("GDU_VERSION"), GDU_PINNED_VERSION, "GDU_VERSION"),
        "DUST_VER": _resolve_pinned_version(os.getenv("DUST_VERSION"), DUST_PINNED_VERSION, "DUST_VERSION"),
        "BAT_VER": _resolve_version(os.getenv("BAT_VERSION"), "sharkdp/bat"),
        "FD_VER": _resolve_version(os.getenv("FD_VERSION"), "sharkdp/fd"),
        "RIPGREP_VER": _resolve_version(os.getenv("RIPGREP_VERSION"), "BurntSushi/ripgrep"),
        "SHELLCHECK_VER": _resolve_version(os.getenv("SHELLCHECK_VERSION"), "koalaman/shellcheck"),
        "FZF_VER": _resolve_version(os.getenv("FZF_VERSION"), "junegunn/fzf"),
        "HTOP_VER": _resolve_version(os.getenv("HTOP_VERSION"), "htop-dev/htop"),
        "YQ_VER": _resolve_version(os.getenv("YQ_VERSION"), "mikefarah/yq"),
        "CONSUL_VER": _resolve_version(os.getenv("CONSUL_VERSION"), "hashicorp/consul"),
        "DELTA_VER": _resolve_version(os.getenv("DELTA_VERSION"), "dandavison/delta"),
        "GH_VER": _resolve_version(os.getenv("GH_VERSION"), "cli/cli"),
        "NVIM_VER": _resolve_version(os.getenv("NVIM_VERSION"), "neovim/neovim"),
        "NVCHAD_VER": _resolve_version(os.getenv("NVCHAD_VERSION"), "NvChad/NvChad"),
        "GRPCURL_VER": _resolve_version(os.getenv("GRPCURL_VERSION"), "fullstorydev/grpcurl"),
        "LNAV_VER": _resolve_version(os.getenv("LNAV_VERSION"), "tstack/lnav"),
        "RGA_VER": _resolve_version(os.getenv("RGA_VERSION"), "phiresky/ripgrep-all"),
        "VAULT_VER": _resolve_version(os.getenv("VAULT_VERSION"), "hashicorp/vault"),
    }

    resolved.update(_vetted_ai_cli_versions())
    if install_enabled("INSTALL_CLAUDE_CODE"):
        resolved["CLAUDE_CODE_VER"] = _resolve_claude_code_version(os.getenv("CLAUDE_CODE_VERSION"))
    else:
        _log("INSTALL_CLAUDE_CODE is false: skipping Claude Code version resolution (no registry lookup)")

    aws_requested = (os.getenv("AWSCLI_VERSION") or "latest").strip()
    resolved["AWSCLI_VER"] = aws_requested or "latest"
    return resolved


def _write_versions_env(resolved_versions: dict[str, str]) -> None:
    import shlex

    # The file is sourced by the Dockerfile's shell: a value with spaces (an override reason) must be quoted.
    lines = [f"{key}={shlex.quote(str(value))}" for key, value in sorted(resolved_versions.items())]
    VERSIONS_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_metadata(*, build_date: str, resolved_versions: dict[str, str], artifacts: list[StagedArtifact]) -> None:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "build_date": build_date,
        "stage_root": str(STAGE_ROOT),
        "downloads_dir": str(DOWNLOADS_DIR),
        "resolved_versions": resolved_versions,
        "artifacts": [asdict(item) for item in artifacts],
    }
    METADATA_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stage_tool_artifacts(*, build_date: str) -> StagingResult:
    """Prepare all tool artifacts before docker bake.

    Raises StageError on any failure.
    """
    _log("Preparing local tool artifact staging directory")
    if STAGE_ROOT.exists():
        # Staging is disposable; the verified cache is deliberately outside it.
        shutil.rmtree(STAGE_ROOT)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

    _log("Resolving tool versions")
    resolved_versions = _resolve_versions()

    _log("Downloading tool artifacts")
    artifacts = _stage_tools(resolved_versions)

    _log("Writing staging metadata")
    _write_versions_env(resolved_versions)
    _write_metadata(
        build_date=build_date,
        resolved_versions=resolved_versions,
        artifacts=artifacts,
    )

    _log(f"Staged {len(artifacts)} artifacts in {STAGE_ROOT}")
    return StagingResult(
        stage_root=STAGE_ROOT,
        metadata_path=METADATA_PATH,
        versions_env_path=VERSIONS_ENV_PATH,
        resolved_versions=resolved_versions,
    )


def _parse_args() -> argparse.Namespace:
    parser = MdtArgumentParser(description="Stage tool artifacts before docker bake")
    parser.add_argument(
        "--build-date",
        default=os.getenv("BUILD_DATE") or "",
        help="Build date identifier used in metadata (defaults to BUILD_DATE env)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    build_date = (args.build_date or "").strip()
    if not build_date:
        raise StageError("BUILD_DATE is required for tool artifact staging")

    result = stage_tool_artifacts(build_date=build_date)
    print(f"STAGE_ROOT={result.stage_root}")
    print(f"STAGE_METADATA={result.metadata_path}")
    print(f"STAGE_VERSIONS_ENV={result.versions_env_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=os.sys.stderr)
        raise SystemExit(2)
