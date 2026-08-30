"""B036/A-346 — the two Vitest coverage providers are not equally TRUSTWORTHY,
measured against ground truth rather than against each other.

The rest of this change measured the two providers' SHAPE — extent geometry,
`branchMap` typing, key spelling. This module measures the one property an R1
judge actually rests on: **is a line the artifact calls executed a line that
really ran.** The round-1 adversarial review found that question had never
been asked, and that the answer for one provider is no.

`tests/fixtures/coverage/probe-js-provider-defect` is a project built so
ground truth needs no instrumentation to know: five functions, each with a
``if (v === 0) return 0`` guard on its second line, and one test that calls
every function with ``0``. **Every line below a guard is therefore provably
never executed** — that is a fact about the program, not about any coverage
tool. Four real artifacts are committed: both providers, on Vitest 3.2.4 AND
Vitest 4.1.11.

`@vitest/coverage-istanbul` is correct on all five shapes in both versions.
`@vitest/coverage-v8` reports never-executed lines as EXECUTED whenever a
CONDITIONAL (ternary) expression appears earlier in the same block — in both
versions, for a one-line ternary as well as a multi-line one, and not fixed by
``coverage.experimentalAstAwareRemapping``. A multi-line binary expression, a
multi-line call and a multi-line object literal do NOT trigger it.

**These tests pin a DEFECT as a witness, deliberately, against the FOUR
COMMITTED artifacts** — they read no live Node/Vitest, so an upstream fix
does not turn them red on its own. Regenerating `probe-js-provider-defect`'s
v8 fixtures against a fixed `@vitest/coverage-v8` (B040's manual recheck
item) is what would make `test_the_v8_provider_*` below FAIL, and that
failure is the signal to revisit A-346's ruling, not a test to relax. The
defect is why assay's own documentation names `@vitest/coverage-istanbul` as
the only Vitest provider safe for a judged lane; if it goes away, so does the
reason.

Negative: without this module the change ships guidance steering consumers
onto a provider that produces false-clean R1 verdicts, with the committed v8
fixture already carrying an instance nothing asserted against.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

import pytest

from assay.adapters.javascript import JavaScriptAdapter
from assay.coverage import load_coverage_profile
from assay.coverage_parsers.model import CoverageProfile
from assay.diff import AddedLines
from assay.errors import Outcome, ReasonCode
from assay.evaluate import evaluate_coverage

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "coverage"
DEFECT_PROBE = FIXTURES / "probe-js-provider-defect"

V8_3 = "coverage-istanbul-json.provider-defect.vitest3-v8.json"
ISTANBUL_3 = "coverage-istanbul-json.provider-defect.vitest3-istanbul.json"
V8_4 = "coverage-istanbul-json.provider-defect.vitest4-v8.json"
ISTANBUL_4 = "coverage-istanbul-json.provider-defect.vitest4-istanbul.json"

V8_ARTIFACTS = (V8_3, V8_4)
ISTANBUL_ARTIFACTS = (ISTANBUL_3, ISTANBUL_4)
ALL_ARTIFACTS = V8_ARTIFACTS + ISTANBUL_ARTIFACTS

#: Ground truth, read off `shapes.ts` by hand: the guard line of each
#: function, and the lines below it that the single ``f(0)`` call can never
#: reach. Closing braces are excluded -- whether a brace "executes" is a
#: question about the instrumenter's model, not about the program, and this
#: module is deliberately only about lines whose execution is unambiguous.
NEVER_EXECUTED = {
    "ternaryMultiLine": (5, [6, 7, 8, 9, 10, 11]),
    "ternaryOneLine": (15, [16, 17, 18]),
    "binaryMultiLine": (22, [23, 24, 25, 26, 27]),
    "callMultiLine": (31, [32, 33, 34, 35, 36, 37]),
    "objectLiteralMultiLine": (41, [42, 43, 44, 45, 46, 47]),
}

#: The lines `@vitest/coverage-v8` gets WRONG, per version -- measured, and
#: exactly the ternary shapes. Vitest 4 fixed the ternary's OWN line (16) and
#: nothing else; the lines after a ternary are still falsely executed in both.
V8_FALSE_GREENS = {
    V8_3: [10, 11, 16, 17, 18],
    V8_4: [10, 11, 17, 18],
}


def _load(name: str) -> CoverageProfile:
    return load_coverage_profile(
        (FIXTURES / name).read_text(encoding="utf-8"),
        declared_format="coverage-istanbul-json",
    )


def _shapes(name: str):
    profile = _load(name)
    (record,) = [
        value for key, value in profile.files.items() if key.endswith("shapes.ts")
    ]
    return record


def _all_never_executed() -> list[int]:
    return sorted(
        line for _guard, lines in NEVER_EXECUTED.values() for line in lines
    )


# --- the fixture's own ground truth is still what it claims -----------------


def test_the_probe_source_still_has_the_shape_this_module_reasons_about():
    """Ground truth here is an argument about the SOURCE, so the source is
    pinned. If `shapes.ts` is ever edited, every expectation below becomes a
    claim about a program that no longer exists -- this fails first and says
    so, rather than letting the numbers quietly stop meaning anything."""
    lines = (DEFECT_PROBE / "src" / "shapes.ts").read_text(
        encoding="utf-8"
    ).splitlines()

    for name, (guard, body) in NEVER_EXECUTED.items():
        assert f"export function {name}(" in lines[guard - 2], name
        assert lines[guard - 1].strip() == "if (v === 0) return 0", name
        assert lines[body[-1] - 1].strip() == "return b", name
        assert lines[body[-1]].strip() == "}", name

    test_source = (DEFECT_PROBE / "src" / "shapes.test.ts").read_text(encoding="utf-8")
    for name in NEVER_EXECUTED:
        assert f"expect({name}(0)).toBe(0)" in test_source, name


@pytest.mark.parametrize("name", ALL_ARTIFACTS)
def test_every_artifact_measures_the_probe_and_agrees_on_the_guards(name: str):
    """The must-succeed control: all four artifacts really measured this file,
    and all four agree the guard lines DID run. A provider that measured
    nothing, or that this parser mis-read wholesale, would fail here rather
    than produce an interesting-looking disagreement below."""
    record = _shapes(name)

    assert record.executed or record.missing
    for function, (guard, _body) in NEVER_EXECUTED.items():
        assert guard in record.executed, f"{name}: {function} guard line {guard}"


# --- istanbul: correct on every shape, in both versions ---------------------


@pytest.mark.parametrize("name", ISTANBUL_ARTIFACTS)
def test_the_istanbul_provider_never_reports_a_never_executed_line_as_executed(
    name: str,
):
    """Zero false greens, across all five shapes and both major versions.
    This is what makes A-346's ruling a CHOICE between providers rather than
    a limitation of the format."""
    record = _shapes(name)
    false_greens = sorted(set(_all_never_executed()) & record.executed)

    assert false_greens == []


@pytest.mark.parametrize("name", ISTANBUL_ARTIFACTS)
def test_the_istanbul_provider_reports_the_ternary_bodies_as_missing(name: str):
    """Not merely "not executed" -- actually classified MISSING, so the lines
    reach the denominator and an R1 floor can refuse them. A provider that
    simply omitted them would be silent rather than wrong, which is a
    different (and much less dangerous) failure."""
    record = _shapes(name)

    assert {10, 11} <= record.missing
    assert {17, 18} <= record.missing


# --- v8: the defect, pinned as a witness ------------------------------------


@pytest.mark.parametrize("name", V8_ARTIFACTS)
def test_the_v8_provider_falsely_reports_never_executed_lines_as_executed(name: str):
    """**This test asserts that a bug still exists, against a COMMITTED
    fixture** -- it cannot go red from an upstream fix on its own. If a
    fixture regenerated against a newer `@vitest/coverage-v8` (B040's manual
    recheck item) ever makes this fail, do not relax it -- revisit A-346's
    ruling and re-measure, because the reason assay's docs name istanbul as
    the only judged-safe Vitest provider will have gone away."""
    record = _shapes(name)
    false_greens = sorted(set(_all_never_executed()) & record.executed)

    assert false_greens == V8_FALSE_GREENS[name]


@pytest.mark.parametrize("name", V8_ARTIFACTS)
def test_only_the_ternary_shapes_trigger_the_v8_mis_attribution(name: str):
    """The characterisation A-346 rests on, and the reason no guard is
    possible: the trigger is a CONDITIONAL expression, one-line or
    multi-line. A multi-line binary expression, a multi-line call and a
    multi-line object literal are all reported correctly by the same provider
    in the same artifact -- so there is no structural property of the
    document that separates a trustworthy record from an untrustworthy one."""
    record = _shapes(name)
    unaffected = ("binaryMultiLine", "callMultiLine", "objectLiteralMultiLine")

    for function in unaffected:
        _guard, body = NEVER_EXECUTED[function]
        # No FALSE GREEN is the claim. Not every body line need be `missing`:
        # Vitest 4's v8 provider emits real multi-line extents, so a bare
        # `const a =` line whose recorded statement starts on the next line is
        # unattributed rather than missing -- the same rule-4 gap the istanbul
        # provider has always had (A-342's corrected guarantee). Unattributed
        # is silence; falsely-executed is a lie. Only the second is this
        # module's subject.
        assert not (set(body) & record.executed), function
        assert set(body) & record.missing, function

    for function in ("ternaryMultiLine", "ternaryOneLine"):
        _guard, body = NEVER_EXECUTED[function]
        assert set(body) & record.executed, function


def test_the_v8_defect_is_not_a_version_that_can_be_upgraded_past():
    """Both currently-released major versions, measured. This is why A-346
    rules the provider unsafe rather than pinning a minimum version."""
    assert V8_FALSE_GREENS[V8_3] and V8_FALSE_GREENS[V8_4]
    assert set(V8_FALSE_GREENS[V8_4]) <= set(V8_FALSE_GREENS[V8_3])
    # ...and the istanbul provider is clean in BOTH, so it is the provider
    # that differs, not the Vitest version.
    for name in ISTANBUL_ARTIFACTS:
        assert not (set(_all_never_executed()) & _shapes(name).executed)


# --- what it costs an actual R1 lane ----------------------------------------


def _judge(name: str, repo: Path, changed: frozenset[int], *, fail_under: float):
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    rebased = {
        f"{repo}/src/shapes.ts": record
        for key, record in raw.items()
        if key.endswith("shapes.ts")
    }
    return evaluate_coverage(
        added=AddedLines(by_file=MappingProxyType({"src/shapes.ts": changed})),
        profile=load_coverage_profile(
            json.dumps(rebased), declared_format="coverage-istanbul-json"
        ),
        adapter=JavaScriptAdapter(),
        repo_top=repo,
        project_root=repo,
        source_root_paths=(repo / "src",),
        fail_under=fail_under,
        allow_excluded=False,
        read_source_text=lambda path: (repo / path).read_text(encoding="utf-8"),
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "shapes.ts").write_text(
        (DEFECT_PROBE / "src" / "shapes.ts").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize("name", V8_ARTIFACTS)
def test_a_v8_artifact_makes_a_real_r1_lane_pass_on_lines_that_never_ran(
    name: str, repo: Path
):
    """The whole point, driven through the shipped evaluation: a diff that
    touches ONLY provably-never-executed lines reports 100% and PASSes a
    100% floor. This is a false clean verdict, produced from a real artifact
    by real assay code -- the thing an R1 gate exists to make impossible."""
    result = _judge(name, repo, frozenset({10, 11}), fail_under=100.0)

    assert result.outcome is Outcome.PASS
    assert result.pct == 100.0
    assert result.covered == 2
    assert result.missing_lines == {}


@pytest.mark.parametrize("name", ISTANBUL_ARTIFACTS)
def test_the_same_lines_correctly_fail_under_the_istanbul_provider(
    name: str, repo: Path
):
    """Same source, same tests, same assay code, same changed lines: the
    other provider's artifact refuses them by name. The divergence is
    entirely the producer's."""
    result = _judge(name, repo, frozenset({10, 11}), fail_under=100.0)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES
    assert result.pct == 0.0
    assert result.missing_lines == {"src/shapes.ts": frozenset({10, 11})}


