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
from cmru.hosts.github import GitHubReleaseHost


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
        from cmru import cli as cmru_cli

        configured = []
        monkeypatch.setattr(output, "configure", configured.append)
        args = cmru_cli._build_cli().parser.parse_args(
            ["build", "--log-prefix-time-short"]
        )
        assert args.log_prefix_time_short is True
        assert output.os.environ[output._TIME_ENV] == "1"
        assert configured == [True]
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


def test_manifest_image_shape_is_explicit():
    with pytest.raises(TypeError, match="images must be a dict"):
        manifest._validate_images([], "demo")


def test_github_host_resolve_latest_without_sha_url_does_not_fetch(monkeypatch):
    host = GitHubReleaseHost("o", "r", "t")
    host._gh.list_releases = lambda: [{"tag_name": "demo-v1.0.0", "draft": False, "prerelease": False,
                                       "assets": [{"name": "demo.whl", "browser_download_url": "u"}]}]
    monkeypatch.setattr("urllib.request.urlopen", lambda *_: (_ for _ in ()).throw(AssertionError("unexpected fetch")))
    result = host.resolve_latest("demo")
    assert result["sha256"] is None


def test_version_public_semver_and_release_helpers_remain_deterministic():
    assert version.bump_version("1.2.3", "minor") == "1.3.0"
