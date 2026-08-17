"""Wave-1 §5 (A-260) -- B005's whole-target judge,
:func:`assay.evaluate.evaluate_targets` and its own per-target validator
:func:`assay.evaluate._resolve_whole_target`, proven directly against a real
filesystem (``tmp_path``) rather than only through :mod:`assay.runner`'s
integration path.

The claim this module defends: a target is a REGULAR FILE (never a
directory -- the exact hole this mode exists to close, per the task's own
override of an internally-contradictory carve reading), and a target absent
from the artifact or measured with zero executable lines refuses outright
(``NO_MEASUREMENT``/``TARGET_NOT_MEASURED``) rather than passing on nothing
-- the anti-vacuity guarantee B005 exists to give.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest
from conftest import FakeAdapter

from assay.coverage_parsers.model import BranchCoverage, CoverageProfile, FileCoverage
from assay.errors import AssayError, Outcome, ReasonCode
from assay.evaluate import _resolve_whole_target, evaluate_targets

ADAPTER = FakeAdapter()


def _profile(files: dict[str, FileCoverage]) -> CoverageProfile:
    return CoverageProfile(files=MappingProxyType(dict(files)))


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A real, disposable filesystem tree: `<repo>/pkg/mod.zzz` under one
    declared source root `pkg`, `repo_top == project_root` (the common
    case -- the mono-repo split is covered by its own dedicated test
    below).
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.zzz").write_text("code\n")
    return tmp_path


# --- evaluate_targets: outcome/reason-code precedence -------------------------


def test_a_fully_covered_target_passes(repo: Path):
    profile = _profile({
        "pkg/mod.zzz": FileCoverage(
            executed=frozenset({1, 2}), missing=frozenset(), excluded=frozenset(),
        ),
    })
    result = evaluate_targets(
        profile=profile, adapter=ADAPTER, repo_top=repo, project_root=repo,
        targets=("pkg/mod.zzz",), source_root_paths=(repo / "pkg",),
        fail_under=100.0, allow_excluded=False,
    )
    assert result.covered == 2
    assert result.executable == 2
    assert result.pct == 100.0
    assert result.considered == 1
    assert result.outcome is Outcome.PASS
    assert result.reason_code is None
    assert result.files_missing_coverage == ()
    assert result.unclassified_lines == {}
    assert result.files_with_unclassified_lines == ()


def test_a_missing_line_fails_as_uncovered_lines(repo: Path):
    profile = _profile({
        "pkg/mod.zzz": FileCoverage(
            executed=frozenset({1}), missing=frozenset({2}), excluded=frozenset(),
        ),
    })
    result = evaluate_targets(
        profile=profile, adapter=ADAPTER, repo_top=repo, project_root=repo,
        targets=("pkg/mod.zzz",), source_root_paths=(repo / "pkg",),
        fail_under=100.0, allow_excluded=False,
    )
    assert result.missing_lines == {"pkg/mod.zzz": frozenset({2})}
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES


def test_a_branch_deficit_alone_fails_as_uncovered_branches(repo: Path):
    profile = _profile({
        "pkg/mod.zzz": FileCoverage(
            executed=frozenset({1}), missing=frozenset(), excluded=frozenset(),
            branches=BranchCoverage(by_line={1: (1, 2)}),
        ),
    })
    result = evaluate_targets(
        profile=profile, adapter=ADAPTER, repo_top=repo, project_root=repo,
        targets=("pkg/mod.zzz",), source_root_paths=(repo / "pkg",),
        fail_under=100.0, allow_excluded=False,
    )
    assert result.missing_lines == {}
    assert result.branches_covered == 1
    assert result.branches_total == 2
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_BRANCHES
    assert result.missing_branch_lines == {"pkg/mod.zzz": frozenset({1})}


def test_a_disallowed_excluded_line_fails_even_at_100_percent(repo: Path):
    profile = _profile({
        "pkg/mod.zzz": FileCoverage(
            executed=frozenset({1}), missing=frozenset(), excluded=frozenset({2}),
        ),
    })
    result = evaluate_targets(
        profile=profile, adapter=ADAPTER, repo_top=repo, project_root=repo,
        targets=("pkg/mod.zzz",), source_root_paths=(repo / "pkg",),
        fail_under=100.0, allow_excluded=False,
    )
    assert result.pct == 100.0
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.EXCLUDED_LINES
    assert result.excluded_lines == {"pkg/mod.zzz": frozenset({2})}


def test_allow_excluded_opts_a_lane_back_into_passing(repo: Path):
    profile = _profile({
        "pkg/mod.zzz": FileCoverage(
            executed=frozenset({1}), missing=frozenset(), excluded=frozenset({2}),
        ),
    })
    result = evaluate_targets(
        profile=profile, adapter=ADAPTER, repo_top=repo, project_root=repo,
        targets=("pkg/mod.zzz",), source_root_paths=(repo / "pkg",),
        fail_under=100.0, allow_excluded=True,
    )
    assert result.outcome is Outcome.PASS
    # WHICH lines were excluded is still recorded regardless of permission.
    assert result.excluded_lines == {"pkg/mod.zzz": frozenset({2})}


def test_considered_counts_declared_targets_not_files(repo: Path):
    (repo / "pkg" / "other.zzz").write_text("code\n")
    profile = _profile({
        "pkg/mod.zzz": FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=frozenset()),
        "pkg/other.zzz": FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=frozenset()),
    })
    result = evaluate_targets(
        profile=profile, adapter=ADAPTER, repo_top=repo, project_root=repo,
        targets=("pkg/mod.zzz", "pkg/other.zzz"), source_root_paths=(repo / "pkg",),
        fail_under=100.0, allow_excluded=False,
    )
    assert result.considered == 2
    assert result.covered == 2
    assert result.executable == 2
    assert result.outcome is Outcome.PASS


# --- anti-vacuity: TARGET_NOT_MEASURED ----------------------------------------


def test_a_target_absent_from_the_artifact_refuses_target_not_measured(repo: Path):
    profile = _profile({})
    with pytest.raises(AssayError) as exc:
        evaluate_targets(
            profile=profile, adapter=ADAPTER, repo_top=repo, project_root=repo,
            targets=("pkg/mod.zzz",), source_root_paths=(repo / "pkg",),
            fail_under=100.0, allow_excluded=False,
        )
    assert exc.value.outcome is Outcome.NO_MEASUREMENT
    assert exc.value.reason_code is ReasonCode.TARGET_NOT_MEASURED
    assert "pkg/mod.zzz" in str(exc.value)
    assert "no entry at all" in str(exc.value)


def test_a_target_with_zero_executable_lines_refuses_target_not_measured(repo: Path):
    """The vacuous-pass hole B005 exists to close: a target present in the
    artifact but measuring nothing must not PASS at "100% of zero"."""
    profile = _profile({
        "pkg/mod.zzz": FileCoverage(executed=frozenset(), missing=frozenset(), excluded=frozenset()),
    })
    with pytest.raises(AssayError) as exc:
        evaluate_targets(
            profile=profile, adapter=ADAPTER, repo_top=repo, project_root=repo,
            targets=("pkg/mod.zzz",), source_root_paths=(repo / "pkg",),
            fail_under=100.0, allow_excluded=False,
        )
    assert exc.value.outcome is Outcome.NO_MEASUREMENT
    assert exc.value.reason_code is ReasonCode.TARGET_NOT_MEASURED
    assert "zero executable lines" in str(exc.value)


# --- normalized-key collision, reachable from this call site too -------------


def test_a_normalized_key_collision_refuses_unreadable_artifact(repo: Path):
    # FakeAdapter is a frozen, kw_only dataclass -- constructing it directly
    # with a non-default `key_prefix` is how a subclass would need `@dataclass`
    # of its own to achieve the same override.
    adapter = FakeAdapter(key_prefix="X-")
    profile = _profile({
        "X-pkg/mod.zzz": FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=frozenset()),
        "pkg/mod.zzz": FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=frozenset()),
    })
    with pytest.raises(AssayError) as exc:
        evaluate_targets(
            profile=profile, adapter=adapter, repo_top=repo, project_root=repo,
            targets=("pkg/mod.zzz",), source_root_paths=(repo / "pkg",),
            fail_under=100.0, allow_excluded=False,
        )
    assert exc.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


# --- the monorepo split: project_root a strict subdirectory of repo_top ------


def test_a_project_root_nested_under_repo_top_resolves_the_repo_relative_key(tmp_path: Path):
    """`repo_top` is the git top; `project_root` (where `judge.targets` is
    spelled from) is a subdirectory of it -- exactly assay's own case inside
    the vbpub monorepo (and B006's own CMRU shape). The profile's own RAW
    keys are PROJECT-relative -- the spelling a real coverage tool actually
    emits, since the lane's own command always runs with `cwd =
    project_root`, never `repo_top` (B006's own fix; a fixture spelling the
    key `"assay/pkg/mod.zzz"` here -- repo-top-relative already -- would
    silently hide the bug this test exists to catch: `_normalized_profile_
    files` must ITSELF prepend the project's own repo-relative prefix,
    never receive it pre-applied by the fixture)."""
    repo_top = tmp_path
    project_root = tmp_path / "assay"
    (project_root / "pkg").mkdir(parents=True)
    (project_root / "pkg" / "mod.zzz").write_text("code\n")

    profile = _profile({
        "pkg/mod.zzz": FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=frozenset()),
    })
    result = evaluate_targets(
        profile=profile, adapter=ADAPTER, repo_top=repo_top, project_root=project_root,
        targets=("pkg/mod.zzz",), source_root_paths=(project_root / "pkg",),
        fail_under=100.0, allow_excluded=False,
    )
    assert result.outcome is Outcome.PASS
    assert result.covered == 1


def test_a_project_root_nested_under_repo_top_never_double_prefixes_an_already_repo_relative_key(
    tmp_path: Path,
):
    """The double-prefixing trap the fix above must NOT fall into: if a
    coverage artifact's raw key already happens to spell the repo-top-relative
    path (never true for a real tool run from `project_root`, but not
    something `_normalized_profile_files` can tell apart from the
    project-relative spelling by inspection alone), naively prepending
    `project_prefix` a second time would produce `"assay/assay/pkg/mod.zzz"`
    and this lookup would MISS. This is not a defect: the project-relative
    key `"pkg/mod.zzz"` (the sibling test above) is the ONLY spelling
    `_to_repo_relative_key` is contracted to reconcile, because it is the
    ONLY spelling a real tool can actually produce here (`cwd = project_root`
    is a fact of `assay.runner`'s own command construction, not a guess) --
    this test pins that a key ALREADY carrying the prefix is simply not
    found under the doubled path, rather than silently matching by accident."""
    repo_top = tmp_path
    project_root = tmp_path / "assay"
    (project_root / "pkg").mkdir(parents=True)
    (project_root / "pkg" / "mod.zzz").write_text("code\n")

    profile = _profile({
        "assay/pkg/mod.zzz": FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=frozenset()),
    })
    with pytest.raises(AssayError) as exc:
        evaluate_targets(
            profile=profile, adapter=ADAPTER, repo_top=repo_top, project_root=project_root,
            targets=("pkg/mod.zzz",), source_root_paths=(project_root / "pkg",),
            fail_under=100.0, allow_excluded=False,
        )
    assert exc.value.outcome is Outcome.NO_MEASUREMENT
    assert exc.value.reason_code is ReasonCode.TARGET_NOT_MEASURED


def test_a_project_root_not_contained_by_repo_top_is_refused(tmp_path: Path):
    """A caller-supplied `repo_top`/`project_root` pair with no containment
    relation at all -- structurally impossible for a real git worktree, but
    a pure function must refuse it explicitly rather than let
    ``Path.relative_to`` propagate a bare, unclassified ``ValueError``."""
    repo_top = tmp_path / "repo"
    project_root = tmp_path / "elsewhere"
    repo_top.mkdir()
    project_root.mkdir()

    with pytest.raises(AssayError) as exc:
        evaluate_targets(
            profile=_profile({}), adapter=ADAPTER, repo_top=repo_top, project_root=project_root,
            targets=("pkg/mod.zzz",), source_root_paths=(project_root / "pkg",),
            fail_under=100.0, allow_excluded=False,
        )
    assert exc.value.outcome is Outcome.ERROR
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "not contained by its own repository top" in str(exc.value)


# --- _resolve_whole_target: every refusal gate, directly ---------------------


def test_resolve_whole_target_accepts_a_regular_source_file(repo: Path):
    result = _resolve_whole_target(
        "pkg/mod.zzz", adapter=ADAPTER, project_root=repo,
        source_root_paths=(repo / "pkg",),
    )
    assert result == "pkg/mod.zzz"


def test_resolve_whole_target_refuses_a_symlink(repo: Path):
    link = repo / "pkg" / "link.zzz"
    link.symlink_to(repo / "pkg" / "mod.zzz")
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "pkg/link.zzz", adapter=ADAPTER, project_root=repo,
            source_root_paths=(repo / "pkg",),
        )
    assert exc.value.outcome is Outcome.ERROR
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "symlink" in str(exc.value)


def test_resolve_whole_target_refuses_a_symlink_to_a_nonexistent_target():
    """`is_symlink` never raises for a dangling link, so this gate must run
    BEFORE existence is ever checked."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pkg").mkdir()
        dangling = root / "pkg" / "ghost.zzz"
        dangling.symlink_to(root / "pkg" / "does-not-exist.zzz")
        with pytest.raises(AssayError) as exc:
            _resolve_whole_target(
                "pkg/ghost.zzz", adapter=ADAPTER, project_root=root,
                source_root_paths=(root / "pkg",),
            )
        assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
        assert "symlink" in str(exc.value)


def test_resolve_whole_target_refuses_a_path_outside_every_source_root(repo: Path):
    (repo / "other").mkdir()
    (repo / "other" / "mod.zzz").write_text("code\n")
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "other/mod.zzz", adapter=ADAPTER, project_root=repo,
            source_root_paths=(repo / "pkg",),
        )
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "not contained beneath any declared source root" in str(exc.value)


def test_resolve_whole_target_refuses_a_directory():
    """The exact hole B005 exists to close: a directory target expanding to
    N files of which only one is measured would PASS while leaving the rest
    unjudged -- never accepted, regardless of what it contains."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pkg" / "sub").mkdir(parents=True)
        with pytest.raises(AssayError) as exc:
            _resolve_whole_target(
                "pkg/sub", adapter=ADAPTER, project_root=root,
                source_root_paths=(root / "pkg",),
            )
        assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
        assert "regular file, never a directory" in str(exc.value)


def test_resolve_whole_target_refuses_a_nonexistent_path(repo: Path):
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "pkg/nope.zzz", adapter=ADAPTER, project_root=repo,
            source_root_paths=(repo / "pkg",),
        )
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "does not exist as a regular file" in str(exc.value)


def test_resolve_whole_target_refuses_an_excluded_directory(repo: Path):
    (repo / "pkg" / "vendor").mkdir()
    (repo / "pkg" / "vendor" / "mod.zzz").write_text("code\n")
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "pkg/vendor/mod.zzz", adapter=ADAPTER, project_root=repo,
            source_root_paths=(repo / "pkg",),
        )
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "excluded directory" in str(exc.value)


def test_resolve_whole_target_refuses_a_non_source_glob():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pkg").mkdir()
        (root / "pkg" / "mod.txt").write_text("not source\n")
        with pytest.raises(AssayError) as exc:
            _resolve_whole_target(
                "pkg/mod.txt", adapter=ADAPTER, project_root=root,
                source_root_paths=(root / "pkg",),
            )
        assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
        assert "not adapter-recognised source" in str(exc.value)


def test_resolve_whole_target_refuses_a_test_path(repo: Path):
    (repo / "pkg" / "mod_test.zzz").write_text("code\n")
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "pkg/mod_test.zzz", adapter=ADAPTER, project_root=repo,
            source_root_paths=(repo / "pkg",),
        )
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "test path" in str(exc.value)
