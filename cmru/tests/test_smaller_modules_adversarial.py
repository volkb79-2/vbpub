"""Behavior-led tests for the remaining small CMRU boundary modules."""
from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import dependencies, ghcr, manifest, output, resolve, standards
from cmru.agent import protocol, selfupdate, state
from cmru.hosts.github import GitHubReleaseHost


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


def test_resolve_latest_json_and_fallback_are_observable(monkeypatch):
    # resolve_via_latest_json imports urllib.request.urlopen at call time.
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Response(
        b'{"version":"1.2.3","tag":"demo-v1.2.3","url":"u","sha256":"h"}'
    ))
    result = resolve.resolve_via_latest_json("https://github.test/releases", "demo-v")
    assert result == {"version": "1.2.3", "tag": "demo-v1.2.3", "asset": None, "sha256": "h", "url": "u"}
    host = SimpleNamespace(resolve_latest=lambda prefix: {"version": "1.0.0"})
    assert resolve.resolve(host, "demo-v", use_latest_json=False) == {"version": "1.0.0"}


def test_resolve_format_has_machine_readable_contract():
    result = {"version": "1.2.3", "tag": "demo-v1.2.3", "url": "https://a", "sha256": "abc"}
    assert resolve.format_result(result, "url") == "https://a"
    assert "DEMO_VERSION=1.2.3" in resolve.format_result(result, "env")
    assert json.loads(resolve.format_result(result, "json")) == result


def test_dependency_report_catches_unknown_self_and_undeclared_wheels(tmp_path):
    consumer = tmp_path / "consumer"
    (consumer / "pip").mkdir(parents=True)
    (consumer / "pip" / "wheels.list").write_text("provider\nmissing[extra]\nconsumer\n", encoding="utf-8")
    projects = {
        "provider": SimpleNamespace(scm_dist="provider", project_root=None),
        "consumer": SimpleNamespace(scm_dist="consumer", project_root=consumer),
    }
    report = dependencies.build_report(
        repo_root=tmp_path, project_order=["consumer", "provider"],
        declared={"consumer": ()}, projects=projects,
    )
    assert not report.ok
    assert any("unknown first-party" in e for e in report.errors)
    assert any("contains itself" in e for e in report.errors)
    assert any("does not declare" in e for e in report.errors)
    assert report.artifact_inputs["consumer"] == ("provider",)


