"""The runner seam: where a block-based coverage profile is actually
corrected to statement truth before anything judges it (A-217/A-239/A-392).

`evaluate_coverage`'s guard (`test_evaluate_statement_attribution_guard.py`)
proves an uncorrected profile is REFUSED. These tests prove the other half --
that the correction happens at all, on both modes, through the adapter's own
hook and through the ONE key-resolution join the evaluator judges by. Without
them, "the guard passes" would be satisfiable by a runner that never runs the
oracle and a Go lane that therefore always refuses: green tests, zero
capability.

The adapter here is synthetic and its "oracle" returns canned blocks. That is
the honest boundary for this file: the subject under test is assay's wiring
(is the hook called, with which paths, and is its answer joined onto the right
records), and every claim about what the Go toolchain actually emits is proven
elsewhere against the real toolchain (A-334) --
`nyxloom-trove/carve-assets/P27-recarve/PROVENANCE.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

import pytest
from conftest import GitRepo, make_lane, make_r1_judge

from assay import runner
from assay.adapters.base import HelperInvocation, StatementBlockReport
from assay.coverage_parsers.model import CoverageBlock, CoverageProfile, FileCoverage
from assay.errors import Outcome, ReasonCode
from assay.statement_attribution import StatementBlock

#: One block spanning lines 3-7 with two statements. The naive expansion
#: `range(3, 8)` claims FIVE lines; the truth is two, on 4 and 6. The gap
#: between them is what every assertion below actually measures.
_BLOCK = CoverageBlock(
    start_line=3, start_col=22, end_line=7, end_col=2, num_stmts=2, count=1
)
_ORACLE_BLOCK = StatementBlock(
    start_line=3,
    start_col=22,
    end_line=7,
    end_col=2,
    num_stmts=2,
    stmt_lines=(4, 6),
)


@dataclass(frozen=True, kw_only=True)
class _OracleAdapter:
    name: str = "blocky"
    source_globs: tuple[str, ...] = ("*.blk",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = False
    requires_statement_attribution: bool = True
    external_tools: tuple[str, ...] = ()
    #: Recorded arguments, so a test can assert WHAT the runner asked for --
    #: repo-relative paths, and the repo top -- not merely that it asked.
    calls: list[tuple[Path, tuple[str, ...]]] = field(default_factory=list)
    blocks: tuple[StatementBlock, ...] = (_ORACLE_BLOCK,)
    report_is_none: bool = False
    report_omits_paths: bool = False
    key_prefix: str = ""

    def for_project(self, *, repo_top: Path, project_root: Path) -> "_OracleAdapter":
        return self

    def is_test_path(self, rel_path: str) -> bool:
        return False

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return True

    def normalize_coverage_key(self, key: str) -> str:
        if self.key_prefix and key.startswith(self.key_prefix):
            return key[len(self.key_prefix) :]
        return key

    def statement_spans(self, text: str):
        return None

    def statement_blocks(self, repo_top, rel_paths, *, remaining=None):
        self.calls.append((repo_top, tuple(rel_paths)))
        if self.report_is_none:
            return None
        return StatementBlockReport(
            blocks_by_path=(
                {}
                if self.report_omits_paths
                else {path: self.blocks for path in rel_paths}
            ),
            helper=HelperInvocation(
                tool="blocky-oracle",
                resolved_path="/usr/bin/blocky",
                identity="blocky version 1.2.3",
            ),
        )


def _profile(key: str = "pkg/mod.blk") -> CoverageProfile:
    """A profile in the shape `go_cover` emits: line sets that are the NAIVE
    expansion of the block, plus the block itself kept whole (A-390)."""
    return CoverageProfile(
        files=MappingProxyType(
            {
                key: FileCoverage(
                    executed=frozenset({3, 4, 5, 6, 7}),
                    missing=frozenset(),
                    excluded=None,
                    branches=None,
                    blocks=(_BLOCK,),
                )
            }
        )
    )


def _seed(git_repo: GitRepo) -> None:
    git_repo.write("pkg/mod.blk", "".join(f"line {n}\n" for n in range(1, 11)))
    git_repo.commit_all("seed the source the oracle reads")


def _whole_target_lane(git_repo: GitRepo):
    judge = make_r1_judge(
        source_root_paths=(git_repo.path / "pkg",),
        mode="whole_target",
        targets=("pkg/mod.blk",),
        base=None,
    )
    return make_lane(rigor=("R0", "R1"), judge=judge)


def test_the_runner_corrects_a_block_profile_before_the_verdict_is_computed(
    git_repo: GitRepo,
):
    """The headline: five lines claimed by the artifact, two judged.

    If the correction were skipped, `executable` would be 5 -- and the claim
    would be about function signatures and closing braces, which is exactly
    the wrong verdict A-217 exists to prevent."""
    _seed(git_repo)
    adapter = _OracleAdapter()

    claim = runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=adapter,
        profile=_profile(),
    )

    assert claim.status is Outcome.PASS
    assert claim.coverage is not None
    assert claim.coverage.executable == 2
    assert claim.coverage.covered == 2


def test_the_oracle_is_asked_in_repo_relative_paths_anchored_at_the_repo_top(
    git_repo: GitRepo,
):
    """The protocol's path contract, and A-397's one narrow amendment,
    asserted rather than assumed: path STRINGS stay in `git diff`'s spelling
    and the absolute anchor arrives separately."""
    _seed(git_repo)
    adapter = _OracleAdapter()

    runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=adapter,
        profile=_profile(),
    )

    assert len(adapter.calls) == 1
    repo_top, rel_paths = adapter.calls[0]
    assert rel_paths == ("pkg/mod.blk",)
    assert repo_top.is_absolute()
    assert (repo_top / rel_paths[0]).is_file()


def test_the_oracle_receives_the_key_the_evaluator_will_judge_not_the_raw_one(
    git_repo: GitRepo,
):
    """The reason `resolve_coverage_keys` is borrowed rather than re-derived
    (A-385/A-367: there is ONE join). The artifact spells this file with a
    module prefix the adapter strips; the file the oracle READS must be the
    file the evaluator JUDGES, and a private copy of the mapping in the
    runner is how those two silently diverge."""
    _seed(git_repo)
    adapter = _OracleAdapter(key_prefix="example.invalid/mod/")

    claim = runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=adapter,
        profile=_profile("example.invalid/mod/pkg/mod.blk"),
    )

    assert adapter.calls[0][1] == ("pkg/mod.blk",)
    assert claim.status is Outcome.PASS
    assert claim.coverage.executable == 2


def test_the_helper_identity_is_reported_exactly_once(git_repo: GitRepo):
    """`helpers[].identity` must record the toolchain that actually ran
    (A-395). The callback is the channel out of a frozen signature -- the
    same mechanism `on_base_resolved`/`on_added_resolved` already use -- and
    "exactly once" is the part worth pinning: a second call would mean the
    oracle ran twice for one lane."""
    _seed(git_repo)
    seen: list[HelperInvocation] = []

    runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=_OracleAdapter(),
        profile=_profile(),
        on_helper_invoked=seen.append,
    )

    assert len(seen) == 1
    assert seen[0].identity == "blocky version 1.2.3"


def test_an_oracle_disagreeing_with_the_profile_yields_a_payload_free_error(
    git_repo: GitRepo,
):
    """A-391's refusal, reaching the claim. The oracle reports a block the
    profile does not carry, which means the two were produced from different
    revisions -- and there is no safe direction to guess in, so the lane
    reports ERROR/UNREADABLE_ARTIFACT with NO coverage payload rather than a
    plausible percentage about the wrong lines (A-136)."""
    _seed(git_repo)
    stale = StatementBlock(
        start_line=3,
        start_col=22,
        end_line=9,  # the source moved; the profile did not
        end_col=2,
        num_stmts=2,
        stmt_lines=(4, 6),
    )

    claim = runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=_OracleAdapter(blocks=(stale,)),
        profile=_profile(),
    )

    assert claim.status is Outcome.ERROR
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert claim.coverage is None


def test_an_adapter_contradicting_its_own_declaration_is_refused(
    git_repo: GitRepo,
):
    """`requires_statement_attribution=True` with a hook returning `None`
    ("this adapter does no statement attribution") is a contradiction, not a
    fallback. Judging anyway would read block extents as statement truth,
    which is the one outcome this whole chain exists to make impossible."""
    _seed(git_repo)

    claim = runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=_OracleAdapter(report_is_none=True),
        profile=_profile(),
    )

    assert claim.status is Outcome.ERROR
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_a_report_omitting_a_requested_path_is_a_named_refusal_not_a_crash(
    git_repo: GitRepo,
):
    """`StatementBlockReport`'s contract is one entry per requested path. An
    adapter that violates it would otherwise raise `KeyError` straight past
    `evaluate_r1`'s `except AssayError` and crash the lane, producing no
    verdict at all -- which is strictly worse than a refusal, because a crash
    is not auditable."""
    _seed(git_repo)

    claim = runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=_OracleAdapter(report_omits_paths=True),
        profile=_profile(),
    )

    assert claim.status is Outcome.ERROR
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_an_adapter_requiring_no_attribution_never_reaches_the_oracle(
    git_repo: GitRepo,
):
    """The control. Every shipped adapter but Go declares `False`, and this
    is what proves the seam costs them nothing: the hook is not called, and
    a profile that was never corrected is judged exactly as before."""
    _seed(git_repo)
    adapter = _OracleAdapter(requires_statement_attribution=False)

    claim = runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=adapter,
        profile=_profile(),
    )

    assert adapter.calls == []
    assert claim.status is Outcome.PASS
    # Uncorrected: the naive five lines, which is precisely why an adapter
    # whose format is block-based must NOT declare False.
    assert claim.coverage.executable == 5


def test_a_profile_with_no_block_bearing_files_is_attributed_vacuously(
    git_repo: GitRepo,
):
    """`blocks is None` means "this format has no block concept at all"
    (A-390), which is a different fact from "block-based with zero blocks".
    Such a profile is already statement truth, so the oracle is not run --
    but the profile must still clear A-392's guard, or an adapter would be
    unable to judge a file its own format cannot describe in blocks."""
    _seed(git_repo)
    adapter = _OracleAdapter()
    line_based = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg/mod.blk": FileCoverage(
                    executed=frozenset({4, 6}),
                    missing=frozenset(),
                    excluded=None,
                    branches=None,
                    blocks=None,
                )
            }
        )
    )

    claim = runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=adapter,
        profile=line_based,
    )

    assert adapter.calls == []
    assert claim.status is Outcome.PASS
    assert claim.coverage.executable == 2
