"""Offline checks for the local PWMCP console-frame corpus pin and verifier."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import struct
import sys
import zlib
from pathlib import Path

import pytest
from _testdeps import REQUIRE_CONSOLE_CORPUS_VAR, optional_local_data

PWMCP = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "console_corpus", PWMCP / "tools" / "corpus" / "console_corpus.py"
)
assert _spec and _spec.loader
cc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cc
_spec.loader.exec_module(cc)

CURATED = PWMCP / "tests" / "corpus" / "console" / "truenas-27-vanilla" / "frames"


def _png(width: int, height: int, seed: int) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body)))

    raw = b"".join(b"\x00" + bytes([seed]) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def mini(tmp_path: Path) -> dict:
    """A three-frame local corpus with a committed-style index and tar pin."""

    directory = tmp_path / "local-corpus"
    frames = directory / "frames"
    frames.mkdir(parents=True)
    corpus_root = tmp_path / "index"
    (corpus_root / "set").mkdir(parents=True)
    records = []
    for number, seed in enumerate((1, 2, 3), 1):
        name = f"{number:04d}_{number * 3}s.png"
        data = _png(8, 4, seed)
        (frames / name).write_bytes(data)
        records.append({
            "sequence": number,
            "file": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "width": 8,
            "height": 4,
        })
    (corpus_root / "set" / "series-manifest.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )
    pin = {
        "id": "mini",
        "source": cc.ENV_DIR,
        "archive": "mini.tar",
        "sha256": "0" * 64,
        "size": 1,
        "frames": 3,
        "series_manifest": "set/series-manifest.jsonl",
    }
    archive = tmp_path / "mini.tar"
    pin.update(cc.package(frames, archive, pin, corpus_root))
    assets = tmp_path / "assets.json"
    assets.write_text(json.dumps({"schema": 1, "assets": [pin]}), encoding="utf-8")
    return {
        "directory": directory,
        "frames": frames,
        "corpus_root": corpus_root,
        "pin": pin,
        "archive": archive,
        "assets": assets,
    }


def _index_records(mini: dict) -> list[dict]:
    path = mini["corpus_root"] / mini["pin"]["series_manifest"]
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_index_records(mini: dict, records: list[dict]) -> None:
    path = mini["corpus_root"] / mini["pin"]["series_manifest"]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def test_package_is_deterministic_and_checks_the_series_index_first(mini: dict, tmp_path: Path) -> None:
    again = tmp_path / "again.tar"
    assert cc.package(mini["frames"], again, mini["pin"], mini["corpus_root"]) == {
        "sha256": mini["pin"]["sha256"], "size": mini["pin"]["size"]
    }
    assert again.read_bytes() == mini["archive"].read_bytes()
    (mini["frames"] / "0002_6s.png").write_bytes(_png(8, 4, 99))
    with pytest.raises(cc.CorpusError, match="sha256 differs"):
        cc.package(mini["frames"], tmp_path / "bad.tar", mini["pin"], mini["corpus_root"])


def test_local_verification_checks_frame_set_and_pinned_deterministic_tar(mini: dict) -> None:
    assert cc.verify_directory(mini["directory"], mini["pin"], mini["corpus_root"]) == mini["frames"]
    wrong_pin = dict(mini["pin"], sha256="f" * 64)
    with pytest.raises(cc.CorpusError, match="deterministic tar size or sha256 differs"):
        cc.verify_directory(mini["directory"], wrong_pin, mini["corpus_root"])
    (mini["frames"] / "extra.png").write_bytes(b"extra")
    with pytest.raises(cc.CorpusError, match="extra"):
        cc.verify_directory(mini["directory"], mini["pin"], mini["corpus_root"])


def test_locate_uses_only_the_explicit_absolute_local_path(
    mini: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cc.ENV_DIR, str(mini["directory"]))
    assert cc.locate(
        assets_file=mini["assets"], corpus_root=mini["corpus_root"]
    ) == mini["frames"]
    monkeypatch.setenv(cc.ENV_DIR, "relative/corpus")
    with pytest.raises(cc.CorpusError, match="must be an absolute path"):
        cc.locate(assets_file=mini["assets"], corpus_root=mini["corpus_root"])


def test_an_invalid_explicit_path_with_trailing_slash_is_an_error_and_never_skips_or_falls_back(
    mini: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cc.ENV_DIR, str(tmp_path / "missing") + "/")
    with pytest.raises(cc.CorpusError, match=re.escape("is set but does not verify")):
        cc.locate(assets_file=mini["assets"], corpus_root=mini["corpus_root"])
    monkeypatch.setenv(cc.ENV_DIR, str(mini["directory"]))
    broken = mini["frames"] / "0001_3s.png"
    broken.write_bytes(b"changed")
    with pytest.raises(cc.CorpusError, match="sha256 differs"):
        cc.locate(assets_file=mini["assets"], corpus_root=mini["corpus_root"])


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("missing", "missing ['0001_3s.png']"),
        ("symlink", "is not a regular file"),
    ],
)
def test_verify_frames_rejects_missing_and_symlinked_frame_files(
    mini: dict, tmp_path: Path, damage: str, message: str
) -> None:
    victim = mini["frames"] / "0001_3s.png"
    index = cc.load_index(mini["pin"], mini["corpus_root"])
    if damage == "missing":
        victim.unlink()
    else:
        data = victim.read_bytes()
        victim.unlink()
        target = tmp_path / "outside.png"
        target.write_bytes(data)
        victim.symlink_to(target)

    with pytest.raises(cc.CorpusError, match=re.escape(message)):
        cc.verify_frames(mini["frames"], index)


def test_verify_directory_rejects_a_symlinked_frames_directory(mini: dict) -> None:
    frames = mini["frames"]
    target = mini["directory"] / "frames-real"
    frames.rename(target)
    frames.symlink_to(target, target_is_directory=True)

    with pytest.raises(cc.CorpusError, match=re.escape("is not a regular directory")):
        cc.verify_directory(mini["directory"], mini["pin"], mini["corpus_root"])


def test_verify_directory_rejects_a_symlinked_corpus_directory(
    mini: dict, tmp_path: Path
) -> None:
    directory = mini["directory"]
    target = tmp_path / "corpus-real"
    directory.rename(target)
    directory.symlink_to(target, target_is_directory=True)

    with pytest.raises(cc.CorpusError, match=re.escape("is not a local corpus directory")):
        cc.verify_directory(directory, mini["pin"], mini["corpus_root"])


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("non_png", "not a PNG with an IHDR header"),
        ("dimensions", "PNG dimensions differ from the series manifest"),
    ],
)
def test_verify_frames_rejects_bad_png_data_and_dimensions(
    mini: dict, damage: str, message: str
) -> None:
    name = "0001_3s.png"
    frame = mini["frames"] / name
    records = _index_records(mini)
    record = next(row for row in records if row["file"] == name)
    if damage == "non_png":
        data = b"this is not a PNG image with an IHDR header"
    else:
        data = _png(9, 4, 99)
    frame.write_bytes(data)
    # Keep the content hash current so these cases reach the PNG checks.
    record["sha256"] = hashlib.sha256(data).hexdigest()
    _write_index_records(mini, records)
    index = cc.load_index(mini["pin"], mini["corpus_root"])

    with pytest.raises(cc.CorpusError, match=re.escape(message)):
        cc.verify_frames(mini["frames"], index)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("traversal", "unexpected file name or digest"),
        ("duplicate", "duplicate frame 0001_3s.png"),
        ("wrong_count", "2 frames, the pin says 3"),
    ],
)
def test_load_index_rejects_unsafe_duplicate_and_wrong_count_records(
    mini: dict, damage: str, message: str
) -> None:
    records = _index_records(mini)
    if damage == "traversal":
        records[0]["file"] = "../0001_3s.png"
    elif damage == "duplicate":
        records[1] = dict(records[0])
    else:
        records.pop()
    _write_index_records(mini, records)

    with pytest.raises(cc.CorpusError, match=re.escape(message)):
        cc.load_index(mini["pin"], mini["corpus_root"])


def test_locate_rejects_an_explicitly_empty_corpus_directory(
    mini: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cc.ENV_DIR, "")

    with pytest.raises(
        cc.CorpusError,
        match=re.escape(f"{cc.ENV_DIR} is set but empty"),
    ):
        cc.locate(assets_file=mini["assets"], corpus_root=mini["corpus_root"])


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": "short"},
        {"source": "another-path"},
        {"archive": "../mini.tar"},
        {"url": "https://example.org/mini.tar"},
        {"release_tag": "pwmcp-console-corpus-mini-v1"},
    ],
)
def test_pin_rejects_bad_or_release_asset_fields(
    mini: dict, tmp_path: Path, change: dict
) -> None:
    pin = {**mini["pin"], **change}
    path = tmp_path / "bad-assets.json"
    path.write_text(json.dumps({"schema": 1, "assets": [pin]}), encoding="utf-8")
    with pytest.raises(cc.CorpusError):
        cc.load_pin(None, path)


def test_committed_pin_has_a_local_source_and_matches_the_full_series_index() -> None:
    pin = cc.load_pin()
    index = cc.load_index(pin)
    assert pin["source"] == cc.ENV_DIR
    assert "url" not in pin and "release_tag" not in pin
    assert len(index) == pin["frames"] == 242
    assert pin["size"] == 5_365_760 and pin["size"] % 512 == 0
    assert {(record["width"], record["height"]) for record in index.values()} == {(1280, 800)}


def _real_frames(locate=cc.locate) -> Path:
    return optional_local_data(
        locate,
        cc.CorpusMissing,
        description="local console corpus",
        require_env=REQUIRE_CONSOLE_CORPUS_VAR,
    )


def test_absent_local_corpus_skips_with_an_explicit_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(cc.ENV_DIR, raising=False)
    monkeypatch.delenv(REQUIRE_CONSOLE_CORPUS_VAR, raising=False)
    with pytest.raises(pytest.skip.Exception) as skipped:
        _real_frames()
    assert cc.ENV_DIR in str(skipped.value)


def test_required_absent_local_corpus_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(cc.ENV_DIR, raising=False)
    monkeypatch.setenv(REQUIRE_CONSOLE_CORPUS_VAR, "1")
    with pytest.raises(pytest.fail.Exception) as failed:
        _real_frames()
    assert cc.ENV_DIR in str(failed.value)


def test_selected_but_invalid_local_corpus_is_not_skipped(
    mini: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cc.ENV_DIR, str(tmp_path / "missing-corpus"))

    with pytest.raises(cc.CorpusError, match=re.escape("does not verify")):
        _real_frames(
            lambda: cc.locate(
                assets_file=mini["assets"], corpus_root=mini["corpus_root"]
            )
        )


def test_selected_valid_local_corpus_returns_verified_frames(
    mini: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cc.ENV_DIR, str(mini["directory"]))

    assert _real_frames(
        lambda: cc.locate(assets_file=mini["assets"], corpus_root=mini["corpus_root"])
    ) == mini["frames"]


def test_real_corpus_is_complete_and_verified() -> None:
    frames = _real_frames()
    index = cc.load_index(cc.load_pin())
    cc.verify_frames(frames, index)
    assert {(record["width"], record["height"]) for record in index.values()} == {(1280, 800)}


def test_curated_frames_are_byte_identical_members_of_the_full_series() -> None:
    frames = _real_frames()
    by_hash = {record["sha256"]: name for name, record in cc.load_index(cc.load_pin()).items()}
    curated = sorted(CURATED.glob("*.png"))
    assert curated, f"no curated screenshots found in {CURATED}"
    for path in curated:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest in by_hash, f"{path.name} is not a frame of the pinned series"
        assert (frames / by_hash[digest]).read_bytes() == path.read_bytes()


def test_main_reports_local_verification_errors_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cc.main(["verify", "/nonexistent"]) == 1
    captured = capsys.readouterr()
    assert "[ERROR]" in captured.err
    assert "Traceback" not in captured.err
