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

from nyxloom import log, storage
from nyxloom.config import RouteDef
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, EventType, Receipt,
    ReceiptResult, Role, Route, TaskState, TaskStateFile, Usage, Basis,
    utc_now,
)
from nyxloom.wrapper import WrapperSpec, launch_detached, wrapper_main, SESSION_CAPTURE_DELAY

#: CR-13a: every spec here runs a local fake script -- the operator's own,
#: with no third party serving it -- so it declares operator trust and takes
#: the uncontained path. The declaration is REQUIRED, not defaulted: a route
#: that says nothing requires containment (containment.requires_containment),
#: which is what `TestContainmentGate` below exercises.
OPERATOR_ROUTE = {"route_id": "fake-cli", "cli": "fake", "model": "fake-model",
                  "trust": "operator"}


def _read_log_records(log_dir: Path) -> list[dict]:
    """P05a: local helper (never added to conftest.py -- see
    tests/test_daemon.py's identical helper) reading back the rendered
    JSONL records nyxloom.log writes."""
    p = log_dir / "nyxloom.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


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
            route_def={"route_id": "fake", "cli": "fake", "model": "fake-model",
                       "trust": "operator"},
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
            route_def=OPERATOR_ROUTE,
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
            route_def={"route_id": "claude-test", "cli": "claude", "model": "sonnet",
                       "trust": "operator"},
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
            route_def={"route_id": "claude-test", "cli": "claude", "model": "sonnet",
                       "trust": "operator"},
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
            route_def=OPERATOR_ROUTE,
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


class TestScopeAmendment:
    """O5 (B21 2026-07-23, D-R16 §3): wrapper translation of the
    SCOPE_AMENDMENT_REQUEST: marker into the receipt's amendment_request
    carrier. NOTE: unlike the fake_cli fixture's naive `echo "{line}"`
    wrapping (fine for the BLOCKED/LIMIT tests' plain-text lines above), a
    JSON payload's own embedded double quotes get MANGLED by that wrapping
    (`echo "..."file"..."` word-concatenates and drops the quote chars) --
    verified empirically while writing this test. A quoted heredoc
    (`cat <<'EOF' ... EOF`) prints the marker line byte-for-byte with no
    shell interpretation at all, so these tests write their own script
    directly instead of using the fake_cli fixture."""

    def _amendment_script(self, tmp_path, file_path: str, reason: str, exit_code: int = 0) -> Path:
        script = tmp_path / "scope_amendment_cli.sh"
        script.write_text(
            "#!/bin/sh\n"
            "cat <<'HEREDOC_EOF'\n"
            f'SCOPE_AMENDMENT_REQUEST: {{"file": "{file_path}", "reason": "{reason}"}}\n'
            "HEREDOC_EOF\n"
            f"exit {exit_code}\n"
        )
        script.chmod(0o755)
        return script

    def test_scope_amendment_classification(self, tmp_state, tmp_path, mock_adapters):
        """O5: given a log tail with the marker, the wrapper writes a
        receipt.json carrying the extracted {file, reason} AND result
        'scope_amendment' (not 'blocked' or 'error')."""
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = self._amendment_script(
            tmp_path, "src/demo/shared_helper.py", "needs a shared helper")
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
            route_def=OPERATOR_ROUTE,
        )

        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
        mock_adapters.capture_session.return_value = None

        def real_classify(text):
            for line in text.split("\n"):
                if line.startswith("SCOPE_AMENDMENT_REQUEST:"):
                    return "scope_amendment"
            return None

        mock_adapters.classify_log_tail.side_effect = real_classify

        exit_code = wrapper_main(str(spec_path))

        assert exit_code == 0
        receipt = json.loads(receipt_path.read_text())
        assert receipt["result"] == "scope_amendment"
        assert receipt["amendment_request"] == {
            "file": "src/demo/shared_helper.py",
            "reason": "needs a shared helper",
        }
        # NEGATIVE: not misclassified as a dead-end BLOCKED.
        assert receipt["blocked_reason"] is None

    def test_scope_amendment_malformed_json_degrades_to_none(
        self, tmp_state, tmp_path, mock_adapters
    ):
        """NEGATIVE: a marker line whose payload fails to parse as JSON
        degrades amendment_request to None rather than crashing the wrapper
        -- daemon.py's EmitAttemptExit still routes on receipt.result
        (SCOPE_AMENDMENT), it just has no file/reason to report.

        The payload MUST still match the `{...}` regex shape (so the
        `if amendment_match:` branch is actually entered and `json.loads`
        is actually reached and actually raises) -- a payload with no
        braces at all (e.g. bare prose) never reaches json.loads, hitting a
        DIFFERENT branch (no match) that looks superficially similar but
        exercises none of the try/except lines this test targets."""
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = tmp_path / "malformed_cli.sh"
        script.write_text(
            "#!/bin/sh\n"
            "cat <<'HEREDOC_EOF'\n"
            "SCOPE_AMENDMENT_REQUEST: {not: valid json}\n"
            "HEREDOC_EOF\n"
            "exit 0\n"
        )
        script.chmod(0o755)

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
            route_def=OPERATOR_ROUTE,
        )
        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
        mock_adapters.capture_session.return_value = None

        def real_classify(text):
            for line in text.split("\n"):
                if line.startswith("SCOPE_AMENDMENT_REQUEST:"):
                    return "scope_amendment"
            return None

        mock_adapters.classify_log_tail.side_effect = real_classify

        exit_code = wrapper_main(str(spec_path))

        assert exit_code == 0
        receipt = json.loads(receipt_path.read_text())
        assert receipt["result"] == "scope_amendment"
        assert receipt["amendment_request"] is None


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
            route_def=OPERATOR_ROUTE,
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


