"""Guided stepped memory.high working-set measurement (``topos squeeze``).

Implements the P56 ``topos squeeze`` command: a timed, multi-step
``memory.high`` squeeze that measures a cgroup's real (hot+warm) working set
under pressure, absorbing the workflow of
``scripts/gstammtisch-guide/files/usr/local/sbin/container-mempress.sh`` into
topos natively.

The squeeze writes ``memory.high`` direct to cgroupfs (not via ``systemctl
set-property``), gated by the same root/``--admin``/typed-confirmation/audit
posture the P46 action execution kernel established.  ``memory.high`` is always
restored on exit, including SIGINT/SIGTERM — a hard safety requirement.

Audit is per-session (start + end), not per-step, to keep the audit log
proportionate.  The per-step JSONL log is compatible with the P2 record/writer
schema (header+frame JSONL convention) so ``topos report`` (P54) or a future
replay path can consume it.
"""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path

from topos.actions.audit import AuditLog


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
    from topos.collect.cgroup import read_int

    value, _src = read_int(Path(cgroup_path) / filename)
    return value


def _default_cgroup_flat_kv_reader(cgroup_path: str, filename: str) -> dict[str, int]:
    """Read a flat key-value file from *filename* under *cgroup_path*."""
    from topos.collect.cgroup import read_flat_kv

    data, _src = read_flat_kv(Path(cgroup_path) / filename)
    return data


def _default_cgroup_pressure_reader(cgroup_path: str, filename: str) -> dict[str, dict[str, float]]:
    """Read a PSI pressure file from *filename* under *cgroup_path*."""
    from topos.collect.cgroup import read_pressure

    data, _src = read_pressure(Path(cgroup_path) / filename)
    return data


