"""Exception vocabulary and process exit codes."""

from __future__ import annotations


class DrStateError(Exception):
    """Base class for errors with a defined command-line outcome."""

class UsageError(DrStateError):
    """The command line or request is invalid."""
    exit_code = 2

class Refused(DrStateError):
    """A safety rule refused the request before changing anything."""
    exit_code = 3

class FormatError(DrStateError):
    """An unsupported or malformed persistent format blocked a write."""
    exit_code = 3

class BootConfigError(DrStateError):
    """A boot selector cannot be interpreted safely."""
    exit_code = 3

class Unsupported(DrStateError):
    """The selected backend does not implement the operation."""
    exit_code = 4

class CheckFailed(DrStateError):
    """One or more check violations are error-level."""
    exit_code = 5

class LockTimeout(DrStateError):
    """Another process held the required lock for the whole timeout."""
    exit_code = 6

class StorageError(DrStateError):
    """A storage operation failed after its own cleanup."""
    exit_code = 1

class BootError(DrStateError):
    """A boot-publication operation failed after its own cleanup."""
    exit_code = 1

class CapacityError(DrStateError):
    """The boot medium cannot hold the requested publication."""
    exit_code = 1


def exit_code(error: BaseException) -> int:
    """Return the command-line exit code for an exception."""
    if isinstance(error, DrStateError):
        return error.exit_code
    return 70
