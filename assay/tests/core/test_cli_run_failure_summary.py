"""B146: the concise ``assay run`` summary states a measured R0 failure.

The headline stays the verdict's own ``outcome/reason`` pair. When the R0
command measurably failed but a later refusal made the headline
``NO_MEASUREMENT`` (a failing suite that dirtied the tree), the summary adds an
``R0: FAIL`` line naming the first failing test. A genuine no-measurement is
never recast as a test failure.
"""

from __future__ import annotations

import io

import pytest
from conftest import R0_LANE, GitRepo, set_key

from assay import failure_summary
from assay.cli import main

FIRST = "tests/test_alpha.py::test_first"
SECOND = "tests/test_beta.py::test_second"


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _lane(repo: GitRepo, shell: str):
    toml_shell = shell.replace("\\", "\\\\").replace('"', '\\"')
    lane = set_key(R0_LANE, "argv", f'["/bin/sh", "-c", "{toml_shell}"]')
    path = repo.write("assay.toml", lane)
    repo.commit_all("add assay.toml")
    return path


def test_a_failing_pytest_style_r0_names_the_first_failure_in_execution_order(
    git_repo: GitRepo,
):
    path = _lane(
        git_repo,
        f"echo 'FAILED {FIRST} - boom'; echo 'FAILED {SECOND} - bang'; exit 1",
    )

    code, out, _ = _run(["run", "package", "--file", str(path)])

    assert code == 1
    assert "package: FAIL/COMMAND_FAILED (exit 1)" in out
    assert f"  R0: FAIL (first failing test: {FIRST})" in out
    assert f"first failing test: {SECOND}" not in out


def test_a_failing_suite_that_dirties_the_tree_still_reports_r0_fail(
    git_repo: GitRepo,
):
    path = _lane(
        git_repo,
        f"echo 'FAILED {FIRST} - boom'; echo 'FAILED {SECOND}'; "
        "echo dirt > made.txt; exit 1",
    )

    code, out, _ = _run(["run", "package", "--file", str(path)])

    assert code == 3
    assert "package: NO_MEASUREMENT/DIRTY_TREE (exit 3)" in out
    assert f"  R0: FAIL (first failing test: {FIRST})" in out


def test_a_no_measurement_without_a_failing_test_is_not_recast(git_repo: GitRepo):
    path = _lane(git_repo, "exit 0")
    git_repo.write("uncommitted.txt", "dirty\n")

    code, out, _ = _run(["run", "package", "--file", str(path)])

    assert code == 3
    assert "package: NO_MEASUREMENT/DIRTY_TREE (exit 3)" in out
    assert "R0:" not in out


def test_a_failure_with_unrecognised_output_adds_no_line(git_repo: GitRepo):
    path = _lane(git_repo, "echo something broke; exit 1")

    code, out, _ = _run(["run", "package", "--file", str(path)])

    assert code == 1
    assert "package: FAIL/COMMAND_FAILED (exit 1)" in out
    assert "R0:" not in out


def test_a_passed_r0_claim_never_gets_a_failure_line():
    from types import SimpleNamespace

    from assay.cli import _print_run_summary
    from assay.errors import Outcome

    verdict = SimpleNamespace(
        lane="package",
        outcome=Outcome.FAIL,
        reason_code=None,
        exit_code=1,
        commit="c" * 40,
        argv_effective=("true",),
        argv_modified=False,
        claims=(
            SimpleNamespace(rigor="R0", status=Outcome.PASS),
            SimpleNamespace(rigor="R1", status=Outcome.FAIL),
        ),
        result_stdout_tail=f"FAILED {FIRST}\n",
        result_stderr_tail=None,
    )
    out = io.StringIO()

    _print_run_summary(verdict, out)

    assert "R0:" not in out.getvalue()
    verdict.claims = (SimpleNamespace(rigor="R1", status=Outcome.FAIL),)
    out = io.StringIO()
    _print_run_summary(verdict, out)
    assert f"R0: FAIL (first failing test: {FIRST})" in out.getvalue()


def test_a_passing_run_prints_no_failure_line(git_repo: GitRepo):
    path = _lane(git_repo, f"echo 'FAILED {FIRST}'; exit 0")

    code, out, _ = _run(["run", "package", "--file", str(path)])

    assert code == 0
    assert "R0:" not in out


# --- the reader, one case per supported R0 producer ----------------------------

PYTEST_OUTPUT = (
    "=================== FAILURES ===================\n"
    "_______________ test_first _______________\n"
    "E assert 1 == 2\n"
    "=========== short test summary info ============\n"
    f"FAILED {FIRST} - assert 1 == 2\n"
    f"FAILED {SECOND} - boom\n"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (PYTEST_OUTPUT, FIRST),
        (f"ERROR {SECOND} - fixture error\nFAILED {FIRST}\n", SECOND),
        ("ERROR tests/test_c.py - collection\n", "tests/test_c.py"),
        ("_____ test_only_header _____\nE boom\n", "test_only_header"),
        ("ERROR something logged\n", None),
        (
            "ok  \tpkg\t0.1s\n--- FAIL: TestOne (0.00s)\n--- FAIL: TestTwo (0.00s)\n",
            "TestOne",
        ),
        (
            '{"Action":"run","Test":"TestA"}\n'
            '{"Action":"fail","Package":"p"}\n'
            "not json\n"
            "{broken\n"
            '{"Action":"fail","Test":"TestB"}\n'
            '{"Action":"fail","Test":"TestC"}\n',
            "TestB",
        ),
        ("[1, 2]\n", None),
        (" FAIL  src/a.test.ts > suite > does a thing\n", "src/a.test.ts > suite > does a thing"),
        ("  ● Suite › case one\n  ● Suite › case two\n", "Suite › case one"),
        ("  FAIL  src/a.test.ts\n", None),
        ("all good\n", None),
        ("", None),
    ],
)
def test_first_failing_test_reads_each_supported_producer(text, expected):
    assert failure_summary.first_failing_test(text) == expected


def test_the_earliest_recogniser_hit_wins_across_producers():
    text = "--- FAIL: TestGoFirst (0s)\nFAILED tests/t.py::test_later\n"
    assert failure_summary.first_failing_test(text) == "TestGoFirst"


def test_stdout_is_read_before_stderr_and_none_is_skipped():
    assert failure_summary.first_failing_test(None, "", "--- FAIL: TestErr\n") == "TestErr"
    assert (
        failure_summary.first_failing_test("--- FAIL: TestOut\n", "--- FAIL: TestErr\n")
        == "TestOut"
    )


def test_a_deeply_nested_json_line_is_skipped_not_fatal():
    text = "{" + '"a":{' * 200000 + "}\n" + '{"Action":"fail","Test":"TestAfter"}\n'
    assert failure_summary.first_failing_test(text) == "TestAfter"


def test_a_name_is_single_line_and_bounded():
    long_name = "a" * (failure_summary.MAX_NAME_CHARS + 50)
    got = failure_summary.first_failing_test(f"--- FAIL: {long_name}\n")
    assert got == "a" * failure_summary.MAX_NAME_CHARS
    assert failure_summary.first_failing_test("--- FAIL: \x01\n") is None
    assert failure_summary.first_failing_test(" FAIL  a > b\x1b[0m\n") == "a > b [0m"
