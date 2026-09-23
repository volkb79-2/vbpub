"""Behavioral and CLI-contract tests for the Netcup task monitor."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cli_extended import CliIdentity, assert_cli_contract
from conftest import FakeHTTPResponse

TASK_UUID = "3a27fe8e-e747-4f3b-80b0-f930c0d0db3f"
SCRIPT = Path(__file__).resolve().parents[1] / "monitor-task.py"
REPO_ROOT = SCRIPT.parents[2]
CLI_LIBRARY = REPO_ROOT / "libraries" / "cli-extended" / "src"


def _invoke_app(app, argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = app.run(argv=argv, stdout=stdout, stderr=stderr)
    return SimpleNamespace(
        returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue()
    )


def _invoke_executable(argv, cwd: Path):
    environment = os.environ.copy()
    environment.pop("NETCUP_SCP_API_REFRESH_TOKEN", None)
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(CLI_LIBRARY) + (
        os.pathsep + existing if existing else ""
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
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
            self.responses = iter(responses)

        def get(self, endpoint):
            return next(self.responses)

    monkeypatch.setattr(mod, "NetcupSCPClient", FakeClient)


def test_real_executable_obeys_help_version_and_parse_contract(tmp_path):
    identity = CliIdentity(
        name="NETCUP SCP",
        command="monitor-task",
        version=(SCRIPT.parent / "VERSION").read_text(encoding="utf-8").strip(),
        long_name="Netcup Server Control Panel task monitor",
    )

    assert_cli_contract(
        lambda argv: _invoke_executable(argv, tmp_path),
        identity,
        ("show", "watch"),
        invalid_invocations={
            "missing show task UUID": ("show",),
            "malformed task UUID": ("watch", "not-a-uuid"),
            "poll interval is watch-only": ("show", TASK_UUID, "--poll", "1"),
        },
        known_verb_errors={
            "missing show task UUID": "show",
            "malformed task UUID": "watch",
            "poll interval is watch-only": "show",
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


def test_watch_rejects_invalid_poll_interval_before_authentication(
    monitor_task_mod, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid poll interval must be refused before API setup")

    monkeypatch.setattr(monitor_task_mod, "_create_client", forbidden)
    result = _invoke_app(
        monitor_task_mod.build_cli(), ["watch", TASK_UUID, "--poll", "nan"]
    )
    assert result.returncode == 2
    assert "finite number greater than zero" in result.stderr
    assert "usage:" in result.stderr
    assert "Traceback" not in result.stderr


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
