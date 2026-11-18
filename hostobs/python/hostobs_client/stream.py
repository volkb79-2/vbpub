"""The frame reader (WIRE.md 5.2) and the client's per-stream dictionary state (6.2).

Standard library only. ``FrameReader`` is the section 5.2 machine with the section 7.3
buffering: once the 12 header bytes validate against the client limit, one ``bytearray(len)``
is allocated, the header is copied in and the rest is filled in place. ``fill()`` returns the
writable slice to fill (``sock.recv_into``) and ``advance(n, now_ns)`` accounts for the bytes
written; ``feed(data, now_ns)`` is implemented on top of the pair.
"""

from __future__ import annotations

from typing import NamedTuple

from .wire import (
    FRAME_HEADER_LEN,
    MAX_FRAME_BYTES,
    Block,
    Dictionary,
    WireError,
    _err,
    parse_frame_header,
)


class RawFrame(NamedTuple):
    """One complete frame: ``data`` holds all ``length`` bytes, header first (owned by the caller)."""

    type: int
    corr: int
    length: int
    data: bytearray


class FrameReader:
    """The section 5.2 state table. ``now_ns`` comes from the caller's injected clock."""

    def __init__(self, max_frame: int, completion_ns: int) -> None:
        self._max_frame = max_frame
        self._completion_ns = completion_ns
        self._hdr = bytearray(FRAME_HEADER_LEN)
        self._have = 0
        self._buf: bytearray | None = None
        self._body_have = 0
        self._length = 0
        self._type = 0
        self._corr = 0
        self._started = 0
        self._error: WireError | None = None
        self._closed = False

    @property
    def error(self) -> WireError | None:
        """The error that poisoned the reader, or ``None``."""
        return self._error

    @property
    def buffered_len(self) -> int:
        """Bytes of the partly received frame held now (0 at a frame boundary)."""
        return self._body_have if self._buf is not None else self._have

    @property
    def buffered_capacity(self) -> int:
        """Size of the buffer being filled: 12 until a header validates, then the frame's length."""
        return len(self._buf) if self._buf is not None else len(self._hdr)

    def _poison(self, error: WireError) -> WireError:
        self._error = error
        self._buf = None
        self._have = 0
        self._body_have = 0
        return error

    def fill(self) -> memoryview:
        """The writable slice the next bytes of the stream belong in (empty once poisoned)."""
        if self._error is not None:
            return memoryview(b"")
        if self._buf is not None:
            return memoryview(self._buf)[self._body_have :]
        return memoryview(self._hdr)[self._have :]

    def advance(self, n: int, now_ns: int) -> tuple[list[RawFrame], WireError | None]:
        """Account for ``n`` bytes written into the last ``fill()`` slice at ``now_ns``.

        After ``eof()`` any ``n > 0`` is ``trailing-bytes`` with ``extra = n``, the bytes this call
        accounts for (``feed`` names its whole chunk, the same thing when it is one ``recv``).
        """
        if n < 0:
            raise ValueError("advance() of a negative byte count")
        if self._error is not None:
            return [], self._error
        if n == 0:
            return [], None
        if self._closed:
            return [], self._poison(_err("trailing-bytes", extra=n))
        if n > len(self.fill()):
            raise ValueError("advance() beyond the slice fill() returned")
        if self._buf is None:
            if self._have == 0:
                self._started = now_ns
            self._have += n
            if self._have < FRAME_HEADER_LEN:
                return [], None
            try:
                length, code, _flags, corr = parse_frame_header(self._hdr, self._max_frame)
            except WireError as error:
                return [], self._poison(error)
            if length == FRAME_HEADER_LEN:
                frame = RawFrame(code, corr, length, self._hdr)
                self._hdr = bytearray(FRAME_HEADER_LEN)
                self._have = 0
                return [frame], None
            buf = bytearray(length)
            buf[:FRAME_HEADER_LEN] = self._hdr
            self._buf = buf
            self._body_have = FRAME_HEADER_LEN
            self._length = length
            self._type = code
            self._corr = corr
            self._have = 0
            return [], None
        self._body_have += n
        if self._body_have < self._length:
            return [], None
        frame = RawFrame(self._type, self._corr, self._length, self._buf)
        self._buf = None
        self._body_have = 0
        return [frame], None

    def feed(self, data: bytes, now_ns: int) -> tuple[list[RawFrame], WireError | None]:
        """Consume ``data`` received at ``now_ns``: the frames it completed and at most one error.

        An empty ``data`` is not an event; a poisoned reader returns its error again.
        """
        frames: list[RawFrame] = []
        if self._error is not None:
            return frames, self._error
        if self._closed and data:
            return frames, self._poison(_err("trailing-bytes", extra=len(data)))
        source = memoryview(data)
        position = 0
        total = len(source)
        while position < total:
            target = self.fill()
            take = min(len(target), total - position)
            target[:take] = source[position : position + take]
            position += take
            done, error = self.advance(take, now_ns)
            frames.extend(done)
            if error is not None:
                return frames, error
        return frames, None

    def poll(self, now_ns: int) -> WireError | None:
        """The completion age: ``deadline`` when a frame is partial and ``now > started + completion``."""
        if self._error is not None:
            return self._error
        if (self._have > 0 or self._buf is not None) and now_ns > self._started + self._completion_ns:
            return self._poison(_err("deadline"))
        return None

    def eof(self) -> WireError | None:
        """The peer ended the stream: ``None`` at a frame boundary, else ``truncated``."""
        if self._error is not None:
            return self._error
        if self._buf is not None:
            return self._poison(
                _err("truncated", context="frame body at end of stream", need=self._length, have=self._body_have)
            )
        if self._have > 0:
            return self._poison(
                _err("truncated", context="frame header at end of stream", need=FRAME_HEADER_LEN, have=self._have)
            )
        self._closed = True
        return None


