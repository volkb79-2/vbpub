"""B036 — the istanbul ``coverage-final.json`` parser, driven through
:func:`assay.coverage.load_coverage_profile`.

No Node toolchain is used anywhere here (the same constraint §10 states for
Go): the artifacts this module builds inline are literal committed text in the
format's own grammar, and the two REAL ``vitest run --coverage`` documents
live in ``tests/fixtures/coverage/`` and are read by
``test_coverage_istanbul_real_fixtures.py``, never regenerated at test time.

Negative (line classification): collapsing statement EXTENTS to their start
line alone — `istanbul-lib-coverage`'s own ``getLineCoverage`` behaviour —
leaves a multi-line statement's continuation lines in neither ``executed`` nor
``missing``, silently dropping lines a human edited from both the numerator
and the denominator. Negative (resolution): resolving a nested extent with
go-cover's "executed wins" instead of innermost-wins reports a provably
never-executed line as covered — a false green, witnessed by the real
istanbul-provider fixture and reproduced literally below. Negative
(capability): reporting ``excluded=frozenset()`` or a ``BranchCoverage``
claims measurements this format's producers do not agree on making.
Negative (bounds): expanding an extent without a fixed ceiling lets a ~60-byte
record inside the 16 MiB read bound materialize a billion line entries.
"""

from __future__ import annotations

import json

import pytest

from assay.coverage import (
    FileCoverage,
    derive_branch_capability,
    derive_exclusion_capability,
    load_coverage_profile,
)
from assay.coverage_parsers import coverage_istanbul_json
from assay.errors import AssayError, Outcome, ReasonCode

FORMAT = "coverage-istanbul-json"


def artifact(records: dict) -> str:
    return json.dumps(records)


def one_file(statement_map: dict, counts: dict, *, path: str = "/repo/src/a.ts") -> str:
    return artifact(
        {
            path: {
                "path": path,
                "statementMap": statement_map,
                "fnMap": {},
                "branchMap": {},
                "s": counts,
                "f": {},
                "b": {},
            }
        }
    )


def span(start: int, end: int, *, end_column: object = 1) -> dict:
    return {
        "start": {"line": start, "column": 0},
        "end": {"line": end, "column": end_column},
    }


# --- sniffing ---------------------------------------------------------------


def test_sniff_accepts_a_json_object_carrying_a_statement_map():
    assert coverage_istanbul_json.sniff(one_file({"0": span(1, 1)}, {"0": 1}))


@pytest.mark.parametrize(
    "text",
    [
        "mode: set\ngithub.com/x/y.go:1.1,2.2 1 1\n",
        "SF:/repo/src/a.ts\nDA:1,1\nend_of_record\n",
        '{"files": {"a.py": {"executed_lines": [1], "missing_lines": []}}}',
        "",
        "  \n",
    ],
)
def test_sniff_rejects_every_other_registered_formats_own_artifact(text: str):
    """A coverage.py JSON document is the one that could plausibly collide —
    both are JSON objects — and it cannot, because it has no ``statementMap``
    anywhere and this one has no top-level ``files`` key."""
    assert not coverage_istanbul_json.sniff(text)


def test_a_coverage_py_document_declared_as_istanbul_is_a_format_mismatch():
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(
            '{"files": {"a.py": {"executed_lines": [1], "missing_lines": []}}}',
            declared_format=FORMAT,
        )
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.FORMAT_MISMATCH


# --- line classification ----------------------------------------------------


def test_a_single_line_statement_classifies_only_its_own_line():
    profile = load_coverage_profile(
        one_file({"0": span(4, 4), "1": span(6, 6)}, {"0": 3, "1": 0}),
        declared_format=FORMAT,
    )
    assert profile.files["/repo/src/a.ts"] == FileCoverage(
        executed=frozenset({4}), missing=frozenset({6}), excluded=None, branches=None
    )


def test_a_multi_line_statement_classifies_every_line_of_its_own_extent():
    """The whole reason this parser reads extents rather than start lines:
    lines 8-10 are a human-edited continuation of one statement, and
    `getLineCoverage`'s start-line-only reduction would leave them in neither
    bucket."""
    profile = load_coverage_profile(
        one_file({"0": span(7, 10)}, {"0": 0}), declared_format=FORMAT
    )
    assert profile.files["/repo/src/a.ts"] == FileCoverage(
        executed=frozenset(),
        missing=frozenset({7, 8, 9, 10}),
        excluded=None,
        branches=None,
    )


