"""B036 — a whole JavaScript/TypeScript R1 evaluation, end to end, over a REAL
``vitest run --coverage`` artifact.

The coverage data here is not invented: it is
``tests/fixtures/coverage/coverage-istanbul-json.vitest-v8.json``, produced by
a real Vitest run against the committed ``fixtures/coverage/probe-js``
project, with ONLY its own producing-directory prefix rebased onto the test's
temp repository. Every executed/missing line, every extent, every count is the
tool's own. Rebasing the prefix is what lets an absolute-keyed artifact be
judged against a diff computed in a different directory -- which is exactly
the reconciliation ``evaluate._to_repo_relative_key`` performs in production,
tested here against the real key shape rather than a hand-written one.

Negative: without the core's absolute-key branch, every record key stays
absolute, matches no changed file, and the lane reports every changed file as
"missing coverage" -- a total, silent misjudgement that a relative-keyed
fixture would never surface.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import MappingProxyType

import pytest

from assay.adapters.javascript import JavaScriptAdapter
from assay.coverage import load_coverage_profile
from assay.diff import AddedLines
from assay.errors import Outcome, ReasonCode
from assay.evaluate import evaluate_coverage

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "coverage"
PROBE = FIXTURES / "probe-js"

#: Both committed artifacts, so nothing here is a v8-only fact. The round-1
#: review found this module drove the v8 one ONLY, which is how it iterated
#: over `format.ts` -- a file whose v8 record carries a provider false green
#: on lines 17-18 -- and asserted nothing that would notice (A-346, B040;
#: `test_coverage_istanbul_provider_accuracy.py` now owns that witness).
ARTIFACTS = {
    "v8": FIXTURES / "coverage-istanbul-json.vitest-v8.json",
    "istanbul": FIXTURES / "coverage-istanbul-json.vitest-istanbul.json",
}
BOTH = tuple(ARTIFACTS)
V8_ARTIFACT = ARTIFACTS["v8"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The probe project's own sources, copied into a temp "repository" so
    the artifact's rebased keys name files that really exist there."""
    shutil.copytree(PROBE / "src", tmp_path / "src")
    return tmp_path


def rebased_profile(repo: Path, provider: str = "v8"):
    """The REAL artifact with only its ``/…/probe-js`` directory prefix
    rewritten to *repo*. Nothing else is touched -- not a line number, not a
    count, not a statement extent."""
    raw = json.loads(ARTIFACTS[provider].read_text(encoding="utf-8"))
    rebased = {
        f"{repo}/{key.split('/probe-js/', 1)[1]}": record
        for key, record in raw.items()
    }
    return load_coverage_profile(
        json.dumps(rebased), declared_format="coverage-istanbul-json"
    )


def evaluate(
    repo: Path,
    by_file: dict[str, frozenset[int]],
    *,
    fail_under: float,
    provider: str = "v8",
):
    def read_source_text(path: str) -> str:
        return (repo / path).read_text(encoding="utf-8")

    return evaluate_coverage(
        added=AddedLines(by_file=MappingProxyType(by_file)),
        profile=rebased_profile(repo, provider),
        adapter=JavaScriptAdapter(),
        repo_top=repo,
        project_root=repo,
        source_root_paths=(repo / "src",),
        fail_under=fail_under,
        allow_excluded=False,
        read_source_text=read_source_text,
    )


def test_a_fully_covered_change_passes(repo: Path):
    """``roles.ts`` is fully exercised by the probe's own test, so a diff
    touching only its lines clears a 100% floor. This is the proof the
    absolute keys reconciled at all: without that, ``roles.ts`` would have no
    coverage entry and this would fail as an unmeasured file."""
    result = evaluate(
        repo, {"src/roles.ts": frozenset({7, 8, 9, 17, 18, 19})}, fail_under=100.0
    )

    assert result.outcome is Outcome.PASS
    assert result.reason_code is None
    assert result.considered == 1
    assert result.covered == 6
    assert result.executable == 6
    assert result.pct == 100.0
    assert result.missing_lines == {}
    assert result.files_missing_coverage == ()


def test_an_uncovered_change_fails_and_names_the_lines(repo: Path):
    """``branchy.ts``'s ``return 'negative'`` (line 3) and its closing brace
    (line 4) are the two lines the probe's own test never reaches -- the
    artifact says so. A diff touching a covered line and those two is 1/3."""
    result = evaluate(repo, {"src/branchy.ts": frozenset({2, 3, 4})}, fail_under=90.0)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES
    assert result.covered == 1
    assert result.executable == 3
    assert result.missing_lines == {"src/branchy.ts": frozenset({3, 4})}
    assert result.files_with_unclassified_lines == ()


