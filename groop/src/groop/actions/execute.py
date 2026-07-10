"""Admin action execution kernel — gated, audited, argv-only subprocess runner.

Every execution path goes through the same gates:
1.  --admin must be True
2.  --confirm must be exactly "EXECUTE"
3.  kind must be in EXECUTION_ALLOWLIST (start/stop/restart only)
4.  target must pass validate_target() — no shell metacharacters, no option-like
    strings, no path syntax, valid container/unit form
5.  Audit record is written BEFORE subprocess is called; the write is durable
    (fsync'd). If the audit write fails, execution is refused (fail closed).
6.  subprocess.run(shell=False, timeout=30, capture_output=True) with a clean
    minimal environment.
7.  Audit outcome is appended after execution completes.

The module never accepts arbitrary argv from the client, never uses shell=True,
never executes outside the allowlisted catalog, and never runs as root without
all gates passing.  Injected runner/clock/identity fixtures let tests prove
every gate without real Docker or systemd calls.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from groop.actions.catalog import EXECUTION_ALLOWLIST, ACTION_CATALOG, ActionKind

# ---------------------------------------------------------------------------
# Typed result
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ExecuteResult:
    """Immutable result of one execution attempt.

    outcome is one of:
        "success"        — returncode == 0
        "nonzero"        — returncode != 0
        "timeout"        — subprocess.TimeoutExpired raised
        "refusal"        — one or more gates rejected the request
        "runner_failure" — unexpected runner error (not timeout, not nonzero)
    """

    kind: str
    target: str
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    outcome: str
    duration_s: float


# ---------------------------------------------------------------------------
# Target validation
# ---------------------------------------------------------------------------

# Reject characters that are never valid in a container name or unit name.
# Shell metacharacters, control chars, path syntax.  Whitespace is rejected
# separately for execution-allowed kinds (see validate_target) because
# systemd-set-property preview targets legitimately contain spaces.
_INVALID_TARGET_RE = re.compile(
    r"[\x00-\x1f\x7f"       # control characters
    r";&|`$(){}[]<>\"'\\"    # shell metacharacters
    r"\/]"                   # path separator and bracket (for safety)
)

# Valid Docker container name characters (as documented by Docker).
_DOCKER_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")

# Valid systemd unit name characters (alphanumeric, ASCII punctuation).
_SYSTEMD_NAME_RE = re.compile(r"^[a-zA-Z0-9@._:-]+$")

# Docker container id: exactly 64 lowercase hex characters.
_DOCKER_ID_RE = re.compile(r"^[a-f0-9]{64}$")

# Maximum length for a Docker container name per Docker engine.
_MAX_DOCKER_NAME_LEN = 128

# Systemd unit suffixes that are valid for start/stop/restart.
_VALID_SYSTEMD_SUFFIXES = frozenset({
    ".service",
    ".slice",
    ".scope",
    ".target",
    ".socket",
    ".mount",
    ".timer",
    ".path",
})


def validate_target(kind: ActionKind, target: str) -> None:
    """Validate *target* for the given *kind*.

    Raises ValueError with a descriptive message on any violation.
    Called both at preview-build time and immediately before execution.

    For preview-only kinds (not in EXECUTION_ALLOWLIST) such as
    systemd-set-property, only basic safety checks are applied.
    Execution-allowed kinds get strict kind-specific rules.
    """
    if not target:
        raise ValueError("target must not be empty")

    # Reject option-like targets (e.g. "--something", "-x")
    if target.startswith("-"):
        raise ValueError(f"target must not be option-like: {target!r}")

    # Reject any invalid characters (shell metacharacters, control chars)
    m = _INVALID_TARGET_RE.search(target)
    if m:
        ch = m.group(0)
        raise ValueError(
            f"target contains invalid character {ch!r}: {target!r}"
        )

    # Preview-only kinds (systemd-set-property, etc.) pass basic checks above.
    if kind not in EXECUTION_ALLOWLIST:
        return

    # Reject whitespace for execution-allowed kinds (Docker/systemd start/stop/restart)
    if any(c.isspace() for c in target):
        raise ValueError(
            f"target must not contain whitespace: {target!r}"
        )

    # Kind-specific rules
    if kind in (ActionKind.DOCKER_RESTART, ActionKind.DOCKER_STOP, ActionKind.DOCKER_START):
        if len(target) > _MAX_DOCKER_NAME_LEN:
            raise ValueError(
                f"Docker target exceeds max length {_MAX_DOCKER_NAME_LEN}: "
                f"{len(target)} chars"
            )
        # Accept either a full 64-char hex id or a valid name
        if _DOCKER_ID_RE.match(target):
            return  # valid container id
        if _DOCKER_NAME_RE.match(target):
            return  # valid container name
        raise ValueError(
            f"invalid Docker container identifier: {target!r}"
        )

    if kind in (ActionKind.SYSTEMD_RESTART, ActionKind.SYSTEMD_STOP, ActionKind.SYSTEMD_START):
        if not _SYSTEMD_NAME_RE.match(target):
            raise ValueError(
                f"invalid systemd unit name characters: {target!r}"
            )
        # Must have a valid suffix or be a bare name (for templated units, etc.)
        if "." in target:
            has_valid_suffix = any(target.endswith(sfx) for sfx in _VALID_SYSTEMD_SUFFIXES)
            if not has_valid_suffix:
                raise ValueError(
                    f"systemd unit target has unsupported suffix: {target!r}"
                )
        return

    # Should not reach here for known execution-allowed kinds.
    raise ValueError(f"execution not allowed for kind {kind.value!r}: {target!r}")


# ---------------------------------------------------------------------------
# Default subprocess runner
# ---------------------------------------------------------------------------

# Clean minimal environment for subprocess calls.
_MINIMAL_ENV: dict[str, str] = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
}

_EXECUTION_TIMEOUT: float = 30.0


def _default_runner(
    argv: tuple[str, ...],
    *,
    timeout: float = _EXECUTION_TIMEOUT,
) -> ExecuteResult:
    """Execute *argv* via subprocess with shell=False and a clean env.

    This is the production runner.  Tests inject a fake runner instead.
    """
    deadline = time.monotonic()
    try:
        cp = subprocess.run(
            list(argv),
            capture_output=True,
            shell=False,
            env=_MINIMAL_ENV,
            timeout=timeout,
            text=True,
        )
        duration = time.monotonic() - deadline
        returncode: int | None = cp.returncode
        outcome = "success" if cp.returncode == 0 else "nonzero"
        stdout = _bound_output(cp.stdout)
        stderr = _bound_output(cp.stderr)
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - deadline
        returncode = None
        outcome = "timeout"
        stdout = _bound_output(exc.stdout.decode("utf-8", errors="replace") if exc.stdout else "")
        stderr = _bound_output(exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "")
    except OSError as exc:
        duration = time.monotonic() - deadline
        returncode = None
        outcome = "runner_failure"
        stdout = ""
        stderr = str(exc)
    return ExecuteResult(
        kind="",
        target="",
        argv=argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        outcome=outcome,
        duration_s=duration,
    )


# ---------------------------------------------------------------------------
# Output bounding / redaction
# ---------------------------------------------------------------------------

_MAX_OUTPUT_CHARS = 4096
_REDACT_SUFFIX = " ... (truncated)"


def _bound_output(text: str) -> str:
    """Bound and redact stdout/stderr content."""
    if len(text) > _MAX_OUTPUT_CHARS:
        return text[:_MAX_OUTPUT_CHARS] + _REDACT_SUFFIX
    return text


# ---------------------------------------------------------------------------
# Audit helpers for execution
# ---------------------------------------------------------------------------

def _resolve_user() -> str:
    """Best-effort: return LOGNAME/USER or 'unknown'."""
    return os.environ.get("LOGNAME") or os.environ.get("USER") or "unknown"


def _write_execution_audit_pre(
    audit_path: str | Path | None,
    kind: str,
    target: str,
    argv: tuple[str, ...],
    admin: bool,
    confirm: str,
) -> TextIO | None:
    """Write the pre-execution audit record.  Returns the open file handle
    for the post-execution outcome append, or *None* if no audit path
    was configured.

    Fail closed: if the audit path is set but the write fails, re-raises
    the exception so execution does not proceed.
    """
    if audit_path is None:
        return None

    path = Path(audit_path) if isinstance(audit_path, str) else audit_path
    rec = {
        "ts": time.time(),
        "user": _resolve_user(),
        "kind": kind,
        "target": target,
        "argv": list(argv),
        "mode": "execute",
        "admin": admin,
        "confirm": confirm,
        "stage": "pre",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open for append, write, and fsync for durability before execution.
    fh = path.open("a", encoding="utf-8")
    try:
        fh.write(json.dumps(rec, sort_keys=True))
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    except OSError:
        fh.close()
        raise
    return fh


def _write_execution_audit_post(
    fh: TextIO | None,
    result: ExecuteResult,
    kind: str,
    target: str,
    argv: tuple[str, ...],
    admin: bool,
    confirm: str,
) -> None:
    """Append the post-execution audit outcome to the already-open file."""
    if fh is None:
        return
    try:
        rec = {
            "ts": time.time(),
            "user": _resolve_user(),
            "kind": kind,
            "target": target,
            "argv": list(argv),
            "mode": "execute",
            "admin": admin,
            "confirm": confirm,
            "stage": "post",
            "outcome": result.outcome,
            "returncode": result.returncode,
            "duration_s": round(result.duration_s, 3),
        }
        fh.write(json.dumps(rec, sort_keys=True))
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    finally:
        fh.close()


# ---------------------------------------------------------------------------
# Gated execution
# ---------------------------------------------------------------------------

def execute_plan(
    kind: str,
    target: str,
    *,
    admin: bool = False,
    confirm: str = "",
    audit_path: str | Path | None = None,
    runner: Callable[..., ExecuteResult] | None = None,
    clock: Callable[[], float] | None = None,
    timeout: float = _EXECUTION_TIMEOUT,
) -> ExecuteResult:
    """Execute an admin action through all gates.

    Gates (fail closed — first violation stops the call):
    1. ``admin`` must be True.
    2. ``confirm`` must equal ``"EXECUTE"`` (case-sensitive).
    3. ``kind`` must be a valid ActionKind.
    4. ``kind`` must be in EXECUTION_ALLOWLIST.
    5. ``target`` must pass validate_target().
    6. Pre-execution audit write must succeed (or no audit path configured).
    7. subprocess run with shell=False, bounded timeout, captured output.
    8. Post-execution audit outcome append.

    *runner* — injectable callable for tests (signature: f(argv, *, timeout)
    returning ExecuteResult).  Defaults to _default_runner.
    *clock* — injectable for deterministic timestamps in tests.
    """
    if runner is None:
        runner = _default_runner

    # ------------------------------------------------------------------
    # Gate 1: admin mode
    # ------------------------------------------------------------------
    if not admin:
        return ExecuteResult(
            kind=kind,
            target=target,
            argv=(),
            returncode=None,
            stdout="",
            stderr="",
            outcome="refusal",
            duration_s=0.0,
        )

    # ------------------------------------------------------------------
    # Gate 2: typed confirmation
    # ------------------------------------------------------------------
    if confirm != "EXECUTE":
        return ExecuteResult(
            kind=kind,
            target=target,
            argv=(),
            returncode=None,
            stdout="",
            stderr="",
            outcome="refusal",
            duration_s=0.0,
        )

    # ------------------------------------------------------------------
    # Gate 3: valid ActionKind
    # ------------------------------------------------------------------
    try:
        ak = ActionKind(kind)
    except ValueError:
        return ExecuteResult(
            kind=kind,
            target=target,
            argv=(),
            returncode=None,
            stdout="",
            stderr="",
            outcome="refusal",
            duration_s=0.0,
        )

    # ------------------------------------------------------------------
    # Gate 4: execution allowlist
    # ------------------------------------------------------------------
    if ak not in EXECUTION_ALLOWLIST:
        return ExecuteResult(
            kind=kind,
            target=target,
            argv=(),
            returncode=None,
            stdout="",
            stderr=f"kind {kind!r} is not in execution allowlist",
            outcome="refusal",
            duration_s=0.0,
        )

    # ------------------------------------------------------------------
    # Gate 5: target validation
    # ------------------------------------------------------------------
    try:
        validate_target(ak, target)
    except ValueError as exc:
        return ExecuteResult(
            kind=kind,
            target=target,
            argv=(),
            returncode=None,
            stdout="",
            stderr=str(exc),
            outcome="refusal",
            duration_s=0.0,
        )

    # Build argv from catalog builder
    entry = ACTION_CATALOG[ak]
    argv = tuple(entry.builder(target))

    # ------------------------------------------------------------------
    # Gate 6: pre-execution audit (fail closed)
    # ------------------------------------------------------------------
    try:
        audit_fh = _write_execution_audit_pre(
            audit_path, kind, target, argv, admin, confirm,
        )
    except OSError:
        return ExecuteResult(
            kind=kind,
            target=target,
            argv=argv,
            returncode=None,
            stdout="",
            stderr="audit write failed before execution",
            outcome="runner_failure",
            duration_s=0.0,
        )

    # ------------------------------------------------------------------
    # Gate 7: subprocess execution
    # ------------------------------------------------------------------
    start = (clock() if clock else time.time())
    result = runner(argv, timeout=timeout)
    duration = (clock() if clock else time.time()) - start

    # Stitch in kind/target so the result is self-describing
    result = dataclasses.replace(
        result,
        kind=kind,
        target=target,
        argv=argv,
        duration_s=duration,
    )

    # ------------------------------------------------------------------
    # Gate 8: post-execution audit
    # ------------------------------------------------------------------
    _write_execution_audit_post(
        audit_fh, result, kind, target, argv, admin, confirm,
    )

    return result


# ---------------------------------------------------------------------------
# Result rendering helpers
# ---------------------------------------------------------------------------

def result_to_jsonable(result: ExecuteResult) -> dict[str, object]:
    """Convert an ExecuteResult to a JSON-safe dict."""
    return {
        "kind": result.kind,
        "target": result.target,
        "argv": list(result.argv),
        "returncode": result.returncode,
        "outcome": result.outcome,
        "duration_s": round(result.duration_s, 3),
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def render_result_text(result: ExecuteResult) -> str:
    """Render an ExecuteResult as human-readable text."""
    lines = [
        f"Action: {result.kind}",
        f"Target: {result.target}",
        f"Outcome: {result.outcome}",
        f"Return code: {result.returncode}",
        f"Duration: {result.duration_s:.3f}s",
    ]
    if result.stdout:
        lines.append("--- stdout ---")
        lines.append(result.stdout)
    if result.stderr:
        lines.append("--- stderr ---")
        lines.append(result.stderr)
    return "\n".join(lines)
