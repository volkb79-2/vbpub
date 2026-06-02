#!/usr/bin/env python3
"""Pre-stage external tool artifacts before docker bake runs.

This module resolves effective tool versions and downloads all required release
artifacts locally. Any failure raises an exception so callers can fail fast
before docker buildx bake starts.
"""
from __future__ import annotations

import argparse
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STAGE_ROOT = PROJECT_ROOT / "build" / "tool-artifacts-staging"
DOWNLOADS_DIR = STAGE_ROOT / "downloads"
METADATA_PATH = STAGE_ROOT / "metadata.json"
VERSIONS_ENV_PATH = STAGE_ROOT / "tool-versions.env"

USER_AGENT = "modern-debian-tools-python-debug/stage-tool-artifacts"
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}
DEFAULT_RETRIES = 3
DOWNLOAD_TIMEOUT_SECONDS = 90
AWSCLI_VERSION_RE = re.compile(r"aws-cli/(?P<version>[0-9][^\s]+)")
CLAUDE_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.+-]+)?$")

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


def _download(url: str, destination: Path, *, headers: dict[str, str] | None = None) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
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
                return final_url
        except urllib.error.HTTPError as exc:
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
        with urllib.request.urlopen(req, timeout=30) as response:
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


def _resolve_claude_code_version(requested: str | None) -> str:
    value = (requested or "").strip()
    if value and value != "latest":
        if not CLAUDE_VERSION_RE.fullmatch(value):
            raise StageError(f"Unsupported CLAUDE_CODE_VERSION value: {value!r}")
        return value

    latest = _download_text("https://downloads.claude.ai/claude-code-releases/latest").strip()
    if not CLAUDE_VERSION_RE.fullmatch(latest):
        raise StageError(
            "Failed to resolve latest Claude Code release version from "
            "downloads.claude.ai"
        )
    return latest


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


