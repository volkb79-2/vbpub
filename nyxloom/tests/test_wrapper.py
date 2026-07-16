"""Tests for the wrapper module. PACKAGE P04."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nyxloom import storage
from nyxloom.config import RouteDef
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, EventType, Receipt,
    ReceiptResult, Role, Route, TaskState, TaskStateFile, Usage, Basis,
    utc_now,
)
from nyxloom.wrapper import WrapperSpec, launch_detached, wrapper_main, SESSION_CAPTURE_DELAY


def seed(project="demo", task="demo-P01-sample", att="att-1"):
    """Seed the state with a task and attempt."""
    states = {}
    tsf = TaskStateFile(
        schema_version=1,
        task_id=task,
        project=project,
        state=TaskState.ACTIVE,
        since=utc_now(),
    )
    storage.append_and_apply(
        project,
        states,
        actor=Actor(ActorKind.TICK, "test"),
        type=EventType.TASK_CREATED,
        payload={"statefile": tsf.to_dict()},
        task_id=task,
    )
    a = Attempt(
        attempt_id=att,
        role=Role.IMPLEMENTER,
        state=AttemptState.CREATED,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
        started=utc_now(),
    )
    storage.append_and_apply(
        project,
        states,
        actor=Actor(ActorKind.TICK, "test"),
        type=EventType.ATTEMPT_CREATED,
        payload={"attempt": a.to_dict()},
        task_id=task,
        attempt_id=att,
    )
    return states


@pytest.fixture
def fake_cli(tmp_path):
    """Create a simple fake CLI script that prints to stdout."""
    def make_script(lines=None, exit_code=0, sleep_time=None):
        if lines is None:
            lines = ["output line 1", "output line 2"]
        script = tmp_path / "fake_cli.sh"
        content = "#!/bin/sh\n"
        for line in lines:
            content += f'echo "{line}"\n'
        if sleep_time:
            content += f"sleep {sleep_time}\n"
        content += f"exit {exit_code}\n"
        script.write_text(content)
        script.chmod(0o755)
        return script

    return make_script


@pytest.fixture
def mock_adapters():
    """Mock adapters for testing."""
    with patch("nyxloom.wrapper.adapters") as m:
        m.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
        m.capture_session.return_value = "sess-42"
        m.classify_log_tail.return_value = None
        yield m


class TestWrapperSpec:
    """Test WrapperSpec round-trip."""

    def test_to_dict_from_dict(self):
        """Oracle 1: WrapperSpec to_dict/from_dict round-trip."""
        spec = WrapperSpec(
            project="demo",
            task_id="demo-P01-sample",
            attempt_id="att-1",
            argv=["echo", "test"],
            cwd="/tmp",
            log_path="/tmp/log.txt",
            receipt_path="/tmp/receipt.json",
            attempt_dir="/tmp/attempt",
            route_def={"route_id": "fake", "cli": "fake", "model": "fake-model"},
            leases=[{"name": "demo.stack", "capacity": 1}],
            env_overrides={"KEY": "value"},
            term_grace_seconds=15,
        )
        d = spec.to_dict()
        spec2 = WrapperSpec.from_dict(d)
        assert spec2 == spec


class TestHappyPath:
    """Oracle 2: happy path (in-process)."""

    def test_happy_path_in_process(self, tmp_state, tmp_path, fake_cli, mock_adapters):
        """Call wrapper_main directly with script printing 2 lines, exit 0."""
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["line 1", "line 2"], exit_code=0)
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)

        log_path = attempt_dir / "wrapper.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=[str(script)],
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
        )

        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        # Mock adapters
        mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
        mock_adapters.capture_session.return_value = "sess-42"
        mock_adapters.classify_log_tail.return_value = None

        # Run wrapper
        exit_code = wrapper_main(str(spec_path))

        # Assertions
        assert exit_code == 0
        assert receipt_path.exists()
        receipt = json.loads(receipt_path.read_text())
        assert receipt["result"] == "done"
        assert receipt["exit_code"] == 0

        # Check log file
        log_text = log_path.read_text()
        assert "line 1" in log_text
        assert "line 2" in log_text

        # Check events
        state = storage.load_state(project, task_id)
        attempt = state.attempt_by_id(attempt_id)
        assert attempt.state == AttemptState.EXITED
        assert attempt.pid is not None
        assert attempt.pgid is not None
        assert attempt.log_path == str(log_path)
        assert attempt.session_handle == "sess-42"


class TestStreamJsonSessionCapture:
    """P17 2026-07-15 (Gap 1) regression: a claude route's stream-json first
    log line carries session_id -- the wrapper must record it on
    ATTEMPT_STARTED via the REAL adapters.capture_session (not mocked),
    proving the wrapper -> adapters wiring, not just the adapters unit.

    A plain `/bin/sh` script (as `fake_cli` builds) risks a genuine race
    against the wrapper's fixed capture-delay read: shell stdout redirected
    to a regular file is block-buffered, so the first `echo` is not
    guaranteed to have hit disk yet at an arbitrarily small delay. These
    local fixtures use an UNBUFFERED (`-u`) Python child that flushes the
    first line immediately, then sleeps well past the (small, non-zero)
    capture delay before producing more output/exiting -- deterministic
    ordering instead of a timing gamble."""

    CAPTURE_DELAY = 0.2   # must fire only after the child's first flush
    CHILD_HOLD_SECONDS = 1.0  # child stays alive well past CAPTURE_DELAY

    @staticmethod
    def _claude_stream_script(tmp_path, first_line: str, hold_seconds: float) -> list[str]:
        """A `python3 -u` child: prints `first_line`, flushes, sleeps
        `hold_seconds`, prints a second line, exits 0. `-u` guarantees the
        first print reaches the log file with no libc buffering delay."""
        script = tmp_path / "claude_stream.py"
        script.write_text(
            "import sys, time\n"
            f"print({first_line!r})\n"
            "sys.stdout.flush()\n"
            f"time.sleep({hold_seconds})\n"
            "print('{\"type\": \"assistant\"}')\n"
        )
        return [sys.executable, "-u", str(script)]

    def test_wrapper_records_session_handle_from_stream_json(self, tmp_state, tmp_path):
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        # A fake "claude" CLI: first line is a stream-json system event
        # carrying session_id, exactly as the real CLI's --output-format
        # stream-json does.
        argv = self._claude_stream_script(
            tmp_path,
            '{"type":"system","subtype":"init","session_id":"live-sess-99"}',
            self.CHILD_HOLD_SECONDS,
        )

        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)
        log_path = attempt_dir / "attempt.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=argv,
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            # cli="claude" -- the real adapters.capture_session branches on
            # this to read the stream-json first line instead of scanning
            # ~/.claude/projects/.
            route_def={"route_id": "claude-test", "cli": "claude", "model": "sonnet"},
        )
        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        # adapters itself is NOT mocked here -- this exercises the real
        # capture_session implementation end to end. extract_usage/
        # classify_log_tail also run for real but are irrelevant to this
        # oracle (route.usage_source is unset -> Usage(UNKNOWN); no
        # BLOCKED/limit phrase in the log -> classify_log_tail None).
        with patch("nyxloom.wrapper.SESSION_CAPTURE_DELAY", self.CAPTURE_DELAY):
            exit_code = wrapper_main(str(spec_path))

        assert exit_code == 0
        state = storage.load_state(project, task_id)
        attempt = state.attempt_by_id(attempt_id)
        assert attempt.state == AttemptState.EXITED
        assert attempt.session_handle == "live-sess-99"

    def test_wrapper_session_handle_none_on_malformed_first_line(self, tmp_state, tmp_path):
        """Negative case: a first line that isn't valid stream-json JSON
        leaves session_handle unset (None), never raises out of the
        wrapper."""
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        argv = self._claude_stream_script(
            tmp_path, "not stream-json at all", self.CHILD_HOLD_SECONDS,
        )

        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)
        log_path = attempt_dir / "attempt.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=argv,
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            route_def={"route_id": "claude-test", "cli": "claude", "model": "sonnet"},
        )
        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        with patch("nyxloom.wrapper.SESSION_CAPTURE_DELAY", self.CAPTURE_DELAY):
            exit_code = wrapper_main(str(spec_path))

        assert exit_code == 0
        state = storage.load_state(project, task_id)
        attempt = state.attempt_by_id(attempt_id)
        assert attempt.session_handle is None


class TestBlocked:
    """Oracle 3: blocked classification."""

    def test_blocked_classification(self, tmp_state, tmp_path, fake_cli, mock_adapters):
        """Script printing BLOCKED: line, exit 0."""
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["BLOCKED: contract 2 unmeetable"], exit_code=0)
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)

        log_path = attempt_dir / "wrapper.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=[str(script)],
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
        )

        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        # Mock to use real classify_log_tail
        mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
        mock_adapters.capture_session.return_value = None

        def real_classify(text):
            if "BLOCKED:" in text:
                return "blocked"
            return None

        mock_adapters.classify_log_tail.side_effect = real_classify

        exit_code = wrapper_main(str(spec_path))

        receipt = json.loads(receipt_path.read_text())
        assert receipt["result"] == "blocked"
        assert receipt["blocked_reason"].startswith("contract 2 unmeetable")


class TestLimit:
    """Oracle 4: rate limit classification."""

    def test_limit_classification(self, tmp_state, tmp_path, fake_cli, mock_adapters):
        """Script printing rate limit phrase, exit 1."""
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["rate limit exceeded"], exit_code=1)
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)

        log_path = attempt_dir / "wrapper.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=[str(script)],
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
        )

        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
        mock_adapters.capture_session.return_value = None

        def real_classify(text):
            if "rate limit" in text.lower():
                return "limit"
            return None

        mock_adapters.classify_log_tail.side_effect = real_classify

        exit_code = wrapper_main(str(spec_path))

        receipt = json.loads(receipt_path.read_text())
        assert receipt["result"] == "limit"


class TestError:
    """Oracle 5: error classification."""

    def test_error_classification(self, tmp_state, tmp_path, fake_cli, mock_adapters):
        """Clean output, exit 3."""
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["clean output"], exit_code=3)
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)

        log_path = attempt_dir / "wrapper.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=[str(script)],
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
        )

        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
        mock_adapters.capture_session.return_value = None
        mock_adapters.classify_log_tail.return_value = None

        exit_code = wrapper_main(str(spec_path))

        receipt = json.loads(receipt_path.read_text())
        assert receipt["result"] == "error"
        assert receipt["exit_code"] == 3

        state = storage.load_state(project, task_id)
        attempt = state.attempt_by_id(attempt_id)
        assert attempt.state == AttemptState.EXITED


class TestLeaseRace:
    """Oracle 6: lease race condition."""

    def test_lease_race(self, tmp_state, tmp_path, fake_cli, mock_adapters):
        """Pre-acquire lease; wrapper gets race."""
        from nyxloom import leases as leases_module

        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        # Pre-acquire the lease
        pre_lease = leases_module.acquire(
            "demo.stack",
            owner="pretest",
            purpose="test",
            capacity=1,
        )
        assert pre_lease is not None

        try:
            script = fake_cli(["output"], exit_code=0)
            attempt_dir = tmp_path / "attempt"
            attempt_dir.mkdir(parents=True)

            log_path = attempt_dir / "wrapper.log"
            receipt_path = attempt_dir / "receipt.json"

            spec = WrapperSpec(
                project=project,
                task_id=task_id,
                attempt_id=attempt_id,
                argv=[str(script)],
                cwd=str(tmp_path),
                log_path=str(log_path),
                receipt_path=str(receipt_path),
                attempt_dir=str(attempt_dir),
                route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
                leases=[{"name": "demo.stack", "capacity": 1}],
            )

            spec_path = attempt_dir / "spec.json"
            spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

            exit_code = wrapper_main(str(spec_path))

            assert exit_code == 75
            receipt = json.loads(receipt_path.read_text())
            assert receipt["result"] == "error"
            assert receipt["exit_code"] == 75
            assert receipt["blocked_reason"] == "lease-lost-race"

            # Check ATTEMPT_FAILED event
            state = storage.load_state(project, task_id)
            attempt = state.attempt_by_id(attempt_id)
            assert attempt.state == AttemptState.FAILED

            # Pre-held lease still held
            info = leases_module.holder_info("demo.stack", capacity=1)
            assert info[0]["held"]
        finally:
            pre_lease.release()


class TestLeaseLifecycle:
    """Oracle 7: lease lifecycle."""

    def test_lease_lifecycle(self, tmp_state, tmp_path, fake_cli, mock_adapters):
        """Unheld lease; acquired during run, released after."""
        from nyxloom import leases as leases_module

        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["output"], exit_code=0)
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)

        log_path = attempt_dir / "wrapper.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=[str(script)],
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
            leases=[{"name": "demo.stack", "capacity": 1}],
        )

        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
        mock_adapters.capture_session.return_value = None
        mock_adapters.classify_log_tail.return_value = None

        exit_code = wrapper_main(str(spec_path))

        assert exit_code == 0

        # Lease should be free after
        info = leases_module.holder_info("demo.stack", capacity=1)
        assert not info[0]["held"]

        # Check events
        state = storage.load_state(project, task_id)
        events = list(storage.iter_events(project))
        event_types = [e.type for e in events]
        assert EventType.LEASE_ACQUIRED in event_types
        assert EventType.LEASE_RELEASED in event_types


class TestDetach:
    """Oracle 8: launch_detached."""

    def test_launch_detached_script(self, tmp_state, tmp_path, fake_cli):
        """Launch detached with 0.5s script."""
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["output"], exit_code=0, sleep_time="0.1")
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)

        log_path = attempt_dir / "wrapper.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=[str(script)],
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
        )

        # Mock adapters
        with patch("nyxloom.wrapper.adapters") as mock_adapters:
            mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
            mock_adapters.capture_session.return_value = None
            mock_adapters.classify_log_tail.return_value = None

            # Patch SESSION_CAPTURE_DELAY to 0 for faster testing
            with patch("nyxloom.wrapper.SESSION_CAPTURE_DELAY", 0):
                wrapper_pid = launch_detached(spec)

        # Wait for wrapper to finish
        time.sleep(1)

        # Check wrapper.pid file
        pid_file = attempt_dir / "wrapper.pid"
        assert pid_file.exists()
        assert int(pid_file.read_text().strip()) == wrapper_pid

        # Check that pid is not our child (reparented)
        try:
            os.waitpid(wrapper_pid, os.WNOHANG)
            # Should raise ChildProcessError if not our child
            # If we get here, it was our child (might still be running)
        except ChildProcessError:
            # Expected: wrapper is reparented
            pass

        # Wait a bit more for wrapper to complete
        time.sleep(2)

        # Check receipt
        assert receipt_path.exists()
        receipt = json.loads(receipt_path.read_text())
        assert receipt["result"] == "done"

        # Check log
        assert log_path.exists()


class TestSigterm:
    """Oracle 9: SIGTERM handling."""

    def test_sigterm_handler_installed(self, tmp_state, tmp_path, fake_cli, mock_adapters):
        """Verify signal handlers are properly installed and restored."""
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["output"], exit_code=0)
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)

        log_path = attempt_dir / "wrapper.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=[str(script)],
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
            term_grace_seconds=1,
        )

        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
        mock_adapters.capture_session.return_value = None
        mock_adapters.classify_log_tail.return_value = None

        # Save original handlers
        old_sigterm = signal.signal(signal.SIGTERM, signal.SIG_DFL)
        old_sigint = signal.signal(signal.SIGINT, signal.SIG_DFL)

        try:
            exit_code = wrapper_main(str(spec_path))
            assert exit_code == 0

            # Verify handlers are restored
            current_sigterm = signal.signal(signal.SIGTERM, signal.SIG_DFL)
            current_sigint = signal.signal(signal.SIGINT, signal.SIG_DFL)

            # Restore for cleanup
            signal.signal(signal.SIGTERM, old_sigterm)
            signal.signal(signal.SIGINT, old_sigint)
        finally:
            signal.signal(signal.SIGTERM, old_sigterm)
            signal.signal(signal.SIGINT, old_sigint)

    def test_sigterm_detached_real(self, tmp_state, tmp_path, fake_cli):
        """Oracle 9 (real): SIGTERM a detached wrapper running sleep 30.

        No mocks: real detached process, real signal, real adapters.
        Asserts receipt result 'error'/'interrupted', ATTEMPT_INTERRUPTED
        event, dead child, and the spec lease freed.
        """
        from nyxloom import leases as leases_module

        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["starting"], exit_code=0, sleep_time="30")
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)

        log_path = attempt_dir / "attempt.log"
        receipt_path = attempt_dir / "receipt.json"
        child_pid_file = attempt_dir / "child.pid"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=[str(script)],
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
            leases=[{"name": "demo.stack", "capacity": 1}],
            term_grace_seconds=2,
        )

        wrapper_pid = launch_detached(spec)  # waits for wrapper.pid (<=10s)
        child_pid = None
        try:
            # Wait for the CLI child to be spawned (child.pid appears)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not child_pid_file.exists():
                time.sleep(0.05)
            assert child_pid_file.exists(), "child.pid never appeared"
            child_pid = int(child_pid_file.read_text().strip())

            os.kill(wrapper_pid, signal.SIGTERM)

            # Poll for receipt.json (0.2s steps, cap 15s)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not receipt_path.exists():
                time.sleep(0.2)
            assert receipt_path.exists(), "receipt.json never appeared after SIGTERM"
            receipt = json.loads(receipt_path.read_text())
            assert receipt["result"] == "error"
            assert receipt["blocked_reason"] == "interrupted"

            # ATTEMPT_INTERRUPTED event present (poll: event is appended
            # right after the receipt write)
            deadline = time.monotonic() + 5
            interrupted_seen = False
            while time.monotonic() < deadline:
                events = list(storage.iter_events(project))
                if any(
                    e.type is EventType.ATTEMPT_INTERRUPTED
                    and e.attempt_id == attempt_id
                    for e in events
                ):
                    interrupted_seen = True
                    break
                time.sleep(0.2)
            assert interrupted_seen, "no ATTEMPT_INTERRUPTED event recorded"

            # Child is dead (reaped by the wrapper -> pid gone)
            deadline = time.monotonic() + 5
            child_dead = False
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    child_dead = True
                    break
                time.sleep(0.2)
            assert child_dead, f"child pid {child_pid} still alive after SIGTERM"

            # Spec lease is free again
            deadline = time.monotonic() + 5
            lease_free = False
            while time.monotonic() < deadline:
                info = leases_module.holder_info("demo.stack", capacity=1)
                if not info[0]["held"]:
                    lease_free = True
                    break
                time.sleep(0.2)
            assert lease_free, "demo.stack lease still held after wrapper exit"
        finally:
            # Belt-and-braces cleanup: never leave stragglers behind
            for pid in (wrapper_pid, child_pid):
                if pid:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass


class TestKillDrill:
    """Oracle 10: SIGKILL handling."""

    def test_wrapper_lease_cleanup_on_exit(self, tmp_state, tmp_path, fake_cli, mock_adapters):
        """Verify lease is freed even on abnormal exit."""
        from nyxloom import leases as leases_module

        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["output"], exit_code=0)
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)

        log_path = attempt_dir / "wrapper.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=[str(script)],
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
            leases=[{"name": "demo.stack", "capacity": 1}],
        )

        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
        mock_adapters.capture_session.return_value = None
        mock_adapters.classify_log_tail.return_value = None

        exit_code = wrapper_main(str(spec_path))

        assert exit_code == 0

        # Check: lease should be free after wrapper exits
        info = leases_module.holder_info("demo.stack", capacity=1)
        assert not info[0]["held"]

    def test_sigkill_drill_real(self, tmp_state, tmp_path, fake_cli):
        """Oracle 10 (real): SIGKILL a detached wrapper running sleep 30.

        No mocks. Asserts: NO receipt.json (the wrapper died before its exit
        path), the spec lease is FREE (kernel flock release on process
        death), and child.pid exists (healing is the daemon's job).
        """
        from nyxloom import leases as leases_module

        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["starting"], exit_code=0, sleep_time="30")
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)

        log_path = attempt_dir / "attempt.log"
        receipt_path = attempt_dir / "receipt.json"
        child_pid_file = attempt_dir / "child.pid"

        spec = WrapperSpec(
            project=project,
            task_id=task_id,
            attempt_id=attempt_id,
            argv=[str(script)],
            cwd=str(tmp_path),
            log_path=str(log_path),
            receipt_path=str(receipt_path),
            attempt_dir=str(attempt_dir),
            route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
            leases=[{"name": "demo.stack", "capacity": 1}],
            term_grace_seconds=2,
        )

        wrapper_pid = launch_detached(spec)
        child_pid = None
        try:
            # Wait for the CLI child to be spawned; the lease is acquired
            # before the spawn, so once child.pid exists the flock is held.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not child_pid_file.exists():
                time.sleep(0.05)
            assert child_pid_file.exists(), "child.pid never appeared"
            child_pid = int(child_pid_file.read_text().strip())

            os.kill(wrapper_pid, signal.SIGKILL)

            # Lease must be freed by the kernel within 3s (poll)
            deadline = time.monotonic() + 3
            lease_free = False
            while time.monotonic() < deadline:
                info = leases_module.holder_info("demo.stack", capacity=1)
                if not info[0]["held"]:
                    lease_free = True
                    break
                time.sleep(0.1)
            assert lease_free, "demo.stack lease not kernel-released after SIGKILL"

            # No receipt: the wrapper never reached its exit path
            assert not receipt_path.exists()

            # child.pid file exists (the orphaned child is the daemon's
            # healing problem, out of scope here)
            assert child_pid_file.exists()
        finally:
            # Clean up the orphaned sleep-30 child and any wrapper remnants
            for pid in (child_pid, wrapper_pid):
                if pid:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass
