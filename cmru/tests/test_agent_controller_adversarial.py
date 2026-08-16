"""Behavioural boundary tests for the agent/controller public seams.

These tests deliberately exercise protocol, persistence, HTTP and rollout
boundaries.  They use fakes only at the external boundary and assert the
observable state or refusal, so a plausible branch deletion does not remain
green.
"""
from __future__ import annotations

import base64
import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

_BASE = importlib.util.spec_from_file_location(
    "test_agent_controller", Path(__file__).with_name("test_agent_controller.py")
)
assert _BASE and _BASE.loader
_BASE_MOD = importlib.util.module_from_spec(_BASE)
_BASE.loader.exec_module(_BASE_MOD)
StubBackend = _BASE_MOD.StubBackend
StubRolloutBackend = _BASE_MOD.StubRolloutBackend
_make_desired = _BASE_MOD._make_desired


def _args(**kw):
    values = dict(
        scope="user", node_id=None, landscape=None, token=None,
        minisign_pubkey=None, release_root=None, consul_addr=None,
        plan=None, to_tag=None, generation=None, dry_run=False,
        log_level="INFO",
    )
    values.update(kw)
    return SimpleNamespace(**values)


class TestProtocolAndPersistenceBoundaries:
    @pytest.mark.parametrize("field,value", [
        ("schema_version", "1"), ("generation", True), ("action", 1),
        ("release", []), ("config_hash", None), ("plan_id", 3),
        ("step_id", []),
    ])
    def test_wrong_field_types_are_refused(self, field, value):
        from cmru.agent.protocol import DesiredStateError, parse_desired_json
        raw = _make_desired()
        raw[field] = value
        with pytest.raises(DesiredStateError):
            parse_desired_json(json.dumps(raw).encode())

    @pytest.mark.parametrize("name", ["manifest_url", "manifest_sha256"])
    def test_empty_release_fact_is_refused(self, name):
        from cmru.agent.protocol import DesiredStateError, parse_desired_json
        raw = _make_desired()
        raw["release"][name] = ""
        with pytest.raises(DesiredStateError, match=name):
            parse_desired_json(json.dumps(raw).encode())

    def test_observed_malformed_and_unknown_data_have_distinct_safe_outcomes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent import state
        state.ensure_state_dir()
        (state.state_dir() / "observed.json").write_text("not-json")
        (state.state_dir() / "current_generation").write_text("not-an-int")
        assert state.read_observed() is None
        assert state.read_current_generation() is None
        (state.state_dir() / "identity.json").write_text("[]")
        assert state.read_identity() == []

    def test_lock_handle_release_delegates_to_backend(self):
        from cmru.agent.backend import LockHandle
        seen = []
        lock = LockHandle("session", "key", True)
        lock.release(SimpleNamespace(release_lock=lambda got: seen.append(got)), "node")
        assert seen == [lock]


class TestAdapterBoundaries:
    def test_fallback_adapter_module_is_loaded(self, tmp_path):
        from cmru.agent.adapter import ProjectAdapter, StepResult, HealthResult, load_adapter
        (tmp_path / "adapter.py").write_text(
            "from cmru.agent.adapter import ProjectAdapter, StepResult, HealthResult\n"
            "class Adapter(ProjectAdapter):\n"
            " def validate(self,d,r): pass\n"
            " def prepare(self,d,r): pass\n"
            " def apply_step(self,s): return StepResult(True,0)\n"
            " def health(self,s): return HealthResult('healthy')\n"
            " def rollback(self,p): pass\n"
        )
        adapter = load_adapter(tmp_path)
        assert isinstance(adapter, ProjectAdapter)
        assert adapter.apply_step(None).success

    def test_missing_class_and_constructor_failure_are_rejected(self, tmp_path):
        from cmru.agent.adapter import load_adapter
        (tmp_path / "adapter.py").write_text("VALUE = 1\n")
        with pytest.raises(RuntimeError, match="does not define"):
            load_adapter(tmp_path)
        (tmp_path / "adapter.py").write_text("from cmru.agent.adapter import ProjectAdapter\nclass Adapter(ProjectAdapter):\n def __init__(self): raise RuntimeError('bad')\n")
        with pytest.raises(RuntimeError, match="instantiate"):
            load_adapter(tmp_path)

    @pytest.mark.parametrize("source,needle", [
        ("raise RuntimeError('boom')", "Failed to execute adapter"),
        ("class Adapter: pass", "does not subclass"),
    ])
    def test_adapter_module_execution_and_type_errors_are_wrapped(self, tmp_path, source, needle):
        from cmru.agent.adapter import load_adapter
        (tmp_path / "adapter.py").write_text(source)
        with pytest.raises(RuntimeError, match=needle):
            load_adapter(tmp_path)


