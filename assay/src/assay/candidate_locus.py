"""Pure sub-file identity helpers for native mutation candidates.

The locus key is a *lookup* input, never proof that an earlier verdict remains
valid.  A match only tells the caller which prior record to examine or replay.

A def segment starts at the tokenized ``@`` of its first decorator, including
any parenthesised or commented decorator lines, and ends at the AST's
``end_lineno``/``end_col_offset``.  A comment after the final AST statement is
therefore outside the segment and does not change the key; a comment before the
final statement is inside and does.
"""

from __future__ import annotations

import ast
import hashlib
import io
import tokenize
from bisect import bisect_right
from functools import lru_cache
from typing import Iterable, NamedTuple, cast

from .records import record

LOCUS_SCHEMA = "assay-locus/1"
WINDOW_SCHEMA = "assay-window/1"
WINDOW_LINES = 3

_HEX_DIGITS = frozenset("0123456789abcdef")
_LOCUS_KEYS = frozenset(
    {"schema", "key", "qualname", "segment_sha256", "rel_start", "rel_end"}
)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    )


def _valid_path_or_operator(value: object) -> bool:
    return isinstance(value, str) and bool(value) and "\0" not in value


def _valid_span(source: object, start_byte: object, end_byte: object) -> bool:
    return (
        isinstance(source, bytes)
        and isinstance(start_byte, int)
        and not isinstance(start_byte, bool)
        and isinstance(end_byte, int)
        and not isinstance(end_byte, bool)
        and 0 <= start_byte < end_byte <= len(source)
    )


@record
class Locus:
    """The stored A' identity of one mutation site."""

    key: str
    qualname: str
    segment_sha256: str
    rel_start: int
    rel_end: int

    def to_record(self) -> dict[str, object]:
        """Return the schema-shaped record for a state payload."""
        return {
            "schema": LOCUS_SCHEMA,
            "key": self.key,
            "qualname": self.qualname,
            "segment_sha256": self.segment_sha256,
            "rel_start": self.rel_start,
            "rel_end": self.rel_end,
        }


def from_record(obj: object) -> Locus | None:
    """Return a strictly validated locus record, or ``None`` for any defect."""
    if not isinstance(obj, dict) or frozenset(obj) != _LOCUS_KEYS:
        return None
    schema = obj["schema"]
    key = obj["key"]
    qualname = obj["qualname"]
    segment_sha256 = obj["segment_sha256"]
    rel_start = obj["rel_start"]
    rel_end = obj["rel_end"]
    if (
        schema != LOCUS_SCHEMA
        or not _is_sha256(key)
        or not isinstance(qualname, str)
        or not qualname
        or "\0" in qualname
        or not _is_sha256(segment_sha256)
        or not isinstance(rel_start, int)
        or isinstance(rel_start, bool)
        or not isinstance(rel_end, int)
        or isinstance(rel_end, bool)
        or rel_start < 0
        or rel_end <= rel_start
    ):
        return None
    return Locus(
        key=key,
        qualname=qualname,
        segment_sha256=segment_sha256,
        rel_start=rel_start,
        rel_end=rel_end,
    )


class _Segment(NamedTuple):
    qualname: str
    start_byte: int
    end_byte: int
    segment_sha256: str
    ambiguous: bool


class _SourceIndex(NamedTuple):
    line_starts: tuple[int, ...]
    segments: tuple[_Segment, ...]


def _line_starts(source: bytes) -> tuple[int, ...]:
    starts = [0]
    starts.extend(index + 1 for index, byte in enumerate(source) if byte == 0x0A)
    return tuple(starts)


def _line_count(source: bytes, starts: tuple[int, ...]) -> int:
    if source.endswith(b"\n"):
        return len(starts) - 1
    return len(starts)


def _byte_offset(starts: tuple[int, ...], lineno: int, col_offset: int) -> int:
    return starts[lineno - 1] + col_offset


def _token_byte_offset(
    source: bytes,
    starts: tuple[int, ...],
    lineno: int,
    char_column: int,
) -> int:
    line_start = starts[lineno - 1]
    line = source[line_start:].partition(b"\n")[0].decode("utf-8")
    return line_start + len(line[:char_column].encode("utf-8"))


def _at_token_offsets(source: bytes, starts: tuple[int, ...]) -> tuple[int, ...]:
    tokens = tokenize.generate_tokens(io.StringIO(source.decode("utf-8")).readline)
    return tuple(
        _token_byte_offset(source, starts, *tok.start)
        for tok in tokens
        if tok.type == tokenize.OP and tok.string == "@"
    )


