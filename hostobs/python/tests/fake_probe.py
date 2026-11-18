"""A scripted in-process fake probe for the connection tests (no live probe, no sleeps).

The probe end of an ``AF_UNIX`` socketpair is written **before** the client call that reads it
(the bytes wait in the socket buffer), and the client's bytes are read back afterwards, so one
thread steps both sides deterministically. :class:`ScriptedSocket` wraps the client end: a
``recv_into`` with nothing buffered does not block, it advances the fake clock by the socket timeout
the client just set and raises ``TimeoutError``, so every deadline is exercised without waiting.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOSTOBS / "python"))

from hostobs_client.conn import Connection, LIMIT_KEYS  # noqa: E402
from sp4_frames import frame as sp4_frame  # noqa: E402

BOOT = "00112233-4455-6677-8899-aabbccddeeff"
NS = 10**9
ERROR_CODES = (
    "unknown-mode", "unsupported-version", "hello-required", "bad-frame", "frame-too-large", "deadline",
    "idle", "slow-client", "shutting-down", "bad-request", "unknown-verb", "forbidden", "binary-required",
    "too-many-requests", "budget", "rate-limited", "not-found", "cursor-invalid", "span-unknown",
    "unsupported-feature", "session-lost", "window-lost", "internal",
)
REQ, REP, EVT, DICT, BLOCK, END = 1, 2, 3, 4, 5, 6


class Clock:
    def __init__(self, now: int = 1_000 * NS) -> None:
        self.now = now
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        return self.now

    def advance(self, ns: int) -> None:
        self.now += int(ns)


class ScriptedSocket:
    """The client end: never blocks, turns 'no data yet' into a clock jump plus ``TimeoutError``."""

    def __init__(self, sock: socket.socket, clock: Clock, max_chunk: int | None = None) -> None:
        self.sock = sock
        self.clock = clock
        self.max_chunk = max_chunk
        self.timeout: float | None = None
        self.timeouts: list[float] = []
        self.reset_after: int | None = None  # raise ECONNRESET once this many bytes were read
        self.read = 0
        self.exact = False  # a timeout advances the clock by exactly the timeout (no extra nanosecond)
        self.on_send = None  # called with the bytes about to be sent (may advance the clock)

    def settimeout(self, value: float | None) -> None:
        # 0 would make a real socket non-blocking and a negative value raises: both are client bugs
        assert value is not None and value > 0, f"non-positive socket timeout {value!r}"
        self.timeout = value
        self.timeouts.append(value)

    def recv_into(self, view) -> int:
        if self.reset_after is not None and self.read >= self.reset_after:
            raise ConnectionResetError(104, "Connection reset by peer")
        if self.max_chunk is not None:
            view = view[: self.max_chunk]
        try:
            got = self.sock.recv_into(view, 0, socket.MSG_DONTWAIT)
        except BlockingIOError:
            self.clock.advance(round(self.timeout * NS) + (0 if self.exact else 1))
            raise TimeoutError("timed out") from None
        self.read += got
        return got

    def sendall(self, data: bytes) -> None:
        if self.on_send is not None:
            self.on_send(data)
        self.sock.sendall(data)

    def close(self) -> None:
        self.sock.close()

    @property
    def closed(self) -> bool:
        return self.sock.fileno() == -1


def json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def hello_result(level: str = "observe", minor: int = 1, **over: object) -> dict:
    limits = dict.fromkeys(LIMIT_KEYS, 4)
    limits.update(max_frame_bytes=16 * 1024 * 1024, max_request_bytes=32 * 1024, max_outstanding=4, tick_ms=1000)
    result = {
        "proto": {"major": 1, "minor": minor}, "level": level, "server": "hostobsd-test", "boot_id": BOOT,
        "epoch": 1, "features": ["peak_reset"], "verbs": ["hello", "status"],
        "quality_codes": [{"code": 0, "name": "ok"}], "limits": limits,
    }
    result.update(over)
    return result


def raw_frame(kind: int, corr: int, payload: bytes = b"", flags: int = 0) -> bytes:
    return struct.pack("<IBBHI", 12 + len(payload), kind, flags, 0, corr) + payload


def block_frame(corr: int, with_dictionary: bool, dropped: int = 0, n: int = 3, variant: int = 0) -> bytes:
    data = bytearray(sp4_frame(n, variant, with_dictionary))
    struct.pack_into("<I", data, 8, corr)
    n_sec = struct.unpack_from("<H", data, 12)[0]
    struct.pack_into("<I", data, 88 + 8 * n_sec, dropped)
    return bytes(data)


class Probe:
    """The server end of the socketpair."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.buffer = bytearray()
        self.prefix: bytes | None = None

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def prefix_reply(self, major: int = 1, minor: int = 1) -> None:
        self.send(b"HOB\x01" + struct.pack("<HH", major, minor))

    def rep_ok(self, corr: int, result: dict | None = None) -> None:
        self.send(raw_frame(REP, corr, json_bytes({"ok": True, "result": result if result is not None else {}})))

    def rep_err(self, corr: int, code: str, message: str = "m", detail: dict | None = None) -> None:
        body = {"ok": False, "error": {"code": code, "message": message, "detail": detail or {}}}
        self.send(raw_frame(REP, corr, json_bytes(body)))

    def block(self, corr: int, with_dictionary: bool, dropped: int = 0, variant: int = 0) -> None:
        self.send(block_frame(corr, with_dictionary, dropped, variant=variant))

    def end(self, corr: int, blocks: int, dropped: int = 0, reason: str = "complete", nxt: str | None = None) -> None:
        body = {"ok": True, "blocks": blocks, "dropped": dropped, "next": nxt, "reason": reason}
        self.send(raw_frame(END, corr, json_bytes(body)))

    def end_err(self, corr: int, blocks: int, code: str = "internal") -> None:
        body = {"ok": False, "blocks": blocks, "dropped": 0, "error": {"code": code, "message": "m", "detail": {}}}
        self.send(raw_frame(END, corr, json_bytes(body)))

    def requests(self) -> list[tuple[int, str, dict]]:
        """Complete requests the client has sent since the last call: ``(corr, verb, args)``."""
        while True:
            try:
                chunk = self.sock.recv(1 << 16, socket.MSG_DONTWAIT)
            except BlockingIOError:
                break
            if not chunk:
                break
            self.buffer += chunk
        if self.prefix is None and len(self.buffer) >= 8:
            self.prefix = bytes(self.buffer[:8])
            del self.buffer[:8]
        out = []
        while len(self.buffer) >= 12:
            length, kind, _flags, _res, corr = struct.unpack_from("<IBBHI", self.buffer)
            if len(self.buffer) < length:
                break
            assert kind == REQ
            body = json.loads(bytes(self.buffer[12:length]))
            out.append((corr, body["verb"], body["args"]))
            del self.buffer[:length]
        return out

    def at_eof(self) -> bool:
        try:
            return self.sock.recv(1, socket.MSG_DONTWAIT) == b""
        except BlockingIOError:
            return False
        except ConnectionResetError:
            return True


class Rig:
    """A socketpair, a clock, the scripted client socket and the probe."""

    def __init__(self, max_chunk: int | None = None) -> None:
        self.clock = Clock()
        client, server = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.client = ScriptedSocket(client, self.clock, max_chunk)
        self.probe = Probe(server)

    def connect(self, level: str = "observe", timeout_s: float = 5.0, minor: int = 1, **hello_over: object) -> Connection:
        self.probe.prefix_reply(1, minor)
        self.probe.rep_ok(1, hello_result(level, minor, **hello_over))
        return Connection(self.client, level, client="test-client", timeout_s=timeout_s, clock=self.clock)

    def close(self) -> None:
        self.client.close()
        self.probe.sock.close()
