"""Exact witnesses for operational residual branches."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import handlers, release, tester_gate
from cmru.agent.consul_backend import ConsulBackend


def test_handlers_validate_commands_emit_resolved_artifact_contract(monkeypatch, capsys):
    fake = SimpleNamespace(
        version="1.2.3", asset="demo-1.2.3.whl", url="https://asset",
        sha256_url="https://hash",
    )
    monkeypatch.setenv("GITHUB_USERNAME", "owner")
    monkeypatch.setenv("GITHUB_REPO", "repo")
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(handlers, "validate_latest_release", lambda *a, **k: fake.__dict__)
    handlers.cmd_wheel_validate(SimpleNamespace(prefix="demo"))
    output = capsys.readouterr().out
    assert "DEMO_WHEEL_NAME=demo-1.2.3.whl" in output
    assert "DEMO_WHEEL_SHA256_URL=https://hash" in output


def test_handlers_tarball_validate_uses_explicit_suffix(monkeypatch, capsys):
    calls = []
    monkeypatch.setenv("GITHUB_USERNAME", "owner")
    monkeypatch.setenv("GITHUB_REPO", "repo")
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *a, **k: object())
    monkeypatch.setattr(handlers, "validate_latest_release", lambda gh, prefix, **kw: calls.append(kw) or {
        "version": "1", "asset": "a.tar", "url": "u",
    })
    handlers.cmd_tarball_validate(SimpleNamespace(prefix="demo", artifact_suffix=".tar"))
    assert calls == [{"artifact_suffix": ".tar"}]
    assert "DEMO_TARBALL_NAME=a.tar" in capsys.readouterr().out


def test_release_latest_resolution_ignores_drafts_and_prereleases():
    gh = SimpleNamespace(list_releases=lambda: [
        {"tag_name": "demo-v1.0.0", "draft": True, "assets": []},
        {"tag_name": "demo-v1.1.0", "prerelease": True, "assets": []},
        {"tag_name": "other-v9.0.0", "assets": []},
    ])
    client = release.GitHubReleases("o", "r", "t")
    client.list_releases = gh.list_releases
    assert client.resolve_latest("demo-v") is None


def test_tester_gate_slice_probe_surfaces_probe_failure_and_nonloaded_units(monkeypatch):
    monkeypatch.setattr(tester_gate.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(tester_gate.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("probe failed")))
    ok, note = tester_gate.check_slice_unit("dev.slice", "probe")
    assert ok is False and "probe failed" in note
    monkeypatch.setattr(tester_gate.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="LoadState=not-found\nFragmentPath=\n", stderr="", returncode=0,
    ))
    ok, note = tester_gate.check_slice_unit("missing.slice", "probe")
    assert ok is False and "not installed" in note


def test_consul_read_absent_observed_and_signature_are_none():
    backend = ConsulBackend()
    backend._get = lambda *args, **kwargs: (404, b"", {})
    assert backend.read_observed("node", "land") is None
    assert backend.read_desired_sig("node", "land") is None


def test_release_read_wheel_version_without_metadata_fails_exactly(tmp_path, capsys):
    import zipfile
    wheel = tmp_path / "demo.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("README", "demo")
    with pytest.raises(SystemExit) as error:
        release.read_wheel_version(wheel)
    assert error.value.code == 1
    assert "No Version field in demo.whl METADATA" in capsys.readouterr().err


def test_handlers_wheel_glob_normalizes_distribution_name():
    assert handlers._wheel_glob("demo-tools") == "demo_tools-*.whl"


def test_tester_gate_unloaded_probe_without_docker_is_explicit_skip(monkeypatch):
    monkeypatch.setattr(tester_gate.shutil, "which", lambda _: None)
    ok, note = tester_gate.check_slice_unit("dev.slice", "probe")
    assert ok is None and "no docker" in note
