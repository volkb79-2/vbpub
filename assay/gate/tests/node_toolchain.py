"""Node engine checks for opt-in qualification tools."""

from __future__ import annotations

import re


def node_version_supports_pinned_qualification_engines(version: str) -> bool:
    """Match ESLint 10.12.0's declared Node range for the qualification lock."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version.strip())
    if match is None:
        return False
    major, minor, patch = (int(part) for part in match.groups())
    return (
        major >= 24
        or (major == 20 and (minor, patch) >= (19, 0))
        or (major == 22 and (minor, patch) >= (13, 0))
    )
