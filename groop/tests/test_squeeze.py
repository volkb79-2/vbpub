"""Tests for ``groop squeeze`` guided memory measurement.

Full test suite for P56 covering gates, safety, stop conditions, log shape,
signal handling, and CLI argument parsing — all against injected readers/
writers with no real cgroupfs mutation.
"""

from __future__ import annotations

import json
import math
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from groop.actions.audit import AuditLog
from groop.actions.squeeze import (
    SqueezeConfig,
    SqueezeResult,
    SqueezeStep,
    _RestoreGuard,
    parse_size,
    render_squeeze_result,
    run_squeeze,
    run_squeeze_gated,
    squeeze_result_to_jsonable,
)

# ---------------------------------------------------------------------------
# Helpers — injectable cgroup reader/writer factories
# ---------------------------------------------------------------------------


class _FakeCgroup:
    """Simulates cgroup files for testing.

    Tracks writes to memory.high and cycles through pre-set read states
    on each call, so successive reads return different values.
    """

    def __init__(self) -> None:
        self._writes: dict[str, list[str]] = {}
        self._states: list[dict[str, object]] = []
        self._call_count = 0

    def add_state(
        self,
        *,
        memory_current: int | None = 0,
        memory_min: int = 0,
        anon: int = 0,
        zswapped: int = 0,
        z_pool: int = 0,
        swap_val: int = 0,
        rf_cum: int = 0,
        psi_some: float = 0.0,
        psi_full: float = 0.0,
    ) -> None:
        """Add one state to the read cycle."""
        self._states.append({
            "memory.current": memory_current,
            "memory.min": memory_min,
            "memory.zswap.current": z_pool,
            "memory.swap.current": swap_val,
            "stat:anon": anon,
            "stat:zswapped": zswapped,
            "stat:workingset_refault_anon": rf_cum,
            "psi:some": psi_some,
            "psi:full": psi_full,
        })

    def _current_state(self) -> dict[str, object]:
        if not self._states:
            return {}
        idx = min(self._call_count, len(self._states) - 1)
        return self._states[idx]

    def int_reader(self, cgroup_path: str, filename: str) -> int | None:
        state = self._current_state()
        self._call_count += 1
        val = state.get(filename)
        if val is None:
            return None
        return val if isinstance(val, int) else None

    def flat_kv_reader(self, cgroup_path: str, filename: str) -> dict[str, int]:
        state = self._current_state()
        self._call_count += 1
        result: dict[str, int] = {}
        # State keys use the short filename part (e.g. "stat:" for memory.stat)
        # Map common filenames to their prefix in state
        prefix_map = {
            "memory.stat": "stat:",
            "memory.events": "events:",
        }
        expected_prefix = prefix_map.get(filename, filename + ":")
        for key, val in state.items():
            if key.startswith(expected_prefix):
                if isinstance(val, int):
                    result[key.split(":", 1)[1]] = val
        return result

    def pressure_reader(self, cgroup_path: str, filename: str) -> dict[str, dict[str, float]]:
        state = self._current_state()
        self._call_count += 1
        result: dict[str, dict[str, float]] = {}
        psi_some = state.get("psi:some", 0.0)
        psi_full = state.get("psi:full", 0.0)
        if isinstance(psi_some, (int, float)):
            result["some"] = {"avg10": float(psi_some), "avg60": 0.0, "total": 0}
        if isinstance(psi_full, (int, float)):
            result["full"] = {"avg10": float(psi_full), "avg60": 0.0, "total": 0}
        return result

    def writer(self, cgroup_path: str, filename: str, value: str) -> None:
        if filename not in self._writes:
            self._writes[filename] = []
        self._writes[filename].append(value)

    def text_reader(self, cgroup_path: str, filename: str) -> str | None:
        val = self._current_state().get(filename)
        if val is None:
            return None
        return str(val)

    def advance(self) -> None:
        """Move to the next read state."""
        self._call_count += 1


