"""Deterministic devcontainer-network attachment contracts for CIU.

These are boundary tests only: Docker is represented by a controlled
``subprocess.run`` seam, never by a daemon or a live container.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TestDevcontainerNetworkAttachment:
    def test_existing_attachment_does_not_issue_second_connect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An already attached cockpit is discovered, not reconnected."""
        monkeypatch.setenv("ENV_TYPE", "devcontainer")
        monkeypatch.setenv("DEVCONTAINER_NAME", "ciu-cockpit")
        monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
        calls: list[list[str]] = []

        def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(argv)
            return _result(stdout="other ciu-cockpit ")

        monkeypatch.setattr(workspace_env.subprocess, "run", fake_run)

        workspace_env._connect_devcontainer_to_network("ciu-internal")

        assert calls == [[
            "docker", "network", "inspect", "ciu-internal", "--format",
            "{{range .Containers}}{{.Name}} {{end}}",
        ]]

    def test_missing_attachment_connects_the_detected_cockpit_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A devcontainer absent from the network is attached by its exact name."""
        monkeypatch.setenv("ENV_TYPE", "devcontainer")
        monkeypatch.setenv("DEVCONTAINER_NAME", "ciu-cockpit")
        monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
        calls: list[list[str]] = []

        def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(argv)
            if argv[1:3] == ["network", "inspect"]:
                return _result(stdout="other ")
            return _result()

        monkeypatch.setattr(workspace_env.subprocess, "run", fake_run)

        workspace_env._connect_devcontainer_to_network("ciu-internal")

        assert calls == [
            [
                "docker", "network", "inspect", "ciu-internal", "--format",
                "{{range .Containers}}{{.Name}} {{end}}",
            ],
            ["docker", "network", "connect", "ciu-internal", "ciu-cockpit"],
        ]

    def test_connect_failure_warns_without_breaking_environment_generation(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Attachment is best-effort: a daemon refusal remains actionable but non-fatal."""
        monkeypatch.setenv("ENV_TYPE", "devcontainer")
        monkeypatch.setenv("DEVCONTAINER_NAME", "ciu-cockpit")
        monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)

        def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if argv[1:3] == ["network", "inspect"]:
                return _result(stdout="")
            return _result(returncode=1, stderr="permission denied")

        monkeypatch.setattr(workspace_env.subprocess, "run", fake_run)

        workspace_env._connect_devcontainer_to_network("ciu-internal")

        assert "Failed to connect devcontainer to ciu-internal: permission denied" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("env", "warning"),
        [
            ({}, "Devcontainer name not detected; skipping network attach."),
            ({"DEVCONTAINER_NAME": "ciu-cockpit"}, "Docker not available; skipping devcontainer network attach."),
        ],
    )
    def test_missing_attachment_prerequisite_degrades_without_subprocess(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        env: dict[str, str],
        warning: str,
    ) -> None:
        """Absent identity or Docker cannot turn env generation into a hard failure."""
        monkeypatch.setenv("ENV_TYPE", "devcontainer")
        monkeypatch.delenv("DEVCONTAINER_NAME", raising=False)
        monkeypatch.delenv("HOSTNAME", raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(workspace_env, "_docker_available", lambda: False)
        monkeypatch.setattr(
            workspace_env.subprocess,
            "run",
            lambda *_args, **_kwargs: pytest.fail("unavailable prerequisites must not invoke Docker"),
        )

        workspace_env._connect_devcontainer_to_network("ciu-internal")

        assert warning in capsys.readouterr().out
