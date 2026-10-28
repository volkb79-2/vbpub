"""R1 gap closure for ``assay.mutation_witness`` and ``assay.verify``.

Every test drives one guarded refusal with a document or receipt that differs
from a known-good base in exactly one field, so removing the guard flips the
asserted outcome. No processes, no timing: descriptor/poller/read seams are
scripted fakes.
"""

from __future__ import annotations

import os
import select
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from assay import mutation_witness as mw
from assay import verify as v
from assay.errors import AssayError, Outcome, ReasonCode
from assay.mutation_witness import ReceiptCapture, receipt_facts
from assay.r2_command import R2_APPENDED, R2_TRANSFORM_ID, transform_argv
from assay.runner import CommandPlan

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
NODE = "tests/test_example.py::test_case"


# --------------------------------------------------------------------------
# mutation_witness: receipt capture
# --------------------------------------------------------------------------


def _plan(argv=("pytest", "tests"), env=None) -> CommandPlan:
    return CommandPlan(
        argv_declared=tuple(argv),
        argv_appended=(),
        argv_effective=tuple(argv),
        env_declared={},
        env_effective={} if env is None else env,
        env_passthrough=(),
        allow_argv_append=False,
        budget_seconds=60,
        project_prefix=None,
    )


def _receipt(**overrides):
    value = {
        "unsupported": False,
        "unsupported_pytest_cov_only": False,
        "replay_supported": False,
        "target_node_id": None,
        "target_count": None,
        "earlier_failure": False,
        "auxiliary_failure": False,
        "witness_node_id": NODE,
        "witness_when": "call",
        "witness_outcome": "failed",
        "session_exit_status": 1,
        "stopped_at_target": False,
        "cold_requested": False,
        "stopped_cold": False,
        "collection_error": False,
        "manifest_supported": True,
        "collection_count": 1,
        "collection_sha256": SHA_A,
        "collection_duplicates": 0,
        "started_count": 1,
        "started_prefix_ok": True,
        "failed_call_index": None,
        "hook_fingerprint_sha256": SHA_B,
        "hook_count": 10,
        "runtime_fingerprint_sha256": SHA_C,
        "config_sha256": None,
        "archive_hook_exception_used": False,
        "unsupported_hook_names": [],
    }
    value.update(overrides)
    return value


def _closing_then_failing(monkeypatch, tracked):
    """``os.close`` that really closes tracked descriptors, then raises OSError."""
    original = os.close

    def close(fd):
        original(fd)
        if fd in tracked:
            raise OSError("injected close failure")

    monkeypatch.setattr(os, "close", close)


def _track_descriptors(monkeypatch):
    tracked: list[int] = []
    pipe, dup = os.pipe, os.dup

    def record_pipe():
        pair = pipe()
        tracked.extend(pair)
        return pair

    def record_dup(fd):
        duplicate = dup(fd)
        tracked.append(duplicate)
        return duplicate

    monkeypatch.setattr(os, "pipe", record_pipe)
    monkeypatch.setattr(os, "dup", record_dup)
    return tracked


def test_pipe_creation_interrupt_is_not_wrapped(monkeypatch):
    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "pipe", interrupted)
    with pytest.raises(KeyboardInterrupt):
        ReceiptCapture()


def test_reader_start_interrupt_propagates_after_close_failures_are_swallowed(monkeypatch):
    tracked = _track_descriptors(monkeypatch)

    def interrupted(_thread):
        raise KeyboardInterrupt

    monkeypatch.setattr(threading.Thread, "start", interrupted)
    _closing_then_failing(monkeypatch, tracked)
    with pytest.raises(KeyboardInterrupt):
        ReceiptCapture()
    assert len(tracked) == 3
    monkeypatch.undo()
    for descriptor in tracked:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_setup_failure_before_the_lock_exists_survives_close_errors(monkeypatch):
    tracked = _track_descriptors(monkeypatch)

    def fail_blocking(_fd, _blocking):
        raise OSError("injected")

    monkeypatch.setattr(os, "set_blocking", fail_blocking)
    _closing_then_failing(monkeypatch, tracked)
    with pytest.raises(AssayError, match="receipt reader") as raised:
        ReceiptCapture()
    assert raised.value.outcome is Outcome.ERROR
    assert raised.value.reason_code is ReasonCode.EXEC_FAILED
    assert len(tracked) == 3
    monkeypatch.undo()
    for descriptor in tracked:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def _bare_capture(fd: int) -> ReceiptCapture:
    capture = object.__new__(ReceiptCapture)
    capture._drain_fd = fd
    capture._read_fd = fd
    capture._drain_fd_lock = threading.Lock()
    capture._drain_fd_closed = False
    capture._stop = threading.Event()
    capture._data = bytearray()
    capture._overflow = False
    capture._read_failed = False
    capture._finished = False
    return capture


def _script_reads(monkeypatch, fd, script):
    original = os.read
    items = list(script)

    def fake(descriptor, size):
        if descriptor != fd:
            return original(descriptor, size)
        item = items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(os, "read", fake)
    return items


class _Poller:
    """A poller whose first poll reports ``fd`` ready and whose second stops the loop."""

    def __init__(self, capture, fd, *, error=None, register_error=None):
        self._capture, self._fd = capture, fd
        self._error, self._register_error = error, register_error
        self._calls = 0

    def register(self, _fd, _mask):
        if self._register_error is not None:
            raise self._register_error

    def poll(self, _timeout):
        if self._error is not None:
            raise self._error
        self._calls += 1
        if self._calls == 1:
            return [(self._fd, select.POLLIN)]
        self._capture._stop.set()
        return []


