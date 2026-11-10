"""Execute every active CLI review decision against synthetic mount tables."""

from __future__ import annotations

import pytest

from cli_cases import execute, load_cases, slug


CASES = load_cases()


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, marks=pytest.mark.cli_case(case["id"]), id=slug(case["id"]))
     for case in CASES],
)
def test_reviewed_case(case):
    code, stdout, stderr = execute(case["invocation"])
    assert code == case["expected_exit_status"], (
        case["invocation"],
        code,
        stdout[:500],
        stderr[:500],
    )
    assert case["expected_stdout_contains"] in stdout
    assert case["expected_stderr_contains"] in stderr


def test_every_active_case_has_evidence():
    assert CASES
    for case in CASES:
        assert case["decision"] == "accept"
        assert case["rationale"]
        assert case["effects"]
        assert case["test_ids"] == [
            f"tests/test_cli_review_cases.py::test_reviewed_case[{slug(case['id'])}]"
        ]
