"""Argparse-compatible help, common options, and command dispatch."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from shutil import get_terminal_size
from textwrap import TextWrapper
from typing import Any, TextIO

from .identity import CliIdentity
from .output import CliOutput, LogLevel, logging_context
from .progress import ProgressMode, ProgressRenderer


def _nargs_display(label: str, nargs: Any) -> str:
    if nargs == "?":
        return f"[{label}]"
    if nargs == "*":
        return f"[{label} ...]"
    if nargs == "+":
        return f"{label} [{label} ...]"
    if isinstance(nargs, int) and nargs >= 2:
        return " ".join([label] * nargs)
    if nargs in {argparse.REMAINDER, argparse.PARSER}:
        return f"{label} ..."
    return label


class UsageError(Exception):
    """An invocation error that must be rendered with command help."""

    def __init__(self, message: str, parser: ExtendedArgumentParser) -> None:
        super().__init__(message)
        self.message = message
        self.parser = parser

    def render(self) -> str:
        help_text = self.parser.format_help()
        return f"[ERROR] {self.parser.prog}: {self.message}\n\n{help_text}"


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


class HelpFormat(str, Enum):
    """Supported generated usage-document formats."""

    TEXT = "text"
    MARKDOWN = "markdown"

    @classmethod
    def parse(cls, value: str) -> HelpFormat:
        try:
            return cls(value.lower())
        except ValueError as exc:
            allowed = ", ".join(item.value for item in cls)
            raise ValueError(
                f"invalid help format {value!r}; expected one of: {allowed}"
            ) from exc


@dataclass(frozen=True)
class OptionSpec:
    """Structured option metadata and argparse registration details.

    ``parser_kwargs`` is deliberately an escape hatch for argparse's large
    option surface (``action``, ``choices``, ``type``, ``nargs``, and so on).
    The stable contract-facing fields remain the flags, description, group,
    and displayed metavar.
    """

    flags: tuple[str, ...]
    description: str
    group: str = "OPTIONS"
    metavar: str | None = None
    parser_kwargs: Mapping[str, Any] = field(default_factory=dict)
    mutually_exclusive_group: str | None = None
    mutually_exclusive_required: bool = False

    def __post_init__(self) -> None:
        if not self.flags or any(not flag for flag in self.flags):
            raise ValueError("an option must define at least one non-empty flag")
        if any(not flag.startswith("-") for flag in self.flags):
            raise ValueError(
                "option flags must begin with '-' (positionals are not options)"
            )
        if not self.description.strip():
            raise ValueError("an option must define a description")
        if not self.group:
            raise ValueError("an option must define a non-empty group")
        if (
            self.mutually_exclusive_group is not None
            and not self.mutually_exclusive_group
        ):
            raise ValueError(
                "an option's mutually-exclusive group name must not be empty"
            )
        if self.mutually_exclusive_required and self.mutually_exclusive_group is None:
            raise ValueError(
                "mutually_exclusive_required needs a mutually_exclusive_group"
            )
        if self.mutually_exclusive_group and self.parser_kwargs.get("required"):
            raise ValueError(
                "required belongs on the mutually-exclusive group, not an option member"
            )

    @property
    def display(self) -> str:
        """Return the stable help label for this option."""

        label = "/".join(self.flags)
        metavar = self.metavar
        if metavar is None:
            candidate = self.parser_kwargs.get("metavar")
            if isinstance(candidate, str):
                metavar = candidate
            elif isinstance(candidate, tuple):
                metavar = " ".join(str(item) for item in candidate)
        if metavar is None:
            choices = self.parser_kwargs.get("choices")
            if choices is not None:
                metavar = "{" + ",".join(str(choice) for choice in choices) + "}"
        action = self.parser_kwargs.get("action", "store")
        action_name = (
            action if isinstance(action, str) else getattr(action, "__name__", "")
        )
        takes_value = action_name not in {
            "store_true",
            "store_false",
            "store_const",
            "append_const",
            "count",
            "help",
            "version",
            "BooleanOptionalAction",
        }
        if not takes_value:
            return label
        if metavar is None:
            destination = self.parser_kwargs.get("dest")
            if not isinstance(destination, str) or not destination:
                destination = next(
                    (flag for flag in reversed(self.flags) if flag.startswith("--")),
                    self.flags[-1],
                ).lstrip("-")
                destination = destination.replace("-", "_")
            metavar = str(destination).upper()
        return f"{label} {_nargs_display(metavar, self.parser_kwargs.get('nargs'))}"

    @property
    def markdown_description(self) -> str:
        """Include parser constraints that an adopter encoded as attributes."""

        details = []
        choices = self.parser_kwargs.get("choices")
        if choices is not None:
            details.append("choices: " + ", ".join(f"`{choice}`" for choice in choices))
        if self.mutually_exclusive_group:
            requirement = (
                "one option in this group is required"
                if self.mutually_exclusive_required
                else "mutually exclusive with other group members"
            )
            details.append(f"{requirement} ({self.mutually_exclusive_group})")
        elif self.parser_kwargs.get("required"):
            details.append("required")
        if "default" in self.parser_kwargs:
            default = self.parser_kwargs["default"]
            if default is not argparse.SUPPRESS and default is not None:
                details.append(f"default: `{default}`")
        return self.description + (f" ({'; '.join(details)})" if details else "")

    def add_to(self, parser: Any, *, suppress_default: bool = False) -> Any:
        """Register this option with argparse and return its action."""

        kwargs = dict(self.parser_kwargs)
        kwargs["help"] = self.description
        if self.metavar is not None:
            kwargs.setdefault("metavar", self.metavar)
        if suppress_default:
            kwargs.setdefault("default", argparse.SUPPRESS)
        return parser.add_argument(*self.flags, **kwargs)


@dataclass(frozen=True)
class ArgumentSpec:
    """Structured positional argument metadata for a registered verb."""

    name: str
    description: str
    metavar: str | None = None
    parser_kwargs: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or self.name.startswith("-"):
            raise ValueError(
                "a positional argument needs a name without a leading dash"
            )
        if not self.description:
            raise ValueError("a positional argument must define a description")

    @property
    def display(self) -> str:
        if self.metavar is not None:
            return self.metavar
        candidate = self.parser_kwargs.get("metavar")
        return candidate if isinstance(candidate, str) else self.name

    @property
    def markdown_description(self) -> str:
        details = []
        choices = self.parser_kwargs.get("choices")
        if choices is not None:
            details.append("choices: " + ", ".join(f"`{choice}`" for choice in choices))
        if "default" in self.parser_kwargs:
            default = self.parser_kwargs["default"]
            if default is not argparse.SUPPRESS and default is not None:
                details.append(f"default: `{default}`")
        return self.description + (f" ({'; '.join(details)})" if details else "")

    def add_to(self, parser: argparse.ArgumentParser) -> Any:
        """Register this positional argument and return its action."""

        kwargs = dict(self.parser_kwargs)
        kwargs["help"] = self.description
        if self.metavar is not None:
            kwargs.setdefault("metavar", self.metavar)
        return parser.add_argument(self.name, **kwargs)


def _coerce_option_spec(value: OptionSpec | tuple[str, str]) -> OptionSpec:
    if isinstance(value, OptionSpec):
        return value
    display, description = value
    parts = display.split(maxsplit=1)
    flags = tuple(parts[0].split("/"))
    metavar = parts[1] if len(parts) == 2 else None
    return OptionSpec(flags, description, group="GLOBAL OPTIONS", metavar=metavar)


@dataclass(frozen=True)
class VerbSpec:
    """One public verb and its registration/help attributes.

    ``configure`` adds domain-specific positional arguments or options to the
    generated command parser. ``handler`` is the command implementation. The
    shared library owns registration and shell behavior; the consumer owns
    both callbacks and all domain decisions.
    """

    name: str
    synopsis: str | None = None
    description: str = ""
    group: str = VerbGroup.EXPLORATION.value
    examples: tuple[str, ...] = ()
    mutating: bool = False
    interactive: bool = False
    expensive: bool = False
    include_json: bool = True
    include_progress: bool = True
    arguments: tuple[ArgumentSpec, ...] = ()
    options: tuple[OptionSpec, ...] = ()
    configure: Callable[[ExtendedArgumentParser], None] | None = None
    handler: Callable[..., int | None] | None = None
    summary_description: str | None = None

    def __post_init__(self) -> None:
        if not self.name or self.name.startswith("-"):
            raise ValueError("a verb needs a non-empty name without a leading dash")
        if not self.description:
            raise ValueError(f"verb {self.name!r} must define a description")
        if not self.group:
            raise ValueError(f"verb {self.name!r} must define a semantic group")
        if self.summary_description is not None and not self.summary_description:
            raise ValueError(f"verb {self.name!r} has an empty summary description")

    @property
    def behavior_labels(self) -> tuple[str, ...]:
        labels = []
        if self.mutating:
            labels.append("mutating")
        if self.interactive:
            labels.append("interactive")
        if self.expensive:
            labels.append("potentially expensive")
        return tuple(labels)

    @property
    def summary(self) -> str:
        """Return the top-level one-line description with behavior cues."""

        labels = self.behavior_labels
        description = self.summary_description or self.description
        if not labels:
            return description
        return f"{description} [{'; '.join(labels)}]"

    @property
    def display_synopsis(self) -> str:
        """Use an override or derive concise syntax from declared arguments/options."""
        if self.synopsis is not None:
            return self.synopsis

        parts = []
        for argument in self.arguments:
            parts.append(
                _nargs_display(argument.display, argument.parser_kwargs.get("nargs"))
            )

        exclusive_seen: set[str] = set()
        has_optional_options = self.configure is not None
        for option in self.options:
            group_name = option.mutually_exclusive_group
            if group_name:
                if group_name in exclusive_seen:
                    continue
                exclusive_seen.add(group_name)
                members = [
                    member
                    for member in self.options
                    if member.mutually_exclusive_group == group_name
                ]
                choices = " | ".join(member.display for member in members)
                required = members[0].mutually_exclusive_required
                parts.append(f"({choices})" if required else f"[{choices}]")
            elif option.parser_kwargs.get("required"):
                parts.append(option.display)
            else:
                has_optional_options = True
        if has_optional_options:
            parts.append("[options]")
        return " ".join(parts)

    @property
    def command_description(self) -> str:
        """Return command help including attributes and pasteable examples."""

        sections = [self.description]
        labels = self.behavior_labels
        if labels:
            sections.append("Behavior: " + "; ".join(labels) + ".")
        if self.mutating:
            sections.append(
                "Mutating actions require confirmation unless --yes is supplied."
            )
        if self.examples:
            sections.append(
                "Examples:\n" + "\n".join(f"  {item}" for item in self.examples)
            )
        return "\n\n".join(sections)


def _common_option_specs(
    *, include_json: bool, include_progress: bool, include_confirmation: bool
) -> tuple[OptionSpec, ...]:
    """Return common option metadata for generated help surfaces."""

    options = [
        OptionSpec(
            ("--help",),
            "show this help and exit",
            group="HELP AND VERSION",
            parser_kwargs={"action": "help"},
        ),
        OptionSpec(
            ("--version",),
            "print the short version and exit",
            group="HELP AND VERSION",
            parser_kwargs={"action": "version"},
        ),
        OptionSpec(
            ("--log-level",),
            "set diagnostic verbosity: error, warn, info, debug",
            group="DEBUGGING",
            metavar="LEVEL",
        ),
        OptionSpec(
            ("--quiet",),
            "show warnings and errors only",
            group="DEBUGGING",
            parser_kwargs={"action": "store_true"},
        ),
        OptionSpec(
            ("--debug", "--verbose"),
            "show diagnostic detail",
            group="DEBUGGING",
            parser_kwargs={"action": "store_true"},
        ),
        OptionSpec(
            ("--debug-raw",),
            "show supported raw diagnostic data without redaction",
            group="DEBUGGING",
            parser_kwargs={"action": "store_true"},
        ),
        OptionSpec(
            ("--color", "--no-color"),
            "control terminal colour",
            group="OUTPUT CONTROL",
            parser_kwargs={"action": "store_true"},
        ),
    ]
    if include_json:
        options.append(
            OptionSpec(
                ("--json",),
                "emit machine-readable output",
                group="OUTPUT CONTROL",
                parser_kwargs={"action": "store_true"},
            )
        )
    if include_progress:
        options.append(
            OptionSpec(
                ("--progress",),
                "choose progress: auto, tty, plain, quiet, rawjson (rawjson uses stdout; muted with --json)",
                group="OUTPUT CONTROL",
                metavar="MODE",
            )
        )
    if include_confirmation:
        options.append(
            OptionSpec(
                ("--yes",),
                "accept confirmation prompts",
                group="CONFIRMATION",
                parser_kwargs={"action": "store_true"},
            )
        )
    return tuple(options)


class HelpCatalog:
    """Render one grouped top-level help document from verb metadata."""

    def __init__(
        self,
        identity: CliIdentity,
        *,
        prog: str,
        description: str = "",
        usage: str | None = None,
        getting_started: Sequence[str] = (),
        verbs: Iterable[VerbSpec] = (),
        global_options: Sequence[OptionSpec | tuple[str, str]] = (),
        width: int | None = None,
    ) -> None:
        self.identity = identity
        self.prog = prog
        self.description = description
        self.usage = usage or f"{prog} <verb> [options]"
        self.getting_started = tuple(getting_started)
        self.verbs = tuple(verbs)
        self.global_options = tuple(
            _coerce_option_spec(item) for item in global_options
        )
        displays = [option.display for option in self.global_options]
        if len(displays) != len(set(displays)):
            raise ValueError("top-level help cannot contain duplicate option entries")
        self.command_parsers: Mapping[str, ExtendedArgumentParser] = {}
        self.width = width
        names = [verb.name for verb in self.verbs]
        if len(names) != len(set(names)):
            raise ValueError("top-level help cannot contain duplicate verb names")

    def add_global_options(
        self, options: Iterable[OptionSpec | tuple[str, str]]
    ) -> None:
        """Add structured metadata, refusing conflicting duplicate labels."""

        by_display = {option.display: option for option in self.global_options}
        additions = []
        for option in (_coerce_option_spec(item) for item in options):
            existing = by_display.get(option.display)
            if existing is None:
                by_display[option.display] = option
                additions.append(option)
            elif existing != option:
                raise ValueError(
                    f"conflicting top-level help metadata for {option.display!r}"
                )
        self.global_options += tuple(additions)

    def attach_command_parsers(
        self, parsers: Mapping[str, ExtendedArgumentParser]
    ) -> None:
        """Attach generated parsers for Markdown details of custom structures."""

        self.command_parsers = dict(parsers)

    def render(
        self,
        *,
        width: int | None = None,
        output_format: HelpFormat | str = HelpFormat.TEXT,
    ) -> str:
        output_format = (
            HelpFormat.parse(output_format)
            if isinstance(output_format, str)
            else output_format
        )
        if output_format is HelpFormat.MARKDOWN:
            return self.render_markdown(width=width)
        width = width or self.width or get_terminal_size((120, 24)).columns
        width = max(60, width)
        lines = [self.identity.headline, "", f"Usage: {self.usage}"]
        lines.extend(
            (f"       {self.prog} help [verb]", f"       {self.prog} version", "")
        )
        if self.description:
            wrapper = TextWrapper(
                width=width,
                break_long_words=False,
                break_on_hyphens=False,
            )
            for paragraph in self.description.splitlines():
                if paragraph:
                    lines.extend(wrapper.wrap(paragraph))
                else:
                    lines.append("")
            lines.append("")
        if self.getting_started:
            lines.append("GETTING STARTED")
            lines.extend(f"  {line}" for line in self.getting_started)
            lines.append("")

        group_order: list[str] = []
        for verb in self.verbs:
            if verb.group not in group_order:
                group_order.append(verb.group)
        verb_name_width = max((len(verb.name) for verb in self.verbs), default=0)
        description_column = 2 + verb_name_width + 2
        wrapper_width = max(width, description_column + 20)
        for group in group_order:
            lines.append(group)
            for verb in self.verbs:
                if verb.group != group:
                    continue
                wrapper = TextWrapper(
                    width=wrapper_width,
                    initial_indent=(f"  {verb.name.ljust(verb_name_width)}  "),
                    subsequent_indent=" " * description_column,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                lines.extend(wrapper.wrap(verb.summary) or [f"  {verb.name}"])
            lines.append("")

        option_groups: list[str] = []
        for option in self.global_options:
            if option.group not in option_groups:
                option_groups.append(option.group)
        for group in option_groups:
            lines.append(group)
            group_options = [
                option for option in self.global_options if option.group == group
            ]
            option_width = max(len(option.display) for option in group_options)
            for option in group_options:
                wrapper = TextWrapper(
                    width=max(20, width - option_width - 6),
                    initial_indent=f"  {option.display.ljust(option_width)}  ",
                    subsequent_indent=" " * (option_width + 4),
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                lines.extend(wrapper.wrap(option.description))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def render_markdown(
        self, *, width: int | None = None, include_details: bool = True
    ) -> str:
        """Render the catalog as a Markdown reference from the same metadata."""

        width = width or self.width or 120
        width = max(60, width)
        lines = [f"# {self.identity.headline}", "", "## Usage", "", "```text"]
        lines.extend(
            (
                f"Usage: {self.usage}",
                f"       {self.prog} help [verb]",
                f"       {self.prog} version",
                "```",
                "",
            )
        )
        if self.description:
            lines.extend((self.description, ""))
        if self.getting_started:
            lines.extend(("## Getting started", ""))
            lines.extend(f"- `{line}`" for line in self.getting_started)
            lines.append("")

        group_order: list[str] = []
        for verb in self.verbs:
            if verb.group not in group_order:
                group_order.append(verb.group)
        for group in group_order:
            lines.extend((f"## {group.title()}", ""))
            for verb in self.verbs:
                if verb.group != group:
                    continue
                label = f"`{verb.name}`"
                if not include_details:
                    wrapper = TextWrapper(
                        width=max(20, width - 6),
                        initial_indent=f"- {label} — ",
                        subsequent_indent="  ",
                        break_long_words=False,
                        break_on_hyphens=False,
                    )
                    lines.extend(wrapper.wrap(verb.summary) or [f"- {label}"])
                    continue
                heading = f"### `{verb.name}`"
                if verb.display_synopsis:
                    heading += f" {verb.display_synopsis}"
                command_parser = self.command_parsers.get(verb.name)
                if verb.configure is not None and command_parser is not None:
                    lines.extend((heading, ""))
                    if verb.behavior_labels:
                        lines.extend(
                            (
                                "**Behavior:** "
                                + "; ".join(verb.behavior_labels)
                                + ".",
                                "",
                            )
                        )
                    parser_help = command_parser.format_help()
                    if parser_help.startswith(self.identity.headline):
                        parser_help = parser_help[len(self.identity.headline) :].lstrip(
                            "\n"
                        )
                    lines.extend(
                        ("```text", *parser_help.rstrip().splitlines(), "```", "")
                    )
                    continue
                lines.extend((heading, "", verb.description, ""))
                if verb.behavior_labels:
                    lines.extend(
                        ("**Behavior:** " + "; ".join(verb.behavior_labels) + ".", "")
                    )
                if verb.examples:
                    lines.extend(("**Examples:**", "", "```sh"))
                    lines.extend(verb.examples)
                    lines.extend(("```", ""))
                if verb.arguments:
                    lines.extend(
                        (
                            "#### Arguments",
                            "",
                            "| Argument | Description |",
                            "| --- | --- |",
                        )
                    )
                    for argument in verb.arguments:
                        lines.append(
                            f"| `{_markdown_cell(argument.display)}` | {_markdown_cell(argument.markdown_description)} |"
                        )
                    lines.append("")
                options = (
                    *_common_option_specs(
                        include_json=verb.include_json,
                        include_progress=verb.include_progress,
                        include_confirmation=verb.mutating,
                    ),
                    *verb.options,
                )
                option_groups: list[str] = []
                for option in options:
                    if option.group not in option_groups:
                        option_groups.append(option.group)
                for option_group in option_groups:
                    lines.extend(
                        (
                            f"#### {option_group.title()}",
                            "",
                            "| Option | Description |",
                            "| --- | --- |",
                        )
                    )
                    for option in options:
                        if option.group == option_group:
                            lines.append(
                                f"| `{_markdown_cell(option.display)}` | {_markdown_cell(option.markdown_description)} |"
                            )
                    lines.append("")
            lines.append("")

        option_groups = []
        for option in self.global_options:
            if option.group not in option_groups:
                option_groups.append(option.group)
        for group in option_groups:
            lines.extend(
                (f"## {group.title()}", "", "| Option | Description |", "| --- | --- |")
            )
            for option in self.global_options:
                if option.group == group:
                    lines.append(
                        f"| `{_markdown_cell(option.display)}` | {_markdown_cell(option.description)} |"
                    )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def validate_parser(self, parser: ExtendedArgumentParser) -> None:
        """Ensure the catalog and top-level argparse verbs cannot drift."""

        parser_verbs = set(discover_command_parsers(parser))
        catalog_verbs = {verb.name for verb in self.verbs}
        missing = sorted(parser_verbs - catalog_verbs)
        undocumented = sorted(catalog_verbs - parser_verbs)
        if missing or undocumented:
            details = []
            if missing:
                details.append(
                    "parser verbs missing from catalog: " + ", ".join(missing)
                )
            if undocumented:
                details.append(
                    "catalog verbs missing from parser: " + ", ".join(undocumented)
                )
            raise ValueError("help catalog/parser mismatch; " + "; ".join(details))


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
        kwargs.setdefault("formatter_class", WideRawDescriptionHelpFormatter)
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


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


class WideRawDescriptionHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Preserve examples and use terminal width with a 120-column fallback."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        width = kwargs.setdefault(
            "width", max(60, get_terminal_size((120, 24)).columns)
        )
        kwargs.setdefault("max_help_position", min(40, max(24, width // 3)))
        super().__init__(*args, **kwargs)


def discover_command_parsers(
    parser: ExtendedArgumentParser,
) -> dict[str, ExtendedArgumentParser]:
    """Discover top-level subparsers so consumers need not duplicate a map."""

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _leading_option_remainder(
    argv: Sequence[str], parser: ExtendedArgumentParser
) -> list[str] | None:
    """Return argv after leading root options, or None if it is ambiguous.

    This is used only to recognize the reserved ``help`` and ``version``
    command forms when global options precede them. Normal command parsing is
    still delegated entirely to argparse.
    """

    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            return None
        if not token.startswith("-"):
            return list(argv[index:])
        if token in {"--help", "--version"}:
            return None

        option, separator, _value = token.partition("=")
        action = parser._option_string_actions.get(option)
        if action is None:
            return None
        if separator:
            if action.nargs == 0:
                return None
            index += 1
        elif action.nargs == 0:
            index += 1
        elif action.nargs is None:
            if index + 1 >= len(argv):
                return None
            index += 2
        elif action.nargs == "?":
            if index + 1 < len(argv) and not argv[index + 1].startswith("-"):
                index += 2
            else:
                index += 1
        elif isinstance(action.nargs, int):
            index += 1 + action.nargs
            if index > len(argv):
                return None
        else:
            return None
    return []


def _common_option_conflict(argv: Sequence[str]) -> str | None:
    """Reject mutually exclusive common flags split across parser levels."""

    verbosity: list[tuple[str, str | None]] = []
    color_options: set[str] = set()
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            break
        option, separator, value = token.partition("=")
        if option == "--log-level":
            if not separator:
                if index + 1 < len(argv):
                    value = argv[index + 1]
                    index += 1
                else:
                    value = None
            verbosity.append(("log-level", value))
        elif option == "--quiet":
            verbosity.append(("quiet", None))
        elif option in {"--debug", "--verbose"}:
            verbosity.append(("debug", None))
        elif option in {"--color", "--no-color"}:
            color_options.add(option)
        index += 1

    if len(verbosity) > 1:
        supplied = ", ".join(
            dict.fromkeys(
                f"--log-level={value}" if name == "log-level" and value else f"--{name}"
                for name, value in verbosity
            )
        )
        return f"use only one verbosity control; received {supplied}"
    if len(color_options) > 1:
        return "--color and --no-color are mutually exclusive"
    return None


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
        _print_help(
            parser.format_help(),
            stream,
            output=getattr(parser, "_cli_help_output", None),
            command_names=_parser_command_names(parser),
        )
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
        parser.catalog.add_global_options(
            _common_option_specs(
                include_json=include_json,
                include_progress=include_progress,
                include_confirmation=include_confirmation,
            )
        )
    help_group = parser.add_argument_group("HELP AND VERSION")
    help_group.add_argument(
        "--help", action=_HelpAction, help="show this help and exit"
    )
    help_group.add_argument(
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


@dataclass
class CliRuntime:
    """Per-invocation context passed to a command handler."""

    identity: CliIdentity
    output: CliOutput
    yes: bool = False
    debug: bool = False
    debug_raw: bool = False
    json_mode: bool = False
    progress_mode: ProgressMode = ProgressMode.AUTO

    def confirm(self, prompt: str) -> bool:
        """Ask for a safe default-no confirmation after caller preflight.

        ``--yes`` accepts the prompt, but does not replace domain validation.
        Non-interactive stdin refuses instead of attempting a prompt that will
        fail with EOF. EOF and an explicit negative answer are clean declines.
        """

        if self.yes:
            self.output.info("Confirmation accepted via --yes.")
            return True
        if not self.output._is_tty(self.output.stdin):
            raise CliFailure(
                "confirmation is required, but stdin is not interactive; "
                "review the requested change and rerun with --yes",
                exit_code=2,
            )
        self.output.stderr.write(f"{self.output._message(prompt)} [y/N] ")
        self.output.stderr.flush()
        answer = self.output.stdin.readline()
        if answer == "":
            self.output.emit(
                LogLevel.INFO, "No confirmation received; no changes made.", force=True
            )
            return False
        if answer.strip().casefold() in {"y", "yes"}:
            return True
        self.output.emit(LogLevel.INFO, "Declined; no changes made.", force=True)
        return False

    def progress(self, *, mode: ProgressMode | str | None = None) -> ProgressRenderer:
        requested_mode = mode or self.progress_mode
        if not isinstance(requested_mode, ProgressMode):
            requested_mode = ProgressMode.parse(requested_mode)
        if self.json_mode and requested_mode is ProgressMode.RAWJSON:
            requested_mode = ProgressMode.QUIET
        stream = (
            self.output.stdout
            if requested_mode is ProgressMode.RAWJSON
            else self.output.stderr
        )
        return ProgressRenderer(
            requested_mode,
            stream=stream,
            color=self.output.color,
            level=self.output.level,
            json_mode=self.json_mode,
            secrets=list(self.output.secrets),
            debug_raw=self.debug_raw,
        )


Handler = Callable[[argparse.Namespace, CliRuntime], int | None]


@dataclass
class RegisteredCli:
    """A fully-built parser and dispatch table produced by :class:`CliRegistry`."""

    identity: CliIdentity
    parser: ExtendedArgumentParser
    handlers: Mapping[str, Handler]
    command_parsers: Mapping[str, ExtendedArgumentParser]
    default_handler: Handler | None = None
    logging_logger: str | None = None
    no_args_action: bool = False

    @property
    def catalog(self) -> HelpCatalog | None:
        """Return the generated help catalog when this CLI has verbs."""

        return self.parser.catalog

    def run(self, **kwargs: Any) -> int:
        """Run this registration with the shared boundary."""

        return run_cli(
            self.parser,
            self.handlers,
            identity=self.identity,
            command_parsers=self.command_parsers,
            default_handler=self.default_handler,
            logging_logger=self.logging_logger,
            no_args_action=self.no_args_action,
            **kwargs,
        )


class CliRegistry:
    """Declare verbs/options once and generate argparse, help, and dispatch.

    Consumers provide the product-specific parser callback and handler for
    each verb. The registry owns the repetitive parser registration, option
    groups, common flags, help catalog, and dispatch map.
    """

    def __init__(
        self,
        identity: CliIdentity,
        *,
        prog: str,
        description: str,
        getting_started: Sequence[str] = (),
        global_options: Sequence[OptionSpec] = (),
        single_command: bool = False,
        logging_logger: str | None = None,
        no_args_action: bool = False,
    ) -> None:
        self.identity = identity
        self.prog = prog
        self.description = description
        self.getting_started = tuple(getting_started)
        self.global_options = tuple(global_options)
        self.single_command = single_command
        self.logging_logger = logging_logger or identity.command_name
        self.no_args_action = no_args_action
        self._verbs: list[VerbSpec] = []

    @property
    def verbs(self) -> tuple[VerbSpec, ...]:
        return tuple(self._verbs)

    def register(self, verb: VerbSpec) -> None:
        """Register one public command, refusing ambiguous duplicate names."""

        if verb.name in {"help", "version"}:
            raise ValueError(f"{verb.name!r} is reserved for standard CLI behavior")
        if any(existing.name == verb.name for existing in self._verbs):
            raise ValueError(f"verb {verb.name!r} is already registered")
        self._verbs.append(verb)

    @staticmethod
    def _add_option_specs(
        parser: ExtendedArgumentParser,
        options: Sequence[OptionSpec],
        *,
        suppress_defaults: bool = False,
    ) -> None:
        groups: dict[str, Any] = {}
        exclusive_members: dict[str, list[OptionSpec]] = {}
        for option in options:
            name = option.mutually_exclusive_group
            if name is not None:
                exclusive_members.setdefault(name, []).append(option)
        for name, members in exclusive_members.items():
            if len(members) < 2:
                raise ValueError(
                    f"mutually-exclusive option group {name!r} needs at least two options"
                )
            if len({option.group for option in members}) != 1:
                raise ValueError(
                    f"mutually-exclusive option group {name!r} must use one help group"
                )
            if len({option.mutually_exclusive_required for option in members}) != 1:
                raise ValueError(
                    f"mutually-exclusive option group {name!r} has inconsistent required metadata"
                )

        exclusive_groups: dict[str, Any] = {}
        for option in options:
            if option.group not in groups:
                groups[option.group] = parser.add_argument_group(option.group)
            target = groups[option.group]
            name = option.mutually_exclusive_group
            if name is not None:
                if name not in exclusive_groups:
                    exclusive_groups[name] = target.add_mutually_exclusive_group(
                        required=option.mutually_exclusive_required
                    )
                target = exclusive_groups[name]
            option.add_to(target, suppress_default=suppress_defaults)

    @staticmethod
    def _add_argument_specs(
        parser: ExtendedArgumentParser, arguments: Sequence[ArgumentSpec]
    ) -> None:
        for argument in arguments:
            argument.add_to(parser)

    def build(self) -> RegisteredCli:
        """Build parser and handler map from the registered definitions."""

        if not self._verbs:
            raise ValueError("a CLI must register at least one command")
        if self.single_command and len(self._verbs) != 1:
            raise ValueError(
                "single_command registries must register exactly one command"
            )
        if self.no_args_action and not self.single_command:
            raise ValueError("no_args_action is only valid for a single-command CLI")
        missing_handlers = [verb.name for verb in self._verbs if verb.handler is None]
        if missing_handlers:
            raise ValueError("verbs missing handlers: " + ", ".join(missing_handlers))

        catalog = None
        if not self.single_command:
            catalog = HelpCatalog(
                self.identity,
                prog=self.prog,
                description=self.description,
                getting_started=self.getting_started,
                verbs=self._verbs,
            )
        parser = ExtendedArgumentParser(
            prog=self.prog,
            description=self.description,
            formatter_class=WideRawDescriptionHelpFormatter,
            identity=self.identity,
            catalog=catalog,
            top_level=not self.single_command,
        )
        add_common_options(
            parser,
            self.identity,
            include_json=(
                self._verbs[0].include_json
                if self.single_command
                else any(verb.include_json for verb in self._verbs)
            ),
            include_progress=(
                self._verbs[0].include_progress
                if self.single_command
                else any(verb.include_progress for verb in self._verbs)
            ),
            include_confirmation=self.single_command and self._verbs[0].mutating,
        )
        self._add_option_specs(parser, self.global_options)
        if catalog is not None:
            catalog.add_global_options(self.global_options)

        command_parsers: dict[str, ExtendedArgumentParser] = {}
        handlers: dict[str, Handler] = {}
        default_handler: Handler | None = None
        if self.single_command:
            verb = self._verbs[0]
            self._add_argument_specs(parser, verb.arguments)
            self._add_option_specs(parser, verb.options)
            if verb.configure is not None:
                verb.configure(parser)
            default_handler = verb.handler  # type: ignore[assignment]
        else:
            subparsers = parser.add_subparsers(
                dest="verb", metavar="VERB", required=True
            )
            for verb in self._verbs:
                command_parser = subparsers.add_parser(
                    verb.name,
                    help=verb.summary,
                    description=verb.command_description,
                    formatter_class=WideRawDescriptionHelpFormatter,
                )
                add_common_options(
                    command_parser,
                    self.identity,
                    include_json=verb.include_json,
                    include_progress=verb.include_progress,
                    include_confirmation=verb.mutating,
                    suppress_defaults=True,
                )
                self._add_argument_specs(command_parser, verb.arguments)
                self._add_option_specs(
                    command_parser, verb.options, suppress_defaults=True
                )
                if verb.configure is not None:
                    verb.configure(command_parser)
                command_parsers[verb.name] = command_parser
                handlers[verb.name] = verb.handler  # type: ignore[assignment]

        if catalog is not None:
            catalog.attach_command_parsers(command_parsers)
        return RegisteredCli(
            self.identity,
            parser,
            handlers,
            command_parsers,
            default_handler,
            self.logging_logger,
            self.no_args_action,
        )


def _runtime_from_args(
    args: argparse.Namespace,
    identity: CliIdentity,
    *,
    stdout: TextIO | None,
    stderr: TextIO | None,
    stdin: TextIO | None,
    secrets: Sequence[str],
) -> CliRuntime:
    debug_raw = bool(getattr(args, "debug_raw", False))
    quiet = bool(getattr(args, "quiet", False))
    explicit_level = getattr(args, "log_level", None)
    debug = (
        bool(getattr(args, "debug", False))
        or debug_raw
        or explicit_level == LogLevel.DEBUG.value
    )
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
        level = LogLevel.WARN
    elif debug:
        level = LogLevel.DEBUG
    else:
        level = LogLevel.INFO
    progress_value = getattr(args, "progress", None) or ProgressMode.AUTO.value
    progress = ProgressMode.parse(progress_value)
    json_mode = bool(getattr(args, "json", False))
    if json_mode and progress is ProgressMode.RAWJSON:
        progress = ProgressMode.QUIET
    if quiet and debug_raw:
        raise CliFailure(
            "--quiet and --debug-raw are contradictory", exit_code=2, show_help=True
        )
    output = CliOutput(
        identity,
        level=level,
        color=getattr(args, "color", None),
        json_mode=json_mode,
        debug_raw=debug_raw,
        secrets=secrets,
        stdin=stdin,
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


def _parser_command_names(parser: ExtendedArgumentParser) -> tuple[str, ...]:
    catalog = parser.catalog
    if catalog is None:
        return ()
    return tuple(verb.name for verb in catalog.verbs)


def _help_color_setting(argv: Sequence[str]) -> bool | None:
    """Read explicit color switches even when ``--help`` exits parsing early.

    Conflicting switches are refused before help dispatch; choose plain output
    for that refusal rather than letting their order affect its presentation.
    """
    switches: set[str] = set()
    for token in argv:
        if token == "--":
            break
        if token in {"--color", "--no-color"}:
            switches.add(token)
    if "--no-color" in switches:
        return False
    return True if "--color" in switches else None


def _print_help(
    text: str,
    stream: TextIO,
    *,
    output: CliOutput | None = None,
    command_names: Sequence[str] = (),
) -> None:
    if output is not None:
        text = output.format_help(text, stream=stream, command_names=command_names)
    stream.write(text)
    if not text.endswith("\n"):
        stream.write("\n")
    stream.flush()


def _print_error_with_help(
    message: str,
    parser: ExtendedArgumentParser,
    output: CliOutput,
    *,
    command_names: Sequence[str] = (),
    hint: str | None = None,
    debug_detail: str | None = None,
) -> None:
    """Put the actionable failure first, then separate it from full help."""
    output.emit("error", message)
    if hint is not None:
        output.hint(hint)
    if debug_detail is not None:
        output.debug(debug_detail)
    output.stderr.write("\n")
    output.stderr.flush()
    _print_help(
        parser.format_help(),
        output.stderr,
        output=output,
        command_names=command_names,
    )


def run_cli(
    parser: ExtendedArgumentParser,
    handlers: Mapping[str, Handler],
    *,
    identity: CliIdentity,
    argv: Sequence[str] | None = None,
    command_parsers: Mapping[str, ExtendedArgumentParser] | None = None,
    default_handler: Handler | None = None,
    logging_logger: str | None = None,
    no_args_action: bool = False,
    expected_exceptions: tuple[type[BaseException], ...] = (),
    secrets: Sequence[str] = (),
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    """Run a conventional CLI while keeping parser and exception policy shared."""

    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    stdin = stdin if stdin is not None else sys.stdin
    raw = list(sys.argv[1:] if argv is None else argv)
    discovered_parsers = discover_command_parsers(parser)
    discovered_parsers.update(command_parsers or {})
    command_parsers = discovered_parsers
    if parser.catalog is not None:
        parser.catalog.validate_parser(parser)
    help_output = CliOutput(
        identity,
        color=_help_color_setting(raw),
        secrets=secrets,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )
    for command_parser in (parser, *command_parsers.values()):
        command_parser._cli_help_stream = stdout
        command_parser._cli_help_output = help_output

    command_form = _leading_option_remainder(raw, parser)
    option_conflict = _common_option_conflict(raw)
    if option_conflict:
        error_parser = parser
        if command_form and command_form[0] in command_parsers:
            error_parser = command_parsers[command_form[0]]
        _print_error_with_help(
            f"{parser.prog}: {option_conflict}",
            error_parser,
            help_output,
            command_names=_parser_command_names(error_parser),
        )
        return 2

    if not raw and not no_args_action:
        _print_help(
            parser.format_help(),
            stdout,
            output=help_output,
            command_names=_parser_command_names(parser),
        )
        return 0
    if command_form == ["help"]:
        _print_help(
            parser.format_help(),
            stdout,
            output=help_output,
            command_names=_parser_command_names(parser),
        )
        return 0
    if (
        command_form is not None
        and len(command_form) == 2
        and command_form[0] == "help"
    ):
        command_parser = command_parsers.get(command_form[1])
        if command_parser is None:
            _print_error_with_help(
                f"unknown help topic {command_form[1]!r}",
                parser,
                help_output,
                command_names=_parser_command_names(parser),
            )
            return 2
        _print_help(
            command_parser.format_help(),
            stdout,
            output=help_output,
            command_names=_parser_command_names(command_parser),
        )
        return 0
    if command_form == ["version"]:
        _print_help(identity.version_line, stdout)
        return 0

    try:
        args = parser.parse_args(raw)
    except UsageError as exc:
        error_parser = exc.parser
        if (
            error_parser is parser
            and command_form
            and command_form[0] in command_parsers
        ):
            # argparse can report an unsupported option after a known verb
            # against the root parser. Keep the standard's known-verb error
            # behavior by showing that verb's help, not the whole CLI catalog.
            error_parser = command_parsers[command_form[0]]
        _print_error_with_help(
            f"{error_parser.prog}: {exc.message}",
            error_parser,
            help_output,
            command_names=_parser_command_names(error_parser),
        )
        return 2
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return int(exc.code)

    verb = getattr(args, "verb", None)
    if verb is None and default_handler is None:
        _print_error_with_help(
            "a verb is required for this invocation",
            parser,
            help_output,
            command_names=_parser_command_names(parser),
        )
        return 2
    handler = handlers.get(verb)
    if handler is None and verb is None:
        handler = default_handler
    if handler is None:
        _print_error_with_help(
            f"no handler registered for verb {verb!r}",
            parser,
            help_output,
            command_names=_parser_command_names(parser),
        )
        return 2

    try:
        command_parser = command_parsers.get(verb)
        if command_parser is not None:
            if (
                bool(getattr(args, "json", False))
                and "--json" not in command_parser._option_string_actions
            ):
                raise CliFailure(
                    f"--json is not supported for verb {verb!r}",
                    exit_code=2,
                    show_help=True,
                )
            if (
                getattr(args, "progress", None) is not None
                and "--progress" not in command_parser._option_string_actions
            ):
                raise CliFailure(
                    f"--progress is not supported for verb {verb!r}",
                    exit_code=2,
                    show_help=True,
                )
        runtime = _runtime_from_args(
            args,
            identity,
            stdout=stdout,
            stderr=stderr,
            stdin=stdin,
            secrets=secrets,
        )
        if logging_logger is None:
            result = handler(args, runtime)
        else:
            with logging_context(runtime.output, logging.getLogger(logging_logger)):
                result = handler(args, runtime)
        return 0 if result is None else int(result)
    except KeyboardInterrupt:
        # Runtime creation normally precedes the handler, but keep Ctrl-C safe
        # even when a handler raises it before producing any output.
        try:
            runtime.output.cancelled()  # type: ignore[union-attr]
        except UnboundLocalError:
            help_output.cancelled()
        return 130
    except CliFailure as exc:
        if exc.show_help:
            command_parser = command_parsers.get(verb)
            help_output_for_error = (
                runtime.output if "runtime" in locals() else help_output
            )
            _print_error_with_help(
                exc.message,
                command_parser if command_parser is not None else parser,
                help_output_for_error,
                command_names=_parser_command_names(parser),
                hint=exc.hint,
                debug_detail=(
                    f"handled CLI refusal in {verb or parser.prog}"
                    if "runtime" in locals() and runtime.debug
                    else None
                ),
            )
        elif "runtime" in locals():
            runtime.output.error(exc.message, hint=exc.hint)
            if runtime.debug:
                runtime.output.debug(f"handled CLI refusal in {verb or parser.prog}")
        else:
            help_output.error(exc.message, hint=exc.hint)
        return exc.exit_code
    except expected_exceptions as exc:
        if "runtime" in locals():
            runtime.output.error(str(exc))
            if runtime.debug:
                runtime.output.debug(
                    f"handled {type(exc).__name__} in {verb or parser.prog}"
                )
        else:
            help_output.error(str(exc))
        return 1
    except Exception:
        # Unexpected programming failures intentionally remain tracebacks. The
        # outer Python entrypoint prints one traceback; do not print a second
        # copy here when --debug is active.
        raise