def _default_cgroup_writer(cgroup_path: str, filename: str, value: str) -> None:
    """Write *value* to an EXISTING *filename* under *cgroup_path*.

    Used for ``memory.high`` writes. In production this requires root.  The
    file is opened without ``O_CREAT`` and without following a final symlink,
    so a wrong target can never create a regular file.
    """
    fd = os.open(
        os.path.join(cgroup_path, filename), os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        os.write(fd, value.encode("ascii"))
    finally:
        os.close(fd)


def _default_cgroup_text_reader(cgroup_path: str, filename: str) -> str | None:
    """Read the raw stripped text of a cgroup file, or None if unreadable."""
    try:
        with open(os.path.join(cgroup_path, filename), encoding="ascii") as fh:
            return fh.read().strip()
    except (OSError, ValueError):
        return None


DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"
DEFAULT_RECOVERY_DIR = Path("/run/topos/squeeze")


def validate_relax_to(value: str) -> str | None:
    """Return an error message when *value* is not ``max`` or a positive size."""
    if value == "max":
        return None
    try:
        size = parse_size(value)
    except ValueError as exc:
        return f"invalid --relax-to: {exc}"
    if size <= 0:
        return f"invalid --relax-to: must be 'max' or a positive size, got {value!r}"
    return None


def validate_config(config: "SqueezeConfig") -> str | None:
    """Return an error message when the numeric parameters are unsafe."""
    if config.step <= 0:
        return f"step must be positive, got {config.step}"
    if config.floor < 0:
        return f"floor must not be negative, got {config.floor}"
    if config.start is not None and config.start < config.floor:
        return f"start ({config.start}) must not be below floor ({config.floor})"
    return validate_relax_to(config.relax_to)


def check_confinement(target: str, cgroup_root: str = DEFAULT_CGROUP_ROOT) -> str | None:
    """C-06: target must resolve under the cgroup root and own a real memory.high."""
    try:
        root = os.path.realpath(cgroup_root)
        resolved = os.path.realpath(target)
    except (OSError, ValueError) as exc:
        return f"cannot resolve target: {exc}"
    if not resolved.startswith(root.rstrip(os.sep) + os.sep):
        return f"target {target!r} is not under the cgroup root {root}"
    try:
        mode = os.lstat(os.path.join(resolved, "memory.high")).st_mode
    except OSError:
        return f"target {target!r} has no memory.high file"
    if not stat.S_ISREG(mode):
        return f"{target!r}/memory.high is not a regular file"
    return None


# ---------------------------------------------------------------------------
# Recovery file (C-03)
# ---------------------------------------------------------------------------


def recovery_path_for(target: str, recovery_dir: Path = DEFAULT_RECOVERY_DIR) -> Path:
    leaf = os.path.realpath(target).strip(os.sep).replace(os.sep, "%") or "root"
    return recovery_dir / f"{leaf}.json"


def _write_recovery(path: Path, *, target: str, original: str | None, relax_to: str) -> None:
    """Durably write the recovery record; refuse if one already exists."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        raise OSError(
            f"recovery file {path} already exists (a previous squeeze did not "
            "finish); run 'topos squeeze --recover' first"
        )
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(
        {"target": target, "original": original, "relax_to": relax_to}, sort_keys=True
    )
    try:
        tmp.unlink()  # stale temp of a crashed run with the same pid
    except OSError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        # link() fails if the record exists: atomic create-or-refuse.
        os.link(tmp, path)
    except FileExistsError:
        raise OSError(
            f"recovery file {path} already exists (a previous or concurrent "
            "squeeze did not finish); run 'topos squeeze --recover' first"
        ) from None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


@dataclasses.dataclass(frozen=True)
class RecoverOutcome:
    path: Path
    target: str
    value: str
    ok: bool
    error: str = ""


def recover_squeeze(
    target: str | None = None,
    *,
    recovery_dir: Path = DEFAULT_RECOVERY_DIR,
    cgroup_root: str | None = DEFAULT_CGROUP_ROOT,
    writer: Callable[[str, str, str], None] | None = None,
) -> list[RecoverOutcome]:
    """Restore memory.high from leftover recovery files (``--recover``).

    Restores the recorded pre-squeeze value (``relax_to`` when the original was
    unreadable) and removes the file only after a successful write.
    """
    write = writer or _default_cgroup_writer
    if target is not None:
        paths = [recovery_path_for(target, recovery_dir)]
        paths = [p for p in paths if p.exists()]
    else:
        paths = sorted(recovery_dir.glob("*.json")) if recovery_dir.is_dir() else []
    outcomes: list[RecoverOutcome] = []
    for path in paths:
        try:
            data = json.loads(path.read_text())
            tgt = data["target"]
            value = data.get("original") or data["relax_to"]
            if not isinstance(tgt, str) or not isinstance(value, str):
                raise ValueError("malformed recovery record")
            bad_value = validate_relax_to(value)
            if bad_value is not None:
                raise ValueError(f"malformed recovery value: {bad_value}")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            outcomes.append(RecoverOutcome(path, "?", "?", False, f"unreadable: {exc}"))
            continue
        if cgroup_root is not None:
            problem = check_confinement(tgt, cgroup_root)
            if problem:
                outcomes.append(RecoverOutcome(path, tgt, value, False, problem))
                continue
        try:
            write(tgt, "memory.high", value)
        except OSError as exc:
            outcomes.append(RecoverOutcome(path, tgt, value, False, str(exc)))
            continue
        try:
            path.unlink()
        except OSError:
            pass
        outcomes.append(RecoverOutcome(path, tgt, value, True))
    return outcomes


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

    recovery_path: Path | None = None
    """Recovery record path. None = default under /run/topos/squeeze when the
    production cgroup writer is used, none when a writer is injected."""

    relax_explicit: bool = True
    """False when ``relax_to`` is only the default: the pre-squeeze memory.high
    (when readable and valid) is restored instead."""

    cgroup_root: str = DEFAULT_CGROUP_ROOT
    """Target must resolve under this root (checked with the production writer)."""


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
    """Error message if stop_reason is ``"error"`` or ``"restore_failed"``."""

    exit_signal: int = 0
    """Signal number when the run was ended by SIGHUP/SIGQUIT/SIGPIPE/SIGTERM."""


# ---------------------------------------------------------------------------
# Signal-registration seam
# ---------------------------------------------------------------------------


class _Terminated(BaseException):
    """Raised from the signal handler (after restoring) to unwind run_squeeze."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


_GUARDED_SIGNALS = tuple(
    getattr(signal, name)
    for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT", "SIGPIPE")
    if hasattr(signal, name)
)


class _RestoreGuard:
    """Context manager that restores ``memory.high`` on exit and on signal.

    Covers normal exit, exceptions and SIGINT/SIGTERM/SIGHUP/SIGQUIT/SIGPIPE
    (SIGKILL and a host crash are covered by the recovery file, ``topos
    squeeze --recover``).  The signal handler is injectable so tests do not
    depend on real OS signal delivery.

    A restore is retried once; if it still fails ``restore_failed`` is set and
    it is never reported as restored.  The recovery file is removed only after
    a successful restore.
    """

    def __init__(
        self,
        cgroup_path: str,
        relax_to: str,
        *,
        writer: Callable[[str, str, str], None] | None = None,
        signal_handler: Callable[[int, Callable[..., object]], object] | None = None,
        recovery_path: Path | None = None,
    ) -> None:
        self._cgroup_path = cgroup_path
        self._relax_to = relax_to
        self._writer = writer or _default_cgroup_writer
        self._attempted = False
        self.restore_failed = False
        self.restore_error = ""
        self._recovery_path = recovery_path
        self._signal_handler = signal_handler or signal.signal
        self._orig_handlers: dict[int, object] = {}

    def __enter__(self) -> _RestoreGuard:
        def _restore_on_signal(signum: int, frame: object) -> None:
            self.restore()
            if signum == signal.SIGINT:
                raise KeyboardInterrupt
            # Unwind through run_squeeze so the log summary and the end audit
            # record are written (no os._exit).
            raise _Terminated(signum)

        for signum in _GUARDED_SIGNALS:
            self._orig_handlers[signum] = self._signal_handler(signum, _restore_on_signal)
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.restore()
        for signum, original in self._orig_handlers.items():
            if original is not None:
                self._signal_handler(signum, original)

    def restore(self) -> None:
        if self._attempted:
            return
        self._attempted = True
        last: OSError | None = None
        # G-04: a second signal must not abort the restore write; block the
        # guarded signals while it runs (they are delivered afterwards).
        try:
            old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _GUARDED_SIGNALS)
        except (AttributeError, OSError, ValueError):
            old_mask = None
        try:
            failures = 0
            interruptions = 0
            while failures < 2 and interruptions < 10:  # one retry on real errors
                try:
                    self._writer(self._cgroup_path, "memory.high", self._relax_to)
                    last = None
                    break
                except InterruptedError as exc:
                    last = exc
                    interruptions += 1
                except OSError as exc:
                    last = exc
                    failures += 1
            # All bookkeeping happens before the unmask: a signal that arrived
            # during the write fires at the unmask and unwinds from there.
            if last is not None:
                self.restore_failed = True
                self.restore_error = str(last)
            elif self._recovery_path is not None:
                try:
                    self._recovery_path.unlink()
                except OSError:
                    pass
        finally:
            if old_mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)


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
    cgroup_text_reader: Callable[[str, str], str | None] | None = None,
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
    text_reader = cgroup_text_reader or _default_cgroup_text_reader
    audit = auditor if auditor is not None else AuditLog(config.audit_path)

    target = config.target
    relax_to = config.relax_to
    step_bytes = config.step
    delay = config.delay
    floor_bytes = config.floor

    # -- Validate before anything is written (C-02, C-04, C-06)
    invalid = validate_config(config)
    if invalid is not None:
        return _result_error(config, invalid, restored_to="")
    if cgroup_writer is None:
        problem = check_confinement(target, config.cgroup_root)
        if problem is not None:
            return _result_error(config, problem, restored_to="")
    recovery_path = config.recovery_path
    if recovery_path is None and cgroup_writer is None:
        recovery_path = recovery_path_for(target)

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
    except Exception:
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
    # C-04: never write below the floor, not even the first value.
    high = max(high, floor_bytes)

    # -- Build the restore guard (hard safety: always restore on exit)
    last_written: list[str] = []

    def tracked_writer(path: str, filename: str, value: str) -> None:
        writer(path, filename, value)
        if filename == "memory.high":
            last_written.append(value)

    original_high = text_reader(target, "memory.high")
    if (
        not config.relax_explicit
        and original_high is not None
        and validate_relax_to(original_high) is None
    ):
        # G-05: a clean exit restores the pre-squeeze value, like --recover.
        relax_to = original_high
    restore_guard = _RestoreGuard(
        target,
        relax_to,
        writer=tracked_writer,
        recovery_path=recovery_path,
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

    # -- C-03: durable recovery record BEFORE the first memory.high write
    if recovery_path is not None:
        try:
            _write_recovery(
                recovery_path,
                target=target,
                original=original_high,
                relax_to=relax_to,
            )
        except OSError as exc:
            log_fh.close()
            return _result_error(
                config, f"cannot write recovery file: {exc}", restored_to=""
            )

    terminated_by = 0
    error_message = ""

    # -- Enter the restore guard (installs signal handlers + restore on exit)
    with restore_guard:
        try:
            # Set initial memory.high
            tracked_writer(target, "memory.high", str(high))

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
                if high >= floor_bytes:
                    tracked_writer(target, "memory.high", str(high))

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
        except _Terminated as term:
            stop_reason = "terminated"
            terminated_by = term.signum
        except Exception as exc:
            stop_reason = "error"
            error_message = str(exc)

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

    restore_failed = restore_guard.restore_failed
    stuck = last_written[-1] if last_written else "unknown"
    if restore_failed:
        failure = (
            f"memory.high restore to {relax_to} FAILED ({restore_guard.restore_error}); "
            f"memory.high is still {stuck} on {target}. Recover with: "
            f"topos squeeze --recover --target {target}"
        )
        print(f"topos squeeze: {failure}", file=sys.stderr)

    # -- Audit session end
    try:
        audit.record(
            kind="squeeze-end",
            target=target,
            argv=(
                "stop_reason", "restore_failed" if restore_failed else stop_reason,
                "squeeze_point", str(squeeze_point),
                "restored_to", "" if restore_failed else relax_to,
                *(("signal", str(terminated_by)) if terminated_by else ()),
            ),
            admin=config.admin,
        )
    except Exception:
        pass

    if restore_failed:
        return SqueezeResult(
            stop_reason="restore_failed",
            stop_high=steps[-1].memory_high if steps else high,
            squeeze_point=squeeze_point,
            config=config,
            steps=tuple(steps),
            restored_to="",
            error=failure,
            exit_signal=terminated_by,
        )
    if stop_reason == "error":
        return _result_error(config, error_message, restored_to=relax_to)

    return SqueezeResult(
        stop_reason=stop_reason,
        stop_high=steps[-1].memory_high if steps else high,
        squeeze_point=squeeze_point,
        config=config,
        steps=tuple(steps),
        restored_to=relax_to,
        error="",
        exit_signal=terminated_by,
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
# convention so ``topos report`` (P54) or a future replay path can consume it.
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
    if result.stop_reason == "restore_failed":
        return f"SQUEEZE RESTORE FAILED: {result.error}"
    if result.stop_reason == "error":
        if not result.restored_to:
            return f"SQUEEZE ERROR: {result.error}\nmemory.high was not modified"
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
    except Exception:
        is_root = False
    if is_root is not True:
        return _result_error(config, "root privileges are required")

    return run_squeeze(config, **kwargs)
