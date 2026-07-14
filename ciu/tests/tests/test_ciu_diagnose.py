from __future__ import annotations

import json
from subprocess import CompletedProcess

from ciu import diagnose


def test_detects_oom_unhealthy_and_redis_acl(monkeypatch):
    inspected = [{
        "Name": "/app",
        "State": {"Status": "running", "ExitCode": 0, "OOMKilled": True, "Health": {"Status": "unhealthy", "Log": [{"Output": "bad"}]}},
        "HostConfig": {"Memory": 1024, "MemorySwap": 1024},
        "RestartCount": 1,
    }]

    def fake_run(argv):
        if argv[:3] == ["docker", "ps", "-aq"]:
            return CompletedProcess(argv, 0, "id\n", "")
        if argv[:2] == ["docker", "inspect"]:
            return CompletedProcess(argv, 0, json.dumps(inspected), "")
        return CompletedProcess(argv, 0, "", "No permissions to access a channel")

    monkeypatch.setattr(diagnose, "_run", fake_run)
    codes = {item.code for item in diagnose.collect()}
    assert {"oom_killed", "unhealthy", "restarted", "swap_disabled", "redis_channel_acl"} <= codes


def test_empty_stack(monkeypatch):
    monkeypatch.setattr(diagnose, "_run", lambda argv: CompletedProcess(argv, 0, "", ""))
    assert diagnose.collect() == []
