"""Internal stdlib-only helpers for metadata-bearing Git patch series."""

from .core import (
    Change,
    ChangeDelta,
    SeriesError,
    check,
    closure,
    delta,
    load_vocabulary,
    parse,
    render,
    series_digest,
)

__version__ = "0.1.0"

__all__ = [
    "Change",
    "ChangeDelta",
    "SeriesError",
    "__version__",
    "check",
    "closure",
    "delta",
    "load_vocabulary",
    "parse",
    "render",
    "series_digest",
]
