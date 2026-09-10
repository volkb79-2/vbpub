"""The one seam every session-log format must cross to become a list of
NormalizedEvent. Adding a new CLI means writing one module here that
implements this Protocol -- nothing in classifier.py, select.py, or
render.py should ever need to change.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from ..config import ExtractConfig
from ..events import NormalizedEvent


@runtime_checkable
class SessionAdapter(Protocol):
    """name is the --format value this adapter answers to."""

    name: ClassVar[str]

    @staticmethod
    def sniff(path: Path) -> bool:
        """Cheap, best-effort format detection from the path alone (a
        header peek, an extension, a directory shape) -- never a full
        parse. Auto-detect tries every registered adapter's sniff() and
        errors if zero or more than one claims the path."""
        ...

    @staticmethod
    def list_sessions(path: Path) -> list[str]:
        """Session identifiers available at this path. A one-session-per-
        file format (Claude Code, Codex) returns exactly one synthetic id
        (str(path)); a shared store (opencode's SQLite DB) returns the real
        session ids it holds, and the caller must then pick one via
        --session."""
        ...

    @staticmethod
    def parse(path: Path, session_id: str, config: ExtractConfig) -> list[NormalizedEvent]:
        """The whole job: raw source -> chronologically-ordered
        NormalizedEvent list, already filtered to what this tool ever
        wants (real operator/controller text, Q&A pairs, lifecycle
        markers, assistant text, and thinking only if config says so).
        config.since_marker, if set, must be honored here (raise
        ValueError if the marker doesn't resolve within this session) --
        selection downstream assumes every returned event is already
        in-scope."""
        ...
