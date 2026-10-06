"""Shared CLI diagnostics and project-target selection.

Keeping these rules in one small module prevents the public verbs from slowly
growing different meanings for an omitted target, ``all``, or a comma list.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Mapping

# The ONE exception policy for every CMRU registry (root and delegates). The
# library refuses a delegate whose policy differs from its parent's, so this
# is flipped to "report" in a single commit once every parser uses
# ``cmru_registry`` (program W2 plan).
UNEXPECTED_EXCEPTIONS_POLICY = "raise"
CMRU_DISTRIBUTION = "cmru"
CMRU_COMMAND = "cmru"
CMRU_LONG_NAME = "Configurable Multi Release Utility"
CMRU_LOGGER = "cmru"
# The pip requirement the missing-prompt-driver hint names. cli_extended is
# bundled as a wheel dependency, so the extra that pulls questionary is cmru's
# own (``cmru[interactive]`` -> ``cli-extended[interactive]``).
INTERACTIVE_EXTRA = "cmru[interactive]"


def cmru_headline() -> str:
    """Return the dynamic headline used by every CMRU parser diagnostic."""
    return f"CMRU {cmru_version()} — Configurable Multi Release Utility"


def cmru_version() -> str:
    """Return CMRU's version from installed distribution metadata only (D2).

    Raises ``cli_extended.identity.VersionLookupError`` when ``cmru`` is not
    installed as a distribution; there is no source-tree or literal fallback.
    """
    return cmru_identity(command=CMRU_COMMAND, long_name=CMRU_LONG_NAME).version


def cmru_identity(*, command: str, long_name: str) -> CliIdentity:
    """Build the identity shared by CMRU's installed command entrypoints."""
    from cli_extended import CliIdentity

    return CliIdentity.resolve(
        name="CMRU",
        long_name=long_name,
        command=command,
        distribution=CMRU_DISTRIBUTION,
    )


NOT_INSTALLED_MESSAGE = (
    "cmru is not installed as a distribution; install the wheel (see README)"
)


def report_not_installed() -> int:
    """Print the one-line prerequisite diagnostic and return exit code 3."""
    from cmru import exit_codes

    print(f"[ERROR] {NOT_INSTALLED_MESSAGE}", file=sys.stderr, flush=True)
    return exit_codes.PREREQ_MISSING


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


def cmru_registry(prog: str, description: str, **kwargs):
    """Build the ``CliRegistry`` every CMRU parser (root and delegates) shares.

    One place decides the identity (installed ``cmru`` metadata only, never a
    literal fallback), the ``unexpected_exceptions`` policy
    (:data:`UNEXPECTED_EXCEPTIONS_POLICY`; a delegate whose policy differs from
    its parent's is refused by the library), the logger, and the consumer
    global ``--log-prefix-time-short``. Remaining ``CliRegistry`` keywords
    (``getting_started``, ``single_command``, ``no_args_action``, ...) pass
    through; ``global_options`` extends the shared set rather than replacing it.
    """
    from cli_extended import CliIdentity, CliRegistry

    for owned in ("identity", "unexpected_exceptions"):
        if owned in kwargs:
            raise TypeError(f"cmru_registry owns {owned!r}; do not pass it")
    identity = CliIdentity.resolve(
        name="CMRU",
        long_name=CMRU_LONG_NAME,
        command=CMRU_COMMAND,
        distribution=CMRU_DISTRIBUTION,
    )
    extra_options = tuple(kwargs.pop("global_options", ()))
    kwargs.setdefault("logging_logger", CMRU_LOGGER)
    return CliRegistry(
        identity,
        prog=prog,
        description=description,
        global_options=(*cmru_presentation_options(), *extra_options),
        unexpected_exceptions=UNEXPECTED_EXCEPTIONS_POLICY,
        **kwargs,
    )


TARGET_METAVAR = "[all|PROJECT[,PROJECT...]]"
TARGET_DESCRIPTION = (
    "project target; omitted: the current project, "
    "or every orchestrated project at the estate root"
)


def target_argument(
    description: str = TARGET_DESCRIPTION,
    *,
    name: str = "target",
):
    """The optional ``all|PROJECT[,PROJECT...]`` positional, as a library selector.

    Parsing yields ``None`` (omitted), ``SelectorList.ALL`` or a tuple of names
    in the order given. Resolve it against the loaded registry with
    :func:`select_target_names`. Unlike the legacy string parser, a structural
    error is an argparse usage error (exit 2, wording from the library).
    """
    from cli_extended import ArgumentSpec, SelectorList

    return ArgumentSpec(
        name,
        description,
        metavar=TARGET_METAVAR,
        parser_kwargs={"nargs": "?", "default": None, "type": SelectorList()},
    )


class TargetSelectionError(ValueError):
    """A malformed or ambiguous public project selector."""


def _check_target_parts(parts: list[str]) -> list[str]:
    if any(not part for part in parts):
        raise TargetSelectionError("project target contains an empty name")
    if len(set(parts)) != len(parts):
        raise TargetSelectionError("project target contains a duplicate name")
    if "all" in parts and len(parts) != 1:
        raise TargetSelectionError("'all' is exclusive and cannot be combined with another project")
    if any(part == "all" for part in parts):
        return ["all"]
    return parts


def parse_target_names(raw: str | None) -> list[str] | None:
    """Parse one optional ``all``/name/comma-separated target argument."""
    if raw is None:
        return None
    return _check_target_parts([part.strip() for part in raw.split(",")])


def _normalise_target(raw) -> list[str] | None:
    """Accept the legacy raw string or a library ``SelectorList`` result."""
    from cli_extended import SelectorList

    if raw is None:
        return None
    if isinstance(raw, str):
        return parse_target_names(raw)
    if raw is SelectorList.ALL:
        return ["all"]
    # A tuple/list of names (already structurally checked by the library, but
    # programmatic callers get the same refusals as the string path).
    return _check_target_parts([str(part).strip() for part in raw])


def select_target_names(
    raw: "str | Iterable[str] | object | None",
    projects: Mapping[str, object],
    project_order: Iterable[str],
    *,
    context_project: str | None = None,
) -> list[str]:
    """Resolve a target against the loaded registry in declared order.

    ``raw`` is either the legacy comma string or the result of a
    :func:`target_argument` (``None``, ``SelectorList.ALL`` or a name tuple).
    An omitted target selects the current project, or every orchestrated project
    (``project_order``) at the estate root.
    """
    parsed = _normalise_target(raw)
    ordered = [name for name in project_order if name in projects]
    if parsed is None:
        parsed = [context_project] if context_project is not None else ordered
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
