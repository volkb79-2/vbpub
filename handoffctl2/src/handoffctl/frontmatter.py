"""Handoff file parsing: YAML frontmatter + Markdown body. PACKAGE P01.

INTERFACE CONTRACT (frozen — implement exactly; do not change signatures):

- The handoff file format: first line exactly '---'; YAML mapping until the
  next line that is exactly '---'; everything after is the Markdown body.
- Schema: src/handoffctl/schemas/handoff-frontmatter.schema.json, loaded via
  importlib.resources from package 'handoffctl.schemas'.
- Errors carry file path and (where known) 1-based line numbers.
"""

from __future__ import annotations

from pathlib import Path

from .config import ProjectConfig
from .types import Frontmatter


class HandoffParseError(Exception):
    """Raised on malformed frontmatter or schema violation.

    Attributes: path (str), errors (list[str]), line (int|None).
    """

    def __init__(self, path: str, errors: list[str], line: int | None = None):
        self.path, self.errors, self.line = path, errors, line
        super().__init__(f"{path}: " + "; ".join(errors))


def split_frontmatter(text: str) -> tuple[dict, str, int]:
    """Return (frontmatter_mapping, body, body_start_line).

    body_start_line is the 1-based line number of the first body line.
    Raises HandoffParseError(path='<text>') on: missing leading '---',
    unterminated frontmatter, YAML that is not a mapping, YAML parse errors
    (include the yaml error message).
    """
    raise NotImplementedError


def schema_errors(data: dict) -> list[str]:
    """Validate against the packaged JSON schema; return human-readable
    error strings ('<json-path>: <message>'), empty when valid. Uses
    jsonschema.Draft202012Validator; errors sorted by path for determinism."""
    raise NotImplementedError


def parse_handoff(path: Path) -> tuple[Frontmatter, str]:
    """Read, split, schema-validate, construct types.Frontmatter.

    Raises HandoffParseError with ALL schema errors at once (not first-only).
    """
    raise NotImplementedError


def discover_handoffs(cfg: ProjectConfig) -> list[Path]:
    """All files matching cfg.handoff_globs relative to cfg.root, sorted.
    Files under cfg.reports_dir are excluded."""
    raise NotImplementedError


def convert_legacy_header(text: str) -> str:
    """Best-effort conversion of a v2 §7 blockquote header to frontmatter.

    Input: a handoff whose header lines look like
        > **Tier:** flash-high
        > **Depends-on:** app-P03 (merged), D-012 | none
        > **Base:** main after P53 merge
        > **Stack:** none|readonly|exclusive
        > **Session-hint:** fresh | resume <area> session
        > **Serialize-with:** P02 (shared files: ...)
        > **Escalate-if:** trigger a; trigger b
    Output: the same document with a generated frontmatter block prepended
    (fields it cannot infer get placeholders: input_revision '0000000',
    oracles [] -> a single TODO oracle, gates ['TODO'], scope.touch ['TODO'])
    and the original blockquote header left in place in the body.
    'none' values map to absent/empty. Depends-on '(merged)' suffixes are
    stripped. Serialize-with maps to mutexes: ['serialize-<peer-id>'].
    This is an evolution aid; its output intentionally FAILS lint until a
    human fills the TODOs — it must parse, not pass.
    """
    raise NotImplementedError
