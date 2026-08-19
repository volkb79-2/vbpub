"""Wave-1 §3.2/A-257 — :func:`assay.coverage.derive_branch_capability`.

Mirrors ``tests/test_coverage_exclusion_capability.py`` exactly, one field
over: the claim under attack is the identical one A-008/A-183 already
defends for exclusions -- **"this format cannot report branch arcs" and
"this format reported none" are different facts, and the artifact must be
able to say which.**

The wrong implementations this module exists to catch:

* infer ``"unavailable"`` whenever the branch detail happens to be empty
  (which would report every fully-covered or branch-free file as
  incapable);
* infer capability from a FORMAT NAME (a registry key is a declaration; the
  parsed records are the fact).
"""

from __future__ import annotations

import pytest

from assay.coverage import derive_branch_capability
from assay.coverage_parsers.model import BranchCoverage, CoverageProfile, FileCoverage
from assay.errors import AssayError, Outcome, ReasonCode


def _profile(**files: FileCoverage) -> CoverageProfile:
    return CoverageProfile(files=dict(files))


def _reported(by_line: dict[int, tuple[int, int]] | None = None) -> FileCoverage:
    return FileCoverage(
        executed=frozenset({1}),
        missing=frozenset({2}),
        excluded=None,
        branches=BranchCoverage(by_line=by_line if by_line is not None else {}),
    )


def _unavailable() -> FileCoverage:
    return FileCoverage(
        executed=frozenset({1}), missing=frozenset({2}), excluded=None, branches=None
    )


def test_a_format_that_reports_branches_is_reported_even_when_it_found_none():
    """The case an "empty means unavailable" shortcut gets wrong: a file
    with no branches at all in a branch-tracking artifact reports an EMPTY
    by_line mapping, which is a measurement, not silence."""
    profile = _profile(a=_reported(), b=_reported())

    assert derive_branch_capability(profile) == "reported"


def test_a_format_that_reports_branches_and_found_some_is_also_reported():
    # line 1 is `_reported`'s own `executed` line, so this arc is a
    # well-formed FileCoverage, not merely a well-formed BranchCoverage.
    profile = _profile(a=_reported({1: (1, 2)}), b=_reported())

    assert derive_branch_capability(profile) == "reported"


def test_a_format_that_cannot_express_branches_is_unavailable():
    """go-cover has no arc concept at all, so every record says ``None`` and
    the artifact must say so rather than claiming a clean measurement."""
    profile = _profile(a=_unavailable(), b=_unavailable())

    assert derive_branch_capability(profile) == "unavailable"


def test_capability_is_not_inferred_from_the_format_name():
    assert derive_branch_capability(_profile(only=_reported())) == "reported"
    assert derive_branch_capability(_profile(only=_unavailable())) == "unavailable"


def test_a_mixed_profile_is_unreadable_rather_than_a_majority_vote():
    """One artifact is one tool's output. Records disagreeing about whether
    that tool tracks branches at all cannot be read without inventing a
    precedence rule the lane never declared."""
    profile = _profile(a=_reported(), b=_unavailable())

    with pytest.raises(AssayError) as caught:
        derive_branch_capability(profile)

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_the_mixed_refusal_does_not_depend_on_record_order():
    with pytest.raises(AssayError):
        derive_branch_capability(_profile(a=_unavailable(), b=_reported()))
    with pytest.raises(AssayError):
        derive_branch_capability(_profile(a=_reported(), b=_unavailable()))
