"""systemd memory.high governance — structured property/value validation,
current-value reader, and argv builder for systemctl set-property.

This module implements the P49 memory.high governance action on top of the
P46 execution kernel.  It validates only ``memory.high`` with ``max`` or a
canonical positive byte value (overflow/range checked), detects persistence
mode based on unit type, reads the current value via ``systemctl show``, and
revalidates it before execution (stale detection).

No subprocess import, no shell, no cgroupfs writes.  The injectable
current-value reader and runner are test-only API fixtures.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable

from groop.actions.catalog import SYSTEMCTL_EXECUTABLE

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The only property this governance module allows.
ALLOWED_PROPERTY = "memory.high"

# Regex for a canonical positive byte value.
_BYTE_VALUE_RE = re.compile(r"^[+]?(\d+)$")

# Unit suffix patterns for persistence detection.
# Transient/container scopes default to --runtime; slices/services default to
# persistent (which is the safer default for durable configuration).
_TRANSIENT_UNIT_RE = re.compile(
    r"\.scope$"
)

# Maximum safe byte value (2^63 - 1) — systemd's property parser uses int64.
_MAX_BYTE_VALUE = 2**63 - 1

# Default systemctl show executable path and property column.
_SYSTEMCTL_EXECUTABLE = SYSTEMCTL_EXECUTABLE


# ---------------------------------------------------------------------------
# Value validation
# ---------------------------------------------------------------------------


def validate_memory_high_value(value: str) -> str:
    """Validate a ``memory.high`` value.

    Accepts the literal ``"max"`` or a canonical positive integer byte count
    without sign, whitespace, percentage, floating point, or suffix.

    Returns the canonical value string (``"max"`` or the decimal byte count).
    Raises ``ValueError`` on invalid input.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("memory.high value must be a non-empty string")

    # Reject whitespace, control characters, commas
    if any(
        ord(c) <= 0x20 or ord(c) == 0x7F or c in {",", ";", "'", '"'}
        for c in value
    ):
        raise ValueError(
            f"memory.high value contains invalid characters: {value!r}"
        )

    # Reject signs (except optional leading +), percentages, decimals
    if value.startswith("-"):
        raise ValueError(
            f"memory.high value must not be negative: {value!r}"
        )
    if value.startswith("+"):
        # Strip leading + for the canonical form
        value = value[1:]
    if "%" in value:
        raise ValueError(
            f"memory.high value must not contain %: {value!r}"
        )
    if "." in value:
        raise ValueError(
            f"memory.high value must be an integer byte count: {value!r}"
        )

    if value == "max":
        return "max"

    # Must be a positive integer
    m = _BYTE_VALUE_RE.match(value)
    if not m:
        raise ValueError(
            f"memory.high value must be 'max' or a positive integer: {value!r}"
        )

    # Range and overflow check
    try:
        byte_val = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"memory.high value is not a valid integer: {value!r}"
        ) from exc

    if byte_val < 1:
        raise ValueError(
            f"memory.high value must be positive (got {byte_val})"
        )
    if byte_val > _MAX_BYTE_VALUE:
        raise ValueError(
            f"memory.high value {byte_val} exceeds maximum {_MAX_BYTE_VALUE}"
        )

    return str(byte_val)


def validate_memory_high_unit(unit: str) -> None:
    """Validate a systemd unit name for set-property operations.

    Uses the same validation as the execution kernel's ``_SYSTEMD_UNIT_RE``.
    """
    from groop.actions.catalog import _SYSTEMD_UNIT_RE  # reuse existing

    if not isinstance(unit, str) or not unit:
        raise ValueError("unit must be a non-empty string")
    if unit.startswith("-"):
        raise ValueError(f"unit must not be option-like: {unit!r}")
    if ".." in unit or not _SYSTEMD_UNIT_RE.fullmatch(unit):
        raise ValueError(f"invalid systemd unit name: {unit!r}")


# ---------------------------------------------------------------------------
# Persistence mode detection
# ---------------------------------------------------------------------------


def detect_default_persistence(unit: str) -> str:
    """Detect the default persistence mode for a given unit name.

    ``.scope`` units (transient/container scopes) default to ``--runtime``.
    Slice, service, and other durable unit types default to persistent
    (no --runtime flag), which is the safer default for durable cgroup
    configuration.
    """
    if _TRANSIENT_UNIT_RE.search(unit):
        return "runtime"
    return "persistent"


def validate_persistence_mode(mode: str) -> str:
    """Validate an explicit persistence mode.

    Accepts ``"runtime"`` or ``"persistent"`` (case-insensitive).
    Returns the canonical lowercase form.
    """
    if not isinstance(mode, str) or mode.lower() not in {"runtime", "persistent"}:
        raise ValueError(
            f"persistence mode must be 'runtime' or 'persistent', got {mode!r}"
        )
    return mode.lower()


# ---------------------------------------------------------------------------
# Current-value reader (injectable for tests)
# ---------------------------------------------------------------------------


