"""O1/O2 — rule 3b (statement-span attribution) proven through the real,
unmodified ``evaluate_coverage`` (P05's own function, untouched by this
package's changes to its BODY -- only its behaviour is extended).

O1's claim: *a changed continuation line in an executed multiline Python
statement is attributed to that statement and passes, provably (covered/
executable/missing_lines counts reflect the attribution, not merely
the final PASS outcome); the same span unexecuted fails with the statement
start named.* Proven below with the REAL ``PythonAdapter`` and real,
hand-derived (never adapter-generated) coverage.py semantics for the exact
worked example in ``assay-P06-BRIEF.md`` (a multi-line dict literal
coverage.py attributes to its own first line only).

O2's claim: overlapping/malformed/genuinely-unattributable spans all render
``FAIL``/``UNCLASSIFIED_LINES``, never PASS. The overlap/malformed cases are
hand-built spans fed through a synthetic adapter (never hunted for in real
Python, which cannot produce genuine overlap under correct nesting, A-101);
the "genuinely unattributable" case (a changed interior line whose enclosing
span is itself untracked) IS a real, natural Python case and is proven
against real ``PythonAdapter`` parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from assay.adapters.base import StatementSpan
from assay.adapters.python import PythonAdapter
from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import Outcome, ReasonCode
from assay.evaluate import evaluate_coverage

REPO_TOP = Path("/repo")

# The exact worked example from assay-P06-BRIEF.md / dstdns's own B065 bug:
# real coverage.py attributes the trace hit for this whole `return {...}`
# statement to line 2 ONLY -- lines 3-5 appear in NEITHER executed_lines NOR
# missing_lines at all, even though line 4 is real, changed code.
SOURCE = (
    "def build_config():\n"  # 1
    "    return {\n"  # 2
    '        "a": 1,\n'  # 3
    '        "b": 2,\n'  # 4
    "    }\n"  # 5
)

# The same statement, but its own anchor line (2) carries a pragma -- so the
# anchor itself is EXCLUDED, not executed/missing: a genuinely unattributable
# real-Python case (A-101's own named example).
SOURCE_EXCLUDED_ANCHOR = (
    "def build_config():\n"  # 1
    "    return {  # pragma: no cover\n"  # 2
    '        "a": 1,\n'  # 3
    '        "b": 2,\n'  # 4
    "    }\n"  # 5
)

PATH = "pkg/mod.py"


def _evaluate(source: str, *, added_lines: set[int], executed, missing, excluded=frozenset()):
    adapter = PythonAdapter()
    added = AddedLines(by_file=MappingProxyType({PATH: frozenset(added_lines)}))
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                PATH: FileCoverage(
                    executed=frozenset(executed),
                    missing=frozenset(missing),
                    excluded=frozenset(excluded),
                )
            }
        )
    )

    def read_source_text(path: str) -> str:
        assert path == PATH
        return source

    return evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=REPO_TOP,
        source_root_paths=(REPO_TOP / "pkg",),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=read_source_text,
    )


# --- O1: the executed continuation line is attributed and passes -------------


def test_an_executed_interior_line_is_attributed_and_passes_provably():
    """The negative's first half: line-number-only evaluation would drop
    line 4 out of BOTH the numerator and denominator (it is in neither
    `executed` nor `missing`) -- silently PASSING at 0/0 with zero signal.
    This asserts the counts THEMSELVES moved, not merely the outcome."""
    result = _evaluate(
        SOURCE, added_lines={4}, executed={2}, missing=set()
    )

    assert result.covered == 1
    assert result.executable == 1
    assert result.missing_lines == {}
    assert result.unclassified_lines == {}
    assert result.outcome is Outcome.PASS
    assert result.reason_code is None


def test_the_same_span_unexecuted_fails_with_the_statement_start_named():
    """The negative's second half: the interior line's anchor (the
    statement's own start, line 2) is what the attribution mechanism
    resolves it to; when line 2 ITSELF is also changed and reported
    `missing`, the failure names BOTH the statement start (2) and the
    attributed interior line (4) in `missing_lines` -- provably, not merely
    a flipped PASS/FAIL bit."""
    result = _evaluate(
        SOURCE, added_lines={2, 4}, executed=set(), missing={2}
    )

    assert result.covered == 0
    assert result.executable == 2
    assert result.missing_lines == {PATH: frozenset({2, 4})}
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES


def test_an_unexecuted_interior_line_alone_fails_naming_itself_not_the_anchor():
    """The diff never touches the anchor line (2) at all here -- only the
    interior line (4) is 'changed'. The failure names the CHANGED line (4),
    the location a human diffing the change would actually look at -- never
    line 2 (which was not part of this diff), and never silently 0/0."""
    result = _evaluate(
        SOURCE, added_lines={4}, executed=set(), missing={2}
    )

    assert result.covered == 0
    assert result.executable == 1
    assert result.missing_lines == {PATH: frozenset({4})}
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES


def test_a_changed_line_that_is_itself_an_untracked_statement_start_is_non_code():
    """The anchor resolves to the CHANGED line itself (it IS a statement's
    own start -- here, a decorator line), yet the coverage format still
    never tracked it. Genuinely non-executable, the same as a comment --
    never an ambiguity, and never counted."""
    source = "@decorate\ndef f():\n    return 1\n"
    result = _evaluate(source, added_lines={1}, executed=set(), missing=set())

    assert result.executable == 0
    assert result.missing_lines == {}
    assert result.unclassified_lines == {}
    assert result.outcome is Outcome.PASS


def test_a_line_between_statements_is_still_silently_non_code():
    """A blank line changed alongside the interior line stays rule-4
    non-code -- span attribution must not turn every unattributed line into
    a claim about something."""
    source = SOURCE + "\n\nx = 1\n"  # line 6 blank, line 7 blank, line 8 code
    result = _evaluate(source, added_lines={4, 6}, executed={2}, missing=set())

    assert result.executable == 1  # only line 4 counted; line 6 is not code
    assert result.unclassified_lines == {}
    assert result.outcome is Outcome.PASS


# --- O2: a genuinely unattributable real-Python case (A-101's own example) ---


def test_an_interior_line_whose_anchor_is_excluded_is_genuinely_unattributable():
    """The enclosing statement's own tracked line (2) carries a pragma, so
    it is EXCLUDED rather than executed/missing -- its own status is
    unknown, so the interior line cannot inherit anything. FAIL/
    UNCLASSIFIED_LINES, never silently dropped as non-code and never
    silently passed."""
    result = _evaluate(
        SOURCE_EXCLUDED_ANCHOR,
        added_lines={4},
        executed=set(),
        missing=set(),
        excluded={2},
    )

    assert result.unclassified_lines == {PATH: frozenset({4})}
    assert result.files_with_unclassified_lines == (PATH,)
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCLASSIFIED_LINES
    # The ambiguous line contributes to NEITHER the numerator nor the
    # denominator -- it is not merely folded into a lower percentage.
    assert result.executable == 0
    assert result.covered == 0


def test_unparseable_source_makes_every_unattributed_line_unclassified():
    result = _evaluate(
        "def broken(:\n", added_lines={1}, executed=set(), missing=set()
    )

    assert result.unclassified_lines == {PATH: frozenset({1})}
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCLASSIFIED_LINES


# --- O2: overlap/malformed -- hand-built spans, never hunted for in real
# Python, fed through a synthetic adapter directly into the real union -----


@dataclass(frozen=True, kw_only=True)
class SpanAdapter:
    """A synthetic adapter (in the spirit of ``conftest.FakeAdapter``) whose
    ``statement_spans`` is directly injectable, so O2's defensive
    overlap/malformed-set cases can be driven through the REAL
    ``evaluate_coverage`` union with hand-built spans -- A-101's own
    instruction that these are never derived from parsed source, since
    correctly-nested real Python cannot produce genuine overlap."""

    name: str = "spanzzz"
    source_globs: tuple[str, ...] = ("*.zzz",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = True
    external_tools: tuple[str, ...] = ()
    spans: tuple[StatementSpan, ...] | None = ()

    def is_test_path(self, rel_path: str) -> bool:
        return False

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return True

    def normalize_coverage_key(self, key: str) -> str:
        return key

    def statement_spans(self, text: str) -> tuple[StatementSpan, ...] | None:
        return self.spans


SPAN_PATH = "pkg/mod.zzz"


def _evaluate_spans(
    adapter: SpanAdapter,
    *,
    added_line: int,
    fail_under: float = 100.0,
    executed: frozenset[int] = frozenset(),
    missing: frozenset[int] = frozenset(),
):
    added = AddedLines(by_file=MappingProxyType({SPAN_PATH: frozenset({added_line})}))
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                SPAN_PATH: FileCoverage(
                    executed=executed, missing=missing, excluded=frozenset()
                )
            }
        )
    )

    def read_source_text(path: str) -> str:
        return "irrelevant -- SpanAdapter ignores its text argument\n"

    return evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=REPO_TOP,
        source_root_paths=(REPO_TOP / "pkg",),
        fail_under=fail_under,
        allow_excluded=False,
        read_source_text=read_source_text,
    )


#: Two spans both containing line 5; neither contains the other (3-6 and 5-8
#: partially intersect). Deliberately given the SAME size (3) so a naive
#: "pick the smallest, tie-break arbitrarily" resolution (dstdns's own
#: `attribute_line`, un-defended) would deterministically pick whichever
#: sorts/lists first -- here, the span starting at line 3.
_OVERLAPPING_SPANS = (
    StatementSpan(start_line=3, end_line=6),
    StatementSpan(start_line=5, end_line=8),
)


def test_overlapping_non_nesting_spans_render_unclassified_never_pass():
    """The negative's sharpest form: 'choosing the first overlap ... turns
    at least one ambiguity fixture green.' Line 3 (the first-listed span's
    own start) is genuinely EXECUTED -- a naive resolution that silently
    picked span (3, 6) over span (5, 8) would inherit line 3's own COVERED
    status and this fixture would PASS. The real guard refuses to choose and
    renders FAIL/UNCLASSIFIED_LINES instead."""
    adapter = SpanAdapter(spans=_OVERLAPPING_SPANS)
    result = _evaluate_spans(adapter, added_line=5, executed=frozenset({3}))

    assert result.unclassified_lines == {SPAN_PATH: frozenset({5})}
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCLASSIFIED_LINES
    # The ambiguous line never contributes to the numerator, even though a
    # naive resolution would have credited it as covered.
    assert result.covered == 0
    assert result.executable == 0


def test_overlap_ambiguity_outranks_a_permissive_fail_under():
    """The sharpest form of 'no ambiguity becomes PASS': even a fail_under
    of 0.0 (the most permissive floor possible) does not launder an
    ambiguous line into a pass -- UNCLASSIFIED_LINES is checked before the
    percentage at all, unconditionally (A-100)."""
    adapter = SpanAdapter(spans=_OVERLAPPING_SPANS)
    result = _evaluate_spans(adapter, added_line=5, fail_under=0.0)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCLASSIFIED_LINES


def test_an_adapter_returning_none_while_requiring_attribution_is_unclassified():
    """A ``statement_spans`` implementation that reports 'could not parse'
    (``None``) makes every unattributed changed line unclassified -- never
    dropped as if it were merely non-code."""
    adapter = SpanAdapter(spans=None)
    result = _evaluate_spans(adapter, added_line=5)

    assert result.unclassified_lines == {SPAN_PATH: frozenset({5})}
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCLASSIFIED_LINES


def test_a_disjoint_span_that_does_not_contain_the_line_is_simply_non_code():
    """Sanity check on the SpanAdapter harness itself: a span that does not
    contain the changed line at all leaves it as ordinary rule-4 non-code,
    not an ambiguity -- proving the FAIL cases above are really about
    overlap/malformed spans, not about SpanAdapter always failing."""
    adapter = SpanAdapter(spans=(StatementSpan(start_line=100, end_line=105),))
    result = _evaluate_spans(adapter, added_line=5)

    assert result.unclassified_lines == {}
    assert result.outcome is Outcome.PASS


def test_no_declared_span_attribution_leaves_the_line_silently_non_code():
    """`requires_span_attribution=False` means `statement_spans` is never
    even called -- an adapter with no such capability is unaffected by this
    package's addition, exactly as `FakeAdapter` already proves at the P05
    layer with an empty-tuple default."""
    adapter = SpanAdapter(requires_span_attribution=False, spans=(StatementSpan(start_line=1, end_line=10),))
    result = _evaluate_spans(adapter, added_line=5)

    assert result.unclassified_lines == {}
    assert result.outcome is Outcome.PASS
