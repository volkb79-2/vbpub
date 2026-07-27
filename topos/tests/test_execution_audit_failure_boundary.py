"""Behavioural failure contracts for execution-audit writes."""

from __future__ import annotations

import pytest

from topos.actions.execute import (
    AuditIdentity,
    ExecuteResult,
    _AuditError,
    _write_execution_audit_post,
    _write_execution_audit_pre,
)


class _Handle:
    def __init__(self, *, close_error: OSError | None = None) -> None:
        self.close_error = close_error
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


def _result() -> ExecuteResult:
    return ExecuteResult(
        kind="docker-stop",
        target="container",
        argv=("/usr/bin/docker", "stop", "container"),
        returncode=0,
        stdout="",
        stderr="",
        outcome="success",
        duration_s=0.1,
    )


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (OSError("disk failed"), "pre-audit write failed"),
        (_AuditError("bounded typed failure"), "bounded typed failure"),
    ],
)
def test_pre_audit_normalises_raw_failures_preserves_typed_failures_and_closes(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    expected: str,
) -> None:
    from topos.actions import execute

    handle = _Handle()
    monkeypatch.setattr(execute, "_open_safe_audit", lambda *_args, **_kwargs: handle)
    monkeypatch.setattr(
        execute,
        "_write_json_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(_AuditError, match=f"^{expected}$"):
        _write_execution_audit_pre(
            "/tmp/audit.jsonl",
            identity=AuditIdentity(1000, "tester"),
            kind="docker-stop",
            target="container",
            argv=("/usr/bin/docker", "stop", "container"),
            clock=lambda: 1.0,
        )

    assert handle.close_count == 1


def test_post_audit_normalises_write_failure_and_swallows_close_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from topos.actions import execute

    handle = _Handle(close_error=OSError("close failed"))
    monkeypatch.setattr(
        execute,
        "_write_json_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk failed")),
    )

    with pytest.raises(_AuditError, match="^post-audit write failed$"):
        _write_execution_audit_post(
            handle,
            identity=AuditIdentity(1000, "tester"),
            kind="docker-stop",
            target="container",
            argv=("/usr/bin/docker", "stop", "container"),
            result=_result(),
            clock=lambda: 1.0,
        )

    assert handle.close_count == 1


def test_post_audit_success_closes_handle_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from topos.actions import execute

    handle = _Handle()
    monkeypatch.setattr(execute, "_write_json_record", lambda *_args, **_kwargs: None)

    _write_execution_audit_post(
        handle,
        identity=AuditIdentity(1000, "tester"),
        kind="docker-stop",
        target="container",
        argv=("/usr/bin/docker", "stop", "container"),
        result=_result(),
        clock=lambda: 1.0,
    )

    assert handle.close_count == 1
