"""The cli-extended library contract: the single source of library-owned controls.

Library-owned common controls (``--json``, ``--log-level``, ...) are not part
of a consumer's reviewed grammar. A generated surface manifest names the
enabled controls per route and records one contract version; everything else
about them (help text, metavar, default, action class) stays inside the
library. Bump :data:`CONTRACT_VERSION` only when a control's accepted syntax
or meaning changes in a way consumers must re-review.
"""

from __future__ import annotations

import argparse
import copy
import functools
import inspect
from collections.abc import Mapping, Sequence
from typing import Any

from .identity import CliIdentity
from .parser import ExtendedArgumentParser, add_common_options

LIBRARY_CONTRACT_NAME = "cli-extended"
CONTRACT_VERSION = 1

# Common controls that still receive consumer-review candidates: the consumer
# decides what ``--json`` etc. mean for each of its routes.
CONSUMER_REVIEWED_COMMON_FLAGS = frozenset(
    {"--json", "--yes", "--debug-raw", "--dry-run"}
)

_MARKER = "_cli_extended_common"


def canonical_flag(flags: Sequence[str]) -> str:
    """Return the first long spelling, or the first spelling when none is long."""

    return next((flag for flag in flags if flag.startswith("--")), flags[0])


def is_library_action(action: argparse.Action) -> bool:
    """Return whether ``add_common_options`` created this action.

    Identification is by the marker set at creation, never by flag spelling:
    a consumer option that happens to be spelled ``--json`` is consumer grammar.
    """

    return getattr(action, _MARKER, False) is True


@functools.cache
def _common_control_table() -> Mapping[str, Mapping[str, Any]]:
    flag_names = [
        name
        for name in inspect.signature(add_common_options).parameters
        if name.startswith("include_")
    ]
    parser = ExtendedArgumentParser(
        prog="cli-extended-contract",
        identity=CliIdentity(
            name="cli-extended", version="0", long_name="library contract"
        ),
    )
    add_common_options(parser, parser.identity, **{name: True for name in flag_names})
    table: dict[str, Mapping[str, Any]] = {}
    for action in filter(is_library_action, parser._actions):
        choices = None if action.choices is None else list(action.choices)
        table[canonical_flag(action.option_strings)] = {
            "flags": list(action.option_strings),
            "nargs": action.nargs,
            "takes_value": action.nargs != 0,
            "choices": choices,
            "before_verb": True,
            "after_verb": True,
        }
    return table


def common_control_table() -> dict[str, dict[str, Any]]:
    """Return ``canonical_flag -> shape`` for every library-owned control.

    Built from a scratch parser with every ``include_*`` option of
    ``add_common_options`` enabled, so it cannot drift from the parser.
    The mapping is a fresh copy; callers may mutate it.
    """

    return copy.deepcopy(dict(_common_control_table()))  # type: ignore[arg-type]