class TestConsulHttpContracts:
    def test_http_statuses_and_payload_shapes_fail_closed(self, monkeypatch):
        from cmru.agent.consul_backend import ConsulBackend, ConsulUnavailable
        backend = ConsulBackend("http://consul", token="secret")
        assert backend._headers()["X-Consul-Token"] == "secret"
        responses = iter([
            (404, b"", {"X-Consul-Index": "9"}),
            (500, b"", {}),
            (200, b"{}", {}),
            (200, b"[]", {}),
            (200, json.dumps([{"Value": None}]).encode(), {}),
            (200, json.dumps([{"Value": base64.b64encode(b"x").decode()}]).encode(), {}),
        ])
        monkeypatch.setattr(backend, "_get", lambda *a, **k: next(responses))
        assert backend.watch_desired("n", "l", 1)[0] is None
        with pytest.raises(ConsulUnavailable, match="HTTP 500"):
            backend.watch_desired("n", "l", 1)
        with pytest.raises(ConsulUnavailable, match="malformed"):
            backend.watch_desired("n", "l", 1)
        # A JSON array is still malformed when its entry is not an object;
        # this must not turn into an indexing/attribute exception.
        with pytest.raises(ConsulUnavailable, match="entry is not an object"):
            backend._get = lambda *a, **k: (200, b"[1]", {})
            backend.watch_desired("n", "l", 1)
        # Invalid base64 is a transport refusal, not arbitrary decoded data.
        backend._get = lambda *a, **k: (200, b'[{"Value":"@@@@"}]', {})
        with pytest.raises(ConsulUnavailable, match="invalid base64"):
            backend.watch_desired("n", "l", 1)
        backend._get = lambda *a, **k: next(responses)
        assert backend.watch_desired("n", "l", 1)[0] is None
        assert backend.watch_desired("n", "l", 1)[0] is None
        assert backend.watch_desired("n", "l", 1)[0] == b"x"

    @pytest.mark.parametrize("method", ["read_observed", "read_desired_sig"])
    def test_readers_return_none_for_http_error_and_bad_json(self, monkeypatch, method):
        from cmru.agent.consul_backend import ConsulBackend
        backend = ConsulBackend("http://consul")
        for response in [(500, b"", {}), (200, b"not-json", {}),
                          (200, b"[]", {}), (200, json.dumps([{"Value": None}]).encode(), {})]:
            monkeypatch.setattr(backend, "_get", lambda *a, response=response, **k: response)
            assert getattr(backend, method)("n", "l") is None

    def test_enroll_restores_original_token_and_publishes_standby(self, monkeypatch):
        from cmru.agent.backend import EnrollmentSeed
        from cmru.agent.consul_backend import ConsulBackend
        backend = ConsulBackend("http://consul", token="old")
        calls = []
        monkeypatch.setattr(backend, "register_service", lambda node: calls.append(("service", node, backend._token)))
        monkeypatch.setattr(backend, "publish_observed", lambda node, landscape, raw: calls.append(("observed", node, landscape, raw, backend._token)))
        ident = backend.enroll(EnrollmentSeed("n", "l", "provision", "pub"))
        assert ident.node_id == "n" and backend._token == "old"
        assert calls[0][:3] == ("service", "n", "provision")
        assert json.loads(calls[1][3])["message"] == "standby"

    def test_lock_and_release_status_failures_are_explicit(self, monkeypatch):
        from cmru.agent.consul_backend import ConsulBackend, ConsulUnavailable
        backend = ConsulBackend("http://consul")
        calls = []
        def put(path, body, params=None):
            calls.append((path, body, params))
            if path == "/v1/session/create":
                return 500, b""
            return 200, b"false"
        monkeypatch.setattr(backend, "_put", put)
        from cmru.agent.consul_backend import ConsulUnavailable
        with pytest.raises(ConsulUnavailable, match="session/create"):
            backend.acquire_lock("n", "l", 2)
        # A non-true lock response is an acquired=False handle, not success.
        monkeypatch.setattr(backend, "_put", lambda path, body, params=None: (200, b'{"ID":"sid"}') if path == "/v1/session/create" else (200, b"false"))
        lock = backend.acquire_lock("n", "l", 2)
        assert not lock.acquired