def _decorator_start(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    starts: tuple[int, ...],
    at_offsets: tuple[int, ...],
) -> int:
    if not node.decorator_list:
        return _byte_offset(starts, node.lineno, node.col_offset)
    first = min(node.decorator_list, key=lambda item: (item.lineno, item.col_offset))
    expression_start = _byte_offset(starts, first.lineno, first.col_offset)
    return at_offsets[bisect_right(at_offsets, expression_start) - 1]


def _collect_segments(tree: ast.Module, source: bytes) -> tuple[_Segment, ...]:
    starts = _line_starts(source)
    at_offsets = _at_token_offsets(source, starts)
    provisional: list[tuple[str, int, int, str]] = []

    def visit(node: ast.AST, classes: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = ".".join((*classes, node.name))
            start = _decorator_start(node, starts, at_offsets)
            end = _byte_offset(
                starts,
                cast(int, node.end_lineno),
                cast(int, node.end_col_offset),
            )
            provisional.append(
                (qualname, start, end, hashlib.sha256(source[start:end]).hexdigest())
            )
            return
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                visit(child, (*classes, node.name))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, classes)

    for child in tree.body:
        visit(child, ())

    counts: dict[tuple[str, str], int] = {}
    for qualname, _start, _end, digest in provisional:
        pair = (qualname, digest)
        counts[pair] = counts.get(pair, 0) + 1
    return tuple(
        _Segment(
            qualname=qualname,
            start_byte=start,
            end_byte=end,
            segment_sha256=digest,
            ambiguous=counts[(qualname, digest)] > 1,
        )
        for qualname, start, end, digest in provisional
    )


def _parse_source(source: bytes) -> _SourceIndex | None:
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    return _SourceIndex(
        line_starts=_line_starts(source),
        segments=_collect_segments(tree, source),
    )


@lru_cache(maxsize=8)
def _cached_source_index(
    _path: str,
    _source_sha256: str,
    source: bytes,
) -> _SourceIndex | None:
    return _parse_source(source)


def _source_index(path: str, source: bytes) -> _SourceIndex | None:
    return _cached_source_index(path, hashlib.sha256(source).hexdigest(), source)


def _containing_segment(
    index: _SourceIndex, start_byte: int, end_byte: int
) -> _Segment | None:
    containing = tuple(
        segment
        for segment in index.segments
        if segment.start_byte <= start_byte and end_byte <= segment.end_byte
    )
    if len(containing) != 1:
        return None
    return containing[0]


def _locus_key(
    *,
    path: str,
    qualname: str,
    segment_sha256: str,
    rel_start: int,
    rel_end: int,
    operator: str,
    replacement_sha256: str,
) -> str:
    fields = (
        LOCUS_SCHEMA,
        path,
        qualname,
        segment_sha256,
        str(rel_start),
        str(rel_end),
        operator,
        replacement_sha256,
    )
    return hashlib.sha256("\0".join(fields).encode("utf-8")).hexdigest()


def locus_for_site(
    path: str,
    source: bytes,
    start_byte: int,
    end_byte: int,
    operator: str,
    replacement_sha256: str,
) -> Locus | None:
    """Return A' for a site in the outermost enclosing function, else ``None``.

    ``None`` is the design's exact-id fallback: module or class-body code, a
    cross-segment span, unparseable or non-UTF-8 source, a malformed argument,
    and any duplicate ``(qualname, segment_sha256)`` pair in the file.
    """
    if (
        not _valid_path_or_operator(path)
        or not _valid_path_or_operator(operator)
        or not _is_sha256(replacement_sha256)
        or not _valid_span(source, start_byte, end_byte)
    ):
        return None
    index = _source_index(path, source)
    if index is None:
        return None
    segment = _containing_segment(index, start_byte, end_byte)
    if segment is None or segment.ambiguous:
        return None
    return Locus(
        key=_locus_key(
            path=path,
            qualname=segment.qualname,
            segment_sha256=segment.segment_sha256,
            rel_start=start_byte - segment.start_byte,
            rel_end=end_byte - segment.start_byte,
            operator=operator,
            replacement_sha256=replacement_sha256,
        ),
        qualname=segment.qualname,
        segment_sha256=segment.segment_sha256,
        rel_start=start_byte - segment.start_byte,
        rel_end=end_byte - segment.start_byte,
    )


def _window_key(
    *,
    path: str,
    window: bytes,
    rel_start: int,
    operator: str,
    replacement_sha256: str,
) -> str:
    fields = (
        WINDOW_SCHEMA.encode("utf-8"),
        path.encode("utf-8"),
        window,
        str(rel_start).encode("utf-8"),
        operator.encode("utf-8"),
        replacement_sha256.encode("utf-8"),
    )
    return hashlib.sha256(b"\0".join(fields)).hexdigest()


