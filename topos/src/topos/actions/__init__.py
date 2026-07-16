"""Action planning and execution module.

This package builds immutable preview argv plans for a small allowlisted
catalog of Docker and systemd actions.  The preview path (P21) never executes
commands.  The execution path (P46) validates targets, gates on --admin +
--confirm EXECUTE, runs argv-only subprocess calls with a clean minimal
environment, bounded timeout, durable audit, and returns typed results.

P49 adds structured ``memory.high`` set-property governance through
``SetPropertyPlan`` / ``build_set_property_preview()``, reusing the P46
execution gates with additional stale detection.

P72 adds ``kill`` (docker-kill/systemd-kill) and ``update`` (docker-update)
verbs.  ``kill`` validates signals against a closed allowlist and requires
``--force`` for KILL.  ``update`` validates ``--memory``/``--cpus`` limits and
refuses memory limits below current usage unless ``--below-current`` is passed.

Exposed public API:
    ActionKind — enum of allowed action kinds.
    ActionPlan — immutable preview plan (kind, target, argv, description).
    build_preview(kind, target) — build an ActionPlan (raises for unknown kinds).
    build_admin_preview(kind, target, admin=False) — gated version (P49:
        also accepts property_name, property_value for systemd-set-property;
        P72: also accepts signal/force for kill, memory/cpus for update).
    SetPropertyPlan — structured preview plan for memory.high governance.
    build_set_property_preview(unit, property_name, property_value, ...) —
        build a SetPropertyPlan.
    KillPlan — structured preview plan for kill actions.
    build_kill_preview(kind, target, signal, force) — build a KillPlan.
    UpdatePlan — structured preview plan for update actions.
    build_update_preview(target, memory, cpus) — build an UpdatePlan.
    AuditLog — append-only JSONL audit logger.
    ExecuteResult — typed execution result.
    execute_plan(kind, target, *, admin, confirm, audit_path, runner, clock) —
        gated execution entry point. Production defaults to the fixed
        /var/log/topos/actions.jsonl audit; fixture paths are API-only.
    execute_set_property(unit, *, property_name, property_value, ...) —
        execute a systemd set-property action (P49).
    execute_kill(kind, target, *, signal, force, ...) —
        execute a kill action (P72).
    execute_update(target, *, memory, cpus, ...) —
        execute a docker-update action (P72).
    validate_target(kind, target) — validate target safety (shared by preview
        and execution).
"""

from topos.actions.catalog import (
    ACTION_CATALOG,
    DOCKER_EXECUTABLE,
    EXECUTION_ALLOWLIST,
    SYSTEMCTL_EXECUTABLE,
    ActionKind,
)
from topos.actions.governance import (
    SetPropertyPlan,
    build_set_property_preview,
    render_set_property_preview,
    set_property_plan_to_jsonable,
    validate_memory_high_value,
    validate_memory_high_unit,
)
from topos.actions.kill_ops import (
    KillPlan,
    build_kill_preview,
    kill_plan_to_jsonable,
    render_kill_preview,
    validate_signal,
)
from topos.actions.update_ops import (
    UpdatePlan,
    build_update_preview,
    update_plan_to_jsonable,
    render_update_preview,
    validate_cpus,
    validate_memory,
)
from topos.actions.squeeze import (
    SqueezeConfig,
    SqueezeResult,
    SqueezeStep,
    parse_size,
    render_squeeze_result,
    run_squeeze,
    run_squeeze_gated,
    squeeze_result_to_jsonable,
)
from topos.actions.preview import ActionPlan, build_preview, build_admin_preview
from topos.actions.audit import AuditLog, AuditRecord
from topos.actions.execute import AuditIdentity, ExecuteResult, execute_plan, execute_set_property, execute_kill, execute_update, validate_target

__all__ = [
    "ActionKind",
    "ACTION_CATALOG",
    "EXECUTION_ALLOWLIST",
    "DOCKER_EXECUTABLE",
    "SYSTEMCTL_EXECUTABLE",
    "ActionPlan",
    "build_preview",
    "build_admin_preview",
    "SetPropertyPlan",
    "build_set_property_preview",
    "render_set_property_preview",
    "set_property_plan_to_jsonable",
    "validate_memory_high_value",
    "validate_memory_high_unit",
    "KillPlan",
    "build_kill_preview",
    "kill_plan_to_jsonable",
    "render_kill_preview",
    "validate_signal",
    "UpdatePlan",
    "build_update_preview",
    "update_plan_to_jsonable",
    "render_update_preview",
    "validate_cpus",
    "validate_memory",
    "AuditLog",
    "AuditRecord",
    "ExecuteResult",
    "AuditIdentity",
    "execute_plan",
    "execute_set_property",
    "execute_kill",
    "execute_update",
    "validate_target",
    "SqueezeConfig",
    "SqueezeResult",
    "SqueezeStep",
    "parse_size",
    "render_squeeze_result",
    "run_squeeze",
    "run_squeeze_gated",
    "squeeze_result_to_jsonable",
]
