"""Behavioral tests for action execute CLI dispatch and result contracts."""

import json

import pytest

from topos.actions.execute import ExecuteResult
from topos.cli import _main_action


def _result(kind: str, target: str, outcome: str = "success") -> ExecuteResult:
    return ExecuteResult(
        kind=kind,
        target=target,
        argv=("/inert", target),
        returncode=0 if outcome == "success" else 1,
        stdout="stdout",
        stderr="stderr",
        outcome=outcome,
        duration_s=1.25,
    )


def test_execute_set_property_resolves_target_and_forwards_public_options(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    expected = _result("systemd-set-property", "resolved.service")
    monkeypatch.setattr(
        "topos.cli._resolve_mutual_exclusive_target",
        lambda target, container, command: calls.append(("resolve", target, container, command)) or "resolved.service",
    )
    monkeypatch.setattr(
        "topos.actions.execute.execute_set_property",
        lambda *args, **kwargs: calls.append(("set", args, kwargs)) or expected,
    )

    assert _main_action([
        "execute", "--kind", "systemd-set-property", "--container", "prefix",
        "--property", "memory.high", "--value", "512M", "--mode", "persistent",
        "--admin", "--confirm", "EXECUTE", "--timeout", "7",
    ]) == 0
    assert calls == [
        ("resolve", None, "prefix", "action"),
        ("set", ("resolved.service",), {
            "property_name": "memory.high", "property_value": "512M",
            "persistence": "persistent", "admin": True, "confirm": "EXECUTE",
            "timeout": 7.0,
        }),
    ]


@pytest.mark.parametrize(
    ("kind", "args", "expected_call"),
    [
        (
            "docker-kill",
            ["--signal", "KILL", "--force", "--confirm", "KILL"],
            ("kill", ("docker-kill", "resolved"), {
                "signal": "KILL", "force": True, "admin": True, "confirm": "KILL",
                "timeout": 30.0,
            }),
        ),
        (
            "docker-update",
            ["--memory", "512M", "--cpus", "1.5", "--below-current", "--confirm", "UPDATE"],
            ("update", ("resolved",), {
                "memory": "512M", "cpus": "1.5", "below_current": True,
                "admin": True, "confirm": "UPDATE", "timeout": 30.0,
            }),
        ),
        (
            "docker-restart",
            ["--confirm", "EXECUTE"],
            ("plan", ("docker-restart", "resolved"), {
                "admin": True, "confirm": "EXECUTE", "timeout": 30.0,
            }),
        ),
    ],
)
def test_execute_routes_guarded_and_generic_families_with_owner_callback(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    args: list[str],
    expected_call: tuple[object, ...],
) -> None:
    calls: list[object] = []
    expected = _result(kind, "resolved")
    owner_inspect = object()
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: "resolved")
    monkeypatch.setattr("topos.actions.owner_safety.default_owner_inspect", owner_inspect)
    monkeypatch.setattr(
        "topos.actions.execute.execute_kill",
        lambda *call_args, **kwargs: calls.append(("kill", call_args, kwargs)) or expected,
    )
    monkeypatch.setattr(
        "topos.actions.execute.execute_update",
        lambda *call_args, **kwargs: calls.append(("update", call_args, kwargs)) or expected,
    )
    monkeypatch.setattr(
        "topos.actions.execute.execute_plan",
        lambda *call_args, **kwargs: calls.append(("plan", call_args, kwargs)) or expected,
    )

    assert _main_action(["execute", "--kind", kind, "--admin", *args]) == 0
    call_name, call_args, call_kwargs = expected_call
    assert calls == [(call_name, call_args, {**call_kwargs, "owner_inspect": owner_inspect})]


@pytest.mark.parametrize(
    ("outcome", "expected_code"), [("success", 0), ("refusal", 2), ("nonzero", 1)]
)
@pytest.mark.parametrize("json_output", [True, False])
def test_execute_uses_public_formatter_and_maps_outcome(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
    expected_code: int,
    json_output: bool,
) -> None:
    result = _result("docker-stop", "target", outcome)
    calls: list[object] = []
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: "target")
    monkeypatch.setattr("topos.actions.execute.execute_plan", lambda *args, **kwargs: result)
    monkeypatch.setattr(
        "topos.actions.execute.result_to_jsonable",
        lambda value: calls.append(("json", value)) or {"sentinel": "json"},
    )
    monkeypatch.setattr(
        "topos.actions.execute.render_result_text",
        lambda value: calls.append(("text", value)) or "SENTINEL TEXT",
    )

    argv = ["execute", "--kind", "docker-stop", "--target", "target", "--admin", "--confirm", "EXECUTE"]
    if json_output:
        argv.append("--json")
    assert _main_action(argv) == expected_code
    captured = capsys.readouterr()
    if json_output:
        assert json.loads(captured.out) == {"sentinel": "json"}
        assert calls == [("json", result)]
    else:
        assert captured.out == "SENTINEL TEXT\n"
        assert calls == [("text", result)]
    assert captured.err == ""
