"""Shared CLI diagnostics and project-target selection.

Keeping these rules in one small module prevents the public verbs from slowly
growing different meanings for an omitted target, ``all``, or a comma list.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Mapping


def cmru_headline() -> str:
    """Return the dynamic headline used by every CMRU parser diagnostic."""
    return f"CMRU {cmru_version()} — Configurable Multi Release Utility"


def cmru_version() -> str:
    """Return CMRU's authoritative source or installed version."""
    try:
        from cmru.cli import _cmru_version

        version = _cmru_version()
    except Exception:  # pragma: no cover - only protects bootstrap diagnostics
        try:
            from importlib.metadata import version

            version = version("cmru")
        except Exception:
            version = "dev"
    return version


class CMRUArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose help and argument errors have a stable first line."""

    def format_help(self) -> str:
        return cmru_headline() + "\n" + super().format_help()

    def error(self, message: str) -> "NoReturn":
        self._print_message(cmru_headline() + "\n", sys.stderr)
        self._print_message(self.format_usage(), sys.stderr)
        self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        raise SystemExit(2)


class TargetSelectionError(ValueError):
    """A malformed or ambiguous public project selector."""


def parse_target_names(raw: str | None) -> list[str] | None:
    """Parse one optional ``all``/name/comma-separated target argument."""
    if raw is None:
        return None
    parts = [part.strip() for part in raw.split(",")]
    if any(not part for part in parts):
        raise TargetSelectionError("project target contains an empty name")
    if len(set(parts)) != len(parts):
        raise TargetSelectionError("project target contains a duplicate name")
    if "all" in parts and len(parts) != 1:
        raise TargetSelectionError("'all' is exclusive and cannot be combined with another project")
    if any(part == "all" for part in parts):
        return ["all"]
    return parts


def select_target_names(
    raw: str | None,
    projects: Mapping[str, object],
    project_order: Iterable[str],
    *,
    context_project: str | None = None,
    estate_scope: bool = False,
) -> list[str]:
    """Resolve a parsed target against the loaded registry in declared order."""
    parsed = parse_target_names(raw)
    ordered = [name for name in project_order if name in projects]
    if parsed is None:
        if context_project is not None:
            parsed = [context_project]
        elif estate_scope:
            parsed = ["all"]
        else:
            parsed = ordered
    if parsed == ["all"]:
        return ordered
    unknown = [name for name in parsed if name not in projects]
    if unknown:
        raise TargetSelectionError("unknown project(s): " + ", ".join(unknown))
    wanted = set(parsed)
    return [name for name in ordered if name in wanted]


def write_config_diagnostic(message: str, *, level: str = "ERROR") -> None:
    """Print a version-headed CMRU diagnostic."""
    print(cmru_headline(), file=sys.stderr, flush=True)
    print(f"[{level}] {message}", file=sys.stderr, flush=True)
