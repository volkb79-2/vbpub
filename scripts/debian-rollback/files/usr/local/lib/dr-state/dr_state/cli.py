"""The argparse shell and shared exception-to-exit-code translation."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from io import TextIOBase
from typing import NoReturn

from .errors import UsageError, exit_code
from .paths import Paths


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


@dataclass(frozen=True, slots=True)
class Invocation:
    """Global options plus the untouched command argv."""

    paths: Paths
    json: bool
    command: tuple[str, ...]


def parse_invocation(argv: Sequence[str]) -> Invocation:
    """Parse global options wherever they occur, preserving command argv."""

    parser = _Parser(prog="dr-state", allow_abbrev=False)
    parser.add_argument(
        "--sysroot",
        metavar="DIR",
        help="resolve every runtime path beneath DIR",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit versioned JSON where the selected verb supports it",
    )
    namespace, command = parser.parse_known_args(list(argv))
    try:
        paths = Paths.from_sysroot(namespace.sysroot)
    except ValueError as error:
        raise UsageError(str(error)) from error
    return Invocation(paths, namespace.json, tuple(command))


Dispatcher = Callable[[Invocation], int]


def run(
    argv: Sequence[str],
    dispatch: Dispatcher,
    *,
    stderr: TextIOBase | None = None,
    debug: bool | None = None,
) -> int:
    """Parse and dispatch, translating every exception to the §4 exit code."""

    output = sys.stderr if stderr is None else stderr
    if debug is None:
        debug = os.environ.get("DR_STATE_DEBUG") == "1"
    try:
        invocation = parse_invocation(argv)
        if not invocation.command:
            raise UsageError("a command is required")
        return dispatch(invocation)
    except Exception as error:
        code = exit_code(error)
        message = str(error) or error.__class__.__name__
        print(f"dr-state: {message}", file=output)
        if code == 70 and debug:
            traceback.print_exc(file=output)
        return code


def _unimplemented(invocation: Invocation) -> int:
    raise UsageError(
        f"{invocation.command[0]}: command implementation is not installed"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Launcher entry point; P-20b replaces the core-only dispatcher."""

    arguments = sys.argv[1:] if argv is None else list(argv)
    return run(arguments, _unimplemented)
