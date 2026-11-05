"""Typed client of the topos-sampler wire protocol v1 (``docs/SAMPLER-PROTOCOL.md``).

Standard library only. One connection, requests strictly one at a time, every
read bounded in bytes and in time. A failure is always a :class:`SamplerError`
subclass; nothing here blocks forever, so the caller may run it on a worker
thread and stop waiting at any time.

The sampler's ``epoch`` is remembered per client. When a response carries a
different epoch than the previous one, the sampler restarted: counters, ids and
history gaps are not comparable across it. The client records that in
:attr:`SamplerClient.epoch_changes` and keeps answering; the caller decides.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
DEFAULT_SOCKET_PATH = Path("/run/topos/sampler.sock")
"""Production socket path (docs/SAMPLER-PROTOCOL.md, Transport)."""

DEFAULT_TIMEOUT_S = 2.0
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
"""Bound used until ``hello`` reports the sampler's own ``limits.max_response_bytes``."""
_SLACK_BYTES = 4096
_CHUNK = 65536


class SamplerError(Exception):
    """Base of every error this module raises."""


class SamplerUnavailable(SamplerError):
    """The socket is absent, refused the connection, or closed it."""


class SamplerTimeout(SamplerError):
    """The sampler did not answer in time."""


class SamplerProtocolError(SamplerError):
    """The answer is not valid protocol v1 (bad JSON, wrong version, over the byte bound)."""


class SamplerRemoteError(SamplerError):
    """The sampler answered ``ok: false``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class EpochChange:
    previous: str
    current: str


class SamplerClient:
    def __init__(
        self,
        socket_path: Path | str = DEFAULT_SOCKET_PATH,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.timeout_s = float(timeout_s)
        self.max_response_bytes = int(max_response_bytes)
        self.epoch: str | None = None
        self.epoch_changes: list[EpochChange] = []
        self._sock: socket.socket | None = None
        self._buf = b""
        self._next_id = 0
        self._lock = threading.Lock()

    # ── connection ────────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            self._drop()

    def _drop(self) -> None:
        sock, self._sock, self._buf = self._sock, None, b""
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _connect(self) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout_s)
        try:
            sock.connect(str(self.socket_path))
        except socket.timeout as exc:
            sock.close()
            raise SamplerTimeout(f"connecting to {self.socket_path} timed out after {self.timeout_s:g}s") from exc
        except OSError as exc:
            sock.close()
            raise SamplerUnavailable(f"cannot connect to {self.socket_path}: {exc.strerror or exc}") from exc
        self._sock, self._buf = sock, b""
        return sock

    # ── one request ───────────────────────────────────────────────────

    def request(self, req: str, **fields: Any) -> dict[str, Any]:
        """Send one request and return the ``ok: true`` response.

        A connection that went away while idle (the sampler closes idle ones) is
        replaced once; a second failure is raised.
        """
        with self._lock:
            reused = self._sock is not None
            try:
                return self._exchange(req, fields)
            except (SamplerUnavailable, SamplerTimeout) as exc:
                self._drop()
                if reused and isinstance(exc, SamplerUnavailable):
                    return self._exchange(req, fields)
                raise
            except SamplerProtocolError:
                self._drop()
                raise

    def _exchange(self, req: str, fields: dict[str, Any]) -> dict[str, Any]:
        sock = self._sock or self._connect()
        self._next_id += 1
        rid = self._next_id
        line = json.dumps({"req": req, "id": rid, **fields}, separators=(",", ":")).encode() + b"\n"
        try:
            sock.sendall(line)
        except socket.timeout as exc:
            raise SamplerTimeout(f"sending {req} timed out after {self.timeout_s:g}s") from exc
        except OSError as exc:
            raise SamplerUnavailable(f"sending {req} failed: {exc.strerror or exc}") from exc
        raw = self._read_line(sock, req)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SamplerProtocolError(f"{req}: response is not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SamplerProtocolError(f"{req}: response is not an object")
        if data.get("protocol") != PROTOCOL_VERSION:
            raise SamplerProtocolError(f"{req}: protocol {data.get('protocol')!r}, this client speaks {PROTOCOL_VERSION}")
        if data.get("id") != rid:
            raise SamplerProtocolError(f"{req}: response id {data.get('id')!r} does not echo request id {rid}")
        self._note_epoch(data.get("epoch"))
        if not data.get("ok"):
            err = data.get("error")
            err = err if isinstance(err, dict) else {}
            raise SamplerRemoteError(str(err.get("code", "unknown")), str(err.get("message", "")))
        return data

    def _read_line(self, sock: socket.socket, req: str) -> bytes:
        limit = self.max_response_bytes + _SLACK_BYTES
        deadline = time.monotonic() + self.timeout_s * 3.0  # a slow drip cannot extend the wait
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line, self._buf = self._buf[:nl], self._buf[nl + 1:]
                return line
            if len(self._buf) > limit:
                raise SamplerProtocolError(f"{req}: response exceeds the {self.max_response_bytes} byte bound")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SamplerTimeout(f"{req}: response not complete within {self.timeout_s * 3.0:g}s")
            try:
                sock.settimeout(min(self.timeout_s, remaining))
                chunk = sock.recv(_CHUNK)
            except socket.timeout as exc:
                raise SamplerTimeout(f"{req}: no answer within {self.timeout_s:g}s") from exc
            except OSError as exc:
                raise SamplerUnavailable(f"{req}: connection lost: {exc.strerror or exc}") from exc
            if not chunk:
                raise SamplerUnavailable(f"{req}: the sampler closed the connection")
            self._buf += chunk

    def _note_epoch(self, epoch: Any) -> None:
        if not isinstance(epoch, str):
            return
        if self.epoch is not None and epoch != self.epoch:
            self.epoch_changes.append(EpochChange(self.epoch, epoch))
        self.epoch = epoch

    def take_epoch_changes(self) -> list[EpochChange]:
        """Epoch changes seen since the last call (oldest first)."""
        with self._lock:
            out, self.epoch_changes = self.epoch_changes, []
        return out

    # ── typed requests ────────────────────────────────────────────────

    def hello(self) -> dict[str, Any]:
        data = self.request("hello")
        limits = data.get("limits")
        if isinstance(limits, dict):
            cap = limits.get("max_response_bytes")
            if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
                self.max_response_bytes = cap
        return data

    def current(
        self,
        *,
        prefix: str | None = None,
        procs: bool = True,
        fields: Sequence[str] | None = None,
        compact: bool = False,
    ) -> dict[str, Any]:
        """Latest tick. *fields* limits the metrics (exact names, ``prefix*``); *compact* drops ``raw``,
        sends a gauge as a bare number and a counter as ``[delta, rate]``, and leaves ``not_applicable`` out."""
        body: dict[str, Any] = {"procs": procs}
        if prefix is not None:
            body["prefix"] = prefix
        if fields is not None:
            body["fields"] = list(fields)
        if compact:
            body["compact"] = True
        return self.request("current", **body)

    def series(self, entity: str, metric: str, *, tier: str = "hot", window_s: int = 60) -> dict[str, Any]:
        return self.request("series", entity=entity, metric=metric, tier=tier, window_s=int(window_s))

    def events(self, *, window_s: int = 300, entity: str | None = None, limit: int = 100) -> dict[str, Any]:
        fields: dict[str, Any] = {"window_s": int(window_s), "limit": int(limit)}
        if entity is not None:
            fields["entity"] = entity
        return self.request("events", **fields)

    def status(self) -> dict[str, Any]:
        return self.request("status")
