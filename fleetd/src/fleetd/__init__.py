"""Fleet facts and campaign planning, without transport or execution."""

from __future__ import annotations


def __getattr__(name: str) -> str:
    """Expose ``__version__`` from the installed distribution, with no literal fallback.

    The version is derived at build time from the ``fleetd-v*`` tag
    (``tool.setuptools_scm``); a literal kept here would go stale between a tag
    and the next commit. ``fleetd.cli.identity`` is the same source, so
    library callers and the CLI cannot disagree.
    """

    if name == "__version__":
        from .cli import identity

        return identity().version
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
