"""The vendored cgprofile ``machine/1`` schema accepts its owner's goldens and keeps the basis vocabulary (RR-1a)."""

import pytest

from conftest import DELETE, edit, golden, has
from rigor_record.validate import validate_machine

GOLDENS = ("measured", "table", "no-speed")


@pytest.mark.parametrize("name", GOLDENS)
def test_cp1_goldens_validate(name):
    assert validate_machine(golden(f"machine/machine-1-{name}.json")) == []


def test_basis_names_its_major():
    measured = golden("machine/machine-1-measured.json")
    assert measured["speed"]["basis"] == "geekbench7-measured"
    wrong = edit(measured, (("speed", "geekbench_major"), 6))
    assert has(validate_machine(wrong), "$.speed.basis", "own Geekbench major")


def test_the_old_unversioned_basis_is_refused():
    old = edit(golden("machine/machine-1-measured.json"), (("speed", "basis"), "geekbench-measured"))
    assert validate_machine(old)


def test_measured_and_table_forms_are_distinct():
    table = golden("machine/machine-1-table.json")
    assert validate_machine(edit(table, (("speed", "multi_core"), 100)))
    assert validate_machine(edit(table, (("speed", "source_url"), DELETE)))
    assert validate_machine(edit(table, (("speed", "basis"), "geekbench6-measured")))


def test_unknown_key_is_refused():
    assert validate_machine(edit(golden("machine/machine-1-measured.json"), (("host", ), "x")))
