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

.. warning::

   **One of those producers is unsound for judging, and this parser cannot
   tell (A-346, B040).** ``@vitest/coverage-v8`` reports provably-never-
   executed lines as EXECUTED whenever a conditional (ternary) expression
   appears earlier in the same block — measured on Vitest **3.2.4 and
   4.1.11 alike**, one-line and multi-line ternaries alike, and NOT fixed by
   ``coverage.experimentalAstAwareRemapping``. The committed
   ``probe-js-provider-defect`` fixtures carry the witness under both
   versions; ``@vitest/coverage-istanbul`` is correct on every one of them.

   This parser reports faithfully what the artifact says, so a v8-provider
   false green becomes an assay PASS on a line that never ran. Nothing in
   the document distinguishes a true ``s`` count of 1 from a false one —
   there is no inconsistency to detect — so this cannot be guarded here, and
   guessing the producer from the artifact's shape is precisely the
   declaration-versus-sniffing collapse A-007 forbids. The remedy is a
   PRODUCT one, recorded in A-346: assay's documentation names
   ``@vitest/coverage-istanbul`` as the only Vitest provider safe for a
   judged lane. Nothing here changes for nyc/istanbul or Jest's DEFAULT
   ``babel`` coverage provider, which share ``@vitest/coverage-istanbul``'s
   own instrumenter -- but this is scoped, not blanket: Jest's
   ``coverageProvider: "v8"`` remains genuinely unmeasured, and a THIRD,
   independently-buggy producer of this same format, ``c8``'s own
   ``v8-to-istanbul`` remapping, was measured (B042) and shares the defect
   CLASS (not a byte-identical false-positive set) -- see
   ``docs/CONSUMERS.md``'s "The v8 provider is not safe to gate on" section.
   Every producer of this format reaches this parser identically; only the
   documentation says which ones to trust.

**``statementMap``/``s`` classify lines; ``branchMap``/``b`` are read only
under an arc-bearing producer (below); ``fnMap``/``f`` are never read** —
they are function-entry counts, not line classification.
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

**``branches`` depends on the declared PRODUCER (B045/B038(a), schema v9).**
``branchMap`` exists in this format, but its real producers disagree about
what it MEANS, measured on the same source file (``probe-js/src/branchy.ts``,
two ifs and one ternary):

* ``@vitest/coverage-istanbul`` emits real typed arcs — three entries typed
  ``if``/``if``/``cond-expr``, each with one location per arm and one count per
  arm (``[0,1]``, ``[1,0]``, ``[0,0]``): six arcs, two covered;
* ``@vitest/coverage-v8`` emits four entries all typed ``"branch"``, each with
  exactly ONE location and ONE count, describing v8's own executed/unexecuted
  RANGES (one of them spans the whole function; another starts at a closing
  brace) rather than the arms of a branch: four "arcs", one covered.

A single translation cannot be honest for both. Through v8 the producer was
not declared anywhere in a lane's config, so ``None`` — "this artifact carries
no branch detail this parser can honestly read" — was the only truthful
answer, and A-344 recorded why. **B045 removes that constraint**: a lane
declares ``judge.coverage.producer``, this parser receives it, and arcs are
read for the producers :data:`assay.vocabulary.ARC_BEARING_COVERAGE_PRODUCERS`
names — today exactly ``istanbul``, the babel-plugin-istanbul instrumenter
family. Every OTHER producer, and an undeclared one, still gets ``None`` and
therefore ``branch_capability = "unavailable"``. The two facts are now
separable, which is the whole point of the declaration: "this format cannot
say" and "this producer's ``branchMap`` means something else" were previously
one answer.

