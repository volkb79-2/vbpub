"""O2 (half 1/2) — the SAME evaluation reaches the same result for a
non-Python, synthetic language, using only the adapter's own hooks.

The negative this defends (verbatim): *adding a .py filter, AST import, or
default adapter makes the synthetic-language fixture disappear or the
unknown language silently select Python.* This module proves the first
half: nothing in ``assay.evaluate`` special-cases an extension, a directory
name, or a syntax family. ``test_registry.py`` proves the second half — an
unknown declared language is refused, never silently mapped to anything.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest
from conftest import FakeAdapter

from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import AssayError, Outcome, ReasonCode
from assay.evaluate import evaluate_coverage

REPO_TOP = Path("/repo")


def _evaluate(added, profile, adapter, *, source_texts=None, allow_excluded=False):
    texts = source_texts or {}

    def read_source_text(path: str) -> str:
        return texts[path]

    return evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=REPO_TOP,
        project_root=REPO_TOP,
        source_root_paths=(REPO_TOP / "pkg",),
        fail_under=100.0,
        allow_excluded=allow_excluded,
        read_source_text=read_source_text,
    )


def test_a_non_python_extension_and_synthetic_syntax_reaches_the_same_result():
    """The identical four-way union as test_evaluate_four_way_union.py's own
    ACCEPT case, but for a file whose extension and content are not, and
    could not be mistaken for, Python."""
    adapter = FakeAdapter(source_globs=("*.zzz",))
    added = AddedLines(
        by_file=MappingProxyType({"pkg/mod.zzz": frozenset({2, 3})})
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg/mod.zzz": FileCoverage(
                    executed=frozenset({2}),
                    missing=frozenset({3}),
                    excluded=frozenset(),
                )
            }
        )
    )
    result = _evaluate(added, profile, adapter)

    assert result.covered == 1
    assert result.executable == 2
    assert result.pct == 50.0
    assert result.outcome is Outcome.FAIL


def test_a_file_extension_the_adapter_does_not_recognise_is_invisible():
    """A changed file that does not match ANY of the adapter's source_globs
    contributes nothing at all -- proving the boundary is the adapter's own
    declared globs, never a hardcoded suffix list."""
    adapter = FakeAdapter(source_globs=("*.zzz",))
    added = AddedLines(
        by_file=MappingProxyType({"pkg/notes.txt": frozenset({1, 2})})
    )
    profile = CoverageProfile(files=MappingProxyType({}))
    result = _evaluate(added, profile, adapter)

    assert result.considered == 0
    assert result.executable == 0
    assert result.pct == 100.0
    assert result.outcome is Outcome.PASS


def test_a_test_path_per_the_adapters_own_rule_is_excluded():
    """is_test_path is consulted, not a hardcoded `test_*`/`_test.py` guess:
    FakeAdapter's own marker (`_test.zzz`) is what excludes it here."""
    adapter = FakeAdapter()
    added = AddedLines(
        by_file=MappingProxyType({"pkg/mod_test.zzz": frozenset({2, 3})})
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg/mod_test.zzz": FileCoverage(
                    executed=frozenset(), missing=frozenset({2, 3}), excluded=frozenset()
                )
            }
        )
    )
    result = _evaluate(added, profile, adapter)

    assert result.considered == 0
    assert result.executable == 0
    assert result.outcome is Outcome.PASS  # would be FAIL if the test file counted


def test_an_excluded_directory_name_is_skipped_entirely():
    adapter = FakeAdapter(excluded_dir_names=frozenset({"vendor"}))
    added = AddedLines(
        by_file=MappingProxyType({"pkg/vendor/mod.zzz": frozenset({2, 3})})
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg/vendor/mod.zzz": FileCoverage(
                    executed=frozenset(), missing=frozenset({2, 3}), excluded=frozenset()
                )
            }
        )
    )
    result = _evaluate(added, profile, adapter)

    assert result.considered == 0
    assert result.outcome is Outcome.PASS


