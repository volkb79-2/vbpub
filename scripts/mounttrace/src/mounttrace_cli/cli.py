"""Registered command-line adapter for the dependency-free mounttrace core."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
import time
from types import ModuleType
from typing import Any, Sequence

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    CliRuntime,
    OptionSpec,
    RegisteredCli,
    VerbGroup,
    VerbSpec,
)

import mounttrace_core


DESCRIPTION = (
    "Explain mount views, compare paths, audit trees, predict propagation, "
    "and inspect Soulmask state. The analysis is read-only and never runs "
    "external programs."
)


def identity() -> CliIdentity:
    """Resolve from installed metadata and the package's checked-in VERSION."""
    return CliIdentity.resolve(
        name="MOUNTTRACE",
        long_name="read-only mount namespace analyzer",
        command="mounttrace",
        distribution="mounttrace",
        version_file=Path(__file__).resolve().with_name("VERSION"),
    )


def _type_error(parser: Any, value: str) -> str:
    try:
        parser(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return value


def _path_spec(value: str) -> str:
    return _type_error(mounttrace_core.parse_path_spec, value)


def _host_prefix(value: str) -> str:
    def parse(candidate: str) -> None:
        pid, _path = mounttrace_core.parse_path_spec(candidate)
        if pid is not None or not candidate.startswith("/"):
            raise ValueError("audit PREFIX must be an absolute host path")

    return _type_error(parse, value)


def _pid_prefix(value: str) -> str:
    def parse(candidate: str) -> None:
        pid, _path = mounttrace_core.parse_path_spec(candidate)
        if pid is None:
            raise ValueError("audit PID:PREFIX must name a PID and absolute path")

    return _type_error(parse, value)


def _absolute_path(value: str) -> str:
    if not value.startswith("/"):
        raise argparse.ArgumentTypeError("PATH must be absolute")
    return value


def _positive_pid(value: str) -> int:
    try:
        pid = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid PID value: {value!r}") from None
    if pid < 1:
        raise argparse.ArgumentTypeError("PID values must be positive")
    return pid


def _positive_watch(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid watch interval: {value!r}") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("--watch must be a finite positive number")
    return seconds


def _emit_report(report: mounttrace_core.Report, args: argparse.Namespace, runtime: CliRuntime,
                 core: ModuleType) -> None:
    if runtime.json_mode:
        def emit(line: str) -> None:
            runtime.output.stdout.write(line + "\n")
            if args.watch is not None:
                runtime.output.stdout.flush()

        # Keep the established compact JSON bytes. CliOutput.primary() uses
        # json.dump() with default separators, which is a different wire form.
        core.render_report(report, as_json=True, output=emit)
        return
    core.render_report(
        report,
        verbose=args.verbose,
        flush=args.watch is not None,
        output=runtime.output.primary,
    )


def _handler(command: str, core: ModuleType):
    def run(args: argparse.Namespace, runtime: CliRuntime) -> int:
        # The analysis functions use `command`; cli-extended names this field `verb`.
        args.command = command
        # `-v` is the established short spelling. The library-owned
        # `--verbose`/`--debug` control supplies the same detail level.
        args.verbose = bool(getattr(args, "report_verbose", False) or runtime.debug)
        last_status = 0
        try:
            tables = core.read_tables(args)
            report = core.one_pass(args, tables)
            _emit_report(report, args, runtime, core)
            last_status = report.exit_status()
            if args.watch is None:
                return last_status
            while True:
                time.sleep(args.watch)
                current = core.read_tables(args)
                if current != tables:
                    changes = core.mount_changes(tables, current)
                    tables = current
                    report = core.one_pass(args, tables)
                    report.change_line = core.render_change_line(changes)
                    report.kv["mount_changes"] = changes
                    _emit_report(report, args, runtime, core)
                    last_status = report.exit_status()
        except KeyboardInterrupt:
            return last_status
        except (OSError, ValueError, RuntimeError) as exc:
            raise CliFailure(core.error_message(exc, args), exit_code=1) from None

    return run


def build_cli(
    cli_identity: CliIdentity | None = None,
    *,
    core_module: ModuleType | None = None,
) -> RegisteredCli:
    """Build the executable registration, optionally over a test core module."""
    core = core_module or mounttrace_core
    ident = cli_identity or identity()
    registry = CliRegistry(
        ident,
        prog="mounttrace",
        description=DESCRIPTION,
        getting_started=(
            "mounttrace explain /srv/data/projects --submounts",
            "mounttrace compare /srv/data 123:/data",
            "mounttrace --json --no-probe audit /srv/data 123:/data",
        ),
        logging_logger="mounttrace",
        unexpected_exceptions="report",
        global_options=(
            OptionSpec(
                ("--watch",),
                "repeat when inspected mount data changes",
                group="OBSERVATION",
                metavar="SECONDS",
                parser_kwargs={"type": _positive_watch, "default": None},
            ),
            OptionSpec(
                ("--no-probe",),
                "skip path and filesystem probes (soulmask still reads its state file)",
                group="OBSERVATION",
                parser_kwargs={"action": "store_true"},
            ),
            OptionSpec(
                ("-v",),
                "expand collapsed details and disable truncation",
                group="OUTPUT DETAIL",
                parser_kwargs={"action": "store_true", "dest": "report_verbose"},
            ),
        ),
    )
    readonly = VerbGroup.EXPLORATION.value
    registry.register(
        VerbSpec(
            "explain",
            description="Explain one path in one view.",
            group=readonly,
            include_progress=False,
            examples=(
                "mounttrace explain /srv/data/projects --submounts",
                "mounttrace explain PID:/app --json --no-probe",
            ),
            arguments=(
                ArgumentSpec("path", "host absolute path or PID:/absolute/path", "[PID:]PATH",
                             {"type": _path_spec}),
            ),
            options=(
                OptionSpec(("--submounts",), "list visible mounts below the path",
                           group="DETAILS", parser_kwargs={"action": "store_true"}),
            ),
            handler=_handler("explain", core),
        )
    )
    registry.register(
        VerbSpec(
            "compare",
            description="Compare one path across views.",
            group=readonly,
            include_progress=False,
            examples=(
                "mounttrace compare /srv/data PID:/data",
                "mounttrace compare /srv/data /srv/copy --json",
            ),
            arguments=(
                ArgumentSpec("ref", "reference path in a host or PID view", "REF",
                             {"type": _path_spec}),
                ArgumentSpec("other", "one or more paths to compare with REF", "OTHER",
                             {"nargs": "+", "type": _path_spec}),
            ),
            handler=_handler("compare", core),
        )
    )
    registry.register(
        VerbSpec(
            "audit",
            description="Compare mountpoints below host and PID prefixes.",
            group=readonly,
            include_progress=False,
            examples=("mounttrace audit /srv/data PID:/data --json",),
            arguments=(
                ArgumentSpec("host_prefix", "absolute host path prefix", "PREFIX",
                             {"type": _host_prefix}),
                ArgumentSpec("pid_prefix", "PID and absolute path prefix", "PID:PREFIX",
                             {"type": _pid_prefix}),
            ),
            handler=_handler("audit", core),
        )
    )
    registry.register(
        VerbSpec(
            "predict",
            description="Predict propagation of a new host mount.",
            group=readonly,
            include_progress=False,
            examples=("mounttrace predict /srv/data/new PID --no-probe",),
            arguments=(
                ArgumentSpec("path", "absolute path of the proposed host mount", "PATH",
                             {"type": _absolute_path}),
                ArgumentSpec("pids", "one or more positive process IDs", "PID",
                             {"nargs": "+", "type": _positive_pid}),
            ),
            handler=_handler("predict", core),
        )
    )
    registry.register(
        VerbSpec(
            "soulmask",
            description="Audit Soulmask state and volume mounts.",
            group=readonly,
            include_progress=False,
            examples=("mounttrace soulmask PID --volumes-root /srv/vol",),
            arguments=(
                ArgumentSpec("pids", "one or more positive process IDs", "PID",
                             {"nargs": "+", "type": _positive_pid}),
            ),
            options=(
                OptionSpec(("--state-file",),
                           "Soulmask state file (default: /run/soulmask_tmpfs.state)",
                           group="SOULMASK",
                           metavar="FILE", parser_kwargs={"default": core.DEFAULT_STATE_FILE}),
                OptionSpec(("--volumes-root",),
                           "volume tree to audit (default: /var/lib/pterodactyl/volumes)",
                           group="SOULMASK",
                           metavar="DIR", parser_kwargs={"default": core.DEFAULT_VOLUMES_ROOT,
                                                        "type": _absolute_path}),
            ),
            handler=_handler("soulmask", core),
        )
    )
    return registry.build()


def main(
    argv: Sequence[str] | None = None,
    *,
    core_module: ModuleType | None = None,
    cli_identity: CliIdentity | None = None,
) -> int:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if not isinstance(stream, mounttrace_core.TerminalSafeStream):
            setattr(sys, name, mounttrace_core.TerminalSafeStream(stream))
    return build_cli(cli_identity, core_module=core_module).run(argv=argv)