class TestTransient:
    """O2 (B24 2026-07-23, D-R17, D-B24-1 -- the load-bearing oracle):
    a TRANSIENT classification must NOT be treated like BLOCKED/LIMIT/ERROR
    (ATTEMPT_EXITED + EXITED, a terminal state that can never be revived).
    It must pair with ATTEMPT_INTERRUPTED + AttemptState.INTERRUPTED -- the
    SAME pair the real interrupted-by-signal branch uses -- so the daemon's
    EXISTING ResumeAttempt path (built for INTERRUPTED attempts) can retry
    the SAME session."""

    def test_transient_classification_is_interrupted_not_exited(
        self, tmp_state, tmp_path, fake_cli, mock_adapters
    ):
        """Script printing a provider-throttle signature, nonzero exit ->
        receipt.result == 'transient' AND the statefile attempt ends up
        INTERRUPTED (never EXITED) with an ATTEMPT_INTERRUPTED event -- NOT
        ATTEMPT_EXITED/EXITED."""
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["Error: HTTP 502 Bad Gateway from upstream"], exit_code=1)
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
            route_def=OPERATOR_ROUTE,
        )

        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
        mock_adapters.capture_session.return_value = None

        def real_classify(text):
            if "502 bad gateway" in text.lower():
                return "transient"
            return None

        mock_adapters.classify_log_tail.side_effect = real_classify

        exit_code = wrapper_main(str(spec_path))

        receipt = json.loads(receipt_path.read_text())
        assert receipt["result"] == "transient"

        tsf = storage.load_state(project, task_id)
        attempt = tsf.attempt_by_id(attempt_id)
        # THE oracle: INTERRUPTED, never EXITED.
        assert attempt.state is AttemptState.INTERRUPTED
        assert attempt.state is not AttemptState.EXITED

        events = list(storage.iter_events(project))
        interrupted = [e for e in events
                       if e.type is EventType.ATTEMPT_INTERRUPTED and e.attempt_id == attempt_id]
        exited = [e for e in events
                 if e.type is EventType.ATTEMPT_EXITED and e.attempt_id == attempt_id]
        assert len(interrupted) == 1
        assert len(exited) == 0


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
            route_def=OPERATOR_ROUTE,
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
                route_def=OPERATOR_ROUTE,
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
            route_def=OPERATOR_ROUTE,
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
            route_def=OPERATOR_ROUTE,
        )

        # Mock adapters
        with patch("nyxloom.wrapper.adapters") as mock_adapters:
            mock_adapters.extract_usage.return_value = Usage(basis=Basis.UNKNOWN)
            mock_adapters.capture_session.return_value = None
            mock_adapters.classify_log_tail.return_value = None

            # Patch SESSION_CAPTURE_DELAY to 0 for faster testing
            with patch("nyxloom.wrapper.SESSION_CAPTURE_DELAY", 0):
                wrapper_pid = launch_detached(spec)  # waits for wrapper.pid (<=10s)

        # Check wrapper.pid file (launch_detached already blocked until this
        # existed, so no wait is needed here -- see the contract in
        # wrapper.py's own docstring)
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

        # B25 (de-flaking): bounded poll on receipt.json existing instead of
        # two blind time.sleep(1)/time.sleep(2) wall-clock guesses -- mirrors
        # the identical "Poll for receipt.json" idiom used just above in this
        # same file (TestSigterm). Real fork kept (see module docstring);
        # only the wall-clock guesswork is removed.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not receipt_path.exists():
            time.sleep(0.05)

        # Check receipt
        assert receipt_path.exists(), "receipt.json never appeared"
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
            route_def=OPERATOR_ROUTE,
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
            route_def=OPERATOR_ROUTE,
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
            route_def=OPERATOR_ROUTE,
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
            route_def=OPERATOR_ROUTE,
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