def test_a_file_outside_every_source_root_is_ignored():
    adapter = FakeAdapter()
    added = AddedLines(
        by_file=MappingProxyType({"docs/notes.zzz": frozenset({1})})
    )
    profile = CoverageProfile(files=MappingProxyType({}))
    result = _evaluate(added, profile, adapter)

    assert result.considered == 0
    assert result.outcome is Outcome.PASS


def test_a_sibling_directory_sharing_the_source_roots_name_prefix_is_not_matched():
    """`pkg` must not match `pkg_evil` -- the same boundary discipline
    measurability.check_dirty_tree already applies, proven here for
    evaluate_coverage's own source-root matching."""
    adapter = FakeAdapter()
    added = AddedLines(
        by_file=MappingProxyType({"pkg_evil/mod.zzz": frozenset({1, 2})})
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg_evil/mod.zzz": FileCoverage(
                    executed=frozenset(), missing=frozenset({1, 2}), excluded=frozenset()
                )
            }
        )
    )
    result = _evaluate(added, profile, adapter)

    assert result.considered == 0
    assert result.outcome is Outcome.PASS  # would be FAIL if the sibling matched


def test_normalize_coverage_key_reconciles_a_language_specific_prefix():
    """The coverage artifact's own key spelling (a stand-in for Go's module
    path) is stripped by the adapter's own hook before matching against the
    diff's path -- proving the core never assumes identity."""
    adapter = FakeAdapter(key_prefix="zzzmod::")
    added = AddedLines(
        by_file=MappingProxyType({"pkg/mod.zzz": frozenset({2, 3})})
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "zzzmod::pkg/mod.zzz": FileCoverage(
                    executed=frozenset({2, 3}), missing=frozenset(), excluded=frozenset()
                )
            }
        )
    )
    result = _evaluate(added, profile, adapter)

    assert result.covered == 2
    assert result.executable == 2
    assert result.outcome is Outcome.PASS


def test_a_missing_key_prefix_leaves_the_file_unmatched():
    """The mirror case: WITHOUT normalisation the same two paths never
    align, so the file is treated as missing from coverage entirely."""
    adapter = FakeAdapter(key_prefix="")
    added = AddedLines(
        by_file=MappingProxyType({"pkg/mod.zzz": frozenset({2, 3})})
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "zzzmod::pkg/mod.zzz": FileCoverage(
                    executed=frozenset({2, 3}), missing=frozenset(), excluded=frozenset()
                )
            }
        )
    )
    result = _evaluate(
        added, profile, adapter, source_texts={"pkg/mod.zzz": "some code\n"}
    )

    assert result.files_missing_coverage == ("pkg/mod.zzz",)
    assert result.outcome is Outcome.FAIL


# --- B006: project_root a strict subdirectory of repo_top (nested projects) --


def test_a_nested_project_root_prepends_the_project_prefix_before_matching():
    """B006's own fix, at the unit level: a real coverage tool always runs
    with ``cwd = project_root`` (:mod:`assay.runner`'s own ``evaluate_r1``),
    so its raw key is PROJECT-relative (``"pkg/mod.zzz"``), never the
    repo-relative spelling ``added.by_file`` uses (``"proj/pkg/mod.zzz"``).
    Before the fix, ``evaluate_coverage`` had no ``project_root`` parameter
    at all and these two spellings could never meet -- mirrors
    ``test_evaluate_whole_target.py``'s own nested-project proof for
    ``evaluate_targets``, here for the changed-lines mode WI-5's real
    reproduction actually hit."""
    adapter = FakeAdapter()
    added = AddedLines(
        by_file=MappingProxyType({"proj/pkg/mod.zzz": frozenset({2, 3})})
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg/mod.zzz": FileCoverage(
                    executed=frozenset({2, 3}), missing=frozenset(), excluded=frozenset()
                )
            }
        )
    )
    result = evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=REPO_TOP,
        project_root=REPO_TOP / "proj",
        source_root_paths=(REPO_TOP / "proj" / "pkg",),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=lambda path: (_ for _ in ()).throw(
            AssertionError(f"unexpected read_source_text call for {path!r}")
        ),
    )

    assert result.covered == 2
    assert result.executable == 2
    assert result.outcome is Outcome.PASS


