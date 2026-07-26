"""P81 adversarial oracles: one fail-closed redaction enforcement point.

Every oracle drives a *real* ``DaemonApi`` over a temporary ``AF_UNIX`` socket
through both read frontends -- the HTTP gateway and the MCP stdio server -- with
no mocked client.  The frame carries a ``Finding`` whose free-text message
embeds the literal value of a ``sensitive`` metric it names in
``source_metrics``: the exact ``findings[]`` bypass P81 exists to close.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

# topos[mcp] is an optional extra; without the SDK this whole module skips
# rather than aborting collection (the P84 gate installs the extra).
pytest.importorskip("mcp", reason="topos[mcp] extra not installed")

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from topos.daemon import (  # noqa: E402
    DaemonApi,
    FrameBroker,
    Sensitivity,
    serve_versioned_unix_socket,
)
from topos.daemon import redaction  # noqa: E402
from topos.daemon.client import DaemonClient  # noqa: E402
from topos.daemon.http_gateway import (  # noqa: E402
    IDENTITY_HEADER,
    GatewayAuthConfig,
    GatewayConfig,
    VersionedReadHttpGateway,
    serve_versioned_http_gateway,
)
from topos.daemon.redaction import PayloadShape, classify_metric, redaction_marker  # noqa: E402
from topos.mcp.server import McpServer  # noqa: E402
from topos.model import (  # noqa: E402
    Entity,
    EntityFrame,
    Finding,
    Frame,
    MetricValue,
)

# A distinctive byte pattern for the sensitive metric value. The oracles grep
# raw response bytes for this; it must not collide with any registry/meta text.
SENTINEL = 987654321
ENTITY_KEY = "system.slice/leaky.service"


def _leaky_frame() -> Frame:
    """A frame whose finding message embeds a sensitive metric value verbatim."""
    entity = Entity(key=ENTITY_KEY, kind="service", parent="system.slice")
    finding = Finding(
        rule_id="pids_saturation",
        severity="warn",
        message=f"process count reached {SENTINEL} of its limit",
        remedy=f"cap the workload below {SENTINEL} processes",
        source_metrics=("cgroup_procs",),
        confidence="exact",
    )
    entity_frame = EntityFrame(
        entity=entity,
        metrics={
            "cgroup_procs": MetricValue(v=SENTINEL, src="exact"),
            "ram": MetricValue(v=4096.0, src="exact"),
        },
        findings=[finding],
    )
    return Frame(
        schema_version=1,
        ts=1_000.0,
        interval_s=10.0,
        host={},
        entities={ENTITY_KEY: entity_frame},
    )


@dataclass
class _LiveStack:
    gateway: VersionedReadHttpGateway
    mcp: McpServer


@contextmanager
def _live_stack(tmp_path: Path, *, ceiling: str = "operational") -> Iterator[_LiveStack]:
    """Real DaemonApi -> {DaemonClient -> HTTP gateway, DaemonClient -> MCP}."""
    daemon_socket = tmp_path / "daemon.sock"
    broker = FrameBroker([_leaky_frame()])
    daemon = serve_versioned_unix_socket(daemon_socket, broker, DaemonApi(broker))
    daemon_thread = threading.Thread(target=daemon.serve_forever, daemon=True)
    daemon_thread.start()

    gateway = serve_versioned_http_gateway(
        daemon_socket,
        config=GatewayConfig(auth=GatewayAuthConfig({"operator": ceiling})),
    )
    gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    gateway_thread.start()

    mcp = McpServer(DaemonClient(daemon_socket), redact_above=Sensitivity(ceiling))
    try:
        yield _LiveStack(gateway=gateway, mcp=mcp)
    finally:
        gateway.shutdown()
        gateway.server_close()
        gateway_thread.join(timeout=2.0)
        daemon.shutdown()
        daemon.server_close()
        daemon_thread.join(timeout=2.0)
        broker.stop()
        broker.join(timeout=2.0)


def _http_get(gateway: VersionedReadHttpGateway, target: str) -> bytes:
    host, port = gateway.server_address
    connection = http.client.HTTPConnection(host, port, timeout=2.0)
    connection.request("GET", target, headers={IDENTITY_HEADER: "operator"})
    response = connection.getresponse()
    body = response.read()
    assert response.status == 200, (response.status, body)
    connection.close()
    return body


async def _mcp_tool_text(mcp: McpServer, name: str, arguments: dict[str, object]) -> str:
    """Return the raw MCP tool response text a client would receive."""
    app = mcp.build_mcp_server(FastMCP)
    async with create_connected_server_and_client_session(app) as session:
        result = await session.call_tool(name, arguments)
    assert result.isError is False, result.content
    assert result.content
    return result.content[0].text


def _mcp_entity_text(mcp: McpServer) -> str:
    return asyncio.run(_mcp_tool_text(mcp, "topos_entity", {"selector": ENTITY_KEY}))


def _assert_sentinel_absent(label: str, raw: bytes | str) -> None:
    """The load-bearing leak check every oracle shares."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    assert str(SENTINEL) not in text, f"{label}: sensitive value leaked in raw bytes"