def test_a_nested_extent_wins_over_the_one_containing_it():
    """The load-bearing resolution rule, reproduced from the REAL
    ``@vitest/coverage-istanbul`` shape of ``probe-js/src/branchy.ts``: the
    whole ``if`` (lines 2-4) ran once, its own ``return`` (line 3) never did.
    Innermost wins, so line 3 is missing. A go-cover-style "executed wins"
    merge would call it covered — a false green on a line that provably never
    ran."""
    profile = load_coverage_profile(
        one_file({"0": span(2, 4), "1": span(3, 3)}, {"0": 1, "1": 0}),
        declared_format=FORMAT,
    )
    assert profile.files["/repo/src/a.ts"] == FileCoverage(
        executed=frozenset({2, 4}),
        missing=frozenset({3}),
        excluded=None,
        branches=None,
    )


def test_three_levels_of_nesting_resolve_innermost_first():
    """`format.ts` measured by the istanbul provider really does carry
    ``[33,37]`` containing ``[34,36]``; a third level is added here so the
    rule is proven as containment, not as a two-case special case."""
    profile = load_coverage_profile(
        one_file(
            {"0": span(1, 9), "1": span(3, 7), "2": span(5, 5)},
            {"0": 4, "1": 0, "2": 2},
        ),
        declared_format=FORMAT,
    )
    assert profile.files["/repo/src/a.ts"] == FileCoverage(
        executed=frozenset({1, 2, 5, 8, 9}),
        missing=frozenset({3, 4, 6, 7}),
        excluded=None,
        branches=None,
    )


@pytest.mark.parametrize("counts", [{"0": 1, "1": 0}, {"0": 0, "1": 1}])
def test_two_statements_sharing_one_extent_resolve_to_the_larger_count(counts: dict):
    """``if (x) return y`` on one line is two statements with one extent.
    `istanbul-lib-coverage`'s own ``getLineCoverage`` takes the MAX count for
    a line, and this follows it — parametrized both ways so the answer cannot
    be an artifact of which statement happens to be iterated last."""
    profile = load_coverage_profile(
        one_file({"0": span(5, 5), "1": span(5, 5)}, counts), declared_format=FORMAT
    )
    assert profile.files["/repo/src/a.ts"] == FileCoverage(
        executed=frozenset({5}), missing=frozenset(), excluded=None, branches=None
    )


def test_a_record_with_an_empty_statement_map_measures_zero_lines():
    """Real ``@vitest/coverage-v8`` output for a type-only TypeScript module
    (``probe-js/src/typesonly.ts``). A legitimate record measuring nothing —
    NOT an absent one, and not an error: it reaches evaluation as a real
    entry contributing zero executable lines, which is exactly how the NoCode
    case should read."""
    profile = load_coverage_profile(
        one_file({}, {}), declared_format=FORMAT
    )
    assert profile.files["/repo/src/a.ts"] == FileCoverage(
        executed=frozenset(), missing=frozenset(), excluded=None, branches=None
    )


def test_absolute_keys_are_returned_exactly_as_the_artifact_spells_them():
    """``CoverageProfile`` keys files "exactly as that format's artifact names
    it" (the model's own contract) — no repo-relative resolution here. The
    core's ``evaluate._to_repo_relative_key`` owns that, and
    ``JavaScriptAdapter.normalize_coverage_key`` deliberately owns nothing."""
    key = "/build/agent/7/applications/ui/src/App.tsx"
    profile = load_coverage_profile(
        one_file({"0": span(1, 1)}, {"0": 1}, path=key), declared_format=FORMAT
    )
    assert set(profile.files) == {key}


# --- capabilities -----------------------------------------------------------


def test_exclusion_capability_is_unavailable_for_this_format():
    """A-343: no producer's output carries a per-line exclusion field, so
    ``frozenset()`` — "zero exclusions, verified" — would be a claim nothing
    verified."""
    profile = load_coverage_profile(
        one_file({"0": span(1, 1)}, {"0": 1}), declared_format=FORMAT
    )
    assert profile.files["/repo/src/a.ts"].excluded is None
    assert derive_exclusion_capability(profile) == "unavailable"


