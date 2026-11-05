"""Exact behavioral coverage for the action execution primitive boundary."""

from __future__ import annotations

import dataclasses
import io
import math
import os
import stat
import types
from collections.abc import Callable
from pathlib import Path

import pytest


def _open_fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def _owner_override(monkeypatch: pytest.MonkeyPatch, *, dir_uid: int, file_uid: int) -> None:
    """Report chosen owners from real stats (the sandbox user is never root)."""
    import topos.actions.execute as execute

    real_stat, real_fstat = os.stat, os.fstat

    def with_uid(result: os.stat_result) -> os.stat_result:
        uid = dir_uid if stat.S_ISDIR(result.st_mode) else file_uid
        fields = list(result)
        fields[4] = uid
        return os.stat_result(fields)

    monkeypatch.setattr(execute.os, "stat", lambda *a, **k: with_uid(real_stat(*a, **k)))
    monkeypatch.setattr(execute.os, "fstat", lambda fd: with_uid(real_fstat(fd)))


def test_output_identity_timeout_and_audit_record_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    import topos.actions.execute as execute

    assert execute._bound_output(object()) == ""
    assert execute._decode_output(b"bad:\xff") == "bad:\ufffd"
    monkeypatch.setattr(execute.pwd, "getpwuid", lambda uid: (_ for _ in ()).throw(KeyError(uid)))
    assert execute._production_identity().user == "unknown"

    for bad, message in (
        (object(), "identity provider returned an invalid identity"),
        (execute.AuditIdentity(True, "root"), "identity uid is invalid"),
        (execute.AuditIdentity(0, ""), "identity user is invalid"),
        (execute.AuditIdentity(0, "bad\nuser"), "identity user contains control characters"),
    ):
        with pytest.raises(ValueError, match=message):
            execute._coerce_identity(bad)
    with pytest.raises(ValueError, match="timeout must be a finite positive number"):
        execute._validate_timeout("fast")
    with pytest.raises(execute._AuditError, match="non-finite"):
        execute._audit_record(
            identity=execute.AuditIdentity(0, "root"),
            kind="docker-start",
            target="demo",
            argv=("/usr/bin/docker", "start", "demo"),
            clock=lambda: math.nan,
            stage="pre",
        )

    record = execute._audit_record(
        identity=execute.AuditIdentity(0, "root"),
        kind="docker-start",
        target="demo",
        argv=("/usr/bin/docker", "start", "demo"),
        clock=lambda: 12.5,
        stage="post",
        result=execute.ExecuteResult("docker-start", "demo", (), 0, "", "", "success", 99.0),
    )
    assert record == {
        "ts": 12.5,
        "uid": 0,
        "user": "root",
        "kind": "docker-start",
        "target": "demo",
        "argv": ["/usr/bin/docker", "start", "demo"],
        "mode": "execute",
        "admin": True,
        "stage": "post",
        "outcome": "success",
        "action_outcome": "success",
        "returncode": 0,
        "duration_s": 30.0,
    }


def test_validation_refuses_forged_or_nonimmutable_plans(monkeypatch: pytest.MonkeyPatch) -> None:
    import topos.actions.execute as execute
    from topos.actions.catalog import ActionKind
    from topos.actions.preview import build_preview

    plan = build_preview("docker-start", "demo")
    cases = (
        (dataclasses.replace(plan, target="other"), "execution plan does not match"),
        (dataclasses.replace(plan, argv=list(plan.argv)), "execution plan is not an immutable"),
        (dataclasses.replace(plan, argv=(plan.argv[0], 3, "demo")), "execution plan argv is invalid"),
        (dataclasses.replace(plan, argv=(plan.argv[0], "start", "other")), "execution plan argv does not match"),
    )
    for forged, message in cases:
        with pytest.raises(ValueError, match=message):
            execute._validate_plan(forged, ActionKind.DOCKER_START, "demo")

    monkeypatch.setattr(execute, "DOCKER_EXECUTABLE", "docker")
    with pytest.raises(ValueError, match="fixed absolute path"):
        execute._validate_plan(plan, ActionKind.DOCKER_START, "demo")

    monkeypatch.undo()
    monkeypatch.setitem(
        execute.ACTION_CATALOG,
        ActionKind.DOCKER_START,
        types.SimpleNamespace(builder=lambda target: [plan.argv[0], "start", target, "extra"]),
    )
    four_part = dataclasses.replace(plan, argv=(plan.argv[0], "start", "demo", "extra"))
    with pytest.raises(ValueError, match="argv shape"):
        execute._validate_plan(four_part, ActionKind.DOCKER_START, "demo")


def test_safe_audit_refuses_unsafe_path_and_unavailable_secure_open(monkeypatch: pytest.MonkeyPatch) -> None:
    import topos.actions.execute as execute

    with pytest.raises(execute._AuditError, match="must be absolute"):
        execute._open_safe_audit(Path("audit.jsonl"), require_root_owner=False)
    monkeypatch.delattr(execute.os, "O_NOFOLLOW")
    with pytest.raises(execute._AuditError, match="secure audit open is unavailable"):
        execute._open_safe_audit(Path("/audit.jsonl"), require_root_owner=False)


def test_safe_audit_rejects_unsafe_parent_component(tmp_path: Path) -> None:
    import topos.actions.execute as execute

    before = _open_fd_count()
    with pytest.raises(execute._AuditError, match="unsafe path syntax"):
        execute._open_safe_audit(Path(f"{tmp_path}/safe/../audit.jsonl"), require_root_owner=False)
    assert _open_fd_count() == before  # no directory fd leaked on the refusal