def test_dependency_comment_writer_rejects_malformed_markers_and_writes_newline(tmp_path):
    report = dependencies.DependencyReport(("demo",), {}, {}, (), ())
    path = tmp_path / "cmru.toml"
    path.write_text("[orchestration]\nname='x'", encoding="utf-8")
    dependencies.write_comment_block(path, report)
    text = path.read_text()
    assert "BEGIN CMRU GENERATED" in text and text.endswith("\n")
    path.write_text("# BEGIN CMRU GENERATED DEPENDENCY GRAPH\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        dependencies.write_comment_block(path, report)


def test_standards_assessment_reports_policy_failures_and_success():
    command = SimpleNamespace(argv=("tester-gate", "--enable-docker"))
    project = SimpleNamespace(
        template_revision=standards.PROJECT_TEMPLATE_REVISION, changelog="CHANGES.md",
        steps={}, runner_steps={"run-tests": SimpleNamespace(quiet=False)},
        env={},
    )
    result = standards.assess_projects(Path("."), {"demo": project}, ["demo"], ["demo"])[0]
    assert "summary-only" in " ".join(result.problems)
    project.runner_steps = {"run-tests": SimpleNamespace(quiet=True)}
    project.steps = {"run-tests": [command]}
    result = standards.assess_projects(Path("."), {"demo": project}, ["demo"], ["demo"])[0]
    assert any("tester-gate requires" in p for p in result.problems)


def test_standards_revision_update_is_atomic_and_requires_project_section(tmp_path):
    path = tmp_path / "cmru.toml"
    path.write_text("[project]\nname='demo'\n", encoding="utf-8")
    assert standards._update_project_revision(path) is True
    assert f"template_revision = {standards.PROJECT_TEMPLATE_REVISION}" in path.read_text()
    assert standards._update_project_revision(path) is False
    path.write_text("[runner]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="section vanished"):
        standards._update_project_revision(path)


def test_ghcr_visibility_shapes_and_unsupported_patch_are_explicit(monkeypatch):
    client = ghcr.GitHubPackages("owner", "repo", "token", owner_type="org")
    client._request = lambda *a, **k: (200, '{"private":true}')
    assert client.repo_visibility() == "private"
    client._request = lambda *a, **k: (404, "missing")
    assert client.package_visibility("pkg") is None
    client._request = lambda *a, **k: (404, "unsupported")
    with pytest.raises(ghcr.PackageVisibilityApiUnsupported):
        client.set_package_visibility("pkg", "public")
    client._request = lambda *a, **k: (200, '{"visibility":"public"}')
    assert client.mirror_package_visibility("pkg", expected_visibility="public") == "public"


def test_ghcr_mirror_retries_then_fails_without_inventing_visibility(monkeypatch):
    client = ghcr.GitHubPackages("owner", "repo", "token", owner_type="user")
    client.package_visibility = lambda name: None
    monkeypatch.setattr(ghcr.time, "sleep", lambda _: None)
    with pytest.raises(SystemExit) as error:
        client.mirror_package_visibility("pkg", expected_visibility="private", retries=2, delay=0)
    assert error.value.code == 1


def test_manifest_validation_and_canonical_write(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="empty"):
        manifest._validate_images({}, "demo")
    with pytest.raises(ValueError, match="missing required"):
        manifest._validate_images({"web": {"repository": "r", "tag": "t"}}, "demo")
    assert manifest._version_from_wheel_name(Path("ciu-1.2.3-py3-none-any.whl")) == "1.2.3"
    assert manifest._version_from_wheel_name(Path("invalid.whl")) == "0.0.0"
    out = tmp_path / "nested" / "manifest.json"
    assert manifest.write_manifest({"b": 1, "a": 2}, out) == out
    assert out.read_text() == '{"a":2,"b":1}\n'


def test_manifest_build_uses_fallback_cmru_version_and_image_facts(tmp_path, monkeypatch):
    cmru_wheel = tmp_path / "cmru-1.whl"; cmru_wheel.write_bytes(b"cmru")
    ciu_wheel = tmp_path / "ciu-2.3.4-py3-none-any.whl"; ciu_wheel.write_bytes(b"ciu")
    import importlib.metadata
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    monkeypatch.setattr(importlib.metadata, "version", lambda name: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError()))
    result = manifest.build_manifest(
        project="demo", tag="demo-v1", source_commit="abc", cmru_wheel=cmru_wheel,
        ciu_wheel=ciu_wheel, images={"web": {"repository": "r", "tag": "latest", "digest": "sha"}},
        installer_schema_version=1, host_config_schema_version=2,
        platform={"min_python": "3.11", "arch": "amd64"}, upgrade={"min_from": "1", "rollback_to": "0"},
    )
    assert result["cmru"]["version"] == "0.0.0"
    assert result["ciu"]["version"] == "2.3.4"


def test_output_stream_handles_partial_prefix_and_literal_passthrough(monkeypatch):
    stream = io.StringIO()
    decorated = output.SeverityStream(stream, time_short=False, colour=False)
    assert decorated.write("[WA") == 3
    decorated.flush()
    assert stream.getvalue() == "[WA"
    stream = io.StringIO(); decorated = output.SeverityStream(stream, time_short=False, colour=False)
    decorated.write("[INFO] ok\n")
    assert stream.getvalue() == "[INFO] ok\n"
    monkeypatch.delenv(output._TIME_ENV, raising=False)
    assert output.consume_cli_flags(["x", "--", "--log-prefix-time-short"]) == ["x", "--", "--log-prefix-time-short"]


def test_selfupdate_handoff_updates_link_and_reports_restart_failure(tmp_path, monkeypatch):
    venv = tmp_path / "venvs" / "v2"; venv.mkdir(parents=True)
    current = venv.parent / "venv-current"
    selfupdate.handoff_via_systemd("2", venv, scope="user", dry_run=True)
    assert current.is_symlink() and current.resolve() == venv
    monkeypatch.setattr(selfupdate.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr="failed"))
    with pytest.raises(SystemExit) as error:
        selfupdate.handoff_via_systemd("2", venv, scope="system")
    assert error.value.code == 0


def test_protocol_and_state_reject_malformed_inputs_and_write_atomically(tmp_path, monkeypatch):
    raw = {"schema_version": 1, "generation": 1, "action": "hold",
           "release": {"tag": "t", "manifest_url": "u", "manifest_sha256": "s"},
           "profiles": [""]}
    with pytest.raises(protocol.DesiredStateError, match="profile"):
        protocol.validate_desired(raw)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    state.write_observed(protocol.ObservedState(applied_generation=4), "user")
    assert state.read_observed("user").applied_generation == 4
    state_dir = state.state_dir("user")
    (state_dir / "identity.json").write_text("not-json")
    (state_dir / "current_generation").write_text("not-an-int")
    assert state.read_identity("user") is None
    assert state.read_current_generation("user") is None


def test_github_host_filters_releases_and_surfaces_sha_retry_failure(monkeypatch):
    host = GitHubReleaseHost("o", "r", "t")
    host._gh.list_releases = lambda: [
        {"tag_name": "demo-v1.0.0", "assets": [{"name": "a.whl", "browser_download_url": "u"}, {"name": "a.whl.sha256", "browser_download_url": "s"}]},
        {"tag_name": "demo-v2.0.0", "draft": True, "assets": []},
        {"tag_name": "other-v9.0.0", "assets": []},
    ]
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    result = host.resolve_latest("demo-v")
    assert result["version"] == "1.0.0" and result["sha256"] is None
    assert host.list_releases("demo-v")[0]["tag"] == "demo-v1.0.0"