def _run_drain(monkeypatch, *, reads=(), poll_error=None, register_error=None):
    fd = os.open(os.devnull, os.O_RDONLY)
    capture = _bare_capture(fd)
    monkeypatch.setattr(
        mw.select,
        "poll",
        lambda: _Poller(
            capture, fd, error=poll_error, register_error=register_error
        ),
    )
    _script_reads(monkeypatch, fd, reads)
    capture._drain()
    assert capture._drain_fd_closed is True
    with pytest.raises(OSError):
        os.fstat(fd)
    return capture


def test_drain_poll_failure_marks_the_read_failed(monkeypatch):
    capture = _run_drain(monkeypatch, poll_error=OSError("poll"))
    assert capture._read_failed is True
    assert bytes(capture._data) == b""


def test_drain_register_failure_marks_the_read_failed(monkeypatch):
    capture = _run_drain(monkeypatch, register_error=OSError("register"))
    assert capture._read_failed is True


def test_drain_retries_interrupts_and_stops_at_would_block(monkeypatch):
    capture = _run_drain(
        monkeypatch, reads=[InterruptedError(), b"ab", BlockingIOError()]
    )
    assert bytes(capture._data) == b"ab"
    assert capture._read_failed is False
    assert capture._overflow is False


def test_drain_read_error_marks_the_read_failed(monkeypatch):
    capture = _run_drain(monkeypatch, reads=[OSError("read")])
    assert capture._read_failed is True


def test_drain_flags_overflow_and_stops_at_eof(monkeypatch):
    monkeypatch.setattr(mw, "MAX_INTERNAL_RECEIPT_BYTES", 2)
    capture = _run_drain(monkeypatch, reads=[b"abcd", b"efgh", b""])
    assert capture._overflow is True
    assert bytes(capture._data) == b"abcd"


def test_final_drain_fails_closed_while_a_writer_remains():
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    capture = _bare_capture(read_fd)
    try:
        capture._drain_final_bytes()
        assert capture._read_failed is True
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize(
    ("reads", "data", "failed", "overflow"),
    [
        ([InterruptedError(), b"xy", b""], b"xy", False, False),
        ([OSError("boom")], b"", True, False),
        ([b"abcdefg"], b"", False, True),
    ],
)
def test_final_drain_outcomes(monkeypatch, reads, data, failed, overflow):
    monkeypatch.setattr(mw, "MAX_INTERNAL_RECEIPT_BYTES", 2)
    capture = _bare_capture(10**6)
    _script_reads(monkeypatch, 10**6, reads)
    capture._drain_final_bytes()
    assert bytes(capture._data) == data
    assert capture._read_failed is failed
    assert capture._overflow is overflow


def test_finish_is_single_shot_and_swallows_descriptor_close_errors():
    capture = _bare_capture(-1)
    capture._finished = True
    assert capture.finish() is None

    capture = _bare_capture(-1)
    capture.write_fd = -1
    capture._thread = SimpleNamespace(join=lambda _t: None, is_alive=lambda: False)
    # write_fd, the final drain and the read_fd close all hit EBADF.
    assert capture.finish() is None
    assert capture._read_failed is True
    assert capture._finished is True


# --------------------------------------------------------------------------
# mutation_witness: receipt facts and proofs
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        _receipt(manifest_supported=False),
        _receipt(started_count=2),
        _receipt(collection_sha256="zz"),
        _receipt(collection_error="no"),
    ],
)
def test_receipt_facts_rejects_malformed_receipts(receipt):
    assert receipt_facts(receipt) is None
    assert receipt_facts(_receipt()) is not None


def _survivor(**overrides):
    base = {
        "session_exit_status": 0,
        "witness_node_id": None,
        "witness_when": None,
        "witness_outcome": None,
    }
    base.update(overrides)
    return _receipt(**base)


def _failure(receipt, *, exit_status=0, command="declared", expected="default"):
    if expected == "default":
        expected = receipt_facts(_survivor())
    return mw.survivor_proof_failure(
        receipt, process_exit_status=exit_status, expected=expected, command=command
    )


@pytest.mark.parametrize(
    ("receipt", "kwargs", "message"),
    [
        (None, {}, "missing, malformed, or has an unexpected schema"),
        (_survivor(), {"command": "other"}, "missing, malformed, or has an unexpected schema"),
        (_survivor(), {"exit_status": 1}, "baseline process did not exit successfully"),
        (_survivor(session_exit_status=1), {}, "pytest session did not exit successfully"),
        (_survivor(auxiliary_failure=True), {}, "auxiliary or earlier failure"),
        (_survivor(stopped_at_target=True), {}, "stopped before completing"),
        (_survivor(stopped_cold=True), {}, "stopped before completing"),
        (_survivor(unsupported="no"), {}, "support flags are missing or malformed"),
        (
            _survivor(unsupported=True, unsupported_pytest_cov_only=False, collection_count=-1),
            {"command": "r2"},
            "no-coverage R2 baseline uses an unsupported pytest hook configuration; receipt facts",
        ),
        (_survivor(collection_count=-1), {}, "collection manifest or its fingerprints are missing"),
        (_survivor(started_count=0), {}, "did not start every collected test"),
        (_survivor(), {"expected": None}, "expected collection facts could not be parsed"),
        (_survivor(collection_duplicates=1), {}, "duplicate node IDs"),
        (_survivor(hook_fingerprint_sha256=SHA_A), {}, "hook fingerprint differs"),
        (_survivor(runtime_fingerprint_sha256=SHA_A), {}, "runtime fingerprint differs"),
    ],
)
def test_survivor_proof_failure_names_each_refusal(receipt, kwargs, message):
    assert message in _failure(receipt, **kwargs)
    assert _failure(_survivor()) is None


