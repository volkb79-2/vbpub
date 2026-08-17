"""Behavioral coverage for remaining core parsing, persistence, and guards."""
from __future__ import annotations

import io
import importlib.util
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import dependencies, manifest, output, version
from cmru.agent import adapter, protocol, selfupdate, state
from cmru.controller import cli as controller_cli, planner
from cmru.hosts.github import GitHubReleaseHost


def _plan(**wave):
    item = dict(phase=1, name="canary", type="canary", nodes=["n"], profiles=[])
    item.update(wave)
    return {"plan": {"id": "p", "landscape": "l", "release_tag": "v1", "manifest_url": "u",
                      "manifest_sha256": "a" * 64, "waves": [item]}}


def test_planner_rejects_missing_plan_nodes_and_profile_entries():
    with pytest.raises(ValueError, match="section required"):
        planner.load_plan_json("{}")
    bad_nodes = _plan(nodes=[])
    with pytest.raises(ValueError, match="nodes must be a non-empty list"):
        planner.load_plan_json(json.dumps(bad_nodes))
    bad_profiles = _plan(profiles=[""])
    with pytest.raises(ValueError, match="profiles entries"):
        planner.load_plan_json(json.dumps(bad_profiles))


def test_output_stream_configure_flush_and_empty_write_contract(monkeypatch):
    stream = io.StringIO()
    wrapped = output.SeverityStream(stream, time_short=False, colour=False)
    original_stderr = output.sys.stderr
    monkeypatch.delenv(output._TIME_ENV, raising=False)
    wrapped.configure(time_short=True, colour=False)
    assert wrapped.write("") == 0
    wrapped.write("[INFO]")
    wrapped.flush()
    assert "[INFO]" in stream.getvalue()
    assert wrapped.encoding == stream.encoding
    monkeypatch.setattr(output.sys, "stdout", wrapped)
    try:
        output.configure(time_short=False)
        assert output.consume_cli_flags(["--log-prefix-time-short", "build", "--", "--log-prefix-time-short"]) == ["build", "--", "--log-prefix-time-short"]
        assert output.os.environ[output._TIME_ENV] == "1"
    finally:
        output.sys.stderr = original_stderr
        monkeypatch.delenv(output._TIME_ENV, raising=False)
    assert output._TIME_ENV not in output.os.environ


def test_dependencies_records_wheel_source_outside_repository(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    project = outside / "consumer"; (project / "pip").mkdir(parents=True)
    (project / "pip" / "wheels.list").write_text("provider\n")
    projects = {"provider": SimpleNamespace(scm_dist="provider", project_root=None),
                "consumer": SimpleNamespace(scm_dist="consumer", project_root=project)}
    report = dependencies.build_report(repo_root=tmp_path, project_order=["provider", "consumer"],
                                       declared={"consumer": ["provider"]}, projects=projects)
    assert any(edge.kind == "artifact" and edge.source.startswith("/") for edge in report.edges)


def test_selfupdate_existing_temp_link_is_replaced_and_dry_run_skips_restart(monkeypatch, tmp_path):
    venv = tmp_path / "venv"; venv.mkdir()
    tmp_link = tmp_path / "venv-current.new"; tmp_link.symlink_to(tmp_path)
    calls = []
    monkeypatch.setattr(selfupdate.os, "system", lambda command: calls.append(command) or 0)
    selfupdate.handoff_via_systemd("1", venv, dry_run=True)
    assert (tmp_path / "venv-current").is_symlink() and calls == []
    marker = tmp_path / "pending-selfupdate"
    marker.write_text("malformed\nversion=1\n")
    assert selfupdate.read_pending_marker(tmp_path) == {"version": "1"}
    selfupdate.clear_pending_marker(tmp_path)
    selfupdate.clear_pending_marker(tmp_path)


def test_adapter_loader_rejects_non_adapter_class_and_protocol_non_object(tmp_path):
    root = tmp_path / "release"
    root.mkdir()
    (root / "adapter.py").write_text("class Adapter: pass\n")
    with pytest.raises(RuntimeError, match="does not subclass"):
        adapter.load_adapter(root)
    with pytest.raises(protocol.DesiredStateError, match="JSON object"):
        protocol.validate_desired([])
    (root / "adapter.py").write_text("class Adapter: pass\n")
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="Cannot load adapter"):
            adapter.load_adapter(root)
    finally:
        monkeypatch.undo()


def test_state_identity_absence_and_manifest_image_shape_are_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert state.read_identity() is None
    with pytest.raises(TypeError, match="images must be a dict"):
        manifest._validate_images([], "demo")


def test_controller_cli_module_guard_executes_parser(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cmru-controller", "--help"])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(Path(controller_cli.__file__)), run_name="__main__")
    assert error.value.code == 0 and "publish" in capsys.readouterr().out


def test_github_host_resolve_latest_without_sha_url_does_not_fetch(monkeypatch):
    host = GitHubReleaseHost("o", "r", "t")
    host._gh.list_releases = lambda: [{"tag_name": "demo-v1.0.0", "draft": False, "prerelease": False,
                                       "assets": [{"name": "demo.whl", "browser_download_url": "u"}]}]
    monkeypatch.setattr("urllib.request.urlopen", lambda *_: (_ for _ in ()).throw(AssertionError("unexpected fetch")))
    result = host.resolve_latest("demo")
    assert result["sha256"] is None


def test_version_public_semver_and_release_helpers_remain_deterministic():
    assert version.bump_version("1.2.3", "minor") == "1.3.0"
