""":func:`~assay.mutation.collect_mutation_sites` -- bounded cross-file
aggregation, and the three-way distinction that keeps a cap honest (P21,
A-180/A-183).

The claim under attack: **the candidate limit bounds WORK, not just the
report.** A collector that calls every adapter, gathers everything, and then
slices is indistinguishable from this one by its return value alone -- and is
exactly what made P11's cap untrue. So the tests below assert what the
collector DID (which files it called, with what remaining capacity), not only
what it returned.

The second claim: **capability absence, a failed boundary, and a valid empty
result are three different facts.** Collapsing any pair turns an inability to
measure into evidence that nothing was mutable.
"""

from __future__ import annotations

import pytest

from assay.adapters.python import PythonAdapter
from assay.mutation import (
    MutationDiscoveryError,
    MutationSite,
    MutationTarget,
    collect_mutation_sites,
)

OPERATORS = ("compare-swap", "boolop-swap", "bool-const-flip")


def _target(path: str, text: str, lines: set[int]) -> MutationTarget:
    return MutationTarget(path=path, text=text, lines=frozenset(lines))


class _RecordingAdapter:
    """Records every call so a test can assert the collector's own
    behaviour, not merely its result."""

    name = "recording"

    def __init__(self, per_file: dict[str, object]) -> None:
        self.per_file = per_file
        self.calls: list[tuple[str, int]] = []

    def generate_mutation_sites(self, text, lines, *, operators, limit):
        # The collector identifies a file only by the text it hands over.
        path = {v: k for k, v in _TEXTS.items()}[text]
        self.calls.append((path, limit))
        return self.per_file[path]


_TEXTS = {
    "a.py": "x = 1\n",
    "b.py": "y = 2\n",
    "c.py": "z = 3\n",
}


def _sites(count: int) -> tuple[MutationSite, ...]:
    return tuple(
        MutationSite(
            start_byte=index,
            end_byte=index + 1,
            replacement=b"9",
            lineno=1,
            operator="compare-swap",
            description=f"site-{index}",
        )
        for index in range(count)
    )


def test_targets_are_visited_in_path_order_regardless_of_input_order():
    """Determinism: a caller passing an unordered collection still gets a
    stable job list, because the artifact's identities must not depend on
    dict iteration order."""
    adapter = _RecordingAdapter({"a.py": _sites(1), "b.py": _sites(1), "c.py": _sites(1)})
    jobs = collect_mutation_sites(
        (
            _target("c.py", _TEXTS["c.py"], {1}),
            _target("a.py", _TEXTS["a.py"], {1}),
            _target("b.py", _TEXTS["b.py"], {1}),
        ),
        adapter=adapter,
        operators=("compare-swap",),
        limit=10,
    )

    assert [path for path, _ in adapter.calls] == ["a.py", "b.py", "c.py"]
    assert [job.path for job in jobs] == ["a.py", "b.py", "c.py"]


def test_each_file_receives_only_the_remaining_capacity():
    """The bound is a WORK bound: capacity handed to file N+1 is what file N
    left over. A collector that passes the full limit to every file has not
    bounded anything -- it has only bounded its own output."""
    adapter = _RecordingAdapter({"a.py": _sites(2), "b.py": _sites(1), "c.py": _sites(0)})

    collect_mutation_sites(
        (
            _target("a.py", _TEXTS["a.py"], {1}),
            _target("b.py", _TEXTS["b.py"], {1}),
            _target("c.py", _TEXTS["c.py"], {1}),
        ),
        adapter=adapter,
        operators=("compare-swap",),
        limit=5,
    )

    assert adapter.calls == [("a.py", 5), ("b.py", 3), ("c.py", 2)]


def test_a_full_sentinel_stops_calling_later_files_entirely():
    """Not "calls them and ignores the answer" -- does not call them. This
    is the difference between a bound on work and a bound on the report."""
    adapter = _RecordingAdapter({"a.py": _sites(3), "b.py": _sites(9), "c.py": _sites(9)})

    jobs = collect_mutation_sites(
        (
            _target("a.py", _TEXTS["a.py"], {1}),
            _target("b.py", _TEXTS["b.py"], {1}),
            _target("c.py", _TEXTS["c.py"], {1}),
        ),
        adapter=adapter,
        operators=("compare-swap",),
        limit=3,
    )

    assert [path for path, _ in adapter.calls] == ["a.py"]
    assert len(jobs) == 3


def test_a_job_shares_its_target_text_rather_than_copying_it_per_site():
    """A-180: one reference per target, never one full source copy per
    candidate. Identity (`is`) is the assertion, because equality would pass
    for copies too -- and copies are precisely the defect."""
    adapter = _RecordingAdapter({"a.py": _sites(3)})
    target = _target("a.py", _TEXTS["a.py"], {1})

    jobs = collect_mutation_sites(
        (target,), adapter=adapter, operators=("compare-swap",), limit=5
    )

    assert len(jobs) == 3
    assert all(job.original_text is target.text for job in jobs)


def test_an_unsupported_adapter_propagates_the_marker_with_no_jobs():
    """A-183: capability absence, returned immediately. Not an empty tuple,
    which would claim a supported analysis observed nothing."""
    adapter = _RecordingAdapter({"a.py": "UNSUPPORTED", "b.py": _sites(2)})

    result = collect_mutation_sites(
        (
            _target("a.py", _TEXTS["a.py"], {1}),
            _target("b.py", _TEXTS["b.py"], {1}),
        ),
        adapter=adapter,
        operators=("compare-swap",),
        limit=5,
    )

    assert result == "UNSUPPORTED"
    assert [path for path, _ in adapter.calls] == ["a.py"]


