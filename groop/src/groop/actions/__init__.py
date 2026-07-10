"""Action planning and execution module.

This package builds immutable preview argv plans for a small allowlisted
catalog of Docker and systemd actions.  The preview path (P21) never executes
commands.  The execution path (P46) validates targets, gates on --admin +
--confirm EXECUTE, runs argv-only subprocess calls with a clean minimal
environment, bounded timeout, durable audit, and returns typed results.

Preview is available for all catalog kinds.  Execution is restricted to the
EXECUTION_ALLOWLIST (start/stop/restart only).

Exposed public API:
    ActionKind — enum of allowed action kinds.
    ActionPlan — immutable preview plan (kind, target, argv, description).
    build_preview(kind, target) — build an ActionPlan (raises for unknown kinds).
    build_admin_preview(kind, target, admin=False) — gated version.
    AuditLog — append-only JSONL audit logger.
    ExecuteResult — typed execution result.
    execute_plan(kind, target, *, admin, confirm, audit_path, runner, clock) —
        gated execution entry point. Production defaults to the fixed
        /var/log/groop/actions.jsonl audit; fixture paths are API-only.
    validate_target(kind, target) — validate target safety (shared by preview
        and execution).
"""

from groop.actions.catalog import (
    ACTION_CATALOG,
    DOCKER_EXECUTABLE,
    EXECUTION_ALLOWLIST,
    SYSTEMCTL_EXECUTABLE,
    ActionKind,
)
from groop.actions.preview import ActionPlan, build_preview, build_admin_preview
from groop.actions.audit import AuditLog, AuditRecord
from groop.actions.execute import AuditIdentity, ExecuteResult, execute_plan, validate_target

__all__ = [
    "ActionKind",
    "ACTION_CATALOG",
    "EXECUTION_ALLOWLIST",
    "DOCKER_EXECUTABLE",
    "SYSTEMCTL_EXECUTABLE",
    "ActionPlan",
    "build_preview",
    "build_admin_preview",
    "AuditLog",
    "AuditRecord",
    "ExecuteResult",
    "AuditIdentity",
    "execute_plan",
    "validate_target",
]
