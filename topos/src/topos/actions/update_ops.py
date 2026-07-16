"""Update action — resource-limit validation, argv builder, preview plan for
docker-update.

Applies ``--memory`` and/or ``--cpus`` to a running Docker container.
Memory values are parsed by the same ``parse_size`` helper from
``topos.actions.squeeze`` (suffix handling, overflow, max, range) — the
same validated path P49's governance module established.  ``--cpus`` is a
bounded positive float.

Refuses a memory limit below the container's current ``memory.current``
usage (read from cgroupfs) at plan time, unless ``--below-current`` is
passed.

``update`` against systemd targets is refused with a message pointing at
``topos action set-property`` — no second governance path.

No subprocess, no shell, no host mutation.
"""

from __future__ import annotations

import dataclasses
import math
import os
import re
from collections.abc import Callable
from pathlib import Path

from topos.actions.catalog import DOCKER_EXECUTABLE, ActionKind

# ---------------------------------------------------------------------------
# CPU validation
# ---------------------------------------------------------------------------

# Maximum safe CPU count — injectable for tests.
_DEFAULT_MAX_CPUS: float = float(os.cpu_count() or 64)


def validate_cpus(value: str, max_cpus: float | None = None) -> float:
    """Validate a ``--cpus`` value.

    Accepts a positive finite float.  The upper bound is the host CPU
    count (from ``os.cpu_count()``) or an explicit *max_cpus*.

    Returns:
        The validated CPU count as a float.

    Raises:
        ValueError: On invalid input.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("cpus must be a non-empty string")
    try:
        cpus = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"cpus must be a number: {value!r}")
    if not math.isfinite(cpus):
        raise ValueError(f"cpus must be a finite number: {value!r}")
    if cpus <= 0:
        raise ValueError(f"cpus must be positive: {value!r}")
    upper = max_cpus if max_cpus is not None else _DEFAULT_MAX_CPUS
    if cpus > upper:
        raise ValueError(
            f"cpus value {cpus} exceeds host CPU count {int(upper)}"
        )
    return cpus


# ---------------------------------------------------------------------------
# Memory validation (reuses squeeze.parse_size for suffix support)
# ---------------------------------------------------------------------------


def validate_memory(value: str, max_bytes: int | None = None) -> int:
    """Validate a ``--memory`` value and convert to bytes.

    Accepts suffixed values (``512M``, ``2G``, ``1024K``) or a bare byte
    count via ``parse_size`` from the squeeze module (P49-established
    validated code path).

    Args:
        value: Size string (e.g. ``"512M"``, ``"2G"``, ``"1073741824"``).
        max_bytes: Optional maximum byte value (default 2^63-1).

    Returns:
        The validated byte count.

    Raises:
        ValueError: On invalid input.
    """
    from topos.actions.squeeze import parse_size

    if not isinstance(value, str) or not value:
        raise ValueError("memory must be a non-empty string")
    try:
        parsed = parse_size(value)
    except ValueError:
        raise ValueError(f"invalid memory value: {value!r}")
    if parsed <= 0:
        raise ValueError(f"memory must be positive: {value!r}")
    upper = max_bytes if max_bytes is not None else 2**63 - 1
    if parsed > upper:
        raise ValueError(f"memory value {parsed} exceeds maximum {upper}")
    return parsed


# ---------------------------------------------------------------------------
# Current-memory reader (injectable for tests)
# ---------------------------------------------------------------------------

# Type for injectable current-memory reader for update actions.
# Takes the target (container name or cgroup path) and returns the
# current memory usage in bytes, or None if unreadable.
CurrentMemoryReader = Callable[[str], int | None]


def _default_current_memory_reader(target: str) -> int | None:
    """Read a container's current memory usage in bytes, or None if unreadable.

    An ``update`` target is a Docker container name or 64-hex id
    (``catalog.validate_target``), never a path — so this resolves the name to
    its cgroup key through one collector sweep, the same path ``--container``
    resolution already uses (``cli._resolve_container_target``), and reads
    ``memory.current`` from the resolved cgroup.

    Returning None means "current usage could not be established", which callers
    treat as a refusal unless ``--below-current`` is passed: an unverifiable
    usage must not silently permit a limit that OOM-kills the container.
    """
    key = target
    if "/" not in target:
        try:
            from topos.collect.collector import Collector
            from topos.collect.dockerjoin import resolve_container_key
            from topos.config import load

            frame = Collector(config=load(None)).collect_once()
            entities = {k: ef.entity for k, ef in frame.entities.items()}
            key = resolve_container_key(target, entities)
        except BaseException:
            return None
    try:
        raw = (Path("/sys/fs/cgroup") / key / "memory.current").read_text(encoding="utf-8").strip()
        return int(raw)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Update argv builder
# ---------------------------------------------------------------------------


def build_update_argv(
    target: str,
    *,
    memory: int | None = None,
    cpus: float | None = None,
) -> list[str]:
    """Build the docker update argv for memory and/or CPU changes.

    Args:
        target: Docker container identifier.
        memory: Memory limit in bytes (validated).
        cpus: CPU limit (validated).

    Returns:
        The argv list for ``subprocess.run`` (no shell).

    Raises:
        ValueError: On invalid inputs.
    """
    if not isinstance(target, str) or not target:
        raise ValueError("target must be a non-empty string")
    _reject_systemd_target(target)

    if memory is None and cpus is None:
        raise ValueError("at least one of --memory or --cpus is required")
    if memory is not None and (not isinstance(memory, int) or memory <= 0):
        raise ValueError("memory must be a positive integer")
    if cpus is not None and (not isinstance(cpus, (int, float)) or cpus <= 0):
        raise ValueError("cpus must be a positive number")

    argv = [DOCKER_EXECUTABLE, "update"]
    if memory is not None:
        argv.extend(["--memory", str(memory)])
    if cpus is not None:
        argv.extend(["--cpus", str(cpus)])
    argv.append(target)
    return argv


# ---------------------------------------------------------------------------
# UpdatePlan — immutable preview plan
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class UpdatePlan:
    """Immutable preview plan for one docker-update action.

    Includes the requested memory/CPU values, the current memory usage
    (if available), and whether the below-current override was passed.
    """

    kind: str  # "docker-update"
    target: str
    memory: int | None
    cpus: float | None
    argv: tuple[str, ...]
    current_memory: int | None = None
    below_current: bool = False
    description: str = (
        "Update Docker container resource limits."
    )
    mode: str = "preview"


# ---------------------------------------------------------------------------
# Systemd target check -- refuse update on systemd units
# ---------------------------------------------------------------------------


_SYSTEMD_SUFFIX_RE = re.compile(r"\.(?:service|slice|scope|target|socket|mount|timer|path)$")


def _reject_systemd_target(target: str) -> None:
    """Refuse update for systemd unit targets.

    Systemd resource changes are P49's ``set-property`` surface.
    """
    if _SYSTEMD_SUFFIX_RE.search(target):
        raise ValueError(
            f"target {target!r} looks like a systemd unit; "
            "use 'topos action set-property' for systemd resource changes"
        )


# ---------------------------------------------------------------------------
# Preview builder
# ---------------------------------------------------------------------------


def build_update_preview(
    target: str,
    *,
    memory: str | None = None,
    cpus: str | None = None,
    below_current: bool = False,
    current_memory_reader: CurrentMemoryReader | None = None,
) -> UpdatePlan:
    """Build a structured preview plan for a docker-update action.

    Args:
        target: Docker container identifier.
        memory: Memory limit (suffixed or bare bytes), or None.
        cpus: CPU count, or None.
        below_current: Allow memory limit below current usage.
        current_memory_reader: Injectable reader for current memory usage.

    Returns:
        An ``UpdatePlan`` with the preview argv.

    Raises:
        ValueError: On invalid inputs.
    """
    if memory is None and cpus is None:
        raise ValueError("at least one of --memory or --cpus is required")

    # Before anything else: a systemd target belongs to set-property, and the
    # operator must be told so.  Ordering matters -- the current-usage check below
    # would otherwise answer a systemd target with "usage could not be established",
    # which is true but useless (contract 8 / oracle 6 require the pointer).
    _reject_systemd_target(target)

    parsed_memory: int | None = None
    if memory is not None:
        parsed_memory = validate_memory(memory)

    parsed_cpus: float | None = None
    if cpus is not None:
        parsed_cpus = validate_cpus(cpus)

    # Read current memory usage.  A memory limit below current usage OOM-kills the
    # container immediately, so the check is fail-closed: an unreadable current
    # usage is refused exactly like a breach, under the same override flag.  Only
    # a --memory request needs it; a --cpus-only update cannot OOM anything.
    reader = current_memory_reader or _default_current_memory_reader
    current_usage: int | None = None
    if parsed_memory is not None:
        try:
            current_usage = reader(target)
        except BaseException:
            current_usage = None

        if not below_current:
            if current_usage is None:
                raise ValueError(
                    f"current memory usage of {target!r} could not be established, so a "
                    f"limit of {parsed_memory} bytes cannot be shown to be safe; pass "
                    "--below-current to apply it anyway (this may OOM the container)"
                )
            if parsed_memory < current_usage:
                raise ValueError(
                    f"memory limit {parsed_memory} bytes is below current "
                    f"usage {current_usage} bytes; use --below-current to "
                    "override (this may OOM the container)"
                )

    argv = build_update_argv(target, memory=parsed_memory, cpus=parsed_cpus)

    return UpdatePlan(
        kind="docker-update",
        target=target,
        memory=parsed_memory,
        cpus=parsed_cpus,
        argv=tuple(argv),
        current_memory=current_usage,
        below_current=below_current,
    )


# ---------------------------------------------------------------------------
# Render helpers for preview display
# ---------------------------------------------------------------------------


def render_update_preview(plan: UpdatePlan) -> str:
    """Render a human-readable preview of an update action."""
    parts = [
        f"Action: {plan.kind}",
        f"Target: {plan.target}",
    ]
    if plan.memory is not None:
        parts.append(f"Memory: {plan.memory} bytes")
        if plan.current_memory is not None:
            parts.append(f"Current memory usage: {plan.current_memory} bytes")
    if plan.cpus is not None:
        parts.append(f"CPUs: {plan.cpus}")
    parts.extend([
        f"Below-current override: {'yes' if plan.below_current else 'no'}",
        f"Command argv: {list(plan.argv)}",
        f"Description: {plan.description}",
        "Mode: preview only; no command was executed",
    ])
    return "\n".join(parts)


def update_plan_to_jsonable(plan: UpdatePlan) -> dict[str, object]:
    """Convert an UpdatePlan to JSON-safe data."""
    return {
        "argv": list(plan.argv),
        "below_current": plan.below_current,
        "cpus": plan.cpus,
        "current_memory": plan.current_memory,
        "description": plan.description,
        "kind": plan.kind,
        "memory": plan.memory,
        "mode": plan.mode,
        "target": plan.target,
    }
