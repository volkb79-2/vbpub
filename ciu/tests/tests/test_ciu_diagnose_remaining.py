from __future__ import annotations

import json
from subprocess import CompletedProcess

import pytest

from ciu import diagnose


def test_run_uses_bounded_text_subprocess_boundary(monkeypatch):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return CompletedProcess(argv, 0, "containers", "")

    monkeypatch.setattr(diagnose.subprocess, "run", fake_run)

    result = diagnose._run(["docker", "ps"])

    assert result.stdout == "containers"
    assert calls == [(["docker", "ps"], {"text": True, "capture_output": True, "check": False})]


def test_inspect_scopes_project_and_reports_failed_probes(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv):
        calls.append(argv)
        if argv[:3] == ["docker", "ps", "-aq"]:
            return CompletedProcess(argv, 0, "first\nsecond\n", "")
        return CompletedProcess(argv, 0, json.dumps([{"Name": "/first"}]), "")

    monkeypatch.setattr(diagnose, "_run", fake_run)

    assert diagnose._inspect("demo") == [{"Name": "/first"}]
    assert calls[0][-2:] == ["--filter", "label=com.docker.compose.project=demo"]
    assert calls[1] == ["docker", "inspect", "first", "second"]

    monkeypatch.setattr(
        diagnose,
        "_run",
        lambda argv: CompletedProcess(argv, 1, "", "daemon unavailable") if argv[:3] == ["docker", "ps", "-aq"] else CompletedProcess(argv, 1, "", "inspection refused"),
    )
    with pytest.raises(RuntimeError, match="daemon unavailable"):
        diagnose._inspect(None)


def test_inspect_rejects_non_list_payload_and_inspect_failure(monkeypatch):
    def non_list(argv):
        if argv[:3] == ["docker", "ps", "-aq"]:
            return CompletedProcess(argv, 0, "one", "")
        return CompletedProcess(argv, 0, json.dumps({"Name": "/one"}), "")

    monkeypatch.setattr(diagnose, "_run", non_list)
    with pytest.raises(ValueError, match="non-list"):
        diagnose._inspect(None)

    def failed_inspect(argv):
        if argv[:3] == ["docker", "ps", "-aq"]:
            return CompletedProcess(argv, 0, "one", "")
        return CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(diagnose, "_run", failed_inspect)
    with pytest.raises(RuntimeError, match="docker inspect failed"):
        diagnose._inspect(None)


def test_collect_reports_non_oom_sigkill_bad_state_unhealthy_and_all_log_signatures(monkeypatch):
    inspected = [{
        "Name": "/worker",
        "State": {"Status": "exited", "ExitCode": 137, "OOMKilled": False, "Health": {"Status": "unhealthy", "Log": []}},
        "HostConfig": {"Memory": 2048, "MemorySwap": 0},
        "RestartCount": 0,
    }]

    def fake_run(argv):
        if argv[:3] == ["docker", "ps", "-aq"]:
            return CompletedProcess(argv, 0, "worker", "")
        if argv[:2] == ["docker", "inspect"]:
            return CompletedProcess(argv, 0, json.dumps(inspected), "")
        assert argv == ["docker", "logs", "--tail", "7", "worker"]
        return CompletedProcess(argv, 0, "OOM kill; no space left on device", "segmentation fault; cannot allocate memory")

    monkeypatch.setattr(diagnose, "_run", fake_run)

    findings = diagnose.collect(log_lines=7)
    by_code = {item.code: item for item in findings}
    assert {"exit_137", "bad_state", "unhealthy", "memory_exhaustion", "disk_full", "segfault"} == set(by_code)
    assert by_code["unhealthy"].summary == "Healthcheck is unhealthy"
    assert "OOMKilled flag is not conclusive" in by_code["exit_137"].remedy


def test_run_formats_stable_json_human_findings_and_empty_success(monkeypatch, capsys):
    warning = diagnose.Finding("warning", "cache", "restarted", "Container has restarted 1 time(s)", "Inspect history.")
    error = diagnose.Finding("error", "api", "disk_full", "Log indicates exhausted storage", "Free capacity.")

    monkeypatch.setattr(diagnose, "collect", lambda **kwargs: [warning])
    assert diagnose.run(project="demo", log_lines=3, json_output=False) == 0
    output = capsys.readouterr().out
    assert "[WARNING] cache: Container has restarted 1 time(s) (restarted)" in output
    assert "remedy: Inspect history." in output

    monkeypatch.setattr(diagnose, "collect", lambda **kwargs: [error])
    assert diagnose.run(project=None, log_lines=100, json_output=True) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"severity": "error", "container": "api", "code": "disk_full", "summary": "Log indicates exhausted storage", "remedy": "Free capacity."}]

    monkeypatch.setattr(diagnose, "collect", lambda **kwargs: [])
    assert diagnose.run(project=None, log_lines=100, json_output=False) == 0
    assert capsys.readouterr().out == "[OK] No common container failure signatures found.\n"


@pytest.mark.parametrize("error", [OSError("docker missing"), RuntimeError("probe failed"), ValueError("bad payload")])
def test_run_turns_boundary_failure_into_exit_two(monkeypatch, capsys, error):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(diagnose, "collect", fail)

    assert diagnose.run(project=None, log_lines=100, json_output=False) == 2
    assert capsys.readouterr().out == f"[ERROR] diagnose failed: {error}\n"
