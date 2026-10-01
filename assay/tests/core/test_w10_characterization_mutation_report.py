"""B129 / W10 step 1: characterization of the inline guards in
``mutation_parsers.mutation_report_json``.

Pins accept and refuse, with the exact message and reason code, for the
non-empty-string and strict-int guards. Run against the UNCHANGED source;
never edited to follow the refactor.
"""

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


def _document():
    return {
        "schemaVersion": "1.0",
        "framework": {"name": "StrykerJS", "version": "10.0.0"},
        "projectRoot": "/project",
        "files": {"src/a.js": {"source": "x\n", "mutants": [_mutant()]}},
    }


def refusal(document, message: str) -> None:
    with pytest.raises(AssayError) as caught:
        parser.parse(json.dumps(document))
    assert str(caught.value) == message
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


# ---- files key: non-empty string -----------------------------------------------


def test_an_empty_file_key_is_refused():
    document = _document()
    document["files"] = {"": document["files"]["src/a.js"]}
    refusal(document, "mutation report 'files' has a non-string key")


def test_a_one_character_file_key_is_accepted():
    document = _document()
    document["files"] = {"a": document["files"]["src/a.js"]}
    assert parser.parse(json.dumps(document)).mutants[0].path == "a"


# ---- framework name and version: non-empty string ------------------------------


@pytest.mark.parametrize("label", ["name", "version"])
@pytest.mark.parametrize("value", ["", 1, None, True, [], {}])
def test_framework_name_and_version_refuse_an_empty_or_non_string(label, value):
    document = _document()
    document["framework"][label] = value
    refusal(
        document,
        f"mutation report 'framework.{label}' must be a non-empty string, "
        f"got {value!r}",
    )


@pytest.mark.parametrize("label", ["name", "version"])
def test_framework_name_and_version_refuse_an_absent_member(label):
    document = _document()
    del document["framework"][label]
    refusal(
        document,
        f"mutation report 'framework.{label}' must be a non-empty string, "
        f"got None",
    )


def test_framework_accepts_one_character_name_and_version():
    document = _document()
    document["framework"] = {"name": "x", "version": "y"}
    producer = parser.parse(json.dumps(document)).producer
    assert (producer.name, producer.version) == ("x", "y")


# ---- projectRoot: non-empty string ---------------------------------------------

PROJECT_ROOT_MESSAGE = (
    "mutation report carries no 'projectRoot'. The upstream schema "
    "makes it optional; assay REQUIRES it (A-375), because it is the "
    "only field that says where the report's own relative file keys "
    "are anchored. Without it, checking that this report describes "
    "THIS snapshot -- rather than some other checkout the same tool "
    "ran in -- would have to start by assuming the answer. Configure "
    "the reporter to emit it, or run the tool from the directory the "
    "lane declares"
)


@pytest.mark.parametrize("value", ["", 1, None, True, [], {}])
def test_project_root_refuses_an_empty_or_non_string(value):
    document = _document()
    document["projectRoot"] = value
    refusal(document, PROJECT_ROOT_MESSAGE)


def test_project_root_refuses_an_absent_member():
    document = _document()
    del document["projectRoot"]
    refusal(document, PROJECT_ROOT_MESSAGE)


def test_project_root_accepts_one_character():
    document = _document()
    document["projectRoot"] = "/"
    assert parser.parse(json.dumps(document)).project_root == "/"


# ---- mutant location line and column: strict int -------------------------------


@pytest.mark.parametrize("label", ["start", "end"])
@pytest.mark.parametrize("field", ["line", "column"])
@pytest.mark.parametrize("value", [True, False, 1.5, "1", None, [1]])
def test_location_positions_refuse_a_bool_or_non_int(label, field, value):
    document = _document()
    location = {
        "start": {"line": 1, "column": 1},
        "end": {"line": 1, "column": 2},
    }
    location[label][field] = value
    document["files"]["src/a.js"]["mutants"] = [_mutant(location=location)]
    refusal(
        document,
        f"src/a.js mutant 'm1' location.{label}.{field} must be an integer, "
        f"got {value!r}",
    )
