"""Audit logging for admin action previews.

Writes append-only JSONL records to a user-specified path. Each record contains
timestamp, user, action kind, target, command argv, mode (preview), and whether
admin mode was enabled.

No implicit writes — only when --audit-log PATH is provided.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path
from typing import TextIO


@dataclasses.dataclass(frozen=True)
class AuditRecord:
    """One audit record for an admin action preview."""

    ts: float
    user: str
    kind: str
    target: str
    argv: tuple[str, ...]
    mode: str
    admin: bool


def _resolve_user() -> str:
    """Best-effort: return LOGNAME/USER or 'unknown'."""
    return os.environ.get("LOGNAME") or os.environ.get("USER") or "unknown"


class AuditLog:
    """Append-only JSONL audit log for admin action previews."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _open(self) -> TextIO:
        """Open in append mode; create parent dirs if needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return self._path.open("a")

    def record(self, kind: str, target: str, argv: tuple[str, ...], *, admin: bool) -> AuditRecord:
        """Write one audit record and return it."""
        rec = AuditRecord(
            ts=time.time(),
            user=_resolve_user(),
            kind=kind,
            target=target,
            argv=argv,
            mode="preview",
            admin=admin,
        )
        line = json.dumps({
            "ts": rec.ts,
            "user": rec.user,
            "kind": rec.kind,
            "target": rec.target,
            "argv": list(rec.argv),
            "mode": rec.mode,
            "admin": rec.admin,
        })
        with self._open() as fh:
            fh.write(line)
            fh.write("\n")
        return rec