def test_branch_capability_is_unavailable_even_when_the_artifact_has_a_branch_map():
    """A-344, and the reason this is a MEASURED property rather than an
    omission: the artifact below carries a populated ``branchMap``, and this
    parser still reports ``None``, because the two real producers of this
    format disagree about what that map means."""
    text = artifact(
        {
            "/repo/src/a.ts": {
                "path": "/repo/src/a.ts",
                "statementMap": {"0": span(1, 1)},
                "fnMap": {},
                "branchMap": {
                    "0": {
                        "type": "if",
                        "loc": span(1, 1),
                        "locations": [span(1, 1), {"start": {}, "end": {}}],
                    }
                },
                "s": {"0": 1},
                "f": {},
                "b": {"0": [1, 0]},
            }
        }
    )
    profile = load_coverage_profile(text, declared_format=FORMAT)
    assert profile.files["/repo/src/a.ts"].branches is None
    assert derive_branch_capability(profile) == "unavailable"


# --- refusals ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, fragment",
    [
        ('{"a": {"statementMap": {}, "s": {}}', "not valid JSON"),
        ('{"a": 3, "b": {"statementMap": {}}}', "expected object"),
        ('{"a": {"statementMap": [], "s": {}}}', "'statementMap' is list"),
        ('{"a": {"statementMap": {}}}', "'s' is absent"),
        ('{"a": {"statementMap": {}, "s": []}}', "'s' is list"),
    ],
)
def test_a_malformed_document_is_an_unreadable_artifact(text: str, fragment: str):
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(text, declared_format=FORMAT)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert fragment in str(excinfo.value)


def test_a_non_object_top_level_is_refused_by_the_parser_itself():
    """Unreachable through :func:`load_coverage_profile` — the sniff already
    requires a leading ``{`` — so this drives the parser directly, exactly as
    :mod:`assay.coverage_parsers.coverage_py_json`'s own equivalent guard is
    reached: a caller that skips the registry still gets a typed refusal
    rather than an ``AttributeError`` from inside a record loop."""
    with pytest.raises(AssayError) as excinfo:
        coverage_istanbul_json.parse('["statementMap"]')
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "top level is list" in str(excinfo.value)