# --- Oracle 1: the findings bypass is closed -----------------------------


def test_oracle1_findings_bypass_is_closed_on_both_frontends(tmp_path: Path) -> None:
    with _live_stack(tmp_path, ceiling="operational") as stack:
        current = _http_get(stack.gateway, "/v1/current")
        entity = _http_get(stack.gateway, "/v1/entity?key=" + ENTITY_KEY.replace("/", "%2F"))
        mcp_text = _mcp_entity_text(stack.mcp)

    # The value must appear nowhere -- not in a metric, not in finding prose --
    # on any route of either frontend.
    _assert_sentinel_absent("gateway /v1/current", current)
    _assert_sentinel_absent("gateway /v1/entity", entity)
    _assert_sentinel_absent("mcp topos_entity", mcp_text)

    # And the finding's operational facts survive the redaction.
    entity_finding = json.loads(entity)["entity"]["findings"][0]
    assert entity_finding["rule_id"] == "pids_saturation"
    assert entity_finding["severity"] == "warn"
    assert entity_finding["source_metrics"] == ["cgroup_procs"]
    assert entity_finding["message"] == redaction_marker(Sensitivity.SENSITIVE)
    assert entity_finding["remedy"] == redaction_marker(Sensitivity.SENSITIVE)

    mcp_finding = json.loads(mcp_text)["data"]["findings"][0]
    assert mcp_finding["rule_id"] == "pids_saturation"
    assert mcp_finding["source_metrics"] == ["cgroup_procs"]
    assert mcp_finding["message"] == redaction_marker(Sensitivity.SENSITIVE)


# --- Oracle 2: disarming the enforcement function fails the suite ---------


def test_oracle2_disarming_the_enforcement_function_goes_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Armed: the shared leak check passes on both frontends.
    with _live_stack(tmp_path, ceiling="operational") as stack:
        _assert_sentinel_absent("armed gateway", _http_get(stack.gateway, "/v1/entity?key=" + ENTITY_KEY.replace("/", "%2F")))
        _assert_sentinel_absent("armed mcp", _mcp_entity_text(stack.mcp))

    # Disarm the single enforcement function to the identity and re-drive.
    # If redaction had no single choke point, this no-op could not disarm both.
    monkeypatch.setattr(redaction, "redact_payload", lambda payload, **_kwargs: payload)

    with _live_stack(tmp_path, ceiling="operational") as stack:
        disarmed_gateway = _http_get(stack.gateway, "/v1/entity?key=" + ENTITY_KEY.replace("/", "%2F"))
        disarmed_mcp = _mcp_entity_text(stack.mcp)

    # The same leak check the real oracles use must now fire -- proof the suite
    # goes red against a disarmed redactor rather than staying falsely green.
    with pytest.raises(AssertionError):
        _assert_sentinel_absent("disarmed gateway", disarmed_gateway)
    with pytest.raises(AssertionError):
        _assert_sentinel_absent("disarmed mcp", disarmed_mcp)


# --- Oracle 3: one marker dialect, byte-equal across frontends ------------