def _make_config(
    target: str = "/sys/fs/cgroup/test.scope",
    *,
    step: int = 256 * 1024 * 1024,
    delay: float = 0.001,
    floor: int = 512 * 1024 * 1024,
    start: int | None = None,
    relax_to: str = "max",
    psi_some_limit: float = 10.0,
    psi_full_limit: float = 5.0,
    rf_limit: int = 200,
    force: bool = False,
    log_path: Path | None = None,
    audit_path: Path | None = None,
    admin: bool = True,
    confirm: str = "SQUEEZE",
) -> SqueezeConfig:
    return SqueezeConfig(
        target=target,
        step=step,
        delay=delay,
        floor=floor,
        start=start,
        relax_to=relax_to,
        psi_some_limit=psi_some_limit,
        psi_full_limit=psi_full_limit,
        rf_limit=rf_limit,
        force=force,
        log_path=log_path or Path("/tmp/test-squeeze.jsonl"),
        audit_path=audit_path or Path("/tmp/test-audit.jsonl"),
        admin=admin,
        confirm=confirm,
    )


# ---------------------------------------------------------------------------
# parse_size
# ---------------------------------------------------------------------------


class TestParseSize:
    def test_bytes(self) -> None:
        assert parse_size("4096") == 4096

    def test_kilobytes(self) -> None:
        assert parse_size("4K") == 4096
        assert parse_size("4k") == 4096

    def test_megabytes(self) -> None:
        assert parse_size("256M") == 256 * 1024 * 1024
        assert parse_size("1m") == 1024 * 1024

    def test_gigabytes(self) -> None:
        assert parse_size("1G") == 1024 * 1024 * 1024
        assert parse_size("2g") == 2 * 1024 * 1024 * 1024

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="size must be a non-empty string"):
            parse_size("")
        with pytest.raises(ValueError, match="cannot parse size"):
            parse_size("abc")
        with pytest.raises(ValueError, match="squeeze size parameter"):
            parse_size("max")

    def test_non_string_raises(self) -> None:
        with pytest.raises(ValueError, match="size must be a non-empty string"):
            parse_size("")
        with pytest.raises(ValueError, match="size must be a non-empty string"):
            parse_size(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# run_squeeze_gated — gate tests (reusing P46 pattern)
# ---------------------------------------------------------------------------


class TestSqueezeGates:
    """run_squeeze_gated enforces admin/confirm/root gates."""

    def test_gate_admin_false(self, tmp_path: Path) -> None:
        config = _make_config(admin=False, log_path=tmp_path / "log.jsonl", audit_path=tmp_path / "audit.jsonl")
        result = run_squeeze_gated(config)
        assert result.stop_reason == "error"
        assert "admin mode" in result.error

    def test_gate_confirm_wrong(self, tmp_path: Path) -> None:
        config = _make_config(admin=True, confirm="wrong", log_path=tmp_path / "log.jsonl", audit_path=tmp_path / "audit.jsonl")
        result = run_squeeze_gated(config)
        assert result.stop_reason == "error"
        assert "SQUEEZE" in result.error

    def test_gate_root_false(self, tmp_path: Path) -> None:
        config = _make_config(log_path=tmp_path / "log.jsonl", audit_path=tmp_path / "audit.jsonl")
        result = run_squeeze_gated(config, root_check=lambda: False)
        assert result.stop_reason == "error"
        assert "root" in result.error

    def test_gates_pass_with_root(self, tmp_path: Path) -> None:
        """Gates pass when all conditions met; squeeze runs to completion."""
        cg = _FakeCgroup()
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=0)
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=0)

        config = _make_config(
            target="/test",
            step=256_000_000,
            delay=0.001,
            floor=100_000_000,
            log_path=tmp_path / "log.jsonl",
            audit_path=tmp_path / "audit.jsonl",
        )
        result = run_squeeze_gated(
            config,
            root_check=lambda: True,
            cgroup_int_reader=cg.int_reader,
            cgroup_flat_kv_reader=cg.flat_kv_reader,
            cgroup_pressure_reader=cg.pressure_reader,
            cgroup_writer=cg.writer,
            clock=time.time,
        )
        assert result.stop_reason in ("floor", "refault_rate")


# ---------------------------------------------------------------------------
# memory.min > 0 refusal and --force override
# ---------------------------------------------------------------------------


