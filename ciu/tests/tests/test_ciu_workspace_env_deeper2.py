"""Additional hermetic fallback contracts for ``ciu.workspace_env``.

These tests exercise controlled filesystem/subprocess boundaries only.  They
never inspect a live Docker daemon or make a network request.
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


def test_mountinfo_decodes_tab_newline_and_backslash_before_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mountinfo-escaped path must remain a usable physical bind mapping."""
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "9 1 0:1 /host/a\\134b\\011tab\\012line /work/a\\134b\\011tab\\012line rw - ext4 /dev/x rw\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(workspace_env, "_MOUNTINFO_PATH", mountinfo)

    assert workspace_env._physical_root_from_mountinfo(
        Path("/work/a\\b\ttab\nline/nested")
    ) == Path("/host/a\\b\ttab\nline/nested")


def test_docker_available_requires_binary_and_successful_info(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Docker binary that cannot talk to its daemon is not availability."""
    monkeypatch.setattr(workspace_env.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(workspace_env.subprocess, "run", lambda *_args, **_kwargs: _result(returncode=1))
    assert workspace_env._docker_available() is False

    monkeypatch.setattr(workspace_env.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("no binary must not invoke docker"),
    )
    assert workspace_env._docker_available() is False


@pytest.mark.usefixtures("real_network_side_effects")  # CIU-87 gate opt-out
def test_network_existing_is_not_recreated_and_empty_name_is_rejected(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing named network is preserved; an absent identity is a hard error."""
    monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda argv, **_kwargs: calls.append(argv) or _result(),
    )

    workspace_env._ensure_network_exists("ciu-net")
    assert calls == [["docker", "network", "inspect", "ciu-net"]]
    with pytest.raises(workspace_env.WorkspaceEnvError, match="missing or empty"):
        workspace_env._ensure_network_exists("")


@pytest.mark.usefixtures("real_network_side_effects")  # CIU-87 gate opt-out
def test_network_unavailable_fails_before_inspecting_daemon(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Network creation reports unavailable Docker rather than shelling out anyway."""
    monkeypatch.setattr(workspace_env, "_docker_available", lambda: False)
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("unavailable Docker must not be called"),
    )

    with pytest.raises(workspace_env.WorkspaceEnvError, match="Docker not available"):
        workspace_env._ensure_network_exists("ciu-net")


@pytest.mark.parametrize(
    ("env", "warning"),
    [
        ({"PUBLIC_TLS_CRT_PEM": "/cert"}, None),
        (
            {"PUBLIC_TLS_CRT_PEM": "/cert", "PUBLIC_TLS_KEY_PEM": "/key"},
            "Docker not available; skipping TLS accessibility checks.",
        ),
        (
            {"PUBLIC_TLS_CRT_PEM": "/cert", "PUBLIC_TLS_KEY_PEM": "/key"},
            "DOCKER_GID not set; skipping TLS accessibility checks.",
        ),
    ],
)
def test_tls_probe_fallbacks_do_not_run_privileged_test_containers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env: dict[str, str],
    warning: str | None,
) -> None:
    """Incomplete TLS state and unavailable identity safely avoid Docker runs."""
    for key in ("PUBLIC_TLS_CRT_PEM", "PUBLIC_TLS_KEY_PEM", "DOCKER_GID", "CONTAINER_GID"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # The second parametrized state is Docker-unavailable; the third is
    # Docker-available but lacks both supported GID sources.
    docker_available = warning != "Docker not available; skipping TLS accessibility checks."
    monkeypatch.setattr(workspace_env, "_docker_available", lambda: docker_available)
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("fallback path must not launch a test container"),
    )

    workspace_env._check_tls_access()
    output = capsys.readouterr().out
    assert (warning in output) if warning else output == ""
