"""Error types; each carries the CLI exit code it maps to."""
from __future__ import annotations

EXIT_TIMEOUT = 3
EXIT_NO_EXACT_TEXT = 4
EXIT_QMP = 5


class VmscreenError(Exception):
    exit_code = 1


class QmpError(VmscreenError):
    """Cannot reach, or got an error from, the QMP socket."""

    exit_code = EXIT_QMP


class WaitTimeout(VmscreenError):
    exit_code = EXIT_TIMEOUT

    def __init__(self, message: str, last_text: str = "") -> None:
        super().__init__(message)
        self.last_text = last_text


class NoExactText(VmscreenError):
    """No exact-text source applies to the current screen."""

    exit_code = EXIT_NO_EXACT_TEXT

    def __init__(self, message: str, tried: list[dict]) -> None:
        super().__init__(message)
        self.tried = tried


class ResumeFailed(QmpError):
    """The guest was stopped for a capture and could not be confirmed running again."""


class OutputExists(VmscreenError):
    """Refusing to overwrite an existing output (use --force)."""
