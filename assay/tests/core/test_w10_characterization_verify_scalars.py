"""W10 step 1: characterization of verify.py's raw scalar guards (I2, verify side).

The private twins the W10 boundary keeps inside ``verify.py`` (it may never
import ``assay.guards``): the strict-int guard used by ``_raw_mutant_identity``,
and the non-empty-text guard used by ``_check_snapshot_policy`` (link paths and
unsafe-symlink omissions) and ``_is_bounded_node_id``. Each operand is varied
alone, and the ``failures`` text is compared byte for byte. The remaining
`_is_int` sites (the ingested residual, the mutation-payload shape and the
B106 provenance sentinel) are pinned in
``test_w10_characterization_verify_mutation_ints.py``. Never edited after the
consolidation lands (W10 brief, step 1).
"""

from __future__ import annotations

import pytest

from assay import verify

# --- _raw_mutant_identity: str, str, str and two strict ints ----------------


def _entry(**overrides) -> dict:
    values = {
        "path": "src/a.py",
        "start_byte": 4,
        "end_byte": 5,
        "replacement_sha256": "0" * 64,
        "operator": "python:compare-swap",
    }
    values.update(overrides)
    return values


def test_a_mutant_identity_is_the_five_fields_in_order():
    assert verify._raw_mutant_identity(_entry()) == (
        "src/a.py",
        4,
        5,
        "0" * 64,
        "python:compare-swap",
    )


@pytest.mark.parametrize("value", [0, -1, 10**30])
def test_any_plain_int_offset_is_accepted(value):
    assert verify._raw_mutant_identity(_entry(start_byte=value, end_byte=value))[1:3] == (
        value,
        value,
    )


@pytest.mark.parametrize("name", ["start_byte", "end_byte"])
@pytest.mark.parametrize("value", [True, False, 1.5, "1", None, b"1", [1]])
def test_an_offset_that_is_not_a_strict_int_has_no_identity(name, value):
    assert verify._raw_mutant_identity(_entry(**{name: value})) is None


@pytest.mark.parametrize("name", ["path", "replacement_sha256", "operator"])
@pytest.mark.parametrize("value", [1, None, b"x", ["x"]])
def test_a_text_field_that_is_not_a_string_has_no_identity(name, value):
    assert verify._raw_mutant_identity(_entry(**{name: value})) is None


@pytest.mark.parametrize("entry", ["x", None, 1, ["path"]])
def test_a_non_dict_entry_has_no_identity(entry):
    assert verify._raw_mutant_identity(entry) is None


# --- _check_snapshot_policy: link_paths and omissions entries ---------------


def _snapshot_failures(policy: dict) -> list[str]:
    failures: list[str] = []
    verify._check_snapshot_policy(
        {"declared_rigor": ["R0", "R1"], "snapshot_policy": policy}, failures
    )
    return failures


REPOSITORY = "repository"
OMISSION = "repository-minus-unsafe-symlinks"


def test_link_paths_of_non_empty_ascending_strings_are_accepted():
    assert _snapshot_failures({"selection": REPOSITORY, "link_paths": ["a", "b"]}) == []


@pytest.mark.parametrize("bad", ["", 1, None, b"a", ["a"]])
def test_a_link_path_that_is_not_non_empty_text_is_named_by_index(bad):
    assert _snapshot_failures({"selection": REPOSITORY, "link_paths": [bad]}) == [
        "snapshot_policy.link_paths[0] must be a non-empty string"
    ]
    assert _snapshot_failures({"selection": REPOSITORY, "link_paths": ["a", bad]}) == [
        "snapshot_policy.link_paths[1] must be a non-empty string"
    ]


def test_a_bad_link_path_stops_the_ordering_check():
    assert _snapshot_failures({"selection": REPOSITORY, "link_paths": ["b", "a", ""]}) == [
        "snapshot_policy.link_paths[2] must be a non-empty string"
    ]


def test_unordered_link_paths_are_refused_when_every_entry_is_text():
    assert _snapshot_failures({"selection": REPOSITORY, "link_paths": ["b", "a"]}) == [
        "snapshot_policy.link_paths must be strictly ascending by UTF-8 bytes"
    ]


def test_omissions_of_normalized_ascending_text_are_accepted():
    assert _snapshot_failures({"selection": OMISSION, "unsafe_symlink_omissions": ["a", "b"]}) == []


@pytest.mark.parametrize("bad", ["", 1, None, b"a", ["a"]])
def test_an_omission_that_is_not_non_empty_text_is_named_by_index(bad):
    assert _snapshot_failures({"selection": OMISSION, "unsafe_symlink_omissions": [bad]}) == [
        "snapshot_policy.unsafe_symlink_omissions[0] must be a non-empty string"
    ]
    assert _snapshot_failures(
        {"selection": OMISSION, "unsafe_symlink_omissions": ["a", bad]}
    ) == ["snapshot_policy.unsafe_symlink_omissions[1] must be a non-empty string"]


# --- _is_bounded_node_id: text, non-empty, at most 4096 UTF-8 bytes ---------


@pytest.mark.parametrize(
    "value", ["a", "a" * 4096, "é" * 2048, "tests/test_x.py::test_y"]
)
def test_a_non_empty_string_within_4096_bytes_is_a_node_id(value):
    assert verify._is_bounded_node_id(value) is True


@pytest.mark.parametrize("value", ["", 1, None, b"a", ["a"], True])
def test_a_non_string_or_empty_value_is_not_a_node_id(value):
    assert verify._is_bounded_node_id(value) is False


@pytest.mark.parametrize("value", ["a" * 4097, "é" * 2049])
def test_a_string_over_4096_utf8_bytes_is_not_a_node_id(value):
    assert verify._is_bounded_node_id(value) is False


def test_a_string_that_cannot_be_encoded_is_not_a_node_id():
    assert verify._is_bounded_node_id("\ud800") is False
