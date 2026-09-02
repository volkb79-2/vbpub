"""B045/B038(a) — real branch ARCS from ``coverage-istanbul-json``, under a
declared arc-bearing producer.

Through schema v8 this parser answered ``branches = None`` for every record,
and A-344 recorded why: ``branchMap`` means one thing under
``@vitest/coverage-istanbul`` (typed entries, one location and one count per
ARM) and something else entirely under ``@vitest/coverage-v8``/``c8`` (every
entry typed ``branch``, one location, describing v8's executed RANGES), and
nothing in a lane's config said which. B045 makes the producer a DECLARED
fact, so the same bytes can now be read as arcs when — and only when — the
lane says who wrote them.

Everything asserted here about a real artifact is re-derivable from that
artifact's own bytes with ``jq``; the derivation is written out in each test's
own comment so a reviewer checks the FIXTURE, not this file. The two v8-shaped
documents are used as REAL negative evidence (A-334): they are not
hand-written approximations of a wrong artifact, they are the wrong artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assay.coverage import derive_branch_capability, load_coverage_profile
from assay.coverage_parsers import coverage_istanbul_json
from assay.errors import Outcome, ReasonCode
from assay.errors import AssayError
from assay.vocabulary import ARC_BEARING_COVERAGE_PRODUCERS

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "coverage"

FORMAT = "coverage-istanbul-json"


def _load(name: str, producer: str | None):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return load_coverage_profile(text, declared_format=FORMAT, producer=producer)


def _by_basename(profile) -> dict[str, dict[int, tuple[int, int]]]:
    """Arcs keyed by each record's file BASENAME.

    The artifacts' own keys are absolute paths naming the scratch directory
    the producing run happened in (A-341), so asserting on them would pin this
    test to a machine that no longer exists. The basename is the part that is
    a fact about the SOURCE.
    """
    return {
        Path(key).name: dict(sorted(record.branches.by_line.items()))
        for key, record in profile.files.items()
    }


# ---------------------------------------------------------------------------
# The two real istanbul-produced artifacts
# ---------------------------------------------------------------------------


def test_vitest_istanbul_arcs_are_read_under_the_declared_producer():
    """``jq`` re-derivation, per file: for each ``branchMap`` entry, pair
    ``locations[i]`` with ``b[id][i]``; the arm's line is
    ``locations[i].start.line`` when present, else the entry's own
    ``line // loc.start.line``; ``total`` counts arms, ``covered`` counts arms
    whose count is nonzero.

    ``branchy.ts`` is the readable one: line 2 is ``if (x > 0)`` with its
    consequent taken and its IMPLICIT ELSE not — ``(1, 2)`` — and line 8 is
    the ternary, neither arm taken, ``(0, 2)``.
    """
    profile = _load("coverage-istanbul-json.vitest-istanbul.json", "istanbul")
    assert _by_basename(profile) == {
        "Badge.tsx": {10: (0, 1), 11: (0, 1), 12: (0, 1), 13: (0, 1)},
        "branchy.ts": {2: (1, 2), 5: (1, 2), 8: (0, 2)},
        "format.ts": {9: (4, 5), 14: (0, 1), 15: (0, 5), 18: (0, 2), 34: (0, 4)},
        "hinted.ts": {3: (1, 2)},
        # A file with no branches at all: present, with an EMPTY mapping.
        # Absent-means-none is `BranchCoverage`'s contract one level down
        # (a line with no branch is absent from `by_line`); a FILE with no
        # branch still carries a `BranchCoverage`, because the producer could
        # have reported one and did — with nothing in it.
        "orphan.ts": {},
        "roles.ts": {18: (2, 2)},
    }


def test_vite_plugin_istanbul_arcs_are_read_under_the_declared_producer():
    """The second real producer of the same format, from a different
    toolchain (``vite-plugin-istanbul`` instrumenting at build time rather
    than ``@vitest/coverage-istanbul`` at test time). Both are the
    babel-plugin-istanbul family, and both must read identically — that is
    what makes ``istanbul`` one producer NAME rather than two."""
    profile = _load("coverage-istanbul-json.vite-plugin-istanbul.json", "istanbul")
    assert _by_basename(profile) == {"main.ts": {4: (1, 2)}, "math.ts": {5: (1, 2)}}


@pytest.mark.parametrize(
    "name",
    [
        "coverage-istanbul-json.vitest-istanbul.json",
        "coverage-istanbul-json.vite-plugin-istanbul.json",
    ],
)
def test_both_real_artifacts_satisfy_every_filecoverage_cross_bucket_invariant(
    name: str,
):
    """`FileCoverage.__post_init__` enforces three relations between arcs and
    line buckets — a branch line must be considered, must not be excluded, and
    a MISSING line can carry no covered arc. They are enforced at construction,
    so a violation would have raised above; this test states plainly that they
    hold on both real artifacts rather than leaving it implied, because
    B045's acceptance box asks for exactly that."""
    profile = _load(name, "istanbul")
    for record in profile.files.values():
        branch_lines = set(record.branches.by_line)
        assert branch_lines <= (record.executed | record.missing)
        assert all(
            record.branches.by_line[line][0] == 0
            for line in branch_lines & record.missing
        )


