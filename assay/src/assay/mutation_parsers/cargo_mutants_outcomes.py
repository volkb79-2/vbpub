"""``cargo-mutants-outcomes`` -- the ``mutants.out/outcomes.json`` document
cargo-mutants writes, plus the ``diff/*.diff`` members it names (B245).

A DIRECTORY-kind format: the artifact is ``mutants.out`` (a
:class:`~assay.mutation_parsers.model.DirectoryArtifactLike`), not one file.
This module is the U3a half of the parser: the closed grammar of
``outcomes.json`` at every level, duplicate-key and size refusals, the
major-version pin (``27.``, design Q2) and the D5 status normaliser. Binding
each mutant to a committed blob (byte spans, genre, per-mutant diff tier,
``sources``) is U3b and lives behind :func:`_bind_mutants`.

**Why every level is closed.** The document is the foreign tool's word. An
unknown key at any level means the tool changed shape under us, and reading
the rest "optimistically" is how a status or phase is silently misread, so an
unknown OR missing key refuses the whole report.

**Read once.** :func:`load_document` is memoised on the report bytes
(``lru_cache(maxsize=1)``), so :func:`diff_paths`, the result-report reader
and :func:`parse_directory` share one ``json.loads`` of one document (design
7.3/7.14).
"""

from __future__ import annotations

import functools
import json
from typing import Any, Mapping, Sequence

from ..errors import AssayError, Outcome, ReasonCode
from ..guards import is_nonempty_str, is_strict_int
from ..records import record
from ..vocabulary import DISCARD_REASONS, MAX_INGESTED_MUTANTS
from .model import (
    MAX_MUTATION_REPORT_BYTES,
    DirectoryArtifactLike,
    IngestedMutationReport,
    MutationProducerIdentity,
    ParseContext,
)

__all__ = [
    "DISCARDED_STATUSES",
    "REPORT_SCHEMA_VERSION",
    "STATUS_BUCKETS",
    "SUPPORTED_MAJORS",
    "NormalisedMutant",
    "diff_paths",
    "load_document",
    "normalise_outcomes",
    "parse_directory",
    "sniff",
]

#: cargo-mutants has no report schema version, so assay owns the contract
#: string (design Q2) and refuses any ``cargo_mutants_version`` outside the
#: majors it was qualified against.
REPORT_SCHEMA_VERSION = "outcomes.json"
SUPPORTED_MAJORS: frozenset[str] = frozenset({"27"})

#: Normalised status -> assay bucket. ``Timeout`` is the per-mutant budget
#: (never a kill); ``Crashed`` is its own bucket (design 7.10: loud, ERROR).
STATUS_BUCKETS: Mapping[str, str] = {
    "Killed": "killed",
    "Crashed": "crashed",
    "Survived": "survived",
    "Timeout": "budget_exceeded",
}
#: Normalised status -> discard reason. An unviable mutant did not build.
DISCARDED_STATUSES: Mapping[str, str] = {"Unviable": DISCARD_REASONS[0]}

#: The one exit status cargo-nextest/`cargo test` uses for "a test failed".
_TEST_FAILURE_EXIT = 100

_TOP_KEYS = frozenset(
    {
        "outcomes",
        "total_mutants",
        "missed",
        "caught",
        "timeout",
        "unviable",
        "success",
        "start_time",
        "end_time",
        "cargo_mutants_version",
    }
)
_COUNT_KEYS = ("total_mutants", "missed", "caught", "timeout", "unviable", "success")
_OUTCOME_KEYS = frozenset(
    {"scenario", "summary", "log_path", "diff_path", "phase_results"}
)
_MUTANT_KEYS = frozenset(
    {"name", "package", "file", "function", "span", "replacement", "genre"}
)
_FUNCTION_KEYS = frozenset({"function_name", "return_type", "span"})
_PHASE_KEYS = frozenset({"phase", "duration", "process_status", "argv"})
_PHASES = ("Build", "Test")
_BASELINE_SUMMARIES = frozenset({"Success", "Failure"})
_MUTANT_SUMMARIES = frozenset(
    {"CaughtMutant", "MissedMutant", "Unviable", "Timeout"}
)


def _unreadable(message: str) -> AssayError:
    return AssayError(
        message, outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT
    )


