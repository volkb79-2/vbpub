"""§3.1a/§3.3's coverage.py JSON branch rules, driven directly with
hand-authored JSON -- the malformed shapes and grammar edges no real
fixture witnesses (A-080's convention: invented fixtures for invented
shapes are fine; invented fixtures standing in for REAL tool output are the
failure ``tests/fixtures/coverage/`` exists to prevent).
"""

from __future__ import annotations

import json

import pytest

from assay.coverage import load_coverage_profile
from assay.errors import AssayError, Outcome, ReasonCode


def _assert_unreadable(document: dict) -> None:
    with pytest.raises(AssayError) as caught:
        load_coverage_profile(json.dumps(document), declared_format="coverage-py-json")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def _artifact(**files: dict) -> dict:
    return {"files": files}


def test_a_file_with_neither_branch_key_in_a_branch_tracking_artifact_is_empty():
    # has_branch_detail is ARTIFACT-level (another file supplies it); THIS
    # file lacks both keys entirely, which is legal -- not "carries only
    # one", and not malformed.
    document = _artifact(
        detailed={
            "executed_lines": [1],
            "missing_lines": [],
            "executed_branches": [[1, 2]],
            "missing_branches": [],
        },
        plain={"executed_lines": [5], "missing_lines": []},
    )
    profile = load_coverage_profile(json.dumps(document), declared_format="coverage-py-json")
    assert profile.files["plain"].branches is not None
    assert profile.files["plain"].branches.by_line == {}


def test_executed_branches_not_a_list_is_refused():
    document = _artifact(
        a={
            "executed_lines": [1],
            "missing_lines": [],
            "executed_branches": "not-a-list",
            "missing_branches": [],
        }
    )
    _assert_unreadable(document)


def test_a_malformed_arc_pair_shape_is_refused():
    document = _artifact(
        a={
            "executed_lines": [1],
            "missing_lines": [],
            "executed_branches": [[1]],  # only one element, not [src, dst]
            "missing_branches": [],
        }
    )
    _assert_unreadable(document)


def test_a_boolean_arc_member_is_refused_even_though_bool_is_an_int_subclass():
    document = _artifact(
        a={
            "executed_lines": [1],
            "missing_lines": [],
            "executed_branches": [[1, True]],
            "missing_branches": [],
        }
    )
    _assert_unreadable(document)


def test_missing_branches_repeating_an_arc_identity_is_refused():
    document = _artifact(
        a={
            "executed_lines": [],
            "missing_lines": [1],
            "executed_branches": [],
            "missing_branches": [[1, 2], [1, 2]],
        }
    )
    _assert_unreadable(document)


def test_a_record_with_no_summary_key_at_all_skips_the_summary_cross_check():
    document = _artifact(
        a={
            "executed_lines": [1],
            "missing_lines": [],
            "executed_branches": [[1, 2]],
            "missing_branches": [],
        }
    )
    profile = load_coverage_profile(json.dumps(document), declared_format="coverage-py-json")
    assert profile.files["a"].branches.by_line == {1: (1, 1)}
