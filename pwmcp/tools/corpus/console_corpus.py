#!/usr/bin/env python3
"""Package and verify the local PWMCP console-frame corpus.

The 242-frame capture is kept outside the repository and is never published. The
repository carries its frame index and a pin for a deterministic tar of the
frames. Set ``PWMCP_CONSOLE_CORPUS_DIR`` to an absolute directory containing
``frames/`` to opt in to verification. This tool reads local files only.

Commands (``-h`` per command): ``package`` builds the deterministic tar,
``verify DIR`` verifies a local corpus directory, and ``locate`` verifies and
prints the directory selected by the environment variable.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
from pathlib import Path

PWMCP = Path(__file__).resolve().parents[2]
CORPUS_ROOT = PWMCP / "tests" / "corpus" / "console"
ASSETS_FILE = CORPUS_ROOT / "CORPUS-ASSETS.json"
ENV_DIR = "PWMCP_CONSOLE_CORPUS_DIR"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FRAME = re.compile(r"[0-9]{4}_[0-9]+s\.png")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class CorpusError(Exception):
    """The pin, index, or explicitly selected local corpus is invalid."""


class CorpusMissing(CorpusError):
    """No local corpus path was selected."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_pin(asset_id: str | None = None, assets_file: Path = ASSETS_FILE) -> dict:
    """Load the single local-source pin; reject release/download-era fields."""

    try:
        document = json.loads(assets_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorpusError(f"cannot read the corpus pin {assets_file}: {exc}") from exc
    assets = document.get("assets") if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get("schema") != 1
            or not isinstance(assets, list) or not assets
            or any(not isinstance(item, dict) for item in assets)):
        raise CorpusError(f"{assets_file}: expected {{'schema': 1, 'assets': [..]}}")
    chosen = [item for item in assets if asset_id in (None, item.get("id"))]
    if len(chosen) != 1:
        raise CorpusError(f"{assets_file}: corpus {asset_id!r} not found exactly once")
    pin = chosen[0]
    required = ("id", "source", "archive", "sha256", "size", "frames", "series_manifest")
    for key in required:
        if key not in pin:
            raise CorpusError(f"{assets_file}: corpus {pin.get('id')!r} lacks {key!r}")
    if "url" in pin or "release_tag" in pin:
        raise CorpusError(f"{assets_file}: local corpus pins cannot contain release URLs or tags")
    if pin["source"] != ENV_DIR:
        raise CorpusError(f"{assets_file}: source must be {ENV_DIR!r}")
    if (not isinstance(pin["id"], str) or not pin["id"]
            or not isinstance(pin["archive"], str) or not pin["archive"]
            or Path(pin["archive"]).name != pin["archive"] or "\\" in pin["archive"]):
        raise CorpusError(f"{assets_file}: id and archive must be non-empty; archive must be a file name")
    if not isinstance(pin["sha256"], str) or not _SHA256.fullmatch(pin["sha256"]):
        raise CorpusError(f"{assets_file}: sha256 for {pin['id']!r} must be 64 lowercase hex digits")
    if type(pin["size"]) is not int or pin["size"] <= 0:
        raise CorpusError(f"{assets_file}: size for {pin['id']!r} must be a positive integer")
    if type(pin["frames"]) is not int or pin["frames"] <= 0:
        raise CorpusError(f"{assets_file}: frames for {pin['id']!r} must be a positive integer")
    manifest = Path(pin["series_manifest"]) if isinstance(pin["series_manifest"], str) else None
    if (manifest is None or manifest.is_absolute() or not manifest.parts
            or ".." in manifest.parts):
        raise CorpusError(f"{assets_file}: series_manifest must stay inside the console corpus directory")
    return pin


