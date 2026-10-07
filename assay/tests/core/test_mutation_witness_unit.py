"""B105 source coverage for B106's bounded pytest evidence boundaries."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from _pytest.config import get_config

from assay import liveness, mutation_witness
from assay.errors import AssayError, Outcome, ReasonCode
from assay.mutation_witness import (
    _PLUGIN_SOURCE,
    COLD_PYTEST_FLAG_OPTIONS,
    COLD_PYTEST_VALUE_OPTIONS,
    HOOK_FINGERPRINT_HOOKS,
    MAX_INTERNAL_RECEIPT_BYTES,
    WITNESS_COLD_ENV,
    WITNESS_FD_ENV,
    WITNESS_LIVENESS_PLUGIN_PATH_ENV,
    WITNESS_MANIFEST_FILE_ENV,
    WITNESS_PLUGIN_PATH_ENV,
    WITNESS_TARGET_ENV,
    ReceiptCapture,
    cold_shape_refusal,
    cold_witness_from_receipt,
    declared_failure_proof_ok,
    inject_witness_plugin,
    make_attempt_plan,
    read_internal_receipt,
    receipt_facts,
    replay_witness_from_receipt,
    supports_sequential_pytest,
    survivor_proof_ok,
    witness_from_receipt,
)
from assay.runner import CommandPlan


def _plan(argv: tuple[str, ...], *, env: dict[str, str] | None = None) -> CommandPlan:
    return CommandPlan(
        argv_declared=argv,
        argv_appended=(),
        argv_effective=argv,
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
        "witness_node_id": "tests/test_example.py::test_case",
        "witness_when": "call",
        "witness_outcome": "failed",
        "session_exit_status": 1,
        "stopped_at_target": False,
        "cold_requested": False,
        "stopped_cold": False,
        "collection_error": False,
        "manifest_supported": True,
        "collection_count": 1,
        "collection_sha256": "a" * 64,
        "collection_duplicates": 0,
        "started_count": 1,
        "started_prefix_ok": True,
        "failed_call_index": None,
        "hook_fingerprint_sha256": "b" * 64,
        "hook_count": 10,
        "runtime_fingerprint_sha256": "c" * 64,
        "config_sha256": None,
        "archive_hook_exception_used": False,
    }
    value.update(overrides)
    return value


def _run_child_pytest(
    project: Path,
    plugin_dir: Path,
    receipt_path: Path,
    *,
    pytest_args: tuple[str, ...] = (),
    env_overrides: dict[str, str] | None = None,
    cold: bool = False,
    liveness_plugin_path: Path | None = None,
):
    env = os.environ.copy()
    env.pop("PYTEST_PLUGINS", None)
    env.pop("PYTEST_ADDOPTS", None)
    # These subprocesses pin their own hook shapes; ambient entry-point plugins
    # from the cockpit would make the expected builtin-only baseline vary.
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("INSTALL_FORGER", None)
    env.pop("FAIL_CALL", None)
    if env_overrides:
        env.update(env_overrides)
    liveness_args = (
        ("-p", "assay_liveness_plugin")
        if liveness_plugin_path is not None
        else ()
    )
    plan = _plan(
        (sys.executable, "-m", "pytest", "-q", *liveness_args, *pytest_args, "tests"),
        env=env,
    )
    injected = inject_witness_plugin(
        plan,
        plugin_dir=plugin_dir,
        cwd=project,
        liveness_plugin_path=liveness_plugin_path,
    )
    assert injected.active
    capture = ReceiptCapture()
    attempt = make_attempt_plan(
        injected.plan,
        receipt_fd=capture.write_fd,
        target_node_id=None,
        cold=cold,
        manifest_path=receipt_path.parent / "manifest.txt" if cold else None,
    )
    try:
        result = subprocess.run(
            attempt.argv_effective,
            cwd=project,
            env=attempt.env_effective,
            check=False,
            capture_output=True,
            text=True,
            pass_fds=(capture.write_fd,),
        )
    finally:
        receipt = capture.finish()
    return result, receipt


def test_sequential_pytest_refuses_empty_override_and_untrusted_plugin_inputs():
    argv = (sys.executable, "-m", "pytest", "tests")
    assert not supports_sequential_pytest((), env={})
    assert not supports_sequential_pytest((*argv, "-o", "addopts=-n 2"), env={})
    assert not supports_sequential_pytest((*argv, "--override-ini=addopts=-n 2"), env={})
    assert not supports_sequential_pytest((*argv, "-n", "2"), env={})
    assert not supports_sequential_pytest(argv, env={"PYTEST_PLUGINS": "external"})
    assert not supports_sequential_pytest(argv, env={"PYTEST_ADDOPTS": "'unterminated"})
    assert not supports_sequential_pytest(argv, env={"PYTEST_ADDOPTS": "-n 2"})


def test_cold_short_option_classes_match_installed_pytest_and_xdist_parser():
    config = get_config()
    config.pluginmanager.import_plugin("xdist.plugin")
    flags: set[str] = set()
    values: set[str] = set()
    for action in config._parser.optparser._actions:
        for option in action.option_strings:
            if len(option) != 2 or not option.startswith("-"):
                continue
            if action.nargs == 0:
                flags.add(option[1])
            else:
                values.add(option[1])

    assert flags == COLD_PYTEST_FLAG_OPTIONS
    assert values == COLD_PYTEST_VALUE_OPTIONS


@pytest.mark.parametrize(
    ("name", "contents", "expected"),
    [
        ("pytest.ini", "[pytest]\naddopts = -q\n", True),
        (".pytest.ini", "[pytest]\naddopts = --dist=load\n", False),
        ("tox.ini", "[pytest]\naddopts = --numprocesses 2\n", False),
        ("setup.cfg", "[tool:pytest]\naddopts = -q\n", True),
        (
            "pyproject.toml",
            '[tool.pytest.ini_options]\naddopts = "-q --maxfail=1"\n',
            True,
        ),
        (
            "pyproject.toml",
            '[tool.pytest.ini_options]\naddopts = ["-q", "--maxfail=1"]\n',
            True,
        ),
        (
            "pyproject.toml",
            '[tool.pytest.ini_options]\naddopts = ["-q", "-n 2"]\n',
            False,
        ),
        (
            "pyproject.toml",
            '[tool.pytest.ini_options]\naddopts = ["-q", "--maxfail=1"]\n',
            True,
        ),
        ("pytest.ini", "[pytest]\n[pytest]\n", False),
        ("pyproject.toml", "[tool.pytest.ini_options\n", False),
    ],
)
def test_pytest_config_files_are_read_fail_closed(tmp_path, name, contents, expected):
    config = tmp_path / name
    config.write_text(contents, encoding="utf-8")
    assert supports_sequential_pytest(
        (sys.executable, "-m", "pytest", "tests"), cwd=tmp_path, env={}
    ) is expected


@pytest.mark.parametrize(
    "contents",
    [
        '[tool.pytest.ini_options]\naddopts = 9\n',
        '[tool.pytest.ini_options]\naddopts = ["-q", 9]\n',
    ],
)
def test_malformed_pyproject_addopts_are_not_assumed_sequential(tmp_path, contents):
    config = tmp_path / "pyproject.toml"
    config.write_text(contents, encoding="utf-8")
    assert not supports_sequential_pytest(
        (sys.executable, "-m", "pytest", "tests"),
        cwd=tmp_path,
        env={},
    )


def test_explicit_pytest_config_paths_and_missing_arguments(tmp_path):
    config = tmp_path / "custom.ini"
    config.write_text("[pytest]\naddopts = -n 2\n", encoding="utf-8")
    argv = (sys.executable, "-m", "pytest")
    assert not supports_sequential_pytest((*argv, "-c"), cwd=tmp_path, env={})
    assert not supports_sequential_pytest(
        (*argv, "--config-file", "custom.ini"), cwd=tmp_path, env={}
    )
    assert not supports_sequential_pytest(
        (*argv, f"--config-file={config}"), cwd=tmp_path, env={}
    )
    assert not supports_sequential_pytest(
        (*argv, "--config-file=missing.ini"), cwd=tmp_path, env={}
    )


def test_plugin_injection_writes_once_and_preserves_declared_cli_and_environment(
    tmp_path, monkeypatch
):
    plugin_dir = tmp_path / "plugins"
    plan = _plan(
        (sys.executable, "-m", "pytest", "tests"),
        env={
            "PYTHONPATH": str(plugin_dir),
            WITNESS_LIVENESS_PLUGIN_PATH_ENV: "stale",
        },
    )
    injected = inject_witness_plugin(
        plan, plugin_dir=plugin_dir, liveness_plugin_path=tmp_path / "liveness.py"
    )
    plugin_path = plugin_dir / "assay_mutation_witness_plugin.py"
    assert injected.active
    assert injected.plan.argv_effective[-2:] == ("-p", "assay_mutation_witness_plugin")
    assert injected.plan.env_effective[WITNESS_PLUGIN_PATH_ENV] == str(plugin_path)
    assert injected.plan.env_effective[WITNESS_LIVENESS_PLUGIN_PATH_ENV] == str(
        tmp_path / "liveness.py"
    )
    assert injected.plan.env_effective["PYTHONPATH"] == str(plugin_dir)

    def no_rewrite(*args, **kwargs):
        raise AssertionError("identical plugin source should not be rewritten")

    monkeypatch.setattr(Path, "write_text", no_rewrite)
    unchanged = inject_witness_plugin(plan, plugin_dir=plugin_dir)
    assert unchanged.active
    assert WITNESS_LIVENESS_PLUGIN_PATH_ENV not in unchanged.plan.env_effective


def test_plugin_injection_refuses_wrappers_and_prepends_a_new_pythonpath(tmp_path):
    unsupported = inject_witness_plugin(
        _plan(("sh", "-c", "pytest tests")), plugin_dir=tmp_path / "plugins"
    )
    assert not unsupported.active
    assert unsupported.reason == "unsupported-pytest-command"

    plan = _plan(("pytest", "tests"), env={"PYTHONPATH": "existing"})
    injected = inject_witness_plugin(plan, plugin_dir=tmp_path / "plugins")
    assert injected.plan.env_effective["PYTHONPATH"].split(":") == [
        str(tmp_path / "plugins"),
        "existing",
    ]


def test_liveness_hook_pin_rejects_same_fingerprint_callable_substitution(
    tmp_path, monkeypatch
):
    plugin_dir = tmp_path / "plugins"
    plugin_path = liveness.materialize_liveness_plugin(plugin_dir)
    spec = importlib.util.spec_from_file_location("assay_liveness_plugin", plugin_path)
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    implementations = {
        name: SimpleNamespace(
            plugin_name="assay_liveness_plugin",
            function=getattr(plugin, name),
        )
        for name in (
            "pytest_configure",
            "pytest_runtest_logreport",
            "pytest_sessionfinish",
            "pytest_unconfigure",
        )
    }

    class Hook:
        def __init__(self, impl):
            self.impl = impl

        def get_hookimpls(self):
            return [self.impl]

    manager = SimpleNamespace(
        get_plugin=lambda name: plugin if name == "assay_liveness_plugin" else None,
        get_name=lambda value: "assay_liveness_plugin" if value is plugin else None,
    )
    config = SimpleNamespace(
        pluginmanager=manager,
        hook=SimpleNamespace(
            **{name: Hook(impl) for name, impl in implementations.items()}
        ),
    )
    monkeypatch.setenv("ASSAY_MUTATION_WITNESS_LIVENESS_PLUGIN_PATH", str(plugin_path))
    injected = mutation_witness.inject_witness_plugin(
        _plan((sys.executable, "-m", "pytest", "tests")),
        plugin_dir=plugin_dir,
        liveness_plugin_path=plugin_path,
    )
    assert injected.active
    witness_path = plugin_dir / "assay_mutation_witness_plugin.py"
    witness_spec = importlib.util.spec_from_file_location(
        "assay_mutation_witness_plugin", witness_path
    )
    assert witness_spec is not None and witness_spec.loader is not None
    witness = importlib.util.module_from_spec(witness_spec)
    witness_spec.loader.exec_module(witness)

    assert witness._pin_liveness_hook_callables(config)
    assert witness._liveness_hook_registry_matches(config)
    original_impl = implementations["pytest_runtest_logreport"]
    original = original_impl.function
    substitute = types.FunctionType(
        original.__code__, original.__globals__, original.__name__
    )
    substitute.__module__ = original.__module__
    substitute.__qualname__ = original.__qualname__
    original_impl.function = substitute

    assert not witness._is_pinned_liveness_hook(
        "pytest_runtest_logreport", original_impl
    )
    assert not witness._liveness_hook_registry_matches(config)


def test_attempt_plan_adds_or_clears_target_node_id(tmp_path):
    plan = _plan(("pytest", "tests"), env={WITNESS_TARGET_ENV: "old"})
    targeted = make_attempt_plan(
        plan, receipt_path=tmp_path / "receipt.json", target_node_id="test::target"
    )
    assert targeted.env_effective[WITNESS_TARGET_ENV] == "test::target"
    assert targeted.env_effective["ASSAY_MUTATION_WITNESS_FILE"] == str(
        tmp_path / "receipt.json"
    )
    untargeted = make_attempt_plan(
        plan, receipt_path=tmp_path / "receipt.json", target_node_id=None
    )
    assert WITNESS_TARGET_ENV not in untargeted.env_effective


def test_attempt_plan_passes_a_pipe_descriptor_without_a_receipt_path():
    plan = _plan(("pytest", "tests"), env={"ASSAY_MUTATION_WITNESS_FILE": "stale"})
    capture = ReceiptCapture()
    try:
        attempt = make_attempt_plan(
            plan,
            receipt_fd=capture.write_fd,
            target_node_id=None,
        )
        assert attempt.env_effective[WITNESS_FD_ENV] == str(capture.write_fd)
        assert "ASSAY_MUTATION_WITNESS_FILE" not in attempt.env_effective
    finally:
        capture.finish()


def test_receipt_capture_rejects_a_second_or_trailing_frame():
    capture = ReceiptCapture()
    frame = (2).to_bytes(4, "big") + b"{}"
    try:
        os.write(capture.write_fd, frame)
        os.write(capture.write_fd, frame)
    finally:
        assert capture.finish() is None


def test_receipt_capture_discards_oversized_frames():
    capture = ReceiptCapture()
    payload = b"x" * (MAX_INTERNAL_RECEIPT_BYTES + 1)
    frame = len(payload).to_bytes(4, "big") + payload
    try:
        view = memoryview(frame)
        while view:
            view = view[os.write(capture.write_fd, view) :]
    finally:
        assert capture.finish() is None


def test_receipt_capture_closes_pipe_if_reader_descriptor_dup_fails(monkeypatch):
    original_pipe = os.pipe
    read_fd, write_fd = original_pipe()
    monkeypatch.setattr(os, "pipe", lambda: (read_fd, write_fd))

    def fail_dup(_fd: int) -> int:
        raise OSError("injected descriptor exhaustion")

    monkeypatch.setattr(os, "dup", fail_dup)
    try:
        with pytest.raises(AssayError, match="receipt reader") as raised:
            ReceiptCapture()
        assert raised.value.outcome is Outcome.ERROR
        assert raised.value.reason_code is ReasonCode.EXEC_FAILED
        assert isinstance(raised.value.__cause__, OSError)
        for descriptor in (read_fd, write_fd):
            with pytest.raises(OSError):
                os.fstat(descriptor)
    finally:
        for descriptor in (read_fd, write_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_receipt_capture_raises_typed_error_if_pipe_creation_fails(monkeypatch):
    def fail_pipe():
        raise OSError("injected pipe descriptor exhaustion")

    monkeypatch.setattr(os, "pipe", fail_pipe)
    with pytest.raises(AssayError, match="receipt reader") as raised:
        ReceiptCapture()
    assert raised.value.outcome is Outcome.ERROR
    assert raised.value.reason_code is ReasonCode.EXEC_FAILED
    assert isinstance(raised.value.__cause__, OSError)


def test_receipt_capture_closes_pipe_if_nonblocking_setup_fails(monkeypatch):
    original_pipe = os.pipe
    original_dup = os.dup
    descriptors: list[int] = []

    def record_pipe() -> tuple[int, int]:
        pair = original_pipe()
        descriptors.extend(pair)
        return pair

    def record_dup(fd: int) -> int:
        duplicate = original_dup(fd)
        descriptors.append(duplicate)
        return duplicate

    def fail_set_blocking(_fd: int, _blocking: bool) -> None:
        raise OSError("injected nonblocking descriptor setup failure")

    monkeypatch.setattr(os, "pipe", record_pipe)
    monkeypatch.setattr(os, "dup", record_dup)
    monkeypatch.setattr(os, "set_blocking", fail_set_blocking)

    with pytest.raises(AssayError, match="receipt reader") as raised:
        ReceiptCapture()

    assert raised.value.outcome is Outcome.ERROR
    assert raised.value.reason_code is ReasonCode.EXEC_FAILED
    assert isinstance(raised.value.__cause__, OSError)
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_receipt_capture_closes_descriptors_and_raises_typed_error_if_reader_thread_cannot_start(
    monkeypatch,
):
    descriptors: list[int] = []
    original_pipe = os.pipe
    original_dup = os.dup

    def record_pipe() -> tuple[int, int]:
        pair = original_pipe()
        descriptors.extend(pair)
        return pair

    def record_dup(fd: int) -> int:
        duplicate = original_dup(fd)
        descriptors.append(duplicate)
        return duplicate

    def fail_start(_thread):
        raise RuntimeError("injected process-limit thread-start failure")

    monkeypatch.setattr(os, "pipe", record_pipe)
    monkeypatch.setattr(os, "dup", record_dup)
    monkeypatch.setattr(threading.Thread, "start", fail_start)

    with pytest.raises(AssayError, match="receipt reader") as raised:
        ReceiptCapture()

    assert raised.value.outcome is Outcome.ERROR
    assert raised.value.reason_code is ReasonCode.EXEC_FAILED
    assert isinstance(raised.value.__cause__, RuntimeError)
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_started_receipt_reader_retains_drain_descriptor_ownership_on_start_error(
    monkeypatch,
):
    original_dup = os.dup
    original_start = threading.Thread.start
    original_close = os.close
    drain_descriptors: list[int] = []
    drain_close_calls: list[int] = []

    def record_dup(fd: int) -> int:
        duplicate = original_dup(fd)
        drain_descriptors.append(duplicate)
        return duplicate

    def start_then_fail(thread: threading.Thread) -> None:
        original_start(thread)
        if thread.name == "assay-witness-receipt":
            raise RuntimeError("injected post-start failure")

    def record_close(fd: int) -> None:
        if fd in drain_descriptors:
            drain_close_calls.append(fd)
        original_close(fd)

    monkeypatch.setattr(os, "dup", record_dup)
    monkeypatch.setattr(os, "close", record_close)
    monkeypatch.setattr(threading.Thread, "start", start_then_fail)

    with pytest.raises(AssayError, match="receipt reader") as raised:
        ReceiptCapture()

    assert raised.value.reason_code is ReasonCode.EXEC_FAILED
    assert len(drain_descriptors) == 1
    assert drain_close_calls == drain_descriptors
    with pytest.raises(OSError):
        os.fstat(drain_descriptors[0])


def test_receipt_capture_stalled_reader_cannot_consume_a_later_capture(monkeypatch):
    read_entered = threading.Event()
    release_read = threading.Event()
    later_read_entered = threading.Event()
    release_later_read = threading.Event()
    capture = ReceiptCapture()
    stale_reader_fd = capture._drain_fd
    later_reader_fd: int | None = None
    original_read = os.read

    def delayed_read(fd: int, size: int) -> bytes:
        if fd == stale_reader_fd and not read_entered.is_set():
            read_entered.set()
            assert release_read.wait(2.0)
        if fd == later_reader_fd:
            later_read_entered.set()
            assert release_later_read.wait(2.0)
        return original_read(fd, size)

    monkeypatch.setattr(os, "read", delayed_read)
    normal_join_timeout = ReceiptCapture._JOIN_TIMEOUT_SECONDS
    monkeypatch.setattr(ReceiptCapture, "_JOIN_TIMEOUT_SECONDS", 0.01)
    later_capture: ReceiptCapture | None = None
    try:
        os.write(capture.write_fd, b"stale")
        assert read_entered.wait(1.0)
        assert capture.finish() is None
        assert capture._thread.is_alive()

        later_capture = ReceiptCapture()
        assert later_capture._read_fd == capture._read_fd
        assert later_capture._read_fd != stale_reader_fd
        later_reader_fd = later_capture._drain_fd
        expected = {"ok": True}
        payload = json.dumps(expected).encode()
        os.write(
            later_capture.write_fd,
            len(payload).to_bytes(4, "big") + payload,
        )
        assert later_read_entered.wait(1.0)

        release_read.set()
        capture._thread.join(1.0)
        assert not capture._thread.is_alive()
        release_later_read.set()
        monkeypatch.setattr(
            ReceiptCapture, "_JOIN_TIMEOUT_SECONDS", normal_join_timeout
        )
        assert later_capture.finish() == expected
    finally:
        release_read.set()
        release_later_read.set()
        if not capture._finished:
            capture.finish()
        capture._thread.join(1.0)
        if later_capture is not None and not later_capture._finished:
            later_capture.finish()


def test_attempt_plan_sets_cold_and_manifest_only_for_that_attempt(tmp_path):
    plan = _plan(("pytest", "tests"), env={WITNESS_COLD_ENV: "stale", WITNESS_MANIFEST_FILE_ENV: "stale"})
    cold = make_attempt_plan(
        plan,
        receipt_path=tmp_path / "receipt.json",
        target_node_id=None,
        cold=True,
        manifest_path=tmp_path / "manifest.txt",
    )
    assert cold.env_effective[WITNESS_COLD_ENV] == "1"
    assert cold.env_effective[WITNESS_MANIFEST_FILE_ENV] == str(tmp_path / "manifest.txt")
    full = make_attempt_plan(plan, receipt_path=tmp_path / "full.json", target_node_id=None)
    assert WITNESS_COLD_ENV not in full.env_effective
    assert WITNESS_MANIFEST_FILE_ENV not in full.env_effective


@pytest.mark.parametrize(
    ("argv", "appended", "env", "reason"),
    [
        (("pytest", "tests", "--cov"), (), {}, "unrecognized coverage option"),
        (("pytest", "tests", "-qoaddopts=-n4"), (), {}, "pytest override"),
        (("pytest", "tests", "-cfoo.ini"), (), {}, "pytest configuration override"),
        (("pytest", "tests", "-qx"), (), {}, "fail-fast option"),
        (("pytest", "tests", "-pxdist"), (), {}, "parallel option"),
        (("pytest", "tests", "-prandomly"), (), {}, "order-changing option"),
        (("pytest", "tests", "-ppytest_cov"), (), {}, "coverage plugin re-enabled"),
        (("pytest", "tests"), ("-pxdist",), {}, "parallel option"),
        (("pytest", "tests"), (), {"PYTEST_ADDOPTS": "-x"}, "pytest environment option"),
        (("pytest", "tests"), (), {"COVERAGE_PROCESS_START": ".coveragerc"}, "coverage re-enabled by environment"),
    ],
)
def test_cold_shape_refusal_rejects_unprovable_pytest_shapes(argv, appended, env, reason):
    assert reason in (cold_shape_refusal(argv, env, appended=appended) or "")


@pytest.mark.parametrize(
    "argv",
    [
        ("pytest", "tests", "-qkexpr"),
        ("pytest", "tests", "-qk", "expr"),
        ("pytest", "tests", "-pno:randomly"),
    ],
)
def test_cold_shape_refusal_accepts_selection_and_disabled_randomizer(argv):
    assert cold_shape_refusal(argv, {}) is None


def test_plugin_source_and_hook_fingerprint_contract_are_current():
    compile(_PLUGIN_SOURCE, "assay_mutation_witness_plugin.py", "exec")
    expected_hooks = (
        "pytest_runtestloop", "pytest_runtest_protocol", "pytest_runtest_logstart",
        "pytest_runtest_logreport", "pytest_runtest_call", "pytest_runtest_makereport",
        "pytest_runtest_setup",
        "pytest_runtest_teardown", "pytest_collectreport",
        "pytest_collection_modifyitems", "pytest_sessionfinish",
    )
    namespace = {}
    exec(compile(_PLUGIN_SOURCE, "assay_mutation_witness_plugin.py", "exec"), namespace)
    assert HOOK_FINGERPRINT_HOOKS == expected_hooks
    assert namespace["_HOOKS"] == expected_hooks


def test_cold_and_survivor_proofs_require_complete_matching_receipts():
    expected = receipt_facts(_receipt())
    assert expected is not None
    survivor = _receipt(
        session_exit_status=0,
        witness_node_id=None,
        witness_when=None,
        witness_outcome=None,
    )
    assert survivor_proof_ok(survivor, process_exit_status=0, expected=expected, command="r2")
    assert survivor_proof_ok(
        {**survivor, "unsupported": True, "unsupported_pytest_cov_only": True},
        process_exit_status=0,
        expected=expected,
        command="declared",
    )
    assert not survivor_proof_ok(
        {**survivor, "unsupported": True},
        process_exit_status=0,
        expected=expected,
        command="declared",
    )
    assert not survivor_proof_ok(
        {**survivor, "unsupported_pytest_cov_only": True},
        process_exit_status=0,
        expected=expected,
        command="declared",
    )
    assert not survivor_proof_ok(
        {**survivor, "unsupported": True},
        process_exit_status=0,
        expected=expected,
        command="r2",
    )
    assert not survivor_proof_ok(
        {**survivor, "started_prefix_ok": False},
        process_exit_status=0,
        expected=expected,
        command="declared",
    )
    assert not survivor_proof_ok(
        _receipt(session_exit_status=0),
        process_exit_status=0,
        expected=expected,
        command="declared",
    )
    cold = _receipt(
        cold_requested=True,
        stopped_cold=True,
        failed_call_index=0,
    )
    proof = cold_witness_from_receipt(cold, process_exit_status=1, expected=expected)
    assert proof is not None
    assert proof[1:] == (1, 0)
    assert proof[0]["node_id"] == "tests/test_example.py::test_case"
    assert cold_witness_from_receipt(
        {**cold, "collection_error": True}, process_exit_status=1, expected=expected
    ) is None
    assert cold_witness_from_receipt(
        {**cold, "session_exit_status": True}, process_exit_status=1, expected=expected
    ) is None


def test_makereport_wrapper_changes_fingerprint_and_cannot_prove_a_cold_kill(tmp_path):
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (tests / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    plugin_dir = tmp_path / "plugins"
    baseline_result, baseline = _run_child_pytest(
        project,
        plugin_dir,
        tmp_path / "baseline.json",
        env_overrides={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        cold=True,
    )
    assert baseline_result.returncode == 0, baseline_result.stderr
    assert baseline is not None and baseline["unsupported"] is False
    baseline_facts = receipt_facts(baseline)
    assert baseline_facts is not None

    (tests / "conftest.py").write_text(
        "import pytest\n\n"
        "@pytest.hookimpl(hookwrapper=True, tryfirst=True)\n"
        "def pytest_runtest_makereport(item, call):\n"
        "    outcome = yield\n"
        "    report = outcome.get_result()\n"
        "    if report.when == 'call' and report.outcome == 'passed':\n"
        "        report.outcome = 'failed'\n"
        "        report.longrepr = 'forged failure for cold-witness test'\n",
        encoding="utf-8",
    )
    forged_result, forged = _run_child_pytest(
        project, plugin_dir, tmp_path / "forged.json", cold=True
    )
    assert forged_result.returncode == 1
    assert forged is not None
    assert forged["unsupported"] is True
    assert forged["unsupported_pytest_cov_only"] is False
    assert forged["hook_fingerprint_sha256"] != baseline["hook_fingerprint_sha256"]
    assert cold_witness_from_receipt(
        forged, process_exit_status=forged_result.returncode, expected=baseline_facts
    ) is None


@pytest.mark.parametrize(
    ("fail_call", "forged_outcome", "cold", "exit_status"),
    [
        (False, "failed", True, 1),
        (True, "passed", False, 0),
    ],
)
def test_hook_registered_during_call_cannot_forge_a_result(
    tmp_path, fail_call, forged_outcome, cold, exit_status
):
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (tests / "test_dynamic.py").write_text(
        "import pytest\n\n"
        "import os\n\n"
        "class ReportForger:\n"
        "    @pytest.hookimpl(hookwrapper=True, tryfirst=True)\n"
        "    def pytest_runtest_makereport(self, item, call):\n"
        "        outcome = yield\n"
        "        report = outcome.get_result()\n"
        "        if report.when == 'call':\n"
        f"            report.outcome = {forged_outcome!r}\n"
        "        item.config.pluginmanager.unregister(self)\n\n"
        "def test_behavior(pytestconfig):\n"
        "    if os.environ.get('INSTALL_FORGER'):\n"
        "        pytestconfig.pluginmanager.register(ReportForger(), 'dynamic-report-forger')\n"
        "    if os.environ.get('FAIL_CALL'):\n"
        "        assert False\n"
        "    assert True\n",
        encoding="utf-8",
    )
    plugin_dir = tmp_path / "plugins"
    baseline_result, baseline = _run_child_pytest(
        project, plugin_dir, tmp_path / "baseline.json"
    )
    assert baseline_result.returncode == 0, baseline_result.stderr
    assert baseline is not None and baseline["unsupported"] is False
    expected = receipt_facts(baseline)
    assert expected is not None

    result, receipt = _run_child_pytest(
        project,
        plugin_dir,
        tmp_path / "dynamic.json",
        env_overrides={
            **{"INSTALL_FORGER": "1"},
            **({"FAIL_CALL": "1"} if fail_call else {}),
        },
        cold=cold,
    )

    assert result.returncode == exit_status
    assert receipt is not None
    assert receipt["unsupported"] is True
    assert receipt["unsupported_pytest_cov_only"] is False
    if cold:
        assert cold_witness_from_receipt(
            receipt, process_exit_status=result.returncode, expected=expected
        ) is None
    else:
        assert not survivor_proof_ok(
            receipt,
            process_exit_status=result.returncode,
            expected=expected,
            command="declared",
        )


def test_archive_sessionfinish_status_change_cannot_prove_a_survivor(tmp_path, monkeypatch):
    for name in (
        "ASSAY_B105_COVERAGE_SOURCE",
        "ASSAY_B105_COVERAGE_ARCHIVE_DIR",
        "ASSAY_B105_SOURCE_COMMIT",
        "ASSAY_B105_SOURCE_TREE",
    ):
        monkeypatch.delenv(name, raising=False)
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (tests / "test_failure.py").write_text(
        "def test_failure():\n    assert False\n", encoding="utf-8"
    )
    (tests / "conftest.py").write_text(
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    session.exitstatus = 0\n",
        encoding="utf-8",
    )
    result, receipt = _run_child_pytest(
        project, tmp_path / "plugins", tmp_path / "receipt.json"
    )

    assert result.returncode == 0
    assert receipt is not None
    assert receipt["archive_hook_exception_used"] is True
    assert receipt["unsupported"] is False
    assert receipt["session_exit_status"] == 1
    assert receipt["witness_node_id"] == "tests/test_failure.py::test_failure"
    facts = receipt_facts(receipt)
    assert facts is not None
    assert not survivor_proof_ok(
        receipt, process_exit_status=result.returncode, expected=facts, command="declared"
    )
    assert not declared_failure_proof_ok(
        receipt,
        process_exit_status=result.returncode,
        expected=facts,
        manifest_node_ids=("tests/test_failure.py::test_failure",),
    )


def test_external_hook_allowlist_pins_distribution_version_and_source_digest():
    from types import FunctionType, MethodType, SimpleNamespace

    import _hypothesis_pytestplugin
    from pytest_cov.plugin import CovPlugin

    namespace = {}
    exec(compile(_PLUGIN_SOURCE, "assay_mutation_witness_plugin.py", "exec"), namespace)
    cases = (
        (
            "hypothesis",
            "pytest_runtest_call",
            _hypothesis_pytestplugin.pytest_runtest_call,
            "hypothesispytest",
            {"hookwrapper": True},
        ),
        (
            "pytest-cov",
            "pytest_runtestloop",
            CovPlugin.pytest_runtestloop,
            "_cov",
            {"wrapper": True},
        ),
        (
            "pytest-cov",
            "pytest_runtest_call",
            CovPlugin.pytest_runtest_call,
            "_cov",
            {"hookwrapper": True},
        ),
        (
            "hypothesis",
            "pytest_runtest_makereport",
            _hypothesis_pytestplugin.pytest_runtest_makereport,
            "hypothesispytest",
            {"hookwrapper": True},
        ),
        (
            "hypothesis",
            "pytest_collection_modifyitems",
            _hypothesis_pytestplugin.pytest_collection_modifyitems,
            "hypothesispytest",
            {},
        ),
    )
    for distribution, hook_name, function, plugin_name, flags in cases:
        impl = SimpleNamespace(
            function=function,
            plugin_name=plugin_name,
            hookwrapper=flags.get("hookwrapper", False),
            wrapper=flags.get("wrapper", False),
            tryfirst=False,
            trylast=False,
        )
        version, source_sha256 = namespace["_REVIEWED_EXTERNAL_DISTRIBUTIONS"][distribution]
        installed = namespace["metadata"].distribution(distribution)
        module = namespace["sys"].modules.get(function.__module__)
        module_file = getattr(module, "__file__", None)
        installed_source_sha256 = (
            namespace["hashlib"].sha256(Path(module_file).read_bytes()).hexdigest()
            if isinstance(module_file, str)
            else None
        )
        reviewed_here = (
            installed.version == version and installed_source_sha256 == source_sha256
        )
        assert namespace["_reviewed_external_hook"](
            impl, hook_name, distribution
        ) is reviewed_here

        if distribution == "pytest-cov" and reviewed_here:
            bound_impl = SimpleNamespace(
                function=MethodType(function, object()),
                plugin_name=plugin_name,
                hookwrapper=flags.get("hookwrapper", False),
                wrapper=flags.get("wrapper", False),
                tryfirst=False,
                trylast=False,
            )
            assert namespace["_reviewed_external_hook"](
                bound_impl, hook_name, distribution
            )

        def substituted(*_args, **_kwargs):
            return None

        fake = FunctionType(
            substituted.__code__.replace(
                co_filename=function.__code__.co_filename
            ),
            function.__globals__,
            function.__name__,
        )
        fake.__module__ = function.__module__
        fake.__qualname__ = function.__qualname__
        fake_impl = SimpleNamespace(
            function=fake,
            plugin_name=plugin_name,
            hookwrapper=flags.get("hookwrapper", False),
            wrapper=flags.get("wrapper", False),
            tryfirst=False,
            trylast=False,
        )
        assert fake.__module__ == function.__module__
        assert fake.__qualname__ == function.__qualname__
        assert fake.__code__.co_filename == function.__code__.co_filename
        assert not namespace["_reviewed_external_hook"](
            fake_impl, hook_name, distribution
        )

        namespace["_REVIEWED_EXTERNAL_DISTRIBUTIONS"][distribution] = (
            version,
            "0" * 64,
        )
        assert not namespace["_reviewed_external_hook"](impl, hook_name, distribution)
        namespace["_REVIEWED_EXTERNAL_DISTRIBUTIONS"][distribution] = (
            version,
            source_sha256,
        )
        namespace["_REVIEWED_EXTERNAL_DISTRIBUTIONS"][distribution] = (
            version + ".unreviewed",
            source_sha256,
        )
        assert not namespace["_reviewed_external_hook"](impl, hook_name, distribution)
        namespace["_REVIEWED_EXTERNAL_DISTRIBUTIONS"][distribution] = (
            version,
            source_sha256,
        )


@pytest.mark.parametrize(
    ("fail_call", "forged_outcome", "cold", "exit_status"),
    [
        (False, "failed", True, 1),
        (True, "passed", False, 0),
    ],
)
def test_temporary_report_constructor_patch_cannot_forge_a_cold_result(
    tmp_path, fail_call, forged_outcome, cold, exit_status
):
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (tests / "test_constructor.py").write_text(
        "import os\n"
        "import pytest\n"
        "from _pytest.reports import TestReport\n\n"
        "def test_behavior(monkeypatch):\n"
        "    if not os.environ.get('INSTALL_FORGER'):\n"
        "        assert True\n"
        "        return\n"
        "    original = TestReport.from_item_and_call\n"
        "    def forged(cls, item, call):\n"
        "        report = original(item, call)\n"
        "        if report.when == 'call':\n"
        f"            report.outcome = {forged_outcome!r}\n"
        "            if report.outcome == 'failed':\n"
        "                report.longrepr = 'forged call failure'\n"
        "        return report\n"
        "    monkeypatch.setattr(TestReport, 'from_item_and_call', classmethod(forged))\n"
        "    if os.environ.get('FAIL_CALL'):\n"
        "        assert False\n"
        "    assert True\n",
        encoding="utf-8",
    )
    plugin_dir = tmp_path / "plugins"
    baseline_result, baseline = _run_child_pytest(
        project, plugin_dir, tmp_path / "baseline.json"
    )
    assert baseline_result.returncode == 0, baseline_result.stderr
    assert baseline is not None and baseline["unsupported"] is False
    expected = receipt_facts(baseline)
    assert expected is not None

    result, receipt = _run_child_pytest(
        project,
        plugin_dir,
        tmp_path / "forged.json",
        env_overrides={
            "INSTALL_FORGER": "1",
            **({"FAIL_CALL": "1"} if fail_call else {}),
        },
        cold=cold,
    )

    assert result.returncode == exit_status
    assert receipt is not None
    assert receipt["unsupported"] is True
    assert receipt["unsupported_pytest_cov_only"] is False
    assert receipt["hook_fingerprint_sha256"] == baseline["hook_fingerprint_sha256"]
    if cold:
        assert cold_witness_from_receipt(
            receipt, process_exit_status=result.returncode, expected=expected
        ) is None
    else:
        assert not survivor_proof_ok(
            receipt,
            process_exit_status=result.returncode,
            expected=expected,
            command="declared",
        )


@pytest.mark.parametrize(
    ("forged_mode", "fail_call", "exit_status"),
    [("fail", False, 1), ("suppress", True, 0)],
)
def test_precollection_pytest_builtin_hook_substitution_cannot_prove_cold_result(
    tmp_path, forged_mode, fail_call, exit_status
):
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (tests / "test_builtin_hook.py").write_text(
        "import os\n\n"
        "def test_behavior():\n"
        "    if os.environ.get('FAIL_CALL'):\n"
        "        assert False, 'real test failure'\n"
        "    assert True\n",
        encoding="utf-8",
    )
    plugin_dir = tmp_path / "plugins"
    baseline_result, baseline = _run_child_pytest(
        project, plugin_dir, tmp_path / "baseline.json", cold=True
    )
    assert baseline_result.returncode == 0, baseline_result.stderr
    assert baseline is not None and baseline["unsupported"] is False
    expected = receipt_facts(baseline)
    assert expected is not None

    (tests / "conftest.py").write_text(
        "import os\n\n"
        "def pytest_configure(config):\n"
        "    if not os.environ.get('INSTALL_FORGER'):\n"
        "        return\n"
        "    hook = config.hook.pytest_runtest_call\n"
        "    impl = next(item for item in hook.get_hookimpls() if item.plugin_name == 'runner')\n"
        "    original = impl.function\n"
        "    original.__globals__['_assay_test_original_hook'] = original\n"
        "    source = '''def _assay_test_forged_hook(item):\n"
        "    import os\n"
        "    mode = os.environ.get('FORGER_MODE')\n"
        "    if mode == 'fail':\n"
        "        raise AssertionError('forged call failure')\n"
        "    if mode == 'suppress':\n"
        "        return None\n"
        "    return _assay_test_original_hook(item)\n"
        "'''\n"
        "    namespace = original.__globals__\n"
        "    exec(compile(source, original.__code__.co_filename, 'exec'), namespace)\n"
        "    forged = namespace['_assay_test_forged_hook']\n"
        "    forged.__module__ = original.__module__\n"
        "    forged.__qualname__ = original.__qualname__\n"
        "    forged.__name__ = original.__name__\n"
        "    impl.function = forged\n",
        encoding="utf-8",
    )

    result, receipt = _run_child_pytest(
        project,
        plugin_dir,
        tmp_path / "forged.json",
        env_overrides={
            "INSTALL_FORGER": "1",
            "FORGER_MODE": forged_mode,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            **({"FAIL_CALL": "1"} if fail_call else {}),
        },
        cold=True,
    )

    assert result.returncode == exit_status
    assert receipt is not None
    assert receipt["unsupported"] is True
    assert receipt["unsupported_pytest_cov_only"] is False
    # The counterfeit intentionally preserves all fields in the ordinary hook
    # fingerprint. The pre-conftest callable pin must reject it independently.
    assert receipt["hook_fingerprint_sha256"] == baseline["hook_fingerprint_sha256"]
    assert cold_witness_from_receipt(
        receipt, process_exit_status=result.returncode, expected=expected
    ) is None
    assert not survivor_proof_ok(
        receipt,
        process_exit_status=result.returncode,
        expected=expected,
        command="declared",
    )


def test_post_session_unconfigure_cannot_replace_the_receipt(tmp_path):
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (project / "conftest.py").write_text(
        "import json, os\n"
        "def pytest_unconfigure(config):\n"
        "    fd = int(os.environ['ASSAY_MUTATION_WITNESS_FD'])\n"
        "    forged = json.dumps({'witness_node_id': 'tests/test_setup.py::test_target', "
        "'witness_when': 'call', 'witness_outcome': 'failed', "
        "'session_exit_status': 1}).encode()\n"
        "    try:\n"
        "        os.write(fd, len(forged).to_bytes(4, 'big') + forged)\n"
        "    except OSError:\n"
        "        pass\n",
        encoding="utf-8",
    )
    (tests / "test_setup.py").write_text(
        "import pytest\n"
        "@pytest.fixture(autouse=True)\n"
        "def setup_failure():\n"
        "    raise RuntimeError('setup failed before the test call')\n"
        "def test_target():\n"
        "    assert False\n",
        encoding="utf-8",
    )

    result, receipt = _run_child_pytest(
        project, tmp_path / "plugins", tmp_path / "receipt.json"
    )

    assert result.returncode == 1
    assert receipt is not None
    assert receipt["witness_node_id"] is None
    assert receipt["witness_when"] is None
    assert receipt["witness_outcome"] is None


@pytest.mark.parametrize(
    ("forged_mode", "fail_call"), [("fail", False), ("suppress", True)]
)
def test_precollection_assay_report_hook_substitution_cannot_prove_cold_result(
    tmp_path, forged_mode, fail_call
):
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (tests / "test_assay_hook.py").write_text(
        "import os\n\n"
        "def test_behavior():\n"
        "    if os.environ.get('FAIL_CALL'):\n"
        "        assert False, 'real test failure'\n"
        "    assert True\n",
        encoding="utf-8",
    )
    plugin_dir = tmp_path / "plugins"
    baseline_result, baseline = _run_child_pytest(
        project, plugin_dir, tmp_path / "baseline.json", cold=True
    )
    assert baseline_result.returncode == 0, baseline_result.stderr
    assert baseline is not None and baseline["unsupported"] is False
    expected = receipt_facts(baseline)
    assert expected is not None

    (tests / "conftest.py").write_text(
        "import os\n\n"
        "def pytest_configure(config):\n"
        "    if not os.environ.get('INSTALL_FORGER'):\n"
        "        return\n"
        "    hook = config.hook.pytest_runtest_logreport\n"
        "    impl = next(item for item in hook.get_hookimpls()\n"
        "                if item.plugin_name == 'assay_mutation_witness_plugin')\n"
        "    original = impl.function\n"
        "    namespace = original.__globals__\n"
        "    namespace['_assay_test_original_report_hook'] = original\n"
        "    source = '''def _assay_test_forged_report_hook(report):\n"
        "    import os\n"
        "    mode = os.environ.get('FORGER_MODE')\n"
        "    if mode == 'fail' and report.when == 'call' and report.outcome == 'passed':\n"
        "        report.outcome = 'failed'\n"
        "        report.longrepr = 'forged failure'\n"
        "    if mode == 'suppress' and report.when == 'call' and report.outcome == 'failed':\n"
        "        return None\n"
        "    return _assay_test_original_report_hook(report)\n"
        "'''\n"
        "    exec(compile(source, original.__code__.co_filename, 'exec'), namespace)\n"
        "    forged = namespace['_assay_test_forged_report_hook']\n"
        "    forged.__module__ = original.__module__\n"
        "    forged.__qualname__ = original.__qualname__\n"
        "    forged.__name__ = original.__name__\n"
        "    impl.function = forged\n",
        encoding="utf-8",
    )

    result, receipt = _run_child_pytest(
        project,
        plugin_dir,
        tmp_path / "forged.json",
        env_overrides={
            "INSTALL_FORGER": "1",
            "FORGER_MODE": forged_mode,
            **({"FAIL_CALL": "1"} if fail_call else {}),
        },
        cold=True,
    )

    assert result.returncode == 1
    assert receipt is not None
    assert receipt["unsupported"] is True
    assert receipt["unsupported_pytest_cov_only"] is False
    assert receipt["hook_fingerprint_sha256"] == baseline["hook_fingerprint_sha256"]
    assert cold_witness_from_receipt(
        receipt, process_exit_status=result.returncode, expected=expected
    ) is None
    assert not survivor_proof_ok(
        receipt,
        process_exit_status=result.returncode,
        expected=expected,
        command="declared",
    )


@pytest.mark.parametrize(
    ("forged_mode", "fail_call", "cold"),
    [
        ("fail", False, True),
        ("suppress", True, True),
        ("fail", False, False),
        ("suppress", True, False),
    ],
)
def test_candidate_cannot_replace_liveness_report_hook_to_change_mutation_outcome(
    tmp_path, forged_mode, fail_call, cold
):
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (tests / "test_liveness_hook.py").write_text(
        "import os\n\n"
        "def test_behavior():\n"
        "    if os.environ.get('FAIL_CALL'):\n"
        "        assert False, 'real candidate failure'\n"
        "    assert True\n",
        encoding="utf-8",
    )
    plugin_dir = tmp_path / "plugins"
    liveness_plugin_path = liveness.materialize_liveness_plugin(plugin_dir)
    baseline_result, baseline = _run_child_pytest(
        project,
        plugin_dir,
        tmp_path / "baseline.json",
        cold=cold,
        liveness_plugin_path=liveness_plugin_path,
    )
    assert baseline_result.returncode == 0, baseline_result.stderr
    assert baseline is not None and baseline["unsupported"] is False
    expected = receipt_facts(baseline)
    assert expected is not None

    (tests / "conftest.py").write_text(
        "import os\n\n"
        "def pytest_configure(config):\n"
        "    if not os.environ.get('INSTALL_FORGER'):\n"
        "        return\n"
        "    hook = config.hook.pytest_runtest_logreport\n"
        "    impl = next(item for item in hook.get_hookimpls()\n"
        "                if item.plugin_name == 'assay_liveness_plugin')\n"
        "    original = impl.function\n"
        "    namespace = original.__globals__\n"
        "    namespace['_liveness_test_original_hook'] = original\n"
        "    source = '''def _liveness_test_forged_hook(report):\n"
        "    import os\n"
        "    mode = os.environ.get('FORGER_MODE')\n"
        "    if mode == 'fail' and report.when == 'call' and report.outcome == 'passed':\n"
        "        report.outcome = 'failed'\n"
        "        report.longrepr = 'forged candidate failure'\n"
        "    elif mode == 'suppress' and report.when == 'call' and report.outcome == 'failed':\n"
        "        report.outcome = 'passed'\n"
        "        report.longrepr = None\n"
        "    return _liveness_test_original_hook(report)\n"
        "'''\n"
        "    exec(compile(source, original.__code__.co_filename, 'exec'), namespace)\n"
        "    forged = namespace['_liveness_test_forged_hook']\n"
        "    forged.__module__ = original.__module__\n"
        "    forged.__qualname__ = original.__qualname__\n"
        "    forged.__name__ = original.__name__\n"
        "    impl.function = forged\n",
        encoding="utf-8",
    )
    overrides = {
        "INSTALL_FORGER": "1",
        "FORGER_MODE": forged_mode,
        **({"FAIL_CALL": "1"} if fail_call else {}),
    }
    result, receipt = _run_child_pytest(
        project,
        plugin_dir,
        tmp_path / f"candidate-{forged_mode}-{cold}.json",
        env_overrides=overrides,
        cold=cold,
        liveness_plugin_path=liveness_plugin_path,
    )

    assert result.returncode in (0, 1)
    assert receipt is not None
    assert receipt["unsupported"] is True
    assert receipt["unsupported_pytest_cov_only"] is False
    assert receipt["hook_fingerprint_sha256"] == baseline["hook_fingerprint_sha256"]
    assert cold_witness_from_receipt(
        receipt, process_exit_status=result.returncode, expected=expected
    ) is None
    assert not survivor_proof_ok(
        receipt,
        process_exit_status=result.returncode,
        expected=expected,
        command="declared",
    )
    assert not declared_failure_proof_ok(
        receipt,
        process_exit_status=result.returncode,
        expected=expected,
        manifest_node_ids=("tests/test_liveness_hook.py::test_behavior",),
    )


def test_pytest_cov_in_place_code_change_is_not_a_reviewed_exception(tmp_path):
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (tests / "test_coverage_hook.py").write_text(
        "def test_ok():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    plugin_dir = tmp_path / "plugins"
    pytest_args = ("-p", "pytest_cov.plugin", "--cov=tests")
    baseline_result, baseline = _run_child_pytest(
        project,
        plugin_dir,
        tmp_path / "baseline.json",
        pytest_args=pytest_args,
    )
    assert baseline_result.returncode == 0, baseline_result.stderr
    assert baseline is not None and baseline["unsupported_pytest_cov_only"] is True
    expected = receipt_facts(baseline)
    assert expected is not None
    assert survivor_proof_ok(
        baseline,
        process_exit_status=baseline_result.returncode,
        expected=expected,
        command="declared",
    )

    (tests / "conftest.py").write_text(
        "import os\n\n"
        "def pytest_configure(config):\n"
        "    if not os.environ.get('MUTATE_COV_CODE'):\n"
        "        return\n"
        "    from pytest_cov.plugin import CovPlugin\n"
        "    function = CovPlugin.pytest_runtest_call\n"
        "    namespace = {}\n"
        "    source = 'def replacement(self, item):\\n    yield\\n'\n"
        "    exec(compile(source, function.__code__.co_filename, 'exec'), namespace)\n"
        "    function.__code__ = namespace['replacement'].__code__\n",
        encoding="utf-8",
    )
    result, receipt = _run_child_pytest(
        project,
        plugin_dir,
        tmp_path / "mutated-cov.json",
        pytest_args=pytest_args,
        env_overrides={"MUTATE_COV_CODE": "1"},
    )

    assert result.returncode == 0, result.stderr
    assert receipt is not None
    assert receipt["unsupported"] is True
    assert receipt["unsupported_pytest_cov_only"] is False
    assert not survivor_proof_ok(
        receipt,
        process_exit_status=result.returncode,
        expected=expected,
        command="declared",
    )


@pytest.mark.parametrize(("strict", "exit_status"), [(False, 0), (True, 1)])
def test_builtin_xfail_rewrites_remain_supported_for_cold_proof(
    tmp_path, strict, exit_status
):
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (tests / "test_xfail.py").write_text(
        "import pytest\n\n"
        f"@pytest.mark.xfail(reason='expected', strict={strict!r})\n"
        "def test_expected_failure():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    result, receipt = _run_child_pytest(
        project, tmp_path / "plugins", tmp_path / "receipt.json", cold=True
    )

    assert result.returncode == exit_status, result.stderr
    assert receipt is not None
    assert receipt["unsupported"] is False
    facts = receipt_facts(receipt)
    assert facts is not None
    if strict:
        assert cold_witness_from_receipt(
            receipt, process_exit_status=result.returncode, expected=facts
        ) is not None
    else:
        assert survivor_proof_ok(
            receipt,
            process_exit_status=result.returncode,
            expected=facts,
            command="r2",
        )


def test_declared_failure_proof_only_allows_reviewed_pytest_cov_unsupported_hooks():
    node = "tests/test_example.py::test_case"
    coverage_baseline = _receipt(
        unsupported=True,
        unsupported_pytest_cov_only=True,
        session_exit_status=0,
        witness_node_id=None,
        witness_when=None,
        witness_outcome=None,
        started_count=0,
    )
    expected = receipt_facts(coverage_baseline)
    assert expected is not None
    declared_failure = _receipt(
        unsupported=True,
        unsupported_pytest_cov_only=True,
        witness_node_id=node,
        started_count=1,
    )
    assert declared_failure_proof_ok(
        declared_failure,
        process_exit_status=1,
        expected=expected,
        manifest_node_ids=(node,),
    )

    refusals = (
        {"unsupported_pytest_cov_only": False},
        {"unsupported_pytest_cov_only": 1},
        {"unsupported_pytest_cov_only": True, "unsupported": False},
        {"collection_error": True},
        {"auxiliary_failure": True},
        {"earlier_failure": True},
        {"started_prefix_ok": False},
        {"hook_fingerprint_sha256": "d" * 64},
    )
    for change in refusals:
        assert not declared_failure_proof_ok(
            {**declared_failure, **change},
            process_exit_status=1,
            expected=expected,
            manifest_node_ids=(node,),
        ), change
    missing_fact = dict(declared_failure)
    del missing_fact["unsupported_pytest_cov_only"]
    assert not declared_failure_proof_ok(
        missing_fact,
        process_exit_status=1,
        expected=expected,
        manifest_node_ids=(node,),
    )
    assert not declared_failure_proof_ok(
        None,
        process_exit_status=1,
        expected=expected,
        manifest_node_ids=(node,),
    )


def test_declared_failure_proof_accepts_a_failure_inside_the_started_prefix():
    failed_node = "tests/test_example.py::test_first"
    later_node = "tests/test_example.py::test_later"
    baseline = _receipt(
        unsupported=True,
        unsupported_pytest_cov_only=True,
        session_exit_status=0,
        witness_node_id=None,
        witness_when=None,
        witness_outcome=None,
        started_count=0,
        collection_count=2,
    )
    expected = receipt_facts(baseline)
    assert expected is not None
    continued_failure = _receipt(
        unsupported=True,
        unsupported_pytest_cov_only=True,
        witness_node_id=failed_node,
        started_count=2,
        collection_count=2,
    )

    assert declared_failure_proof_ok(
        continued_failure,
        process_exit_status=1,
        expected=expected,
        manifest_node_ids=(failed_node, later_node),
    )
    assert not declared_failure_proof_ok(
        {**continued_failure, "started_count": 1},
        process_exit_status=1,
        expected=expected,
        manifest_node_ids=(later_node, failed_node),
    )


def test_installed_pytest_cov_hooks_are_classified_as_the_only_declared_exception(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tests").mkdir()
    (project / "tests" / "test_ok.py").write_text(
        "import os\n\n"
        "def test_ok():\n"
        "    if os.environ.get('FAIL_CALL'):\n"
        "        assert False\n",
        encoding="utf-8",
    )
    with (project / "tests" / "test_ok.py").open("a", encoding="utf-8") as stream:
        stream.write("\ndef test_z_after_failure():\n    assert True\n")
    plugin_dir = tmp_path / "plugins"
    receipt_path = tmp_path / "declared-receipt.json"
    result, receipt = _run_child_pytest(
        project,
        plugin_dir,
        receipt_path,
        pytest_args=("-p", "pytest_cov.plugin", "--cov=tests"),
    )
    assert result.returncode == 0, result.stderr
    assert receipt is not None
    assert receipt["unsupported"] is True
    assert receipt["unsupported_pytest_cov_only"] is True
    facts = receipt_facts(receipt)
    assert facts is not None
    assert survivor_proof_ok(
        receipt, process_exit_status=0, expected=facts, command="declared"
    )

    failure_result, failure_receipt = _run_child_pytest(
        project,
        plugin_dir,
        tmp_path / "declared-failure.json",
        pytest_args=("-p", "pytest_cov.plugin", "--cov=tests"),
        env_overrides={"FAIL_CALL": "1"},
    )
    assert failure_result.returncode == 1
    assert failure_receipt is not None
    assert failure_receipt["unsupported"] is True
    assert failure_receipt["unsupported_pytest_cov_only"] is True
    assert failure_receipt["started_count"] == 2
    assert declared_failure_proof_ok(
        failure_receipt,
        process_exit_status=failure_result.returncode,
        expected=facts,
        manifest_node_ids=(
            "tests/test_ok.py::test_ok",
            "tests/test_ok.py::test_z_after_failure",
        ),
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json",
        b'{"a":1,"a":2}',
        b"\xff",
        b"NaN",
        b"[]",
        b"[" * 1200 + b"0" + b"]" * 1200,
    ],
)
def test_receipt_reader_returns_no_witness_for_unreadable_json(tmp_path, raw):
    path = tmp_path / "receipt.json"
    path.write_bytes(raw)
    assert read_internal_receipt(path) is None


def test_receipt_reader_returns_only_json_objects_and_handles_missing_files(tmp_path):
    missing = tmp_path / "absent.json"
    assert read_internal_receipt(missing) is None
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"ok": True}), encoding="utf-8")
    assert read_internal_receipt(path) == {"ok": True}


def test_receipt_reader_refuses_a_symlink(tmp_path):
    target = tmp_path / "target.json"
    target.write_text('{"ok":true}', encoding="utf-8")
    link = tmp_path / "receipt.json"
    link.symlink_to(target.name)
    assert read_internal_receipt(link) is None


def test_valid_failure_receipt_projects_and_replays_only_for_its_exact_target():
    node = "tests/test_example.py::test_case"
    receipt = _receipt(
        target_node_id=node,
        target_count=1,
        witness_node_id=node,
        stopped_at_target=True,
    )
    expected = {
        "node_id": node,
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    assert witness_from_receipt(receipt, process_exit_status=1) == expected
    assert replay_witness_from_receipt(
        receipt, process_exit_status=1, target_node_id=node
    ) == expected
    assert replay_witness_from_receipt(
        receipt, process_exit_status=1, target_node_id="another::test"
    ) is None


def test_legacy_b106_replay_support_does_not_certify_a_cold_witness():
    node = "tests/test_example.py::test_case"
    receipt = _receipt(
        unsupported=True,
        replay_supported=True,
        target_node_id=node,
        target_count=1,
        witness_node_id=node,
        stopped_at_target=True,
    )
    assert witness_from_receipt(receipt, process_exit_status=1) is not None
    assert replay_witness_from_receipt(
        receipt, process_exit_status=1, target_node_id=node
    ) is not None

    expected = receipt_facts(_receipt())
    assert expected is not None
    cold_receipt = _receipt(
        unsupported=True,
        replay_supported=True,
        cold_requested=True,
        stopped_cold=True,
        failed_call_index=0,
    )
    assert cold_witness_from_receipt(
        cold_receipt, process_exit_status=1, expected=expected
    ) is None


@pytest.mark.parametrize(
    ("changes", "process_status"),
    [
        ({"unsupported": True}, 1),
        ({"session_exit_status": True}, 1),
        ({"session_exit_status": 0}, 1),
        ({"witness_node_id": ""}, 1),
        ({"witness_node_id": "x" * 4097}, 1),
        ({"witness_node_id": "bad\ud800"}, 1),
        ({"witness_when": "setup"}, 1),
        ({"witness_outcome": "passed"}, 1),
        ({"auxiliary_failure": True}, 1),
        ({"target_count": True}, 1),
        ({"target_count": 2}, 1),
        ({"target_node_id": "other::test"}, 1),
        ({"unexpected": "extra"}, 1),
        ({}, True),
        ({}, 0),
    ],
)
def test_failure_receipt_projection_rejects_ambiguous_or_foreign_facts(
    changes, process_status
):
    receipt = _receipt(**changes)
    assert witness_from_receipt(receipt, process_exit_status=process_status) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_count": None},
        {"target_node_id": None},
        {"stopped_at_target": False},
        {"earlier_failure": True},
        {"collection_error": True},
    ],
)
def test_replay_receipt_requires_one_exact_target_and_no_earlier_failure(overrides):
    node = "tests/test_example.py::test_case"
    receipt = _receipt(
        target_node_id=node, target_count=1, witness_node_id=node,
        stopped_at_target=True,
    )
    receipt.update(overrides)
    assert replay_witness_from_receipt(
        receipt, process_exit_status=1, target_node_id=node
    ) is None


def test_replay_receipt_rejects_wrong_target_count_and_target_name():
    node = "tests/test_example.py::test_case"
    for changes in (
        {"target_count": True},
        {"target_count": 2},
        {"target_node_id": "other::test"},
    ):
        receipt = _receipt(
            target_node_id=node,
            target_count=1,
            witness_node_id=node,
            stopped_at_target=True,
        )
        receipt.update(changes)
        assert replay_witness_from_receipt(
            receipt, process_exit_status=1, target_node_id=node
        ) is None
