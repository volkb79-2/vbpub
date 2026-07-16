"""P78 regression tests for the shared action execution chain."""

from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path

import pytest


def _success_runner(argv, *, timeout=30.0):
    from topos.actions.execute import ExecuteResult

    return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)


def _execute_verb(verb: str, audit_path: Path | str, **overrides):
    """Call one public executor with stable valid defaults."""
    from topos.actions.execute import (
        AuditIdentity,
        execute_kill,
        execute_plan,
        execute_set_property,
        execute_update,
    )

    common = {
        "admin": True,
        "audit_path": audit_path,
        "clock": lambda: 10.0,
        "identity": lambda: AuditIdentity(0, "tester"),
        "root_check": lambda: True,
        "runner": _success_runner,
    }
    target = overrides.pop("target", None)
    common.update(overrides)
    if verb == "plan":
        common.setdefault("confirm", "EXECUTE")
        return execute_plan("docker-start", target or "demo", **common)
    if verb == "set-property":
        common.setdefault("confirm", "EXECUTE")
        common.setdefault("property_name", "memory.high")
        common.setdefault("property_value", "1024")
        return execute_set_property(target or "demo.service", **common)
    if verb == "kill":
        kind = common.pop("kind", "docker-kill")
        common.setdefault("confirm", "KILL")
        common.setdefault("signal", "TERM")
        common.setdefault("protected_check", lambda kind, value: False)
        return execute_kill(kind, target or "demo", **common)
    if verb == "update":
        common.setdefault("confirm", "UPDATE")
        common.setdefault("memory", "512M")
        common.setdefault("current_memory_reader", lambda value: 0)
        return execute_update(target or "demo", **common)
    raise AssertionError(f"unknown test verb: {verb}")


def test_all_public_executors_use_the_one_private_chain() -> None:
    import topos.actions.execute as execute

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
    from topos.actions.execute import execute_set_property

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


@pytest.mark.parametrize("verb", ("plan", "set-property", "kill", "update"))
@pytest.mark.parametrize(
    ("failure", "outcome", "audit_outcome", "stderr"),
    (
        ("non-admin", "refusal", None, "admin mode is required"),
        ("wrong-token", "refusal", None, "<per-verb confirmation>"),
        ("non-root", "refusal", None, "root privileges are required"),
        (
            "bad-timeout",
            "refusal",
            None,
            "timeout must be finite and between 0.001 and 30.0 seconds",
        ),
        ("relative-audit", "refusal", None, "audit path must be absolute"),
        (
            "invalid-identity",
            "refusal",
            None,
            "invalid execution identity: ValueError",
        ),
        (
            "pre-audit-failure",
            "refusal",
            "pre_failure:OSError",
            "audit failed before execution",
        ),
        (
            "invalid-target",
            "refusal",
            None,
            "target must not be option-like: '-bad'",
        ),
        ("runner-oserror", "runner_failure", None, "OSError: runner unavailable"),
        ("runner-timeout", "timeout", None, ""),
        ("post-audit-failure", "audit_failure", "post_failure", ""),
    ),
)
def test_differential_common_refusal_taxonomy(
    tmp_path: Path,
    monkeypatch,
    verb: str,
    failure: str,
    outcome: str,
    audit_outcome: str | None,
    stderr: str,
) -> None:
    """Pin every common gate/result failure for every public verb."""
    import topos.actions.execute as execute

    overrides = {}
    audit_path: Path | str = tmp_path / f"{verb}-{failure}.jsonl"
    if failure == "non-admin":
        overrides["admin"] = False
    elif failure == "wrong-token":
        overrides["confirm"] = "WRONG"
    elif failure == "non-root":
        overrides["root_check"] = lambda: False
    elif failure == "bad-timeout":
        overrides["timeout"] = 0
    elif failure == "relative-audit":
        audit_path = Path("relative-audit.jsonl")
    elif failure == "invalid-identity":
        overrides["identity"] = lambda: object()
    elif failure == "pre-audit-failure":
        monkeypatch.setattr(
            execute,
            "_write_execution_audit_pre",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("audit unavailable")),
        )
    elif failure == "invalid-target":
        overrides["target"] = "-bad"
        if verb == "update":
            overrides["below_current"] = True
    elif failure == "runner-oserror":
        overrides["runner"] = lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("runner unavailable")
        )
    elif failure == "runner-timeout":
        overrides["runner"] = lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd="action", timeout=30.0)
        )
    elif failure == "post-audit-failure":
        monkeypatch.setattr(
            execute,
            "_write_execution_audit_post",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("audit unavailable")),
        )

    result = _execute_verb(verb, audit_path, **overrides)
    if failure == "wrong-token":
        token = {"plan": "EXECUTE", "set-property": "EXECUTE", "kill": "KILL", "update": "UPDATE"}[verb]
        stderr = f"exact confirmation {token} is required"
    elif failure == "invalid-target" and verb == "set-property":
        stderr = "unit must not be option-like: '-bad'"
    assert (result.outcome, result.audit_outcome, result.stderr) == (
        outcome,
        audit_outcome,
        stderr,
    )


