"""Discover and download TrueNAS installer ISOs with SHA-256 sidecars."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Callable
from urllib.error import URLError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import Request, urlopen


CATALOG_URLS = (
    "https://download.sys.truenas.net/",
    "https://iso.sys.truenas.net/",
)
ALLOWED_HOSTS = {"download.sys.truenas.net", "iso.sys.truenas.net"}
USER_AGENT = "truenas-vanilla-iso-patcher"
CATALOG_MAX_BYTES = 32 * 1024 * 1024
CHECKSUM_MAX_BYTES = 4096
CHUNK_BYTES = 8 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ISO_NAME_RE = re.compile(
    r"^(?:TrueNAS-SCALE-|TrueNAS-)(?P<version>\d+(?:\.\d+)+(?:[-+][A-Za-z0-9][A-Za-z0-9.+-]*)?)\.iso$"
)


class UpstreamError(RuntimeError):
    """An upstream index, checksum, or download did not meet the contract."""


@dataclass(frozen=True)
class UpstreamIso:
    family: str
    version: str
    filename: str
    iso_url: str
    checksum_url: str

    @property
    def release_id(self) -> str:
        return f"{self.family}/{self.version}"

    @property
    def label(self) -> str:
        return f"{self.family} {self.version} ({self.filename})"


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)


def _trusted_url(url: str, *, expected_host: str | None = None) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname not in ALLOWED_HOSTS
        or (expected_host is not None and parsed.hostname != expected_host)
    ):
        raise UpstreamError(f"refusing non-TrueNAS or non-HTTPS URL: {url!r}")


def _request(url: str, *, accept: str = "*/*"):
    _trusted_url(url)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        response = urlopen(request, timeout=60)
    except (OSError, URLError) as exc:
        raise UpstreamError(f"could not fetch {url}: {exc}") from exc
    final_url = response.geturl()
    try:
        _trusted_url(final_url)
    except UpstreamError:
        response.close()
        raise
    return response


def _read_limited(url: str, maximum: int, *, accept: str = "*/*") -> bytes:
    with _request(url, accept=accept) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit() and int(content_length) > maximum:
            raise UpstreamError(f"upstream response is larger than {maximum} bytes: {url}")
        chunks: list[bytes] = []
        received = 0
        while True:
            chunk = response.read(min(CHUNK_BYTES, maximum + 1 - received))
            if not chunk:
                break
            received += len(chunk)
            if received > maximum:
                raise UpstreamError(f"upstream response is larger than {maximum} bytes: {url}")
            chunks.append(chunk)
        return b"".join(chunks)


def _relative_href(href: str, index_url: str) -> str | None:
    absolute = urljoin(index_url, href)
    try:
        _trusted_url(absolute, expected_host=urlsplit(index_url).hostname)
    except UpstreamError:
        return None
    parsed = urlsplit(absolute)
    path = unquote(parsed.path).lstrip("/")
    parts = PurePosixPath(path).parts
    if not parts or any(part in (".", "..") for part in parts):
        return None
    return "/".join(parts)


def _parse_index(html: bytes, index_url: str) -> list[UpstreamIso]:
    try:
        text = html.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpstreamError(f"TrueNAS index is not UTF-8: {index_url}") from exc
    parser = _Links()
    parser.feed(text)
    paths = {
        path
        for href in parser.hrefs
        if (path := _relative_href(href, index_url)) is not None
    }
    assets: list[UpstreamIso] = []
    for relative in sorted(paths):
        if not relative.lower().endswith(".iso") or relative + ".sha256" not in paths:
            continue
        parts = PurePosixPath(relative).parts
        if len(parts) < 2:
            continue
        filename = parts[-1]
        match = ISO_NAME_RE.fullmatch(filename)
        if match is None:
            continue
        version = match.group("version")
        major_match = re.match(r"^(\d+)", version)
        if major_match is None or int(major_match.group(1)) < 25:
            continue
        family = parts[0]
        if len(parts) > 2 and re.fullmatch(r"\d+(?:\.\d+)+(?:[-+].*)?", parts[-2]):
            if parts[-2] != version:
                continue
        base = urlsplit(index_url)
        root = f"{base.scheme}://{base.netloc}/"
        assets.append(
            UpstreamIso(
                family=family,
                version=version,
                filename=filename,
                iso_url=urljoin(root, relative),
                checksum_url=urljoin(root, relative + ".sha256"),
            )
        )
    return assets


def discover_releases(
    *,
    catalog_urls: tuple[str, ...] = CATALOG_URLS,
    fetch: Callable[[str, int], bytes] | None = None,
) -> list[UpstreamIso]:
    """Return ISO entries paired with SHA-256 sidecar links in official catalogs.

    The direct-download and ISO indexes currently expose different release
    families. Both must be read successfully; returning a partial catalog
    would make a network failure look like an upstream release being absent.
    """
    reader = fetch or (lambda url, maximum: _read_limited(url, maximum, accept="text/html"))
    found: dict[tuple[str, str, str], UpstreamIso] = {}
    failures: list[str] = []
    succeeded = 0
    for index_url in catalog_urls:
        try:
            _trusted_url(index_url)
            html = reader(index_url, CATALOG_MAX_BYTES)
            entries = _parse_index(html, index_url)
        except (OSError, UpstreamError) as exc:
            failures.append(f"{index_url}: {exc}")
            continue
        succeeded += 1
        for asset in entries:
            found.setdefault((asset.family, asset.version, asset.filename), asset)
    if failures:
        detail = "; ".join(failures)
        raise UpstreamError(
            "could not read every official TrueNAS ISO catalog; refusing to "
            f"return an incomplete release list: {detail}"
        )
    if not succeeded:
        raise UpstreamError("no official TrueNAS ISO catalogs were configured")
    if not found:
        raise UpstreamError(
            "the official TrueNAS ISO catalog was readable but contained no "
            "installer ISO entries with matching SHA-256 sidecar links numbered 25 or later"
        )
    return sorted(found.values(), key=lambda asset: (asset.family.casefold(), asset.version, asset.filename))


def resolve_release(
    releases: list[UpstreamIso],
    version: str,
    family: str | None = None,
) -> UpstreamIso:
    matches = [
        asset
        for asset in releases
        if asset.version == version and (family is None or asset.family == family)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise UpstreamError(
            f"no TrueNAS installer ISO with a matching SHA-256 sidecar found for version {version!r}"
        )
    choices = ", ".join(sorted(asset.release_id for asset in matches))
    raise UpstreamError(f"version {version!r} is ambiguous; specify --family from: {choices}")


def parse_checksum(data: bytes, *, filename: str) -> str:
    try:
        text = data.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise UpstreamError("TrueNAS SHA-256 sidecar is not ASCII") from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise UpstreamError("TrueNAS SHA-256 sidecar must contain exactly one checksum line")
    match = re.fullmatch(r"([0-9a-fA-F]{64})(?:\s+\*?(.+))?", lines[0])
    if match is None:
        raise UpstreamError("TrueNAS SHA-256 sidecar does not contain a valid SHA-256 digest")
    sidecar_name = match.group(2)
    if sidecar_name is not None and Path(sidecar_name).name != filename:
        raise UpstreamError(
            f"TrueNAS SHA-256 sidecar names {sidecar_name!r}, expected {filename!r}"
        )
    return match.group(1).lower()


def fetch_published_checksum(asset: UpstreamIso) -> str:
    sidecar = _read_limited(asset.checksum_url, CHECKSUM_MAX_BYTES)
    return parse_checksum(sidecar, filename=asset.filename)


def download_verified_iso(
    asset: UpstreamIso,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """Download into a private temporary file, verify SHA-256, then publish.

    The output path is created with a hard link so a concurrent file creation
    cannot be overwritten. The caller gets the verified digest for a manifest
    or checksum sidecar.
    """
    destination = destination.expanduser().resolve(strict=False)
    if destination.exists():
        raise UpstreamError(f"output already exists; choose another path: {destination}")
    if not destination.parent.is_dir():
        raise UpstreamError(f"output directory does not exist: {destination.parent}")
    if expected_sha256 is None:
        expected_sha256 = fetch_published_checksum(asset)
    if SHA256_RE.fullmatch(expected_sha256) is None:
        raise UpstreamError("expected upstream SHA-256 must contain exactly 64 hexadecimal digits")
    expected_sha256 = expected_sha256.lower()

    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.download-", dir=destination.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "wb") as output, _request(asset.iso_url, accept="application/octet-stream") as response:
            content_length = response.headers.get("Content-Length")
            expected_bytes = int(content_length) if content_length and content_length.isdigit() else None
            while True:
                chunk = response.read(CHUNK_BYTES)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                if progress is not None and (total % (256 * 1024 * 1024) < len(chunk)):
                    shown = f"{total:,} bytes"
                    if expected_bytes:
                        shown += f" of {expected_bytes:,}"
                    progress(f"Downloaded {shown} of {asset.filename}.")
            output.flush()
            os.fsync(output.fileno())
        actual = digest.hexdigest()
        if total == 0:
            raise UpstreamError("downloaded ISO is empty")
        if expected_bytes is not None and total != expected_bytes:
            raise UpstreamError(
                f"downloaded ISO length mismatch: received {total} bytes, expected {expected_bytes}"
            )
        if actual != expected_sha256:
            raise UpstreamError(
                f"downloaded ISO SHA-256 mismatch for {asset.filename}: "
                f"expected {expected_sha256}, got {actual}"
            )
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise UpstreamError(f"output appeared during download; refusing to overwrite: {destination}") from exc
        if progress is not None:
            progress(f"Verified {asset.filename} SHA-256: {actual}")
        return actual
    except OSError as exc:
        raise UpstreamError(f"could not download {asset.filename}: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
