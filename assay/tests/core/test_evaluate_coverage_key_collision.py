"""O2 (P15) — two raw coverage-artifact keys normalizing to the same
repository path are refused, never resolved by "whichever came last".

sol's post-series review, finding 4: a bare dict comprehension keyed by
``adapter.normalize_coverage_key(raw_key)`` silently let the SECOND of two
colliding keys win, which means reversing their order in the artifact's own
JSON object could flip a verdict from PASS to FAIL on otherwise-identical
data. :func:`~conftest.FakeAdapter`'s ``key_prefix`` stands in for a real
adapter's module-path strip (Go's own case) — two raw keys, one with the
prefix and one without, collide on the exact same repository path.

Negative: restoring the dict-comprehension form makes both orderings below
pass (silently keeping one file's coverage data and discarding the other's)
instead of raising.
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


def _fail_on_read(path: str) -> str:
    raise AssertionError(f"read_source_text unexpectedly called for {path!r}")


def _evaluate(profile: CoverageProfile):
    adapter = FakeAdapter(key_prefix="pkg1/")
    added = AddedLines(by_file=MappingProxyType({"mod.zzz": frozenset({1})}))
    return evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=REPO_TOP,
        project_root=REPO_TOP,
        source_root_paths=(REPO_TOP,),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=_fail_on_read,
    )


_CLEAN = FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=frozenset())
_DIRTY = FileCoverage(executed=frozenset(), missing=frozenset({1}), excluded=frozenset())


def test_colliding_keys_raise_unreadable_artifact_in_declared_order():
    # "pkg1/mod.zzz" strips to "mod.zzz"; "mod.zzz" has no prefix to strip --
    # both normalize to the identical repository path "mod.zzz".
    profile = CoverageProfile(
        files=MappingProxyType({"pkg1/mod.zzz": _CLEAN, "mod.zzz": _DIRTY})
    )
    with pytest.raises(AssayError) as excinfo:
        _evaluate(profile)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "pkg1/mod.zzz" in str(excinfo.value)
    assert "mod.zzz" in str(excinfo.value)


def test_colliding_keys_raise_unreadable_artifact_in_reversed_order():
    # The exact property a last-key-wins dict comprehension violates: the
    # SAME two keys, reversed, must raise the SAME way -- never silently
    # accept whichever key happens to be declared second.
    profile = CoverageProfile(
        files=MappingProxyType({"mod.zzz": _DIRTY, "pkg1/mod.zzz": _CLEAN})
    )
    with pytest.raises(AssayError) as excinfo:
        _evaluate(profile)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_non_colliding_keys_evaluate_normally():
    profile = CoverageProfile(
        files=MappingProxyType({"pkg1/mod.zzz": _CLEAN, "pkg1/other.zzz": _DIRTY})
    )
    # "pkg1/other.zzz" normalizes to "other.zzz", distinct from "mod.zzz" --
    # no collision, evaluation proceeds and considers only the changed file.
    result = _evaluate(profile)
    assert result.outcome is Outcome.PASS
