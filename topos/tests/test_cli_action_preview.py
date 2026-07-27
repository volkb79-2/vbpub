"""Direct behavioral tests for action preview CLI dispatch."""

import json
from pathlib import Path

import pytest

from topos.cli import _main_action
from topos.actions.governance import SetPropertyPlan
from topos.actions.kill_ops import KillPlan
from topos.actions.preview import ActionPlan, DisabledPlan
from topos.actions.catalog import ActionKind
from topos.actions.update_ops import UpdatePlan


class _AuditSpy:
    records: list[dict[str, object]] = []

    def __init__(self, path: Path) -> None:
        assert path == Path("/inert/audit.jsonl")

    def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


def test_preview_forwards_resolved_target_and_every_safety_option(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []
    plan = ActionPlan(
        kind=ActionKind.DOCKER_UPDATE,
        target="resolved-target",
        argv=("inert",),
        description="inert plan",
    )
    monkeypatch.setattr(
        "topos.cli._resolve_mutual_exclusive_target",
        lambda target, container, command: calls.append(("resolve", target, container, command)) or "resolved-target",
    )
    monkeypatch.setattr(
        "topos.actions.preview.build_admin_preview",
        lambda *args, **kwargs: calls.append(("preview", *args, kwargs)) or plan,
    )
    monkeypatch.setattr("topos.actions.audit.AuditLog", _AuditSpy)

    assert _main_action([
        "preview", "--kind", "docker-update", "--container", "prefix", "--admin",
        "--property", "memory.high", "--value", "123", "--mode", "runtime",
        "--signal", "TERM", "--force", "--memory", "512M", "--cpus", "1.5",
        "--below-current", "--audit-log", "/inert/audit.jsonl",
    ]) == 0
    assert calls == [
        ("resolve", None, "prefix", "action"),
        ("preview", "docker-update", "resolved-target", {
            "admin": True, "property_name": "memory.high", "property_value": "123",
            "persistence": "runtime", "signal": "TERM", "force": True,
            "memory": "512M", "cpus": "1.5", "below_current": True,
        }),
    ]


@pytest.mark.parametrize("failure, needle", [
    (ValueError("bad value"), "bad value"),
    (DisabledPlan(ActionKind.DOCKER_RESTART, "resolved-target"), "not enabled"),
    (object(), "unexpected action preview result"),
])
def test_preview_failures_short_circuit_audit_and_rendering(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: object,
    needle: str,
) -> None:
    audited: list[object] = []
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: "resolved-target")
    if isinstance(failure, ValueError):
        monkeypatch.setattr("topos.actions.preview.build_admin_preview", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure))
    else:
        monkeypatch.setattr("topos.actions.preview.build_admin_preview", lambda *_args, **_kwargs: failure)
    monkeypatch.setattr("topos.actions.audit.AuditLog", lambda *_: pytest.fail("audit reached"))
    monkeypatch.setattr("topos.actions.governance.render_set_property_preview", lambda *_: audited.append("render") or pytest.fail("renderer reached"))

    assert _main_action([
        "preview", "--kind", "docker-restart", "--target", "target", "--admin",
        "--audit-log", "/inert/audit.jsonl",
    ]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert needle in captured.err
    assert audited == []


def test_resolution_failure_never_reaches_preview_audit_or_render(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: (_ for _ in ()).throw(ValueError("target required")))
    monkeypatch.setattr("topos.actions.preview.build_admin_preview", lambda *_args, **_kwargs: pytest.fail("preview reached"))
    monkeypatch.setattr("topos.actions.audit.AuditLog", lambda *_: pytest.fail("audit reached"))
    assert _main_action(["preview", "--kind", "docker-restart", "--target", "target", "--audit-log", "/inert/audit.jsonl"]) == 2
    assert capsys.readouterr().err == "target required\n"


@pytest.mark.parametrize("plan, text, jsonable, audit", [
    (SetPropertyPlan("systemd-set-property", "unit.service", "memory.high", "123", ("set",), "max", "persistent"), "SET TEXT", {"type": "set"}, {"kind": "systemd-set-property", "target": "unit.service", "argv": ("set",), "admin": True}),
    (KillPlan("docker-kill", "container", "TERM", ("kill",), False), "KILL TEXT", {"type": "kill"}, {"kind": "docker-kill", "target": "container", "argv": ("kill",), "admin": True}),
    (UpdatePlan("docker-update", "container", 123, 1.5, ("update",), 100, False), "UPDATE TEXT", {"type": "update"}, {"kind": "docker-update", "target": "container", "argv": ("update",), "admin": True}),
    (ActionPlan(ActionKind.DOCKER_RESTART, "container", ("restart",), "generic"), "GENERIC TEXT", {"argv": ["restart"], "description": "generic", "kind": "docker-restart", "mode": "preview", "target": "container"}, {"kind": "docker-restart", "target": "container", "argv": ("restart",), "admin": True}),
])
def test_each_plan_uses_its_renderer_json_contract_and_configured_audit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], plan: object,
    text: str, jsonable: dict[str, str], audit: dict[str, object],
) -> None:
    _AuditSpy.records = []
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: "container")
    monkeypatch.setattr("topos.actions.preview.build_admin_preview", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr("topos.actions.audit.AuditLog", _AuditSpy)
    renderer = {
        SetPropertyPlan: "topos.actions.governance.render_set_property_preview",
        KillPlan: "topos.actions.kill_ops.render_kill_preview",
        UpdatePlan: "topos.actions.update_ops.render_update_preview",
    }.get(type(plan))
    if renderer:
        monkeypatch.setattr(renderer, lambda _plan: text)

    kind = plan.kind.value if isinstance(plan.kind, ActionKind) else plan.kind
    assert _main_action(["preview", "--kind", kind, "--target", "target", "--admin", "--audit-log", "/inert/audit.jsonl"]) == 0
    output = capsys.readouterr().out
    if renderer:
        assert output == text + "\n"
    else:
        assert output == "Action: docker-restart\nTarget: container\nCommand argv: ['restart']\nDescription: generic\nMode: preview only; no command was executed\n"
    assert _AuditSpy.records == [audit]

    converters = {
        SetPropertyPlan: "topos.actions.governance.set_property_plan_to_jsonable",
        KillPlan: "topos.actions.kill_ops.kill_plan_to_jsonable",
        UpdatePlan: "topos.actions.update_ops.update_plan_to_jsonable",
    }
    if type(plan) in converters:
        monkeypatch.setattr(converters[type(plan)], lambda _plan: jsonable)
    _AuditSpy.records = []
    assert _main_action(["preview", "--kind", kind, "--target", "target", "--admin", "--json"]) == 0
    json_output = json.loads(capsys.readouterr().out)
    if type(plan) in converters:
        assert json_output == jsonable
    else:
        assert json_output == jsonable
