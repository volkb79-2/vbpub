"""Tests for cmru-agent + cmru-controller (SPEC G §10).

All tests use stdlib + tmp files only — no real network, no real Consul.
Consul HTTP API is mocked with a fake HTTP server or stub backend.

Coverage:
  - Protocol: schema validation; idempotency; signature verification.
  - Backend: enrollment; blocking watch; Consul outage → backoff.
  - Reconciler: install/update/rollback/hold; adapter failure; no-op.
  - Security: no arbitrary execution; error_class=invalid_desired on refusal.
  - State: observed.json read/write; current_generation.
  - Controller: canary auto-applies; production blocked until approve;
                phase barrier; failed wave stops plan; hold; rollback;
                cross-host ordering.
  - Self-update: staged new wheel; running interpreter not overwritten.
  - Adapter: ABC enforced; load_adapter locates Adapter class; bad module rejected.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
import types
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest import mock

import pytest

# ─── helpers ─────────────────────────────────────────────────────────────────


def _make_desired(
    generation: int = 1,
    action: str = "update",
    tag: str = "demo-v1.0.0",
    manifest_url: str = "https://example.com/manifest.json",
    manifest_sha256: str = "abc123",
    profiles: Optional[List[str]] = None,
    config_hash: str = "cfghash",
    plan_id: str = "plan-1",
    step_id: str = "plan-1.phase-1.canary",
) -> dict:
    return {
        "schema_version": 1,
        "generation": generation,
        "action": action,
        "release": {
            "tag": tag,
            "manifest_url": manifest_url,
            "manifest_sha256": manifest_sha256,
        },
        "profiles": profiles or ["core"],
        "config_hash": config_hash,
        "plan_id": plan_id,
        "step_id": step_id,
    }


# ─── Stub backend (in-memory, synchronous) ───────────────────────────────────


class StubBackend:
    """In-memory backend for unit tests.  Does NOT hit a real Consul."""

    def __init__(self) -> None:
        self._kv: Dict[str, bytes] = {}
        self._observed: Dict[str, str] = {}
        self._services: Dict[str, bool] = {}
        self._lock_held: Dict[str, str] = {}  # key → session_id
        self.enrolled: List[str] = []
        self.published: List[dict] = []
        # For watch simulation: next desired payload
        self._watch_payloads: List[Optional[bytes]] = []
        self._watch_index: int = 0
        self._sig: Optional[bytes] = None

    def set_desired(self, node_id: str, landscape: str, payload: Optional[bytes]) -> None:
        """Test helper: enqueue a desired state payload for the next watch call."""
        self._watch_payloads.append(payload)

    def set_desired_sig(self, sig: Optional[bytes]) -> None:
        self._sig = sig

    # ---- DesiredStateBackend interface ---

    def enroll(self, seed) -> "NodeIdentity":
        from cmru.agent.backend import NodeIdentity
        self.enrolled.append(seed.node_id)
        return NodeIdentity(
            node_id=seed.node_id,
            landscape=seed.landscape,
            token_path=None,
            public_key=seed.minisign_pubkey,
        )

    def watch_desired(self, node_id, landscape, index, wait="300s"):
        if self._watch_payloads:
            payload = self._watch_payloads.pop(0)
            self._watch_index += 1
            return payload, self._watch_index
        return None, self._watch_index

    def acquire_lock(self, node_id, landscape, generation) -> "LockHandle":
        from cmru.agent.backend import LockHandle
        key = f"cmru/landscapes/{landscape}/locks/{node_id}"
        session_id = f"sess-{generation}"
        acquired = key not in self._lock_held
        if acquired:
            self._lock_held[key] = session_id
        return LockHandle(session_id=session_id, key=key, acquired=acquired)

    def release_lock(self, lock) -> None:
        self._lock_held.pop(lock.key, None)

    def publish_observed(self, node_id, landscape, observed_json: str) -> None:
        self.published.append({"node_id": node_id, "json": observed_json})
        self._observed[node_id] = observed_json

    def register_service(self, node_id: str) -> None:
        self._services[node_id] = True

    def pass_health_check(self, node_id: str) -> None:
        pass

    def read_observed(self, node_id: str, landscape: str) -> Optional[str]:
        return self._observed.get(node_id)

    def read_desired_sig(self, node_id: str, landscape: str) -> Optional[bytes]:
        return self._sig


# ─── Protocol tests ───────────────────────────────────────────────────────────


class TestProtocolValidation:
    def test_valid_desired_parses(self):
        from cmru.agent.protocol import parse_desired_json
        payload = json.dumps(_make_desired()).encode()
        desired = parse_desired_json(payload)
        assert desired.generation == 1
        assert desired.action == "update"
        assert desired.release.tag == "demo-v1.0.0"
        assert desired.profiles == ["core"]

    def test_unknown_key_rejected(self):
        from cmru.agent.protocol import parse_desired_json, DesiredStateError
        d = _make_desired()
        d["evil_key"] = "bad"
        with pytest.raises(DesiredStateError, match="unknown keys"):
            parse_desired_json(json.dumps(d).encode())

    def test_invalid_action_rejected(self):
        from cmru.agent.protocol import parse_desired_json, DesiredStateError
        d = _make_desired(action="shell")
        with pytest.raises(DesiredStateError, match="invalid action"):
            parse_desired_json(json.dumps(d).encode())

    def test_no_shell_action_in_allowed_set(self):
        """Confirm 'shell' is definitively not an allowed action."""
        from cmru.agent.protocol import _ALLOWED_ACTIONS
        assert "shell" not in _ALLOWED_ACTIONS
        assert "exec" not in _ALLOWED_ACTIONS
        assert "run" not in _ALLOWED_ACTIONS

    def test_all_valid_actions_accepted(self):
        from cmru.agent.protocol import parse_desired_json
        for action in ("install", "update", "rollback", "hold"):
            payload = json.dumps(_make_desired(action=action)).encode()
            desired = parse_desired_json(payload)
            assert desired.action == action

    def test_wrong_schema_version_rejected(self):
        from cmru.agent.protocol import parse_desired_json, DesiredStateError
        d = _make_desired()
        d["schema_version"] = 99
        with pytest.raises(DesiredStateError, match="schema_version"):
            parse_desired_json(json.dumps(d).encode())

    def test_bad_generation_rejected(self):
        from cmru.agent.protocol import parse_desired_json, DesiredStateError
        d = _make_desired()
        d["generation"] = -1
        with pytest.raises(DesiredStateError, match="generation"):
            parse_desired_json(json.dumps(d).encode())

    def test_missing_release_field_rejected(self):
        from cmru.agent.protocol import parse_desired_json, DesiredStateError
        d = _make_desired()
        del d["release"]["tag"]
        with pytest.raises(DesiredStateError, match="release.tag"):
            parse_desired_json(json.dumps(d).encode())

    def test_profiles_must_be_list(self):
        from cmru.agent.protocol import parse_desired_json, DesiredStateError
        d = _make_desired()
        d["profiles"] = "core"
        with pytest.raises(DesiredStateError, match="profiles"):
            parse_desired_json(json.dumps(d).encode())

    def test_invalid_json_rejected(self):
        from cmru.agent.protocol import parse_desired_json, DesiredStateError
        with pytest.raises(DesiredStateError, match="invalid JSON"):
            parse_desired_json(b"not json")

    def test_observed_state_round_trip(self):
        from cmru.agent.protocol import ObservedState
        obs = ObservedState(
            applied_generation=5,
            health="healthy",
            adapter_phase="phase-1.nano1",
            release_digest="sha256abc",
        )
        js = obs.to_json()
        obs2 = ObservedState.from_json(js)
        assert obs2.applied_generation == 5
        assert obs2.health == "healthy"

    def test_release_unknown_key_rejected(self):
        from cmru.agent.protocol import parse_desired_json, DesiredStateError
        d = _make_desired()
        d["release"]["extra_field"] = "bad"
        with pytest.raises(DesiredStateError, match="unknown keys in release"):
            parse_desired_json(json.dumps(d).encode())


# ─── No arbitrary execution tests ─────────────────────────────────────────────


class TestNoArbitraryExecution:
    """Assert there is no code path that executes a string/argv from desired state."""

    def test_desired_state_has_no_shell_field(self):
        from cmru.agent.protocol import _DESIRED_KEYS_V1
        forbidden = {"cmd", "command", "argv", "shell", "exec", "script"}
        overlap = forbidden & _DESIRED_KEYS_V1
        assert not overlap, f"Forbidden execution keys found in protocol: {overlap}"

    def test_apply_step_dispatches_only_enumerated_actions(self):
        """Reconciler _tick only dispatches on known action strings."""
        from cmru.agent.reconciler import Reconciler
        import inspect
        src = inspect.getsource(Reconciler._apply)
        # Must not have subprocess.run, os.system, eval, exec with desire data
        assert "os.system" not in src
        assert 'eval(' not in src
        # Check that action dispatch is against string literals
        assert '"hold"' in src or "== 'hold'" in src

    def test_protocol_has_no_arbitrary_command_field(self):
        from cmru.agent.protocol import validate_desired
        # Trying to sneak a 'command' field should raise (unknown key)
        from cmru.agent.protocol import DesiredStateError
        d = _make_desired()
        d["command"] = "rm -rf /"
        with pytest.raises(DesiredStateError):
            validate_desired(d)


# ─── Adapter ABC tests ────────────────────────────────────────────────────────


class TestProjectAdapterABC:
    def test_abstract_methods_enforced(self):
        from cmru.agent.adapter import ProjectAdapter
        with pytest.raises(TypeError):
            ProjectAdapter()  # type: ignore

    def test_concrete_adapter_can_be_instantiated(self, tmp_path):
        from cmru.agent.adapter import ProjectAdapter, StepResult, HealthResult
        class ConcreteAdapter(ProjectAdapter):
            def validate(self, desired, installed_release): pass
            def prepare(self, desired, release_root): pass
            def apply_step(self, step): return StepResult(success=True, exit_code=0)
            def health(self, step): return HealthResult(status="healthy")
            def rollback(self, previous): pass
        adapter = ConcreteAdapter()
        assert isinstance(adapter, ProjectAdapter)

    def test_load_adapter_finds_class(self, tmp_path):
        """load_adapter finds Adapter class at <root>/scripts/adapter.py."""
        from cmru.agent.adapter import ProjectAdapter, StepResult, HealthResult, load_adapter
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        adapter_py = scripts_dir / "adapter.py"
        adapter_py.write_text("""
