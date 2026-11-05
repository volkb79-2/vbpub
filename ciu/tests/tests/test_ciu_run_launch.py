"""CIU-141 foreground and detached lifecycle contracts through procutil fakes."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ciu import engine, oneshot

_CONTAINER_ID = "a" * 64
_NETWORK_NAME = "demo_instance-net"


def _service():
    return {
        "image": "python:3.11-slim",
        "user": "1001:1001",
        "working_dir": "/workspace",
        "cgroup_parent": "dev-gates.slice",
        "tty": True,
        "stdin_open": True,
        "networks": {"instance-net": {}},
        "volumes": [],
        "environment": {"TOKEN": "planned-secret"},
        "labels": {"service.label": "kept"},
    }


def _prepared(tmp_path):
    return SimpleNamespace(
        working_dir=tmp_path,
        repo_root=tmp_path,
        project="demo-runner",
        file_args=["-f", "ciu.compose.yml"],
        compose_env={"SCHEMA_GATE_DSN": "password-bearing-dsn"},
        rendered_compose="services: {}\n",
        ownership_path=None,
        config={},
        secret_values=frozenset({"planned-secret"}),
    )


def _compose_config():
    return {
        "services": {"worker": _service()},
        "networks": {"instance-net": {"name": _NETWORK_NAME, "external": True}},
    }


def _labels_from(argv):
    labels = {}
    for index, token in enumerate(argv[:-1]):
        if token in {"-l", "--label"}:
            key, _, value = argv[index + 1].partition("=")
            labels[key] = value
    return labels


def _inspect_document(name, labels, *, status="running", exit_code=0):
    return {
        "Id": _CONTAINER_ID,
        "Name": f"/{name}",
        "Image": "sha256:imageid",
        "Config": {
            "Image": "python:3.11-slim",
            "User": "1001:1001",
            "Labels": labels,
        },
        "HostConfig": {"CgroupParent": "dev-gates.slice", "CgroupnsMode": "private"},
        "NetworkSettings": {"Networks": {_NETWORK_NAME: {}}},
        "State": {
            "Status": status,
            "ExitCode": exit_code,
            "OOMKilled": False,
            "FinishedAt": "2026-11-05T00:00:00Z",
        },
    }


class _FakeProcess:
    def __init__(self, returncode, *, event_file=None):
        self.returncode = returncode
        self.event_file = Path(event_file) if event_file else None
        self.argv = None

    def poll(self):
        if (
            self.event_file is not None
            and (
                not self.event_file.exists()
                or '"event":"launched"' not in self.event_file.read_text()
            )
        ):
            return None
        return self.returncode


def _install_fake_docker(monkeypatch, *, returncode=0, event_file=None):
    calls = []
    process = _FakeProcess(returncode, event_file=event_file)

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        calls.append((argv, kwargs))
        if argv[:3] == ["docker", "compose", "--progress"] and "config" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(_compose_config()), "")
        if argv[:3] == ["docker", "compose", "--progress"] and "run" in argv:
            process.argv = argv
            if "-d" in argv:
                return subprocess.CompletedProcess(argv, 0, _CONTAINER_ID + "\n", "")
            raise AssertionError("foreground compose run must use procutil.popen")
        if argv[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps([{"Name": argv[-1], "Labels": {}}]), "",
            )
        if argv[:2] == ["docker", "inspect"]:
            document = _inspect_document(
                argv[-1], _labels_from(process.argv or []),
                status="exited", exit_code=process.returncode or 0,
            )
            return subprocess.CompletedProcess(argv, 0, json.dumps([document]), "")
        if argv[:3] == ["docker", "rm", "-f"]:
            return subprocess.CompletedProcess(argv, 0, argv[-1] + "\n", "")
        if argv[:3] == ["docker", "kill", "-s"] or argv[:2] == ["docker", "kill"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected docker command: {argv!r}")

    def popen(argv, **kwargs):
        process.argv = list(argv)
        calls.append((list(argv), kwargs))
        return process

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)
    monkeypatch.setattr(oneshot.procutil, "popen", popen, raising=False)
    return calls, process


def _mock_preparation(monkeypatch, prepared):
    monkeypatch.setattr(engine, "verify_workspace_runtime_network", lambda *_args: None)
    monkeypatch.setattr(
        oneshot, "_prepare_run_stack",
        lambda *args, **kwargs: (prepared, "tools/runner", "worker"),
    )


def test_detached_run_crosschecks_exact_id_and_prints_one_launch_document(
    tmp_path, monkeypatch, capsys,
):
    prepared = _prepared(tmp_path)
    _mock_preparation(monkeypatch, prepared)
    calls, _process = _install_fake_docker(monkeypatch)

    result = oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["python", "-c", "print('ok')"],
        detach=True, json_output=True, name="ciurun-test-unit-1",
        labels=["run-gate.job=lane 1"], env_names=["SCHEMA_GATE_DSN"],
        owner_pid=os.getpid(),
    )

    captured = capsys.readouterr()
    event = json.loads(captured.out)
    assert result == 0
    assert captured.err == ""
    assert event["schema"] == "ciu-run-event/1"
    assert event["event"] == "launched"
    assert event["container"] == {"id": _CONTAINER_ID, "name": "ciurun-test-unit-1"}
    assert event["run_id"]
    assert event["labels"]["ciu.run"] == "1"
    assert event["labels"]["run-gate.job"] == "lane 1"
    assert "password-bearing-dsn" not in captured.out

    compose, kwargs = next(
        (argv, kwargs) for argv, kwargs in calls if "run" in argv and "compose" in argv
    )
    assert compose[:4] == ["docker", "compose", "--progress", "quiet"]
    assert "--no-deps" in compose and "-d" in compose
    assert "--name" in compose and "ciurun-test-unit-1" in compose
    assert "-T" in compose and "--interactive=false" in compose
    assert "--use-aliases" not in compose and "--rm" not in compose
    assert "--remove-orphans" not in compose and "--pull" not in compose
    assert "-e" in compose and "SCHEMA_GATE_DSN" in compose
    assert "password-bearing-dsn" not in compose
    assert "CIU_RUN_CONTAINER_NAME=ciurun-test-unit-1" in compose
    assert any(token.startswith("CIU_RUN_ID=") for token in compose)
    assert kwargs["env"]["SCHEMA_GATE_DSN"] == "password-bearing-dsn"
    assert any(argv == ["docker", "inspect", "ciurun-test-unit-1"] for argv, _ in calls)


@pytest.mark.parametrize("exit_code", [0, 1, 42, 130, 137])
def test_foreground_status_passthrough_and_event_order(
    tmp_path, monkeypatch, capsys, exit_code,
):
    prepared = _prepared(tmp_path)
    _mock_preparation(monkeypatch, prepared)
    events_path = tmp_path / "events.jsonl"
    calls, _process = _install_fake_docker(
        monkeypatch, returncode=exit_code, event_file=events_path,
    )

    result = oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["python", "-c", "exit()"],
        events_file=events_path, name=f"ciurun-test-unit-{exit_code}",
        owner_pid=os.getpid(),
    )

    captured = capsys.readouterr()
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert result == exit_code
    assert captured.out == ""
    assert '"event"' not in captured.err
    assert [event["event"] for event in events] == ["launched", "exited", "removed"]
    assert events[1]["exit_code"] == exit_code
    assert events[2] == {
        "schema": "ciu-run-event/1", "event": "removed",
        "run_id": events[0]["run_id"], "removed": True, "error": None,
    }
    assert events_path.stat().st_mode & 0o777 == 0o600
    assert any(
        argv[:3] == ["docker", "rm", "-f"] and argv[-1] == f"ciurun-test-unit-{exit_code}"
        for argv, _ in calls
    )
    compose, kwargs = next(
        (argv, kwargs) for argv, kwargs in calls if "run" in argv and "compose" in argv
    )
    assert "-T" in compose and "--interactive=false" in compose
    assert kwargs.get("stdout") is None and kwargs.get("stderr") is None


def test_foreground_sigterm_is_forwarded_once_to_the_exact_container(
    tmp_path, monkeypatch,
):
    prepared = _prepared(tmp_path)
    _mock_preparation(monkeypatch, prepared)
    events_path = tmp_path / "events.jsonl"
    calls, _process = _install_fake_docker(
        monkeypatch, returncode=143, event_file=events_path,
    )
    real_signal = signal.signal
    triggered = False

    def deliver_sigterm(signum, handler):
        nonlocal triggered
        previous = real_signal(signum, handler)
        if signum == signal.SIGTERM and callable(handler) and not triggered:
            triggered = True
            handler(signal.SIGTERM, None)
        return previous

    monkeypatch.setattr(oneshot.signal, "signal", deliver_sigterm)

    result = oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["python", "-c", "sleep()"],
        events_file=events_path, name="ciurun-test-unit-sigterm", owner_pid=os.getpid(),
    )

    assert result == 143
    forwarded = [
        argv for argv, _ in calls
        if argv[:3] == ["docker", "kill", "-s"]
    ]
    assert forwarded == [["docker", "kill", "-s", "SIGTERM", "ciurun-test-unit-sigterm"]]


def test_foreground_escalates_to_exact_container_after_signal_grace(
    tmp_path, monkeypatch,
):
    prepared = _prepared(tmp_path)
    _mock_preparation(monkeypatch, prepared)
    events_path = tmp_path / "events.jsonl"
    calls, process = _install_fake_docker(
        monkeypatch, returncode=None, event_file=events_path,
    )
    real_signal = signal.signal
    triggered = False

    def deliver_sigint(signum, handler):
        nonlocal triggered
        previous = real_signal(signum, handler)
        if signum == signal.SIGINT and callable(handler) and not triggered:
            triggered = True
            handler(signal.SIGINT, None)
        return previous

    clock_tick = 0

    def monotonic():
        nonlocal clock_tick
        clock_tick += 1
        return float(clock_tick)

    original_run_cmd = oneshot.procutil.run_cmd

    def run_cmd(argv, **kwargs):
        result = original_run_cmd(argv, **kwargs)
        if list(argv) == ["docker", "kill", "ciurun-test-unit-escalate"]:
            process.returncode = 137
        return result

    monkeypatch.setattr(oneshot.signal, "signal", deliver_sigint)
    monkeypatch.setattr(oneshot.time, "monotonic", monotonic)
    monkeypatch.setattr(oneshot.time, "sleep", lambda _duration: None)
    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)

    result = oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["python", "-c", "ignore_signal()"],
        events_file=events_path, name="ciurun-test-unit-escalate", owner_pid=os.getpid(),
    )

    assert result == 137
    kill_calls = [argv for argv, _ in calls if argv[:2] == ["docker", "kill"]]
    assert kill_calls == [
        ["docker", "kill", "-s", "SIGINT", "ciurun-test-unit-escalate"],
        ["docker", "kill", "ciurun-test-unit-escalate"],
    ]


def test_stack_prepare_lock_is_exclusive_and_per_run_overlays_are_private(tmp_path):
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    with oneshot._stack_prepare_lock(stack_dir):
        lock_path = stack_dir / ".machine" / ".ciu-run.lock"
        assert lock_path.stat().st_mode & 0o777 == 0o600
        fd = os.open(lock_path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    ownership = stack_dir / ".machine" / "ciu.compose.ownership.yml"
    ownership.write_text("services:\n  worker:\n    labels:\n      ciu.instance: i1\n")
    prepared = _prepared(stack_dir)
    prepared.ownership_path = ownership
    prepared.file_args = [
        "-f", "ciu.compose.yml", "-f", ".machine/ciu.compose.ownership.yml",
    ]
    temp_roots = []
    for _ in range(2):
        with __import__("tempfile").TemporaryDirectory(prefix="ciu-run-test-") as temp:
            temp_dir = Path(temp)
            args = oneshot._compose_file_args(
                prepared, temp_dir=temp_dir, extend=None, service_name="worker",
            )
            overlay = Path(args[-1])
            assert overlay != ownership
            assert overlay.read_text() == ownership.read_text()
            temp_roots.append(temp_dir)
    assert temp_roots[0] != temp_roots[1]


def test_event_fd_is_writable_and_keeps_events_off_standard_streams(tmp_path, capsys):
    read_fd, write_fd = os.pipe()
    try:
        writer = oneshot._EventWriter(fd=write_fd)
        writer.write({"schema": "ciu-run-event/1", "event": "launched"})
        assert os.read(read_fd, 4096) == b'{"event":"launched","schema":"ciu-run-event/1"}\n'
        writer.close()  # borrowed descriptors remain open
        assert os.fstat(write_fd)
        with pytest.raises(ValueError, match="not writable"):
            oneshot._EventWriter(fd=read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_foreground_terminal_keeps_compose_tty_and_stdin_enabled(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)
    _mock_preparation(monkeypatch, prepared)
    _calls, _process = _install_fake_docker(monkeypatch, returncode=0)
    monkeypatch.setattr(oneshot.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(oneshot.sys, "stdout", SimpleNamespace(isatty=lambda: True))

    result = oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["python", "-V"],
        name="ciurun-test-unit-tty", owner_pid=os.getpid(),
    )

    assert result == 0
    compose = next(
        call[0] for call in _calls if "run" in call[0] and "compose" in call[0]
    )
    assert "-T" not in compose and "--interactive=false" not in compose


def test_detached_id_mismatch_is_refused_and_exact_name_is_removed(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)
    _mock_preparation(monkeypatch, prepared)
    calls = []

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        if argv[:3] == ["docker", "compose", "--progress"] and "config" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(_compose_config()), "")
        if argv[:3] == ["docker", "compose", "--progress"] and "run" in argv:
            return subprocess.CompletedProcess(argv, 0, "b" * 64 + "\n", "")
        if argv[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps([{"Name": _NETWORK_NAME}]), "")
        if argv[:2] == ["docker", "inspect"]:
            document = _inspect_document(argv[-1], {})
            return subprocess.CompletedProcess(argv, 0, json.dumps([document]), "")
        if argv[:3] == ["docker", "rm", "-f"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)

    with pytest.raises(RuntimeError, match="ID disagrees"):
        oneshot.run_cli(
            tmp_path, "tools/runner:worker", ["true"], detach=True,
            json_output=True, name="ciurun-test-unit-mismatch", owner_pid=os.getpid(),
        )

    assert ["docker", "rm", "-f", "ciurun-test-unit-mismatch"] in calls


def test_missing_stack_network_refuses_before_compose_run(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)
    _mock_preparation(monkeypatch, prepared)
    calls = []

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        if argv[:3] == ["docker", "compose", "--progress"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(_compose_config()), "")
        if argv[:3] == ["docker", "network", "inspect"]:
            return subprocess.CompletedProcess(argv, 1, "", "not found")
        raise AssertionError(f"one-shot must not launch: {argv!r}")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)
    monkeypatch.setattr(oneshot.procutil, "popen", lambda *args, **kwargs: pytest.fail("started"),
                        raising=False)

    with pytest.raises(ValueError, match="required stack network .* is not present"):
        oneshot.run_cli(
            tmp_path, "tools/runner:worker", ["true"],
            name="ciurun-test-unit-no-network", owner_pid=os.getpid(),
        )

    assert not any("run" in argv and "compose" in argv for argv in calls)
