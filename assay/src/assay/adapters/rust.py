"""The pure-Python Rust R1 adapter.

Rust's lane runs the Cargo tools and writes LCOV; this adapter never invokes
the toolchain. It maps ``*.rs`` files, conservatively answers the missing-file
NoCode question, and normalizes stable ``cargo-llvm-cov`` branch-total markers
before the shared LCOV parser sees them. R2 ingestion and R3 canaries remain
unregistered and unavailable in this package.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Sequence

from ..coverage_parsers.rust_lcov import normalize_stable_rust_branch_markers
from ..mutation import MutationSite
from ..records import record
from .base import Remaining, StatementBlockReport, StatementSpan

__all__ = ["RustAdapter"]

_FN_TOKEN = re.compile(r"(?<![\w])fn(?![\w])")


def _is_identifier_continue(char: str) -> bool:
    return char == "_" or char.isalnum()


def _has_identifier_boundary(text: str, start: int) -> bool:
    return start == 0 or not _is_identifier_continue(text[start - 1])


def _blank(output: list[str], source: str, start: int, end: int) -> None:
    """Replace a non-code range with same-length whitespace, preserving lines."""
    for offset in range(start, end):
        if source[offset] != "\n":
            output[offset] = " "


def _raw_string_end(text: str, start: int) -> int | None | Literal[False]:
    """Return the end of a Rust raw string, ``None`` if unclosed, or ``False``."""
    if not _has_identifier_boundary(text, start):
        return False
    prefix = next(
        (candidate for candidate in ("br", "cr", "r") if text.startswith(candidate, start)),
        None,
    )
    if prefix is None:
        return False
    cursor = start + len(prefix)
    while cursor < len(text) and text[cursor] == "#":
        cursor += 1
    if cursor >= len(text) or text[cursor] != '"':
        return False
    hashes = "#" * (cursor - start - len(prefix))
    closing = '"' + hashes
    end = text.find(closing, cursor + 1)
    return None if end < 0 else end + len(closing)


def _quoted_string_end(text: str, quote: int) -> int | None:
    cursor = quote + 1
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            if cursor + 1 >= len(text):
                return None
            cursor += 2
        elif char == '"':
            return cursor + 1
        else:
            cursor += 1
    return None


def _char_literal_end(text: str, quote: int) -> int | None:
    """Recognize one Rust char/byte-char scalar, including Rust escapes."""
    cursor = quote + 1
    if cursor >= len(text) or text[cursor] in "\r\n'":
        return None
    if text[cursor] == "\\":
        cursor += 1
        if cursor >= len(text):
            return None
        escape = text[cursor]
        if escape == "x":
            cursor += 1
            if cursor + 2 > len(text) or not all(ch in "0123456789abcdefABCDEF" for ch in text[cursor : cursor + 2]):
                return None
            cursor += 2
        elif escape == "u":
            cursor += 1
            if cursor >= len(text) or text[cursor] != "{":
                return None
            end = text.find("}", cursor + 1)
            if end < 0 or not text[cursor + 1 : end].replace("_", ""):
                return None
            cursor = end + 1
        elif escape in "nrt0\\'\"":
            cursor += 1
        else:
            return None
    else:
        cursor += 1
    return cursor + 1 if cursor < len(text) and text[cursor] == "'" else None


def _lifetime_end(text: str, quote: int) -> int | None:
    """Return the end of a lifetime token; it is code punctuation, not a char."""
    cursor = quote + 1
    if cursor >= len(text) or not (text[cursor] == "_" or text[cursor].isalpha()):
        return None
    cursor += 1
    while cursor < len(text) and _is_identifier_continue(text[cursor]):
        cursor += 1
    return cursor


def _mask_rust_non_code(text: str) -> str | None:
    """Mask comments and literal contents, or return ``None`` on uncertainty."""
    output = list(text)
    cursor = 0
    while cursor < len(text):
        if text.startswith("//", cursor):
            end = text.find("\n", cursor + 2)
            end = len(text) if end < 0 else end
            _blank(output, text, cursor, end)
            cursor = end
            continue

        if text.startswith("/*", cursor):
            start = cursor
            cursor += 2
            depth = 1
            while cursor < len(text) and depth:
                if text.startswith("/*", cursor):
                    depth += 1
                    cursor += 2
                elif text.startswith("*/", cursor):
                    depth -= 1
                    cursor += 2
                else:
                    cursor += 1
            if depth:
                return None
            _blank(output, text, start, cursor)
            continue

        raw_end = _raw_string_end(text, cursor)
        if raw_end is None:
            return None
        if raw_end is not False:
            _blank(output, text, cursor, raw_end)
            cursor = raw_end
            continue

        char_prefix = 0
        if text[cursor] == "b" and _has_identifier_boundary(text, cursor):
            if cursor + 1 < len(text) and text[cursor + 1] in "\"'":
                char_prefix = 1
        elif text[cursor] == "c" and _has_identifier_boundary(text, cursor):
            if cursor + 1 < len(text) and text[cursor + 1] == '"':
                char_prefix = 1
        quote_cursor = cursor + char_prefix
        if text[quote_cursor] == '"':
            end = _quoted_string_end(text, quote_cursor)
            if end is None:
                return None
            _blank(output, text, cursor, end)
            cursor = end
            continue
        if text[quote_cursor] == "'":
            end = _char_literal_end(text, quote_cursor)
            if end is not None:
                _blank(output, text, cursor, end)
                cursor = end
                continue
            if char_prefix:
                return None
            lifetime_end = _lifetime_end(text, cursor)
            if lifetime_end is None:
                return None
            cursor = lifetime_end
            continue
        cursor += 1
    return "".join(output)


def _function_body_state(text: str, start: int) -> bool | None:
    """Find a function body before its top-level semicolon.

    ``True`` means a body, ``False`` a declaration, and ``None`` an
    incomplete/ambiguous signature. The caller treats ambiguity as code.
    """
    cursor = start + 2
    parens = brackets = angles = 0
    while cursor < len(text):
        char = text[cursor]
        if char == "(":
            parens += 1
        elif char == ")":
            parens -= 1
            if parens < 0:
                return None
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets -= 1
            if brackets < 0:
                return None
        elif char == "<":
            angles += 1
        elif char == ">" and angles:
            angles -= 1
        elif parens == 0 and brackets == 0 and angles == 0:
            if char == "{":
                return True
            if char == ";":
                return False
            if char == "}":
                return None
        cursor += 1
    return None


def _has_function_body(text: str) -> bool:
    masked = _mask_rust_non_code(text)
    if masked is None:
        return True
    for match in _FN_TOKEN.finditer(masked):
        start = match.start()
        if start >= 2 and masked[start - 2 : start] == "r#" and _has_identifier_boundary(masked, start - 2):
            continue
        if _function_body_state(masked, start) is not False:
            return True
    return False


@record
class RustAdapter:
    """Rust source mapping and missing-LCOV classification for R1."""

    name: str = "rust"
    source_globs: tuple[str, ...] = ("*.rs",)
    excluded_dir_names: frozenset[str] = frozenset({"vendor", "target"})
    requires_span_attribution: bool = False
    requires_statement_attribution: bool = False
    external_tools: tuple[str, ...] = ()

    def for_project(self, *, repo_top: Path, project_root: Path) -> "RustAdapter":
        del repo_top, project_root
        return self

    def is_test_path(self, rel_path: str) -> bool:
        parts = tuple(part for part in rel_path.replace("\\", "/").split("/") if part)
        basename = parts[-1] if parts else ""
        return (
            any(part in {"tests", "examples", "benches"} for part in parts)
            or basename == "tests.rs"
            or basename.endswith(("_tests.rs", "-tests.rs"))
        )

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        del rel_path
        return _has_function_body(text)

    def normalize_coverage_key(self, key: str) -> str:
        return key

    def normalize_coverage_text(self, text: str) -> str:
        return normalize_stable_rust_branch_markers(text)

    def statement_spans(self, text: str) -> tuple[StatementSpan, ...] | None:
        del text
        return None

    def statement_blocks(
        self,
        repo_top: Path,
        rel_paths: Sequence[str],
        *,
        remaining: Remaining | None = None,
        sensitive_values: Sequence[str] = (),
    ) -> StatementBlockReport | None:
        del repo_top, rel_paths, remaining, sensitive_values
        return None

    def inject_import_break(self, text: str, *, nonce: str) -> tuple[str, str]:
        del text, nonce
        raise NotImplementedError("Rust R3 test-break injection is reserved for R-4")

    def inject_uncovered_line(self, text: str) -> tuple[str, str]:
        del text
        raise NotImplementedError("Rust R3 uncovered-line injection is reserved for R-4")

    def generate_mutation_sites(
        self,
        text: str,
        lines: set[int],
        *,
        operators: tuple[str, ...],
        limit: int,
    ) -> tuple[MutationSite, ...] | Literal["UNSUPPORTED"]:
        del text, lines, operators, limit
        return "UNSUPPORTED"