def test_the_same_change_passes_under_a_floor_it_actually_clears(repo: Path):
    """The paired must-succeed control for the failure above: the FLOOR is
    what refused, not the evaluation itself."""
    result = evaluate(repo, {"src/branchy.ts": frozenset({2, 3, 4})}, fail_under=33.0)

    assert result.outcome is Outcome.PASS
    assert result.pct == pytest.approx(100.0 / 3)


EVERY_LINE = {
    f"src/{name}": frozenset(range(1, 41))
    for name in ("roles.ts", "format.ts", "branchy.ts", "Badge.tsx", "hinted.ts")
}


@pytest.mark.parametrize("provider", BOTH)
def test_the_denominator_is_exactly_the_offered_lines_the_artifact_classified(
    repo: Path, provider: str
):
    """The pin the round-1 review's M2 asked for, replacing one that could
    not fail.

    The previous version asserted ``result.unclassified_lines == {}``, which
    ``evaluate.py:426`` makes structurally impossible to violate for an
    adapter declaring ``requires_span_attribution = False`` -- it held for
    every artifact, including a deliberately broken one, so it pinned
    nothing.

    This derives the expectation from the parsed profile -- every offered
    line that really is in some file's ``executed | missing`` -- and requires
    the evaluation's own denominator to equal it exactly. **What it pins,
    precisely:** that the EVALUATION counts neither more nor fewer lines than
    the profile classified. It is deliberately NOT a pin on the parser's own
    completeness: its expectation is read from the same profile, so a
    parser-side regression moves both sides together and this test would not
    notice (verified by mutating ``_paint`` to a start-line-only reduction --
    this test still passed). The parser's classification is pinned against
    hand-derived ground truth in
    ``test_coverage_istanbul_real_fixtures.py``'s own literal sets, which is
    where that mutation IS caught. Both are needed; neither substitutes for
    the other."""
    profile = rebased_profile(repo, provider)
    by_repo_path = {
        key.rsplit("/src/", 1)[0] and f"src/{key.rsplit('/src/', 1)[1]}": record
        for key, record in profile.files.items()
    }

    expected = 0
    for path, offered in EVERY_LINE.items():
        record = by_repo_path.get(path)
        if record is not None:
            expected += len(offered & (record.executed | record.missing))

    result = evaluate(repo, EVERY_LINE, fail_under=0.0, provider=provider)

    assert expected > 0, "a vacuous expectation would prove nothing"
    assert result.executable == expected
    assert result.covered == sum(
        len(offered & by_repo_path[path].executed)
        for path, offered in EVERY_LINE.items()
        if path in by_repo_path
    )
    assert result.considered == 5


@pytest.mark.parametrize("provider", BOTH)
def test_rule_3b_is_never_reached_because_the_adapter_declares_it_off(
    repo: Path, provider: str
):
    """Kept, but demoted to what it actually proves. ``unclassified_lines``
    is empty for this adapter BY CONSTRUCTION, not because the artifact left
    nothing unattributed -- it did leave lines unattributed (see
    ``test_coverage_istanbul_real_fixtures.py``'s own unattributed-line pin),
    and those silently take rule 4. This asserts only the structural fact,
    and its docstring is the correction: it is not evidence about the
    parser's completeness."""
    result = evaluate(repo, EVERY_LINE, fail_under=0.0, provider=provider)

    assert result.unclassified_lines == {}
    assert result.files_with_unclassified_lines == ()
    assert JavaScriptAdapter().requires_span_attribution is False


@pytest.mark.parametrize("provider", BOTH)
def test_a_fully_covered_change_passes_under_either_provider(
    repo: Path, provider: str
):
    """``roles.ts``'s object literal (lines 7-11) and its ``hasRole`` body are
    exercised by the probe's own test under both providers -- the one shape
    where the two agree exactly, so it is the honest cross-provider control
    for the parametrization above."""
    result = evaluate(
        repo, {"src/roles.ts": frozenset({7, 8, 9})}, fail_under=100.0, provider=provider
    )

    assert result.outcome is Outcome.PASS
    assert result.covered == 3
    assert result.executable == 3


