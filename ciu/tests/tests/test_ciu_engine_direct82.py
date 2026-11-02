"""Privileged filesystem-helper and injected ownership contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from ciu import engine


def test_privileged_fs_op_runs_exact_root_helper_argv(monkeypatch, tmp_path: Path):
    """A fallback chown/chmod operation mounts only its physical target at `/t`."""

    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(engine.governance, "check_slice_unit", lambda _name: (True, "loaded"))
    calls: list[tuple[list[str], bool]] = []
    monkeypatch.setattr(
        engine.procutil,
        "docker",
        lambda argv, *, check: calls.append((argv, check)),
    )

    engine.privileged_fs_op(tmp_path / "volume", "chown", "999:999")

    assert calls == [(
        ["run", "--rm", "--cgroup-parent", "dev-interactive.slice",
         "-v", f"{tmp_path / 'volume'}:/t", "alpine", "chown", "999:999", "/t"],
        True,
    )]


def test_privileged_rmtree_uses_loaded_slice_and_refuses_missing_bind_targets(
    monkeypatch, tmp_path: Path
):
    target = tmp_path / "root-owned"
    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    checked = []
    monkeypatch.setattr(
        engine.governance, "check_slice_unit",
        lambda name: checked.append(name) or (True, "loaded"),
    )
    calls = []
    monkeypatch.setattr(
        engine.procutil, "docker",
        lambda argv, *, check: calls.append((argv, check)),
    )

    engine.privileged_rmtree(target)

    assert checked == ["dev-interactive.slice"]
    assert calls == [(
        [
            "run", "--rm", "--cgroup-parent", "dev-interactive.slice",
            "--mount", f"type=bind,src={tmp_path},dst=/t", "alpine", "sh", "-c",
            "set -eu; target=$1; if [ ! -e \"$target\" ] && [ ! -L \"$target\" ]; "
            "then echo \"refusing absent bind target: $target\" >&2; exit 42; fi; "
            "exec rm -rf -- \"$target\"",
            "ciu-rmtree", "/t/root-owned",
        ],
        True,
    )]


def test_privileged_rmtree_fails_closed_without_slice_or_for_unsafe_path(
    monkeypatch, tmp_path: Path
):
    calls = []
    monkeypatch.delenv("CGROUP_PARENT_DEV_INTERACTIVE", raising=False)
    monkeypatch.setattr(
        engine.procutil, "docker", lambda argv, **kwargs: calls.append((argv, kwargs))
    )
    with pytest.raises(RuntimeError, match="CGROUP_PARENT_DEV_INTERACTIVE is required"):
        engine.privileged_rmtree(tmp_path / "target")
    assert calls == []

    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "not/a.slice")
    with pytest.raises(RuntimeError, match="not a slice unit name"):
        engine.privileged_rmtree(tmp_path / "target")
    assert calls == []

    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(engine.governance, "check_slice_unit", lambda _name: (None, "unknown"))
    with pytest.raises(RuntimeError, match="not verified loaded"):
        engine.privileged_rmtree(tmp_path / "target")
    assert calls == []

    monkeypatch.setattr(engine.governance, "check_slice_unit", lambda _name: (True, "loaded"))
    with pytest.raises(RuntimeError, match="absolute child path"):
        engine.privileged_rmtree(Path("relative-target"))
    with pytest.raises(RuntimeError, match="containing ','"):
        engine.privileged_rmtree(tmp_path / "comma,parent" / "target")
    assert calls == []


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
