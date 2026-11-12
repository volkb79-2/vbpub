"""Plumbing shared by the two clients, ``cgprofilectl`` and ``cgprofile``.

Standard library, cli-extended and :mod:`cgprofile.ctlwire` only: ``cgprofile mark`` runs in
every profiled command's environment and must start fast and import nothing heavy.
"""

from __future__ import annotations

import argparse
import functools
from typing import Any, Callable, Dict, Optional, Sequence

from cli_extended import CliFailure, CliRuntime, OptionSpec

from .. import ctlwire
from ..ctlwire import CAUSE_REMEDY, WireFailure

SCHEMA = 1
REPORT_EXTRA_HINT = "pip install 'cgprofile[report]'"


def bad(message: str, code: str = "bad-argument") -> WireFailure:
    return WireFailure(message, exit_code=2, code=code)


def fail(runtime: CliRuntime, exc: WireFailure) -> int:
    """Render a failure: the ``--json`` error document, or one ``[ERROR]`` line plus the remedy."""
    if runtime.json_mode:
        runtime.output.error(exc.message)
        runtime.output.primary({"schema": SCHEMA, "ok": False, "error": {
            "code": exc.code, "message": exc.message, "cause": exc.cause}})
        return exc.exit_code
    message = f"{exc.message} (cause: {exc.cause})" if exc.cause else exc.message
    raise CliFailure(message, exit_code=exc.exit_code,
                     hint=CAUSE_REMEDY.get(exc.cause) if exc.cause else None)


def guarded(handler: Callable[[argparse.Namespace, CliRuntime], Optional[int]]):
    """Turn a ``WireFailure`` into the CLI's exit code and, under ``--json``, its error document."""
    @functools.wraps(handler)
    def run(args: argparse.Namespace, runtime: CliRuntime) -> Optional[int]:
        try:
            return handler(args, runtime)
        except WireFailure as exc:
            return fail(runtime, exc)
    return run


def socket_path(args: argparse.Namespace) -> str:
    return ctlwire.resolve_socket(getattr(args, "socket", None))[0]


def emit(runtime: CliRuntime, doc: Dict[str, Any], lines: Sequence[str]) -> None:
    body = {"schema": SCHEMA, "ok": True, **doc}
    runtime.output.primary(body if runtime.json_mode else "\n".join(lines))


def opt(flags: str, description: str, *, group: str = "OPTIONS", metavar: Optional[str] = None,
        mutually_exclusive_group: Optional[str] = None, **parser_kwargs: Any) -> OptionSpec:
    return OptionSpec(tuple(flags.split("/")), description, group=group, metavar=metavar,
                      parser_kwargs=parser_kwargs, mutually_exclusive_group=mutually_exclusive_group)


def socket_option() -> OptionSpec:
    return opt("--socket", "control socket path override (when omitted, uses CGPROFILE_CONTROL_SOCKET)",
               group="CONNECTION", metavar="PATH")
