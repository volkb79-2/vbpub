"""Behavioral witnesses for the remaining non-CLI CMRU runtime surface."""
from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import bundle, dependencies, delegated, ghcr, manifest, output, runner, standards
from cmru.agent import adapter, protocol


def test_adapter_loader_accepts_verified_release_adapter_and_rejects_bad_contract(tmp_path):
    good = tmp_path / "scripts"; good.mkdir()
    (good / "adapter.py").write_text(
        "from cmru.agent.adapter import ProjectAdapter, StepResult, HealthResult\n"
        "class Adapter(ProjectAdapter):\n"
        " def validate(self, desired, installed_release): pass\n"
        " def prepare(self, desired, release_root): pass\n"
        " def apply_step(self, step): return StepResult(True, 0)\n"
        " def health(self, step): return HealthResult('healthy')\n"
        " def rollback(self, previous): pass\n"
    )
    loaded = adapter.load_adapter(tmp_path)
    assert loaded.apply_step({}).success and loaded.health({}).status == "healthy"
    (good / "adapter.py").write_text("class Adapter: pass\n")
    with pytest.raises(RuntimeError, match="does not subclass"):
        adapter.load_adapter(tmp_path)


def test_protocol_observed_state_round_trip_preserves_nested_contract():
    observed = protocol.ObservedState(
        applied_generation=4, release_digest="sha", adapter_phase="applied",
        health="healthy", message="ok",
    )
    encoded = observed.to_json()
    assert protocol.ObservedState.from_json(encoded) == observed
    with pytest.raises(AttributeError, match="get"):
        protocol.ObservedState.from_json("[]")


def test_dependency_graph_witnesses_artifact_alias_and_order_errors(tmp_path):
    (tmp_path / "consumer" / "pip").mkdir(parents=True)
    (tmp_path / "consumer" / "pip" / "wheels.list").write_text("provider_pkg[extra]\n")
    provider = SimpleNamespace(project_root=tmp_path / "provider", scm_dist="provider-pkg")
    consumer = SimpleNamespace(project_root=tmp_path / "consumer", scm_dist="consumer")
    report = dependencies.build_report(
        repo_root=tmp_path, project_order=["consumer", "provider"],
        declared={"consumer": []}, projects={"provider": provider, "consumer": consumer},
    )
    assert not report.ok
    assert any("does not declare" in error for error in report.errors)
    assert report.as_dict()["ok"] is False
    assert "PREFLIGHT: FAIL" in dependencies.render_text(report)


def test_dependency_comment_update_replaces_only_generated_region(tmp_path):
    path = tmp_path / "cmru.toml"
    path.write_text("[orchestration]\nname='x'\n")
    report = dependencies.build_report(project_order=["x"], declared={}, projects={})
    dependencies.write_comment_block(path, report)
    first = path.read_text()
    dependencies.write_comment_block(path, report)
    assert path.read_text() == first and "[orchestration]" in first
    with pytest.raises(ValueError, match="malformed"):
        path.write_text("# BEGIN CMRU GENERATED DEPENDENCY GRAPH\n[orchestration]\n")
        dependencies.write_comment_block(path, report)


@pytest.mark.parametrize("fn, tool, argv", [
    (delegated.cosign_sign, "cosign", ["sign-blob"]),
    (delegated.syft_sbom, "syft", ["scan"]),
    (delegated.grype_scan, "grype", ["--fail-on=high"]),
    (delegated.git_cliff_changelog, "git-cliff", ["--output"]),
    (delegated.nfpm_package, "nfpm", ["package"]),
    (delegated.minisign_sign, "minisign", ["-S"]),
])
def test_delegated_tools_construct_real_boundary_argv(monkeypatch, tmp_path, fn, tool, argv):
    calls = []
    monkeypatch.setattr(delegated, "_which", lambda name: "/bin/" + name)
    monkeypatch.setattr(delegated, "_run", lambda command, cwd=None: calls.append(list(command)) or 0)
    artifact = tmp_path / "artifact"; artifact.write_bytes(b"x")
    if fn is delegated.cosign_sign:
        fn(artifact, key="key", extra_args=["--tlog-upload=false"])
    elif fn is delegated.syft_sbom:
        fn(artifact, tmp_path / "sbom.json")
    elif fn is delegated.grype_scan:
        fn(artifact)
    elif fn is delegated.git_cliff_changelog:
        fn(tmp_path / "CHANGELOG.md", tag="v1")
    elif fn is delegated.nfpm_package:
        fn(tmp_path / "nfpm.yaml", tmp_path)
    else:
        fn(artifact, secret_key="key", trusted_comment="comment")
    assert calls and any(token in calls[0] for token in argv)


def test_delegated_missing_tool_is_a_prerequisite_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(delegated, "_which", lambda _: None)
    with pytest.raises(SystemExit) as error:
        delegated.cosign_sign(tmp_path / "x")
    assert error.value.code == 3


