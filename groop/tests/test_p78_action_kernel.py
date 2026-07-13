"""P78 regression tests for the shared action execution chain."""

from __future__ import annotations

import inspect
import json
from pathlib import Path


def _success_runner(argv, *, timeout=30.0):
    from groop.actions.execute import ExecuteResult

    return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)


def test_all_public_executors_use_the_one_private_chain() -> None:
    import groop.actions.execute as execute

    for public_executor in (
        execute.execute_plan,
        execute.execute_set_property,
        execute.execute_kill,
        execute.execute_update,
    ):
        source = inspect.getsource(public_executor)
        assert "_execute_gated(" in source
        assert "_write_execution_audit_" not in source
        assert "_default_runner" not in source


def test_stale_revalidation_preserves_the_existing_two_record_audit_trail(
    tmp_path: Path,
) -> None:
    from groop.actions.execute import execute_set_property

    called = []

    def runner(argv, *, timeout=30.0):
        called.append(argv)
        return _success_runner(argv, timeout=timeout)

    audit_path = tmp_path / "audit.jsonl"
    result = execute_set_property(
        "demo.service",
        property_name="memory.high",
        property_value="1024",
        planned_current_value="1024",
        current_value_reader=lambda unit: "2048",
        admin=True,
        confirm="EXECUTE",
        audit_path=audit_path,
        root_check=lambda: True,
        runner=runner,
        clock=lambda: 1.0,
    )

    assert result.outcome == "stale"
    assert result.action_outcome == "refusal"
    assert result.audit_outcome is None
    assert result.stderr == (
        "current memory.high value changed (1024 -> 2048); preview again with the fresh value"
    )
    assert called == []
    records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert [record["stage"] for record in records] == ["pre", "post"]
    assert records[1]["outcome"] == "refusal"
    assert records[1]["action_outcome"] == "refusal"


def test_pre_audit_gate_ordering_remains_per_verb(tmp_path: Path) -> None:
    from groop.actions.execute import execute_kill, execute_update

    kill = execute_kill(
        "docker-kill",
        "protected",
        signal="not-a-signal",
        admin=True,
        confirm="KILL",
        audit_path=tmp_path / "kill.jsonl",
        root_check=lambda: True,
        protected_check=lambda kind, target: True,
    )
    assert kill.stderr == (
        "unknown signal 'not-a-signal'; allowed signals: HUP, INT, KILL, QUIT, TERM, USR1, USR2"
    )

    update = execute_update(
        "demo.service",
        memory="512M",
        admin=True,
        confirm="UPDATE",
        audit_path=tmp_path / "update.jsonl",
        root_check=lambda: True,
        current_memory_reader=lambda target: None,
    )
    assert update.stderr == (
        "target 'demo.service' looks like a systemd unit; use 'groop action set-property' "
        "for systemd resource changes"
    )


def test_all_four_success_paths_keep_the_pre_post_audit_shape(tmp_path: Path) -> None:
    from groop.actions.execute import (
        execute_kill,
        execute_plan,
        execute_set_property,
        execute_update,
    )

    calls = (
        lambda path: execute_plan(
            "docker-start", "demo", admin=True, confirm="EXECUTE", audit_path=path,
            root_check=lambda: True, runner=_success_runner,
        ),
        lambda path: execute_set_property(
            "demo.service", property_name="memory.high", property_value="1024",
            admin=True, confirm="EXECUTE", audit_path=path, root_check=lambda: True,
            runner=_success_runner,
        ),
        lambda path: execute_kill(
            "docker-kill", "demo", signal="TERM", admin=True, confirm="KILL",
            audit_path=path, root_check=lambda: True, runner=_success_runner,
            protected_check=lambda kind, target: False,
        ),
        lambda path: execute_update(
            "demo", memory="512M", admin=True, confirm="UPDATE", audit_path=path,
            root_check=lambda: True, runner=_success_runner,
            current_memory_reader=lambda target: 0,
        ),
    )

    for index, call in enumerate(calls):
        audit_path = tmp_path / f"{index}.jsonl"
        assert call(audit_path).outcome == "success"
        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        assert [record["stage"] for record in records] == ["pre", "post"]
        assert records[1]["outcome"] == "success"
