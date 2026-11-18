"""The hostobs/1 binary connection: handshake, requests, block streams, subscriptions (WIRE.md 1, 4, 6, 7.3).

Standard library only, no threads, one socket, one :class:`~hostobs_client.stream.FrameReader`.
Every time comes from the injected ``clock`` (``Callable[[], int]`` of monotonic nanoseconds): the
operation deadline of ``timeout_s``, the 30 s frame completion age of WIRE.md 5.2/7.3 and every
socket timeout derived from them. A call that waits runs the receive loop: frames for other
streams that arrive meanwhile are decoded and queued on their stream (a subscription keeps its
newest 8 undelivered blocks, a query stream up to 1,024).

Failure model: a decoder or envelope error, an unexpected frame or a counts mismatch closes the
connection and raises a :class:`~hostobs_client.wire.WireError`; EOF or reset raises
:class:`~hostobs_client.wire.ConnectionLost`; a wait past its deadline closes the connection and
raises :class:`OperationTimeout`; an error reply raises
:class:`~hostobs_client.wire.ServerError` and keeps the connection unless the code is
connection-fatal (WIRE.md 5.4), in which case it is closed first.
"""

from __future__ import annotations

import socket
import struct
import time
from collections import deque
from typing import Any, Callable

from . import envelope
from .envelope import LocalError
from .stream import FrameReader, RawFrame, Session, StreamDicts
from .wire import (
    FRAME_HEADER_LEN,
    MAX_FRAME_BYTES,
    Block,
    ConnectionLost,
    HostobsError,
    ServerError,
    WireError,
    decode_block,
)

PROTO_MAJOR = 1
PROTO_MINOR = 1
MAGIC = b"HOB\x01"
#: The client's frame completion age (WIRE.md 5.2, D-W16).
COMPLETION_NS = 30 * 10**9
LEVELS = ("observe", "operator", "observer", "control")
#: Connection-fatal codes (WIRE.md 5.4).
FATAL_CODES = frozenset(
    (
        "unknown-mode", "unsupported-version", "hello-required", "bad-frame", "frame-too-large",
        "deadline", "idle", "slow-client", "shutting-down",
    )
)
LIVE_QUEUE = 8
QUERY_QUEUE = 1024
LIMIT_KEYS = (
    "max_frame_bytes", "max_request_bytes", "request_deadline_ms", "hello_deadline_ms", "idle_ms",
    "stall_ms", "frame_completion_ms", "max_outstanding", "max_subscriptions", "client_queue_bytes",
    "query_queue_bytes", "control_queue_bytes", "max_reply_bytes", "max_block_bytes", "max_rows",
    "max_block_buckets", "max_page_blocks", "min_interval_ms", "tick_ms",
)
_NS = 10**9
_HEADER = struct.Struct("<IBBHI")
_PREFIX = struct.Struct("<4sHH")
_TYPE_REQ, _TYPE_REP, _TYPE_EVT, _TYPE_BLOCK, _TYPE_END = 1, 2, 3, 5, 6


class OperationTimeout(HostobsError, TimeoutError):
    """A wait ran past its ``timeout_s`` deadline; the connection is closed (its stream is unknown)."""


class ProtocolError(LocalError):
    """The server broke a rule of the protocol that is not a decoder error (the server is faulty)."""

    KINDS = frozenset(
        (
            "unexpected-frame", "unknown-corr", "bad-reply", "end-counts", "hello-mismatch",
            "queue-overflow", "corr-exhausted",
        )
    )


#: The longest ``timeout_s`` accepted (one year): ``socket.settimeout`` overflows far below ``inf``.
U64_MAX = (1 << 64) - 1
MAX_TIMEOUT_S = 365 * 24 * 3600.0
#: Bytes a connection may hold in undelivered query blocks before ``queue-overflow``.
QUERY_BYTES = 64 * 1024 * 1024


def check_timeout(timeout_s: object) -> None:
    """``ValueError`` unless ``timeout_s`` is a finite number in ``(0, MAX_TIMEOUT_S]``."""
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not 0 < timeout_s <= MAX_TIMEOUT_S
    ):
        raise ValueError(f"timeout_s must be a number in (0, {MAX_TIMEOUT_S:g}]")


