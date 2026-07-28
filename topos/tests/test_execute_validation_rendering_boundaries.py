"""Boundary contracts for safe action result rendering and stale validation."""

from __future__ import annotations

from pathlib import Path

from topos.actions.execute import (
    AuditIdentity,
    ExecuteResult,
    _MAX_OUTPUT_CHARS,
    execute_set_property,
    render_result_text,
    result_to_jsonable,
)


def _success_runner(argv: tuple[str, ...], *, timeout: float) -> ExecuteResult:
    return ExecuteResult("", "", argv, 0, "runner output", "", "success", 0.0)


def test_render_and_json_keep_optional_audit_fields_and_child_output_bounded() -> None:
    """Audit failures remain visible without allowing a child to flood output."""
    result = ExecuteResult(
        kind="docker-stop",
        target="demo",
        argv=("/usr/bin/docker", "stop", "demo"),
        returncode=None,
        stdout="out" * (_MAX_OUTPUT_CHARS + 1),
        stderr="err" * (_MAX_OUTPUT_CHARS + 1),
        outcome="audit_failure",
        duration_s=1.23456,
        action_outcome="success",
        audit_outcome="post_failure",
        audit_error="durable audit write failed",
    )

    rendered = render_result_text(result)
    encoded = result_to_jsonable(result)

    assert "Audit: post_failure" in rendered
    assert "Audit error: durable audit write failed" in rendered
    assert "--- stdout ---" in rendered
    assert "--- stderr ---" in rendered
    assert encoded["action_outcome"] == "success"
    assert encoded["audit_outcome"] == "post_failure"
    assert encoded["duration_s"] == 1.235
    assert str(encoded["stdout"]).endswith(" ... (truncated)")
    assert str(encoded["stderr"]).endswith(" ... (truncated)")


def test_set_property_executes_when_fresh_value_still_matches_preview(tmp_path: Path) -> None:
    """A matching post-audit re-read must not be mistaken for a stale plan."""
    called: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], *, timeout: float) -> ExecuteResult:
        called.append(argv)
        return _success_runner(argv, timeout=timeout)

    result = execute_set_property(
        "demo.service",
        property_name="memory.high",
        property_value="1024",
        persistence="persistent",
        planned_current_value="512",
        current_value_reader=lambda unit: "512",
        admin=True,
        confirm="EXECUTE",
        audit_path=tmp_path / "audit.jsonl",
        root_check=lambda: True,
        identity=lambda: AuditIdentity(0, "tester"),
        clock=lambda: 10.0,
        runner=runner,
    )

    assert result.outcome == "success"
    assert result.action_outcome == "success"
    assert called == [
        ("/usr/bin/systemctl", "set-property", "demo.service", "memory.high=1024")
    ]


def test_set_property_refuses_when_stale_value_revalidation_cannot_read(tmp_path: Path) -> None:
    """An unreadable stale-check source must never permit the systemd mutation."""
    called: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], *, timeout: float) -> ExecuteResult:
        called.append(argv)
        return _success_runner(argv, timeout=timeout)

    def unreadable_current_value(unit: str) -> str:
        raise OSError("systemctl unavailable")

    result = execute_set_property(
        "demo.service",
        property_name="memory.high",
        property_value="1024",
        persistence="persistent",
        planned_current_value="512",
        current_value_reader=unreadable_current_value,
        admin=True,
        confirm="EXECUTE",
        audit_path=tmp_path / "audit.jsonl",
        root_check=lambda: True,
        identity=lambda: AuditIdentity(0, "tester"),
        clock=lambda: 10.0,
        runner=runner,
    )

    assert result.outcome == "refusal"
    assert result.action_outcome == "refusal"
    assert "could not be read (OSError)" in result.stderr
    assert called == []
