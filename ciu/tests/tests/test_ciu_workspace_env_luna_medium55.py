"""Focused TLS accessibility failure contracts for ``ciu.workspace_env``."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402


def test_tls_probe_warns_for_each_docker_inaccessible_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cert_path = "/etc/letsencrypt/live/example/fullchain.pem"
    key_path = "/etc/letsencrypt/live/example/privkey.pem"
    monkeypatch.setenv("PUBLIC_TLS_CRT_PEM", cert_path)
    monkeypatch.setenv("PUBLIC_TLS_KEY_PEM", key_path)
    monkeypatch.setenv("DOCKER_GID", "1000")
    monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, "", "permission denied"
        ),
    )

    workspace_env._check_tls_access()

    output = capsys.readouterr().out
    assert f"TLS certificate not accessible to Docker: {cert_path}" in output
    assert f"TLS private key not accessible to Docker: {key_path}" in output


def test_tls_cert_without_docker_gid_reports_unknown_gid(
    tmp_path: Path,
) -> None:
    cert_path = tmp_path / "private-cert.pem"
    cert_path.write_text("certificate", encoding="utf-8")
    cert_path.chmod(0o640)

    with pytest.raises(
        workspace_env.WorkspaceEnvError,
        match=rf"DOCKER_GID \(None\).*{cert_path}",
    ):
        workspace_env.validate_required_certs(
            {"PUBLIC_TLS_CRT_PEM": str(cert_path)}
        )
