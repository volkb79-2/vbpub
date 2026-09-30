"""B129 / W10 step 1: characterization of the inline scalar guards in ``config``.

Pins accept and refuse, with the exact message, for the non-empty-string,
strict-int, real-number and percentage guards in the lane loader. Run against
the UNCHANGED source; never edited to follow the refactor.
"""

from __future__ import annotations

import math
import tomllib

import pytest
from conftest import R0_LANE, R1_LANE

from assay import config
from assay.errors import LaneConfigError

NOT_STRICT_INT = [True, False, 1.5, "1", None, b"1"]


def refusal(call, message: str) -> None:
    with pytest.raises(LaneConfigError) as caught:
        call()
    assert str(caught.value) == message


# ---- _load_schema_version: strict int ------------------------------------------


@pytest.mark.parametrize("value", [True, False, 2.0, "2", None])
def test_schema_version_refuses_a_bool_or_non_int(value, tmp_path):
    path = tmp_path / "assay.toml"
    refusal(
        lambda: config._load_schema_version({"schema_version": value}, path),
        f"{path}: 'schema_version' must be an integer, got {type(value).__name__}",
    )


def test_schema_version_accepts_the_understood_integer(tmp_path):
    version = config.LANE_SCHEMA_VERSION
    assert (
        config._load_schema_version({"schema_version": version}, tmp_path / "a")
        == version
    )


# ---- _load_lane: infrastructure names and declarations -------------------------


def _lane_table(**updates):
    table = tomllib.loads(R0_LANE)["lanes"]["package"]
    table.update(updates)
    return table


def _load_lane(project, **updates):
    path = project.root / "assay.toml"
    return path, lambda: config._load_lane(
        "package", _lane_table(**updates), path, project.root
    )


@pytest.mark.parametrize("key", ["", 1, None, b"X"])
def test_infrastructure_names_refuse_an_empty_or_non_string_name(project, key):
    path, call = _load_lane(project, infrastructure={key: "required-env:X"})
    refusal(
        call,
        f"{path}: lane 'package': infrastructure names must be non-empty strings",
    )


@pytest.mark.parametrize("declaration", ["", 1, None, True, b"x", ["required-env:X"]])
def test_infrastructure_declarations_refuse_an_empty_or_non_string_value(
    project, declaration
):
    path, call = _load_lane(project, infrastructure={"X": declaration})
    refusal(
        call,
        f"{path}: lane 'package': 'infrastructure.X' must be a non-empty string, "
        f"got {type(declaration).__name__}",
    )


def test_infrastructure_accepts_a_single_character_name_and_declaration(project):
    _path, call = _load_lane(project, infrastructure={"X": "required-env:X"})
    assert call().infrastructure


# ---- _validate_evidence_dir: non-empty string ----------------------------------


@pytest.mark.parametrize("field", ["attestation_dir", "adjudication_dir"])
@pytest.mark.parametrize("value", ["", 1, None, True, b"a", ["a"]])
def test_evidence_dir_refuses_an_empty_or_non_string_value(field, value):
    refusal(
        lambda: config._validate_evidence_dir(value, "W", field),
        f"W: 'judge.{field}' must be a non-empty string, got {type(value).__name__}",
    )


def test_evidence_dir_accepts_one_character():
    assert config._validate_evidence_dir("a", "W", "attestation_dir") == "a"


# ---- _as_float: real number ----------------------------------------------------


@pytest.mark.parametrize("value", [True, False, "1", None, b"1", [1]])
def test_as_float_refuses_a_bool_or_non_number(value):
    refusal(
        lambda: config._as_float(value, "W", "field.x"),
        f"W: 'field.x' must be a number, got {type(value).__name__}",
    )


@pytest.mark.parametrize("value", [0, 1, -1, 1.5, -0.0, math.inf])
def test_as_float_accepts_an_int_or_float_and_returns_a_float(value):
    result = config._as_float(value, "W", "field.x")
    assert isinstance(result, float)
    assert result == float(value)


# ---- _load_judge: fail_under percentage ----------------------------------------


def _load_r1(project, fail_under):
    text = R1_LANE.replace("fail_under = 100.0", f"fail_under = {fail_under}")
    path = project.write(text)
    return path, lambda: config.load_lane_file(path)


@pytest.mark.parametrize("fail_under", ["0.0", "0", "50", "100", "100.0", "99.9"])
def test_judge_fail_under_accepts_zero_to_one_hundred(project, fail_under):
    _path, call = _load_r1(project, fail_under)
    assert call().lanes["package"].judge.fail_under == float(fail_under)


@pytest.mark.parametrize(
    ("fail_under", "shown"),
    [
        ("-0.0000001", "-1e-07"),
        ("100.0000001", "100.0000001"),
        ("-1", "-1.0"),
        ("101", "101.0"),
        ("inf", "inf"),
        ("-inf", "-inf"),
        ("nan", "nan"),
    ],
)
def test_judge_fail_under_refuses_outside_zero_to_one_hundred(
    project, fail_under, shown
):
    path, call = _load_r1(project, fail_under)
    refusal(
        call,
        f"{path}: lane 'package': 'judge.fail_under' must be a percentage "
        f"between 0 and 100, got {shown}",
    )


