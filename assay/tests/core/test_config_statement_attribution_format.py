"""A-406 (DA-R1) -- a language judged from block extents may only declare a
coverage format that CARRIES them, and the refusal is at CONFIG LOAD.

**The hole this closes was reachable, not theoretical.** ``judge.language`` and
``judge.coverage.format`` are independent by design, so nothing stopped a Go
lane from declaring ``format = "lcov"``. lcov CONVERTED from a Go coverprofile
(the ``gcov2lcov`` family) does the naive block expansion -- a block's whole
extent becomes executable lines, so function signatures, closing braces and
``case`` labels are counted as code -- which is exactly the over-approximation
A-392 exists to refuse. And it arrives carrying no ``blocks`` at all, so
``runner._attribute_statements_for_lane`` used to mark it
``statement_attributed=True`` vacuously, with no oracle and no ``helpers``
entry, and A-392's guard -- whose whole purpose is to make skipping the oracle
impossible -- waved it through. Found by adversarial review round 1
(should-fix 3), ruled by DA-R1.

Two independent guards now, and this module tests both plus the fact they are
derived from:

1. **load time** (`assay.config`): the lane never runs. Cheapest and earliest;
   the fault is a property of the file, not of any artifact.
2. **run time** (`assay.runner`): a block-less profile reaching a
   ``requires_statement_attribution`` adapter is refused rather than passed
   through. Kept even though (1) makes it unreachable through the CLI, because
   a library caller can hand-build a ``Lane``.
"""

from __future__ import annotations

import pytest
from conftest import R1_LANE, Project, set_key

from assay.cli import _built_in_registry
from assay.config import load_lane_file
from assay.errors import LaneConfigError, Outcome, ReasonCode
from assay.vocabulary import STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE


def _go_lane(coverage_table: str) -> str:
    return set_key(
        set_key(R1_LANE, "language", '"go"'), "coverage", coverage_table
    )


def test_the_reviewers_probe_is_refused_at_load(project: Project):
    """The exact lane the round-1 reviewer built: a Go lane declaring
    ``lcov``, whose keys carry the module prefix so the key join would have
    worked and the profile would have been judged.

    It is refused before anything runs, and the message carries the three
    facts a consumer needs: which language, which format they declared, and
    which format they can use instead."""
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(
            project.write(_go_lane('{ format = "lcov", artifact = "cov.info" }'))
        )

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    message = str(excinfo.value)
    assert "'go'" in message
    assert "'lcov'" in message
    assert "go-cover" in message
    # The producers are named too, because "declare go-cover instead" leaves
    # a consumer one lookup short of a working lane.
    assert "go-test" in message and "covdata" in message


@pytest.mark.parametrize(
    "fmt, artifact",
    [
        ("coverage-py-json", "cov.json"),
        ("coverage-istanbul-json", "cov.json"),
        ("lcov", "cov.info"),
        ("cobertura", "cov.xml"),
    ],
)
def test_every_block_less_format_is_refused_for_go(
    project: Project, fmt: str, artifact: str
):
    """Parametrized over EVERY other registered format rather than lcov
    alone: a check that only knew about the one format the reviewer happened
    to probe would leave the same hole open three doors down.

    No ``producer`` is declared even for ``coverage-istanbul-json``, which
    normally requires one. That is deliberate and is itself an assertion
    about ORDER: this refusal fires before ``_load_coverage_producer``, so a
    consumer whose format is wrong for their language is told THAT rather
    than being sent to fix a producer key on a format they must not use."""
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(
            project.write(
                _go_lane(f'{{ format = "{fmt}", artifact = "{artifact}" }}')
            )
        )

    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "carries no block extents" in str(excinfo.value)


def test_a_go_lane_declaring_go_cover_still_loads(project: Project):
    """The anti-vacuity control. A refusal test alone would also pass if the
    check refused EVERY Go lane, which would be a worse defect than the one
    it fixes."""
    lane_file = load_lane_file(
        project.write(_go_lane('{ format = "go-cover", artifact = "cov.out" }'))
    )

    judge = lane_file.lane("package").judge
    assert judge is not None and judge.coverage is not None
    assert judge.coverage.format == "go-cover"


@pytest.mark.parametrize(
    "language, fmt, artifact",
    [
        ("python", "coverage-py-json", "cov.json"),
        ("javascript", "lcov", "cov.info"),
        ("sql", "lcov", "cov.info"),
    ],
)
def test_no_other_language_is_constrained(
    project: Project, language: str, fmt: str, artifact: str
):
    """The constraint is scoped to languages whose adapter is judged from
    block extents, and today that is Go alone. A language absent from the
    mapping places NO constraint on the format -- Python reading lcov, or
    JavaScript reading anything a converter emits, is a supported shape and
    this check must not narrow it."""
    lane = set_key(
        set_key(R1_LANE, "language", f'"{language}"'),
        "coverage",
        f'{{ format = "{fmt}", artifact = "{artifact}" }}',
    )

    lane_file = load_lane_file(project.write(lane))

    judge = lane_file.lane("package").judge
    assert judge is not None and judge.coverage is not None
    assert judge.coverage.format == fmt


def test_the_vocabulary_fact_agrees_with_every_registered_adapter():
    """**The drift guard, and the reason it is worth its weight.**

    ``STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE`` and
    ``LanguageAdapter.requires_statement_attribution`` are two statements of
    one fact, held apart because ``assay.config`` must not import
    ``assay.adapters`` (``assay.registry``'s own O2 guarantee: no adapter
    import anywhere in the loader's dependency cone). Two statements of one
    fact drift; this test is what stops them.

    Derived from the CLI's built-in registry rather than from a literal list,
    so a language registered later is checked by this test the day it lands
    -- the derivation shape A-400 had to repair when it was written the other
    way round. The vacuity guard matters as much as the equality: an empty
    registry, or one no adapter requires attribution in, would satisfy the
    equality trivially."""
    entries = _built_in_registry().entries

    requiring = {
        name
        for name, entry in entries.items()
        if entry.adapter.requires_statement_attribution
    }

    assert requiring, (
        "no registered adapter requires statement attribution, so this test "
        "checks nothing -- the mapping it guards may now be dead code"
    )
    assert requiring == set(STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE)
    # And every format named is one the parser registry really knows, so the
    # refusal's "declare instead" can never point at a format that would
    # itself be rejected one check earlier.
    from assay.coverage import FORMAT_REGISTRY

    for formats in STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE.values():
        assert formats
        assert formats <= set(FORMAT_REGISTRY)
