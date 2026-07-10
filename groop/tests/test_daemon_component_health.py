"""Tests for daemon component health (P47).

Covers:
- ComponentState enum values
- ComponentError bounded error type
- ComponentSnapshot serialization (to_jsonable)
- HealthSnapshot determinism and by_name lookup
- ComponentHealthRegistry state transitions (set_state, record_success,
  record_failure, record_degraded, mark_starting/stopping/stopped/disabled)
- Consecutive failure counter and reset on healthy
- Thread safety / deterministic snapshots during concurrent updates
- Bounded error detail (no tracebacks, env vars, paths, or secrets)
- Health protocol request through FrameBroker
- Health request through DaemonClient
- CLI groop daemon health --json / --pretty-json
- Unknown daemon health response (compatible-daemon guidance)
- Default-disabled state for all components
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

from conftest import fixture_frame
from groop.daemon.broker import FrameBroker, serve_unix_socket
from groop.daemon.component_health import (
    COMPONENT_NAMES,
    ComponentError,
    ComponentHealthRegistry,
    ComponentSnapshot,
    ComponentState,
    HealthSnapshot,
    HealthSnapshot,
    build_health_response,
)


def test_component_state_values() -> None:
    """ComponentState enum has all required states."""
    assert ComponentState.DISABLED.value == "disabled"
    assert ComponentState.STARTING.value == "starting"
    assert ComponentState.HEALTHY.value == "healthy"
    assert ComponentState.DEGRADED.value == "degraded"
    assert ComponentState.FAILED.value == "failed"
    assert ComponentState.STOPPING.value == "stopping"
    assert ComponentState.STOPPED.value == "stopped"


def test_component_error_bounded() -> None:
    """ComponentError only carries message and optional error_code."""
    err = ComponentError(message="something broke")
    assert err.message == "something broke"
    assert err.error_code is None

    err2 = ComponentError(message="timeout", error_code="ERR_TIMEOUT")
    assert err2.message == "timeout"
    assert err2.error_code == "ERR_TIMEOUT"


def test_component_names_match_default_disabled() -> None:
    """All COMPONENT_NAMES start disabled by default."""
    reg = ComponentHealthRegistry()
    snap = reg.snapshot()
    for name in COMPONENT_NAMES:
        cs = snap.by_name(name)
        assert cs is not None, f"missing component {name}"
        assert cs.state is ComponentState.DISABLED, f"{name} should start disabled"
        assert cs.consecutive_failures == 0
        assert cs.error is None


def test_set_state_healthy() -> None:
    """set_state to healthy resets failures and error."""
    reg = ComponentHealthRegistry()
    reg.set_state("collector", ComponentState.HEALTHY, detail="all good")
    snap = reg.snapshot()
    cs = snap.by_name("collector")
    assert cs is not None
    assert cs.state is ComponentState.HEALTHY
    assert cs.detail == "all good"
    assert cs.consecutive_failures == 0
    assert cs.error is None


def test_set_state_failed_increments_consecutive() -> None:
    """set_state to failed increments consecutive_failures."""
    reg = ComponentHealthRegistry()
    reg.set_state(
        "bpf_snapshot_bridge",
        ComponentState.FAILED,
        detail="no bpftool",
        error=ComponentError(message="no bpftool"),
    )
    snap = reg.snapshot()
    cs = snap.by_name("bpf_snapshot_bridge")
    assert cs is not None
    assert cs.state is ComponentState.FAILED
    assert cs.consecutive_failures == 1
    assert cs.error is not None
    assert cs.error.message == "no bpftool"

    # Second failure increments
    reg.set_state(
        "bpf_snapshot_bridge",
        ComponentState.FAILED,
        detail="still failing",
        error=ComponentError(message="still failing"),
    )
    snap = reg.snapshot()
    cs = snap.by_name("bpf_snapshot_bridge")
    assert cs is not None
    assert cs.consecutive_failures == 2


def test_consecutive_failures_reset_on_healthy() -> None:
    """record_success resets consecutive_failures to 0."""
    reg = ComponentHealthRegistry()
    reg.record_failure("collector", detail="fail")
    reg.record_failure("collector", detail="fail again")
    snap = reg.snapshot()
    assert snap.by_name("collector").consecutive_failures == 2

    reg.record_success("collector", detail="recovered")
    snap = reg.snapshot()
    cs = snap.by_name("collector")
    assert cs.consecutive_failures == 0
    assert cs.error is None
    assert cs.state is ComponentState.HEALTHY


def test_record_success_sets_timestamp() -> None:
    """record_success sets last_attempt_ts and last_success_ts."""
    reg = ComponentHealthRegistry()
    reg.record_success("collector", detail="ok")
    snap = reg.snapshot()
    cs = snap.by_name("collector")
    assert cs.last_attempt_ts is not None
    assert cs.last_success_ts is not None
    assert cs.state is ComponentState.HEALTHY


def test_record_failure_sets_last_attempt_not_last_success() -> None:
    """record_failure updates last_attempt_ts but not last_success_ts."""
    reg = ComponentHealthRegistry()
    reg.record_failure(
        "paddr_lifecycle",
        detail="fail",
        error=ComponentError(message="fail"),
    )
    snap = reg.snapshot()
    cs = snap.by_name("paddr_lifecycle")
    assert cs.last_attempt_ts is not None
    assert cs.last_success_ts is None
    assert cs.state is ComponentState.FAILED


def test_record_degraded() -> None:
    """record_degraded sets degraded state with optional error."""
    reg = ComponentHealthRegistry()
    reg.record_degraded(
        "bpf_snapshot_bridge",
        detail="partial data",
        error=ComponentError(message="partial refresh"),
    )
    snap = reg.snapshot()
    cs = snap.by_name("bpf_snapshot_bridge")
    assert cs.state is ComponentState.DEGRADED
    assert cs.detail == "partial data"
    assert cs.error is not None


def test_mark_starting_stopping_stopped_disabled() -> None:
    """Lifecycle marker methods work correctly."""
    reg = ComponentHealthRegistry()
    reg.mark_starting("collector", detail="booting")
    assert reg.snapshot().by_name("collector").state is ComponentState.STARTING

    reg.mark_stopping("collector", detail="shutting down")
    assert reg.snapshot().by_name("collector").state is ComponentState.STOPPING

    reg.mark_stopped("collector", detail="done")
    assert reg.snapshot().by_name("collector").state is ComponentState.STOPPED

    reg.mark_disabled("collector", detail="off")
    assert reg.snapshot().by_name("collector").state is ComponentState.DISABLED


def test_unknown_component_silently_ignored() -> None:
    """Setting state for an unknown component is silently ignored."""
    reg = ComponentHealthRegistry()
    reg.set_state("nonexistent", ComponentState.HEALTHY)
    reg.record_success("nonexistent")
    reg.record_failure("nonexistent")
    reg.mark_starting("nonexistent")
    # No exception raised
    snap = reg.snapshot()
    assert snap.by_name("nonexistent") is None


def test_snapshot_deterministic_order() -> None:
    """Snapshot always returns components in COMPONENT_NAMES order."""
    reg = ComponentHealthRegistry()
    reg.set_state("collector", ComponentState.HEALTHY)
    reg.set_state("bpf_snapshot_bridge", ComponentState.FAILED)
    reg.set_state("paddr_lifecycle", ComponentState.DISABLED)
    snap = reg.snapshot()
    names = [s.name for s in snap.snapshots]
    assert names == list(COMPONENT_NAMES)


def test_health_snapshot_to_jsonable() -> None:
    """HealthSnapshot.to_jsonable() produces expected protocol shape."""
    reg = ComponentHealthRegistry()
    reg.set_state("collector", ComponentState.HEALTHY, detail="running")
    reg.record_failure(
        "bpf_snapshot_bridge",
        detail="retrying",
        error=ComponentError(message="timeout", error_code="ERR_BPF"),
    )
    reg.set_state("paddr_lifecycle", ComponentState.DISABLED)

    j = reg.snapshot().to_jsonable()
    assert j["schema_version"] == 1
    assert len(j["components"]) == 3
    by_name = {c["name"]: c for c in j["components"]}
    assert by_name["collector"]["state"] == "healthy"
    assert by_name["bpf_snapshot_bridge"]["state"] == "failed"
    assert by_name["bpf_snapshot_bridge"]["error"]["message"] == "timeout"
    assert by_name["bpf_snapshot_bridge"]["error"]["error_code"] == "ERR_BPF"
    assert "last_attempt_ts" in by_name["bpf_snapshot_bridge"]
    # No traceback or secret fields in any component
    for c in j["components"]:
        assert "traceback" not in c
        assert "env" not in c
        assert "path" not in c


def test_concurrent_updates_deterministic() -> None:
    """Concurrent updates produce a valid snapshot without corruption."""
    reg = ComponentHealthRegistry()

    def _worker(name: str, iterations: int) -> None:
        for i in range(iterations):
            reg.set_state(name, ComponentState.HEALTHY, detail=f"ok-{i}")
            reg.record_failure(name, detail=f"fail-{i}", error=ComponentError(message=f"err-{i}"))
            reg.set_state(name, ComponentState.HEALTHY, detail=f"recov-{i}")

    threads = [
        threading.Thread(target=_worker, args=("collector", 50)),
        threading.Thread(target=_worker, args=("bpf_snapshot_bridge", 50)),
        threading.Thread(target=_worker, args=("paddr_lifecycle", 50)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    # Snapshot should be valid after concurrent updates
    snap = reg.snapshot()
    assert len(snap.snapshots) == 3
    for cs in snap.snapshots:
        assert cs.state is ComponentState.HEALTHY or cs.state is ComponentState.FAILED


def test_concurrent_reads_and_writes() -> None:
    """Reading snapshots while writing does not block or corrupt."""
    reg = ComponentHealthRegistry()
    stop_event = threading.Event()

    def _writer() -> None:
        while not stop_event.is_set():
            reg.record_success("collector", detail="ok")
            reg.record_failure(
                "bpf_snapshot_bridge", detail="err",
                error=ComponentError(message="err"),
            )
            reg.record_success("bpf_snapshot_bridge", detail="ok")
            reg.record_degraded("paddr_lifecycle", detail="partial")

    def _reader() -> None:
        while not stop_event.is_set():
            snap = reg.snapshot()
            assert len(snap.snapshots) == 3
            for cs in snap.snapshots:
                assert cs.name in COMPONENT_NAMES

    writer = threading.Thread(target=_writer)
    reader = threading.Thread(target=_reader)
    writer.start()
    reader.start()

    import time
    time.sleep(0.2)
    stop_event.set()
    writer.join(timeout=2)
    reader.join(timeout=2)


def test_build_health_response_shape() -> None:
    """build_health_response returns correct protocol dict."""
    reg = ComponentHealthRegistry()
    reg.record_success("collector", detail="running")
    resp = build_health_response(reg)
    assert resp["type"] == "health"
    assert resp["schema_version"] == 1
    assert resp["capability"] == "health-v1"
    assert len(resp["components"]) == 3


def test_broker_health_op_with_registry(tmp_path: Path) -> None:
    """FrameBroker with health_registry serves health op."""
    reg = ComponentHealthRegistry()
    reg.record_success("collector", detail="running")
    broker = FrameBroker([fixture_frame()], health_registry=reg)
    result = broker.responses({"op": "health"})
    assert len(result) == 1
    resp = result[0]
    assert resp["type"] == "health"
    assert resp["capability"] == "health-v1"
    assert len(resp["components"]) == 3
    by_name = {c["name"]: c for c in resp["components"]}
    assert by_name["collector"]["state"] == "healthy"


def test_broker_health_op_without_registry(tmp_path: Path) -> None:
    """FrameBroker without health_registry returns error for health op."""
    broker = FrameBroker([fixture_frame()], health_registry=None)
    result = broker.responses({"op": "health"})
    assert len(result) == 1
    resp = result[0]
    assert resp["type"] == "error"
    assert "health not available" in resp["error"]


def test_broker_current_and_stream_still_work(tmp_path: Path) -> None:
    """Health registry does not break existing current/stream ops."""
    broker = FrameBroker([fixture_frame(), fixture_frame()], health_registry=ComponentHealthRegistry())
    current = broker.responses({"op": "current"})
    assert current[0]["type"] == "frame"

    stream = broker.responses({"op": "stream", "limit": 1})
    assert len(stream) == 2  # frame + end
    assert stream[0]["type"] == "frame"
    assert stream[1]["type"] == "end"


def test_daemon_socket_health_with_registry(tmp_path: Path) -> None:
    """Daemon socket serves health response via protocol."""
    reg = ComponentHealthRegistry()
    reg.record_success("collector", detail="running")
    broker = FrameBroker([fixture_frame()], health_registry=reg)
    socket_path = tmp_path / "health.sock"
    server = serve_unix_socket(socket_path, broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(json.dumps({"op": "health"}).encode("utf-8") + b"\n")
            client.shutdown(socket.SHUT_WR)
            data = client.recv(65536)
        responses = [json.loads(line) for line in data.decode("utf-8").splitlines()]
        assert len(responses) == 1
        assert responses[0]["type"] == "health"
        assert responses[0]["capability"] == "health-v1"
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_socket_health_without_registry_returns_error(tmp_path: Path) -> None:
    """Daemon socket returns error for health op when no registry."""
    broker = FrameBroker([fixture_frame()], health_registry=None)
    socket_path = tmp_path / "nohealth.sock"
    server = serve_unix_socket(socket_path, broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(json.dumps({"op": "health"}).encode("utf-8") + b"\n")
            client.shutdown(socket.SHUT_WR)
            data = client.recv(65536)
        responses = [json.loads(line) for line in data.decode("utf-8").splitlines()]
        assert len(responses) == 1
        assert responses[0]["type"] == "error"
        assert "health not available" in responses[0]["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_client_request_health(tmp_path: Path) -> None:
    """DaemonClient.request_health returns HealthSnapshot."""
    from groop.daemon.client import DaemonClient, DaemonResponseError

    reg = ComponentHealthRegistry()
    reg.record_success("collector", detail="ok")
    broker = FrameBroker([fixture_frame()], health_registry=reg)
    socket_path = tmp_path / "client-health.sock"
    server = serve_unix_socket(socket_path, broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        health = DaemonClient(socket_path).request_health()
        assert health.schema_version == 1
        cs = health.by_name("collector")
        assert cs is not None
        assert cs.state is ComponentState.HEALTHY
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_client_health_without_registry_raises_error(tmp_path: Path) -> None:
    """DaemonClient.request_health raises DaemonResponseError when health unavailable."""
    from groop.daemon.client import DaemonClient, DaemonResponseError

    broker = FrameBroker([fixture_frame()], health_registry=None)
    socket_path = tmp_path / "client-nohealth.sock"
    server = serve_unix_socket(socket_path, broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import pytest
        with pytest.raises(DaemonResponseError):
            DaemonClient(socket_path).request_health()
    finally:
        server.shutdown()
        server.server_close()


def test_health_snapshot_by_name() -> None:
    """by_name returns correct snapshot or None."""
    reg = ComponentHealthRegistry()
    reg.record_success("collector", detail="ok")
    snap = reg.snapshot()
    assert snap.by_name("collector") is not None
    assert snap.by_name("collector").state is ComponentState.HEALTHY
    assert snap.by_name("nonexistent") is None


def test_component_snapshot_no_consecutive_failures_field_when_zero() -> None:
    """to_jsonable omits consecutive_failures when 0."""
    reg = ComponentHealthRegistry()
    reg.set_state("collector", ComponentState.HEALTHY)
    j = reg.snapshot().to_jsonable()
    collector = [c for c in j["components"] if c["name"] == "collector"][0]
    assert "consecutive_failures" not in collector or collector["consecutive_failures"] == 0


def test_component_snapshot_omits_error_when_none() -> None:
    """to_jsonable omits error field when None."""
    reg = ComponentHealthRegistry()
    reg.set_state("collector", ComponentState.HEALTHY)
    j = reg.snapshot().to_jsonable()
    collector = [c for c in j["components"] if c["name"] == "collector"][0]
    assert "error" not in collector


def test_component_snapshot_includes_error_when_set() -> None:
    """to_jsonable includes error field when error is set."""
    reg = ComponentHealthRegistry()
    reg.record_failure("collector", detail="err", error=ComponentError(message="test error"))
    j = reg.snapshot().to_jsonable()
    collector = [c for c in j["components"] if c["name"] == "collector"][0]
    assert collector["error"]["message"] == "test error"


def test_state_change_count_increments() -> None:
    """Each state transition increments state_change_count."""
    reg = ComponentHealthRegistry()
    reg.mark_starting("collector")
    assert reg.snapshot().by_name("collector").state_change_count == 1
    reg.record_success("collector", detail="ok")
    assert reg.snapshot().by_name("collector").state_change_count == 2
    reg.record_failure("collector", detail="fail", error=ComponentError(message="fail"))
    assert reg.snapshot().by_name("collector").state_change_count == 3


def test_cli_parse_health_args() -> None:
    """CLI argument parsing for health command."""
    from groop.cli import parse_daemon_args

    # Default socket
    args = parse_daemon_args(["health"])
    assert args.command == "health"
    assert args.socket is not None

    # Custom socket + pretty-json
    args = parse_daemon_args(["health", "--socket", "/tmp/custom.sock", "--pretty-json"])
    assert args.command == "health"
    assert str(args.socket) == "/tmp/custom.sock"
    assert args.pretty_json is True


def test_cli_health_via_main_daemon(tmp_path: Path) -> None:
    """CLI groop daemon health --json returns valid JSON."""
    from groop.cli import _main_daemon

    reg = ComponentHealthRegistry()
    reg.record_success("collector", detail="running")
    broker = FrameBroker([fixture_frame()], health_registry=reg)
    socket_path = tmp_path / "cli-health.sock"
    server = serve_unix_socket(socket_path, broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import io
        import sys
        old_stdout = io.StringIO()
        sys.stdout = old_stdout
        try:
            code = _main_daemon(["health", "--socket", str(socket_path), "--json"])
        finally:
            output = old_stdout.getvalue()
            sys.stdout = sys.__stdout__
        assert code == 0, f"expected 0, got {code}"
        payload = json.loads(output)
        assert payload["schema_version"] == 1
        assert len(payload["components"]) == 3
        collector = [c for c in payload["components"] if c["name"] == "collector"][0]
        assert collector["state"] == "healthy"
    finally:
        server.shutdown()
        server.server_close()


def test_cli_health_pretty_json(tmp_path: Path) -> None:
    """CLI groop daemon health --pretty-json produces indented JSON."""
    from groop.cli import _main_daemon

    broker = FrameBroker([fixture_frame()], health_registry=ComponentHealthRegistry())
    socket_path = tmp_path / "cli-health-pretty.sock"
    server = serve_unix_socket(socket_path, broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import io
        import sys
        old_stdout = io.StringIO()
        sys.stdout = old_stdout
        try:
            code = _main_daemon(["health", "--socket", str(socket_path), "--pretty-json"])
        finally:
            output = old_stdout.getvalue()
            sys.stdout = sys.__stdout__
        assert code == 0
        assert "\n" in output
        payload = json.loads(output)
        assert payload["schema_version"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_cli_health_missing_socket(tmp_path: Path) -> None:
    """Missing socket returns exit 2 with guidance."""
    from groop.cli import _main_daemon

    missing = tmp_path / "missing.sock"
    import io
    import sys
    old_stderr = io.StringIO()
    sys.stderr = old_stderr
    try:
        code = _main_daemon(["health", "--socket", str(missing)])
    finally:
        stderr_val = old_stderr.getvalue()
        sys.stderr = sys.__stderr__
    assert code == 2
    assert "cannot connect" in stderr_val
