"""MCP-level deterministic tests for the P58 stdio frontend."""

from __future__ import annotations

import asyncio
import builtins
import json
import subprocess
import sys
from pathlib import Path

import pytest

# topos[mcp] is an optional extra: a base install must still collect and run the
# suite. Without this guard the missing SDK is a collection error that aborts the
# whole run rather than skipping this module.
pytest.importorskip("mcp", reason="topos[mcp] extra not installed")

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from topos.daemon.api import DEFAULT_MAX_RESPONSE_BYTES, Sensitivity, metric_sensitivity
from topos.daemon.redaction import classify_metric
from topos.daemon.client import (
    DaemonConnectError,
    DaemonCurrentResult,
    DaemonEntityResult,
    DaemonHello,
    DaemonHistoryResult,
    DaemonResponseError,
)
from topos.daemon.component_health import ComponentSnapshot, ComponentState, HealthSnapshot
from topos.mcp.server import MAX_HISTORY_LIMIT, MAX_OVERVIEW_LIMIT, McpServer
from topos.model import DockerMeta, Entity, EntityFrame, EntityKey, Frame, MetricValue
from topos.registry import REGISTRY


DOCKER_KEY = "system.slice/docker-" + ("a" * 64) + ".scope"
SERVICE_KEY = "system.slice/worker.service"


class FakeClient:
    def __init__(self, *, oversized_entity: bool = False) -> None:
        docker = Entity(
            key=DOCKER_KEY,
            kind="scope",
            parent="system.slice",
            docker=DockerMeta(
                cid="a" * 12,
                full_id="a" * 64,
                name="api-server",
                image="example/api",
            ),
        )
        service = Entity(key=SERVICE_KEY, kind="service", parent="system.slice")
        self.frame = Frame(
            schema_version=1,
            ts=1_000.0,
            interval_s=10.0,
            host={},
            entities={
                DOCKER_KEY: EntityFrame(
                    entity=docker,
                    metrics={
                        "ram": MetricValue(v=2_000.0, src="exact"),
                        "psi_mem_full_avg10": MetricValue(v=15.0, src="exact"),
                        "rf_z_per_s": MetricValue(v=5.0, src="derived"),
                        "cgroup_procs": MetricValue(v=3, src="exact"),
                    },
                ),
                SERVICE_KEY: EntityFrame(
                    entity=service,
                    metrics={
                        "ram": MetricValue(v=500.0, src="exact"),
                        "psi_mem_full_avg10": MetricValue(v=85.0, src="exact"),
                        "rf_z_per_s": MetricValue(v=10.0, src="derived"),
                    },
                ),
            },
        )
        self.oversized_entity = oversized_entity
        self.health = HealthSnapshot(
            snapshots=(
                ComponentSnapshot(
                    name="collector",
                    state=ComponentState.HEALTHY,
                    detail="running",
                    consecutive_failures=0,
                    state_change_count=1,
                ),
            )
        )

    @property
    def metrics_meta(self) -> dict[str, dict[str, object]]:
        return {
            "ram": {"sensitivity": "operational", "unit": "bytes"},
            "psi_mem_full_avg10": {"sensitivity": "operational", "unit": "%"},
            "rf_z_per_s": {"sensitivity": "operational", "unit": "/s"},
            "cgroup_procs": {"sensitivity": "sensitive", "unit": "count"},
        }

    def request_hello(self) -> DaemonHello:
        return DaemonHello((1,), ("hello", "current", "history", "entity", "health"), {}, {})

    def request_health(self) -> HealthSnapshot:
        return self.health

    def request_current(self) -> DaemonCurrentResult:
        return DaemonCurrentResult(seq=12, frame=self.frame, metrics_meta=self.metrics_meta)

    def request_entity(self, key: str) -> DaemonEntityResult:
        row = self.frame.entities.get(EntityKey(key))
        if row is None:
            raise DaemonResponseError("not found", code="not_found")
        if self.oversized_entity:
            row = EntityFrame(
                entity=row.entity,
                metrics=row.metrics,
                findings=[
                    type("Finding", (), {
                        "rule_id": "huge",
                        "severity": "warn",
                        "message": "x" * (DEFAULT_MAX_RESPONSE_BYTES + 1),
                        "source_metrics": (),
                    })()
                ],
            )
        return DaemonEntityResult(seq=12, entity=row, metrics_meta=self.metrics_meta)

    def request_history(
        self, *, limit: int, since_ts: float | None = None, until_ts: float | None = None
    ) -> DaemonHistoryResult:
        entries: list[tuple[int, Frame]] = []
        for index in range(3):
            copied_entities: dict[EntityKey, EntityFrame] = {}
            for key, row in self.frame.entities.items():
                copied_metrics = dict(row.metrics)
                copied_metrics["ram"] = MetricValue(
                    v=float(copied_metrics["ram"].v or 0) + index, src="exact"
                )
                copied_entities[key] = EntityFrame(entity=row.entity, metrics=copied_metrics)
            entries.append(
                (
                    index,
                    Frame(1, 1_000.0 + index, 1.0, {}, copied_entities),
                )
            )
        return DaemonHistoryResult(
            entries=tuple(entries[:limit]),
            oldest_seq=0,
            latest_seq=2,
            next_cursor=2,
            gap=False,
            metrics_meta=self.metrics_meta,
        )


