"""Reusable black-box contract assertions for consumer CLI tests."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import Any

from .identity import CliIdentity

CliInvoker = Callable[[Sequence[str]], Any]

_LIBRARY_ROOT = Path(__file__).resolve().parents[1]
_ALWAYS_REMOVED = ("FORCE_COLOR", "CLICOLOR_FORCE", "CLAUDE_CONFIG_DIR")
_XDG_KEYS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")


def _child_environment(
    home: Path | str,
    scrub_prefixes: Sequence[str],
    env: Mapping[str, str | None] | None,
    pythonpath: Sequence[Path | str],
    library_path: bool,
    isolated: bool = False,
) -> dict[str, str]:
    if isolated and (pythonpath or library_path):
        raise ValueError("isolated=True cannot be combined with pythonpath or library_path")
    if not str(home):
        raise ValueError("home must be a non-empty directory")
    home_path = Path(home)
    if not home_path.is_absolute():
        raise ValueError(f"home must be an absolute path, got {str(home)!r}")
    prefixes = tuple(scrub_prefixes)
    if "" in prefixes:
        raise ValueError("scrub_prefixes must not contain an empty string")
    for key in env or {}:
        if key == "HOME" or key in _XDG_KEYS:
            raise ValueError(f"env may not override {key}; home is the only source")
    child = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(prefixes) and key not in _ALWAYS_REMOVED
    }
    child["HOME"] = str(home_path)
    child["XDG_CONFIG_HOME"] = str(home_path / ".config")
    child["XDG_DATA_HOME"] = str(home_path / ".local" / "share")
    child["XDG_CACHE_HOME"] = str(home_path / ".cache")
    child["XDG_STATE_HOME"] = str(home_path / ".local" / "state")
    child["NO_COLOR"] = "1"
    # CLI-EXT-27: the library directory is never added implicitly; it could
    # be a whole site-packages that shadows the consumer's own copy.
    paths = [str(_LIBRARY_ROOT)] if library_path else []
    paths.extend(str(item) for item in pythonpath)
    if child.get("PYTHONPATH") and not isolated:
        paths.append(child["PYTHONPATH"])
    if isolated:
        child.pop("PYTHONPATH", None)
    elif paths:
        child["PYTHONPATH"] = os.pathsep.join(paths)
    for key, value in (env or {}).items():
        if value is None:
            child.pop(key, None)
        else:
            child[key] = value
    return child


def _run(
    command: list[str],
    *,
    home: Path | str,
    scrub_prefixes: Sequence[str],
    env: Mapping[str, str | None] | None,
    cwd: Path | str | None,
    timeout: float,
    pythonpath: Sequence[Path | str],
    library_path: bool,
    isolated: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env=_child_environment(
            home, scrub_prefixes, env, pythonpath, library_path, isolated
        ),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _interpreter_command(
    python: str | None,
    python_args: Sequence[str],
    isolated: bool,
    tail: list[str],
) -> list[str]:
    if isinstance(python_args, str):
        raise ValueError("python_args must be a sequence of strings, not one string")
    args = [*(["-I"] if isolated else []), *python_args]
    return [python or sys.executable, *args, *tail]


def invoke_script(
    script: Path | str,
    argv: Sequence[str],
    *,
    home: Path | str,
    python: str | None = None,
    scrub_prefixes: Sequence[str] = (),
    env: Mapping[str, str | None] | None = None,
    cwd: Path | str | None = None,
    timeout: float = 60,
    pythonpath: Sequence[Path | str] = (),
    library_path: bool = False,
    python_args: Sequence[str] = (),
    isolated: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` in a subprocess with a hermetic environment.

    ``home`` is required: the child gets ``HOME`` and the four ``XDG_*_HOME``
    directories under it, so a test can never read or write the real home.
    ``home`` must be absolute, and ``env`` may not set ``HOME`` or any
    ``XDG_*_HOME`` (``ValueError``); an empty ``scrub_prefixes`` entry is also
    refused. ``PYTHONPATH`` is ``pythonpath`` followed by the inherited value;
    nothing else is added, so the child imports the consumer's own packages
    and the ``cli_extended`` the caller's environment resolves (CLI-EXT-27).
    Pass ``library_path=True`` to put the directory of the imported
    ``cli_extended`` first; that directory is a whole ``site-packages`` when
    the library is installed, so it can shadow the consumer's own copy and
    is for tests that deliberately pin the in-process library.
    ``NO_COLOR=1`` is set; ``FORCE_COLOR``, ``CLICOLOR_FORCE`` and
    ``CLAUDE_CONFIG_DIR`` and every variable starting with a ``scrub_prefixes``
    entry are removed. ``env`` is applied last; a ``None`` value deletes the
    key. ``timeout`` is only a failsafe.

    ``python_args`` are interpreter options placed before the script (for
    example ``("-S",)``). ``isolated=True`` is the "library not installed"
    probe: it adds ``-I`` (ignore ``PYTHON*`` variables, user site and the
    script directory) and drops the inherited ``PYTHONPATH`` from the child
    environment; it cannot be combined with ``pythonpath`` or
    ``library_path`` (``ValueError``). CLI-EXT-19.
    """

    return _run(
        _interpreter_command(python, python_args, isolated, [str(script), *argv]),
        home=home,
        scrub_prefixes=scrub_prefixes,
        env=env,
        cwd=cwd,
        timeout=timeout,
        pythonpath=pythonpath,
        library_path=library_path,
        isolated=isolated,
    )


def invoke_module(
    module: str,
    argv: Sequence[str],
    *,
    home: Path | str,
    python: str | None = None,
    scrub_prefixes: Sequence[str] = (),
    env: Mapping[str, str | None] | None = None,
    cwd: Path | str | None = None,
    timeout: float = 60,
    pythonpath: Sequence[Path | str] = (),
    library_path: bool = False,
    python_args: Sequence[str] = (),
    isolated: bool = False,
) -> subprocess.CompletedProcess[str]:
    """The ``python -m MODULE`` twin of :func:`invoke_script`."""

    return _run(
        _interpreter_command(python, python_args, isolated, ["-m", module, *argv]),
        home=home,
        scrub_prefixes=scrub_prefixes,
        env=env,
        cwd=cwd,
        timeout=timeout,
        pythonpath=pythonpath,
        library_path=library_path,
        isolated=isolated,
    )


def make_invoker(
    target: Path | str, *, module: bool = False, **kwargs: Any
) -> Callable[[Sequence[str]], subprocess.CompletedProcess[str]]:
    """Return the ``invoke`` callable :func:`assert_cli_contract` expects."""

    return partial(invoke_module if module else invoke_script, target, **kwargs)


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