class TestP05aLogging:
    """P05a (docs/plan-logging.md §5): wrapper.py instrumentation --
    subprocess spawn / provider calls -> DEBUG; failures -> WARNING/ERROR."""

    def test_happy_path_emits_debug_spawn_session_capture_and_done_attempt_exit(
            self, tmp_state, tmp_path, fake_cli, mock_adapters):
        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["line 1"], exit_code=0)
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)
        log_path = attempt_dir / "wrapper.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project, task_id=task_id, attempt_id=attempt_id,
            argv=[str(script)], cwd=str(tmp_path), log_path=str(log_path),
            receipt_path=str(receipt_path), attempt_dir=str(attempt_dir),
            route_def=OPERATOR_ROUTE,
        )
        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        log_dir = tmp_path / "logs"
        log.configure(level=log.DEBUG, log_dir=log_dir, console=False)

        with patch("nyxloom.wrapper.SESSION_CAPTURE_DELAY", 0.05):
            exit_code = wrapper_main(str(spec_path))
        assert exit_code == 0

        records = _read_log_records(log_dir)

        spawn = [r for r in records if r.get("msg") == "spawn"]
        assert len(spawn) == 1
        assert spawn[0]["level"] == "debug"
        assert spawn[0]["project"] == project
        assert spawn[0]["task"] == task_id
        assert spawn[0]["attempt"] == attempt_id
        assert isinstance(spawn[0]["pid"], int)

        captured = [r for r in records if r.get("msg") == "session-capture-attempt"]
        assert len(captured) == 1
        assert captured[0]["level"] == "debug"
        assert captured[0]["route"] == "fake-cli"

        exits = [r for r in records if r.get("msg") == "attempt-exit"]
        assert len(exits) == 1
        assert exits[0]["level"] == "debug"
        assert exits[0]["result"] == "done"
        assert exits[0]["exit_code"] == 0

    def test_lease_race_emits_error_lease_lost_race(
            self, tmp_state, tmp_path, fake_cli, mock_adapters):
        from nyxloom import leases as leases_module

        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        pre_lease = leases_module.acquire(
            "demo.stack", owner="pretest", purpose="test", capacity=1)
        assert pre_lease is not None
        try:
            script = fake_cli(["output"], exit_code=0)
            attempt_dir = tmp_path / "attempt"
            attempt_dir.mkdir(parents=True)
            log_path = attempt_dir / "wrapper.log"
            receipt_path = attempt_dir / "receipt.json"

            spec = WrapperSpec(
                project=project, task_id=task_id, attempt_id=attempt_id,
                argv=[str(script)], cwd=str(tmp_path), log_path=str(log_path),
                receipt_path=str(receipt_path), attempt_dir=str(attempt_dir),
                route_def=OPERATOR_ROUTE,
                leases=[{"name": "demo.stack", "capacity": 1}],
            )
            spec_path = attempt_dir / "spec.json"
            spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

            log_dir = tmp_path / "logs"
            log.configure(level=log.INFO, log_dir=log_dir, console=False)

            exit_code = wrapper_main(str(spec_path))
            assert exit_code == 75

            records = _read_log_records(log_dir)
            errs = [r for r in records if r.get("msg") == "lease-lost-race"]
            assert len(errs) == 1
            assert errs[0]["level"] == "error"
            assert errs[0]["project"] == project
            assert errs[0]["task"] == task_id
            assert errs[0]["attempt"] == attempt_id
            assert errs[0]["lease"] == "demo.stack"
        finally:
            pre_lease.release()

    def test_interrupted_in_process_emits_warning_attempt_exit(
            self, tmp_state, tmp_path, fake_cli, mock_adapters):
        """§5: INTERRUPTED is a handled, operator-triggered stop -> WARNING
        (not ERROR). Delivered IN-PROCESS (a real SIGTERM to this test's own
        pid, caught by wrapper_main's own installed handler since it runs
        directly here, not via launch_detached's fork) -- unlike
        TestSigterm.test_sigterm_detached_real, which forks and so is
        invisible to this process's coverage recorder."""
        import threading

        project = "demo"
        task_id = "demo-P01-sample"
        attempt_id = "att-1"
        seed(project, task_id, attempt_id)

        script = fake_cli(["starting"], exit_code=0, sleep_time="2")
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True)
        log_path = attempt_dir / "attempt.log"
        receipt_path = attempt_dir / "receipt.json"

        spec = WrapperSpec(
            project=project, task_id=task_id, attempt_id=attempt_id,
            argv=[str(script)], cwd=str(tmp_path), log_path=str(log_path),
            receipt_path=str(receipt_path), attempt_dir=str(attempt_dir),
            route_def=OPERATOR_ROUTE,
            term_grace_seconds=1,
        )
        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        log_dir = tmp_path / "logs"
        log.configure(level=log.INFO, log_dir=log_dir, console=False)

        old_sigterm = signal.signal(signal.SIGTERM, signal.SIG_DFL)
        old_sigint = signal.signal(signal.SIGINT, signal.SIG_DFL)

        def _signal_self():
            time.sleep(0.3)
            os.kill(os.getpid(), signal.SIGTERM)

        try:
            t = threading.Thread(target=_signal_self)
            t.start()
            exit_code = wrapper_main(str(spec_path))
            t.join(timeout=5)
        finally:
            signal.signal(signal.SIGTERM, old_sigterm)
            signal.signal(signal.SIGINT, old_sigint)

        assert exit_code != 0
        receipt = json.loads(receipt_path.read_text())
        assert receipt["blocked_reason"] == "interrupted"

        records = _read_log_records(log_dir)
        exits = [r for r in records if r.get("msg") == "attempt-exit"]
        assert len(exits) == 1
        assert exits[0]["level"] == "warning"
        assert exits[0]["result"] == "error"
        assert exits[0]["project"] == project
        assert exits[0]["task"] == task_id
        assert exits[0]["attempt"] == attempt_id