@pytest.mark.parametrize(
    ("verb", "overrides", "outcome", "audit_outcome", "stderr"),
    (
        ("plan", {"kind": "unknown"}, "refusal", None, "unknown action kind"),
        (
            "plan",
            {"kind": "docker-kill"},
            "refusal",
            None,
            "kind 'docker-kill' is not in execution allowlist",
        ),
        (
            "set-property",
            {"property_name": "cpu.max"},
            "refusal",
            None,
            "property must be 'memory.high', got 'cpu.max'",
        ),
        (
            "set-property",
            {"property_value": "0"},
            "refusal",
            None,
            "memory.high value must be positive (got 0)",
        ),
        (
            "set-property",
            {"persistence": "sometimes"},
            "refusal",
            None,
            "persistence mode must be 'runtime' or 'persistent', got 'sometimes'",
        ),
        (
            "set-property",
            {
                "planned_current_value": "1024",
                "current_value_reader": lambda unit: "2048",
            },
            "stale",
            None,
            "current memory.high value changed (1024 -> 2048); preview again with the fresh value",
        ),
        (
            "kill",
            {"signal": "bogus"},
            "refusal",
            None,
            "unknown signal 'bogus'; allowed signals: HUP, INT, KILL, QUIT, TERM, USR1, USR2",
        ),
        (
            "kill",
            {"signal": "KILL"},
            "refusal",
            None,
            "KILL signal requires --force (data-loss prevention gate)",
        ),
        (
            "kill",
            {"protected_check": lambda kind, target: True},
            "refusal",
            None,
            "target is a protected service; kill refused",
        ),
        (
            "kill",
            {
                "protected_check": lambda kind, target: (_ for _ in ()).throw(
                    OSError("config unreadable")
                )
            },
            "refusal",
            None,
            "protected-service check failed (OSError); kill refused",
        ),
        (
            "kill",
            {"kind": "unknown"},
            "refusal",
            None,
            "invalid action kind: 'unknown'",
        ),
        (
            "update",
            {"memory": "garbage"},
            "refusal",
            None,
            "invalid memory value: 'garbage'",
        ),
        (
            "update",
            {"memory": None, "cpus": "0"},
            "refusal",
            None,
            "cpus must be positive: '0'",
        ),
        (
            "update",
            {"memory": None, "cpus": None},
            "refusal",
            None,
            "at least one of --memory or --cpus is required",
        ),
        (
            "update",
            {"target": "demo.service"},
            "refusal",
            None,
            "target 'demo.service' looks like a systemd unit; use 'topos action set-property' for systemd resource changes",
        ),
        (
            "update",
            {"memory": "100", "current_memory_reader": lambda target: 500},
            "refusal",
            None,
            "memory limit 100 bytes is below current usage 500 bytes; use --below-current to override (this may OOM the container)",
        ),
        (
            "update",
            {"memory": "100", "current_memory_reader": lambda target: None},
            "refusal",
            None,
            "current memory usage of 'demo' could not be established, so a limit of 100 bytes cannot be shown to be safe; pass --below-current to apply it anyway (this may OOM the container)",
        ),
    ),
)
def test_differential_verb_gate_taxonomy(
    tmp_path: Path,
    verb: str,
    overrides: dict[str, object],
    outcome: str,
    audit_outcome: str | None,
    stderr: str,
) -> None:
    """Pin each verb-specific gate's observable refusal."""
    overrides = dict(overrides)
    if verb == "plan":
        from topos.actions.execute import execute_plan

        kind = overrides.pop("kind")
        result = execute_plan(
            kind,
            "-bad",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "plan.jsonl",
            root_check=lambda: True,
        )
    else:
        result = _execute_verb(verb, tmp_path / f"{verb}.jsonl", **overrides)
    assert (result.outcome, result.audit_outcome, result.stderr) == (
        outcome,
        audit_outcome,
        stderr,
    )