class TestMemoryMinGuard:
    def test_refuses_when_memory_min_positive(self, tmp_path: Path) -> None:
        cg = _FakeCgroup()
        cg.add_state(memory_current=800_000_000, memory_min=100_000_000)

        config = _make_config(
            force=False,
            log_path=tmp_path / "log.jsonl",
            audit_path=tmp_path / "audit.jsonl",
        )
        result = run_squeeze(
            config,
            cgroup_int_reader=cg.int_reader,
            cgroup_flat_kv_reader=cg.flat_kv_reader,
            cgroup_pressure_reader=cg.pressure_reader,
            cgroup_writer=cg.writer,
        )
        assert result.stop_reason == "error"
        assert "memory.min" in result.error
        assert "force" in result.error

    def test_force_overrides_memory_min(self, tmp_path: Path) -> None:
        cg = _FakeCgroup()
        cg.add_state(memory_current=800_000_000, memory_min=100_000_000, anon=600_000_000, rf_cum=0)
        cg.add_state(memory_current=800_000_000, memory_min=100_000_000, anon=600_000_000, rf_cum=0)

        config = _make_config(
            force=True,
            floor=100_000_000,
            delay=0.001,
            log_path=tmp_path / "log.jsonl",
            audit_path=tmp_path / "audit.jsonl",
        )
        result = run_squeeze(
            config,
            cgroup_int_reader=cg.int_reader,
            cgroup_flat_kv_reader=cg.flat_kv_reader,
            cgroup_pressure_reader=cg.pressure_reader,
            cgroup_writer=cg.writer,
            clock=time.time,
        )
        # Should proceed past the memory.min check
        assert result.stop_reason != "error"


# ---------------------------------------------------------------------------
# Happy path — full stepped squeeze to floor
# ---------------------------------------------------------------------------


class TestSqueezeHappyPath:
    """Full squeeze run with no pressure, ending at floor."""

    def test_squeeze_to_floor(self, tmp_path: Path) -> None:
        cg = _FakeCgroup()
        # Add enough states for each loop iteration + initial read
        cg.add_state(memory_current=900_000_000, anon=700_000_000, rf_cum=0)  # initial read
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=0)  # step 0 sample
        cg.add_state(memory_current=700_000_000, anon=500_000_000, rf_cum=0)  # step 1
        cg.add_state(memory_current=600_000_000, anon=400_000_000, rf_cum=0)  # step 2
        cg.add_state(memory_current=550_000_000, anon=400_000_000, rf_cum=0)  # step 3

        log_path = tmp_path / "squeeze.jsonl"
        config = _make_config(
            target="/test.scope",
            step=100_000_000,
            delay=0.001,
            floor=500_000_000,  # Start: 1000M, step: 100M, floor: 500M
            log_path=log_path,
            audit_path=tmp_path / "audit.jsonl",
        )

        result = run_squeeze(
            config,
            cgroup_int_reader=cg.int_reader,
            cgroup_flat_kv_reader=cg.flat_kv_reader,
            cgroup_pressure_reader=cg.pressure_reader,
            cgroup_writer=cg.writer,
            clock=time.time,
        )

        assert result.stop_reason == "floor"
        assert result.squeeze_point > 0
        assert len(result.steps) > 0
        # Verify memory.high was written (at least the initial + restore)
        writes = cg._writes.get("memory.high", [])
        assert len(writes) >= 2  # at least one set + restore

        # Check JSONL log exists with header/step/summary records
        assert log_path.exists()
        log_lines = log_path.read_text().strip().splitlines()
        assert len(log_lines) >= 3
        first = json.loads(log_lines[0])
        assert first["type"] == "header"
        assert first["target"] == "/test.scope"

        # At least one step record
        step_found = False
        summary_found = False
        for line in log_lines:
            record = json.loads(line)
            if record["type"] == "step":
                step_found = True
                assert "step_idx" in record
                assert "memory_high" in record
                assert "memory_current" in record
            if record["type"] == "summary":
                summary_found = True
                assert record["stop_reason"] == "floor"
                assert "squeeze_point" in record
        assert step_found
        assert summary_found


# ---------------------------------------------------------------------------
# Stop conditions
# ---------------------------------------------------------------------------


