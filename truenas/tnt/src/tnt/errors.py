"""Error types. Every domain failure is one of these; the CLI maps them to one ``[ERROR]`` line."""
from __future__ import annotations


class TntError(Exception):
    """Base class for expected failures."""


class ProfileError(TntError):
    """A profile file or key file is missing, malformed or unsafe."""


class TransportError(TntError):
    """Connecting, authenticating or calling failed. ``kind`` is one of connect, auth, call, refused."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class UnsupportedVersion(TntError):
    """The target reports an API generation tnt has no adapter for."""


class Refused(TntError):
    """A safety rule refused the operation (read-only session, refusal list, state drift, missing confirmation)."""

    def __init__(self, rule: str, message: str):
        super().__init__(f"{rule}: {message}")
        self.rule = rule


class PlanExecutionError(TntError):
    """A plan stopped after one or more calls were attempted; ``partial`` records every call outcome."""

    def __init__(self, message: str, partial):
        super().__init__(message)
        self.partial = partial