@pytest.mark.parametrize(
    "name",
    [
        "coverage-istanbul-json.vitest-istanbul.json",
        "coverage-istanbul-json.vite-plugin-istanbul.json",
    ],
)
def test_the_derived_arc_totals_equal_the_artifacts_own_arm_count(name: str):
    """The denominator is not invented: the number of arcs this parser
    derives equals the number of arm counts the artifact itself carries,
    ``sum(len(b[id]) for every record, every id)``. This is the guard against
    the failure mode a "skip what you do not understand" reduction would have:
    a smaller, greener denominator over an artifact that said more.
    """
    document = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    declared_arms = sum(
        len(counts)
        for record in document.values()
        for counts in record["b"].values()
    )
    derived = sum(
        total
        for record in _load(name, "istanbul").files.values()
        for _covered, total in record.branches.by_line.values()
    )
    assert derived == declared_arms
    covered_arms = sum(
        1
        for record in document.values()
        for counts in record["b"].values()
        for count in counts
        if count > 0
    )
    derived_covered = sum(
        covered
        for record in _load(name, "istanbul").files.values()
        for covered, _total in record.branches.by_line.values()
    )
    assert derived_covered == covered_arms


# ---------------------------------------------------------------------------
# Capability: the producer, not the format, decides
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "coverage-istanbul-json.vitest-istanbul.json",
        "coverage-istanbul-json.vite-plugin-istanbul.json",
    ],
)
def test_capability_is_reported_under_istanbul_and_unavailable_without_it(name: str):
    """The whole point of the declaration, in two lines. The SAME BYTES read
    as ``reported`` with the producer declared and ``unavailable`` without
    it — and `require_branch = true` is legal on exactly the first one.
    """
    assert derive_branch_capability(_load(name, "istanbul")) == "reported"
    assert derive_branch_capability(_load(name, None)) == "unavailable"


def test_the_arc_bearing_vocabulary_is_what_the_parser_actually_honours():
    """`ARC_BEARING_COVERAGE_PRODUCERS` is normative, not documentation: the
    parser consults it directly, so this test pins that every name in it
    really does turn arcs on, and that a name outside it does not. If a
    future producer is added to the vocabulary without the parser being able
    to read its `branchMap`, this fails."""
    assert ARC_BEARING_COVERAGE_PRODUCERS == frozenset({"istanbul"})
    for producer in ARC_BEARING_COVERAGE_PRODUCERS:
        profile = _load("coverage-istanbul-json.vitest-istanbul.json", producer)
        assert derive_branch_capability(profile) == "reported"
    for producer in (None, "coverage.py", "jest-v8"):
        profile = _load("coverage-istanbul-json.vitest-istanbul.json", producer)
        assert derive_branch_capability(profile) == "unavailable"


# ---------------------------------------------------------------------------
# The v8-shaped documents, as REAL negative evidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "what"),
    [
        ("coverage-istanbul-json.vitest-v8.json", "@vitest/coverage-v8"),
        ("coverage-istanbul-json.provider-defect.c8.json", "c8"),
    ],
)
def test_a_v8_range_document_declared_as_istanbul_is_refused(name: str, what: str):
    """A lane whose config says ``producer = "istanbul"`` while its command
    actually ran a v8-based provider is a real, reachable misconfiguration —
    and the artifact PROVES it, because every entry is typed ``branch`` with
    exactly one location. Refusing beats skipping: skipping the unrecognised
    entries would silently produce a branch percentage over an artifact assay
    could not read, which is the same silent-excuse direction A-344 refused
    for the whole feature.

    (``vitest-v8`` and ``c8`` are also refused BY NAME at config load, one
    layer earlier. This is the second, independent layer, and it is the one
    that catches a lane whose declaration and whose command have drifted
    apart — the name check cannot see that at all.)
    """
    with pytest.raises(AssayError) as excinfo:
        _load(name, "istanbul")
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    message = str(excinfo.value)
    assert "typed 'branch'" in message
    assert "executed RANGES" in message
    assert "judge.coverage.producer" in message