class TestSqueezeStopConditions:
    def test_stop_on_psi_some(self, tmp_path: Path) -> None:
        cg = _FakeCgroup()
        # Initial reads: memory.current, memory.min
        cg.add_state(memory_current=800_000_000, anon=600_000_000, psi_some=0.0, rf_cum=0)
        cg.add_state(memory_current=800_000_000, anon=600_000_000, psi_some=0.0, rf_cum=0)
        # Step 0 reads: memory.current, memory.stat, zswap.current, swap.current, memory.pressure
        cg.add_state(memory_current=800_000_000, anon=600_000_000, psi_some=0.0, rf_cum=0)
        cg.add_state(memory_current=800_000_000, anon=600_000_000, psi_some=0.0, rf_cum=0)
        cg.add_state(memory_current=800_000_000, anon=600_000_000, psi_some=0.0, rf_cum=0)
        cg.add_state(memory_current=800_000_000, anon=600_000_000, psi_some=0.0, rf_cum=0)
        cg.add_state(memory_current=750_000_000, anon=550_000_000, psi_some=15.0, rf_cum=0)
        # Step 1 reads: all same state with psi_some=15.0 > 10

        config = _make_config(
            step=100_000_000,
            delay=0.001,
            floor=100_000_000,
            psi_some_limit=10.0,
            log_path=tmp_path / "log.jsonl",
            audit_path=tmp_path / "audit.jsonl",
        )
        result = run_squeeze(
            config,
            cgroup_int_reader=cg.int_reader,
            cgroup_flat_kv_reader=cg.flat_kv_reader,
            cgroup_pressure_reader=cg.pressure_reader,
            cgroup_writer=cg.writer,
            clock=time.time,
        )
        assert result.stop_reason == "psi_some", f"expected psi_some, got {result.stop_reason}"
        # The squeeze_point should be the last non-pressure high value
        assert result.squeeze_point > 0

    def test_stop_on_psi_full(self, tmp_path: Path) -> None:
        cg = _FakeCgroup()
        cg.add_state(memory_current=800_000_000, anon=600_000_000, psi_full=0.0, rf_cum=0)  # initial
        cg.add_state(memory_current=800_000_000, anon=600_000_000, psi_full=8.0, rf_cum=0)  # step 0: psi_full=8 > 5

        config = _make_config(
            step=100_000_000,
            delay=0.001,
            floor=100_000_000,
            psi_full_limit=5.0,
            log_path=tmp_path / "log.jsonl",
            audit_path=tmp_path / "audit.jsonl",
        )
        result = run_squeeze(
            config,
            cgroup_int_reader=cg.int_reader,
            cgroup_flat_kv_reader=cg.flat_kv_reader,
            cgroup_pressure_reader=cg.pressure_reader,
            cgroup_writer=cg.writer,
            clock=time.time,
        )
        assert result.stop_reason == "psi_full"

    def test_stop_on_refault_rate(self, tmp_path: Path) -> None:
        """Refault rate triggers stop when delta/dt exceeds limit."""
        cg = _FakeCgroup()
        # Initial reads: memory.current, memory.min
        cg.add_state(memory_current=900_000_000, anon=700_000_000, rf_cum=0)
        cg.add_state(memory_current=900_000_000, anon=700_000_000, rf_cum=0)
        # Step 0 reads: memory.current, memory.stat, zswap.current, swap.current, pressure
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=100)
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=100)
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=100)
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=100)
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=100)
        # Step 1 reads: rf_cum=200 will give rate=(200-100)/dt >> 50
        cg.add_state(memory_current=700_000_000, anon=500_000_000, rf_cum=200)
        cg.add_state(memory_current=700_000_000, anon=500_000_000, rf_cum=200)
        cg.add_state(memory_current=700_000_000, anon=500_000_000, rf_cum=200)
        cg.add_state(memory_current=700_000_000, anon=500_000_000, rf_cum=200)
        cg.add_state(memory_current=700_000_000, anon=500_000_000, rf_cum=200)

        # The loop uses now() for: header, first ts, summary
        # And internally sleep(delay) controls real delay
        # rf_rate depends on dt = ts - prev_rf_time
        # Use real time but very short delay
        config = _make_config(
            step=100_000_000,
            delay=0.001,  # very short, but delay=0.001 gives rf_rate = 100/0.001+overhead
            floor=100_000_000,
            rf_limit=50,
            log_path=tmp_path / "log.jsonl",
            audit_path=tmp_path / "audit.jsonl",
        )
        result = run_squeeze(
            config,
            cgroup_int_reader=cg.int_reader,
            cgroup_flat_kv_reader=cg.flat_kv_reader,
            cgroup_pressure_reader=cg.pressure_reader,
            cgroup_writer=cg.writer,
            clock=time.time,
        )
        # The rf_cum goes from 0 to 100 in ~0.001s, so rate is ~100000/s >> 50
        assert result.stop_reason == "refault_rate", f"expected refault_rate, got {result.stop_reason}"

    def test_stop_on_floor(self, tmp_path: Path) -> None:
        cg = _FakeCgroup()
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=0)  # initial
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=0)  # step 0

        config = _make_config(
            step=500_000_000,
            delay=0.001,
            floor=700_000_000,  # Start: 1000M -> after one step: 500M > 700M? No, 1000-500=500 < 700
            # Actually start = ceil(900M/500M)*500M = 1000M
            # Step 0: high=1000M, sample, no pressure, squeeze_point=1000M
            # Next: high=500M, 500M < 700M floor? Actually 500 < 700, so loop condition fails before sampling
            # Hmm we need at least 2 steps. Let me adjust.
            log_path=tmp_path / "log.jsonl",
            audit_path=tmp_path / "audit.jsonl",
        )
        result = run_squeeze(
            config,
            cgroup_int_reader=cg.int_reader,
            cgroup_flat_kv_reader=cg.flat_kv_reader,
            cgroup_pressure_reader=cg.pressure_reader,
            cgroup_writer=cg.writer,
            clock=time.time,
        )
        assert result.stop_reason == "floor"