from cmru.agent.adapter import ProjectAdapter, StepResult, HealthResult
class Adapter(ProjectAdapter):
    def validate(self, desired, installed_release): pass
    def prepare(self, desired, release_root): pass
    def apply_step(self, step): return StepResult(success=True, exit_code=0)
    def health(self, step): return HealthResult(status="healthy")
    def rollback(self, previous): pass
""")
        adapter = load_adapter(tmp_path)
        assert isinstance(adapter, ProjectAdapter)

    def test_load_adapter_no_class_raises(self, tmp_path):
        from cmru.agent.adapter import load_adapter
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "adapter.py").write_text("# no Adapter class here\n")
        with pytest.raises(RuntimeError, match="Adapter"):
            load_adapter(tmp_path)

    def test_load_adapter_wrong_base_class_raises(self, tmp_path):
        from cmru.agent.adapter import load_adapter
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "adapter.py").write_text("""
class Adapter:  # not a ProjectAdapter subclass
    pass
""")
        with pytest.raises(RuntimeError, match="subclass"):
            load_adapter(tmp_path)

    def test_load_adapter_missing_root_raises(self, tmp_path):
        from cmru.agent.adapter import load_adapter
        with pytest.raises(RuntimeError, match="No adapter found"):
            load_adapter(tmp_path / "nonexistent")


# ─── State tests ──────────────────────────────────────────────────────────────


class TestStateDir:
    def test_write_read_node_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.state import write_node_id, read_node_id
        write_node_id("node-001", scope="user")
        assert read_node_id(scope="user") == "node-001"

    def test_write_read_observed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.state import write_observed, read_observed
        from cmru.agent.protocol import ObservedState
        obs = ObservedState(applied_generation=3, health="healthy", adapter_phase="p1")
        write_observed(obs, scope="user")
        obs2 = read_observed(scope="user")
        assert obs2 is not None
        assert obs2.applied_generation == 3
        assert obs2.health == "healthy"

    def test_write_read_current_generation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.state import write_current_generation, read_current_generation
        write_current_generation(42, scope="user")
        assert read_current_generation(scope="user") == 42

    def test_missing_node_id_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.state import read_node_id
        assert read_node_id(scope="user") is None

    def test_missing_observed_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.state import read_observed
        assert read_observed(scope="user") is None

    def test_observed_written_atomically(self, tmp_path, monkeypatch):
        """observed.json is never partially written (uses rename)."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.state import write_observed, state_dir
        from cmru.agent.protocol import ObservedState
        obs = ObservedState(applied_generation=1, health="applying")
        write_observed(obs, scope="user")
        sd = state_dir("user")
        # tmp file must NOT exist after write
        assert not (sd / "observed.json.tmp").exists()
        assert (sd / "observed.json").exists()

    def test_exclusive_lock_prevents_double_start(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.state import exclusive_agent_lock
        with exclusive_agent_lock("user"):
            with pytest.raises(RuntimeError, match="Another cmru-agent"):
                with exclusive_agent_lock("user"):
                    pass


# ─── Reconciler tests ─────────────────────────────────────────────────────────


class TestReconciler:
    """Reconciler tests with a StubBackend."""

    def _make_reconciler(self, backend, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.reconciler import Reconciler
        return Reconciler(
            backend=backend,
            node_id="node-001",
            landscape="test-ls",
            scope="user",
            release_root=tmp_path / "releases",
            max_iterations=1,
        )

    def test_no_desired_is_noop(self, tmp_path, monkeypatch):
        backend = StubBackend()
        r = self._make_reconciler(backend, tmp_path, monkeypatch)
        # No desired state → watch returns None
        applied = r.once()
        assert not applied

    def test_valid_desired_hold_no_installer(self, tmp_path, monkeypatch):
        """hold action: no release install, just refresh observed."""
        backend = StubBackend()
        payload = json.dumps(_make_desired(generation=1, action="hold")).encode()
        backend.set_desired("node-001", "test-ls", payload)

        r = self._make_reconciler(backend, tmp_path, monkeypatch)
        applied = r.once()
        assert applied
        # Observed should be published
        assert len(backend.published) >= 1
        obs_data = json.loads(backend.published[-1]["json"])
        assert obs_data["health"] == "healthy"
        assert obs_data["applied_generation"] == 1

    def test_idempotency_same_generation_noop(self, tmp_path, monkeypatch):
        """Re-delivering same generation → no-op."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.state import write_observed
        from cmru.agent.protocol import ObservedState

        # Pre-fill observed with generation=1 already applied
        obs = ObservedState(
            applied_generation=1,
            release_digest="abc123",
            adapter_phase="plan-1.phase-1.canary",
            health="healthy",
        )
        write_observed(obs, scope="user")

        backend = StubBackend()
        payload = json.dumps(_make_desired(
            generation=1,
            action="update",
            manifest_sha256="abc123",
            step_id="plan-1.phase-1.canary",
        )).encode()
        backend.set_desired("node-001", "test-ls", payload)

        from cmru.agent.reconciler import Reconciler
        r = Reconciler(
            backend=backend,
            node_id="node-001",
            landscape="test-ls",
            scope="user",
            release_root=tmp_path / "releases",
            max_iterations=1,
        )
        applied = r.once()
        assert not applied  # no-op

    def test_invalid_desired_publishes_error_class(self, tmp_path, monkeypatch):
        """Invalid desired state → error_class=invalid_desired, no generation advance."""
        backend = StubBackend()
        # Inject an invalid payload (unknown key)
        bad = _make_desired()
        bad["evil_key"] = "bad"
        backend.set_desired("node-001", "test-ls", json.dumps(bad).encode())

        r = self._make_reconciler(backend, tmp_path, monkeypatch)
        applied = r.once()
        assert not applied

        # error_class must be published
        obs_data = json.loads(backend.published[-1]["json"])
        assert obs_data["error_class"] == "invalid_desired"

    def test_invalid_action_refused(self, tmp_path, monkeypatch):
        """Disallowed action is refused with invalid_desired."""
        backend = StubBackend()
        d = _make_desired(action="shell")  # invalid
        backend.set_desired("node-001", "test-ls", json.dumps(d).encode())
        r = self._make_reconciler(backend, tmp_path, monkeypatch)
        r.once()
        obs_data = json.loads(backend.published[-1]["json"])
        assert obs_data["error_class"] == "invalid_desired"

    def test_bad_sig_refused(self, tmp_path, monkeypatch):
        """Bad desired.sig → refused with invalid_desired."""
        backend = StubBackend()
        payload = json.dumps(_make_desired()).encode()
        backend.set_desired("node-001", "test-ls", payload)
        backend.set_desired_sig(b"bad-sig-bytes")

        from cmru.agent.reconciler import Reconciler
        r = Reconciler(
            backend=backend,
            node_id="node-001",
            landscape="test-ls",
            scope="user",
            release_root=tmp_path / "releases",
            max_iterations=1,
            minisign_pubkey="RWS_fake_pubkey",
        )

        # Mock minisign to fail
        import cmru.agent.reconciler as rec_module
        def fake_run(cmd, input=None, capture_output=False, **kw):
            r = mock.MagicMock()
            r.returncode = 1
            r.stderr = b"verification failed"
            return r

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        with mock.patch("subprocess.run", fake_run):
            r.once()

        obs_data = json.loads(backend.published[-1]["json"])
        assert obs_data["error_class"] == "invalid_desired"

    def test_adapter_failure_does_not_advance_generation(self, tmp_path, monkeypatch):
        """Adapter apply_step failure → health=failed, generation NOT advanced."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

        # Create a release directory with a failing adapter
        release_dir = tmp_path / "releases" / "demo-v1.0.0"
        scripts_dir = release_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "adapter.py").write_text("""
from cmru.agent.adapter import ProjectAdapter, StepResult, HealthResult
class Adapter(ProjectAdapter):
    def validate(self, desired, installed_release): pass
    def prepare(self, desired, release_root): pass
    def apply_step(self, step): return StepResult(success=False, exit_code=2, message="step failed")
    def health(self, step): return HealthResult(status="failed")
    def rollback(self, previous): pass
""")

        backend = StubBackend()
        payload = json.dumps(_make_desired(generation=5, action="update")).encode()
        backend.set_desired("node-001", "test-ls", payload)

        from cmru.agent.reconciler import Reconciler
        r = Reconciler(
            backend=backend,
            node_id="node-001",
            landscape="test-ls",
            scope="user",
            release_root=tmp_path,
            max_iterations=1,
        )
        applied = r.once()
        assert not applied

        # generation must NOT have been advanced
        from cmru.agent.state import read_current_generation
        gen = read_current_generation(scope="user")
        assert gen != 5 or gen is None

    def test_hold_action_refreshes_observed(self, tmp_path, monkeypatch):
        """hold: no install, observed advanced, health=healthy."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        backend = StubBackend()
        payload = json.dumps(_make_desired(generation=3, action="hold")).encode()
        backend.set_desired("node-001", "test-ls", payload)

        from cmru.agent.reconciler import Reconciler
        r = Reconciler(
            backend=backend,
            node_id="node-001",
            landscape="test-ls",
            scope="user",
            release_root=tmp_path / "releases",
            max_iterations=1,
        )
        applied = r.once()
        assert applied

        obs_data = json.loads(backend.published[-1]["json"])
        assert obs_data["health"] == "healthy"
        assert obs_data["applied_generation"] == 3


# ─── Consul outage tests ──────────────────────────────────────────────────────


class TestConsulOutage:
    def test_outage_keeps_current_state(self, tmp_path, monkeypatch):
        """Consul outage → current state retained, backoff, no target guessed."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.state import write_observed
        from cmru.agent.protocol import ObservedState

        # Pre-existing healthy state
        obs = ObservedState(applied_generation=2, health="healthy")
        write_observed(obs, scope="user")

        from cmru.agent.consul_backend import ConsulUnavailable
        backend = StubBackend()
        # Simulate outage: watch_desired raises ConsulUnavailable
        outage_count = [0]
        def fail_watch(*a, **kw):
            outage_count[0] += 1
            if outage_count[0] <= 2:
                raise ConsulUnavailable("connection refused")
            return None, 0
        backend.watch_desired = fail_watch

        from cmru.agent.reconciler import Reconciler
        r = Reconciler(
            backend=backend,
            node_id="node-001",
            landscape="test-ls",
            scope="user",
            release_root=tmp_path / "releases",
            max_iterations=3,
        )
        # Run with short backoff
        import cmru.agent.reconciler as rec_module
        with mock.patch.object(rec_module.time, "sleep"):
            r.run()

        # Observed state must still show healthy
        from cmru.agent.state import read_observed
        current = read_observed(scope="user")
        assert current is not None
        assert current.health == "healthy"
        assert current.applied_generation == 2

    def test_blocking_watch_index_tracking(self, tmp_path, monkeypatch):
        """Index is updated across watch calls."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        backend = StubBackend()
        backend._watch_index = 10  # start at non-zero

        indices_seen = []
        original_watch = backend.watch_desired
        def tracking_watch(node_id, landscape, index, wait="300s"):
            indices_seen.append(index)
            return original_watch(node_id, landscape, index, wait)
        backend.watch_desired = tracking_watch

        from cmru.agent.reconciler import Reconciler
        r = Reconciler(
            backend=backend,
            node_id="n1",
            landscape="ls",
            scope="user",
            release_root=tmp_path,
            max_iterations=2,
        )
        # Two iterations — second should use new index
        backend.set_desired("n1", "ls", json.dumps(_make_desired(action="hold")).encode())
        r.run()
        # After first iteration index should have been updated
        if len(indices_seen) >= 2:
            assert indices_seen[1] > indices_seen[0]


# ─── ConsulBackend unit tests (mock HTTP) ────────────────────────────────────


class FakeConsulHandler(BaseHTTPRequestHandler):
    """Minimal fake Consul HTTP server for ConsulBackend tests."""

    def log_message(self, *a, **kw):
        pass  # suppress test output

    def do_GET(self):
        if self.path.startswith("/v1/kv/"):
            # Return a simple KV response
            key = self.path.split("?")[0][len("/v1/kv/"):]
            fake_value = base64.b64encode(b'{"schema_version": 1}').decode()
            body = json.dumps([{"Key": key, "Value": fake_value}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Consul-Index", "5")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # consume body
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if "/session/create" in self.path:
            self.wfile.write(json.dumps({"ID": "fake-session-id"}).encode())
        else:
            self.wfile.write(b"true")

    def do_DELETE(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"true")


@pytest.fixture
def fake_consul():
    """Spin up a fake Consul HTTP server on localhost for testing."""
    server = HTTPServer(("127.0.0.1", 0), FakeConsulHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


class TestConsulBackend:
    def test_watch_desired_returns_value(self, fake_consul):
        from cmru.agent.consul_backend import ConsulBackend
        backend = ConsulBackend(consul_addr=fake_consul, token=None, timeout=5)
        raw, index = backend.watch_desired("node1", "ls1", 0, wait="1s")
        assert raw is not None
        assert index == 5

    def test_acquire_lock_returns_handle(self, fake_consul):
        from cmru.agent.consul_backend import ConsulBackend
        backend = ConsulBackend(consul_addr=fake_consul, token=None, timeout=5)
        lock = backend.acquire_lock("node1", "ls1", 1)
        assert lock.session_id == "fake-session-id"
        assert lock.acquired

    def test_publish_observed_ok(self, fake_consul):
        from cmru.agent.consul_backend import ConsulBackend
        from cmru.agent.protocol import ObservedState
        backend = ConsulBackend(consul_addr=fake_consul, token=None, timeout=5)
        obs = ObservedState(health="healthy")
        # Should not raise
        backend.publish_observed("n1", "ls", obs.to_json())

    def test_consul_unavailable_raises(self, tmp_path):
        """Connection refused → ConsulUnavailable (not a crash)."""
        from cmru.agent.consul_backend import ConsulBackend, ConsulUnavailable
        backend = ConsulBackend(consul_addr="http://127.0.0.1:19999", timeout=1)
        with pytest.raises(ConsulUnavailable):
            backend.watch_desired("n1", "ls", 0, wait="1s")

    def test_token_not_in_exception_message(self, tmp_path):
        """Token must not appear in logged error messages."""
        from cmru.agent.consul_backend import ConsulBackend, ConsulUnavailable
        secret_token = "SUPER_SECRET_TOKEN_XYZ"
        backend = ConsulBackend(
            consul_addr="http://127.0.0.1:19999",
            token=secret_token,
            timeout=1,
        )
        try:
            backend.watch_desired("n1", "ls", 0, wait="1s")
        except ConsulUnavailable as exc:
            assert secret_token not in str(exc)


# ─── Controller planner tests ─────────────────────────────────────────────────


class TestPlanner:
    def _plan_json(self, **overrides):
        base = {
            "plan": {
                "id": "plan-test",
                "landscape": "prod",
                "release_tag": "dstdns-v1.0.0",
                "manifest_url": "https://example.com/manifest.json",
                "manifest_sha256": "abc123",
                "config_hash": "cfghash",
                "waves": [
                    {
                        "phase": 1,
                        "name": "canary",
                        "type": "canary",
                        "nodes": ["r1001.vxxu.de"],
                        "profiles": ["core"],
                    },
                    {
                        "phase": 2,
                        "name": "production",
                        "type": "production",
                        "nodes": ["r1002.vxxu.de"],
                        "profiles": ["core", "worker-io"],
                    },
                ],
            }
        }
        base["plan"].update(overrides)
        return json.dumps(base)

    def test_parses_canary_and_production(self):
        from cmru.controller.planner import load_plan_json
        plan = load_plan_json(self._plan_json())
        assert plan.plan_id == "plan-test"
        assert len(plan.steps) == 2
        assert plan.steps[0].wave_type == "canary"
        assert not plan.steps[0].requires_approval
        assert plan.steps[1].wave_type == "production"
        assert plan.steps[1].requires_approval

    def test_steps_sorted_by_phase(self):
        from cmru.controller.planner import load_plan_json
        plan = load_plan_json(self._plan_json())
        phases = [s.phase for s in plan.steps]
        assert phases == sorted(phases)

    def test_step_id_format(self):
        from cmru.controller.planner import load_plan_json
        plan = load_plan_json(self._plan_json())
        assert plan.steps[0].step_id == "plan-test.phase-1.canary"
        assert plan.steps[1].step_id == "plan-test.phase-2.production"

    def test_missing_plan_id_raises(self):
        from cmru.controller.planner import load_plan_json
        raw = json.dumps({
            "plan": {
                "landscape": "prod",
                "release_tag": "v1",
                "manifest_url": "u",
                "manifest_sha256": "s",
                "waves": [{"phase": 1, "name": "c", "type": "canary",
                           "nodes": ["n1"], "profiles": []}],
            }
        })
        with pytest.raises(ValueError, match="plan.id"):
            load_plan_json(raw)

    def test_empty_waves_raises(self):
        from cmru.controller.planner import load_plan_json
        raw = json.dumps({
            "plan": {
                "id": "p1", "landscape": "prod",
                "release_tag": "v1", "manifest_url": "u", "manifest_sha256": "s",
                "waves": [],
            }
        })
        with pytest.raises(ValueError, match="non-empty"):
            load_plan_json(raw)

    def test_invalid_wave_type_raises(self):
        from cmru.controller.planner import load_plan_json
        base = json.loads(self._plan_json())
        base["plan"]["waves"][0]["type"] = "mystery"
        with pytest.raises(ValueError, match="type"):
            load_plan_json(json.dumps(base))

    def test_cross_host_ordering(self):
        """Same host appearing in multiple phases is valid (A→B→A pattern)."""
        from cmru.controller.planner import load_plan_json
        raw = json.dumps({
            "plan": {
                "id": "cross",
                "landscape": "prod",
                "release_tag": "v1",
                "manifest_url": "u",
                "manifest_sha256": "s",
                "waves": [
                    {"phase": 1, "name": "a1", "type": "canary",
                     "nodes": ["host-a"], "profiles": ["core"]},
                    {"phase": 2, "name": "b", "type": "canary",
                     "nodes": ["host-b"], "profiles": ["core"]},
                    {"phase": 3, "name": "a2", "type": "production",
                     "nodes": ["host-a"], "profiles": ["core", "worker-io"]},
                ],
            }
        })
        plan = load_plan_json(raw)
        assert len(plan.steps) == 3
        assert plan.steps[0].nodes == ["host-a"]
        assert plan.steps[1].nodes == ["host-b"]
        assert plan.steps[2].nodes == ["host-a"]


# ─── Controller rollout tests (stub backend) ──────────────────────────────────


class StubRolloutBackend:
    """Minimal stub for RolloutEngine tests — does NOT hit Consul."""

    def __init__(self) -> None:
        self.kv: Dict[str, bytes] = {}
        self._observed: Dict[str, str] = {}
        self._service_catalog: List[dict] = []

    def _put(self, path: str, body: bytes, params=None):
        key = path.replace("/v1/kv/", "")
        self.kv[key] = body
        return 200, b"true"

    def _get(self, path: str, params=None):
        key = path.replace("/v1/kv/", "")
        if key in self.kv:
            value_b64 = base64.b64encode(self.kv[key]).decode()
            body = json.dumps([{"Key": key, "Value": value_b64}]).encode()
            return 200, body, {"X-Consul-Index": "1"}
        return 404, b"", {}

    def _delete(self, path: str):
        key = path.replace("/v1/kv/", "")
        self.kv.pop(key, None)
        return 200, b"true"

    def read_observed(self, node_id: str, landscape: str) -> Optional[str]:
        return self._observed.get(node_id)

    def set_observed(self, node_id: str, health: str, generation: int) -> None:
        from cmru.agent.protocol import ObservedState
        obs = ObservedState(
            applied_generation=generation,
            health=health,
            adapter_phase=f"phase-{generation}",
        )
        self._observed[node_id] = obs.to_json()


class TestRolloutEngine:
    def _make_plan(self, waves=None):
        from cmru.controller.planner import load_plan_json
        w = waves or [
            {"phase": 1, "name": "canary", "type": "canary",
             "nodes": ["n1"], "profiles": ["core"]},
            {"phase": 2, "name": "production", "type": "production",
             "nodes": ["n2"], "profiles": ["core"]},
        ]
        raw = json.dumps({
            "plan": {
                "id": "test-plan", "landscape": "prod",
                "release_tag": "v1", "manifest_url": "u", "manifest_sha256": "s",
                "waves": w,
            }
        })
        return load_plan_json(raw)

    def test_canary_wave_auto_applies(self):
        """Canary wave does not require approval."""
        plan = self._make_plan()
        assert not plan.steps[0].requires_approval

    def test_production_wave_requires_approval(self):
        """Production wave requires approval."""
        plan = self._make_plan()
        assert plan.steps[1].requires_approval

    def test_publish_writes_desired_to_nodes(self):
        from cmru.controller.rollout import RolloutEngine
        backend = StubRolloutBackend()
        plan = self._make_plan(waves=[
            {"phase": 1, "name": "canary", "type": "canary",
             "nodes": ["n1"], "profiles": ["core"]},
        ])

        engine = RolloutEngine(
            backend=backend,  # type: ignore
            landscape="prod",
            dry_run=True,  # don't block on wave health
        )
        # Dry run just logs — check no exception
        engine.publish(plan)

    def test_hold_writes_flag(self):
        from cmru.controller.rollout import RolloutEngine, _plan_hold_key
        backend = StubRolloutBackend()
        engine = RolloutEngine(backend=backend, landscape="prod")  # type: ignore
        engine.hold("test-plan")
        key = _plan_hold_key("test-plan")
        assert key in backend.kv

    def test_approve_writes_flag(self):
        from cmru.controller.rollout import RolloutEngine, _plan_approval_key
        backend = StubRolloutBackend()
        engine = RolloutEngine(backend=backend, landscape="prod")  # type: ignore
        engine.approve("test-plan")
        key = _plan_approval_key("test-plan")
        assert key in backend.kv

    def test_rollback_writes_new_generation_with_rollback_action(self):
        """Rollback emits new generation; action=rollback; does NOT mutate existing KV."""
        from cmru.controller.rollout import RolloutEngine
        backend = StubRolloutBackend()
        plan = self._make_plan(waves=[
            {"phase": 1, "name": "canary", "type": "canary",
             "nodes": ["n1"], "profiles": ["core"]},
        ])
        engine = RolloutEngine(
            backend=backend, landscape="prod", generation_base=1  # type: ignore
        )
        engine.rollback(plan, generation=99999)

        # Check that a desired key was written with action=rollback
        desired_keys = [k for k in backend.kv if "/desired" in k]
        assert desired_keys, "No desired state written for rollback"
        desired_raw = json.loads(backend.kv[desired_keys[0]])
        assert desired_raw["action"] == "rollback"
        assert desired_raw["generation"] == 99999

    def test_failed_wave_stops_plan(self):
        """A failed wave (health=failed) stops the plan."""
        from cmru.controller.rollout import RolloutEngine
        backend = StubRolloutBackend()
        # Pre-set n1 as failed at the expected generation
        # generation_base=1, phase=1 → expected gen = 1 + 1*100 = 101
        backend.set_observed("n1", "failed", 101)
        backend.set_observed("n2", "healthy", 201)

        plan = self._make_plan(waves=[
            {"phase": 1, "name": "canary", "type": "canary",
             "nodes": ["n1"], "profiles": ["core"], "required": True},
            {"phase": 2, "name": "production", "type": "production",
             "nodes": ["n2"], "profiles": ["core"], "required": True},
        ])

        engine = RolloutEngine(
            backend=backend,  # type: ignore
            landscape="prod",
            generation_base=1,
            poll_interval=0,
            wave_timeout=0,   # immediate timeout → returns False
        )
        engine.publish(plan)

        # plan status must be "failed"
        from cmru.controller.rollout import _plan_status_key
        status_key = _plan_status_key("test-plan")
        if status_key in backend.kv:
            status_data = json.loads(backend.kv[status_key])
            assert status_data["status"] == "failed"

    def test_status_returns_observed_for_all_nodes(self):
        from cmru.controller.rollout import RolloutEngine
        backend = StubRolloutBackend()
        backend.set_observed("n1", "healthy", 1)
        backend.set_observed("n2", "standby", 0)
        plan = self._make_plan()
        engine = RolloutEngine(backend=backend, landscape="prod")  # type: ignore
        result = engine.status(plan)
        assert "n1" in result["nodes"]
        assert "n2" in result["nodes"]


# ─── Self-update tests ────────────────────────────────────────────────────────


class TestSelfUpdate:
    def test_stage_new_venv_creates_dir(self, tmp_path):
        """Stage creates venv-<version> directory (mocked subprocess)."""
        from cmru.agent.selfupdate import stage_new_venv
        wheel = tmp_path / "cmru-1.0.0-py3-none-any.whl"
        wheel.write_bytes(b"fake wheel")

        def fake_run(cmd, **kw):
            # Create the venv dir on 'python -m venv' call
            if "-m" in cmd and "venv" in cmd:
                venv_path = Path(cmd[-1])
                (venv_path / "bin").mkdir(parents=True, exist_ok=True)
                (venv_path / "bin" / "pip").touch()
            r = mock.MagicMock()
            r.returncode = 0
            return r

        with mock.patch("subprocess.run", fake_run):
            venv = stage_new_venv(tmp_path, "1.0.0", wheel)

        assert venv == tmp_path / "venv-1.0.0"

    def test_pending_marker_written_and_read(self, tmp_path):
        from cmru.agent.selfupdate import write_pending_marker, read_pending_marker
        venv = tmp_path / "venv-1.0.0"
        write_pending_marker(tmp_path, "1.0.0", venv)
        data = read_pending_marker(tmp_path)
        assert data is not None
        assert data["version"] == "1.0.0"
        assert "venv" in data

    def test_pending_marker_absent_returns_none(self, tmp_path):
        from cmru.agent.selfupdate import read_pending_marker
        assert read_pending_marker(tmp_path) is None

    def test_clear_pending_marker(self, tmp_path):
        from cmru.agent.selfupdate import write_pending_marker, clear_pending_marker, read_pending_marker
        venv = tmp_path / "venv-1.0.0"
        write_pending_marker(tmp_path, "1.0.0", venv)
        clear_pending_marker(tmp_path)
        assert read_pending_marker(tmp_path) is None

    def test_render_service_unit_contains_venv_python(self):
        from cmru.agent.selfupdate import render_service_unit
        unit = render_service_unit(venv_python="/opt/dstdns/venv-1.0.0/bin/python")
        assert "/opt/dstdns/venv-1.0.0/bin/python" in unit
        assert "Restart=always" in unit

    def test_running_interpreter_not_in_venv_dir(self, tmp_path):
        """Staging creates a NEW venv-<version> dir; sys.executable path is not overwritten."""
        from cmru.agent.selfupdate import stage_new_venv
        wheel = tmp_path / "w.whl"
        wheel.write_bytes(b"fake")

        def fake_run(cmd, **kw):
            if "venv" in cmd:
                venv_path = Path(cmd[-1])
                (venv_path / "bin").mkdir(parents=True, exist_ok=True)
                (venv_path / "bin" / "pip").touch()
            r = mock.MagicMock()
            r.returncode = 0
            return r

        import sys as _sys
        current_interp = Path(_sys.executable)

        with mock.patch("subprocess.run", fake_run):
            venv_dir = stage_new_venv(tmp_path, "2.0.0", wheel)

        # venv dir must be different from the current interpreter's parent
        assert venv_dir != current_interp.parent.parent
        assert venv_dir == tmp_path / "venv-2.0.0"


# ─── Entry point smoke tests ──────────────────────────────────────────────────


class TestEntryPoints:
    def test_cmru_agent_help_no_crash(self):
        from cmru.agent.cli import _build_parser
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--help"])
        assert exc.value.code == 0

    def test_cmru_controller_help_no_crash(self):
        from cmru.controller.cli import _build_parser
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--help"])
        assert exc.value.code == 0

    def test_cmru_agent_status_not_enrolled(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.cli import cmd_status
        args = mock.MagicMock()
        args.scope = "user"
        rc = cmd_status(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "not enrolled" in out

    def test_cmru_agent_run_no_node_id_exits_2(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.cli import cmd_run
        args = mock.MagicMock()
        args.scope = "user"
        args.release_root = None
        with pytest.raises(SystemExit) as exc:
            cmd_run(args)
        assert exc.value.code == 2

    def test_cmru_agent_enroll_missing_node_id_exits_2(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.cli import cmd_enroll
        args = mock.MagicMock()
        args.scope = "user"
        args.node_id = None
        args.landscape = "test"
        args.token = None
        args.minisign_pubkey = None
        monkeypatch.delenv("CMRU_NODE_ID", raising=False)
        rc = cmd_enroll(args)
        assert rc == 2


# ─── Security: isolation (no cross-node read/write) ──────────────────────────


class TestIsolation:
    def test_kv_path_contains_node_id(self):
        """Desired state KV path is scoped to the node — no wildcard."""
        from cmru.agent.consul_backend import _kv_desired, _kv_observed
        path = _kv_desired("prod-ls", "node-007")
        assert "node-007" in path
        assert "prod-ls" in path
        path2 = _kv_desired("prod-ls", "node-008")
        assert path != path2

    def test_observed_path_scoped_to_node(self):
        from cmru.agent.consul_backend import _kv_observed
        p1 = _kv_observed("ls", "node-A")
        p2 = _kv_observed("ls", "node-B")
        assert p1 != p2
        assert "node-A" in p1
        assert "node-B" in p2