@pytest.mark.parametrize(
    "name",
    [
        "coverage-istanbul-json.vitest-v8.json",
        "coverage-istanbul-json.provider-defect.c8.json",
        "coverage-istanbul-json.provider-defect.vitest3-v8.json",
        "coverage-istanbul-json.provider-defect.vitest4-v8.json",
    ],
)
def test_a_v8_range_document_still_parses_with_no_producer_declared(name: str):
    """The refusal above is about ARCS, and must not become a refusal of the
    document. With no arc-bearing producer declared these artifacts parse
    exactly as they did before B045 — same line classification, ``branches``
    ``None`` — so nothing that read them under schema v8 changes."""
    profile = _load(name, None)
    assert profile.files
    assert all(record.branches is None for record in profile.files.values())
    assert derive_branch_capability(profile) == "unavailable"


# ---------------------------------------------------------------------------
# Synthetic malformed documents — one per validation, each unreachable from a
# real producer's output, which is why they are written by hand here
# ---------------------------------------------------------------------------


def _record(**overrides) -> str:
    """A minimal one-file istanbul document: one statement on line 1 that
    ran, and one two-armed ``if`` branch on line 1."""
    record = {
        "path": "/p/src/a.ts",
        "statementMap": {"0": {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 9}}},
        "s": {"0": 1},
        "fnMap": {},
        "f": {},
        "branchMap": {
            "0": {
                "type": "if",
                "loc": {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 9}},
                "locations": [
                    {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 9}},
                    {"start": {}, "end": {}},
                ],
            }
        },
        "b": {"0": [1, 0]},
    }
    record.update(overrides)
    return json.dumps({"/p/src/a.ts": record})


def test_the_synthetic_baseline_is_itself_valid():
    """Every negative below is this document with ONE thing changed, so the
    baseline must parse — otherwise a negative could be passing for the wrong
    reason."""
    profile = coverage_istanbul_json.parse(_record(), producer="istanbul")
    assert profile.files["/p/src/a.ts"].branches.by_line == {1: (1, 2)}


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"branchMap": None}, "'branchMap' is absent"),
        ({"branchMap": []}, "'branchMap' is list"),
        ({"b": None}, "'b' is absent"),
        ({"b": {"0": [1, 0], "1": [1]}}, "name different branch ids"),
        ({"b": {"0": [1]}}, "declares 2 arm location(s) but 1 arm count(s)"),
        ({"b": {"0": "10"}}, "b['0'] is str"),
        ({"b": {"0": [1, -1]}}, "a negative execution count"),
        ({"b": {"0": [1, "0"]}}, "b['0'][1] is str"),
    ],
)
def test_a_malformed_branch_payload_is_unreadable(overrides: dict, fragment: str):
    with pytest.raises(AssayError) as excinfo:
        coverage_istanbul_json.parse(_record(**overrides), producer="istanbul")
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert fragment in str(excinfo.value)


@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ("not-an-object", "branch '0' is str"),
        ({"type": "if", "loc": {"start": {"line": 1}}}, "'locations' = absent"),
        (
            {"type": "if", "loc": {"start": {"line": 1}}, "locations": {}},
            "'locations' = dict",
        ),
        (
            {"type": "if", "locations": [{"start": {}}]},
            "carries neither a 'line' nor a 'loc' object",
        ),
        (
            {"type": "if", "line": 0, "locations": [{"start": {}}]},
            "has line = 0, expected a positive integer",
        ),
        (
            {"type": "if", "line": 1, "locations": ["nope"]},
            "branch '0' arm 0 is str",
        ),
        (
            {"type": "if", "line": 1, "locations": [{"start": "nope"}]},
            "branch '0' arm 0 has no 'start' position object",
        ),
        (
            {"type": "if", "line": 1, "locations": [{"start": {"line": 0}}]},
            "branch '0' arm 0 has start.line = 0",
        ),
    ],
)
def test_a_malformed_branch_entry_is_unreadable(entry: object, fragment: str):
    with pytest.raises(AssayError) as excinfo:
        coverage_istanbul_json.parse(
            _record(branchMap={"0": entry}, b={"0": [1]}), producer="istanbul"
        )
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert fragment in str(excinfo.value)


