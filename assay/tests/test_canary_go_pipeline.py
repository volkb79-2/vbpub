"""O1/A-107 -- Go's R1-ONLY cause-sensitive canary: two COMMITTED
coverprofiles (``tests/fixtures/canary/go/greet/**``) fed through the real
:func:`~assay.evaluate.evaluate_coverage` with the real
:class:`~assay.adapters.go.GoAdapter`, via :func:`assay.canary.run_go_canary`.
No Go toolchain, no subprocess, no git repo (A-042/A-087/A-107) -- this
devcontainer has no Go toolchain anywhere, and scripting a fake R0 result
would substitute a hand-picked value for a genuine measurement (A-107).

**Both profiles are real ``go test -coverprofile`` output (F008-A4)**, and so
are the statement positions that make them readable: one run of
``nyxloom-trove/carve-assets/P27-recarve/regenerate-fixtures.sh`` inside
``tester-unified-go:local`` produced the profiles and
``fixture-oracle.json`` together, from these exact source bytes. This module
joins them with the real
:func:`~assay.statement_attribution.attribute_statements` -- the same call
``runner._attribute_statements_for_lane`` makes on a live lane, with the
oracle's subprocess replaced by its committed output and nothing else.

That is what retired the ``_PreOracleGoAdapter`` double this module used to
carry (B057): with genuinely statement-attributed profiles the SHIPPED
adapter, ``requires_statement_attribution=True`` and all, judges these
fixtures directly, so the canary is now proven cause-sensitive at statement
granularity rather than against a downgraded declaration.

``greet_control.out`` marks ``Greet``'s body EXECUTED; ``greet_transformed.out``
marks the SAME plus the appended, never-called canary function's body
MISSING. The transformed SOURCE is still never committed as a second ``.go``
file -- it is computed here by literally running the real
``GoAdapter().inject_uncovered_line`` against the committed ``greet.go``
text, so the fixture cannot drift from the adapter under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import as_statement_attributed, load_go_statement_oracle

from assay import canary
from assay.adapters.go import GoAdapter
from assay.coverage_parsers import go_cover
from assay.errors import Outcome, ReasonCode
from assay.statement_attribution import attribute_statements

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "canary" / "go" / "greet"
TARGET_PATH = "greet/greet.go"

#: The real oracle's statement positions for both halves of this fixture,
#: keyed by basename: ``greet.go`` (the control) and ``greet_transformed.go``
#: (byte-identical to what ``inject_uncovered_line`` produces below).
ORACLE = load_go_statement_oracle(
    Path(__file__).resolve().parents[1]
    / "nyxloom-trove"
    / "carve-assets"
    / "P27-recarve"
    / "fixture-oracle.json"
)

#: THE SHIPPED ADAPTER, undowngraded. Nothing here overrides a declaration.
ADAPTER = GoAdapter()

#: Line 31 is ``Greet``'s single statement -- the ``return``. Its block extent
#: is ``30.32,32.2``, so the naive expansion would also claim the signature
#: (30) and the closing brace (32); the oracle says one statement, and this is
#: it. Named rather than repeated so the synthetic profiles below cannot drift
#: from the real one.
CONTROL_STATEMENT_LINE = 31


@pytest.fixture
def control_source() -> str:
    return (FIXTURE_DIR / "greet.go").read_text(encoding="utf-8")


@pytest.fixture
def control_profile():
    return attribute_statements(
        go_cover.parse(
            (FIXTURE_DIR / "greet_control.out").read_text(encoding="utf-8"),
            producer=None,
        ),
        {TARGET_PATH: ORACLE["greet.go"]},
    )


@pytest.fixture
def transformed_profile():
    return attribute_statements(
        go_cover.parse(
            (FIXTURE_DIR / "greet_transformed.out").read_text(encoding="utf-8"),
            producer=None,
        ),
        {TARGET_PATH: ORACLE["greet_transformed.go"]},
    )


def test_the_canary_fixtures_are_statement_granular_not_the_block_expansion():
    """The claim the retired double could not make, asserted directly.

    ``greet_control.out``'s one block spans lines 30-32 and the transformed
    profile's second block spans 35-38; the naive expansion would report
    three and four lines. The real oracle reports one statement and two. If
    this ever fails, every cause-sensitivity assertion below is judging the
    wrong lines."""
    control = go_cover.parse(
        (FIXTURE_DIR / "greet_control.out").read_text(encoding="utf-8"),
        producer=None,
    )
    assert control.files[TARGET_PATH].executed == frozenset({30, 31, 32})
    corrected = attribute_statements(control, {TARGET_PATH: ORACLE["greet.go"]})
    assert corrected.files[TARGET_PATH].executed == frozenset(
        {CONTROL_STATEMENT_LINE}
    )

    transformed = go_cover.parse(
        (FIXTURE_DIR / "greet_transformed.out").read_text(encoding="utf-8"),
        producer=None,
    )
    assert transformed.files[TARGET_PATH].missing == frozenset({35, 36, 37, 38})
    corrected_transformed = attribute_statements(
        transformed, {TARGET_PATH: ORACLE["greet_transformed.go"]}
    )
    assert corrected_transformed.files[TARGET_PATH].missing == frozenset({36, 37})


# --- O1: the real pipeline catches the transform for the SPECIFIC reason ------


def test_the_real_control_passes_and_the_real_transform_fails_for_uncovered_lines(
    control_source, control_profile, transformed_profile
):
    result = canary.run_go_canary(
        adapter=ADAPTER,
        mechanism=canary.MECHANISM_UNCOVERED_LINE,
        control_source=control_source,
        target_path=TARGET_PATH,
        control_profile=control_profile,
        transformed_profile=transformed_profile,
    )

    assert result.control_outcome is Outcome.PASS
    assert result.transformed_outcome is Outcome.FAIL
    assert result.expected_reason_code is ReasonCode.UNCOVERED_LINES
    assert result.observed_reason_code is ReasonCode.UNCOVERED_LINES

    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.PASS
    assert claim.reason_code is None
    assert claim.canary is result


def test_the_appended_function_is_the_real_adapter_transform_of_the_control_text(
    control_source,
):
    """Ties the fixture's own coverprofile line numbers to the REAL adapter
    output, not to a hand-maintained duplicate -- if the adapter's snippet
    shape ever changes, this fails loudly rather than the coverprofile
    silently describing a transform that no longer happens."""
    transformed_text, _ = ADAPTER.inject_uncovered_line(control_source)

    added = canary._appended_line_range(control_source, transformed_text)
    assert added == frozenset({33, 34, 35, 36, 37, 38})  # blank+blank+sig+body+body+brace

    lines = transformed_text.splitlines()
    assert lines[35] == "\tdoubled := value * 2 // assay-canary: executed by no test"  # line 36
    assert lines[36] == "\treturn doubled"  # line 37

    # ...and those two lines are exactly what the REAL oracle calls statements
    # inside the appended block, so `greet_transformed.out` and the transform
    # are describing the same bytes. The signature (35) and the closing brace
    # (38) are inside the same block extent and are not statements.
    (_, appended_block) = ORACLE["greet_transformed.go"]
    assert appended_block.stmt_lines == (36, 37)
    assert (appended_block.start_line, appended_block.end_line) == (35, 38)


# --- O3/A-109: malformed and no-op transforms are INCONCLUSIVE ----------------


def test_import_break_is_not_provable_by_the_r1_only_go_canary_and_is_inconclusive(
    control_source, control_profile, transformed_profile
):
    """A-107's own carve-out: Go's canary proves R1 sensitivity only, and
    import-break is not an R1-observable mechanism (a real panicking
    init() prevents `go test -coverprofile` from ever writing a profile at
    all) -- requesting it renders INCONCLUSIVE, not a guess."""
    result = canary.run_go_canary(
        adapter=ADAPTER,
        mechanism=canary.MECHANISM_IMPORT_BREAK,
        control_source=control_source,
        target_path=TARGET_PATH,
        control_profile=control_profile,
        transformed_profile=transformed_profile,
    )

    assert result.control_outcome is Outcome.PASS  # the control still ran
    assert result.transformed_outcome is None
    assert result.expected_reason_code is None

    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.INCONCLUSIVE
    assert claim.reason_code is ReasonCode.CANARY_INCONCLUSIVE


def test_an_unrecognised_mechanism_is_inconclusive(control_profile, control_source):
    result = canary.run_go_canary(
        adapter=ADAPTER,
        mechanism="not-a-real-mechanism",
        control_source=control_source,
        target_path=TARGET_PATH,
        control_profile=control_profile,
        transformed_profile=control_profile,  # never consulted
    )

    assert result.transformed_outcome is None
    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.INCONCLUSIVE
    assert claim.reason_code is ReasonCode.CANARY_INCONCLUSIVE


def test_a_transform_that_produces_no_change_is_inconclusive(control_profile):
    """O3's own second INCONCLUSIVE cause: 'a transform that changes no
    target'. A fake adapter's inject_uncovered_line deliberately returns the
    SAME text unchanged, proving the no-op path is genuinely reached (never
    naturally true of the real GoAdapter, which always appends something)."""

    class NoOpAdapter(GoAdapter):
        def inject_uncovered_line(self, text: str) -> tuple[str, str]:
            return text, "no-op: nothing changed"

    adapter = NoOpAdapter()
    source = "package greet\n"
    result = canary.run_go_canary(
        adapter=adapter,
        mechanism=canary.MECHANISM_UNCOVERED_LINE,
        control_source=source,
        target_path=TARGET_PATH,
        control_profile=control_profile,
        transformed_profile=control_profile,
    )

    assert result.transformed_outcome is None
    assert "nothing to judge" in result.description
    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.INCONCLUSIVE
    assert claim.reason_code is ReasonCode.CANARY_INCONCLUSIVE


# --- O3/A-109: a wrong-reason failure still SURVIVES ---------------------------


def test_a_transformed_run_that_fails_for_the_wrong_reason_survives(
    control_source, control_profile
):
    """Engineered via a synthetic transformed profile that marks the WRONG
    lines missing (not the canary's own appended lines, but a pragma-style
    exclusion on the control's own already-covered line) -- the transformed
    run still renders FAIL, but for EXCLUDED_LINES rather than the mechanism's
    own expected UNCOVERED_LINES, so the canary must record it as SURVIVED,
    not PASS."""
    from assay.coverage_parsers.model import CoverageProfile, FileCoverage
    from types import MappingProxyType

    transformed_text, _ = ADAPTER.inject_uncovered_line(control_source)
    added = canary._appended_line_range(control_source, transformed_text)

    # A profile that marks the appended lines EXCLUDED (not missing) with
    # allow_excluded left False -- a real, valid FAIL cause, just not the
    # one this mechanism is supposed to produce. executed and excluded must
    # stay disjoint (P15's common-model invariant): only the control's own
    # already-covered line is executed, never the excluded lines too.
    wrong_reason_profile = as_statement_attributed(
        CoverageProfile(
            files=MappingProxyType(
                {
                    TARGET_PATH: FileCoverage(
                        executed=frozenset({CONTROL_STATEMENT_LINE}),
                        missing=frozenset(),
                        excluded=added,
                    )
                }
            )
        )
    )

    result = canary.run_go_canary(
        adapter=ADAPTER,
        mechanism=canary.MECHANISM_UNCOVERED_LINE,
        control_source=control_source,
        target_path=TARGET_PATH,
        control_profile=control_profile,
        transformed_profile=wrong_reason_profile,
    )

    assert result.transformed_outcome is Outcome.FAIL
    assert result.observed_reason_code is ReasonCode.EXCLUDED_LINES
    assert result.expected_reason_code is ReasonCode.UNCOVERED_LINES

    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.FAIL
    assert claim.reason_code is ReasonCode.CANARY_SURVIVED


# --- control validity: a broken baseline is caught, not silently passed ------


def test_a_broken_control_profile_renders_inconclusive_not_a_silent_pass(
    control_source, transformed_profile
):
    """O1's negative: 'a broken baseline makes the good half fail' -- proven
    here by feeding a CONTROL profile that reports the control's own line as
    MISSING (as if the control itself were never actually exercised)."""
    from assay.coverage_parsers.model import CoverageProfile, FileCoverage
    from types import MappingProxyType

    broken_control_profile = as_statement_attributed(
        CoverageProfile(
            files=MappingProxyType(
                {
                    TARGET_PATH: FileCoverage(
                        executed=frozenset(),
                        missing=frozenset({CONTROL_STATEMENT_LINE}),
                        excluded=None,
                    )
                }
            )
        )
    )

    result = canary.run_go_canary(
        adapter=ADAPTER,
        mechanism=canary.MECHANISM_UNCOVERED_LINE,
        control_source=control_source,
        target_path=TARGET_PATH,
        control_profile=broken_control_profile,
        transformed_profile=transformed_profile,
    )

    assert result.control_outcome is Outcome.FAIL

    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.INCONCLUSIVE
    assert claim.reason_code is ReasonCode.CANARY_INCONCLUSIVE