def test_a_nested_project_root_never_double_prefixes_an_already_repo_relative_key():
    """The double-prefixing trap the fix above must not fall into: a raw key
    that ALREADY happens to carry the repo-relative spelling
    (``"proj/pkg/mod.zzz"`` -- never what a real tool run from
    ``cwd=project_root`` actually emits, but not something the join can tell
    apart from the project-relative spelling by inspection alone) gets
    ``project_prefix`` prepended a second time (``"proj/proj/pkg/mod.zzz"``)
    and so simply does not match -- proving the join always trusts
    *project_root* being the tool's own cwd, never guesses from the key's
    own text."""
    adapter = FakeAdapter()
    added = AddedLines(
        by_file=MappingProxyType({"proj/pkg/mod.zzz": frozenset({2, 3})})
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "proj/pkg/mod.zzz": FileCoverage(
                    executed=frozenset({2, 3}), missing=frozenset(), excluded=frozenset()
                )
            }
        )
    )
    result = evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=REPO_TOP,
        project_root=REPO_TOP / "proj",
        source_root_paths=(REPO_TOP / "proj" / "pkg",),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=lambda path: "some code\n",
    )

    assert result.files_missing_coverage == ("proj/pkg/mod.zzz",)
    assert result.outcome is Outcome.FAIL


def test_a_project_root_not_contained_by_repo_top_is_refused():
    """``evaluate_coverage`` gains this refusal for the first time (B006):
    previously it had no ``project_root`` parameter to validate at all.
    Mirrors ``evaluate_targets``'s own identical, separately-raised
    ``test_a_project_root_not_contained_by_repo_top_is_refused`` in
    ``test_evaluate_whole_target.py`` -- both entry points share the one
    ``_project_prefix`` implementation, so this pins that the SHARED guard
    is actually reachable from THIS entry point too, not merely from the
    other one."""
    added = AddedLines(by_file=MappingProxyType({}))
    profile = CoverageProfile(files=MappingProxyType({}))
    with pytest.raises(AssayError) as exc:
        evaluate_coverage(
            added=added,
            profile=profile,
            adapter=FakeAdapter(),
            repo_top=REPO_TOP,
            project_root=Path("/elsewhere"),
            source_root_paths=(REPO_TOP / "pkg",),
            fail_under=100.0,
            allow_excluded=False,
            read_source_text=lambda path: "unused\n",
        )
    assert exc.value.outcome is Outcome.ERROR
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "not contained by its own repository top" in str(exc.value)


# --- B006: absolute coverage-artifact keys (coverage.py's own fallback) -----


def test_an_absolute_key_under_repo_top_resolves_to_its_repo_relative_identity():
    """Real ``coverage.py`` falls back to an ABSOLUTE path when the measured
    file sits outside the process's own ``cwd``-relative tree (confirmed
    empirically against real ``coverage.py`` 7.15.3: a ``--cov`` target
    outside the process's own cwd reports an absolute path with no special
    configuration). ``_to_repo_relative_key`` must resolve such a key
    against *repo_top* directly rather than nonsensically joining
    *project_prefix* onto it."""
    adapter = FakeAdapter()
    added = AddedLines(
        by_file=MappingProxyType({"pkg/mod.zzz": frozenset({2, 3})})
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                str(REPO_TOP / "pkg" / "mod.zzz"): FileCoverage(
                    executed=frozenset({2, 3}), missing=frozenset(), excluded=frozenset()
                )
            }
        )
    )
    result = _evaluate(added, profile, adapter)

    assert result.covered == 2
    assert result.executable == 2
    assert result.outcome is Outcome.PASS


