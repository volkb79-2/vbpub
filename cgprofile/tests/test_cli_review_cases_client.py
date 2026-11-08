"""The reviewed CLI cases of docs/cli-review-cgprofile.toml: each runs its recorded invocation and asserts the reviewed outcome."""
import pytest

from cli_cases import CLIENT_CATALOG, execute_client, load_cases, slug

CASES = load_cases(CLIENT_CATALOG)


def _slug(case_id):
    return slug(case_id, "cgprofile")


@pytest.mark.parametrize("case", [pytest.param(c, marks=pytest.mark.cli_case(c["id"]), id=_slug(c["id"])) for c in CASES])
def test_reviewed_case(case):
    code, out, err = execute_client(case["invocation"])
    assert code == case["expected_exit_status"], (case["invocation"], code, out[:300], err[:300])
    assert case["expected_stdout_contains"] in out
    assert case["expected_stderr_contains"] in err


def test_every_active_case_is_reviewed_with_evidence():
    assert CASES
    for c in CASES:
        assert c["decision"] == "accept" and c["rationale"] and c["effects"] and c["test_ids"]
        assert c["test_ids"] == [f"tests/test_cli_review_cases_client.py::test_reviewed_case[{_slug(c['id'])}]"]
