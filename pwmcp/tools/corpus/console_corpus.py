#!/usr/bin/env python3
"""Package, fetch and verify the pwmcp console-frame corpus (a GitHub release asset, not git content).

The 242-frame TrueNAS install capture (about 5.4 MB of PNG) is too large to commit, so the repository
carries only its INDEX and its pin:

* ``tests/corpus/console/<corpus>/series-manifest.jsonl`` - one record per frame (file name, sha256, size);
* ``tests/corpus/console/CORPUS-ASSETS.json`` - the release tag, asset name, URL, tar sha256 and size.

A test that needs the frames calls :func:`locate`, which returns a directory whose frames were verified
against BOTH pins, or raises :class:`CorpusMissing` with the exact command that fixes it. Nothing here
ever substitutes other data: a missing, short, extra or altered frame is an error.

Gate lanes have no network, so the lane never fetches. The corpus is a declared dependency of the TESTER
IMAGE (tester-unified build runs ``fetch`` into ``/opt/pwmcp-console-corpus``, see the README next to the
asset pin); a developer runs ``python3 pwmcp/tools/corpus/console_corpus.py fetch`` once.

Stdlib only (it also runs under the image build's system python before any venv exists).

Commands (``-h`` per command): ``package`` (build the deterministic tar and print the pin),
``fetch`` (download + verify + extract), ``verify DIR``, ``locate`` (print the verified directory).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterable

PWMCP = Path(__file__).resolve().parents[2]
CORPUS_ROOT = PWMCP / "tests" / "corpus" / "console"
ASSETS_FILE = CORPUS_ROOT / "CORPUS-ASSETS.json"

#: Where a verified corpus is looked for, first hit wins. The tester image installs to the second entry.
ENV_DIR = "PWMCP_CONSOLE_CORPUS_DIR"
IMAGE_DIR = Path("/opt/pwmcp-console-corpus")
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "pwmcp-console-corpus"

ALLOWED_URL_PREFIX = "https://github.com/volkb79-2/vbpub/releases/download/"
ALLOWED_REDIRECT_HOSTS = frozenset({
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FRAME = re.compile(r"[0-9]{4}_[0-9]+s\.png")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class CorpusError(Exception):
    """The corpus (asset, index or extracted frames) is wrong; the message says how."""


class CorpusMissing(CorpusError):
    """No verified corpus is present; the message carries the command that fetches it."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_pin(asset_id: str | None = None, assets_file: Path = ASSETS_FILE) -> dict:
    """The committed pin of one asset (the only one when ``asset_id`` is None), fully validated."""

    try:
        document = json.loads(assets_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorpusError(f"cannot read the corpus pin {assets_file}: {exc}") from exc
    assets = document.get("assets") if isinstance(document, dict) else None
    if not isinstance(document, dict) or document.get("schema") != 1 or not isinstance(assets, list) or not assets:
        raise CorpusError(f"{assets_file}: expected {{'schema': 1, 'assets': [..]}}")
    chosen = [a for a in assets if asset_id in (None, a.get("id"))]
    if len(chosen) != 1:
        raise CorpusError(f"{assets_file}: asset {asset_id!r} not found exactly once")
    pin = chosen[0]
    for key in ("id", "release_tag", "asset", "url", "sha256", "size", "frames", "series_manifest"):
        if key not in pin:
            raise CorpusError(f"{assets_file}: asset {pin.get('id')!r} lacks {key!r}")
    if not _SHA256.fullmatch(str(pin["sha256"])):
        raise CorpusError(f"{assets_file}: sha256 of {pin['id']!r} is not 64 lowercase hex digits (unpublished pin?)")
    expected_url = f"{ALLOWED_URL_PREFIX}{pin['release_tag']}/{pin['asset']}"
    if pin["url"] != expected_url:
        raise CorpusError(f"{assets_file}: url must be exactly {expected_url}")
    if "/" in pin["asset"] or pin["release_tag"].startswith("pwmcp-v"):
        raise CorpusError("asset name must be a bare file name and the tag must not use cmru's `pwmcp-v` prefix")
    return pin


def load_index(pin: dict, corpus_root: Path = CORPUS_ROOT) -> dict[str, dict]:
    """``{file name: record}`` from the committed series manifest; every record is checked for shape."""

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
        if not _FRAME.fullmatch(str(name)) or not _SHA256.fullmatch(str(digest)):
            raise CorpusError(f"{path}:{number}: unexpected file name or digest")
        if name in index:
            raise CorpusError(f"{path}:{number}: duplicate frame {name}")
        index[name] = record
    if len(index) != pin["frames"]:
        raise CorpusError(f"{path}: {len(index)} frames, the pin says {pin['frames']}")
    return index


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != _PNG_MAGIC or data[12:16] != b"IHDR":
        raise CorpusError("not a PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def verify_frames(directory: Path, index: dict[str, dict]) -> None:
    """``directory`` holds exactly the indexed frames, each with the indexed sha256 and dimensions."""

    if not directory.is_dir():
        raise CorpusError(f"{directory} is not a directory")
    present = {p.name for p in directory.iterdir()}
    if present != set(index):
        missing, extra = sorted(set(index) - present), sorted(present - set(index))
        raise CorpusError(f"{directory}: missing {missing[:5]} extra {extra[:5]} ({len(missing)} missing, {len(extra)} extra)")
    for name, record in sorted(index.items()):
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise CorpusError(f"{path} is not a regular file")
        if sha256_file(path) != record["sha256"]:
            raise CorpusError(f"{path}: sha256 differs from the series manifest")
        size = (record.get("width"), record.get("height"))
        if None not in size and png_size(path.read_bytes()[:32]) != size:
            raise CorpusError(f"{path}: PNG dimensions differ from the series manifest")


def _tar_bytes(frames_dir: Path, index: dict[str, dict]) -> bytes:
    """Deterministic ustar of ``frames/<name>`` in sorted order: fixed owner, mode and mtime, no compression."""

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(index):
            data = (frames_dir / name).read_bytes()
            info = tarfile.TarInfo(f"frames/{name}")
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def package(frames_dir: Path, out: Path, pin: dict, corpus_root: Path = CORPUS_ROOT) -> dict:
    """Verify ``frames_dir`` against the committed index, then write the deterministic tar to ``out``.

    Returns ``{'sha256', 'size'}`` of the tar, which the operator pastes into CORPUS-ASSETS.json."""

    index = load_index(pin, corpus_root)
    verify_frames(frames_dir, index)
    data = _tar_bytes(frames_dir, index)
    out.write_bytes(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_REDIRECT_HOSTS:
            raise CorpusError(f"refusing a redirect to {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str) -> bytes:
    opener = urllib.request.build_opener(_HttpsOnlyRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": "pwmcp-console-corpus"})
    try:
        with opener.open(request, timeout=120) as response:
            return response.read()
    except (OSError, ValueError) as exc:
        raise CorpusError(f"cannot download {url}: {exc}") from exc


def _extract(data: bytes, index: dict[str, dict], target: Path) -> None:
    """Write exactly the indexed ``frames/<name>`` members as regular files; anything else is refused."""

    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive.getmembers():
            name = member.name.removeprefix("frames/")
            if not member.isreg() or member.name != f"frames/{name}" or name not in index or name in seen:
                raise CorpusError(f"unexpected tar member {member.name!r}")
            seen.add(name)
            payload = archive.extractfile(member)
            assert payload is not None
            (target / name).write_bytes(payload.read())


def fetch(dest: Path, pin: dict, corpus_root: Path = CORPUS_ROOT,
          download: Callable[[str], bytes] = _download) -> Path:
    """Make ``dest/frames`` a verified corpus and return it; no network when it already verifies.

    The tar's sha256 and size are compared BEFORE anything is written, and the extracted frames are
    verified again against the index, so a tampered asset or a stale pin cannot produce a usable tree."""

    index = load_index(pin, corpus_root)
    frames = dest / "frames"
    try:
        verify_frames(frames, index)
        return frames
    except CorpusError:
        pass
    data = download(pin["url"])
    if len(data) != pin["size"] or hashlib.sha256(data).hexdigest() != pin["sha256"]:
        raise CorpusError(f"{pin['asset']}: size or sha256 differs from the pin in CORPUS-ASSETS.json; refusing it")
    dest.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".corpus-", dir=dest))
    try:
        _extract(data, index, staging)
        verify_frames(staging, index)
        if frames.exists():
            shutil.rmtree(frames)
        staging.rename(frames)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return frames


def candidates() -> list[Path]:
    found = [Path(value) for value in (os.environ.get(ENV_DIR),) if value]
    return found + [IMAGE_DIR, CACHE_DIR]


def locate(asset_id: str | None = None, *, search: Iterable[Path] | None = None,
           assets_file: Path = ASSETS_FILE, corpus_root: Path = CORPUS_ROOT) -> Path:
    """The verified ``frames`` directory, or :class:`CorpusMissing`. An explicit env path that fails
    verification is an error, never a silent fall-through to another directory."""

    pin = load_pin(asset_id, assets_file)
    index = load_index(pin, corpus_root)
    roots = list(search) if search is not None else candidates()
    problems = []
    for root in roots:
        frames = root / "frames"
        if not frames.is_dir():
            problems.append(f"{frames}: absent")
            continue
        try:
            verify_frames(frames, index)
        except CorpusError as exc:
            if str(root) == os.environ.get(ENV_DIR):
                raise CorpusError(f"{ENV_DIR}={root} is set but does not verify: {exc}") from exc
            problems.append(str(exc))
            continue
        return frames
    raise CorpusMissing(
        f"the console corpus {pin['id']} ({pin['frames']} frames) is not available. Tried: "
        + "; ".join(problems)
        + f". Fetch it with `python3 pwmcp/tools/corpus/console_corpus.py fetch` (needs network, verifies "
        f"sha256 {pin['sha256'][:12]}...), or point {ENV_DIR} at a directory containing `frames/`. The tester "
        "image carries it at /opt/pwmcp-console-corpus; if that is missing the image must be rebuilt."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pk = sub.add_parser("package", help="build the deterministic tar from a verified frames directory")
    pk.add_argument("frames_dir", type=Path)
    pk.add_argument("--out", type=Path, required=True)
    pk.add_argument("--asset", default=None)
    fe = sub.add_parser("fetch", help="download, verify and extract the pinned asset")
    fe.add_argument("--dest", type=Path, default=None, help=f"default: the first writable of {IMAGE_DIR} / {CACHE_DIR}")
    fe.add_argument("--asset", default=None)
    ve = sub.add_parser("verify", help="verify a directory of frames against the committed index")
    ve.add_argument("frames_dir", type=Path)
    ve.add_argument("--asset", default=None)
    lo = sub.add_parser("locate", help="print the verified frames directory or fail")
    lo.add_argument("--asset", default=None)
    args = parser.parse_args(argv)
    try:
        if args.command == "package":
            # the pin may still hold a placeholder digest while it is being created: read it unvalidated
            raw = json.loads(ASSETS_FILE.read_text(encoding="utf-8"))["assets"]
            pin = next(a for a in raw if args.asset in (None, a["id"]))
            result = package(args.frames_dir, args.out, pin)
            print(json.dumps({"asset": pin["asset"], **result}))
        elif args.command == "fetch":
            pin = load_pin(args.asset)
            dest = args.dest or CACHE_DIR
            print(fetch(dest, pin))
        elif args.command == "verify":
            pin = load_pin(args.asset)
            verify_frames(args.frames_dir, load_index(pin))
            print(f"OK: {pin['frames']} frames verified")
        else:
            print(locate(args.asset))
    except CorpusError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