def _systemctl_show_reader(unit: str) -> str | None:
    """Read the current ``memory.high`` value for *unit* via ``systemctl show``.

    Returns the value as a string (e.g. ``"max"``, ``"1073741824"``) or
    ``None`` if the unit does not exist or the property is not readable.

    Uses a process runner matching the P46 execution kernel pattern for
    injectability.
    """
    import subprocess  # noqa: PLC0415  # intentional local import for test seam

    try:
        proc = subprocess.run(
            [_SYSTEMCTL_EXECUTABLE, "show", "--property", "MemoryHigh", "--value", unit],
            capture_output=True,
            text=True,
            timeout=10.0,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None

    if proc.returncode != 0:
        return None

    raw = proc.stdout.strip()
    if not raw or raw in {"", "(null)", "infinity"}:
        return None

    return raw


# Type for injectable current-value readers.
# Returns the current string value or None if unreadable.
CurrentValueReader = Callable[[str], str | None]

_DEFAULT_CURRENT_VALUE_READER: CurrentValueReader = _systemctl_show_reader


# ---------------------------------------------------------------------------
# argv builder
# ---------------------------------------------------------------------------


def build_set_property_argv(
    unit: str,
    property_name: str,
    property_value: str,
    *,
    persistence: str = "persistent",
) -> list[str]:
    """Build the systemctl set-property argv for a memory.high adjustment.

    Args:
        unit: The systemd unit name (e.g. ``"my.slice"``, ``"user@1000.service"``).
        property_name: Must be ``"memory.high"``.
        property_value: ``"max"`` or a validated positive byte count.
        persistence: ``"runtime"`` (adds ``--runtime``) or ``"persistent"``.

    Returns:
        The argv list for ``subprocess.run`` (no shell).

    Raises:
        ValueError: On invalid inputs.
    """
    if not isinstance(unit, str) or not unit:
        raise ValueError("unit must be a non-empty string")
    if property_name != ALLOWED_PROPERTY:
        raise ValueError(
            f"property must be {ALLOWED_PROPERTY!r}, got {property_name!r}"
        )

    # Validate the value (this also normalises it)
    canonical_value = validate_memory_high_value(property_value)

    # Validate the unit
    validate_memory_high_unit(unit)

    # Validate persistence mode
    persistence = validate_persistence_mode(persistence)

    argv = [_SYSTEMCTL_EXECUTABLE, "set-property"]
    if persistence == "runtime":
        argv.append("--runtime")
    argv.extend([unit, f"{ALLOWED_PROPERTY}={canonical_value}"])
    return argv


# ---------------------------------------------------------------------------
# SetPropertyPlan — structured preview plan for systemd set-property
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SetPropertyPlan:
    """Immutable preview plan for one systemd memory.high adjustment.

    This is intentionally a separate type from ``ActionPlan`` because the
    set-property action has structured unit/property/value inputs that differ
    from the simple kind+target pattern of start/stop/restart actions.

    The plan includes the current value (if available) for stale detection
    and the persistence mode for display.
    """

    kind: str  # "systemd-set-property"
    unit: str
    property_name: str
    property_value: str
    argv: tuple[str, ...]
    current_value: str | None
    persistence: str
    description: str = (
        "Set memory.high via systemctl set-property for cgroup memory governance."
    )
    mode: str = "preview"


# ---------------------------------------------------------------------------
# Preview builder
# ---------------------------------------------------------------------------


def build_set_property_preview(
    unit: str,
    property_name: str = ALLOWED_PROPERTY,
    property_value: str = "",
    *,
    persistence: str | None = None,
    current_value_reader: CurrentValueReader | None = None,
) -> SetPropertyPlan:
    """Build a structured preview plan for a systemd memory.high adjustment.

    Args:
        unit: The systemd unit name.
        property_name: Must be ``"memory.high"``.
        property_value: ``"max"`` or a validated positive byte count.
        persistence: ``"runtime"``, ``"persistent"``, or ``None`` to auto-detect.
        current_value_reader: Injectable reader for the current value.

    Returns:
        A ``SetPropertyPlan`` with the preview argv, current value, and
        persistence mode.

    Raises:
        ValueError: On invalid inputs.
    """
    # Default persistence based on unit type
    if persistence is None:
        persistence = detect_default_persistence(unit)
    else:
        persistence = validate_persistence_mode(persistence)

    # Build the argv
    argv = build_set_property_argv(
        unit, property_name, property_value, persistence=persistence
    )

    # Read the current value
    reader = current_value_reader or _DEFAULT_CURRENT_VALUE_READER
    try:
        current_value = reader(unit)
    except BaseException:
        current_value = None

    return SetPropertyPlan(
        kind="systemd-set-property",
        unit=unit,
        property_name=property_name,
        property_value=property_value,
        argv=tuple(argv),
        current_value=current_value,
        persistence=persistence,
    )


# ---------------------------------------------------------------------------
# Render helpers for preview display
# ---------------------------------------------------------------------------


def render_set_property_preview(plan: SetPropertyPlan) -> str:
    """Render a human-readable preview of a set-property action."""
    lines = [
        f"Action: {plan.kind}",
        f"Unit: {plan.unit}",
        f"Property: {plan.property_name}",
        f"Current value: {plan.current_value or 'unavailable'}",
        f"New value: {plan.property_value}",
        f"Persistence: {plan.persistence}",
        f"Command argv: {list(plan.argv)}",
        f"Description: {plan.description}",
        "Mode: preview only; no command was executed",
    ]
    return "\n".join(lines)


def set_property_plan_to_jsonable(plan: SetPropertyPlan) -> dict[str, object]:
    """Convert a SetPropertyPlan to JSON-safe data."""
    return {
        "argv": list(plan.argv),
        "current_value": plan.current_value,
        "description": plan.description,
        "kind": plan.kind,
        "mode": plan.mode,
        "persistence": plan.persistence,
        "property": plan.property_name,
        "target": plan.unit,
        "value": plan.property_value,
    }
