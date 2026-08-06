"""assay — HOW to judge a change.

assay reads a project's declared lanes, runs one declared command, judges the
result against declared rigor levels, and emits one machine-readable verdict per
lane per commit. It does not choose what to run (the project's ``assay.toml``
does) and it does not choose where to run (an environment tool such as ``ciu``
does).

The one invariant, from which every exclusion follows:

    assay never renders a judgement it cannot make deterministically.

**Zero runtime dependencies — stdlib only** (A-005), enforced mechanically by
``tests/test_dependency_purity.py``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .config import (
    CoverageConfig,
    JudgeConfig,
    Lane,
    LaneFile,
    find_lane_file,
    load_lane_file,
    parse_duration,
)
from .errors import AssayError, LaneConfigError, Outcome, ReasonCode

try:
    __version__ = version("assay")
except PackageNotFoundError:  # running from a source tree with no install
    __version__ = "0+unknown"

__all__ = [
    "AssayError",
    "CoverageConfig",
    "JudgeConfig",
    "Lane",
    "LaneConfigError",
    "LaneFile",
    "Outcome",
    "ReasonCode",
    "__version__",
    "find_lane_file",
    "load_lane_file",
    "parse_duration",
]
