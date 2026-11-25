"""The ``agctl`` command line: a thin cli-extended shell over the read-only readers.

Every verb here is a READER. Nothing in this module opens a source for writing, takes a lock,
or reaches a host or the network (DESIGN sections 4a and 8 1d).
"""

from __future__ import annotations

import argparse
import sys

from cli_extended import (
    CliFailure,
    CliIdentity,
    CliRegistry,
    CliRuntime,
    VerbSpec,
    VersionLookupError,
)

from .config import ConfigError

PROG = "agctl"
DISTRIBUTION = "agctl"
LONG_NAME = "agent control: controller state and the estate view"


def identity() -> CliIdentity:
    """The one identity: the installed ``agctl`` distribution, never a literal fallback.

    A source checkout that was never built or installed therefore refuses to run (one
    actionable line, exit 2) instead of reporting an invented version. The version itself
    comes from the ``agctl-v*`` tag at build time (``tool.setuptools_scm``).
    """

    return CliIdentity.resolve(
        name="AGCTL", long_name=LONG_NAME, command=PROG, distribution=DISTRIBUTION
    )


def _estate(args: argparse.Namespace, runtime: CliRuntime) -> int:
    # Imported here so a broken reader cannot break `--help` or `--version`, and so the
    # reader module's own imports stay out of the process until a verb needs them.
    from . import estate as estate_reader

    return estate_reader.run(args, runtime)


def build_cli(cli_identity: CliIdentity | None = None):
    """Build the registry (resolving its identity when omitted)."""

    registry = CliRegistry(
        cli_identity or identity(),
        prog=PROG,
        description="Read controller and estate state. Read-only: this CLI writes no file, "
        "takes no lock and reaches no host.",
        getting_started=("agctl estate", "agctl estate --json"),
        logging_logger=PROG,
        unexpected_exceptions="report",
        expected_exceptions=(ConfigError, OSError, ValueError),
    )
    registry.register(
        VerbSpec(
            "estate",
            description="print one row per package: plan state, worktree branch and head, its runs and its gates, "
            "each field naming its source (read-only)",
            group="EXPLORATION",
            examples=("agctl estate", "agctl estate --json"),
            include_progress=False,
            handler=_estate,
        ),
    )
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    try:
        app = build_cli()
    except VersionLookupError as error:
        print(f"[ERROR] {PROG}: {error}", file=sys.stderr)
        return 2
    return app.run(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