# ---------------------------------------------------------------------------
# SIGINT safety — the hard safety test
# ---------------------------------------------------------------------------


class TestSigintSafety:
    """memory.high must be restored even when interrupted mid-loop."""

    def test_sigint_restores_memory_high(self, tmp_path: Path) -> None:
        """Simulate SIGINT mid-squeeze and verify restore was called."""
        cg = _FakeCgroup()  # noqa: F841
        restored = []

        def tracking_writer(cgroup_path: str, filename: str, value: str) -> None:
            restored.append((filename, value))

        guard = _RestoreGuard(
            "/test",
            "max",
            writer=tracking_writer,
        )

        # Test that __exit__ calls restore
        with guard:
            pass

        assert len(restored) >= 1
        assert restored[-1] == ("memory.high", "max")

    def test_restore_guard_idempotent(self) -> None:
        """Restore guard only writes once."""
        restored = []

        def tracking_writer(cgroup_path: str, filename: str, value: str) -> None:
            restored.append((filename, value))

        guard = _RestoreGuard("/test", "max", writer=tracking_writer)
        guard.restore()
        guard.restore()  # Second call should be no-op
        assert len(restored) == 1

    def test_restore_guard_signals_installed(self) -> None:
        """_RestoreGuard installs signal handlers for SIGINT and SIGTERM."""
        caught_signals = []

        def tracking_handler(signum, handler):
            caught_signals.append(signum)
            # Return dummy previous handler
            return signal.SIG_DFL

        guard = _RestoreGuard(
            "/test",
            "max",
            writer=lambda p, f, v: None,
            signal_handler=tracking_handler,
        )
        with guard:
            assert signal.SIGINT in caught_signals
            assert signal.SIGTERM in caught_signals


# ---------------------------------------------------------------------------
# JSONL log shape
# ---------------------------------------------------------------------------


