"""Daemon deployment CLI boundaries without starting privileged services."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import topos.cli as cli


def test_daemon_preflight_renders_json_and_uses_usability_exit(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    report = SimpleNamespace(usable=False)
    monkeypatch.setattr(cli, "preflight_daemon_deployment", lambda socket, group_name: report)
    monkeypatch.setattr(cli, "preflight_report_to_jsonable", lambda value: {"usable": value.usable, "group": "topos"})

    assert cli._main_daemon(["preflight", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"group": "topos", "usable": False}


def test_daemon_preflight_reports_operational_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "preflight_daemon_deployment", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("socket policy failed")))

    assert cli._main_daemon(["preflight"]) == 2
    assert "socket policy failed" in capsys.readouterr().err


def test_daemon_install_plan_renders_text_and_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    plan = object()
    monkeypatch.setattr(cli, "build_install_plan", lambda **_kwargs: plan)
    monkeypatch.setattr(cli, "render_install_plan_text", lambda value: "install safely" if value is plan else "wrong")
    monkeypatch.setattr(cli, "install_plan_to_jsonable", lambda value: {"steps": 2} if value is plan else {})

    assert cli._main_daemon(["install-plan"]) == 0
    assert capsys.readouterr().out == "install safely\n"
    assert cli._main_daemon(["install-plan", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"steps": 2}


def test_daemon_install_plan_reports_build_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "build_install_plan", lambda **_kwargs: (_ for _ in ()).throw(ValueError("unsafe destination")))

    assert cli._main_daemon(["install-plan"]) == 2
    assert "unsafe destination" in capsys.readouterr().err
