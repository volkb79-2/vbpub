"""Contract-compatible diagnostics, colour, redaction, and logging."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from enum import Enum
from typing import Any, TextIO

from .identity import CliIdentity


class LogLevel(str, Enum):
    """Public diagnostic levels, in the spelling exposed by the CLI."""

    ERROR = "error"
    WARN = "warn"
    INFO = "info"
    DEBUG = "debug"

    @property
    def numeric(self) -> int:
        return {
            LogLevel.ERROR: 40,
            LogLevel.WARN: 30,
            LogLevel.INFO: 20,
            LogLevel.DEBUG: 10,
        }[self]

    @classmethod
    def parse(cls, value: str) -> LogLevel:
        try:
            return cls(value.lower())
        except ValueError as exc:
            allowed = ", ".join(item.value for item in cls)
            raise ValueError(
                f"invalid log level {value!r}; expected one of: {allowed}"
            ) from exc


_LEVEL_LABEL = {
    LogLevel.ERROR: "ERROR",
    LogLevel.WARN: "WARN",
    LogLevel.INFO: "INFO",
    LogLevel.DEBUG: "DEBUG",
}
_ANSI = {
    LogLevel.ERROR: "\033[31m",
    LogLevel.WARN: "\033[33m",
    LogLevel.INFO: "\033[36m",
    LogLevel.DEBUG: "\033[2m",
}
_RESET = "\033[0m"
_BOLD = "\033[1m"
_BLUE = "\033[34m"
_CYAN_BOLD = "\033[1;36m"
_HELP_OPTION_RE = re.compile(r"(?<![\w-])(--[A-Za-z0-9][A-Za-z0-9-]*)")
_HELP_TAG_RE = re.compile(r"\[(INFO|WARN|ERROR|DEBUG)\]")
_HELP_TAG_COLOR = {label: _ANSI[level] for level, label in _LEVEL_LABEL.items()}


def _colorize_help(
    text: str,
    *,
    identity: CliIdentity,
    command_names: Sequence[str] = (),
) -> str:
    """Apply restrained ANSI styling to generated terminal help only."""
    rendered: list[str] = []
    command_names = tuple(command_names)
    command_pattern = None
    if command_names:
        command_pattern = re.compile(
            r"^(\s{2})("
            + "|".join(re.escape(name) for name in command_names)
            + r")(?=\s|$)"
        )
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        ending = raw_line[len(line) :]
        stripped = line.strip()
        if stripped == identity.headline:
            line = f"{_CYAN_BOLD}{line}{_RESET}"
        elif re.fullmatch(r"[A-Z][A-Z0-9 /&()_-]*", stripped) or re.fullmatch(
            r"[A-Za-z][A-Za-z0-9 /&()_-]*:", stripped
        ):
            line = f"{_BOLD}{_BLUE}{line}{_RESET}"
        else:
            if stripped.lower().startswith(("usage:", "usage ")):
                line = re.sub(
                    r"(?i)(usage:?)",
                    lambda match: f"{_BOLD}{_CYAN_BOLD}{match.group(0)}{_RESET}",
                    line,
                    count=1,
                )
            if command_pattern is not None:
                line = command_pattern.sub(
                    lambda match: (
                        f"{match.group(1)}{_CYAN_BOLD}{match.group(2)}{_RESET}"
                    ),
                    line,
                    count=1,
                )
            line = _HELP_OPTION_RE.sub(
                lambda match: f"{_CYAN_BOLD}{match.group(1)}{_RESET}", line
            )
            line = _HELP_TAG_RE.sub(
                lambda match: (
                    f"{_HELP_TAG_COLOR[match.group(1)]}{match.group(0)}{_RESET}"
                ),
                line,
            )
        rendered.append(line + ending)
    return "".join(rendered)


def redact_text(value: object, secrets: Sequence[str] = ()) -> str:
    """Render text while replacing explicitly supplied secret values.

    Redaction is intentionally explicit. A caller that knows a token, private
    value, or password must provide it; the helper does not pretend that a
    heuristic can identify every secret safely.
    """

    text = str(value)
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    return text


def redact_value(value: Any, secrets: Sequence[str] = ()) -> Any:
    """Recursively redact strings in a JSON-like value."""

    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, Mapping):
        return {key: redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(redact_value(item, secrets) for item in value)
    if isinstance(value, list):
        return [redact_value(item, secrets) for item in value]
    return value


class CliOutput:
    """Human diagnostics plus a clean primary-result stream.

    Diagnostics are written to stderr and primary results to stdout. The
    streams are injectable so tests and embedding applications do not need to
    redirect process-global streams.
    """

    def __init__(
        self,
        identity: CliIdentity,
        *,
        level: LogLevel | str = LogLevel.INFO,
        color: bool | None = None,
        json_mode: bool = False,
        debug_raw: bool = False,
        secrets: Sequence[str] = (),
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.identity = identity
        self.level = LogLevel.parse(level) if isinstance(level, str) else level
        self.color = color
        self.json_mode = json_mode
        self.debug_raw = debug_raw
        self.secrets = tuple(secrets)
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self.stderr = stderr if stderr is not None else sys.stderr
        self._identity_written = False
        self._raw_warning_written = False

    @staticmethod
    def _is_tty(stream: TextIO) -> bool:
        try:
            return bool(stream.isatty())
        except (AttributeError, OSError):
            return False

    def color_enabled(self, stream: TextIO | None = None) -> bool:
        """Resolve explicit colour, TTY detection, and ``NO_COLOR``."""

        stream = stream if stream is not None else self.stderr
        if self.color is False:
            return False
        if self.color is True:
            return True
        return self._is_tty(stream) and "NO_COLOR" not in os.environ

    def format_help(
        self,
        text: str,
        *,
        stream: TextIO | None = None,
        command_names: Sequence[str] = (),
    ) -> str:
        """Style generated terminal help using this invocation's color policy.

        Help and primary results are written to stdout; diagnostics use stderr.
        The caller supplies the destination stream so automatic TTY detection
        follows the stream that will actually receive the text.
        """
        # Parser diagnostics can echo a rejected token. Redact them even on
        # early-help paths that have not constructed the full runtime yet.
        text = redact_text(text, self.secrets)
        if not self.color_enabled(stream):
            return text
        return _colorize_help(text, identity=self.identity, command_names=command_names)

    @property
    def is_interactive(self) -> bool:
        """Whether both input and output are terminals suitable for prompts."""

        return self._is_tty(self.stdin) and self._is_tty(self.stdout)

    def _tag(self, level: LogLevel) -> str:
        label = f"[{_LEVEL_LABEL[level]}]"
        if self.color_enabled():
            return f"{_ANSI[level]}{label}{_RESET}"
        return label

    def _message(self, value: object) -> str:
        return str(value) if self.debug_raw else redact_text(value, self.secrets)

    def emit(
        self, level: LogLevel | str, message: object, *, force: bool = False
    ) -> None:
        """Write one severity-tagged diagnostic to stderr."""

        level = LogLevel.parse(level) if isinstance(level, str) else level
        if not force and level.numeric < self.level.numeric:
            return
        print(
            f"{self._tag(level)} {self._message(message)}", file=self.stderr, flush=True
        )

    def info(self, message: object) -> None:
        self.emit(LogLevel.INFO, message)

    def warn(self, message: object) -> None:
        self.emit(LogLevel.WARN, message)

    def debug(self, message: object) -> None:
        self.emit(LogLevel.DEBUG, message)

    def error(self, message: object, *, hint: object | None = None) -> None:
        self.emit(LogLevel.ERROR, message)
        if hint is not None:
            self.hint(hint)
        if not self._identity_written:
            self.stderr.write("\n")
            headline = self.format_help(self.identity.headline, stream=self.stderr)
            print(headline, file=self.stderr, flush=True)
            self._identity_written = True

    def hint(self, message: object) -> None:
        """Write an actionable hint without pretending it is a log level."""

        text = self._message(message)
        if self.color_enabled():
            text = f"\033[36mHint:\033[0m {text}"
        else:
            text = f"Hint: {text}"
        print(text, file=self.stderr, flush=True)

    def cancelled(self) -> None:
        """Write cancellation even when normal informational output is quiet."""

        self.emit(LogLevel.INFO, "Cancelled.", force=True)

    def raw_warning(self) -> None:
        """Warn once that ``--debug-raw`` can expose credentials and secrets."""

        if self.debug_raw and not self._raw_warning_written:
            self.emit(
                LogLevel.WARN,
                "--debug-raw is active: credentials, tokens, passwords, private keys, "
                "and other secrets may be exposed; do not copy this output into shared logs.",
                force=True,
            )
            self._raw_warning_written = True

    def primary(self, value: object) -> None:
        """Write the command's primary result without diagnostic decoration."""

        if self.json_mode:
            json.dump(
                redact_value(value, ())
                if self.debug_raw
                else redact_value(value, self.secrets),
                self.stdout,
            )
            self.stdout.write("\n")
            self.stdout.flush()
            return
        print(
            value if self.debug_raw else redact_text(value, self.secrets),
            file=self.stdout,
            flush=True,
        )

    def primary_json(self, value: object) -> None:
        """Write a JSON result regardless of the normal output mode."""

        json.dump(
            redact_value(value, ())
            if self.debug_raw
            else redact_value(value, self.secrets),
            self.stdout,
        )
        self.stdout.write("\n")
        self.stdout.flush()


