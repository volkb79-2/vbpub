"""Read-only MCP frontend for the typed daemon client.

This module deliberately contains no MCP SDK import.  The optional dependency is
loaded only by :meth:`McpServer.run`, after the CLI selected `mcp serve`.
"""

from __future__ import annotations

import json
import math
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from groop.collect.dockerjoin import ContainerResolveError, resolve_container_key
from groop.daemon.api import DEFAULT_MAX_RESPONSE_BYTES, Sensitivity, metric_sensitivity
from groop.daemon.client import (
    DaemonClient,
    DaemonClientError,
    DaemonConnectError,
    DaemonCurrentResult,
    DaemonEntityResult,
    DaemonHistoryResult,
    DaemonProtocolError,
    DaemonResponseError,
)
from groop.daemon.component_health import HealthSnapshot
from groop.daemon.deploy import DEFAULT_DAEMON_SOCKET
from groop.model import Entity, EntityFrame, EntityKey, MetricValue

DEFAULT_SOCKET_PATH = DEFAULT_DAEMON_SOCKET
MAX_RESPONSE_BYTES = DEFAULT_MAX_RESPONSE_BYTES
MAX_OVERVIEW_LIMIT = 50
MAX_HISTORY_LIMIT = 100
MAX_ENTITY_METRICS = 128
MAX_ENTITY_FINDINGS = 64
MAX_HEALTH_COMPONENTS = 16
MAX_WINDOW_SECONDS = 7 * 24 * 60 * 60

SORT_KEY_MAP: dict[str, str] = {
    "psi_mem_full": "psi_mem_full_avg10",
    "psi_io_full": "psi_io_full_avg10",
    "ram": "ram",
    "rf_z_per_s": "rf_z_per_s",
}
_REDACTED_MARKER = "__redacted__"


class DaemonReadClient(Protocol):
    def request_hello(self) -> object: ...
    def request_health(self) -> HealthSnapshot: ...
    def request_current(self) -> DaemonCurrentResult: ...
    def request_entity(self, key: str) -> DaemonEntityResult: ...
    def request_history(
        self, *, limit: int, since_ts: float | None = None, until_ts: float | None = None
    ) -> DaemonHistoryResult: ...


SignalRegistration = Callable[[threading.Event], None]


def install_signal_handlers(stop_event: threading.Event) -> None:
    """Install clean-interrupt handlers; a second signal terminates immediately."""

    def handler(_signum: int, _frame: object) -> None:
        if stop_event.is_set():
            os._exit(1)
        stop_event.set()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def _tool_error(code: str, message: str) -> dict[str, object]:
    """Produce the closed, public-safe tool-error shape."""
    return {"error": {"code": code, "message": message}}


def _ok(data: dict[str, object]) -> dict[str, object]:
    """Return a result only when its encoded MCP payload fits the byte cap."""
    payload: dict[str, object] = {"ok": True, "data": data}
    # FastMCP renders returned mappings as formatted JSON text, so measure the
    # formatted representation rather than a smaller private wire encoding.
    encoded = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        return _tool_error("over-limit", "response exceeds the aggregate 4 MiB byte cap")
    return payload


def _is_error(value: object) -> bool:
    return isinstance(value, dict) and "error" in value


def _sensitivity(meta: dict[str, object] | None, metric: str) -> Sensitivity:
    """Use daemon metadata when present; otherwise use the daemon's canonical classifier."""
    if meta is not None:
        raw = meta.get("sensitivity")
        try:
            return Sensitivity(raw)
        except (TypeError, ValueError):
            pass
    return metric_sensitivity(metric)


def _redact(value: object, sensitivity: Sensitivity, threshold: Sensitivity | None) -> object:
    if threshold is None:
        return value
    if tuple(Sensitivity).index(sensitivity) > tuple(Sensitivity).index(threshold):
        return _REDACTED_MARKER
    return value


