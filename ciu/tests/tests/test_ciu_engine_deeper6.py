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
