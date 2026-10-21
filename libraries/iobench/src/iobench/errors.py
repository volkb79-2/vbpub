"""Exceptions that map to the exit codes of IOB-CLI-01."""
from __future__ import annotations


class IobenchError(Exception):
    exit_code = 1


class Refusal(IobenchError):
    """Exit 2: the run is refused; names the condition and a safe remedy (IOB-CLI-02)."""

    exit_code = 2

    def __init__(self, condition: str, message: str, remedy: str = "") -> None:
        self.condition, self.remedy = condition, remedy
        super().__init__(f"refused: {message}" + (f"; {remedy}" if remedy else ""))


class RunFailed(IobenchError):
    exit_code = 1


class Aborted(IobenchError):
    """Exit 8: the run was aborted (pressure, /run/iobench/abort, signal) and restored."""

    exit_code = 8

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"aborted: {reason}")


class VerificationFailure(IobenchError):
    """Exit 9: restore read-back mismatch or an io.max difference."""

    exit_code = 9
