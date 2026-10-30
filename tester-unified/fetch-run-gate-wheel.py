#!/usr/bin/env python3
"""Fetch the PUBLISHED Run-Gate wheel for a tester-unified image build, digest-verified.

The CMRU release gate refuses a tester-unified image whose ``org.vbpub.run-gate.version`` /
``org.vbpub.run-gate.wheel-sha256`` labels are not the exact bytes of the stable release the host's
installed judge came from (``cmru/tools/run_release_gate.py::_check_tester_image_identity``).  An
image built from a developer ``run-gate-project/dist`` wheel (``24.0.2.dev...``) therefore cannot
gate anything.  This helper stages the released wheel instead:

* default: read the thin ``run-gate-latest/latest.json`` pointer (``version``, ``tag``, ``asset``,
  ``sha256``, ``url``);
* ``--version X.Y.Z``: take the versioned release ``run-gate-vX.Y.Z`` directly.

Either way three independent statements of the digest must agree before a file is written: the
pointer (default mode only), the release's ``<asset>.sha256`` sidecar, and the hash of the downloaded
bytes.  Only https downloads from this repository's GitHub Releases are accepted (the redirect policy
is the one ``fetch-cli-extended.py`` already enforces).  Stdlib only.  The staged directory holds
exactly one ``run_gate-<version>-py3-none-any.whl``, which is what ``build-image.sh --wheel-dir``
(and ``run-gate-wheel-version.py``) consume.

No age window is applied: this is a first-party wheel and the gate requires THE published release,
not an aged one.  Third-party packages are aged by the Dockerfile (``PIP_MIN_RELEASE_AGE``).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Callable

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_fetch_cli_extended", _HERE / "fetch-cli-extended.py")
assert _spec is not None and _spec.loader is not None
_net = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_net)

RELEASES = "https://github.com/volkb79-2/vbpub/releases/download/"
POINTER_URL = RELEASES + "run-gate-latest/latest.json"
_SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class FetchError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"fetch-run-gate-wheel: {message}")


def _asset_name(version: str) -> str:
    return f"run_gate-{version}-py3-none-any.whl"


def _asset_url(version: str) -> str:
    return f"{RELEASES}run-gate-v{version}/{_asset_name(version)}"


def _digest(value: object, what: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise FetchError(f"{what} has no valid sha256 (64 lowercase hex digits required)")
    return value


def resolve(version: str | None, pointer_url: str, fetch: Callable[[str], bytes]) -> tuple[str, str | None]:
    """Return ``(version, pointer_sha256_or_None)``."""
    if version is not None:
        if not _SEMVER.fullmatch(version):
            raise FetchError(f"--version {version!r} is not a stable X.Y.Z version")
        return version, None
    try:
        pointer = json.loads(fetch(_net._check_url(pointer_url)).decode("utf-8"))
    except (ValueError, OSError) as exc:
        raise FetchError(f"cannot read the release pointer {pointer_url}: {exc}") from exc
    if not isinstance(pointer, dict):
        raise FetchError("the release pointer is not a JSON object")
    found = pointer.get("version")
    if not isinstance(found, str) or not _SEMVER.fullmatch(found):
        raise FetchError(f"the release pointer version {found!r} is not a stable X.Y.Z version")
    if pointer.get("project") != "run-gate" or pointer.get("tag") != f"run-gate-v{found}":
        raise FetchError("the release pointer does not describe run-gate-v" + found)
    if pointer.get("asset") != _asset_name(found) or pointer.get("url") != _asset_url(found):
        raise FetchError("the release pointer names an asset or url other than the versioned release wheel")
    return found, _digest(pointer.get("sha256"), "the release pointer")


def fetch_wheel(
    version: str | None, dest: Path, fetch: Callable[[str], bytes] | None = None,
    pointer_url: str = POINTER_URL,
) -> Path:
    fetch = fetch or _net._urlopen_bytes
    resolved, pointer_sha = resolve(version, pointer_url, fetch)
    name = _asset_name(resolved)
    url = _net._check_url(_asset_url(resolved))
    try:
        sidecar = fetch(url + ".sha256").decode("utf-8").split()
    except (OSError, UnicodeError) as exc:
        raise FetchError(f"cannot read the checksum sidecar of {name}: {exc}") from exc
    sidecar_sha = _digest(sidecar[0].lower() if sidecar else "", f"the {name}.sha256 sidecar")
    if len(sidecar) > 1 and sidecar[1].lstrip("*") != name:
        raise FetchError(f"the sidecar names {sidecar[1]!r}, not {name!r}")
    if pointer_sha is not None and pointer_sha != sidecar_sha:
        raise FetchError(f"the release pointer ({pointer_sha}) and the sidecar ({sidecar_sha}) disagree")
    try:
        payload = fetch(url)
    except OSError as exc:
        raise FetchError(f"cannot download {url}: {exc}") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if actual != sidecar_sha:
        raise FetchError(f"sha256 mismatch for {name}: expected {sidecar_sha}, got {actual}")
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FetchError(f"cannot stage {name} into {dest}: {exc}") from exc
    if any(dest.glob("run_gate-*.whl")):
        raise FetchError(f"{dest} already holds a run_gate wheel; stage into an empty directory")
    # Atomic and exclusive (temporary name, link(2) into place): a name planted between the glob and
    # the link is refused rather than followed, and a failed write leaves no partial wheel behind.
    return _net._stage(dest, name, payload, FetchError)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, required=True, help="empty directory to stage the wheel into")
    parser.add_argument("--version", default=None, help="stable X.Y.Z; default: the run-gate-latest pointer")
    args = parser.parse_args(argv)
    sys.stdout.write(f"{fetch_wheel(args.version, args.dest)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
