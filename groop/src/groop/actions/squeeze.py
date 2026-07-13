"""Guided stepped memory.high working-set measurement (``groop squeeze``).

Implements the P56 ``groop squeeze`` command: a timed, multi-step
``memory.high`` squeeze that measures a cgroup's real (hot+warm) working set
under pressure, absorbing the workflow of
``scripts/gstammtisch-guide/files/usr/local/sbin/container-mempress.sh`` into
groop natively.

The squeeze writes ``memory.high`` direct to cgroupfs (not via ``systemctl
set-property``), gated by the same root/``--admin``/typed-confirmation/audit
posture the P46 action execution kernel established.  ``memory.high`` is always
restored on exit, including SIGINT/SIGTERM — a hard safety requirement.

Audit is per-session (start + end), not per-step, to keep the audit log
proportionate.  The per-step JSONL log is compatible with the P2 record/writer
schema (header+frame JSONL convention) so ``groop report`` (P54) or a future
replay path can consume it.
"""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path

from groop.actions.audit import AuditLog


# ---------------------------------------------------------------------------
# Size parsing
# ---------------------------------------------------------------------------


def parse_size(text: str) -> int:
    """Parse a human-readable size string to bytes.

    Accepts suffixes ``G``/``g``, ``M``/``m``, ``K``/``k``, or a bare integer.

    Raises ``ValueError`` on unparseable input.
    """
    if not isinstance(text, str) or not text:
        raise ValueError(f"size must be a non-empty string, got {text!r}")
    suffix = text[-1].lower()
    if suffix in ("g", "m", "k"):
        numeric = text[:-1]
        if not numeric or not numeric.lstrip("-").isdigit():
            raise ValueError(f"cannot parse size: {text!r}")
        value = int(numeric)
        if suffix == "g":
            return value * 1024 * 1024 * 1024
        if suffix == "m":
            return value * 1024 * 1024
        return value * 1024  # k
    # Bare integer
    if text.lstrip("-").isdigit():
        return int(text)
    if text == "max":
        raise ValueError("'max' is not a valid squeeze size parameter; use e.g. --relax-to max")
    raise ValueError(f"cannot parse size: {text!r}")


# ---------------------------------------------------------------------------
# Default cgroup readers/writers (production implementations)
# ---------------------------------------------------------------------------


def _default_cgroup_int_reader(cgroup_path: str, filename: str) -> int | None:
    """Read an integer value from *filename* under *cgroup_path*."""
    from groop.collect.cgroup import read_int

    value, _src = read_int(Path(cgroup_path) / filename)
    return value


def _default_cgroup_flat_kv_reader(cgroup_path: str, filename: str) -> dict[str, int]:
    """Read a flat key-value file from *filename* under *cgroup_path*."""
    from groop.collect.cgroup import read_flat_kv

    data, _src = read_flat_kv(Path(cgroup_path) / filename)
    return data


def _default_cgroup_pressure_reader(cgroup_path: str, filename: str) -> dict[str, dict[str, float]]:
    """Read a PSI pressure file from *filename* under *cgroup_path*."""
    from groop.collect.cgroup import read_pressure

    data, _src = read_pressure(Path(cgroup_path) / filename)
    return data


def _default_cgroup_writer(cgroup_path: str, filename: str, value: str) -> None:
    """Write *value* to *filename* under *cgroup_path*.

    Used for ``memory.high`` writes. In production this requires root.
    """
    target = Path(cgroup_path) / filename
    target.write_text(value)


# ---------------------------------------------------------------------------
# Squeeze runner
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SqueezeConfig:
    """Configuration for one squeeze run."""

    target: str
    """Cgroup path (absolute)."""

    step: int
    """High-to-high step size in bytes."""

    delay: float
    """Sleep between steps in seconds."""

    floor: int
    """Never set memory.high below this value."""

    start: int | None
    """Initial memory.high value. None = auto (current rounded up to step)."""

    relax_to: str
    """Value to restore memory.high to on exit. ``"max"`` or a byte count as str."""

    psi_some_limit: float
    """Stop when memory PSI some avg10 exceeds this percent."""

    psi_full_limit: float
    """Stop when memory PSI full avg10 exceeds this percent."""

    rf_limit: int
    """Stop when anon refaults/s exceeds this value."""

    force: bool
    """Allow target with memory.min > 0."""

    log_path: Path
    """Path for the per-step JSONL log."""

    audit_path: Path
    """Path for admin action audit."""

    admin: bool
    """Whether admin mode is enabled."""

    confirm: str
    """Typed confirmation value."""