class McpServer:
    """Four read-only MCP tools backed solely by P63's typed daemon client."""

    def __init__(
        self,
        client: DaemonReadClient,
        *,
        redact_above: Sensitivity | None = None,
        register_signals: SignalRegistration = install_signal_handlers,
    ) -> None:
        self._client = client
        self._redact_above = redact_above
        self._register_signals = register_signals
        self._stop_event = threading.Event()

    def run(self) -> int:
        """Probe the daemon, then serve stdio until EOF or a clean interrupt."""
        try:
            from mcp.server.fastmcp import FastMCP
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith("mcp"):
                print(
                    "groop mcp serve requires the groop[mcp] extra; install it with: pip install 'groop[mcp]'",
                    file=sys.stderr,
                )
                return 2
            raise

        startup = self._try_call(self._client.request_hello)
        if _is_error(startup):
            print("groop mcp serve could not reach a compatible daemon", file=sys.stderr)
            return 1

        self._register_signals(self._stop_event)
        try:
            self.build_mcp_server(FastMCP).run("stdio")
        except KeyboardInterrupt:
            pass
        return 0

    def build_mcp_server(self, fast_mcp: Any) -> Any:
        """Create the registered server; public for deterministic in-process tests."""
        mcp = fast_mcp(
            "groop",
            instructions="Read-only daemon metrics. No mutation or filesystem tools are exposed.",
        )

        @mcp.tool(
            name="groop_health",
            description=(
                "Return the daemon component-health summary using daemon health semantics; "
                "at most 16 components and a 4 MiB aggregate response cap."
            ),
        )
        def groop_health() -> dict[str, object]:
            return self._handle_health()

        @mcp.tool(
            name="groop_overview",
            description=(
                "Rank cgroup entities by one compact registry metric family: psi_mem_full/psi_io_full "
                "(PSI full avg10 percent), ram (bytes), or rf_z_per_s (/s); limit is 1..50 rows and "
                "the aggregate response cap is 4 MiB, with unavailable metrics omitted."
            ),
        )
        def groop_overview(sort_by: str = "ram", limit: object = 20) -> dict[str, object]:
            # Keep this uncoerced at the SDK boundary so Python's ``bool``-is-
            # ``int`` quirk reaches the explicit strict validator below.
            return self._handle_overview(sort_by, limit)

        @mcp.tool(
            name="groop_entity",
            description=(
                "Return one entity's registry-backed metric detail; selector is an exact EntityKey or a "
                "P57 docker name/prefix, with at most 128 metrics, 64 findings, and a 4 MiB aggregate cap; "
                "metric units/sensitivity come from P52 registry metadata, unavailable values are omitted, and "
                "redacted values are marked."
            ),
        )
        def groop_entity(selector: str) -> dict[str, object]:
            return self._handle_entity(selector)

        @mcp.tool(
            name="groop_history",
            description=(
                "Return one registry metric's time series for an exact EntityKey or P57 docker name/prefix; "
                "window is last:Ns or since:TS, limit is 1..100 points, and the aggregate response cap is 4 MiB; "
                "P52 registry metadata supplies the metric semantics/sensitivity, unavailable values are omitted, "
                "and redacted values are marked."
            ),
        )
        def groop_history(
            selector: str, metric: str = "ram", window: str = "last:300", limit: object = 100
        ) -> dict[str, object]:
            return self._handle_history(selector, metric, window, limit)

        return mcp

    def _try_call(self, call: Callable[[], object]) -> object:
        try:
            return call()
        except DaemonConnectError:
            return _tool_error("daemon-unavailable", "daemon is unavailable")
        except DaemonProtocolError:
            return _tool_error("daemon-unavailable", "daemon returned an incompatible response")
        except DaemonResponseError as exc:
            if exc.code in {"not_found", "invalid_type", "bad_request"}:
                return _tool_error("invalid-selector", "daemon rejected the requested selector")
            if exc.code in {"out_of_range", "oversized_response"}:
                return _tool_error("over-limit", "daemon rejected a request bound")
            return _tool_error("daemon-unavailable", "daemon could not serve the request")
        except DaemonClientError:
            return _tool_error("daemon-unavailable", "daemon client request failed")
        except Exception:
            return _tool_error("internal", "internal tool error")

    def _handle_health(self) -> dict[str, object]:
        result = self._try_call(self._client.request_health)
        if _is_error(result):
            return result  # type: ignore[return-value]
        if not isinstance(result, HealthSnapshot):
            return _tool_error("internal", "daemon returned an invalid health summary")
        if len(result.snapshots) > MAX_HEALTH_COMPONENTS:
            return _tool_error("over-limit", "health component count exceeds the tool limit")
        return _ok({"components": [snapshot.to_jsonable() for snapshot in result.snapshots]})

    def _handle_overview(self, sort_by: str, limit: object) -> dict[str, object]:
        metric = SORT_KEY_MAP.get(sort_by)
        if metric is None:
            return _tool_error("invalid-selector", "sort_by must be one of: " + ", ".join(SORT_KEY_MAP))
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return _tool_error("over-limit", "limit must be a positive integer")
        if limit > MAX_OVERVIEW_LIMIT:
            return _tool_error("over-limit", f"limit exceeds maximum {MAX_OVERVIEW_LIMIT}")

        current = self._try_call(self._client.request_current)
        if _is_error(current):
            return current  # type: ignore[return-value]
        if not isinstance(current, DaemonCurrentResult):
            return _tool_error("internal", "daemon returned an invalid current frame")

        rows: list[tuple[float | int, dict[str, object]]] = []
        for key, entity_frame in current.frame.entities.items():
            value = entity_frame.metrics.get(metric)
            if value is None or value.v is None:
                continue
            sensitivity = _sensitivity(current.metrics_meta.get(metric), metric)
            row: dict[str, object] = {
                "key": str(key),
                "metric": metric,
                "value": _redact(value.v, sensitivity, self._redact_above),
                "sensitivity": sensitivity.value,
            }
            if entity_frame.entity.docker is not None and entity_frame.entity.docker.name:
                row["docker_name"] = entity_frame.entity.docker.name
            rows.append((value.v, row))
        rows.sort(key=lambda pair: pair[0], reverse=True)
        return _ok({"sort_by": sort_by, "rows": [row for _, row in rows[:limit]]})

    def _resolve_entity_selector(self, selector: str) -> str | dict[str, object]:
        """Resolve an exact key directly, otherwise reuse P57's sole name resolver."""
        if not isinstance(selector, str) or not selector:
            return _tool_error("invalid-selector", "selector must be a non-empty string")
        direct = self._try_call(lambda: self._client.request_entity(selector))
        if not _is_error(direct):
            return selector
        if not isinstance(direct, dict) or direct.get("error", {}).get("code") != "invalid-selector":
            return direct  # daemon unavailable/internal must not be masked by a resolver lookup

        current = self._try_call(self._client.request_current)
        if _is_error(current):
            return current  # type: ignore[return-value]
        if not isinstance(current, DaemonCurrentResult):
            return _tool_error("internal", "daemon returned an invalid current frame")
        entities: dict[EntityKey, Entity] = {
            key: frame.entity for key, frame in current.frame.entities.items()
        }
        try:
            return str(resolve_container_key(selector, entities))
        except ContainerResolveError:
            return _tool_error("invalid-selector", "selector does not identify one running entity")

    def _handle_entity(self, selector: str) -> dict[str, object]:
        resolved = self._resolve_entity_selector(selector)
        if isinstance(resolved, dict):
            return resolved
        result = self._try_call(lambda: self._client.request_entity(resolved))
        if _is_error(result):
            return result  # type: ignore[return-value]
        if not isinstance(result, DaemonEntityResult):
            return _tool_error("internal", "daemon returned an invalid entity result")
        entity = result.entity
        if len(entity.metrics) > MAX_ENTITY_METRICS or len(entity.findings) > MAX_ENTITY_FINDINGS:
            return _tool_error("over-limit", "entity detail exceeds the tool item limit")

        metrics: dict[str, object] = {}
        for name, value in entity.metrics.items():
            if value.v is None:
                continue
            meta = result.metrics_meta.get(name)
            sensitivity = _sensitivity(meta, name)
            metric_result: dict[str, object] = {
                "value": _redact(value.v, sensitivity, self._redact_above),
                "sensitivity": sensitivity.value,
            }
            if meta is not None and isinstance(meta.get("unit"), str):
                metric_result["unit"] = meta["unit"]
            metrics[name] = metric_result

        data: dict[str, object] = {
            "key": str(entity.entity.key),
            "kind": entity.entity.kind,
            "parent": entity.entity.parent,
            "tier": entity.entity.tier,
            "metrics": metrics,
            "findings": [
                {"rule_id": finding.rule_id, "severity": finding.severity, "message": finding.message}
                for finding in entity.findings
            ],
        }
        if entity.entity.docker is not None and entity.entity.docker.name:
            data["docker_name"] = entity.entity.docker.name
        return _ok(data)

    def _history_selector(
        self, selector: str, history: DaemonHistoryResult
    ) -> str | dict[str, object]:
        if not isinstance(selector, str) or not selector:
            return _tool_error("invalid-selector", "selector must be a non-empty string")
        for _, frame in history.entries:
            if EntityKey(selector) in frame.entities:
                return selector
        if not history.entries:
            return _tool_error("invalid-selector", "selector is not present in history")
        latest = history.entries[-1][1]
        try:
            return str(resolve_container_key(selector, {key: row.entity for key, row in latest.entities.items()}))
        except ContainerResolveError:
            return _tool_error("invalid-selector", "selector does not identify one entity in history")

    def _handle_history(self, selector: str, metric: str, window: str, limit: object) -> dict[str, object]:
        if not isinstance(metric, str) or not metric:
            return _tool_error("invalid-selector", "metric must be a non-empty registry metric name")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return _tool_error("over-limit", "limit must be a positive integer")
        if limit > MAX_HISTORY_LIMIT:
            return _tool_error("over-limit", f"limit exceeds maximum {MAX_HISTORY_LIMIT}")
        if not isinstance(window, str):
            return _tool_error("invalid-selector", "window must be last:Ns or since:TS")

        since_ts: float
        if window.startswith("last:"):
            try:
                seconds = float(window[5:])
            except ValueError:
                return _tool_error("invalid-selector", "window must be last:Ns or since:TS")
            if not math.isfinite(seconds) or seconds <= 0:
                return _tool_error("over-limit", "last window must be positive")
            if seconds > MAX_WINDOW_SECONDS:
                return _tool_error("over-limit", "last window exceeds 7 days")
            since_ts = time.time() - seconds
        elif window.startswith("since:"):
            try:
                since_ts = float(window[6:])
            except ValueError:
                return _tool_error("invalid-selector", "window must be last:Ns or since:TS")
            if not math.isfinite(since_ts) or since_ts < 0:
                return _tool_error("invalid-selector", "since timestamp must be finite and non-negative")
        else:
            return _tool_error("invalid-selector", "window must be last:Ns or since:TS")

        result = self._try_call(
            lambda: self._client.request_history(limit=limit, since_ts=since_ts, until_ts=None)
        )
        if _is_error(result):
            return result  # type: ignore[return-value]
        if not isinstance(result, DaemonHistoryResult):
            return _tool_error("internal", "daemon returned an invalid history result")
        resolved = self._history_selector(selector, result)
        if isinstance(resolved, dict):
            return resolved

        sensitivity = _sensitivity(result.metrics_meta.get(metric), metric)
        series: list[list[object]] = []
        for _, frame in result.entries:
            row = frame.entities.get(EntityKey(resolved))
            if row is None:
                continue
            value = row.metrics.get(metric)
            if value is None or value.v is None:
                continue
            series.append([frame.ts, _redact(value.v, sensitivity, self._redact_above)])
        return _ok(
            {
                "entity_key": resolved,
                "metric": metric,
                "sensitivity": sensitivity.value,
                "series": series,
                "count": len(series),
            }
        )


def run_server(
    socket_path: Path = DEFAULT_SOCKET_PATH,
    *,
    redact_above: Sensitivity | None = None,
    register_signals: SignalRegistration = install_signal_handlers,
) -> int:
    """Run the stdio frontend using the typed daemon client."""
    return McpServer(
        DaemonClient(socket_path), redact_above=redact_above, register_signals=register_signals
    ).run()
