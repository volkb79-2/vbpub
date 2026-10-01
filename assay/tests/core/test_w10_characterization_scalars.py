"""B129 / W10 step 1: characterization of scalar guards outside ``verdict``.

Pins accept and refuse, with the exact message, for the inline strict-int,
positive-int, real-number and positive-or-infinite guards in ``isolation``,
``mutation`` and ``liveness``/``liveness_resources``. Run against the
UNCHANGED source; never edited to follow the refactor.
"""

from __future__ import annotations

import math

import pytest

from assay import isolation, liveness, liveness_resources, mutation

NOT_STRICT_INT = [True, False, 1.5, "1", None, b"1"]
NOT_REAL = [True, False, "1", None, b"1", [1]]


def refusal(call, message: str, exc=ValueError) -> None:
    with pytest.raises(exc) as caught:
        call()
    assert str(caught.value) == message


# ---- isolation.SnapshotLimits: positive strict int ---------------------------

LIMIT_FIELDS = (
    "max_objects",
    "max_entries",
    "max_path_bytes",
    "max_total_path_bytes",
    "max_blob_bytes",
    "max_total_object_bytes",
    "max_total_tree_blob_bytes",
    "max_pack_bytes",
)


def _limits(**overrides) -> isolation.SnapshotLimits:
    values = {name: 10 for name in LIMIT_FIELDS}
    values.update(overrides)
    return isolation.SnapshotLimits(**values)


def test_snapshot_limits_accept_one_as_the_smallest_bound():
    assert _limits(**{name: 1 for name in LIMIT_FIELDS}).max_objects == 1


@pytest.mark.parametrize("name", LIMIT_FIELDS)
@pytest.mark.parametrize("value", NOT_STRICT_INT + [0, -1])
def test_snapshot_limits_refuse_a_bool_non_int_or_non_positive_bound(name, value):
    refusal(
        lambda: _limits(**{name: value}),
        f"{name} must be a positive integer, got {value!r}",
    )


# ---- isolation._check_timeout: positive finite number or math.inf -------------

TIMEOUT_MESSAGE = "timeout must be a positive finite number or math.inf, got {!r}"


@pytest.mark.parametrize("timeout", [1, 1.5, 1e-9, math.inf, 10**6])
def test_check_timeout_accepts_a_positive_number_or_infinity(timeout):
    assert isolation._check_timeout(timeout) is None


@pytest.mark.parametrize(
    "timeout",
    [True, False, "1", None, b"1", 0, 0.0, -0.0, -1, -1.5, math.nan, -math.inf],
)
def test_check_timeout_refuses_everything_else(timeout):
    refusal(lambda: isolation._check_timeout(timeout), TIMEOUT_MESSAGE.format(timeout))


def test_check_timeout_lets_an_int_beyond_float_range_raise_overflow_error():
    with pytest.raises(OverflowError):
        isolation._check_timeout(10**400)


# ---- mutation.MutationSite / MutationTarget -----------------------------------


def _site(**overrides) -> mutation.MutationSite:
    values = dict(
        start_byte=0,
        end_byte=1,
        replacement=b"x",
        lineno=1,
        operator="python:compare-swap",
        description="Lt->LtE",
    )
    values.update(overrides)
    return mutation.MutationSite(**values)


@pytest.mark.parametrize("name", ["start_byte", "end_byte", "lineno"])
@pytest.mark.parametrize("value", NOT_STRICT_INT)
def test_mutation_site_positions_refuse_a_bool_or_non_int(name, value):
    refusal(
        lambda: _site(**{name: value}),
        f"MutationSite.{name} must be an integer, got {value!r}",
    )


def test_mutation_site_accepts_start_byte_zero_and_refuses_minus_one():
    assert _site().start_byte == 0
    refusal(
        lambda: _site(start_byte=-1),
        "MutationSite.start_byte must be >= 0, got -1",
    )


@pytest.mark.parametrize("line", [True, False, 1.5, "1", None, 0, -1])
def test_mutation_target_lines_refuse_a_bool_non_int_or_non_positive_line(line):
    refusal(
        lambda: mutation.MutationTarget(path="a.py", text="x", lines=frozenset({line})),
        f"MutationTarget.lines must contain only positive line numbers, got {line!r}",
    )


def test_mutation_target_accepts_line_one():
    assert mutation.MutationTarget(path="a.py", text="x", lines=frozenset({1})).lines == frozenset({1})


@pytest.mark.parametrize("path", [None, 0, b"a", ""])
def test_mutation_target_path_refuses_a_non_string_or_empty_value(path):
    refusal(
        lambda: mutation.MutationTarget(path=path, text="x", lines=frozenset({1})),
        f"MutationTarget.path must be a non-empty string, got {path!r}",
    )


# ---- liveness: positive pid, real numbers ------------------------------------


@pytest.mark.parametrize("value", [1, 2, 4242])
def test_valid_pid_accepts_a_positive_int(value):
    assert liveness._valid_pid(value) == value


@pytest.mark.parametrize("value", NOT_STRICT_INT + [0, -1])
def test_valid_pid_returns_none_for_everything_else(value):
    assert liveness._valid_pid(value) is None


@pytest.mark.parametrize("value", [0, 1, -1, 1.5, -0.0, 10**6])
def test_finite_number_returns_a_finite_int_or_float(value):
    result = liveness._finite_number(value)
    assert result == value
    assert type(result) is type(value)


@pytest.mark.parametrize("value", NOT_REAL + [math.nan, math.inf, -math.inf, 10**400])
def test_finite_number_returns_none_for_bool_non_number_or_non_finite(value):
    assert liveness._finite_number(value) is None


# ---- liveness_resources: positive pid --------------------------------------------


@pytest.mark.parametrize("pid", NOT_STRICT_INT + [0, -1])
def test_read_liveness_resources_reports_an_invalid_pid_as_unavailable(pid, tmp_path):
    assert liveness_resources.read_liveness_resources(pid, proc_root=tmp_path) == {
        "schema_version": liveness_resources.RESOURCE_SNAPSHOT_SCHEMA_VERSION,
        "status": "unavailable",
        "reason": "candidate-pid-invalid",
    }


def test_read_liveness_resources_does_not_call_pid_one_invalid(tmp_path):
    snapshot = liveness_resources.read_liveness_resources(1, proc_root=tmp_path)
    assert snapshot["reason"] != "candidate-pid-invalid"
