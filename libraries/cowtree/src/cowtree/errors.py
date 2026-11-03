"""Typed failures raised by cowtree."""
from __future__ import annotations


class CowError(Exception):
    """Base class for failures safe for a consumer to classify."""

    code = "cow"


class RootInvalid(CowError):
    code = "root-invalid"


class SpaceRefused(CowError):
    code = "space"

    def __init__(self, needed_bytes: int, available_bytes: int, estimate: dict[str, int]):
        self.needed_bytes = needed_bytes
        self.available_bytes = available_bytes
        self.estimate = dict(estimate)
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(self.estimate.items()))
        super().__init__(f"space estimate needs {needed_bytes} bytes but only {available_bytes} are available ({rendered})")


class PathRefused(CowError):
    code = "path"


class DestinationExists(PathRefused):
    """A destination appeared before an exclusive backend create."""


class BackendFailed(CowError):
    code = "backend"

    def __init__(self, op: str, err: int | None, path: str | None = None, detail: str | None = None):
        self.op = op
        self.errno = err
        self.path = path
        self.detail = detail
        message = f"backend operation {op!r} failed"
        if err is not None:
            message += f" (errno={err})"
        if path is not None:
            message += f" at {path!r}"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class LeaseHeld(CowError):
    code = "lease"