def check_arguments(level: object, client: object, timeout_s: object) -> None:
    """Validate the arguments of ``open``/``Connection`` (``ValueError``), before any socket exists."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    if not isinstance(client, str) or not 0 < len(client.encode("utf-8")) <= 64:
        raise ValueError("client must be a non-empty string of at most 64 bytes")
    check_timeout(timeout_s)


def _protocol(kind: str, **detail: object) -> ProtocolError:
    return ProtocolError(kind, detail)


class _Stream:
    """One live or query stream: its decoded, undelivered blocks and what END reported."""

    def __init__(self, corr: int, verb: str, live: bool) -> None:
        self.corr = corr
        self.verb = verb
        self.live = live
        self.queue: deque[Block] = deque()
        self.received = 0
        self.sizes: deque[int] = deque()  # frame length of each queued block (parallel to ``queue``)
        self.header_dropped = 0
        self.client_dropped = 0
        self.discard = False
        self.end: envelope.End | None = None
        self.result: dict = {}


class BlockStream:
    """The BLOCKs of one block verb (``current``, ``snapshot_at``, ``series_batch``); an iterator.

    The stream header (the REP's ``result``) is ``.result``. After exhaustion ``.end`` is the END
    (``reason``, ``next`` cursor, ``blocks``, ``dropped``). An ``END ok:false`` raises
    :class:`ServerError` after the blocks queued ahead of it.
    """

    def __init__(self, connection: Connection, stream: _Stream) -> None:
        self._connection = connection
        self._stream = stream

    @property
    def result(self) -> dict:
        return self._stream.result

    @property
    def end(self) -> dict | None:
        end = self._stream.end
        if end is None:
            return None
        return {"ok": end.ok, "blocks": end.blocks, "dropped": end.dropped, "reason": end.reason, "next": end.next}

    @property
    def received(self) -> int:
        return self._stream.received

    def __iter__(self) -> BlockStream:
        return self

    def __next__(self) -> Block:
        return self._connection._next_block(self._stream, self._connection._timeout_ns)

    def close(self) -> None:
        """Stop delivering: later blocks of the stream are counted and dropped without decoding."""
        self._connection._discard(self._stream)


class Subscription:
    """A live subscription: an iterator of :class:`Block` (the newest ticks), ``.close()`` unsubscribes.

    A wait for the next block is bounded by ``timeout_s`` plus the subscription's interval.
    ``dropped`` sums the server-reported ``dropped`` counters (block headers and END);
    ``client_dropped`` counts blocks this client discarded because it did not read them (it keeps
    the newest 8).
    """

    def __init__(self, connection: Connection, stream: _Stream, interval_ms: int) -> None:
        self._connection = connection
        self._stream = stream
        self.interval_ms = interval_ms
        self.corr = stream.corr

    @property
    def dropped(self) -> int:
        end = self._stream.end
        return self._stream.header_dropped + (end.dropped if end is not None else 0)

    @property
    def client_dropped(self) -> int:
        return self._stream.client_dropped

    @property
    def received(self) -> int:
        return self._stream.received

    @property
    def reason(self) -> str | None:
        end = self._stream.end
        return end.reason if end is not None else None

    @property
    def ended(self) -> bool:
        return self._stream.end is not None

    def __iter__(self) -> Subscription:
        return self

    def __next__(self) -> Block:
        wait = self._connection._timeout_ns + self.interval_ms * 1_000_000
        return self._connection._next_block(self._stream, wait)

    def close(self) -> None:
        """Unsubscribe (REP, then END ``unsubscribed``); idempotent; a lost connection just ends it."""
        stream = self._stream
        if stream.end is not None or self._connection._sock is None:
            stream.discard = True
            self._connection._drop_queue(stream)
            return
        self._connection._discard(stream)
        try:
            self._connection.request("unsubscribe", {"sub": stream.corr})
            self._connection._pump(self._connection._deadline(), lambda: stream.end is not None)
        except ServerError as error:
            if error.code != "not-found":
                raise
        except (ConnectionLost, OperationTimeout):
            pass

    def __enter__(self) -> Subscription:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class Connection:
    """One binary-mode connection to a hostobs socket at one level (WIRE.md 1.4).

    ``Connection.open(path, level, client=..., timeout_s=..., clock=...)`` connects and completes
    the hello handshake at minor 1; the result of ``hello`` is ``.hello`` and its fields are
    ``.level``, ``.minor``, ``.boot_id``, ``.epoch``, ``.features``, ``.verbs``, ``.limits``.
    ``.session`` is ``"first"``, ``"same"`` or ``"changed"`` against the previous connection of
    the same lineage (:meth:`reconnect`, WIRE.md 6.4): ``"changed"`` means every cursor, open span
    key, session and window held from before is invalid.
    """

    def __init__(
        self,
        sock: Any,
        level: str,
        *,
        client: str,
        timeout_s: float,
        clock: Callable[[], int] = time.monotonic_ns,
        path: str | None = None,
        dicts: StreamDicts | None = None,
    ) -> None:
        check_arguments(level, client, timeout_s)
        self._sock: Any = sock
        self._expected_level = level
        self._client = client
        self._timeout_s = timeout_s
        self._timeout_ns = int(timeout_s * _NS)
        self._clock = clock
        self._path = path
        self._dicts = dicts if dicts is not None else StreamDicts()
        self._reader = FrameReader(MAX_FRAME_BYTES, COMPLETION_NS)
        self._frame_started = 0
        self._next_corr = 1
        self._waiting: set[int] = set()
        self._replies: dict[int, envelope.Rep] = {}
        self._streams: dict[int, _Stream] = {}
        self._query_bytes = 0
        self.closing_reason: str | None = None
        self.hello: dict = {}
        self.session = Session.FIRST
        self.minor = 0
        try:
            self._handshake()
        except BaseException:
            self.close()
            raise

    # ------------------------------------------------------------------ opening

    @classmethod
    def open(
        cls,
        path: str,
        level: str,
        *,
        client: str,
        timeout_s: float,
        clock: Callable[[], int] = time.monotonic_ns,
        _dicts: StreamDicts | None = None,
    ) -> Connection:
        """Connect to the ``AF_UNIX`` stream socket ``path`` and complete the handshake.

        Arguments are validated before the socket is created (``ValueError``). A connect failure
        (missing path, refused, permission) surfaces as the ``OSError`` the system raised, unchanged,
        and nothing leaks: ``require_profiling`` maps it to ``unreachable`` with its errno. Everything
        after the connect is a ``ConnectionLost``, ``OperationTimeout``, ``ServerError`` or
        ``WireError`` (WIRE.md 1.4 refusals included).
        """
        check_arguments(level, client, timeout_s)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout_s)
            sock.connect(path)
        except BaseException:
            sock.close()
            raise
        return cls(sock, level, client=client, timeout_s=timeout_s, clock=clock, path=path, dicts=_dicts)

    def reconnect(self) -> Connection:
        """Close this connection and open a new one on the same path, keeping the lineage.

        Every dictionary and subscription of this connection is dropped; ``.session`` of the result
        tells whether ``boot_id``/``epoch`` changed (WIRE.md 6.4).
        """
        if self._path is None:
            raise ValueError("a connection built from a socket cannot reconnect")
        self.close()
        return Connection.open(
            self._path, self._expected_level, client=self._client, timeout_s=self._timeout_s,
            clock=self._clock, _dicts=self._dicts,
        )

    @property
    def closed(self) -> bool:
        return self._sock is None

    @property
    def level(self) -> str:
        return self.hello["level"]

    @property
    def boot_id(self) -> str:
        return self.hello["boot_id"]

    @property
    def epoch(self) -> int:
        return self.hello["epoch"]

    @property
    def features(self) -> list:
        return self.hello["features"]

    @property
    def verbs(self) -> list:
        return self.hello["verbs"]

    @property
    def limits(self) -> dict:
        return self.hello["limits"]

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the socket and end every stream; idempotent."""
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:  # pragma: no cover - close() of a socket does not fail on Linux
                pass
        for stream in self._streams.values():
            stream.discard = True
        self._streams.clear()
        self._query_bytes = 0
        self._waiting.clear()
        self._replies.clear()
        self._dicts.reset()

    # ------------------------------------------------------------------ handshake

    def _deadline(self) -> int:
        return self._clock() + self._timeout_ns

    def _handshake(self) -> None:
        sock = self._sock
        deadline = self._deadline()
        self._settimeout(deadline)
        self._send(_PREFIX.pack(MAGIC, PROTO_MAJOR, PROTO_MINOR), deadline)
        prefix = bytearray(8)
        have = 0
        while have < 8:
            self._settimeout(deadline)
            try:
                got = sock.recv_into(memoryview(prefix)[have:])
            except TimeoutError as error:
                self._timed_out(deadline, error)
                continue
            except OSError as error:
                raise ConnectionLost(f"connection lost during the handshake: {error}") from error
            if got == 0:
                raise ConnectionLost("connection closed before the server prefix (refused, or not a hostobs socket)")
            have += got
        magic, major, minor = _PREFIX.unpack(prefix)
        if magic != MAGIC:
            raise WireError("unknown-mode", {"prefix": bytes(prefix).hex()})
        if major != PROTO_MAJOR:
            raise WireError("unsupported-version", {"server_major": major, "server_minor": minor})
        if minor > PROTO_MINOR:
            raise _protocol("hello-mismatch", why="server minor above the client's", minor=minor)
        corr = self._allocate()
        hello_args = {"client": self._client, "proto": {"major": PROTO_MAJOR, "minor": PROTO_MINOR}}
        hello_len = FRAME_HEADER_LEN + len(envelope.build_request("hello", hello_args))
        self._request_frame("hello", hello_args, corr, deadline)
        rep = self._await(corr, deadline)
        result = self._reply_result(rep)
        self._check_hello(result, minor, hello_len)
        self.hello = result
        self.minor = minor
        self.session = self._dicts.on_hello(result["boot_id"], result["epoch"])
        if self._reader.buffered_len == 0:
            self._reader = FrameReader(min(MAX_FRAME_BYTES, result["limits"]["max_frame_bytes"]), COMPLETION_NS)

    def _check_hello(self, result: dict, minor: int, hello_len: int) -> None:
        def bad(why: str, **detail: object) -> ProtocolError:
            return _protocol("hello-mismatch", why=why, **detail)

        proto = result.get("proto")
        if (
            not isinstance(proto, dict)
            or type(proto.get("major")) is not int
            or type(proto.get("minor")) is not int
            or proto["major"] != PROTO_MAJOR
            or proto["minor"] != minor
        ):
            raise bad("proto disagrees with the prefix", proto=proto)
        if result.get("level") != self._expected_level:
            raise bad("level differs from the requested one", got=result.get("level"), want=self._expected_level)
        boot_id, epoch = result.get("boot_id"), result.get("epoch")
        if type(boot_id) is not str or type(epoch) is not int or not 0 <= epoch <= U64_MAX:
            raise bad("boot_id/epoch have the wrong type or range")
        for key in ("features", "verbs", "quality_codes"):
            if type(result.get(key)) is not list:
                raise bad(f"{key} is not a list")
        if type(result.get("server")) is not str:
            raise bad("server is not a string")
        limits = result.get("limits")
        if not isinstance(limits, dict):
            raise bad("limits is not an object")
        for key in LIMIT_KEYS:
            value = limits.get(key)
            if type(value) is not int or not 0 <= value <= envelope.U32_MAX:
                raise bad("a limit is missing or not a u32", key=key)
        if limits["max_request_bytes"] < hello_len:
            raise bad("max_request_bytes cannot carry the client's own hello", max_request_bytes=limits["max_request_bytes"])
        if limits["max_frame_bytes"] < FRAME_HEADER_LEN:
            raise bad("max_frame_bytes is below a frame header", max_frame_bytes=limits["max_frame_bytes"])

    # ------------------------------------------------------------------ socket plumbing

    def _settimeout(self, deadline_ns: int) -> None:
        self._sock.settimeout(max(0.001, (deadline_ns - self._clock()) / _NS))

    def _timed_out(self, deadline_ns: int, cause: BaseException | None = None) -> None:
        if self._clock() > deadline_ns:
            self.close()
            raise OperationTimeout("the server did not answer within the deadline") from cause

    def _send(self, data: bytes, deadline_ns: int) -> None:
        if self._sock is None:
            raise ConnectionLost("connection closed")
        self._settimeout(deadline_ns)
        try:
            self._sock.sendall(data)
        except TimeoutError as error:
            self.close()
            raise OperationTimeout("the server did not accept the request within the deadline") from error
        except OSError as error:
            self.close()
            raise ConnectionLost(f"connection lost while sending: {error}") from error

    def _allocate(self) -> int:
        corr = self._next_corr
        if corr > envelope.U32_MAX:
            self.close()
            raise _protocol("corr-exhausted")
        self._next_corr = corr + 1
        return corr

    def _request_frame(self, verb: str, args: dict, corr: int, deadline_ns: int) -> None:
        payload = envelope.build_request(verb, args)
        limit = self.hello["limits"]["max_request_bytes"] if self.hello else 32 * 1024
        if FRAME_HEADER_LEN + len(payload) > limit:
            raise ValueError(f"request of {FRAME_HEADER_LEN + len(payload)} bytes exceeds max_request_bytes {limit}")
        self._send(_HEADER.pack(FRAME_HEADER_LEN + len(payload), _TYPE_REQ, 0, 0, corr) + payload, deadline_ns)

    def _fail(self, error: BaseException, corr: int | None = None) -> None:
        if corr is not None:
            self._dicts.close(corr)
        self.close()
        raise error

    # ------------------------------------------------------------------ the receive loop (7.3)

    def _pump(self, wait_deadline: int, until: Callable[[], bool]) -> None:
        while not until():
            self._receive_once(wait_deadline)

    def _receive_once(self, wait_deadline: int) -> None:
        sock = self._sock
        if sock is None:
            raise ConnectionLost("connection closed")
        reader = self._reader
        now = self._clock()
        error = reader.poll(now)
        if error is not None:
            self._fail(error)
        partial = reader.buffered_len > 0
        deadline = self._frame_started + COMPLETION_NS if partial else wait_deadline
        if not partial and now > wait_deadline:
            self._timed_out(wait_deadline)
        sock.settimeout(max(0.001, (deadline - now) / _NS))
        try:
            got = sock.recv_into(reader.fill())
        except TimeoutError:
            error = reader.poll(self._clock())
            if error is not None:
                self._fail(error)
            if reader.buffered_len == 0:
                self._timed_out(wait_deadline)
            return
        except OSError as lost:
            self.close()
            raise ConnectionLost(f"connection lost: {lost}") from lost
        now = self._clock()
        if got == 0:
            self._eof()
        boundary = reader.buffered_len == 0
        frames, error = reader.advance(got, now)
        if boundary:
            self._frame_started = now
        for frame in frames:
            self._dispatch(frame)
        if error is not None:
            self._fail(error)
        error = reader.poll(self._clock())
        if error is not None:
            self._fail(error)

    def _eof(self) -> None:
        error = self._reader.eof()
        reason = f" (server closing: {self.closing_reason})" if self.closing_reason else ""
        self.close()
        if error is not None:
            raise ConnectionLost(f"end of stream inside a frame{reason}") from error
        raise ConnectionLost(f"end of stream{reason}")

    # ------------------------------------------------------------------ dispatch

    def _dispatch(self, frame: RawFrame) -> None:
        kind = frame.type
        payload = memoryview(frame.data)[FRAME_HEADER_LEN:]
        try:
            if kind == _TYPE_BLOCK:
                self._on_block(frame)
            elif kind == _TYPE_REP:
                self._on_rep(frame, payload)
            elif kind == _TYPE_END:
                self._on_end(frame, payload)
            elif kind == _TYPE_EVT:
                event = envelope.check_evt(payload)
                if event.get("event") == "closing":
                    self.closing_reason = event["reason"]
            else:
                raise _protocol("unexpected-frame", type=kind)
        except (WireError, HostobsError) as error:
            self._fail(error, frame.corr)

    def _on_rep(self, frame: RawFrame, payload: memoryview) -> None:
        rep = envelope.check_rep(payload)
        if frame.corr == 0:
            if rep.ok:
                raise _protocol("bad-reply", why="REP ok:true with corr 0")
            error = rep.body
            self.close()
            raise ServerError(error["code"], error["message"], error["detail"])
        if frame.corr not in self._waiting:
            raise _protocol("unknown-corr", type="rep", corr=frame.corr)
        self._waiting.discard(frame.corr)
        self._replies[frame.corr] = rep

    def _on_block(self, frame: RawFrame) -> None:
        stream = self._streams.get(frame.corr)
        if stream is None:
            raise _protocol("unknown-corr", type="block", corr=frame.corr)
        stream.received += 1
        if stream.discard:
            return
        block = decode_block(frame.data, self._dicts.held(frame.corr))
        self._dicts.accept(frame.corr, block)
        stream.header_dropped += block.header.dropped
        size = len(frame.data)
        if stream.live:
            if len(stream.queue) >= LIVE_QUEUE:
                self._pop(stream)
                stream.client_dropped += 1
        elif len(stream.queue) >= QUERY_QUEUE or self._query_bytes + size > QUERY_BYTES:
            raise _protocol("queue-overflow", corr=frame.corr, queued=len(stream.queue), bytes=self._query_bytes)
        else:
            self._query_bytes += size
        stream.queue.append(block)
        stream.sizes.append(size)

    def _pop(self, stream: _Stream) -> Block:
        block = stream.queue.popleft()
        size = stream.sizes.popleft()
        if not stream.live:
            self._query_bytes = max(0, self._query_bytes - size)  # close() already zeroed it
        return block

    def _drop_queue(self, stream: _Stream) -> None:
        while stream.queue:
            self._pop(stream)

    def _on_end(self, frame: RawFrame, payload: memoryview) -> None:
        stream = self._streams.get(frame.corr)
        if stream is None or stream.end is not None:
            raise _protocol("unknown-corr", type="end", corr=frame.corr)
        end = envelope.check_end(payload)
        if end.blocks != stream.received:
            raise _protocol("end-counts", counted=stream.received, reported=end.blocks)
        if not stream.live and end.dropped != 0:
            raise _protocol("end-counts", why="a query stream reports dropped blocks", dropped=end.dropped)
        if end.ok:
            allowed = ("unsubscribed", "client-eof") if stream.live else ("complete", "page")
            if end.reason not in allowed or (end.reason == "page") != (end.next is not None):
                raise _protocol("bad-reply", why="END reason/next", reason=end.reason, next=end.next)
        stream.end = end
        self._dicts.close(frame.corr)
        self._streams.pop(frame.corr, None)

    # ------------------------------------------------------------------ requests

    def _await(self, corr: int, deadline: int) -> envelope.Rep:
        self._waiting.add(corr)
        self._pump(deadline, lambda: corr in self._replies or self._sock is None)
        rep = self._replies.pop(corr, None)
        if rep is None:
            raise ConnectionLost("connection closed")
        return rep

    def _reply_result(self, rep: envelope.Rep) -> dict:
        if rep.ok:
            return rep.body
        error = rep.body
        if error["code"] in FATAL_CODES:
            self.close()
        raise ServerError(error["code"], error["message"], error["detail"])

    def request(self, verb: str, args: dict) -> dict:
        """Send one REQ and return the REP's ``result``; an error reply raises :class:`ServerError`."""
        if self._sock is None:
            raise ConnectionLost("connection closed")
        deadline = self._deadline()
        corr = self._allocate()
        self._request_frame(verb, args, corr, deadline)
        return self._reply_result(self._await(corr, deadline))

    def _open_stream(self, verb: str, args: dict, live: bool) -> _Stream:
        if self._sock is None:
            raise ConnectionLost("connection closed")
        deadline = self._deadline()
        corr = self._allocate()
        stream = _Stream(corr, verb, live)
        self._request_frame(verb, args, corr, deadline)
        stream.result = self._reply_result(self._await(corr, deadline))
        self._streams[corr] = stream
        return stream

    def blocks(self, verb: str, args: dict) -> BlockStream:
        """Send a block verb; the REP is awaited here (an error raises), the BLOCKs are iterated."""
        return BlockStream(self, self._open_stream(verb, args, live=False))

    def subscribe(self, selection: dict, interval_ms: int) -> Subscription:
        """Subscribe to ``selection`` every ``interval_ms`` (WIRE.md 4.7)."""
        stream = self._open_stream("subscribe", {"selection": selection, "interval_ms": interval_ms}, live=True)
        result = stream.result
        sub, echoed = result.get("sub"), result.get("interval_ms")
        if type(sub) is not int or type(echoed) is not int or sub != stream.corr or echoed != interval_ms:
            self._streams.pop(stream.corr, None)
            self._fail(_protocol("bad-reply", why="subscribe result", result=result))
        return Subscription(self, stream, interval_ms)

    # ------------------------------------------------------------------ stream consumption

    def _discard(self, stream: _Stream) -> None:
        stream.discard = True
        self._drop_queue(stream)
        self._dicts.close(stream.corr)

    def _next_block(self, stream: _Stream, wait_ns: int) -> Block:
        if not stream.queue and stream.end is None and not stream.discard:
            deadline = self._clock() + wait_ns
            self._pump(deadline, lambda: bool(stream.queue) or stream.end is not None or self._sock is None)
        if stream.queue:
            return self._pop(stream)
        end = stream.end
        if end is None:
            if self._sock is None:
                raise ConnectionLost("connection closed")
            raise StopIteration  # the caller closed the stream
        if not end.ok:
            error = end.error
            raise ServerError(error["code"], error["message"], error["detail"])
        raise StopIteration


__all__ = [
    "BlockStream",
    "COMPLETION_NS",
    "Connection",
    "FATAL_CODES",
    "OperationTimeout",
    "ProtocolError",
    "Subscription",
]