class CliLoggingHandler(logging.Handler):
    """Adapt standard-library logging records to :class:`CliOutput`."""

    def __init__(self, output: CliOutput) -> None:
        super().__init__()
        self.output = output
        self._owner_logger: logging.Logger | None = None
        self._previous_level: int | None = None
        self._previous_propagate: bool | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            level = LogLevel.ERROR
        elif record.levelno >= logging.WARNING:
            level = LogLevel.WARN
        elif record.levelno >= logging.INFO:
            level = LogLevel.INFO
        else:
            level = LogLevel.DEBUG
        self.output.emit(level, self.format(record))


def install_logging(
    output: CliOutput, logger: logging.Logger | None = None
) -> CliLoggingHandler:
    """Install one handler on a bounded, named application logger.

    With no logger supplied, the command name is used instead of the root
    logger. The selected logger is temporarily made verbose enough for
    ``CliOutput`` to apply the CLI's level policy, and propagation is disabled
    to prevent duplicate root-handler output. Call :func:`uninstall_logging`
    when the invocation ends.
    """

    logger = (
        logger
        if logger is not None
        else logging.getLogger(output.identity.command_name)
    )
    for existing in logger.handlers:
        if isinstance(existing, CliLoggingHandler) and existing.output is output:
            return existing
    handler = CliLoggingHandler(output)
    handler._owner_logger = logger
    handler._previous_level = logger.level
    handler._previous_propagate = logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return handler


def uninstall_logging(handler: CliLoggingHandler) -> None:
    """Remove a handler installed by :func:`install_logging` and restore state."""

    logger = handler._owner_logger
    if logger is not None:
        logger.removeHandler(handler)
        if handler._previous_level is not None:
            logger.setLevel(handler._previous_level)
        if handler._previous_propagate is not None:
            logger.propagate = handler._previous_propagate
    handler.close()


@contextmanager
def logging_context(
    output: CliOutput, logger: logging.Logger | None = None
) -> Iterator[CliLoggingHandler]:
    """Install contract logging for a bounded invocation and clean it up."""

    logger = (
        logger
        if logger is not None
        else logging.getLogger(output.identity.command_name)
    )
    already_installed = any(
        isinstance(existing, CliLoggingHandler) and existing.output is output
        for existing in logger.handlers
    )
    handler = install_logging(output, logger)
    try:
        yield handler
    finally:
        if not already_installed:
            uninstall_logging(handler)
