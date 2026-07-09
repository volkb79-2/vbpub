"""Preview planner — builds immutable ActionPlan objects.

build_preview() always succeeds for known kinds.
build_admin_preview() gates on --admin and returns a DisabledPlan if admin mode
is not active.
"""

from __future__ import annotations

import dataclasses
import datetime
import typing

from groop.actions.catalog import ACTION_CATALOG, ActionKind


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


AdminPreviewResult = ActionPlan | DisabledPlan


def build_preview(kind: str, target: str) -> ActionPlan:
    """Build an ActionPlan for the given kind and target.

    Raises KeyError for unknown action kinds.
    """
    ak = ActionKind(kind)  # raises ValueError for invalid kind name
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
) -> AdminPreviewResult:
    """Build a preview gated on --admin.

    Without admin=True, returns a DisabledPlan instead of an ActionPlan.
    """
    if not admin:
        ak = ActionKind(kind)
        return DisabledPlan(kind=ak, target=target)
    return build_preview(kind, target)