def test_an_absolute_key_outside_repo_top_is_left_inert_never_a_crash_or_collision():
    """The other half of the absolute-key case: a key that does NOT resolve
    under *repo_top* at all (a stdlib module, a dependency outside the
    repository entirely) is left UNCHANGED rather than raised on -- it can
    never collide with a genuine lookup key (``added.by_file`` and
    :func:`~assay.evaluate.evaluate_targets`'s own resolved target paths are
    always relative by construction), so the changed file it does NOT
    describe is correctly reported missing-from-coverage instead of the
    evaluation crashing or silently matching the wrong entry."""
    adapter = FakeAdapter()
    added = AddedLines(
        by_file=MappingProxyType({"pkg/mod.zzz": frozenset({2, 3})})
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "/outside/of/the/repo/unrelated.zzz": FileCoverage(
                    executed=frozenset({1}), missing=frozenset(), excluded=frozenset()
                )
            }
        )
    )
    result = _evaluate(
        added, profile, adapter, source_texts={"pkg/mod.zzz": "some code\n"}
    )

    assert result.files_missing_coverage == ("pkg/mod.zzz",)
    assert result.outcome is Outcome.FAIL


# --- has_executable_code: the NoCode distinction for files missing coverage ---


def test_a_considered_file_missing_from_coverage_with_real_code_is_flagged():
    adapter = FakeAdapter()
    added = AddedLines(
        by_file=MappingProxyType({"pkg/mod.zzz": frozenset({2, 3})})
    )
    profile = CoverageProfile(files=MappingProxyType({}))  # no entry at all
    result = _evaluate(
        added,
        profile,
        adapter,
        source_texts={"pkg/mod.zzz": "def real_code(): return 1\n"},
    )

    assert result.files_missing_coverage == ("pkg/mod.zzz",)
    assert result.missing_lines == {"pkg/mod.zzz": frozenset({2, 3})}
    assert result.executable == 2
    assert result.covered == 0
    assert result.considered == 1
    assert result.outcome is Outcome.FAIL


def test_a_considered_file_missing_from_coverage_with_no_code_is_not_flagged():
    """srdm's NoCode bucket: absence from the coverage artifact is expected,
    not a gap, when the adapter says the file has no instrumentable code."""
    adapter = FakeAdapter()
    added = AddedLines(
        by_file=MappingProxyType({"pkg/mod.zzz": frozenset({2, 3})})
    )
    profile = CoverageProfile(files=MappingProxyType({}))
    result = _evaluate(
        added,
        profile,
        adapter,
        source_texts={"pkg/mod.zzz": "# NO-CODE here, just a comment\n"},
    )

    assert result.files_missing_coverage == ()
    assert result.missing_lines == {}
    assert result.executable == 0
    assert result.considered == 1  # still considered -- just contributes 0/0
    assert result.outcome is Outcome.PASS


def test_has_executable_code_is_never_consulted_for_a_file_coverage_did_measure():
    """A considered file WITH a coverage entry never reaches
    has_executable_code/read_source_text at all -- the coverage artifact's
    own executed/missing sets are already authoritative."""
    adapter = FakeAdapter()
    added = AddedLines(
        by_file=MappingProxyType({"pkg/mod.zzz": frozenset({2})})
    )
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg/mod.zzz": FileCoverage(
                    executed=frozenset({2}), missing=frozenset(), excluded=frozenset()
                )
            }
        )
    )

    def explode(path: str) -> str:
        raise AssertionError("read_source_text must not be called")

    result = evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=REPO_TOP,
        project_root=REPO_TOP,
        source_root_paths=(REPO_TOP / "pkg",),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=explode,
    )
    assert result.outcome is Outcome.PASS