def test_a_branch_entry_with_zero_arms_is_unreadable():
    """Its own test rather than a row in the table above, because the row's
    shared ``b = {"0": [1]}`` would trip the arm-count mismatch FIRST and this
    branch would never be reached — a test passing for the wrong reason."""
    with pytest.raises(AssayError) as excinfo:
        coverage_istanbul_json.parse(
            _record(
                branchMap={
                    "0": {"type": "if", "loc": {"start": {"line": 1}}, "locations": []}
                },
                b={"0": []},
            ),
            producer="istanbul",
        )
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "has zero arms" in str(excinfo.value)


def test_an_arc_on_a_line_no_statement_covers_is_unreadable():
    """`FileCoverage` invariant 3, reached through this parser rather than
    asserted on the dataclass: ``branchMap`` and ``statementMap`` are
    INDEPENDENT arrays in external input, so an artifact can name a branch on
    line 99 while classifying only line 1. Before B045 no istanbul artifact
    could violate a `FileCoverage` invariant at all and the construction
    carried no guard; the guard exists now, and this is what reaches it."""
    document = _record(
        branchMap={
            "0": {
                "type": "if",
                "line": 99,
                "locations": [{"start": {"line": 99}}, {"start": {"line": 99}}],
            }
        },
        b={"0": [1, 0]},
    )
    with pytest.raises(AssayError) as excinfo:
        coverage_istanbul_json.parse(document, producer="istanbul")
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "contradict its own" in str(excinfo.value)
    assert "in neither .executed nor .missing" in str(excinfo.value)


def test_a_covered_arc_on_a_never_executed_line_is_unreadable():
    """`FileCoverage` invariant 5, the anti-tamper one: a line whose statement
    count is 0 cannot have TAKEN a branch. Same construction as above with
    ``s`` flipped to 0, so the line lands in ``missing`` while the arc claims
    it was covered."""
    document = _record(s={"0": 0})
    with pytest.raises(AssayError) as excinfo:
        coverage_istanbul_json.parse(document, producer="istanbul")
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "cannot have taken an arc" in str(excinfo.value)


@pytest.mark.parametrize(
    "branch_type", ["if", "cond-expr", "binary-expr", "switch", "default-arg"]
)
def test_every_arm_structured_branch_type_is_accepted(branch_type: str):
    """The five types the babel-plugin-istanbul instrumenter emits. ``switch``
    and ``default-arg`` do not occur in either committed real artifact — the
    probe sources have neither construct — so they are covered here rather
    than left as a claim the code makes and nothing exercises."""
    document = _record(
        branchMap={
            "0": {
                "type": branch_type,
                "loc": {"start": {"line": 1}},
                "locations": [{"start": {"line": 1}}, {"start": {"line": 1}}],
            }
        },
        b={"0": [1, 0]},
    )
    profile = coverage_istanbul_json.parse(document, producer="istanbul")
    assert profile.files["/p/src/a.ts"].branches.by_line == {1: (1, 2)}


def test_an_arm_with_its_own_line_is_keyed_there_not_on_the_entrys_line():
    """The departure from ``istanbul-lib-coverage``'s own
    ``getBranchCoverageByLine``, which attributes an entire entry to ONE line
    (A-265: detail over metadata). A ``switch`` whose cases are on lines 2 and
    3 reports two lines here, where upstream would report one line with two
    arcs — and a consumer reading `survived`-style file:line detail wants the
    case, not the `switch` keyword.

    The implicit-else arm (no line of its own) still falls back to the entry's
    line, which is the MEASURED shape real output has.
    """
    document = _record(
        statementMap={
            "0": {"start": {"line": 1, "column": 0}, "end": {"line": 4, "column": 1}}
        },
        s={"0": 1},
        branchMap={
            "0": {
                "type": "switch",
                "loc": {"start": {"line": 1}},
                "locations": [
                    {"start": {"line": 2}},
                    {"start": {"line": 3}},
                    {"start": {}},
                ],
            }
        },
        b={"0": [1, 0, 0]},
    )
    profile = coverage_istanbul_json.parse(document, producer="istanbul")
    assert profile.files["/p/src/a.ts"].branches.by_line == {
        1: (0, 1),
        2: (1, 1),
        3: (0, 1),
    }


def test_a_malformed_branch_payload_is_invisible_without_an_arc_bearing_producer():
    """`branchMap` is not read at all unless the producer says to read it, so
    a document with a wrecked `branchMap` and a sound `statementMap` still
    parses for a lane that declared no producer. This is what keeps B045
    additive: nothing that parsed under v8 stops parsing."""
    profile = coverage_istanbul_json.parse(_record(branchMap=None, b=None), producer=None)
    assert profile.files["/p/src/a.ts"].branches is None
    assert profile.files["/p/src/a.ts"].executed == frozenset({1})
