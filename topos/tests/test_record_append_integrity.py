"""Appending to a recording after a crash must not destroy it (B-18)."""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.model import Frame
from topos.record.reader import RecordReader
from topos.record.writer import RecordWriter


def _frame(ts: float) -> Frame:
    return Frame(schema_version=1, ts=ts, interval_s=1.0, host={}, entities={})


def test_append_after_a_torn_final_line_keeps_the_recording_readable(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    with RecordWriter(path, started_at=0.0) as writer:
        writer.write_frame(_frame(1.0))
        writer.write_frame(_frame(2.0))
    with path.open("ab") as fh:
        fh.write(b'{"type":"frame","ts":3.0,"inter')  # crash mid-line

    with RecordWriter(path, started_at=0.0) as writer:
        writer.write_frame(_frame(4.0))

    assert [f.ts for f in RecordReader(path)] == [1.0, 2.0, 4.0]


def test_append_to_a_file_that_is_only_a_partial_line_starts_a_fresh_recording(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rec.jsonl"
    path.write_bytes(b'{"type":"header","schema_ver')

    with RecordWriter(path, started_at=0.0) as writer:
        writer.write_frame(_frame(1.0))

    assert [f.ts for f in RecordReader(path)] == [1.0]
    assert path.read_text().count('"type":"header"') == 1


def test_append_to_a_clean_plain_recording_does_not_cut_anything(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    with RecordWriter(path, started_at=0.0) as writer:
        writer.write_frame(_frame(1.0))
    with RecordWriter(path, started_at=0.0) as writer:
        writer.write_frame(_frame(2.0))
    assert [f.ts for f in RecordReader(path)] == [1.0, 2.0]


def test_append_refuses_a_truncated_zstd_recording_and_leaves_it_alone(tmp_path: Path) -> None:
    pytest.importorskip("zstandard")
    path = tmp_path / "rec.jsonl.zst"
    with RecordWriter(path, started_at=0.0) as writer:
        writer.write_frame(_frame(1.0))
    with RecordWriter(path, started_at=0.0) as writer:
        writer.write_frame(_frame(2.0))
    intact = path.read_bytes()
    path.write_bytes(intact[:-6])  # cut inside the last zstd frame
    before = path.read_bytes()

    with pytest.raises(RuntimeError, match="truncated or corrupt"):
        RecordWriter(path, started_at=0.0)

    assert path.read_bytes() == before


def test_append_to_an_intact_zstd_recording_still_works(tmp_path: Path) -> None:
    pytest.importorskip("zstandard")
    path = tmp_path / "rec.jsonl.zst"
    for ts in (1.0, 2.0, 3.0):
        with RecordWriter(path, started_at=0.0) as writer:
            writer.write_frame(_frame(ts))
    assert [f.ts for f in RecordReader(path)] == [1.0, 2.0, 3.0]