def test_oracle3_one_marker_dialect_byte_equal_across_frontends(tmp_path: Path) -> None:
    with _live_stack(tmp_path, ceiling="operational") as stack:
        entity = _http_get(stack.gateway, "/v1/entity?key=" + ENTITY_KEY.replace("/", "%2F"))
        mcp_text = _mcp_entity_text(stack.mcp)

    gateway_marker = json.loads(entity)["entity"]["metrics"]["cgroup_procs"]
    mcp_marker = json.loads(mcp_text)["data"]["metrics"]["cgroup_procs"]["value"]

    # Byte-equal marker object for the same sensitive input.
    gateway_bytes = json.dumps(gateway_marker, sort_keys=True).encode("utf-8")
    mcp_bytes = json.dumps(mcp_marker, sort_keys=True).encode("utf-8")
    assert gateway_bytes == mcp_bytes
    assert gateway_marker == {"redacted": True, "sensitivity": "sensitive"}
    # The retired dialect must not appear anywhere in either raw response.
    assert b"__redacted__" not in entity
    assert "__redacted__" not in mcp_text


# --- Oracle 4: fail closed on an unclassified metric ----------------------


@pytest.mark.parametrize("ceiling", [Sensitivity.PUBLIC, Sensitivity.OPERATIONAL])
def test_oracle4_unclassified_metric_fails_closed_in_the_shared_point(ceiling: Sensitivity) -> None:
    # A registry frame can never carry a metric the daemon omits from
    # metrics_meta, so the only place this is reachable is the shared
    # enforcement point that BOTH frontends delegate to. Exercise it in the
    # gateway (FRAME) and MCP (MCP_ENTITY) shapes with empty metadata.
    assert classify_metric("ram", {}) is Sensitivity.SENSITIVE

    frame_payload = {
        "schema_version": 1,
        "ts": 1.0,
        "interval_s": 10.0,
        "host": {},
        "entities": {
            ENTITY_KEY: {
                "entity": {"key": ENTITY_KEY, "kind": "service", "parent": "system.slice"},
                "metrics": {"ram": [4096.0, "exact"]},
                "findings": [],
            }
        },
    }
    redaction.redact_payload(frame_payload, shape=PayloadShape.FRAME, metrics_meta={}, ceiling=ceiling)
    redacted = frame_payload["entities"][ENTITY_KEY]["metrics"]["ram"]
    assert redacted == {"redacted": True, "sensitivity": "sensitive"}

    mcp_payload = {"metrics": {"ram": {"value": 4096.0, "sensitivity": "operational"}}, "findings": []}
    redaction.redact_payload(mcp_payload, shape=PayloadShape.MCP_ENTITY, metrics_meta={}, ceiling=ceiling)
    assert mcp_payload["metrics"]["ram"]["value"] == {"redacted": True, "sensitivity": "sensitive"}


def test_oracle4_unclassified_metric_is_hidden_end_to_end(tmp_path: Path) -> None:
    # Belt and braces: at a public ceiling the operational ``ram`` value 4096 is
    # above ceiling and must be hidden on both live frontends.
    with _live_stack(tmp_path, ceiling="public") as stack:
        entity = _http_get(stack.gateway, "/v1/entity?key=" + ENTITY_KEY.replace("/", "%2F"))
        mcp_text = _mcp_entity_text(stack.mcp)
    assert b"4096" not in entity
    assert "4096" not in mcp_text


# --- Oracle 5: keys, units, and metrics_meta survive redaction ------------


