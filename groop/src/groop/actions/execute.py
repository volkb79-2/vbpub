"""Production action execution boundary.

Only the catalog's Docker/systemd start, stop, and restart plans can reach a
runner.  The production path requires admin mode, an exact confirmation, UID
0, a durable root-owned audit record, a finite bounded timeout, and a fixed
absolute executable.  ``runner``, ``clock``, ``identity``, ``root_check``, and
an absolute ``audit_path`` are API fixtures; the CLI exposes none of them.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import pwd
import selectors
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from groop.actions.catalog import (
    ACTION_CATALOG,
    DOCKER_EXECUTABLE,
    EXECUTION_ALLOWLIST,
    SYSTEMCTL_EXECUTABLE,
    ActionKind,
    validate_target,
)
from groop.actions.preview import ActionPlan, build_preview


DEFAULT_EXECUTION_AUDIT_PATH = Path("/var/log/groop/actions.jsonl")
_MIN_TIMEOUT = 0.001
_MAX_TIMEOUT = 30.0
_MAX_OUTPUT_CHARS = 4096
_MAX_OUTPUT_BYTES = 16 * 1024
_MAX_AUDIT_TEXT = 256
_TRUNCATED = " ... (truncated)"
_VALID_ACTION_OUTCOMES = frozenset({"success", "nonzero", "timeout", "runner_failure"})


@dataclasses.dataclass(frozen=True)
class AuditIdentity:
    """Stable identity captured once and used by both audit records."""

    uid: int
    user: str


@dataclasses.dataclass(frozen=True)
class ExecuteResult:
    """Bounded, typed result of an execution attempt.

    ``action_outcome`` preserves the runner result when a post-audit write
    fails.  In that case ``outcome`` is ``audit_failure`` and the mutation
    result is never silently presented as a success.
    """

    kind: str
    target: str
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    outcome: str
    duration_s: float
    action_outcome: str | None = None
    audit_outcome: str | None = None
    audit_error: str = ""


class _AuditError(OSError):
    """Internal typed audit failure; never escapes the public API."""


def _bound_output(value: str) -> str:
    """Bound a rendered field without exposing unbounded child output."""
    if not isinstance(value, str):
        return ""
    if len(value) > _MAX_OUTPUT_CHARS:
        return value[:_MAX_OUTPUT_CHARS] + _TRUNCATED
    return value


def _bound_audit_text(value: str) -> str:
    value = _bound_output(value)
    return value[:_MAX_AUDIT_TEXT]


def _decode_output(value: bytes) -> str:
    return _bound_output(value.decode("utf-8", errors="replace"))


def _refusal(
    kind: str,
    target: str,
    message: str = "action refused",
    *,
    audit_outcome: str | None = None,
) -> ExecuteResult:
    return ExecuteResult(
        kind=kind if isinstance(kind, str) else "",
        target=target if isinstance(target, str) else "",
        argv=(),
        returncode=None,
        stdout="",
        stderr=_bound_output(message),
        outcome="refusal",
        duration_s=0.0,
        action_outcome="refusal",
        audit_outcome=audit_outcome,
    )


def _production_identity() -> AuditIdentity:
    uid = os.geteuid()
    try:
        user = pwd.getpwuid(uid).pw_name
    except (KeyError, OSError):
        user = "unknown"
    return AuditIdentity(uid=uid, user=user)


def _coerce_identity(value: AuditIdentity) -> AuditIdentity:
    if not isinstance(value, AuditIdentity):
        raise ValueError("identity provider returned an invalid identity")
    if not isinstance(value.uid, int) or isinstance(value.uid, bool) or value.uid < 0:
        raise ValueError("identity uid is invalid")
    if (
        not isinstance(value.user, str)
        or not value.user
        or len(value.user) > _MAX_AUDIT_TEXT
    ):
        raise ValueError("identity user is invalid")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value.user):
        raise ValueError("identity user contains control characters")
    return AuditIdentity(value.uid, _bound_audit_text(value.user))


def _validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a finite positive number")
    if not math.isfinite(float(timeout)) or not (
        _MIN_TIMEOUT <= float(timeout) <= _MAX_TIMEOUT
    ):
        raise ValueError(
            f"timeout must be finite and between {_MIN_TIMEOUT} and {_MAX_TIMEOUT} seconds"
        )


def _validate_plan(plan: ActionPlan, kind: ActionKind, target: str) -> None:
    if (
        not isinstance(plan, ActionPlan)
        or plan.kind is not kind
        or plan.target != target
    ):
        raise ValueError("execution plan does not match the requested action")
    if plan.mode != "preview" or not isinstance(plan.argv, tuple):
        raise ValueError("execution plan is not an immutable preview plan")
    if not all(isinstance(part, str) for part in plan.argv):
        raise ValueError("execution plan argv is invalid")
    expected = tuple(ACTION_CATALOG[kind].builder(target))
    if plan.argv != expected:
        raise ValueError("execution plan argv does not match the catalog")
    if kind in {
        ActionKind.DOCKER_START,
        ActionKind.DOCKER_STOP,
        ActionKind.DOCKER_RESTART,
    }:
        expected_executable = DOCKER_EXECUTABLE
    else:
        expected_executable = SYSTEMCTL_EXECUTABLE
    if plan.argv[0] != expected_executable or not plan.argv[0].startswith("/"):
        raise ValueError("execution executable is not a fixed absolute path")
    if len(plan.argv) != 3 or plan.argv[2] != target:
        raise ValueError("execution argv shape is invalid")


def _open_safe_audit(path: Path, *, require_root_owner: bool) -> TextIO:
    """Open an audit target using directory FDs and no-follow flags."""
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise _AuditError("audit path must be absolute")
    if not hasattr(os, "O_NOFOLLOW"):
        raise _AuditError("secure audit open is unavailable")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    parent_fd = os.open(os.sep, flags)
    try:
        # Resolve each parent component without following symlinks.  Newly
        # created components are private directories and are opened again with
        # O_NOFOLLOW before use.
        for component in path.parts[1:-1]:
            if component in {"", ".", ".."}:
                raise _AuditError("audit parent contains unsafe path syntax")
            try:
                next_fd = os.open(component, flags, dir_fd=parent_fd)
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=parent_fd)
                next_fd = os.open(component, flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
            parent_stat = os.fstat(parent_fd)
            private_or_sticky = not parent_stat.st_mode & 0o022 or bool(
                parent_stat.st_mode & stat.S_ISVTX
            )
            if not stat.S_ISDIR(parent_stat.st_mode) or not private_or_sticky:
                raise _AuditError("audit parent is not a private directory")
            if require_root_owner and parent_stat.st_uid != 0:
                raise _AuditError("production audit parent is not root-owned")

        try:
            existing = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
                raise _AuditError("audit target is not a regular file")
            if existing.st_mode & 0o077:
                raise _AuditError("audit target permissions are too broad")
            if require_root_owner and existing.st_uid != 0:
                raise _AuditError("production audit target is not root-owned")
        leaf_flags = (
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
        )
        fd = os.open(path.name, leaf_flags, 0o600, dir_fd=parent_fd)
        try:
            leaf_stat = os.fstat(fd)
            if not stat.S_ISREG(leaf_stat.st_mode):
                raise _AuditError("audit target is not a regular file")
            if leaf_stat.st_mode & 0o077:
                raise _AuditError("audit target permissions are too broad")
            if require_root_owner and leaf_stat.st_uid != 0:
                raise _AuditError("production audit target is not root-owned")
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, "a", encoding="utf-8", newline="\n")
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(parent_fd)


def _audit_record(
    *,
    identity: AuditIdentity,
    kind: str,
    target: str,
    argv: tuple[str, ...],
    clock: Callable[[], float],
    stage: str,
    result: ExecuteResult | None = None,
) -> dict[str, object]:
    timestamp = float(clock())
    if not math.isfinite(timestamp):
        raise _AuditError("audit clock returned a non-finite timestamp")
    record: dict[str, object] = {
        "ts": timestamp,
        "uid": identity.uid,
        "user": _bound_audit_text(identity.user),
        "kind": _bound_audit_text(kind),
        "target": _bound_audit_text(target),
        "argv": [_bound_audit_text(part) for part in argv],
        "mode": "execute",
        "admin": True,
        "stage": stage,
    }
    if result is not None:
        record.update(
            {
                "outcome": result.outcome,
                "action_outcome": result.action_outcome or result.outcome,
                "returncode": result.returncode,
                "duration_s": round(max(0.0, min(result.duration_s, _MAX_TIMEOUT)), 3),
            }
        )
    return record


def _write_json_record(fh: TextIO, record: dict[str, object]) -> None:
    line = json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    if len(line) > 4096:
        raise _AuditError("audit record exceeds bounded size")
    fh.write(line)
    fh.write("\n")
    fh.flush()
    os.fsync(fh.fileno())


def _write_execution_audit_pre(
    audit_path: str | Path,
    *,
    identity: AuditIdentity,
    kind: str,
    target: str,
    argv: tuple[str, ...],
    clock: Callable[[], float],
) -> TextIO:
    path = Path(audit_path)
    fh = _open_safe_audit(path, require_root_owner=path == DEFAULT_EXECUTION_AUDIT_PATH)
    try:
        _write_json_record(
            fh,
            _audit_record(
                identity=identity,
                kind=kind,
                target=target,
                argv=argv,
                clock=clock,
                stage="pre",
            ),
        )
        return fh
    except BaseException as exc:
        try:
            fh.close()
        finally:
            if isinstance(exc, _AuditError):
                raise
            raise _AuditError("pre-audit write failed") from None


def _write_execution_audit_post(
    fh: TextIO,
    *,
    identity: AuditIdentity,
    kind: str,
    target: str,
    argv: tuple[str, ...],
    result: ExecuteResult,
    clock: Callable[[], float],
) -> None:
    try:
        _write_json_record(
            fh,
            _audit_record(
                identity=identity,
                kind=kind,
                target=target,
                argv=argv,
                clock=clock,
                stage="post",
                result=result,
            ),
        )
    except BaseException as exc:
        raise _AuditError("post-audit write failed") from None
    finally:
        try:
            fh.close()
        except OSError:
            # The post-audit failure is already represented by the typed
            # result; this finally block guarantees no handle is leaked.
            pass


def _drain_process(
    proc: subprocess.Popen[bytes], timeout: float
) -> tuple[bytes, bytes, bool]:
    """Drain both pipes while retaining only a bounded prefix."""
    selector = selectors.DefaultSelector()
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    streams = ((proc.stdout, "stdout"), (proc.stderr, "stderr"))
    for stream, name in streams:
        if stream is not None:
            selector.register(stream, selectors.EVENT_READ, name)
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0 and proc.poll() is None:
                timed_out = True
                proc.kill()
                deadline = time.monotonic() + 1.0
                remaining = 1.0
            events = selector.select(max(0.0, min(remaining, 0.1)))
            if not events and proc.poll() is not None:
                # A final zero-time read observes EOF without waiting on the
                # child and keeps the drain loop bounded after exit.
                events = selector.select(0)
            for key, _ in events:
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 4096)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                elif len(captured[key.data]) < _MAX_OUTPUT_BYTES:
                    room = _MAX_OUTPUT_BYTES - len(captured[key.data])
                    captured[key.data].extend(chunk[:room])
            if timed_out and proc.poll() is not None and not events:
                break
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1.0)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise
    finally:
        selector.close()
        for stream, _ in streams:
            if stream is not None and not stream.closed:
                stream.close()
    return bytes(captured["stdout"]), bytes(captured["stderr"]), timed_out


def _default_runner(argv: tuple[str, ...], *, timeout: float) -> ExecuteResult:
    """Run a fixed argv with a minimal environment and bounded output."""
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            env={
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C.UTF-8",
            },
        )
        stdout, stderr, timed_out = _drain_process(proc, timeout)
        returncode = proc.returncode
        outcome = (
            "timeout" if timed_out else ("success" if returncode == 0 else "nonzero")
        )
        return ExecuteResult(
            kind="",
            target="",
            argv=argv,
            returncode=None if timed_out else returncode,
            stdout=_decode_output(stdout),
            stderr=_decode_output(stderr),
            outcome=outcome,
            duration_s=max(0.0, time.monotonic() - started),
        )
    except subprocess.TimeoutExpired:
        return ExecuteResult(
            kind="",
            target="",
            argv=argv,
            returncode=None,
            stdout="",
            stderr="",
            outcome="timeout",
            duration_s=max(0.0, time.monotonic() - started),
        )
    except OSError as exc:
        return ExecuteResult(
            kind="",
            target="",
            argv=argv,
            returncode=None,
            stdout="",
            stderr=_bound_output(f"{type(exc).__name__}: {exc}"),
            outcome="runner_failure",
            duration_s=max(0.0, time.monotonic() - started),
        )


def _normalise_runner_result(result: object, plan: ActionPlan) -> ExecuteResult:
    if not isinstance(result, ExecuteResult):
        raise ValueError("runner returned an invalid result type")
    if (
        result.argv != plan.argv
        or result.kind not in ("", plan.kind.value)
        or result.target not in ("", plan.target)
    ):
        raise ValueError("runner result does not match the immutable plan")
    if result.outcome not in _VALID_ACTION_OUTCOMES:
        raise ValueError("runner returned an invalid outcome")
    if result.returncode is not None and (
        not isinstance(result.returncode, int) or isinstance(result.returncode, bool)
    ):
        raise ValueError("runner returned an invalid return code")
    if result.outcome == "success" and result.returncode != 0:
        raise ValueError("successful runner result must have return code zero")
    if result.outcome == "nonzero" and (
        result.returncode is None or result.returncode == 0
    ):
        raise ValueError("nonzero runner result must have a nonzero return code")
    if (
        result.outcome in {"timeout", "runner_failure"}
        and result.returncode is not None
    ):
        raise ValueError("failed runner result must not have a return code")
    if result.action_outcome not in (None, result.outcome):
        raise ValueError("runner returned an inconsistent action outcome")
    if (
        isinstance(result.duration_s, bool)
        or not isinstance(result.duration_s, (int, float))
        or not math.isfinite(float(result.duration_s))
        or result.duration_s < 0
    ):
        raise ValueError("runner returned an invalid duration")
    if not isinstance(result.stdout, str) or not isinstance(result.stderr, str):
        raise ValueError("runner output must be text")
    return dataclasses.replace(
        result,
        kind=plan.kind.value,
        target=plan.target,
        argv=plan.argv,
        stdout=_bound_output(result.stdout),
        stderr=_bound_output(result.stderr),
    )


@dataclasses.dataclass(frozen=True)
class _ExecutionSpec:
    """Verb-provided immutable inputs for the shared execution chain."""

    kind: str
    target: str
    argv: tuple[str, ...]
    plan: ActionPlan


@dataclasses.dataclass(frozen=True)
class _GateRefusal:
    """A gate refusal, including the externally visible result outcome."""

    message: str
    outcome: str = "refusal"


Gate = Callable[[], _GateRefusal | None]


def _execute_gated(
    initial_kind: str,
    initial_target: str,
    *,
    confirmation: str,
    admin: bool,
    confirm: str,
    audit_path: str | Path,
    runner: Callable[..., ExecuteResult] | None,
    clock: Callable[[], float] | None,
    identity: Callable[[], AuditIdentity] | None,
    root_check: Callable[[], bool] | None,
    timeout: float,
    build_spec: Callable[[], _ExecutionSpec],
    pre_audit_gates: tuple[Gate, ...] = (),
    post_audit_gates: tuple[Gate, ...] = (),
) -> ExecuteResult:
    """Run the single P46 gate/audit/runner sequence for every action verb.

    ``pre_audit_gates`` refuse before the durable pre record.  The smaller
    ``post_audit_gates`` category is for revalidation that must remain after
    that record to preserve its existing audit trail (target revalidation and
    P49 stale-plan detection).
    """
    if not admin:
        return _refusal(initial_kind, initial_target, "admin mode is required")
    if confirm != confirmation:
        return _refusal(
            initial_kind,
            initial_target,
            f"exact confirmation {confirmation} is required",
        )
    try:
        is_root = root_check() if root_check is not None else os.geteuid() == 0
    except BaseException:
        is_root = False
    if is_root is not True:
        return _refusal(initial_kind, initial_target, "root privileges are required")
    try:
        _validate_timeout(timeout)
    except (TypeError, ValueError) as exc:
        return _refusal(initial_kind, initial_target, str(exc))
    if not isinstance(audit_path, (str, Path)):
        return _refusal(initial_kind, initial_target, "a mandatory audit path is required")
    audit_path_obj = Path(audit_path)
    if not audit_path_obj.is_absolute():
        return _refusal(initial_kind, initial_target, "audit path must be absolute")

    for gate in pre_audit_gates:
        refusal = gate()
        if refusal is not None:
            return _refusal(initial_kind, initial_target, refusal.message)
    try:
        spec = build_spec()
    except (TypeError, ValueError, KeyError) as exc:
        return _refusal(initial_kind, initial_target, str(exc))

    now = clock or time.time
    try:
        stable_identity = _coerce_identity(
            identity() if identity is not None else _production_identity()
        )
    except BaseException as exc:
        return _refusal(
            initial_kind,
            initial_target,
            f"invalid execution identity: {type(exc).__name__}",
        )
    try:
        audit_fh = _write_execution_audit_pre(
            audit_path_obj,
            identity=stable_identity,
            kind=spec.kind,
            target=spec.target,
            argv=spec.argv,
            clock=now,
        )
    except BaseException as exc:
        return _refusal(
            initial_kind,
            initial_target,
            "audit failed before execution",
            audit_outcome=f"pre_failure:{type(exc).__name__}",
        )

    try:
        started = float(now())
    except BaseException:
        started = time.time()
    for gate in post_audit_gates:
        refusal = gate()
        if refusal is None:
            continue
        audited_refusal = _refusal(spec.kind, spec.target, refusal.message)
        try:
            _write_execution_audit_post(
                audit_fh,
                identity=stable_identity,
                kind=spec.kind,
                target=spec.target,
                argv=spec.argv,
                result=audited_refusal,
                clock=now,
            )
        except _AuditError:
            pass
        return dataclasses.replace(audited_refusal, outcome=refusal.outcome)

    try:
        raw_result = (runner or _default_runner)(spec.argv, timeout=float(timeout))
    except subprocess.TimeoutExpired:
        raw_result = ExecuteResult("", "", spec.argv, None, "", "", "timeout", 0.0)
    except OSError as exc:
        raw_result = ExecuteResult(
            "",
            "",
            spec.argv,
            None,
            "",
            _bound_output(f"{type(exc).__name__}: {exc}"),
            "runner_failure",
            0.0,
        )
    except BaseException as exc:
        raw_result = ExecuteResult(
            "",
            "",
            spec.argv,
            None,
            "",
            _bound_output(f"{type(exc).__name__}: runner failed"),
            "runner_failure",
            0.0,
        )

    try:
        result = _normalise_runner_result(raw_result, spec.plan)
    except (TypeError, ValueError) as exc:
        result = ExecuteResult(
            kind=spec.kind,
            target=spec.target,
            argv=spec.argv,
            returncode=None,
            stdout="",
            stderr=_bound_output(str(exc)),
            outcome="runner_failure",
            duration_s=0.0,
        )
    try:
        elapsed = float(now()) - started
    except BaseException:
        elapsed = 0.0
    result = dataclasses.replace(
        result,
        kind=spec.kind,
        target=spec.target,
        argv=spec.argv,
        duration_s=max(
            0.0, min(elapsed if math.isfinite(elapsed) else 0.0, _MAX_TIMEOUT)
        ),
        action_outcome=result.outcome,
    )

    try:
        _write_execution_audit_post(
            audit_fh,
            identity=stable_identity,
            kind=spec.kind,
            target=spec.target,
            argv=spec.argv,
            result=result,
            clock=now,
        )
    except BaseException as exc:
        try:
            audit_fh.close()
        except BaseException:
            pass
        return dataclasses.replace(
            result,
            outcome="audit_failure",
            audit_outcome="post_failure",
            audit_error=_bound_output(str(exc) or "post-audit write failed"),
        )
    return result


def execute_plan(
    kind: str,
    target: str,
    *,
    admin: bool = False,
    confirm: str = "",
    audit_path: str | Path = DEFAULT_EXECUTION_AUDIT_PATH,
    runner: Callable[..., ExecuteResult] | None = None,
    clock: Callable[[], float] | None = None,
    identity: Callable[[], AuditIdentity] | None = None,
    root_check: Callable[[], bool] | None = None,
    timeout: float = 30.0,
    plan: ActionPlan | None = None,
) -> ExecuteResult:
    """Execute one immutable catalog plan through the production gates.

    The optional fixture parameters are intentionally API-only.  The
    production CLI supplies no audit path, runner, identity, clock, or root
    override and therefore uses the fixed root-owned policy.
    """
    action_kind: ActionKind | None = None
    current_plan: ActionPlan | None = None

    def action_kind_gate() -> _GateRefusal | None:
        nonlocal action_kind
        try:
            action_kind = ActionKind(kind)
        except (TypeError, ValueError):
            return _GateRefusal("unknown action kind")
        if action_kind not in EXECUTION_ALLOWLIST:
            return _GateRefusal(f"kind {kind!r} is not in execution allowlist")
        return None

    def plan_gate() -> _GateRefusal | None:
        nonlocal current_plan
        assert action_kind is not None
        try:
            validate_target(action_kind, target)
            current_plan = plan if plan is not None else build_preview(kind, target)
            _validate_plan(current_plan, action_kind, target)
        except (TypeError, ValueError, KeyError) as exc:
            return _GateRefusal(str(exc))
        return None

    def build_spec() -> _ExecutionSpec:
        assert action_kind is not None and current_plan is not None
        return _ExecutionSpec(action_kind.value, target, current_plan.argv, current_plan)

    def target_revalidation_gate() -> _GateRefusal | None:
        assert action_kind is not None and current_plan is not None
        try:
            validate_target(action_kind, target)
            _validate_plan(current_plan, action_kind, target)
        except (TypeError, ValueError, KeyError) as exc:
            return _GateRefusal(str(exc))
        return None

    return _execute_gated(
        kind,
        target,
        confirmation="EXECUTE",
        admin=admin,
        confirm=confirm,
        audit_path=audit_path,
        runner=runner,
        clock=clock,
        identity=identity,
        root_check=root_check,
        timeout=timeout,
        build_spec=build_spec,
        pre_audit_gates=(action_kind_gate, plan_gate),
        post_audit_gates=(target_revalidation_gate,),
    )


def result_to_jsonable(result: ExecuteResult) -> dict[str, object]:
    """Convert a bounded result to JSON-safe data."""
    return {
        "kind": result.kind,
        "target": result.target,
        "argv": list(result.argv),
        "returncode": result.returncode,
        "outcome": result.outcome,
        "action_outcome": result.action_outcome,
        "audit_outcome": result.audit_outcome,
        "audit_error": _bound_output(result.audit_error),
        "duration_s": round(result.duration_s, 3),
        "stdout": _bound_output(result.stdout),
        "stderr": _bound_output(result.stderr),
    }


def render_result_text(result: ExecuteResult) -> str:
    lines = [
        f"Action: {result.kind}",
        f"Target: {result.target}",
        f"Outcome: {result.outcome}",
        f"Action outcome: {result.action_outcome}",
        f"Return code: {result.returncode}",
        f"Duration: {result.duration_s:.3f}s",
    ]
    if result.audit_outcome:
        lines.append(f"Audit: {result.audit_outcome}")
    if result.audit_error:
        lines.append(f"Audit error: {_bound_output(result.audit_error)}")
    if result.stdout:
        lines.extend(("--- stdout ---", _bound_output(result.stdout)))
    if result.stderr:
        lines.extend(("--- stderr ---", _bound_output(result.stderr)))
    return "\n".join(lines)


def execute_set_property(
    unit: str,
    *,
    property_name: str | None = None,
    property_value: str | None = None,
    persistence: str | None = None,
    admin: bool = False,
    confirm: str = "",
    audit_path: str | Path = DEFAULT_EXECUTION_AUDIT_PATH,
    runner: Callable[..., ExecuteResult] | None = None,
    clock: Callable[[], float] | None = None,
    identity: Callable[[], AuditIdentity] | None = None,
    root_check: Callable[[], bool] | None = None,
    timeout: float = 30.0,
    planned_current_value: str | None = None,
    current_value_reader: Callable[[str], str | None] | None = None,
) -> ExecuteResult:
    """Execute a systemd memory.high set-property action through the P46 gates.

    Reuses the P46 root/admin/typed-confirmation, absolute argv, timeout,
    result bounds, and fail-closed audit contract.  Additionally validates
    the property/value and performs stale detection by re-reading the
    current value immediately before execution.

    If *planned_current_value* is provided and the fresh read differs, the
    action is refused with ``outcome="stale"`` — the plan was built against a
    value that no longer holds.

    The optional fixture parameters are intentionally API-only.  The
    production CLI supplies no audit path, runner, identity, clock, or root
    override and therefore uses the fixed root-owned policy.

    Returns an ``ExecuteResult``.
    """
    from groop.actions.governance import (
        ALLOWED_PROPERTY,
        build_set_property_argv,
        detect_default_persistence,
        validate_memory_high_unit,
        validate_memory_high_value,
        validate_persistence_mode,
    )

    canonical_value: str | None = None
    persistence_mode: str | None = None

    def property_gate() -> _GateRefusal | None:
        if property_name != ALLOWED_PROPERTY:
            return _GateRefusal(
                f"property must be {ALLOWED_PROPERTY!r}, got {property_name!r}"
            )
        return None

    def unit_gate() -> _GateRefusal | None:
        try:
            validate_memory_high_unit(unit)
        except ValueError as exc:
            return _GateRefusal(str(exc))
        return None

    def value_gate() -> _GateRefusal | None:
        nonlocal canonical_value
        try:
            canonical_value = validate_memory_high_value(property_value or "")
        except ValueError as exc:
            return _GateRefusal(str(exc))
        return None

    def persistence_gate() -> _GateRefusal | None:
        nonlocal persistence_mode
        if persistence is None:
            persistence_mode = detect_default_persistence(unit)
            return None
        try:
            persistence_mode = validate_persistence_mode(persistence)
        except ValueError as exc:
            return _GateRefusal(str(exc))
        return None

    def build_spec() -> _ExecutionSpec:
        assert canonical_value is not None and persistence_mode is not None
        argv = tuple(
            build_set_property_argv(
                unit, property_name, canonical_value, persistence=persistence_mode
            )
        )
        return _ExecutionSpec(
            "systemd-set-property",
            unit,
            argv,
            _make_plan_stub("systemd-set-property", unit, argv),
        )

    def stale_revalidation_gate() -> _GateRefusal | None:
        if current_value_reader is None and planned_current_value is None:
            return None
        reader = current_value_reader or _default_current_value_reader
        try:
            fresh_current_value = reader(unit)
        except BaseException:
            fresh_current_value = None
        if (
            planned_current_value is not None
            and fresh_current_value is not None
            and fresh_current_value != planned_current_value
        ):
            return _GateRefusal(
                f"current memory.high value changed ({planned_current_value} -> {fresh_current_value}); "
                "preview again with the fresh value",
                outcome="stale",
            )
        return None

    return _execute_gated(
        property_name or "systemd-set-property",
        unit,
        confirmation="EXECUTE",
        admin=admin,
        confirm=confirm,
        audit_path=audit_path,
        runner=runner,
        clock=clock,
        identity=identity,
        root_check=root_check,
        timeout=timeout,
        build_spec=build_spec,
        pre_audit_gates=(property_gate, unit_gate, value_gate, persistence_gate),
        post_audit_gates=(stale_revalidation_gate,),
    )


def _default_current_value_reader(unit: str) -> str | None:
    """Read the current memory.high value via systemctl show.

    This is an injectable seam for tests; see governance.py's
    ``_systemctl_show_reader`` for the production implementation.
    """
    from groop.actions.governance import _systemctl_show_reader
    return _systemctl_show_reader(unit)


def _make_plan_stub(kind: str, target: str, argv: tuple[str, ...]) -> ActionPlan:
    """Create a minimal plan-like object for ``_normalise_runner_result``."""
    from groop.actions.preview import ActionPlan
    from groop.actions.catalog import ActionKind

    return ActionPlan(
        kind=ActionKind(kind),
        target=target,
        argv=argv,
        description="",
    )


# ---------------------------------------------------------------------------
# execute_kill — kill action through P46 gates
# ---------------------------------------------------------------------------


def execute_kill(
    kind: str,
    target: str,
    *,
    signal: str = "TERM",
    force: bool = False,
    admin: bool = False,
    confirm: str = "",
    audit_path: str | Path = DEFAULT_EXECUTION_AUDIT_PATH,
    runner: Callable[..., ExecuteResult] | None = None,
    clock: Callable[[], float] | None = None,
    identity: Callable[[], AuditIdentity] | None = None,
    root_check: Callable[[], bool] | None = None,
    timeout: float = 30.0,
    protected_check: Callable[[str, str], bool] | None = None,
) -> ExecuteResult:
    """Execute a kill action through the P46 gates.

    Reuses the P46 root/admin/typed-confirmation, absolute argv, timeout,
    result bounds, and fail-closed audit contract.  Additionally validates
    the signal (closed allowlist) and checks protected entities.

    ``confirm`` must be ``"KILL"`` (per-verb token — distinct from
    ``"EXECUTE"`` used by start/stop/restart).

    Signature matches the ``execute_set_property`` pattern.
    """
    from groop.actions.kill_ops import (
        _default_protected_check,
        build_kill_argv,
        validate_signal,
    )

    validated_signal: str | None = None
    action_kind: ActionKind | None = None

    def signal_gate() -> _GateRefusal | None:
        nonlocal validated_signal
        try:
            validated_signal = validate_signal(signal)
        except ValueError as exc:
            return _GateRefusal(str(exc))
        return None

    def force_gate() -> _GateRefusal | None:
        if validated_signal == "KILL" and not force:
            return _GateRefusal("KILL signal requires --force (data-loss prevention gate)")
        return None

    def protected_gate() -> _GateRefusal | None:
        check = protected_check or _default_protected_check
        try:
            is_protected = check(kind, target)
        except BaseException as exc:
            return _GateRefusal(
                f"protected-service check failed ({type(exc).__name__}); kill refused"
            )
        if is_protected is not False:
            return _GateRefusal("target is a protected service; kill refused")
        return None

    def action_kind_gate() -> _GateRefusal | None:
        nonlocal action_kind
        try:
            action_kind = ActionKind(kind)
        except (TypeError, ValueError):
            return _GateRefusal(f"invalid action kind: {kind!r}")
        return None

    def build_spec() -> _ExecutionSpec:
        assert action_kind is not None and validated_signal is not None
        argv = tuple(build_kill_argv(action_kind, target, validated_signal))
        return _ExecutionSpec(kind, target, argv, _make_plan_stub(kind, target, argv))

    def target_revalidation_gate() -> _GateRefusal | None:
        assert action_kind is not None
        try:
            validate_target(action_kind, target)
        except (TypeError, ValueError, KeyError) as exc:
            return _GateRefusal(str(exc))
        return None

    return _execute_gated(
        kind,
        target,
        confirmation="KILL",
        admin=admin,
        confirm=confirm,
        audit_path=audit_path,
        runner=runner,
        clock=clock,
        identity=identity,
        root_check=root_check,
        timeout=timeout,
        build_spec=build_spec,
        pre_audit_gates=(signal_gate, force_gate, protected_gate, action_kind_gate),
        post_audit_gates=(target_revalidation_gate,),
    )


# ---------------------------------------------------------------------------
# execute_update — update action through P46 gates
# ---------------------------------------------------------------------------


def execute_update(
    target: str,
    *,
    memory: str | None = None,
    cpus: str | None = None,
    below_current: bool = False,
    admin: bool = False,
    confirm: str = "",
    audit_path: str | Path = DEFAULT_EXECUTION_AUDIT_PATH,
    runner: Callable[..., ExecuteResult] | None = None,
    clock: Callable[[], float] | None = None,
    identity: Callable[[], AuditIdentity] | None = None,
    root_check: Callable[[], bool] | None = None,
    timeout: float = 30.0,
    current_memory_reader: Callable[[str], int | None] | None = None,
) -> ExecuteResult:
    """Execute a docker-update action through the P46 gates.

    Reuses the P46 root/admin/typed-confirmation, absolute argv, timeout,
    result bounds, and fail-closed audit contract.  Additionally validates
    memory/CPU limits and checks current memory usage.

    ``confirm`` must be ``"UPDATE"`` (per-verb token — distinct from
    ``"EXECUTE"`` and ``"KILL"``).

    Signature matches the ``execute_set_property`` pattern.
    """
    from groop.actions.update_ops import (
        validate_memory,
        validate_cpus,
        build_update_argv,
        _reject_systemd_target,
        _default_current_memory_reader,
    )

    parsed_memory: int | None = None
    parsed_cpus: float | None = None

    def memory_gate() -> _GateRefusal | None:
        nonlocal parsed_memory
        if memory is None:
            return None
        try:
            parsed_memory = validate_memory(memory)
        except ValueError as exc:
            return _GateRefusal(str(exc))
        return None

    def cpus_gate() -> _GateRefusal | None:
        nonlocal parsed_cpus
        if cpus is None:
            return None
        try:
            parsed_cpus = validate_cpus(cpus)
        except ValueError as exc:
            return _GateRefusal(str(exc))
        return None

    def requested_limit_gate() -> _GateRefusal | None:
        if parsed_memory is None and parsed_cpus is None:
            return _GateRefusal("at least one of --memory or --cpus is required")
        return None

    def systemd_target_gate() -> _GateRefusal | None:
        try:
            _reject_systemd_target(target)
        except ValueError as exc:
            return _GateRefusal(str(exc))
        return None

    def current_memory_gate() -> _GateRefusal | None:
        if parsed_memory is None or below_current:
            return None
        reader = current_memory_reader or _default_current_memory_reader
        try:
            current_usage = reader(target)
        except BaseException:
            current_usage = None
        if current_usage is None:
            return _GateRefusal(
                f"current memory usage of {target!r} could not be established, so a "
                f"limit of {parsed_memory} bytes cannot be shown to be safe; pass "
                "--below-current to apply it anyway (this may OOM the container)",
            )
        if parsed_memory < current_usage:
            return _GateRefusal(
                f"memory limit {parsed_memory} bytes is below current "
                f"usage {current_usage} bytes; use --below-current to "
                "override (this may OOM the container)",
            )
        return None

    def build_spec() -> _ExecutionSpec:
        argv = tuple(build_update_argv(target, memory=parsed_memory, cpus=parsed_cpus))
        return _ExecutionSpec(
            "docker-update", target, argv, _make_plan_stub("docker-update", target, argv)
        )

    def target_revalidation_gate() -> _GateRefusal | None:
        try:
            validate_target(ActionKind.DOCKER_UPDATE, target)
        except (TypeError, ValueError, KeyError) as exc:
            return _GateRefusal(str(exc))
        return None

    return _execute_gated(
        "docker-update",
        target,
        confirmation="UPDATE",
        admin=admin,
        confirm=confirm,
        audit_path=audit_path,
        runner=runner,
        clock=clock,
        identity=identity,
        root_check=root_check,
        timeout=timeout,
        build_spec=build_spec,
        pre_audit_gates=(
            memory_gate,
            cpus_gate,
            requested_limit_gate,
            systemd_target_gate,
            current_memory_gate,
        ),
        post_audit_gates=(target_revalidation_gate,),
    )
