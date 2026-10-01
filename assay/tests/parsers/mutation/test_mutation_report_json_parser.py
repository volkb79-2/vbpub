"""Strict mutation-report JSON input controls, independent of R2 execution."""

from __future__ import annotations

import json

import pytest

from assay.errors import AssayError, ReasonCode
from assay.mutation_parsers import mutation_report_json as parser


def _mutant(**overrides):
    mutant = {
        "id": "m1",
        "status": "NoCoverage",
        "mutatorName": "BlockStatement",
        "replacement": "{}",
        "location": {
            "start": {"line": 1, "column": 1},
            "end": {"line": 1, "column": 2},
        },
    }
    mutant.update(overrides)
    return mutant


def _document(*, source="x\n", mutants=None):
    return {
        "schemaVersion": "1.0",
        "framework": {"name": "StrykerJS", "version": "10.0.0"},
        "projectRoot": "/project",
        "files": {
            "src/a.js": {
                "source": source,
                "mutants": [_mutant()] if mutants is None else mutants,
            }
        },
    }


def _parse(document):
    return parser.parse(json.dumps(document))


def _refuse(document, message):
    with pytest.raises(AssayError, match=message) as caught:
        _parse(document)
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_sniff_and_parse_distinguish_a_report_from_malformed_json():
    document = _document()
    text = json.dumps(document)
    assert parser.sniff(text)
    assert not parser.sniff("not JSON")
    assert not parser.sniff("[]")
    with pytest.raises(AssayError, match="not valid JSON"):
        parser.parse("not JSON")


def test_a_minimal_report_retains_its_source_producer_and_mutant_identity():
    report = _parse(_document())
    (mutant,) = report.mutants
    assert report.project_root == "/project"
    assert report.producer.name == "StrykerJS"
    assert report.producer.version == "10.0.0"
    assert mutant.path == "src/a.js"
    assert mutant.start_byte == 0
    assert mutant.end_byte == 1
    assert mutant.description == "BlockStatement -> {}"


@pytest.mark.parametrize("document", [[], None, "text"])
def test_a_non_object_top_level_document_is_refused(document):
    with pytest.raises(AssayError, match="must be a JSON object"):
        parser.parse(json.dumps(document))


@pytest.mark.parametrize("value", [None, True, [], {}])
def test_schema_version_must_have_an_supported_scalar_type(value):
    document = _document()
    document["schemaVersion"] = value
    _refuse(document, "schemaVersion.*must be a string")


def test_a_foreign_schema_major_is_refused():
    document = _document()
    document["schemaVersion"] = "9.0"
    _refuse(document, "major '9'.*does not read")


@pytest.mark.parametrize("framework", [None, "StrykerJS", []])
def test_framework_must_be_an_object(framework):
    document = _document()
    document["framework"] = framework
    _refuse(document, "no 'framework' object")


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", ""), ("name", None), ("version", ""), ("version", None)],
)
def test_framework_identity_fields_must_be_non_empty_strings(field, value):
    document = _document()
    document["framework"][field] = value
    _refuse(document, f"framework\\.{field}.*non-empty string")


@pytest.mark.parametrize("project_root", [None, ""])
def test_project_root_is_required(project_root):
    document = _document()
    document["projectRoot"] = project_root
    _refuse(document, "no 'projectRoot'")


@pytest.mark.parametrize("files", [None, [], "not a map"])
def test_files_member_must_be_an_object(files):
    document = _document()
    document["files"] = files
    _refuse(document, "'files' must be an object")


def test_empty_file_keys_and_non_object_file_records_are_refused():
    document = _document()
    record = document["files"].pop("src/a.js")
    document["files"][""] = record
    _refuse(document, "non-string key")

    document = _document()
    document["files"]["src/a.js"] = []
    _refuse(document, "must be an object")


def test_source_and_mutant_arrays_are_required():
    document = _document()
    document["files"]["src/a.js"]["source"] = None
    _refuse(document, "no 'source' string")

    document = _document()
    document["files"]["src/a.js"]["mutants"] = None
    _refuse(document, "no 'mutants' array")


def test_mutant_inventory_ceiling_refuses_instead_of_truncating(monkeypatch):
    monkeypatch.setattr(parser, "MAX_INGESTED_MUTANTS", 0)
    _refuse(_document(), "more than 0 mutants")


@pytest.mark.parametrize(
    ("mutants", "message"),
    [
        ([None], "non-object mutant entry"),
        ([_mutant(status="Unknown")], "carries status"),
        ([_mutant(mutatorName="bad-name")], "not spellable as an assay operator"),
        ([_mutant(mutatorName=None)], "not spellable as an assay operator"),
        ([_mutant(replacement=None)], "no 'replacement' string"),
        ([_mutant(location=None)], "no 'location' object"),
        ([_mutant(location={"start": None, "end": {"line": 1, "column": 2}})], "no 'start' position"),
        ([_mutant(location={"start": {"line": 1, "column": 1}, "end": None})], "no 'end' position"),
        ([_mutant(location={"start": {"line": True, "column": 1}, "end": {"line": 1, "column": 2}})], "start\\.line must be an integer"),
        ([_mutant(location={"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 2}})], "names column 0"),
        ([_mutant(location={"start": {"line": 3, "column": 1}, "end": {"line": 3, "column": 2}})], "outside the 1\\.\\.2 lines"),
        ([_mutant(location={"start": {"line": 1, "column": 4}, "end": {"line": 1, "column": 5}})], "past the end of that line"),
        ([_mutant(location={"start": {"line": 1, "column": 2}, "end": {"line": 1, "column": 2}})], "empty or reversed"),
        ([_mutant(status="Pending")], "still marked 'Pending'"),
    ],
)
def test_malformed_mutants_are_named_and_refused(mutants, message):
    _refuse(_document(mutants=mutants), message)


def test_long_replacement_preview_is_bounded_with_an_ellipsis():
    replacement = "r" * 100
    report = _parse(_document(mutants=[_mutant(replacement=replacement)]))
    assert report.mutants[0].description == "BlockStatement -> " + "r" * 60 + "..."


def test_byte_offset_is_also_bounded_by_the_entire_source():
    with pytest.raises(AssayError, match="past the end of that line"):
        parser._byte_offset(
            (0, 100),
            2,
            line=1,
            column=4,
            what="controlled source bound",
        )
