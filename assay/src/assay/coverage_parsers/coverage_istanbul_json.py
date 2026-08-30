"""Istanbul's ``coverage-final.json`` — the JavaScript/TypeScript ecosystem's
own coverage-object format (B036, A-341/A-342).

The format is `istanbul-lib-coverage`'s serialized coverage map: a top-level
JSON object keyed by **absolute filesystem path**, one record per measured
file::

    {
      "/abs/path/src/roles.ts": {
        "path": "/abs/path/src/roles.ts",
        "statementMap": {"0": {"start": {"line": 7, "column": 41},
                               "end":   {"line": 11, "column": 1}}},
        "fnMap": {...}, "branchMap": {...},
        "s": {"0": 1}, "f": {...}, "b": {...}
      }
    }

It is emitted natively by nyc/istanbul and by Jest (``--coverageReporters=json``),
and by Vitest through either of its two coverage providers
(``@vitest/coverage-v8`` and ``@vitest/coverage-istanbul``) configured with
``coverage.reporter = ['json']`` — a real ecosystem standard with several
independent producers, which is exactly why the registry is keyed by FORMAT
rather than by language (DESIGN-GUIDE §11: "TypeScript alone emits three
formats").

**Only ``statementMap``/``s`` are read.** ``fnMap``/``f`` are function-entry
counts, not line classification; ``branchMap``/``b`` are addressed below.
Neither is needed to answer this registry's one question ("which physical
lines did this format measure, and did they run"), and every field this parser
does not need is ignored rather than rejected — the same rule
:mod:`~assay.coverage_parsers.lcov` applies to legal-but-unread record types.
That includes each record's own ``path`` field (a duplicate of its key in
every artifact witnessed here) and the ``all`` flag ``@vitest/coverage-v8``
adds for a file it included but never loaded.

**Line classification: statement EXTENTS, innermost wins (A-341).** Each
``statementMap`` entry carries the statement's own ``start.line`` and
``end.line``, and ``s[id]`` its execution count. Every line in
``[start.line, end.line]`` is classified from that statement's own count, with
two resolution rules:

* **innermost wins.** Statement extents nest (a ``for`` loop's extent contains
  the ``if`` inside it, which contains its own ``return``), and the SMALLEST
  extent containing a line decides that line's status — the identical
  containment resolution :func:`assay.evaluate._attribute_line` already
  performs for Python's span attribution. This is load-bearing, not a
  refinement: ``tests/fixtures/coverage/probe-js/src/branchy.ts`` measured by
  ``@vitest/coverage-istanbul`` reports statement ``[2, 4]`` (the whole ``if``)
  with count 1 and statement ``[3, 3]`` (its own never-taken ``return``) with
  count 0. A go-cover-style "executed wins" merge would call line 3 covered —
  a false green on a line that provably never ran.
* **widest ties resolve by MAX count.** Two statements sharing one extent
  (``if (x) return y`` written on one line) resolve to the larger count,
  adopted from `istanbul-lib-coverage`'s own ``FileCoverage.getLineCoverage``,
  which reduces statements to lines by taking the maximum count of every
  statement starting on that line.

Going BEYOND ``getLineCoverage`` — which keys only each statement's own START
line and so leaves a multi-line statement's continuation lines unclassified —
is deliberate, and is what makes this format usable for CHANGED-line coverage
at all. A continuation line is a line a human actually edited; leaving it in
neither ``executed`` nor ``missing`` would silently drop it from both the
numerator and the denominator (``evaluate.py``'s rule 4), which is srdm's
silent-excuse direction. Python needs a real AST walk to recover the same
lines (:meth:`assay.adapters.python.PythonAdapter.statement_spans`, P07/A-100)
because ``coverage.py``'s artifact does not carry statement extents; this one
does, so the recovery is a fact of the artifact rather than a re-parse — and
:class:`~assay.adapters.javascript.JavaScriptAdapter` needs no JS/TS parser of
its own to declare ``requires_span_attribution = False`` honestly (A-342).

The known over-approximation, stated rather than hidden: a line that is only
structurally part of a statement (a closing ``}`` on its own line, a bare
continuation) is classified with that statement's status, so it counts toward
the denominator. That is the visible-false-failure direction, chosen over the
silent-excuse one (srdm's asymmetry, ``adapters/base.py``'s
``has_executable_code`` docstring). A line no statement extent covers at all
(a comment, a blank line, and — under ``@vitest/coverage-istanbul`` — a
function's own signature line) stays unclassified and falls to rule 4, exactly
as it does for every other format.

**``excluded`` is always ``None`` (A-008, A-343).** Neither real producer's
output carries a per-line exclusion field: measured directly, an
``/* istanbul ignore next */`` hint left no ``skip`` marker and no other trace
in a real ``@vitest/coverage-istanbul`` artifact. By the time this parser sees
the document an ignored line is indistinguishable from a line that was never
code — lcov's exact situation, and lcov's exact answer. Reporting
``frozenset()`` would claim "this file reports zero exclusions, verified",
which nothing verified.

**``branches`` is always ``None`` (A-344), and this is a MEASURED refusal, not
an omission.** ``branchMap`` exists in this format, but its two real producers
disagree about what it MEANS, measured on the same source file
(``probe-js/src/branchy.ts``, two ifs and one ternary):

* ``@vitest/coverage-istanbul`` emits real typed arcs — three entries typed
  ``if``/``if``/``cond-expr``, each with one location per arm and one count per
  arm (``[0,1]``, ``[1,0]``, ``[0,0]``): six arcs, two covered;
* ``@vitest/coverage-v8`` emits four entries all typed ``"branch"``, each with
  exactly ONE location and ONE count, describing v8's own executed/unexecuted
  RANGES (one of them spans the whole function; another starts at a closing
  brace) rather than the arms of a branch: four "arcs", one covered.

A single translation cannot be honest for both, and the producer is not
declared anywhere in a lane's config, so any :class:`~.model.BranchCoverage`
built here would put a number on the wire whose meaning depends on an
undeclared fact — the ``declared_unverified``-class lie A-O12 was corrected
for. ``None`` is this project's existing spelling for "this artifact carries
no branch detail this parser can honestly read" (lcov and Cobertura already
return it for an artifact produced without branch tracking). B038 tracks
adding real arc support once a producer can be declared.
"""