@pytest.mark.parametrize(
    ("fail_under", "shown"), [("true", "bool"), ('"5"', "str")]
)
def test_judge_fail_under_refuses_a_bool_or_string(project, fail_under, shown):
    path, call = _load_r1(project, fail_under)
    refusal(
        call,
        f"{path}: lane 'package': 'judge.fail_under' must be a number, got {shown}",
    )


# ---- _load_mutation: jobs / max_mutants / shard numbers / budget ---------------


def _valid_native_mutation(**overrides):
    value = {"jobs": 1, "max_mutants": 10, "operators": ["python:compare-swap"]}
    value.update(overrides)
    return value


def _mutation(tmp_path, **overrides):
    return lambda: config._load_mutation(
        _valid_native_mutation(**overrides),
        "W",
        "python",
        tmp_path,
        ("pytest",),
    )


@pytest.mark.parametrize("value", [True, False, 1.5, "1", None, b"1"])
def test_mutation_jobs_refuse_a_bool_or_non_int(tmp_path, value):
    refusal(
        _mutation(tmp_path, jobs=value),
        f"W: 'judge.mutation.jobs' must be an integer, got {type(value).__name__}",
    )


def test_mutation_jobs_accept_one_and_refuse_zero(tmp_path):
    assert _mutation(tmp_path, jobs=1)().jobs == 1
    refusal(
        _mutation(tmp_path, jobs=0),
        "W: 'judge.mutation.jobs' must be a positive integer, got 0",
    )


@pytest.mark.parametrize("value", [True, False, 1.5, "1", None, b"1"])
def test_mutation_max_mutants_refuse_a_bool_or_non_int(tmp_path, value):
    refusal(
        _mutation(tmp_path, max_mutants=value),
        "W: 'judge.mutation.max_mutants' must be an integer, got "
        f"{type(value).__name__}",
    )


@pytest.mark.parametrize("field", ["shard_index", "shard_count"])
@pytest.mark.parametrize("value", [True, False, 1.5, "1", b"1"])
def test_mutation_shard_numbers_refuse_a_bool_or_non_int(tmp_path, field, value):
    overrides = {"shard_index": 0, "shard_count": 2}
    overrides[field] = value
    refusal(
        _mutation(tmp_path, **overrides),
        f"W: 'judge.mutation.{field}' must be an integer, got {type(value).__name__}",
    )


@pytest.mark.parametrize("value", ["", 1, True, False, b"x", ["auto"]])
def test_mutation_budget_per_candidate_refuses_an_empty_or_non_string(
    tmp_path, value
):
    refusal(
        _mutation(tmp_path, budget_per_candidate=value),
        "W: 'judge.mutation.budget_per_candidate' must be a non-empty string, "
        f"got {type(value).__name__}",
    )


def test_mutation_budget_per_candidate_accepts_a_duration_string(tmp_path):
    assert _mutation(tmp_path, budget_per_candidate="auto")().budget_per_candidate


# ---- _load_ingested_mutation: fail_under percentage ----------------------------


def _ingested(tmp_path, fail_under):
    (tmp_path / "src").mkdir(exist_ok=True)
    return lambda: config._load_ingested_mutation(
        {
            "format": "mutation-report-json",
            "artifact": "src/m.json",
            "fail_under": fail_under,
        },
        "W",
        "javascript",
        tmp_path,
    )


@pytest.mark.parametrize("fail_under", [0, 0.0, 50, 100, 100.0])
def test_ingested_mutation_fail_under_accepts_zero_to_one_hundred(
    tmp_path, fail_under
):
    assert _ingested(tmp_path, fail_under)().fail_under == float(fail_under)


@pytest.mark.parametrize(
    ("fail_under", "shown"),
    [
        (-0.0000001, "-1e-07"),
        (100.0000001, "100.0000001"),
        (-1, "-1.0"),
        (101, "101.0"),
        (math.inf, "inf"),
        (-math.inf, "-inf"),
        (math.nan, "nan"),
    ],
)
def test_ingested_mutation_fail_under_refuses_outside_zero_to_one_hundred(
    tmp_path, fail_under, shown
):
    refusal(
        _ingested(tmp_path, fail_under),
        f"W: 'judge.mutation.fail_under' must be in 0.0..100.0, got {shown}",
    )


@pytest.mark.parametrize("fail_under", [True, False, "5", None])
def test_ingested_mutation_fail_under_refuses_a_bool_or_non_number(
    tmp_path, fail_under
):
    refusal(
        _ingested(tmp_path, fail_under),
        "W: 'judge.mutation.fail_under' must be a number, got "
        f"{type(fail_under).__name__}",
    )


# ---- _load_canary: budget_per_attempt ------------------------------------------


def _canary(tmp_path, budget):
    source = tmp_path / "src"
    source.mkdir(exist_ok=True)
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    return lambda: config._load_canary(
        {
            "mechanism": "import-break",
            "target": "src/app.py",
            "budget_per_attempt": budget,
        },
        "W",
        tmp_path,
        (source,),
    )


@pytest.mark.parametrize("budget", ["", 1, True, False, b"x", ["1m"]])
def test_canary_budget_per_attempt_refuses_an_empty_or_non_string(tmp_path, budget):
    refusal(
        _canary(tmp_path, budget),
        "W: 'judge.canary.budget_per_attempt' must be a non-empty string, "
        f"got {type(budget).__name__}",
    )


def test_canary_budget_per_attempt_accepts_a_duration_string(tmp_path):
    assert _canary(tmp_path, "5m")().budget_per_attempt == "5m"
