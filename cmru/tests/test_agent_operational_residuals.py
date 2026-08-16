"""Contract witnesses for residual agent/controller operational branches."""
from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from cmru.agent import consul_backend, reconciler, state
from cmru.agent.backend import LockHandle
from cmru.controller.planner import LandscapePlan, PlanStep
from cmru.controller.rollout import RolloutEngine


class _HTTPResponse:
    def __init__(self, status=200, body=b"ok", headers=None):
        self.status, self.body, self.headers = status, body, headers or {}

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return self.body


def _step(**overrides):
    values = dict(
        plan_id="plan", wave_name="wave", phase=1, wave_type="canary", nodes=["n1"],
        profiles=["core"], release_tag="demo-v1", manifest_url="https://m",
        manifest_sha256="a" * 64, config_hash="h", step_id="plan.wave",
        required=True, requires_approval=False,
    )
    values.update(overrides)
    return PlanStep(**values)


def _plan():
    return LandscapePlan("plan", "land", [_step()])


def test_consul_http_boundaries_return_status_or_typed_outage(monkeypatch):
    backend = consul_backend.ConsulBackend("http://consul", token="secret")
    monkeypatch.setattr(consul_backend.urllib.request, "urlopen", lambda *a, **k: _HTTPResponse(201, b"ok"))
    assert backend._put("/v1/x", b"x", {"a": "b"}) == (201, b"ok")
    assert backend._delete("/v1/x") == (201, b"ok")
    error = urllib.error.HTTPError("u", 409, "conflict", {}, io.BytesIO(b"bad"))
    monkeypatch.setattr(consul_backend.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(error))
    assert backend._put("/v1/x", b"x") == (409, b"bad")
    monkeypatch.setattr(consul_backend.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("offline")))
    with pytest.raises(consul_backend.ConsulUnavailable, match="offline"):
        backend._delete("/v1/x")


def test_consul_observed_publish_and_service_paths_preserve_http_outcome(caplog):
    backend = consul_backend.ConsulBackend()
    calls = []
    backend._put = lambda path, body, params=None: (calls.append((path, body, params)) or (503, b""))
    backend.publish_observed("n", "land", "{}")
    backend.register_service("n")
    backend.pass_health_check("n")
    assert [item[0] for item in calls] == [
        "/v1/kv/cmru/landscapes/land/nodes/n/observed",
        "/v1/agent/service/register",
        "/v1/agent/check/pass/service:cmru-agent-n",
    ]
    assert "Failed to publish observed" in caplog.text


def test_reconciler_run_backs_off_unexpected_errors_and_stops_at_limit(monkeypatch):
    rec = reconciler.Reconciler(SimpleNamespace(), "n", "l", max_iterations=2)
    calls = []
    monkeypatch.setattr(rec, "_tick", lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    monkeypatch.setattr(reconciler.time, "sleep", lambda delay: calls.append(delay))
    rec.run()
    assert calls == [2, 4]


def test_rollout_release_hold_status_and_failed_wave_are_data_contracts(monkeypatch):
    calls = []
    observed = {"n1": json.dumps({"health": "failed", "applied_generation": 101})}
    backend = SimpleNamespace(
        _put=lambda path, body: (calls.append(("put", path, body)) or (200, b"")),
        _delete=lambda path: calls.append(("delete", path)),
        _get=lambda path: (404, b"", {}),
        read_observed=lambda node, landscape: observed.get(node),
    )
    engine = RolloutEngine(backend, "land", generation_base=1, poll_interval=0, wave_timeout=1)
    engine.release_hold("plan")
    status = engine.status(_plan())
    assert calls[0] == ("delete", "/v1/kv/cmru/controller/plans/plan/hold")
    assert status["nodes"]["n1"]["health"] == "failed"
    monkeypatch.setattr(reconciler.time, "sleep", lambda _: None)
    assert engine._wait_for_wave("plan", _step()) is False


def test_rollout_hold_unexpected_status_does_not_block_and_writes_failed_status():
    calls = []
    backend = SimpleNamespace(
        _put=lambda path, body: (calls.append((path, body)) or (200, b"")),
        _get=lambda path: (500, b"bad", {}),
        read_observed=lambda *_: None,
    )
    engine = RolloutEngine(backend, "land")
    engine._check_hold("plan")
    assert calls == []


def test_reconciler_publish_error_tolerates_backend_outage_after_local_state(monkeypatch, tmp_path):
    backend = SimpleNamespace(publish_observed=lambda *a: (_ for _ in ()).throw(consul_backend.ConsulUnavailable("down")))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    rec = reconciler.Reconciler(backend, "n", "l", release_root=tmp_path)
    monkeypatch.setattr(reconciler, "read_observed", lambda scope: None)
    reconciler.write_observed(reconciler.ObservedState(health="failed"), "user")
    rec._publish_error("invalid_desired", "bad input")
    stored = state.read_observed("user")
    assert stored is not None and stored.error_class == "invalid_desired"