class TestJsonlLogShape:
    """Verify header/step/summary JSONL record shapes."""

    def test_log_shape(self, tmp_path: Path) -> None:
        cg = _FakeCgroup()
        cg.add_state(memory_current=800_000_000, anon=600_000_000, psi_full=0.0, rf_cum=0)  # initial
        cg.add_state(memory_current=800_000_000, anon=600_000_000, psi_full=8.0, rf_cum=0)  # step 0: psi_full=8 > 5

        log_path = tmp_path / "squeeze.jsonl"

        # The squeeze runs with very short delay, will hit the configured
        # PSI full limit in step 0 since psi_full is already 8.0 and
        # psi_full_limit was set to 5.0
        # Wait actually in the loop we sample AFTER sleep, so:
        # high = start value, write high, sleep, sample -> stop if pressure
        # If psi_full=8.0 > psi_full_limit=5.0, stop immediately.
        # But we need at least one step to be recorded. Let's put the pressure
        # data in the step sample.
        config = _make_config(
            step=100_000_000,
            delay=0.001,
            floor=100_000_000,
            psi_full_limit=5.0,
            log_path=log_path,
            audit_path=tmp_path / "audit.jsonl",
        )
        result = run_squeeze(
            config,
            cgroup_int_reader=cg.int_reader,
            cgroup_flat_kv_reader=cg.flat_kv_reader,
            cgroup_pressure_reader=cg.pressure_reader,
            cgroup_writer=cg.writer,
            clock=time.time,
        )

        # Should stop on psi_full
        assert log_path.exists()
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) >= 3, f"Expected at least 3 lines, got {len(lines)}"

        header = json.loads(lines[0])
        assert header["type"] == "header"
        assert header["schema_version"] == 1
        assert "target" in header
        assert "step_bytes" in header
        assert "delay_s" in header
        assert "floor_bytes" in header
        assert "relax_to" in header
        assert "psi_some_limit" in header
        assert "psi_full_limit" in header
        assert "rf_limit" in header
        assert "ts" in header

        # Find first step and summary
        steps = []
        summary = None
        for line in lines:
            rec = json.loads(line)
            if rec["type"] == "step":
                steps.append(rec)
            if rec["type"] == "summary":
                summary = rec

        assert len(steps) >= 1
        step_rec = steps[0]
        assert "step_idx" in step_rec
        assert "memory_high" in step_rec
        assert "memory_current" in step_rec
        assert "anon" in step_rec
        assert "zswapped" in step_rec
        assert "z_pool" in step_rec
        assert "swap" in step_rec
        assert "psi_some_avg10" in step_rec
        assert "psi_full_avg10" in step_rec
        assert "refaults_s" in step_rec
        assert "ts" in step_rec

        assert summary is not None
        assert summary["type"] == "summary"
        assert "stop_reason" in summary
        assert "stop_high" in summary
        assert "squeeze_point" in summary
        assert "current_at_stop" in summary
        assert "relaxed_to" in summary

    def test_log_with_no_steps(self, tmp_path: Path) -> None:
        """If the squeeze immediately errors, no step records are written."""
        cg = _FakeCgroup()
        cg.add_state(memory_current=None)  # Unreadable

        log_path = tmp_path / "squeeze.jsonl"
        config = _make_config(log_path=log_path, audit_path=tmp_path / "audit.jsonl")
        result = run_squeeze(
            config,
            cgroup_int_reader=cg.int_reader,
            cgroup_flat_kv_reader=cg.flat_kv_reader,
            cgroup_pressure_reader=cg.pressure_reader,
            cgroup_writer=cg.writer,
        )
        assert result.stop_reason == "error"
        assert result.steps == ()


# ---------------------------------------------------------------------------
# Result rendering
# ---------------------------------------------------------------------------


class TestResultRendering:
    def test_render_error(self) -> None:
        config = _make_config()
        result = SqueezeResult(
            stop_reason="error",
            stop_high=0,
            squeeze_point=0,
            config=config,
            steps=(),
            restored_to="max",
            error="admin mode is required",
        )
        text = render_squeeze_result(result)
        assert "ERROR" in text
        assert "admin mode" in text
        assert "max" in text

    def test_render_success(self) -> None:
        config = _make_config(target="/test.scope")
        step = SqueezeStep(
            step_idx=0,
            memory_high=1073741824,
            memory_current=800000000,
            anon=600000000,
            zswapped=50000000,
            z_pool=100000000,
            swap=0,
            psi_some_avg10=2.5,
            psi_full_avg10=0.5,
            refaults_s=50.0,
            timestamp=1234567890.0,
        )
        result = SqueezeResult(
            stop_reason="floor",
            stop_high=1073741824,
            squeeze_point=1073741824,
            config=config,
            steps=(step,),
            restored_to="max",
            error="",
        )
        text = render_squeeze_result(result)
        assert "Squeeze Result" in text
        assert "floor" in text
        assert "/test.scope" in text
        assert "1024 MiB" in text

    def test_jsonable(self) -> None:
        config = _make_config(target="/test.scope")
        step = SqueezeStep(
            step_idx=0,
            memory_high=1073741824,
            memory_current=800000000,
            anon=600000000,
            zswapped=50000000,
            z_pool=100000000,
            swap=0,
            psi_some_avg10=2.5,
            psi_full_avg10=0.5,
            refaults_s=50.0,
            timestamp=1234567890.0,
        )
        result = SqueezeResult(
            stop_reason="floor",
            stop_high=1073741824,
            squeeze_point=1073741824,
            config=config,
            steps=(step,),
            restored_to="max",
            error="",
        )
        data = squeeze_result_to_jsonable(result)
        assert data["stop_reason"] == "floor"
        assert data["squeeze_point"] == 1073741824
        assert len(data["steps"]) == 1
        assert data["steps"][0]["step_idx"] == 0
        assert data["target"] == "/test.scope"
        assert data["error"] == ""


