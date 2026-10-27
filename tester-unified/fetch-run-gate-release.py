#!/usr/bin/env python3
"""Fetch the PUBLISHED Run-Gate wheel for a tester-unified image, sha256-verified.

A tester-unified image that serves another tool's release gate (cmru, assay, ...)
must carry the exact bytes of the PUBLISHED Run-Gate release that the host judge
runs: CMRU's release gate compares the image's ``org.vbpub.run-gate.version`` and
``org.vbpub.run-gate.wheel-sha256`` labels with the published wheel of the
installed Run-Gate version and refuses any other image (for example one built
from a development source wheel, ``24.0.2.devN+gHASH``).  This script downloads
``run_gate-<version>-py3-none-any.whl`` and its ``.sha256`` sidecar from the
``run-gate-v<version>`` GitHub release, checks the digest, and writes the single
wheel into ``--dest``; pass that directory to ``build-image.sh --wheel-dir``
(``build-image.sh --run-gate-release VERSION`` does both).  Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Callable

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_fetch_cli_extended", _HERE / "fetch-cli-extended.py")
assert _spec is not None and _spec.loader is not None
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)

RELEASE_URL = _shared.ALLOWED_URL_PREFIX + "run-gate-v{version}/"
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


class FetchError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"fetch-run-gate-release: {message}")


def fetch_release(version: str, dest: Path, fetch: Callable[[str], bytes] | None = None) -> Path:
    if _VERSION.fullmatch(version) is None:
        raise FetchError(f"{version!r} is not a stable SemVer release (X.Y.Z)")
    fetch = fetch or _shared._urlopen_bytes
    name = f"run_gate-{version}-py3-none-any.whl"
    base = RELEASE_URL.format(version=version)
    try:
        sidecar = fetch(_shared._check_url(base + name + ".sha256")).decode("utf-8").split()
    except (OSError, UnicodeError, SystemExit) as exc:
        raise FetchError(f"cannot read the checksum sidecar of run-gate-v{version}: {exc}") from exc
    expected = sidecar[0].lower() if sidecar else ""
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise FetchError(f"the checksum sidecar of run-gate-v{version} has no valid sha256")
    try:
        payload = fetch(_shared._check_url(base + name))
    except (OSError, SystemExit) as exc:
        raise FetchError(f"cannot download {name}: {exc}") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise FetchError(f"sha256 mismatch for {name}: sidecar {expected}, downloaded {actual}")
    dest.mkdir(parents=True, exist_ok=True)
    stale = [path for path in dest.glob("run_gate-*.whl")]
    if stale:
        raise FetchError(f"{dest} already holds a Run-Gate wheel ({stale[0].name}); use an empty directory")
    target = dest / name
    target.write_bytes(payload)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="published stable release, e.g. 24.0.1")
    parser.add_argument("--dest", type=Path, required=True, help="empty directory for the wheel")
    args = parser.parse_args(argv)
    sys.stdout.write(f"{fetch_release(args.version, args.dest)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
