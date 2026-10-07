"""LCR-2: doctor ``fail_exit_code`` and per-check detail ``lines`` (DESIGN-RG82 2.4)."""

from __future__ import annotations

import json

import pytest
from test_doctor import check, doctor_registry, invoke, ok, registry

from cli_extended import CheckResult, DoctorCheck, register_doctor


# ------------------------------------------------------------ fail_exit_code


def test_fail_exit_code_is_used_when_a_check_fails():
    reg = registry()
    register_doctor(
        reg, [check("alpha", CheckResult("fail", "broken")), check("beta", ok())],
        fail_exit_code=2,
    )
    code, out, _ = invoke(reg)
    assert code == 2
    assert out.endswith("doctor: 1 ok, 0 warn, 1 fail, 0 skip\n")


def test_fail_exit_code_applies_to_json_mode_too():
    reg = registry()
    register_doctor(reg, [check("alpha", CheckResult("fail", "broken"))], fail_exit_code=77)
    code, out, _ = invoke(reg, "--json")
    assert code == 77
    assert json.loads(out)["summary"]["fail"] == 1


def test_clean_and_warn_only_runs_exit_zero_whatever_fail_exit_code_is():
    reg = registry()
    register_doctor(
        reg, [check("alpha", ok()), check("beta", CheckResult("warn", "meh"))],
        fail_exit_code=9,
    )
    assert invoke(reg)[0] == 0


def test_a_crashed_check_exits_with_fail_exit_code():
    def boom(runtime, args):
        raise RuntimeError("nope")

    reg = registry()
    register_doctor(reg, [check("alpha", run=boom)], fail_exit_code=3)
    code, out, _ = invoke(reg)
    assert code == 3 and "check crashed: RuntimeError: nope" in out


def test_the_default_fail_exit_code_stays_one():
    reg = registry()
    register_doctor(reg, [check("alpha", CheckResult("fail", "broken"))])
    assert invoke(reg)[0] == 1


@pytest.mark.parametrize("value", [1, 2, 128, 255])
def test_fail_exit_code_bounds_are_accepted(value):
    reg = registry()
    register_doctor(reg, [check("alpha", CheckResult("fail", "x"))], fail_exit_code=value)
    assert invoke(reg)[0] == value


@pytest.mark.parametrize("value", [0, -1, 256, 1000, True, False, 2.0, "2", None])
def test_fail_exit_code_outside_one_to_255_is_refused(value):
    reg = registry()
    with pytest.raises(ValueError, match="fail_exit_code must be an integer from 1 to 255"):
        register_doctor(reg, [check("alpha", ok())], fail_exit_code=value)
    # The refusal happens before anything is registered.
    register_doctor(reg, [check("alpha", ok())])


# --------------------------------------------------------------------- lines


def test_human_output_prints_lines_indented_before_the_remedy():
    reg = doctor_registry(
        check(
            "alpha",
            CheckResult(
                "fail", "broken", remedy="fix it", lines=("first detail", "second detail")
            ),
        ),
        check("beta", ok()),
    )
    code, out, _ = invoke(reg)
    assert code == 1
    assert out == (
        "[FAIL] alpha: broken\n"
        "    first detail\n"
        "    second detail\n"
        "    remedy: fix it\n"
        "[OK] beta: fine\n"
        "doctor: 1 ok, 0 warn, 1 fail, 0 skip\n"
    )


def test_json_has_a_lines_key_only_when_non_empty():
    reg = doctor_registry(
        check("alpha", CheckResult("warn", "hm", lines=("one", "two"))),
        check("beta", ok("fine", details={"n": 1})),
    )
    _, out, _ = invoke(reg, "--json")
    checks = json.loads(out)["checks"]
    assert checks[0] == {
        "name": "alpha", "status": "warn", "summary": "hm", "remedy": None,
        "details": {}, "lines": ["one", "two"],
    }
    assert checks[1] == {
        "name": "beta", "status": "ok", "summary": "fine", "remedy": None,
        "details": {"n": 1},
    }
    assert "lines" not in checks[1]


def test_doctor_json_of_a_tool_that_never_sets_lines_is_unchanged():
    reg = doctor_registry(check("alpha", ok("good", details={"n": 3})))
    _, out, _ = invoke(reg, "--json")
    assert out == (
        '{"tool": "mytool", "version": "1.2.3", "checks": [{"name": "alpha", '
        '"status": "ok", "summary": "good", "remedy": null, "details": {"n": 3}}], '
        '"summary": {"ok": 1, "warn": 0, "fail": 0, "skip": 0}}\n'
    )


def test_lines_survive_remedy_normalisation():
    reg = doctor_registry(
        check("alpha", CheckResult("warn", "hm", remedy="a\n  b", lines=("kept",)))
    )
    _, out, _ = invoke(reg)
    assert out.splitlines()[:3] == ["[WARN] alpha: hm", "    kept", "    remedy: a b"]


def test_a_crashed_check_carries_no_lines():
    def boom(runtime, args):
        raise RuntimeError("nope")

    reg = doctor_registry(check("alpha", run=boom))
    _, out, _ = invoke(reg, "--json")
    assert "lines" not in json.loads(out)["checks"][0]


def test_check_result_lines_default_is_empty():
    assert CheckResult("ok", "fine").lines == ()


@pytest.mark.parametrize(
    "lines",
    [["a", "b"], "abc", ("a\nb",), ("a\rb",), (1,), (None,)],
)
def test_check_result_lines_must_be_a_tuple_of_single_line_strings(lines):
    with pytest.raises(ValueError, match="check lines must be a tuple of single-line"):
        CheckResult("ok", "fine", lines=lines)


def test_check_result_still_validates_status_and_summary():
    with pytest.raises(ValueError, match="check status"):
        CheckResult("bad", "x")
    with pytest.raises(ValueError, match="summary"):
        CheckResult("ok", "two\nlines")


def test_doctor_check_still_requires_a_name():
    with pytest.raises(ValueError):
        DoctorCheck("Bad Name", "d", lambda r, a: ok())
