"""Adapter registry + format auto-detection.

Adding a fifth CLI: write adapters/<name>.py exposing `name`, `sniff`,
`list_sessions`, `parse` (see base.SessionAdapter), then add the module to
ADAPTERS below. Nothing else in this package changes.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from . import claude_code, codex, opencode

ADAPTERS: list[ModuleType] = [claude_code, codex, opencode]


class DetectionError(ValueError):
    pass


def get_adapter(fmt: str) -> ModuleType:
    for adapter in ADAPTERS:
        if adapter.name == fmt:
            return adapter
    known = ", ".join(a.name for a in ADAPTERS)
    raise DetectionError(f"unknown --format {fmt!r}; known formats: {known}")


def detect(path: Path) -> ModuleType:
    matches = [a for a in ADAPTERS if a.sniff(path)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        known = ", ".join(a.name for a in ADAPTERS)
        raise DetectionError(
            f"could not detect a session-log format for {path}; "
            f"pass --format explicitly (one of: {known})"
        )
    names = ", ".join(a.name for a in matches)
    raise DetectionError(
        f"{path} looks like more than one known format ({names}); pass --format explicitly"
    )
