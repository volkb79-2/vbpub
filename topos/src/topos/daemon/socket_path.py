"""Resolve the host-provided observe socket for topos clients."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from cli_extended import CliFailure

from augeaswrap.endpoints import require_env


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
        return Path(value)
    try:
        return Path(require_env(SOCKET_ENV, remedy=_REMEDY, environ=environ))
    except CliFailure as exc:
        raise CliFailure(exc.message, exit_code=2) from None


__all__ = ["SOCKET_ENV", "resolve_observe_socket"]
