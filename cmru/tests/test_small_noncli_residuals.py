"""Exact branch witnesses for small non-CLI operational modules."""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import ghcr, release, standards
from cmru.agent import cli as agent_cli, protocol
from cmru.controller.planner import PlanStep
from cmru.controller.rollout import RolloutEngine


def _step(**overrides):
    values = dict(plan_id="p", wave_name="w", phase=1, wave_type="canary", nodes=["n"],
                  profiles=[], release_tag="demo-v1", manifest_url="u", manifest_sha256="a" * 64,
                  config_hash="h", step_id="s", required=True, requires_approval=False)
    values.update(overrides)
    return PlanStep(**values)


def test_rollout_wait_barrier_ignores_malformed_observed_then_times_out(monkeypatch):
    backend = SimpleNamespace(read_observed=lambda *_: "not-json")
    engine = RolloutEngine(backend, "land", poll_interval=0, wave_timeout=1)
    ticks = iter([0, 0, 2])
    monkeypatch.setattr("cmru.controller.rollout.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("cmru.controller.rollout.time.sleep", lambda _: None)
    assert engine._wait_for_wave("p", _step()) is False
    empty_backend = SimpleNamespace(read_observed=lambda *_: None)
    empty_engine = RolloutEngine(empty_backend, "land", poll_interval=0, wave_timeout=1)
    ticks = iter([0, 0, 2])
    monkeypatch.setattr("cmru.controller.rollout.time.monotonic", lambda: next(ticks))
    assert empty_engine._wait_for_wave("p", _step()) is False

    degraded = json.dumps({"health": "degraded", "applied_generation": 101})
    engine = RolloutEngine(SimpleNamespace(read_observed=lambda *_: degraded), "land", poll_interval=0, wave_timeout=1)
    ticks = iter([0, 0, 2])
    monkeypatch.setattr("cmru.controller.rollout.time.monotonic", lambda: next(ticks))
    assert engine._wait_for_wave("p", _step()) is False


def test_rollout_wait_barrier_stops_on_matching_failed_node():
    observed = json.dumps({"health": "failed", "applied_generation": 101})
    backend = SimpleNamespace(read_observed=lambda *_: observed)
    engine = RolloutEngine(backend, "land", generation_base=1, poll_interval=0, wave_timeout=1)
    assert engine._wait_for_wave("p", _step()) is False


def test_standards_assessment_reports_disabled_history_and_manual_projects():
    project = SimpleNamespace(template_revision=4, changelog=None, steps={}, runner_steps={}, env={})
    result = standards.assess_projects(Path("."), {"demo": project}, [], ["demo"])[0]
    assert "source-first release history is disabled" in result.problems
    assert any("not in orchestration.project_order" in message for message in result.messages)


def test_standards_main_unknown_project_is_refused(monkeypatch):
    loaded = (Path("."), {"demo": SimpleNamespace()}, ["demo"])
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _: Path("cmru.toml"))
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    with pytest.raises(SystemExit) as error:
        standards.standards_main(["--project", "missing"])
    assert error.value.code == 2


def test_standards_update_requires_project_local_config(monkeypatch):
    loaded = (Path("."), {"demo": SimpleNamespace(project_root=None)}, ["demo"])
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _: Path("cmru.toml"))
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    with pytest.raises(ValueError, match="project-local"):
        standards.standards_main(["--project", "demo", "--update"])


def test_standards_atomic_write_cleans_temporary_file_after_replace_failure(monkeypatch, tmp_path):
    path = tmp_path / "cmru.toml"
    monkeypatch.setattr(Path, "replace", lambda self, target: None)
    standards._atomic_write(path, "contents")
    assert not path.exists() and not path.with_name(".cmru.toml.cmru-tmp").exists()


def test_agent_cli_main_dispatch_fallback_is_explicit(monkeypatch, capsys):
    class Parser:
        def parse_args(self, _argv): return SimpleNamespace(log_level="INFO", verb="unknown")
        def print_help(self): print("help")
    monkeypatch.setattr(agent_cli, "_build_parser", lambda: Parser())
    with pytest.raises(SystemExit) as error:
        agent_cli.main([])
    assert error.value.code == 1 and "help" in capsys.readouterr().out


def test_ghcr_request_http_error_and_repository_failure_are_explicit(monkeypatch):
    api = ghcr.GitHubPackages("owner", "repo", "token", "org")
    error = urllib.error.HTTPError("u", 503, "down", {}, io.BytesIO(b"body"))
    monkeypatch.setattr(ghcr, "urlopen", lambda *_: (_ for _ in ()).throw(error))
    assert api._request("GET", "https://example") == (503, "body")
    api._request = lambda *args, **kwargs: (500, "bad")
    with pytest.raises(SystemExit) as raised:
        api.repo_visibility()
    assert raised.value.code == 1


def test_ghcr_request_omits_auth_header_when_token_is_absent(monkeypatch):
    api = ghcr.GitHubPackages("owner", "repo", None, "org")
    seen = {}
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self): return b"{}"
    def open_request(request):
        seen.update(request.header_items())
        return Response()
    monkeypatch.setattr(ghcr, "urlopen", open_request)
    assert api._request("GET", "https://example") == (200, "{}")
    assert "Authorization" not in seen


def test_release_publish_creates_missing_release_and_asset_url_is_deterministic(tmp_path):
    api = release.GitHubReleases("owner", "repo", "token", "org")
    calls = []
    api.get_release_by_tag = lambda tag: None
    api.create_release = lambda *args: (calls.append(args) or {"id": 1, "upload_url": "https://upload/{id}"})
    api.list_assets = lambda _rid: []
    api.upload_asset = lambda *args: calls.append(args)
    result = api.publish("demo-v1", "title", "notes", [])
    assert result["id"] == 1 and calls[0][:3] == ("demo-v1", "title", "notes")
    assert api.asset_download_url("demo-v1", "demo.whl").endswith("/demo-v1/demo.whl")
