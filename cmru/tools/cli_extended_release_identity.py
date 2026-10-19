#!/usr/bin/env python3
"""Bind a tester-image build to the exact cli-extended bytes that were released.

``tester-unified/Dockerfile`` embeds a *pinned* cli-extended asset URL and
SHA-256 as its default. The first stable ``cli-extended`` release is built from
this checkout, so its digest is NOT the one already embedded; an image built in
the default ``pinned`` mode afterwards would silently keep the old bytes. This
check compares three identities and prints the build mode that is safe:

* the digest ``cmru release cli-extended`` printed for the published wheel
  (``CLI-EXTENDED_WHEEL_SHA256=``), which the caller supplies;
* the ``cli-extended-latest/latest.json`` pointer the ``latest`` mode reads;
* the pin embedded in the Dockerfile.

The pointer disagreeing with the published digest (stale, replaced, or
unreachable) is a refusal, never "nothing released yet". Output is
``KEY=value`` lines, so a shell can ``eval`` or ``source`` it: every value is
validated against its exact shape and shell-quoted, because the pointer is
remote data. Stdlib only: it runs on a host that has no CMRU installed yet.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ALLOWED_URL_PREFIX = "https://github.com/volkb79-2/vbpub/releases/download/"
POINTER_URL = ALLOWED_URL_PREFIX + "cli-extended-latest/latest.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_RELEASE_VERSION = r"[0-9]+(?:\.[0-9]+)*"
#: The only asset shape a cli-extended release publishes; nothing else is
#: trusted enough to reach a shell, even quoted.
_WHEEL_URL = re.compile(
    re.escape(ALLOWED_URL_PREFIX)
    + rf"cli-extended-v(?P<tag>{_RELEASE_VERSION})/"
    + rf"cli_extended-(?P<version>{_RELEASE_VERSION})-py3-none-any\.whl"
)


class IdentityError(SystemExit):
    """A refusal: the message goes to stderr and the exit status is 2."""

    def __init__(self, message: str) -> None:
        print(f"cli-extended-release-identity: {message}", file=sys.stderr)
        super().__init__(2)


def _dockerfile_pin(dockerfile: Path) -> tuple[str, str]:
    try:
        text = dockerfile.read_text(encoding="utf-8")
    except OSError as exc:
        raise IdentityError(f"cannot read {dockerfile}: {exc}") from exc
    values = {}
    for name in ("CLI_EXTENDED_WHEEL_URL", "CLI_EXTENDED_WHEEL_SHA256"):
        match = re.search(rf"^ARG {name}=(\S+)\s*$", text, re.MULTILINE)
        if match is None:
            raise IdentityError(f"{dockerfile} declares no `ARG {name}=` default")
        values[name] = match.group(1)
    return values["CLI_EXTENDED_WHEEL_URL"], values["CLI_EXTENDED_WHEEL_SHA256"]


def _read_pointer(pointer_file: Path | None, pointer_url: str) -> dict:
    try:
        if pointer_file is not None:
            raw = pointer_file.read_bytes()
        else:
            parts = urllib.parse.urlsplit(pointer_url)
            if parts.scheme != "https" or not pointer_url.startswith(ALLOWED_URL_PREFIX):
                raise IdentityError(f"refusing pointer URL {pointer_url!r}: not a vbpub release URL")
            with urllib.request.urlopen(pointer_url, timeout=60) as response:  # noqa: S310
                raw = response.read()
        document = json.loads(raw)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise IdentityError(f"cannot read the release pointer: {exc}") from exc
    if not isinstance(document, dict):
        raise IdentityError("the release pointer is not a JSON object")
    return document


def resolve(
    released_sha256: str, pointer: dict, pin_url: str, pin_sha256: str,
) -> dict[str, str]:
    """Return the identities and the tester-image resolve mode, or refuse."""
    if not _SHA256.fullmatch(released_sha256):
        raise IdentityError("--released-sha256 must be 64 lowercase hex digits")
    pointer_sha = pointer.get("sha256")
    pointer_url = pointer.get("url")
    if not isinstance(pointer_sha, str) or not isinstance(pointer_url, str):
        raise IdentityError("the release pointer lacks string `sha256` and `url` fields")
    if pointer_sha != released_sha256:
        raise IdentityError(
            f"the cli-extended-latest pointer names sha256 {pointer_sha}, not the "
            f"published wheel {released_sha256}; a `latest` image build would not "
            "contain the released bytes. Wait for the pointer to publish, or "
            "investigate a replaced release."
        )
    if not pointer_url.startswith(ALLOWED_URL_PREFIX):
        raise IdentityError(f"pointer url {pointer_url!r} is outside {ALLOWED_URL_PREFIX}")
    wheel = _WHEEL_URL.fullmatch(pointer_url)
    if wheel is None or wheel.group("tag") != wheel.group("version"):
        raise IdentityError(
            f"pointer url {pointer_url!r} is not a cli-extended release wheel URL "
            f"({ALLOWED_URL_PREFIX}cli-extended-v<version>/cli_extended-<version>-py3-none-any.whl)"
        )
    pointer_version = pointer.get("version")
    if pointer_version != wheel.group("version"):
        raise IdentityError(
            f"the release pointer version {pointer_version!r} does not name its wheel "
            f"version {wheel.group('version')!r}"
        )
    if not _SHA256.fullmatch(pin_sha256):
        raise IdentityError("the Dockerfile's pinned CLI_EXTENDED_WHEEL_SHA256 is not 64 lowercase hex digits")
    pin_current = pin_sha256 == released_sha256 and pin_url == pointer_url
    return {
        "CLI_EXTENDED_RELEASED_SHA256": released_sha256,
        "CLI_EXTENDED_RELEASED_URL": pointer_url,
        "CLI_EXTENDED_RELEASED_VERSION": pointer_version,
        "CLI_EXTENDED_DOCKERFILE_PIN_SHA256": pin_sha256,
        "CLI_EXTENDED_DOCKERFILE_PIN": "current" if pin_current else "stale",
        # `pinned` is safe only when the embedded pin IS the released asset.
        "CLI_EXTENDED_RESOLVE": "pinned" if pin_current else "latest",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--released-sha256", required=True,
                        help="digest printed by `cmru release cli-extended` (CLI-EXTENDED_WHEEL_SHA256)")
    parser.add_argument("--dockerfile", type=Path, required=True,
                        help="tester-unified/Dockerfile whose pinned default is compared")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--pointer-file", type=Path, help="read latest.json from a local file")
    group.add_argument("--pointer-url", default=POINTER_URL)
    args = parser.parse_args(argv)
    pin_url, pin_sha = _dockerfile_pin(args.dockerfile)
    pointer = _read_pointer(args.pointer_file, args.pointer_url)
    for key, value in resolve(args.released_sha256, pointer, pin_url, pin_sha).items():
        print(f"{key}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