**The arc reduction, and where it departs from its source.**
``istanbul-lib-coverage``'s own ``FileCoverage.getBranchCoverageByLine``
attributes an ENTIRE ``branchMap`` entry to one line — ``map.line ||
map.loc.start.line`` — and pushes every arm's count onto it. This parser keys
each arm by the arm's OWN ``locations[i].start.line`` where the artifact
gives one, and falls back to the entry's line where it does not. That is
strictly more detail than the upstream reduction (A-265: detail over
metadata) — a ``switch``'s cases and a ``binary-expr``'s operands land on the
lines a reader would point at — and the fallback is MEASURED, not defensive:
real ``@vitest/coverage-istanbul`` output writes an implicit ``else`` arm as
``{"start": {}, "end": {}}``, a location object carrying no line at all
(seven such arms across the committed artifacts). Keying strictly per-arm
would have to drop those, and an untaken implicit ``else`` is precisely the
arc a consumer most needs to see; dropping it would shrink the denominator
and report a GREENER branch percentage than the artifact supports.

Arcs aggregate per line as ``(covered, total)``: one arm contributes one to
``total`` and one to ``covered`` iff its count is nonzero. An entry typed
anything outside :data:`_ARM_STRUCTURED_BRANCH_TYPES` REFUSES the artifact
rather than being skipped — see that constant's own note.

