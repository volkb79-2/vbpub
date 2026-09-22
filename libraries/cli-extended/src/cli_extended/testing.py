"""Reusable black-box contract assertions for consumer CLI tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .identity import CliIdentity

CliInvoker = Callable[[Sequence[str]], Any]


def assert_cli_contract(
    invoke: CliInvoker,
    identity: CliIdentity,
    verbs: Sequence[str],
    *,
    invalid_invocations: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """Black-box check help/version discovery and parse-error conventions.

    ``invoke`` should launch the real executable in a subprocess and return an
    object with ``returncode``, ``stdout``, and ``stderr`` attributes (such as
    ``subprocess.CompletedProcess``). Side-effect freedom must additionally be
    asserted by the consumer using observable state appropriate to its CLI.
    This helper assumes bare invocation prints help; it is not for a CLI that
    deliberately performs a documented action without arguments.
    """

    help_cases = [([], "bare invocation"), (["--help"], "--help"), (["help"], "help")]
    for argv, label in help_cases:
        result = invoke(argv)
        _assert(result.returncode == 0, f"{label} exited {result.returncode}")
        _assert(
            result.stdout.startswith(identity.headline),
            f"{label} did not begin with {identity.headline!r}",
        )
        _assert(
            not result.stderr, f"{label} wrote diagnostics to stderr: {result.stderr!r}"
        )

    for argv in (["version"], ["--version"]):
        result = invoke(argv)
        _assert(result.returncode == 0, f"{argv!r} exited {result.returncode}")
        _assert(
            result.stdout == identity.version_line + "\n",
            f"{argv!r} output was not the exact version line: {result.stdout!r}",
        )
        _assert(not result.stderr, f"{argv!r} wrote to stderr: {result.stderr!r}")

    top_help = invoke(["--help"]).stdout
    for verb in verbs:
        listed = any(
            line.startswith(f"  {verb}")
            and (len(line) == len(verb) + 2 or line[len(verb) + 2].isspace())
            for line in top_help.splitlines()
        )
        _assert(listed, f"top-level help omits registered verb {verb!r}")
        topic = invoke(["help", verb])
        local = invoke([verb, "--help"])
        for label, result in ((f"help {verb}", topic), (f"{verb} --help", local)):
            _assert(result.returncode == 0, f"{label} exited {result.returncode}")
            _assert(
                result.stdout.startswith(identity.headline),
                f"{label} is missing the product identity line",
            )
            _assert(not result.stderr, f"{label} wrote to stderr: {result.stderr!r}")
        _assert(
            topic.stdout == local.stdout,
            f"help {verb} and {verb} --help differ",
        )

    for label, argv in (invalid_invocations or {}).items():
        result = invoke(argv)
        _assert(
            result.returncode == 2,
            f"invalid invocation {label!r} exited {result.returncode}",
        )
        _assert(
            result.stderr.startswith(identity.headline),
            f"invalid invocation {label!r} lacks an identity-headed diagnostic",
        )
        _assert(
            "[ERROR]" in result.stderr,
            f"invalid invocation {label!r} lacks an error diagnostic",
        )
        _assert(
            "usage:" in result.stderr,
            f"invalid invocation {label!r} did not include command usage/help",
        )
        _assert("Traceback" not in result.stderr, f"{label!r} printed a traceback")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
