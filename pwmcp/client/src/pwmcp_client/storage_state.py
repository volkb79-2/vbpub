"""Operator-supplied browser storage state, scoped to one origin (human-in-the-loop path).

A HUMAN solves a bot-protection challenge once in their own browser and exports the
session as a Playwright storage-state file; ``pwmcp inspect --storage-state FILE`` reuses
it. Nothing here solves or bypasses a challenge.

The file holds session cookies, so it is a secret: it must be owned by the current user and mode 0600 (no group or other
access), only the cookies and origins that belong to the target URL's host are handed to
the browser, and neither the file's content nor any cookie value ever appears in a message,
log line or result (only counts do).
"""

from __future__ import annotations

import json
import os
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


# Second-level labels that make a two-label domain a public suffix (``co.uk``, ``com.au``).
_SUFFIX_SLDS = frozenset({"co", "com", "org", "net", "gov", "edu", "ac", "or", "ne", "go"})


def _is_bare_suffix(domain: str) -> bool:
    """A bare TLD (``com``) or a ``co.uk``-style public suffix: never a valid cookie scope."""

    labels = domain.split(".")
    if len(labels) < 2:
        return True
    return len(labels) == 2 and labels[0] in _SUFFIX_SLDS and len(labels[1]) == 2


def _host_matches(host: str, domain: str) -> bool:
    """The cookie domain is the host itself, or a parent of it that is not a public suffix."""

    domain = domain.lstrip(".").lower()
    if not domain:
        return False
    if host == domain:
        return True
    return host.endswith("." + domain) and not _is_bare_suffix(domain)


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


def _check(file: Path, info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise StorageStateError(f"the storage-state file {file} is not a regular file")
    if info.st_uid != os.geteuid():
        raise StorageStateError(f"the storage-state file {file} is not owned by the current user")
    if info.st_mode & 0o077:
        raise StorageStateError(
            f"the storage-state file {file} is accessible by group or others "
            f"(mode {stat.S_IMODE(info.st_mode):04o}); it holds session cookies, run: chmod 600 {file}"
        )


def load_scoped(path: str | Path, url: str) -> ScopedState:
    """Read ``path`` and keep only what applies to ``url``'s host; refuse an unsafe file."""

    file = Path(path).expanduser()
    try:
        fd = os.open(file, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise StorageStateError(f"cannot read the storage-state file {file}: {exc.strerror or 'unreadable'}") from exc
    # Open once and judge the descriptor itself, so the checked file is the file that is read.
    try:
        info = os.fstat(fd)
        _check(file, info)
        handle = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with handle:
        try:
            data = json.loads(handle.read().decode("utf-8"))
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
