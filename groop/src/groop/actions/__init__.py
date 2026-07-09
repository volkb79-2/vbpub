"""Action planning module — preview-only admin action gating skeleton.

This package builds immutable preview argv plans for a small allowlisted
catalog of Docker and systemd actions. It never executes commands, never
calls subprocess, and never mutates host state.

Exposed public API:
    ActionKind — enum of allowed action kinds.
    ActionPlan — immutable preview plan (kind, target, argv, description).
    build_preview(kind, target) — build an ActionPlan (raises for unknown kinds).
    build_admin_preview(kind, target, admin=False) — gated version.
    AuditLog — append-only JSONL audit logger.
"""

from groop.actions.catalog import ActionKind, ACTION_CATALOG
from groop.actions.preview import ActionPlan, build_preview, build_admin_preview
from groop.actions.audit import AuditLog, AuditRecord

__all__ = [
    "ActionKind",
    "ACTION_CATALOG",
    "ActionPlan",
    "build_preview",
    "build_admin_preview",
    "AuditLog",
    "AuditRecord",
]