async def _mcp_call(
    server: McpServer, name: str, arguments: dict[str, object]
) -> tuple[set[str], dict[str, object]]:
    app = server.build_mcp_server(FastMCP)
    async with create_connected_server_and_client_session(app) as session:
        tools = await session.list_tools()
        result = await session.call_tool(name, arguments)
    assert result.isError is False
    assert result.content
    return {tool.name for tool in tools.tools}, json.loads(result.content[0].text)


def call(server: McpServer, name: str, **arguments: object) -> tuple[set[str], dict[str, object]]:
    return asyncio.run(_mcp_call(server, name, arguments))


async def _discovered_descriptions(server: McpServer) -> dict[str, str]:
    app = server.build_mcp_server(FastMCP)
    async with create_connected_server_and_client_session(app) as session:
        tools = await session.list_tools()
    return {tool.name: tool.description or "" for tool in tools.tools}


def error_code(result: dict[str, object]) -> str:
    return result["error"]["code"]  # type: ignore[index]


def test_discovery_and_happy_paths_are_real_mcp_calls() -> None:
    server = McpServer(FakeClient())
    names, health = call(server, "topos_health")
    assert names == {"topos_health", "topos_overview", "topos_entity", "topos_history"}
    assert health["data"]["components"][0]["name"] == "collector"  # type: ignore[index]

    _, overview = call(server, "topos_overview", sort_by="psi_mem_full", limit=2)
    rows = overview["data"]["rows"]  # type: ignore[index]
    assert [row["key"] for row in rows] == [SERVICE_KEY, DOCKER_KEY]
    assert rows[0]["value"] == 85.0

    _, entity = call(server, "topos_entity", selector="api-server")
    assert entity["data"]["key"] == DOCKER_KEY  # type: ignore[index]
    assert entity["data"]["metrics"]["ram"]["value"] == 2_000.0  # type: ignore[index]

    _, history = call(server, "topos_history", selector="api", metric="ram", window="last:30", limit=3)
    assert history["data"]["entity_key"] == DOCKER_KEY  # type: ignore[index]
    assert history["data"]["series"] == [[1000.0, 2000.0], [1001.0, 2001.0], [1002.0, 2002.0]]  # type: ignore[index]


def test_discovered_descriptions_state_only_enforced_contracts() -> None:
    descriptions = asyncio.run(_discovered_descriptions(McpServer(FakeClient())))
    assert "at most 16 components" in descriptions["topos_health"]
    assert "limit is 1..50 rows" in descriptions["topos_overview"]
    assert "P57 docker name/prefix" in descriptions["topos_entity"]
    assert "at most 128 metrics, 64 findings" in descriptions["topos_entity"]
    assert "P57 docker name/prefix" in descriptions["topos_history"]
    assert "limit is 1..100 points" in descriptions["topos_history"]
    assert all("4 MiB" in description for description in descriptions.values())
    assert all("1000" not in description for description in descriptions.values())


def test_classification_fails_closed_without_metadata_but_trusts_it_when_present() -> None:
    # P81: absent metadata means "unclassified", which fails closed to sensitive
    # in the shared enforcement point (oracle #4), for every registry metric.
    assert all(classify_metric(name, {}) is Sensitivity.SENSITIVE for name in REGISTRY)
    # When the daemon supplies metadata, the enforcement point trusts it verbatim,
    # so a public/operational metric is not over-redacted.
    meta = {name: {"sensitivity": metric_sensitivity(name).value} for name in REGISTRY}
    assert all(classify_metric(name, meta) is metric_sensitivity(name) for name in REGISTRY)


