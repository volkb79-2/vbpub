"""Remaining public ``ciu deploy --build`` boundary contracts.

The Docker daemon is deliberately replaced at its process boundary.  These
tests retain the user-visible action contract: an empty deploy selection means
"build all", while an unavailable Docker client is a red result rather than a
silent success.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402


def test_build_defaults_to_all_and_reports_success_when_bake_succeeds(monkeypatch, tmp_path, capsys):
    """A profile with no buildable selection still performs CIU's documented all-target bake."""
    commands: list[list[str]] = []

    def successful_docker(argv, **_kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(deploy.procutil, "docker", successful_docker)
    monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")

    assert deploy.action_build(tmp_path, [], use_cache=True) == 0
    assert commands == [["buildx", "bake", "all", "--load", "--set", "*.labels.org.opencontainers.image.revision=abc12345"]]
    assert "build complete" in capsys.readouterr().out


def test_build_reports_red_when_docker_client_is_unavailable(monkeypatch, tmp_path, capsys):
    """Missing Docker cannot make a requested image build look successful."""
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("docker")),
    )

    assert deploy.action_build(tmp_path, [{"path": "applications/api"}], use_cache=True) == 1
    assert "docker not available: docker" in capsys.readouterr().out
