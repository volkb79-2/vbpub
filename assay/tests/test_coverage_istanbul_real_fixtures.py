"""B036 — the two REAL ``vitest run --coverage`` documents under
``tests/fixtures/coverage/`` parse to the real numbers ``PROVENANCE.md``
documents, and their differences are what the design turns on.

These two artifacts are carver-owned evidence produced outside this repository
by real Vitest 3.2.4 runs against the committed ``fixtures/coverage/probe-js``
project — one per coverage provider, same program, same tests. They are never
edited and never regenerated at test time (no Node toolchain is used anywhere
in assay's suite, the same constraint DESIGN-GUIDE §10 states for Go).

Every number asserted here is a fact of the artifact's own bytes; grep the raw
file rather than this test to re-verify one. The comments name where.

Negative: a parser that reduces statements to their START line alone (which is
what `istanbul-lib-coverage`'s own ``getLineCoverage`` does) drops every
interior line asserted below from BOTH buckets; a parser that resolves nested
extents with go-cover's "executed wins" reports ``branchy.ts`` line 3 as
covered, when the artifact says its own statement ran zero times.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assay.coverage import (
    derive_branch_capability,
    derive_exclusion_capability,
    load_coverage_profile,
)
from assay.coverage_parsers.model import CoverageProfile

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "coverage"

V8_ARTIFACT = "coverage-istanbul-json.vitest-v8.json"
ISTANBUL_ARTIFACT = "coverage-istanbul-json.vitest-istanbul.json"
BOTH = (V8_ARTIFACT, ISTANBUL_ARTIFACT)


def _load(name: str) -> CoverageProfile:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return load_coverage_profile(text, declared_format="coverage-istanbul-json")


def _raw(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _by_source_path(profile: CoverageProfile) -> dict[str, object]:
    """*profile*'s records re-keyed by their ``probe-js``-relative tail.

    The artifact's own keys are the producing machine's ABSOLUTE paths (that
    is the format's real key shape, and the reason
    ``evaluate._to_repo_relative_key``'s absolute branch is what reconciles
    them); this test asserts about WHICH file a record describes, not about
    where it was produced.
    """
    return {
        key.split("/probe-js/", 1)[1]: record for key, record in profile.files.items()
    }


# --- the key shape ----------------------------------------------------------


@pytest.mark.parametrize("name", BOTH)
def test_every_record_is_keyed_by_an_absolute_filesystem_path(name: str):
    """The single fact that makes this format's path handling different from
    every other registered one — and the reason
    ``JavaScriptAdapter.normalize_coverage_key`` has nothing to strip."""
    profile = _load(name)
    assert profile.files
    assert all(key.startswith("/") for key in profile.files)
    assert all("/probe-js/src/" in key for key in profile.files)


# --- which files a real run measures at all ---------------------------------


def test_the_v8_provider_measures_every_source_file_including_never_imported_ones():
    """``orphan.ts`` is imported by nothing and still measured (``"all":
    true`` in the raw record); ``typesonly.ts`` is a type-only module and is
    measured with an EMPTY statement map. Both matter to
    ``has_executable_code``: under this provider it is barely ever consulted,
    because the artifact has a record for the file already."""
    measured = _by_source_path(_load(V8_ARTIFACT))
    assert set(measured) == {
        "src/Badge.tsx",
        "src/branchy.ts",
        "src/format.ts",
        "src/hinted.ts",
        "src/orphan.ts",
        "src/roles.ts",
        "src/typesonly.ts",
    }
    typesonly = measured["src/typesonly.ts"]
    assert typesonly.executed == frozenset()
    assert typesonly.missing == frozenset()

    raw = {
        key.split("/probe-js/", 1)[1]: record for key, record in _raw(V8_ARTIFACT).items()
    }
    assert raw["src/orphan.ts"]["all"] is True
    assert raw["src/typesonly.ts"]["statementMap"] == {}


def test_the_istanbul_provider_omits_the_type_only_module_entirely():
    """The same source tree, the other provider: a type-only module is absent
    rather than empty. This is the one shape where
    ``JavaScriptAdapter.has_executable_code`` would be consulted and would
    answer ``True`` (fail-closed) — documented in that adapter's own module
    docstring and tracked as B038, not silently excused."""
    measured = _by_source_path(_load(ISTANBUL_ARTIFACT))
    assert "src/typesonly.ts" not in measured
    assert set(measured) == {
        "src/Badge.tsx",
        "src/branchy.ts",
        "src/format.ts",
        "src/hinted.ts",
        "src/orphan.ts",
        "src/roles.ts",
    }


@pytest.mark.parametrize("name", BOTH)
def test_a_declaration_file_is_measured_by_neither_provider(name: str):
    """``probe-js/src/types.d.ts`` exists and is imported (``import type``),
    and appears in neither artifact — the measured basis for
    ``has_executable_code`` answering ``False`` for a ``.d.ts`` path."""
    assert (FIXTURES / "probe-js" / "src" / "types.d.ts").is_file()
    assert not any(key.endswith(".d.ts") for key in _load(name).files)


@pytest.mark.parametrize("name", BOTH)
def test_test_files_are_measured_by_neither_provider(name: str):
    """All three naming conventions the adapter's own ``is_test_path``
    recognises are present in the probe project and absent from both
    artifacts — the tool excludes them, and the adapter excludes them
    independently, which is the belt-and-braces this gate wants."""
    for relative in (
        "src/__tests__/roles.test.ts",
        "src/branchy.test.ts",
        "src/Badge.spec.tsx",
        "src/hinted.test.ts",
    ):
        assert (FIXTURES / "probe-js" / relative).is_file()
    measured = set(_by_source_path(_load(name)))
    assert not any(
        path.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")) for path in measured
    )
    assert not any("__tests__" in path for path in measured)


# --- the two providers' statement maps are structurally different -----------


def test_the_v8_provider_emits_only_single_line_statement_extents():
    """Why ``requires_span_attribution = False`` is trivially true under this
    provider: there is no multi-line extent anywhere in the artifact, so
    there is no interior line to attribute in the first place."""
    for record in _raw(V8_ARTIFACT).values():
        for location in record["statementMap"].values():
            assert location["start"]["line"] == location["end"]["line"]


def test_the_istanbul_provider_emits_real_multi_line_statement_extents():
    """And why that flag is NOT trivially true in general: this provider
    reproduces coverage.py's own multi-line-statement gap. ``format.ts``'s
    ``[24, 32]`` is the ten-line ``units`` array literal; ``[33, 37]`` is the
    ``for`` loop that contains ``[34, 36]``."""
    raw = {
        key.split("/probe-js/", 1)[1]: record
        for key, record in _raw(ISTANBUL_ARTIFACT).items()
    }
    extents = {
        (location["start"]["line"], location["end"]["line"])
        for location in raw["src/format.ts"]["statementMap"].values()
    }
    assert {(13, 15), (24, 32), (33, 37), (34, 36)} <= extents
    # ...and the interior lines have NO statement entry of their own, which is
    # exactly the gap the extent expansion closes.
    starts = {start for start, _end in extents}
    assert not ({25, 26, 27, 28, 29, 30, 31, 32} & starts)


def test_every_istanbul_end_position_carries_a_null_column():
    """A parser that validated the column would reject genuine output."""
    for record in _raw(ISTANBUL_ARTIFACT).values():
        for location in record["statementMap"].values():
            assert location["end"]["column"] is None


# --- the classification the parser derives from them ------------------------


def test_a_multi_line_object_literal_classifies_all_of_its_own_lines():
    """``roles.ts``'s ``export const ROLE_HIERARCHY = { ... }`` is ONE
    statement spanning lines 7-11 with count 1 in the istanbul artifact. All
    five lines are executed. Under a start-line-only reduction, 8-11 would be
    in neither bucket — and a diff touching one of them would be silently
    dropped from the denominator."""
    measured = _by_source_path(_load(ISTANBUL_ARTIFACT))
    assert {7, 8, 9, 10, 11} <= measured["src/roles.ts"].executed


def test_the_nested_never_taken_return_is_missing_not_covered():
    """The decisive resolution witness, in the real artifact: ``branchy.ts``
    statement ``[2, 4]`` (the whole ``if (n < 0) { ... }``) has count 1 and
    statement ``[3, 3]`` (its own ``return 'negative'``) has count 0. The test
    only ever calls ``branchy(0)``, so line 3 provably never ran.

    Innermost-wins classifies it missing. go-cover's "executed wins" merge
    would classify it EXECUTED — a false green — which is why the two rules
    are not interchangeable and why this file is a fixture."""
    raw = {
        key.split("/probe-js/", 1)[1]: record
        for key, record in _raw(ISTANBUL_ARTIFACT).items()
    }
    branchy = raw["src/branchy.ts"]
    outer = next(
        sid
        for sid, loc in branchy["statementMap"].items()
        if (loc["start"]["line"], loc["end"]["line"]) == (2, 4)
    )
    inner = next(
        sid
        for sid, loc in branchy["statementMap"].items()
        if (loc["start"]["line"], loc["end"]["line"]) == (3, 3)
    )
    assert branchy["s"][outer] == 1
    assert branchy["s"][inner] == 0

    measured = _by_source_path(_load(ISTANBUL_ARTIFACT))["src/branchy.ts"]
    assert 3 in measured.missing
    assert 3 not in measured.executed
    assert measured.executed == frozenset({2, 4, 5, 6, 7})
    assert measured.missing == frozenset({3, 8, 9})


def test_the_v8_provider_classifies_the_same_source_line_by_line():
    """The same file under the other provider: every executable physical line
    of ``branchy.ts`` is classified, including the function signature (line 1)
    and the closing braces the istanbul provider leaves untracked."""
    measured = _by_source_path(_load(V8_ARTIFACT))["src/branchy.ts"]
    assert measured.executed == frozenset({1, 2, 5, 6, 7, 8, 9, 10})
    assert measured.missing == frozenset({3, 4})


# --- capabilities, on real artifacts ----------------------------------------


@pytest.mark.parametrize("name", BOTH)
def test_both_capabilities_are_unavailable_on_a_real_artifact(name: str):
    profile = _load(name)
    assert derive_exclusion_capability(profile) == "unavailable"
    assert derive_branch_capability(profile) == "unavailable"


def test_an_istanbul_ignore_hint_leaves_no_exclusion_marker_to_read():
    """A-343's measured basis. ``probe-js/src/hinted.ts`` carries a real
    ``/* istanbul ignore next */`` above its ``if``; in the artifact that
    ``if`` is an ordinary statement with a live count and no ``skip`` marker
    appears anywhere in the document. So by the time this parser sees it, an
    "ignored" line is indistinguishable from a line that was never code —
    lcov's exact situation, and lcov's exact ``None`` answer."""
    source = (FIXTURES / "probe-js" / "src" / "hinted.ts").read_text(encoding="utf-8")
    assert "/* istanbul ignore next */" in source

    for name in BOTH:
        text = (FIXTURES / name).read_text(encoding="utf-8")
        assert "skip" not in text
        measured = _by_source_path(_load(name))["src/hinted.ts"]
        # The hinted `if` still classifies real lines in both providers, and
        # its never-taken `return` (line 4) is still reported as missing --
        # the hint changed nothing this parser can see.
        assert 4 in measured.missing
        assert measured.excluded is None


def test_the_two_providers_branch_maps_are_not_the_same_measurement():
    """A-344's measured basis, on ONE source file. Translating either shape
    into a ``BranchCoverage`` would put a number on the wire whose meaning
    depends on which provider ran — a fact no lane declares."""
    istanbul = {
        key.split("/probe-js/", 1)[1]: record
        for key, record in _raw(ISTANBUL_ARTIFACT).items()
    }["src/branchy.ts"]
    v8 = {
        key.split("/probe-js/", 1)[1]: record for key, record in _raw(V8_ARTIFACT).items()
    }["src/branchy.ts"]

    # istanbul: three real typed branch nodes, one location (arm) per arc.
    assert [entry["type"] for entry in istanbul["branchMap"].values()] == [
        "if",
        "if",
        "cond-expr",
    ]
    istanbul_arcs = sum(len(counts) for counts in istanbul["b"].values())
    istanbul_covered = sum(
        1 for counts in istanbul["b"].values() for count in counts if count > 0
    )
    assert (istanbul_arcs, istanbul_covered) == (6, 2)

    # v8: every entry typed "branch" with exactly ONE location -- these are
    # v8's own executed/unexecuted RANGES, not the arms of a branch.
    assert {entry["type"] for entry in v8["branchMap"].values()} == {"branch"}
    assert {len(entry["locations"]) for entry in v8["branchMap"].values()} == {1}
    v8_arcs = sum(len(counts) for counts in v8["b"].values())
    v8_covered = sum(1 for counts in v8["b"].values() for count in counts if count > 0)
    assert (v8_arcs, v8_covered) == (4, 1)

    # Same file, same tests: 2/6 versus 1/4. Not the same measurement.
    assert (istanbul_arcs, istanbul_covered) != (v8_arcs, v8_covered)


# --- ground truth: exactly which lines each provider leaves unattributed ----

#: Every non-comment, non-blank source line of the probe project that the
#: parser classifies into NEITHER `executed` nor `missing`, per provider.
#: Hand-derived from the committed artifacts and pinned literally, because
#: this is the one assertion in the suite that a parser-side regression
#: actually breaks: reducing `_paint` to `istanbul-lib-coverage`'s own
#: start-line-only `getLineCoverage` behaviour changes these sets
#: immediately, where an expectation read back out of the same profile would
#: move in lockstep and notice nothing (round-1 review, M2).
#:
#: The istanbul entries ARE the correction to A-342's original overclaim.
#: They are, file by file: every function declaration line (`branchy.ts:1`,
#: `hinted.ts:1`, `orphan.ts:1`, `roles.ts:17`, `format.ts:8`,
#: `Badge.tsx:8`), every function-level closing brace (`branchy.ts:10`,
#: `hinted.ts:7`, `orphan.ts:3`, `roles.ts:20`, `format.ts:39`), the
#: interface/type declarations TypeScript erases before instrumentation, and
#: `format.ts:12` -- a genuinely executable `const date =` line whose own
#: recorded statement starts at 13, on its initialiser. Those lines take
#: `evaluate.py`'s rule 4 and leave the denominator, exactly as an untracked
#: line does for every other format in this registry.
UNATTRIBUTED = {
    V8_ARTIFACT: {
        "src/Badge.tsx": [1, 3, 4, 5, 6, 18],
        "src/roles.ts": [1],
        "src/typesonly.ts": [1, 3, 4, 5, 6, 8],
    },
    ISTANBUL_ARTIFACT: {
        "src/Badge.tsx": [1, 3, 4, 5, 6, 8, 9, 18, 19, 21, 24],
        "src/branchy.ts": [1, 10],
        "src/format.ts": [8, 12, 39],
        "src/hinted.ts": [1, 7],
        "src/orphan.ts": [1, 3],
        "src/roles.ts": [1, 17, 20],
    },
}


def _unattributed_lines(name: str) -> dict[str, list[int]]:
    """Per measured file, the non-comment, non-blank source lines the parser
    put in neither bucket -- computed from the real committed artifact and
    the real committed source, never from an expectation."""
    measured = _by_source_path(_load(name))
    out: dict[str, list[int]] = {}
    for relative, record in measured.items():
        source = (FIXTURES / "probe-js" / relative).read_text(encoding="utf-8")
        classified = record.executed | record.missing
        lines = [
            number
            for number, text in enumerate(source.splitlines(), start=1)
            if text.strip()
            and not text.lstrip().startswith(("//", "/*", "*"))
            and number not in classified
        ]
        if lines:
            out[relative] = lines
    return out


@pytest.mark.parametrize("name", BOTH)
def test_the_unattributed_line_set_is_exactly_what_was_measured(name: str):
    assert _unattributed_lines(name) == UNATTRIBUTED[name]


def test_the_istanbul_provider_leaves_strictly_more_lines_unattributed():
    """The comparative fact A-342 now states instead of its original
    overclaim: 23 non-comment lines under istanbul against 13 under v8, and
    every one of the v8 thirteen is a type declaration TypeScript erases
    before any instrumenter sees it, while istanbul's include real signature
    lines, real closing braces, and one genuinely executable line."""
    v8_total = sum(len(lines) for lines in UNATTRIBUTED[V8_ARTIFACT].values())
    istanbul_total = sum(
        len(lines) for lines in UNATTRIBUTED[ISTANBUL_ARTIFACT].values()
    )

    assert (v8_total, istanbul_total) == (13, 23)
    assert _unattributed_lines(V8_ARTIFACT) == UNATTRIBUTED[V8_ARTIFACT]


def test_extent_expansion_classifies_strictly_more_than_start_lines_alone():
    """The measured size of what the expansion actually buys, on the real
    istanbul artifact -- the honest replacement for "leaves no line
    unattributed". `istanbul-lib-coverage`'s own `getLineCoverage` keys each
    statement by its START line only; the parser expands the whole extent.
    Both numbers are derived here from the artifact's own bytes."""
    raw = {
        key.split("/probe-js/", 1)[1]: record
        for key, record in _raw(ISTANBUL_ARTIFACT).items()
    }
    start_lines = {
        relative: {
            location["start"]["line"] for location in record["statementMap"].values()
        }
        for relative, record in raw.items()
    }
    expanded = {
        relative: record.executed | record.missing
        for relative, record in _by_source_path(_load(ISTANBUL_ARTIFACT)).items()
    }

    start_total = sum(len(lines) for lines in start_lines.values())
    expanded_total = sum(len(lines) for lines in expanded.values())

    # Every start line is still classified, and the expansion strictly adds.
    for relative, lines in start_lines.items():
        assert lines <= expanded[relative], relative
    assert (start_total, expanded_total) == (29, 54)

