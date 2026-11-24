"""The ``fleet`` entry point: verify the declared wheel dependency, then run the CLI.

``fleetd.cli`` is a registered cli-extended CLI, so ``cli-extended`` is a real
wheel dependency (never vendored, never put on ``sys.path``). A managed host
that runs ``fleet facts collect`` therefore needs the fleetd wheel, which
installs cli-extended with it. This module imports
nothing outside the standard library: it is the first thing an entry point
executes, and it turns a missing dependency into one actionable line naming the
install step instead of a bare ``ModuleNotFoundError`` traceback.
"""

from __future__ import annotations

import importlib
import sys
from typing import Callable, Sequence

MISSING_DEPENDENCY = (
    "cli-extended is not installed and the fleet CLI is registered with it; "
    "install the cli-extended wheel (GitHub Releases only, not PyPI) next to the fleetd wheel "
    "(fleetd docs/SPEC.md, 'Host install and the wheel layout')"
)

EXIT_MISSING_DEPENDENCY = 3


def main(
    argv: Sequence[str] | None = None,
    *,
    importer: Callable[[str], object] = importlib.import_module,
) -> int:
    """Run ``fleet``; exit 3 with one actionable line when cli-extended is missing."""

    try:
        module = importer("fleetd.cli")
    except ModuleNotFoundError as exc:
        if exc.name != "cli_extended":
            raise
        print(f"fleet: ERROR {MISSING_DEPENDENCY}", file=sys.stderr, flush=True)
        return EXIT_MISSING_DEPENDENCY
    return module.main(argv)  # type: ignore[attr-defined]