def window_key_for_site(
    path: str,
    source: bytes,
    start_byte: int,
    end_byte: int,
    operator: str,
    replacement_sha256: str,
    lines: int = WINDOW_LINES,
) -> str | None:
    """Return B, the physical-line window key used only to LOOK UP prior kills.

    B is never proof that a kill is still valid: the verifier and replay decide.
    Its fallback set is the parse/non-UTF-8/malformed-input set in the design;
    unlike A', a parseable site with no unique enclosing def is still windowed to
    the file.
    """
    if (
        not _valid_path_or_operator(path)
        or not _valid_path_or_operator(operator)
        or not _is_sha256(replacement_sha256)
        or not _valid_span(source, start_byte, end_byte)
        or not isinstance(lines, int)
        or isinstance(lines, bool)
        or lines < 0
    ):
        return None
    index = _source_index(path, source)
    if index is None:
        return None
    lineno = bisect_right(index.line_starts, start_byte)
    first_line = max(1, lineno - lines)
    last_line = min(_line_count(source, index.line_starts), lineno + lines)
    window_start = index.line_starts[first_line - 1]
    if last_line < len(index.line_starts):
        window_end = index.line_starts[last_line]
    else:
        window_end = len(source)
    segment = _containing_segment(index, start_byte, end_byte)
    if segment is not None:
        window_start = max(window_start, segment.start_byte)
        window_end = min(window_end, segment.end_byte)
    return _window_key(
        path=path,
        window=source[window_start:window_end],
        rel_start=start_byte - window_start,
        operator=operator,
        replacement_sha256=replacement_sha256,
    )


def _entry_rank(
    entry: tuple[str, str | None, str | None, object],
) -> tuple[bool, str, bool, str, str, str]:
    _candidate_id, locus_key, window_key, payload = entry
    payload_type = type(payload)
    return (
        locus_key is None,
        locus_key or "",
        window_key is None,
        window_key or "",
        f"{payload_type.__module__}.{payload_type.__qualname__}",
        repr(payload),
    )


class LocusIndex:
    """Deduplicated candidate-id index over prior locus and window keys."""

    def __init__(
        self,
        entries: Iterable[tuple[str, str | None, str | None, object]],
    ) -> None:
        chosen: dict[str, tuple[str, str | None, str | None, object]] = {}
        for entry in entries:
            candidate_id, locus_key, window_key, _payload = entry
            if (
                not _valid_path_or_operator(candidate_id)
                or (locus_key is not None and not _is_sha256(locus_key))
                or (window_key is not None and not _is_sha256(window_key))
            ):
                raise ValueError("locus index entry has an invalid identity key")
            previous = chosen.get(candidate_id)
            if previous is None or _entry_rank(entry) < _entry_rank(previous):
                chosen[candidate_id] = entry

        by_locus_ids: dict[str, list[str]] = {}
        by_window_ids: dict[str, list[str]] = {}
        for candidate_id, locus_key, window_key, _payload in chosen.values():
            if locus_key is not None:
                by_locus_ids.setdefault(locus_key, []).append(candidate_id)
            if window_key is not None:
                by_window_ids.setdefault(window_key, []).append(candidate_id)

        payloads = {
            candidate_id: payload
            for candidate_id, _locus_key, _window_key, payload in chosen.values()
        }
        self._by_locus: dict[str, object] = {}
        self._ambiguous_loci: frozenset[str] = frozenset(
            key for key, candidate_ids in by_locus_ids.items() if len(candidate_ids) > 1
        )
        for key, candidate_ids in by_locus_ids.items():
            if len(candidate_ids) == 1:
                self._by_locus[key] = payloads[candidate_ids[0]]
        self._by_window: dict[str, tuple[object, ...]] = {
            key: tuple(payloads[candidate_id] for candidate_id in sorted(candidate_ids))
            for key, candidate_ids in by_window_ids.items()
        }

    def by_locus(self, key: str) -> object | None:
        """Return the sole payload for *key*; ambiguous or unknown keys are ``None``."""
        if not _is_sha256(key):
            return None
        return self._by_locus.get(key)

    def by_window(self, key: str) -> tuple[object, ...]:
        """Return payloads for *key* in candidate-id order."""
        if not _is_sha256(key):
            return ()
        return self._by_window.get(key, ())

    def ambiguous_loci(self) -> frozenset[str]:
        """Return every locus key held by more than one candidate id."""
        return self._ambiguous_loci
