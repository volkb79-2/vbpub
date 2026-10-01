"""Direct foreground entrypoint for the supervised Nyxloom daemon."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from . import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nyxloomd",
        description="Run the Nyxloom daemon in the foreground for a service manager.",
        add_help=False,
        allow_abbrev=False,
    )
    parser.add_argument("--help", action="help", help="show this help and exit")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="show the installed Nyxloom version and exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Parse before loading the daemon or registry: help, version, and invalid
    # invocations must never start service work or initialize host state.
    _parser().parse_args(argv)

    from . import config
    from .daemon import Daemon

    Daemon(config.load_registry()).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
