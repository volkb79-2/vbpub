"""B080/A-456: default-argument lines use function calls, never default hits.

The committed ChartCard specimen is the only consumer artifact input. Variants
below change one fact at a time; no test reads a consumer checkout. The CLI
numbers and the B054 witnesses also run under the controlled M1/M2 breaks
recorded in the P80 handoff's implementation evidence.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from assay.coverage_parsers import coverage_istanbul_json as parser
from assay.coverage_parsers.model import BranchCoverage, FileCoverage
from assay.errors import AssayError, Outcome, ReasonCode

FIXTURE = Path(__file__).parent / "fixtures/coverage/coverage-istanbul-json.default-arg-signature.json"
KEY = "/fixture/src/ChartCard.tsx"


def specimen() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))[KEY]


def parse(record: dict):
    return parser.parse(json.dumps({KEY: record}), producer="istanbul").files[KEY]


def multiline_specimen() -> dict:
    """A node on 34 whose default expression and nested return start on 35.

    The arm's line already has a statement, so the old parser did not refuse
    it. Counting the node line too would change a prior 0/0 signature diff.
    This synthetic hostile shape motivated the operator's A-459 narrowing.
    """
    record = specimen()
    record["fnMap"]["0"]["loc"]["start"] = {"line": 36, "column": 0}
    record["branchMap"]["0"]["loc"]["end"] = {"line": 35, "column": 32}
    record["branchMap"]["0"]["locations"][0] = {
        "start": {"line": 35, "column": 2}, "end": {"line": 35, "column": 32}
    }
    record["statementMap"]["0"] = {
        "start": {"line": 35, "column": 11}, "end": {"line": 35, "column": 26}
    }
    record["s"]["0"] = 9
    return record


def test_a_multiline_default_without_a_matching_arc_keeps_its_node_unclassified():
    result = parse(multiline_specimen())
    assert result.executed == frozenset({35})
    assert result.missing == frozenset()
    assert result.branches.by_line == {35: (1, 1)}
    assert result.contradictory_branch_lines is None


def test_no_matching_arc_leaves_function_metadata_and_node_column_unread():
    record = multiline_specimen()
    record["fnMap"] = "unread"
    record["f"] = None
    record["branchMap"]["0"]["loc"]["start"]["column"] = None
    result = parse(record)
    assert result.executed == frozenset({35})
    assert result.branches.by_line == {35: (1, 1)}
    assert result.contradictory_branch_lines is None


@pytest.mark.parametrize(
    "start,entry_line,arc_line",
    [({"line": 34}, 35, 34), ({}, 34, 34), ({}, None, 34),
     ({"line": 35}, 34, 35), ({}, 35, 35)],
)
def test_matching_uses_the_existing_per_arm_attribution(start, entry_line, arc_line):
    record = multiline_specimen()
    entry = record["branchMap"]["0"]
    entry["locations"][0]["start"] = start
    if entry_line is not None:
        entry["line"] = entry_line
    result = parse(record)
    assert result.executed == (frozenset({34, 35}) if arc_line == 34 else frozenset({35}))
    assert result.branches.by_line == {arc_line: (1, 1)}
    assert result.contradictory_branch_lines is None


def test_one_matching_arm_is_enough_and_other_arms_are_preserved():
    record = multiline_specimen()
    record["branchMap"]["0"]["locations"].append({"start": {"line": 34}})
    record["b"]["0"].append(0)
    result = parse(record)
    assert result.executed == frozenset({34, 35})
    assert result.branches.by_line == {34: (0, 1), 35: (1, 1)}
    assert result.contradictory_branch_lines is None


def test_a_sibling_branch_on_the_node_line_does_not_qualify_the_default():
    record = multiline_specimen()
    record["branchMap"]["1"] = {
        "type": "if", "line": 34, "locations": [{"start": {"line": 34}}]
    }
    record["b"]["1"] = [0]
    result = parse(record)
    assert result.executed == frozenset({35})
    assert result.missing == frozenset()
    assert result.branches.by_line == {35: (1, 1)}
    assert result.contradictory_branch_lines == frozenset({34})


@pytest.mark.parametrize("calls,default_hits", [(9, 9), (9, 0), (0, 0)])
def test_function_calls_classify_the_signature_and_keep_default_arcs(calls, default_hits):
    """A default can be unused even when the function ran. Only f distinguishes
    that ordinary 1/1 line, 0/1 branch from an entirely uncalled function.
    """
    record = specimen()
    record["f"]["0"] = calls
    record["b"]["0"] = [default_hits]
    result = parse(record)
    assert result.executed == (frozenset({34}) if calls else frozenset())
    assert result.missing == (frozenset() if calls else frozenset({34}))
    assert result.branches.by_line == {34: (int(default_hits > 0), 1)}
    assert result.contradictory_branch_lines is None


def test_tampered_missing_raises_independently_at_filecoverage():
    """Invariant 5 must stay green when M1 removes invariant 3. Parser-level
    checks cannot prove this: B054 isolates their contradiction instead.
    """
    with pytest.raises(ValueError, match="in .missing with a nonzero covered arc count"):
        FileCoverage(
            executed=frozenset(), missing=frozenset({34}), excluded=None,
            branches=BranchCoverage(by_line=MappingProxyType({34: (1, 1)})),
        )


def test_an_uncalled_function_with_a_taken_default_keeps_the_b054_tamper_disposition():
    record = specimen()
    record["f"]["0"] = 0
    result = parse(record)
    assert result.missing == frozenset({34})
    assert result.branches.by_line == {}
    assert result.contradictory_branch_lines == frozenset({34})


@pytest.mark.parametrize("statement_count", [0, 1])
def test_statements_win_and_do_not_need_function_metadata(statement_count):
    record = specimen()
    record["statementMap"] = {"0": {"start": {"line": 34}, "end": {"line": 34}}}
    record["s"] = {"0": statement_count}
    record["b"]["0"] = [0]
    record["fnMap"] = "unread"
    record["f"] = ["unread"]
    result = parse(record)
    assert result.executed == (frozenset({34}) if statement_count else frozenset())
    assert result.missing == (frozenset() if statement_count else frozenset({34}))
    assert result.branches.by_line == {34: (0, 1)}


def test_no_default_node_leaves_function_metadata_unread():
    record = specimen()
    record["branchMap"] = {}
    record["b"] = {}
    record["fnMap"] = False
    record.pop("f")
    result = parse(record)
    assert result.executable == frozenset()
    assert result.branches.by_line == {}


@pytest.mark.parametrize("producer", [None, "coverage.py", "jest-v8"])
def test_no_arc_bearing_producer_leaves_function_metadata_unread(producer):
    record = specimen()
    record["fnMap"] = False
    record.pop("f")
    result = parser.parse(json.dumps({KEY: record}), producer=producer).files[KEY]
    assert result.executable == frozenset()
    assert result.branches is None


@pytest.mark.parametrize("column,matches", [(16, True), (103, True), (15, False), (104, False)])
def test_full_positions_include_declaration_and_exclude_body(column, matches):
    record = specimen()
    record["branchMap"]["0"]["loc"]["start"]["column"] = column
    if matches:
        assert parse(record).executed == frozenset({34})
    else:
        with pytest.raises(AssayError, match="maps to 0 functions") as error:
            parse(record)
        assert error.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize("calls", [(0, 0), (9, 0), (0, 4)])
@pytest.mark.parametrize("reverse", [False, True])
def test_two_functions_on_one_line_use_columns_and_any_positive_call(calls, reverse):
    """Line-only matching is ambiguous. Keeping the original statement set
    fixed also prevents the first default from hiding the second one's hits.
    """
    record = specimen()
    record["fnMap"]["1"] = copy.deepcopy(record["fnMap"]["0"])
    record["fnMap"]["1"]["decl"]["start"]["column"] = 110
    record["fnMap"]["1"]["loc"]["start"]["column"] = 200
    record["branchMap"]["1"] = copy.deepcopy(record["branchMap"]["0"])
    record["branchMap"]["1"]["loc"]["start"]["column"] = 150
    record["f"] = dict(zip(("0", "1"), calls))
    record["b"] = {"0": [0], "1": [0]}
    if reverse:
        record["branchMap"] = dict(reversed(list(record["branchMap"].items())))
    result = parse(record)
    assert result.executed == (frozenset({34}) if any(calls) else frozenset())
    assert result.missing == (frozenset() if any(calls) else frozenset({34}))
    assert result.branches.by_line == {34: (0, 2)}


def test_an_unrelated_function_count_is_not_read():
    record = specimen()
    record["fnMap"]["1"] = copy.deepcopy(record["fnMap"]["0"])
    record["fnMap"]["1"]["decl"]["start"]["line"] = 94
    record["fnMap"]["1"]["loc"]["start"]["line"] = 95
    record["f"]["1"] = "unread"
    assert parse(record).executed == frozenset({34})


@pytest.mark.parametrize("bad", [None, [], "9", True, -1, 1.5])
def test_malformed_function_counts_are_unreadable(bad):
    record = specimen()
    record["f"]["0"] = bad
    with pytest.raises(AssayError, match="function call count") as error:
        parse(record)
    assert error.value.outcome is Outcome.ERROR
    assert error.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize(
    "field,value,fragment",
    [("fnMap", None, "'fnMap'"), ("fnMap", [], "'fnMap'"),
     ("fnMap", {}, "maps to 0 functions"), ("f", None, "requires f['0']"),
     ("f", [], "requires f['0']"), ("f", {}, "requires f['0']")],
)
def test_missing_mapping_or_count_is_unreadable(field, value, fragment):
    record = specimen()
    record[field] = value
    with pytest.raises(AssayError) as error:
        parse(record)
    assert fragment in str(error.value)
    assert error.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_ambiguous_mapping_is_unreadable():
    record = specimen()
    record["fnMap"]["1"] = copy.deepcopy(record["fnMap"]["0"])
    record["f"]["1"] = 0
    with pytest.raises(AssayError, match="maps to 2 functions") as error:
        parse(record)
    assert error.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize("field", ["decl", "loc", "node"])
@pytest.mark.parametrize("part,bad", [("column", None), ("column", -1), ("column", True),
                                      ("column", "72"), ("line", 0), ("line", True)])
def test_mapping_positions_must_be_real_coordinates(field, part, bad):
    record = specimen()
    location = (record["branchMap"]["0"]["loc"] if field == "node"
                else record["fnMap"]["0"][field])
    location["start"][part] = bad
    with pytest.raises(AssayError, match=f"start.{part}") as error:
        parse(record)
    assert error.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize("field", ["function", "decl", "loc", "node"])
def test_mapping_objects_cannot_be_absent_or_nonobjects(field):
    record = specimen()
    if field == "function":
        record["fnMap"]["0"] = None
    elif field == "node":
        record["branchMap"]["0"]["line"] = 34
        record["branchMap"]["0"]["loc"] = None
    else:
        record["fnMap"]["0"][field] = None
    with pytest.raises(AssayError, match="object") as error:
        parse(record)
    assert error.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_signature_lines_spend_the_existing_artifact_line_budget(monkeypatch):
    monkeypatch.setattr(parser, "MAX_CLASSIFIED_LINES", 1)
    assert parse(specimen()).executed == frozenset({34})
    document = {KEY: specimen(), "/fixture/src/second.ts": specimen()}
    with pytest.raises(AssayError) as error:
        parser.parse(json.dumps(document), producer="istanbul")
    assert error.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
