"""Remaining deterministic MCP server boundary contracts.

These tests exercise only in-process seams: no MCP SDK session, daemon socket,
or real process signal is involved.
"""

from __future__ import annotations

import builtins
import signal
import threading
from pathlib import Path

import pytest

from topos.daemon.client import (
    DaemonConnectError,
    DaemonCurrentResult,
    DaemonEntityResult,
    DaemonHistoryResult,
    DaemonResponseError,
)
from topos.mcp import server as server_module
from topos.mcp.server import McpServer, install_signal_handlers, run_server
from topos.model import Entity, EntityFrame, EntityKey, Frame, MetricValue


ENTITY_KEY = "system.slice/api.service"
METRICS_META = {"ram": {"sensitivity": "operational", "unit": "bytes"}}


def _frame() -> Frame:
    entity = Entity(key=ENTITY_KEY, kind="service", parent="system.slice")
    return Frame(
        schema_version=1,
        ts=100.0,
        interval_s=1.0,
        host={},
        entities={
            EntityKey(ENTITY_KEY): EntityFrame(
                entity=entity, metrics={"ram": MetricValue(v=12.0, src="exact")}
            )
        },
    )


class Client:
    def __init__(self) -> None:
        self.current: object = DaemonCurrentResult(seq=1, frame=_frame(), metrics_meta=METRICS_META)
        self.entity: object = DaemonEntityResult(
            seq=1, entity=_frame().entities[EntityKey(ENTITY_KEY)], metrics_meta=METRICS_META
        )
        self.history: object = DaemonHistoryResult(
            entries=(), oldest_seq=None, latest_seq=None, next_cursor=None, gap=False, metrics_meta=METRICS_META
        )

    def request_hello(self) -> object:
        return object()

    def request_current(self) -> object:
        if isinstance(self.current, Exception):
            raise self.current
        return self.current

    def request_entity(self, _key: str) -> object:
        if isinstance(self.entity, Exception):
            raise self.entity
        return self.entity

    def request_history(
        self, *, limit: int, since_ts: float | None = None, until_ts: float | None = None
    ) -> object:
        if isinstance(self.history, Exception):
            raise self.history
        return self.history


def _code(result: dict[str, object]) -> str:
    return result["error"]["code"]  # type: ignore[index]


def test_signal_handlers_interrupt_once_then_use_the_injected_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers: dict[signal.Signals, object] = {}
    exits: list[int] = []

    class ExitRequested(Exception):
        pass

    monkeypatch.setattr(server_module.signal, "signal", lambda signum, handler: handlers.__setitem__(signum, handler))

    def fake_exit(status: int) -> None:
        exits.append(status)
        raise ExitRequested

    monkeypatch.setattr(server_module.os, "_exit", fake_exit)
    stop_event = threading.Event()
    install_signal_handlers(stop_event)

    assert set(handlers) == {signal.SIGINT, signal.SIGTERM}
    with pytest.raises(KeyboardInterrupt):
        handlers[signal.SIGINT](signal.SIGINT, None)  # type: ignore[operator]
    assert stop_event.is_set()
    with pytest.raises(ExitRequested):
        handlers[signal.SIGTERM](signal.SIGTERM, None)  # type: ignore[operator]
    assert exits == [1]


def test_run_reraises_an_unrelated_optional_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def broken_fastmcp_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "mcp.server.fastmcp":
            raise ModuleNotFoundError("No module named 'unrelated'", name="unrelated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_fastmcp_import)

    with pytest.raises(ModuleNotFoundError, match="unrelated"):
        McpServer(Client()).run()


def test_run_treats_a_clean_server_interrupt_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[threading.Event] = []

    class InterruptedApp:
        def run(self, transport: str) -> None:
            assert transport == "stdio"
            raise KeyboardInterrupt

    server = McpServer(Client(), register_signals=registered.append)
    monkeypatch.setattr(server, "build_mcp_server", lambda _: InterruptedApp())

    assert server.run() == 0
    assert len(registered) == 1


def test_daemon_error_results_are_returned_without_shape_or_selector_masking() -> None:
    client = Client()
    client.current = DaemonConnectError("offline")
    server = McpServer(client)

    assert _code(server._handle_overview("ram", 1)) == "daemon-unavailable"
    assert _code(server._resolve_docker_selector("api")) == "daemon-unavailable"

    client.entity = DaemonResponseError("missing", code="not_found")
    assert _code(server._handle_entity("api")) == "daemon-unavailable"

    client.history = DaemonConnectError("offline")
    assert _code(server._handle_history(ENTITY_KEY, "ram", "last:1", 1)) == "daemon-unavailable"


def test_invalid_daemon_shapes_are_safe_and_metrics_without_units_stay_unitless() -> None:
    client = Client()
    server = McpServer(client)

    client.current = object()
    assert _code(server._resolve_docker_selector("api")) == "internal"

    client.entity = object()
    assert _code(server._handle_entity(ENTITY_KEY)) == "internal"

    client.entity = DaemonEntityResult(
        seq=1,
        entity=_frame().entities[EntityKey(ENTITY_KEY)],
        metrics_meta={"ram": {"sensitivity": "operational"}},
    )
    result = server._handle_entity(ENTITY_KEY)
    assert result["data"]["metrics"]["ram"] == {"value": 12.0, "sensitivity": "operational"}  # type: ignore[index]


def test_run_server_wires_the_client_and_signal_seam_without_opening_a_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object()
    captured: dict[str, object] = {}

    class FakeServer:
        def __init__(self, received_client: object, **kwargs: object) -> None:
            captured["client"] = received_client
            captured.update(kwargs)

        def run(self) -> int:
            return 23

    register_signals = lambda event: None
    def fake_client(socket_path: Path) -> object:
        captured["socket"] = socket_path
        return client

    monkeypatch.setattr(server_module, "DaemonClient", fake_client)
    monkeypatch.setattr(server_module, "McpServer", FakeServer)

    assert run_server(Path("/tmp/topos.sock"), redact_above=None, register_signals=register_signals) == 23
    assert captured == {
        "socket": Path("/tmp/topos.sock"),
        "client": client,
        "redact_above": None,
        "register_signals": register_signals,
    }