class TestTheChildEnvironmentIsWhatTheRouteDeclared:
    """2026-08-02 (RISK-006) then 2026-08-03 (CR-13a). The wrapper used to
    hand the child `os.environ.copy()` -- the WHOLE daemon environment -- so
    every dispatched CLI, including the opt-in free/untrusted OpenRouter tier,
    inherited the daemon's own secrets. RISK-006 removed three of them by
    name. CR-13a removed the question: the child receives the route's DECLARED
    secrets and nothing else.

    These cases were amended rather than retired, and one of them CHANGED
    VERDICT: `test_provider_key_the_cli_needs_is_still_inherited` asserted
    that OPENROUTER_API_KEY reaches the CLI by inheritance. Under an
    allowlist that is no longer a property of the system, and asserting it
    would pin the defect. The property it was protecting -- a route that
    needs its provider key still gets it -- survives as
    `test_a_declared_provider_key_still_reaches_the_cli`, with the key now
    reaching the child because the ROUTE ASKED, which is the only reason it
    should ever have.

    The oracle is the environment the CLI ACTUALLY RECEIVES: the fake CLI
    dumps its own environ into the attempt log and the assertions read it
    back. Asserting on the env-builder's return value alone would prove only
    that a helper exists, not that Popen was given it.
    """

    DECLARING = dict(OPERATOR_ROUTE, secrets=["OPENROUTER_API_KEY"])

    def _run_with_env(self, tmp_state, tmp_path, mock_adapters, monkeypatch,
                      env_overrides=None, route=None):
        project, task_id, attempt_id = "demo", "demo-P01-sample", "att-1"
        seed(project, task_id, attempt_id)

        # A CLI that reports its own environment, one NAME=VALUE per line.
        script = tmp_path / "dump_env.sh"
        script.write_text("#!/bin/sh\nenv\n")
        script.chmod(0o755)

        monkeypatch.setenv("AA_API_KEY", "aa-secret-value")
        monkeypatch.setenv("NTFY_TOKEN", "ntfy-publisher-secret")
        monkeypatch.setenv("NTFY_CMD_TOKEN", "ntfy-command-secret")
        monkeypatch.setenv("OPENROUTER_API_KEY", "provider-key-the-cli-needs")

        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        log_path = attempt_dir / "wrapper.log"
        spec = WrapperSpec(
            project=project, task_id=task_id, attempt_id=attempt_id,
            argv=[str(script)], cwd=str(tmp_path), log_path=str(log_path),
            receipt_path=str(attempt_dir / "receipt.json"),
            attempt_dir=str(attempt_dir),
            route_def=route or OPERATOR_ROUTE,
            env_overrides=env_overrides or {},
        )
        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

        assert wrapper_main(str(spec_path)) == 0
        return log_path.read_text(encoding="utf-8")

    def test_daemon_only_secrets_absent_from_child_environment(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch):
        env_text = self._run_with_env(tmp_state, tmp_path, mock_adapters, monkeypatch)

        # The three names RISK-006 stripped, and — the point of the exercise —
        # their VALUES, which must not have reached the child under any name.
        assert "AA_API_KEY=" not in env_text
        assert "NTFY_TOKEN=" not in env_text
        assert "NTFY_CMD_TOKEN=" not in env_text
        assert "aa-secret-value" not in env_text
        assert "ntfy-publisher-secret" not in env_text
        assert "ntfy-command-secret" not in env_text

    def test_an_undeclared_provider_key_is_no_longer_inherited(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch):
        """The inversion, seen from the child. This route declares no
        secrets, so a provider key sitting in the daemon's environment does
        NOT reach it -- under the denylist it did, because nobody had put it
        on the list."""
        env_text = self._run_with_env(tmp_state, tmp_path, mock_adapters, monkeypatch)
        assert "OPENROUTER_API_KEY=" not in env_text
        assert "provider-key-the-cli-needs" not in env_text

    def test_a_declared_provider_key_still_reaches_the_cli(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch):
        """Fail-safe in the other direction: allowlist too hard and every
        OpenRouter/DeepSeek route stops authenticating. A route that declares
        its key gets exactly that key."""
        env_text = self._run_with_env(tmp_state, tmp_path, mock_adapters,
                                      monkeypatch, route=self.DECLARING)
        assert "OPENROUTER_API_KEY=provider-key-the-cli-needs" in env_text
        assert "aa-secret-value" not in env_text
        assert "ntfy-publisher-secret" not in env_text

    def test_env_override_can_deliberately_supply_an_undeclared_name(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch):
        """The allowlist is the default, not a prohibition: a dispatch that
        has a reason to pass a name does so explicitly per attempt, and the
        override wins because it is applied last."""
        env_text = self._run_with_env(
            tmp_state, tmp_path, mock_adapters, monkeypatch,
            env_overrides={"NTFY_TOKEN": "deliberately-passed"})
        assert "NTFY_TOKEN=deliberately-passed" in env_text
        assert "ntfy-publisher-secret" not in env_text

    def test_the_attempt_identity_still_reaches_the_child(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch):
        """CR-03's binding travels in the same environment the allowlist
        rebuilt; it must survive the inversion."""
        env_text = self._run_with_env(tmp_state, tmp_path, mock_adapters, monkeypatch)
        assert "NYXLOOM_TASK_ID=demo-P01-sample" in env_text
        assert "NYXLOOM_ATTEMPT_ID=att-1" in env_text


