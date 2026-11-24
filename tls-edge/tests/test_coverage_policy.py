"""Lint D10.2-3 coverage exclusions against their classes, reasons, and budget."""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest
from coverage.exceptions import NotPython
from coverage.parser import PythonParser

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "tls_edge_cli"
MAX_EXCLUDED_LINES = 12
EXCLUDE_PATTERN = r"#\s*pragma:\s*no\s*cover"
VALID_PRAGMA = re.compile(
    r"# pragma: no cover -- (os-error|main-guard|tty-only): \S(?:.*\S)?$"
)


def test_coverage_exclusions_follow_the_d10_policy() -> None:
    violations: list[str] = []
    excluded: list[tuple[Path, int]] = []

    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT and "pragma: no cover" in token.string:
                if VALID_PRAGMA.fullmatch(token.string) is None:
                    violations.append(
                        f"{relative}:{token.start[0]}: invalid coverage exclusion "
                        f"{token.string!r}; expected '# pragma: no cover -- "
                        "<os-error|main-guard|tty-only>: <reason>'"
                    )

        try:
            parser = PythonParser(
                text=text,
                filename=str(path),
                exclude=EXCLUDE_PATTERN,
            )
            parser.parse_source()
        except NotPython as exc:
            violations.append(f"{relative}: coverage.py could not parse source: {exc}")
            continue
        excluded.extend((relative, line) for line in sorted(parser.excluded))

    if len(excluded) > MAX_EXCLUDED_LINES:
        relative, line = excluded[MAX_EXCLUDED_LINES]
        violations.append(
            f"{relative}:{line}: {len(excluded)} coverage-excluded lines exceed "
            f"D10's budget of {MAX_EXCLUDED_LINES}"
        )

    assert not violations, "\n".join(violations)
