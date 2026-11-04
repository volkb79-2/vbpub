"""Operator-supplied browser storage state, scoped to one origin (human-in-the-loop path).

A HUMAN solves a bot-protection challenge once in their own browser and exports the
session as a Playwright storage-state file; ``pwmcp inspect --storage-state FILE`` reuses
it. Nothing here solves or bypasses a challenge.

The file holds session cookies, so it is a secret: it must be mode 0600 (no group or other
access), only the cookies and origins that belong to the target URL's host are handed to
the browser, and neither the file's content nor any cookie value ever appears in a message,
log line or result (only counts do).
"""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class StorageStateError(Exception):
    """The storage-state file cannot be used. The message never contains file content."""


@dataclass(frozen=True)
class ScopedState:
    """The part of a storage-state file that applies to one URL, plus its counts."""

    state: dict[str, object]
    cookies: int
    origins: int


def _host_matches(host: str, domain: str) -> bool:
    domain = domain.lstrip(".").lower()
    return bool(domain) and (host == domain or host.endswith("." + domain))


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:  # an out-of-range port in a hand-edited file simply never matches
        return scheme, (parts.hostname or "").lower(), -1
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return scheme, (parts.hostname or "").lower(), port


def load_scoped(path: str | Path, url: str) -> ScopedState:
    """Read ``path`` and keep only what applies to ``url``'s host; refuse an unsafe file."""

    file = Path(path).expanduser()
    try:
        info = file.stat()
    except OSError as exc:
        raise StorageStateError(f"cannot read the storage-state file {file}: {exc.strerror or 'unreadable'}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise StorageStateError(f"the storage-state file {file} is not a regular file")
    if info.st_mode & 0o077:
        raise StorageStateError(
            f"the storage-state file {file} is accessible by group or others "
            f"(mode {stat.S_IMODE(info.st_mode):04o}); it holds session cookies, run: chmod 600 {file}"
        )
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # JSON errors can quote content; say only that the file is not usable.
        raise StorageStateError(f"the storage-state file {file} is not readable JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("cookies", []), list) or not isinstance(
        data.get("origins", []), list
    ):
        raise StorageStateError(
            f"the storage-state file {file} is not a Playwright storage state "
            '(an object with "cookies" and "origins" lists)'
        )

    host = _origin(url)[1]
    cookies = [
        cookie
        for cookie in data.get("cookies", [])
        if isinstance(cookie, dict) and isinstance(cookie.get("domain"), str) and _host_matches(host, cookie["domain"])
    ]
    target = _origin(url)
    origins = [
        entry
        for entry in data.get("origins", [])
        if isinstance(entry, dict) and isinstance(entry.get("origin"), str) and _origin(entry["origin"]) == target
    ]
    if not cookies and not origins:
        raise StorageStateError(
            f"the storage-state file {file} holds no cookies or origins for {host}; "
            "export it from a session on that site"
        )
    return ScopedState({"cookies": cookies, "origins": origins}, len(cookies), len(origins))