def test_a_truncated_artifact_is_refused_rather_than_partially_read():
    """The shape a killed test runner actually leaves behind: a document that
    starts out well-formed and stops mid-record."""
    complete = one_file({"0": span(1, 1), "1": span(2, 2)}, {"0": 1, "1": 0})
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(complete[: len(complete) // 2], declared_format=FORMAT)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "not valid JSON" in str(excinfo.value)


@pytest.mark.parametrize(
    "statement_map, counts, fragment",
    [
        ({"0": span(1, 1)}, {"1": 1}, "name different statement ids"),
        ({"0": span(1, 1), "1": span(2, 2)}, {"0": 1}, "name different statement ids"),
        ({"0": 7}, {"0": 1}, "is int, expected object"),
        ({"0": {"end": {"line": 1}}}, {"0": 1}, "no 'start' position object"),
        ({"0": {"start": {"line": 1}}}, {"0": 1}, "no 'end' position object"),
        ({"0": {"start": {}, "end": {"line": 1}}}, {"0": 1}, "start.line = None"),
        (
            {"0": {"start": {"line": "3"}, "end": {"line": 3}}},
            {"0": 1},
            "start.line = '3'",
        ),
        (
            {"0": {"start": {"line": True}, "end": {"line": 3}}},
            {"0": 1},
            "start.line = True",
        ),
        ({"0": span(0, 2)}, {"0": 1}, "not a positive line number"),
        ({"0": span(-1, 2)}, {"0": 1}, "not a positive line number"),
        ({"0": span(9, 4)}, {"0": 1}, "before it starts on line 9"),
        ({"0": span(1, 1)}, {"0": "1"}, "expected int"),
        ({"0": span(1, 1)}, {"0": True}, "expected int"),
        ({"0": span(1, 1)}, {"0": -2}, "a negative execution count"),
    ],
)
def test_a_malformed_record_is_refused_by_name(
    statement_map: dict, counts: dict, fragment: str
):
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(one_file(statement_map, counts), declared_format=FORMAT)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert fragment in str(excinfo.value)


def test_a_null_end_column_is_accepted_because_real_output_writes_one():
    """The must-succeed control paired with the refusals above: real
    ``@vitest/coverage-istanbul`` output writes ``"column": null`` on every
    end position, so a parser validating the column would reject its genuine
    output. Only ``line`` is read."""
    profile = load_coverage_profile(
        one_file({"0": span(3, 5, end_column=None)}, {"0": 1}), declared_format=FORMAT
    )
    assert profile.files["/repo/src/a.ts"].executed == frozenset({3, 4, 5})


def test_one_enormous_extent_is_refused_rather_than_expanded():
    """O4's fixed bound, one level below the 16 MiB read limit: this ~100-byte
    record sits far inside that limit and would otherwise materialize a
    billion line classifications."""
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(
            one_file({"0": span(1, 999_999_999)}, {"0": 1}), declared_format=FORMAT
        )
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "classified lines" in str(excinfo.value)


@pytest.fixture
def tiny_bound(monkeypatch: pytest.MonkeyPatch) -> int:
    """:data:`~assay.coverage_parsers.coverage_istanbul_json.MAX_CLASSIFIED_LINES`
    lowered to a handful of lines for the boundary tests.

    Round-1 review, Minor: the previous must-succeed control drove a REAL
    two-million-line extent, peaking at ~352 MB of resident memory on every
    suite run, to prove an off-by-one in a single ``<`` comparison. The
    comparison is identical at any ceiling, and :func:`.parse` reads the
    constant at call time, so lowering it proves the same arithmetic for
    nothing. The SHIPPED value's own justification is a product argument
    about real artifact sizes and lives in the constant's docstring, where
    the memory it implies is also stated — it is not something a test should
    be paying to re-enact.
    """
    monkeypatch.setattr(coverage_istanbul_json, "MAX_CLASSIFIED_LINES", 12)
    return 12


def test_the_bound_is_spent_across_the_whole_document_not_per_record(
    tiny_bound: int,
):
    """A document made of many small records is refused by the same counter
    that refuses one huge extent — the bound is an ARTIFACT property. Neither
    record here would breach it alone."""
    per_record = tiny_bound // 2 + 1
    text = artifact(
        {
            f"/repo/src/{index}.ts": {
                "statementMap": {"0": span(1, per_record)},
                "s": {"0": 1},
            }
            for index in range(2)
        }
    )
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(text, declared_format=FORMAT)
    assert "classified lines" in str(excinfo.value)


def test_an_extent_exactly_at_the_bound_still_parses(tiny_bound: int):
    """The paired must-succeed control, and the real off-by-one pin: EXACTLY
    the ceiling is allowed, so the refusal is strictly "past it", not "at
    it"."""
    profile = load_coverage_profile(
        one_file({"0": span(1, tiny_bound)}, {"0": 1}), declared_format=FORMAT
    )
    assert len(profile.files["/repo/src/a.ts"].executed) == tiny_bound


def test_one_line_past_the_bound_is_refused(tiny_bound: int):
    """The other side of the same off-by-one, which the old two-million-line
    control never pinned at all: ceiling + 1 refuses."""
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(
            one_file({"0": span(1, tiny_bound + 1)}, {"0": 1}), declared_format=FORMAT
        )
    assert "classified lines" in str(excinfo.value)


def test_the_shipped_bound_is_the_documented_value():
    """The constant is a product decision (see its own docstring for why two
    million and not something tighter), so it is pinned literally — the tests
    above deliberately run against a monkeypatched ceiling and would
    otherwise say nothing about what actually ships."""
    assert coverage_istanbul_json.MAX_CLASSIFIED_LINES == 2_000_000


def test_fields_this_parser_does_not_read_are_ignored_not_rejected():
    """``fnMap``/``f``/``branchMap``/``b``/``path``/``all`` are legal record
    content this parser has no question for. Ignoring them (lcov's own rule
    for unread record types) keeps this parser from being STRICTER than the
    format, including against fields a future istanbul version adds."""
    text = artifact(
        {
            "/repo/src/a.ts": {
                "path": "/somewhere/else/entirely.ts",
                "all": True,
                "statementMap": {"0": span(1, 1)},
                "s": {"0": 1},
                "fnMap": {"0": {"name": "f", "decl": span(1, 1), "loc": span(1, 3)}},
                "f": {"0": 0},
                "branchMap": {},
                "b": {},
                "aFieldFromTheFuture": [1, 2, 3],
            }
        }
    )
    profile = load_coverage_profile(text, declared_format=FORMAT)
    assert set(profile.files) == {"/repo/src/a.ts"}
    assert profile.files["/repo/src/a.ts"].executed == frozenset({1})
