"""Behaviour tests for the reviewed install-host catalog cases.

Each reviewed case in ``cli-review-install-host.toml`` names an invocation and
the outcome that was judged acceptable.  The tests here replay that exact
invocation through the real ``main()`` (fake Netcup client, HOME and cwd under
``tmp_path``) and compare what really happens with what the catalog claims:
exit status, output, the parsed option values, and the one invariant every
case shares -- the install POST is sent only by a live, accepted run.

The library controls (``--dry-run``, ``--yes``, ``--debug-raw``) each have one
parametrised test across routes that also proves the contrast (the run
without the control behaves differently); every other option shares one
replay test.  Each parameter carries its own ``cli_case`` marker, and the
catalog's ``test_ids`` name the parameter node ids.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from case_harness import run_install_host

NETCUP_DIR = Path(__file__).resolve().parent.parent
CLI = "install-host"
ROWS = {
    row["id"]: row
    for row in tomllib.loads(
        (NETCUP_DIR / "cli-review-install-host.toml").read_text(encoding="utf-8")
    ).get("cases", [])
}
CHOICE_HASH = {"remove": "66f3d68b76", "retain": "689680efe3"}


def _route(route: str) -> str:
    return f"case:route:entrypoint:{CLI}/{route}"


def _option(route: str, flag: str) -> str:
    return f"option:route:entrypoint:{CLI}/{route}/{flag}"


def spelling_id(route: str, flag: str, spelling: str | None = None) -> str:
    return f"{_route(route)}/option-spelling/{_option(route, flag)}/{spelling or flag}"


def choice_id(route: str, flag: str, choice: str) -> str:
    return f"{_route(route)}/option-choice/{_option(route, flag)}/{CHOICE_HASH[choice]}"


def member_id(route: str, flag: str) -> str:
    return f"{_route(route)}/exclusive-member/task-monitor-mode/{_option(route, flag)}"


def conflict_id(route: str) -> str:
    return (
        f"{_route(route)}/exclusive-conflict/task-monitor-mode/"
        f"{_option(route, '--monitor')}/{_option(route, '--no-monitor')}"
    )


def minimum_id(route: str) -> str:
    return f"{_route(route)}/minimum"


def _case(case_id, node, scenario, args=None, follower=None):
    return pytest.param(
        case_id,
        scenario,
        args or {},
        follower or {},
        marks=pytest.mark.cli_case(case_id),
        id=node,
    )


def _wizard_cases(route: str):
    """Cases shared by the wizard and its configure alias."""
    s = "gather"
    options = [
        ("--attach-custom-script", None, s, {"attach_custom_script": True}),
        ("--no-attach-custom-script", None, s, {"attach_custom_script": False}),
        ("--completion-marker", None, s, {"completion_marker": "/var/tmp/netcup-install-done"}),
        ("--completion-wait-seconds", None, s, {"completion_wait_seconds": 45.0}),
        ("--config", None, s, {"config_path": "reviewed-target.jsonc"}),
        ("--config", "--payload", s, {"config_path": "reviewed-target.jsonc"}),
        ("--custom-script-file", None, s, {"custom_script_file": "custom-script.sh"}),
        ("--local-controller-key", None, s, {"local_controller_key": "retain"}),
        ("--monitor", None, s, {"monitor": True, "no_monitor": False}),
        ("--no-monitor", None, s, {"no_monitor": True, "monitor": False}),
        ("--poll-interval", None, s, {"poll_interval": 2.5}),
        ("--server-id", None, "gather-id", {"server_id": 42}),
        ("--ssh-host", None, s, {"ssh_host": "192.0.2.10"}),
        ("--ssh-identity-file", None, s, {"ssh_identity_file": "controller-key"}),
        ("--ssh-key-id", None, s, {"ssh_key_ids": [7]}),
        ("--ssh-user", None, s, {"ssh_user": "ops"}),
    ]
    params = [
        _case(
            spelling_id(route, flag, alias),
            f"{route}-{(alias or flag).lstrip('-')}",
            scenario,
            args,
        )
        for flag, alias, scenario, args in options
    ]
    for choice in ("remove", "retain"):
        params.append(
            _case(
                choice_id(route, "--local-controller-key", choice),
                f"{route}-local-controller-key-{choice}",
                s,
                {"local_controller_key": choice},
            )
        )
    params.append(
        _case(
            member_id(route, "--monitor"),
            f"{route}-exclusive-monitor",
            s,
            {"monitor": True, "no_monitor": False},
        )
    )
    params.append(
        _case(
            member_id(route, "--no-monitor"),
            f"{route}-exclusive-no-monitor",
            s,
            {"no_monitor": True, "monitor": False},
        )
    )
    params.append(_case(conflict_id(route), f"{route}-conflict-monitor", "none"))
    params.append(_case(minimum_id(route), f"{route}-minimum", "none"))
    return params


def _install_cases():
    route, s = "install", "file"
    options = [
        ("--attach-custom-script", None, {"attach_custom_script": True}),
        ("--no-attach-custom-script", None, {"attach_custom_script": False}),
        ("--completion-wait-seconds", None, {"completion_wait_seconds": 45.0}),
        ("--config", None, {"config_path": "target-host.jsonc"}),
        ("--config", "--payload", {"config_path": "target-host.jsonc"}),
        ("--local-controller-key", None, {"local_controller_key": "retain"}),
        ("--no-monitor", None, {"no_monitor": True, "monitor": False}),
        ("--poll-interval", None, {"poll_interval": 2.5}),
        ("--ssh-host", None, {"ssh_host": "192.0.2.10"}),
        ("--ssh-identity-file", None, {"ssh_identity_file": "controller-key"}),
        ("--ssh-key-id", None, {"ssh_key_ids": [7]}),
        ("--ssh-user", None, {"ssh_user": "ops"}),
    ]
    params = [
        _case(
            spelling_id(route, flag, alias),
            f"{route}-{(alias or flag).lstrip('-')}",
            s,
            args,
        )
        for flag, alias, args in options
    ]
    for choice in ("remove", "retain"):
        params.append(
            _case(
                choice_id(route, "--local-controller-key", choice),
                f"{route}-local-controller-key-{choice}",
                s,
                {"local_controller_key": choice},
            )
        )
    params.append(_case(minimum_id(route), f"{route}-minimum", s))
    return params


def _attach_cases():
    route, s = "attach", "none"
    options = [
        ("--attach-initial-delay", {"attach_initial_delay": 1.5}, {"initial_delay": 1.5}),
        ("--attach-max-wait-seconds", {"attach_max_wait_seconds": 90.0}, {"max_wait_seconds": 90.0}),
        ("--attach-task-uuid", {"attach_task_uuid": "task-7"}, {"task_uuid": "task-7"}),
        ("--poll-interval", {"poll_interval": 2.5}, {"poll_interval": 2.5}),
        ("--simulate-disconnect-seconds", {"simulate_disconnect_seconds": 5.0}, {"simulate_disconnect_seconds": 5.0}),
        ("--ssh-host", {"ssh_host": "192.0.2.10"}, {"host": "192.0.2.10"}),
        ("--ssh-identity-file", {"ssh_identity_file": "controller-key"}, {"identity_file": "controller-key"}),
        ("--ssh-user", {"ssh_user": "ops"}, {"user": "ops"}),
    ]
    params = [
        _case(spelling_id(route, flag), f"{route}-{flag.lstrip('-')}", s, args, follower)
        for flag, args, follower in options
    ]
    params.append(_case(minimum_id(route), f"{route}-minimum", s))
    return params


REPLAYED = _wizard_cases("wizard") + _wizard_cases("configure") + _install_cases() + _attach_cases()


def _replay(case_id, scenario, tmp_path, monkeypatch, capsys, fake_client, mod, argv=None):
    row = ROWS[case_id]
    run = run_install_host(
        mod,
        argv if argv is not None else row["invocation"],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
        fake_client=fake_client,
        scenario=scenario,
    )
    return row, run


def _assert_catalog_outcome(row, run):
    assert run.status == row["expected_exit_status"]
    assert row["expected_stdout_contains"] in run.out
    assert row["expected_stderr_contains"] in run.err
    # The install POST is sent only by a live run that was accepted.
    live_accepted = run.status == 0 and "--dry-run" not in row["invocation"]
    assert ("post" in run.methods) is live_accepted


@pytest.mark.parametrize(("case_id", "scenario", "args", "follower"), REPLAYED)
def test_replayed_case_matches_catalog(
    case_id, scenario, args, follower, install_host_mod, tmp_path, monkeypatch, capsys, fake_client
):
    row, run = _replay(
        case_id, scenario, tmp_path, monkeypatch, capsys, fake_client, install_host_mod
    )
    _assert_catalog_outcome(row, run)
    if row["expected_exit_status"] == 2:
        # Refusals happen before any file is written for the operator.
        assert run.followers == []
    for dest, expected in args.items():
        assert getattr(run.args, dest) == expected
    if follower:
        assert len(run.followers) == 1
        for key, expected in follower.items():
            assert run.followers[0].kwargs[key] == expected
    if "--server-id" in row["invocation"]:
        assert run.calls[0][1] == "/api/v1/servers/42"
    if "--custom-script-file" in row["invocation"]:
        assert "REDACTED customScript (len=28)" in run.out
    if "--ssh-key-id" in row["invocation"]:
        assert '"sshKeyIds": [\n    7\n  ]' in run.out
    if "--config" in row["invocation"] and row["invocation"][0] in ("wizard", "configure"):
        assert "[dry-run] NOT saving to reviewed-target.jsonc" in run.out
    if row["invocation"][0] == "configure" and row["expected_exit_status"] != 2:
        assert "[compat] configure is the interactive installer wizard" in run.out


# --- library controls: one parametrised test each, across routes ------------

DRY_RUN = [
    pytest.param(
        route,
        scenario,
        marks=pytest.mark.cli_case(spelling_id(route, "--dry-run")),
        id=route,
    )
    for route, scenario in (("wizard", "gather"), ("configure", "gather"), ("install", "file"))
]


@pytest.mark.parametrize(("route", "scenario"), DRY_RUN)
def test_dry_run_plans_without_mutating(
    route, scenario, install_host_mod, tmp_path, monkeypatch, capsys, fake_client
):
    case_id = spelling_id(route, "--dry-run")
    live_dir = tmp_path / "live"
    dry_dir = tmp_path / "dry"
    live_dir.mkdir()
    dry_dir.mkdir()
    row, dry = _replay(case_id, scenario, dry_dir, monkeypatch, capsys, fake_client, install_host_mod)
    _assert_catalog_outcome(row, dry)
    assert "[dry-run] Preflight OK. NOT calling POST /api/v1/servers/42/image." in dry.out
    assert "post" not in dry.methods
    assert list(dry_dir.glob("*.jsonc")) == ([dry_dir / "target-host.jsonc"] if scenario == "file" else [])
    # Contrast: the same invocation without --dry-run is the one that POSTs.
    live_argv = [token for token in row["invocation"] if token != "--dry-run"]
    _, live = _replay(
        case_id, scenario, live_dir, monkeypatch, capsys, fake_client, install_host_mod, argv=live_argv
    )
    assert live.status == 0
    assert live.methods.count("post") == 1
    assert "[dry-run]" not in live.out


YES = [
    pytest.param(
        route,
        scenario,
        marks=pytest.mark.cli_case(spelling_id(route, "--yes")),
        id=route,
    )
    for route, scenario in (("wizard", "gather"), ("configure", "gather"), ("install", "file"))
]


@pytest.mark.parametrize(("route", "scenario"), YES)
def test_yes_is_the_only_consent_in_a_non_interactive_run(
    route, scenario, install_host_mod, tmp_path, monkeypatch, capsys, fake_client
):
    case_id = spelling_id(route, "--yes")
    accepted_dir = tmp_path / "accepted"
    refused_dir = tmp_path / "refused"
    accepted_dir.mkdir()
    refused_dir.mkdir()
    row, accepted = _replay(
        case_id, scenario, accepted_dir, monkeypatch, capsys, fake_client, install_host_mod
    )
    _assert_catalog_outcome(row, accepted)
    assert accepted.methods.count("post") == 1
    # Contrast: without --yes and without a terminal the run refuses and never POSTs.
    bare = [token for token in row["invocation"] if token != "--yes"]
    _, refused = _replay(
        case_id, scenario, refused_dir, monkeypatch, capsys, fake_client, install_host_mod, argv=bare
    )
    assert refused.status == 2
    assert "confirmation is required, but stdin is not interactive" in refused.err
    assert "post" not in refused.methods


DEBUG_RAW = [
    pytest.param(
        route,
        scenario,
        marks=pytest.mark.cli_case(spelling_id(route, "--debug-raw")),
        id=route,
    )
    for route, scenario in (
        ("wizard", "gather"),
        ("configure", "gather"),
        ("install", "file"),
        ("attach", "none"),
    )
]


@pytest.mark.parametrize(("route", "scenario"), DEBUG_RAW)
def test_debug_raw_warns_and_is_off_by_default(
    route, scenario, install_host_mod, tmp_path, monkeypatch, capsys, fake_client
):
    case_id = spelling_id(route, "--debug-raw")
    raw_dir = tmp_path / "raw"
    plain_dir = tmp_path / "plain"
    raw_dir.mkdir()
    plain_dir.mkdir()
    row, raw = _replay(case_id, scenario, raw_dir, monkeypatch, capsys, fake_client, install_host_mod)
    _assert_catalog_outcome(row, raw)
    assert "--debug-raw is active: credentials, tokens, passwords" in raw.err
    plain_argv = [token for token in row["invocation"] if token != "--debug-raw"]
    _, plain = _replay(
        case_id, scenario, plain_dir, monkeypatch, capsys, fake_client, install_host_mod, argv=plain_argv
    )
    assert plain.status == raw.status
    assert "--debug-raw is active" not in plain.err
    if route == "install":
        # Secret-bearing customScript text is shown raw only with the opt-out.
        assert '"customScript": "echo hi"' in raw.out
        assert "REDACTED customScript (len=7)" in plain.out
