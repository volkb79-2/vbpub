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


def _stat(mode: int, uid: int = 0) -> types.SimpleNamespace:
    return types.SimpleNamespace(st_mode=mode, st_uid=uid)


def _fake_audit_os(
    monkeypatch: pytest.MonkeyPatch,
    *,
    component_results: dict[str, list[int | BaseException]] | None = None,
    parent_stat: object | None = None,
    existing: object | None = None,
    leaf_stat: object | None = None,
    fdopen: Callable[..., io.StringIO] | None = None,
) -> dict[str, object]:
    """Install a narrow directory-FD model and retain its security operations."""
    import topos.actions.execute as execute

    component_results = component_results or {}
    attempts = {name: iter(results) for name, results in component_results.items()}
    parent_stat = parent_stat or _stat(stat.S_IFDIR | 0o700)
    leaf_stat = leaf_stat or _stat(stat.S_IFREG | 0o600)
    calls: dict[str, object] = {"open": [], "mkdir": [], "close": [], "fchmod": []}

    def fake_open(name, flags, mode=None, *, dir_fd=None):
        calls["open"].append((name, flags, mode, dir_fd))
        if name == os.sep:
            return 10
        if name == "audit.jsonl":
            return 12
        result = next(attempts[name])
        if isinstance(result, BaseException):
            raise result
        return result

    def fake_stat(name, *, dir_fd=None, follow_symlinks=False):
        assert (name, dir_fd, follow_symlinks) == ("audit.jsonl", 10, False) or dir_fd != 10
        if existing is None:
            raise FileNotFoundError(name)
        if isinstance(existing, BaseException):
            raise existing
        return existing

    def fake_fstat(fd):
        return leaf_stat if fd == 12 else parent_stat

    monkeypatch.setattr(execute.os, "open", fake_open)
    monkeypatch.setattr(execute.os, "stat", fake_stat)
    monkeypatch.setattr(execute.os, "fstat", fake_fstat)
    monkeypatch.setattr(execute.os, "mkdir", lambda *args, **kwargs: calls["mkdir"].append((args, kwargs)))
    monkeypatch.setattr(execute.os, "close", lambda fd: calls["close"].append(fd))
    monkeypatch.setattr(execute.os, "fchmod", lambda fd, mode: calls["fchmod"].append((fd, mode)))
    monkeypatch.setattr(execute.os, "fdopen", fdopen or (lambda *args, **kwargs: io.StringIO()))
    return calls


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


def test_safe_audit_rejects_unsafe_parent_component(monkeypatch: pytest.MonkeyPatch) -> None:
    import topos.actions.execute as execute

    calls = _fake_audit_os(monkeypatch, component_results={"safe": [11]})
    with pytest.raises(execute._AuditError, match="unsafe path syntax"):
        execute._open_safe_audit(Path("/safe/../audit.jsonl"), require_root_owner=False)
    assert calls["close"] == [10, 11]


def test_safe_audit_creates_private_component_with_nofollow(monkeypatch: pytest.MonkeyPatch) -> None:
    import topos.actions.execute as execute

    calls = _fake_audit_os(
        monkeypatch,
        component_results={"logs": [FileNotFoundError("logs"), 11]},
    )
    fh = execute._open_safe_audit(Path("/logs/audit.jsonl"), require_root_owner=False)
    assert calls["mkdir"] == [(("logs", 0o700), {"dir_fd": 10})]
    assert [entry[0] for entry in calls["open"]] == ["/", "logs", "logs", "audit.jsonl"]
    assert calls["fchmod"] == [(12, 0o600)]
    fh.close()


@pytest.mark.parametrize(
    ("parent_stat", "require_root_owner", "message"),
    (
        (_stat(stat.S_IFDIR | 0o722), False, "parent is not a private directory"),
        (_stat(stat.S_IFDIR | 0o700, uid=1000), True, "parent is not root-owned"),
    ),
)
def test_safe_audit_rejects_unsafe_parent_metadata(
    monkeypatch: pytest.MonkeyPatch,
    parent_stat: object,
    require_root_owner: bool,
    message: str,
) -> None:
    import topos.actions.execute as execute

    calls = _fake_audit_os(monkeypatch, component_results={"logs": [11]}, parent_stat=parent_stat)
    with pytest.raises(execute._AuditError, match=message):
        execute._open_safe_audit(Path("/logs/audit.jsonl"), require_root_owner=require_root_owner)
    assert calls["close"] == [10, 11]


@pytest.mark.parametrize(
    ("existing", "leaf_stat", "require_root_owner", "message"),
    (
        (_stat(stat.S_IFREG | 0o600, uid=1000), _stat(stat.S_IFREG | 0o600), True, "target is not root-owned"),
        (None, _stat(stat.S_IFDIR | 0o600), False, "target is not a regular file"),
        (None, _stat(stat.S_IFREG | 0o644), False, "permissions are too broad"),
        (None, _stat(stat.S_IFREG | 0o600, uid=1000), True, "target is not root-owned"),
    ),
)
def test_safe_audit_rejects_unsafe_existing_or_leaf_metadata(
    monkeypatch: pytest.MonkeyPatch,
    existing: object | None,
    leaf_stat: object,
    require_root_owner: bool,
    message: str,
) -> None:
    import topos.actions.execute as execute

    calls = _fake_audit_os(
        monkeypatch,
        existing=existing,
        leaf_stat=leaf_stat,
    )
    with pytest.raises(execute._AuditError, match=message):
        execute._open_safe_audit(Path("/audit.jsonl"), require_root_owner=require_root_owner)
    assert calls["close"][-1] == 10


def test_safe_audit_keeps_existing_root_owned_leaf_and_rethrows_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    import topos.actions.execute as execute

    calls = _fake_audit_os(
        monkeypatch,
        existing=_stat(stat.S_IFREG | 0o600),
        leaf_stat=_stat(stat.S_IFREG | 0o600),
    )
    fh = execute._open_safe_audit(Path("/audit.jsonl"), require_root_owner=True)
    assert calls["fchmod"] == [(12, 0o600)]
    fh.close()

    interrupt_calls = _fake_audit_os(
        monkeypatch,
        fdopen=lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        execute._open_safe_audit(Path("/audit.jsonl"), require_root_owner=False)
    assert interrupt_calls["close"] == [12, 10]


def test_write_json_record_refuses_unbounded_payload() -> None:
    import topos.actions.execute as execute

    with pytest.raises(execute._AuditError, match="exceeds bounded size"):
        execute._write_json_record(io.StringIO(), {"payload": "x" * 5000})
