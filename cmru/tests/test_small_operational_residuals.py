"""Behavioral witnesses for resolver, Consul transport, and reconciler edges."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from cmru import resolve
from cmru.agent.adapter import StepResult
from cmru.agent import consul_backend, protocol, reconciler


def test_resolve_main_rejects_missing_or_unknown_project(monkeypatch):
    with pytest.raises(SystemExit) as error:
        resolve.resolve_main([])
    assert error.value.code == 2

    loaded = (
        None, {"known": SimpleNamespace(prefix="known-v", github_token="")}, None, None,
        None, None, None, None, SimpleNamespace(owner="o", repo="r", token=None), None,
    )
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _: None)
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    with pytest.raises(SystemExit) as error:
        resolve.resolve_main(["--project", "missing"])
    assert error.value.code == 2


def test_resolve_main_uses_project_prefix_and_refuses_missing_owner_or_release(monkeypatch, capsys):
    project = SimpleNamespace(prefix="demo-v", github_token="project-token")
    loaded = (
        None, {"demo": project}, None, None, None, None, None, None,
        SimpleNamespace(owner="owner", repo="repo", token="root-token"), None,
    )
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _: None)
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    captured = {}

    class Host:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("cmru.hosts.github.GitHubReleaseHost", Host)
    monkeypatch.setattr(resolve, "resolve", lambda host, prefix, **kwargs: None)
    with pytest.raises(SystemExit) as error:
        resolve.resolve_main(["--project", "demo"])
    assert error.value.code == 1
    assert captured == {"owner": "owner", "repo": "repo", "token": "project-token"}
    assert "No releases found" in capsys.readouterr().err

    no_owner = loaded[:8] + (SimpleNamespace(owner="", repo="repo", token=None), None)
    monkeypatch.setattr("cmru.cli.load_config", lambda _: no_owner)
    with pytest.raises(SystemExit) as error:
        resolve.resolve_main(["--prefix", "demo-v"])
    assert error.value.code == 2
    assert "owner/repo unknown" in capsys.readouterr().err


def test_consul_transport_redacts_empty_and_surfaces_http_error_bodies():
    assert consul_backend._redact("") == "(empty)"
    assert consul_backend._redact("secret") == "secr****"
    backend = consul_backend.ConsulBackend("http://consul")
    error = consul_backend.urllib.error.HTTPError(
        "http://consul/x", 503, "unavailable", {}, io.BytesIO(b"down")
    )
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(consul_backend.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(
            consul_backend.urllib.error.HTTPError("http://consul/x", 503, "unavailable", {}, io.BytesIO(b"down"))))
        assert backend._get("/x") == (503, b"down", {})
        monkeypatch.setattr(consul_backend.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(
            consul_backend.urllib.error.HTTPError("http://consul/x", 503, "unavailable", {}, io.BytesIO(b"down"))))
        assert backend._delete("/x") == (503, b"down")
    finally:
        monkeypatch.undo()


def test_consul_service_and_health_registration_accept_http_failure_without_raising():
    backend = consul_backend.ConsulBackend()
    backend._put = lambda *args, **kwargs: (500, b"failed")
    backend.register_service("node")
    backend.pass_health_check("node")
    backend._put = lambda *args, **kwargs: (200, b"")
    backend.register_service("node")
    backend._put = lambda *args, **kwargs: (201, b"")
    backend.pass_health_check("node")


def test_reconciler_health_failure_degrades_and_missing_postinstall_path_refuses(monkeypatch, tmp_path):
    desired = protocol.DesiredState(
        schema_version=1, generation=2, action="apply",
        release=protocol.ReleaseRef("tag", "https://manifest", "a" * 64),
        profiles=[], config_hash="h", plan_id="p", step_id="s",
    )
    observed = protocol.ObservedState()
    published = []
    monkeypatch.setattr(reconciler, "write_observed", lambda value, scope: published.append(value))
    monkeypatch.setattr(reconciler, "write_current_generation", lambda *_: None)

    class Adapter:
        def validate(self, *_): pass
        def prepare(self, *_): pass
        def apply_step(self, *_): return StepResult(True, 0)
        def health(self, *_): raise RuntimeError("health unavailable")

    runner = reconciler.Reconciler(SimpleNamespace(), "n", "l", release_root=tmp_path)
    assert runner._do_install_or_update(Adapter(), desired, observed, "now", tmp_path)
    assert published[-1].health == "degraded"

    monkeypatch.setattr(reconciler.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stderr=""))
    assert runner._ensure_release(desired) is None

    release_path = tmp_path / "releases" / "tag"
    def stage_then_fail(*_args, **_kwargs):
        release_path.mkdir(parents=True)
        raise FileNotFoundError("cmru-get")
    monkeypatch.setattr(reconciler.subprocess, "run", stage_then_fail)
    assert runner._ensure_release(desired) == release_path


def test_reconciler_accepts_a_successful_present_signature(monkeypatch):
    monkeypatch.setattr(reconciler.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b""))
    reconciler._verify_sig_if_present(b"{}", b"signature", "public-key")