from __future__ import annotations

import json
from types import MappingProxyType

from ..errors import AssayError, Outcome, ReasonCode
from .model import CoverageProfile, FileCoverage

_SIGNATURE_KEY = '"statementMap"'

#: A fixed ceiling on how many (line, statement) classifications one artifact
#: may ask this parser to materialize — O4's "a fixed bound, never an ambient
#: or elapsed-time guess", one level below
#: :data:`assay.coverage.MAX_COVERAGE_ARTIFACT_BYTES`. Statement EXTENTS are
#: expanded line by line, so a single ~60-byte record declaring ``"end":
#: {"line": 999999999}`` would otherwise materialize a billion entries from an
#: artifact that is well inside the 16 MiB read bound. Two million is already
#: far past any real codebase measured in one artifact (the largest witnessed
#: here is four figures), so a document that exceeds it is refused as
#: unreadable rather than allowed to exhaust memory.
MAX_CLASSIFIED_LINES = 2_000_000


def sniff(text: str) -> bool:
    """A JSON object whose text contains a ``"statementMap"`` key —
    the literal signature of an istanbul coverage map (DESIGN-GUIDE §5's own
    per-format signature table).

    Deliberately a cheap substring/prefix check rather than a full
    ``json.loads``, exactly as
    :func:`assay.coverage_parsers.coverage_py_json.sniff` is: sniffing answers
    "does this look like the declared format", parsing answers "is it
    well-formed", and collapsing the two would surface a malformed document as
    ``FORMAT_MISMATCH`` from inside the sniffer instead of
    ``UNREADABLE_ARTIFACT`` from the real parser.

    ``"statementMap"`` cannot collide with
    :mod:`~assay.coverage_parsers.coverage_py_json`'s own ``"files"``
    signature: an istanbul document has no top-level ``files`` key, and a
    coverage.py document has no ``statementMap`` anywhere.
    """
    stripped = text.lstrip()
    return stripped.startswith("{") and _SIGNATURE_KEY in text


