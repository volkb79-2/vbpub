"""A-392's guard: an adapter that requires statement attribution may not be
handed a profile that never received any.

The guard exists because the failure it catches is otherwise INVISIBLE. An
uncorrected Go coverprofile parses cleanly, produces well-formed line sets and
yields a plausible percentage -- it is simply about the wrong lines. That is
AGENTS.md's masked default in its purest form: harmless in every context that
happens to run the correction, and reachable only on the one path that skips
it. "The runner remembers" is precisely the check that cannot fail, so these
tests are what make it one.

Both directions are asserted, per AGENTS.md §"A check is only as strong as
what it actually compares": the alarming condition is not reported as benign
(an unattributed profile refuses) AND the benign one is not reported as
alarming (an attributed profile, and an adapter that requires nothing, both
evaluate normally).
"""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

from assay.coverage_parsers.model import CoverageBlock, CoverageProfile, FileCoverage
from assay.errors import AssayError, Outcome, ReasonCode
from assay.diff import AddedLines
from assay.evaluate import evaluate_coverage, evaluate_targets


@dataclass(frozen=True, kw_only=True)
class _BlockAdapter:
    """The minimum surface both evaluate entry points touch, with
    ``requires_statement_attribution`` as the ONE parameter under test.

    Deliberately not :class:`~assay.adapters.go.GoAdapter`: this guard is
    language-free core behaviour (``evaluate.py`` never names a language), so
    proving it through Go would prove it for Go only and would drag a real
    toolchain requirement into a pure unit test.
    """

    name: str = "blocky"
    source_globs: tuple[str, ...] = ("*.blk",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = False
    requires_statement_attribution: bool = True
    external_tools: tuple[str, ...] = ()

    def is_test_path(self, rel_path: str) -> bool:
        return False

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return True

    def normalize_coverage_key(self, key: str) -> str:
        return key

    def statement_spans(self, text: str):
        return None

    def statement_blocks(self, repo_top, rel_paths, *, remaining=None):
        raise AssertionError(
            "evaluate must never invoke the oracle itself -- the correction "
            "is the runner's step, and evaluate only checks that it happened"
        )


def _profile(*, attributed: bool) -> CoverageProfile:
    return CoverageProfile(
        files=MappingProxyType(
            {
                "a.blk": FileCoverage(
                    executed=frozenset({4}),
                    missing=frozenset(),
                    excluded=None,
                    branches=None,
                    blocks=(
                        CoverageBlock(
                            start_line=3,
                            start_col=22,
                            end_line=7,
                            end_col=2,
                            num_stmts=2,
                            count=1,
                        ),
                    ),
                )
            }
        ),
        statement_attributed=attributed,
    )


def _added() -> AddedLines:
    return AddedLines(by_file=MappingProxyType({"a.blk": frozenset({4})}))


def _write(tmp_path: Path) -> Path:
    (tmp_path / "a.blk").write_text("x\n" * 10, encoding="utf-8")
    return tmp_path


def test_evaluate_coverage_refuses_an_unattributed_profile(tmp_path: Path):
    repo_top = _write(tmp_path)

    with pytest.raises(AssayError) as caught:
        evaluate_coverage(
            added=_added(),
            profile=_profile(attributed=False),
            adapter=_BlockAdapter(),
            repo_top=repo_top,
            project_root=repo_top,
            source_root_paths=[repo_top],
            fail_under=100.0,
            allow_excluded=False,
            read_source_text=lambda path: "x\n" * 10,
        )

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    # The message must name the CAUSE, not just the symptom: a consumer
    # reading this needs to know the oracle never ran, not that "something
    # was false".
    assert "statement_attributed=False" in str(caught.value)
    assert "statement_blocks" in str(caught.value)


def test_evaluate_targets_refuses_the_same_profile_identically(tmp_path: Path):
    """The SAME refusal on the whole-target path. Both modes judge the same
    profile, so a guard on one only would leave the other publishing exactly
    the verdict A-217 exists to prevent -- and `judge.mode` is a lane's own
    declaration, so which path runs is a consumer's choice, not assay's."""
    repo_top = _write(tmp_path)

    with pytest.raises(AssayError) as caught:
        evaluate_targets(
            profile=_profile(attributed=False),
            adapter=_BlockAdapter(),
            repo_top=repo_top,
            project_root=repo_top,
            targets=["a.blk"],
            source_root_paths=[repo_top],
            fail_under=100.0,
            allow_excluded=False,
        )

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_an_attributed_profile_passes_the_guard(tmp_path: Path):
    """The legitimate state, constructed before the refusal ships (AGENTS.md:
    "a refusal that blocks work must have its legitimate state constructed
    before it ships"). Same adapter, same profile bytes, one flag flipped."""
    repo_top = _write(tmp_path)

    result = evaluate_coverage(
        added=_added(),
        profile=_profile(attributed=True),
        adapter=_BlockAdapter(),
        repo_top=repo_top,
        project_root=repo_top,
        source_root_paths=[repo_top],
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=lambda path: "x\n" * 10,
    )

    assert result.outcome is Outcome.PASS
    assert result.covered == 1
    assert result.executable == 1


def test_an_adapter_requiring_nothing_is_unaffected_by_an_unattributed_profile(
    tmp_path: Path,
):
    """The control that keeps the guard from being vacuous: every existing
    adapter (python/javascript/sql) declares ``False``, and their profiles are
    ``statement_attributed=False`` too -- so if the guard read the flag alone
    rather than the PAIR, this test goes red and every Python lane refuses."""
    repo_top = _write(tmp_path)

    result = evaluate_coverage(
        added=_added(),
        profile=_profile(attributed=False),
        adapter=_BlockAdapter(requires_statement_attribution=False),
        repo_top=repo_top,
        project_root=repo_top,
        source_root_paths=[repo_top],
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=lambda path: "x\n" * 10,
    )

    assert result.outcome is Outcome.PASS
