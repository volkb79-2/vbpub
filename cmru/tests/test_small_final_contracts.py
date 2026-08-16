"""Exact witnesses for the last small operational branches."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import changelog, handlers, release, tester_gate


def test_release_missing_tag_is_distinguished_from_http_failure():
    client = release.GitHubReleases("o", "r", "t")
    client._request = lambda *args, **kwargs: (404, "missing")
    assert client.get_release_by_tag("demo-v9") is None


def test_release_upload_failure_reports_asset_action(monkeypatch, tmp_path, capsys):
    asset = tmp_path / "a.whl"; asset.write_bytes(b"x")
    client = release.GitHubReleases("o", "r", "t")
    client._request = lambda *args, **kwargs: (500, "bad upload")
    with pytest.raises(SystemExit) as error:
        client.upload_asset("https://upload/{?name}", asset, asset.name)
    assert error.value.code == 1 and "upload asset a.whl" in capsys.readouterr().err


def test_handler_required_environment_error_names_missing_variable(monkeypatch, capsys):
    monkeypatch.delenv("REQUIRED_TEST_VALUE", raising=False)
    with pytest.raises(SystemExit) as error:
        handlers._require_env("REQUIRED_TEST_VALUE")
    assert error.value.code == 1 and "REQUIRED_TEST_VALUE is required" in capsys.readouterr().err


def test_tester_gate_slice_probe_empty_load_state_fails_closed(monkeypatch):
    monkeypatch.setattr(tester_gate.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(tester_gate.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="", stderr="probe stderr", returncode=0))
    ok, note = tester_gate.check_slice_unit("unknown.slice", "probe")
    assert ok is False and "could not determine" in note


def test_changelog_backfill_existing_missing_marker_refuses_overwrite(tmp_path, monkeypatch):
    root = tmp_path / "demo"; root.mkdir()
    path = root / "CHANGES.md"; path.write_text("# hand-authored\n", encoding="utf-8")
    project = SimpleNamespace(name="demo", cwd="demo", changelog="CHANGES.md", prefix="demo-v")
    monkeypatch.setattr(changelog, "_git", lambda *args, **kwargs: "a" * 40)
    monkeypatch.setattr(changelog, "_previous_project_tag", lambda *args: None)
    monkeypatch.setattr(changelog, "_subject_groups", lambda *args, **kwargs: {})
    with pytest.raises(RuntimeError, match="lacks"):
        changelog.backfill_release_changelog(tmp_path, project, "demo-v1.0.0")
