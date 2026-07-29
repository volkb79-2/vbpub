"""Thin-activation cleanup fallback contract."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import activate  # noqa: E402


def test_successful_scp_push_stays_successful_when_local_bundle_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A best-effort local cleanup failure cannot misreport a completed push.

    Once remote mkdir, scp, and extraction have succeeded, the Passenger-host
    bundle is usable.  A local filesystem race while deleting the transient
    tarball must therefore preserve the successful activation result rather
    than falsely asking an operator to retry a deployment that already landed.
    """
    tarball = tmp_path / "bundle.tar.gz"
    remote_commands: list[str] = []
    monkeypatch.setattr(activate, "make_tarball", lambda *_args, **_kwargs: str(tarball))
    monkeypatch.setattr(
        activate,
        "ssh_exec",
        lambda _host, argv, *, config, repo_root: remote_commands.append(argv[0]) or 0,
    )
    monkeypatch.setattr(activate, "scp_file", lambda *_args, **_kwargs: 0)

    def cleanup_busy(_path: str) -> None:
        raise OSError("filesystem busy")

    monkeypatch.setattr(activate.os, "unlink", cleanup_busy)

    assert activate._push_scp({}, "/local/repo", "/srv/app", config={}, repo_root=tmp_path) == 0
    assert len(remote_commands) == 2
    assert remote_commands[0].startswith("mkdir -p /srv/app")
    assert "tar xzf" in remote_commands[1]