def test_survivor_proof_failure_collection_error_is_unreachable_past_the_flag_guard():
    # collection_error is already refused earlier, so the later facts check
    # reports the earlier, flag-level reason.
    assert "collection failed" in _failure(_survivor(collection_error=True))


def _cold(**overrides):
    base = {"cold_requested": True, "stopped_cold": True, "failed_call_index": 0}
    base.update(overrides)
    return _receipt(**base)


@pytest.mark.parametrize(
    "overrides",
    [
        {"cold_requested": False},
        {"unsupported": True},
        {"earlier_failure": True},
        {"witness_when": "setup"},
        {"witness_outcome": "passed"},
        {"session_exit_status": 0},
        {"witness_node_id": ""},
        {"witness_node_id": "n" * 5000},
        {"witness_node_id": 7},
        {"hook_fingerprint_sha256": SHA_A},
        {"collection_count": -1},
        {"started_count": 0, "failed_call_index": -1},
        {"failed_call_index": None},
        {"failed_call_index": 3},
    ],
)
def test_cold_witness_refuses_each_inconsistent_receipt(overrides):
    expected = receipt_facts(_receipt())
    good = mw.cold_witness_from_receipt(_cold(), process_exit_status=1, expected=expected)
    assert good is not None and good[1:] == (1, 0)
    assert (
        mw.cold_witness_from_receipt(
            _cold(**overrides), process_exit_status=1, expected=expected
        )
        is None
    )
    assert mw.cold_witness_from_receipt(_cold(), process_exit_status=0, expected=expected) is None
    assert mw.cold_witness_from_receipt(None, process_exit_status=1, expected=expected) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"session_exit_status": 0},
        {"witness_when": "setup"},
        {"witness_outcome": "passed"},
        {"unsupported": "no"},
        {"unsupported_pytest_cov_only": True},
        {"unsupported": True},
        {"witness_node_id": ""},
        {"earlier_failure": True},
        {"started_prefix_ok": False},
        {"collection_count": -1},
        {"hook_fingerprint_sha256": SHA_A},
        {"started_count": 0},
    ],
)
def test_declared_failure_proof_refuses_each_inconsistent_receipt(overrides):
    expected = receipt_facts(_receipt())
    manifest = [NODE]

    def proof(receipt, status=1, ids=manifest):
        return mw.declared_failure_proof_ok(
            receipt, process_exit_status=status, expected=expected, manifest_node_ids=ids
        )

    assert proof(_receipt()) is True
    assert proof(_receipt(**overrides)) is False
    assert proof(_receipt(), status=0) is False
    assert proof(None) is False
    assert proof(_receipt(), ids=[]) is False
    assert proof(_receipt(), ids=["tests/other.py::t"]) is False


# --------------------------------------------------------------------------
# mutation_witness: command parsing
# --------------------------------------------------------------------------


def test_unclassified_short_letter_is_treated_as_parallel():
    assert mw._contains_xdist_option(("-Z",)) is True
    assert mw.supports_sequential_pytest(("pytest", "-Z")) is False


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ((), None),
        (("python", "-m"), None),
        (("python", "-m", "unittest", "-x"), None),
        (("python", "-W"), None),
        (("python", "-Wignore", "-m", "pytest", "-x"), "-x"),
        (("python", "-Z", "-m", "pytest", "-x"), None),
        (("python", "-I"), None),
        (("pytest", "--maxfail"), None),
        (("pytest", "--maxfail=abc"), None),
        (("pytest", "--maxfail", "abc"), None),
        (("pytest", "--exitfirst=1"), None),
        (("pytest", "-Z", "-x"), None),
        (("pytest", "-kexpr", "-x"), "-x"),
    ],
)
def test_pytest_fail_fast_option_edge_shapes(argv, expected):
    assert mw.pytest_fail_fast_option(argv) == expected


@pytest.mark.parametrize(
    ("argv", "outcome"),
    [
        (("--cov", "src"), (None, ())),
        (("--cov", "-q"), (None, ())),
        (("--config-file=a.ini",), (Path("a.ini"), ())),
        (("-kfoo", "tests"), (None, ("tests",))),
        (("-c", "a.ini"), (Path("a.ini"), ())),
        (("--config-file",), None),
        (("--config-file", "-q"), None),
        (("--config-file=",), None),
        (("--config-file=a.ini", "--config-file=b.ini"), None),
        (("-c", "a.ini", "-c", "b.ini"), None),
        (("-Z",), None),
        (("-k",), None),
    ],
)
def test_selector_and_config_parser(argv, outcome):
    assert mw._pytest_selectors_and_config(argv) == outcome


