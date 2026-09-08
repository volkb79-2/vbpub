"""P93 - gated execution through the owner-chain protocol.

``execute_via_owner_chain`` is the oracle-facing (O5-O9) executor: it drives
discovery -> resolution -> capability-check -> plan, then the same P46/P78
audited-execution primitives ``execute.py`` uses (root/admin/typed-
confirmation gate, fail-closed durable pre/post audit, bounded timeout,
bounded output), and finally adds owner-chain revalidation immediately
before execution and post-execution verification with a typed ``partial``
outcome.

It is deliberately not wired into the CLI. Production Docker/systemd verbs
are migrated onto the protocol's *resolution* kernel directly inside
``execute.py``'s existing gates (see
``lifecycle_adapters.evaluate_docker_owner_chain`` and
``execute._make_systemd_owner_gate``) so their audit/argv/error-message
contract stays byte-identical for existing callers. This executor exists to
fixture-test the protocol's full contract -- including revalidation and
verification, which no existing verb needs today -- ahead of any new
mutating adapter plugging into it.
"""

from __future__ import annotations

import dataclasses
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from topos.actions.execute import (
    AuditIdentity,
    ExecuteResult,
    _AuditError,
    _bound_output,
    _coerce_identity,
    _default_runner,
    _production_identity,
    _validate_timeout,
    _write_execution_audit_post,
    _write_execution_audit_pre,
)
from topos.actions.lifecycle import (
    Capability,
    ChainRefusal,
    LifecycleAdapter,
    check_capability,
    resolve_authoritative_owner,
    revalidate,
    verify,
)

_VALID_RUNNER_OUTCOMES = frozenset({"success", "nonzero", "timeout", "runner_failure"})


def _refusal(kind: str, target: str, message: str, *, outcome: str = "refusal") -> ExecuteResult:
    return ExecuteResult(
        kind=kind,
        target=target,
        argv=(),
        returncode=None,
        stdout="",
        stderr=_bound_output(message),
        outcome=outcome,
        duration_s=0.0,
        action_outcome=outcome,
    )


def execute_via_owner_chain(
    adapter: LifecycleAdapter,
    target: str,
    action: Capability,
    *,
    admin: bool = False,
    confirm: str = "",
    confirmation: str = "EXECUTE",
    audit_path: str | Path,
    runner: Callable[..., ExecuteResult] | None = None,
    clock: Callable[[], float] | None = None,
    identity: Callable[[], AuditIdentity] | None = None,
    root_check: Callable[[], bool] | None = None,
    timeout: float = 30.0,
    owner_check: Callable[[], bool] | None = None,
    runtime_check: Callable[[], bool] | None = None,
) -> ExecuteResult:
    """Discover, resolve, gate, execute and verify one owner-chain action.

    ``owner_check``/``runtime_check`` default to "always true" (no
    verification configured); pass both to exercise oracle O9's partial
    outcome. Discovery/resolution/capability refusals happen before any
    durable audit record (mirroring ``execute.py``'s ``pre_audit_gates``);
    revalidation refusals happen after the pre-audit record, so they leave a
    durable pre/post pair (mirroring its ``post_audit_gates``).
    """

    initial_kind = f"{adapter.family.value}:{action.value}"

    if not admin:
        return _refusal(initial_kind, target, "admin mode is required")
    if confirm != confirmation:
        return _refusal(initial_kind, target, f"exact confirmation {confirmation} is required")
    try:
        is_root = root_check() if root_check is not None else False
    except BaseException:
        is_root = False
    if is_root is not True:
        return _refusal(initial_kind, target, "root privileges are required")
    try:
        _validate_timeout(timeout)
    except (TypeError, ValueError) as exc:
        return _refusal(initial_kind, target, str(exc))
    audit_path_obj = Path(audit_path)
    if not audit_path_obj.is_absolute():
        return _refusal(initial_kind, target, "audit path must be absolute")

    discovery = adapter.discover(target)
    resolution = resolve_authoritative_owner(discovery)
    if isinstance(resolution, ChainRefusal):
        return _refusal(initial_kind, target, resolution.message, outcome=resolution.reason)
    owner = resolution

    capability_refusal = check_capability(owner, action)
    if capability_refusal is not None:
        return _refusal(
            initial_kind, target, capability_refusal.message, outcome=capability_refusal.reason
        )

    try:
        plan = adapter.plan(discovery, action, target)
    except (TypeError, ValueError) as exc:
        return _refusal(initial_kind, target, str(exc))

    kind = f"{owner.family.value}:{action.value}"
    argv = plan.argv if plan.argv is not None else ()

    now = clock or time.time
    try:
        stable_identity = _coerce_identity(
            identity() if identity is not None else _production_identity()
        )
    except BaseException as exc:
        return _refusal(kind, target, f"invalid execution identity: {type(exc).__name__}")

    try:
        audit_fh = _write_execution_audit_pre(
            audit_path_obj,
            identity=stable_identity,
            kind=kind,
            target=target,
            argv=argv,
            clock=now,
        )
    except BaseException:
        return _refusal(kind, target, "audit failed before execution")

    # Revalidate the chosen owner immediately before execution (oracles
    # O6/O7): a disappeared owner or changed incarnation refuses here,
    # durably audited, rather than executing through a stale/lower link.
    revalidation_refusal = revalidate(adapter, target, owner)
    if revalidation_refusal is not None:
        audited = _refusal(
            kind, target, revalidation_refusal.message, outcome=revalidation_refusal.reason
        )
        try:
            _write_execution_audit_post(
                audit_fh, identity=stable_identity, kind=kind, target=target, argv=argv,
                result=audited, clock=now,
            )
        except _AuditError:
            pass
        return audited

    started = float(now())
    try:
        raw_result = (runner or _default_runner)(argv, timeout=float(timeout))
    except subprocess.TimeoutExpired:
        raw_result = ExecuteResult(kind, target, argv, None, "", "", "timeout", 0.0)
    except OSError as exc:
        raw_result = ExecuteResult(
            kind, target, argv, None, "", _bound_output(f"{type(exc).__name__}: {exc}"),
            "runner_failure", 0.0,
        )
    if not isinstance(raw_result, ExecuteResult) or raw_result.outcome not in _VALID_RUNNER_OUTCOMES:
        raw_result = ExecuteResult(
            kind, target, argv, None, "", "runner returned an invalid result",
            "runner_failure", 0.0,
        )

    elapsed = max(0.0, min(float(now()) - started, float(timeout) or 0.0))
    result = dataclasses.replace(
        raw_result, kind=kind, target=target, argv=argv, duration_s=elapsed,
        action_outcome=raw_result.outcome,
    )

    # Post-execution verification (contract 4 / oracle O9): a failing check
    # is never swallowed into a bare "success" -- it becomes a typed
    # "partial" outcome, still carried through the durable post-audit below.
    if result.outcome == "success" and (owner_check is not None or runtime_check is not None):
        outcome = verify(
            owner_check=owner_check or (lambda: True),
            runtime_check=runtime_check or (lambda: True),
        )
        if not outcome.ok:
            result = dataclasses.replace(result, outcome="partial", action_outcome="partial")

    try:
        _write_execution_audit_post(
            audit_fh, identity=stable_identity, kind=kind, target=target, argv=argv,
            result=result, clock=now,
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