def test_safe_audit_creates_private_directory_and_file_on_real_filesystem(tmp_path: Path) -> None:
    import topos.actions.execute as execute

    target = tmp_path / "logs" / "audit.jsonl"
    fh = execute._open_safe_audit(target, require_root_owner=False)
    fh.write("x\n")
    fh.close()

    assert stat.S_IMODE((tmp_path / "logs").stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text() == "x\n"


def test_safe_audit_refuses_symlinked_parent_component(tmp_path: Path) -> None:
    import topos.actions.execute as execute

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (tmp_path / "logs").symlink_to(elsewhere)

    with pytest.raises(OSError):  # O_NOFOLLOW on a symlinked directory component
        execute._open_safe_audit(tmp_path / "logs" / "audit.jsonl", require_root_owner=False)
    assert list(elsewhere.iterdir()) == []


def test_safe_audit_refuses_symlinked_leaf(tmp_path: Path) -> None:
    import topos.actions.execute as execute

    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    logs = tmp_path / "logs"
    logs.mkdir(mode=0o700)
    (logs / "audit.jsonl").symlink_to(victim)

    with pytest.raises(execute._AuditError, match="not a regular file"):
        execute._open_safe_audit(logs / "audit.jsonl", require_root_owner=False)
    assert victim.read_text() == "keep"


def test_safe_audit_refuses_group_writable_parent_and_broad_leaf(tmp_path: Path) -> None:
    import topos.actions.execute as execute

    logs = tmp_path / "logs"
    logs.mkdir()
    logs.chmod(0o770)
    with pytest.raises(execute._AuditError, match="not a private directory"):
        execute._open_safe_audit(logs / "audit.jsonl", require_root_owner=False)

    logs.chmod(0o700)
    leaf = logs / "audit.jsonl"
    leaf.write_text("")
    leaf.chmod(0o644)
    with pytest.raises(execute._AuditError, match="permissions are too broad"):
        execute._open_safe_audit(leaf, require_root_owner=False)


def test_safe_audit_rejects_group_or_world_writable_parent_without_sticky(tmp_path: Path) -> None:
    import topos.actions.execute as execute

    logs = tmp_path / "logs"
    logs.mkdir()
    logs.chmod(0o722)
    before = _open_fd_count()
    with pytest.raises(execute._AuditError, match="parent is not a private directory"):
        execute._open_safe_audit(logs / "audit.jsonl", require_root_owner=False)
    assert _open_fd_count() == before


def test_safe_audit_rejects_parent_not_owned_by_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import topos.actions.execute as execute

    logs = tmp_path / "logs"
    logs.mkdir(mode=0o700)
    _owner_override(monkeypatch, dir_uid=1000, file_uid=0)
    before = _open_fd_count()
    with pytest.raises(execute._AuditError, match="parent is not root-owned"):
        execute._open_safe_audit(logs / "audit.jsonl", require_root_owner=True)
    assert _open_fd_count() == before


def test_safe_audit_rejects_existing_leaf_not_owned_by_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import topos.actions.execute as execute

    logs = tmp_path / "logs"
    logs.mkdir(mode=0o700)
    leaf = logs / "audit.jsonl"
    leaf.write_text("kept\n")
    leaf.chmod(0o600)
    _owner_override(monkeypatch, dir_uid=0, file_uid=1000)
    before = _open_fd_count()
    with pytest.raises(execute._AuditError, match="target is not root-owned"):
        execute._open_safe_audit(leaf, require_root_owner=True)
    assert _open_fd_count() == before
    assert leaf.read_text() == "kept\n"


def test_safe_audit_rejects_new_leaf_not_owned_by_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import topos.actions.execute as execute

    logs = tmp_path / "logs"
    logs.mkdir(mode=0o700)
    _owner_override(monkeypatch, dir_uid=0, file_uid=1000)
    before = _open_fd_count()
    with pytest.raises(execute._AuditError, match="target is not root-owned"):
        execute._open_safe_audit(logs / "audit.jsonl", require_root_owner=True)
    assert _open_fd_count() == before


def test_safe_audit_rejects_non_regular_existing_target(tmp_path: Path) -> None:
    import topos.actions.execute as execute

    logs = tmp_path / "logs"
    logs.mkdir(mode=0o700)
    os.mkfifo(logs / "audit.jsonl", 0o600)
    before = _open_fd_count()
    with pytest.raises(execute._AuditError, match="target is not a regular file"):
        execute._open_safe_audit(logs / "audit.jsonl", require_root_owner=False)
    assert _open_fd_count() == before


def test_safe_audit_appends_to_existing_private_leaf_and_closes_fd_on_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import topos.actions.execute as execute

    logs = tmp_path / "logs"
    logs.mkdir(mode=0o700)
    leaf = logs / "audit.jsonl"
    leaf.write_text("one\n")
    leaf.chmod(0o600)
    fh = execute._open_safe_audit(leaf, require_root_owner=False)
    fh.write("two\n")
    fh.close()
    assert leaf.read_text() == "one\ntwo\n"
    assert stat.S_IMODE(leaf.stat().st_mode) == 0o600

    def interrupted(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(execute.os, "fdopen", interrupted)
    before = _open_fd_count()
    with pytest.raises(KeyboardInterrupt):
        execute._open_safe_audit(leaf, require_root_owner=False)
    assert _open_fd_count() == before  # leaf fd and directory fd both closed


def test_write_json_record_refuses_unbounded_payload() -> None:
    import topos.actions.execute as execute

    with pytest.raises(execute._AuditError, match="exceeds bounded size"):
        execute._write_json_record(io.StringIO(), {"payload": "x" * 5000})