def test_oracle5_keys_units_and_metrics_meta_survive_on_every_route(tmp_path: Path) -> None:
    with _live_stack(tmp_path, ceiling="operational") as stack:
        current = json.loads(_http_get(stack.gateway, "/v1/current"))
        history = json.loads(_http_get(stack.gateway, "/v1/history?limit=1"))
        entity = json.loads(_http_get(stack.gateway, "/v1/entity?key=" + ENTITY_KEY.replace("/", "%2F")))
        mcp_text = json.loads(_mcp_entity_text(stack.mcp))

    for label, decoded in (("current", current), ("history", history), ("entity", entity)):
        meta = decoded["metrics_meta"]
        # metrics_meta passes through intact: the UI must still learn *why* a
        # value is hidden.
        assert meta["cgroup_procs"]["sensitivity"] == "sensitive", label
        assert "unit" in meta["cgroup_procs"], label
        assert meta["ram"]["sensitivity"] == "operational", label

    # Redaction replaces values, it never drops keys: the redacted metric key
    # remains and a below-ceiling value is untouched.
    for label, frame in (("current", current["frame"]), ("history", history["frames"][0]["frame"])):
        metrics = frame["entities"][ENTITY_KEY]["metrics"]
        assert metrics["cgroup_procs"] == {"redacted": True, "sensitivity": "sensitive"}, label
        assert metrics["ram"] == [4096.0, "exact"], label

    entity_metrics = entity["entity"]["metrics"]
    assert entity_metrics["cgroup_procs"] == {"redacted": True, "sensitivity": "sensitive"}
    assert entity_metrics["ram"] == [4096.0, "exact"]

    # MCP keeps the redacted key with its unit and sensitivity, and the
    # below-ceiling value survives.
    mcp_metrics = mcp_text["data"]["metrics"]
    assert mcp_metrics["cgroup_procs"]["value"] == {"redacted": True, "sensitivity": "sensitive"}
    assert mcp_metrics["cgroup_procs"]["sensitivity"] == "sensitive"
    assert "unit" in mcp_metrics["cgroup_procs"]
    assert mcp_metrics["ram"]["value"] == 4096.0


# --- Fail-closed on unrecognized value-bearing entity fields --------------


def test_unrecognized_value_bearing_field_is_failed_closed() -> None:
    # governance carries live_value/recorded_value (metric values) but has no
    # typed visitor; the enforcement point must not emit it above the ceiling.
    payload = {
        "entity": {"key": ENTITY_KEY, "kind": "service", "parent": "system.slice"},
        "metrics": {},
        "findings": [],
        "governance": {"limits": {"mem_min": {"live_value": 1073741824}}},
    }
    redaction.redact_payload(
        payload, shape=PayloadShape.ENTITY_FRAME, metrics_meta={}, ceiling=Sensitivity.OPERATIONAL
    )
    assert payload["governance"] == {"redacted": True, "sensitivity": "sensitive"}
    # Identity metadata is not value-bearing and passes through.
    assert payload["entity"]["kind"] == "service"


def test_unrecognized_field_passes_at_the_explicit_sensitive_ceiling() -> None:
    # Review fix: the unknown-field branch classifies the whole field
    # ``sensitive`` and then compares against the ceiling. A principal whose
    # configured ceiling IS ``sensitive`` (the top of the closed enum) is
    # entitled to it — redacting it anyway would make the top ceiling
    # unreachable and silently strip governance/network/damon/host_meta from
    # the one principal class allowed to see them.
    governance = {"limits": {"mem_min": {"live_value": 1073741824}}}
    payload = {
        "entity": {"key": ENTITY_KEY, "kind": "service", "parent": "system.slice"},
        "metrics": {},
        "findings": [],
        "governance": governance,
    }
    redaction.redact_payload(
        payload, shape=PayloadShape.ENTITY_FRAME, metrics_meta={}, ceiling=Sensitivity.SENSITIVE
    )
    assert payload["governance"] == governance

    frame_payload = {
        "schema_version": 1,
        "ts": 1000.0,
        "interval_s": 5.0,
        "host": {},
        "entities": {},
        "host_meta": {"zram_devices": [{"name": "zram0"}]},
    }
    redaction.redact_payload(
        frame_payload, shape=PayloadShape.FRAME, metrics_meta={}, ceiling=Sensitivity.SENSITIVE
    )
    assert frame_payload["host_meta"] == {"zram_devices": [{"name": "zram0"}]}

    # And the same field is still failed closed one step below the top.
    redaction.redact_payload(
        frame_payload, shape=PayloadShape.FRAME, metrics_meta={}, ceiling=Sensitivity.OPERATIONAL
    )
    assert frame_payload["host_meta"] == {"redacted": True, "sensitivity": "sensitive"}