def test_overview_validation_is_typed_at_the_mcp_boundary() -> None:
    server = McpServer(FakeClient())
    for arguments in (
        {"sort_by": "nope", "limit": 1},
        {"sort_by": "ram", "limit": 0},
        {"sort_by": "ram", "limit": -1},
        {"sort_by": "ram", "limit": MAX_OVERVIEW_LIMIT + 1},
        {"sort_by": "ram", "limit": True},
    ):
        _, result = call(server, "topos_overview", **arguments)
        assert error_code(result) in {"invalid-selector", "over-limit"}


def test_history_limit_and_window_validation_are_typed_at_the_mcp_boundary() -> None:
    server = McpServer(FakeClient())
    for arguments in (
        {"selector": DOCKER_KEY, "metric": "ram", "window": "bad", "limit": 1},
        {"selector": DOCKER_KEY, "metric": "ram", "window": "last:0", "limit": 1},
        {"selector": DOCKER_KEY, "metric": "ram", "window": "last:30", "limit": 0},
        {"selector": DOCKER_KEY, "metric": "ram", "window": "last:30", "limit": MAX_HISTORY_LIMIT + 1},
        {"selector": DOCKER_KEY, "metric": "ram", "window": "last:30", "limit": True},
    ):
        _, result = call(server, "topos_history", **arguments)
        assert error_code(result) in {"invalid-selector", "over-limit"}


def test_redaction_uses_sensitivity_metadata_without_changing_rank_order() -> None:
    server = McpServer(FakeClient(), redact_above=Sensitivity.PUBLIC)
    _, overview = call(server, "topos_overview", sort_by="ram", limit=2)
    rows = overview["data"]["rows"]  # type: ignore[index]
    assert [row["key"] for row in rows] == [DOCKER_KEY, SERVICE_KEY]
    # P81: one marker dialect -- the gateway's typed object, not the old
    # "__redacted__" string. ram is operational, redacted at a public ceiling.
    assert all(row["value"] == {"redacted": True, "sensitivity": "operational"} for row in rows)

    _, entity = call(server, "topos_entity", selector=DOCKER_KEY)
    metrics = entity["data"]["metrics"]  # type: ignore[index]
    assert metrics["ram"]["value"] == {"redacted": True, "sensitivity": "operational"}
    assert metrics["cgroup_procs"]["value"] == {"redacted": True, "sensitivity": "sensitive"}
    assert metrics["cgroup_procs"]["sensitivity"] == "sensitive"


def test_response_cap_is_enforced_by_an_observable_tool_call() -> None:
    _, result = call(McpServer(FakeClient(oversized_entity=True)), "topos_entity", selector=DOCKER_KEY)
    assert error_code(result) == "over-limit"
    assert "4 MiB" in result["error"]["message"]  # type: ignore[index]


def test_maximal_overview_fixture_is_observably_under_the_cap() -> None:
    client = FakeClient()
    for index in range(MAX_OVERVIEW_LIMIT):
        key = f"system.slice/service-{index}.service"
        client.frame.entities[key] = EntityFrame(
            entity=Entity(key=key, kind="service", parent="system.slice"),
            metrics={"ram": MetricValue(v=float(index), src="exact")},
        )
    _, result = call(McpServer(client), "topos_overview", sort_by="ram", limit=MAX_OVERVIEW_LIMIT)
    assert result["ok"] is True
    assert len(json.dumps(result, indent=2).encode()) < DEFAULT_MAX_RESPONSE_BYTES


class LeakingClient(FakeClient):
    def request_health(self) -> HealthSnapshot:
        raise DaemonResponseError("TOKEN=topsecret /private/path", code="unavailable")


def test_adapter_secrets_never_cross_the_mcp_boundary() -> None:
    _, result = call(McpServer(LeakingClient()), "topos_health")
    payload = json.dumps(result)
    assert error_code(result) == "daemon-unavailable"
    assert "TOKEN" not in payload
    assert "/private/path" not in payload
    assert "topsecret" not in payload


class LossyClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def request_health(self) -> HealthSnapshot:
        self.calls += 1
        if self.calls > 1:
            raise DaemonConnectError("daemon disappeared")
        return self.health


