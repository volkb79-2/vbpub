"""Exact residual and safety coverage for the guided memory squeeze."""

from __future__ import annotations

import signal
from pathlib import Path
from typing import Any

import pytest

from topos.actions import squeeze
from topos.actions.squeeze import (
    SqueezeConfig,
    SqueezeResult,
    SqueezeStep,
    _RestoreGuard,
    _default_cgroup_flat_kv_reader,
    _default_cgroup_int_reader,
    _default_cgroup_pressure_reader,
    _default_cgroup_writer,
    _mib,
    parse_size,
    render_squeeze_result,
    run_squeeze,
    run_squeeze_gated,
)


def _config(tmp_path: Path, **overrides: object) -> SqueezeConfig:
    values: dict[str, object] = {
        "target": "/cg/worker.scope",
        "step": 1,
        "delay": 0.0,
        "floor": 1,
        "start": 2,
        "relax_to": "max",
        "psi_some_limit": 10.0,
        "psi_full_limit": 5.0,
        "rf_limit": 200,
        "force": False,
        "log_path": tmp_path / "squeeze.jsonl",
        "audit_path": tmp_path / "audit.jsonl",
        "admin": True,
        "confirm": "SQUEEZE",
    }
    values.update(overrides)
    return SqueezeConfig(**values)  # type: ignore[arg-type]