class TestReconcilerBehaviour:
    def _r(self, tmp_path, monkeypatch, backend, **kw):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.reconciler import Reconciler
        return Reconciler(backend, "n", "l", release_root=tmp_path, max_iterations=1, **kw)

    def _release(self, tmp_path, adapter_body):
        p = tmp_path / "releases" / "v1" / "scripts"
        p.mkdir(parents=True)
        (p / "adapter.py").write_text(adapter_body)

    def test_update_success_persists_generation_and_degraded_health(self, tmp_path, monkeypatch):
        self._release(tmp_path, """
from cmru.agent.adapter import ProjectAdapter, StepResult, HealthResult
class Adapter(ProjectAdapter):
 def validate(self,d,r): assert r.name == 'v1'
 def prepare(self,d,r): pass
 def apply_step(self,s): return StepResult(True, 0)
 def health(self,s): return HealthResult('degraded')
 def rollback(self,p): pass
""")
        b = StubBackend(); b.set_desired("n", "l", json.dumps(_make_desired(tag="v1", generation=4)).encode())
        assert self._r(tmp_path, monkeypatch, b).once()
        from cmru.agent.state import read_current_generation, read_observed
        assert read_current_generation() == 4
        assert read_observed().health == "degraded"

    def test_missing_release_and_bad_adapter_publish_refusal_without_advance(self, tmp_path, monkeypatch):
        from cmru.agent.state import read_observed
        for body in [None, "class Adapter: pass"]:
            if body is not None:
                self._release(tmp_path, body)
            b = StubBackend(); b.set_desired("n", "l", json.dumps(_make_desired(tag="v1")).encode())
            with mock.patch("cmru.agent.reconciler.subprocess.run", return_value=SimpleNamespace(returncode=1, stderr="no")):
                assert not self._r(tmp_path, monkeypatch, b).once()
            assert read_observed().health == "failed"
            # clean the attempted fixture for next path
            import shutil
            if (tmp_path / "releases").exists():
                shutil.rmtree(tmp_path / "releases")

    def test_rollback_success_and_failure_do_not_skip_state(self, tmp_path, monkeypatch):
        self._release(tmp_path, """
from cmru.agent.adapter import ProjectAdapter, StepResult, HealthResult
class Adapter(ProjectAdapter):
 def validate(self,d,r): pass
 def prepare(self,d,r): pass
 def apply_step(self,s): return StepResult(True, 0)
 def health(self,s): return HealthResult('healthy')
 def rollback(self,p): self.called = True
""")
        b = StubBackend(); b.set_desired("n", "l", json.dumps(_make_desired(tag="v1", action="rollback", generation=7)).encode())
        assert self._r(tmp_path, monkeypatch, b).once()
        assert json.loads(b.published[-1]["json"])["applied_generation"] == 7

    def test_lock_retry_is_bounded_and_no_action_when_unavailable(self, tmp_path, monkeypatch):
        b = StubBackend(); b.set_desired("n", "l", json.dumps(_make_desired()).encode())
        from cmru.agent.backend import LockHandle
        b.acquire_lock = lambda *a: LockHandle("s", "k", False)
        with mock.patch("cmru.agent.reconciler.time.sleep") as sleep:
            assert not self._r(tmp_path, monkeypatch, b).once()
        assert sleep.call_count == 4

    def test_adapter_exception_and_rollback_exception_are_persisted_as_failure(self, tmp_path, monkeypatch):
        self._release(tmp_path, """
from cmru.agent.adapter import ProjectAdapter, StepResult, HealthResult
class Adapter(ProjectAdapter):
 def validate(self,d,r): raise ValueError('bad validation')
 def prepare(self,d,r): pass
 def apply_step(self,s): return StepResult(True, 0)
 def health(self,s): raise RuntimeError('health unavailable')
 def rollback(self,p): raise RuntimeError('rollback bad')
""")
        for action in ("update", "rollback"):
            b = StubBackend(); b.set_desired("n", "l", json.dumps(_make_desired(tag="v1", action=action)).encode())
            assert not self._r(tmp_path, monkeypatch, b).once()
            assert json.loads(b.published[-1]["json"])["health"] == "failed"

    def test_staged_release_is_used_and_cmru_get_missing_is_fail_closed(self, tmp_path, monkeypatch):
        self._release(tmp_path, """
from cmru.agent.adapter import ProjectAdapter, StepResult, HealthResult
class Adapter(ProjectAdapter):
 def validate(self,d,r): pass
 def prepare(self,d,r): pass
 def apply_step(self,s): return StepResult(True, 0)
 def health(self,s): return HealthResult('healthy')
 def rollback(self,p): pass
""")
        b = StubBackend(); b.set_desired("n", "l", json.dumps(_make_desired(tag="v1")).encode())
        # Existing release bypasses installer completely.
        assert self._r(tmp_path, monkeypatch, b).once()
        from cmru.agent.reconciler import Reconciler
        r = Reconciler(b, "n", "l", release_root=tmp_path)
        missing = mock.Mock(); missing.release = mock.Mock(manifest_sha256="s", tag="absent", manifest_url="u")
        monkeypatch.setattr("cmru.agent.reconciler.subprocess.run", mock.Mock(side_effect=FileNotFoundError))
        assert r._ensure_release(SimpleNamespace(release=missing.release)) is None


