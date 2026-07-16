"""Preview planner — builds immutable ActionPlan objects.

build_preview() validates the target and builds a plan for known kinds.
build_admin_preview() gates on --admin and returns a DisabledPlan if admin mode
is not active.

P72 adds ``KillPlan`` (for docker-kill/systemd-kill) and ``UpdatePlan`` (for
docker-update) — structured plan types that carry verb-specific arguments
(signal/force, memory/cpus) in addition to the common kind+target+argv.
"""

from __future__ import annotations

import dataclasses

from topos.actions.catalog import ACTION_CATALOG, ActionKind, validate_target
from topos.actions.governance import (
    SetPropertyPlan,
    build_set_property_preview as _build_set_property_preview,
)
from topos.actions.kill_ops import KillPlan, build_kill_preview as _build_kill_preview
from topos.actions.update_ops import UpdatePlan, build_update_preview as _build_update_preview


@dataclasses.dataclass(frozen=True)
class ActionPlan:
    """Immutable preview of one admin action. Never executed."""

    kind: ActionKind
    target: str
    argv: tuple[str, ...]
    description: str
    mode: str = "preview"


@dataclasses.dataclass(frozen=True)
class DisabledPlan:
    """Returned when admin mode is not enabled."""

    kind: ActionKind
    target: str
    message: str = "admin mode is not enabled; re-run with --admin to preview commands"
    mode: str = "disabled"


AdminPreviewResult = ActionPlan | SetPropertyPlan | KillPlan | UpdatePlan | DisabledPlan


def build_preview(kind: str, target: str) -> ActionPlan:
    """Build an ActionPlan for the given kind and target.

    Validates the target for safety, then builds the argv preview.
    Raises ValueError for unknown action kinds or invalid targets.

    Note: For ``SYSTEMD_SET_PROPERTY``, use ``build_admin_preview()`` with
    ``property_name`` and ``property_value`` instead, which routes through
    the structured governance.py path.  This function rejects set-property
    targets that include property assignments.

    Note: For ``docker-kill``, ``systemd-kill``, and ``docker-update``, use
    ``build_admin_preview()`` with verb-specific arguments (--signal/--force
    for kill, --memory/--cpus for update).  The basic catalog-level preview
    omits those arguments.
    """
    ak = ActionKind(kind)  # raises ValueError for invalid kind name
    validate_target(ak, target)
    entry = ACTION_CATALOG[ak]
    argv = entry.builder(target)
    return ActionPlan(
        kind=ak,
        target=target,
        argv=tuple(argv),
        description=entry.description,
    )


def build_admin_preview(
    kind: str,
    target: str,
    *,
    admin: bool = False,
    property_name: str | None = None,
    property_value: str | None = None,
    persistence: str | None = None,
    # P72 kill-specific arguments
    signal: str | None = None,
    force: bool = False,
    # P72 update-specific arguments
    memory: str | None = None,
    cpus: str | None = None,
    below_current: bool = False,
    current_memory_reader: object = None,
) -> AdminPreviewResult:
    """Build a preview gated on --admin.

    Without admin=True, returns a DisabledPlan instead of an ActionPlan.

    For ``SYSTEMD_SET_PROPERTY``, pass ``property_name`` and
    ``property_value`` for structured governance preview.  Without them the
    preview falls back to the composite target format (which raises a clear
    error for invalid inputs).

    For ``docker-kill`` / ``systemd-kill``, pass ``signal`` (and ``force``
    for KILL signal).

    For ``docker-update``, pass ``memory`` and/or ``cpus``.
    """
    if not admin:
        ak = ActionKind(kind)
        return DisabledPlan(kind=ak, target=target)
    if kind == ActionKind.SYSTEMD_SET_PROPERTY.value or kind == "systemd-set-property":
        if property_name is not None and property_value is not None:
            return _build_set_property_preview(
                target,
                property_name=property_name,
                property_value=property_value,
                persistence=persistence,
            )
        # If no structured inputs, fall through to the catalog preview which
        # will reject composite targets with a clear error.
    if kind in ("docker-kill", "systemd-kill"):
        return _build_kill_preview(
            kind, target, signal=signal or "TERM", force=force,
        )
    if kind == "docker-update":
        return _build_update_preview(
            target,
            memory=memory,
            cpus=cpus,
            below_current=below_current,
            current_memory_reader=current_memory_reader,
        )
    return build_preview(kind, target)
