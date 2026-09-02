"""The ``assay.toml`` loader — the config contract that refuses to invent.

The whole point of this module is stated in AGENTS.md §4.2a and applied in
DESIGN-GUIDE §5: *a default is legitimate only when it is a policy choice that
is correct in the absence of information; it is a hazard the moment it
substitutes for a fact that exists somewhere else.* All four `coverage_gate.py`
copies in the estate ship anti-pattern #1 — `default="src/nyxloom"`,
`default="topos/src/topos"`, `DEFAULT_SOURCE = "libs/common/src,…"`,
`-source internal` — and when one of those literals is wrong the gate measures
the wrong tree and **passes**.

So: **this loader has no defaults at all.** Every value in a :class:`Lane` came
out of the file. There is no key on a loaded object that the file did not
declare, which is what :meth:`Lane.as_declared` exists to make mechanical.

The complete rejection surface, so it can be reviewed in one place:

============================  ==========================================  =======
Rule                          Rejects                                     Source
============================  ==========================================  =======
schema version                missing / non-int / not ``1``               §12
lane table                    file with no ``[lanes]``, or an empty one   §12
required lane fields          any of the eight missing                    P01a O1
closed vocabularies           ``scope`` ∉ S0-S4, ``rigor`` ∉ R0-R3,       A-053
                              ``enforcement`` ∉ {gate, advisory}
duration                      ``budget`` that does not parse              A-052
declared rigor is enforced    R1 without the five judge fields, R2        A-017,
                              without ``mutation``, R3 without            A-048
                              ``canary``
source roots                  absolute, or not an existing directory      A-016,
                              under the project root                      A-049
unknown keys                  a key assay does not understand, in a       §12
                              lane table or in ``[…judge]``
coverage format                ``judge.coverage.format`` not a key the    A-068
                              parser registry knows
============================  ==========================================  =======

One thing this loader deliberately does **not** reject, because rejecting it
would be inventing policy rather than refusing to invent values:

* a ``[…where]`` table's contents. §7: WHERE is "data assay parses and never
  interprets", permanently. It is carried through verbatim.

It *does* reject ``judge`` config for a rigor level the lane does not declare
(A-062) — ``mutation`` on a lane without ``R2``, ``fail_under`` on an R0 lane.
Inert configuration cannot fail loudly when it is wrong, and it reads to a
human exactly like the capability it is not providing.

`judge.coverage.format` is checked against :data:`assay.coverage.FORMAT_REGISTRY`
(A-068): the closed vocabulary for it is *"a key the parser registry knows"*
(§12), and that registry is imported here rather than duplicated — the same
defence against a second, driftable copy of the key list that this loader
already applies to `scope`/`rigor`/`enforcement` via their own `frozenset`s.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .coverage import FORMAT_REGISTRY
from .errors import LaneConfigError
# (B046) The ingested-mutation format registry, imported at module level for
# `FORMAT_REGISTRY`'s own reason (A-068): `judge.mutation.format` is closed
# against the registry's own keys, never a second hardcoded list. The package
# imports only `assay.errors` and `assay.vocabulary`, both leaves, so this
# does not open the `config -> mutation -> config` cycle that would exist if
# the registry lived in `assay.mutation`.
from .mutation_parsers import MUTATION_FORMAT_REGISTRY
from .vocabulary import (
    COVERAGE_PRODUCERS_BY_FORMAT,
    COVERAGE_PRODUCER_REQUIRED_FORMATS,
    MUTATION_OPERATORS,
    MUTATION_OPERATORS_BY_LANGUAGE,
    REFUSED_COVERAGE_PRODUCERS,
    STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE,
    WITHDRAWN_MUTATION_OPERATORS,
    operator_language,
)

__all__ = [
    "CanaryConfig",
    "CoverageConfig",
    "ENFORCEMENTS",
    "EvidenceConfig",
    "IsolationConfig",
    "JUDGE_BASE_SOURCES",
    "JUDGE_FIELDS_BY_RIGOR",
    "JUDGE_MODES",
    "JudgeConfig",
    "LANE_FILE_NAME",
    "LANE_SCHEMA_VERSION",
    "Lane",
    "LaneFile",
    "MutationConfig",
    "REQUIRED_LANE_FIELDS",
    "RIGOR_LEVELS",
    "SCOPES",
    "SNAPSHOT_SELECTIONS",
    "find_lane_file",
    "load_lane_file",
    "parse_duration",
]

#: P26/A-209-A-210. Duplicated verbatim from :mod:`assay.attestation` (which
#: repeats it at its own public boundary) rather than imported: config.py and
#: attestation.py stay independent readers of the same closed grammar, and
#: neither trusts the other to have already validated it.
MAX_ATTESTATION_DIR_BYTES = 4096
MAX_ATTESTATION_DIR_COMPONENTS = 128
MIN_EVIDENCE_DECLARATIONS = 1
MAX_EVIDENCE_DECLARATIONS = 64
_EVIDENCE_FIELDS: tuple[str, ...] = ("source", "key")
_EVIDENCE_SOURCES: frozenset[str] = frozenset({"attested"})
_EVIDENCE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ATTESTATION_DIR_CONTROL: frozenset[str] = frozenset(
    chr(c) for c in range(0x20)
) | {chr(0x7F)}

#: A-014. The lane file is `assay.toml`, at the project root.
LANE_FILE_NAME = "assay.toml"

#: The lane-file schema this build understands. Distinct from the *verdict*
#: artifact's `schema_version` (§6), which is P01b's.
#:
#: A-269/B006(a) WI-1: bumped 1 -> 2 for `[lanes.X.isolation]`, the declared
#: repository snapshot policy. The bump is a hard cut, not additive: an old
#: missing `[isolation]` table cannot be interpreted as repository mode
#: without inventing the exact shadowing default this loader exists to
#: refuse (`config.py`'s own docstring, "a default is legitimate only when it
#: is a policy choice that is correct in the absence of information"), and an
#: old binary cannot parse the new table either. Only Assay-owned literals
#: this build itself loads and executes migrate in WI-1; `cmru/assay.toml` is
#: evaluated by a pinned v1-only binary and stays v1 until WI-6's atomic
#: consumer adoption commit.
LANE_SCHEMA_VERSION = 2

#: A-053. TESTING-METHODOLOGY §Axis 1 / §Axis 2, and §12's table.
SCOPES: frozenset[str] = frozenset({"S0", "S1", "S2", "S3", "S4"})
RIGOR_LEVELS: tuple[str, ...] = ("R0", "R1", "R2", "R3")
ENFORCEMENTS: frozenset[str] = frozenset({"gate", "advisory"})

#: The eight fields every lane declares at its top level, whatever its rigor.
REQUIRED_LANE_FIELDS: tuple[str, ...] = (
    "scope",
    "rigor",
    "enforcement",
    "argv",
    "env",
    "env_passthrough",
    "budget",
    "allow_argv_append",
)

_OPTIONAL_LANE_FIELDS: tuple[str, ...] = (
    "judge",
    "where",
    "isolation",
    "env_required",
    "environment_command",
    "infrastructure",
    # (B043, schema v9) the lane command's declared working directory.
    "cwd",
)

INFRASTRUCTURE_SOURCES: frozenset[str] = frozenset({"required-env", "derived"})
MAX_INFRASTRUCTURE_FACTS = 64

#: (B022 item 4) bounds a single RESOLVED infrastructure value at runtime, not
#: the declared fact count above -- `resolve_command_plan` enforces this one,
#: since the value is only known once `required-env`/`derived` resolves. Keeps
#: a malformed or oversized source (e.g. a `derived:` path landing on a whole
#: file's content instead of one field) from failing late and opaquely at
#: `E2BIG` on exec; comfortably above any real fact (ports, hosts, tokens,
#: small JSON blobs) and comfortably below typical Linux `ARG_MAX`.
MAX_INFRASTRUCTURE_VALUE_BYTES = 65536

#: (B006a/A-269, §3.2) The closed `isolation.snapshot_selection` vocabulary.
#: `"repository"` materialises the whole commit; `"repository-minus-unsafe-
#: symlinks"` additionally omits exactly the declared, commit-validated
#: unsafe symlink leaves. There is no third value and no default -- an R1+
#: lane must pick one, an R0-only lane must pick none.
SNAPSHOT_SELECTIONS: frozenset[str] = frozenset(
    {"repository", "repository-minus-unsafe-symlinks"}
)

_ISOLATION_FIELDS: tuple[str, ...] = (
    "snapshot_selection",
    "unsafe_symlink_omissions",
    # (B041(b), schema v9) paths symlinked IN from the invoking checkout.
    "link_paths",
)

#: (B041(b)) The bound on a declared `link_paths` list, deliberately equal to
#: `MAX_UNSAFE_SYMLINK_OMISSIONS` and deliberately declared separately. The
#: two lists mean OPPOSITE things -- one names what the snapshot left out,
#: the other what was linked in -- and a shared constant would make one bound
#: change silently move the other (A-366's own transcription reasoning, one
#: constant over).
MAX_LINK_PATHS = 64

#: (§3.2) "contains 1 through 64 strings. Empty omission mode is refused; use
#: `\"repository\"` instead." A cap, never derived from a measured repository
#: -- the same "no runtime consumer may invent a missing cap" reasoning
#: `MAX_MAX_MUTANTS` already gives one policy over.
MIN_UNSAFE_SYMLINK_OMISSIONS = 1
MAX_UNSAFE_SYMLINK_OMISSIONS = 64

#: (§3.2) The byte ceiling on one declared omission pathname, the same bound
#: `MAX_ATTESTATION_DIR_BYTES` already applies to a different declared path
#: one field over -- both exist so a pathological string cannot make the
#: loader itself the resource the config file attacks.
MAX_OMISSION_PATH_BYTES = 4096

#: A-017/A-048: declared rigor is ENFORCED, and the `judge` fields are
#: CONDITIONALLY required — an R0-only lane has no `[judge]` table at all.
#: Requirements are per DECLARED level, not per highest level, because `rigor`
#: is a *list* of independently declared methods (§6: a lane declaring
#: ["R0","R1","R2"] can pass R0, pass R1 and be INCONCLUSIVE on R2). R2 and R3
#: pull in `language` and `source_roots` because mutation and canary cannot act
#: at all without knowing the adapter and the tree they act on — A-017's own
#: rule, that a lane may not claim a level it cannot exercise.
JUDGE_FIELDS_BY_RIGOR: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "R0": (),
        "R1": (
            "language",
            "source_roots",
            "fail_under",
            "allow_excluded",
            "coverage",
            "base",
        ),
        "R2": ("language", "source_roots", "mutation", "base"),
        "R3": ("language", "source_roots", "canary"),
    }
)

_KNOWN_JUDGE_FIELDS: tuple[str, ...] = (
    "language",
    "source_roots",
    "fail_under",
    "allow_excluded",
    "coverage",
    "mutation",
    "canary",
    "base",
    "base_source",
    "mode",
    "targets",
    "require_branch",
)

#: (B019/A-328) who owns the comparison commit a changed-line lane judges
#: against. ``"declared"`` -- the default, and the only behaviour that existed
#: before this key -- means ``judge.base`` is a fact of the lane file.
#: ``"request"`` means the lane requires changed-line judging but DELEGATES the
#: base identity to whatever gate request invokes it (``assay run
#: --request-base``), so one static lane declaration stays correct across every
#: branch and worktree while the orchestrator owns branch awareness. The two
#: are mutually exclusive: a lane declaring ``"request"`` may not also declare
#: ``judge.base``, and a run may not supply ``--request-base`` to a lane that
#: did not delegate.
JUDGE_BASE_SOURCES: frozenset[str] = frozenset({"declared", "request"})

#: (wave-1 §5, A-260) the closed `judge.mode` vocabulary. Absent means
#: `"changed_lines"` -- the only mode that existed before this wave -- so
#: `JudgeConfig.mode` stores the DECLARED value or `None`, never a filled-in
#: default: `as_declared()` stays a faithful reproduction of the file, and
#: the *effective* mode is resolved at exactly one named place
#: (:mod:`assay.runner`'s `evaluate_r1`).
JUDGE_MODES: frozenset[str] = frozenset({"changed_lines", "whole_target"})

#: The two fields every `[judge.coverage]` table must declare.
_COVERAGE_FIELDS: tuple[str, ...] = ("format", "artifact")

#: (B045, schema v9) `producer` -- the toolchain that WRITES the artifact --
#: is a THIRD key, listed separately because it is required for some formats
#: and refused for others, so it cannot join the flat required tuple above.
#: See :data:`assay.vocabulary.COVERAGE_PRODUCERS_BY_FORMAT`.
_COVERAGE_OPTIONAL_FIELDS: tuple[str, ...] = ("producer",)

# A duration is one or more <number><unit> segments in descending order, e.g.
# "5m", "90s", "1h30m". A bare number is REFUSED: unit-less is ambiguous, and
# guessing the unit is exactly the invention this loader exists to prevent.
_DURATION_RE = re.compile(
    r"^(?:(?P<h>\d+(?:\.\d+)?)h)?(?:(?P<m>\d+(?:\.\d+)?)m)?(?:(?P<s>\d+(?:\.\d+)?)s)?$"
)
_DURATION_HINT = "expected a duration such as '90s', '5m' or '1h30m'"


def parse_duration(text: str) -> float:
    """Parse a duration string to seconds (A-052 — at LOAD, not at run time).

    Raises :class:`ValueError` for anything that is not a positive duration.
    Callers in this module convert that into a :class:`LaneConfigError`; the
    function itself stays reusable by P07's runner.
    """
    if not isinstance(text, str):
        raise ValueError(f"duration must be a string, got {type(text).__name__}")
    match = _DURATION_RE.fullmatch(text.strip())
    if match is None or not any(match.group(g) for g in ("h", "m", "s")):
        raise ValueError(f"{text!r} is not a duration ({_DURATION_HINT})")
    seconds = (
        float(match.group("h") or 0) * 3600.0
        + float(match.group("m") or 0) * 60.0
        + float(match.group("s") or 0)
    )
    if seconds <= 0:
        raise ValueError(f"{text!r} is not a positive duration ({_DURATION_HINT})")
    return seconds


@dataclass(frozen=True)
class CoverageConfig:
    """``[lanes.X.judge.coverage]`` — the declared format, artifact path and
    (B045, schema v9) the declared PRODUCER.

    ``producer`` is ``None`` exactly when the file omitted it, never a
    filled-in default: for ``coverage-istanbul-json`` the loader REFUSES the
    omission outright (its producers disagree, so no implied value is correct
    -- DESIGN-GUIDE §5), and for every other format ``None`` is a real,
    meaningful "not declared" that the verdict records by ABSENCE.
    """

    format: str
    artifact: str
    producer: str | None = None

    def as_declared(self) -> dict[str, Any]:
        declared: dict[str, Any] = {
            "format": self.format,
            "artifact": self.artifact,
        }
        # Omitted, never null (A-051) -- `as_declared` must compare equal to
        # tomllib's own parse of the table.
        if self.producer is not None:
            declared["producer"] = self.producer
        return declared


#: The fields a NATIVE R2 lane must declare -- assay's own execution policy.
#: Required on a native lane and REFUSED on an ingested one (B046/A-360):
#: assay chose none of them for a run it did not orchestrate.
_MUTATION_FIELDS: tuple[str, ...] = ("jobs", "max_mutants", "operators")
_MUTATION_OPTIONAL_FIELDS: tuple[str, ...] = (
    "budget_per_candidate",
    "shard_index",
    "shard_count",
)

#: (B046) The fields an INGESTED R2 lane declares: which report FORMAT the
#: lane's own argv will write, WHERE it writes it, and the score floor assay
#: judges it against. `format`'s PRESENCE is the discriminator -- the runner
#: selects native vs ingested by exactly this, never by the lane's language
#: and never by sniffing the artifact.
_MUTATION_INGESTED_FIELDS: tuple[str, ...] = ("format", "artifact", "fail_under")

#: (P33/A-227/A-230b, narrowed P34/W4) the two v6 artifact fields, legal
#: ONLY on a ``judge.language = "sql"`` lane. Named separately from
#: `_MUTATION_FIELDS` so the refusal can name the specific reason (language
#: mismatch) rather than reporting an UNKNOWN key: both are refused either
#: way for a non-SQL lane, and only the message tells a reader whether the
#: field is a typo or a capability this lane's own language cannot use.
#: Through P33 both names were reserved for EVERY language (no adapter
#: shipped a producer at all); P34 lifts the refusal for `"sql"` only --
#: A-227's "the refusal for every other language must survive" stays a
#: test, never a note.
_MUTATION_SQL_ONLY_FIELDS: tuple[str, ...] = (
    "kill_signal_artifact",
    "equivalence_artifact",
)

#: (P21/A-163) the declared candidate ceiling's inclusive bounds. Required,
#: never defaulted: "no runtime consumer may invent a missing cap" is the
#: whole point -- a default cap would be a policy assay chose while looking
#: like one the lane declared, and if it were wrong nothing would fail
#: loudly (AGENTS.md 4.2a's own test).
MIN_MAX_MUTANTS = 1
MAX_MAX_MUTANTS = 10_000

#: (B026 N-5 round-3 note) `judge.mutation.shard_count`'s own bound below used
#: to reuse `MAX_MAX_MUTANTS` -- a DIFFERENT ceiling that happens to equal the
#: same 10,000 today, for its own unrelated reason (the discovery-limit
#: sentinel, `MAX_CANDIDATE_CEILING`, is `max_mutants + 1`). A shard count has
#: no relationship to a mutant-count ceiling; this constant is the one a
#: shard count should actually be checked against. Deliberately duplicated
#: from `verdict.py`'s identically-named, identically-valued constant rather
#: than imported (`verdict.py` already imports FROM this module, so the
#: reverse import would be circular) -- the same tradeoff `COMMAND_TAIL_BYTES`
#: already makes between these two modules, for the same reason.
MAX_SHARD_COUNT = 10_000


@dataclass(frozen=True)
class MutationConfig:
    """``[lanes.X.judge.mutation]`` (P18) -- the closed R2 execution policy:
    a required positive ``jobs`` worker count (A-082/A-122: never derived
    from the running machine, so this loader is the one place a value for
    it can come from at all) and a non-empty, duplicate-free, ORDER-
    preserving ``operators`` list, cross-checked at LOAD time against
    :data:`assay.mutation.MUTATION_OPERATORS` -- the same "vocabulary
    imported from its own owner, never duplicated" discipline
    :data:`assay.coverage.FORMAT_REGISTRY` already gets one field over
    (A-068). Before P18 this table was an opaque, unvalidated passthrough
    (P11/P12 owned construction/execution, not the declared policy); this
    loader is the first reader of its actual shape.
    """

    #: **Required on a NATIVE lane, `None` on an ingested one** (B046). The
    #: three fields below are assay's own execution policy; an ingested lane
    #: declared none of it, because Stryker's own configuration chose the
    #: mutators, the concurrency and the ceiling. They are refused at LOAD
    #: with their own message, and independently forbidden on the WIRE by
    #: `JudgmentR2` -- two layers, two diagnostics, because a verdict-layer
    #: refusal is not a thing a consumer editing `assay.toml` ever sees.
    jobs: int | None = None
    #: (P21/A-163) the declared candidate ceiling, in ``1..10_000``.
    #: ``jobs`` bounds CONCURRENCY; this bounds total work and memory, which
    #: concurrency never did (A-160's own "jobs bounds workers, not total
    #: executions"). `None` on an ingested lane -- see :attr:`jobs`.
    max_mutants: int | None = None
    operators: tuple[str, ...] | None = None
    #: (B046) The ingested report FORMAT, a
    #: :data:`assay.mutation_parsers.MUTATION_FORMAT_REGISTRY` key, or `None`
    #: on a native lane. **This field's PRESENCE is the discriminator**: the
    #: runner selects the ingested path by `format is not None` and by
    #: nothing else -- never by the lane's language (any language whose tool
    #: emits a registered format may use it) and never by sniffing the
    #: artifact (A-007).
    format: str | None = None
    #: (B046) Where the lane's own argv writes that report, project-relative
    #: like every other declared artifact path. Required with
    #: :attr:`format`, `None` without it. **Project-root-relative even on a
    #: lane declaring `cwd`** (A-369/A-271: one path grammar).
    artifact: str | None = None
    #: (B046) The mutation-score floor an ingested lane is judged against:
    #: `killed / (killed + survived)`, in percent. Required with
    #: :attr:`format` and never defaulted -- a floor assay invented would be
    #: a policy assay chose while looking like one the lane declared
    #: (DESIGN-GUIDE §5). Native R2 has no such field and needs none: it
    #: fails on any survivor at all.
    fail_under: float | None = None
    #: (B012) Optional per-candidate wall-clock bound. ``None`` preserves the
    #: existing lane-wide-only behavior; a declared value bounds each mutant's
    #: command independently without changing the lane deadline.
    budget_per_candidate: str | None = None
    #: (P34/W4) SQL-only. ``None`` for every other language -- `_load_mutation`
    #: refuses either key at load for a non-``"sql"`` lane, so a non-``None``
    #: value here is only ever possible on a ``judge.language = "sql"`` lane.
    #: Optional even there: A-223b derives `kill_attribution` from this
    #: field's own presence, so "declared" is a real, meaningful choice.
    kill_signal_artifact: str | None = None
    #: (P34/W4) SQL-only, and REQUIRED once ``judge.language == "sql"``
    #: (`_load_mutation` refuses a SQL lane that omits it): without it, a
    #: mutant that never actually mutated is recorded ``survived`` rather
    #: than ``equivalent`` -- a false statement about the consumer's tests
    #: (§4.3 of the P34 carve). ``None`` for every other language.
    equivalence_artifact: str | None = None
    #: (B012) The declared zero-based shard position. ``None`` means the
    #: whole declared workload.
    #:
    #: (B026 N-5, decided 2026-08-25) **Reserved for future use -- read by
    #: nothing in `runner.py`/`mutation.py` today.** The lane declaration is
    #: validated and echoed back by `as_declared()` (round-tripping what a
    #: consumer wrote), but the shard actually EXECUTED and recorded in
    #: `judgment.r2.shard_index`/`shard_count` always comes from the `--shard`
    #: CLI flag alone, never from here. Declaring these two fields with no
    #: `--shard` flag on the invocation runs the WHOLE workload, silently --
    #: this is not a bug to fix by wiring them as a `--shard` default (that
    #: would be a real behavior change, decided against here) so much as a
    #: capability this build has not built yet; kept declared/validated
    #: rather than removed so a future package can wire them without a lane
    #: schema migration.
    shard_index: int | None = None
    #: (B012) The declared shard cardinality; required with ``shard_index``.
    #: See the B026 N-5 note on ``shard_index`` above -- identically inert.
    shard_count: int | None = None

    @property
    def is_ingested(self) -> bool:
        """(B046) Whether this lane's R2 evidence is INGESTED rather than
        computed by assay's own engine.

        Derived from :attr:`format`'s presence -- the one discriminator --
        rather than stored, so no artifact can exist in which the flag and the
        fields disagree (A-036's own deriving-removes-the-disagreement rule).
        """
        return self.format is not None

    def as_declared(self) -> dict[str, Any]:
        if self.is_ingested:
            payload = {
                "format": self.format,
                "artifact": self.artifact,
                "fail_under": self.fail_under,
            }
            if self.kill_signal_artifact is not None:
                payload["kill_signal_artifact"] = self.kill_signal_artifact
            return payload
        payload: dict[str, Any] = {
            "jobs": self.jobs,
            "max_mutants": self.max_mutants,
            "operators": list(self.operators or ()),
        }
        # (P34/W4) Declared value or omitted, never null (A-051) -- present
        # only for the SQL lane that actually named it.
        if self.kill_signal_artifact is not None:
            payload["kill_signal_artifact"] = self.kill_signal_artifact
        if self.equivalence_artifact is not None:
            payload["equivalence_artifact"] = self.equivalence_artifact
        if self.budget_per_candidate is not None:
            payload["budget_per_candidate"] = self.budget_per_candidate
        if self.shard_index is not None and self.shard_count is not None:
            payload["shard_index"] = self.shard_index
            payload["shard_count"] = self.shard_count
        return payload


_CANARY_FIELDS: tuple[str, ...] = ("mechanism", "target")


@dataclass(frozen=True)
class CanaryConfig:
    """``[lanes.X.judge.canary]`` (P19) -- the closed R3 declaration: which
    :mod:`assay.canary` mechanism to attempt, and which single source file to
    attempt it against. Exactly two fields, never a plural list (P19 work
    item 2: one R3 claim is one mechanism execution, never several results
    collapsed into schema v3's single canary payload).

    Before P19 this table was opaque and unvalidated (A-106: P09 built the
    mechanism, not the config reader for it); this loader is the first
    reader of its actual shape. ``target`` is the DECLARED string, verbatim
    and PROJECT-relative -- the same spelling :attr:`CoverageConfig.artifact`
    already uses, and the one :func:`~assay.canary.run_python_canary` already
    expects (A-145: repo-relative and project-relative are two spellings of
    the same file, and this loader speaks the project-relative one).
    """

    mechanism: str
    target: str

    def as_declared(self) -> dict[str, Any]:
        return {"mechanism": self.mechanism, "target": self.target}


@dataclass(frozen=True)
class EvidenceConfig:
    """``judge.evidence[]`` (P26/A-209) -- one declared Tier-3 identity.

    Exactly ``source``/``key``; ``source`` is closed to ``"attested"`` (the
    adjudicated sibling has no loader, A-085). Reproduces the parsed TOML
    entry exactly via :meth:`as_declared`, the same mechanical no-invented-
    default proof :meth:`Lane.as_declared` already gives the rest of the file.
    """

    source: str
    key: str

    def as_declared(self) -> dict[str, str]:
        return {"source": self.source, "key": self.key}


@dataclass(frozen=True)
class JudgeConfig:
    """``[lanes.X.judge]`` — HOW to judge (D7's second question, A-015).

    Every field is ``None`` when the lane did not declare it. ``None`` here
    means *absent from the file*, never *assay chose this*.
    """

    language: str | None
    #: exactly the strings the file declared, in file order
    source_roots: tuple[str, ...] | None
    #: those strings resolved against the project root (A-049), each verified
    #: to be an existing directory (A-016)
    source_root_paths: tuple[Path, ...] | None
    fail_under: float | None
    allow_excluded: bool | None
    coverage: CoverageConfig | None
    #: the closed R2 execution policy (P18) -- ``None`` when the lane does
    #: not declare R2 at all.
    mutation: MutationConfig | None
    #: the closed R3 declaration (P19) -- ``None`` when the lane does not
    #: declare R3 at all.
    canary: CanaryConfig | None
    #: (P17) the declared comparison ref R1/R2 diff against -- a git
    #: revision expression (branch name, tag, SHA...), resolved at RUN time
    #: (:func:`assay.git.resolve_base`/:func:`assay.measurability.
    #: check_base_is_head`), never guessed here. Required, never defaulted
    #: to "main" or another assumed ref (A-018's own "no invented values").
    base: str | None
    #: (P26/A-209) Tier-3 evidence's HOW pair -- both ``None`` or both
    #: present, on ANY canonical R0-led rigor sequence including R0-only.
    #: Separate from computed rigor (§3): declaring these two never satisfies
    #: nor requires a computed ``judge`` field.
    attestation_dir: str | None = None
    evidence: tuple[EvidenceConfig, ...] | None = None
    #: (wave-1 §5, A-260) the declared R1 mode, verbatim -- `None` when the
    #: file omits `judge.mode`, which means `"changed_lines"`. Never
    #: defaulted here: the loader stores exactly what the file said, and
    #: `evaluate_r1` resolves the effective mode at exactly one named place.
    mode: str | None = None
    #: (wave-1 §5) the declared `judge.targets`, canonical project-relative
    #: spellings in file order -- `None` iff the lane is not `whole_target`
    #: mode (`targets` is refused at load otherwise).
    targets: tuple[str, ...] | None = None
    #: (wave-1 §4, A-259) the declared `judge.require_branch` -- `None` when
    #: the file omits it, which means `false`. Legal only on a lane
    #: declaring R1.
    require_branch: bool | None = None
    #: (B019/A-328) the declared `judge.base_source`, verbatim -- `None` when
    #: the file omits it, which means `"declared"`. Stored as declared, never
    #: defaulted here, exactly as `mode` is: the loader records what the file
    #: said and `runner.resolve_base_declaration` resolves the effective
    #: policy at exactly one named place.
    base_source: str | None = None

    def as_declared(self) -> dict[str, Any]:
        declared: dict[str, Any] = {}
        if self.language is not None:
            declared["language"] = self.language
        if self.source_roots is not None:
            declared["source_roots"] = list(self.source_roots)
        if self.fail_under is not None:
            declared["fail_under"] = self.fail_under
        if self.allow_excluded is not None:
            declared["allow_excluded"] = self.allow_excluded
        if self.coverage is not None:
            declared["coverage"] = self.coverage.as_declared()
        if self.mutation is not None:
            declared["mutation"] = self.mutation.as_declared()
        if self.canary is not None:
            declared["canary"] = self.canary.as_declared()
        if self.base is not None:
            declared["base"] = self.base
        if self.base_source is not None:
            declared["base_source"] = self.base_source
        if self.mode is not None:
            declared["mode"] = self.mode
        if self.targets is not None:
            declared["targets"] = list(self.targets)
        if self.require_branch is not None:
            declared["require_branch"] = self.require_branch
        if self.attestation_dir is not None:
            declared["attestation_dir"] = self.attestation_dir
        if self.evidence is not None:
            declared["evidence"] = [item.as_declared() for item in self.evidence]
        return declared


def _validate_omission_path(value: Any, *, where: str, field: str) -> str:
    """One declared ``unsafe_symlink_omissions`` entry (§3.2's own order):
    require a ``str``; require it non-empty; refuse a leading ``/``; require
    strict UTF-8 encoding of at most :data:`MAX_OMISSION_PATH_BYTES` bytes;
    split the raw value on ``/``; and refuse if any component is ``''``,
    ``.``, ``..``, or ``.git``, or contains a backslash or NUL.

    There is deliberately no second ``PurePosixPath(value).as_posix() ==
    value`` branch: every spelling this rejects (``./x``, ``x//y``, ``x/``)
    is already refused by its OWN component landing in the forbidden set
    above (a leading ``.`` component, an empty component from a doubled or
    trailing slash), so equality to that round-trip is a THEOREM of the
    accepted grammar, not a separate check -- proved for every accepted path
    by the accept-side matrix in ``tests/test_config_snapshot_selection.py``,
    never by an unreachable extra refusal branch here.
    """
    if not isinstance(value, str):
        raise LaneConfigError(f"{where}: {field} must be a string, got {_type_name(value)}")
    if not value:
        raise LaneConfigError(f"{where}: {field} must not be empty")
    if value.startswith("/"):
        raise LaneConfigError(f"{where}: {field} {value!r} must not be absolute")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise LaneConfigError(
            f"{where}: {field} {value!r} cannot be encoded as strict UTF-8: {exc}"
        ) from exc
    if len(encoded) > MAX_OMISSION_PATH_BYTES:
        raise LaneConfigError(
            f"{where}: {field} {value!r} is {len(encoded)} UTF-8 bytes, exceeding "
            f"the {MAX_OMISSION_PATH_BYTES}-byte ceiling"
        )
    for component in value.split("/"):
        if (
            component in ("", ".", "..", ".git")
            or "\\" in component
            or "\x00" in component
        ):
            raise LaneConfigError(
                f"{where}: {field} {value!r} has an invalid path component "
                f"{component!r}; a component must not be empty, '.', '..', "
                f"'.git', or contain a backslash or NUL"
            )
    return value


@dataclass(frozen=True)
class IsolationConfig:
    """``[lanes.X.isolation]`` (B006a/A-269, §3.2) -- the declared repository
    snapshot materialisation policy for an R1/R2/R3 lane. A POLICY object,
    never a Git fact: it says what Assay was TOLD to omit, not that a commit
    was inspected (that is P22/WI-2's job, in :mod:`assay.isolation`).

    Both constructor arguments are REQUIRED, on the loaded and the directly
    constructed path alike -- ``__post_init__`` runs the identical closed
    grammar either way, so a direct caller cannot construct a shape the
    loader would have refused. Under ``"repository"`` the caller supplies the
    derived internal empty tuple explicitly; there is no default that would
    let one meaning of "no omissions" and one meaning of "not declared"
    collapse into the same absent value.
    """

    snapshot_selection: str
    unsafe_symlink_omissions: tuple[str, ...]
    #: (B041(b), schema v9) Repo-top-relative directories symlinked from the
    #: INVOKING CHECKOUT into every snapshot this lane creates, immediately
    #: after ``read-tree`` and before any command runs. Empty -- the only
    #: value that existed before v9 -- means the snapshot is purely committed
    #: objects.
    #:
    #: Defaulted, unlike this class's other two fields, and for a reason that
    #: does not apply to them: ``unsafe_symlink_omissions`` has two meanings
    #: for "empty" (``repository`` mode's *derived* empty and an omission
    #: mode's *refused* empty), which is why it may not default. ``link_paths``
    #: has exactly one -- nothing was linked -- so a default cannot collapse
    #: two states here.
    #:
    #: **Independent of ``snapshot_selection``** (A-366): linking content IN
    #: is orthogonal to the unsafe-symlink omission policy, and a lane may
    #: declare this under either selection.
    link_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        where = "IsolationConfig"
        self._check_link_paths(where)
        if (
            not isinstance(self.snapshot_selection, str)
            or self.snapshot_selection not in SNAPSHOT_SELECTIONS
        ):
            raise LaneConfigError(
                f"{where}: 'snapshot_selection' must be one of "
                f"{sorted(SNAPSHOT_SELECTIONS)}, got {self.snapshot_selection!r}"
            )
        if self.snapshot_selection == "repository":
            if self.unsafe_symlink_omissions != ():
                raise LaneConfigError(
                    f"{where}: 'unsafe_symlink_omissions' is forbidden under "
                    f"snapshot_selection = 'repository'; declare "
                    f"'repository-minus-unsafe-symlinks' to name omissions"
                )
            return

        # snapshot_selection == "repository-minus-unsafe-symlinks"
        count = len(self.unsafe_symlink_omissions)
        if not (MIN_UNSAFE_SYMLINK_OMISSIONS <= count <= MAX_UNSAFE_SYMLINK_OMISSIONS):
            raise LaneConfigError(
                f"{where}: 'unsafe_symlink_omissions' must declare "
                f"{MIN_UNSAFE_SYMLINK_OMISSIONS}..{MAX_UNSAFE_SYMLINK_OMISSIONS} "
                f"entries under snapshot_selection = "
                f"'repository-minus-unsafe-symlinks', got {count}; empty omission "
                f"mode is refused -- use 'repository' instead"
            )
        encoded: list[bytes] = []
        for index, item in enumerate(self.unsafe_symlink_omissions):
            validated = _validate_omission_path(
                item, where=where, field=f"unsafe_symlink_omissions[{index}]"
            )
            encoded.append(validated.encode("utf-8"))
        for previous, current in zip(encoded, encoded[1:]):
            if not previous < current:
                raise LaneConfigError(
                    f"{where}: 'unsafe_symlink_omissions' must be strictly "
                    f"ascending by the UTF-8 bytes of the canonical spelling, "
                    f"got {list(self.unsafe_symlink_omissions)}; the loader does "
                    f"not silently sort a declared list"
                )

    def _check_link_paths(self, where: str) -> None:
        """(B041(b) rule 1, the SPELLING half) -- the grammar, the bound and
        the strict ascending order.

        EXISTENCE is deliberately not checked here, and that is the one place
        this key departs from `cwd` beside it. B041(b)'s own contract makes an
        absent link path ``NO_MEASUREMENT``/``MISSING_EXTERNAL_TOOL`` -- "a
        declared prerequisite the environment did not provide" -- not
        ``BAD_LANE_CONFIG``, because the whole point of the key is that the
        directory is built by the ENVIRONMENT (an image-baked ``npm ci``, a
        mounted cache) rather than committed. The same lane file is correct on
        a machine that has run the install and incorrect on one that has not,
        so the lane file is not what is wrong, and refusing it at load would
        misname the fault. :mod:`assay.isolation` checks existence at
        materialisation, where the environment is actually in view.
        """
        if not isinstance(self.link_paths, tuple):
            raise LaneConfigError(
                f"{where}: 'link_paths' must be a tuple, got {self.link_paths!r}"
            )
        if len(self.link_paths) > MAX_LINK_PATHS:
            raise LaneConfigError(
                f"{where}: 'link_paths' declares {len(self.link_paths)} "
                f"entries, exceeding the ceiling of {MAX_LINK_PATHS}"
            )
        encoded: list[bytes] = []
        for index, item in enumerate(self.link_paths):
            validated = _validate_omission_path(
                item, where=where, field=f"link_paths[{index}]"
            )
            encoded.append(validated.encode("utf-8"))
        for previous, current in zip(encoded, encoded[1:]):
            if not previous < current:
                raise LaneConfigError(
                    f"{where}: 'link_paths' must be strictly ascending by the "
                    f"UTF-8 bytes of the canonical spelling, got "
                    f"{list(self.link_paths)}; the loader does not silently "
                    f"sort a declared list"
                )

    def as_declared(self) -> dict[str, Any]:
        declared: dict[str, Any] = {"snapshot_selection": self.snapshot_selection}
        if self.unsafe_symlink_omissions:
            declared["unsafe_symlink_omissions"] = list(self.unsafe_symlink_omissions)
        if self.link_paths:
            declared["link_paths"] = list(self.link_paths)
        return declared


@dataclass(frozen=True)
class Lane:
    """One declared lane. Every attribute came out of the file."""

    name: str
    scope: str
    rigor: tuple[str, ...]
    enforcement: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    env_passthrough: tuple[str, ...]
    #: the declared duration string, verbatim
    budget: str
    #: ...and its parsed value, which is ADDITIONAL to the declaration, not a
    #: replacement for it (A-052)
    budget_seconds: float
    allow_argv_append: bool
    judge: JudgeConfig | None
    #: §7: WHERE is data assay parses and never interprets.
    where: Mapping[str, Any] | None
    #: (B006a/A-269) The declared repository snapshot materialisation policy
    #: -- REQUIRED, never defaulted: `None` on an R0-only lane, and the real
    #: `IsolationConfig` object on any lane declaring R1, R2, or R3. Every
    #: direct `Lane(...)` constructor must say one or the other explicitly;
    #: there is no third, inferred value. Placed immediately before
    #: `env_required` (the field order §3.2 specifies), which stays the ONLY
    #: defaulted field.
    isolation: IsolationConfig | None
    #: (A-254) The subset of `env_passthrough` whose ABSENCE refuses the lane
    #: before its command runs. Defaults to empty, so every lane written before
    #: this field existed is unchanged -- and it is LAST in the field order
    #: because it is the only defaulted field on a positional dataclass.
    env_required: tuple[str, ...] = ()
    #: (B010) Optional gate-environment probe. ``None`` means the lane is
    #: meaningful wherever it is invoked; a declared argv is executed in the
    #: invoking context before the lane command and must exit zero.
    environment_command: tuple[str, ...] | None = None
    #: (B013) Declared infrastructure facts, resolved in the invoking context
    #: before snapshot execution and injected into the isolated command.
    infrastructure: Mapping[str, str] | None = None
    #: (B043, schema v9) The lane command's declared working directory,
    #: repository-top-relative, forward-slash. ``None`` -- the only value that
    #: existed before v9 -- means every command of this lane runs at the
    #: snapshot's project root, and the verdict then records NO
    #: ``cwd_declared`` at all (absent, never ``"."``).
    #:
    #: **Nothing else re-roots** (A-271, one path grammar):
    #: ``judge.coverage.artifact``, ``judge.mutation.equivalence_artifact``/
    #: ``kill_signal_artifact``/``artifact``, ``source_roots``, ``targets``
    #: and every ``infrastructure`` fact stay project-root-relative whatever
    #: this says. ``environment_command`` is likewise unaffected: it is a
    #: probe of the INVOKING environment (B010/DESIGN-GUIDE §4), not the lane
    #: command, and keeps running in the invoking cwd.
    cwd: str | None = None

    def as_declared(self) -> dict[str, Any]:
        """Reconstruct the TOML table this lane was loaded from.

        This is the mechanical form of *"no key present that the file did not
        declare"*: the result compares equal to ``tomllib``'s own parse of the
        lane table, so an invented default or a dropped field shows up as an
        inequality rather than as something a reviewer has to notice.
        """
        declared: dict[str, Any] = {
            "scope": self.scope,
            "rigor": list(self.rigor),
            "enforcement": self.enforcement,
            "argv": list(self.argv),
            "env": dict(self.env),
            "env_passthrough": list(self.env_passthrough),
            "budget": self.budget,
            "allow_argv_append": self.allow_argv_append,
        }
        if self.env_required:
            declared["env_required"] = list(self.env_required)
        if self.environment_command is not None:
            declared["environment_command"] = list(self.environment_command)
        if self.infrastructure is not None:
            declared["infrastructure"] = dict(self.infrastructure)
        if self.cwd is not None:
            declared["cwd"] = self.cwd
        if self.judge is not None:
            declared["judge"] = self.judge.as_declared()
        if self.where is not None:
            declared["where"] = dict(self.where)
        if self.isolation is not None:
            declared["isolation"] = self.isolation.as_declared()
        return declared


@dataclass(frozen=True)
class LaneFile:
    """A parsed ``assay.toml``."""

    #: the file itself, resolved
    path: Path
    #: the directory containing it — the PROJECT root, which is what
    #: `source_roots` resolve against (A-049), and not the repo root
    project_root: Path
    schema_version: int
    lanes: Mapping[str, Lane]

    def lane(self, name: str) -> Lane:
        try:
            return self.lanes[name]
        except KeyError:
            known = ", ".join(sorted(self.lanes)) or "(none)"
            raise LaneConfigError(
                f"{self.path}: no lane named {name!r}; declared lanes: {known}"
            ) from None


def find_lane_file(start: Path | None = None) -> Path:
    """Search upward from *start* for ``assay.toml``.

    DERIVE, then FAIL (§4.2a). The project root is *discovered* from the file's
    own location rather than guessed, and when there is no file the caller is
    told exactly where the search ran instead of getting a default path.
    """
    origin = (Path.cwd() if start is None else Path(start)).resolve()
    for directory in (origin, *origin.parents):
        candidate = directory / LANE_FILE_NAME
        if candidate.is_file():
            return candidate
    raise LaneConfigError(
        f"no {LANE_FILE_NAME} found in {origin} or any parent directory; "
        f"pass --file to name one explicitly"
    )


def load_lane_file(path: Path) -> LaneFile:
    """Load and fully validate an ``assay.toml``.

    Raises :class:`LaneConfigError` (``ERROR`` / ``BAD_LANE_CONFIG``) on any
    defect, always naming the file, the lane and the field.
    """
    file_path = Path(path).resolve()
    try:
        raw_bytes = file_path.read_bytes()
    except OSError as exc:
        raise LaneConfigError(f"{file_path}: cannot be read: {exc}") from exc
    try:
        document = tomllib.loads(raw_bytes.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise LaneConfigError(f"{file_path}: is not valid TOML: {exc}") from exc

    project_root = file_path.parent
    schema_version = _load_schema_version(document, file_path)

    unknown = sorted(set(document) - {"schema_version", "lanes"})
    if unknown:
        raise LaneConfigError(
            f"{file_path}: unknown top-level key(s): {', '.join(unknown)}; "
            f"expected only: schema_version, lanes"
        )

    if "lanes" not in document:
        raise LaneConfigError(
            f"{file_path}: declares no [lanes] table; a lane file with no lanes "
            f"declares nothing to judge"
        )
    lanes_table = document["lanes"]
    if not isinstance(lanes_table, dict):
        raise LaneConfigError(
            f"{file_path}: 'lanes' must be a table of lanes, got "
            f"{_type_name(lanes_table)}"
        )
    if not lanes_table:
        raise LaneConfigError(
            f"{file_path}: declares no lanes; a lane file with no lanes declares "
            f"nothing to judge"
        )

    lanes = {
        name: _load_lane(name, table, file_path, project_root)
        for name, table in lanes_table.items()
    }
    return LaneFile(
        path=file_path,
        project_root=project_root,
        schema_version=schema_version,
        lanes=MappingProxyType(lanes),
    )


# --- internals ---------------------------------------------------------------


def _type_name(value: Any) -> str:
    return type(value).__name__


def _load_schema_version(document: Mapping[str, Any], file_path: Path) -> int:
    if "schema_version" not in document:
        raise LaneConfigError(
            f"{file_path}: missing required field 'schema_version' "
            f"(this assay understands schema_version = {LANE_SCHEMA_VERSION})"
        )
    value = document["schema_version"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise LaneConfigError(
            f"{file_path}: 'schema_version' must be an integer, got {_type_name(value)}"
        )
    if value != LANE_SCHEMA_VERSION:
        raise LaneConfigError(
            f"{file_path}: declares schema_version = {value}; this assay "
            f"understands schema_version = {LANE_SCHEMA_VERSION}"
        )
    return value


def _load_lane(
    name: str, table: Any, file_path: Path, project_root: Path
) -> Lane:
    where = f"{file_path}: lane {name!r}"
    if not isinstance(table, dict):
        raise LaneConfigError(f"{where}: must be a table, got {_type_name(table)}")

    for field in REQUIRED_LANE_FIELDS:
        if field not in table:
            raise LaneConfigError(f"{where}: missing required field {field!r}")

    unknown = sorted(
        set(table) - set(REQUIRED_LANE_FIELDS) - set(_OPTIONAL_LANE_FIELDS)
    )
    if unknown:
        raise LaneConfigError(
            f"{where}: unknown key(s): {', '.join(unknown)}; expected only: "
            f"{', '.join((*REQUIRED_LANE_FIELDS, *_OPTIONAL_LANE_FIELDS))}"
        )

    scope = _as_str(table["scope"], where, "scope")
    if scope not in SCOPES:
        raise LaneConfigError(
            f"{where}: 'scope' must be one of {sorted(SCOPES)}, got {scope!r}"
        )

    rigor = _as_str_list(table["rigor"], where, "rigor")
    if not rigor:
        raise LaneConfigError(
            f"{where}: 'rigor' declares no level; a lane that declares no rigor "
            f"level can render no claim"
        )
    for level in rigor:
        if level not in RIGOR_LEVELS:
            raise LaneConfigError(
                f"{where}: 'rigor' must contain only {list(RIGOR_LEVELS)}, got "
                f"{level!r}"
            )
    if len(set(rigor)) != len(rigor):
        raise LaneConfigError(
            f"{where}: 'rigor' contains a duplicate level: {list(rigor)}; assay "
            f"records one claim per declared level"
        )

    canonical = tuple(level for level in RIGOR_LEVELS if level in rigor)
    if tuple(rigor) != canonical or rigor[0] != "R0":
        raise LaneConfigError(
            f"{where}: 'rigor' must be an R0-led ordered subsequence of "
            f"{list(RIGOR_LEVELS)}, got {list(rigor)}"
        )

    enforcement = _as_str(table["enforcement"], where, "enforcement")
    if enforcement not in ENFORCEMENTS:
        raise LaneConfigError(
            f"{where}: 'enforcement' must be one of {sorted(ENFORCEMENTS)}, got "
            f"{enforcement!r}"
        )

    argv = _as_str_list(table["argv"], where, "argv")
    if not argv:
        raise LaneConfigError(
            f"{where}: 'argv' is empty; a lane must declare the command it runs"
        )

    env = _as_str_table(table["env"], where, "env")
    env_passthrough = _as_str_list(
        table["env_passthrough"], where, "env_passthrough"
    )
    # P15 (A-067 finding 9): a name declared in BOTH tables let the ambient
    # process environment silently override a value the lane declared as
    # FIXED (assay.runner.resolve_command_plan builds env_declared then
    # overwrites it with whatever env_passthrough finds present) -- a "fixed"
    # value that changes with whoever runs the lane is not fixed. Refusing
    # the collision here, at load time, makes that overwrite structurally
    # unreachable without touching runner.py at all: it can never see a lane
    # whose two tables disagree about a name's ownership.
    env_required = _as_str_list(
        table.get("env_required", []), where, "env_required"
    )
    environment_command = None
    if "environment_command" in table:
        environment_command = _as_str_list(
            table["environment_command"], where, "environment_command"
        )
        if not environment_command:
            raise LaneConfigError(
                f"{where}: 'environment_command' is empty; omit the field when "
                f"the lane is meaningful in any invoking environment"
            )
    infrastructure = None
    if "infrastructure" in table:
        raw_infrastructure = table["infrastructure"]
        if not isinstance(raw_infrastructure, dict):
            raise LaneConfigError(
                f"{where}: 'infrastructure' must be a table, got "
                f"{_type_name(raw_infrastructure)}"
            )
        if not raw_infrastructure:
            raise LaneConfigError(
                f"{where}: 'infrastructure' declares no facts; omit the table"
            )
        if len(raw_infrastructure) > MAX_INFRASTRUCTURE_FACTS:
            raise LaneConfigError(
                f"{where}: 'infrastructure' declares {len(raw_infrastructure)} "
                f"facts; the bound is {MAX_INFRASTRUCTURE_FACTS}"
            )
        parsed_facts: dict[str, str] = {}
        for key, declaration in raw_infrastructure.items():
            if not isinstance(key, str) or not key:
                raise LaneConfigError(
                    f"{where}: infrastructure names must be non-empty strings"
                )
            if not isinstance(declaration, str) or not declaration:
                raise LaneConfigError(
                    f"{where}: 'infrastructure.{key}' must be a non-empty string, "
                    f"got {_type_name(declaration)}"
                )
            source, separator, expression = declaration.partition(":")
            if not separator or source not in INFRASTRUCTURE_SOURCES or not expression:
                raise LaneConfigError(
                    f"{where}: 'infrastructure.{key}' must use "
                    f"{sorted(INFRASTRUCTURE_SOURCES)} with a non-empty value, got "
                    f"{declaration!r}"
                )
            if any(character.isspace() for character in expression):
                raise LaneConfigError(
                    f"{where}: 'infrastructure.{key}' expression contains whitespace"
                )
            if source == "derived" and any(part == "" for part in expression.split(".")):
                raise LaneConfigError(
                    f"{where}: 'infrastructure.{key}' derived path is not dotted: "
                    f"{expression!r}"
                )
            parsed_facts[key] = declaration
        infrastructure = MappingProxyType(dict(parsed_facts))
    # (A-254) `env_required` must be a SUBSET of `env_passthrough`, because a
    # name outside it is unreachable by construction -- `resolve_command_plan`
    # only ever copies declared passthrough names, so requiring a name the lane
    # never asked for would refuse every run for a reason no environment could
    # satisfy. Caught at load, where a typo is cheap, rather than at run time.
    unreachable = sorted(set(env_required) - set(env_passthrough))
    if unreachable:
        raise LaneConfigError(
            f"{where}: 'env_required' names {unreachable} which "
            f"'env_passthrough' does not declare. A required name that is not "
            f"passed through can never be satisfied -- add it to "
            f"'env_passthrough' or drop it from 'env_required'."
        )

    collisions = sorted(set(env) & set(env_passthrough))
    if collisions:
        raise LaneConfigError(
            f"{where}: {', '.join(collisions)} declared in both 'env' (a "
            f"fixed value) and 'env_passthrough' (an ambient name) -- pick "
            f"exactly one. A name in both means the ambient process "
            f"environment silently overrides the fixed value, which makes "
            f"'fixed' a lie."
        )
    if infrastructure is not None:
        collisions = sorted(set(infrastructure) & set(env))
        if collisions:
            raise LaneConfigError(
                f"{where}: {', '.join(collisions)} declared in both "
                f"'infrastructure' and fixed 'env'; an injected fact must own its "
                f"name exclusively"
            )
        collisions = sorted(set(infrastructure) & set(env_passthrough))
        if collisions:
            raise LaneConfigError(
                f"{where}: {', '.join(collisions)} declared in both "
                f"'infrastructure' and 'env_passthrough'; an injected fact must "
                f"own its name exclusively"
            )
    if "/" not in argv[0] and "PATH" not in env and "PATH" not in env_passthrough:
        raise LaneConfigError(
            f"{where}: argv[0] {argv[0]!r} is a bare executable name but PATH "
            f"is declared by neither 'env' nor 'env_passthrough'. Without an "
            f"explicit PATH, process launch may search an implementation default "
            f"that the lane never declared. Declare PATH or use an executable path."
        )

    budget = _as_str(table["budget"], where, "budget")
    try:
        budget_seconds = parse_duration(budget)
    except ValueError as exc:
        raise LaneConfigError(f"{where}: 'budget' {exc}") from exc

    allow_argv_append = _as_bool(
        table["allow_argv_append"], where, "allow_argv_append"
    )

    judge = _load_judge(table.get("judge"), rigor, where, project_root)

    if (
        judge is not None
        and judge.canary is not None
        and judge.canary.mechanism == "uncovered-line"
        and "R1" not in rigor
    ):
        raise LaneConfigError(f"{where}: uncovered-line R3 requires declared R1")

    # wave-1 §5: `uncovered-line` proves "a changed-line coverage floor
    # rejects an uncovered line" -- a premise whole-target mode replaces.
    # Outside the declared targets it would prove nothing about the floor
    # actually enforced and would produce an accidental CANARY_SURVIVED
    # that looks like a real finding, so it is refused at load unless the
    # canary target is itself one of `targets`.
    if (
        judge is not None
        and judge.canary is not None
        and judge.canary.mechanism == "uncovered-line"
        and judge.mode == "whole_target"
        and judge.canary.target not in (judge.targets or ())
    ):
        raise LaneConfigError(
            f"{where}: judge.canary.target {judge.canary.target!r} is not "
            f"one of judge.targets {list(judge.targets or ())} under "
            f"mode = 'whole_target'; outside the declared targets the "
            f"uncovered-line canary proves nothing about the whole-target "
            f"floor"
        )

    where_table = table.get("where")
    if where_table is not None and not isinstance(where_table, dict):
        raise LaneConfigError(
            f"{where}: 'where' must be a table, got {_type_name(where_table)}"
        )

    isolation = _load_isolation_for_lane(table.get("isolation"), rigor, where)

    cwd = _load_lane_cwd(table.get("cwd"), where, project_root)
    # (B043 x B041(b), fix round 1) The two keys are individually valid and
    # were individually checked; this is the check on the PAIR, and it needs
    # neither a resolved commit nor snapshot machinery -- both facts are on
    # the same `Lane` the moment they are read.
    _check_cwd_is_not_under_a_link_path(cwd, isolation, where)

    return Lane(
        name=name,
        scope=scope,
        rigor=tuple(rigor),
        enforcement=enforcement,
        argv=tuple(argv),
        environment_command=None if environment_command is None else tuple(environment_command),
        env=MappingProxyType(dict(env)),
        env_passthrough=tuple(env_passthrough),
        env_required=tuple(env_required),
        infrastructure=infrastructure,
        budget=budget,
        budget_seconds=budget_seconds,
        allow_argv_append=allow_argv_append,
        judge=judge,
        where=None if where_table is None else MappingProxyType(dict(where_table)),
        isolation=isolation,
        cwd=cwd,
    )


def _load_lane_cwd(value: Any, where: str, project_root: Path) -> str | None:
    """``cwd`` (B043) -- the lane command's declared working directory, or
    ``None`` when the file omitted the key.

    Three checks, each refusing a genuinely different thing:

    1. the SPELLING -- :func:`_validate_omission_path`'s own grammar, shared
       verbatim with ``unsafe_symlink_omissions`` rather than re-derived
       (repo-relative, forward-slash, no empty/``.``/``..``/``.git``
       component, no backslash or NUL, never absolute). ``..`` is what makes
       this more than cosmetic: a lane whose cwd escapes the project would
       run its command outside the snapshot the verdict names;
    2. CONTAINMENT after symlink collapse -- exactly
       :func:`_validate_artifact_path`'s check one field over. The grammar
       above cannot see a symlink, so the two are complementary, not
       redundant;
    3. that it names a real DIRECTORY in the INVOKING checkout. This is the
       load-time half of the contract's "must resolve to a tracked directory
       at the resolved commit": at load there is no resolved commit yet
       (base resolution happens per-run, well after ``assay.toml`` is read),
       so the commit-bound half is checked by :mod:`assay.runner` against the
       materialised snapshot, where the commit IS known and can be named in
       the message. A typo is caught here, at the earliest moment the fact
       exists; a path that is present-but-untracked is caught there, at the
       earliest moment ITS fact exists. Neither check can stand in for the
       other, and a lane file that passes this one and fails that one is a
       real, reportable state (an ignored build directory, say).
    """
    if value is None:
        return None
    cwd = _validate_omission_path(value, where=where, field="cwd")
    resolved = (project_root / cwd).resolve()
    if not resolved.is_relative_to(project_root.resolve()):
        raise LaneConfigError(
            f"{where}: 'cwd' {cwd!r} resolves to {resolved}, which is not "
            f"contained beneath the project root {project_root} (via a "
            f"symlink) -- a lane must not be able to run its command outside "
            f"the project it declares"
        )
    if not resolved.is_dir():
        raise LaneConfigError(
            f"{where}: 'cwd' {cwd!r} is not a directory in the checkout at "
            f"{project_root}; the lane command's working directory must "
            f"exist, and (because every command runs inside a snapshot built "
            f"from committed objects) must be tracked at the commit the run "
            f"resolves -- which assay.runner re-checks against the "
            f"materialised snapshot, where the commit is known"
        )
    return cwd


def _check_cwd_is_not_under_a_link_path(
    cwd: str | None, isolation: IsolationConfig | None, where: str
) -> None:
    """(B043 x B041(b), fix round 1) Refuse a lane whose ``cwd`` IS, or lies
    beneath, one of its own ``isolation.link_paths`` entries.

    **This combination is never satisfiable, so refusing it is exact rather
    than merely cautious.** A ``link_paths`` entry must be UNTRACKED at the
    resolved commit -- :meth:`assay.isolation.SnapshotRepository._plant_link_paths`
    rule 2 refuses to link a tracked path, because doing so would replace
    committed content with whatever the invoking checkout holds. A ``cwd``
    must be a TRACKED DIRECTORY at that same commit. A tracked directory's
    ancestors are themselves tracked directories, so no lane can satisfy both
    rules about the same path: whichever way the run goes, one of the two
    keys is wrong, and it is better to say so while a human is editing the
    file than to discover it per-run.

    **What it actually prevented.** Shipped, the two keys COMPOSED into a
    snapshot escape. ``_plant_link_paths`` plants its symlinks after
    ``_verify`` (A-370), and the runner's commit-bound ``cwd`` check was a
    filesystem test -- which followed the freshly planted link back into the
    invoking checkout and accepted an untracked directory as committed. The
    lane's command then ran in the consumer's REAL working tree and wrote
    there for real. :func:`assay.runner._execute_snapshot_unit` now decides
    that question from the commit's own manifest and is the authoritative,
    commit-bound refusal; this one is the cheap, early, better-diagnosed
    half, in the layer that can name the file being edited.

    The comparison is between the two DECLARED SPELLINGS. ``link_paths`` is
    repo-top-relative and ``cwd`` is project-relative (A-145), which coincide
    for the ordinary single-project repository and can differ under a
    ``project_prefix``. That makes this check deliberately approximate in
    exactly one direction -- it can refuse a same-named pair that a prefix
    would have kept apart -- and never the other: the runner's manifest check
    is prefix-exact and catches everything this one lets past. An approximate
    refusal that names both keys is the right trade for a defense-in-depth
    layer; it would be the wrong trade for the only layer.
    """
    if cwd is None or isolation is None or not isolation.link_paths:
        return
    linked = set(isolation.link_paths)
    parts = cwd.split("/")
    for index in range(1, len(parts) + 1):
        ancestor = "/".join(parts[:index])
        if ancestor in linked:
            raise LaneConfigError(
                f"{where}: 'cwd' {cwd!r} is at or beneath "
                f"'isolation.link_paths' entry {ancestor!r}. A linked path is "
                f"by rule NOT tracked at the resolved commit, and a lane's "
                f"working directory by rule IS -- so no commit can satisfy "
                f"both, and a run that appeared to would be running the "
                f"command through the link, in the invoking checkout rather "
                f"than in the snapshot. Point 'cwd' at a committed directory, "
                f"or stop linking the one it names"
            )


def _load_isolation_for_lane(
    value: Any, rigor: Iterable[str], where: str
) -> IsolationConfig | None:
    """The R0/R1+ conditional (§3.2): `[isolation]` is required the moment
    `rigor` declares R1, R2, or R3, and forbidden on an R0-only lane. There
    is no default and no inference from the file's own location -- an R1+
    lane that omits the table is exactly as wrong as an R0-only lane that
    declares one.
    """
    higher_rigor = any(level != "R0" for level in rigor)
    if value is None:
        if higher_rigor:
            raise LaneConfigError(
                f"{where}: declares rigor {list(rigor)} but has no [isolation] "
                f"table; R1/R2/R3 requires an explicit isolation.snapshot_selection"
            )
        return None
    if not higher_rigor:
        raise LaneConfigError(
            f"{where}: declares an [isolation] table but rigor {list(rigor)} is "
            f"R0-only; isolation selects a snapshot policy for a higher-rigor "
            f"unit, which an R0-only lane never runs"
        )
    return _load_isolation(value, where)


def _load_isolation(value: Any, where: str) -> IsolationConfig:
    if not isinstance(value, dict):
        raise LaneConfigError(
            f"{where}: 'isolation' must be a table, got {_type_name(value)}"
        )
    unknown = sorted(set(value) - set(_ISOLATION_FIELDS))
    if unknown:
        raise LaneConfigError(
            f"{where}: unknown isolation key(s): {', '.join(unknown)}; expected "
            f"only: {', '.join(_ISOLATION_FIELDS)}"
        )
    if "snapshot_selection" not in value:
        raise LaneConfigError(
            f"{where}: missing required field 'isolation.snapshot_selection'"
        )
    selection = _as_str(value["snapshot_selection"], where, "isolation.snapshot_selection")
    if selection not in SNAPSHOT_SELECTIONS:
        raise LaneConfigError(
            f"{where}: 'isolation.snapshot_selection' must be one of "
            f"{sorted(SNAPSHOT_SELECTIONS)}, got {selection!r}"
        )
    # (B041(b)/A-366) Read BEFORE the selection fork and passed to both
    # branches: `link_paths` is independent of `snapshot_selection`, and
    # reading it inside one branch is how a key silently becomes legal under
    # only one selection.
    link_paths: tuple[str, ...] = ()
    if "link_paths" in value:
        link_paths = tuple(
            _as_str_list(value["link_paths"], where, "isolation.link_paths")
        )
        if not link_paths:
            raise LaneConfigError(
                f"{where}: 'isolation.link_paths' is declared but empty; omit "
                f"the key instead -- an empty list and an absent one would "
                f"otherwise be two spellings of the same fact, and the verdict "
                f"records this by ABSENCE (A-051)"
            )
    has_omissions = "unsafe_symlink_omissions" in value
    if selection == "repository":
        if has_omissions:
            raise LaneConfigError(
                f"{where}: 'isolation.unsafe_symlink_omissions' is forbidden "
                f"under snapshot_selection = 'repository'"
            )
        return IsolationConfig(
            snapshot_selection=selection,
            unsafe_symlink_omissions=(),
            link_paths=link_paths,
        )
    if not has_omissions:
        raise LaneConfigError(
            f"{where}: missing required field "
            f"'isolation.unsafe_symlink_omissions' (required under "
            f"snapshot_selection = 'repository-minus-unsafe-symlinks')"
        )
    omissions = _as_str_list(
        value["unsafe_symlink_omissions"], where, "isolation.unsafe_symlink_omissions"
    )
    return IsolationConfig(
        snapshot_selection=selection,
        unsafe_symlink_omissions=tuple(omissions),
        link_paths=link_paths,
    )


def _load_targets(value: Any, where: str) -> tuple[str, ...]:
    """``judge.targets`` (wave-1 §5) -- required and non-empty iff
    ``mode = "whole_target"``. Project-relative file paths, the same
    spelling ``judge.coverage.artifact``/``judge.canary.target`` use
    (A-145). Load-time refusal for: non-list, non-string, empty, absolute,
    any ``.``/``..``/empty path component, backslash, or duplicate.

    Existence is deliberately NOT checked here (unlike
    :func:`_load_canary`'s target): a whole-target lane must be judgeable
    from ANY commit, including a post-merge ``main`` (A-260), and a
    target's existence is a fact of the commit being judged, not of the
    declaration. Filesystem kind/containment is
    :func:`assay.evaluate._resolve_whole_target`'s job, at judge time,
    against the real snapshot.

    The component-by-component check below is EXHAUSTIVE for canonical
    POSIX spelling, not merely a first pass: every way
    :class:`~pathlib.PurePosixPath` normalises a string during its own
    ``as_posix()`` round-trip -- collapsing a doubled or trailing slash,
    dropping a leading/embedded ``.`` component -- reduces to producing an
    empty or ``"."`` component somewhere in ``raw.split("/")``, which the
    loop below already refuses; a ``PurePosixPath(raw).as_posix() != raw``
    check run AFTER this loop can therefore never fire on any input that
    reaches it (proven by exhaustive search over every string of length <=
    6 built from ``{"a", "/", "."}—the alphabet spanning every
    normalisation ``PurePosixPath`` performs), and a check that can never
    be false is not a check: it would be unreachable code and an
    unreachable branch, not defence in depth. It is intentionally NOT
    duplicated here.
    """
    declared = _as_str_list(value, where, "judge.targets")
    if not declared:
        raise LaneConfigError(
            f"{where}: 'judge.targets' is empty; whole-target mode requires "
            f"at least one target"
        )
    seen: set[str] = set()
    validated: list[str] = []
    for index, raw in enumerate(declared):
        field = f"judge.targets[{index}]"
        if not raw:
            raise LaneConfigError(f"{where}: {field} must not be empty")
        if raw.startswith("/"):
            raise LaneConfigError(f"{where}: {field} {raw!r} must not be absolute")
        if "\\" in raw:
            raise LaneConfigError(
                f"{where}: {field} {raw!r} contains a backslash; declare it "
                f"with forward slashes regardless of platform"
            )
        for component in raw.split("/"):
            if component in ("", ".", ".."):
                raise LaneConfigError(
                    f"{where}: {field} {raw!r} has an invalid path component "
                    f"{component!r}; a target must not contain an empty, "
                    f"'.', or '..' component"
                )
        if raw in seen:
            raise LaneConfigError(
                f"{where}: 'judge.targets' declares {raw!r} more than once"
            )
        seen.add(raw)
        validated.append(raw)
    return tuple(validated)


def _required_judge_fields(rigor: Iterable[str]) -> tuple[str, ...]:
    """The union of what every DECLARED rigor level requires (A-017/A-048)."""
    required: list[str] = []
    for level in rigor:
        for field in JUDGE_FIELDS_BY_RIGOR[level]:
            if field not in required:
                required.append(field)
    return tuple(required)


def _load_judge(
    table: Any, rigor: Iterable[str], where: str, project_root: Path
) -> JudgeConfig | None:
    rigor = tuple(rigor)
    required = list(_required_judge_fields(rigor))

    if table is None:
        if required:
            raise LaneConfigError(
                f"{where}: declares rigor {list(rigor)} but has no [judge] table; "
                f"that rigor requires judge.{{{', '.join(required)}}}"
            )
        # A-048: an R0-only lane has NO [judge] table, and that is correct.
        return None

    if not isinstance(table, dict):
        raise LaneConfigError(
            f"{where}: 'judge' must be a table, got {_type_name(table)}"
        )

    unknown = sorted(set(table) - set(_KNOWN_JUDGE_FIELDS) - {"attestation_dir", "evidence"})
    if unknown:
        raise LaneConfigError(
            f"{where}: unknown judge key(s): {', '.join(unknown)}; expected only: "
            f"{', '.join((*_KNOWN_JUDGE_FIELDS, 'attestation_dir', 'evidence'))}"
        )

    # wave-1 §5, A-260: `mode`/`targets` and §4/A-259's `require_branch` are
    # R1-specific and OPTIONAL -- legal only on a lane declaring R1, and
    # deliberately not folded into the generic per-rigor `required` set the
    # way `fail_under`/`allow_excluded` are (this loader has never had a
    # genuinely optional judge field before evidence/attestation_dir, and
    # those are a separate axis entirely). `targets` is the one exception:
    # it becomes REQUIRED, but only once `mode` resolves to `"whole_target"`,
    # which is why it is folded into `required` below rather than treated
    # like its two siblings.
    r1_declared = "R1" in rigor
    r2_declared = "R2" in rigor
    mode_declared = "mode" in table
    if mode_declared and not (r1_declared or r2_declared):
        raise LaneConfigError(
            f"{where}: declares 'judge.mode' but rigor {list(rigor)} includes "
            f"neither R1 nor R2; mode selects the judging SCOPE both tiers "
            f"read (A-325), so on a lane with neither it reads nothing"
        )
    mode: str | None = None
    if mode_declared:
        mode = _as_str(table["mode"], where, "judge.mode")
        if mode not in JUDGE_MODES:
            raise LaneConfigError(
                f"{where}: 'judge.mode' must be one of {sorted(JUDGE_MODES)}, "
                f"got {mode!r}"
            )
    # A-260: absent means "changed_lines" -- the only mode that existed
    # before this wave. `mode` itself (the JudgeConfig field) stays the
    # DECLARED value or None; only this local variable resolves the default,
    # and only to decide `required`/the canary interaction below.
    effective_mode = mode if mode is not None else "changed_lines"

    require_branch_declared = "require_branch" in table
    if require_branch_declared and not r1_declared:
        raise LaneConfigError(
            f"{where}: declares 'judge.require_branch' but rigor "
            f"{list(rigor)} does not include R1"
        )

    # B019/A-328. `base_source` is optional and, like `require_branch`, is
    # not folded into any `JUDGE_FIELDS_BY_RIGOR` tuple -- it is a policy ABOUT
    # `base`, so it is legal exactly where `base` is: on a changed-line lane
    # declaring R1 and/or R2. Every other placement is inert config and is
    # refused by name here rather than by the generic surplus message below,
    # which would say the rigor "reads none of judge.{base_source}" and send
    # the operator looking for the wrong mistake.
    base_source = None
    if "base_source" in table:
        base_source = _as_str(table["base_source"], where, "judge.base_source")
        if base_source not in JUDGE_BASE_SOURCES:
            raise LaneConfigError(
                f"{where}: 'judge.base_source' must be one of "
                f"{sorted(JUDGE_BASE_SOURCES)}, got {base_source!r}"
            )
        if not (r1_declared or r2_declared):
            raise LaneConfigError(
                f"{where}: declares 'judge.base_source' but rigor "
                f"{list(rigor)} includes neither R1 nor R2 -- no tier here "
                f"reads a comparison commit, so naming who supplies one is "
                f"inert config"
            )
        if effective_mode == "whole_target":
            raise LaneConfigError(
                f"{where}: declares 'judge.base_source' under judge.mode = "
                f"'whole_target' -- whole-target scope replaces the diff at "
                f"every tier, so no tier reads a comparison commit and "
                f"delegating one to the gate request declares nothing "
                f"(A-325's own rule for 'judge.base', which this key is a "
                f"policy about)"
            )
        if base_source == "request" and "base" in table:
            raise LaneConfigError(
                f"{where}: declares BOTH 'judge.base' and judge.base_source = "
                f"'request'. Exactly one comparison base can be read, so "
                f"whichever loses is inert config that cannot fail loudly if "
                f"it is wrong (A-062). Delete 'judge.base' to let the invoking "
                f"gate request supply it with --request-base, or delete "
                f"'judge.base_source' to keep the lane's own declared base."
            )

    targets_declared = "targets" in table
    if targets_declared and effective_mode != "whole_target":
        raise LaneConfigError(
            f"{where}: declares 'judge.targets' but judge.mode is not "
            f"'whole_target' -- a target list under changed-line mode does "
            f"nothing and silently declaring one is how a consumer comes to "
            f"believe a floor is enforced when it is not"
        )

    if effective_mode == "whole_target":
        # A-260/§5 as corrected by A-325: `base` resolves nothing for a
        # whole-target lane at EITHER tier, so it moves OUT of `required`
        # here -- the generic surplus check below then refuses it as inert
        # config if the lane still declares it (A6 addendum, extending
        # A-062's own argument). `targets` moves IN: required and non-empty
        # precisely in this mode.
        #
        # The two SQL carve-outs this branch used to carry are GONE (B033/
        # A-325). The earlier `"R2" not in rigor and declared_language !=
        # "sql"` guard forced a SQL R1-only whole-target lane to declare an
        # inert `base` (the inverse of docs/CONSUMERS.md's own rule) while
        # never firing on an R2 lane at all, and it left `judge.base`
        # REQUIRED on a whole-target R2 lane that reads it nowhere:
        # `whole_file_r2` skips both `check_base_is_head` and the `git diff`,
        # and a whole-target R1 never resolves a base either (evaluate_r1's
        # own docstring). Whole-target scope and a comparison commit are
        # mutually exclusive by construction, in every language.
        if "base" in required:
            required.remove("base")
        # `targets` is never a member of any `JUDGE_FIELDS_BY_RIGOR` tuple
        # (only this mode-specific branch ever requires it), so it can
        # never already be in `required` here -- appended unconditionally
        # rather than guarded by a membership check that could never be
        # false, which would be an unreachable branch, not a real guard.
        required.append("targets")
    elif base_source == "request":
        # B019/A-328: the lane still REQUIRES changed-line judging; what it
        # has delegated is only the identity of the commit. `base` therefore
        # leaves `required` here -- the same move `whole_target` makes above,
        # for the mirror-image reason -- and the explicit both-declared
        # refusal above (not the generic surplus message) is what catches a
        # lane that delegates and hardcodes at once.
        if "base" in required:
            required.remove("base")
    required = tuple(required)

    for field in required:
        if field not in table:
            hint = " (judge.mode = 'whole_target')" if field == "targets" else ""
            raise LaneConfigError(
                f"{where}: declares rigor {list(rigor)} but is missing required "
                f"field 'judge.{field}'{hint}"
            )

    # P26/A-209: Tier-3 evidence's HOW pair is a separate axis from computed
    # rigor -- both present or both absent, on ANY rigor sequence including
    # R0-only, and never itself counted toward `required`/`surplus` below.
    has_attestation_dir = "attestation_dir" in table
    has_evidence = "evidence" in table
    if has_attestation_dir != has_evidence:
        raise LaneConfigError(
            f"{where}: 'judge.attestation_dir' and 'judge.evidence' must "
            f"both be present or both be absent"
        )

    # A-062 (controller ruling, overriding this package's first reading).
    # Judge config for a rigor level the lane does NOT declare is refused.
    #
    # The argument for allowing it was that the rigor list is the claim, so
    # surplus config claims nothing. That is true of the DATA and false of the
    # READER: `fail_under = 100` on an R0 lane looks exactly like a coverage
    # floor and is not one -- nothing reads it, so if it is wrong nothing fails
    # (AGENTS.md 4.2a). This project's whole subject is a declaration implying
    # capability that does not exist; dstdns's five-lane table over two real
    # lanes is the named specimen, and this is the same shape one level down.
    #
    # The cost is the "write the config, enable the level later" workflow,
    # which is a habit rather than a requirement: declaring the level is one
    # line in the same edit. The remedy is always visible in the message.
    surplus = sorted(
        set(table)
        - set(required)
        # B033/A-325: `targets` is NOT exempt any more. It was added to this
        # set alongside the SQL carve-out above, and with that carve-out gone
        # the exemption is not merely unnecessary but wrong: `targets` is a
        # member of `required` in the one mode that reads it, so exempting it
        # could only ever hide a declaration nothing reads.
        # B019/A-328: `base_source` joins `mode`/`require_branch` here for
        # their reason exactly -- it is optional in every rigor that reads it,
        # so it can never be a member of `required`, and its own placement
        # rules are enforced by name above rather than by this message.
        - {"attestation_dir", "evidence", "mode", "require_branch", "base_source"}
    )
    if surplus:
        raise LaneConfigError(
            f"{where}: declares rigor {list(rigor)}, which reads none of "
            f"judge.{{{', '.join(surplus)}}} -- so that configuration is inert "
            f"and cannot fail loudly if it is wrong. Either declare the rigor "
            f"level that consumes it, or delete it."
        )

    language = None
    if "language" in table:
        language = _as_str(table["language"], where, "judge.language")
        if not language:
            raise LaneConfigError(f"{where}: 'judge.language' is empty")

    source_roots: tuple[str, ...] | None = None
    source_root_paths: tuple[Path, ...] | None = None
    if "source_roots" in table:
        declared = _as_str_list(table["source_roots"], where, "judge.source_roots")
        if not declared:
            raise LaneConfigError(
                f"{where}: 'judge.source_roots' is empty; a lane that measures "
                f"nothing gates nothing"
            )
        source_roots = tuple(declared)
        source_root_paths = tuple(
            _resolve_source_root(raw, where, project_root) for raw in declared
        )

    fail_under = None
    if "fail_under" in table:
        fail_under = _as_float(table["fail_under"], where, "judge.fail_under")
        if not 0.0 <= fail_under <= 100.0:
            raise LaneConfigError(
                f"{where}: 'judge.fail_under' must be a percentage between 0 and "
                f"100, got {fail_under}"
            )

    allow_excluded = None
    if "allow_excluded" in table:
        allow_excluded = _as_bool(
            table["allow_excluded"], where, "judge.allow_excluded"
        )

    coverage = None
    if "coverage" in table:
        coverage = _load_coverage(table["coverage"], where, language, project_root)

    mutation = None
    if "mutation" in table:
        mutation = _load_mutation(table["mutation"], where, language, project_root)
    canary = None
    if "canary" in table:
        canary = _load_canary(table["canary"], where, project_root, source_root_paths)

    base = None
    if "base" in table:
        base = _as_str(table["base"], where, "judge.base")
        if not base:
            raise LaneConfigError(f"{where}: 'judge.base' is empty")

    require_branch = None
    if require_branch_declared:
        require_branch = _as_bool(
            table["require_branch"], where, "judge.require_branch"
        )

    targets = None
    if targets_declared:
        targets = _load_targets(table["targets"], where)

    attestation_dir = None
    evidence = None
    if has_attestation_dir:
        attestation_dir = _validate_attestation_dir(table["attestation_dir"], where)
        evidence = _load_evidence(table["evidence"], where)

    return JudgeConfig(
        language=language,
        source_roots=source_roots,
        source_root_paths=source_root_paths,
        fail_under=fail_under,
        allow_excluded=allow_excluded,
        coverage=coverage,
        mutation=mutation,
        canary=canary,
        base=base,
        base_source=base_source,
        mode=mode,
        targets=targets,
        require_branch=require_branch,
        attestation_dir=attestation_dir,
        evidence=evidence,
    )


def _validate_attestation_dir(value: Any, where: str) -> str:
    """The closed ``judge.attestation_dir`` grammar (P26/A-210): canonical,
    nonempty, project-relative POSIX spelling, 1..4,096 UTF-8 bytes and at
    most 128 nonempty components; not absolute; no ``.``/``..``/repeated
    slash/trailing slash/NUL/control character (U+0000..U+001F, U+007F).
    Existence is not required at load time -- runtime descriptor traversal
    owns absence and symlink/type facts (:func:`assay.safeio.read_bounded_input`).
    """
    if not isinstance(value, str) or not value:
        raise LaneConfigError(
            f"{where}: 'judge.attestation_dir' must be a non-empty string, "
            f"got {_type_name(value)}"
        )
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise LaneConfigError(
            f"{where}: 'judge.attestation_dir' {value!r} cannot be encoded as "
            f"UTF-8: {exc}"
        ) from exc
    if not (1 <= len(encoded) <= MAX_ATTESTATION_DIR_BYTES):
        raise LaneConfigError(
            f"{where}: 'judge.attestation_dir' must be 1..{MAX_ATTESTATION_DIR_BYTES} "
            f"UTF-8 bytes, got {len(encoded)}"
        )
    if value.startswith("/"):
        raise LaneConfigError(
            f"{where}: 'judge.attestation_dir' {value!r} must not be absolute"
        )
    if any(ch in _ATTESTATION_DIR_CONTROL for ch in value):
        raise LaneConfigError(
            f"{where}: 'judge.attestation_dir' {value!r} contains a control character"
        )
    if PurePosixPath(value).as_posix() != value:
        raise LaneConfigError(
            f"{where}: 'judge.attestation_dir' {value!r} is not canonical POSIX "
            f"spelling (no repeated slash, trailing slash, or '.' component)"
        )
    parts = value.split("/")
    if "." in parts or ".." in parts:
        # A-210's "no `.`/`..`". The canonical-spelling check above catches
        # every EMBEDDED dot component ("./a", "a/."), because PurePosixPath
        # normalises those away -- but not a bare ".", which round-trips as
        # itself and would otherwise be an accepted spelling for the project
        # root. ".assay/attestations" is unaffected: a leading-dot FILENAME is
        # not a "." component.
        raise LaneConfigError(
            f"{where}: 'judge.attestation_dir' {value!r} contains a "
            f"'.' or '..' component"
        )
    if len(parts) > MAX_ATTESTATION_DIR_COMPONENTS:
        raise LaneConfigError(
            f"{where}: 'judge.attestation_dir' {value!r} has more than "
            f"{MAX_ATTESTATION_DIR_COMPONENTS} components"
        )
    return value


def _load_evidence(value: Any, where: str) -> tuple[EvidenceConfig, ...]:
    """The closed ``judge.evidence`` grammar (P26/A-209): 1..64 entries,
    input order preserved, exactly the inline keys ``source``/``key``, only
    ``source="attested"`` supported, and no duplicate ``(source, key)``.
    """
    if not isinstance(value, list):
        raise LaneConfigError(
            f"{where}: 'judge.evidence' must be an array, got {_type_name(value)}"
        )
    if not (MIN_EVIDENCE_DECLARATIONS <= len(value) <= MAX_EVIDENCE_DECLARATIONS):
        raise LaneConfigError(
            f"{where}: 'judge.evidence' must declare "
            f"{MIN_EVIDENCE_DECLARATIONS}..{MAX_EVIDENCE_DECLARATIONS} entries, "
            f"got {len(value)}"
        )
    items: list[EvidenceConfig] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise LaneConfigError(
                f"{where}: 'judge.evidence[{index}]' must be a table, got "
                f"{_type_name(entry)}"
            )
        unknown = sorted(set(entry) - set(_EVIDENCE_FIELDS))
        if unknown:
            raise LaneConfigError(
                f"{where}: unknown judge.evidence[{index}] key(s): "
                f"{', '.join(unknown)}; expected only: {', '.join(_EVIDENCE_FIELDS)}"
            )
        for field in _EVIDENCE_FIELDS:
            if field not in entry:
                raise LaneConfigError(
                    f"{where}: missing required field 'judge.evidence[{index}].{field}'"
                )
        source = _as_str(entry["source"], where, f"judge.evidence[{index}].source")
        if source not in _EVIDENCE_SOURCES:
            raise LaneConfigError(
                f"{where}: 'judge.evidence[{index}].source' must be one of "
                f"{sorted(_EVIDENCE_SOURCES)}, got {source!r}"
            )
        key = _as_str(entry["key"], where, f"judge.evidence[{index}].key")
        if not _EVIDENCE_KEY_RE.fullmatch(key):
            raise LaneConfigError(
                f"{where}: 'judge.evidence[{index}].key' {key!r} does not match "
                f"the closed grammar {_EVIDENCE_KEY_RE.pattern!r}"
            )
        identity = (source, key)
        if identity in seen:
            raise LaneConfigError(
                f"{where}: 'judge.evidence' declares {identity} more than once"
            )
        seen.add(identity)
        items.append(EvidenceConfig(source=source, key=key))
    return tuple(items)


def _validate_artifact_path(value: Any, where: str, project_root: Path, field: str) -> str:
    """The project-relative OUTPUT-artifact path grammar (P17 work item 3),
    shared by every declared artifact a lane's own command later WRITES:
    ``judge.coverage.artifact`` and, as of P34/W4, ``judge.mutation.
    equivalence_artifact``/``kill_signal_artifact`` -- the identical
    containment reasoning :func:`_resolve_source_root` already applies to
    ``source_roots``, one field over. A non-empty string, never absolute,
    and resolving beneath *project_root* once ``..`` and any symlink are
    collapsed. Existence is deliberately NOT checked here: every one of
    these paths is an OUTPUT the lane's own command writes, so it need not
    exist yet when ``assay.toml`` loads (A-048's own timing) -- only
    :mod:`assay.runner`'s own RUNTIME checks (tracked-by-git, in
    particular) need live filesystem/git state, and belong to the module
    that actually executes the lane.

    *field* is the fully-dotted config key *value* came from (e.g.
    ``"judge.coverage.artifact"``), so one shared function still names the
    SPECIFIC offending field in every message (AGENTS.md 4.2a) -- extracting
    this out of :func:`_load_coverage` must not cost a reader the ability to
    tell which of the three call sites refused.
    """
    artifact = _as_str(value, where, field)
    if not artifact:
        raise LaneConfigError(f"{where}: '{field}' is empty")
    if Path(artifact).is_absolute():
        raise LaneConfigError(
            f"{where}: '{field}' {artifact!r} is absolute; it is relative "
            f"to the directory containing assay.toml ({project_root})"
        )
    resolved_artifact = (project_root / artifact).resolve()
    if not resolved_artifact.is_relative_to(project_root):
        raise LaneConfigError(
            f"{where}: '{field}' {artifact!r} resolves to "
            f"{resolved_artifact}, which is not contained beneath the "
            f"project root {project_root} (via '..' or a symlink) -- a lane "
            f"must not be able to point '{field}' outside the project it "
            f"declares"
        )
    return artifact


def _load_coverage(
    value: Any, where: str, language: str | None, project_root: Path
) -> CoverageConfig:
    if not isinstance(value, dict):
        raise LaneConfigError(
            f"{where}: 'judge.coverage' must be a table, got {_type_name(value)}"
        )
    known = (*_COVERAGE_FIELDS, *_COVERAGE_OPTIONAL_FIELDS)
    unknown = sorted(set(value) - set(known))
    if unknown:
        raise LaneConfigError(
            f"{where}: unknown judge.coverage key(s): {', '.join(unknown)}; "
            f"expected only: {', '.join(known)}"
        )
    for field in _COVERAGE_FIELDS:
        if field not in value:
            raise LaneConfigError(
                f"{where}: missing required field 'judge.coverage.{field}'"
            )
    fmt = _as_str(value["format"], where, "judge.coverage.format")
    if not fmt:
        raise LaneConfigError(f"{where}: 'judge.coverage.format' is empty")
    artifact = _validate_artifact_path(
        value["artifact"], where, project_root, "judge.coverage.artifact"
    )
    if fmt not in FORMAT_REGISTRY:
        # A-068: cross-checked against the parser registry's own keys, not a
        # second hardcoded list, so the vocabulary can never drift out of
        # sync with what P03's registry actually parses.
        raise LaneConfigError(
            f"{where}: 'judge.coverage.format' {fmt!r} is not a format the "
            f"parser registry knows; declared formats: "
            f"{sorted(FORMAT_REGISTRY)}"
        )
    # (A-406, DA-R1) A language whose adapter is judged from block extents may
    # only declare a format that CARRIES them. Checked AFTER format-registry
    # membership so an unknown format is still reported as unknown -- naming
    # the language would misdirect a plain typo -- and BEFORE the producer,
    # because a producer name is only meaningful once the format is one this
    # lane may use at all.
    #
    # WHY AT LOAD, and not where the profile is parsed. `judge.language` and
    # `judge.coverage.format` are independent by design, so a Go lane
    # declaring `format = "lcov"` is a file assay used to ACCEPT: the profile
    # then arrived with no block extents, `runner` marked it
    # statement-attributed vacuously, and A-392's guard -- the whole point of
    # which is to make skipping the oracle impossible -- waved it through with
    # no oracle and no `helpers` entry. lcov converted from a Go coverprofile
    # carries exactly the naive block expansion A-392 exists to refuse, so the
    # verdict would have been wrong in the direction that looks right.
    # Refusing here means the lane never runs at all, which is the earliest
    # and cheapest place the fault is knowable: it is a property of the file,
    # not of any artifact.
    attributable = STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE.get(language or "")
    if attributable is not None and fmt not in attributable:
        readable = ", ".join(
            f"{name!r} (producers: {', '.join(COVERAGE_PRODUCERS_BY_FORMAT[name])})"
            if name in COVERAGE_PRODUCERS_BY_FORMAT
            else repr(name)
            for name in sorted(attributable)
        )
        raise LaneConfigError(
            f"{where}: judge.language = {language!r} is judged from the block "
            f"extents its coverage records carry -- its executable lines are "
            f"derived from the source by a statement-position oracle, never "
            f"read off the artifact -- but 'judge.coverage.format' is "
            f"{fmt!r}, which carries no block extents. A format like {fmt!r} "
            f"produced from a {language} coverage profile by a converter "
            f"reports each block's whole extent as executable lines, counting "
            f"function signatures, closing braces and `case` labels as code, "
            f"and assay would have no way to tell that from statement truth. "
            f"Declare instead: {readable}"
        )
    producer = _load_coverage_producer(value, where, fmt)
    return CoverageConfig(format=fmt, artifact=artifact, producer=producer)


def _load_coverage_producer(
    value: Mapping[str, Any], where: str, fmt: str
) -> str | None:
    """``judge.coverage.producer`` (B045) -- the declared, closed, PER-FORMAT
    producer name, or ``None`` when the format allows the omission and the
    file took it.

    The refusal order is deliberate and is the whole point of the key. A name
    this build KNOWS but refuses (``vitest-v8``, ``jest-v8``, ``c8``) is
    tested BEFORE catalogue membership, exactly as
    ``WITHDRAWN_MUTATION_OPERATORS`` already is one field over: a consumer who
    declared the unsound Vitest provider must be told what is wrong with it
    and how to fix it, not merely that the string is not in a list.
    """
    open_vocabulary = COVERAGE_PRODUCERS_BY_FORMAT.get(fmt)
    declared = value.get("producer")

    if declared is None:
        if fmt in COVERAGE_PRODUCER_REQUIRED_FORMATS:
            assert open_vocabulary is not None
            allowed = [
                name
                for name in open_vocabulary
                if name not in REFUSED_COVERAGE_PRODUCERS
            ]
            raise LaneConfigError(
                f"{where}: missing required field 'judge.coverage.producer'; "
                f"format {fmt!r} is written by several producers that "
                f"DISAGREE about what its contents mean (A-344: what "
                f"`branchMap` is; A-346: whether a line ran), so there is no "
                f"value assay could imply that is correct in every context. "
                f"Declare one of: {allowed}"
            )
        return None

    producer = _as_str(declared, where, "judge.coverage.producer")
    if not producer:
        raise LaneConfigError(f"{where}: 'judge.coverage.producer' is empty")

    if open_vocabulary is None:
        raise LaneConfigError(
            f"{where}: 'judge.coverage.producer' {producer!r} is declared for "
            f"format {fmt!r}, which has no open producer vocabulary; assay "
            f"does not accept a producer name it cannot check or explain "
            f"(DESIGN-GUIDE §5 -- no speculative names). Formats with an open "
            f"vocabulary: {sorted(COVERAGE_PRODUCERS_BY_FORMAT)}"
        )

    if producer in REFUSED_COVERAGE_PRODUCERS and producer in open_vocabulary:
        raise LaneConfigError(
            f"{where}: 'judge.coverage.producer' {producer!r} is a known "
            f"producer of {fmt!r} that assay REFUSES to gate on: "
            f"{REFUSED_COVERAGE_PRODUCERS[producer]}"
        )

    if producer not in open_vocabulary:
        allowed = [
            name for name in open_vocabulary if name not in REFUSED_COVERAGE_PRODUCERS
        ]
        raise LaneConfigError(
            f"{where}: 'judge.coverage.producer' {producer!r} is not a "
            f"producer of format {fmt!r}; the vocabulary is closed per "
            f"format, and {fmt!r} accepts: {allowed}"
        )

    return producer


def _load_mutation(
    value: Any, where: str, language: str | None, project_root: Path
) -> MutationConfig:
    if not isinstance(value, dict):
        raise LaneConfigError(
            f"{where}: 'judge.mutation' must be a table, got {_type_name(value)}"
        )
    # (P33/A-227/A-230b, narrowed P34/W4) The two v6 artifact fields are
    # legal ONLY on a `judge.language = "sql"` lane. Checked BEFORE the
    # unknown-key sweep on purpose: for every OTHER language `judge.mutation`
    # is still a closed table that does not name either field, so both are
    # already rejected today as unknown keys -- which means "the declaration
    # is refused" is true with or without this code and distinguishes
    # nothing. The observable is the MESSAGE: a Python or Go lane declaring
    # either gets a refusal that names WHY (language-scoped), not one that
    # reads like a typo (A-227's "the refusal for every other language must
    # survive" is a test, not a note).
    if language != "sql":
        reserved = sorted(set(value) & set(_MUTATION_SQL_ONLY_FIELDS))
        if reserved:
            raise LaneConfigError(
                f"{where}: judge.mutation key(s) {', '.join(reserved)} "
                f"require judge.language = 'sql' (P34); this lane declares "
                f"judge.language = {language!r}, and the v6 verdict contract "
                f"carries these fields only for a sql lane, the only "
                f"language this build ships a producer for"
            )
    unknown = sorted(
        set(value)
        - set(_MUTATION_FIELDS)
        - set(_MUTATION_OPTIONAL_FIELDS)
        - set(_MUTATION_SQL_ONLY_FIELDS)
        - set(_MUTATION_INGESTED_FIELDS)
    )
    if unknown:
        raise LaneConfigError(
            f"{where}: unknown judge.mutation key(s): {', '.join(unknown)}; "
            f"expected only: {', '.join((*_MUTATION_FIELDS, *_MUTATION_OPTIONAL_FIELDS, *_MUTATION_SQL_ONLY_FIELDS, *_MUTATION_INGESTED_FIELDS))}"
        )
    # (B046) The producer fork, decided BEFORE any per-field validation. The
    # discriminator is `format`'s presence and nothing else -- not the lane's
    # language (any language whose tool emits a registered format may ingest)
    # and not the artifact's content (A-007).
    if "format" in value:
        return _load_ingested_mutation(value, where, language, project_root)
    for field in _MUTATION_INGESTED_FIELDS[1:]:
        if field in value:
            raise LaneConfigError(
                f"{where}: 'judge.mutation.{field}' requires "
                f"'judge.mutation.format'; it describes an INGESTED mutation "
                f"report -- where the lane's own argv runs a foreign mutation "
                f"tool inside the snapshot and assay judges what it wrote -- "
                f"and without a declared format assay has no parser to read "
                f"that report with. Declare format = "
                f"{sorted(MUTATION_FORMAT_REGISTRY)[0]!r}, or drop this key"
            )
    for field in _MUTATION_FIELDS:
        if field not in value:
            raise LaneConfigError(
                f"{where}: missing required field 'judge.mutation.{field}'"
            )
    jobs = value["jobs"]
    if isinstance(jobs, bool) or not isinstance(jobs, int):
        raise LaneConfigError(
            f"{where}: 'judge.mutation.jobs' must be an integer, got "
            f"{_type_name(jobs)}"
        )
    if jobs < 1:
        raise LaneConfigError(
            f"{where}: 'judge.mutation.jobs' must be a positive integer, got {jobs}"
        )
    max_mutants = value["max_mutants"]
    if isinstance(max_mutants, bool) or not isinstance(max_mutants, int):
        raise LaneConfigError(
            f"{where}: 'judge.mutation.max_mutants' must be an integer, got "
            f"{_type_name(max_mutants)}"
        )
    if not MIN_MAX_MUTANTS <= max_mutants <= MAX_MAX_MUTANTS:
        raise LaneConfigError(
            f"{where}: 'judge.mutation.max_mutants' must be in "
            f"{MIN_MAX_MUTANTS}..{MAX_MAX_MUTANTS:,}, got {max_mutants}"
        )
    operators = _as_str_list(value["operators"], where, "judge.mutation.operators")
    if not operators:
        raise LaneConfigError(
            f"{where}: 'judge.mutation.operators' is empty; a policy that "
            f"selects no operator mutates nothing"
        )
    if len(set(operators)) != len(operators):
        raise LaneConfigError(
            f"{where}: 'judge.mutation.operators' contains a duplicate: "
            f"{operators}"
        )
    # P21 work item 2: a MODULE-LEVEL import now (see the top of this file).
    # The deferred import this used to need existed only because the
    # vocabulary lived in `assay.mutation`, which imports `Lane` from here --
    # a genuine `config -> mutation -> config` cycle. `assay.vocabulary` is a
    # leaf that imports nothing, so the workaround is deleted rather than
    # maintained.
    # (P33/V5-2/O2) The vocabulary is language-qualified and closed PER
    # LANGUAGE, so the cross-check is two questions, not one, and the
    # cross-language answer must come first: `sql:drop-check` on a Python
    # lane IS a known operator, and reporting it as unknown would misname the
    # defect. A flat extension would have let a Python lane declare
    # `drop-check` and then report SQL mutation operators it could not
    # possibly have applied.
    foreign = sorted(
        operator
        for operator in operators
        if operator in MUTATION_OPERATORS
        and language is not None
        and operator_language(operator) != language
    )
    # (A-331) Under v7 the catalogue still SPELLED the two withdrawn
    # operators, so these suggestion lists had to filter them back out.
    # At the v8 cut the spellings are gone from the catalogue itself, so
    # declarable == spellable and the filter would be dead code -- deleted
    # rather than left standing as a no-op that reads like a live guard.
    declarable_for_language = MUTATION_OPERATORS_BY_LANGUAGE.get(language or "", ())
    declarable = MUTATION_OPERATORS
    if foreign:
        raise LaneConfigError(
            f"{where}: 'judge.mutation.operators' names {', '.join(foreign)}, "
            f"which belong to another language; this lane declares "
            f"judge.language = {language!r}, and its operators are: "
            f"{', '.join(declarable_for_language)}"
        )
    # B034/A-326/A-331: the withdrawn check now runs BEFORE the unknown
    # check, and the order is load-bearing rather than cosmetic. Under v7
    # these two names were still in `MUTATION_OPERATORS`, so "unknown" could
    # not fire on them and either order read the same. At the v8 cut the
    # spellings are gone from the catalogue, so a lane file that still
    # carries one is now literally an unknown operator -- and answering a
    # consumer's stale-but-once-legal `python:enum-comparison-swap` with a
    # bare "unknown operator(s)" would misname the defect and hide the one
    # thing they need to be told, which is WHY it went away and what covers
    # it instead.
    withdrawn = sorted(set(operators) & WITHDRAWN_MUTATION_OPERATORS)
    if withdrawn:
        raise LaneConfigError(
            f"{where}: 'judge.mutation.operators' names withdrawn "
            f"operator(s): {', '.join(withdrawn)}. Every site they ever "
            f"produced was already produced by python:compare-swap at the "
            f"same byte span with the same replacement, so declaring both "
            f"emitted each shared site twice and added no coverage. Delete "
            f"them; python:compare-swap already covers ==/!= swapping"
        )
    unknown_operators = sorted(set(operators) - set(MUTATION_OPERATORS))
    if unknown_operators:
        raise LaneConfigError(
            f"{where}: 'judge.mutation.operators' names unknown operator(s): "
            f"{', '.join(unknown_operators)}; known operators: "
            f"{', '.join(declarable)}"
        )
    # (P34/W4) `equivalence_artifact` is REQUIRED on a sql lane -- the
    # carve's single most consequential config decision (§4.3): without it,
    # a mutant that never actually mutated (residue from a previous run
    # still in the database, say) exits 0 and is recorded `survived`, a
    # false statement about the consumer's tests. `kill_signal_artifact`
    # stays optional even on a sql lane (A-223b derives `kill_attribution`
    # from its presence). Both share `judge.coverage.artifact`'s own path
    # grammar via `_validate_artifact_path`, and neither is even INSPECTED
    # for a non-sql lane -- the reserved-key check above already refused
    # any lane that tried to declare one.
    equivalence_artifact: str | None = None
    kill_signal_artifact: str | None = None
    if language == "sql":
        if "equivalence_artifact" not in value:
            raise LaneConfigError(
                f"{where}: 'judge.mutation.equivalence_artifact' is required "
                f"on a sql lane; without it a mutant that never actually "
                f"mutated would be recorded 'survived', a false statement "
                f"about the consumer's tests"
            )
        equivalence_artifact = _validate_artifact_path(
            value["equivalence_artifact"],
            where,
            project_root,
            "judge.mutation.equivalence_artifact",
        )
    if "kill_signal_artifact" in value:
            kill_signal_artifact = _validate_artifact_path(
                value["kill_signal_artifact"],
                where,
                project_root,
                "judge.mutation.kill_signal_artifact",
            )
    budget_per_candidate = value.get("budget_per_candidate")
    if budget_per_candidate is not None:
        if not isinstance(budget_per_candidate, str) or not budget_per_candidate:
            raise LaneConfigError(
                f"{where}: 'judge.mutation.budget_per_candidate' must be a "
                f"non-empty string, got {_type_name(budget_per_candidate)}"
            )
        try:
            parse_duration(budget_per_candidate)
        except ValueError as exc:
            raise LaneConfigError(
                f"{where}: 'judge.mutation.budget_per_candidate' {exc}"
            ) from exc
    shard_index = value.get("shard_index")
    shard_count = value.get("shard_count")
    shard_specified = "shard_index" in value or "shard_count" in value
    if shard_specified and (shard_index is None or shard_count is None):
        raise LaneConfigError(
            f"{where}: 'judge.mutation.shard_index' and "
            f"'judge.mutation.shard_count' must be declared together"
        )
    if shard_specified:
        for field, number in (
            ("shard_index", shard_index),
            ("shard_count", shard_count),
        ):
            if isinstance(number, bool) or not isinstance(number, int):
                raise LaneConfigError(
                    f"{where}: 'judge.mutation.{field}' must be an integer, got {_type_name(number)}"
                )
        if not 1 <= shard_count <= MAX_SHARD_COUNT:
            raise LaneConfigError(
                f"{where}: 'judge.mutation.shard_count' must be in "
                f"1..{MAX_SHARD_COUNT:,}, got {shard_count}"
            )
        if not 0 <= shard_index < shard_count:
            raise LaneConfigError(
                f"{where}: 'judge.mutation.shard_index' {shard_index} is outside "
                f"0..{shard_count - 1}"
            )
    return MutationConfig(
        jobs=jobs,
        max_mutants=max_mutants,
        operators=tuple(operators),
        kill_signal_artifact=kill_signal_artifact,
        equivalence_artifact=equivalence_artifact,
        budget_per_candidate=budget_per_candidate,
        shard_index=shard_index,
        shard_count=shard_count,
    )


def _load_ingested_mutation(
    value: Mapping[str, Any], where: str, language: str | None, project_root: Path
) -> MutationConfig:
    """``judge.mutation`` on an INGESTED lane (B046): the lane's own argv runs
    a foreign mutation tool inside the private snapshot, and assay judges the
    report it wrote.

    **The refusals here are a SECOND layer, not the only one.** The verdict
    model independently forbids ``jobs``/``max_mutants``/``operators``/
    ``equivalence_artifact`` on an ingested ``judgment.r2`` (A-360), and both
    layers are deliberate: the model's refusal protects a document a consumer
    reads back, and this one is what a human editing ``assay.toml`` actually
    sees. A verdict-layer ValueError is not a loader diagnostic, and telling
    someone "your artifact is invalid" when what they need is "assay ran none
    of this, so it declares none of it" is the weaker of the two messages.
    """
    declared_format = _as_str(value["format"], where, "judge.mutation.format")
    if declared_format not in MUTATION_FORMAT_REGISTRY:
        # A-068 one tier over: cross-checked against the parser registry's own
        # keys, never a second hardcoded list.
        raise LaneConfigError(
            f"{where}: 'judge.mutation.format' {declared_format!r} is not a "
            f"mutation-report format the parser registry knows; declared "
            f"formats: {sorted(MUTATION_FORMAT_REGISTRY)}"
        )

    native_only = sorted(
        set(value) & (set(_MUTATION_FIELDS) | {"equivalence_artifact"})
    )
    if native_only:
        raise LaneConfigError(
            f"{where}: judge.mutation key(s) {', '.join(native_only)} are "
            f"forbidden on an INGESTED lane (one declaring "
            f"judge.mutation.format). They are assay's OWN R2 policy, and "
            f"assay decided none of it here: the mutation tool your argv runs "
            f"chose its own mutators, its own concurrency and its own "
            f"ceiling, and its equivalence story is its own. Declaring them "
            f"would put that tool's configuration on the wire under assay's "
            f"name -- so the verdict leaves judgment.r2.{native_only[0]} "
            f"ABSENT rather than backfilled. The operators the tool actually "
            f"applied are still recorded, one per mutant, in "
            f"mutation.*[].operator"
        )
    orchestration_only = sorted(
        set(value) & {"budget_per_candidate", "shard_index", "shard_count"}
    )
    if orchestration_only:
        raise LaneConfigError(
            f"{where}: judge.mutation key(s) "
            f"{', '.join(orchestration_only)} are forbidden on an INGESTED "
            f"lane: they bound or partition an execution assay performs, and "
            f"on this lane assay executes no mutant at all -- it reads a "
            f"report the lane's own command already produced. Bound the run "
            f"with the lane's own `budget`, and configure the mutation tool's "
            f"own timeout in its own config"
        )

    for field in ("artifact", "fail_under"):
        if field not in value:
            raise LaneConfigError(
                f"{where}: missing required field 'judge.mutation.{field}' "
                f"(required once judge.mutation.format is declared)"
            )
    artifact = _validate_artifact_path(
        value["artifact"], where, project_root, "judge.mutation.artifact"
    )
    fail_under = _as_float(value["fail_under"], where, "judge.mutation.fail_under")
    if not 0.0 <= fail_under <= 100.0:
        raise LaneConfigError(
            f"{where}: 'judge.mutation.fail_under' must be in 0.0..100.0, got "
            f"{fail_under}"
        )
    if fail_under != 100.0:
        # (A-380/B050) The one place this build cannot yet do what B046's
        # contract describes, refused LOUDLY rather than half-implemented.
        #
        # A mutation-score floor below 100 means "some survivors are
        # acceptable" -- and the v9 verdict has NO judgment.r2 field that can
        # record which floor was applied. A lane judging at 90 would therefore
        # emit a PASS beside recorded survivors with nothing in the document
        # explaining it, and that breaks the single property judgment.r2
        # exists to protect: that an independent consumer can re-derive the R2
        # claim's STATUS from the payload's own buckets, with no external
        # policy input. `verify.py`'s own re-derivation would (correctly) call
        # such a document inconsistent.
        #
        # Accepting the key and silently ignoring it would be worse than
        # either alternative -- inert config that cannot fail loudly when it
        # is wrong (AGENTS.md 4.2a) -- and dropping the key would leave the
        # documented worked lane unloadable. So: declarable, honoured at
        # exactly its one currently-expressible value, and the message names
        # the field a later schema cut needs.
        raise LaneConfigError(
            f"{where}: 'judge.mutation.fail_under' is {fail_under}, but this "
            f"build can only honour 100.0 on an ingested lane. A lower floor "
            f"means some surviving mutants are acceptable, and verdict schema "
            f"v9 has no judgment.r2 field that records WHICH floor was "
            f"applied -- so the verdict would report PASS beside recorded "
            f"survivors with nothing in it to explain that, and an "
            f"independent consumer re-deriving the R2 status from the payload "
            f"(which is exactly what `assay verify` does) would call the "
            f"document inconsistent. Declare 100.0, or track B050 for the "
            f"wire field a lower floor needs"
        )
    kill_signal_artifact: str | None = None
    if "kill_signal_artifact" in value:
        # Reachable only on a sql lane -- the reserved-key check above already
        # refused it for every other language -- and a sql lane cannot be
        # ingested today anyway (`equivalence_artifact` is required there and
        # forbidden here). Validated rather than assumed absent, so the shape
        # stays honest if a future sql-emitting mutation tool registers a
        # format.
        kill_signal_artifact = _validate_artifact_path(
            value["kill_signal_artifact"],
            where,
            project_root,
            "judge.mutation.kill_signal_artifact",
        )
    if language == "sql":
        raise LaneConfigError(
            f"{where}: judge.language = 'sql' requires "
            f"'judge.mutation.equivalence_artifact' (without it a mutant that "
            f"never actually mutated would be recorded 'survived'), and that "
            f"key is forbidden on an ingested lane -- so a sql lane cannot "
            f"ingest a mutation report in this build. No registered format is "
            f"emitted by a sql mutation tool today, so this combination "
            f"names no real toolchain"
        )
    return MutationConfig(
        format=declared_format,
        artifact=artifact,
        fail_under=fail_under,
        kill_signal_artifact=kill_signal_artifact,
    )


def _load_canary(
    value: Any,
    where: str,
    project_root: Path,
    source_root_paths: tuple[Path, ...] | None,
) -> CanaryConfig:
    """``judge.canary`` (P19): a closed table, exactly ``mechanism`` and
    ``target`` -- never a plural list (work item 2: one R3 claim is one
    mechanism execution).

    ``target`` must be a normalized, project-relative path to a REAL,
    ordinary source file contained beneath one of the lane's own declared
    ``source_roots`` -- the identical containment discipline
    :func:`_resolve_source_root` already applies to a source root itself,
    reused here rather than re-derived (both compare two already-resolved
    paths via :meth:`~pathlib.Path.is_relative_to`, so a symlink escape or a
    ``..`` traversal is caught the same way for either). Existence IS
    checked at load time (unlike :func:`_load_coverage`'s own artifact,
    which is this run's own OUTPUT and need not exist yet) -- a canary
    target is real source code :mod:`assay.canary` reads and transforms, so
    a typo'd path is a config mistake this loader can catch instead of a
    bare ``FileNotFoundError`` surfacing deep inside a scratch-copy pipeline.

    Whether *target* is itself a TEST path (an adapter-specific question --
    Python's own convention differs from a future second language's) is
    deliberately NOT decided here: this module carries zero adapter
    knowledge anywhere else (``judge.language`` stays an opaque string all
    the way through), and checking it would mean importing one. That
    rejection belongs to :mod:`assay.canary`'s own orchestration, which
    already receives a real, resolved adapter.
    """
    if not isinstance(value, dict):
        raise LaneConfigError(
            f"{where}: 'judge.canary' must be a table, got {_type_name(value)}"
        )
    unknown = sorted(set(value) - set(_CANARY_FIELDS))
    if unknown:
        raise LaneConfigError(
            f"{where}: unknown judge.canary key(s): {', '.join(unknown)}; "
            f"expected only: {', '.join(_CANARY_FIELDS)}"
        )
    for field in _CANARY_FIELDS:
        if field not in value:
            raise LaneConfigError(
                f"{where}: missing required field 'judge.canary.{field}'"
            )

    mechanism = _as_str(value["mechanism"], where, "judge.canary.mechanism")
    # Deferred, not module-level -- the identical reasoning `_load_mutation`
    # already gives for `assay.mutation.MUTATION_OPERATORS`, one field over:
    # `assay.canary` imports `Lane` from THIS module at ITS OWN module
    # level, so a module-level import here would be a genuine cycle
    # (config -> canary -> config).
    from .canary import CANARY_MECHANISMS

    if mechanism not in CANARY_MECHANISMS:
        raise LaneConfigError(
            f"{where}: 'judge.canary.mechanism' {mechanism!r} is not one of "
            f"{sorted(CANARY_MECHANISMS)}"
        )

    target = _as_str(value["target"], where, "judge.canary.target")
    if not target:
        raise LaneConfigError(f"{where}: 'judge.canary.target' is empty")
    candidate = Path(target)
    if candidate.is_absolute():
        raise LaneConfigError(
            f"{where}: 'judge.canary.target' {target!r} is absolute; it is "
            f"relative to the project root, the same as source_roots"
        )
    raw_path = project_root / candidate
    if raw_path.is_symlink():
        # Checked first, exactly like `_is_unsafe_coverage_artifact`'s own
        # ordering (`runner.py`): `is_symlink` never raises for a
        # non-existent path, unlike a naive existence check that follows
        # the link first.
        raise LaneConfigError(
            f"{where}: 'judge.canary.target' {target!r} is a symlink; a "
            f"canary target must be a real, ordinary source file"
        )
    resolved = raw_path.resolve()
    roots = source_root_paths or ()
    if not any(resolved.is_relative_to(root) for root in roots):
        raise LaneConfigError(
            f"{where}: 'judge.canary.target' {target!r} resolves to "
            f"{resolved}, which is not contained beneath any declared "
            f"source root {[str(root) for root in roots]} (via '..' or a "
            f"symlink) -- a canary target must live beneath a declared "
            f"source root"
        )
    if not resolved.is_file():
        raise LaneConfigError(
            f"{where}: 'judge.canary.target' {target!r} does not exist as "
            f"a file under the project root {project_root} (looked for "
            f"{resolved})"
        )
    # P21/A-152: the declared spelling becomes the NORMALIZED wire spelling
    # here, at the one boundary that reads it, so `CanaryResult.target` and
    # `judgment.r3.target` are equal as STRINGS rather than merely as
    # filesystem paths. `./src/p.py` and `src/p.py` name one file; if both
    # spellings could reach the artifact, the equality check that finally
    # makes `judgment.r3.target` witnessable could be satisfied -- or
    # broken -- by spelling alone. Normalizing at load also means the model
    # never receives a shape its wire grammar would refuse, so a legal lane
    # can never crash a producer.
    if "\\" in target:
        raise LaneConfigError(
            f"{where}: 'judge.canary.target' {target!r} contains a backslash; "
            f"declare it with forward slashes regardless of platform"
        )
    normalized = PurePosixPath(os.path.normpath(target)).as_posix()
    if normalized == "." or normalized.startswith("../"):
        raise LaneConfigError(
            f"{where}: 'judge.canary.target' {target!r} does not normalize to "
            f"a path inside the project ({normalized!r})"
        )
    return CanaryConfig(mechanism=mechanism, target=normalized)


def _resolve_source_root(raw: str, where: str, project_root: Path) -> Path:
    """Resolve one declared source root against the PROJECT root (A-049).

    Not the repo root and not the process cwd: the lane file must not need to
    know where it sits inside a monorepo, and a project vendored one level
    deeper must not silently start measuring nothing.

    *project_root* is already fully resolved (:func:`load_lane_file` resolves
    ``assay.toml``'s own path before taking its parent), so the CONTAINMENT
    check below (P15, A-067 finding 9: "a lane can measure a sibling
    project despite the diagnostic claiming containment") compares two
    already-resolved paths: rejecting a raw string that merely
    LOOKS relative (no leading ``/``, no ``..`` visible in *raw* itself) is
    not enough, because ``../sibling`` is exactly such a string, and a
    symlink inside the project root can point anywhere on disk regardless of
    what *raw* spells. ``Path.resolve()`` collapses BOTH ``..`` components
    and symlinks to their real final target, so comparing the two resolved
    paths catches either escape route the same way.
    """
    if not raw:
        raise LaneConfigError(f"{where}: 'judge.source_roots' contains an empty path")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise LaneConfigError(
            f"{where}: source root {raw!r} is absolute; source_roots are relative "
            f"to the directory containing assay.toml ({project_root})"
        )
    resolved = (project_root / candidate).resolve()
    if not resolved.is_dir():
        # A-016/A-035: a typo'd root matches no changed file, so the gate
        # returns 0/0 PASS forever. That is a laundering gate, and none of the
        # four existing copies guards it.
        raise LaneConfigError(
            f"{where}: source root {raw!r} does not exist under the project root "
            f"{project_root} (looked for {resolved})"
        )
    if not resolved.is_relative_to(project_root):
        raise LaneConfigError(
            f"{where}: source root {raw!r} resolves to {resolved}, which is "
            f"not contained beneath the project root {project_root} (via "
            f"'..' or a symlink) -- a lane must not be able to measure a "
            f"tree outside the project it declares"
        )
    return resolved


def _as_str(value: Any, where: str, field: str) -> str:
    if not isinstance(value, str):
        raise LaneConfigError(
            f"{where}: {field!r} must be a string, got {_type_name(value)}"
        )
    return value


def _as_bool(value: Any, where: str, field: str) -> bool:
    if not isinstance(value, bool):
        raise LaneConfigError(
            f"{where}: {field!r} must be a boolean, got {_type_name(value)}"
        )
    return value


def _as_float(value: Any, where: str, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LaneConfigError(
            f"{where}: {field!r} must be a number, got {_type_name(value)}"
        )
    return float(value)


def _as_str_list(value: Any, where: str, field: str) -> list[str]:
    if not isinstance(value, list):
        raise LaneConfigError(
            f"{where}: {field!r} must be an array, got {_type_name(value)}"
        )
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise LaneConfigError(
                f"{where}: {field}[{index}] must be a string, got "
                f"{_type_name(item)}"
            )
    return list(value)


def _as_str_table(value: Any, where: str, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise LaneConfigError(
            f"{where}: {field!r} must be a table, got {_type_name(value)}"
        )
    for key, item in value.items():
        if not isinstance(item, str):
            # A-019: `env` is declared-only. Coercing 1 or true into "1"/"true"
            # would be assay inventing the spelling of a value the process will
            # actually see.
            raise LaneConfigError(
                f"{where}: {field}.{key} must be a string, got {_type_name(item)}; "
                f"environment values are strings — quote it"
            )
    return dict(value)
