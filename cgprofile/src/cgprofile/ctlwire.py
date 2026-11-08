"""The client half of the ctl wire (PROTOCOL.md sections 2 and 10): socket selection,
one-line round trips, streams, the export transfer, cause tags and exit-code mapping.

Standard library only. Nothing here imports the report stack.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
from typing import Any, Dict, Iterator, List, Optional, Tuple

SOCKET_ENV = "CGPROFILE_CONTROL_SOCKET"
CLIENT_CONTRACTS = (1, 2)
ROUNDTRIP_TIMEOUT = 25.0

# Daemon answers by exit code (CGPROFILECTL-SPEC.md section 1).
RUNTIME_CODES = frozenset({
    "daemon-error", "stop-incomplete", "export-failed", "export-too-large",
    "persist-failed", "busy",
})

CAUSE_REMEDY = {
    "endpoint-unset": "log in again, or `set -a; . /etc/host-endpoints.env`, or run `cgprofiled install`",
    "socket-not-mounted": "bind-mount the directory named by CGPROFILE_CONTROL_SOCKET_DIR into this environment",
    "socket-missing": "the daemon is not running: systemctl status cgprofiled; systemctl start cgprofiled",
    "daemon-refusing": "journalctl -u cgprofiled; report it if it loops",
    "socket-permission": "join the socket's group (or the allow_uids list) and re-login",
    "contract-unsupported": "install a cgprofile version whose daemon and client share a contract",
}


class WireFailure(Exception):
    """A failure with its exit code and, for exit 3, its cause tag."""

    def __init__(self, message: str, *, exit_code: int, code: str, cause: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.code = code
        self.cause = cause


def resolve_socket(flag: Optional[str], env: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """Use an explicit ``--socket`` or the required host endpoint variable."""
    environ = os.environ if env is None else env
    if flag is not None:
        return flag, "flag"
    path = environ.get(SOCKET_ENV)
    if path is None or path == "":
        raise WireFailure(
            f"{SOCKET_ENV} is unset: log in again, or `set -a; . /etc/host-endpoints.env`, "
            "or run `cgprofiled install`",
            exit_code=2,
            code="endpoint-unset",
        )
    return path, "env"


def classify(path: str, exc: BaseException) -> Tuple[str, str]:
    """Which way the daemon is unreachable (CP-31), from what this process can see."""
    if isinstance(exc, FileNotFoundError):
        if not os.path.isdir(os.path.dirname(path) or "."):
            return "socket-not-mounted", f"the socket directory of {path} does not exist here"
        return "socket-missing", f"no socket at {path}"
    if isinstance(exc, ConnectionRefusedError):
        return "daemon-refusing", f"{path} exists but nothing is listening"
    if isinstance(exc, PermissionError):
        return "socket-permission", f"this user may not connect to {path}"
    return "daemon-refusing", f"{path}: {exc}"


def unreachable(path: str, exc: BaseException) -> WireFailure:
    tag, why = classify(path, exc)
    return WireFailure(f"cannot reach the daemon: {why}", exit_code=3, code="unreachable", cause=tag)


def _connect(path: str, timeout: Optional[float]) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(timeout)
        client.connect(path)
    except OSError as exc:
        client.close()
        raise unreachable(path, exc) from exc
    return client


def request_line(verb: str, args: Optional[Dict[str, Any]], contract: int) -> bytes:
    body = {"verb": verb, "args": dict(args or {}), "contract": contract}
    return (json.dumps(body) + "\n").encode("utf-8")


def _read_line(client: socket.socket) -> bytes:
    data = b""
    while not data.endswith(b"\n"):
        try:
            chunk = client.recv(65536)
        except OSError as exc:
            raise WireFailure(f"connection to the daemon failed: {exc}", exit_code=3,
                              code="unreachable", cause="daemon-refusing") from exc
        if not chunk:
            break
        data += chunk
    return data


def _bad_answer(why: str) -> WireFailure:
    return WireFailure(f"the daemon sent an unusable answer: {why}", exit_code=3,
                       code="bad-response", cause="daemon-refusing")


def parse_response(data: bytes) -> Dict[str, Any]:
    if not data.strip():
        raise _bad_answer("empty response")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _bad_answer(f"malformed JSON ({exc})") from exc
    if not isinstance(doc, dict) or type(doc.get("ok")) is not bool:
        raise _bad_answer("expected a JSON object with a boolean ok")
    return doc


def roundtrip(path: str, verb: str, args: Optional[Dict[str, Any]] = None, contract: int = 1,
              timeout: float = ROUNDTRIP_TIMEOUT) -> Dict[str, Any]:
    """One request line out, one answer line in. Returns the raw document (ok or not)."""
    client = _connect(path, timeout)
    with client:
        try:
            client.sendall(request_line(verb, args, contract))
        except OSError as exc:
            raise unreachable(path, exc) from exc
        return parse_response(_read_line(client))


OWNERSHIP_VERBS = frozenset({"stop", "export", "config-set", "config-reset"})


def refusal(doc: Dict[str, Any], verb: str = "") -> WireFailure:
    """Map a daemon ``ok: false`` answer to its exit code (CGPROFILECTL-SPEC.md section 1).

    ``peer-refused`` on an ownership or root-only verb is the safety refusal (exit 2); on any
    other verb it means this caller's uid is not authorised on the socket (exit 3,
    ``socket-permission``).
    """
    error = doc.get("error")
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or "daemon-error")
    message = str(error.get("message") or code)
    if code == "peer-refused":
        if verb in OWNERSHIP_VERBS:
            return WireFailure(f"{code}: {message}", exit_code=2, code=code)
        return WireFailure(f"{code}: {message}", exit_code=3, code=code, cause="socket-permission")
    if code in RUNTIME_CODES or code == "daemon-error":
        return WireFailure(f"{code}: {message}", exit_code=1, code=code)
    return WireFailure(f"{code}: {message}", exit_code=2, code=code)


def call(path: str, verb: str, args: Optional[Dict[str, Any]] = None, contract: int = 1) -> Dict[str, Any]:
    """A round trip that turns a daemon refusal into a ``WireFailure``."""
    doc = roundtrip(path, verb, args, contract)
    if not doc["ok"]:
        raise refusal(doc, verb)
    return doc


def daemon_contracts(version_doc: Dict[str, Any]) -> List[int]:
    contracts = version_doc.get("contracts")
    if isinstance(contracts, list) and all(type(c) is int for c in contracts) and contracts:
        return list(contracts)
    return [1]


def daemon_version(version_doc: Dict[str, Any]) -> Optional[str]:
    daemon = version_doc.get("daemon")
    if isinstance(daemon, dict) and isinstance(daemon.get("version"), str):
        return daemon["version"]
    legacy = version_doc.get("cgprofile")
    return legacy if isinstance(legacy, str) else None


def handshake(path: str) -> Tuple[Dict[str, Any], List[int]]:
    """Every daemon understands ``version`` at contract 1; read ``contracts`` from it."""
    doc = call(path, "version", None, 1)
    return doc, daemon_contracts(doc)


def need_contract(path: str, verb: str, wanted: int = 2) -> Dict[str, Any]:
    """Handshake and refuse with ``contract-unsupported`` when the daemon lacks ``wanted``."""
    doc, contracts = handshake(path)
    if wanted not in contracts:
        raise WireFailure(
            f"{verb} needs ctl contract {wanted}; the daemon "
            f"{daemon_version(doc) or '(unknown version)'} speaks {contracts}; "
            f"this client speaks {list(CLIENT_CONTRACTS)}. Install cgprofile 2.0 or newer on the daemon host.",
            exit_code=3, code="contract-unsupported", cause="contract-unsupported")
    return doc


def lines(client: socket.socket) -> Iterator[bytes]:
    """Complete non-empty lines until EOF. A trailing partial line is yielded as-is (no newline)."""
    buffer = b""
    while True:
        try:
            chunk = client.recv(65536)
        except OSError as exc:
            raise WireFailure(f"connection to the daemon failed: {exc}", exit_code=3,
                              code="unreachable", cause="daemon-refusing") from exc
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            if raw.strip():
                yield raw
    if buffer.strip():
        yield buffer


def open_stream(path: str, verb: str, args: Optional[Dict[str, Any]], contract: int) -> socket.socket:
    client = _connect(path, None)
    try:
        client.sendall(request_line(verb, args, contract))
    except OSError as exc:
        client.close()
        raise unreachable(path, exc) from exc
    return client


def watch_lines(path: str, session: str, interval: Optional[float]) -> Iterator[Dict[str, Any]]:
    """Yield each watch line as a dict. Raises after the stream unless exactly one ``end`` arrived."""
    wire_args: Dict[str, Any] = {"session": session}
    if interval is not None:
        wire_args["watch_interval"] = interval
    ends = 0
    client = open_stream(path, "watch", wire_args, 1)
    with client:
        for raw in lines(client):
            try:
                doc = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError as exc:
                raise _bad_answer(f"malformed watch line ({exc})") from exc
            if not isinstance(doc, dict):
                raise _bad_answer("a watch line that is not an object")
            if doc.get("ok") is False:
                raise refusal(doc, "watch")
            if doc.get("event") == "end":
                ends += 1
            yield doc
    if ends != 1:
        raise WireFailure("the watch stream closed without exactly one terminal end line",
                          exit_code=3, code="stream-truncated", cause="daemon-refusing")


def export_parts(path: str, session: str) -> Dict[str, bytes]:
    """Fetch and verify the stored parts of a finished session (PROTOCOL.md section 10.3)."""
    client = open_stream(path, "export", {"session": session}, 2)
    parts: Dict[str, bytearray] = {}
    header: Optional[Dict[str, Any]] = None
    ended = False
    with client:
        for raw in lines(client):
            try:
                doc = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError as exc:
                raise _bad_answer(f"malformed export line ({exc})") from exc
            if not isinstance(doc, dict):
                raise _bad_answer("an export line that is not an object")
            if header is None:
                if doc.get("ok") is not True:
                    raise refusal(doc, "export") if doc.get("ok") is False else _bad_answer("export header missing")
                if not isinstance(doc.get("parts"), list):
                    raise _bad_answer("export header has no parts list")
                header = doc
                parts = {str(p.get("name")): bytearray() for p in doc["parts"] if isinstance(p, dict)}
                continue
            if doc.get("event") == "end":
                if doc.get("status") != "complete":
                    error = doc.get("error") if isinstance(doc.get("error"), dict) else {}
                    raise WireFailure(
                        f"export-failed: {error.get('message', 'the daemon aborted the export')}",
                        exit_code=1, code="export-failed")
                ended = True
                break
            name = doc.get("part")
            if name not in parts or not isinstance(doc.get("data"), str):
                raise _bad_answer("a data line for a part the header did not announce")
            try:
                parts[name].extend(base64.b64decode(doc["data"], validate=True))
            except ValueError as exc:
                raise _bad_answer(f"bad base64 in part {name}") from exc
    if header is None or not ended:
        raise WireFailure("the export stream closed before its end line", exit_code=3,
                          code="stream-truncated", cause="daemon-refusing")
    out: Dict[str, bytes] = {}
    for entry in header["parts"]:
        name = str(entry.get("name"))
        body = bytes(parts[name])
        if len(body) != entry.get("bytes") or hashlib.sha256(body).hexdigest() != entry.get("sha256"):
            raise WireFailure(f"export of part {name} failed verification (size or sha256 mismatch)",
                              exit_code=1, code="export-failed")
        out[name] = body
    return out
