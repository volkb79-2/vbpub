from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from topos.model import Frame, frame_from_jsonable

_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

try:
    import zstandard as _zstd
    _ZstdError = _zstd.ZstdError
except ImportError:  # pragma: no cover - depends on optional extra.
    _zstd = None
    _ZstdError = None


def _sniff_magic(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read(len(_ZSTD_MAGIC))


class _ZstdStreamReader(io.RawIOBase):
    """Streaming zstd reader that fails closed on a truncated frame.

    ``ZstdDecompressor.stream_reader`` reports a truncated frame as a plain
    EOF rather than an error, so a half-written recording decompresses to a
    prefix and reads back as a shorter but perfectly valid recording. For a
    diagnostic tool that is worse than a crash: the report is believable and
    wrong.

    A ``decompressobj`` decodes exactly one frame, sets ``eof`` when that frame
    terminates cleanly, and hands the remaining bytes back as ``unused_data``.
    Chaining one per frame therefore reads an append-mode recording (each
    ``RecordWriter`` session appends its own frame) while still being able to
    tell "the frame ended" from "the input ran out". ``read_across_frames=True``
    cannot be used instead: it leaves ``eof`` permanently False, which is the
    signal this class exists to read.
    """

    def __init__(self, binary, path: Path, chunk_size: int = 65536) -> None:
        self._binary = binary
        self._path = path
        self._chunk_size = chunk_size
        self._decompressor = None
        self._carry = b""
        self._pending = b""

    def readable(self) -> bool:
        return True

    def _pump(self) -> bytes:
        """Decompress the next chunk; b"" only at a clean end of the last frame."""
        while True:
            if self._carry:
                data, self._carry = self._carry, b""
            else:
                data = self._binary.read(self._chunk_size)
            if not data:
                if self._decompressor is not None and not self._decompressor.eof:
                    raise ValueError(f"corrupt or truncated compressed recording: {self._path}")
                return b""
            if self._decompressor is None or self._decompressor.eof:
                self._decompressor = _zstd.ZstdDecompressor().decompressobj()
            chunk = self._decompressor.decompress(data)
            if self._decompressor.eof and self._decompressor.unused_data:
                self._carry = self._decompressor.unused_data
            if chunk:
                return chunk

    def readinto(self, buf) -> int:
        if not self._pending:
            self._pending = self._pump()
            if not self._pending:
                return 0
        count = min(len(buf), len(self._pending))
        buf[:count] = self._pending[:count]
        self._pending = self._pending[count:]
        return count

    def close(self) -> None:
        try:
            self._binary.close()
        finally:
            super().close()


def _open_text(path: Path) -> TextIO:
    magic = _sniff_magic(path)
    binary = path.open("rb")
    if magic == _ZSTD_MAGIC:
        if _zstd is None:
            binary.close()
            raise RuntimeError(f"cannot read compressed recording without zstandard: {path}")
        raw = _ZstdStreamReader(binary, path)
        return io.TextIOWrapper(io.BufferedReader(raw), encoding="utf-8", newline="")
    return io.TextIOWrapper(binary, encoding="utf-8", newline="")


@dataclass(frozen=True)
class RecordHeader:
    schema_version: int
    topos_version: str | None
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
        try:
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
                        try:
                            schema_version = int(payload["schema_version"])
                        except (KeyError, TypeError, ValueError):
                            raise ValueError(
                                f"invalid recording header on line 1 of {self.path}"
                            ) from None
                        if schema_version != 1:
                            raise ValueError(f"unsupported recording schema_version: {schema_version}")
                        self.header = RecordHeader(
                            schema_version=schema_version,
                            topos_version=payload.get("topos_version"),
                            host_id=payload.get("host_id"),
                            started_at=payload.get("started_at"),
                            config_digest=payload.get("config_digest"),
                        )
                        continue
                    if record_type not in (None, "frame"):
                        raise ValueError(f"unexpected record type on line {line_no} of {self.path}: {record_type!r}")
                    frame_payload = dict(payload)
                    frame_payload.pop("type", None)
                    try:
                        frame = frame_from_jsonable(frame_payload)
                    except (KeyError, TypeError, ValueError) as exc:
                        # Valid JSON that is not a valid P2 frame (missing
                        # required fields, wrong types, unknown metrics)
                        raise ValueError(
                            f"invalid recording frame on line {line_no} of {self.path}"
                        ) from None
                    if frame.schema_version != 1:
                        raise ValueError(f"unsupported frame schema_version: {frame.schema_version}")
                    yield frame
        except Exception as exc:
            if _ZstdError is not None and isinstance(exc, _ZstdError):
                raise ValueError(f"corrupt or truncated compressed recording: {self.path}") from None
            raise


def iter_frames(path: Path):
    yield from RecordReader(path).iter_frames()
