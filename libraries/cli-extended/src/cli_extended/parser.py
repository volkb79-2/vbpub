"""Argparse-compatible help, common options, and command dispatch."""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from shutil import get_terminal_size
from textwrap import TextWrapper
from typing import Any, TextIO

from .identity import CliIdentity
from .output import CliOutput, LogLevel
from .progress import ProgressMode, ProgressRenderer


class UsageError(Exception):
    """An invocation error that must be rendered with command help."""

    def __init__(self, message: str, parser: ExtendedArgumentParser) -> None:
        super().__init__(message)
        self.message = message
        self.parser = parser

    def render(self) -> str:
        help_text = self.parser.format_help()
        headline = self.parser.identity.headline
        if help_text.startswith(headline):
            help_text = help_text[len(headline) :].lstrip("\n")
        return f"{headline}\n[ERROR] {self.parser.prog}: {self.message}\n\n{help_text}"


class CliFailure(Exception):
    """An expected runtime failure rendered without a traceback."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        hint: str | None = None,
        show_help: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.hint = hint
        self.show_help = show_help


class VerbGroup(str, Enum):
    """Common semantic groups for top-level verbs."""

    EXPLORATION = "EXPLORATION"
    MODIFICATION = "MODIFICATION"
    MIXED = "MIXED OPERATIONS"
    AUTHENTICATION = "AUTHENTICATION / SETUP"
    MAINTENANCE = "MAINTENANCE"


@dataclass(frozen=True, slots=True)
class VerbSpec:
    """One public verb rendered in the grouped top-level help."""

    name: str
    synopsis: str
    description: str
    group: str = VerbGroup.EXPLORATION.value


class HelpCatalog:
    """Render one grouped top-level help document from verb metadata."""

    def __init__(
        self,
        identity: CliIdentity,
        *,
        prog: str,
        usage: str | None = None,
        getting_started: Sequence[str] = (),
        verbs: Iterable[VerbSpec] = (),
        global_options: Sequence[tuple[str, str]] = (),
        width: int | None = None,
    ) -> None:
        self.identity = identity
        self.prog = prog
        self.usage = usage or f"{prog} <verb> [options]"
        self.getting_started = tuple(getting_started)
        self.verbs = tuple(verbs)
        self.global_options = tuple(global_options)
        self.width = width
        names = [verb.name for verb in self.verbs]
        if len(names) != len(set(names)):
            raise ValueError("top-level help cannot contain duplicate verb names")

    def add_global_options(self, options: Iterable[tuple[str, str]]) -> None:
        """Add option metadata without allowing duplicate top-level entries."""

        existing = {name for name, _ in self.global_options}
        additions = tuple(
            (name, description) for name, description in options if name not in existing
        )
        self.global_options += additions

    def render(self, *, width: int | None = None) -> str:
        width = width or self.width or get_terminal_size((120, 24)).columns
        width = max(60, width)
        lines = [self.identity.headline, "", f"Usage: {self.usage}"]
        lines.extend(
            (f"       {self.prog} help [verb]", f"       {self.prog} version", "")
        )
        if self.getting_started:
            lines.append("GETTING STARTED")
            lines.extend(f"  {line}" for line in self.getting_started)
            lines.append("")

        group_order: list[str] = []
        for verb in self.verbs:
            if verb.group not in group_order:
                group_order.append(verb.group)
        for group in group_order:
            lines.append(group)
            for verb in self.verbs:
                if verb.group != group:
                    continue
                prefix = f"  {verb.name}"
                if verb.synopsis:
                    prefix += f" {verb.synopsis}"
                wrapper = TextWrapper(
                    width=max(20, width - 4),
                    initial_indent=f"{prefix} ",
                    subsequent_indent=" " * 4,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                lines.extend(wrapper.wrap(verb.description) or [prefix])
            lines.append("")

        if self.global_options:
            lines.append("GLOBAL OPTIONS")
            option_width = max(len(name) for name, _ in self.global_options)
            for name, description in self.global_options:
                wrapper = TextWrapper(
                    width=max(20, width - option_width - 6),
                    initial_indent=f"  {name.ljust(option_width)}  ",
                    subsequent_indent=" " * (option_width + 4),
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                lines.extend(wrapper.wrap(description) or [f"  {name}"])
        return "\n".join(lines).rstrip() + "\n"


class ExtendedArgumentParser(argparse.ArgumentParser):
    """ArgumentParser with no ``-h`` and command-help-on-error semantics."""

    def __init__(
        self,
        *args: Any,
        identity: CliIdentity,
        catalog: HelpCatalog | None = None,
        top_level: bool = False,
        **kwargs: Any,
    ) -> None:
        kwargs["add_help"] = False
        self.identity = identity
        self.catalog = catalog
        self.top_level = top_level
        super().__init__(*args, **kwargs)

    def format_help(self) -> str:
        if self.top_level and self.catalog is not None:
            return self.catalog.render()
        return f"{self.identity.headline}\n\n{super().format_help()}"

    def format_usage(self) -> str:
        return f"{self.identity.headline}\n{super().format_usage()}"

    def error(self, message: str) -> None:
        raise UsageError(message, self)

    def add_subparsers(self, **kwargs: Any) -> Any:
        def parser_factory(*args: Any, **sub_kwargs: Any) -> ExtendedArgumentParser:
            sub_kwargs.setdefault("identity", self.identity)
            return type(self)(*args, **sub_kwargs)

        kwargs.setdefault("parser_class", parser_factory)
        return super().add_subparsers(**kwargs)


class _HelpAction(argparse.Action):
    """Print help through the invocation's injected stream and exit cleanly."""

    def __init__(
        self,
        option_strings: Sequence[str],
        dest: str = argparse.SUPPRESS,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("default", argparse.SUPPRESS)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        stream = getattr(parser, "_cli_help_stream", sys.stdout)
        stream.write(parser.format_help())
        stream.flush()
        parser.exit(0)


class _VersionAction(argparse.Action):
    """Print only the short version through the invocation's output stream."""

    def __init__(
        self,
        option_strings: Sequence[str],
        dest: str = argparse.SUPPRESS,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("default", argparse.SUPPRESS)
        self.version_text = kwargs.pop("version")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        stream = getattr(parser, "_cli_help_stream", sys.stdout)
        stream.write(f"{self.version_text}\n")
        stream.flush()
        parser.exit(0)


def add_common_options(
    parser: ExtendedArgumentParser,
    identity: CliIdentity,
    *,
    include_json: bool = True,
    include_progress: bool = True,
    include_confirmation: bool = True,
    suppress_defaults: bool = False,
) -> None:
    """Add the standard long options without introducing ``-h`` aliases."""

    if getattr(parser, "_cli_extended_common_options", False):
        return
    parser._cli_extended_common_options = True
    default = argparse.SUPPRESS if suppress_defaults else None
    if parser.top_level and parser.catalog is not None:
        common_help = [
            ("--help", "show this help and exit"),
            ("--version", "print the short version and exit"),
            ("--log-level LEVEL", "set diagnostic verbosity"),
            ("--quiet", "show errors and warnings only"),
            ("--debug/--verbose", "show diagnostic detail"),
            ("--debug-raw", "show supported raw diagnostic data without redaction"),
            ("--color/--no-color", "control terminal colour"),
        ]
        if include_json:
            common_help.append(("--json", "emit machine-readable output"))
        if include_progress:
            common_help.append(("--progress MODE", "choose progress presentation"))
        if include_confirmation:
            common_help.append(("--yes", "accept confirmation prompts"))
        parser.catalog.add_global_options(common_help)
    parser.add_argument("--help", action=_HelpAction, help="show this help and exit")
    parser.add_argument(
        "--version",
        action=_VersionAction,
        version=identity.version_line,
        help="print the short version and exit",
    )
    debug_group = parser.add_argument_group("DEBUGGING")
    level_group = debug_group.add_mutually_exclusive_group()
    level_group.add_argument(
        "--log-level", choices=[level.value for level in LogLevel], default=default
    )
    level_group.add_argument(
        "--quiet",
        action="store_true",
        default=default,
        help="show errors and warnings only",
    )
    level_group.add_argument(
        "--debug",
        "--verbose",
        dest="debug",
        action="store_true",
        default=default,
        help="show diagnostic detail",
    )
    debug_group.add_argument(
        "--debug-raw",
        action="store_true",
        default=default,
        help="show supported raw diagnostic data without redaction (dangerous)",
    )
    output_group = parser.add_argument_group("OUTPUT CONTROL")
    color_group = output_group.add_mutually_exclusive_group()
    color_group.add_argument(
        "--color",
        dest="color",
        action="store_true",
        default=default,
        help="force terminal colour",
    )
    color_group.add_argument(
        "--no-color",
        dest="color",
        action="store_false",
        default=default,
        help="disable terminal colour",
    )
    if include_json:
        output_group.add_argument(
            "--json",
            action="store_true",
            default=default,
            help="emit machine-readable output",
        )
    if include_progress:
        output_group.add_argument(
            "--progress",
            choices=[item.value for item in ProgressMode],
            default=default,
            help="choose progress presentation",
        )
    if include_confirmation:
        parser.add_argument_group("CONFIRMATION").add_argument(
            "--yes",
            action="store_true",
            default=default,
            help="accept confirmation prompts",
        )


@dataclass(slots=True)
class CliRuntime:
    """Per-invocation context passed to a command handler."""

    identity: CliIdentity
    output: CliOutput
    yes: bool = False
    debug: bool = False
    debug_raw: bool = False
    json_mode: bool = False
    progress_mode: ProgressMode = ProgressMode.AUTO

    def progress(self, *, mode: ProgressMode | str | None = None) -> ProgressRenderer:
        return ProgressRenderer(
            mode or self.progress_mode,
            color=self.output.color,
            level=self.output.level,
        )


Handler = Callable[[argparse.Namespace, CliRuntime], int | None]


def _runtime_from_args(
    args: argparse.Namespace,
    identity: CliIdentity,
    *,
    stdout: TextIO | None,
    stderr: TextIO | None,
    secrets: Sequence[str],
) -> CliRuntime:
    debug_raw = bool(getattr(args, "debug_raw", False))
    debug = bool(getattr(args, "debug", False)) or debug_raw
    quiet = bool(getattr(args, "quiet", False))
    explicit_level = getattr(args, "log_level", None)
    if debug_raw and explicit_level not in (None, LogLevel.DEBUG.value):
        raise CliFailure(
            "--debug-raw implies --log-level=debug; do not combine it with "
            f"--log-level={explicit_level}",
            exit_code=2,
            show_help=True,
        )
    if explicit_level is not None:
        level = LogLevel.parse(explicit_level)
    elif quiet:
        level = LogLevel.ERROR
    elif debug:
        level = LogLevel.DEBUG
    else:
        level = LogLevel.INFO
    progress_value = getattr(args, "progress", None) or ProgressMode.AUTO.value
    progress = ProgressMode.parse(progress_value)
    if quiet and debug_raw:
        raise CliFailure(
            "--quiet and --debug-raw are contradictory", exit_code=2, show_help=True
        )
    output = CliOutput(
        identity,
        level=level,
        color=getattr(args, "color", None),
        json_mode=bool(getattr(args, "json", False)),
        debug_raw=debug_raw,
        secrets=secrets,
        stdout=stdout,
        stderr=stderr,
    )
    output.raw_warning()
    return CliRuntime(
        identity=identity,
        output=output,
        yes=bool(getattr(args, "yes", False)),
        debug=debug,
        debug_raw=debug_raw,
        json_mode=output.json_mode,
        progress_mode=progress,
    )


def _print_help(text: str, stream: TextIO) -> None:
    stream.write(text)
    if not text.endswith("\n"):
        stream.write("\n")
    stream.flush()


def _help_body(text: str, identity: CliIdentity) -> str:
    """Remove the identity line when help follows an identity-headed error."""

    if text.startswith(identity.headline):
        return text[len(identity.headline) :].lstrip("\n")
    return text


def run_cli(
    parser: ExtendedArgumentParser,
    handlers: Mapping[str, Handler],
    *,
    identity: CliIdentity,
    argv: Sequence[str] | None = None,
    command_parsers: Mapping[str, ExtendedArgumentParser] | None = None,
    expected_exceptions: tuple[type[BaseException], ...] = (),
    secrets: Sequence[str] = (),
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run a conventional CLI while keeping parser and exception policy shared."""

    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    raw = list(sys.argv[1:] if argv is None else argv)
    command_parsers = command_parsers or {}
    for command_parser in (parser, *command_parsers.values()):
        command_parser._cli_help_stream = stdout

    if not raw:
        _print_help(parser.format_help(), stdout)
        return 0
    if raw == ["help"]:
        _print_help(parser.format_help(), stdout)
        return 0
    if len(raw) == 2 and raw[0] == "help":
        command_parser = command_parsers.get(raw[1])
        if command_parser is None:
            print(identity.headline, file=stderr)
            print(f"[ERROR] unknown help topic {raw[1]!r}", file=stderr)
            _print_help(_help_body(parser.format_help(), identity), stderr)
            return 2
        _print_help(command_parser.format_help(), stdout)
        return 0
    if raw == ["version"]:
        _print_help(identity.version_line, stdout)
        return 0

    try:
        args = parser.parse_args(raw)
    except UsageError as exc:
        _print_help(exc.render(), stderr)
        return 2
    except SystemExit as exc:
        return int(exc.code or 0)

    verb = getattr(args, "verb", None)
    handler = handlers.get(verb)
    if handler is None:
        print(identity.headline, file=stderr)
        print(f"[ERROR] no handler registered for verb {verb!r}", file=stderr)
        _print_help(_help_body(parser.format_help(), identity), stderr)
        return 2

    try:
        runtime = _runtime_from_args(
            args,
            identity,
            stdout=stdout,
            stderr=stderr,
            secrets=secrets,
        )
        result = handler(args, runtime)
        return 0 if result is None else int(result)
    except KeyboardInterrupt:
        # Runtime creation normally precedes the handler, but keep Ctrl-C safe
        # even when a handler raises it before producing any output.
        try:
            runtime.output.cancelled()  # type: ignore[union-attr]
        except UnboundLocalError:
            print(f"{identity.headline}\n[INFO] Cancelled.", file=stderr, flush=True)
        return 130
    except CliFailure as exc:
        if "runtime" in locals():
            runtime.output.error(exc.message, hint=exc.hint)
        else:
            print(identity.headline, file=stderr, flush=True)
            print(f"[ERROR] {exc.message}", file=stderr, flush=True)
            if exc.hint:
                print(f"Hint: {exc.hint}", file=stderr, flush=True)
        if exc.show_help:
            command_parser = command_parsers.get(verb)
            _print_help(
                _help_body(command_parser.format_help(), identity)
                if command_parser is not None
                else _help_body(parser.format_help(), identity),
                stderr,
            )
        return exc.exit_code
    except expected_exceptions as exc:
        if "runtime" in locals():
            runtime.output.error(str(exc))
        else:
            print(f"{identity.headline}\n[ERROR] {exc}", file=stderr, flush=True)
        return 1
    except Exception:
        # Unexpected programming failures intentionally remain tracebacks. A
        # caller can use --debug for its own additional context, but the
        # library must not disguise a bug as a successful-looking diagnostic.
        if getattr(locals().get("runtime", None), "debug", False):
            traceback.print_exc(file=stderr)
        raise
