"""Privilege and invocation contracts for CIU certificate permission setup.

The helper is deliberately exercised through controlled process boundaries: it
must never call a real privileged shell script from the test suite.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402


def test_update_cert_permissions_requires_root_before_script_lookup_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unprivileged caller gets the actionable command and cannot run bash."""
    monkeypatch.setattr(workspace_env.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("unprivileged invocation must not execute a script"),
    )

    with pytest.raises(workspace_env.WorkspaceEnvError, match=r"requires root.*sudo ciu --update-cert-permission"):
        workspace_env.update_cert_permissions(tmp_path, "example.test")


def test_update_cert_permissions_requires_the_repo_owned_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root does not silently succeed when the checkout lacks its setup script."""
    monkeypatch.setattr(workspace_env.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("missing script must not execute bash"),
    )

    with pytest.raises(workspace_env.WorkspaceEnvError, match=r"Certificate permission script not found"):
        workspace_env.update_cert_permissions(tmp_path)


def test_update_cert_permissions_runs_repo_script_with_optional_fqdn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root runs only the repository script and preserves a supplied FQDN as one argv item."""
    script = tmp_path / "scripts" / "setup-letsencrypt-permissions.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(workspace_env.os, "geteuid", lambda: 0)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(workspace_env.subprocess, "run", fake_run)

    workspace_env.update_cert_permissions(tmp_path, "api.example.test")
    workspace_env.update_cert_permissions(tmp_path)

    assert calls == [
        (["bash", str(script), "api.example.test"], {"check": True}),
        (["bash", str(script)], {"check": True}),
    ]
