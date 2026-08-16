"""Remaining runtime behavior witnesses; external services are boundary fakes."""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import handlers, runner, tester_gate
from cmru.agent import cli as agent_cli
from cmru.agent import consul_backend, protocol, reconciler
from cmru.agent.backend import EnrollmentSeed, LockHandle


class _HTTP:
    def __init__(self, payload=b"", status=200, headers=None):
        self.payload, self.status, self.headers = payload, status, headers or {}

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return self.payload


def test_consul_watch_observed_signature_and_lock_contracts():
    backend = consul_backend.ConsulBackend("http://consul", token="secret")
    desired = base64.b64encode(b'{"generation":1}').decode()
    backend._get = lambda *args, **kwargs: (200, json.dumps([{"Value": desired}]).encode(), {"X-Consul-Index": "7"})
    assert backend.watch_desired("n", "l", 3) == (b'{"generation":1}', 7)
    backend._get = lambda *args, **kwargs: (200, b"[]", {})
    assert backend.read_observed("n", "l") is None
    backend._put = lambda path, body, params=None: (
        (200, b'{"ID":"sid"}') if path == "/v1/session/create"
        else (200, b"true" if params and "acquire" in params else b""))
    lock = backend.acquire_lock("n", "l", 4)
    assert lock == LockHandle(session_id="sid", key="cmru/landscapes/l/locks/n", acquired=True)


def test_consul_enroll_restores_token_and_release_tolerates_outage(monkeypatch):
    backend = consul_backend.ConsulBackend(token="original")
    calls = []
    backend.register_service = lambda node: calls.append(("register", backend._token))
    backend.publish_observed = lambda *args: calls.append(("observed", backend._token))
    identity = backend.enroll(EnrollmentSeed("node", "land", "provision", "pub"))
    assert identity.node_id == "node" and backend._token == "original"
    assert calls == [("register", "provision"), ("observed", "provision")]
    backend._put = lambda *args, **kwargs: (_ for _ in ()).throw(consul_backend.ConsulUnavailable("down"))
    backend.release_lock(LockHandle("sid", "key", True))


def test_consul_read_paths_fail_closed_on_bad_shape_and_status():
    backend = consul_backend.ConsulBackend()
    backend._get = lambda *args, **kwargs: (200, b'{"not":"list"}', {})
    with pytest.raises(consul_backend.ConsulUnavailable, match="malformed"):
        backend.watch_desired("n", "l", 0)
    backend._get = lambda *args, **kwargs: (500, b"", {})
    assert backend.read_observed("n", "l") is None
    backend._get = lambda *args, **kwargs: (200, json.dumps([{"Value": "@@@"}]).encode(), {})
    assert backend.read_desired_sig("n", "l") is None


def test_agent_cli_enroll_and_status_use_persisted_state(monkeypatch, capsys):
    identity = SimpleNamespace(node_id="node", landscape="land", token_path=None, public_key="pub")
    backend = SimpleNamespace(enroll=lambda seed: identity)
    monkeypatch.setattr(agent_cli, "_build_backend", lambda args: backend)
    state = {}
    monkeypatch.setattr("cmru.agent.state.ensure_state_dir", lambda scope: None)
    monkeypatch.setattr("cmru.agent.state.write_node_id", lambda value, scope: state.update(node=value))
    monkeypatch.setattr("cmru.agent.state.write_identity", lambda value, scope: state.update(identity=value))
    args = SimpleNamespace(node_id="node", landscape="land", token="t", minisign_pubkey="pub", scope="user")
    assert agent_cli.cmd_enroll(args) == 0 and state["node"] == "node"
    monkeypatch.setattr("cmru.agent.state.read_node_id", lambda scope: "node")
    monkeypatch.setattr("cmru.agent.state.read_observed", lambda scope: None)
    monkeypatch.setattr("cmru.agent.state.read_current_generation", lambda scope: None)
    assert agent_cli.cmd_status(SimpleNamespace(scope="user")) == 0
    assert "node_id:            node" in capsys.readouterr().out


def test_reconciler_release_install_paths_are_atomic(monkeypatch, tmp_path):
    backend = SimpleNamespace()
    r = reconciler.Reconciler(backend, "n", "l", "user", tmp_path)
    desired = protocol.DesiredState(
        schema_version=1, generation=1, action="apply",
        release=protocol.ReleaseRef("tag", "https://manifest", "a" * 64),
        profiles=[], config_hash="h", plan_id="p", step_id="s",
    )
    existing = tmp_path / "releases" / "tag"; existing.mkdir(parents=True)
    assert r._ensure_release(desired) == existing
    existing.rmdir()
    monkeypatch.setattr(reconciler.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="bad"))
    assert r._ensure_release(desired) is None


def test_runner_helpers_cover_required_env_evidence_and_git_date(tmp_path, monkeypatch):
    pytest_line = "================= 5 passed in 0.2s ================="
    assert runner._success_evidence(["noise", pytest_line]) == "5 passed in 0.2s"
    assert runner._success_evidence(["noise"]) is None
    monkeypatch.delenv("MISSING", raising=False)
    with pytest.raises(RuntimeError, match="MISSING"):
        runner.ensure_required_env(["MISSING"])
    monkeypatch.setenv("MISSING", "present")
    runner.ensure_required_env(["MISSING"])
    assert runner.resolve_path(tmp_path, "x") == tmp_path / "x"


def test_handlers_refuse_oci_repack_and_missing_prerequisites(monkeypatch):
    with pytest.raises(SystemExit):
        handlers._reject_experimental_repack(True)
    monkeypatch.setattr(handlers.shutil, "which", lambda _: None)
    with pytest.raises(SystemExit) as error:
        handlers._check_prerequisites()
    assert error.value.code == 3


def test_tester_gate_slice_probe_distinguishes_real_transient_and_missing(monkeypatch):
    monkeypatch.setattr(tester_gate.shutil, "which", lambda _: None)
    assert tester_gate.check_slice_unit("dev.slice", "probe")[0] is None
    monkeypatch.setattr(tester_gate.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(tester_gate.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout="LoadState=loaded\nFragmentPath=\n", stderr="", returncode=0))
    ok, note = tester_gate.check_slice_unit("typo.slice", "probe")
    assert ok is False and "TRANSIENT" in note
    monkeypatch.setattr(tester_gate.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout="LoadState=loaded\nFragmentPath=/etc/systemd/system/dev.slice\n", stderr="", returncode=0))
    assert tester_gate.check_slice_unit("dev.slice", "probe")[0] is True