class _Audit:
    def __init__(
        self,
        *,
        fail_on: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_on = fail_on
        self.error = error

    def record(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if self.fail_on == len(self.calls):
            if self.error is None:
                raise AssertionError("failing audit fixture requires an error")
            raise self.error


class _ControlledFile:
    def __init__(
        self,
        *,
        fail_on_write: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.fail_on_write = fail_on_write
        self.error = error or OSError("write failed")
        self.write_calls = 0
        self.writes: list[str] = []
        self.flush_calls = 0
        self.closed = False

    def write(self, value: str) -> int:
        self.write_calls += 1
        if self.write_calls == self.fail_on_write:
            raise self.error
        self.writes.append(value)
        return len(value)

    def flush(self) -> None:
        self.flush_calls += 1

    def close(self) -> None:
        self.closed = True


def _int_reader(target: str, filename: str) -> int:
    assert target == "/cg/worker.scope"
    return {
        "memory.current": 10,
        "memory.min": 0,
        "memory.zswap.current": 2,
        "memory.swap.current": 3,
    }[filename]


def _flat_reader(target: str, filename: str) -> dict[str, int]:
    assert (target, filename) == ("/cg/worker.scope", "memory.stat")
    return {"anon": 5, "zswapped": 1, "workingset_refault_anon": 100}


def _pressure_reader(
    target: str,
    filename: str,
) -> dict[str, dict[str, float]]:
    assert (target, filename) == ("/cg/worker.scope", "memory.pressure")
    return {"some": {"avg10": 0.0}, "full": {"avg10": 0.0}}


def _expected_audit_calls(
    config: SqueezeConfig,
    *,
    stop_reason: str,
    squeeze_point: int,
) -> list[dict[str, object]]:
    return [
        {
            "kind": "squeeze",
            "target": config.target,
            "argv": (
                "--target",
                config.target,
                "--admin" if config.admin else "",
                "--confirm",
                config.confirm,
            ),
            "admin": config.admin,
        },
        {
            "kind": "squeeze-end",
            "target": config.target,
            "argv": (
                "stop_reason",
                stop_reason,
                "squeeze_point",
                str(squeeze_point),
                "restored_to",
                config.relax_to,
            ),
            "admin": config.admin,
        },
    ]


def test_parse_size_rejects_missing_suffix_number_exactly() -> None:
    with pytest.raises(ValueError) as exc_info:
        parse_size("K")
    assert str(exc_info.value) == "cannot parse size: 'K'"


def test_default_cgroup_boundaries_use_exact_paths_and_values(
    monkeypatch,
) -> None:
    from topos.collect import cgroup

    calls: list[tuple[str, str]] = []

    def read_int(path: Path) -> tuple[int, str]:
        calls.append(("int", str(path)))
        return 7, "exact"

    def read_flat(path: Path) -> tuple[dict[str, int], str]:
        calls.append(("flat", str(path)))
        return {"anon": 11}, "exact"

    def read_pressure(path: Path) -> tuple[dict[str, dict[str, float]], str]:
        calls.append(("pressure", str(path)))
        return {"some": {"avg10": 1.5}}, "exact"

    write_calls: list[tuple[str, str]] = []

    def write_text(path: Path, value: str) -> int:
        write_calls.append((str(path), value))
        return len(value)

    monkeypatch.setattr(cgroup, "read_int", read_int)
    monkeypatch.setattr(cgroup, "read_flat_kv", read_flat)
    monkeypatch.setattr(cgroup, "read_pressure", read_pressure)
    monkeypatch.setattr(Path, "write_text", write_text)

    assert _default_cgroup_int_reader("/cg", "memory.current") == 7
    assert _default_cgroup_flat_kv_reader("/cg", "memory.stat") == {"anon": 11}
    assert _default_cgroup_pressure_reader("/cg", "memory.pressure") == {
        "some": {"avg10": 1.5}
    }
    assert _default_cgroup_writer("/cg", "memory.high", "4096") is None
    assert calls == [
        ("int", "/cg/memory.current"),
        ("flat", "/cg/memory.stat"),
        ("pressure", "/cg/memory.pressure"),
    ]
    assert write_calls == [("/cg/memory.high", "4096")]


def test_sigterm_handler_restores_then_calls_injected_exit(monkeypatch) -> None:
    writes: list[tuple[str, str, str]] = []
    handler_calls: list[tuple[int, object]] = []
    installed: dict[int, object] = {}
    exits: list[int] = []
    original_int = object()
    original_term = object()

    def writer(path: str, filename: str, value: str) -> None:
        writes.append((path, filename, value))

    def handle(signum: int, handler: object) -> object:
        handler_calls.append((signum, handler))
        installed[signum] = handler
        return original_int if signum == signal.SIGINT else original_term

    monkeypatch.setattr(squeeze.os, "_exit", exits.append)
    guard = _RestoreGuard(
        "/cg",
        "max",
        writer=writer,
        signal_handler=handle,
    )

    guard.__enter__()
    int_handler = installed[signal.SIGINT]
    term_handler = installed[signal.SIGTERM]
    assert callable(term_handler)
    assert term_handler(signal.SIGTERM, None) is None
    guard.__exit__(None, None, None)

    assert writes == [("/cg", "memory.high", "max")]
    assert exits == [128 + signal.SIGTERM]
    assert handler_calls == [
        (signal.SIGINT, int_handler),
        (signal.SIGTERM, term_handler),
        (signal.SIGINT, original_int),
        (signal.SIGTERM, original_term),
    ]


def test_restore_guard_without_enter_skips_handler_restoration() -> None:
    writes: list[tuple[str, str, str]] = []
    handler_calls: list[tuple[int, object]] = []
    guard = _RestoreGuard(
        "/cg",
        "max",
        writer=lambda path, filename, value: writes.append(
            (path, filename, value)
        ),
        signal_handler=lambda signum, handler: handler_calls.append(
            (signum, handler)
        ),
    )

    assert guard.__exit__(None, None, None) is None
    assert writes == [("/cg", "memory.high", "max")]
    assert handler_calls == []


def test_restore_write_failure_is_best_effort_and_idempotent() -> None:
    calls: list[tuple[str, str, str]] = []

    def writer(path: str, filename: str, value: str) -> None:
        calls.append((path, filename, value))
        raise OSError("read-only cgroup")

    guard = _RestoreGuard("/cg", "max", writer=writer)

    assert guard.restore() is None
    assert guard.restore() is None
    assert calls == [("/cg", "memory.high", "max")]


def test_multistep_run_applies_every_measured_high_before_sampling(
    tmp_path: Path,
    monkeypatch,
) -> None:
    writes: list[tuple[str, str, str]] = []
    sleeps: list[float] = []
    audit = _Audit()

    monkeypatch.setattr(squeeze.time, "sleep", sleeps.append)
    config = _config(tmp_path)

    result = run_squeeze(
        config,
        cgroup_int_reader=_int_reader,
        cgroup_flat_kv_reader=_flat_reader,
        cgroup_pressure_reader=_pressure_reader,
        cgroup_writer=lambda path, filename, value: writes.append(
            (path, filename, value)
        ),
        clock=lambda: 50.0,
        auditor=audit,  # type: ignore[arg-type]
    )

    expected_steps = (
        SqueezeStep(0, 2, 10, 5, 1, 2, 3, 0.0, 0.0, None, 50.0),
        SqueezeStep(1, 1, 10, 5, 1, 2, 3, 0.0, 0.0, None, 50.0),
    )
    assert result == SqueezeResult(
        stop_reason="floor",
        stop_high=1,
        squeeze_point=1,
        config=config,
        steps=expected_steps,
        restored_to="max",
        error="",
    )
    assert writes == [
        ("/cg/worker.scope", "memory.high", "2"),
        ("/cg/worker.scope", "memory.high", "1"),
        ("/cg/worker.scope", "memory.high", "max"),
    ]
    assert sleeps == [0.0, 0.0]
    assert [step.refaults_s for step in result.steps] == [None, None]
    assert audit.calls == _expected_audit_calls(
        config,
        stop_reason="floor",
        squeeze_point=1,
    )


def test_explicit_start_below_floor_has_complete_no_step_result(
    tmp_path: Path,
) -> None:
    writes: list[tuple[str, str, str]] = []
    audit = _Audit()
    config = _config(tmp_path, start=0, floor=1)

    result = run_squeeze(
        config,
        cgroup_int_reader=_int_reader,
        cgroup_flat_kv_reader=_flat_reader,
        cgroup_pressure_reader=_pressure_reader,
        cgroup_writer=lambda path, filename, value: writes.append(
            (path, filename, value)
        ),
        clock=lambda: 50.0,
        auditor=audit,  # type: ignore[arg-type]
    )

    assert result == SqueezeResult(
        "floor", 0, 0, config, (), "max", ""
    )
    assert writes == [
        ("/cg/worker.scope", "memory.high", "0"),
        ("/cg/worker.scope", "memory.high", "max"),
    ]
    assert audit.calls == _expected_audit_calls(
        config,
        stop_reason="floor",
        squeeze_point=0,
    )


def test_log_open_failure_has_complete_error_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path)

    def open_log(path: Path, mode: str, *, encoding: str) -> Any:
        assert (path, mode, encoding) == (config.log_path, "w", "utf-8")
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", open_log)

    assert run_squeeze(
        config,
        cgroup_int_reader=_int_reader,
        auditor=_Audit(),  # type: ignore[arg-type]
    ) == SqueezeResult(
        "error",
        0,
        0,
        config,
        (),
        "max",
        f"cannot open log file {config.log_path}: permission denied",
    )


def _run_with_controlled_file(
    tmp_path: Path,
    monkeypatch,
    file: _ControlledFile,
    *,
    start: int,
    floor: int,
) -> tuple[SqueezeConfig, SqueezeResult, list[tuple[str, str, str]]]:
    config = _config(tmp_path, start=start, floor=floor)
    writes: list[tuple[str, str, str]] = []

    def open_log(path: Path, mode: str, *, encoding: str) -> _ControlledFile:
        assert (path, mode, encoding) == (config.log_path, "w", "utf-8")
        return file

    monkeypatch.setattr(Path, "open", open_log)
    result = run_squeeze(
        config,
        cgroup_int_reader=_int_reader,
        cgroup_flat_kv_reader=_flat_reader,
        cgroup_pressure_reader=_pressure_reader,
        cgroup_writer=lambda path, filename, value: writes.append(
            (path, filename, value)
        ),
        clock=lambda: 50.0,
        auditor=_Audit(),  # type: ignore[arg-type]
    )
    return config, result, writes


def test_header_write_failure_closes_log_and_returns_exact_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file = _ControlledFile(fail_on_write=1)
    config, result, writes = _run_with_controlled_file(
        tmp_path,
        monkeypatch,
        file,
        start=0,
        floor=1,
    )

    assert result == SqueezeResult(
        "error", 0, 0, config, (), "max", "log write failed"
    )
    assert writes == []
    assert file.closed is True


def test_step_write_failure_is_nonfatal_and_summary_still_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file = _ControlledFile(fail_on_write=3)
    config, result, writes = _run_with_controlled_file(
        tmp_path,
        monkeypatch,
        file,
        start=1,
        floor=1,
    )

    expected_step = SqueezeStep(
        0, 1, 10, 5, 1, 2, 3, 0.0, 0.0, None, 50.0
    )
    assert result == SqueezeResult(
        "floor", 1, 1, config, (expected_step,), "max", ""
    )
    assert writes == [
        ("/cg/worker.scope", "memory.high", "1"),
        ("/cg/worker.scope", "memory.high", "max"),
    ]
    assert file.closed is True
    records = [
        squeeze.json.loads(line)
        for line in "".join(file.writes).splitlines()
    ]
    assert records == [
        {
            "type": "header",
            "schema_version": 1,
            "ts": 50.0,
            "target": "/cg/worker.scope",
            "step_bytes": 1,
            "delay_s": 0.0,
            "floor_bytes": 1,
            "start_bytes": 1,
            "relax_to": "max",
            "psi_some_limit": 10.0,
            "psi_full_limit": 5.0,
            "rf_limit": 200,
            "force": False,
            "memory_current_bytes": 10,
            "memory_min_bytes": 0,
        },
        {
            "type": "summary",
            "ts": 50.0,
            "stop_reason": "floor",
            "stop_high": 1,
            "squeeze_point": 1,
            "current_at_stop": 10,
            "anon_at_stop": 5,
            "zswapped_at_stop": 1,
            "z_pool_at_stop": 2,
            "relaxed_to": "max",
        },
    ]


def test_summary_write_failure_is_nonfatal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file = _ControlledFile(fail_on_write=3)
    config, result, writes = _run_with_controlled_file(
        tmp_path,
        monkeypatch,
        file,
        start=0,
        floor=1,
    )

    assert result == SqueezeResult(
        "floor", 0, 0, config, (), "max", ""
    )
    assert writes == [
        ("/cg/worker.scope", "memory.high", "0"),
        ("/cg/worker.scope", "memory.high", "max"),
    ]
    assert file.closed is True


def test_keyboard_interrupt_before_first_step_returns_complete_result(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    writes: list[tuple[str, str, str]] = []
    config = _config(tmp_path, start=1, floor=1)

    def reader(target: str, filename: str) -> int:
        calls.append(filename)
        if calls == ["memory.current", "memory.min", "memory.current"]:
            raise KeyboardInterrupt
        return _int_reader(target, filename)

    result = run_squeeze(
        config,
        cgroup_int_reader=reader,
        cgroup_flat_kv_reader=_flat_reader,
        cgroup_pressure_reader=_pressure_reader,
        cgroup_writer=lambda path, filename, value: writes.append(
            (path, filename, value)
        ),
        clock=lambda: 50.0,
        auditor=_Audit(),  # type: ignore[arg-type]
    )

    assert result == SqueezeResult(
        "interrupted", 1, 1, config, (), "max", ""
    )
    assert calls == ["memory.current", "memory.min", "memory.current"]
    assert writes == [
        ("/cg/worker.scope", "memory.high", "1"),
        ("/cg/worker.scope", "memory.high", "max"),
    ]


def test_keyboard_interrupt_after_step_preserves_complete_step(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file = _ControlledFile(
        fail_on_write=3,
        error=KeyboardInterrupt(),
    )
    config, result, writes = _run_with_controlled_file(
        tmp_path,
        monkeypatch,
        file,
        start=1,
        floor=1,
    )
    expected_step = SqueezeStep(
        0, 1, 10, 5, 1, 2, 3, 0.0, 0.0, None, 50.0
    )

    assert result == SqueezeResult(
        "interrupted", 1, 1, config, (expected_step,), "max", ""
    )
    assert writes == [
        ("/cg/worker.scope", "memory.high", "1"),
        ("/cg/worker.scope", "memory.high", "max"),
    ]
    assert file.closed is True


def test_ordinary_measurement_failure_returns_error_after_summary_and_restore(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    writes: list[tuple[str, str, str]] = []
    config = _config(tmp_path, start=1, floor=1)

    def reader(target: str, filename: str) -> int:
        calls.append(filename)
        if calls == ["memory.current", "memory.min", "memory.current"]:
            raise RuntimeError("sample failed")
        return _int_reader(target, filename)

    result = run_squeeze(
        config,
        cgroup_int_reader=reader,
        cgroup_flat_kv_reader=_flat_reader,
        cgroup_pressure_reader=_pressure_reader,
        cgroup_writer=lambda path, filename, value: writes.append(
            (path, filename, value)
        ),
        clock=lambda: 50.0,
        auditor=_Audit(),  # type: ignore[arg-type]
    )

    assert result == SqueezeResult(
        "error", 0, 0, config, (), "max", "sample failed"
    )
    assert calls == ["memory.current", "memory.min", "memory.current"]
    assert writes == [
        ("/cg/worker.scope", "memory.high", "1"),
        ("/cg/worker.scope", "memory.high", "max"),
    ]
    records = [
        squeeze.json.loads(line)
        for line in config.log_path.read_text().splitlines()
    ]
    assert [record["type"] for record in records] == ["header", "summary"]
    assert records[1]["stop_reason"] == "error"


def test_audit_start_ordinary_failure_is_nonfatal(tmp_path: Path) -> None:
    audit = _Audit(fail_on=1, error=RuntimeError("audit unavailable"))
    config = _config(tmp_path)

    result = run_squeeze(
        config,
        cgroup_int_reader=lambda target, filename: None,
        auditor=audit,  # type: ignore[arg-type]
    )

    assert result == SqueezeResult(
        "error", 0, 0, config, (), "max", "cannot read memory.current"
    )
    assert audit.calls == _expected_audit_calls(
        config,
        stop_reason="error",
        squeeze_point=0,
    )[:1]


def test_audit_start_does_not_swallow_keyboard_interrupt(tmp_path: Path) -> None:
    audit = _Audit(fail_on=1, error=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        run_squeeze(
            _config(tmp_path),
            cgroup_int_reader=_int_reader,
            auditor=audit,  # type: ignore[arg-type]
        )
    assert audit.calls == _expected_audit_calls(
        _config(tmp_path),
        stop_reason="error",
        squeeze_point=0,
    )[:1]


def test_audit_end_ordinary_failure_is_nonfatal(tmp_path: Path) -> None:
    audit = _Audit(fail_on=2, error=RuntimeError("audit unavailable"))
    config = _config(tmp_path, start=0, floor=1)

    result = run_squeeze(
        config,
        cgroup_int_reader=_int_reader,
        cgroup_flat_kv_reader=_flat_reader,
        cgroup_pressure_reader=_pressure_reader,
        cgroup_writer=lambda path, filename, value: None,
        clock=lambda: 50.0,
        auditor=audit,  # type: ignore[arg-type]
    )

    assert result == SqueezeResult(
        "floor", 0, 0, config, (), "max", ""
    )
    assert audit.calls == _expected_audit_calls(
        config,
        stop_reason="floor",
        squeeze_point=0,
    )


def test_audit_end_does_not_swallow_keyboard_interrupt(tmp_path: Path) -> None:
    audit = _Audit(fail_on=2, error=KeyboardInterrupt())
    writes: list[tuple[str, str, str]] = []

    with pytest.raises(KeyboardInterrupt):
        run_squeeze(
            _config(tmp_path, start=0, floor=1),
            cgroup_int_reader=_int_reader,
            cgroup_flat_kv_reader=_flat_reader,
            cgroup_pressure_reader=_pressure_reader,
            cgroup_writer=lambda path, filename, value: writes.append(
                (path, filename, value)
            ),
            clock=lambda: 50.0,
            auditor=audit,  # type: ignore[arg-type]
        )
    config = _config(tmp_path, start=0, floor=1)
    assert audit.calls == _expected_audit_calls(
        config,
        stop_reason="floor",
        squeeze_point=0,
    )
    assert writes == [
        ("/cg/worker.scope", "memory.high", "0"),
        ("/cg/worker.scope", "memory.high", "max"),
    ]


def test_success_render_without_steps_is_complete(tmp_path: Path) -> None:
    config = _config(tmp_path, start=0, floor=1)
    result = SqueezeResult("floor", 0, 0, config, (), "max", "")

    assert render_squeeze_result(result) == "\n".join(
        [
            "=== Squeeze Result ===",
            "Target: /cg/worker.scope",
            "Stop reason: floor",
            "Stop memory.high: 0 (0 MiB)",
            "Squeeze point (hot+warm set): 0 (0 MiB)",
            "Steps recorded: 0",
            "Restored memory.high to: max",
            "",
            "Per-step data and header/summary in:",
            f"  {config.log_path}",
        ]
    )
    assert _mib(None) == "N/A"


def test_root_check_ordinary_failure_is_fail_closed(tmp_path: Path) -> None:
    calls: list[str] = []

    def root_check() -> bool:
        calls.append("root")
        raise RuntimeError("identity unavailable")

    result = run_squeeze_gated(_config(tmp_path), root_check=root_check)

    assert result == SqueezeResult(
        "error",
        0,
        0,
        _config(tmp_path),
        (),
        "max",
        "root privileges are required",
    )
    assert calls == ["root"]


def test_root_check_does_not_swallow_keyboard_interrupt(tmp_path: Path) -> None:
    calls: list[str] = []

    def root_check() -> bool:
        calls.append("root")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_squeeze_gated(_config(tmp_path), root_check=root_check)
    assert calls == ["root"]
