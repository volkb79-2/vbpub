"""Which Claude Code versions the STOP/ERROR classification has been verified on.

Claude Code writes no structural flag on some harness-synthetic records (a
Ctrl+C interrupt is plain `user` text since 2.1.289; a permission denial is a
`tool_result` whose only marker is its text). Those are recognised by exact
strings, so a new harness release can change a string and silently turn a STOP
into an "operator message". `VERIFIED_HARNESS_VERSIONS` lists the versions for
which a REAL interrupt / denial / error record set is pinned as a fixture
(`tests/fixtures/real_transcript_2_1_289.jsonl`); a transcript recording any
other `version` gets ONE header warning.

Re-verify step for every new Claude Code version (also in the
`nyxloom-successor` skill and session_extract/README.md):
  1. find a transcript recorded by that version that contains an interrupt, a
     permission denial and a tool error;
  2. run `nyxloom extract --preset successor` on it and confirm each shows as
     `[STOP: ...]` / `[tool error: ...]` and none as an operator message;
  3. capture those records verbatim into a fixture, add the version here, and
     extend tests/test_session_extract_real_corpus.py.
"""

from __future__ import annotations

import json
from pathlib import Path

VERIFIED_HARNESS_VERSIONS = frozenset({"2.1.289"})


def transcript_versions(path: Path) -> list[str]:
    """Distinct top-level `version` values in file order (empty when none)."""
    seen: list[str] = []
    with Path(path).open("r", errors="ignore") as f:
        for line in f:
            if '"version"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            version = rec.get("version") if isinstance(rec, dict) else None
            if isinstance(version, str) and version and version not in seen:
                seen.append(version)
    return seen


def version_warning(path: Path) -> str | None:
    """The one-line header warning, or None when every recorded version is
    verified (or the transcript records none)."""
    unverified = [v for v in transcript_versions(path) if v not in VERIFIED_HARNESS_VERSIONS]
    if not unverified:
        return None
    return (
        f"[warning: harness v{', v'.join(unverified)} not verified for interrupt/denial "
        f"detection (verified: {', '.join(sorted(VERIFIED_HARNESS_VERSIONS))}); STOP/ERROR "
        f"classification may be incomplete]"
    )