def test_config_selection_fails_closed_when_paths_cannot_be_inspected(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    assert mw._pytest_configs_allow_sequential(tmp_path, ("::node",)) is False

    original_is_dir = Path.is_dir

    def broken_is_dir(self):
        if self.name == "tests":
            raise OSError("injected")
        return original_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", broken_is_dir)
    assert mw._pytest_configs_allow_sequential(tmp_path, ("tests",)) is False
    monkeypatch.undo()

    def broken_resolve(self, *args, **kwargs):
        raise OSError("injected")

    monkeypatch.setattr(Path, "resolve", broken_resolve)
    assert mw._pytest_configs_allow_sequential(tmp_path, ("tests",)) is False


@pytest.mark.parametrize(
    ("name", "text", "allowed"),
    [
        ("pytest.toml", 'pytest = "x"\n', False),
        ("pytest.toml", "[pytest]\naddopts = 3\n", False),
        ("pytest.toml", '[pytest]\naddopts = ["-q"]\n', True),
        ("pyproject.toml", 'tool = {pytest = "x"}\n', False),
        ("pyproject.toml", "[tool.pytest]\nini_options = 3\n", False),
        ("pyproject.toml", "[tool.pytest]\naddopts = 3\n", False),
        ("pyproject.toml", '[tool.pytest]\naddopts = ["-q"]\n', True),
        ("pyproject.toml", '[tool.pytest]\naddopts = ["-n4"]\n', False),
    ],
)
def test_toml_addopts_shapes(tmp_path, name, text, allowed):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    assert mw._config_addopts_allow_sequential(path) is allowed


# --------------------------------------------------------------------------
# mutation_witness: attempt plans, cold shape, receipt decoding
# --------------------------------------------------------------------------


def test_attempt_plan_argument_validation(tmp_path):
    plan = _plan()
    with pytest.raises(ValueError, match="cannot target one node"):
        mw.make_attempt_plan(plan, receipt_fd=3, target_node_id=NODE, cold=True)
    with pytest.raises(ValueError, match="exactly one receipt"):
        mw.make_attempt_plan(plan, target_node_id=None)
    with pytest.raises(ValueError, match="exactly one receipt"):
        mw.make_attempt_plan(
            plan, receipt_path=tmp_path / "r", receipt_fd=3, target_node_id=None
        )
    with pytest.raises(ValueError, match="non-negative integer"):
        mw.make_attempt_plan(plan, receipt_fd=-1, target_node_id=None)
    ok = mw.make_attempt_plan(plan, receipt_fd=3, target_node_id=None)
    assert ok.env_effective[mw.WITNESS_FD_ENV] == "3"


@pytest.mark.parametrize(
    ("argv", "env", "reason"),
    [
        ((), {}, "not a sequential pytest command"),
        (("make",), {}, "not a sequential pytest command"),
        (("python", "-m", "pytest", "-o", "x=1"), {}, "pytest override"),
        (("pytest", "tests"), {"PYTEST_PLUGINS": ""}, "pytest environment option"),
        (("pytest", "-n4"), {}, "not a sequential pytest command"),
        (("pytest", "-h"), {}, "not a sequential pytest command"),
        (("pytest", "--distx"), {}, "parallel option"),
        (("pytest", "-d"), {}, "parallel option"),
        (("pytest", "--lf"), {}, "order-changing option"),
        (("pytest", "-p", "randomly"), {}, "order-changing option"),
        (("pytest", "-p", "pytest_cov"), {}, "coverage plugin re-enabled"),
        (("pytest", "-p", "xdist"), {}, "parallel option"),
    ],
)
def test_cold_shape_refusal_remaining_reasons(argv, env, reason):
    assert mw.cold_shape_refusal(argv, env) == reason


def test_cold_shape_refusal_stops_scanning_at_the_double_dash_separator():
    assert mw.cold_shape_refusal(("pytest", "--", "tests"), {}) is None


@pytest.mark.parametrize("raw", [b"", b"x" * (mw.MAX_INTERNAL_RECEIPT_BYTES + 1)])
def test_decode_internal_receipt_rejects_empty_and_oversized(raw):
    assert mw._decode_internal_receipt(raw) is None


# --------------------------------------------------------------------------
# verify: lane-resolved env digests, R2 re-derivation without an R0 sibling
# --------------------------------------------------------------------------


def test_lane_resolved_group_requires_an_object_of_passthrough_digests():
    document = {
        "declared_rigor": "R2",
        "declared_evidence": "e",
        "argv_declared": ["pytest"],
        "argv_appended": [],
        "argv_effective": ["pytest"],
        "env_declared": {},
        "env_effective": {},
        "env_passthrough": [],
        "env_effective_passthrough_sha256": {},
        "scope": "s",
        "enforcement": "x",
        "argv_modified": False,
    }
    failures: list[str] = []
    v._check_lane_resolved_group(document, failures)
    assert failures == []
    failures = []
    v._check_lane_resolved_group(
        {**document, "env_effective_passthrough_sha256": "nope"}, failures
    )
    assert failures == ["env_effective_passthrough_sha256 must be an object"]


def test_r2_rederivation_without_an_r0_sibling_has_nothing_to_compare():
    claim = SimpleNamespace(
        rigor="R2", reason_code=ReasonCode.COMMAND_FAILED, mutation=None, status=Outcome.FAIL
    )
    failures: list[str] = []
    v._check_r2_rederivation(SimpleNamespace(claims=[claim], judgment=None), failures)
    assert failures == []


# --------------------------------------------------------------------------
# verify: B106 execution modes
# --------------------------------------------------------------------------


def _witness(**overrides):
    value = {
        "node_id": NODE,
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    value.update(overrides)
    return value


def _execution(bucket, execution, **kwargs):
    failures: list[str] = []
    v._check_b106_execution(bucket, execution, failures, **kwargs)
    return failures


def _prefix(**overrides):
    value = {
        "mode": "witness-prefix",
        "witness": _witness(),
        "prior_verdict_sha256": SHA_A,
        "prior_node_id": NODE,
        "current_node_id": NODE,
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("bucket", "execution", "kwargs", "message"),
    [
        ("killed", None, {}, "execution must be an object"),
        ("killed", {"mode": "full", "extra": 1}, {}, "carries witness-prefix fields"),
        ("killed", {"mode": "full"}, {"cold_policy": True}, "requires a failed-call witness"),
        ("survived", {"mode": "full", "witness": _witness()}, {}, "carries a kill witness"),
        ("killed", {"mode": "full", "witness": _witness(when="setup")}, {}, "failed call-phase"),
        ("killed", {"mode": "witness-cold", "witness": _witness(), "x": 1}, {"cold_policy": True}, "exactly its current witness receipt"),
        ("survived", {"mode": "witness-cold", "witness": _witness()}, {"cold_policy": True}, "only a killed outcome may use witness-cold"),
        ("killed", {"mode": "witness-cold", "witness": _witness()}, {}, "requires cold_witness_kills true"),
        ("killed", {"mode": "ledger", "anchor": "a", "x": 1}, {"ledger_declared": True}, "exactly its anchor"),
        ("killed", {"mode": "ledger", "anchor": "a"}, {"ledger_declared": True}, "only an equivalent outcome"),
        ("equivalent", {"mode": "ledger", "anchor": "a"}, {}, "requires a declared equivalence ledger"),
        ("equivalent", {"mode": "ledger", "anchor": 5}, {"ledger_declared": True}, "non-empty UTF-8"),
        ("equivalent", {"mode": "ledger", "anchor": ""}, {"ledger_declared": True}, "non-empty UTF-8"),
        ("equivalent", {"mode": "ledger", "anchor": "\ud800"}, {"ledger_declared": True}, "non-empty UTF-8"),
        ("equivalent", {"mode": "ledger", "anchor": "a" * 4097}, {"ledger_declared": True}, "non-empty UTF-8"),
        ("killed", {"mode": "bogus"}, {}, "unknown execution mode"),
        ("killed", _prefix(extra=1), {}, "exactly its prior digest"),
        ("survived", _prefix(), {}, "only a killed outcome may use witness-prefix"),
        ("killed", _prefix(prior_verdict_sha256="z"), {}, "prior verdict digest is malformed"),
        ("killed", _prefix(prior_node_id=""), {}, "prior node ID is malformed"),
        ("killed", _prefix(current_node_id=5), {}, "current node ID is malformed"),
        ("killed", _prefix(prior_node_id="other::t"), {}, "node IDs must be identical"),
    ],
)
def test_b106_execution_refusals(bucket, execution, kwargs, message):
    assert any(message in item for item in _execution(bucket, execution, **kwargs))


@pytest.mark.parametrize(
    ("bucket", "execution", "kwargs"),
    [
        ("killed", {"mode": "full", "witness": _witness()}, {}),
        ("killed", {"mode": "witness-cold", "witness": _witness()}, {"cold_policy": True}),
        ("equivalent", {"mode": "ledger", "anchor": "a"}, {"ledger_declared": True}),
        ("killed", _prefix(), {}),
    ],
)
def test_b106_execution_accepts_the_matching_shape(bucket, execution, kwargs):
    assert _execution(bucket, execution, **kwargs) == []


# --------------------------------------------------------------------------
# verify: v15 evidence
# --------------------------------------------------------------------------


def _baseline_facts(**extra):
    return {
        "collection_count": 3,
        "collection_sha256": SHA_A,
        "duplicates": 0,
        "hook_fingerprint_sha256": SHA_B,
        "hook_count": 1,
        "runtime_fingerprint_sha256": SHA_C,
        **extra,
    }


def _evidence(**overrides):
    value = {
        "command": "r2",
        "collection_count": 3,
        "collection_sha256": SHA_A,
        "hook_fingerprint_sha256": SHA_B,
        "started_count": 2,
        "failed_call_index": 1,
    }
    value.update(overrides)
    return value


def _policy(**overrides):
    value = {
        "cold_witness_kills": True,
        "r2_command": {
            "coverage_baseline": _baseline_facts(),
            "r2_baseline": _baseline_facts(wall_s=1.0),
        },
    }
    value.update(overrides)
    return value


def _cold_evidence_failures(bucket, entry, policy=None):
    failures: list[str] = []
    v._check_v15_evidence(bucket, entry, _policy() if policy is None else policy, failures)
    return failures


def _no_started(**overrides):
    return _evidence(started_count=None, failed_call_index=None, **overrides)


@pytest.mark.parametrize(
    ("bucket", "entry", "policy", "message"),
    [
        ("survived", {}, None, "cold-witness survivor requires collection evidence"),
        ("killed", {}, None, "cold-witness kill requires collection evidence"),
        ("killed", {"evidence": []}, None, "evidence must be an object"),
        ("killed", {"evidence": {"command": "r2"}, "execution": {"mode": "witness-cold"}}, None, "exactly its six v15 fields"),
        ("killed", {"evidence": _evidence(command="x"), "execution": {"mode": "witness-cold"}}, None, "must be 'r2' or 'declared'"),
        ("killed", {"evidence": _evidence(collection_count=-1), "execution": {"mode": "witness-cold"}}, None, "collection_count must be an integer"),
        ("killed", {"evidence": _evidence(collection_sha256="z"), "execution": {"mode": "witness-cold"}}, None, "collection_sha256 must be a SHA-256"),
        ("killed", {"evidence": _evidence(failed_call_index=None), "execution": {"mode": "witness-cold"}}, None, "must appear together"),
        ("killed", {"evidence": _evidence(started_count=0, failed_call_index=-1), "execution": {"mode": "witness-cold"}}, None, "started_count must be an integer >= 1"),
        ("killed", {"evidence": _evidence(failed_call_index="1"), "execution": {"mode": "witness-cold"}}, None, "failed_call_index must be an integer"),
        ("killed", {"evidence": _evidence(command="declared"), "execution": {"mode": "witness-cold"}}, None, "only R2 evidence may record"),
        ("killed", {"evidence": _evidence(failed_call_index=0), "execution": {"mode": "witness-cold"}}, None, "must end the started prefix"),
        ("killed", {"evidence": _evidence(started_count=4, failed_call_index=3), "execution": {"mode": "witness-cold"}}, None, "exceeds collection_count"),
        ("killed", {"evidence": _evidence(), "execution": {"mode": "witness-cold"}}, {"cold_witness_kills": False}, "cold-disabled native outcomes"),
        ("killed", {"evidence": _no_started(), "execution": {"mode": "witness-cold"}}, None, "witness-cold kill requires started-prefix"),
        ("killed", {"evidence": _evidence(), "execution": {"mode": "witness-prefix"}}, None, "witness-prefix kill cannot carry"),
        ("killed", {"evidence": _evidence(command="declared"), "execution": {"mode": "full"}}, None, "full kill evidence cannot carry"),
        ("killed", {"evidence": _evidence(), "execution": {"mode": "ledger"}}, None, "unsupported execution mode"),
        ("killed", {"evidence": _no_started(command="declared"), "execution": {"mode": "witness-prefix"}}, None, "must use the 'r2' command"),
        ("survived", {"evidence": _evidence(), "execution": {"mode": "witness-cold"}}, None, "only a killed outcome may carry witness-cold"),
        ("survived", {"evidence": _no_started(), "execution": {"mode": "witness-cold"}}, None, "witness-cold kill requires started-prefix"),
        ("survived", {"evidence": _evidence(), "execution": {"mode": "full"}}, None, "survivor evidence cannot carry"),
        ("survived", {"evidence": _no_started(command="x"), "execution": {"mode": "full"}}, None, "survivor evidence command must match"),
        ("crashed", {"evidence": _evidence(), "execution": {"mode": "full"}}, None, "non-cold outcome evidence cannot carry"),
        ("killed", {"evidence": _no_started(), "execution": {"mode": "witness-prefix"}}, {"cold_witness_kills": True, "r2_command": None}, "no r2_baseline to match"),
        ("killed", {"evidence": _no_started(collection_count=9), "execution": {"mode": "witness-prefix"}}, None, "collection_count differs from r2_baseline"),
    ],
)
def test_v15_evidence_refusals(bucket, entry, policy, message):
    assert any(message in item for item in _cold_evidence_failures(bucket, entry, policy))


@pytest.mark.parametrize(
    ("bucket", "entry"),
    [
        ("killed", {"evidence": _evidence(), "execution": {"mode": "witness-cold"}}),
        ("killed", {"evidence": _no_started(), "execution": {"mode": "witness-prefix"}}),
        ("killed", {"evidence": _no_started(command="declared"), "execution": {"mode": "full"}}),
        ("survived", {"evidence": _no_started(command="declared"), "execution": {"mode": "full"}}),
        ("crashed", {"evidence": _no_started(), "execution": {"mode": "full"}}),
        ("crashed", {}),
    ],
)
def test_v15_evidence_accepts_matching_shapes(bucket, entry):
    assert _cold_evidence_failures(bucket, entry) == []


# --------------------------------------------------------------------------
# verify: v15 R2 command / ledger
# --------------------------------------------------------------------------


def _r2_command():
    declared = ["pytest", "tests"]
    return {
        "transform": R2_TRANSFORM_ID,
        "argv_declared": declared,
        "argv_transformed": list(transform_argv(declared)),
        "appended": list(R2_APPENDED),
        "cwd": ".",
        "config_sha256": None,
        "coverage_baseline": _baseline_facts(),
        "r2_baseline": _baseline_facts(wall_s=1.0),
    }


def _r2_document(*, payload=True, mutation=None, **policy_overrides):
    policy = {
        "producer": "native",
        "cold_witness_kills": True,
        "r2_command": _r2_command(),
        "equivalence_ledger": None,
    }
    policy.update(policy_overrides)
    claim = {"rigor": "R2"}
    if payload:
        claim["mutation"] = {} if mutation is None else mutation
    return {
        "claims": [claim],
        "judgment": {"r2": policy},
        "argv_declared": ["pytest", "tests"],
    }


def _r2_failures(document):
    failures: list[str] = []
    v._check_v15_r2_command(document, failures)
    return failures


_LEDGER = {"path": "a/ledger.json", "sha256": SHA_A, "entry_count": 1, "audit_sha256": SHA_B}


def _drop_key(key):
    def modify(document):
        del document["judgment"]["r2"]["r2_command"][key]

    return modify


def _set_command(key, value):
    def modify(document):
        document["judgment"]["r2"]["r2_command"][key] = value

    return modify


def _set_baseline(name, key, value):
    def modify(document):
        document["judgment"]["r2"]["r2_command"][name][key] = value

    return modify


def test_r2_command_accepts_the_known_good_document():
    assert _r2_failures(_r2_document()) == []
    assert _r2_failures({"claims": [{"rigor": "R0"}]}) == []
    assert _r2_failures(_r2_document(producer="other")) == []


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (
            _r2_document(producer="ingested", r2_command=None),
            "ingested judgment.r2 forbids v15 native field(s)",
        ),
        (
            _r2_document(cold_witness_kills=False),
            "r2_command requires cold_witness_kills true",
        ),
        (
            _r2_document(r2_command=None),
            "cold-witness R2 payload requires r2_command",
        ),
        (
            _r2_document(payload=False),
            "payload-free R2 claim cannot carry r2_command",
        ),
        (
            _r2_document(r2_command="x"),
            "r2_command must be an object or null",
        ),
        (
            _r2_document(equivalence_ledger="x"),
            "equivalence_ledger must be an object or null",
        ),
        (
            _r2_document(equivalence_ledger={"path": "a"}),
            "equivalence_ledger has missing or unknown fields",
        ),
        (
            _r2_document(equivalence_ledger={**_LEDGER, "entry_count": 0}),
            "equivalence_ledger entry_count must be an integer >= 1",
        ),
        (
            _r2_document(
                equivalence_ledger=_LEDGER,
                mutation={"killed": [{"execution": {"mode": "ledger", "anchor": "a"}}]},
            ),
            "ledger execution is legal only in the equivalent bucket",
        ),
        (
            _r2_document(
                mutation={"equivalent": [{"execution": {"mode": "ledger", "anchor": "a"}}]},
            ),
            "ledger execution requires a declared equivalence_ledger",
        ),
        (
            _r2_document(equivalence_ledger=_LEDGER, mutation={"equivalent": [{}]}),
            "every equivalence-ledger outcome must use ledger execution",
        ),
        (
            _r2_document(
                equivalence_ledger=_LEDGER,
                mutation={
                    "equivalent": [
                        {"execution": {"mode": "ledger", "anchor": "a"}, "evidence": {}}
                    ]
                },
            ),
            "ledger outcomes cannot carry candidate or resource evidence",
        ),
        (
            _r2_document(
                payload=False,
                cold_witness_kills=False,
                r2_command=None,
                equivalence_ledger=_LEDGER,
            ),
            "equivalence_ledger requires an R2 mutation payload",
        ),
    ],
)
def test_r2_command_policy_refusals(document, message):
    assert any(message in item for item in _r2_failures(document))


