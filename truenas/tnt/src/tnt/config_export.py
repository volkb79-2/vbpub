"""Fetch a v27 core.download result into a new, private local configuration tar file."""
from __future__ import annotations

import os
import ssl
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .errors import Refused
from .profile import Profile


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def download_config_export(profile: Profile, insecure_tls: bool, job_id: Any, download_url: Any, path: str) -> str:
    """Download a one-time v27 URL, enforcing same-origin and exclusive mode-0600 local storage."""
    if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id < 1:
        raise Refused("config-export-download", "core.download returned an invalid job ID")
    if not isinstance(download_url, str) or not download_url:
        raise Refused("config-export-download", "core.download returned an invalid one-time URL")
    target = Path(path).expanduser()
    if not target.is_absolute() or target.suffix.casefold() != ".tar":
        raise Refused("config-export-path", "the local config export path must be absolute and end in .tar")
    if not target.parent.is_dir():
        raise Refused("config-export-path", "the local config export parent directory must already exist")

    origin = urllib.parse.urlsplit(profile.url)
    scheme = {"wss": "https", "ws": "http"}.get(origin.scheme)
    if scheme is None or not origin.netloc:
        raise Refused("config-export-origin", "the selected profile has no supported WebSocket origin")
    base = urllib.parse.urlunsplit((scheme, origin.netloc, "", "", ""))
    parsed = urllib.parse.urlsplit(download_url)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != scheme or parsed.netloc.casefold() != origin.netloc.casefold():
            raise Refused("config-export-origin", "TrueNAS returned a download URL outside the selected host origin")
        relative = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
    else:
        relative = download_url
        parsed = urllib.parse.urlsplit(relative)
    expected_path = f"/_download/{job_id}"
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.path != expected_path or parsed.fragment or set(query) != {"auth_token"}
            or len(query["auth_token"]) != 1 or not query["auth_token"][0]):
        raise Refused("config-export-url", "TrueNAS returned a malformed single-use config download URL")
    url = urllib.parse.urljoin(base, relative)
    context = ssl._create_unverified_context() if insecure_tls and scheme == "https" else ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(), urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/octet-stream"})
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError:
        raise Refused("config-export-path", f"refusing to overwrite existing local path {target}") from None
    except OSError as exc:
        raise Refused("config-export-path", f"cannot create local config export: {exc.strerror or type(exc).__name__}") from None

    size = 0
    try:
        os.fchmod(fd, 0o600)
        with opener.open(request, timeout=120) as response:
            if response.status != 200:
                raise Refused("config-export-download", f"the TrueNAS download endpoint returned HTTP {response.status}")
            while chunk := response.read(1024 * 1024):
                view = memoryview(chunk)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("short write")
                    size += written
                    view = view[written:]
        if size == 0:
            raise Refused("config-export-download", "the config export download was empty")
        os.fsync(fd)
    except Refused:
        os.close(fd)
        try:
            target.unlink()
        except OSError:
            pass
        raise
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        os.close(fd)
        try:
            target.unlink()
        except OSError:
            pass
        if isinstance(exc, urllib.error.HTTPError):
            reason = f"HTTP {exc.code}"
        elif isinstance(exc, urllib.error.URLError):
            reason = type(exc.reason).__name__
        else:
            reason = type(exc).__name__
        raise Refused("config-export-download", f"the TrueNAS config download failed ({reason})") from None
    else:
        os.close(fd)
    try:
        with tarfile.open(target, mode="r:*") as archive:
            if not archive.getmembers():
                raise tarfile.ReadError("empty archive")
    except (OSError, tarfile.TarError):
        try:
            target.unlink()
        except OSError:
            pass
        raise Refused("config-export-download", "the TrueNAS config export was not a valid non-empty tar archive") from None
    return str(target)