def test_supported_then_unsupported_is_a_discovery_failure_not_a_partial_result():
    """An adapter that claims supported discovery and then reports absence
    is contradicting itself about its OWN capability, so no partial job list
    from it can be trusted -- and silently keeping the first file's jobs
    would report a bounded measurement over an adapter that has no engine."""
    adapter = _RecordingAdapter({"a.py": _sites(2), "b.py": "UNSUPPORTED"})

    with pytest.raises(MutationDiscoveryError, match="capability is a property of the adapter"):
        collect_mutation_sites(
            (
                _target("a.py", _TEXTS["a.py"], {1}),
                _target("b.py", _TEXTS["b.py"], {1}),
            ),
            adapter=adapter,
            operators=("compare-swap",),
            limit=5,
        )


def test_an_empty_first_answer_still_counts_as_supported():
    """The boundary case of the rule above: an empty tuple is a real
    measurement, so a later marker still contradicts it."""
    adapter = _RecordingAdapter({"a.py": _sites(0), "b.py": "UNSUPPORTED"})

    with pytest.raises(MutationDiscoveryError):
        collect_mutation_sites(
            (
                _target("a.py", _TEXTS["a.py"], {1}),
                _target("b.py", _TEXTS["b.py"], {1}),
            ),
            adapter=adapter,
            operators=("compare-swap",),
            limit=5,
        )


def test_no_targets_returns_the_supported_empty_tuple():
    """No language analysis was required, so nothing can be said about
    capability -- returning the marker here would report Go-style absence
    for a lane that simply changed no source file."""
    adapter = _RecordingAdapter({})

    assert (
        collect_mutation_sites(
            (), adapter=adapter, operators=("compare-swap",), limit=5
        )
        == ()
    )
    assert adapter.calls == []


@pytest.mark.parametrize(
    "sites,match",
    [
        (_sites(6), "may never exceed the limit"),
        (
            (
                MutationSite(
                    start_byte=0,
                    end_byte=99,
                    replacement=b"9",
                    lineno=1,
                    operator="compare-swap",
                    description="past the end",
                ),
            ),
            "past the",
        ),
        (
            (
                MutationSite(
                    start_byte=0,
                    end_byte=1,
                    replacement=b"x",
                    lineno=1,
                    operator="compare-swap",
                    description="no-op",
                ),
            ),
            "identical bytes",
        ),
        (
            (
                MutationSite(
                    start_byte=0,
                    end_byte=1,
                    replacement=b"9",
                    lineno=9,
                    operator="compare-swap",
                    description="wrong line",
                ),
            ),
            "starts on line",
        ),
        (
            (
                MutationSite(
                    start_byte=0,
                    end_byte=1,
                    replacement=b"9",
                    lineno=1,
                    operator="boolop-swap",
                    description="unselected operator",
                ),
            ),
            "outside the selected policy",
        ),
        (
            tuple(reversed(_sites(2))),
            "not identity-ordered",
        ),
        (
            _sites(1) + _sites(1),
            "repeat an identity",
        ),
    ],
)
def test_a_wrong_adapter_batch_is_refused_before_any_job_is_built(sites, match: str):
    """An adapter is a language's own code; this is the boundary that stops
    a wrong one from poisoning the artifact's identities. Each row is a
    shape that would otherwise become a `MutantOutcome` a consumer has no
    source text to check."""
    adapter = _RecordingAdapter({"a.py": sites})

    with pytest.raises(MutationDiscoveryError, match=match):
        collect_mutation_sites(
            (_target("a.py", "x = 1\n", {1}),),
            adapter=adapter,
            operators=("compare-swap",),
            limit=5,
        )


@pytest.mark.parametrize(
    "limit,match",
    [
        (0, r"must be in 1\.\."),
        (10_002, r"must be in 1\.\."),
        ("5", "must be an integer"),
        (True, "must be an integer"),
    ],
)
def test_the_limit_itself_is_bounded(limit, match: str):
    """A-163's hard product ceiling: a malicious declared cap cannot create
    unbounded discovery work."""
    with pytest.raises(ValueError, match=match):
        collect_mutation_sites(
            (), adapter=PythonAdapter(), operators=("compare-swap",), limit=limit
        )


@pytest.mark.parametrize(
    "operators,match",
    [
        ((), "non-empty tuple"),
        (["compare-swap"], "non-empty tuple"),
        (("compare-swap", "compare-swap"), "duplicate"),
        (("invented-swap",), "unknown operator"),
    ],
)
def test_the_operator_policy_is_validated_here_too(operators, match: str):
    """`collect_mutation_sites` is a public surface a caller may invoke
    directly, so config's load-time discipline cannot be assumed to have
    run -- an unvalidated policy would silently become the bound handed to
    every adapter."""
    with pytest.raises(ValueError, match=match):
        collect_mutation_sites(
            (), adapter=PythonAdapter(), operators=operators, limit=5
        )


def test_the_real_python_adapter_collects_across_files_in_order():
    """One end-to-end pass through the REAL adapter, so the fake above is
    never the only witness (DESIGN-GUIDE: never mock the component under
    test in every test)."""
    jobs = collect_mutation_sites(
        (
            _target("z.py", "if a < b:\n    pass\n", {1}),
            _target("a.py", "if c and d:\n    pass\n", {1}),
        ),
        adapter=PythonAdapter(),
        operators=OPERATORS,
        limit=10,
    )

    assert [job.path for job in jobs] == ["a.py", "z.py"]
    assert [job.site.operator for job in jobs] == ["boolop-swap", "compare-swap"]