def test_a_changed_test_file_is_not_judged_at_all(repo: Path):
    """A test file's own changed lines contribute to neither the numerator
    nor the denominator -- and the probe carries all three naming
    conventions, so this covers each."""
    result = evaluate(
        repo,
        {
            "src/__tests__/roles.test.ts": frozenset({1, 2, 3}),
            "src/branchy.test.ts": frozenset({1, 2}),
            "src/Badge.spec.tsx": frozenset({1, 2}),
        },
        fail_under=100.0,
    )

    assert result.considered == 0
    assert result.executable == 0
    assert result.outcome is Outcome.PASS


def test_a_changed_declaration_file_is_the_nocode_case_not_a_gap(repo: Path):
    """``types.d.ts`` is real, changed, adapter-recognised source that no
    coverage tool reports. ``has_executable_code`` answering ``False`` is what
    keeps it out of ``files_missing_coverage`` -- it is still COUNTED as
    considered, so a 0/0 pass explains itself."""
    result = evaluate(repo, {"src/types.d.ts": frozenset({3, 4})}, fail_under=100.0)

    assert result.considered == 1
    assert result.executable == 0
    assert result.files_missing_coverage == ()
    assert result.outcome is Outcome.PASS


def test_a_changed_source_file_the_artifact_never_measured_is_a_real_gap(repo: Path):
    """The other half of the NoCode asymmetry: a new module with real code
    and no coverage entry is reported, never silently excused."""
    (repo / "src" / "unmeasured.ts").write_text(
        "export function helper(value: number): number {\n  return value + 1\n}\n",
        encoding="utf-8",
    )
    result = evaluate(repo, {"src/unmeasured.ts": frozenset({1, 2})}, fail_under=100.0)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES
    assert result.files_missing_coverage == ("src/unmeasured.ts",)
    assert result.missing_lines == {"src/unmeasured.ts": frozenset({1, 2})}


def test_changed_files_in_excluded_directories_are_invisible(repo: Path):
    """``node_modules``/``dist``/``coverage`` hold generated content a
    coverage artifact can never meaningfully measure; a changed file under
    any of them is not considered at all, even though it satisfies the source
    globs and sits under the declared source root."""
    for directory in ("node_modules", "dist", "coverage"):
        (repo / "src" / directory).mkdir()
        (repo / "src" / directory / "bundle.js").write_text("var x = 1\n", "utf-8")

    result = evaluate(
        repo,
        {
            f"src/{directory}/bundle.js": frozenset({1})
            for directory in ("node_modules", "dist", "coverage")
        },
        fail_under=100.0,
    )

    assert result.considered == 0
    assert result.files_missing_coverage == ()


def test_a_key_naming_a_file_outside_the_repository_never_matches_a_change(repo: Path):
    """Real istanbul artifacts routinely carry absolute keys for files
    outside the project (a linked package, a dependency). The core leaves such
    a key absolute, so it can never collide with a repo-relative changed
    path -- proven here by adding one and showing the verdict is unchanged."""
    raw = json.loads(V8_ARTIFACT.read_text(encoding="utf-8"))
    rebased = {
        f"{repo}/{key.split('/probe-js/', 1)[1]}": record
        for key, record in raw.items()
    }
    outsider = next(iter(rebased.values()))
    rebased["/opt/vendor/linked-package/src/roles.ts"] = outsider
    profile = load_coverage_profile(
        json.dumps(rebased), declared_format="coverage-istanbul-json"
    )

    result = evaluate_coverage(
        added=AddedLines(
            by_file=MappingProxyType({"src/roles.ts": frozenset({7, 8, 9})})
        ),
        profile=profile,
        adapter=JavaScriptAdapter(),
        repo_top=repo,
        project_root=repo,
        source_root_paths=(repo / "src",),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=lambda path: (repo / path).read_text(encoding="utf-8"),
    )

    assert result.outcome is Outcome.PASS
    assert result.covered == 3


def test_capabilities_reach_the_evaluation_as_unavailable(repo: Path):
    """The two honest ``None``s (A-343/A-344) arrive as
    ``"unavailable"`` on the evaluation a verdict is built from -- never as
    ``"reported"`` with a fabricated zero."""
    result = evaluate(repo, {"src/roles.ts": frozenset({7})}, fail_under=100.0)

    assert result.exclusion_capability == "unavailable"
    assert result.branch_capability == "unavailable"
    assert result.branches_covered == 0
    assert result.branches_total == 0