def load_index(pin: dict, corpus_root: Path = CORPUS_ROOT) -> dict[str, dict]:
    """Read and validate the committed frame index as ``{filename: record}``."""

    path = corpus_root / pin["series_manifest"]
    index: dict[str, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CorpusError(f"cannot read {path}: {exc}") from exc
    for number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
            name, digest = record["file"], record["sha256"]
        except (ValueError, KeyError, TypeError) as exc:
            raise CorpusError(f"{path}:{number}: bad record: {exc}") from exc
        if (not isinstance(record, dict) or not isinstance(name, str)
                or not isinstance(digest, str)
                or type(record.get("width")) is not int or record["width"] <= 0
                or type(record.get("height")) is not int or record["height"] <= 0):
            raise CorpusError(f"{path}:{number}: file, sha256, positive width and positive height are required")
        if not _FRAME.fullmatch(name) or not _SHA256.fullmatch(digest):
            raise CorpusError(f"{path}:{number}: unexpected file name or digest")
        if name in index:
            raise CorpusError(f"{path}:{number}: duplicate frame {name}")
        index[name] = record
    if len(index) != pin["frames"]:
        raise CorpusError(f"{path}: {len(index)} frames, the pin says {pin['frames']}")
    return index


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != _PNG_MAGIC or data[12:16] != b"IHDR":
        raise CorpusError("not a PNG with an IHDR header")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def verify_frames(directory: Path, index: dict[str, dict]) -> None:
    """Require exactly the indexed regular frame files, hashes, and dimensions."""

    if directory.is_symlink() or not directory.is_dir():
        raise CorpusError(f"{directory} is not a regular directory")
    try:
        present = {path.name for path in directory.iterdir()}
    except OSError as exc:
        raise CorpusError(f"cannot list {directory}: {exc}") from exc
    if present != set(index):
        missing, extra = sorted(set(index) - present), sorted(present - set(index))
        raise CorpusError(
            f"{directory}: missing {missing[:5]} extra {extra[:5]} "
            f"({len(missing)} missing, {len(extra)} extra)"
        )
    for name, record in sorted(index.items()):
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise CorpusError(f"{path} is not a regular file")
        try:
            digest = sha256_file(path)
            with path.open("rb") as handle:
                head = handle.read(24)
        except OSError as exc:
            raise CorpusError(f"cannot read {path}: {exc}") from exc
        if digest != record["sha256"]:
            raise CorpusError(f"{path}: sha256 differs from the series manifest")
        if png_size(head) != (record["width"], record["height"]):
            raise CorpusError(f"{path}: PNG dimensions differ from the series manifest")


def _tar_bytes(frames_dir: Path, index: dict[str, dict]) -> bytes:
    """Build a deterministic ustar of ``frames/<name>`` with fixed metadata."""

    buffer = io.BytesIO()
    try:
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted(index):
                data = (frames_dir / name).read_bytes()
                info = tarfile.TarInfo(f"frames/{name}")
                info.size, info.mtime, info.mode = len(data), 0, 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(data))
    except OSError as exc:
        raise CorpusError(f"cannot read frames from {frames_dir}: {exc}") from exc
    return buffer.getvalue()


def package(frames_dir: Path, out: Path, pin: dict, corpus_root: Path = CORPUS_ROOT) -> dict:
    """Verify the indexed frames, then write their deterministic tar and return its pin."""

    index = load_index(pin, corpus_root)
    verify_frames(frames_dir, index)
    data = _tar_bytes(frames_dir, index)
    try:
        out.write_bytes(data)
    except OSError as exc:
        raise CorpusError(f"cannot write deterministic archive {out}: {exc}") from exc
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def verify_directory(directory: Path, pin: dict, corpus_root: Path = CORPUS_ROOT) -> Path:
    """Verify ``directory/frames`` against both the per-frame index and pinned tar digest."""

    if not directory.is_absolute():
        raise CorpusError(f"{ENV_DIR} must be an absolute path, got {directory}")
    if directory.is_symlink() or not directory.is_dir():
        raise CorpusError(f"{directory} is not a local corpus directory")
    frames = directory / "frames"
    index = load_index(pin, corpus_root)
    verify_frames(frames, index)
    data = _tar_bytes(frames, index)
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != pin["size"] or digest != pin["sha256"]:
        raise CorpusError(
            f"{directory}: deterministic tar size or sha256 differs from the committed pin "
            f"(got {len(data)} bytes, {digest})"
        )
    return frames


def locate(asset_id: str | None = None, *, assets_file: Path = ASSETS_FILE,
           corpus_root: Path = CORPUS_ROOT) -> Path:
    """Verify the explicitly selected local corpus, or report that none was selected."""

    pin = load_pin(asset_id, assets_file)
    if ENV_DIR not in os.environ:
        raise CorpusMissing(
            f"the local console corpus {pin['id']} was not selected; set {ENV_DIR} to an absolute "
            "directory containing frames/. The full capture is kept local and is never published."
        )
    value = os.environ[ENV_DIR]
    if not value:
        raise CorpusError(f"{ENV_DIR} is set but empty; set it to an absolute local corpus directory")
    directory = Path(value)
    try:
        return verify_directory(directory, pin, corpus_root)
    except CorpusError as exc:
        raise CorpusError(f"{ENV_DIR}={directory} is set but does not verify: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("package", help="build the deterministic tar from a verified frames directory")
    pack.add_argument("frames_dir", type=Path)
    pack.add_argument("--out", type=Path, required=True)
    pack.add_argument("--asset", default=None)
    verify = sub.add_parser("verify", help="verify a local corpus directory containing frames/")
    verify.add_argument("directory", type=Path)
    verify.add_argument("--asset", default=None)
    find = sub.add_parser("locate", help="verify the local directory named by PWMCP_CONSOLE_CORPUS_DIR")
    find.add_argument("--asset", default=None)
    args = parser.parse_args(argv)
    try:
        if args.command == "package":
            pin = load_pin(args.asset)
            result = package(args.frames_dir, args.out, pin)
            print(json.dumps({"archive": pin["archive"], **result}, sort_keys=True))
        elif args.command == "verify":
            pin = load_pin(args.asset)
            verify_directory(args.directory, pin)
            print(f"OK: {pin['frames']} frames and deterministic tar pin verified")
        else:
            print(locate(args.asset))
    except CorpusError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
