"""Reusable command-line contract helpers for vbpub projects."""

from .identity import CliIdentity, VersionLookupError
from .output import (
    CliLoggingHandler,
    CliOutput,
    LogLevel,
    install_logging,
    redact_text,
    redact_value,
)
from .parser import (
    CliFailure,
    CliRuntime,
    ExtendedArgumentParser,
    HelpCatalog,
    UsageError,
    VerbGroup,
    VerbSpec,
    add_common_options,
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
    "LogLevel",
    "ProgressMode",
    "ProgressRenderer",
    "UsageError",
    "VerbGroup",
    "VerbSpec",
    "VersionLookupError",
    "add_common_options",
    "install_logging",
    "redact_text",
    "redact_value",
    "run_cli",
]
