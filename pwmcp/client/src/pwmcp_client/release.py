"""Server release string <-> client wheel version, and the client/server skew policy.

The pwmcp-client wheel is versioned 1:1 with the server release. A server release is
``<playwright>-r<N>`` (``1.64.1-r2``); PEP 440 reads ``-rN`` as the post-release segment,
so the wheel version is the canonical ``<playwright>.post<N>`` (``1.64.1.post2``).

This module is stdlib-only and is the ONE place that maps in both directions: the
installed CLI imports it, and the release resolver (``scripts/resolve-playwright-version.py``)
loads this very file by path, so the generated ``VERSION`` file can never drift from the
mapping the client applies at runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RELEASE_RE = re.compile(r"(\d+\.\d+\.\d+)-r(\d+)")
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)\.post(\d+)")


def release_to_version(release: str) -> str:
    """``1.64.1-r2`` -> ``1.64.1.post2`` (the PEP 440 canonical form of the same string)."""

    match = _RELEASE_RE.fullmatch(release.strip())
    if match is None:
        raise ValueError(f"not a pwmcp release (expected <major>.<minor>.<patch>-r<N>): {release!r}")
    return f"{match.group(1)}.post{int(match.group(2))}"


def version_to_release(version: str) -> str:
    """``1.64.1.post2`` -> ``1.64.1-r2`` (inverse of :func:`release_to_version`)."""

    match = _VERSION_RE.fullmatch(version.strip())
    if match is None:
        raise ValueError(
            f"not a pwmcp client version (expected <major>.<minor>.<patch>.post<N>): {version!r}"
        )
    return f"{match.group(1)}-r{int(match.group(2))}"


def split_release(release: str) -> tuple[str, int]:
    """``1.64.1-r2`` -> ``("1.64.1", 2)``: the Playwright version and the r-number."""

    match = _RELEASE_RE.fullmatch(release.strip())
    if match is None:
        raise ValueError(f"not a pwmcp release (expected <major>.<minor>.<patch>-r<N>): {release!r}")
    return match.group(1), int(match.group(2))


@dataclass(frozen=True)
class Skew:
    """Outcome of comparing the client release with the server release."""

    status: str  # "ok" | "warn" | "fail"
    message: str


def assess_skew(client_version: str, server_release: str) -> Skew:
    """Compare a client wheel version with the server's contract release.

    * identical release: ok.
    * same Playwright version, different r-number: warn (client and server may differ in
      helper/contract details, but the Playwright wire protocol is the same).
    * different Playwright version: fail (hard incompatibility: the client's pinned
      ``playwright`` package cannot drive the server's browser).
    A value that is not a pwmcp release/version at all (a development build) is a warning.
    """

    try:
        client_release = version_to_release(client_version)
        client_pw, client_n = split_release(client_release)
        server_pw, server_n = split_release(server_release)
    except ValueError as exc:
        return Skew("warn", f"cannot compare client {client_version} with server {server_release}: {exc}")
    if client_release == server_release.strip():
        return Skew("ok", f"client and server are the same release ({server_release})")
    if client_pw != server_pw:
        return Skew(
            "fail",
            f"client {client_release} pins Playwright {client_pw} but the server runs "
            f"{server_release} (Playwright {server_pw})",
        )
    return Skew(
        "warn",
        f"client release {client_release} differs from server release {server_release} "
        f"(same Playwright {server_pw}, r{client_n} vs r{server_n})",
    )
