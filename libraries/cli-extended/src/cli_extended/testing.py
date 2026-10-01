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
    known_verb_errors: Mapping[str, str] | None = None,
    allow_short_help: bool = False,
) -> None:
    """Black-box check help/version discovery and parse-error conventions.

    ``invoke`` should launch the real executable in a subprocess and return an
    object with ``returncode``, ``stdout``, and ``stderr`` attributes (such as
    ``subprocess.CompletedProcess``). Side-effect freedom must additionally be
    asserted by the consumer using observable state appropriate to its CLI.
    ``known_verb_errors`` maps labels in ``invalid_invocations`` to the verb
    whose complete help must accompany that error. By default ``-h`` must be
    rejected; set ``allow_short_help`` only for a documented compatibility
    exception, in which case ``-h`` must match ``--help``. This helper assumes
    bare invocation prints help; it is not for a CLI that deliberately
    performs a documented action without arguments.
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

    short_help = invoke(["-h"])
    if allow_short_help:
        long_help = invoke(["--help"])
        _assert(short_help.returncode == 0, "-h compatibility spelling failed")
        _assert(
            short_help.stdout == long_help.stdout,
            "-h compatibility help differs from --help",
        )
        _assert(not short_help.stderr, "-h wrote diagnostics to stderr")
    else:
        _assert(short_help.returncode == 2, "-h must be rejected by default")
        _assert(
            short_help.stderr.startswith("[ERROR]"),
            "rejected -h lacks an error heading",
        )
        _assert(
            f"\n\n{identity.headline}\n\n" in short_help.stderr,
            "rejected -h lacks separated identity-headed help",
        )
        _assert("[ERROR]" in short_help.stderr, "rejected -h lacks an error diagnostic")
        _assert("usage:" in short_help.stderr.lower(), "rejected -h lacks usage")
        _assert("Traceback" not in short_help.stderr, "rejected -h printed a traceback")

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

    invalid_results = {}
    for label, argv in (invalid_invocations or {}).items():
        result = invoke(argv)
        invalid_results[label] = result
        _assert(
            result.returncode == 2,
            f"invalid invocation {label!r} exited {result.returncode}",
        )
        _assert(
            result.stderr.startswith("[ERROR]"),
            f"invalid invocation {label!r} lacks an error heading",
        )
        _assert(
            f"\n\n{identity.headline}\n\n" in result.stderr,
            f"invalid invocation {label!r} lacks separated identity-headed help",
        )
        _assert(
            "[ERROR]" in result.stderr,
            f"invalid invocation {label!r} lacks an error diagnostic",
        )
        _assert(
            "usage:" in result.stderr.lower(),
            f"invalid invocation {label!r} did not include command usage/help",
        )
        _assert("Traceback" not in result.stderr, f"{label!r} printed a traceback")

    for label, verb in (known_verb_errors or {}).items():
        _assert(
            label in invalid_results,
            f"known-verb error {label!r} is missing from invalid_invocations",
        )
        _assert(
            verb in verbs, f"known-verb error {label!r} names unknown verb {verb!r}"
        )
        expected = invoke(["help", verb])
        _assert(
            expected.returncode == 0,
            f"help for known-verb error {label!r} could not be rendered",
        )
        help_body = expected.stdout
        if help_body.startswith(identity.headline):
            help_body = help_body[len(identity.headline) :].lstrip("\n")
        _assert(
            help_body in invalid_results[label].stderr,
            f"known-verb error {label!r} did not include complete {verb!r} help",
        )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