def test_gate_ordering_proof_for_every_verb(tmp_path: Path) -> None:
    from topos.actions.execute import (
        execute_kill,
        execute_plan,
        execute_set_property,
        execute_update,
    )

    plan = execute_plan(
        "unknown",
        "-bad",
        admin=True,
        confirm="EXECUTE",
        audit_path=tmp_path / "plan.jsonl",
        root_check=lambda: True,
    )
    assert plan.stderr == "unknown action kind"

    set_property = execute_set_property(
        "-bad",
        property_name="cpu.max",
        property_value="0",
        admin=True,
        confirm="EXECUTE",
        audit_path=tmp_path / "set-property.jsonl",
        root_check=lambda: True,
    )
    assert set_property.stderr == "property must be 'memory.high', got 'cpu.max'"

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
        "target 'demo.service' looks like a systemd unit; use 'topos action set-property' "
        "for systemd resource changes"
    )


def test_all_four_success_paths_keep_the_pre_post_audit_shape(tmp_path: Path) -> None:
    from topos.actions.execute import (
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


@pytest.mark.parametrize(
    ("overrides", "expected_kind", "stderr"),
    [
        # Generic P46 gates are entered before the action kind is known, so they
        # report the property name -- as they did before the extraction.
        (
            {"admin": False},
            "memory.high",
            "admin mode is required",
        ),
        (
            {"confirm": "NOPE"},
            "memory.high",
            "exact confirmation EXECUTE is required",
        ),
        (
            {"root_check": lambda: False},
            "memory.high",
            "root privileges are required",
        ),
        (
            {"property_name": "cpu.max"},
            "cpu.max",
            "property must be 'memory.high', got 'cpu.max'",
        ),
        # ...but the verb's own unit/value/persistence refusals report the
        # ACTION kind, not the property name.  Collapsing these onto the
        # chain's initial kind is the regression this test exists to catch.
        (
            {"target": "bad unit!"},
            "systemd-set-property",
            "invalid systemd unit name: 'bad unit!'",
        ),
        (
            {"property_value": "512M"},
            "systemd-set-property",
            "memory.high value must be 'max' or a positive integer: '512M'",
        ),
        (
            {"persistence": "bogus"},
            "systemd-set-property",
            "persistence mode must be 'runtime' or 'persistent', got 'bogus'",
        ),
    ],
)
def test_set_property_refusals_report_the_same_kind_as_before_extraction(
    tmp_path: Path,
    overrides: dict[str, object],
    expected_kind: str,
    stderr: str,
) -> None:
    """``kind`` is an ExecuteResult field surfaced by ``result_to_jsonable``.

    ``execute_set_property`` enters the shared chain with the *property* name
    as its initial kind, so a gate that must report ``systemd-set-property``
    has to say so explicitly.  Asserting only (outcome, audit_outcome, stderr)
    -- as the differential taxonomy above does -- does not catch a kind that
    silently inherits the initial value.
    """
    result = _execute_verb("set-property", tmp_path / "audit.jsonl", **overrides)
    assert (result.kind, result.outcome, result.stderr) == (
        expected_kind,
        "refusal",
        stderr,
    )
