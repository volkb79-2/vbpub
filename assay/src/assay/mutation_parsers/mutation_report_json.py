"""``mutation-report-json`` — the `mutation-testing-report-schema` document
StrykerJS's ``json`` reporter writes (and Stryker.NET and Stryker4s write
identically).

Keyed by the FORMAT, never by the tool, for the reason
:mod:`assay.coverage_parsers` already gives one tier over: three Stryker
implementations across three language ecosystems emit this one shape, and a
registry keyed by tool would need three entries for one document. A future
producer of the same format needs no code here at all.

**What this module refuses, and why each refusal is not paranoia.** An
ingested report is evidence assay did not produce. It was written inside the
private snapshot by the lane's own argv, so it is bound to the resolved commit
by construction (A-161) — but everything else about it is the foreign tool's
word, and a judgment computed over a report assay has not checked is a Tier-1
number resting on a Tier-3 fact. So:

* an unknown ``schemaVersion`` major is refused rather than read
  optimistically — the field's whole purpose is to say when the shape changed;
* a ``Pending`` mutant ANYWHERE refuses the whole report: pending means the
  run did not finish, and incomplete evidence is not evidence at a lower
  confidence, it is not evidence;
* a status this build does not know is refused rather than dropped — a
  silently discarded mutant leaves both the numerator and the denominator
  wrong in a way that reads exactly like a correct score;
* a mutant with no ``replacement`` is refused: ``replacement`` is optional
  upstream and it is half of assay's mutant IDENTITY (A-180), so without it
  two genuinely distinct experiments collapse into one record;
* ``projectRoot`` is REQUIRED here though the upstream schema makes it
  optional (only ``schemaVersion``/``thresholds``/``files`` are required
  there). See A-375: it is the only field that says where the report's own
  relative file keys are anchored, and without it assay would have to GUESS
  that anchor — which is the "an artifact from elsewhere" check B046 exists
  to make possible, turned into an assumption.

Nothing here consults a lane, a diff, a source root or a scope. This module
answers "is this a well-formed report, and what does it say"; whether a mutant
is in scope, and which bucket it lands in, is
:func:`assay.mutation.ingest_mutation_report`'s question and needs facts a
parser cannot see.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from ..errors import AssayError, Outcome, ReasonCode
from ..vocabulary import INGESTED_OPERATOR_NAMESPACES
from .model import (
    INGESTED_MUTANT_STATUSES,
    IngestedMutant,
    IngestedMutationReport,
    MutationProducerIdentity,
)

__all__ = ["MAX_INGESTED_MUTANTS", "SUPPORTED_REPORT_SCHEMA_MAJORS", "parse", "sniff"]

#: The `mutation-testing-report-schema` majors this build reads. Pinned to the
#: major the committed real fixture carries (B046's own non-repudiation item
#: (iv)); a report announcing a major this build has never seen is refused
#: rather than read as if the shape had not changed.
#:
#: **Major 1 only, and the docstring above is the reason.** This shipped as
#: `{"1", "2"}` while the one committed real artifact carries
#: `schemaVersion: "1.0"` -- so major 2 was admitted on the strength of nothing
#: at all, which is precisely the state the pin exists to prevent. Major 2 is
#: refused as UNPROVEN, not as proven-defective, exactly as `jest-v8` is one
#: format over: assay has no artifact in which to see what changed, and reading
#: an unseen major as if the shape had held is the assumption a version field
#: exists to stop anyone making. It opens when a real report carrying it is
#: committed here and the parser is measured against it -- a one-line edit
#: beside a fixture, never a guess.
SUPPORTED_REPORT_SCHEMA_MAJORS: frozenset[str] = frozenset({"1"})

#: A fixed ceiling on how many mutants one report may carry, in the shape
#: `MAX_MAX_MUTANTS` already has one tier over: a bound, never an ambient
#: guess. It is deliberately NOT `judge.mutation.max_mutants` -- that field is
#: assay's own declared ceiling on a discovery assay performs, and an ingested
#: lane declares none at all (A-360). This is a resource bound on parsing a
#: document, which is a different thing with a different owner.
MAX_INGESTED_MUTANTS = 100_000

#: The namespace prefix ingested operators are qualified with. Derived from
#: `assay.vocabulary` rather than spelled here, so this module, the model, the
#: schema branch and the config loader cannot drift (A-362).
_NAMESPACE = INGESTED_OPERATOR_NAMESPACES[0]

#: Stryker's `mutatorName` values are CamelCase identifiers
#: (`BlockStatement`, `EqualityOperator`, ...). The pattern is
#: `INGESTED_OPERATOR_RE`'s own suffix class, kept in one place: a name that
#: does not match cannot be spelled as an operator in a v9 artifact at all, so
#: refusing it HERE turns a schema-validation failure at write time into a
#: named parse refusal that says which mutator was unspellable.
_MUTATOR_NAME_RE = re.compile(r"^[A-Za-z0-9]+$")

#: How much of a replacement is quoted into a mutant's `description`. The
#: description is a DIAGNOSTIC (A-180: it does not distinguish, the byte span
#: and the replacement digest do), and a `BlockStatement` mutant's replacement
#: can be an entire function body -- so it is bounded rather than embedded,
#: exactly as `MutantOutcome` omits `mutated_text` for the same reason.
_DESCRIPTION_REPLACEMENT_BYTES = 60


def _unreadable(message: str) -> AssayError:
    return AssayError(
        message, outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT
    )


def sniff(text: str) -> bool:
    """Does *text*'s content match THIS format's own signature?

    Cheap and structural, never a validation pass, and never "which format is
    this" -- the identical contract every coverage parser's ``sniff`` carries
    (A-007). The signature is the two members the upstream schema itself
    makes required and that no other artifact assay reads has together:
    ``schemaVersion`` and a ``files`` object.
    """
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return False
    return (
        isinstance(document, dict)
        and "schemaVersion" in document
        and isinstance(document.get("files"), dict)
    )


def parse(text: str) -> IngestedMutationReport:
    """Parse *text* as a ``mutation-report-json`` document.

    Raises ``ERROR``/``UNREADABLE_ARTIFACT`` on every malformed or unreadable
    shape, always naming the specific member at fault.
    """
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise _unreadable(f"mutation report is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise _unreadable(
            f"mutation report must be a JSON object, got "
            f"{type(document).__name__}"
        )

    schema_version = _report_schema_version(document)
    producer = _producer_identity(document, schema_version)
    project_root = _project_root(document)

    files = document.get("files")
    if not isinstance(files, dict):
        raise _unreadable(
            f"mutation report 'files' must be an object, got "
            f"{type(files).__name__}"
        )

    sources: dict[str, str] = {}
    mutants: list[IngestedMutant] = []
    for key, record in sorted(files.items()):
        if not isinstance(key, str) or not key:
            raise _unreadable("mutation report 'files' has a non-string key")
        if not isinstance(record, dict):
            raise _unreadable(
                f"mutation report file {key!r} must be an object, got "
                f"{type(record).__name__}"
            )
        source = record.get("source")
        if not isinstance(source, str):
            raise _unreadable(
                f"mutation report file {key!r} carries no 'source' string; "
                f"the upstream schema makes it required, and assay needs the "
                f"text the tool itself read to locate a mutant's byte span "
                f"and to say which in-scope lines it produced no mutant for"
            )
        sources[key] = source
        raw_mutants = record.get("mutants")
        if not isinstance(raw_mutants, list):
            raise _unreadable(
                f"mutation report file {key!r} carries no 'mutants' array"
            )
        line_offsets = _line_byte_offsets(source)
        source_bytes = len(source.encode("utf-8"))
        for entry in raw_mutants:
            mutants.append(
                _parse_mutant(
                    entry,
                    key=key,
                    line_offsets=line_offsets,
                    source_bytes=source_bytes,
                )
            )
            if len(mutants) > MAX_INGESTED_MUTANTS:
                raise _unreadable(
                    f"mutation report carries more than "
                    f"{MAX_INGESTED_MUTANTS:,} mutants; assay reads a bounded "
                    f"document, and a report this large is not one assay can "
                    f"judge without inventing a truncation policy the lane "
                    f"never declared"
                )

    pending = [mutant for mutant in mutants if mutant.status == "Pending"]
    if pending:
        first = pending[0]
        raise _unreadable(
            f"mutation report carries {len(pending)} mutant(s) still marked "
            f"'Pending' (first: {first.path}:{first.lineno}). Pending means "
            f"the run did not finish, so this report describes a partial "
            f"experiment -- incomplete evidence is not weaker evidence, it is "
            f"not evidence, and a score computed from it would be a real "
            f"number about an unreal run"
        )

    return IngestedMutationReport(
        producer=producer,
        project_root=project_root,
        mutants=tuple(mutants),
        sources=sources,
    )


def _report_schema_version(document: Mapping[str, Any]) -> str:
    raw = document.get("schemaVersion")
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise _unreadable(
            f"mutation report 'schemaVersion' must be a string, got "
            f"{type(raw).__name__}"
        )
    # The upstream field is TYPED as a string and real reporters emit `"1.0"`,
    # `"1"` and `"2"`; a number is accepted here only so a hand-built document
    # gets the MAJOR check below rather than a type complaint that hides it.
    version = str(raw)
    major = version.partition(".")[0]
    if major not in SUPPORTED_REPORT_SCHEMA_MAJORS:
        raise _unreadable(
            f"mutation report announces schemaVersion {version!r}, whose "
            f"major {major!r} this build does not read; supported majors: "
            f"{sorted(SUPPORTED_REPORT_SCHEMA_MAJORS)}. The field exists to "
            f"say when the document's shape changed, so reading it anyway "
            f"would be assuming the one thing it was written to deny"
        )
    return version


def _producer_identity(
    document: Mapping[str, Any], schema_version: str
) -> MutationProducerIdentity:
    """``framework.name``/``framework.version`` + the report's own
    ``schemaVersion``, all three verbatim.

    ``framework`` is OPTIONAL upstream and REQUIRED here, for
    ``projectRoot``'s reason (A-375): ``judgment.r2.producer_tool`` is the
    whole answer to "which tool's word is this verdict resting on", and a
    verdict that records ``ingested`` without naming the producer states the
    dependency without identifying it.
    """
    framework = document.get("framework")
    if not isinstance(framework, dict):
        raise _unreadable(
            "mutation report carries no 'framework' object; assay records the "
            "ingested producer's identity on the wire "
            "(judgment.r2.producer_tool), and a verdict that says 'ingested' "
            "without naming the tool states a dependency it cannot identify"
        )
    name = framework.get("name")
    version = framework.get("version")
    for label, value in (("name", name), ("version", version)):
        if not isinstance(value, str) or not value:
            raise _unreadable(
                f"mutation report 'framework.{label}' must be a non-empty "
                f"string, got {value!r}"
            )
    assert isinstance(name, str) and isinstance(version, str)
    return MutationProducerIdentity(
        name=name, version=version, report_schema_version=schema_version
    )


def _project_root(document: Mapping[str, Any]) -> str:
    project_root = document.get("projectRoot")
    if not isinstance(project_root, str) or not project_root:
        raise _unreadable(
            "mutation report carries no 'projectRoot'. The upstream schema "
            "makes it optional; assay REQUIRES it (A-375), because it is the "
            "only field that says where the report's own relative file keys "
            "are anchored. Without it, checking that this report describes "
            "THIS snapshot -- rather than some other checkout the same tool "
            "ran in -- would have to start by assuming the answer. Configure "
            "the reporter to emit it, or run the tool from the directory the "
            "lane declares"
        )
    return project_root


def _line_byte_offsets(source: str) -> tuple[int, ...]:
    """UTF-8 byte offset of the start of each line of *source*.

    Built once per file rather than per mutant: a real report puts sixty
    mutants in one file, and re-encoding the whole source for each of them is
    quadratic in a document assay already bounds by size.

    ``splitlines`` is deliberately NOT used -- it splits on a dozen Unicode
    line boundaries (``\\x0b``, ``\\u2028``, ...) that neither a JavaScript
    tokenizer nor ``location.line`` counts as line ends, so it would shift
    every offset after such a character.
    """
    offsets = [0]
    running = 0
    for line in source.split("\n"):
        running += len(line.encode("utf-8")) + 1  # + the "\n" itself
        offsets.append(running)
    return tuple(offsets)


def _byte_offset(
    line_offsets: tuple[int, ...], source_bytes: int, *, line: int, column: int, what: str
) -> int:
    """The UTF-8 byte offset of a one-based (*line*, *column*) position.

    Both coordinates are ONE-based and the end position is EXCLUSIVE --
    measured against the committed real Stryker artifact
    (``tests/fixtures/mutation/PROVENANCE.md``), not taken from prose: for the
    ``StringLiteral`` mutant at ``src/format.ts`` line 15, columns 35..43 span
    exactly the eight characters of ``'number'``.
    """
    if line < 1 or line > len(line_offsets) - 1:
        raise _unreadable(
            f"mutation report {what} names line {line}, which is outside the "
            f"1..{len(line_offsets) - 1} lines of the source the report "
            f"itself carries for that file"
        )
    if column < 1:
        raise _unreadable(
            f"mutation report {what} names column {column}; columns are "
            f"one-based"
        )
    line_start = line_offsets[line - 1]
    # The line's own text, so a column past its end is caught as a column
    # error rather than silently landing inside the next line.
    line_end = line_offsets[line] - 1
    offset = line_start + column - 1
    if offset > line_end or offset > source_bytes:
        raise _unreadable(
            f"mutation report {what} names line {line} column {column}, which "
            f"is past the end of that line in the source the report itself "
            f"carries"
        )
    return offset


def _parse_mutant(
    entry: Any,
    *,
    key: str,
    line_offsets: tuple[int, ...],
    source_bytes: int,
) -> IngestedMutant:
    if not isinstance(entry, dict):
        raise _unreadable(
            f"mutation report file {key!r} has a non-object mutant entry"
        )
    identifier = entry.get("id")
    where = f"{key} mutant {identifier!r}"

    status = entry.get("status")
    if not isinstance(status, str) or status not in INGESTED_MUTANT_STATUSES:
        raise _unreadable(
            f"{where} carries status {status!r}, which is not one of the "
            f"mutation-testing-report-schema statuses this build knows "
            f"({sorted(INGESTED_MUTANT_STATUSES)}). Refused rather than "
            f"dropped: a mutant silently discarded here leaves both the "
            f"numerator and the denominator wrong in a way that reads exactly "
            f"like a correct score"
        )

    mutator = entry.get("mutatorName")
    if not isinstance(mutator, str) or not _MUTATOR_NAME_RE.fullmatch(mutator):
        raise _unreadable(
            f"{where} carries mutatorName {mutator!r}, which is not spellable "
            f"as an assay operator: an ingested operator is "
            f"'{_NAMESPACE}:<name>' where <name> matches "
            f"{_MUTATOR_NAME_RE.pattern}"
        )

    replacement = entry.get("replacement")
    if not isinstance(replacement, str):
        raise _unreadable(
            f"{where} carries no 'replacement' string. It is optional in the "
            f"upstream schema and it is half of assay's mutant IDENTITY "
            f"(A-180: the site plus the replacement bytes) -- without it two "
            f"genuinely distinct experiments at one span collapse into one "
            f"record, which is the exact lossiness that identity exists to "
            f"prevent"
        )

    location = entry.get("location")
    if not isinstance(location, dict):
        raise _unreadable(f"{where} carries no 'location' object")
    start = location.get("start")
    end = location.get("end")
    for label, position in (("start", start), ("end", end)):
        if not isinstance(position, dict):
            raise _unreadable(f"{where} location has no {label!r} position")
        for field in ("line", "column"):
            value = position.get(field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise _unreadable(
                    f"{where} location.{label}.{field} must be an integer, "
                    f"got {value!r}"
                )
    assert isinstance(start, dict) and isinstance(end, dict)

    start_byte = _byte_offset(
        line_offsets,
        source_bytes,
        line=start["line"],
        column=start["column"],
        what=f"{where} location.start",
    )
    end_byte = _byte_offset(
        line_offsets,
        source_bytes,
        line=end["line"],
        column=end["column"],
        what=f"{where} location.end",
    )
    if end_byte <= start_byte:
        raise _unreadable(
            f"{where} spans [{start_byte}, {end_byte}), which is empty or "
            f"reversed; a mutation site always replaces at least one byte"
        )

    preview = replacement.encode("utf-8")[:_DESCRIPTION_REPLACEMENT_BYTES].decode(
        "utf-8", errors="ignore"
    )
    if len(preview) < len(replacement):
        preview += "..."
    return IngestedMutant(
        path=key,
        lineno=start["line"],
        start_byte=start_byte,
        end_byte=end_byte,
        replacement_sha256=hashlib.sha256(replacement.encode("utf-8")).hexdigest(),
        operator=f"{_NAMESPACE}:{mutator}",
        description=f"{mutator} -> {preview}" if preview else mutator,
        status=status,
    )