@pytest.mark.parametrize(
    ("modify", "message"),
    [
        (_drop_key("cwd"), "r2_command has missing or unknown fields"),
        (_set_command("argv_declared", [1]), "r2_command.argv_declared must be a list of strings"),
        (_set_baseline("r2_baseline", "runtime_fingerprint_sha256", "z"), "r2_baseline.runtime_fingerprint_sha256 must be a SHA-256"),
        (_set_baseline("coverage_baseline", "collection_sha256", SHA_C), "R2 and coverage baseline collection_sha256 values differ"),
        (_set_baseline("coverage_baseline", "wall_s", 1.0), "coverage_baseline must omit wall_s"),
        (_set_baseline("r2_baseline", "wall_s", -1), "r2_baseline.wall_s must be finite"),
        (_set_command("cwd", "../x"), "cwd must be normalized"),
        (_set_command("config_sha256", "z"), "config_sha256 must be a SHA-256 digest or null"),
        (_set_command("appended", ["-x"]), "appended argv is not the pinned"),
        (_set_command("argv_transformed", ["x"]), "argv_transformed differs"),
        (_set_command("coverage_baseline", 7), "coverage_baseline must be an object"),
    ],
)
def test_r2_command_field_refusals(modify, message):
    document = _r2_document()
    modify(document)
    assert any(message in item for item in _r2_failures(document))


