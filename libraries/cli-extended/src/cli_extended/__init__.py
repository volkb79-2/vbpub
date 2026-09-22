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
    ArgumentSpec,
    CliFailure,
    CliRegistry,
    CliRuntime,
    ExtendedArgumentParser,
    HelpCatalog,
    HelpFormat,
    OptionSpec,
    RegisteredCli,
    UsageError,
    VerbGroup,
    VerbSpec,
    add_common_options,
    discover_command_parsers,
    run_cli,
)
from .progress import ProgressMode, ProgressRenderer
from .testing import assert_cli_contract

__all__ = [
    "ArgumentSpec",
    "CliFailure",
    "CliIdentity",
    "CliLoggingHandler",
    "CliOutput",
    "CliRegistry",
    "CliRuntime",
    "ExtendedArgumentParser",
    "HelpCatalog",
    "HelpFormat",
    "LogLevel",
    "OptionSpec",
    "ProgressMode",
    "ProgressRenderer",
    "RegisteredCli",
    "UsageError",
    "VerbGroup",
    "VerbSpec",
    "VersionLookupError",
    "add_common_options",
    "assert_cli_contract",
    "discover_command_parsers",
    "install_logging",
    "logging_context",
    "redact_text",
    "redact_value",
    "run_cli",
    "uninstall_logging",
]
