"""Inspection plan builder — constructs immutable InspectFilesPlan objects.

build_inspect_plan() always succeeds for known kinds.
build_gated_inspect_plan() gates on --inspect-files and --admin flags,
returning a DisabledInspector if either is not active.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from topos.inspect_files.catalog import INSPECT_CATALOG, InspectFilesKind


@dataclasses.dataclass(frozen=True)
class InspectFilesPlan:
    """Immutable read-only inspection plan. Never reads file contents,
    never executes commands, never mutates host state."""

    kind: InspectFilesKind
    target: str
    kind_label: str
    description: str
    path_previews: tuple[Path, ...]
    command_previews: tuple[tuple[str, ...], ...]
    mode: str = "plan"

    def to_jsonable(self) -> dict:
        """Convert to a JSON-safe dict for --json output."""
        return {
            "kind": self.kind.value,
            "target": self.target,
            "kind_label": self.kind_label,
            "description": self.description,
            "path_previews": [str(p) for p in self.path_previews],
            "command_previews": [list(cmd) for cmd in self.command_previews],
            "mode": self.mode,
        }

    def to_text(self) -> str:
        """Render as human-readable text."""
        lines = [
            f"Inspection Plan: {self.kind.value}",
            f"Target: {self.target}",
            f"Kind: {self.kind_label}",
            f"Description: {self.description}",
            "",
            "Path previews:",
        ]
        for p in self.path_previews:
            lines.append(f"  {p}")
        if self.command_previews:
            lines.append("")
            lines.append("Command previews (not executed):")
            for cmd in self.command_previews:
                lines.append(f"  {' '.join(cmd)}")
        lines.append("")
        lines.append("Mode: plan only; no file contents read, no commands executed")
        return "\n".join(lines)


@dataclasses.dataclass(frozen=True)
class DisabledInspector:
    """Returned when gating flags are not enabled."""

    kind: InspectFilesKind | None
    target: str
    message: str = (
        "file inspection is not enabled; re-run with --inspect-files and "
        "--admin to inspect files"
    )
    mode: str = "disabled"

    def to_jsonable(self) -> dict:
        return {
            "kind": self.kind.value if self.kind else "none",
            "target": self.target,
            "message": self.message,
            "mode": self.mode,
        }


GatedInspectResult = InspectFilesPlan | DisabledInspector


def build_inspect_plan(kind: str, target: str) -> InspectFilesPlan:
    """Build an InspectFilesPlan for the given kind and target.

    Raises ValueError for unknown inspection kinds or invalid targets.
    """
    ik = InspectFilesKind(kind)  # raises ValueError for invalid kind name
    entry = INSPECT_CATALOG[ik]
    path_previews, command_previews = entry.builder(target)
    return InspectFilesPlan(
        kind=ik,
        target=target,
        kind_label=entry.kind_label,
        description=entry.description,
        path_previews=tuple(path_previews),
        command_previews=tuple(tuple(cmd) for cmd in command_previews),
    )


def build_gated_inspect_plan(
    kind: str,
    target: str,
    *,
    inspect_files: bool = False,
    admin: bool = False,
) -> GatedInspectResult:
    """Build an inspection plan gated on --inspect-files and --admin.

    Without both inspect_files=True and admin=True, returns a DisabledInspector
    instead of an InspectFilesPlan.
    """
    if not inspect_files or not admin:
        try:
            ik = InspectFilesKind(kind)
        except ValueError:
            ik = None
        return DisabledInspector(kind=ik, target=target)
    return build_inspect_plan(kind, target)