async def _loss_mid_session() -> tuple[dict[str, object], dict[str, object]]:
    app = McpServer(LossyClient()).build_mcp_server(FastMCP)
    async with create_connected_server_and_client_session(app) as session:
        first = await session.call_tool("topos_health", {})
        second = await session.call_tool("topos_health", {})
    return json.loads(first.content[0].text), json.loads(second.content[0].text)


def test_daemon_loss_mid_session_is_a_typed_mcp_result() -> None:
    first, second = asyncio.run(_loss_mid_session())
    assert first["ok"] is True
    assert error_code(second) == "daemon-unavailable"


def test_run_probes_at_startup_and_signal_seam_is_used(monkeypatch) -> None:
    registered: list[object] = []

    class FakeApp:
        def run(self, transport: str) -> None:
            assert transport == "stdio"

    server = McpServer(FakeClient(), register_signals=lambda event: registered.append(event))
    monkeypatch.setattr(server, "build_mcp_server", lambda _: FakeApp())
    assert server.run() == 0
    assert len(registered) == 1

    class Down(FakeClient):
        def request_hello(self) -> DaemonHello:
            raise DaemonConnectError("not available")

    assert McpServer(Down()).run() == 1


def test_missing_optional_sdk_exits_two_with_install_hint(monkeypatch, capsys) -> None:
    real_import = builtins.__import__

    def missing_mcp(name: str, *args: object, **kwargs: object) -> object:
        if name == "mcp.server.fastmcp":
            raise ModuleNotFoundError("No module named 'mcp'", name="mcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_mcp)
    assert McpServer(FakeClient()).run() == 2
    assert "pip install 'topos[mcp]'" in capsys.readouterr().err

    # ``-S`` proves the actual CLI path when the optional site package is absent.
    proc = subprocess.run(
        [sys.executable, "-S", "-m", "topos.cli", "mcp", "serve"],
        env={"PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "topos[mcp]" in proc.stderr


def test_non_mcp_import_path_does_not_import_the_optional_sdk() -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; import topos.cli; assert not any(n == 'mcp' or n.startswith('mcp.') for n in sys.modules)",
    ]
    proc = subprocess.run(command, env={"PYTHONPATH": str(Path(__file__).parents[1] / "src")}, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_history_rejects_a_metric_that_is_not_in_the_registry() -> None:
    """The tool description promises a registry metric; an unknown name must be refused."""
    server = McpServer(FakeClient())
    _, result = call(
        server, "topos_history", selector=DOCKER_KEY, metric="ram_typo", window="last:30", limit=10
    )
    assert error_code(result) == "invalid-selector"
    # The valid name still resolves, so the check gates on the registry, not on the fixture.
    _, ok = call(
        server, "topos_history", selector=DOCKER_KEY, metric="ram", window="last:30", limit=10
    )
    assert ok["ok"] is True


class EmptyHistoryClient(FakeClient):
    def request_history(
        self, *, limit: int, since_ts: float | None = None, until_ts: float | None = None
    ) -> DaemonHistoryResult:
        return DaemonHistoryResult(
            entries=(),
            oldest_seq=None,
            latest_seq=None,
            next_cursor=None,
            gap=False,
            metrics_meta=self.metrics_meta,
        )


def test_empty_history_window_is_an_empty_series_not_an_invalid_selector() -> None:
    """A live entity with no frames in the window has no data; it is not a bad selector."""
    server = McpServer(EmptyHistoryClient())
    _, result = call(
        server, "topos_history", selector=DOCKER_KEY, metric="ram", window="last:30", limit=10
    )
    assert result["ok"] is True
    assert result["data"]["series"] == []  # type: ignore[index]
    assert result["data"]["count"] == 0  # type: ignore[index]

    # A selector that names nothing is still refused, so the empty window did not
    # turn the tool into a rubber stamp.
    _, bogus = call(
        server, "topos_history", selector="no-such-entity", metric="ram", window="last:30", limit=10
    )
    assert error_code(bogus) == "invalid-selector"


def test_exact_key_entity_lookup_costs_one_daemon_request() -> None:
    """An exact-key hit must be returned, not thrown away and refetched."""

    class CountingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.entity_calls = 0

        def request_entity(self, key: str) -> DaemonEntityResult:
            self.entity_calls += 1
            return super().request_entity(key)

    client = CountingClient()
    _, result = call(McpServer(client), "topos_entity", selector=DOCKER_KEY)
    assert result["ok"] is True
    assert client.entity_calls == 1
