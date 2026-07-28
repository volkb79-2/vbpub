"""Direct contracts for MCP handler validation and bounded result shaping."""

from __future__ import annotations

from topos.daemon.client import (
    DaemonCurrentResult,
    DaemonEntityResult,
    DaemonHistoryResult,
    DaemonResponseError,
)
from topos.daemon.component_health import ComponentSnapshot, ComponentState, HealthSnapshot
from topos.mcp.server import MAX_ENTITY_METRICS, MAX_HEALTH_COMPONENTS, McpServer
from topos.model import DockerMeta, Entity, EntityFrame, EntityKey, Finding, Frame, MetricValue


ENTITY_KEY = "system.slice/api.service"
METRICS_META = {"ram": {"sensitivity": "operational", "unit": "bytes"}}


def _frame(*, ram: float | None = 12.0, extra_unavailable: bool = False) -> Frame:
    metrics = {"ram": MetricValue(v=ram, src="exact")}
    if extra_unavailable:
        metrics["psi_mem_full_avg10"] = MetricValue(v=None, src="unavail_kernel")
    entity = Entity(key=ENTITY_KEY, kind="service", parent="system.slice", tier="important")
    return Frame(
        schema_version=1,
        ts=100.0,
        interval_s=1.0,
        host={},
        entities={EntityKey(ENTITY_KEY): EntityFrame(entity=entity, metrics=metrics)},
    )


class ValidationClient:
    def __init__(self) -> None:
        self.current_calls = 0
        self.entity_calls = 0
        self.history_calls = 0
        self.current: object = DaemonCurrentResult(seq=1, frame=_frame(), metrics_meta=METRICS_META)
        self.entity: object = DaemonEntityResult(
            seq=1,
            entity=_frame(extra_unavailable=True).entities[EntityKey(ENTITY_KEY)],
            metrics_meta=METRICS_META,
        )
        self.history: object = DaemonHistoryResult(
            entries=(), oldest_seq=None, latest_seq=None, next_cursor=None, gap=False, metrics_meta=METRICS_META
        )

    def request_health(self) -> object:
        return HealthSnapshot()

    def request_current(self) -> object:
        self.current_calls += 1
        return self.current

    def request_entity(self, _key: str) -> object:
        self.entity_calls += 1
        return self.entity

    def request_history(
        self, *, limit: int, since_ts: float | None = None, until_ts: float | None = None
    ) -> object:
        self.history_calls += 1
        return self.history


def _code(result: dict[str, object]) -> str:
    return result["error"]["code"]  # type: ignore[index]


def test_health_rejects_invalid_and_oversized_typed_summaries() -> None:
    client = ValidationClient()
    server = McpServer(client)

    client.request_health = lambda: object()  # type: ignore[method-assign]
    assert _code(server._handle_health()) == "internal"

    snapshots = tuple(
        ComponentSnapshot(name=f"component-{index}", state=ComponentState.HEALTHY)
        for index in range(MAX_HEALTH_COMPONENTS + 1)
    )
    client.request_health = lambda: HealthSnapshot(snapshots=snapshots)  # type: ignore[method-assign]
    assert _code(server._handle_health()) == "over-limit"


def test_overview_omits_unavailable_metric_rows_without_losing_available_rows() -> None:
    client = ValidationClient()
    unavailable = Entity(key="system.slice/empty.service", kind="service", parent="system.slice")
    frame = _frame()
    frame.entities[EntityKey(unavailable.key)] = EntityFrame(
        entity=unavailable, metrics={"ram": MetricValue(v=None, src="unavail_kernel")}
    )
    client.current = DaemonCurrentResult(seq=1, frame=frame, metrics_meta=METRICS_META)

    result = McpServer(client)._handle_overview("ram", 50)

    assert result["data"]["rows"] == [  # type: ignore[index]
        {"key": ENTITY_KEY, "metric": "ram", "value": 12.0, "sensitivity": "operational"}
    ]


def test_entity_validation_rejects_bad_selectors_and_omits_unavailable_metrics() -> None:
    client = ValidationClient()
    server = McpServer(client)

    assert _code(server._handle_entity("")) == "invalid-selector"
    assert _code(server._handle_entity(3)) == "invalid-selector"  # type: ignore[arg-type]
    assert client.entity_calls == 0

    result = server._handle_entity(ENTITY_KEY)
    assert result["data"]["metrics"] == {  # type: ignore[index]
        "ram": {"value": 12.0, "sensitivity": "operational", "unit": "bytes"}
    }


def test_entity_rejects_metric_item_cap_before_rendering() -> None:
    client = ValidationClient()
    entity = Entity(key=ENTITY_KEY, kind="service", parent="system.slice")
    client.entity = DaemonEntityResult(
        seq=1,
        entity=EntityFrame(
            entity=entity,
            metrics={f"metric-{index}": MetricValue(v=1, src="exact") for index in range(MAX_ENTITY_METRICS + 1)},
            findings=[Finding("unused", "info", "never rendered")],
        ),
        metrics_meta={},
    )

    assert _code(McpServer(client)._handle_entity(ENTITY_KEY)) == "over-limit"


def test_entity_docker_selector_resolves_once_then_retries_the_canonical_key() -> None:
    client = ValidationClient()
    docker_key = "system.slice/docker-" + ("a" * 64) + ".scope"
    entity = Entity(
        key=docker_key,
        kind="scope",
        parent="system.slice",
        docker=DockerMeta(cid="a" * 12, full_id="a" * 64, name="api-server", image="example/api"),
    )
    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=1.0,
        host={},
        entities={EntityKey(docker_key): EntityFrame(entity=entity, metrics={"ram": MetricValue(v=12.0, src="exact")})},
    )
    client.current = DaemonCurrentResult(seq=1, frame=frame, metrics_meta=METRICS_META)
    client.entity = DaemonEntityResult(
        seq=1, entity=frame.entities[EntityKey(docker_key)], metrics_meta=METRICS_META
    )
    requested: list[str] = []

    def request_entity(key: str) -> object:
        requested.append(key)
        if key == "api-server":
            raise DaemonResponseError("not found", code="not_found")
        return client.entity

    client.request_entity = request_entity  # type: ignore[method-assign]
    result = McpServer(client)._handle_entity("api-server")

    assert result["data"]["key"] == docker_key  # type: ignore[index]
    assert requested == ["api-server", docker_key]
    assert client.current_calls == 1


def test_history_rejects_non_string_selector_and_invalid_live_resolution() -> None:
    client = ValidationClient()
    server = McpServer(client)

    assert _code(server._handle_history(3, "ram", "last:1", 1)) == "invalid-selector"  # type: ignore[arg-type]
    assert client.current_calls == 0

    client.current = object()
    assert _code(server._handle_history(ENTITY_KEY, "ram", "last:1", 1)) == "internal"
    assert client.history_calls == 2
    assert client.current_calls == 1
