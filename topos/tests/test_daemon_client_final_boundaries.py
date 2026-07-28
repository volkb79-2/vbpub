"""Final transport-free boundaries for :mod:`topos.daemon.client`.

The transport is represented by a tiny in-memory socket double: these tests
exercise the client's timeout and typed-error contracts without opening a UNIX
socket server.  Parser inputs remain ordinary ``StringIO`` text streams.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

import topos.daemon.client as client_module
from topos.daemon.client import DaemonClient, DaemonConnectError


class _Socket:
    """Context-manager socket seam that fails if a no-timeout call sets one."""

    def __init__(self, response: str) -> None:
        self._response = response

    def __enter__(self) -> _Socket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, _timeout: float | None) -> None:
        raise AssertionError("timeout=None must not call settimeout")

    def connect(self, _address: str) -> None:
        return None

    def sendall(self, _payload: bytes) -> None:
        return None

    def shutdown(self, _how: int) -> None:
        return None

    def makefile(self, *_args: object, **_kwargs: object) -> io.StringIO:
        return io.StringIO(self._response)


class _FailingSocket(_Socket):
    def __init__(self) -> None:
        super().__init__("")

    def connect(self, _address: str) -> None:
        raise OSError("connection refused")


def _health_response() -> str:
    components = [
        {
            "name": name,
            "state": "healthy",
            "detail": "ready",
            "last_attempt_ts": None,
            "last_success_ts": None,
            "consecutive_failures": 0,
            "state_change_count": 0,
            "error": None,
        }
        for name in ("collector", "bpf_snapshot_bridge", "paddr_lifecycle")
    ]
    return json.dumps(
        {
            "type": "health",
            "schema_version": 1,
            "capability": "health-v1",
            "components": components,
        }
    ) + "\n"


def _install_socket(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
    monkeypatch.setattr(client_module.socket, "socket", lambda *_args: _Socket(response))


def test_no_timeout_transport_paths_do_not_set_a_socket_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All four public transports preserve explicit ``timeout_s=None``."""
    client = DaemonClient(Path("/fixture/daemon.sock"), timeout_s=None)

    _install_socket(monkeypatch, '{"type":"end","count":0,"gap":false}\n')
    assert client.stream_batch(limit=1).frames == ()

    _install_socket(monkeypatch, _health_response())
    assert len(client.request_health().snapshots) == 3

    _install_socket(monkeypatch, '{"type":"end","count":0}\n')
    assert client.request_frames({"op": "current"}) == []

    monkeypatch.setattr(client_module.uuid, "uuid4", lambda: type("Id", (), {"hex": "request-1"})())
    _install_socket(monkeypatch, '{"id":"request-1","ok":true,"result":{}}\n')
    assert client._request_envelope("hello") == {}


def test_stream_batch_maps_connect_oserror_to_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused transport is never leaked as a raw ``OSError``."""
    monkeypatch.setattr(client_module.socket, "socket", lambda *_args: _FailingSocket())
    client = DaemonClient(Path("/fixture/daemon.sock"), timeout_s=None)

    with pytest.raises(DaemonConnectError, match="cannot connect"):
        client.stream_batch()


def test_current_frame_stream_clamps_interval_and_yields_successive_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The convenience iterator has a floor and polls only between frames."""
    frames = iter(["first", "second"])
    sleeps: list[float] = []

    class _CurrentClient:
        def __init__(self, _path: Path) -> None:
            pass

        def current_frame(self) -> str:
            return next(frames)

    monkeypatch.setattr(client_module, "DaemonClient", _CurrentClient)
    monkeypatch.setattr(client_module.time, "sleep", sleeps.append)

    stream = client_module.current_frame_stream(Path("/fixture/daemon.sock"), poll_interval_s=-5)
    assert next(stream) == "first"
    assert next(stream) == "second"
    assert sleeps == [0.1]
    stream.close()
