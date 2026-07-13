"""MCP-level deterministic tests for the P58 stdio frontend."""

from __future__ import annotations

import asyncio
import builtins
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from groop.daemon.api import DEFAULT_MAX_RESPONSE_BYTES, Sensitivity
from groop.daemon.client import (
    DaemonConnectError,
    DaemonCurrentResult,
    DaemonEntityResult,
    DaemonHello,
    DaemonHistoryResult,
    DaemonResponseError,
)
from groop.daemon.component_health import ComponentSnapshot, ComponentState, HealthSnapshot
from groop.mcp.server import MAX_HISTORY_LIMIT, MAX_OVERVIEW_LIMIT, McpServer
from groop.model import DockerMeta, Entity, EntityFrame, EntityKey, Frame, MetricValue


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


def error_code(result: dict[str, object]) -> str:
    return result["error"]["code"]  # type: ignore[index]


def test_discovery_and_happy_paths_are_real_mcp_calls() -> None:
    server = McpServer(FakeClient())
    names, health = call(server, "groop_health")
    assert names == {"groop_health", "groop_overview", "groop_entity", "groop_history"}
    assert health["data"]["components"][0]["name"] == "collector"  # type: ignore[index]

    _, overview = call(server, "groop_overview", sort_by="psi_mem_full", limit=2)
    rows = overview["data"]["rows"]  # type: ignore[index]
    assert [row["key"] for row in rows] == [SERVICE_KEY, DOCKER_KEY]
    assert rows[0]["value"] == 85.0

    _, entity = call(server, "groop_entity", selector="api-server")
    assert entity["data"]["key"] == DOCKER_KEY  # type: ignore[index]
    assert entity["data"]["metrics"]["ram"]["value"] == 2_000.0  # type: ignore[index]

    _, history = call(server, "groop_history", selector="api", metric="ram", window="last:30", limit=3)
    assert history["data"]["entity_key"] == DOCKER_KEY  # type: ignore[index]
    assert history["data"]["series"] == [[1000.0, 2000.0], [1001.0, 2001.0], [1002.0, 2002.0]]  # type: ignore[index]


def test_overview_validation_is_typed_at_the_mcp_boundary() -> None:
    server = McpServer(FakeClient())
    for arguments in (
        {"sort_by": "nope", "limit": 1},
        {"sort_by": "ram", "limit": 0},
        {"sort_by": "ram", "limit": -1},
        {"sort_by": "ram", "limit": MAX_OVERVIEW_LIMIT + 1},
        {"sort_by": "ram", "limit": True},
    ):
        _, result = call(server, "groop_overview", **arguments)
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
        _, result = call(server, "groop_history", **arguments)
        assert error_code(result) in {"invalid-selector", "over-limit"}


def test_redaction_uses_sensitivity_metadata_without_changing_rank_order() -> None:
    server = McpServer(FakeClient(), redact_above=Sensitivity.PUBLIC)
    _, overview = call(server, "groop_overview", sort_by="ram", limit=2)
    rows = overview["data"]["rows"]  # type: ignore[index]
    assert [row["key"] for row in rows] == [DOCKER_KEY, SERVICE_KEY]
    assert all(row["value"] == "__redacted__" for row in rows)

    _, entity = call(server, "groop_entity", selector=DOCKER_KEY)
    metrics = entity["data"]["metrics"]  # type: ignore[index]
    assert metrics["ram"]["value"] == "__redacted__"
    assert metrics["cgroup_procs"]["sensitivity"] == "sensitive"


def test_response_cap_is_enforced_by_an_observable_tool_call() -> None:
    _, result = call(McpServer(FakeClient(oversized_entity=True)), "groop_entity", selector=DOCKER_KEY)
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
    _, result = call(McpServer(client), "groop_overview", sort_by="ram", limit=MAX_OVERVIEW_LIMIT)
    assert result["ok"] is True
    assert len(json.dumps(result, indent=2).encode()) < DEFAULT_MAX_RESPONSE_BYTES


class LeakingClient(FakeClient):
    def request_health(self) -> HealthSnapshot:
        raise DaemonResponseError("TOKEN=topsecret /private/path", code="unavailable")


def test_adapter_secrets_never_cross_the_mcp_boundary() -> None:
    _, result = call(McpServer(LeakingClient()), "groop_health")
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
        first = await session.call_tool("groop_health", {})
        second = await session.call_tool("groop_health", {})
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
    assert "pip install 'groop[mcp]'" in capsys.readouterr().err

    # ``-S`` proves the actual CLI path when the optional site package is absent.
    proc = subprocess.run(
        [sys.executable, "-S", "-m", "groop.cli", "mcp", "serve"],
        env={"PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "groop[mcp]" in proc.stderr


def test_non_mcp_import_path_does_not_import_the_optional_sdk() -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; import groop.cli; assert not any(n == 'mcp' or n.startswith('mcp.') for n in sys.modules)",
    ]
    proc = subprocess.run(command, env={"PYTHONPATH": str(Path(__file__).parents[1] / "src")}, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