class TestCliRefusalAndDispatch:
    def test_agent_parser_rejects_unknown_and_accepts_global_options(self):
        from cmru.agent.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["--scope", "system", "once", "--release-root", "/r"])
        assert args.scope == "system" and args.verb == "once"
        with pytest.raises(SystemExit):
            parser.parse_args(["not-a-verb"])

    def test_agent_once_refuses_identity_without_landscape(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent.state import write_node_id, write_identity
        write_node_id("n"); write_identity({"node_id": "n", "landscape": ""})
        from cmru.agent.cli import cmd_once
        assert cmd_once(_args()) == 2
        assert "landscape" in capsys.readouterr().err

    def test_controller_commands_report_missing_plan_and_backend_errors(self, tmp_path, monkeypatch, capsys):
        from cmru.controller import cli
        assert cli.cmd_publish(_args(plan=str(tmp_path / "missing"))) == 2
        assert cli.cmd_rollback(_args(plan=str(tmp_path / "missing"))) == 2
        args = _args(plan="p")
        assert cli.cmd_approve(args) == 1
        assert cli.cmd_hold(args) == 1
        assert "failed" in capsys.readouterr().err


class TestPlannerAndRolloutBoundaries:
    def _valid(self):
        return {"plan": {"id": "p", "landscape": "l", "release_tag": "v",
            "manifest_url": "u", "manifest_sha256": "s", "waves":
            [{"phase": 1, "name": "w", "type": "canary", "nodes": ["n"], "profiles": []}]}}

    @pytest.mark.parametrize("mutator,needle", [
        (lambda p: p["plan"].update(landscape=""), "landscape"),
        (lambda p: p["plan"].update(release_tag=""), "release_tag"),
        (lambda p: p["plan"].update(waves=["bad"]), "table"),
        (lambda p: p["plan"].update(waves=[{"phase": 0, "name": "w", "nodes": ["n"]}]), "positive"),
        (lambda p: p["plan"].update(waves=[{"phase": 1, "name": "", "nodes": ["n"]}]), "name"),
        (lambda p: p["plan"].update(waves=[{"phase": 1, "name": "w", "nodes": [""]}]), "nodes"),
        (lambda p: p["plan"].update(waves=[{"phase": 1, "name": "w", "nodes": ["n"], "profiles": "x"}]), "profiles"),
    ])
    def test_plan_schema_rejects_malformed_boundary(self, mutator, needle):
        from cmru.controller.planner import load_plan_json
        raw = self._valid(); mutator(raw)
        with pytest.raises(ValueError, match=needle):
            load_plan_json(json.dumps(raw))

    def test_rollout_waits_for_health_then_writes_complete_status(self, monkeypatch):
        from cmru.controller.rollout import RolloutEngine, _plan_status_key
        from cmru.controller.planner import load_plan_json
        b = StubRolloutBackend(); b.set_observed("n", "healthy", 101)
        raw = self._valid(); plan = load_plan_json(json.dumps(raw))
        engine = RolloutEngine(b, "l", poll_interval=0, wave_timeout=1)
        engine.publish(plan)
        assert json.loads(b.kv[_plan_status_key("p")])["status"] == "complete"

    def test_rollout_dry_run_does_not_write_approval_or_hold(self):
        from cmru.controller.rollout import RolloutEngine
        b = StubRolloutBackend(); e = RolloutEngine(b, "l", dry_run=True)
        e.approve("p"); e.hold("p"); e.release_hold("p")
        assert not b.kv

    def test_status_reports_malformed_and_missing_observed(self):
        from cmru.controller.rollout import RolloutEngine
        from cmru.controller.planner import load_plan_json
        b = StubRolloutBackend(); b._observed["n"] = "bad-json"
        plan = load_plan_json(json.dumps(self._valid()))
        result = RolloutEngine(b, "l").status(plan)
        assert result["nodes"]["n"]["health"] == "unknown"


class TestRemainingPublicPaths:
    def test_state_user_default_and_system_path_are_deterministic(self, tmp_path, monkeypatch):
        from cmru.agent import state
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert state.state_dir("user") == tmp_path / ".local/state/cmru-agent"
        monkeypatch.setattr(state, "_SYSTEM_STATE_DIR", tmp_path / "system")
        assert state.state_dir("system") == tmp_path / "system"

    def test_selfupdate_reuses_existing_and_surfaces_creation_failure(self, tmp_path, monkeypatch):
        from cmru.agent.selfupdate import stage_new_venv
        wheel = tmp_path / "x.whl"; wheel.write_bytes(b"x")
        existing = tmp_path / "venv-1"; existing.mkdir()
        assert stage_new_venv(tmp_path, "1", wheel) == existing
        monkeypatch.setattr("cmru.agent.selfupdate.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=2, stderr="bad"))
        with pytest.raises(RuntimeError, match="venv creation"):
            stage_new_venv(tmp_path, "2", wheel)

    def test_selfupdate_install_failure_and_systemd_handoff_are_observable(self, tmp_path, monkeypatch):
        from cmru.agent.selfupdate import stage_new_venv, handoff_via_systemd
        wheel = tmp_path / "x.whl"; wheel.write_bytes(b"x")
        def fake_run(cmd, **kw):
            if "-m" in cmd:
                (tmp_path / "venv-3/bin").mkdir(parents=True)
                return SimpleNamespace(returncode=0, stderr="")
            return SimpleNamespace(returncode=3, stderr="pip bad")
        monkeypatch.setattr("cmru.agent.selfupdate.subprocess.run", fake_run)
        with pytest.raises(RuntimeError, match="wheel install"):
            stage_new_venv(tmp_path, "3", wheel)
        venv = tmp_path / "venv-4"; venv.mkdir()
        handoff_via_systemd("4", venv, scope="user", dry_run=True)
        assert (tmp_path / "venv-current").is_symlink()

    def test_consul_registration_health_and_release_paths_warn_but_do_not_lie(self, monkeypatch):
        from cmru.agent.consul_backend import ConsulBackend
        b = ConsulBackend("http://consul")
        seen = []
        monkeypatch.setattr(b, "_put", lambda *args, **kw: (503, b"no"))
        b.register_service("n"); b.pass_health_check("n"); b.publish_observed("n", "l", "{}")
        # release attempts both operations and tolerates an outage only when raised
        monkeypatch.setattr(b, "_put", lambda *args, **kw: (_ for _ in ()).throw(OSError("down")))
        from cmru.agent.backend import LockHandle
        with pytest.raises(OSError):
            b.release_lock(LockHandle("s", "k", True))

    def test_cli_enroll_success_persists_identity_and_once_dispatches(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent import cli
        identity = SimpleNamespace(node_id="n", landscape="l", token_path=None, public_key="pub")
        backend = SimpleNamespace(enroll=lambda seed: identity, _token=None)
        monkeypatch.setattr(cli, "_build_backend", lambda args: backend)
        args = _args(node_id="n", landscape="l", minisign_pubkey="pub", token="tok")
        assert cli.cmd_enroll(args) == 0
        assert (tmp_path / "cmru-agent/node_id").read_text().strip() == "n"
        monkeypatch.setattr(cli, "_load_identity", lambda scope: ("n", {"landscape": "l", "public_key": "p"}))
        rec = SimpleNamespace(once=lambda: True, run=lambda: None)
        monkeypatch.setattr("cmru.agent.reconciler.Reconciler", lambda **kw: rec)
        assert cli.cmd_once(_args()) == 0
        assert "change applied" in capsys.readouterr().out

    def test_controller_status_catalog_and_parser_success_paths(self, tmp_path, monkeypatch, capsys):
        from cmru.controller import cli
        class Backend:
            def _get(self, path):
                return 200, json.dumps([{"Node": "n", "ServiceTags": ["n"]}]).encode(), {}
        monkeypatch.setattr(cli, "_build_backend", lambda args: Backend())
        assert cli.cmd_status(_args(landscape="l")) == 0
        assert "Registered" in capsys.readouterr().out
        from cmru.controller.cli import _build_parser
        parsed = _build_parser().parse_args(["--dry-run", "rollback", "--plan", "p", "--to", "old", "--generation", "8"])
        assert parsed.to_tag == "old" and parsed.generation == 8
