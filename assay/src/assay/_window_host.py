"""Run one native R2 command window in an isolated Assay process.

The worker owns Assay's process-wide subreaper state for exactly one measured
command. The campaign coordinator still owns mutation materialization,
resource-counter endpoints, classification, retries, state, and progress. The
protocol is private, versioned JSON on stdin and a bounded result record on
file descriptor 3; the host itself does not change candidate outcome rules.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, BinaryIO, Mapping, Sequence

from . import observer_windows
from . import runner as _runner
from .errors import AssayError, Outcome, ReasonCode
from .process_tree import ProcessTreeDescendant, ProcessTreeError, ProcessTreeEvidence

__all__ = ["can_host", "run_window"]


_PROTOCOL = "assay-window-host/1"
_RESULT_FD = 3
_STATUS_FD_ENV = "ASSAY_WINDOW_HOST_CANDIDATE_FD"
_WITNESS_FD_ENV = "ASSAY_WINDOW_HOST_WITNESS_FD"
_CLEANUP_SECONDS = 10.0
_REQUEST_LIMIT_BYTES = 4 * 1024 * 1024
_RESPONSE_LIMIT_BYTES = 4 * 1024 * 1024


class _WindowHostCancelled(BaseException):
    """SIGTERM asks the worker to unwind its ProcessTreeWindow cleanup."""


class WindowHostError(RuntimeError):
    """The worker returned a structured command-execution exception."""


def _runner_spec(process_runner: Any) -> Mapping[str, object] | None:
    """Return a JSON-safe runner configuration, or leave an injected seam alone.

    An injected runner can opt in by implementing ``assay_window_host_spec``.
    Its custom form is ``{"kind":"factory", "factory":"module:name",
    "config": {...}, "python_paths": [...]}``; the importable factory is
    called with that JSON config in the isolated worker.
    """
    describe = getattr(process_runner, "assay_window_host_spec", None)
    if not callable(describe):
        return None
    spec = describe()
    if not isinstance(spec, Mapping):
        return None
    kind = spec.get("kind")
    if kind not in {"process_tree", "liveness", "factory"}:
        return None
    if kind == "factory":
        factory = spec.get("factory")
        config = spec.get("config", {})
        python_paths = spec.get("python_paths", [])
        if (
            not isinstance(factory, str)
            or ":" not in factory
            or not isinstance(config, Mapping)
            or not isinstance(python_paths, list)
            or any(not isinstance(path, str) for path in python_paths)
        ):
            return None
    try:
        json.dumps(dict(spec), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return dict(spec)


def can_host(process_runner: Any, clock: Any) -> bool:
    """Whether this execution uses only dependencies the worker can rebuild."""
    return clock is _runner._utc_now and _runner_spec(process_runner) is not None


def _command_plan_to_payload(plan: Any) -> dict[str, object]:
    return {
        "argv_declared": list(plan.argv_declared),
        "argv_appended": list(plan.argv_appended),
        "argv_effective": list(plan.argv_effective),
        "cli_argv_appended": (
            None if plan.cli_argv_appended is None else list(plan.cli_argv_appended)
        ),
        "env_declared": dict(plan.env_declared),
        "env_effective": dict(plan.env_effective),
        "env_passthrough": list(plan.env_passthrough),
        "allow_argv_append": plan.allow_argv_append,
        "budget_seconds": plan.budget_seconds,
        "project_prefix": (
            None if plan.project_prefix is None else str(plan.project_prefix)
        ),
        "cwd_declared": plan.cwd_declared,
    }


def _command_plan_from_payload(payload: Mapping[str, object]) -> Any:
    from pathlib import PurePosixPath

    expected = {
        "argv_declared", "argv_appended", "argv_effective", "cli_argv_appended",
        "env_declared", "env_effective", "env_passthrough", "allow_argv_append",
        "budget_seconds", "project_prefix", "cwd_declared",
    }
    if set(payload) != expected:
        raise ValueError("window-host command plan has an unexpected shape")
    prefix = payload["project_prefix"]
    cli_appended = payload["cli_argv_appended"]
    return _runner.CommandPlan(
        argv_declared=tuple(payload["argv_declared"]),
        argv_appended=tuple(payload["argv_appended"]),
        argv_effective=tuple(payload["argv_effective"]),
        cli_argv_appended=(
            None if cli_appended is None else tuple(cli_appended)
        ),
        env_declared=dict(payload["env_declared"]),
        env_effective=dict(payload["env_effective"]),
        env_passthrough=tuple(payload["env_passthrough"]),
        allow_argv_append=payload["allow_argv_append"],
        budget_seconds=payload["budget_seconds"],
        project_prefix=None if prefix is None else PurePosixPath(prefix),
        cwd_declared=payload["cwd_declared"],
    )


def _resource_evidence_payload(value: Any) -> object:
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return dict(value)
    raise ValueError("window-host evidence must be a mapping or record")


def _observation_to_payload(value: Any) -> object:
    if value is None:
        return None
    return {
        "status": value.status,
        "limits": _resource_evidence_payload(value.limits),
        "pressure_us": dict(value.pressure_us),
        "reasons": list(value.reasons),
    }


def _observation_from_payload(value: object) -> Any:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("window-host contention observation must be an object")
    from . import contention
    from .resource_limits import ResourceLimitEvidence

    raw_limits = value["limits"]
    limits = (
        None
        if raw_limits is None
        else ResourceLimitEvidence.from_dict(raw_limits)
    )
    return contention.ContentionObservation(
        status=value["status"],
        limits=limits,
        pressure_us=dict(value["pressure_us"]),
        reasons=tuple(value["reasons"]),
    )


def _command_result_to_payload(result: Any) -> dict[str, object]:
    return {
        "outcome": result.outcome.value,
        "reason_code": (
            None if result.reason_code is None else result.reason_code.value
        ),
        "returncode": result.returncode,
        "stdout_tail": result.stdout_tail,
        "stderr_tail": result.stderr_tail,
        "stdout_dropped_bytes": result.stdout_dropped_bytes,
        "stderr_dropped_bytes": result.stderr_dropped_bytes,
        "started": result.started,
        "ended": result.ended,
        "liveness_resource_evidence": _resource_evidence_payload(
            result.liveness_resource_evidence
        ),
        "process_tree_evidence": _resource_evidence_payload(
            result.process_tree_evidence
        ),
        "observation": _observation_to_payload(result.observation),
        "termination": (
            None if result.termination is None else result.termination.value
        ),
        "decided_by_report": result.decided_by_report,
    }


def _command_result_from_payload(plan: Any, payload: Mapping[str, object]) -> Any:
    from . import termination

    expected = {
        "outcome", "reason_code", "returncode", "stdout_tail", "stderr_tail",
        "stdout_dropped_bytes", "stderr_dropped_bytes", "started", "ended",
        "liveness_resource_evidence", "process_tree_evidence", "observation",
        "termination", "decided_by_report",
    }
    if set(payload) != expected:
        raise ValueError("window-host command result has an unexpected shape")
    raw_reason = payload["reason_code"]
    raw_termination = payload["termination"]
    liveness_evidence = payload["liveness_resource_evidence"]
    process_tree_evidence = payload["process_tree_evidence"]
    return _runner.CommandResult(
        plan=plan,
        outcome=Outcome(payload["outcome"]),
        reason_code=None if raw_reason is None else ReasonCode(raw_reason),
        returncode=payload["returncode"],
        stdout_tail=payload["stdout_tail"],
        stderr_tail=payload["stderr_tail"],
        stdout_dropped_bytes=payload["stdout_dropped_bytes"],
        stderr_dropped_bytes=payload["stderr_dropped_bytes"],
        started=payload["started"],
        ended=payload["ended"],
        liveness_resource_evidence=(
            None if liveness_evidence is None else dict(liveness_evidence)
        ),
        process_tree_evidence=(
            None if process_tree_evidence is None else dict(process_tree_evidence)
        ),
        observation=_observation_from_payload(payload["observation"]),
        termination=(
            None
            if raw_termination is None
            else termination.Termination(raw_termination)
        ),
        decided_by_report=payload["decided_by_report"],
    )


def _timeout_payload(timeout: float) -> float | str:
    return "infinity" if timeout == math.inf else timeout


def _timeout_from_payload(value: object) -> float:
    if value == "infinity":
        return math.inf
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("window-host timeout must be positive and finite")
    return float(value)


def _worker_runner(spec: Mapping[str, object], plan: Any) -> Any:
    kind = spec.get("kind")
    if kind == "process_tree":
        from .process_tree import ProcessTreeProcessRunner

        sensitive_values = tuple(
            plan.env_effective[name]
            for name in plan.env_passthrough
            if name in plan.env_effective
        )
        return ProcessTreeProcessRunner(
            cleanup_seconds=spec["cleanup_seconds"],
            sensitive_values=sensitive_values,
        )
    if kind == "liveness":
        from . import liveness

        return liveness.LivenessRunner(
            events_dir=Path(spec["events_dir"]),
            expect_next_event_within_s=spec["expect_next_event_within_s"],
            pre_first_event_within_s=spec["pre_first_event_within_s"],
            cpu_window_s=spec["cpu_window_s"],
        )
    if kind == "factory":
        from importlib import import_module

        factory_name = spec["factory"]
        module_name, _separator, qualified_name = factory_name.partition(":")
        for python_path in reversed(spec.get("python_paths", [])):
            sys.path.insert(0, python_path)
        factory = import_module(module_name)
        for attribute in qualified_name.split("."):
            factory = getattr(factory, attribute)
        return factory(**dict(spec.get("config", {})))
    raise ValueError("window-host runner kind is unsupported")


def _execute_request(request: Mapping[str, object]) -> dict[str, object]:
    keys = set(request) - {"profile"}
    if keys != {"protocol", "plan", "cwd", "timeout", "runner"}:
        raise ValueError("window-host request has an unexpected shape")
    raw_profile = request.get("profile")
    profile_config = (
        None if raw_profile is None else observer_windows.WindowConfig.from_wire(raw_profile)
    )
    if request["protocol"] != _PROTOCOL:
        raise ValueError("window-host request protocol is unsupported")
    raw_plan = request["plan"]
    raw_runner = request["runner"]
    if not isinstance(raw_plan, Mapping) or not isinstance(raw_runner, Mapping):
        raise ValueError("window-host plan and runner must be objects")
    plan = _command_plan_from_payload(raw_plan)
    runner = _worker_runner(raw_runner, plan)
    witness_fd = os.environ.get(_WITNESS_FD_ENV)
    if witness_fd is not None:
        from .mutation_witness import WITNESS_FD_ENV

        plan.env_effective[WITNESS_FD_ENV] = witness_fd
    with observer_windows.activate(profile_config) as slot:
        result = _runner.execute_plan(
            plan,
            cwd=Path(request["cwd"]),
            timeout=_timeout_from_payload(request["timeout"]),
            process_runner=runner,
            clock=_runner._utc_now,
        )
    return {
        "protocol": _PROTOCOL,
        "kind": "result",
        "result": _command_result_to_payload(result),
        "profile": (
            None if slot is None or slot.result is None else slot.result.to_wire()
        ),
    }


def _error_payload(exc: BaseException) -> dict[str, object]:
    outcome = getattr(exc, "outcome", None)
    reason_code = getattr(exc, "reason_code", None)
    return {
        "protocol": _PROTOCOL,
        "kind": "error",
        "error_type": type(exc).__name__,
        "message": str(exc),
        "outcome": None if outcome is None else outcome.value,
        "reason_code": None if reason_code is None else reason_code.value,
    }


def _write_response(fd: int, payload: Mapping[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > _RESPONSE_LIMIT_BYTES:
        raise ValueError("window-host response exceeds its size limit")
    view = memoryview(encoded)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("window-host response descriptor stopped accepting data")
        view = view[written:]


def _raise_for_worker_error(payload: Mapping[str, object]) -> None:
    message = payload.get("message")
    if not isinstance(message, str):
        raise ValueError("window-host error message is invalid")
    error_type = payload.get("error_type")
    if error_type == "ProcessTreeError":
        raise ProcessTreeError(message)
    if error_type == "AssayError":
        outcome = Outcome(payload["outcome"])
        reason_raw = payload["reason_code"]
        reason = None if reason_raw is None else ReasonCode(reason_raw)
        raise AssayError(message, outcome=outcome, reason_code=reason)
    if error_type == "KeyboardInterrupt":
        raise KeyboardInterrupt
    raise WindowHostError(f"window-host command raised {error_type}: {message}")


def main(stdin: BinaryIO | None = None, *, result_fd: int = _RESULT_FD) -> int:
    """Worker entry point; writes exactly one response to its result descriptor."""
    source = sys.stdin.buffer if stdin is None else stdin
    prior_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _handle_sigterm)
    try:
        raw = source.read(_REQUEST_LIMIT_BYTES + 1)
        if len(raw) > _REQUEST_LIMIT_BYTES:
            payload = _error_payload(ValueError("window-host request exceeds its size limit"))
        else:
            try:
                decoded = json.loads(raw)
                if not isinstance(decoded, Mapping):
                    raise ValueError("window-host request must be a JSON object")
                payload = _execute_request(decoded)
            except RecursionError as exc:
                # The request crossed a process boundary, so even Assay's own
                # pipe payload must not be allowed to escape the worker.
                payload = _error_payload(exc)
            except BaseException as exc:
                payload = _error_payload(exc)
        _write_response(result_fd, payload)
        return 0 if payload.get("kind") == "result" else 1
    finally:
        signal.signal(signal.SIGTERM, prior_sigterm)


def _handle_sigterm(_signum: int, _frame: object) -> None:
    raise _WindowHostCancelled("window host was asked to stop")


_WORKER_BOOTSTRAP = """\
import os, runpy, sys
result_source, status_source, witness_source = map(int, sys.argv[1:4])
held = [os.dup(fd) for fd in (result_source, status_source) if fd >= 0]
if witness_source >= 0:
    held.append(os.dup(witness_source))
