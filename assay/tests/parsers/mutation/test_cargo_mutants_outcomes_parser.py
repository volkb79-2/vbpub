"""B245 U3a: the ``cargo-mutants-outcomes`` grammar, statuses and registry.

Every case under ``tests/fixtures/rust/cargo-mutants`` is REAL tool output
except ``synthetic-signalled`` and ``synthetic-other``, which are edited from
``full`` (PROVENANCE.md); the tests that use them say so.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from assay.errors import AssayError, LaneConfigError, Outcome, ReasonCode
from assay.mutation_parsers import (
    MUTATION_FORMAT_REGISTRY,
    ParseContext,
    cargo_mutants_outcomes as cmo,
    load_mutation_directory,
    load_mutation_report,
)
from assay.vocabulary import DISCARD_REASONS

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "rust" / "cargo-mutants"
REAL_WITH_OUTCOMES = (
    "full basefail-build basefail-test crashed-f4 indiff methods paths "
    "rotation subdir unicode w-true"
).split()
SYNTHETIC = ("synthetic-signalled", "synthetic-other")


def _raw(case: str) -> bytes:
    return (FIXTURES / case / "mutants.out" / "outcomes.json").read_bytes()


def _doc(case: str = "full") -> dict:
    return json.loads(_raw(case))


def _dump(doc: dict) -> bytes:
    return json.dumps(doc).encode()


def _statuses(case: str) -> Counter:
    return Counter(m.status for m in cmo.normalise_outcomes(cmo.load_document(_raw(case))))


def _refuses(raw: bytes, match: str) -> None:
    with pytest.raises(AssayError, match=match) as caught:
        cmo.load_document(raw)
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


class _Artifact:
    def __init__(self, report: bytes):
        self.report = report
        self.members: dict[str, bytes] = {}


def test_full_counts_match_provenance():
    assert _statuses("full") == {
        "Killed": 16,
        "Survived": 7,
        "Unviable": 1,
        "Timeout": 1,
    }


def test_crashed_f4_maps_failure_4_to_crashed_and_100_to_killed():
    assert _statuses("crashed-f4") == {"Crashed": 5, "Killed": 3}


def test_w_true_is_all_killed():
    assert _statuses("w-true") == {"Killed": 8}


def test_methods_has_ten_killed_and_one_unviable():
    assert _statuses("methods") == {"Killed": 10, "Unviable": 1}


def test_indiff_and_subdir_are_nine_survivors():
    assert _statuses("indiff") == {"Survived": 9}
    assert _statuses("subdir") == {"Survived": 9}


def test_baseline_failures_have_no_mutants():
    for case in ("basefail-build", "basefail-test"):
        doc = cmo.load_document(_raw(case))
        assert cmo.normalise_outcomes(doc) == ()
        assert cmo.diff_paths(_raw(case)) == []


@pytest.mark.parametrize("case", REAL_WITH_OUTCOMES)
def test_every_real_case_loads(case):
    doc = cmo.load_document(_raw(case))
    assert len(cmo.normalise_outcomes(doc)) == doc["total_mutants"]


def test_synthetic_cases_signalled_and_other_are_crashed():
    # SYNTHETIC cases (edited from `full`, not tool output): only the mapping.
    for case in SYNTHETIC:
        assert _statuses(case)["Crashed"] == 2, case
        assert _statuses(case)["Killed"] == 14, case


def test_diff_paths_are_verbatim_and_skip_the_baseline():
    paths = cmo.diff_paths(_raw("full"))
    assert len(paths) == 25 and paths[0].startswith("diff/")


def test_load_document_parses_once(monkeypatch):
    raw = _raw("full") + b" "
    calls = []
    real = json.loads
    monkeypatch.setattr(
        cmo.json, "loads", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    cmo.load_document(raw)
    cmo.diff_paths(raw)
    cmo.diff_paths(raw)
    assert len(calls) == 1


def test_a_normalised_mutant_carries_function_and_span():
    mutant = cmo.normalise_outcomes(cmo.load_document(_raw("methods")))[0]
    assert mutant.genre and mutant.diff_path.startswith("diff/")
    assert mutant.span[0] >= 1 and mutant.function_name


def test_sniff():
    assert cmo.sniff(_raw("full"))
    assert not cmo.sniff(b"{}")
    assert not cmo.sniff(b"not json")
    assert not cmo.sniff(b"[1]")


def test_not_json_oversize_and_duplicate_keys_refuse(monkeypatch):
    _refuses(b"{", "not valid JSON")
    _refuses(b'{"a": 1, "a": 2}', "repeats the key")
    _refuses(b"[" * 100000, "not valid JSON")
    monkeypatch.setattr(cmo, "MAX_MUTATION_REPORT_BYTES", 10)
    _refuses(_raw("full"), "over the")


def test_unknown_missing_and_non_object_levels_refuse():
    doc = _doc()
    doc["extra"] = 1
    _refuses(_dump(doc), "unknown")
    doc = _doc()
    del doc["success"]
    _refuses(_dump(doc), "missing")
    _refuses(b"[]", "must be an object")
    for path in (
        ["outcomes", 1, "extra"],
        ["outcomes", 1, "scenario", "Mutant", "extra"],
        ["outcomes", 1, "scenario", "Mutant", "function", "extra"],
        ["outcomes", 1, "scenario", "Mutant", "span", "extra"],
        ["outcomes", 1, "scenario", "Mutant", "span", "start", "extra"],
        ["outcomes", 1, "phase_results", 0, "extra"],
        ["outcomes", 1, "scenario", "extra"],
    ):
        doc = _doc()
        node = doc
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = 1
        _refuses(_dump(doc), "exactly the keys")


def _mutate(edit) -> bytes:
    doc = copy.deepcopy(_doc())
    edit(doc)
    return _dump(doc)


def _mutant(doc, index=1):
    return doc["outcomes"][index]["scenario"]["Mutant"]


def test_version_counts_and_times_refuse():
    _refuses(_mutate(lambda d: d.update(cargo_mutants_version="28.0.0")), "qualified")
    _refuses(_mutate(lambda d: d.update(cargo_mutants_version=27)), "qualified")
    _refuses(_mutate(lambda d: d.update(missed=True)), "non-negative")
    _refuses(_mutate(lambda d: d.update(caught=-1)), "non-negative")
    _refuses(_mutate(lambda d: d.update(start_time="")), "start_time")
    _refuses(_mutate(lambda d: d.update(end_time=None)), "end_time is null")
    _refuses(_mutate(lambda d: d.update(end_time=5)), "end_time must")
    _refuses(_mutate(lambda d: d.update(outcomes={})), "must be an array")
    _refuses(_mutate(lambda d: d.update(total_mutants=3)), "total_mutants")


def test_oversize_mutant_count_refuses(monkeypatch):
    monkeypatch.setattr(cmo, "MAX_INGESTED_MUTANTS", 3)
    _refuses(_raw("full"), "more than")


def test_baseline_rules():
    _refuses(_mutate(lambda d: d["outcomes"].pop(0)), "exactly one Baseline")
    _refuses(_mutate(lambda d: d["outcomes"].append(d["outcomes"][0])), "exactly one Baseline")
    _refuses(_mutate(lambda d: d["outcomes"][0].update(summary="Timeout")), "Baseline summary")
    _refuses(_mutate(lambda d: d["outcomes"][0].update(diff_path="diff/x.diff")), "diff_path null")


def test_mutant_summary_success_and_failure_refuse():
    for summary in ("Success", "Failure", "Bogus"):
        _refuses(_mutate(lambda d: d["outcomes"][1].update(summary=summary)), "mutant summary")


def test_mutant_field_refusals():
    for key in ("name", "file", "genre"):
        _refuses(_mutate(lambda d: _mutant(d).update({key: ""})), "non-empty string")
    for key in ("package", "replacement"):
        _refuses(_mutate(lambda d: _mutant(d).update({key: None})), "must be a string")
    _refuses(_mutate(lambda d: _mutant(d).update(function={})), "exactly the keys")
    _refuses(
        _mutate(lambda d: _mutant(d)["function"].update(function_name="")), "not strings"
    )
    _refuses(
        _mutate(lambda d: _mutant(d)["span"]["start"].update(line=True)), "integers"
    )
    _refuses(_mutate(lambda d: _mutant(d)["span"]["end"].update(column=0)), "one-based")
    _refuses(_mutate(lambda d: d["outcomes"][1].update(diff_path=None)), "diff_path")
    _refuses(
        _mutate(lambda d: d["outcomes"][2].update(diff_path=d["outcomes"][1]["diff_path"])),
        "twice",
    )


def test_a_null_function_is_accepted():
    doc = _doc()
    _mutant(doc)["function"] = None
    mutant = cmo.normalise_outcomes(cmo.load_document(_dump(doc)))[0]
    assert mutant.function_name is None and mutant.function_span is None


def test_process_status_grammar():
    def phase(d, status):
        d["outcomes"][1]["phase_results"][1]["process_status"] = status

    for bad in (
        "Bogus",
        True,
        {"Failure": True},
        {"Failure": 0},
        {"Failure": "1"},
        {"Failure": 1, "Signalled": 2},
        {"Exit": 1},
        {},
        5,
    ):
        _refuses(_mutate(lambda d: phase(d, bad)), "process_status")
    for good in ("Other", {"Signalled": 9}, {"Failure": 4}):
        raw = _mutate(lambda d: phase(d, good))
        assert cmo.normalise_outcomes(cmo.load_document(raw))[0].status == "Crashed"


def test_phase_list_grammar():
    _refuses(_mutate(lambda d: d["outcomes"][1].update(phase_results=[])), "phase_results")
    _refuses(
        _mutate(lambda d: d["outcomes"][1]["phase_results"].reverse()), "phases"
    )
    _refuses(
        _mutate(lambda d: d["outcomes"][1]["phase_results"][0].update(duration=True)),
        "duration",
    )
    _refuses(
        _mutate(lambda d: d["outcomes"][1]["phase_results"][0].update(argv=[1])), "argv"
    )
    _refuses(
        _mutate(lambda d: d["outcomes"][1]["phase_results"][0].update(argv="x")), "argv"
    )


def _unviable_index(doc):
    return next(i for i, o in enumerate(doc["outcomes"]) if o["summary"] == "Unviable")


def test_d5_status_consistency_refusals():
    def to(d, i, summary, phases):
        d["outcomes"][i]["summary"] = summary
        if phases is not None:
            d["outcomes"][i]["phase_results"] = d["outcomes"][i]["phase_results"][:phases]

    def refuses(i_of, summary, phases, match):
        doc = _doc()
        to(doc, i_of(doc), summary, phases)
        # The grammar admits the document; the D5 normaliser refuses it.
        loaded = cmo.load_document(_dump(doc))
        with pytest.raises(AssayError, match=match):
            cmo.normalise_outcomes(loaded)

    first = lambda d: 1  # noqa: E731 - a CaughtMutant in `full`
    refuses(first, "Unviable", None, "Build-only")
    refuses(first, "Timeout", None, "timed-out")
    refuses(first, "CaughtMutant", 1, "Build and Test")
    refuses(first, "MissedMutant", None, "failing phase")
    refuses(_unviable_index, "CaughtMutant", None, "Build and Test")
    for status in ("Success", "Timeout"):
        doc = _doc()
        doc["outcomes"][1]["phase_results"][1]["process_status"] = status
        loaded = cmo.load_document(_dump(doc))
        with pytest.raises(AssayError, match="Test phase"):
            cmo.normalise_outcomes(loaded)


def test_registry_entry_and_stryker_tables_unchanged():
    spec = MUTATION_FORMAT_REGISTRY["cargo-mutants-outcomes"]
    assert spec.artifact_kind == "directory" and spec.parse is None
    assert spec.accepts_changed_diff is True
    assert dict(spec.status_buckets) == {
        "Killed": "killed",
        "Crashed": "crashed",
        "Survived": "survived",
        "Timeout": "budget_exceeded",
    }
    assert dict(spec.discarded_statuses) == {"Unviable": DISCARD_REASONS[0]}
    stryker = MUTATION_FORMAT_REGISTRY["mutation-report-json"]
    assert stryker.artifact_kind == "file" and stryker.parse_directory is None
    assert stryker.accepts_changed_diff is False
    assert dict(stryker.status_buckets) == {
        "Killed": "killed",
        "Survived": "survived",
        "NoCoverage": "survived",
        "Timeout": "budget_exceeded",
    }
    assert dict(stryker.discarded_statuses) == {
        "CompileError": DISCARD_REASONS[0],
        "RuntimeError": DISCARD_REASONS[1],
    }


def test_discard_reasons_import_paths_agree():
    from assay import verdict, vocabulary

    assert verdict.DISCARD_REASONS is vocabulary.DISCARD_REASONS


def test_file_load_refuses_a_directory_kind_format():
    with pytest.raises(LaneConfigError, match="directory-kind"):
        load_mutation_report("{}", declared_format="cargo-mutants-outcomes")


def test_directory_load_refuses_file_kind_and_unknown_formats():
    ctx = ParseContext(run_cwd=Path("."), repo_top=Path("."), repository=None, read_timeout=1.0)  # type: ignore[arg-type]
    with pytest.raises(LaneConfigError, match="file-kind"):
        load_mutation_directory(_Artifact(b"{}"), declared_format="mutation-report-json", context=ctx)
    with pytest.raises(LaneConfigError, match="not a mutation-report format"):
        load_mutation_directory(_Artifact(b"{}"), declared_format="nope", context=ctx)


def test_directory_load_checks_the_signature_then_parses():
    ctx = ParseContext(run_cwd=Path("."), repo_top=Path("."), repository=None, read_timeout=1.0)  # type: ignore[arg-type]
    with pytest.raises(AssayError) as caught:
        load_mutation_directory(_Artifact(b"{}"), declared_format="cargo-mutants-outcomes", context=ctx)
    assert caught.value.reason_code is ReasonCode.FORMAT_MISMATCH
    # The grammar and status pass runs, then binding needs the members.
    with pytest.raises(AssayError, match="diff members"):
        load_mutation_directory(
            _Artifact(_raw("full")), declared_format="cargo-mutants-outcomes", context=ctx
        )


def test_producer_identity_is_the_assay_owned_contract_string():
    producer = cmo._producer(cmo.load_document(_raw("full")))
    assert (producer.name, producer.version, producer.report_schema_version) == (
        "cargo-mutants",
        "27.1.0",
        "outcomes.json",
    )


def test_parse_directory_assembles_a_report_with_no_project_root(monkeypatch):
    ctx = ParseContext(run_cwd=Path("."), repo_top=Path("."), repository=None, read_timeout=1.0)  # type: ignore[arg-type]
    monkeypatch.setattr(cmo, "_bind_mutants", lambda mutants, artifact, context: ((), {"a": "b"}))
    report = load_mutation_directory(
        _Artifact(_raw("full")), declared_format="cargo-mutants-outcomes", context=ctx
    )
    assert report.project_root is None and report.sources == {"a": "b"}
    assert report.producer.version == "27.1.0"


def test_a_report_without_project_root_skips_the_root_check():
    from assay import mutation
    from assay.mutation_parsers.model import IngestedMutationReport, MutationProducerIdentity

    producer = MutationProducerIdentity(name="x", version="1", report_schema_version="y")
    report = IngestedMutationReport(producer=producer, project_root=None, mutants=(), sources={})
    assert mutation._check_report_project_root(report, run_cwd=Path("/nonexistent")) is None


def test_parse_directory_refuses_a_bad_document_before_it_reaches_the_stub():
    ctx = ParseContext(run_cwd=Path("."), repo_top=Path("."), repository=None, read_timeout=1.0)  # type: ignore[arg-type]
    with pytest.raises(AssayError) as caught:
        cmo.parse_directory(_Artifact(b"[]"), ctx)
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize("bad", [["Success"], {"Success": 1}, 7, None, True])
def test_a_non_string_summary_refuses_as_unreadable_not_a_type_error(bad):
    """A JSON list/object is unhashable: a set-membership test on it must not
    leak a ``TypeError`` out of the grammar check."""
    _refuses(_mutate(lambda d: d["outcomes"][0].update(summary=bad)), "Baseline summary")
    _refuses(_mutate(lambda d: d["outcomes"][1].update(summary=bad)), "mutant summary")


@pytest.mark.parametrize("bad", [["x"], {"a": 1}, 3, None])
def test_non_string_scenario_and_status_values_refuse_as_unreadable(bad):
    _refuses(_mutate(lambda d: d["outcomes"][1].update(scenario=bad)), "scenario")
    _refuses(
        _mutate(lambda d: d["outcomes"][1]["phase_results"][0].update(process_status=bad)),
        "process_status",
    )
    _refuses(
        _mutate(lambda d: d["outcomes"][1]["phase_results"][0].update(phase=bad)),
        "phases",
    )
