"""Verified uploads from the repo-level ``downloads/`` folder through the TrueNAS HTTP upload endpoint (TNT-61).

The only accepted local source is a file inside ``<repo root>/downloads/``, resolved from this package's own location.
There is no environment variable that moves it. The file is opened read-only without following links and is checked
against the ``<name>.sha256`` file beside it before any byte leaves the machine. Remote paths are NAS paths only.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import ssl
import stat
import time
import urllib.parse
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Iterator

from .errors import Refused
from .policy import _names_boot_pool
from .profile import Profile, Secret

# <repo>/truenas/tnt/src/tnt/uploads.py -> parents[4] is the repository root. A copy of tnt outside a checkout has no
# downloads folder and refuses with a clear message instead of guessing another location.
DOWNLOADS_DIR = Path(__file__).resolve().parents[4] / "downloads"
UPLOAD_MODE = 0o644
JOB_TIMEOUT_SECONDS = 3600.0
JOB_POLL_SECONDS = 1.0
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
_CHUNK = 1024 * 1024


def validate_download_name(name: Any) -> str:
    """A relative POSIX path inside downloads/: no absolute path, no ``..``, no empty or dot segments."""
    if not isinstance(name, str) or not name.strip():
        raise Refused("upload-source", "name a file inside the repository downloads/ folder")
    if name.startswith("/") or "\\" in name or "\x00" in name:
        raise Refused("upload-source", "only a file name inside downloads/ is accepted; absolute paths are refused")
    if any(part in ("", ".", "..") for part in name.split("/")):
        raise Refused("upload-source", "only a file name inside downloads/ is accepted; '..' and empty segments are refused")
    if name.endswith(".sha256"):
        raise Refused("upload-source", "name the download itself, not its .sha256 file")
    return name


def _downloads_root(downloads_dir: Path | None) -> Path:
    root = DOWNLOADS_DIR if downloads_dir is None else Path(downloads_dir)
    try:
        resolved = root.resolve(strict=True)
    except OSError:
        raise Refused("upload-source", "the repository downloads/ folder was not found next to this tnt checkout") from None
    if not resolved.is_dir():
        raise Refused("upload-source", "the repository downloads/ folder was not found next to this tnt checkout")
    return resolved


def _open_regular(path: Path, root: Path) -> tuple[int, int]:
    """Open read-only without following links; return (fd, size). The real path must stay inside ``root``."""
    try:
        real = path.resolve(strict=True)
    except OSError:
        raise Refused("upload-source", f"downloads/{path.name} does not exist") from None
    if root not in real.parents:
        raise Refused("upload-source", "the file resolves outside downloads/; refused")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(real, flags)
    except OSError as exc:
        raise Refused("upload-source", f"cannot open the download read-only: {exc.strerror or type(exc).__name__}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            raise Refused("upload-source", "the download is not a non-empty regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd, info.st_size


def expected_sha256(path: Path) -> str:
    """The digest from ``<name>.sha256`` beside the download (``<hex>`` or ``<hex>  <name>``)."""
    sidecar = path.with_name(path.name + ".sha256")
    try:
        text = sidecar.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        raise Refused("upload-checksum", f"downloads/{sidecar.name} is missing or unreadable; it is required") from None
    tokens = text.split()
    if not tokens or not _HEX64.fullmatch(tokens[0]):
        raise Refused("upload-checksum", f"downloads/{sidecar.name} does not start with a 64-digit hex sha256")
    return tokens[0].lower()


class VerifiedDownload:
    """A download whose sha256 matched its sidecar when this object was made; ``reverify`` repeats the check."""

    def __init__(self, name: str, path: Path, root: Path, digest: str, size: int):
        self.name, self.path, self.root, self.sha256, self.size = name, path, root, digest, size

    def reverify(self) -> None:
        fd, size = _open_regular(self.path, self.root)
        try:
            if size != self.size or _digest_fd(fd) != self.sha256:
                raise Refused("upload-checksum", f"downloads/{self.name} changed since it was verified; refused")
        finally:
            os.close(fd)

    def open(self) -> BinaryIO:
        fd, _ = _open_regular(self.path, self.root)
        return os.fdopen(fd, "rb")


def _digest_fd(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, _CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def verify_download(name: Any, downloads_dir: Path | None = None) -> VerifiedDownload:
    relative = validate_download_name(name)
    root = _downloads_root(downloads_dir)
    path = root.joinpath(*PurePosixPath(relative).parts)
    fd, size = _open_regular(path, root)
    try:
        wanted = expected_sha256(path)
        actual = _digest_fd(fd)
    finally:
        os.close(fd)
    if actual != wanted:
        raise Refused("upload-checksum", f"downloads/{relative} does not match its .sha256 (expected {wanted[:12]}..., "
                                         f"found {actual[:12]}...); nothing was uploaded")
    return VerifiedDownload(relative, path, root, actual, size)


def validate_destination_dir(directory: Any) -> str:
    """A NAS dataset directory under /mnt/<pool>/..., never boot-pool, never containing '..'."""
    if not isinstance(directory, str) or not directory.startswith("/mnt/"):
        raise Refused("upload-destination", "the destination must be a NAS directory path under /mnt/<pool>/...")
    parts = directory.split("/")[1:]
    if len(parts) < 3 or any(part in ("", ".", "..") for part in parts) or "\x00" in directory:
        raise Refused("upload-destination", "the destination must be /mnt/<pool>/<dataset-dir> with no empty, '.' or '..' segments")
    if _names_boot_pool(directory):
        raise Refused("upload-destination", "boot-pool and its datasets are never written from here")
    return directory


# -- the HTTP upload ----------------------------------------------------------------------------------------
class _MultipartBody:
    """A streaming multipart/form-data body: the JSON ``data`` part first, then the ``file`` part."""

    def __init__(self, data: dict[str, Any], filename: str, file: BinaryIO, size: int):
        self.boundary = uuid.uuid4().hex
        quoted = urllib.parse.quote(filename, safe="")
        self._head = (f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"data\"\r\n\r\n"
                      f"{json.dumps(data, sort_keys=True)}\r\n"
                      f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{quoted}\"\r\n"
                      "Content-Type: application/octet-stream\r\n\r\n").encode()
        self._tail = f"\r\n--{self.boundary}--\r\n".encode()
        self._file = file
        self.length = len(self._head) + size + len(self._tail)

    def chunks(self) -> Iterator[bytes]:
        yield self._head
        while chunk := self._file.read(_CHUNK):
            yield chunk
        yield self._tail


def _origin(profile: Profile) -> tuple[bool, str, int]:
    parts = urllib.parse.urlsplit(profile.url)
    if parts.scheme not in ("wss", "ws") or not parts.hostname:
        raise Refused("upload-origin", "the selected profile has no supported WebSocket origin")
    secure = parts.scheme == "wss"
    return secure, parts.hostname, parts.port or (443 if secure else 80)


def _auth_header(profile: Profile, secret: Secret) -> str:
    if profile.auth == "api_key":
        return f"Bearer {secret.reveal()}"
    raw = f"{profile.username}:{secret.reveal()}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def post_upload(profile: Profile, secret: Secret, insecure_tls: bool, download: VerifiedDownload,
                destination: str, mode: int) -> int:
    """POST the verified file to ``/_upload`` as a ``filesystem.put`` job and return the remote job ID."""
    secure, host, port = _origin(profile)
    download.reverify()
    handle = download.open()
    try:
        body = _MultipartBody({"method": "filesystem.put", "params": [destination, {"mode": mode}]},
                              PurePosixPath(destination).name, handle, download.size)
        if secure:
            context = ssl._create_unverified_context() if insecure_tls else ssl.create_default_context()
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(host, port, timeout=600, context=context)
        else:
            connection = http.client.HTTPConnection(host, port, timeout=600)
        try:
            connection.putrequest("POST", "/_upload")
            connection.putheader("Authorization", _auth_header(profile, secret))
            connection.putheader("Content-Type", f"multipart/form-data; boundary={body.boundary}")
            connection.putheader("Content-Length", str(body.length))
            connection.endheaders()
            for chunk in body.chunks():
                connection.send(chunk)
            response = connection.getresponse()
            payload = response.read(1024 * 1024)
            if response.status != 200:
                raise Refused("upload-failed", f"the TrueNAS upload endpoint returned HTTP {response.status}")
        except Refused:
            raise
        except (OSError, http.client.HTTPException) as exc:
            reason = type(exc).__name__
            raise Refused("upload-failed", secret.scrub(f"the TrueNAS upload failed ({reason})")) from None
        finally:
            connection.close()
    finally:
        handle.close()
    try:
        job_id = json.loads(payload)["job_id"]
    except (ValueError, KeyError, TypeError):
        raise Refused("upload-failed", "the TrueNAS upload endpoint returned no job ID") from None
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise Refused("upload-failed", "the TrueNAS upload endpoint returned an invalid job ID")
    return job_id


def wait_for_job(client: Any, job_id: int, *, timeout: float = JOB_TIMEOUT_SECONDS,
                 poll: float = JOB_POLL_SECONDS, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Poll ``core.get_jobs`` for the upload job until it is terminal."""
    deadline = time.monotonic() + timeout
    while True:
        rows = client.call("core.get_jobs", [["id", "=", job_id]])
        row = rows[0] if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict) else None
        state = row.get("state") if row else None
        if state == "SUCCESS":
            return row
        if state in ("FAILED", "ABORTED"):
            raise Refused("upload-failed", f"the upload job {job_id} ended {state}: {row.get('error') or 'no reason reported'}")
        if time.monotonic() >= deadline:
            raise Refused("upload-timeout", f"the upload job {job_id} did not finish in time (last state: {state or 'unknown'}); "
                                            "inspect Jobs before retrying")
        sleep(poll)


def make_sender(profile: Profile, secret: Secret, insecure_tls: bool, download: VerifiedDownload,
                destination: str, mode: int = UPLOAD_MODE) -> Callable[[Any], dict[str, Any]]:
    """The callable ``GuardedRpc.register_upload`` runs in place of the ``filesystem.put`` frame."""
    def send(client: Any) -> dict[str, Any]:
        job_id = post_upload(profile, secret, insecure_tls, download, destination, mode)
        wait_for_job(client, job_id)
        return {"job_id": job_id, "destination": destination, "size": download.size, "sha256": download.sha256}
    return send
