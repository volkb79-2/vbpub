"""``assay.mutation.MutationTarget``'s own construction-time discipline
(A-092) -- the per-file input to :func:`~assay.mutation.collect_mutants`,
tested in isolation the same way ``test_mutation_type.py`` tests
``Mutant`` independently of any adapter.
"""

from __future__ import annotations

import dataclasses

import pytest

from assay.mutation import MutantJob, Mutant, MutationTarget


def _target(**overrides) -> MutationTarget:
    fields = dict(path="pkg/mod.py", text="x = 1\n", lines=frozenset({1}))
    fields.update(overrides)
    return MutationTarget(**fields)


def test_a_target_constructs_with_valid_fields():
    target = _target()
    assert target.path == "pkg/mod.py"
    assert target.text == "x = 1\n"
    assert target.lines == frozenset({1})


def test_a_target_is_frozen():
    target = _target()
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.path = "other.py"  # type: ignore[misc]


@pytest.mark.parametrize("bad_path", ["", None, 42])
def test_rejects_an_empty_or_non_string_path(bad_path):
    with pytest.raises(ValueError, match="path"):
        _target(path=bad_path)


@pytest.mark.parametrize("bad_text", [None, 42])
def test_rejects_a_non_string_text(bad_text):
    with pytest.raises(ValueError, match="text"):
        _target(text=bad_text)


def test_accepts_empty_text():
    """Unlike `path`, an empty string IS a legal `text` -- a genuinely
    empty file is a real, if degenerate, input."""
    target = _target(text="")
    assert target.text == ""


@pytest.mark.parametrize("bad_lines", [frozenset(), {1, 2}, [1, 2], None])
def test_rejects_an_empty_or_non_frozenset_lines(bad_lines):
    with pytest.raises(ValueError, match="lines"):
        _target(lines=bad_lines)


@pytest.mark.parametrize("bad_line", [0, -1, True, False])
def test_rejects_a_non_positive_or_boolean_line_number(bad_line):
    with pytest.raises(ValueError, match="lines"):
        _target(lines=frozenset({bad_line}))


# --- MutantJob: a dataclass pairing, no independent validation needed -------


def test_a_mutant_job_pairs_a_path_with_a_mutant():
    mutant = Mutant(
        lineno=1, operator="compare-swap", description="Lt->LtE", mutated_text="x = 1\n"
    )
    job = MutantJob(path="pkg/mod.py", mutant=mutant)
    assert job.path == "pkg/mod.py"
    assert job.mutant is mutant


def test_a_mutant_job_is_frozen():
    mutant = Mutant(
        lineno=1, operator="compare-swap", description="Lt->LtE", mutated_text="x = 1\n"
    )
    job = MutantJob(path="pkg/mod.py", mutant=mutant)
    with pytest.raises(dataclasses.FrozenInstanceError):
        job.path = "other.py"  # type: ignore[misc]