def parse(text: str) -> CoverageProfile:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _malformed(f"not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise _malformed(f"top level is {type(document).__name__}, expected object")

    files: dict[str, FileCoverage] = {}
    budget = _Budget(remaining=MAX_CLASSIFIED_LINES)
    for path, record in document.items():
        files[path] = _parse_record(path, record, budget)
    return CoverageProfile(files=MappingProxyType(files))


class _Budget:
    """The remaining share of :data:`MAX_CLASSIFIED_LINES` for one whole
    document, spent as statement extents are expanded. A plain mutable holder
    rather than a running total threaded through return values: the bound is a
    property of the ARTIFACT, not of any one record, so a document made of a
    million small records is refused by the same counter that refuses one
    record with a million-line extent.
    """

    __slots__ = ("remaining",)

    def __init__(self, *, remaining: int) -> None:
        self.remaining = remaining

    def spend(self, amount: int, path: str) -> None:
        self.remaining -= amount
        if self.remaining < 0:
            raise _malformed(
                f"record for {path!r} pushes this artifact past "
                f"{MAX_CLASSIFIED_LINES} classified statement lines; a "
                f"document declaring more line classifications than any real "
                f"codebase contains is refused rather than expanded"
            )


def _parse_record(path: str, record: object, budget: _Budget) -> FileCoverage:
    if not isinstance(record, dict):
        raise _malformed(
            f"record for {path!r} is {type(record).__name__}, expected object"
        )
    statement_map = record.get("statementMap")
    if not isinstance(statement_map, dict):
        raise _malformed(
            f"record for {path!r}: 'statementMap' is "
            f"{type(statement_map).__name__ if statement_map is not None else 'absent'}"
            f", expected object"
        )
    counts = record.get("s")
    if not isinstance(counts, dict):
        raise _malformed(
            f"record for {path!r}: 's' is "
            f"{type(counts).__name__ if counts is not None else 'absent'}, "
            f"expected object"
        )
    if set(statement_map) != set(counts):
        raise _malformed(
            f"record for {path!r}: 'statementMap' and 's' name different "
            f"statement ids ({sorted(set(statement_map) ^ set(counts))!r} "
            f"appear in only one of them) -- a statement with no count, or a "
            f"count with no statement, cannot be classified"
        )

    extents: list[tuple[int, int, int]] = []
    for statement_id, location in statement_map.items():
        start, end = _statement_lines(path, statement_id, location)
        count = _statement_count(path, statement_id, counts[statement_id])
        extents.append((start, end, count))
        budget.spend(end - start + 1, path)

    hits = _paint(extents)
    # Deliberately NOT wrapped in a try/except, unlike
    # `coverage_py_json._parse_record`: none of `FileCoverage`'s own
    # invariants can be violated by anything this parser accepts.
    # `_position_line` already refuses a non-positive line number, `_paint`
    # assigns each line exactly ONE count so `executed` and `missing` are
    # disjoint by construction, and `excluded`/`branches` are both `None`, so
    # every cross-bucket invariant is vacuous here. A guard around a call that
    # cannot raise would be a line no honest test could cover -- the same
    # reasoning `lcov._finish_record` states for its own `BranchCoverage`
    # construction, one field over.
    return FileCoverage(
        executed=frozenset(line for line, count in hits.items() if count > 0),
        missing=frozenset(line for line, count in hits.items() if count == 0),
        excluded=None,
        branches=None,
    )


def _paint(extents: list[tuple[int, int, int]]) -> dict[int, int]:
    """Every line any extent covers, mapped to the count that decides it
    (module docstring's two resolution rules).

    Widest extents are painted FIRST and narrower ones over the top, so the
    smallest extent containing a line is the last to write it — innermost
    wins. Among extents of equal width the lower count is painted first, so
    the maximum count survives a tie, matching
    `istanbul-lib-coverage`'s own ``getLineCoverage``.
    """
    hits: dict[int, int] = {}
    for start, end, count in sorted(
        extents, key=lambda extent: (extent[0] - extent[1], extent[2])
    ):
        for line in range(start, end + 1):
            hits[line] = count
    return hits


def _statement_lines(path: str, statement_id: str, location: object) -> tuple[int, int]:
    if not isinstance(location, dict):
        raise _malformed(
            f"record for {path!r}: statement {statement_id!r} is "
            f"{type(location).__name__}, expected object"
        )
    start = _position_line(path, statement_id, location, "start")
    end = _position_line(path, statement_id, location, "end")
    if end < start:
        raise _malformed(
            f"record for {path!r}: statement {statement_id!r} ends on line "
            f"{end}, before it starts on line {start}"
        )
    return start, end


def _position_line(path: str, statement_id: str, location: dict, side: str) -> int:
    """*location*'s ``start``/``end`` line number. Only ``line`` is read;
    ``column`` is deliberately never validated, because real
    ``@vitest/coverage-istanbul`` output writes ``"column": null`` on an end
    position and a parser that required an integer there would reject its
    genuine output."""
    position = location.get(side)
    if not isinstance(position, dict):
        raise _malformed(
            f"record for {path!r}: statement {statement_id!r} has no {side!r} "
            f"position object"
        )
    line = position.get("line")
    if isinstance(line, bool) or not isinstance(line, int):
        raise _malformed(
            f"record for {path!r}: statement {statement_id!r} has "
            f"{side}.line = {line!r}, expected an integer"
        )
    if line < 1:
        raise _malformed(
            f"record for {path!r}: statement {statement_id!r} has "
            f"{side}.line = {line}, which is not a positive line number"
        )
    return line


def _statement_count(path: str, statement_id: str, count: object) -> int:
    if isinstance(count, bool) or not isinstance(count, int):
        raise _malformed(
            f"record for {path!r}: s[{statement_id!r}] is "
            f"{type(count).__name__} ({count!r}), expected int"
        )
    if count < 0:
        raise _malformed(
            f"record for {path!r}: s[{statement_id!r}] is {count}, a negative "
            f"execution count"
        )
    return count


def _malformed(message: str) -> AssayError:
    return AssayError(
        f"istanbul coverage JSON: {message}",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )
