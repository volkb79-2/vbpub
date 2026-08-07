""":func:`~assay.mutation.collect_mutants` -- this package's own answer to
aggregating a lane-wide R2 claim from POSSIBLY MANY per-file
``generate_mutants`` calls (a design choice the handoff leaves to the
implementer; see the LOG for the full reasoning): call once per changed
file with candidate lines, in path order, treating ``"UNSUPPORTED"`` for
one file as zero mutants from that file rather than aborting the whole
call (P11's own per-file union, A-114).

Feeds directly into O1/O4's own "total == 0" -> NO_MUTANTS reading: the
ALL-UNSUPPORTED-or-empty case is proven here to collapse naturally into an
empty job list, with no special-casing needed in this function.
"""

from __future__ import annotations

from assay.adapters.python import PythonAdapter
from assay.mutation import MutationTarget, collect_mutants


def test_collects_mutants_from_a_single_file():
    target = MutationTarget(
        path="pkg/mod.py",
        text="def f(a, b):\n    if a > b:\n        return True\n    return False\n",
        lines=frozenset({2}),
    )
    jobs = collect_mutants((target,), adapter=PythonAdapter())
    assert len(jobs) == 1
    assert jobs[0].path == "pkg/mod.py"
    assert jobs[0].mutant.operator == "compare-swap"
    assert jobs[0].mutant.description == "Gt->GtE"


def test_aggregates_across_multiple_files_in_path_order():
    """One claim covers the whole lane, not one file -- the aggregation
    choice this package makes: sorted by path, regardless of the caller's
    own iteration order."""
    z_file = MutationTarget(
        path="z/mod.py",
        text="def f(a, b):\n    if a > b:\n        return True\n    return False\n",
        lines=frozenset({2}),
    )
    a_file = MutationTarget(
        path="a/mod.py",
        text="def g(a, b):\n    if a == b:\n        return True\n    return False\n",
        lines=frozenset({2}),
    )
    # deliberately supplied out of path order.
    jobs = collect_mutants((z_file, a_file), adapter=PythonAdapter())

    assert [job.path for job in jobs] == ["a/mod.py", "z/mod.py"]


def test_unsupported_for_one_file_contributes_zero_mutants_not_an_abort():
    """A genuinely unparseable file (P11's own UNSUPPORTED trigger, A-114)
    does not abort the whole call -- the OTHER file's real mutant still
    comes back."""
    broken = MutationTarget(path="pkg/broken.py", text="def f(:\n", lines=frozenset({1}))
    valid = MutationTarget(
        path="pkg/valid.py",
        text="def f(a, b):\n    if a > b:\n        return True\n    return False\n",
        lines=frozenset({2}),
    )
    jobs = collect_mutants((broken, valid), adapter=PythonAdapter())

    assert [job.path for job in jobs] == ["pkg/valid.py"]


def test_all_files_unsupported_or_empty_collapses_to_zero_total():
    """The design note's own explicit reading: 'unless ALL files return
    UNSUPPORTED/empty (which is exactly total == 0 -> NO_MUTANTS)' -- this
    function needs no special case for it, it falls out of the loop."""
    broken_one = MutationTarget(path="pkg/broken1.py", text="def f(:\n", lines=frozenset({1}))
    broken_two = MutationTarget(path="pkg/broken2.py", text="def g(:\n", lines=frozenset({1}))
    # a syntactically valid file whose declared line has no eligible site.
    no_site = MutationTarget(
        path="pkg/no_site.py", text="def h():\n    return 1\n", lines=frozenset({2})
    )

    jobs = collect_mutants((broken_one, broken_two, no_site), adapter=PythonAdapter())

    assert jobs == ()


def test_within_one_file_generate_mutants_order_is_preserved():
    """P11's own deterministic per-file ordering (lineno, operator,
    description, byte offset) is untouched -- this function only adds the
    cross-file path ordering on top."""
    target = MutationTarget(
        path="pkg/mod.py",
        text=(
            "def flags():\n"
            "    a = True\n"
            "    if a == 1:\n"
            "        return True\n"
            "    return False\n"
        ),
        lines=frozenset({2, 3}),
    )
    jobs = collect_mutants((target,), adapter=PythonAdapter())

    directly = PythonAdapter().generate_mutants(target.text, {2, 3})
    assert [job.mutant for job in jobs] == list(directly)
