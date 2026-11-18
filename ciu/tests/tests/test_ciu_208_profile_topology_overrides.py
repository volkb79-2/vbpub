"""CIU-208 — profile-mode `ciu up` applies a profile's ``topology_overrides``.

``Profile.config`` already carries the active profiles' ``topology_overrides``
(S7.4), and preflight plus ``ciu resolve`` render with it. The per-stack engine
render used to re-read the global chain from disk, so the workload that
started saw the disk-chain ``topology.*`` value instead. The accumulated
override now travels ``action_deploy`` -> ``_run_stack`` -> ``main_execution``
and is deep-merged over the stack's own rendered global chain, so only the
overridden keys change.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, engine  # noqa: E402
from ciu.deploy_pkg import profiles as profiles_pkg  # noqa: E402
from tests.test_ciu_render_selection_context import _add_stack, _build_repo  # noqa: E402

PROBE = (
    "\n[ciumeta]\n"
    'topo_host = "{{ topology.services.redis.internal_host }}"\n'
    'topo_port = "{{ topology.services.redis.port }}"\n'
    'topo_nested = "{{ topology.services.other.internal_host }}"\n'
)
ROOT_TOPOLOGY = (
    "[topology.services.redis]\n"
    'internal_host = "base-default"\n'
    "port = 6379\n"
    "[topology.services.other]\n"
    'internal_host = "root-other"\n'
    "[deploy.profiles.peer.topology_overrides.services.redis]\n"
    'internal_host = "profile-peer"\n'
    "[deploy.profiles.nested.topology_overrides.services.other]\n"
    'internal_host = "profile-other"\n'
)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
    monkeypatch.delenv("CIU_SERVICES_PROFILE", raising=False)
    return _build_repo(tmp_path, monkeypatch)


def _stack(repo_root: Path) -> Path:
    (repo_root / "ciu.global.instance.toml.j2").write_text(ROOT_TOPOLOGY, encoding="utf-8")
    stack = _add_stack(repo_root)
    cfg = stack / "config.toml.j2"
    cfg.write_text(cfg.read_text(encoding="utf-8") + PROBE, encoding="utf-8")
    return stack


def _rendered(stack: Path) -> str:
    files = list((stack / ".ciu" / "rendered").rglob("config.toml"))
    assert files, "configfile was not rendered"
    return files[0].read_text(encoding="utf-8")


def _deploy(monkeypatch, repo_root: Path, names: list[str] | None) -> int:
    global_cfg = deploy.load_global_config(repo_root, write_rendered=False)
    profile = deploy.resolve_profiles(global_cfg, names)
    monkeypatch.setattr(deploy, "profile_env", lambda _p: {})
    entry = {
        "path": "applications/app-config", "name": "app-config",
        "phase_key": "phase_1", "phase_num": 1,
        "service": {"path": "applications/app-config", "name": "app-config", "enabled": True},
    }
    return deploy.action_deploy(
        repo_root, profile, [entry], dry_run=True, ignore_errors=False,
        health_after_phase=False, update_cert_permission=False, rendered={},
        no_preflight=False,
    )


@pytest.mark.ciu_test_repo_reader
def test_active_profile_override_reaches_the_per_stack_render(repo, monkeypatch):
    """THE ORACLE: real action_deploy -> _run_stack -> main_execution."""
    stack = _stack(repo)
    assert _deploy(monkeypatch, repo, ["peer"]) == 0
    body = _rendered(stack)
    assert 'topo_host = "profile-peer"' in body
    assert 'topo_port = "6379"' in body


@pytest.mark.ciu_test_repo_reader
def test_without_the_profile_the_disk_chain_value_renders(repo, monkeypatch):
    stack = _stack(repo)
    assert _deploy(monkeypatch, repo, None) == 0
    assert 'topo_host = "base-default"' in _rendered(stack)


@pytest.mark.ciu_test_repo_reader
def test_an_unrelated_profile_override_leaves_other_keys_alone(repo, monkeypatch):
    stack = _stack(repo)
    assert _deploy(monkeypatch, repo, ["nested"]) == 0
    body = _rendered(stack)
    assert 'topo_host = "base-default"' in body
    assert 'topo_nested = "profile-other"' in body


class _StopAfterRender(RuntimeError):
    pass


def test_only_the_override_is_applied_over_the_chain_this_call_renders(monkeypatch, tmp_path):
    """The engine merges the override into ITS rendered chain; it never swaps
    the chain for the profile's copy of the root chain, so every value the
    profile leaves alone stays exactly as this render produced it."""
    rendered_chain = {
        "topology": {"services": {
            "redis": {"internal_host": "base-default", "port": 7001},
            "other": {"internal_host": "chain-only"},
        }},
        "deploy": {"log_level": "INFO"},
    }
    seen = {}

    def stop(_dir, global_config, **_k):
        seen["global"] = global_config
        raise _StopAfterRender("stop")

    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_k: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _d: None)
    monkeypatch.setattr(engine, "resolve_env_root", lambda *_a: tmp_path)
    monkeypatch.setattr(
        engine.config_model, "render_global_chain", lambda *a, **k: rendered_chain
    )
    monkeypatch.setattr(engine.config_model, "render_stack", stop)
    with pytest.raises(_StopAfterRender):
        engine.main_execution(
            tmp_path,
            topology_overrides={"services": {"redis": {"internal_host": "profile-peer"}}},
        )
    assert seen["global"]["topology"]["services"] == {
        "redis": {"internal_host": "profile-peer", "port": 7001},
        "other": {"internal_host": "chain-only"},
    }


# --- the carrier and the call shape ------------------------------------------


def _profile(overrides):
    return profiles_pkg.Profile(name="peer", topology_overrides=overrides, config={})


def test_profile_records_the_accumulated_topology_override():
    cfg = {
        "topology": {"services": {"redis": {"internal_host": "base-default", "port": 1}}},
        "deploy": {"profiles": {
            "a": {"topology_overrides": {"services": {"redis": {"internal_host": "h"}}}},
            "b": {"topology_overrides": {"services": {"redis": {"port": 2}}}},
            "plain": {},
        }},
    }
    one = profiles_pkg.resolve_profiles(cfg, ["a", "plain"])
    assert one.topology_overrides == {"services": {"redis": {"internal_host": "h"}}}
    both = profiles_pkg.resolve_profiles(cfg, ["a", "b"])
    assert both.topology_overrides == {"services": {"redis": {"internal_host": "h", "port": 2}}}
    assert profiles_pkg.resolve_profiles(cfg, ["plain"]).topology_overrides == {}
    assert profiles_pkg.Profile().topology_overrides == {}


def _record_calls(monkeypatch):
    seen = {}

    def run_stack(stack_dir, **kw):
        seen["run_stack"] = kw
        return True

    monkeypatch.setattr(deploy, "provisioning_preflight", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "mem_min_admission_check", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "apply_mem_min_injections", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "profile_env", lambda _p: {})
    monkeypatch.setattr(deploy, "_run_stack", run_stack)
    return seen


def _action(tmp_path, profile):
    (tmp_path / "apps" / "api").mkdir(parents=True)
    entry = {"path": "apps/api", "name": "api", "phase_key": "phase_1", "phase_num": 1,
             "service": {"path": "apps/api", "name": "api", "enabled": True}}
    return deploy.action_deploy(
        tmp_path, profile, [entry], dry_run=True, ignore_errors=False,
        health_after_phase=False, update_cert_permission=False, rendered={},
    )


def test_action_deploy_hands_the_override_to_run_stack(monkeypatch, tmp_path):
    seen = _record_calls(monkeypatch)
    override = {"services": {"redis": {"internal_host": "profile-peer"}}}
    assert _action(tmp_path, _profile(override)) == 0
    assert seen["run_stack"]["topology_overrides"] == override


def test_action_deploy_keeps_the_old_call_shape_without_an_override(monkeypatch, tmp_path):
    seen = _record_calls(monkeypatch)
    assert _action(tmp_path, _profile({})) == 0
    assert "topology_overrides" not in seen["run_stack"]


def _run_stack(monkeypatch, tmp_path, **kw):
    seen = {}
    monkeypatch.setattr(
        deploy.engine, "main_execution",
        lambda **k: seen.update(k) or {"status": "success"},
    )
    assert deploy._run_stack(
        tmp_path, env={}, compose_profiles=[], dry_run=True,
        update_cert_permission=False, **kw,
    )
    return seen


def test_run_stack_forwards_the_override_to_the_engine(monkeypatch, tmp_path):
    override = {"services": {"redis": {"internal_host": "profile-peer"}}}
    seen = _run_stack(monkeypatch, tmp_path, topology_overrides=override)
    assert seen["topology_overrides"] == override


def test_run_stack_without_an_override_calls_the_engine_as_before(monkeypatch, tmp_path):
    assert "topology_overrides" not in _run_stack(monkeypatch, tmp_path)
    assert "topology_overrides" not in _run_stack(monkeypatch, tmp_path, topology_overrides={})


def test_a_shipped_stack_never_renders_so_no_override_reaches_it(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(
        deploy.engine, "run_shipped", lambda **k: seen.update(k) or {"status": "success"},
    )
    assert deploy._run_stack(
        tmp_path, env={}, compose_profiles=[], dry_run=True,
        update_cert_permission=False, shipped=True,
        topology_overrides={"services": {"redis": {"internal_host": "x"}}},
    )
    assert "topology_overrides" not in seen


def test_the_engine_parameter_defaults_to_no_override():
    import inspect

    assert inspect.signature(engine.main_execution).parameters["topology_overrides"].default is None
