"""Rigor evidence at publication (rigor-evidence design C-2).

The optional ``record_step`` (see :mod:`cmru.record_step`) hands the ``push`` step two
validated files through ``CMRU_RECORD_JSON`` and ``CMRU_RECORD_BODY``. The GitHub
Release-body handlers (``wheel-publish``, ``tarball-publish``, ``oci-image-release``)
turn them into:

* a ``## Rigor at publication`` appendix at the END of the release body, and
* one release asset ``<prefix>-v<version>-rigor.json`` plus its ``.sha256`` sidecar.

Both are inert when neither variable is set, so a project without a record step
publishes byte-identically to before. ``oci-image-release`` otherwise uploads no
asset: exactly these two files are its single named exception.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Iterator, List, NoReturn, Optional

from cmru import exit_codes
from cmru.record_step import RECORD_BODY_MAX_BYTES, RECORD_JSON_MAX_BYTES

APPENDIX_HEADING = "## Rigor at publication"
RECORD_JSON_ENV = "CMRU_RECORD_JSON"
RECORD_BODY_ENV = "CMRU_RECORD_BODY"


def rigor_asset_name(prefix: str, version: str) -> str:
    return f"{prefix}-v{version}-rigor.json"


def _refuse(message: str) -> NoReturn:
    print(f"[ERROR] rigor record: {message}", file=sys.stderr)
    raise SystemExit(exit_codes.REFUSED)


def _declared() -> Optional[tuple[Path, Path]]:
    """``(record.json, release-body.md)`` from the protected environment, or None.

    Both or neither: one without the other is a broken push-step environment and
    refuses before any GitHub call.
    """
    json_value = (os.environ.get(RECORD_JSON_ENV) or "").strip()
    body_value = (os.environ.get(RECORD_BODY_ENV) or "").strip()
    if not json_value and not body_value:
        return None
    if not json_value or not body_value:
        _refuse(f"{RECORD_JSON_ENV} and {RECORD_BODY_ENV} must be set together")
    return Path(json_value), Path(body_value)


def _read_checked(path: Path, limit: int, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        _refuse(f"{label} {path} is not a regular file")
    data = path.read_bytes()
    if not data or len(data) > limit:
        _refuse(f"{label} must be 1..{limit} bytes, got {len(data)}")
    return data


def appendix_from_env() -> Optional[str]:
    """The release-body appendix, or None when the record step produced nothing."""
    declared = _declared()
    if declared is None:
        return None
    raw = _read_checked(declared[1], RECORD_BODY_MAX_BYTES, "release-body.md")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _refuse("release-body.md is not valid UTF-8")
    return f"{APPENDIX_HEADING}\n\n{text.strip()}\n"


def dry_run_appendix(step: str) -> str:
    """What ``cmru release --dry-run`` prints where the appendix will go."""
    return (
        f"{APPENDIX_HEADING}\n\n"
        f"(the body written by record step {step!r} to release-body.md at release time "
        f"appears here; record.json is uploaded as <prefix>-v<version>-rigor.json "
        f"with a .sha256 sidecar)\n"
    )


@contextlib.contextmanager
def staged_assets(prefix: str, version: str) -> Iterator[List[Path]]:
    """Yield ``[<prefix>-v<version>-rigor.json, its .sha256]`` staged in a temp dir.

    Yields ``[]`` when the record step produced nothing (inert). The staged copy
    keeps the validated record untouched (the keystone writes the sidecar beside it).
    """
    from cmru.release import write_sha256_sidecar

    declared = _declared()
    if declared is None:
        yield []
        return
    data = _read_checked(declared[0], RECORD_JSON_MAX_BYTES, "record.json")
    with tempfile.TemporaryDirectory(prefix="cmru-rigor-") as directory:
        asset = Path(directory) / rigor_asset_name(prefix, version)
        asset.write_bytes(data)
        yield [asset, write_sha256_sidecar(asset)]


def verify_uploaded(gh, release_id: object, tag: str, assets: List[Path]) -> None:
    """Read the release's asset list back: every staged name present at its size."""
    if not assets:
        return
    if type(release_id) is not int or release_id <= 0:
        _refuse(f"cannot verify the rigor assets of {tag}: no release ID was returned")
    present = {a.get("name"): a.get("size") for a in gh.list_assets(release_id)}
    problems = [
        asset.name for asset in assets
        if present.get(asset.name) != asset.stat().st_size
    ]
    if problems:
        _refuse(
            f"{tag} lacks the rigor asset(s) {problems} (missing or wrong size) "
            "after upload. Re-run the publish step: it replaces same-named assets"
        )