@dataclasses.dataclass(frozen=True)
class SqueezeStep:
    """One squeeze step sample."""

    step_idx: int
    memory_high: int
    memory_current: int | None
    anon: int | None
    zswapped: int | None
    z_pool: int | None
    swap: int | None
    psi_some_avg10: float | None
    psi_full_avg10: float | None
    refaults_s: float | None
    timestamp: float


@dataclasses.dataclass(frozen=True)
class SqueezeResult:
    """Typed result of a completed squeeze run."""

    stop_reason: str
    """Why the squeeze stopped: ``"psi_some"``, ``"psi_full"``, ``"refault_rate"``,
    ``"floor"``, or ``"error"``."""

    stop_high: int
    """The ``memory.high`` value that triggered the stop (the value that showed
    pressure)."""

    squeeze_point: int
    """The last ``memory.high`` value that showed NO pressure signal — the
    estimated hot+warm working set."""

    config: SqueezeConfig
    """The configuration used for this run."""

    steps: tuple[SqueezeStep, ...]
    """All recorded steps."""

    restored_to: str
    """The value ``memory.high`` was restored to."""

    error: str
    """Error message if stop_reason is ``"error"``."""


# ---------------------------------------------------------------------------
# Signal-registration seam
# ---------------------------------------------------------------------------


class _RestoreGuard:
    """Context manager that restores ``memory.high`` on exit and on signal.

    This is the hard-safety mechanism: no code path may leave a lowered
    ``memory.high`` in place.  The signal handler is injectable so tests
    do not depend on real OS signal delivery.
    """

    def __init__(
        self,
        cgroup_path: str,
        relax_to: str,
        *,
        writer: Callable[[str, str], None] | None = None,
        signal_handler: Callable[[int, Callable[..., object]], object] | None = None,
    ) -> None:
        self._cgroup_path = cgroup_path
        self._relax_to = relax_to
        self._writer = writer or _default_cgroup_writer
        self._restored = False
        self._signal_handler = signal_handler or signal.signal
        self._orig_sigint: object = None
        self._orig_sigterm: object = None

    def __enter__(self) -> _RestoreGuard:
        def _restore_on_signal(signum: int, frame: object) -> None:
            self.restore()
            # Re-raise the signal with the original handler
            if signum == signal.SIGINT:
                raise KeyboardInterrupt
            # For SIGTERM, just restore and let the process exit naturally
            os._exit(128 + signum)  # noqa: SLF001

        self._orig_sigint = self._signal_handler(signal.SIGINT, _restore_on_signal)
        self._orig_sigterm = self._signal_handler(signal.SIGTERM, _restore_on_signal)
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.restore()
        # Restore original signal handlers
        if self._orig_sigint is not None:
            self._signal_handler(signal.SIGINT, self._orig_sigint)
        if self._orig_sigterm is not None:
            self._signal_handler(signal.SIGTERM, self._orig_sigterm)

    def restore(self) -> None:
        if self._restored:
            return
        try:
            self._writer(self._cgroup_path, "memory.high", self._relax_to)
        except OSError:
            pass  # Best-effort restore; nothing else we can do
        self._restored = True


# ---------------------------------------------------------------------------
# Main squeeze function
# ---------------------------------------------------------------------------


