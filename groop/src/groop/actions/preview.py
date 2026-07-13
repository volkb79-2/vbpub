"""Preview planner — builds immutable ActionPlan objects.

build_preview() validates the target and builds a plan for known kinds.
build_admin_preview() gates on --admin and returns a DisabledPlan if admin mode
is not active.
"""

from __future__ import annotations

import dataclasses

from groop.actions.catalog import ACTION_CATALOG, ActionKind, validate_target
from groop.actions.governance import (
    SetPropertyPlan,
    build_set_property_preview as _build_set_property_preview,
)


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


AdminPreviewResult = ActionPlan | SetPropertyPlan | DisabledPlan


def build_preview(kind: str, target: str) -> ActionPlan:
    """Build an ActionPlan for the given kind and target.

    Validates the target for safety, then builds the argv preview.
    Raises ValueError for unknown action kinds or invalid targets.

    Note: For ``SYSTEMD_SET_PROPERTY``, use ``build_admin_preview()`` with
    ``property_name`` and ``property_value`` instead, which routes through
    the structured governance.py path.  This function rejects set-property
    targets that include property assignments.
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
) -> AdminPreviewResult:
    """Build a preview gated on --admin.

    Without admin=True, returns a DisabledPlan instead of an ActionPlan.

    For ``SYSTEMD_SET_PROPERTY``, pass ``property_name`` and
    ``property_value`` for structured governance preview.  Without them the
    preview falls back to the composite target format (which raises a clear
    error for invalid inputs).
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
    return build_preview(kind, target)