# --------------------------------------------------------------------------
# verify: B145 resource limit evidence, B162 process tree evidence
# --------------------------------------------------------------------------


def _counter(before=0, after=0, delta=0):
    return {"before": before, "after": after, "delta": delta}


def _resources(**overrides):
    value = {
        "cgroup_version": 2,
        "pids_events": {"max": _counter()},
        "memory_events": {
            "max": _counter(),
            "oom": _counter(),
            "oom_kill": _counter(),
            "oom_group_kill": _counter(),
        },
    }
    value.update(overrides)
    return value


def _resource_failures(bucket, entry):
    failures: list[str] = []
    v._check_b145_resource_limit_evidence(bucket, entry, failures)
    return failures


def _memory(**counters):
    base = _resources()["memory_events"]
    base.update(counters)
    return base


@pytest.mark.parametrize(
    ("bucket", "entry", "message"),
    [
        ("equivalent", {"execution": {"mode": "ledger"}, "resource_limit_evidence": {}}, "ledger execution cannot carry"),
        ("killed", {}, "requires resource_limit_evidence"),
        ("killed", {"resource_limit_evidence": {"cgroup_version": 2}}, "invalid resource_limit_evidence fields"),
        ("killed", {"resource_limit_evidence": _resources(cgroup_version=1)}, "requires cgroup v2"),
        ("killed", {"resource_limit_evidence": _resources(pids_events=[])}, "invalid pids_events evidence"),
        ("killed", {"resource_limit_evidence": _resources(memory_events={"max": _counter()})}, "invalid memory_events evidence"),
        ("killed", {"resource_limit_evidence": _resources(pids_events={"max": 5})}, "invalid pids_events.max counter evidence"),
        ("killed", {"resource_limit_evidence": _resources(pids_events={"max": _counter(-1, 0, 1)})}, "invalid non-negative integer"),
        ("killed", {"resource_limit_evidence": _resources(pids_events={"max": _counter(2, 1, 0)})}, "inconsistent pids_events.max counter arithmetic"),
        ("killed", {"resource_limit_evidence": _resources(pids_events={"max": _counter(0, 3, 1)})}, "inconsistent pids_events.max counter arithmetic"),
        ("killed", {"resource_limit_evidence": _resources(pids_events={"max": _counter(0, 1, 1)})}, "positive failed-fork or OOM"),
        ("survived", {"resource_limit_evidence": _resources(memory_events=_memory(oom_kill=_counter(0, 1, 1)))}, "positive failed-fork or OOM"),
    ],
)
def test_b145_refusals(bucket, entry, message):
    assert any(message in item for item in _resource_failures(bucket, entry))


