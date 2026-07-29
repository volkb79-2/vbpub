"""Reset orphan-cleanup outcome contracts."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def _config() -> dict:
    return {"deploy": {"project_name": "project", "labels": {"prefix": "ciu"}}}


def test_reset_reports_failed_orphan_removal_without_aborting(monkeypatch, tmp_path: Path, capsys):
    """One orphan failure is visible but does not prevent reset cleanup completion."""

    results = iter([
        SimpleNamespace(returncode=0, stdout="", stderr=""),  # compose down
        SimpleNamespace(returncode=0, stdout="stale-api\n", stderr=""),  # ps
        SimpleNamespace(returncode=1, stdout="", stderr="still running"),  # rm
    ])
    monkeypatch.setattr(engine.procutil, "run_cmd", lambda *_args, **_kwargs: next(results))

    engine.reset_service(_config(), tmp_path, assume_yes=True)

    output = capsys.readouterr().out
    assert "Found 1 orphaned containers" in output
    assert "Failed to remove stale-api: still running" in output
    assert "Reset complete" in output


def test_reset_warns_but_completes_when_docker_unavailable_for_orphan_scan(monkeypatch, tmp_path: Path, capsys):
    """The final best-effort orphan scan must not turn a completed reset into a crash."""

    calls = 0

    def run_cmd(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise FileNotFoundError("docker")

    monkeypatch.setattr(engine.procutil, "run_cmd", run_cmd)

    engine.reset_service(_config(), tmp_path, assume_yes=True)

    output = capsys.readouterr().out
    assert "docker unavailable): docker" in output
    assert "Reset complete" in output
