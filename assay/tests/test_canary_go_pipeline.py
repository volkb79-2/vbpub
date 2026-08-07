"""O1/A-107 -- Go's R1-ONLY cause-sensitive canary: two COMMITTED,
pre-generated coverprofiles (``tests/fixtures/canary/go/greet/**``) fed
through the real :func:`~assay.evaluate.evaluate_coverage` with the real
:class:`~assay.adapters.go.GoAdapter`, via :func:`assay.canary.run_go_canary`.
No Go toolchain, no subprocess, no git repo (A-042/A-087/A-107) -- this
devcontainer has no Go toolchain anywhere, and scripting a fake R0 result
would substitute a hand-picked value for a genuine measurement (A-107).

``greet_control.out`` marks ``Greet``'s body EXECUTED; ``greet_transformed.out``
marks the SAME plus the appended, never-called canary function's body
MISSING -- computed by literally running the real
``GoAdapter().inject_uncovered_line`` against the committed ``greet.go`` text
(never a second committed ``.go`` file, so the fixture cannot drift from the
adapter under test; the fixture's independence lives in the hand-authored
coverprofile instead).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay import canary
from assay.adapters.go import GoAdapter
from assay.coverage_parsers import go_cover
from assay.errors import Outcome, ReasonCode

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "canary" / "go" / "greet"
TARGET_PATH = "greet/greet.go"

ADAPTER = GoAdapter()


@pytest.fixture
def control_source() -> str:
    return (FIXTURE_DIR / "greet.go").read_text(encoding="utf-8")


@pytest.fixture
def control_profile():
    return go_cover.parse((FIXTURE_DIR / "greet_control.out").read_text(encoding="utf-8"))


@pytest.fixture
def transformed_profile():
    return go_cover.parse(
        (FIXTURE_DIR / "greet_transformed.out").read_text(encoding="utf-8")
    )


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
    assert added == frozenset({21, 22, 23, 24, 25, 26})  # blank+blank+sig+body+body+brace

    lines = transformed_text.splitlines()
    assert lines[23] == "\tdoubled := value * 2 // assay-canary: executed by no test"  # line 24
    assert lines[24] == "\treturn doubled"  # line 25 -- matches greet_transformed.out


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
    wrong_reason_profile = CoverageProfile(
        files=MappingProxyType(
            {
                TARGET_PATH: FileCoverage(
                    executed=frozenset({19}),
                    missing=frozenset(),
                    excluded=added,
                )
            }
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

    broken_control_profile = CoverageProfile(
        files=MappingProxyType(
            {
                TARGET_PATH: FileCoverage(
                    executed=frozenset(), missing=frozenset({19}), excluded=None
                )
            }
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
