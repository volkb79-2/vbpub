from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from groop.model import Frame, frame_from_jsonable

_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

try:
    import zstandard as _zstd
except ImportError:  # pragma: no cover - depends on optional extra.
    _zstd = None


def _sniff_magic(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read(len(_ZSTD_MAGIC))


def _open_text(path: Path) -> TextIO:
    magic = _sniff_magic(path)
    binary = path.open("rb")
    if magic == _ZSTD_MAGIC:
        if _zstd is None:
            binary.close()
            raise RuntimeError(f"cannot read compressed recording without zstandard: {path}")
        decompressor = _zstd.ZstdDecompressor()
        stream = decompressor.stream_reader(binary)
        return io.TextIOWrapper(stream, encoding="utf-8", newline="")
    return io.TextIOWrapper(binary, encoding="utf-8", newline="")


@dataclass(frozen=True)
class RecordHeader:
    schema_version: int
    groop_version: str | None
    host_id: str | None
    started_at: str | None
    config_digest: str | None


class RecordReader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.header: RecordHeader | None = None

    def __iter__(self):
        return self.iter_frames()

    def iter_frames(self):
        with _open_text(self.path) as fh:
            line_no = 0
            for raw_line in fh:
                line_no += 1
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    if not raw_line.endswith("\n"):
                        break
                    raise ValueError(f"invalid JSON on line {line_no} of {self.path}") from exc
                record_type = payload.get("type")
                if line_no == 1 and record_type == "header":
                    schema_version = int(payload["schema_version"])
                    if schema_version != 1:
                        raise ValueError(f"unsupported recording schema_version: {schema_version}")
                    self.header = RecordHeader(
                        schema_version=schema_version,
                        groop_version=payload.get("groop_version"),
                        host_id=payload.get("host_id"),
                        started_at=payload.get("started_at"),
                        config_digest=payload.get("config_digest"),
                    )
                    continue
                if record_type not in (None, "frame"):
                    raise ValueError(f"unexpected record type on line {line_no} of {self.path}: {record_type!r}")
                frame_payload = dict(payload)
                frame_payload.pop("type", None)
                frame = frame_from_jsonable(frame_payload)
                if frame.schema_version != 1:
                    raise ValueError(f"unsupported frame schema_version: {frame.schema_version}")
                yield frame


def iter_frames(path: Path):
    yield from RecordReader(path).iter_frames()
