"""The daemon hello field reflects the listener supplied at startup."""

from __future__ import annotations

import importlib
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest


class _Broker:
    def __init__(self, _source, *, history_size, **_kwargs):
        self.history_size = history_size

    def history_capacity(self):
        return self.history_size

    def start(self):
        pass

    def stop(self):
        pass

    def join(self, *, timeout):
        assert timeout == 5.0


class _Server:
    def serve_forever(self):
        pass

    def server_close(self):
        pass


@pytest.mark.parametrize(
    ("activated", "expected"),
    (
        (False, {"mode": "self-bind", "listeners": 0}),
        (True, {"mode": "activated", "listeners": 1}),
    ),
)
def test_run_daemon_passes_listener_activation_to_hello(tmp_path: Path, monkeypatch, activated, expected) -> None:
    daemon_run = importlib.import_module("topos.daemon.run")
    config = SimpleNamespace(
        history=SimpleNamespace(daemon=SimpleNamespace(enabled=False)),
        bpf_snapshot=SimpleNamespace(enabled=False, root=None),
        damon=SimpleNamespace(paddr_enabled=False),
    )
    collector = SimpleNamespace(cgroup_root=tmp_path / "cgroup", damon_root=tmp_path / "damon")
    monkeypatch.setattr(daemon_run, "load", lambda _path: config)
    monkeypatch.setattr(daemon_run, "Collector", lambda **_kwargs: collector)
    monkeypatch.setattr(daemon_run, "PersistentHistoryStore", lambda _config: object())
    monkeypatch.setattr(daemon_run, "live_frame_stream", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(daemon_run, "FrameBroker", _Broker)
    served = {}

    def serve(_path, _broker, api, **_kwargs):
        served["api"] = api
        return _Server()

    monkeypatch.setattr(daemon_run, "serve_versioned_unix_socket", serve)
    monkeypatch.setattr(daemon_run, "serve_versioned_listener", serve)

    listener = None
    if activated:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(tmp_path / "activated.sock"))
    try:
        result = daemon_run.run_daemon(
            tmp_path / "self.sock", listener=listener, socket_mode=0o660, history_size=12
        )
    finally:
        if listener is not None:
            listener.close()

    assert result == 0
    hello = served["api"]._op_hello()
    assert hello["socket_activation"] == expected