def run_squeeze(
    config: SqueezeConfig,
    *,
    # Injectable test seams
    cgroup_int_reader: Callable[[str, str], int | None] | None = None,
    cgroup_flat_kv_reader: Callable[[str, str], dict[str, int]] | None = None,
    cgroup_pressure_reader: Callable[[str, str], dict[str, dict[str, float]]] | None = None,
    cgroup_writer: Callable[[str, str, str], None] | None = None,
    clock: Callable[[], float] | None = None,
    auditor: AuditLog | None = None,
) -> SqueezeResult:
    """Run a guided ``memory.high`` squeeze.

    Args:
        config: Squeeze configuration (target, step sizes, limits, etc.).
        cgroup_int_reader: Injectable integer cgroup reader.
        cgroup_flat_kv_reader: Injectable flat-key-value cgroup reader.
        cgroup_pressure_reader: Injectable PSI pressure cgroup reader.
        cgroup_writer: Injectable cgroup writer.
        clock: Injectable clock (default ``time.time``).
        auditor: Injectable audit log (default ``AuditLog(config.audit_path)``).

    Returns:
        A ``SqueezeResult`` describing the outcome.
    """
    now = clock or time.time
    int_reader = cgroup_int_reader or _default_cgroup_int_reader
    flat_kv_reader = cgroup_flat_kv_reader or _default_cgroup_flat_kv_reader
    pressure_reader = cgroup_pressure_reader or _default_cgroup_pressure_reader
    writer = cgroup_writer or _default_cgroup_writer
    audit = auditor if auditor is not None else AuditLog(config.audit_path)

    target = config.target
    relax_to = config.relax_to
    step_bytes = config.step
    delay = config.delay
    floor_bytes = config.floor

    # -- Audit session start
    try:
        audit.record(
            kind="squeeze",
            target=target,
            argv=(
                "--target", target,
                "--admin" if config.admin else "",
                "--confirm", config.confirm,
            ),
            admin=config.admin,
        )
    except BaseException:
        pass  # Audit failure is non-fatal for the measurement session

    # -- Read current memory.current and memory.min
    current_val = int_reader(target, "memory.current")
    if current_val is None or current_val < 0:
        return _result_error(config, "cannot read memory.current", restored_to=relax_to)
    min_val = int_reader(target, "memory.min")
    if min_val is not None and min_val > 0 and not config.force:
        return _result_error(
            config,
            f"target has memory.min={min_val} (protected/prod workload?) — "
            f"use --force to override",
            restored_to=relax_to,
        )

    # -- Determine start high
    if config.start is not None:
        high = config.start
    else:
        # Round up current usage to next step boundary
        high = ((current_val // step_bytes) + 1) * step_bytes

    # -- Build the restore guard (hard safety: always restore on exit)
    restore_guard = _RestoreGuard(
        target,
        relax_to,
        writer=writer,
    )

    # -- Open the JSONL log
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_fh = config.log_path.open("w", encoding="utf-8")
    except OSError as exc:
        return _result_error(
            config,
            f"cannot open log file {config.log_path}: {exc}",
            restored_to=relax_to,
        )

    steps: list[SqueezeStep] = []
    stop_reason = "floor"
    squeeze_point = high
    prev_rf: int | None = None
    prev_rf_time: float | None = None

    # -- Write header record
    try:
        _write_log_header(log_fh, config, current_val, min_val, now())
    except OSError:
        log_fh.close()
        return _result_error(config, "log write failed", restored_to=relax_to)

    # -- Enter the restore guard (installs signal handlers + restore on exit)
    with restore_guard:
        try:
            # Set initial memory.high
            writer(target, "memory.high", str(high))

            while high >= floor_bytes:
                # Sleep for the delay interval
                time.sleep(delay)

                # -- Sample current state
                cur = int_reader(target, "memory.current")
                stats = flat_kv_reader(target, "memory.stat")
                anon = stats.get("anon")
                zswapped = stats.get("zswapped")
                pool = int_reader(target, "memory.zswap.current")
                swap = int_reader(target, "memory.swap.current")
                pressure = pressure_reader(target, "memory.pressure")
                psi_some = pressure.get("some", {}).get("avg10")
                psi_full = pressure.get("full", {}).get("avg10")
                rf_cum = stats.get("workingset_refault_anon")
                ts = now()

                # Derive refault rate
                rf_rate: float | None = None
                if rf_cum is not None and prev_rf is not None and prev_rf_time is not None:
                    dt = ts - prev_rf_time
                    if dt > 0:
                        rf_rate = (rf_cum - prev_rf) / dt
                prev_rf = rf_cum
                prev_rf_time = ts

                # Build step record
                step = SqueezeStep(
                    step_idx=len(steps),
                    memory_high=high,
                    memory_current=cur,
                    anon=anon,
                    zswapped=zswapped,
                    z_pool=pool,
                    swap=swap,
                    psi_some_avg10=psi_some,
                    psi_full_avg10=psi_full,
                    refaults_s=rf_rate,
                    timestamp=ts,
                )
                steps.append(step)

                # Write step to log
                try:
                    _write_log_step(log_fh, step, rf_rate)
                except OSError:
                    pass  # Non-fatal; continue sampling

                # -- Check stop conditions
                psi_some_val = psi_some if psi_some is not None else 0.0
                psi_full_val = psi_full if psi_full is not None else 0.0
                rf_rate_val = rf_rate if rf_rate is not None else 0.0

                if psi_some_val > config.psi_some_limit:
                    stop_reason = "psi_some"
                    break
                if psi_full_val > config.psi_full_limit:
                    stop_reason = "psi_full"
                    break
                if rf_rate_val > config.rf_limit:
                    stop_reason = "refault_rate"
                    break

                # Record the last non-pressure value as the squeeze point
                squeeze_point = high

                # Step down
                high -= step_bytes

            else:
                # Loop ended because high < floor; the last attempt would be
                # below floor, so squeeze_point is the last good value.
                # If we never entered the loop, high was already below floor.
                if not steps:
                    stop_reason = "floor"
                else:
                    # The loop exited normally after high went below floor;
                    # the squeeze point is the last non-pressure value.
                    stop_reason = "floor"

            # -- Restore memory.high (handled by restore_guard.__exit__)

        except KeyboardInterrupt:
            stop_reason = "interrupted"
            # Restore is handled by the restore guard
            if steps:
                # Keep the last good squeeze point
                pass
        except BaseException as exc:
            stop_reason = "error"
            log_fh.close()
            return _result_error(
                config,
                str(exc),
                restored_to=relax_to,
            )

        finally:
            # -- Write summary record to log
            try:
                stop_high = steps[-1].memory_high if steps else high
                final_cur = steps[-1].memory_current if steps else current_val
                final_anon = steps[-1].anon if steps else None
                final_zswapped = steps[-1].zswapped if steps else None
                final_pool = steps[-1].z_pool if steps else None
                _write_log_summary(
                    log_fh,
                    stop_reason=stop_reason,
                    stop_high=stop_high,
                    squeeze_point=squeeze_point,
                    current_at_stop=final_cur,
                    anon_at_stop=final_anon,
                    zswapped_at_stop=final_zswapped,
                    z_pool_at_stop=final_pool,
                    relaxed_to=relax_to,
                    timestamp=now(),
                )
            except OSError:
                pass
            log_fh.close()

    # -- Audit session end
    try:
        audit.record(
            kind="squeeze-end",
            target=target,
            argv=(
                "stop_reason", stop_reason,
                "squeeze_point", str(squeeze_point),
                "restored_to", relax_to,
            ),
            admin=config.admin,
        )
    except BaseException:
        pass

    return SqueezeResult(
        stop_reason=stop_reason,
        stop_high=steps[-1].memory_high if steps else high,
        squeeze_point=squeeze_point,
        config=config,
        steps=tuple(steps),
        restored_to=relax_to,
        error="",
    )


# ---------------------------------------------------------------------------
# Error result factory
# ---------------------------------------------------------------------------


def _result_error(
    config: SqueezeConfig,
    message: str,
    *,
    restored_to: str = "max",
) -> SqueezeResult:
    return SqueezeResult(
        stop_reason="error",
        stop_high=0,
        squeeze_point=0,
        config=config,
        steps=(),
        restored_to=restored_to,
        error=message,
    )


# ---------------------------------------------------------------------------
# JSONL log helpers — schema-compatible with P2 record/writer header+frame
# convention so ``groop report`` (P54) or a future replay path can consume it.
# ---------------------------------------------------------------------------


def _write_log_header(
    fh: object,
    config: SqueezeConfig,
    current_val: int | None,
    min_val: int | None,
    ts: float,
) -> None:
    """Write the header JSONL record."""
    record = {
        "type": "header",
        "schema_version": 1,
        "ts": ts,
        "target": config.target,
        "step_bytes": config.step,
        "delay_s": config.delay,
        "floor_bytes": config.floor,
        "start_bytes": config.start,
        "relax_to": config.relax_to,
        "psi_some_limit": config.psi_some_limit,
        "psi_full_limit": config.psi_full_limit,
        "rf_limit": config.rf_limit,
        "force": config.force,
        "memory_current_bytes": current_val,
        "memory_min_bytes": min_val,
    }
    fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
    fh.write("\n")
    fh.flush()


def _write_log_step(
    fh: object,
    step: SqueezeStep,
    rf_rate: float | None,
) -> None:
    """Write one step record to the JSONL log."""
    record = {
        "type": "step",
        "step_idx": step.step_idx,
        "ts": step.timestamp,
        "memory_high": step.memory_high,
        "memory_current": step.memory_current,
        "anon": step.anon,
        "zswapped": step.zswapped,
        "z_pool": step.z_pool,
        "swap": step.swap,
        "psi_some_avg10": (
            round(step.psi_some_avg10, 4) if step.psi_some_avg10 is not None else None
        ),
        "psi_full_avg10": (
            round(step.psi_full_avg10, 4) if step.psi_full_avg10 is not None else None
        ),
        "refaults_s": rf_rate,
    }
    fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
    fh.write("\n")
    fh.flush()


def _write_log_summary(
    fh: object,
    *,
    stop_reason: str,
    stop_high: int,
    squeeze_point: int,
    current_at_stop: int | None,
    anon_at_stop: int | None,
    zswapped_at_stop: int | None,
    z_pool_at_stop: int | None,
    relaxed_to: str,
    timestamp: float,
) -> None:
    """Write the summary JSONL record."""
    record = {
        "type": "summary",
        "ts": timestamp,
        "stop_reason": stop_reason,
        "stop_high": stop_high,
        "squeeze_point": squeeze_point,
        "current_at_stop": current_at_stop,
        "anon_at_stop": anon_at_stop,
        "zswapped_at_stop": zswapped_at_stop,
        "z_pool_at_stop": z_pool_at_stop,
        "relaxed_to": relaxed_to,
    }
    fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
    fh.write("\n")
    fh.flush()


# ---------------------------------------------------------------------------
# Result rendering
# ---------------------------------------------------------------------------


def render_squeeze_result(result: SqueezeResult) -> str:
    """Render a human-readable summary of a squeeze result."""
    if result.stop_reason == "error":
        return f"SQUEEZE ERROR: {result.error}\nmemory.high restored to {result.restored_to}"

    lines = [
        "=== Squeeze Result ===",
        f"Target: {result.config.target}",
        f"Stop reason: {result.stop_reason}",
        f"Stop memory.high: {result.stop_high} ({_mib(result.stop_high)} MiB)",
        f"Squeeze point (hot+warm set): {result.squeeze_point} ({_mib(result.squeeze_point)} MiB)",
        f"Steps recorded: {len(result.steps)}",
        f"Restored memory.high to: {result.restored_to}",
        "",
        "Per-step data and header/summary in:",
        f"  {result.config.log_path}",
    ]
    if result.steps:
        lines.append("")
        lines.append("Step summary:")
        for step in result.steps:
            rf = f"{step.refaults_s:.1f}/s" if step.refaults_s is not None else "N/A"
            psi_s = f"{step.psi_some_avg10:.1f}%" if step.psi_some_avg10 is not None else "N/A"
            psi_f = f"{step.psi_full_avg10:.1f}%" if step.psi_full_avg10 is not None else "N/A"
            lines.append(
                f"  step {step.step_idx}: high={_mib(step.memory_high)}M "
                f"current={_mib(step.memory_current) if step.memory_current else 'N/A'}M "
                f"rf={rf} psi={psi_s}/{psi_f}"
            )
    return "\n".join(lines)


def squeeze_result_to_jsonable(result: SqueezeResult) -> dict[str, object]:
    """Convert a SqueezeResult to JSON-safe data."""
    return {
        "stop_reason": result.stop_reason,
        "stop_high": result.stop_high,
        "squeeze_point": result.squeeze_point,
        "steps": [
            {
                "step_idx": s.step_idx,
                "memory_high": s.memory_high,
                "memory_current": s.memory_current,
                "anon": s.anon,
                "zswapped": s.zswapped,
                "z_pool": s.z_pool,
                "swap": s.swap,
                "psi_some_avg10": s.psi_some_avg10,
                "psi_full_avg10": s.psi_full_avg10,
                "refaults_s": s.refaults_s,
                "ts": s.timestamp,
            }
            for s in result.steps
        ],
        "target": result.config.target,
        "step_bytes": result.config.step,
        "delay_s": result.config.delay,
        "floor_bytes": result.config.floor,
        "relax_to": result.config.relax_to,
        "restored_to": result.restored_to,
        "error": result.error,
    }


def _mib(value: int | None) -> str:
    """Format a byte value as mebibytes."""
    if value is None:
        return "N/A"
    return str(value // (1024 * 1024))


# ---------------------------------------------------------------------------
# CLI-facing entry point for the gate chain
# ---------------------------------------------------------------------------


def run_squeeze_gated(
    config: SqueezeConfig,
    *,
    root_check: Callable[[], bool] | None = None,
    **kwargs: object,
) -> SqueezeResult:
    """Run squeeze through the P46-style gate chain (root/admin/confirm).

    This is the entry point called by the CLI.  Injectable test seams
    (cgroup readers/writers, clock, auditor) are passed through to
    ``run_squeeze``.
    """
    # -- Gates (same pattern as P46 execute_set_property)
    if not config.admin:
        return _result_error(config, "admin mode is required (--admin)")
    if config.confirm != "SQUEEZE":
        return _result_error(
            config, "exact confirmation SQUEEZE is required (--confirm SQUEEZE)"
        )
    try:
        is_root = root_check() if root_check is not None else os.geteuid() == 0
    except BaseException:
        is_root = False
    if is_root is not True:
        return _result_error(config, "root privileges are required")

    return run_squeeze(config, **kwargs)