@record
class NormalisedMutant:
    """One mutant outcome after grammar checks and D5 status normalisation.

    Positions are the tool's own one-based line and CHARACTER column; turning
    them into UTF-8 byte offsets on the committed blob is U3b's job.
    """

    name: str
    package: str
    file: str
    function_name: str | None
    function_span: tuple[int, int, int, int] | None
    span: tuple[int, int, int, int]
    replacement: str
    genre: str
    status: str
    diff_path: str


def sniff(raw: Any) -> bool:
    """Does the report bytes' content carry THIS format's own signature
    (A-007)? Structural only: the keys ``outcomes`` and
    ``cargo_mutants_version``. Never validates, never raises."""
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return False
    return (
        isinstance(document, dict)
        and isinstance(document.get("outcomes"), list)
        and "cargo_mutants_version" in document
    )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise _unreadable(
                f"outcomes.json repeats the key {key!r} in one object; a "
                f"duplicate would let one value silently win"
            )
        seen[key] = value
    return seen


@functools.lru_cache(maxsize=1)
def load_document(raw: bytes) -> Mapping[str, Any]:
    """Parse and grammar-check ``outcomes.json`` ONCE per distinct bytes.

    Returns the validated document; callers must treat it as read-only.
    """
    if len(raw) > MAX_MUTATION_REPORT_BYTES:
        raise _unreadable(
            f"outcomes.json is {len(raw):,} bytes, over the "
            f"{MAX_MUTATION_REPORT_BYTES:,}-byte bound for one report"
        )
    try:
        document = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise _unreadable(f"outcomes.json is not valid JSON: {exc}") from exc
    _check_document(document)
    return document  # type: ignore[no-any-return]