def test_unregistered_shape_fails_closed() -> None:
    with pytest.raises(redaction.RedactionError):
        redaction.redact_payload({}, shape="not-a-shape", metrics_meta={}, ceiling=Sensitivity.PUBLIC)  # type: ignore[arg-type]


def test_no_ceiling_is_a_faithful_no_op() -> None:
    payload = {"metrics": {"cgroup_procs": {"value": SENTINEL, "sensitivity": "sensitive"}}}
    result = redaction.redact_payload(payload, shape=PayloadShape.MCP_ENTITY, metrics_meta={}, ceiling=None)
    assert result["metrics"]["cgroup_procs"]["value"] == SENTINEL


# --- P131 malformed-shape coverage: direct redact_payload tests -----------


class TestMalformedEntityFrame:
    def test_entity_frame_metrics_non_dict_preserved_findings_non_list_preserved(self) -> None:
        """ENTITY_FRAME with non-dict metrics and non-list findings must not crash; values preserved."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {
            "entity": {"key": "test", "kind": "scope", "parent": None},
            "metrics": "not_a_dict",  # malformed
            "findings": "not_a_list",  # malformed
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.ENTITY_FRAME, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["metrics"] == "not_a_dict"
        assert payload["findings"] == "not_a_list"

    def test_entity_frame_findings_list_with_non_dict_entry_skipped(self) -> None:
        """ENTITY_FRAME findings list containing non-dict entry; non-dict skipped, valid sibling redacted."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {
            "entity": {"key": "test", "kind": "scope", "parent": None},
            "metrics": {},
            "findings": [
                "not_a_dict",  # malformed, skipped
                {
                    "rule_id": "test_rule",
                    "severity": "warn",
                    "message": "sensitive finding",
                    "source_metrics": ["secret"],
                },
            ],
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.ENTITY_FRAME, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["findings"][0] == "not_a_dict"
        assert payload["findings"][1]["rule_id"] == "test_rule"
        assert payload["findings"][1]["message"] == redaction_marker(Sensitivity.SENSITIVE)

    def test_entity_frame_finding_with_string_source_metrics_keeps_prose(self) -> None:
        """Finding with string source_metrics (not a list) preserves message."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {
            "entity": {"key": "test", "kind": "scope", "parent": None},
            "metrics": {},
            "findings": [
                {
                    "rule_id": "test_rule",
                    "severity": "info",
                    "message": "test message",
                    "source_metrics": "not_a_list",  # malformed, _finding_ceiling_breach returns None
                },
            ],
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.ENTITY_FRAME, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["findings"][0]["message"] == "test message"

    def test_entity_frame_finding_with_above_ceiling_list_redacts_remedy(self) -> None:
        """Finding with above-ceiling list source_metrics redacts remedy even when message absent."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {
            "entity": {"key": "test", "kind": "scope", "parent": None},
            "metrics": {},
            "findings": [
                {
                    "rule_id": "test_rule",
                    "severity": "warn",
                    "source_metrics": ["secret"],
                    # message and remedy both absent/None initially
                    "remedy": None,
                },
            ],
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.ENTITY_FRAME, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        # remedy=None doesn't create a marker; only redacts if field exists
        assert payload["findings"][0]["remedy"] is None
        # Verify remedy presence is tested separately
        payload["findings"][0]["remedy"] = "fix it"
        redaction.redact_payload(
            payload, shape=PayloadShape.ENTITY_FRAME, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["findings"][0]["remedy"] == redaction_marker(Sensitivity.SENSITIVE)

    def test_entity_frame_finding_source_metrics_public_then_secret_redacts_message(self) -> None:
        """Finding with multiple source_metrics (public, then secret) finds secret and redacts message/remedy."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {
            "entity": {"key": "test", "kind": "scope", "parent": None},
            "metrics": {},
            "findings": [
                {
                    "rule_id": "test_rule",
                    "severity": "info",
                    "message": "alert about public and secret",
                    "remedy": "check both metrics",
                    "source_metrics": ["public", "secret"],  # public first, secret second
                },
            ],
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.ENTITY_FRAME, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        # The secret metric causes message and remedy to be redacted
        assert payload["findings"][0]["message"] == redaction_marker(Sensitivity.SENSITIVE)
        assert payload["findings"][0]["remedy"] == redaction_marker(Sensitivity.SENSITIVE)
        # Operational facts survive
        assert payload["findings"][0]["rule_id"] == "test_rule"
        assert payload["findings"][0]["severity"] == "info"
        assert payload["findings"][0]["source_metrics"] == ["public", "secret"]


class TestMalformedFrame:
    def test_frame_host_non_dict_entities_non_dict_preserved(self) -> None:
        """FRAME with non-dict host and non-dict entities; malformed preserved."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {
            "schema_version": 1,
            "ts": 1000.0,
            "interval_s": 5.0,
            "host": "not_a_dict",  # malformed
            "entities": "not_a_dict",  # malformed
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.FRAME, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["host"] == "not_a_dict"
        assert payload["entities"] == "not_a_dict"

    def test_frame_entities_dict_with_non_dict_sibling_plus_valid_entity(self) -> None:
        """FRAME entities dict with non-dict sibling + valid entity; valid sibling redacted, malformed kept."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {
            "schema_version": 1,
            "ts": 1000.0,
            "interval_s": 5.0,
            "host": {},
            "entities": {
                "malformed_key": "not_a_dict",  # malformed entity, kept as-is
                "valid_entity": {
                    "entity": {"key": "valid_entity", "kind": "scope", "parent": None},
                    "metrics": {
                        "secret": {"value": SENTINEL},
                        "public": {"value": 123},
                    },
                    "findings": [],
                },
            },
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.FRAME, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        # Malformed sibling untouched
        assert payload["entities"]["malformed_key"] == "not_a_dict"
        # Valid sibling redacted
        assert payload["entities"]["valid_entity"]["metrics"]["secret"] == redaction_marker(Sensitivity.SENSITIVE)
        assert payload["entities"]["valid_entity"]["metrics"]["public"]["value"] == 123


class TestMalformedMcpOverview:
    def test_mcp_overview_missing_rows_safe_noop(self) -> None:
        """MCP_OVERVIEW missing rows field is safe no-op."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {"sort_by": "metric"}  # rows missing
        redaction.redact_payload(
            payload, shape=PayloadShape.MCP_OVERVIEW, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload == {"sort_by": "metric"}

    def test_mcp_overview_non_list_rows_safe_noop(self) -> None:
        """MCP_OVERVIEW non-list rows field is safe no-op."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {"sort_by": "metric", "rows": "not_a_list"}
        redaction.redact_payload(
            payload, shape=PayloadShape.MCP_OVERVIEW, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["rows"] == "not_a_list"

    def test_mcp_overview_mixed_rows_preserve_public_redact_sensitive(self) -> None:
        """MCP_OVERVIEW mixed rows: non-dict skipped, missing value skipped, public preserved, sensitive redacted."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {
            "sort_by": "metric",
            "rows": [
                "not_a_dict",  # skipped
                {"metric": "secret"},  # no value, skipped
                {"key": "a.scope", "metric": "public", "value": 42},  # preserved
                {"key": "b.scope", "metric": "secret", "value": SENTINEL},  # redacted
            ],
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.MCP_OVERVIEW, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["rows"][0] == "not_a_dict"
        assert payload["rows"][1] == {"metric": "secret"}
        assert payload["rows"][2]["value"] == 42
        assert payload["rows"][3]["value"] == redaction_marker(Sensitivity.SENSITIVE)


class TestMalformedMcpEntity:
    def test_mcp_entity_non_dict_metrics_preserved(self) -> None:
        """MCP_ENTITY non-dict metrics is safe; findings still processed."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {
            "key": "a.scope",
            "metrics": "not_a_dict",  # malformed
            "findings": [
                {"rule_id": "rule1", "source_metrics": ["secret"], "message": "text"},
            ],
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.MCP_ENTITY, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["metrics"] == "not_a_dict"
        assert payload["findings"][0]["message"] == redaction_marker(Sensitivity.SENSITIVE)

    def test_mcp_entity_non_dict_findings_preserved(self) -> None:
        """MCP_ENTITY non-dict findings is safe; metrics still processed."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {
            "key": "a.scope",
            "metrics": {
                "public": {"value": 42},
                "secret": {"value": SENTINEL},
            },
            "findings": "not_a_list",  # malformed
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.MCP_ENTITY, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["findings"] == "not_a_list"
        assert payload["metrics"]["public"]["value"] == 42
        assert payload["metrics"]["secret"]["value"] == redaction_marker(Sensitivity.SENSITIVE)

    def test_mcp_entity_mixed_metrics_and_findings(self) -> None:
        """MCP_ENTITY mixed entries: preserve public metric, redact sensitive, skip malformed."""
        metrics_meta = {"secret": {"sensitivity": "sensitive"}, "public": {"sensitivity": "public"}}
        payload = {
            "key": "a.scope",
            "metrics": {
                "public": {"value": 42},
                "secret": {"value": SENTINEL},
                "malformed": {"no_value_field": 123},  # no value, skipped
                "also_malformed": "not_a_dict",  # not a dict, skipped
            },
            "findings": [
                {"rule_id": "rule1", "source_metrics": ["secret"], "message": "text"},
                "not_a_dict",  # skipped
                {"rule_id": "rule2", "severity": "warn"},  # no source_metrics, message untouched
            ],
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.MCP_ENTITY, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["metrics"]["public"]["value"] == 42
        assert payload["metrics"]["secret"]["value"] == redaction_marker(Sensitivity.SENSITIVE)
        assert payload["metrics"]["malformed"] == {"no_value_field": 123}
        assert payload["metrics"]["also_malformed"] == "not_a_dict"
        assert payload["findings"][0]["message"] == redaction_marker(Sensitivity.SENSITIVE)
        assert payload["findings"][1] == "not_a_dict"
        # rule2 has no source_metrics so message is untouched
        assert "message" not in payload["findings"][2] or payload["findings"][2].get("severity") == "warn"


class TestMalformedMcpHistory:
    def test_mcp_history_public_metric_leaves_series_intact(self) -> None:
        """MCP_HISTORY public metric leaves series intact."""
        metrics_meta = {"public": {"sensitivity": "public"}, "secret": {"sensitivity": "sensitive"}}
        payload = {
            "entity_key": "a.scope",
            "metric": "public",
            "sensitivity": "public",
            "series": [[1000.0, 42.0], [1001.0, 43.0]],
            "count": 2,
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.MCP_HISTORY, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["series"] == [[1000.0, 42.0], [1001.0, 43.0]]

    def test_mcp_history_sensitive_non_list_series_safe_noop(self) -> None:
        """MCP_HISTORY sensitive metric with non-list series is safe no-op."""
        metrics_meta = {"public": {"sensitivity": "public"}, "secret": {"sensitivity": "sensitive"}}
        payload = {
            "entity_key": "a.scope",
            "metric": "secret",
            "sensitivity": "sensitive",
            "series": "not_a_list",  # malformed
            "count": 0,
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.MCP_HISTORY, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["series"] == "not_a_list"

    def test_mcp_history_sensitive_series_mixed_points_preserves_malformed_replaces_valid(self) -> None:
        """MCP_HISTORY sensitive series: malformed points preserved, valid points have index 1 replaced."""
        metrics_meta = {"public": {"sensitivity": "public"}, "secret": {"sensitivity": "sensitive"}}
        marker = redaction_marker(Sensitivity.SENSITIVE)
        payload = {
            "entity_key": "a.scope",
            "metric": "secret",
            "sensitivity": "sensitive",
            "series": [
                "not_a_point",  # malformed, preserved
                [1000.0, 42.0],  # valid, index 1 replaced
                [1001.0],  # too short, skipped
                [1002.0, 43.0, "extra"],  # valid length >= 2, index 1 replaced
            ],
            "count": 4,
        }
        redaction.redact_payload(
            payload, shape=PayloadShape.MCP_HISTORY, metrics_meta=metrics_meta, ceiling=Sensitivity.PUBLIC
        )
        assert payload["series"][0] == "not_a_point"
        assert payload["series"][1][1] == marker
        assert payload["series"][2] == [1001.0]
        assert payload["series"][3][1] == marker