class Session:
    """What a hello says about the probe seen before (WIRE.md 6.4)."""

    FIRST = "first"
    SAME = "same"
    CHANGED = "changed"


class StreamDicts:
    """One held dictionary per stream ``corr`` and the probe identity of the last hello.

    Per received BLOCK: ``decode_block(buf, dicts.held(corr))``, then ``accept(corr, block)``;
    after any decoder error call ``close(corr)`` and close the connection (WIRE.md 6.5).
    ``accept`` raises ``WireError`` for ``generation-regressed`` and ``stale-dictionary-generation``
    and forgets the stream's dictionary (and its retained rows) on every error.
    """

    def __init__(self) -> None:
        self._streams: dict[int, Dictionary] = {}
        self._identity: tuple[str, int] | None = None

    def held(self, corr: int) -> Dictionary | None:
        return self._streams.get(corr)

    def stream_count(self) -> int:
        return len(self._streams)

    def accept(self, corr: int, block: Block) -> None:
        held = self._streams.get(corr)
        floor = held.generation if held is not None else 0
        carried = block.dictionary
        if carried is not None:
            if carried.generation > floor:
                self._streams[corr] = carried
                return
            error = _err("generation-regressed", held=floor, got=carried.generation)
        else:
            needed = block.header.dict_gen
            if held is not None and held.generation == needed:
                return
            error = _err("stale-dictionary-generation", needed=needed, held=floor)
        self.close(corr)
        raise error

    def close(self, corr: int) -> None:
        held = self._streams.pop(corr, None)
        if held is not None:
            held._rows_ok = None

    def reset(self) -> None:
        for held in self._streams.values():
            held._rows_ok = None
        self._streams.clear()

    def on_hello(self, boot_id: str, epoch: int) -> str:
        self.reset()
        identity = (boot_id, epoch)
        if self._identity is None:
            session = Session.FIRST
        elif self._identity == identity:
            session = Session.SAME
        else:
            session = Session.CHANGED
        if session != Session.SAME:
            self._identity = identity
        return session


__all__ = ["FrameReader", "RawFrame", "Session", "StreamDicts", "MAX_FRAME_BYTES"]
