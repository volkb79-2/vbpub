from __future__ import annotations

import io
import json
import os
import socket
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, TextIO

from topos import __version__
from topos.config import ToposConfig, load
from topos.model import Frame, frame_to_jsonable

_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

try:
    import zstandard as _zstd
except ImportError:  # pragma: no cover - exercised via plain-json fallback tests.
    _zstd = None


def _is_zstd_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(len(_ZSTD_MAGIC)) == _ZSTD_MAGIC
    except FileNotFoundError:
        return False


class RecordWriter:
    def __init__(
        self,
        path: Path,
        *,
        config: ToposConfig | None = None,
        host_id: str | None = None,
        started_at: float | None = None,
        config_digest: str | None = None,
        flush_every_frames: int | None = None,
        fsync: bool | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.path = path
        self.config = config or load()
        self.host_id = host_id or socket.gethostname()
        self.now = now
        started_at_epoch = started_at if started_at is not None else (self.now() if self.now is not None else None)
        self.started_at = datetime.fromtimestamp(started_at_epoch, tz=timezone.utc).isoformat() if started_at_epoch is not None else datetime.now(timezone.utc).isoformat()
        self.config_digest = config_digest or self.config.digest()
        self.flush_every_frames = max(1, flush_every_frames or self.config.record.flush_every_frames)
        self.fsync = self.config.record.fsync if fsync is None else fsync
        self._frames_since_flush = 0
        self._binary: BinaryIO | None = None
        self._text: TextIO | None = None
        self._compressed = False
        self._open()

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        exists = self.path.exists()
        non_empty = exists and self.path.stat().st_size > 0
        existing_is_zstd = non_empty and _is_zstd_file(self.path)
        if existing_is_zstd and _zstd is None:
            raise RuntimeError(f"cannot append to compressed recording without zstandard: {self.path}")
        self._compressed = self.path.suffix == ".zst" and (_zstd is not None) and (existing_is_zstd or not non_empty)
        binary = self.path.open("ab")
        self._binary = binary
        if self._compressed:
            compressor = _zstd.ZstdCompressor()
            stream = compressor.stream_writer(binary)
            self._text = io.TextIOWrapper(stream, encoding="utf-8", newline="")
        else:
            self._text = io.TextIOWrapper(binary, encoding="utf-8", newline="")
        if not non_empty:
            self._write_line(
                {
                    "type": "header",
                    "schema_version": 1,
                    "topos_version": __version__,
                    "host_id": self.host_id,
                    "started_at": self.started_at,
                    "config_digest": self.config_digest,
                }
            )
            self.flush(force=True)

    def _write_line(self, payload: dict[str, object]) -> None:
        assert self._text is not None
        self._text.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        self._text.write("\n")

    def write_frame(self, frame: Frame) -> None:
        if frame.schema_version != 1:
            raise ValueError(f"unsupported schema_version in frame: {frame.schema_version}")
        payload = {"type": "frame", **frame_to_jsonable(frame)}
        self._write_line(payload)
        self._frames_since_flush += 1
        if self._frames_since_flush >= self.flush_every_frames:
            self.flush()

    def flush(self, *, force: bool = False) -> None:
        if self._text is None or self._binary is None:
            return
        self._text.flush()
        if self.fsync or force:
            os.fsync(self._binary.fileno())
        self._frames_since_flush = 0

    def close(self) -> None:
        if self._text is None:
            return
        self.flush()
        self._text.close()
        self._text = None
        self._binary = None

    def __enter__(self) -> RecordWriter:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