@pytest.mark.parametrize(
    ("bucket", "entry"),
    [
        ("killed", {"resource_limit_evidence": _resources()}),
        ("killed", {"resource_limit_evidence": _resources(memory_events=_memory(max=_counter(0, 4, 4)))}),
        ("crashed", {"resource_limit_evidence": _resources(pids_events={"max": _counter(0, 1, 1)})}),
        ("equivalent", {"execution": {"mode": "ledger"}}),
    ],
)
def test_b145_accepts_consistent_counters(bucket, entry):
    assert _resource_failures(bucket, entry) == []


def _tree(**overrides):
    value = {
        "protocol": "assay-process-tree/1",
        "leader_status": "exited",
        "group_signalled_before_reap": False,
        "outliving_descendants": 0,
        "descendants_signalled": 0,
        "cleanup_seconds": 0.1,
        "clean": True,
    }
    value.update(overrides)
    return value


def _tree_failures(bucket, entry):
    failures: list[str] = []
    v._check_b162_process_tree_evidence(bucket, entry, failures)
    return failures


@pytest.mark.parametrize(
    ("bucket", "entry", "message"),
    [
        ("equivalent", {"execution": {"mode": "ledger"}, "process_tree_evidence": {}}, "ledger execution cannot carry process_tree_evidence"),
        ("killed", {}, "requires process_tree_evidence"),
        ("killed", {"process_tree_evidence": {"clean": True}}, "invalid process_tree_evidence fields"),
        ("killed", {"process_tree_evidence": _tree(protocol="x")}, "unsupported process-tree protocol"),
        ("killed", {"process_tree_evidence": _tree(leader_status="x")}, "invalid process-tree leader status"),
        ("killed", {"process_tree_evidence": _tree(group_signalled_before_reap=1)}, "invalid process-tree group-signal evidence"),
        ("killed", {"process_tree_evidence": _tree(outliving_descendants=-1)}, "invalid process-tree outliving_descendants"),
        ("killed", {"process_tree_evidence": _tree(descendants_signalled=1)}, "more signalled descendants than outliving"),
        ("killed", {"process_tree_evidence": _tree(cleanup_seconds=True)}, "invalid process-tree cleanup_seconds"),
        ("killed", {"process_tree_evidence": _tree(cleanup_seconds=float("nan"))}, "invalid process-tree cleanup_seconds"),
        ("killed", {"process_tree_evidence": _tree(clean=False)}, "does not prove a clean process tree"),
        ("killed", {"process_tree_evidence": _tree(leader_status="signaled")}, "functional outcome without a clean leader exit"),
        ("killed", {"process_tree_evidence": _tree(outliving_descendants=1, descendants_signalled=1)}, "functional outcome without a clean leader exit"),
        ("hung", {"process_tree_evidence": _tree(outliving_descendants=1, descendants_signalled=1)}, "outliving descendants without a timeout classification"),
        ("hung", {"process_tree_evidence": _tree()}, "native mutation.hung requires a timeout-killed"),
        ("budget_exceeded", {"process_tree_evidence": _tree()}, "budget_exceeded requires a timed-out or unstarted leader"),
    ],
)
def test_b162_refusals(bucket, entry, message):
    assert any(message in item for item in _tree_failures(bucket, entry))


@pytest.mark.parametrize(
    ("bucket", "entry"),
    [
        ("killed", {"process_tree_evidence": _tree()}),
        ("equivalent", {"execution": {"mode": "ledger"}}),
        ("hung", {"process_tree_evidence": _tree(leader_status="timeout-killed")}),
        ("budget_exceeded", {"process_tree_evidence": _tree(leader_status="not-started")}),
        ("crashed", {"process_tree_evidence": _tree(leader_status="signaled", outliving_descendants=2, descendants_signalled=2)}),
    ],
)
def test_b162_accepts_clean_or_classified_trees(bucket, entry):
    assert _tree_failures(bucket, entry) == []


def test_fixture_bases_are_not_mutated_by_parametrization():
    # Guards the shared module-level parametrize objects against in-place edits.
    assert deepcopy(_r2_document()) == _r2_document()
