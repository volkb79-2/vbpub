"""Direct behavioral contracts for the DAMON CLI dispatch boundary."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from topos.cli import _main_damon
from topos.config import DamonConfig
from topos.damon.control import DamonControlError, RootRequired


def _argv(*parts: str) -> list[str]:
    return [*parts, "--damon-root", "/fixture/damon", "--state-dir", "/fixture/state"]


def test_stop_requires_all_mine_before_calling_control(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[object] = []
    monkeypatch.setattr("topos.cli.stop_owned_sessions", lambda **kwargs: calls.append(kwargs))

    assert _main_damon(["stop"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "topos damon stop requires --all-mine\n"
    assert calls == []


@pytest.mark.parametrize(
    ("error", "status"), [(RootRequired("root required"), 2), (DamonControlError("control failed"), 1)]
)
def test_stop_maps_control_errors_to_distinct_statuses(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    status: int,
) -> None:
    def fail(**_kwargs: object) -> int:
        raise error

    monkeypatch.setattr("topos.cli.stop_owned_sessions", fail)

    assert _main_damon(_argv("stop", "--all-mine", "--allow-non-root-fixture")) == status

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{error}\n"


def test_stop_forwards_all_settings_and_reports_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, object]] = []

    def stop(**kwargs: object) -> int:
        calls.append(kwargs)
        return 3

    monkeypatch.setattr("topos.cli.stop_owned_sessions", stop)

    assert _main_damon(_argv("stop", "--all-mine", "--allow-non-root-fixture")) == 0

    assert calls == [
        {
            "damon_root": Path("/fixture/damon"),
            "state_dir": Path("/fixture/state"),
            "all_mine": True,
            "require_root": False,
        }
    ]
    assert capsys.readouterr().out == "stopped 3 topos-owned DAMON session(s)\n"


def test_paddr_start_plans_and_prints_confirmation_without_starting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = SimpleNamespace(damon=DamonConfig())
    plan = object()
    loads: list[Path | None] = []
    plans: list[dict[str, object]] = []
    starts: list[object] = []

    monkeypatch.setattr("topos.cli.load", lambda path: loads.append(path) or config)
    monkeypatch.setattr(
        "topos.cli.plan_start_paddr_session",
        lambda **kwargs: plans.append(kwargs) or plan,
    )
    monkeypatch.setattr("topos.cli.paddr_confirmation_text", lambda _value: "PLAN\n")
    monkeypatch.setattr("topos.cli.start_planned_paddr_session", starts.append)

    assert _main_damon(_argv("paddr", "start", "--config", "/fixture/config")) == 2

    assert loads == [Path("/fixture/config")]
    assert plans == [
        {
            "damon_root": Path("/fixture/damon"),
            "state_dir": Path("/fixture/state"),
            "config": config.damon,
            "require_root": True,
        }
    ]
    assert starts == []
    captured = capsys.readouterr()
    assert captured.out == "PLAN\n\n"
    assert captured.err == ""


def test_paddr_start_confirms_exact_plan_and_reports_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = SimpleNamespace(damon=DamonConfig())
    plan = object()
    session = SimpleNamespace(kdamond_idx=4)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("topos.cli.load", lambda _path: config)
    monkeypatch.setattr("topos.cli.plan_start_paddr_session", lambda **_kwargs: plan)
    monkeypatch.setattr(
        "topos.cli.start_planned_paddr_session",
        lambda value, **kwargs: calls.append({"plan": value, **kwargs}) or session,
    )

    assert _main_damon(
        _argv("paddr", "start", "--confirm", "START", "--allow-non-root-fixture")
    ) == 0

    assert calls == [{"plan": plan, "confirmed_text": "START", "require_root": False}]
    assert capsys.readouterr().out == "started topos-owned paddr DAMON session on kdamond 4\n"


@pytest.mark.parametrize(
    ("function", "error", "status"),
    [
        ("plan_start_paddr_session", RootRequired("root required"), 2),
        ("start_planned_paddr_session", DamonControlError("control failed"), 1),
    ],
)
def test_paddr_start_maps_errors_without_starting_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    function: str,
    error: Exception,
    status: int,
) -> None:
    plan = object()
    starts: list[object] = []
    monkeypatch.setattr("topos.cli.load", lambda _path: SimpleNamespace(damon=DamonConfig()))
    if function == "plan_start_paddr_session":
        monkeypatch.setattr("topos.cli.plan_start_paddr_session", lambda **_kwargs: (_ for _ in ()).throw(error))
        monkeypatch.setattr("topos.cli.start_planned_paddr_session", lambda *a, **kw: starts.append("UNEXPECTED"))
    else:
        monkeypatch.setattr("topos.cli.plan_start_paddr_session", lambda **_kwargs: plan)
        def fail_start(*_args: object, **_kwargs: object) -> object:
            starts.append(1)
            raise error

        monkeypatch.setattr("topos.cli.start_planned_paddr_session", fail_start)

    assert _main_damon(_argv("paddr", "start", "--confirm", "START")) == status

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{error}\n"
    assert starts == ([1] if function == "start_planned_paddr_session" else [])