**``excluded`` is still always ``None``** (A-008/A-343, above): the producer
declaration changes what ``branchMap`` can be read as, and nothing about
exclusions, because no producer of this format writes a per-line exclusion
field at all.
"""

from __future__ import annotations

import json
from types import MappingProxyType

from ..errors import AssayError, Outcome, ReasonCode
from ..vocabulary import ARC_BEARING_COVERAGE_PRODUCERS
from .model import BranchCoverage, ClassifiedLineBudget, CoverageProfile, FileCoverage
from .model import MAX_CLASSIFIED_LINES as MAX_CLASSIFIED_LINES

_SIGNATURE_KEY = '"statementMap"'

#: The ``branchMap`` entry types the babel-plugin-istanbul instrumenter emits
#: for a real, arm-structured branch, transcribed from the instrumenter's own
#: ``visitor.js`` rather than invented (A-112's transcribe-don't-invent rule
#: applied to a foreign vocabulary). Every one of them carries one location
#: and one count PER ARM, which is what makes the arc reduction below
#: meaningful.
#:
#: Any other type is REFUSED rather than skipped, and that is the guard that
#: catches a v8-shaped document declared as ``istanbul``: both
#: ``@vitest/coverage-v8`` and ``c8`` emit every entry typed ``"branch"``
#: with exactly one location, describing v8's executed RANGES (A-344).
#: Skipping unknown types would silently shrink the denominator -- a smaller,
#: greener branch percentage over an artifact assay could not read -- so the
#: fail-closed direction is to refuse the artifact.
_ARM_STRUCTURED_BRANCH_TYPES: frozenset[str] = frozenset(
    {"if", "cond-expr", "binary-expr", "switch", "default-arg"}
)


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


def parse(text: str, *, producer: str | None) -> CoverageProfile:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _malformed(f"not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise _malformed(f"top level is {type(document).__name__}, expected object")

    # ARTIFACT-level, decided once before any record is built (§3.2's own
    # "capability derivation is ARTIFACT-level"): every record this call
    # emits carries a real `BranchCoverage` or every record carries `None`,
    # never a mix -- `coverage.derive_branch_capability` refuses a mixed
    # profile as a parser defect, and it would be right to.
    read_arcs = producer in ARC_BEARING_COVERAGE_PRODUCERS

    files: dict[str, FileCoverage] = {}
    budget = ClassifiedLineBudget(
        format_name="istanbul coverage JSON", remaining=MAX_CLASSIFIED_LINES
    )
    for path, record in document.items():
        files[path] = _parse_record(path, record, budget, read_arcs=read_arcs)
    return CoverageProfile(files=MappingProxyType(files))


def _parse_record(
    path: str, record: object, budget: ClassifiedLineBudget, *, read_arcs: bool
) -> FileCoverage:
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
    executed = frozenset(line for line, count in hits.items() if count > 0)
    missing = frozenset(line for line, count in hits.items() if count == 0)
    branches = _branch_arcs(path, record) if read_arcs else None

    # The `branches is None` path can violate none of `FileCoverage`'s
    # invariants: `_position_line` already refuses a non-positive line
    # number, `_paint` assigns each line exactly ONE count so `executed` and
    # `missing` are disjoint by construction, and `excluded` is `None`, so
    # every cross-bucket invariant is vacuous -- which is why this
    # construction carried no guard before B045.
    #
    # Real ARCS change that, and the wrap is now load-bearing rather than
    # defensive. `branchMap` is an INDEPENDENT array from `statementMap`,
    # read straight from external input, exactly the situation
    # `coverage_py_json` states for its own three independent arrays: an
    # artifact can name a branch on a line no statement extent covers
    # (invariant 3), or claim a covered arc on a line whose statement count
    # is 0 (invariant 5, the anti-tamper one). Both are real properties of a
    # document, not parser defects, so they must reach a consumer as this
    # format's own ERROR/UNREADABLE_ARTIFACT rather than as a bare
    # `ValueError` from a dataclass.
    #
    # (B054/A-410) The two invariants a REAL producer trips are not parser
    # defects and are no longer the whole artifact's problem: they are a
    # defect of THIS FILE'S record, isolated here. `@vitest/coverage-istanbul`
    # statically instruments a file its `coverage.include` glob matches even
    # when no test imports it, and for an ordinary braceless single-statement
    # `if` it can emit a `branchMap` arc on a line that appears in NEITHER
    # bucket. Refusing the artifact for that took down every OTHER file's
    # correct data -- the exact opposite of what `changed_lines` mode
    # promises a consumer adopting coverage incrementally. The offending arcs
    # are DROPPED (they cannot be kept: `FileCoverage` refuses construction)
    # and their lines RECORDED, so nothing is silent: `evaluate` refuses by
    # name if this file is judged, and `runner` names it on the diagnostics
    # stream either way.
    #
    # Every OTHER `ValueError` this construction can raise still refuses the
    # artifact through the `except` below -- the isolation is per-invariant,
    # not a blanket "ignore what the dataclass says".
    contradictory = _contradictory_branch_lines(executed, missing, branches)
    if contradictory:
        branches = _without_lines(branches, contradictory)
    try:
        return FileCoverage(
            executed=executed,
            missing=missing,
            excluded=None,
            branches=branches,
            contradictory_branch_lines=contradictory or None,
        )
    except ValueError as exc:
        raise _malformed(
            f"record for {path!r}: its 'branchMap' arcs contradict its own "
            f"'statementMap'/'s' line classification -- {exc}"
        ) from exc


def _contradictory_branch_lines(
    executed: frozenset[int],
    missing: frozenset[int],
    branches: "BranchCoverage | None",
) -> frozenset[int]:
    """The branch source lines that contradict this record's own
    ``statementMap``/``s`` classification (B054/A-410).

    Exactly the two :meth:`FileCoverage.__post_init__` invariants that an
    honest istanbul producer can trip on a real file, computed here so the
    parser can name them rather than reading them back out of a
    ``ValueError``'s message:

    * a branch line in NEITHER ``executed`` nor ``missing`` -- no statement
      extent covers it, so the record classifies as code a line it does not
      classify as code (B054's witness: a braceless single-statement ``if``
      in a file no test imports);
    * a branch line in ``missing`` carrying a NONZERO covered-arc count -- a
      line that never ran cannot have taken an arc.

    Deliberately NOT the ``excluded`` invariant: this format never populates
    ``excluded`` (it is always ``None`` here), so that invariant is vacuous
    and including it would be a check that cannot fire.
    """
    if branches is None:
        return frozenset()
    branch_lines = frozenset(branches.by_line)
    unconsidered = branch_lines - (executed | missing)
    tampered = frozenset(
        line
        for line in branch_lines & missing
        if branches.by_line[line][0] != 0
    )
    return unconsidered | tampered


def _without_lines(
    branches: "BranchCoverage | None", drop: frozenset[int]
) -> "BranchCoverage | None":
    """*branches* with *drop*'s lines removed.

    Never ``None`` when *branches* was not ``None``, even if every line is
    dropped: ``None`` means "this FORMAT cannot express arcs at all" (A-008),
    which is a different, false statement about an istanbul artifact. An
    empty :class:`BranchCoverage` says "expressed, and none survived", which
    is the truth.
    """
    if branches is None:
        return None
    return BranchCoverage(
        by_line=MappingProxyType(
            {
                line: counts
                for line, counts in branches.by_line.items()
                if line not in drop
            }
        )
    )


def _branch_arcs(path: str, record: dict) -> BranchCoverage:
    """*record*'s ``branchMap``/``b`` reduced to per-line ``(covered, total)``
    arc counts (module docstring's arc rules).

    Called only for an arc-bearing producer. Every entry is validated BEFORE
    a single arc is aggregated, because aggregation is lossy: once arm counts
    have been summed onto a line, an entry whose ``locations`` and counts had
    different lengths is no longer visible as the defect it is.
    """
    branch_map = record.get("branchMap")
    counts = record.get("b")
    # Under an arc-bearing producer both keys are REQUIRED, and a file with
    # no branches is `{}`/`{}` rather than absent -- which is what every
    # committed real `@vitest/coverage-istanbul` and `vite-plugin-istanbul`
    # artifact writes. Absence here is not "this file has no branches"; it is
    # an artifact that is not the document the declared producer writes.
    if not isinstance(branch_map, dict):
        raise _malformed(
            f"record for {path!r}: 'branchMap' is "
            f"{type(branch_map).__name__ if branch_map is not None else 'absent'}"
            f", expected object -- an arc-bearing producer was declared, and "
            f"this producer's own output always carries it (empty for a file "
            f"with no branches)"
        )
    if not isinstance(counts, dict):
        raise _malformed(
            f"record for {path!r}: 'b' is "
            f"{type(counts).__name__ if counts is not None else 'absent'}, "
            f"expected object"
        )
    if set(branch_map) != set(counts):
        raise _malformed(
            f"record for {path!r}: 'branchMap' and 'b' name different branch "
            f"ids ({sorted(set(branch_map) ^ set(counts))!r} appear in only "
            f"one of them) -- a branch with no counts, or counts with no "
            f"branch, cannot be reduced to arcs"
        )

    arcs: dict[int, tuple[int, int]] = {}
    for branch_id, entry in branch_map.items():
        for line, taken in _entry_arcs(path, branch_id, entry, counts[branch_id]):
            covered, total = arcs.get(line, (0, 0))
            arcs[line] = (covered + (1 if taken else 0), total + 1)
    return BranchCoverage(by_line=MappingProxyType(arcs))


def _entry_arcs(
    path: str, branch_id: str, entry: object, arm_counts: object
) -> list[tuple[int, bool]]:
    """One ``branchMap`` entry as ``(line, was_taken)`` per arm."""
    if not isinstance(entry, dict):
        raise _malformed(
            f"record for {path!r}: branch {branch_id!r} is "
            f"{type(entry).__name__}, expected object"
        )
    branch_type = entry.get("type")
    if branch_type not in _ARM_STRUCTURED_BRANCH_TYPES:
        raise _malformed(
            f"record for {path!r}: branch {branch_id!r} is typed "
            f"{branch_type!r}, which is not one of the arm-structured branch "
            f"types this format's arc-bearing producers emit "
            f"({sorted(_ARM_STRUCTURED_BRANCH_TYPES)}). An artifact whose "
            f"entries are typed 'branch' with one location each is a v8-range "
            f"document (@vitest/coverage-v8 or c8), not an istanbul-"
            f"instrumented one: its 'branchMap' describes executed RANGES, "
            f"not the arms of a branch (A-344), so judge.coverage.producer "
            f"does not describe the artifact the lane actually produced"
        )
    entry_line = _entry_line(path, branch_id, entry)
    locations = entry.get("locations")
    if not isinstance(locations, list):
        raise _malformed(
            f"record for {path!r}: branch {branch_id!r} has 'locations' = "
            f"{type(locations).__name__ if locations is not None else 'absent'}"
            f", expected array"
        )
    if not isinstance(arm_counts, list):
        raise _malformed(
            f"record for {path!r}: b[{branch_id!r}] is "
            f"{type(arm_counts).__name__}, expected array"
        )
    if len(locations) != len(arm_counts):
        raise _malformed(
            f"record for {path!r}: branch {branch_id!r} declares "
            f"{len(locations)} arm location(s) but {len(arm_counts)} arm "
            f"count(s) -- which arm each count belongs to is unknowable"
        )
    if not locations:
        raise _malformed(
            f"record for {path!r}: branch {branch_id!r} has zero arms; a "
            f"branch with no arms is malformed, not \"no branches\" (a file "
            f"with no branches carries an empty 'branchMap' instead)"
        )

    arms: list[tuple[int, bool]] = []
    for index, (location, count) in enumerate(zip(locations, arm_counts)):
        arms.append(
            (
                _arm_line(path, branch_id, index, location, entry_line),
                _arm_count(path, branch_id, index, count) > 0,
            )
        )
    return arms


def _entry_line(path: str, branch_id: str, entry: dict) -> int:
    """The line the WHOLE branch is attributed to —
    ``istanbul-lib-coverage``'s own ``map.line || map.loc.start.line``."""
    line = entry.get("line")
    if line is not None:
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise _malformed(
                f"record for {path!r}: branch {branch_id!r} has line = "
                f"{line!r}, expected a positive integer"
            )
        return line
    location = entry.get("loc")
    if not isinstance(location, dict):
        raise _malformed(
            f"record for {path!r}: branch {branch_id!r} carries neither a "
            f"'line' nor a 'loc' object, so nothing attributes it to a line"
        )
    return _position_line(path, f"branch {branch_id!r}", location, "start")


def _arm_line(
    path: str, branch_id: str, index: int, location: object, entry_line: int
) -> int:
    """One arm's own source line, falling back to the branch's line.

    The fallback is MEASURED, not defensive: real ``@vitest/coverage-istanbul``
    output writes an implicit ``else`` arm as ``{"start": {}, "end": {}}`` --
    a location object with no line at all (module docstring).
    """
    if not isinstance(location, dict):
        raise _malformed(
            f"record for {path!r}: branch {branch_id!r} arm {index} is "
            f"{type(location).__name__}, expected object"
        )
    start = location.get("start")
    if not isinstance(start, dict):
        raise _malformed(
            f"record for {path!r}: branch {branch_id!r} arm {index} has no "
            f"'start' position object"
        )
    if "line" not in start:
        return entry_line
    return _position_line(path, f"branch {branch_id!r} arm {index}", location, "start")


def _arm_count(path: str, branch_id: str, index: int, count: object) -> int:
    if isinstance(count, bool) or not isinstance(count, int):
        raise _malformed(
            f"record for {path!r}: b[{branch_id!r}][{index}] is "
            f"{type(count).__name__} ({count!r}), expected int"
        )
    if count < 0:
        raise _malformed(
            f"record for {path!r}: b[{branch_id!r}][{index}] is {count}, a "
            f"negative execution count"
        )
    return count


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
    start = _position_line(path, f"statement {statement_id!r}", location, "start")
    end = _position_line(path, f"statement {statement_id!r}", location, "end")
    if end < start:
        raise _malformed(
            f"record for {path!r}: statement {statement_id!r} ends on line "
            f"{end}, before it starts on line {start}"
        )
    return start, end


def _position_line(path: str, subject: str, location: dict, side: str) -> int:
    """*location*'s ``start``/``end`` line number. Only ``line`` is read;
    ``column`` is deliberately never validated, because real
    ``@vitest/coverage-istanbul`` output writes ``"column": null`` on an end
    position and a parser that required an integer there would reject its
    genuine output.

    *subject* is the already-rendered noun phrase naming what carries the
    position (``"statement '3'"``, ``"branch 0 arm 1"``) rather than a bare
    id, so one message template serves ``statementMap`` and ``branchMap``
    alike without either claiming to be the other."""
    position = location.get(side)
    if not isinstance(position, dict):
        raise _malformed(
            f"record for {path!r}: {subject} has no {side!r} position object"
        )
    line = position.get("line")
    if isinstance(line, bool) or not isinstance(line, int):
        raise _malformed(
            f"record for {path!r}: {subject} has {side}.line = {line!r}, "
            f"expected an integer"
        )
    if line < 1:
        raise _malformed(
            f"record for {path!r}: {subject} has {side}.line = {line}, which "
            f"is not a positive line number"
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