def _closed(value: Any, keys: frozenset[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _unreadable(
            f"outcomes.json {where} must be an object, got {type(value).__name__}"
        )
    if set(value) != keys:
        raise _unreadable(
            f"outcomes.json {where} must have exactly the keys {sorted(keys)}; "
            f"unknown: {sorted(set(value) - keys)}, missing: "
            f"{sorted(keys - set(value))}"
        )
    return value


def _check_document(document: Any) -> None:
    top = _closed(document, _TOP_KEYS, "document")
    version = top["cargo_mutants_version"]
    if not is_nonempty_str(version) or version.split(".")[0] not in SUPPORTED_MAJORS:
        raise _unreadable(
            f"outcomes.json cargo_mutants_version {version!r} is not in the "
            f"qualified major(s) {sorted(SUPPORTED_MAJORS)}; a different "
            f"major may have changed the document"
        )
    for key in _COUNT_KEYS:
        if not is_strict_int(top[key]) or top[key] < 0:
            raise _unreadable(f"outcomes.json {key} must be a non-negative integer")
    if not is_nonempty_str(top["start_time"]):
        raise _unreadable("outcomes.json start_time must be a non-empty string")
    if top["end_time"] is None:
        raise _unreadable(
            "outcomes.json end_time is null: the run did not finish, and an "
            "incomplete report is not evidence"
        )
    if not is_nonempty_str(top["end_time"]):
        raise _unreadable("outcomes.json end_time must be a non-empty string")
    outcomes = top["outcomes"]
    if not isinstance(outcomes, list):
        raise _unreadable("outcomes.json outcomes must be an array")
    if len(outcomes) > MAX_INGESTED_MUTANTS + 1:
        raise _unreadable(
            f"outcomes.json carries more than {MAX_INGESTED_MUTANTS:,} mutants"
        )
    baselines = 0
    mutants = 0
    seen_diffs: set[str] = set()
    for index, outcome in enumerate(outcomes):
        where = f"outcomes[{index}]"
        record_ = _closed(outcome, _OUTCOME_KEYS, where)
        _check_phases(record_["phase_results"], where)
        if record_["scenario"] == "Baseline":
            baselines += 1
            _check_baseline(record_, where)
            continue
        mutants += 1
        _check_mutant(record_, where)
        diff_path = record_["diff_path"]
        if diff_path in seen_diffs:
            raise _unreadable(
                f"outcomes.json names the diff member {diff_path!r} twice"
            )
        seen_diffs.add(diff_path)
    if baselines != 1:
        raise _unreadable(
            f"outcomes.json must carry exactly one Baseline outcome, found "
            f"{baselines}"
        )
    if mutants != top["total_mutants"]:
        raise _unreadable(
            f"outcomes.json total_mutants is {top['total_mutants']} but "
            f"{mutants} mutant outcome(s) are present"
        )


def _check_baseline(outcome: dict[str, Any], where: str) -> None:
    if outcome["summary"] not in _BASELINE_SUMMARIES:
        raise _unreadable(
            f"outcomes.json {where} Baseline summary {outcome['summary']!r} "
            f"must be one of {sorted(_BASELINE_SUMMARIES)}"
        )
    if outcome["diff_path"] is not None:
        raise _unreadable(f"outcomes.json {where} Baseline must have diff_path null")


def _position(value: Any, where: str) -> tuple[int, int]:
    pos = _closed(value, frozenset({"line", "column"}), where)
    if not (is_strict_int(pos["line"]) and is_strict_int(pos["column"])):
        raise _unreadable(f"outcomes.json {where} line/column must be integers")
    if pos["line"] < 1 or pos["column"] < 1:
        raise _unreadable(f"outcomes.json {where} line/column are one-based")
    return pos["line"], pos["column"]


def _span(value: Any, where: str) -> tuple[int, int, int, int]:
    span = _closed(value, frozenset({"start", "end"}), where)
    start = _position(span["start"], f"{where}.start")
    end = _position(span["end"], f"{where}.end")
    return (*start, *end)


def _check_mutant(outcome: dict[str, Any], where: str) -> None:
    scenario = _closed(outcome["scenario"], frozenset({"Mutant"}), f"{where}.scenario")
    mutant = _closed(scenario["Mutant"], _MUTANT_KEYS, f"{where}.scenario.Mutant")
    for key in ("name", "file", "genre"):
        if not is_nonempty_str(mutant[key]):
            raise _unreadable(
                f"outcomes.json {where} mutant {key} must be a non-empty string"
            )
    for key in ("package", "replacement"):
        if not isinstance(mutant[key], str):
            raise _unreadable(f"outcomes.json {where} mutant {key} must be a string")
    _span(mutant["span"], f"{where}.span")
    function = mutant["function"]
    if function is not None:
        function = _closed(function, _FUNCTION_KEYS, f"{where}.function")
        if not is_nonempty_str(function["function_name"]) or not isinstance(
            function["return_type"], str
        ):
            raise _unreadable(
                f"outcomes.json {where} function_name/return_type are not strings"
            )
        _span(function["span"], f"{where}.function.span")
    if outcome["summary"] not in _MUTANT_SUMMARIES:
        raise _unreadable(
            f"outcomes.json {where} mutant summary {outcome['summary']!r} must "
            f"be one of {sorted(_MUTANT_SUMMARIES)}; Success and Failure "
            f"belong to the Baseline only"
        )
    if not is_nonempty_str(outcome["diff_path"]):
        raise _unreadable(f"outcomes.json {where} mutant diff_path must be a string")


def _check_status(status: Any, where: str) -> None:
    """``Success`` | ``Timeout`` | ``Other`` | ``{"Failure": n}`` |
    ``{"Signalled": n}``; anything else (and a bool or non-positive n) refuses."""
    if status in ("Success", "Timeout", "Other") and isinstance(status, str):
        return
    if isinstance(status, dict) and len(status) == 1:
        (key, number), = status.items()
        if key in ("Failure", "Signalled") and is_strict_int(number) and number > 0:
            return
    raise _unreadable(f"outcomes.json {where} process_status {status!r} is not known")


def _check_phases(phases: Any, where: str) -> None:
    if not isinstance(phases, list) or not phases:
        raise _unreadable(f"outcomes.json {where} phase_results must be a list")
    names = []
    for index, phase in enumerate(phases):
        here = f"{where}.phase_results[{index}]"
        phase = _closed(phase, _PHASE_KEYS, here)
        names.append(phase["phase"])
        duration = phase["duration"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise _unreadable(f"outcomes.json {here} duration must be a number")
        if not isinstance(phase["argv"], list) or not all(
            isinstance(item, str) for item in phase["argv"]
        ):
            raise _unreadable(f"outcomes.json {here} argv must be a list of strings")
        _check_status(phase["process_status"], here)
    if names not in (["Build"], ["Build", "Test"]):
        raise _unreadable(
            f"outcomes.json {where} phases {names} must be [Build] or "
            f"[Build, Test]"
        )


def _status_of(outcome: dict[str, Any], where: str) -> str:
    """The D5 normaliser: ``Killed``/``Crashed``/``Survived``/``Timeout``/
    ``Unviable`` from the tool's summary and its Test phase's process status.

    Only a failing test run that exited 100 is a kill. Every other way a test
    run can fail (nextest's "no tests to run" exit 4, any other nonzero exit,
    a signal, ``Other``) proves only that the suite did not run to a verdict,
    so it is ``Crashed`` and never a quiet kill.
    """
    summary = outcome["summary"]
    phases = outcome["phase_results"]
    statuses = [phase["process_status"] for phase in phases]
    if summary == "Unviable":
        if len(phases) != 1:
            raise _unreadable(
                f"outcomes.json {where} Unviable mutant must have a Build-only "
                f"phase list"
            )
        return "Unviable"
    if summary == "Timeout":
        if "Timeout" not in statuses:
            raise _unreadable(f"outcomes.json {where} Timeout without a timed-out phase")
        return "Timeout"
    if len(phases) != 2:
        raise _unreadable(
            f"outcomes.json {where} a {summary} mutant must have Build and Test phases"
        )
    test = statuses[1]
    if summary == "MissedMutant":
        if statuses != ["Success", "Success"]:
            raise _unreadable(f"outcomes.json {where} MissedMutant with a failing phase")
        return "Survived"
    if test == {"Failure": _TEST_FAILURE_EXIT}:
        return "Killed"
    if test in ("Success", "Timeout"):
        raise _unreadable(
            f"outcomes.json {where} CaughtMutant whose Test phase is {test!r}"
        )
    return "Crashed"


def normalise_outcomes(document: Mapping[str, Any]) -> tuple[NormalisedMutant, ...]:
    """Every mutant outcome of a :func:`load_document` result, status-normalised."""
    result: list[NormalisedMutant] = []
    for index, outcome in enumerate(document["outcomes"]):
        if outcome["scenario"] == "Baseline":
            continue
        where = f"outcomes[{index}]"
        mutant = outcome["scenario"]["Mutant"]
        function = mutant["function"]
        result.append(
            NormalisedMutant(
                name=mutant["name"],
                package=mutant["package"],
                file=mutant["file"],
                function_name=None if function is None else function["function_name"],
                function_span=None if function is None else _span(function["span"], where),
                span=_span(mutant["span"], where),
                replacement=mutant["replacement"],
                genre=mutant["genre"],
                status=_status_of(outcome, where),
                diff_path=outcome["diff_path"],
            )
        )
    return tuple(result)


def diff_paths(raw: bytes) -> Sequence[str]:
    """Every ``diff_path`` the report names, verbatim and in report order.

    The Baseline (``diff_path`` null) is skipped. The member-name grammar is
    applied where the names are opened (design section 8 item 3).
    """
    return [
        outcome["diff_path"]
        for outcome in load_document(raw)["outcomes"]
        if outcome["diff_path"] is not None
    ]


def _producer(document: Mapping[str, Any]) -> MutationProducerIdentity:
    return MutationProducerIdentity(
        name="cargo-mutants",
        version=document["cargo_mutants_version"],
        report_schema_version=REPORT_SCHEMA_VERSION,
    )


def _bind_mutants(
    mutants: Sequence[NormalisedMutant],
    artifact: DirectoryArtifactLike,
    ctx: ParseContext,
) -> tuple[Any, Mapping[str, str]]:
    """U3b: spans to UTF-8 bytes on the committed blob, genre binding (FnValue
    on the last ``::`` segment), the per-mutant diff tier and ``sources``."""
    raise NotImplementedError("B245 U3b: mutant binding is the next package")


def parse_directory(
    artifact: DirectoryArtifactLike, ctx: ParseContext
) -> IngestedMutationReport:
    """Parse ``mutants.out`` into an :class:`IngestedMutationReport`.

    ``project_root`` is ``None``: the document carries none, and the report is
    bound to the commit by content (the diff tier) instead.
    """
    document = load_document(artifact.report)
    producer = _producer(document)
    mutants, sources = _bind_mutants(normalise_outcomes(document), artifact, ctx)
    return IngestedMutationReport(
        producer=producer, project_root=None, mutants=tuple(mutants), sources=sources
    )