# --- the instance already sitting in the main committed fixture -------------


def test_the_main_v8_fixture_already_contains_an_instance_of_the_defect():
    """Found by the round-1 review inside evidence this change had already
    committed and already iterated over without noticing.
    `probe-js/src/format.ts` has a multi-line ternary at ``[12, 15]``; the
    only test calling it is ``relativeTime('')``, which returns at line 9.
    Lines 17-18 therefore never run, and the v8 artifact calls them
    executed while the istanbul artifact correctly calls them missing."""
    source = (
        FIXTURES / "probe-js" / "src" / "format.ts"
    ).read_text(encoding="utf-8").splitlines()
    assert source[8].strip().endswith("return '—'")
    assert source[16].strip() == "const ms = date.getTime()"
    assert source[17].strip() == "if (Number.isNaN(ms)) return '—'"
    assert "relativeTime('')" in (
        FIXTURES / "probe-js" / "src" / "__tests__" / "roles.test.ts"
    ).read_text(encoding="utf-8")

    def format_record(name: str):
        profile = _load(name)
        (record,) = [
            value for key, value in profile.files.items() if key.endswith("format.ts")
        ]
        return record

    v8 = format_record("coverage-istanbul-json.vitest-v8.json")
    istanbul = format_record("coverage-istanbul-json.vitest-istanbul.json")

    assert {17, 18} <= v8.executed
    assert not ({17, 18} & istanbul.executed)
    assert {17, 18} <= istanbul.missing
