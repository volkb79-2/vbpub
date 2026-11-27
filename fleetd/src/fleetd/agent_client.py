"""Local client helpers for the versioned fleetd host-agent protocol."""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .admission import AdmissionError, ExitCode
from .agent_protocol import MAX_FETCH_BYTES, FrameError, read_frame, write_frame

_SEMVER_RE = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)
AGENT_SCHEMA = "fleet-host-agent/1"
_REMOTE_EXIT_CODES = frozenset(
    value for name, value in vars(ExitCode).items()
    if name.isupper() and type(value) is int
)


@dataclass(frozen=True)
class AgentClientReply:
    document: dict[str, object]
    body: bytes = b""


def _version_core(value: object, where: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or _SEMVER_RE.fullmatch(value) is None:
        raise AdmissionError(ExitCode.REFUSAL, f"{where} is not a SemVer version: {value!r}")
    core = value.split("+", 1)[0].split("-", 1)[0]
    return tuple(int(part) for part in core.split("."))  # type: ignore[return-value]


def require_compatible_agent(
    client_version: str,
    request: Mapping[str, object],
    probe: Mapping[str, object],
) -> None:
    """Apply §2.1 major refusal and minor/patch capability checks."""
    client_core = _version_core(client_version, "client version")
    host_version = probe.get("agent_version")
    host_core = _version_core(host_version, "host agent version")
    if probe.get("schema") != AGENT_SCHEMA:
        raise AdmissionError(
            ExitCode.REFUSAL,
            f"host agent schema mismatch: client {AGENT_SCHEMA} ({client_version}), "
            f"host {probe.get('schema')!r} ({host_version})",
        )
    if client_core[0] != host_core[0]:
        raise AdmissionError(
            ExitCode.REFUSAL,
            f"fleet host major-version mismatch: client {client_version}, host {host_version}",
        )
    if host_version == client_version:
        return

    verb = request.get("verb")
    capabilities = probe.get("capabilities")
    advertised = capabilities.get(verb) if isinstance(capabilities, Mapping) and isinstance(verb, str) else None
    if not isinstance(advertised, list) or any(not isinstance(item, str) for item in advertised):
        raise AdmissionError(
            ExitCode.REFUSAL,
            f"host version {host_version} does not advertise verb {verb!r} for client {client_version}",
        )
    requested_fields = {key for key in request if isinstance(key, str)}
    missing = sorted(requested_fields - set(advertised))
    if missing:
        raise AdmissionError(
            ExitCode.REFUSAL,
            f"host version {host_version} does not advertise {verb!r} fields: {', '.join(missing)}",
        )


def _validate_agent_response(
    document: Mapping[str, object], *, expected_version: str | None = None,
) -> str:
    if document.get("schema") != AGENT_SCHEMA:
        raise AdmissionError(
            ExitCode.REFUSAL,
            f"host response schema mismatch: expected {AGENT_SCHEMA}, got {document.get('schema')!r}",
        )
    version = document.get("agent_version")
    _version_core(version, "host agent response version")
    if expected_version is not None and version != expected_version:
        raise AdmissionError(
            ExitCode.REFUSAL,
            f"host agent version changed after probe: expected {expected_version}, got {version}",
        )
    return str(version)


def _read_body(stream: socket.socket, length: object) -> bytes:
    if type(length) is not int or length < 0:
        raise FrameError("agent response body length must be a non-negative integer")
    if length > MAX_FETCH_BYTES:
        raise FrameError(f"agent response body exceeds {MAX_FETCH_BYTES} bytes")
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.recv(min(65536, remaining))
        if not chunk:
            raise FrameError("agent response body was truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class AgentLeaseSession:
    """A lease's connection stays open until its container is removed."""

    def __init__(
        self, stream: socket.socket, first_reply: dict[str, object], lease_id: str, agent_version: str,
    ) -> None:
        self.stream = stream
        self.first_reply = first_reply
        self.lease_id = lease_id
        self.agent_version = agent_version

    def bind(self, container_id: str) -> dict[str, object]:
        try:
            write_frame(self.stream, {
                "event": "bind", "lease_id": self.lease_id, "container_id": container_id,
            })
            response = read_frame(self.stream, None)
        except FrameError as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, str(exc)) from exc
        _validate_agent_response(response, expected_version=self.agent_version)
        _raise_remote_error(response)
        return response

    def close(self) -> None:
        self.stream.close()


def _raise_remote_error(document: Mapping[str, object]) -> None:
    if "error" not in document:
        return
    error = document["error"]
    if (
        type(error) is not dict
        or set(error) != {"exit_code", "reason"}
        or "exit_code" in document
        or "reason" in document
    ):
        raise AdmissionError(ExitCode.RUNNER_FAILURE, "host agent returned a malformed error response")
    code = error["exit_code"]
    reason = error["reason"]
    if type(code) is not int or code not in _REMOTE_EXIT_CODES or type(reason) is not str:
        raise AdmissionError(ExitCode.RUNNER_FAILURE, "host agent returned a malformed error response")
    raise AdmissionError(code, reason)


class AgentClient:
    """Version-check and use one local Unix socket, with no fallback transport."""

    def __init__(self, socket_path: Path, client_version: str) -> None:
        _version_core(client_version, "client version")
        self.socket_path = Path(socket_path)
        self.client_version = client_version

    def connect(self) -> socket.socket:
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            stream.connect(str(self.socket_path))
        except OSError as exc:
            stream.close()
            raise AdmissionError(
                ExitCode.IDENTITY_OR_TRANSPORT,
                f"cannot connect to fleetd agent socket {self.socket_path}: {exc}",
            ) from exc
        return stream

    def _exchange(
        self,
        request: Mapping[str, object],
        body: bytes | None = None,
        *,
        expected_version: str | None = None,
    ) -> AgentClientReply:
        stream = self.connect()
        try:
            write_frame(stream, request)
            if body is not None:
                stream.sendall(body)
            stream.shutdown(socket.SHUT_WR)
            response = read_frame(stream, None)
            _validate_agent_response(response, expected_version=expected_version)
            _raise_remote_error(response)
            response_body = _read_body(stream, response.get("length", 0))
            return AgentClientReply(response, response_body)
        except FrameError as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, str(exc)) from exc
        finally:
            stream.close()

    def _check_version(self, request: Mapping[str, object]) -> str:
        probe = self._exchange({"verb": "probe"}).document
        require_compatible_agent(self.client_version, request, probe)
        return str(probe["agent_version"])

    def request(
        self,
        request: Mapping[str, object],
        *,
        body: bytes | None = None,
    ) -> AgentClientReply:
        """Run an ordinary verb after a fresh probe; lease uses ``open_lease``."""
        verb = request.get("verb")
        if verb == "lease":
            raise AdmissionError(ExitCode.REFUSAL, "use open_lease to retain the lease connection")
        expected_version = None
        if verb != "probe":
            expected_version = self._check_version(request)
        if (verb == "put") != (body is not None):
            raise AdmissionError(ExitCode.REFUSAL, "put requires exactly one streamed body")
        return self._exchange(request, body, expected_version=expected_version)

    def open_lease(self, request: Mapping[str, object]) -> AgentLeaseSession:
        """Negotiate, then keep the lease socket alive for bind and container life."""
        if request.get("verb") != "lease" or not isinstance(request.get("lease_id"), str):
            raise AdmissionError(ExitCode.REFUSAL, "open_lease requires a lease request")
        expected_version = self._check_version(request)
        stream = self.connect()
        try:
            write_frame(stream, request)
            first_reply = read_frame(stream, None)
            _validate_agent_response(first_reply, expected_version=expected_version)
            _raise_remote_error(first_reply)
            return AgentLeaseSession(stream, first_reply, str(request["lease_id"]), expected_version)
        except FrameError as exc:
            stream.close()
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, str(exc)) from exc
        except BaseException:
            stream.close()
            raise