def _parse_claude_sha256(manifest: dict, platform: str) -> str:
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict):
        raise StageError("Claude manifest missing 'platforms' map")

    entry = platforms.get(platform)
    if not isinstance(entry, dict):
        raise StageError(f"Claude manifest missing platform entry for {platform}")

    checksum = str(entry.get("checksum") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise StageError(f"Claude manifest contains invalid checksum for platform {platform}")
    return checksum


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
    records: list[StagedArtifact],
) -> None:
    source_url, final_url = _download_first_available(destination, urls)
    if not _tar_contains_binary(destination, expected_binary):
        raise StageError(
            f"Downloaded {tool} archive does not contain expected binary '{expected_binary}'"
        )

    _record_artifact(
        records,
        tool=tool,
        version=version,
        source_url=source_url,
        final_url=final_url,
        path=destination,
        kind=archive_kind,
        verification=f"archive contains {expected_binary}",
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


def _stage_codex(version: str, records: list[StagedArtifact]) -> None:
    release_tag = f"rust-v{version}"
    archive_name = "codex-package-x86_64-unknown-linux-musl.tar.gz"
    sums_name = "codex-package_SHA256SUMS"

    base_url = f"https://github.com/openai/codex/releases/download/{release_tag}"
    archive_url = f"{base_url}/{archive_name}"
    sums_url = f"{base_url}/{sums_name}"

    archive_path = DOWNLOADS_DIR / f"codex-{version}.tar.gz"
    sums_path = DOWNLOADS_DIR / f"codex-{version}-SHA256SUMS"

    sums_final_url = _download(sums_url, sums_path)
    archive_final_url = _download(archive_url, archive_path)

    expected_sha = _parse_hashicorp_sha256(sums_path, archive_name)
    actual_sha = _sha256(archive_path)
    if expected_sha != actual_sha:
        raise StageError(
            f"Codex checksum mismatch: expected {expected_sha}, got {actual_sha}"
        )

    if not _tar_contains_binary(archive_path, "codex"):
        raise StageError("Downloaded Codex package does not contain codex binary")

    _record_artifact(
        records,
        tool="codex",
        version=version,
        source_url=archive_url,
        final_url=archive_final_url,
        path=archive_path,
        kind="tar.gz",
        verification="sha256 from codex-package_SHA256SUMS + archive contains codex",
    )
    _record_artifact(
        records,
        tool="codex-sha256sums",
        version=version,
        source_url=sums_url,
        final_url=sums_final_url,
        path=sums_path,
        kind="checksum-file",
        verification="downloaded",
    )


def _stage_claude_code(version: str, records: list[StagedArtifact]) -> None:
    platform = "linux-x64"
    manifest_url = f"https://downloads.claude.ai/claude-code-releases/{version}/manifest.json"
    binary_url = f"https://downloads.claude.ai/claude-code-releases/{version}/{platform}/claude"

    manifest_path = DOWNLOADS_DIR / f"claude-{version}-manifest.json"
    binary_path = DOWNLOADS_DIR / f"claude-{version}-{platform}"

    manifest_final_url = _download(manifest_url, manifest_path)
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StageError("Invalid Claude manifest JSON payload") from exc

    expected_sha = _parse_claude_sha256(manifest_payload, platform)
    binary_final_url = _download(binary_url, binary_path)
    actual_sha = _sha256(binary_path)
    if expected_sha != actual_sha:
        raise StageError(
            f"Claude checksum mismatch: expected {expected_sha}, got {actual_sha}"
        )

    _record_artifact(
        records,
        tool="claude",
        version=version,
        source_url=binary_url,
        final_url=binary_final_url,
        path=binary_path,
        kind="binary",
        verification="sha256 from versioned manifest.json",
    )
    _record_artifact(
        records,
        tool="claude-manifest",
        version=version,
        source_url=manifest_url,
        final_url=manifest_final_url,
        path=manifest_path,
        kind="manifest",
        verification="downloaded",
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


def _stage_tools(resolved: dict[str, str]) -> list[StagedArtifact]:
    records: list[StagedArtifact] = []

    _stage_codex(resolved["CODEX_VER"], records)
    _stage_claude_code(resolved["CLAUDE_CODE_VER"], records)
    resolved["ANTIGRAVITY_VER"] = _stage_antigravity(resolved["ANTIGRAVITY_VER"], records)

    _stage_b2(resolved["B2_VER"], records)

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


def _resolve_versions() -> dict[str, str]:
    resolved = {
        "CODEX_VER": _resolve_codex_version(os.getenv("CODEX_VERSION")),
        "CLAUDE_CODE_VER": _resolve_claude_code_version(os.getenv("CLAUDE_CODE_VERSION")),
        "ANTIGRAVITY_VER": (os.getenv("ANTIGRAVITY_VERSION") or "latest").strip() or "latest",
        "AIDER_VER": (os.getenv("AIDER_VERSION") or "latest").strip() or "latest",
        "B2_VER": _resolve_version(os.getenv("B2_VERSION"), "Backblaze/B2_Command_Line_Tool"),
        "BAT_VER": _resolve_version(os.getenv("BAT_VERSION"), "sharkdp/bat"),
        "FD_VER": _resolve_version(os.getenv("FD_VERSION"), "sharkdp/fd"),
        "RIPGREP_VER": _resolve_version(os.getenv("RIPGREP_VERSION"), "BurntSushi/ripgrep"),
        "SHELLCHECK_VER": _resolve_version(os.getenv("SHELLCHECK_VERSION"), "koalaman/shellcheck"),
        "FZF_VER": _resolve_version(os.getenv("FZF_VERSION"), "junegunn/fzf"),
        "YQ_VER": _resolve_version(os.getenv("YQ_VERSION"), "mikefarah/yq"),
        "CONSUL_VER": _resolve_version(os.getenv("CONSUL_VERSION"), "hashicorp/consul"),
        "DELTA_VER": _resolve_version(os.getenv("DELTA_VERSION"), "dandavison/delta"),
        "GH_VER": _resolve_version(os.getenv("GH_VERSION"), "cli/cli"),
        "RGA_VER": _resolve_version(os.getenv("RGA_VERSION"), "phiresky/ripgrep-all"),
        "VAULT_VER": _resolve_version(os.getenv("VAULT_VERSION"), "hashicorp/vault"),
    }

    aws_requested = (os.getenv("AWSCLI_VERSION") or "latest").strip()
    resolved["AWSCLI_VER"] = aws_requested or "latest"
    return resolved


def _write_versions_env(resolved_versions: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in sorted(resolved_versions.items())]
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
    parser = argparse.ArgumentParser(description="Stage tool artifacts before docker bake")
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
