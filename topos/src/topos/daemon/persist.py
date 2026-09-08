"""P91 — recoverable age-and-byte-capped persistent daemon history.

D-005's persistent tier: canonical frames are batched into small segment
files and published atomically (write to a temp path, fsync, then rename into
the visible ``segments/`` directory). A segment is therefore either fully
absent or fully valid-on-disk to any process that lists the directory — a
crash mid-write can never leave a torn segment visible (O5). The index is
republished the same way (temp + rename), so ``index.json`` is always a
complete, valid document even across a crash mid-write.

Recovery always re-derives truth from the segment files themselves: every
listed (and every orphaned) segment's sha256 is recomputed and compared
against its recorded checksum. A mismatch, missing file, or unparseable
segment is quarantined (moved aside, never deleted) rather than trusted, so a
corrupt middle segment cannot silently return wrong data or crash recovery
(O6) — the surrounding, still-valid segments remain queryable and the hole
between them is reported as an explicit gap (O8). Startup scanning is bounded
by the byte cap (256 MiB by default): recovery reads at most that many bytes
regardless of how long the store has been running.

This module deliberately does not aggregate or re-derive metrics: it persists
canonical ``Frame`` objects once, exactly as produced, and hands them back
unmodified for P88's query engine to consume (Contract 2). It is not a second
report/query engine.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from topos.config import PersistConfig
from topos.model import Frame, frame_from_jsonable, frame_to_jsonable

_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_SEGMENT_SCHEMA_VERSION = 1
_INDEX_SCHEMA_VERSION = 1
_SEGMENT_NAME_RE = re.compile(r"^seg-(\d{8})\.jsonl(\.zst)?$")

try:
    import zstandard as _zstd

    _ZstdError: type[Exception] | None = _zstd.ZstdError
except ImportError:  # pragma: no cover - exercised via plain-json fallback tests.
    _zstd = None
    _ZstdError = None


class PersistStoreError(RuntimeError):
    """Base for typed persistent-store errors."""


@dataclass(frozen=True)
class SegmentInfo:
    """Recorded metadata for one published, currently-retained segment."""

    segment_id: int
    filename: str
    frame_count: int
    first_ts: float
    last_ts: float
    byte_size: int
    raw_bytes: int
    sha256: str
    created_at: float


@dataclass(frozen=True)
class GapRange:
    """An explicit, known hole between two still-retained segments."""

    start_ts: float
    end_ts: float
    reason: str  # "quarantined" | "lost"


@dataclass(frozen=True)
class StoreStats:
    """Everything D-005/Contract 3 requires the store to report truthfully."""

    enabled: bool
    dir: str
    degraded: bool
    degraded_reason: str | None
    recovery_state: str  # "disabled" | "empty" | "clean" | "recovered" | "degraded"
    segment_count: int
    frame_count: int
    byte_size: int
    raw_bytes: int
    oldest_ts: float | None
    newest_ts: float | None
    evicted: bool
    evicted_segments: int
    evicted_frames: int
    quarantined_segments: int
    gaps: tuple[GapRange, ...]
    write_errors: int
    compression_ratio: float | None
    age_cap_seconds: float
    byte_cap_bytes: int
    lifetime_bytes_written: int
    lifetime_raw_bytes_written: int
    lifetime_frames_written: int
    lifetime_index_bytes_written: int


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _segment_filename(segment_id: int, *, compressed: bool) -> str:
    suffix = ".jsonl.zst" if compressed else ".jsonl"
    return f"seg-{segment_id:08d}{suffix}"


class _SegmentWriter:
    """Writes one segment to a temp path; never visible until ``publish``."""

    def __init__(self, tmp_path: Path, segment_id: int, *, compress: bool) -> None:
        self.tmp_path = tmp_path
        self.segment_id = segment_id
        self.compress = compress
        self.frame_count = 0
        self.raw_bytes = 0
        self.first_ts: float | None = None
        self.last_ts: float | None = None
        self._hasher = hashlib.sha256()
        self._fh = tmp_path.open("wb")
        self._compressor = _zstd.ZstdCompressor().compressobj() if compress else None
        self._write_line(
            {"type": "segment_header", "schema_version": _SEGMENT_SCHEMA_VERSION, "segment_id": segment_id}
        )

    def _write_raw(self, data: bytes) -> None:
        if self._compressor is not None:
            chunk = self._compressor.compress(data)
            if chunk:
                self._fh.write(chunk)
                self._hasher.update(chunk)
        else:
            self._fh.write(data)
            self._hasher.update(data)

    def _write_line(self, payload: dict[str, object]) -> None:
        line = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        self.raw_bytes += len(line)
        self._write_raw(line)

    def write_frame(self, frame: Frame) -> None:
        self._write_line({"type": "frame", **frame_to_jsonable(frame)})
        self.frame_count += 1
        if self.first_ts is None:
            self.first_ts = frame.ts
        self.last_ts = frame.ts

    def checkpoint(self) -> None:
        """fsync the temp file without publishing it (durability of an
        in-progress segment across the checkpoint interval)."""
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def finalize(self) -> tuple[int, str]:
        """Flush + fsync the temp file. Returns (byte_size, sha256)."""
        if self._compressor is not None:
            tail = self._compressor.flush()
            if tail:
                self._fh.write(tail)
                self._hasher.update(tail)
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()
        return self.tmp_path.stat().st_size, self._hasher.hexdigest()

    def abort(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass
        self.tmp_path.unlink(missing_ok=True)


def _open_segment_text(path: Path, *, compressed: bool) -> io.TextIOWrapper:
    binary = path.open("rb")
    if not compressed:
        return io.TextIOWrapper(binary, encoding="utf-8", newline="")
    if _zstd is None:
        binary.close()
        raise PersistStoreError(f"cannot read compressed segment without zstandard: {path}")
    reader = _zstd.ZstdDecompressor().stream_reader(binary)
    return io.TextIOWrapper(io.BufferedReader(reader), encoding="utf-8", newline="")


def _inspect_segment_file(path: Path, *, expected_segment_id: int | None) -> tuple[int, float, float, int] | None:
    """Fully parse a segment file. Returns (frame_count, first_ts, last_ts, raw_bytes) or None if unreadable."""
    compressed = path.suffix == ".zst"
    try:
        frame_count = 0
        raw_bytes = 0
        first_ts: float | None = None
        last_ts: float | None = None
        with _open_segment_text(path, compressed=compressed) as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                raw_bytes += len(raw_line.encode("utf-8"))
                payload = json.loads(line)
                record_type = payload.get("type")
                if line_no == 1:
                    if record_type != "segment_header":
                        return None
                    if expected_segment_id is not None and payload.get("segment_id") != expected_segment_id:
                        return None
                    continue
                if record_type != "frame":
                    return None
                frame_payload = dict(payload)
                frame_payload.pop("type", None)
                frame = frame_from_jsonable(frame_payload)
                frame_count += 1
                if first_ts is None:
                    first_ts = frame.ts
                last_ts = frame.ts
        if frame_count == 0 or first_ts is None or last_ts is None:
            return None
        return frame_count, first_ts, last_ts, raw_bytes
    except (json.JSONDecodeError, UnicodeError, ValueError, KeyError, TypeError, OSError):
        return None
    except Exception as exc:  # pragma: no cover - defensive: zstd's own error type
        if _ZstdError is not None and isinstance(exc, _ZstdError):
            return None
        raise


def read_segment_frames(path: Path) -> list[Frame]:
    """Read every frame from one segment file, in order. Raises PersistStoreError on corruption."""
    compressed = path.suffix == ".zst"
    frames: list[Frame] = []
    try:
        with _open_segment_text(path, compressed=compressed) as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                record_type = payload.get("type")
                if line_no == 1:
                    if record_type != "segment_header":
                        raise ValueError("missing segment header")
                    continue
                if record_type != "frame":
                    raise ValueError(f"unexpected record type: {record_type!r}")
                frame_payload = dict(payload)
                frame_payload.pop("type", None)
                frames.append(frame_from_jsonable(frame_payload))
    except (json.JSONDecodeError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise PersistStoreError(f"corrupt segment {path.name}: {exc}") from None
    except Exception as exc:
        if _ZstdError is not None and isinstance(exc, _ZstdError):
            raise PersistStoreError(f"corrupt or truncated segment {path.name}: {exc}") from None
        raise  # pragma: no cover - defensive: some exception neither listed above nor zstd's own
    return frames


class PersistentHistoryStore:
    """Atomically-published, age-and-byte-capped, corruption-recoverable
    on-disk frame history.

    ``append`` is best-effort: a disk-full/read-only/unexpected error degrades
    the store (visible via :meth:`stats`) rather than raising, so a caller
    feeding it from the live collection loop never crashes collection (O7).
    """

    def __init__(self, config: PersistConfig, *, now: Callable[[], float] | None = None) -> None:
        if not isinstance(config, PersistConfig):
            raise TypeError("config must be a PersistConfig")
        self.config = config
        self._now = now or time.time
        self._lock = threading.RLock()
        self._segments: list[SegmentInfo] = []
        self._next_segment_id = 0
        self._evicted_segments = 0
        self._evicted_frames = 0
        self._write_errors = 0
        self._lifetime_bytes_written = 0
        self._lifetime_raw_bytes_written = 0
        self._lifetime_frames_written = 0
        self._lifetime_index_bytes_written = 0
        self._quarantined_this_run = 0
        self._degraded = False
        self._degraded_reason: str | None = None
        self._degraded_retry_after = 0.0
        self._recovery_state = "disabled"
        self._active: _SegmentWriter | None = None
        self._closed = False
        if self.config.enabled:
            self._recover()

    # -- paths -----------------------------------------------------------

    @property
    def segments_dir(self) -> Path:
        return self.config.dir / "segments"

    @property
    def quarantine_dir(self) -> Path:
        return self.config.dir / "quarantine"

    @property
    def index_path(self) -> Path:
        return self.config.dir / "index.json"

    @property
    def _compressed(self) -> bool:
        return self.config.compression == "zstd" and _zstd is not None

    # -- recovery ----------------------------------------------------------

    def _recover(self) -> None:
        cfg = self.config
        try:
            cfg.dir.mkdir(parents=True, exist_ok=True, mode=cfg.dir_mode)
            self.segments_dir.mkdir(parents=True, exist_ok=True, mode=cfg.dir_mode)
            self.quarantine_dir.mkdir(parents=True, exist_ok=True, mode=cfg.dir_mode)
        except OSError as exc:
            self._mark_degraded(exc)
            self._recovery_state = "degraded"
            return

        self._clean_tmp_leftovers()
        index_cache = self._load_index_cache()

        candidates: list[tuple[int, Path]] = []
        for path in self.segments_dir.glob("seg-*"):
            match = _SEGMENT_NAME_RE.match(path.name)
            if not match:
                continue
            candidates.append((int(match.group(1)), path))
        candidates.sort(key=lambda item: item[0])

        segments: list[SegmentInfo] = []
        any_bad = False
        for segment_id, path in candidates:
            info = self._validate_segment(segment_id, path, index_cache.get(path.name))
            if info is None:
                any_bad = True
                continue
            segments.append(info)

        self._segments = segments
        highest_id = max((sid for sid, _ in candidates), default=-1)
        cached_next = index_cache.get("__next_segment_id__")
        self._next_segment_id = max(highest_id + 1, int(cached_next) if isinstance(cached_next, int) else 0)
        self._evicted_segments = int(index_cache.get("__evicted_segments__", 0) or 0)
        self._evicted_frames = int(index_cache.get("__evicted_frames__", 0) or 0)

        self._evict_locked(publish_index=False)
        self._recovery_state = "recovered" if (any_bad or self._quarantined_this_run) else (
            "clean" if segments else "empty"
        )
        self._write_index()

    def _clean_tmp_leftovers(self) -> None:
        for path in self.segments_dir.glob("*.tmp"):
            path.unlink(missing_ok=True)
        tmp_index = self.index_path.with_suffix(".json.tmp")
        tmp_index.unlink(missing_ok=True)

    def _load_index_cache(self) -> dict[str, object]:
        """A best-effort filename -> recorded-checksum cache. Never trusted
        for correctness on its own — every segment is still re-hashed."""
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != _INDEX_SCHEMA_VERSION:
            return {}
        cache: dict[str, object] = {
            "__next_segment_id__": payload.get("next_segment_id"),
            "__evicted_segments__": payload.get("evicted_segments"),
            "__evicted_frames__": payload.get("evicted_frames"),
        }
        for entry in payload.get("segments", []) or []:
            if not isinstance(entry, dict):
                continue
            filename = entry.get("filename")
            if isinstance(filename, str):
                cache[filename] = entry
        return cache

    def _validate_segment(self, segment_id: int, path: Path, cached_entry: object) -> SegmentInfo | None:
        try:
            actual_sha256 = _file_sha256(path)
            actual_size = path.stat().st_size
        except OSError:
            self._quarantine(path, reason="unreadable")
            return None

        if isinstance(cached_entry, dict):
            # The index recorded a specific durable checksum for this filename
            # at publish time. A mismatch means the on-disk bytes changed
            # since that commit — always corruption, never trusted via
            # content re-parse (a corrupted file can still happen to parse as
            # syntactically valid, unrelated frames).
            if cached_entry.get("sha256") != actual_sha256:
                self._quarantine(path, reason="corrupt")
                return None
            frame_count = cached_entry.get("frame_count")
            first_ts = cached_entry.get("first_ts")
            last_ts = cached_entry.get("last_ts")
            raw_bytes = cached_entry.get("raw_bytes")
            created_at = cached_entry.get("created_at")
            if all(isinstance(v, (int, float)) for v in (frame_count, first_ts, last_ts, raw_bytes)):
                return SegmentInfo(
                    segment_id=segment_id,
                    filename=path.name,
                    frame_count=int(frame_count),
                    first_ts=float(first_ts),
                    last_ts=float(last_ts),
                    byte_size=actual_size,
                    raw_bytes=int(raw_bytes),
                    sha256=actual_sha256,
                    created_at=float(created_at) if isinstance(created_at, (int, float)) else self._now(),
                )
            # Checksum matched but the cached record itself is malformed
            # (missing/invalid fields) — fall through to a full re-parse.

        # No recorded checksum for this filename (an orphan: e.g. the segment
        # was durably published but the index update that would have
        # referenced it never landed before a crash). Best-effort recover it
        # by fully re-parsing its content.
        inspected = _inspect_segment_file(path, expected_segment_id=segment_id)
        if inspected is None:
            self._quarantine(path, reason="corrupt")
            return None
        frame_count, first_ts, last_ts, raw_bytes = inspected
        return SegmentInfo(
            segment_id=segment_id,
            filename=path.name,
            frame_count=frame_count,
            first_ts=first_ts,
            last_ts=last_ts,
            byte_size=actual_size,
            raw_bytes=raw_bytes,
            sha256=actual_sha256,
            created_at=self._now(),
        )

    def _quarantine(self, path: Path, *, reason: str) -> None:
        self._quarantined_this_run += 1
        try:
            target = self.quarantine_dir / path.name
            if target.exists():
                target = self.quarantine_dir / f"{path.name}.{int(self._now() * 1000)}"
            path.rename(target)
        except OSError:
            pass

    # -- gap/eviction reporting --------------------------------------------

    def _compute_gaps_locked(self) -> tuple[GapRange, ...]:
        gaps: list[GapRange] = []
        for previous, current in zip(self._segments, self._segments[1:]):
            if current.segment_id != previous.segment_id + 1:
                gaps.append(
                    GapRange(start_ts=previous.last_ts, end_ts=current.first_ts, reason="quarantined")
                )
        return tuple(gaps)

    # -- degraded mode -------------------------------------------------

    def _mark_degraded(self, exc: OSError) -> None:
        self._degraded = True
        self._degraded_reason = str(exc)
        self._write_errors += 1
        # Cool down: retry disk writes only every 30s so a persistently full
        # or read-only disk does not turn every append() into a syscall storm.
        self._degraded_retry_after = self._now() + 30.0

    def _should_attempt_write(self) -> bool:
        if not self._degraded:
            return True
        return self._now() >= self._degraded_retry_after

    # -- writing -------------------------------------------------------

    def append(self, frame: Frame) -> None:
        """Persist one canonical frame. Never raises: disk failures degrade
        the store instead (O7)."""
        if not self.config.enabled or self._closed:
            return
        with self._lock:
            if not self._should_attempt_write():
                return
            try:
                self._append_locked(frame)
                if self._degraded:
                    # The retry above succeeded: disk recovered.
                    self._degraded = False
                    self._degraded_reason = None
            except OSError as exc:
                if self._active is not None:
                    self._active.abort()
                    self._active = None
                self._mark_degraded(exc)

    def _append_locked(self, frame: Frame) -> None:
        if self._active is None:
            self._open_active_segment()
        assert self._active is not None
        self._active.write_frame(frame)
        if self.config.fsync and self._active.frame_count % self.config.checkpoint_frames == 0:
            self._active.checkpoint()
        if self._active.frame_count >= self.config.segment_frames:
            self._publish_active_locked()

    def _open_active_segment(self) -> None:
        self.segments_dir.mkdir(parents=True, exist_ok=True, mode=self.config.dir_mode)
        segment_id = self._next_segment_id
        filename = _segment_filename(segment_id, compressed=self._compressed)
        tmp_path = self.segments_dir / f".{filename}.{os.getpid()}.tmp"
        self._active = _SegmentWriter(tmp_path, segment_id, compress=self._compressed)

    def _publish_active_locked(self) -> None:
        writer = self._active
        assert writer is not None
        self._active = None
        byte_size, sha256 = writer.finalize()
        final_path = self.segments_dir / _segment_filename(writer.segment_id, compressed=writer.compress)
        os.replace(writer.tmp_path, final_path)
        os.chmod(final_path, self.config.file_mode)
        info = SegmentInfo(
            segment_id=writer.segment_id,
            filename=final_path.name,
            frame_count=writer.frame_count,
            first_ts=writer.first_ts if writer.first_ts is not None else self._now(),
            last_ts=writer.last_ts if writer.last_ts is not None else self._now(),
            byte_size=byte_size,
            raw_bytes=writer.raw_bytes,
            sha256=sha256,
            created_at=self._now(),
        )
        self._segments.append(info)
        self._next_segment_id = writer.segment_id + 1
        self._lifetime_bytes_written += byte_size
        self._lifetime_raw_bytes_written += writer.raw_bytes
        self._lifetime_frames_written += writer.frame_count
        self._evict_locked(publish_index=False)
        self._write_index()

    def flush(self) -> None:
        """Publish the in-progress segment even if it is not yet full."""
        if not self.config.enabled:
            return
        with self._lock:
            if self._active is not None and self._active.frame_count > 0:
                try:
                    self._publish_active_locked()
                except OSError as exc:
                    # _publish_active_locked() always clears self._active to
                    # None as its first action, before any fallible I/O, and
                    # flush() (unlike append()) has no other call into it
                    # that could raise with an active writer still live --
                    # so this guard can never be True while holding
                    # self._lock. Kept for structural symmetry with
                    # append()'s handler below, where it IS reachable.
                    if self._active is not None:  # pragma: no cover - unreachable, see above
                        self._active.abort()
                        self._active = None
                    self._mark_degraded(exc)

    def close(self) -> None:
        self.flush()
        self._closed = True

    # -- eviction ------------------------------------------------------

    def _evict_locked(self, *, publish_index: bool) -> bool:
        cfg = self.config
        now = self._now()
        changed = False
        while self._segments:
            total_bytes = sum(s.byte_size for s in self._segments)
            oldest = self._segments[0]
            age_violated = (now - oldest.first_ts) > cfg.max_age_seconds
            byte_violated = total_bytes > cfg.max_size_bytes
            if not (age_violated or byte_violated):
                break
            (self.segments_dir / oldest.filename).unlink(missing_ok=True)
            self._segments.pop(0)
            self._evicted_segments += 1
            self._evicted_frames += oldest.frame_count
            changed = True
        if changed and publish_index:
            self._write_index()
        return changed

    # -- index persistence -----------------------------------------------

    def _write_index(self) -> None:
        payload = {
            "schema_version": _INDEX_SCHEMA_VERSION,
            "next_segment_id": self._next_segment_id,
            "evicted_segments": self._evicted_segments,
            "evicted_frames": self._evicted_frames,
            "segments": [
                {
                    "segment_id": s.segment_id,
                    "filename": s.filename,
                    "frame_count": s.frame_count,
                    "first_ts": s.first_ts,
                    "last_ts": s.last_ts,
                    "byte_size": s.byte_size,
                    "raw_bytes": s.raw_bytes,
                    "sha256": s.sha256,
                    "created_at": s.created_at,
                }
                for s in self._segments
            ],
        }
        tmp_path = self.index_path.with_suffix(".json.tmp")
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self.index_path)
        os.chmod(self.index_path, self.config.file_mode)
        self._lifetime_index_bytes_written += len(text)

    # -- reading ---------------------------------------------------------

    def iter_segments(self) -> tuple[SegmentInfo, ...]:
        """Currently-retained segments in ascending id (== time) order."""
        with self._lock:
            return tuple(self._segments)

    def read_frames(
        self, *, since_ts: float | None = None, until_ts: float | None = None
    ) -> list[tuple[int, Frame, bool]]:
        """Read frames across all retained segments as ``(seq, frame, gap_before)``.

        ``seq`` is a store-local monotonic sequence assigned by read order.
        ``gap_before`` is True on the first frame after a detected hole
        (quarantined/lost segment) between two retained segments.
        """
        with self._lock:
            segments = tuple(self._segments)
            gap_after_id: set[int] = {
                previous.segment_id
                for previous, current in zip(segments, segments[1:])
                if current.segment_id != previous.segment_id + 1
            }
        out: list[tuple[int, Frame, bool]] = []
        seq = 0
        for index, segment in enumerate(segments):
            path = self.segments_dir / segment.filename
            try:
                frames = read_segment_frames(path)
            except PersistStoreError:
                continue
            gap_before_segment = index == 0 or segments[index - 1].segment_id in gap_after_id
            for position, frame in enumerate(frames):
                if since_ts is not None and frame.ts < since_ts:
                    continue
                if until_ts is not None and frame.ts >= until_ts:
                    continue
                gap_before = position == 0 and gap_before_segment and index > 0
                out.append((seq, frame, gap_before))
                seq += 1
        return out

    # -- stats -------------------------------------------------------------

    def stats(self) -> StoreStats:
        with self._lock:
            segments = tuple(self._segments)
            gaps = self._compute_gaps_locked()
            quarantined = sum(1 for _ in self.quarantine_dir.glob("*")) if self.quarantine_dir.exists() else 0
            byte_size = sum(s.byte_size for s in segments)
            raw_bytes = sum(s.raw_bytes for s in segments)
            frame_count = sum(s.frame_count for s in segments)
            evicted = self._evicted_segments > 0 or quarantined > 0 or bool(gaps)
            return StoreStats(
                enabled=self.config.enabled,
                dir=str(self.config.dir),
                degraded=self._degraded,
                degraded_reason=self._degraded_reason,
                recovery_state=self._recovery_state,
                segment_count=len(segments),
                frame_count=frame_count,
                byte_size=byte_size,
                raw_bytes=raw_bytes,
                oldest_ts=segments[0].first_ts if segments else None,
                newest_ts=segments[-1].last_ts if segments else None,
                evicted=evicted,
                evicted_segments=self._evicted_segments,
                evicted_frames=self._evicted_frames,
                quarantined_segments=quarantined,
                gaps=gaps,
                write_errors=self._write_errors,
                compression_ratio=(raw_bytes / byte_size) if byte_size else None,
                age_cap_seconds=self.config.max_age_seconds,
                byte_cap_bytes=self.config.max_size_bytes,
                lifetime_bytes_written=self._lifetime_bytes_written,
                lifetime_raw_bytes_written=self._lifetime_raw_bytes_written,
                lifetime_frames_written=self._lifetime_frames_written,
                lifetime_index_bytes_written=self._lifetime_index_bytes_written,
            )
