"""Kill action — signal validation, argv builder, preview plan for docker-kill
and systemd-kill.

Reuses the P46 action kernel (same plan/preview/confirm/execute path, same
audit, same timeout, same fail-closed posture).  The signal is validated
against a closed allowlist; KILL requires an extra ``--force`` opt-in;
protected entities are refused at plan time.

No subprocess, no shell, no host mutation.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

from topos.actions.catalog import DOCKER_EXECUTABLE, SYSTEMCTL_EXECUTABLE, ActionKind

# ---------------------------------------------------------------------------
# Signal allowlist (closed enum, no SIG prefix, no numeric signals)
# ---------------------------------------------------------------------------

_ALLOWED_SIGNALS: frozenset[str] = frozenset({
    "TERM",
    "INT",
    "HUP",
    "KILL",
    "QUIT",
    "USR1",
    "USR2",
})


def validate_signal(value: str) -> str:
    """Validate and canonicalise a signal name.

    Accepts only the bare signal names in ``_ALLOWED_SIGNALS`` (TERM, INT,
    HUP, KILL, QUIT, USR1, USR2).  Rejects SIG-prefixed forms, numeric
    signals, and unknown strings.

    Returns:
        The canonical uppercase signal name.

    Raises:
        ValueError: On invalid input.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("signal must be a non-empty string")

    # Reject SIG prefix
    upper = value.upper()
    if upper.startswith("SIG"):
        bare = upper[3:]
        if bare in _ALLOWED_SIGNALS:
            raise ValueError(
                f"signal must not include SIG prefix: {value!r}; use {bare!r}"
            )
        raise ValueError(
            f"signal must not include SIG prefix: {value!r}"
        )

    # Reject numeric signals
    if value.isdigit():
        raise ValueError(
            f"signal must be a symbolic name, not a number: {value!r}"
        )

    if upper not in _ALLOWED_SIGNALS:
        sorted_signals = ", ".join(sorted(_ALLOWED_SIGNALS))
        raise ValueError(
            f"unknown signal {value!r}; allowed signals: {sorted_signals}"
        )

    return upper


# ---------------------------------------------------------------------------
# Kill argv builder
# ---------------------------------------------------------------------------


def build_kill_argv(kind: ActionKind, target: str, signal: str) -> list[str]:
    """Build the docker kill or systemctl kill argv.

    Args:
        kind: ``ActionKind.DOCKER_KILL`` or ``ActionKind.SYSTEMD_KILL``.
        target: Container identifier or systemd unit name.
        signal: Validated signal name.

    Returns:
        The argv list for ``subprocess.run`` (no shell).

    Raises:
        ValueError: On invalid inputs.
    """
    signal = validate_signal(signal)
    if not isinstance(target, str) or not target:
        raise ValueError("target must be a non-empty string")
    if kind is ActionKind.SYSTEMD_KILL:
        return [SYSTEMCTL_EXECUTABLE, "kill", "--signal", signal, target]
    if kind is ActionKind.DOCKER_KILL:
        return [DOCKER_EXECUTABLE, "kill", "--signal", signal, target]
    raise ValueError(f"invalid kill kind: {kind}")


# ---------------------------------------------------------------------------
# KillPlan — immutable preview plan
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class KillPlan:
    """Immutable preview plan for one kill action.

    Includes the resolved signal, target, and whether ``--force`` was
    provided (required for KILL signal).
    """

    kind: str  # "docker-kill" or "systemd-kill"
    target: str
    signal: str
    argv: tuple[str, ...]
    force: bool = False
    description: str = (
        "Send a signal to a container or systemd unit."
    )
    mode: str = "preview"


def _kind_to_action_kind(kind_str: str) -> ActionKind:
    if kind_str == "docker-kill":
        return ActionKind.DOCKER_KILL
    if kind_str == "systemd-kill":
        return ActionKind.SYSTEMD_KILL
    raise ValueError(f"invalid kill kind: {kind_str!r}")


# ---------------------------------------------------------------------------
# Preview builder
# ---------------------------------------------------------------------------


def build_kill_preview(
    kind: str,
    target: str,
    signal: str = "TERM",
    *,
    force: bool = False,
) -> KillPlan:
    """Build a structured preview plan for a kill action.

    Args:
        kind: ``"docker-kill"`` or ``"systemd-kill"``.
        target: Container identifier or systemd unit name.
        signal: Signal name (default: TERM).
        force: Allow KILL signal (requires explicit opt-in).

    Returns:
        A ``KillPlan`` with the preview argv.

    Raises:
        ValueError: On invalid inputs.
    """
    validated_signal = validate_signal(signal)
    action_kind = _kind_to_action_kind(kind)

    # KILL requires --force
    if validated_signal == "KILL" and not force:
        raise ValueError(
            "KILL signal requires --force (data-loss prevention gate)"
        )

    argv = build_kill_argv(action_kind, target, validated_signal)

    return KillPlan(
        kind=kind,
        target=target,
        signal=validated_signal,
        argv=tuple(argv),
        force=force,
    )


# ---------------------------------------------------------------------------
# Protected-entity check (injectable)
# ---------------------------------------------------------------------------

# Type for injectable protected-entity check.
# Returns True if the target is a protected service.
ProtectedCheck = Callable[[str, str], bool]


def _default_protected_check(kind: str, target: str) -> bool:
    """Production protected-entity check: is *target* a protected service?

    Reads ``[tiers] protected_services`` from the loaded config and compares it
    against the target the same way the collector does when it stamps
    ``Entity.is_protected`` (``collect/collector.py``: an entity is protected if
    its key OR its name is listed).  A ``kill`` target is always a resolved
    Docker container name / id or a systemd unit name, i.e. exactly the "name"
    half of that comparison.

    Raising is a refusal, not a pass: ``execute_kill`` treats an exception here
    as "protection could not be established" and refuses.  Returning False means
    the check ran and the target is not protected.

    Known limit: a container addressed by its 64-hex id is not matched against a
    ``protected_services`` entry that lists it by name (resolving the two would
    need a collector sweep at kill time).  Address protected containers by name,
    or list the id.  Documented in docs/OPERATIONS.md.
    """
    from topos.config import load

    protected = load(None).protected_services
    return target in protected


# ---------------------------------------------------------------------------
# Render helpers for preview display
# ---------------------------------------------------------------------------


def render_kill_preview(plan: KillPlan) -> str:
    """Render a human-readable preview of a kill action."""
    lines = [
        f"Action: {plan.kind}",
        f"Target: {plan.target}",
        f"Signal: {plan.signal}",
        f"Force: {'yes' if plan.force else 'no'}",
        f"Command argv: {list(plan.argv)}",
        f"Description: {plan.description}",
        "Mode: preview only; no command was executed",
    ]
    if plan.signal == "KILL":
        lines.insert(3, "WARNING: KILL signal causes data loss")
    return "\n".join(lines)


def kill_plan_to_jsonable(plan: KillPlan) -> dict[str, object]:
    """Convert a KillPlan to JSON-safe data."""
    return {
        "argv": list(plan.argv),
        "description": plan.description,
        "force": plan.force,
        "kind": plan.kind,
        "mode": plan.mode,
        "signal": plan.signal,
        "target": plan.target,
    }
