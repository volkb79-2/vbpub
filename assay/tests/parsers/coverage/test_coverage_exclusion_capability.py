"""P21 work item 6 / A-008 / A-183 --
:func:`assay.coverage.derive_exclusion_capability`.

The claim under attack: **"this format cannot report exclusions" and "this
format reported none" are different facts, and the artifact must be able to
say which.**

A-008 kept that distinction upstream (``FileCoverage.excluded`` is
``frozenset | None``), but :func:`assay.evaluate.evaluate_coverage`
intersected changed lines with an empty set in BOTH cases, so a Go lane's
structural silence and a Python lane's genuinely-clean run serialised
identically -- A-O16, recorded as diagnostic-only loss and deferred to this
migration.

The wrong implementations this module exists to catch, both named in the
JIT review:

* infer ``"unavailable"`` whenever the exclusion detail happens to be empty
  (which would report every clean Python lane as blind);
* infer capability from a FORMAT NAME (a registry key is a declaration; the
  parsed records are the fact).
"""

from __future__ import annotations

import pytest

from assay.coverage import derive_exclusion_capability
from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.errors import AssayError, Outcome, ReasonCode


def _profile(**files: FileCoverage) -> CoverageProfile:
    return CoverageProfile(files=dict(files))


def _reported(excluded: frozenset[int] | None = frozenset()) -> FileCoverage:
    return FileCoverage(
        executed=frozenset({1}), missing=frozenset({2}), excluded=excluded
    )


def _unavailable() -> FileCoverage:
    return FileCoverage(executed=frozenset({1}), missing=frozenset({2}), excluded=None)


def test_a_format_that_reports_exclusions_is_reported_even_when_it_found_none():
    """The case a "empty means unavailable" shortcut gets wrong. coverage.py
    on a file with no pragmas reports an EMPTY exclusion set, which is a
    measurement, not silence."""
    profile = _profile(a=_reported(), b=_reported())

    assert derive_exclusion_capability(profile) == "reported"


def test_a_format_that_reports_exclusions_and_found_some_is_also_reported():
    profile = _profile(a=_reported(frozenset({7})), b=_reported())

    assert derive_exclusion_capability(profile) == "reported"


def test_a_format_that_cannot_express_exclusions_is_unavailable():
    """Go's coverprofile has no exclusion channel at all, so every record
    says ``None`` and the artifact must say so rather than claiming a clean
    measurement."""
    profile = _profile(a=_unavailable(), b=_unavailable())

    assert derive_exclusion_capability(profile) == "unavailable"


def test_capability_is_not_inferred_from_the_format_name():
    """Same derivation for any profile shape: this function never sees a
    format key, so a registry declaration cannot override the parsed
    evidence."""
    assert derive_exclusion_capability(_profile(only=_reported())) == "reported"
    assert derive_exclusion_capability(_profile(only=_unavailable())) == "unavailable"


def test_a_mixed_profile_is_unreadable_rather_than_a_majority_vote():
    """One artifact is one tool's output. Records disagreeing about whether
    that tool tracks exclusions at all cannot be read without inventing a
    precedence rule the lane never declared -- the same refusal
    ``evaluate_coverage`` already makes for two raw keys normalising onto
    one repository path."""
    profile = _profile(a=_reported(), b=_unavailable())

    with pytest.raises(AssayError) as caught:
        derive_exclusion_capability(profile)

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_the_mixed_refusal_does_not_depend_on_record_order():
    """A majority vote or a first-record-wins shortcut would be sensitive to
    which record came first; this refuses either way."""
    with pytest.raises(AssayError):
        derive_exclusion_capability(_profile(a=_unavailable(), b=_reported()))
    with pytest.raises(AssayError):
        derive_exclusion_capability(_profile(a=_reported(), b=_unavailable()))