os.dup2(held[0], 3)
os.dup2(held[1], 4)
os.environ['ASSAY_WINDOW_HOST_CANDIDATE_FD'] = '4'
if witness_source >= 0:
    os.dup2(held[2], 5)
    os.environ['ASSAY_WINDOW_HOST_WITNESS_FD'] = '5'
for fd in held:
    if fd not in (3, 4, 5):
        os.close(fd)
for root in reversed(sys.argv[4:6]):
    sys.path.insert(0, root)
sys.argv = ['assay._window_host']
runpy.run_module('assay._window_host', run_name='__main__')
"""


def _launch_host_process(
    *,
    command: Sequence[str],
    request: bytes,
    result_fd: int,
    pass_fds: Sequence[int],
    timeout: float | None,
) -> tuple[int, bytes]:
    """Start and reap one worker, unwinding it on cancellation."""
    from . import liveness

    worker_env = {"PATH": os.environ.get("PATH", os.defpath)}
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
        pass_fds=tuple(pass_fds),
        env=worker_env,
    )
    liveness.register_live_window_host_group(process.pid)
    try:
        try:
            _stdout, stderr = process.communicate(input=request, timeout=timeout)
        except KeyboardInterrupt:
            _stop_host_process(process)
            raise
        except subprocess.TimeoutExpired:
            _stop_host_process(process)
            stderr = b""
    finally:
        liveness.unregister_live_window_host_group(process.pid)
    return process.returncode, stderr or b""


def _stop_host_process(process: Any) -> None:
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=_CLEANUP_SECONDS + 1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _candidate_pid(status_file: BinaryIO) -> int | None:
    status_file.seek(0)
    raw = status_file.read()
    if not raw:
        return None
    lines = raw.splitlines()
    if len(lines) != 1 or not lines[0].isdigit():
        raise ProcessTreeError("window host received invalid candidate process identity")
    pid = int(lines[0])
    if pid < 1:
        raise ProcessTreeError("window host received an invalid candidate pid")
    return pid


def _process_group_members(
    group_id: int,
) -> tuple[tuple[ProcessTreeDescendant, str], ...]:
    members = []
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError as exc:
        raise ProcessTreeError(f"cannot inspect candidate process group: {exc}") from exc
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            raw = (entry / "stat").read_bytes().decode(
                "utf-8", "backslashreplace"
            )
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as exc:
            raise ProcessTreeError(
                f"cannot inspect candidate process {entry.name}: {exc}"
            ) from exc
        close = raw.rfind(")")
        fields = raw[close + 1 :].split() if close >= 0 else []
        if len(fields) < 3:
            raise ProcessTreeError(f"malformed process stat for pid {entry.name}")
        try:
            pid = int(entry.name)
            process_group = int(fields[2])
        except ValueError as exc:
            raise ProcessTreeError(
                f"malformed process identity for pid {entry.name}"
            ) from exc
        try:
            ppid: int | None = int(fields[1])
        except ValueError:
            ppid = None
        if ppid is not None and ppid < 1:
            ppid = None
        if process_group == group_id:
            opening = raw.find("(")
            comm = raw[opening + 1 : close] if opening >= 0 else ""
            identity = (
                ProcessTreeDescendant(pid=pid, ppid=ppid, comm=comm)
                if ppid is not None and comm
                else ProcessTreeDescendant(pid=pid, ppid=None, comm=None)
            )
            members.append((identity, fields[0]))
    return tuple(members)


def _cleanup_crashed_candidate(pid: int | None) -> ProcessTreeEvidence:
    """Kill a reported candidate group if its worker died before returning evidence."""
    if pid is None:
        return ProcessTreeEvidence.not_started()
    started = time.monotonic()
    before = _process_group_members(pid)
    try:
        os.killpg(pid, signal.SIGKILL)
        group_signalled = True
    except ProcessLookupError:
        group_signalled = False
    except OSError as exc:
        raise ProcessTreeError(
            f"cannot clean candidate process group {pid} after window-host failure: {exc}"
        ) from exc
    deadline = started + _CLEANUP_SECONDS
    while True:
        remaining = tuple(
            member
            for member in _process_group_members(pid)
            if member[1] not in {"Z", "X"}
        )
        if not remaining:
            break
        if time.monotonic() >= deadline:
            raise ProcessTreeError(
                f"candidate process group {pid} remained live after window-host failure"
            )
        time.sleep(0.01)
    descendants = tuple(
        sorted(
            (
                identity
                for identity, state in before
                if identity.pid != pid and state not in {"Z", "X"}
            ),
            key=lambda item: item.pid,
        )
    ) if group_signalled else ()
    return ProcessTreeEvidence(
        leader_status="signaled" if group_signalled else "exited",
        group_signalled_before_reap=group_signalled,
        outliving_descendants=len(descendants),
        descendants_signalled=len(descendants) if group_signalled else 0,
        signal=int(signal.SIGKILL) if group_signalled else None,
        descendants=descendants,
        clean=True,
    )


def _host_crash_result(plan: Any, status_file: BinaryIO, detail: str) -> Any:
    evidence = _cleanup_crashed_candidate(_candidate_pid(status_file))
    now = _runner.iso_utc(_runner._utc_now())
    return _runner.CommandResult(
        plan=plan,
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.EXEC_FAILED,
        returncode=None,
        stdout_tail="",
        stderr_tail=detail[-4096:],
        started=now,
        ended=now,
        process_tree_evidence=evidence.to_dict(),
    )


def _outer_timeout(timeout: float, spec: Mapping[str, object]) -> float | None:
    if timeout == math.inf:
        return None
    cleanup = spec.get("cleanup_seconds", _CLEANUP_SECONDS)
    if type(cleanup) not in (int, float) or cleanup <= 0 or not math.isfinite(cleanup):
        cleanup = _CLEANUP_SECONDS
    return timeout + float(cleanup) + 2.0


def _receipt_fd(plan: Any) -> int | None:
    from .mutation_witness import WITNESS_FD_ENV

    value = plan.env_effective.get(WITNESS_FD_ENV)
    if value is None:
        return None
    try:
        fd = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate witness descriptor is invalid") from exc
    if fd < 3:
        raise ValueError("candidate witness descriptor is invalid")
    return fd


def run_window(
    plan: Any,
    *,
    cwd: Path,
    timeout: float,
    process_runner: Any,
    clock: Any,
    profile: "observer_windows.WindowSlot | None" = None,
) -> Any:
    """Run one command in a disposable worker and return the normal result.

    *profile* is the candidate's observer window slot (B206): when it is
    active the worker opens and closes the window around the candidate root and
    the exchange's result is stored back into ``profile.result``.
    """
    spec = _runner_spec(process_runner)
    if spec is None or clock is not _runner._utc_now:
        raise ValueError("window host requires a serializable runner and the standard clock")
    receipt_fd = _receipt_fd(plan)
    request = {
        "protocol": _PROTOCOL,
        "plan": _command_plan_to_payload(plan),
        "cwd": str(cwd),
        "timeout": _timeout_payload(timeout),
        "runner": dict(spec),
    }
    if profile is not None and profile.active:
        request["profile"] = profile.config.to_wire()
    request_bytes = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(request_bytes) > _REQUEST_LIMIT_BYTES:
        raise ValueError("window-host request exceeds its size limit")
    with tempfile.TemporaryFile(mode="w+b") as result_file, tempfile.TemporaryFile(
        mode="w+b"
    ) as status_file:
        result_fd = result_file.fileno()
        status_fd = status_file.fileno()
        descriptors = [result_fd, status_fd]
        if receipt_fd is not None:
            descriptors.append(receipt_fd)
        command = (
            sys.executable,
            "-I",
            "-c",
            _WORKER_BOOTSTRAP,
            str(result_fd),
            str(status_fd),
            str(-1 if receipt_fd is None else receipt_fd),
            str(Path(__file__).parents[1]),
            str(Path(__file__).parents[2]),
        )
        returncode, stderr = _launch_host_process(
            command=command,
            request=request_bytes,
            result_fd=result_fd,
            pass_fds=tuple(dict.fromkeys(descriptors)),
            timeout=_outer_timeout(timeout, spec),
        )

        def crashed(detail: str) -> Any:
            result = _host_crash_result(plan, status_file, detail)
            if _runner.termination_requested():
                raise _runner._lane_timeout(
                    "Assay termination interrupted the native R2 command window"
                )
            return result

        result_file.seek(0)
        response_bytes = result_file.read(_RESPONSE_LIMIT_BYTES + 1)
        if len(response_bytes) > _RESPONSE_LIMIT_BYTES:
            return crashed("window-host response exceeded its size limit")
        if not response_bytes:
            detail = stderr.decode("utf-8", "replace") or (
                f"window host exited without a result (status {returncode})"
            )
            return crashed(detail)
        try:
            response = json.loads(response_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            return crashed("window host returned malformed JSON")
        if not isinstance(response, Mapping) or response.get("protocol") != _PROTOCOL:
            return crashed("window host returned an invalid protocol record")
        if response.get("kind") == "error":
            if (
                response.get("error_type") == "_WindowHostCancelled"
                and _runner.termination_requested()
            ):
                raise _runner._lane_timeout(
                    "Assay termination interrupted the native R2 command window"
                )
            _raise_for_worker_error(response)
        if response.get("kind") != "result" or returncode != 0:
            return crashed("window host failed before returning a command result")
        raw_result = response.get("result")
        if not isinstance(raw_result, Mapping):
            return crashed("window host result must be an object")
        try:
            result = _command_result_from_payload(plan, raw_result)
        except (KeyError, TypeError, ValueError):
            return crashed("window host returned an invalid command result")
        if profile is not None and profile.active:
            try:
                raw_profile = response.get("profile")
                profile.result = (
                    None
                    if raw_profile is None
                    else observer_windows.WindowResult.from_wire(raw_profile)
                )
            except (KeyError, TypeError, ValueError):
                # A malformed measurement record never changes the command's
                # result; the candidate's profile is a NO_MEASUREMENT.
                profile.result = observer_windows.WindowResult(
                    reason=observer_windows.OBSERVER_ERROR,
                    detail="window host returned a malformed profile record",
                    mode=profile.config.mode,
                    interval_ms=profile.config.interval_ms,
                    asked=True,
                )
        if _runner.termination_requested():
            raise _runner._lane_timeout(
                "Assay termination interrupted the native R2 command window"
            )
        return result


if __name__ == "__main__":
    raise SystemExit(main())
