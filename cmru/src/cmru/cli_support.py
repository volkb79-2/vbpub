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


def cmru_identity(*, command: str, long_name: str) -> CliIdentity:
    """Build the identity shared by CMRU's installed command entrypoints."""
    from cli_extended import CliIdentity

    return CliIdentity(
        name="CMRU",
        version=cmru_version(),
        long_name=long_name,
        command=command,
    )


class _ShortTimePrefixAction(argparse.Action):
    """Apply CMRU's process-wide presentation choice from a registered option."""

    def __init__(self, option_strings, dest, nargs=0, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        from cmru.output import enable_short_time_prefix

        setattr(namespace, self.dest, True)
        enable_short_time_prefix()


def cmru_presentation_options():
    """Shared options for CMRU's main CLI and all of its delegated commands."""
    from cli_extended import OptionSpec

    return (
        OptionSpec(
            ("--log-prefix-time-short",),
            "prefix severity lines with local HH:MM:SS timestamps",
            group="OUTPUT CONTROL",
            parser_kwargs={
                "action": _ShortTimePrefixAction,
                "nargs": 0,
                "default": False,
            },
        ),
    )


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
