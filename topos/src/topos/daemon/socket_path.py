"""Resolve the host-provided observe socket for topos clients."""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping
from pathlib import Path

from cli_extended import CliFailure

SOCKET_ENV = "TOPOS_OBSERVE_SOCKET"
_REMEDY = (
    "log in again, or `set -a; . /etc/host-endpoints.env`, "
    "or run `toposd install`"
)


def resolve_observe_socket(
    override: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
) -> Path:
    """Use an explicit path or the required process endpoint variable."""
    if override is not None:
        value = os.fspath(override)
        if not value:
            raise CliFailure("observe socket path cannot be empty", exit_code=2)
        return _absolute(value, "the explicit socket path")
    try:
        require_env = importlib.import_module("augeaswrap.endpoints").require_env
        value = require_env(SOCKET_ENV, remedy=_REMEDY, environ=environ)
    except CliFailure as exc:
        raise CliFailure(exc.message, exit_code=2) from None
    return _absolute(value, SOCKET_ENV)


def _absolute(value: str, source: str) -> Path:
    """A relative path would silently follow the working directory: refuse it by name."""
    if not os.path.isabs(value):
        raise CliFailure(f"{source} must be an absolute socket path, got {value!r}", exit_code=2)
    return Path(value)


__all__ = ["SOCKET_ENV", "resolve_observe_socket"]