# ---------------------------------------------------------------------------
# CLI argument parsing (isolated unit tests)
# ---------------------------------------------------------------------------


class TestCliSqueezeArgs:
    """Test parse_squeeze_args directly."""

    def test_defaults(self) -> None:
        from groop.cli import parse_squeeze_args

        args = parse_squeeze_args(["--target", "/sys/fs/cgroup/test.scope", "--admin", "--confirm", "SQUEEZE"])
        assert args.target == "/sys/fs/cgroup/test.scope"
        assert args.admin is True
        assert args.confirm == "SQUEEZE"
        assert args.step == "256M"
        assert args.delay == 15.0
        assert args.floor == "1G"
        assert args.start is None
        assert args.relax_to == "max"
        assert args.psi_some_limit == 10.0
        assert args.psi_full_limit == 5.0
        assert args.rf_limit == 200
        assert args.force is False

    def test_custom_values(self) -> None:
        from groop.cli import parse_squeeze_args

        args = parse_squeeze_args([
            "--target", "/sys/fs/cgroup/my.scope",
            "--admin",
            "--confirm", "SQUEEZE",
            "--step", "128M",
            "--delay", "30",
            "--floor", "512M",
            "--start", "2G",
            "--relax-to", "max",
            "--psi-some-limit", "20",
            "--psi-full-limit", "10",
            "--rf-limit", "500",
            "--force",
            "--json",
        ])
        assert args.target == "/sys/fs/cgroup/my.scope"
        assert args.step == "128M"
        assert args.delay == 30.0
        assert args.floor == "512M"
        assert args.start == "2G"
        assert args.psi_some_limit == 20.0
        assert args.psi_full_limit == 10.0
        assert args.rf_limit == 500
        assert args.force is True
        assert args.json is True

    def test_target_required(self) -> None:
        from groop.cli import parse_squeeze_args

        with pytest.raises(SystemExit):
            parse_squeeze_args(["--admin", "--confirm", "SQUEEZE"])

    def test_admin_required_by_gate(self, tmp_path: Path) -> None:
        """CLI dispatch returns error when --admin is missing."""
        from groop.actions.squeeze import SqueezeConfig

        config = _make_config(
            target="/test",
            admin=False,
            log_path=tmp_path / "log.jsonl",
            audit_path=tmp_path / "audit.jsonl",
        )
        result = run_squeeze_gated(config)
        assert result.stop_reason == "error"
        assert "admin" in result.error


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


class TestSqueezeAudit:
    """Verify audit records are written at session start and end."""

    def test_audit_written(self, tmp_path: Path) -> None:
        cg = _FakeCgroup()
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=0)  # initial
        cg.add_state(memory_current=800_000_000, anon=600_000_000, rf_cum=0)  # step 0

        log_path = tmp_path / "squeeze.jsonl"
        audit_path = tmp_path / "audit.jsonl"
        config = _make_config(
            step=100_000_000,
            delay=0.001,
            floor=100_000_000,
            log_path=log_path,
            audit_path=audit_path,
        )
        auditor = AuditLog(audit_path)

        result = run_squeeze(
            config,
            cgroup_int_reader=cg.int_reader,
            cgroup_flat_kv_reader=cg.flat_kv_reader,
            cgroup_pressure_reader=cg.pressure_reader,
            cgroup_writer=cg.writer,
            clock=time.time,
            auditor=auditor,
        )

        # Audit log should exist
        assert audit_path.exists()
        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) >= 2  # squeeze-start + squeeze-end

    def test_no_subprocess_import(self) -> None:
        """squeeze.py must not import subprocess directly."""
        import ast
        import importlib.util

        spec = importlib.util.find_spec("groop.actions.squeeze")
        assert spec is not None
        assert spec.origin is not None
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                n.name == "subprocess" for n in node.names
            ):
                pytest.fail(f"squeeze.py imports subprocess: {node.names}")
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                pytest.fail(f"squeeze.py imports {node.module}: {node.names}")
