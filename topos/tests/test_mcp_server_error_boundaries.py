"""Direct, deterministic boundary tests for MCP daemon-result handling."""

from __future__ import annotations

from topos.daemon.client import (
    DaemonConnectError,
    DaemonCurrentResult,
    DaemonHistoryResult,
    DaemonProtocolError,
    DaemonResponseError,
)
from topos.mcp.server import McpServer
from topos.model import Entity, EntityFrame, EntityKey, Frame, MetricValue


ENTITY_KEY = "system.slice/api.service"
METRICS_META = {"ram": {"sensitivity": "operational", "unit": "bytes"}}


def _frame(ts: float, *, include_entity: bool = True, ram: float | None = 10.0) -> Frame:
    entities: dict[EntityKey, EntityFrame] = {}
    if include_entity:
        entities[EntityKey(ENTITY_KEY)] = EntityFrame(
            entity=Entity(key=ENTITY_KEY, kind="service", parent="system.slice"),
            metrics={"ram": MetricValue(v=ram, src="exact")},
        )
    return Frame(schema_version=1, ts=ts, interval_s=1.0, host={}, entities=entities)


class BoundaryClient:
    def __init__(self) -> None:
        self.current_calls = 0
        self.history_calls = 0
        self.entity_calls = 0
        self.current_result: object = DaemonCurrentResult(
            seq=1, frame=_frame(100.0), metrics_meta=METRICS_META
        )
        self.history_result: object = DaemonHistoryResult(
            entries=(),
            oldest_seq=None,
            latest_seq=None,
            next_cursor=None,
            gap=False,
            metrics_meta=METRICS_META,
        )
        self.entity_failure: Exception | None = None

    def request_current(self) -> object:
        self.current_calls += 1
        if isinstance(self.current_result, Exception):
            raise self.current_result
        return self.current_result

    def request_history(
        self, *, limit: int, since_ts: float | None = None, until_ts: float | None = None
    ) -> object:
        self.history_calls += 1
        if isinstance(self.history_result, Exception):
            raise self.history_result
        return self.history_result

    def request_entity(self, key: str) -> object:
        self.entity_calls += 1
        if self.entity_failure is not None:
            raise self.entity_failure
        raise DaemonResponseError("missing", code="not_found")


def _code(result: dict[str, object]) -> str:
    return result["error"]["code"]  # type: ignore[index]


def test_invalid_typed_daemon_results_are_safe_internal_errors() -> None:
    client = BoundaryClient()
    client.current_result = object()
    client.history_result = object()
    server = McpServer(client)

    overview = server._handle_overview("ram", 1)
    history = server._handle_history(ENTITY_KEY, "ram", "last:1", 1)

    assert _code(overview) == "internal"
    assert _code(history) == "internal"


def test_entity_protocol_failure_does_not_fall_back_to_live_selector_resolution() -> None:
    client = BoundaryClient()
    client.entity_failure = DaemonProtocolError("incompatible envelope")

    result = McpServer(client)._handle_entity("api")

    assert _code(result) == "daemon-unavailable"
    assert client.entity_calls == 1
    assert client.current_calls == 0


def test_empty_history_uses_live_frame_and_preserves_daemon_unavailability() -> None:
    client = BoundaryClient()
    server = McpServer(client)

    empty = server._handle_history(ENTITY_KEY, "ram", "last:1", 1)
    assert empty["data"]["series"] == []  # type: ignore[index]
    assert client.current_calls == 1

    client.current_result = DaemonConnectError("socket unavailable")
    unavailable = server._handle_history(ENTITY_KEY, "ram", "last:1", 1)
    assert _code(unavailable) == "daemon-unavailable"


def test_history_rejects_non_string_window_before_touching_the_daemon() -> None:
    client = BoundaryClient()

    result = McpServer(client)._handle_history(ENTITY_KEY, "ram", 5, 1)  # type: ignore[arg-type]

    assert _code(result) == "invalid-selector"
    assert client.history_calls == 0


def test_history_skips_absent_entities_and_unavailable_metric_samples() -> None:
    client = BoundaryClient()
    client.history_result = DaemonHistoryResult(
        entries=(
            (1, _frame(1.0, include_entity=False)),
            (2, _frame(2.0, ram=None)),
            (3, _frame(3.0, ram=7.5)),
        ),
        oldest_seq=1,
        latest_seq=3,
        next_cursor=None,
        gap=False,
        metrics_meta=METRICS_META,
    )

    result = McpServer(client)._handle_history(ENTITY_KEY, "ram", "since:0", 3)

    assert result["data"]["series"] == [[3.0, 7.5]]  # type: ignore[index]
    assert result["data"]["count"] == 1  # type: ignore[index]
