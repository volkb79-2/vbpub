"""Tests for scp-api-monitor-task.py: redaction, settings loader, the
get_access_token error-handling fix (regression test for the bug found this
session), the 401-retry-refresh flow, and the poll loop reaching a terminal
state - all mocked (urllib), no live netcup calls."""
from __future__ import annotations

import io
import json
import sys
import urllib.error

import pytest

from conftest import FakeHTTPResponse


def test_redact_masks_sensitive_keys(monitor_task_mod):
    data = {"rootPassword": "secret", "nested": {"token": "t"}, "ok": "visible"}
    redacted = monitor_task_mod._redact(data)
    assert redacted["rootPassword"] == "***REDACTED***"
    assert redacted["nested"]["token"] == "***REDACTED***"
    assert redacted["ok"] == "visible"


def test_uuid_regex_matches_standard_uuid(monitor_task_mod):
    assert monitor_task_mod._UUID_RE.match("3a27fe8e-e747-4f3b-80b0-f930c0d0db3f")
    assert not monitor_task_mod._UUID_RE.match("not-a-uuid")


def test_load_settings_happy_path(monitor_task_mod, tmp_path):
    toml_path = tmp_path / "s.toml"
    toml_path.write_text('[a]\nb = 1\n')
    assert monitor_task_mod._load_settings(toml_path, {"a.b"}) == {"a.b": 1}


def test_load_settings_missing_key_errors(monitor_task_mod, tmp_path):
    toml_path = tmp_path / "s.toml"
    toml_path.write_text("[a]\n")
    with pytest.raises(SystemExit, match="missing required settings"):
        monitor_task_mod._load_settings(toml_path, {"a.b"})


def test_real_settings_file_is_valid(monitor_task_mod):
    assert monitor_task_mod.SETTINGS["monitor.poll_interval"] == 5.0


# --- get_access_token: regression test for the found-and-fixed bug -------
# Previously (when this used `requests`) an HTTP error could escape uncaught
# as a raw traceback instead of a clean message. Must raise RuntimeError.

def test_get_access_token_wraps_http_error_cleanly(monitor_task_mod, monkeypatch):
    def fake_urlopen(req, timeout=30):
        raise urllib.error.HTTPError(req.full_url, 400, "invalid_grant", None, io.BytesIO(b'{"error":"invalid_grant"}'))

    monkeypatch.setattr(monitor_task_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="Token request failed"):
        monitor_task_mod.get_access_token("dead-token")


def test_get_access_token_happy_path(monitor_task_mod, monkeypatch):
    body = json.dumps({"access_token": "tok123"}).encode()
    monkeypatch.setattr(monitor_task_mod.urllib.request, "urlopen", lambda req, timeout=30: FakeHTTPResponse(body))
    assert monitor_task_mod.get_access_token("refresh") == "tok123"


def test_client_get_retries_after_401(monitor_task_mod, monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", None, io.BytesIO(b"{}"))
        return FakeHTTPResponse(json.dumps({"state": "FINISHED"}).encode())

    monkeypatch.setattr(monitor_task_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(monitor_task_mod, "get_access_token", lambda rt: "new-token")

    client = monitor_task_mod.NetcupSCPClient("old-token", refresh_token="rt")
    result = client.get("/api/v1/tasks/x")
    assert result == {"state": "FINISHED"}
    assert calls["n"] == 2


# --- main(): --dry-run and the full poll loop -----------------------------

def test_main_dry_run_confirms_task_and_exits(monitor_task_mod, monkeypatch, capsys):
    mod = monitor_task_mod
    monkeypatch.setenv("NETCUP_SCP_API_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(mod, "get_access_token", lambda rt: "at")
    monkeypatch.setattr(mod.NetcupSCPClient, "get", lambda self, endpoint, params=None: {"state": "RUNNING"})
    monkeypatch.setattr(sys, "argv", ["scp-api-monitor-task.py", "00000000-0000-0000-0000-000000000000", "--dry-run"])

    mod.main()

    out = capsys.readouterr().out.lower()
    assert "task exists" in out or "ok" in out


def test_main_poll_loop_reaches_finished(monitor_task_mod, monkeypatch):
    mod = monitor_task_mod
    monkeypatch.setenv("NETCUP_SCP_API_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(mod, "get_access_token", lambda rt: "at")

    states = iter(["RUNNING", "RUNNING", "FINISHED"])

    def fake_get(self, endpoint, params=None):
        return {"state": next(states), "taskProgress": {"progressInPercent": 50}}

    monkeypatch.setattr(mod.NetcupSCPClient, "get", fake_get)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["scp-api-monitor-task.py", "00000000-0000-0000-0000-000000000000"])

    mod.main()  # must return once FINISHED is reached, not loop forever
