"""Inspect-files planning and bounded content-read module.

This package builds immutable read-only inspection plans and performs bounded
content reads for a small allowlisted set of log and cgroup file paths.  It
never runs subprocesses, never mutates host state, and never reads arbitrary
root files.

Disabled by default — requires both --inspect-files and --admin flags.

Exposed public API:
    InspectFilesKind — enum of allowed inspection plan kinds.
    InspectFilesPlan — immutable inspection plan.
    DisabledInspector — returned when gating flags are not enabled.
    InspectFilesReadResult — bounded file content result.
    InspectFilesReadError — file-read error result.
    ReadDenied — read denied when gating flags are inactive.
    build_inspect_plan(kind, target) — build an InspectFilesPlan.
    build_gated_inspect_plan(kind, target, inspect_files, admin) — gated plan.
    build_inspect_read(kind, target, ...) — bounded file content read.
"""

from groop.inspect_files.catalog import InspectFilesKind, INSPECT_CATALOG
from groop.inspect_files.plan import (
    DisabledInspector,
    InspectFilesPlan,
    build_gated_inspect_plan,
    build_inspect_plan,
)
from groop.inspect_files.reader import (
    InspectFilesReadError,
    InspectFilesReadResult,
    ReadDenied,
    build_inspect_read,
)

__all__ = [
    "InspectFilesKind",
    "INSPECT_CATALOG",
    "InspectFilesPlan",
    "DisabledInspector",
    "InspectFilesReadResult",
    "InspectFilesReadError",
    "ReadDenied",
    "build_inspect_plan",
    "build_gated_inspect_plan",
    "build_inspect_read",
]