def test_ghcr_visibility_contracts_cover_fallback_and_unsupported_api(monkeypatch):
    client = ghcr.GitHubPackages("o", "r", "", "org", api_base="https://api")
    client._request = lambda method, url, **kwargs: (200, '{"private":false}')
    assert client.repo_visibility() == "public" and client.package_visibility("p") == "public"
    client._request = lambda method, url, **kwargs: (404, "missing")
    assert client.package_visibility("p") is None
    client._request = lambda method, url, **kwargs: (404, "unsupported")
    with pytest.raises(ghcr.PackageVisibilityApiUnsupported):
        client.set_package_visibility("p", "public")
    monkeypatch.setattr(ghcr.time, "sleep", lambda _: None)
    client.package_visibility = lambda name: "private"
    client.set_package_visibility = lambda name, visibility: {"visibility": visibility}
    assert client.mirror_package_visibility("p", expected_visibility="public", retries=1, delay=0) == "public"


def test_manifest_is_deterministic_and_image_schema_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    cmru_wheel = tmp_path / "cmru-1.0.0-py3-none-any.whl"; cmru_wheel.write_bytes(b"cmru")
    ciu_wheel = tmp_path / "ciu-2.3.4-py3-none-any.whl"; ciu_wheel.write_bytes(b"ciu")
    kwargs = dict(project="demo", tag="demo-v1", source_commit="a" * 40,
                  cmru_wheel=cmru_wheel, ciu_wheel=ciu_wheel, images=None,
                  installer_schema_version=1, host_config_schema_version=2,
                  platform={"min_python": "3.11"}, upgrade={"min_from": "1"})
    one = manifest.build_manifest(**kwargs)
    assert one["created"] == "2023-11-14T22:13:20Z" and one["ciu"]["version"] == "2.3.4"
    out = manifest.write_manifest(one, tmp_path / "manifest.json")
    assert manifest.manifest_sha256(out) and "project=demo" in manifest.build_trusted_comment(project="demo", tag="demo-v1", manifest_path=out)
    with pytest.raises(ValueError, match="empty"):
        manifest.build_manifest(**{**kwargs, "images": {}})


def test_bundle_archive_is_reproducible_and_excludes_secrets(tmp_path, monkeypatch):
    root = tmp_path / "project"; root.mkdir()
    (root / "app.py").write_text("print('ok')\n")
    (root / "minisign.key").write_text("secret")
    members = bundle.collect_allowlist_members(root, ["."])
    assert all("minisign.key" not in member.archive_path for member in members)
    first = bundle.write_deterministic_tar(members, tmp_path / "one.tar.xz", 1700000000)
    second = bundle.write_deterministic_tar(list(reversed(members)), tmp_path / "two.tar.xz", 1700000000)
    assert first.read_bytes() == second.read_bytes()
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    with pytest.raises(RuntimeError, match="SOURCE_DATE_EPOCH"):
        bundle._read_source_date_epoch()


def test_runner_reproducible_env_and_step_parser_rejects_missing_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    runner.apply_reproducible_env(tmp_path)
    assert "SOURCE_DATE_EPOCH" not in os.environ
    step = runner.parse_step({"steps": {"test": {"commands": [["python", "-m", "pytest"]], "quiet": True}}}, "test")
    assert step.commands == [["python", "-m", "pytest"]] and step.quiet
    with pytest.raises(ValueError):
        runner.parse_step({"steps": {}}, "missing")


def test_output_stream_preserves_partial_severity_and_passthrough():
    stream = io.StringIO()
    wrapped = output.SeverityStream(stream, time_short=False, colour=False)
    assert wrapped.write("[ER") == 3
    wrapped.write("ROR] bad\nplain")
    wrapped.flush()
    assert stream.getvalue() == "[ERROR] bad\nplain"
    assert output.consume_cli_flags(["--log-prefix-time-short", "run", "--", "--log-prefix-time-short"]) == ["run", "--", "--log-prefix-time-short"]


def test_standards_assessment_reports_tester_and_wheel_policy_gaps(tmp_path):
    project = SimpleNamespace(
        template_revision=standards.PROJECT_TEMPLATE_REVISION, changelog="CHANGES.md",
        steps={"run-tests": [SimpleNamespace(argv=["tester-gate"]),]},
        runner_steps={"build": SimpleNamespace(quiet=True)},
        env={"CMRU_TESTER_UNIFIED_IMAGE": "img"},
    )
    result = standards.assess_projects(tmp_path, {"demo": project}, ["demo"], ["demo"])[0]
    assert any("explicit [env]" in problem for problem in result.problems)
    assert not any("no declared run-tests" in problem for problem in result.problems)


def test_agent_release_ref_validation_rejects_invalid_json_contract():
    with pytest.raises(protocol.DesiredStateError):
        protocol.parse_desired_json(b'{"generation": true}')