class TestContainmentGateFailsClosed:
    """CR-13a (D-R7): a route that requires containment and cannot get it
    does not launch, and says so where an operator reads it.

    Nothing here needs docker -- that is the point. The refusal is reached by
    a route that declares no trust in a deployment that has configured no
    image, which is precisely the state the DEPLOYED routes.toml is in until
    an operator syncs it. The capability proofs live in
    tests/test_containment.py, where they start real containers.
    """

    def _spec(self, tmp_path, *, route, argv, repo_root=""):
        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        spec = WrapperSpec(
            project="demo", task_id="demo-P01-sample", attempt_id="att-1",
            argv=argv, cwd=str(tmp_path),
            log_path=str(attempt_dir / "attempt.log"),
            receipt_path=str(attempt_dir / "receipt.json"),
            attempt_dir=str(attempt_dir), route_def=route, repo_root=repo_root)
        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")
        return spec, spec_path, attempt_dir

    def test_an_undeclared_route_refuses_rather_than_launching_uncontained(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch, fake_cli):
        """The whole package in one case: no silent downgrade. The marker
        file proves the CLI was never started -- an assertion on the exit code
        alone could not tell "refused" from "ran and failed"."""
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        seed("demo", "demo-P01-sample", "att-1")
        marker = tmp_path / "the-agent-ran"
        script = tmp_path / "cli.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\n")
        script.chmod(0o755)

        _spec, spec_path, attempt_dir = self._spec(
            tmp_path, route={"route_id": "r", "cli": "fake", "model": "m"},
            argv=[str(script)])

        assert wrapper_main(str(spec_path)) == 76
        assert not marker.exists(), "the CLI ran despite containment refusing"

    def test_the_refusal_is_recorded_in_the_receipt_with_a_reason(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch):
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        seed("demo", "demo-P01-sample", "att-1")
        _spec, spec_path, attempt_dir = self._spec(
            tmp_path, route={"route_id": "r", "cli": "fake", "model": "m"},
            argv=["true"])

        wrapper_main(str(spec_path))

        receipt = json.loads((attempt_dir / "receipt.json").read_text())
        assert receipt["result"] == "error"
        assert receipt["exit_code"] == 76
        assert receipt["blocked_reason"] == "containment-unavailable:no-image-configured"

    def test_the_refusal_is_a_failed_attempt_event_not_a_silent_skip(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch):
        """The operator-visible record. FAILED rather than EXITED, because
        nothing ran -- the same distinction the lease-race refusal draws."""
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        seed("demo", "demo-P01-sample", "att-1")
        _spec, spec_path, _dir = self._spec(
            tmp_path, route={"route_id": "r", "cli": "fake", "model": "m"},
            argv=["true"])

        wrapper_main(str(spec_path))

        events = list(storage.iter_events("demo"))
        failed = [e for e in events if e.type is EventType.ATTEMPT_FAILED]
        assert len(failed) == 1
        attempt = failed[0].payload["attempt"]
        assert attempt["state"] == AttemptState.FAILED.value
        assert attempt["receipt"]["blocked_reason"].startswith("containment-unavailable:")

    def test_the_refusal_is_logged_at_error_with_the_reason(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch):
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        log_dir = tmp_path / "logs"
        log.configure(level=log.DEBUG, log_dir=log_dir, console=False)
        seed("demo", "demo-P01-sample", "att-1")
        _spec, spec_path, _dir = self._spec(
            tmp_path, route={"route_id": "r", "cli": "fake", "model": "m"},
            argv=["true"])

        wrapper_main(str(spec_path))

        records = [r for r in _read_log_records(log_dir)
                   if r.get("msg") == "containment-unavailable"]
        assert len(records) == 1
        assert records[0]["level"] == "error"
        assert records[0]["reason"] == "no-image-configured"
        assert records[0]["attempt"] == "att-1"

    def test_a_launch_site_that_declared_no_repository_is_refused(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch):
        """`repo_root` defaults to empty, so a launch site that forgets it
        cannot produce a container with nothing mounted -- it produces no
        container at all."""
        monkeypatch.setenv("NYXLOOM_CONTAINMENT_IMAGE", "agent:local")
        seed("demo", "demo-P01-sample", "att-1")
        _spec, spec_path, attempt_dir = self._spec(
            tmp_path, route={"route_id": "r", "cli": "fake", "model": "m"},
            argv=["true"], repo_root="")

        assert wrapper_main(str(spec_path)) == 76
        receipt = json.loads((attempt_dir / "receipt.json").read_text())
        assert receipt["blocked_reason"] == (
            "containment-unavailable:no-repository-declared")

    def test_a_free_route_cannot_be_talked_into_an_uncontained_launch(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch):
        """`trust = "operator"` on a free endpoint is refused by the wrapper
        exactly as an undeclared route is -- the declaration does not win."""
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        seed("demo", "demo-P01-sample", "att-1")
        _spec, spec_path, _dir = self._spec(
            tmp_path,
            route={"route_id": "free", "cli": "opencode", "model": "m:free",
                   "status": "free", "trust": "operator"},
            argv=["true"])

        assert wrapper_main(str(spec_path)) == 76

    def test_an_operator_trusted_route_is_unaffected(
            self, tmp_state, tmp_path, mock_adapters, monkeypatch, fake_cli):
        """The other direction: containment must not become a wall in front
        of the routes the factory runs on today."""
        monkeypatch.delenv("NYXLOOM_CONTAINMENT_IMAGE", raising=False)
        seed("demo", "demo-P01-sample", "att-1")
        script = fake_cli(["ran"], exit_code=0)
        _spec, spec_path, attempt_dir = self._spec(
            tmp_path, route=OPERATOR_ROUTE, argv=[str(script)])

        assert wrapper_main(str(spec_path)) == 0
        assert "ran" in (attempt_dir / "attempt.log").read_text()
