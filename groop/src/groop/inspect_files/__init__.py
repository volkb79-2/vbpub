"""Inspect-files planning module — read-only file/log inspection safety skeleton.

This package builds immutable read-only inspection plans for a small allowlisted
set of log and cgroup file paths. It never reads file contents, never tails
logs, never executes subprocesses, and never mutates host state.

Disabled by default — requires both --inspect-files and --admin flags.

Exposed public API:
    InspectFilesKind — enum of allowed inspection plan kinds.
    InspectFilesPlan — immutable inspection plan (kind, target, kind_label,
                       description, path_previews, command_previews).
    DisabledInspector — returned when gating flags are not enabled.
    build_inspect_plan(kind, target) — build an InspectFilesPlan.
    build_gated_inspect_plan(kind, target, inspect_files=False, admin=False)
        — gated version.
"""

from groop.inspect_files.catalog import InspectFilesKind, INSPECT_CATALOG
from groop.inspect_files.plan import (
    DisabledInspector,
    InspectFilesPlan,
    build_gated_inspect_plan,
    build_inspect_plan,
)

__all__ = [
    "InspectFilesKind",
    "INSPECT_CATALOG",
    "InspectFilesPlan",
    "DisabledInspector",
    "build_inspect_plan",
    "build_gated_inspect_plan",
]
