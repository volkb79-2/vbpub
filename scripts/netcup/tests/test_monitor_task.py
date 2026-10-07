"""Behavioral and CLI-contract tests for the Netcup task monitor."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cli_extended import CliIdentity, assert_cli_contract, make_invoker
from conftest import FakeHTTPResponse

TASK_UUID = "3a27fe8e-e747-4f3b-80b0-f930c0d0db3f"
SCRIPT = Path(__file__).resolve().parents[1] / "monitor-task.py"


def _invoke_app(app, argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = app.run(argv=argv, stdout=stdout, stderr=stderr)
    return SimpleNamespace(
        returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue()
    )


def _stub_api(monkeypatch, mod, responses):
    monkeypatch.setenv("NETCUP_SCP_API_REFRESH_TOKEN", "refresh-secret")
    monkeypatch.setattr(mod.netcup_scp_client, "load_env_file", lambda: None)
    monkeypatch.setattr(
        mod.netcup_scp_client,
        "configure_api",
        lambda load_environment=False: {"api.base_url": "https://invalid.test"},
    )
    monkeypatch.setattr(
        mod.netcup_scp_client, "get_access_token", lambda refresh: "access-token"
    )

    class FakeClient:
        def __init__(self, access_token, refresh_token=None):
            self.responses = list(responses)
            self.calls = []

        def get(self, endpoint):
            self.calls.append(endpoint)
            return self.responses.pop(0)

    monkeypatch.setattr(mod, "NetcupSCPClient", FakeClient)


@pytest.mark.cli_case(
    "case:route:entrypoint:monitor-task/show/argument-shape/argument:route:entrypoint:monitor-task/show/task_uuid"
)
@pytest.mark.cli_case(
    "case:route:entrypoint:monitor-task/show/interaction:show-poll-refusal/foreign-watch-poll"
)
@pytest.mark.cli_case(
    "case:route:entrypoint:monitor-task/watch/interaction:watch-json-refusal/show-json"
)
@pytest.mark.cli_case(
    "case:route:entrypoint:monitor-task/watch/argument-shape/argument:route:entrypoint:monitor-task/watch/task_uuid"
)
def test_real_executable_obeys_help_version_and_parse_contract(tmp_path):
    identity = CliIdentity.resolve(
        name="NETCUP SCP",
        command="monitor-task",
        long_name="Netcup Server Control Panel task monitor",
        version_file=SCRIPT.with_name("VERSION"),
    )

    assert_cli_contract(
        make_invoker(
            SCRIPT,
            home=tmp_path / "home",
            cwd=tmp_path,
            scrub_prefixes=("NETCUP_SCP_API_",),
        ),
        identity,
        ("show", "watch"),
        invalid_invocations={
            "missing show task UUID": ("show",),
            "malformed show task UUID": ("show", "not-a-uuid"),
            "malformed task UUID": ("watch", "not-a-uuid"),
            "poll interval is watch-only": ("show", TASK_UUID, "--poll", "1"),
            "JSON output is show-only": ("watch", TASK_UUID, "--json"),
        },
        known_verb_errors={
            "missing show task UUID": "show",
            "malformed show task UUID": "show",
            "malformed task UUID": "watch",
            "poll interval is watch-only": "show",
            "JSON output is show-only": "watch",
        },
    )


def test_help_and_version_do_not_load_env_or_api_config(monitor_task_mod, monkeypatch):
    mod = monitor_task_mod

    def forbidden(*args, **kwargs):
        pytest.fail("help/version path loaded credentials or API configuration")

    monkeypatch.setattr(mod.netcup_scp_client, "load_env_file", forbidden)
    monkeypatch.setattr(mod.netcup_scp_client, "configure_api", forbidden)
    monkeypatch.setattr(mod.netcup_scp_client, "_load_settings", forbidden)
    app = mod.build_cli()
    assert _invoke_app(app, []).returncode == 0
    assert _invoke_app(app, ["show", "--help"]).returncode == 0
    assert _invoke_app(app, ["version"]).stdout == mod.IDENTITY.version_line + "\n"


def test_generated_help_groups_actions_and_watch_options(monitor_task_mod):
    result = _invoke_app(monitor_task_mod.build_cli(), ["watch", "--help"])
    assert result.returncode == 0
    assert "ACTIONS" not in result.stdout
    assert "STOP CONDITIONS" in result.stdout
    assert "--poll SECONDS" in result.stdout
    assert "--json" not in result.stdout
    assert "--debug-raw" in result.stdout


@pytest.mark.cli_case(
    "case:route:entrypoint:monitor-task/show/option-spelling/option:route:entrypoint:monitor-task/show/--json/--json"
)
@pytest.mark.cli_case(
    "case:route:entrypoint:monitor-task/show/option-spelling/option:route:entrypoint:monitor-task/show/--debug-raw/--debug-raw"
)
@pytest.mark.cli_case(
    "case:route:entrypoint:monitor-task/show/interaction:raw-json-opt-out/json-and-debug-raw"
)
def test_show_json_redacts_sensitive_fields_and_debug_raw_disables_redaction(
    monitor_task_mod, monkeypatch
):
    mod = monitor_task_mod
    response = {
        "state": "FINISHED",
        "rootPassword": "root-secret",
        "nested": {"token": "api-secret"},
    }
    _stub_api(monkeypatch, mod, [response, response])

    safe = _invoke_app(mod.build_cli(), ["show", TASK_UUID, "--json"])
    assert safe.returncode == 0
    safe_json = json.loads(safe.stdout)
    assert safe_json["rootPassword"] == "***REDACTED***"
    assert safe_json["nested"]["token"] == "***REDACTED***"

    raw = _invoke_app(mod.build_cli(), ["show", TASK_UUID, "--json", "--debug-raw"])
    assert raw.returncode == 0
    assert json.loads(raw.stdout)["rootPassword"] == "root-secret"
    assert "--debug-raw is active" in raw.stderr


@pytest.mark.cli_case("case:route:entrypoint:monitor-task/show/minimum")
def test_show_fetches_one_snapshot_without_polling(monitor_task_mod, monkeypatch):
    mod = monitor_task_mod
    _stub_api(monkeypatch, mod, [{"state": "FINISHED", "name": "build task"}])
    created = []
    create_client = mod._create_client

    def recording_client(runtime):
        client = create_client(runtime)
        created.append(client)
        return client

    monkeypatch.setattr(mod, "_create_client", recording_client)
    result = _invoke_app(mod.build_cli(), ["show", TASK_UUID])

    assert result.returncode == 0
    assert "Task state: FINISHED — build task" in result.stderr
    assert len(created) == 1
    assert created[0].calls == [f"/api/v1/tasks/{TASK_UUID}"]


def test_show_rejects_non_object_api_response_without_traceback(
    monitor_task_mod, monkeypatch
):
    _stub_api(monkeypatch, monitor_task_mod, [[{"state": "FINISHED"}]])
    result = _invoke_app(monitor_task_mod.build_cli(), ["show", TASK_UUID, "--json"])
    assert result.returncode == 1
    assert "malformed task response" in result.stderr
    assert "Traceback" not in result.stderr


def test_watch_stops_on_missing_task_state(monitor_task_mod, monkeypatch):
    _stub_api(monkeypatch, monitor_task_mod, [{"name": "incomplete response"}])
    result = _invoke_app(monitor_task_mod.build_cli(), ["watch", TASK_UUID])
    assert result.returncode == 1
    assert "stopping instead of polling indefinitely" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.cli_case("case:route:entrypoint:monitor-task/watch/minimum")
def test_watch_polls_changes_until_terminal_and_reads_default_interval(
    monitor_task_mod, monkeypatch, tmp_path
):
    mod = monitor_task_mod
    config = tmp_path / "monitor-task.toml"
    config.write_text("[monitor]\npoll_interval = 2.5\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_SETTINGS_PATH", config)
    _stub_api(
        monkeypatch,
        mod,
        [
            {"state": "RUNNING", "taskProgress": {"progressInPercent": 25}},
            {"state": "FINISHED", "taskProgress": {"progressInPercent": True}},
        ],
    )
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = _invoke_app(mod.build_cli(), ["watch", TASK_UUID])
    assert result.returncode == 0
    assert sleeps == [2.5]
    assert "Task state: RUNNING (25%)" in result.stderr
    assert "Task finished: FINISHED" in result.stderr


@pytest.mark.cli_case(
    "case:route:entrypoint:monitor-task/watch/option-spelling/option:route:entrypoint:monitor-task/watch/--poll/--poll"
)
@pytest.mark.cli_case(
    "case:route:entrypoint:monitor-task/watch/option-spelling/option:route:entrypoint:monitor-task/watch/--debug-raw/--debug-raw"
)
def test_watch_accepts_explicit_poll_and_debug_raw(monitor_task_mod, monkeypatch):
    mod = monitor_task_mod
    _stub_api(
        monkeypatch,
        mod,
        [
            {"state": "RUNNING"},
            {"state": "ERROR", "responseError": {"rootPassword": "root-secret"}},
        ],
    )
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = _invoke_app(
        mod.build_cli(),
        ["watch", TASK_UUID, "--poll", "0.25", "--debug-raw"],
    )

    # The task ended in ERROR, so watch exits 1 after printing the final state.
    assert result.returncode == 1
    assert sleeps == [0.25]
    assert "root-secret" in result.stderr
    assert "Task finished: ERROR" in result.stderr


def test_show_text_output_redacts_response_error_unless_debug_raw(monitor_task_mod, monkeypatch):
    mod = monitor_task_mod
    task = {"state": "ERROR", "responseError": {"rootPassword": "root-secret"}}
    _stub_api(monkeypatch, mod, [dict(task), dict(task)])

    redacted = _invoke_app(mod.build_cli(), ["show", TASK_UUID])
    raw = _invoke_app(mod.build_cli(), ["show", TASK_UUID, "--debug-raw"])

    assert "Task response error:" in redacted.stderr
    assert "root-secret" not in redacted.stderr
    assert "root-secret" in raw.stderr


def test_watch_redacts_response_error_unless_debug_raw(monitor_task_mod, monkeypatch):
    mod = monitor_task_mod
    _stub_api(
        monkeypatch,
        mod,
        [{"state": "ERROR", "responseError": {"rootPassword": "root-secret"}}],
    )
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)

    result = _invoke_app(mod.build_cli(), ["watch", TASK_UUID])

    assert result.returncode == 1
    assert "Task response error:" in result.stderr
    assert "root-secret" not in result.stderr
    assert "Task finished: ERROR" in result.stderr


@pytest.mark.parametrize("state", ["ERROR", "CANCELED", "ROLLBACK", "error"])
def test_watch_exits_1_for_every_unsuccessful_terminal_state(
    state, monitor_task_mod, monkeypatch
):
    _stub_api(monkeypatch, monitor_task_mod, [{"state": state}])
    monkeypatch.setattr(monitor_task_mod.time, "sleep", lambda seconds: None)

    result = _invoke_app(monitor_task_mod.build_cli(), ["watch", TASK_UUID])

    assert result.returncode == 1
    assert f"Task finished: {state}" in result.stderr


def test_watch_exits_0_only_for_finished_even_in_lower_case(monitor_task_mod, monkeypatch):
    _stub_api(monkeypatch, monitor_task_mod, [{"state": "finished"}])

    result = _invoke_app(monitor_task_mod.build_cli(), ["watch", TASK_UUID])

    assert result.returncode == 0
    assert "Task finished: finished" in result.stderr


@pytest.mark.cli_case(
    "case:route:entrypoint:monitor-task/watch/interaction:poll-validation/refuse-invalid"
)
def test_watch_rejects_nonpositive_or_nonfinite_poll_before_authentication(
    monitor_task_mod, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid poll interval must be refused before API setup")

    monkeypatch.setattr(monitor_task_mod, "_create_client", forbidden)
    for raw in ("nan", "0", "-1"):
        result = _invoke_app(
            monitor_task_mod.build_cli(),
            ["watch", TASK_UUID, "--poll", raw],
        )
        assert result.returncode == 2
        assert "finite number greater than zero" in result.stderr
        # A verb-level usage error is the message plus a one-line hint, not the full help.
        assert "Hint: run ./monitor-task.py help watch" in result.stderr
        assert "usage:" not in result.stderr
        assert "Traceback" not in result.stderr


@pytest.mark.cli_case(
    "case:route:entrypoint:monitor-task/watch/interaction:poll-repeat/repeated-poll-last-wins"
)
def test_watch_repeated_poll_uses_last_interval(monitor_task_mod, monkeypatch):
    mod = monitor_task_mod
    _stub_api(monkeypatch, mod, [{"state": "RUNNING"}, {"state": "FINISHED"}])
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = _invoke_app(
        mod.build_cli(),
        ["watch", TASK_UUID, "--poll", "9", "--poll", "0.25"],
    )

    assert result.returncode == 0
    assert sleeps == [0.25]


def test_watch_rejects_nonfinite_config_interval_without_authentication(
    monitor_task_mod, monkeypatch, tmp_path
):
    mod = monitor_task_mod
    config = tmp_path / "monitor-task.toml"
    config.write_text("[monitor]\npoll_interval = inf\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_SETTINGS_PATH", config)
    monkeypatch.setattr(
        mod,
        "_create_client",
        lambda runtime: pytest.fail("invalid settings must fail before authentication"),
    )

    result = _invoke_app(mod.build_cli(), ["watch", TASK_UUID])
    assert result.returncode == 2
    assert "finite number greater than zero" in result.stderr
    assert "Traceback" not in result.stderr


def test_optional_task_fields_and_extreme_progress_are_tolerated(monitor_task_mod):
    huge_integer = 10**10000
    state, name, percent, message = monitor_task_mod._task_fields(
        {
            "state": ["RUNNING"],
            "name": 7,
            "taskProgress": {"progressInPercent": huge_integer},
        }
    )
    assert (state, name, percent, message) == ("unknown", "", None, "")


def test_watch_ctrl_c_is_clean(monitor_task_mod, monkeypatch):
    mod = monitor_task_mod
    _stub_api(monkeypatch, mod, [{"state": "RUNNING"}])

    def cancel(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(mod.time, "sleep", cancel)
    result = _invoke_app(mod.build_cli(), ["watch", TASK_UUID, "--poll", "0.1"])
    assert result.returncode == 130
    assert "Cancelled." in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_refresh_token_points_to_login(monitor_task_mod, monkeypatch):
    mod = monitor_task_mod
    monkeypatch.delenv("NETCUP_SCP_API_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr(mod.netcup_scp_client, "load_env_file", lambda: None)
    monkeypatch.setattr(
        mod.netcup_scp_client,
        "configure_api",
        lambda load_environment=False: {"api.base_url": "https://invalid.test"},
    )
    result = _invoke_app(mod.build_cli(), ["show", TASK_UUID])
    assert result.returncode == 2
    assert "missing NETCUP_SCP_API_REFRESH_TOKEN" in result.stderr
    assert "./scp-api.py login" in result.stderr


def test_netcup_client_raw_debug_option_is_explicit_and_reversible(
    monitor_task_mod, monkeypatch, capsys
):
    client_module = monitor_task_mod.netcup_scp_client
    monkeypatch.setattr(client_module, "BASE_URL", "https://invalid.test")
    monkeypatch.setattr(client_module, "DEBUG", True)
    monkeypatch.setattr(client_module, "DEBUG_RAW", False)
    monkeypatch.setattr(client_module, "DEBUG_LOGGER", None)
    body = json.dumps({"rootPassword": "root-secret"}).encode()
    monkeypatch.setattr(
        client_module.urllib.request,
        "urlopen",
        lambda request, timeout=30: FakeHTTPResponse(body),
    )

    client_module.NetcupSCPClient("token").get("/task")
    assert "***REDACTED***" in capsys.readouterr().err
    monkeypatch.setattr(client_module, "DEBUG_RAW", True)
    client_module.NetcupSCPClient("token").get("/task")
    assert "root-secret" in capsys.readouterr().err


def test_netcup_client_uses_registered_cli_logger(monitor_task_mod, monkeypatch):
    client_module = monitor_task_mod.netcup_scp_client
    logger = Mock()
    monkeypatch.setattr(client_module, "DEBUG", True)
    monkeypatch.setattr(client_module, "DEBUG_LOGGER", logger)

    client_module.log_debug("request details")

    logger.debug.assert_called_once_with("request details")
