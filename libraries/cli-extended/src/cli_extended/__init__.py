"""Reusable command-line contract helpers for vbpub projects."""

from .identity import CliIdentity, VersionLookupError
from .output import (
    CliLoggingHandler,
    CliOutput,
    LogLevel,
    install_logging,
    logging_context,
    redact_text,
    redact_value,
    uninstall_logging,
)
from .parser import (
    CliFailure,
    CliRuntime,
    ExtendedArgumentParser,
    HelpCatalog,
    HelpFormat,
    UsageError,
    VerbGroup,
    VerbSpec,
    add_common_options,
    discover_command_parsers,
    run_cli,
)
from .progress import ProgressMode, ProgressRenderer

__all__ = [
    "CliFailure",
    "CliIdentity",
    "CliLoggingHandler",
    "CliOutput",
    "CliRuntime",
    "ExtendedArgumentParser",
    "HelpCatalog",
    "HelpFormat",
    "LogLevel",
    "ProgressMode",
    "ProgressRenderer",
    "UsageError",
    "VerbGroup",
    "VerbSpec",
    "VersionLookupError",
    "add_common_options",
    "discover_command_parsers",
    "install_logging",
    "logging_context",
    "redact_text",
    "redact_value",
    "run_cli",
    "uninstall_logging",
]
