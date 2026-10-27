"""The console-frame corpus is a release asset pinned by sha256: the tool, and the real frames.

The tool tests run on a tiny synthetic corpus and a fake downloader (no network). The real-corpus tests
use ``console_corpus.locate()``: a missing corpus FAILS with the command that fetches it, by the same
policy as every other missing test dependency (``_testdeps``); gate lanes have no network, so the tester
image carries the frames at /opt/pwmcp-console-corpus.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import struct
import sys
import tarfile
import zlib
from pathlib import Path

import pytest

import _testdeps

PWMCP = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("console_corpus", PWMCP / "tools" / "corpus" / "console_corpus.py")
assert _spec and _spec.loader
cc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cc
_spec.loader.exec_module(cc)

CURATED = PWMCP / "tests" / "corpus" / "console" / "truenas-27-vanilla" / "frames"


def _png(width: int, height: int, seed: int) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    raw = b"".join(b"\x00" + bytes([seed]) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.fixture
def mini(tmp_path: Path):
    """A 3-frame synthetic corpus: frames dir, committed-style index, pin and the packaged tar bytes."""

    frames = tmp_path / "src" / "frames"
    frames.mkdir(parents=True)
    records = []
    for number, seed in enumerate((1, 2, 3), 1):
        name = f"{number:04d}_{number * 3}s.png"
        data = _png(8, 4, seed)
        (frames / name).write_bytes(data)
        records.append({"sequence": number, "file": name, "sha256": hashlib.sha256(data).hexdigest(), "width": 8, "height": 4})
    root = tmp_path / "corpus"
    (root / "set").mkdir(parents=True)
    (root / "set" / "series-manifest.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    pin = {
        "id": "mini", "release_tag": "pwmcp-console-corpus-mini-v1", "asset": "pwmcp-console-corpus-mini-v1.tar",
        "url": "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-console-corpus-mini-v1/pwmcp-console-corpus-mini-v1.tar",
        "sha256": "0" * 64, "size": 0, "frames": 3, "series_manifest": "set/series-manifest.jsonl",
    }
    tar_path = tmp_path / "mini.tar"
    pin.update(cc.package(frames, tar_path, pin, root))
    return frames, root, pin, tar_path


def test_package_is_deterministic_and_verified_first(mini, tmp_path: Path) -> None:
    frames, root, pin, tar_path = mini
    again = tmp_path / "again.tar"
    assert cc.package(frames, again, pin, root) == {"sha256": pin["sha256"], "size": pin["size"]}
    assert again.read_bytes() == tar_path.read_bytes()
    (frames / "0002_6s.png").write_bytes(_png(8, 4, 99))  # a frame that disagrees with the committed index
    with pytest.raises(cc.CorpusError, match="sha256 differs"):
        cc.package(frames, tmp_path / "bad.tar", pin, root)


def test_fetch_extracts_verified_frames_and_is_idempotent(mini, tmp_path: Path) -> None:
    _, root, pin, tar_path = mini
    calls: list[str] = []

    def download(url: str) -> bytes:
        calls.append(url)
        return tar_path.read_bytes()

    out = cc.fetch(tmp_path / "cache", pin, root, download)
    assert out == tmp_path / "cache" / "frames" and len(list(out.iterdir())) == 3
    assert cc.fetch(tmp_path / "cache", pin, root, download) == out
    assert calls == [pin["url"]]  # the second call verified the tree and did not touch the network
    assert not [p for p in (tmp_path / "cache").iterdir() if p.name.startswith(".corpus-")]  # staging removed


@pytest.mark.parametrize("damage", ["flip", "short", "other_pin"])
def test_fetch_refuses_a_wrong_asset_and_leaves_nothing_usable(mini, tmp_path: Path, damage: str) -> None:
    _, root, pin, tar_path = mini
    data = bytearray(tar_path.read_bytes())
    if damage == "flip":
        data[2000] ^= 1
    elif damage == "short":
        data = data[:-512]
    else:
        pin = dict(pin, sha256="1" * 64)
    with pytest.raises(cc.CorpusError, match="refusing it"):
        cc.fetch(tmp_path / "cache", pin, root, lambda url: bytes(data))
    assert not (tmp_path / "cache" / "frames").exists()


def test_fetch_refuses_unexpected_tar_members(mini, tmp_path: Path) -> None:
    _, root, pin, _ = mini
    for member in ("frames/../evil.png", "frames/0009_9s.png", "other/0001_3s.png"):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            info = tarfile.TarInfo(member)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        data = buffer.getvalue()
        bad = dict(pin, sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        with pytest.raises(cc.CorpusError, match="unexpected tar member"):
            cc.fetch(tmp_path / "cache", bad, root, lambda url: data)
    assert not (tmp_path / "cache" / "frames").exists()


def test_locate_reports_missing_with_the_fetch_command_and_never_falls_through_an_explicit_dir(
        mini, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frames, root, pin, _ = mini
    assets = tmp_path / "assets.json"
    assets.write_text(json.dumps({"schema": 1, "assets": [pin]}))
    with pytest.raises(cc.CorpusMissing, match=r"console_corpus.py fetch"):
        cc.locate(search=[tmp_path / "nowhere"], assets_file=assets, corpus_root=root)
    assert cc.locate(search=[frames.parent], assets_file=assets, corpus_root=root) == frames
    monkeypatch.setenv(cc.ENV_DIR, str(tmp_path / "broken"))
    (tmp_path / "broken" / "frames").mkdir(parents=True)
    with pytest.raises(cc.CorpusError, match="is set but does not verify"):
        cc.locate(search=[tmp_path / "broken", frames.parent], assets_file=assets, corpus_root=root)


@pytest.mark.parametrize("change", [{"sha256": "short"}, {"url": "https://example.org/x.tar"},
                                    {"release_tag": "pwmcp-v1.0.0-corpus"}, {"asset": "a/b.tar"}])
def test_pin_validation_rejects_unsafe_or_unpublished_entries(mini, tmp_path: Path, change: dict) -> None:
    pin = dict(mini[2])
    pin.update(change)
    if "release_tag" in change or "asset" in change:
        pin["url"] = f"{cc.ALLOWED_URL_PREFIX}{pin['release_tag']}/{pin['asset']}"
    path = tmp_path / "assets.json"
    path.write_text(json.dumps({"schema": 1, "assets": [pin]}))
    with pytest.raises(cc.CorpusError):
        cc.load_pin(None, path)


def test_the_committed_pin_is_published_form_and_matches_the_committed_index() -> None:
    pin = cc.load_pin()  # validates sha256 shape, the exact release URL and the tag/name rules
    index = cc.load_index(pin)
    assert len(index) == pin["frames"] == 242
    assert pin["size"] > 5_000_000 and pin["size"] % 512 == 0  # a tar of PNGs, not a placeholder


def _real_frames() -> Path:
    try:
        return cc.locate()
    except cc.CorpusMissing as exc:
        reason = str(exc)
    _testdeps.missing("the pwmcp console corpus", reason)  # fails (or skips on the opt-in); never returns


def test_real_corpus_is_complete_and_verified() -> None:
    frames = _real_frames()
    index = cc.load_index(cc.load_pin())
    cc.verify_frames(frames, index)  # exact file set, sha256 and 1280x800 dimensions of all 242 frames
    assert {(r["width"], r["height"]) for r in index.values()} == {(1280, 800)}


def test_curated_frames_are_byte_identical_members_of_the_full_series() -> None:
    frames = _real_frames()
    by_hash = {r["sha256"]: name for name, r in cc.load_index(cc.load_pin()).items()}
    curated = sorted(CURATED.glob("*.png"))
    assert len(curated) == 20
    for path in curated:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest in by_hash, f"{path.name} is not a frame of the pinned series"
        assert (frames / by_hash[digest]).read_bytes() == path.read_bytes()
