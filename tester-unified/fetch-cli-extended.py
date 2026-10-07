#!/usr/bin/env python3
"""Fetch the RELEASED cli-extended wheel for the tester-unified image, sha256-verified.

``cli-extended`` is published to GitHub Releases only (never PyPI: the bare name is
unclaimed there, a dependency-confusion hole, CX-D2).  cmru declares it as a real
dependency, so the image installs it from the release, by digest, BEFORE cmru:

* default: read the thin ``cli-extended-latest/latest.json`` pointer (``version``,
  ``asset``, ``sha256``, ``url``), download the asset it names;
* pinned: ``--wheel-url`` plus ``--sha256`` skip the pointer and fetch exactly that asset.

Either way the sha256 is MANDATORY and compared before the file is written to its final
name; the script prints the wheel path.  Stdlib only (it runs under the image's system
python before any venv exists).  The caller installs the result with
``pip install --no-index --no-deps <wheel>``; no index is ever queried for this name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

#: Releases are only ever fetched from this repository's GitHub Releases.
ALLOWED_URL_PREFIX = "https://github.com/volkb79-2/vbpub/releases/download/"
POINTER_URL = ALLOWED_URL_PREFIX + "cli-extended-latest/latest.json"

_SHA256 = re.compile(r"[0-9a-f]{64}")
_WHEEL = re.compile(r"cli_extended-(?P<version>[0-9][0-9A-Za-z.]*)-py3-none-any\.whl")


class FetchError(SystemExit):
    """A refusal; the message goes to stderr and the exit status is 1."""

    def __init__(self, message: str) -> None:
        super().__init__(f"fetch-cli-extended: {message}")


#: Hosts a release download may be redirected to (GitHub serves release assets from
#: its object storage hosts); the pointer's own host is added per fetch.
ALLOWED_REDIRECT_HOSTS = frozenset({
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "raw.githubusercontent.com",
})


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect to anything but https on an allowlisted host (B2)."""

    def __init__(self, extra_hosts: frozenset[str] = frozenset()) -> None:
        self.allowed_hosts = ALLOWED_REDIRECT_HOSTS | extra_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https":
            raise FetchError(f"refusing redirect to {newurl!r}: not https")
        if (target.hostname or "") not in self.allowed_hosts:
            raise FetchError(f"refusing redirect to {newurl!r}: host not allowed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(url: str) -> urllib.request.OpenerDirector:
    host = urllib.parse.urlsplit(url).hostname or ""
    return urllib.request.build_opener(_HttpsOnlyRedirects(frozenset({host})))


def _urlopen_bytes(url: str) -> bytes:
    with _opener(url).open(url, timeout=60) as response:  # noqa: S310 (https, prefix-checked)
        return response.read()


def _version_key(version: str) -> tuple[int, ...]:
    parts = version.split(".")
    if not all(part.isdigit() for part in parts):
        raise FetchError(f"version {version!r} is not a plain release version")
    return tuple(int(part) for part in parts)


def _at_least(version: str, floor: str) -> bool:
    """Compare release versions with the shorter tuple zero-padded (0.2 == 0.2.0)."""
    have, want = _version_key(version), _version_key(floor)
    width = max(len(have), len(want))
    return have + (0,) * (width - len(have)) >= want + (0,) * (width - len(want))


def _check_url(url: str) -> str:
    if not url.startswith(ALLOWED_URL_PREFIX):
        raise FetchError(f"refusing {url!r}: not under {ALLOWED_URL_PREFIX}")
    return url


def _digest(value: object, what: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise FetchError(f"{what} has no valid sha256 (64 lowercase hex digits required)")
    return value


def resolve(
    pointer_url: str, wheel_url: str | None, sha256: str | None,
    fetch: Callable[[str], bytes],
) -> tuple[str, str]:
    """Return ``(wheel_url, expected_sha256)`` from a pin or from the pointer."""
    if wheel_url or sha256:
        if not (wheel_url and sha256):
            raise FetchError("--wheel-url and --sha256 must be given together")
        return _check_url(wheel_url), _digest(sha256, "--sha256")
    try:
        pointer = json.loads(fetch(_check_url(pointer_url)).decode("utf-8"))
    except (ValueError, OSError) as exc:
        raise FetchError(f"cannot read the release pointer {pointer_url}: {exc}") from exc
    if not isinstance(pointer, dict):
        raise FetchError("the release pointer is not a JSON object")
    url = pointer.get("url")
    if not isinstance(url, str):
        raise FetchError("the release pointer has no url")
    name = url.rsplit("/", 1)[-1]
    if pointer.get("asset") != name:
        raise FetchError(f"the release pointer asset {pointer.get('asset')!r} is not the url's file name {name!r}")
    named = _WHEEL.fullmatch(name)
    if named is not None and pointer.get("version") != named["version"]:
        raise FetchError(
            f"the release pointer version {pointer.get('version')!r} is not the wheel's {named['version']!r}"
        )
    return _check_url(url), _digest(pointer.get("sha256"), "the release pointer")


def fetch_wheel(
    pointer_url: str, wheel_url: str | None, sha256: str | None, dest: Path,
    min_version: str, fetch: Callable[[str], bytes] | None = None,
) -> Path:
    fetch = fetch or _urlopen_bytes
    url, expected = resolve(pointer_url, wheel_url, sha256, fetch)
    name = url.rsplit("/", 1)[-1]
    match = _WHEEL.fullmatch(name)
    if match is None:
        raise FetchError(f"{name!r} is not a cli_extended py3-none-any wheel file name")
    if not _at_least(match["version"], min_version):
        raise FetchError(f"{name} is older than the declared floor {min_version}")
    try:
        payload = fetch(url)
    except OSError as exc:
        raise FetchError(f"cannot download {url}: {exc}") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise FetchError(f"sha256 mismatch for {name}: expected {expected}, got {actual}")
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / name
    target.write_bytes(payload)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--min-version", required=True,
                        help="the floor cmru declares (pyproject cli-extended>=X)")
    parser.add_argument("--pointer-url", default=POINTER_URL)
    parser.add_argument("--wheel-url", default=None)
    parser.add_argument("--sha256", default=None)
    args = parser.parse_args(argv)
    wheel = fetch_wheel(
        args.pointer_url, args.wheel_url or None, args.sha256 or None,
        args.dest, args.min_version,
    )
    sys.stdout.write(f"{wheel}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
