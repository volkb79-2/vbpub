"""Privileged filesystem-helper and injected ownership contracts."""

from __future__ import annotations

from pathlib import Path

from ciu import engine


def test_privileged_fs_op_runs_exact_root_helper_argv(monkeypatch, tmp_path: Path):
    """A fallback chown/chmod operation mounts only its physical target at `/t`."""

    calls: list[tuple[list[str], bool]] = []
    monkeypatch.setattr(
        engine.procutil,
        "docker",
        lambda argv, *, check: calls.append((argv, check)),
    )

    engine.privileged_fs_op(tmp_path / "volume", "chown", "999:999")

    assert calls == [(
        ["run", "--rm", "-v", f"{tmp_path / 'volume'}:/t", "alpine", "chown", "999:999", "/t"],
        True,
    )]


def test_chown_with_injected_seam_uses_seam_and_never_calls_os_chown(monkeypatch, tmp_path: Path):
    """Tests/callers can supply ownership behavior without mutating host ownership."""

    calls: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(
        engine.os,
        "chown",
        lambda *_args: (_ for _ in ()).throw(AssertionError("native chown must not run")),
    )

    engine._chown_with_fallback(
        tmp_path / "logical",
        999,
        998,
        physical_path=tmp_path / "physical",
        chown_fn=lambda path, uid, gid: calls.append((path, uid, gid)),
    )

    assert calls == [(tmp_path / "logical", 999, 998)]
