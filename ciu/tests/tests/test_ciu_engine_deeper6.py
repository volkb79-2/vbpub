"""Additional S6 hostdir execution-boundary contracts.

These tests use the public ``create_hostdirs`` entry point.  They model the
real fixed-UID/DooD boundary where the caller can make the directory but cannot
change its owner or mode locally; CIU must then ask the helper to operate on
the daemon-visible physical path, never the logical workspace path.
"""

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def _config() -> dict:
    return {
        "deploy": {"env": {"shared": {"CONTAINER_UID": "1000", "DOCKER_GID": "994"}}},
        "service": {"name": "database", "hostdir": {"data": "vol-data"}},
    }


def test_hostdir_permission_fallback_targets_physical_path(monkeypatch, tmp_path):
    """S6.5: local ownership failures fall back for the physical mount path."""
    repo_root = tmp_path / "logical-workspace"
    physical_root = tmp_path / "host-workspace"
    stack_dir = repo_root / "stacks" / "database"
    stack_dir.mkdir(parents=True)
    (physical_root / "stacks" / "database").mkdir(parents=True)

    helper_calls = []

    def deny_chown(*_args):
        raise PermissionError("fixed-UID volume")

    def deny_chmod(*_args):
        raise PermissionError("fixed-UID volume")

    monkeypatch.setattr(engine.os, "chown", deny_chown)
    monkeypatch.setattr(engine.os, "chmod", deny_chmod)
    monkeypatch.setattr(
        engine,
        "privileged_fs_op",
        lambda path, operation, argument: helper_calls.append((path, operation, argument)),
    )

    config = _config()
    engine.create_hostdirs(
        config,
        stack_dir,
        repo_root=repo_root,
        physical_root=physical_root,
    )

    physical_dir = physical_root / "stacks" / "database" / "vol-data"
    assert config["service"]["hostdir"]["data"] == str(physical_dir)
    assert helper_calls == [
        (physical_dir, "chown", "1000:994"),
        (physical_dir, "chmod", "775"),
    ]


def test_hostdir_uid_1000_created_through_helper_without_systemd_pid1(
    monkeypatch, tmp_path, capsys,
):
    """CIU-164 acceptance: no systemd PID 1 (devcontainer) -> one warning, helper runs, uid 1000."""
    repo_root = tmp_path / "workspace"
    stack_dir = repo_root / "stacks" / "jitsi"
    stack_dir.mkdir(parents=True)
    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(
        engine.governance, "check_slice_unit",
        lambda _n: (None, "systemd is not PID 1; skipping the slice-existence preflight"),
    )
    monkeypatch.setattr(engine.governance, "slice_probe_unavailable", lambda: True)

    def deny(*_args):
        raise PermissionError("not the operator's uid")

    monkeypatch.setattr(engine.os, "chown", deny)
    monkeypatch.setattr(engine.os, "chmod", deny)
    docker_calls: list[list[str]] = []
    monkeypatch.setattr(
        engine.procutil, "docker", lambda argv, *, check: docker_calls.append(list(argv)),
    )
    config = {
        "deploy": {"env": {"shared": {"CONTAINER_UID": "1000", "DOCKER_GID": "994"}}},
        "service": {"name": "web", "hostdir": {
            "config": {"path": "vol-web-config", "uid": 1000, "gid": 1000, "mode": "0750"},
        }},
    }

    engine.create_hostdirs(
        config, stack_dir, repo_root=repo_root, physical_root=repo_root,
    )

    target = stack_dir / "vol-web-config"
    assert target.is_dir()
    runs = [call for call in docker_calls if call[:1] == ["run"]]
    assert runs
    assert any("chown" in call and "1000:1000" in call for call in runs)
    assert all(call[call.index("--cgroup-parent") + 1] == "dev-interactive.slice" for call in runs)
    assert capsys.readouterr().err.count("[WARN] S6.5 root helper cgroup parent") >= 1
