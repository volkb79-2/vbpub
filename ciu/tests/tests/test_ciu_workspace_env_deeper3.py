"""Lifecycle and failed-Docker contracts for ``ciu.workspace_env``.

These tests use only controlled subprocess and file seams.  They never talk
to a Docker daemon, the public-IP endpoint, or the host mount table.
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


def test_failed_docker_discovery_never_selects_stdout_as_physical_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed Docker query must fall back to this repo, not stale output.

    A broken daemon/wrapper can emit a previous devcontainer label on stdout
    despite a nonzero exit.  Accepting it would bind the current repository
    under the wrong host path.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)
    monkeypatch.setattr(workspace_env, "_physical_root_from_mountinfo", lambda _root: None)
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_args, **_kwargs: _result(returncode=1, stdout="/host/unrelated-repo\n", stderr="daemon down"),
    )

    assert workspace_env._detect_physical_repo_root(repo_root) == repo_root.resolve()


def test_failed_docker_inspect_never_exports_tmp_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nonzero inspect cannot make sibling containers use an invented bind."""
    monkeypatch.delenv("HOST_MDT_TMP", raising=False)
    monkeypatch.setenv("DEVCONTAINER_NAME", "ciu-cockpit")
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_args, **_kwargs: _result(returncode=1, stdout="/host/stale-tmp\n", stderr="not found"),
    )

    assert workspace_env._detect_host_mdt_tmp() == ""


def test_bootstrap_env_init_reloads_generated_network_then_probes_tls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generated identity drives the network step, which precedes the TLS probe."""
    env_path = tmp_path / "ciu.env"
    events: list[tuple[str, str | None]] = []

    def generate(root: Path) -> Path:
        assert root == tmp_path
        env_path.write_text(
            'export DOCKER_NETWORK_INTERNAL="generated-net"\n'
            'export PUBLIC_TLS_CRT_PEM="/cert"\n'
            'export PUBLIC_TLS_KEY_PEM="/key"\n',
            encoding="utf-8",
        )
        events.append(("generate", None))
        return env_path

    monkeypatch.delenv("DOCKER_NETWORK_INTERNAL", raising=False)
    monkeypatch.setattr(workspace_env, "generate_ciu_env", generate)
    monkeypatch.setattr(
        workspace_env,
        "ensure_workspace_network",
        lambda name: events.append(("network", name)),
    )
    monkeypatch.setattr(workspace_env, "_check_tls_access", lambda: events.append(("tls", None)))

    assert workspace_env.bootstrap_env_init(tmp_path) == env_path
    assert events == [("generate", None), ("network", "generated-net"), ("tls", None)]


def test_bootstrap_env_init_warns_on_network_failure_but_still_probes_tls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Network setup is best-effort; a TLS accessibility warning remains observable."""
    env_path = tmp_path / "ciu.env"
    env_path.write_text('DOCKER_NETWORK_INTERNAL="generated-net"\n', encoding="utf-8")
    events: list[str] = []
    monkeypatch.delenv("DOCKER_NETWORK_INTERNAL", raising=False)
    monkeypatch.setattr(workspace_env, "generate_ciu_env", lambda _root: env_path)
    monkeypatch.setattr(
        workspace_env,
        "ensure_workspace_network",
        lambda _name: (_ for _ in ()).throw(workspace_env.WorkspaceEnvError("daemon unavailable")),
    )
    monkeypatch.setattr(workspace_env, "_check_tls_access", lambda: events.append("tls"))

    assert workspace_env.bootstrap_env_init(tmp_path) == env_path
    assert "Network setup skipped: daemon unavailable" in capsys.readouterr().out
    assert events == ["tls"]
