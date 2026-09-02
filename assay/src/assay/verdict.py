"""The verdict model, and the schema that REJECTS a malformed verdict.

The claim this module exists to defend is not *"can it describe a good
verdict"* — every model can do that, including one that requires nothing. It is
**does the contract refuse a bad one?** Three separate things enforce it, and
they are deliberately not the same thing:

* :data:`SCHEMA_RESOURCE` — a hand-written JSON Schema **shipped as data**
  (A-029), so ciu, a CI system or nyxloom validates an artifact against a *file*
  without importing assay. It is never generated from the classes below: a
  schema generated from the model could only prove the model agrees with itself.
* the dataclasses here — which refuse to *construct* a verdict the schema would
  refuse to accept, so a bug is a crash in assay rather than a bad artifact in a
  consumer's hands.
* :func:`rollup` — A-023's precedence, so an outcome is derived from the claims
  rather than chosen.

Three rules are worth stating in prose because they are the ones that get
"simplified" back out:

**Absent means unknowable; empty means known-and-empty.** When no measurement
happened there is no coverage payload, not a zeroed one (A-025) — a consumer
reading ``pct`` and ignoring ``outcome`` must find nothing to read. When no lane
ever resolved there is no ``argv_declared``, not an empty one: ``[]`` asserts
*"the lane declared no argv"*, which is false. So the lane-resolved group
(``declared_rigor``, ``declared_evidence``, the three ``argv_*``,
``argv_modified`` and the two ``env_*``) is all-present or all-absent, and
:class:`Verdict` refuses a mixture.

**One entry per declared rigor level, not a flat verdict** (A-024). A lane
declaring ``["R0","R1","R2"]`` can pass R0, pass R1 and be ``INCONCLUSIVE`` on
R2; a scalar verdict flattens that into a lie. It also means *"R2 was declared
but rendered no judgement"* stays distinguishable from *"R2 was never
declared"*, which is why :class:`Verdict` refuses claims that do not cover
``declared_rigor`` exactly.

**Evidence provenance is not rigor.** ``claims`` contains only evidence assay
computed for R0-R3. Adjudicated and attested requirements use the sibling
``declared_evidence`` / ``evidence`` arrays, keyed by ``(source, key)``. The two
arrays must cover the same identities exactly, preserving the distinction
between *declared but rendered no judgement* and *never declared* without
pretending an external review is an R3 computation.

What is NOT here, on purpose: ``gate_id``, ``phase`` and ``environment``.
DESIGN-GUIDE §6 describes the artifact as a superset of nyxloom's
``GateResult``, but assay has no source for nyxloom's phase vocabulary (§4.2a
forbids inventing one) and no environment knowledge at all (§7, permanently).
nyxloom's ``_Serde.from_dict`` rejects unknown keys, so a literal superset could
never have been fed to ``GateResult.from_dict`` regardless — the consumer
cherry-picks keys. assay carries its own names.

`Outcome`, `ReasonCode`, `EXIT_CODES` and `REASON_CODES` are **imported** from
:mod:`assay.errors` and re-exported here (A-066). They are never redefined.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from types import MappingProxyType
from typing import Any, ClassVar, Iterable, Mapping

from .config import (
    ENFORCEMENTS,
    JUDGE_MODES,
    RIGOR_LEVELS,
    SCOPES,
    SNAPSHOT_SELECTIONS,
)
from .errors import EXIT_CODES, REASON_CODES, Outcome, ReasonCode
from .vocabulary import (
    INGESTED_OPERATOR_RE,
    MUTATION_OPERATORS,
    MUTATION_OPERATORS_BY_LANGUAGE,
    is_ingested_operator,
    operator_language,
)

__all__ = [
    "BRANCH_CAPABILITIES",
    "CLAIM_SOURCES",
    "EVIDENCE_SOURCES",
    "EXCLUSION_CAPABILITIES",
    "EXIT_CODES",
    "HELPER_ROLES",
    "JUDGE_ARTIFACT_KINDS",
    "JUDGE_DIGEST_ALGORITHMS",
    "JUDGE_MODES",
    "KILL_ATTRIBUTIONS",
    "MUTATION_BUCKETS",
    "MUTATION_PRODUCERS",
    "MUTATION_OPERATORS",
    "MUTATION_OPERATORS_BY_LANGUAGE",
    "LANE_RESOLVED_FIELDS",
    "COMMAND_TAIL_BYTES",
    "CLAIM_DETAIL_BYTES",
    "CANARY_AGGREGATIONS",
    "CANARY_DISPOSITIONS",
    "CANARY_NOT_ATTEMPTED_REASONS",
    "MIN_CANARY_TARGETS",
    "MAX_CANARY_TARGETS",
    "RED_FIRST_COMMIT_SOURCES",
    "REASON_CODES",
    "ROLLUP_PRECEDENCE",
    "SCHEMA_RESOURCE",
    "SNAPSHOT_SELECTIONS",
    "VERDICT_SCHEMA_VERSION",
    "CanaryAttempt",
    "CanaryResult",
    "Claim",
    "Coverage",
    "Evidence",
    "EvidenceDeclaration",
    "Helper",
    "JudgeProvenance",
    "Judgment",
    "JudgmentR1",
    "JudgmentR2",
    "JudgmentR3",
    "JudgmentR4",
    "RedFirstResult",
    "JudgmentResolved",
    "Mutation",
    "MutantOutcome",
    "MutationProducerTool",
    "SnapshotPolicy",
    "SourcePosition",
    "operator_language",
    "Outcome",
    "ReasonCode",
    "Verdict",
    "iso_utc",
    "load_schema",
    "rollup",
    "schema_text",
]

#: The VERDICT artifact's schema version. **Distinct** from the lane file's own
#: `schema_version` (A-065): two independently versioned contracts, and
#: conflating them would couple a lane-file format change to every emitted
#: artifact. Bumped 2 -> 3 (P16): a breaking shape change (new mandatory
#: `judgment.r1` whenever an R1 claim carries `coverage`), never a silent
#: coercion of a v2 artifact -- `Verdict.schema_version` still refuses
#: anything but the current value, so an old artifact is rejected with a
#: named diagnostic rather than silently upgraded.
#:
#: Bumped 3 -> 4 (P21): the ONE pre-adoption migration (A-157/A-170), batching
#: every already-known gap so no external consumer ever sees a second bump --
#: killed mutants carry byte-site identities, `candidate_count` bounds them,
#: `judgment.r2.max_mutants` records the declared cap, `canary.target` makes
#: `judgment.r3.target` witnessable (closing A-152/A-O18), and
#: `coverage.exclusion_capability` restores A-008's `None`-versus-empty
#: distinction (closing A-O16). Only ONE schema is active in a released build
#: (A-170): producers emit v4 only, and `assay verify` rejects v1-v3 with a
#: single version diagnostic rather than negotiating or upgrading.
#:
#: Bumped 4 -> 5 (P33/A-220): the artifact must be able to describe a language
#: that has mutation but no coverage. `language`/`source_roots`/`base` hoist
#: out of `judgment.r1` into a required `judgment.resolved` -- a v4 fix, not
#: an SQL accommodation, because `R0,R2` has been legal since A-192 and such
#: a lane recorded no language, no source roots and no comparison commit at
#: all; the operator vocabulary becomes language-qualified and closed per
#: language; `mutation` gains a fifth `equivalent` bucket paired with
#: `judgment.r2.equivalence_artifact`; `judgment.r2` gains a required
#: `kill_attribution`; and a new optional top-level `helpers` records what an
#: adapter actually invoked. Still exactly ONE active schema per build
#: (A-170): a v4 artifact is refused on its version, never upgraded in place.
#:
#: Bumped 6 -> 7 (B014, hard cut): failed and timed-out commands can retain
#: bounded stdout/stderr tails and dropped-byte counts. The fields are optional,
#: but their presence/absence contract is a new artifact dialect: v6 producers
#: never emit them, while this verifier reconstructs and bounds-checks them.
#:
#: Bumped 5 -> 6 (wave-1 §4/§5/§6, A-261, hard cut): branch coverage is
#: judged whenever the artifact reports it (A-258), which changes what PASS
#: means for an existing R1 lane -- precisely why this is a major bump and
#: not an additive one. `coverage.changed_executable` is renamed
#: `executable` (A-262: under the new whole-target mode the denominator is a
#: target's executable lines, which were not "changed" by anything).
#: `coverage` gains `branches_covered`/`branches_total`/`branch_capability`/
#: `missing_branch_lines`/`files_with_missing_branch_lines`. `judgment.r1`
#: gains required `mode`/`require_branch` and conditionally-required
#: `targets` (B005, A-260), and now survives two payload-free R1 terminals
#: -- `BRANCH_UNAVAILABLE`/`TARGET_NOT_MEASURED` -- widening the "present
#: iff a coverage payload" rule the same way A-183 already widened R2's
#: (A-264). `judgment.resolved.base` becomes conditional on R1's MODE, not
#: merely its presence: forbidden for a whole-target R1 with no R2.
#: `reason_code` gains three members: `UNCOVERED_BRANCHES`,
#: `BRANCH_UNAVAILABLE`, `TARGET_NOT_MEASURED`. A new top-level
#: `snapshot_policy` (B006(a)/A-269, `W1-CARVE-B006a-project-scope.md` §5)
#: records the lane-selected repository snapshot materialisation policy --
#: required whenever `declared_rigor` contains R1, R2 or R3, absent
#: otherwise; it replaces the withdrawn `isolation` object this wave's own
#: first design specified (A-269 supersedes A-266/A-267/A-268 in full). No
#: dual-version verifier, no compatibility shim (A-170's rule, restated):
#: producers emit v6 only, and `assay verify` refuses v5 exactly as it
#: refuses v4 today.
#:
#: **v7 -> v8 (B035/A-329, with B018/A-327 riding it).** `judgment.r2` gains
#: `mode` (required) and `targets`, mirroring `judgment.r1`'s pair. This is a
#: BUMP and not a widening for one mechanical reason, the same one B014's
#: 6->7 turned on: the packaged schema sets `additionalProperties: false` on
#: the `judgment.r2` object, so a consumer holding a released v7 copy would
#: reject any document carrying the new keys. With `r2.mode` on the wire the
#: `judgment.resolved.base` rule becomes enforceable for an `R0,R2` lane --
#: every SQL lane, dstdns's `cw2b_schema` by name -- which A-325 had to stop
#: enforcing entirely to make honest whole-target R2 artifacts producible.
#: The optional top-level `judge_provenance` (B018/A-327) is a pure widening
#: that needed no bump of its own and rides this one rather than shipping a
#: second. Same rule as every cut before it: no dual-version verifier, no
#: compatibility shim -- producers emit v8, and `assay verify` refuses v7
#: exactly as it refuses v6.
#:
#: **v8 -> v9 (the "producer" wave: B045, B046, B043, B041(b)).** One cut, four
#: items, because each of them puts a new key inside an object the packaged
#: schema closes with `additionalProperties: false` -- so any one of them alone
#: would already have been a BUMP, and four separate bumps would be four
#: releases' worth of consumer churn for one wave's worth of facts. What the
#: wire gains:
#:
#: * `judgment.r1.coverage_producer` (B045) -- WHICH tool wrote the coverage
#:   artifact, where one format has several producers that disagree about what
#:   its own fields mean. Declared, never sniffed (A-007).
#: * `judgment.r2.producer` (B046) -- `native` (assay's own engine chose the
#:   operators, generated the sites and ran them) or `ingested` (the lane's own
#:   argv ran a foreign mutation tool inside the private snapshot and assay
#:   judged the report it wrote). REQUIRED, so no document is ambiguous about
#:   which it is; the north-star's "never conflate tiers" is what makes this a
#:   wire field. With it come `producer_tool`, `survived_uncovered`,
#:   `discarded` and `lines_without_candidates` -- required together under
#:   `ingested`, forbidden under `native`. Symmetrically, `jobs`,
#:   `max_mutants`, `operators` and `equivalence_artifact` -- assay's OWN
#:   policy -- are required under `native` and FORBIDDEN under `ingested`,
#:   because assay chose none of them for a run it did not orchestrate and
#:   backfilling them from the foreign tool's configuration would put that
#:   tool's decisions on the wire under assay's name (the declared-vs-verified
#:   line A-230a already draws for `helpers[]`).
#: * `mutation_operator` gains an OPEN `^stryker:[A-Za-z0-9]+$` branch (B046):
#:   a namespace assay owns, not a language. A foreign tool's mutator names are
#:   DATA assay records, never a catalogue assay closes.
#: * `cwd_declared` (B043) -- the lane's declared working directory. Root-level
#:   and INDEPENDENTLY optional: deliberately NOT a member of
#:   `LANE_RESOLVED_FIELDS`, because a lane declaring no `cwd` records none,
#:   never `"."`.
#: * `snapshot_policy.link_paths` (B041(b)) -- the paths this lane symlinked in
#:   from the invoking checkout, so a verdict states plainly that its snapshot
#:   was not purely committed objects.
#:
#: Same rule as every cut before it: no dual-version verifier, no compatibility
#: shim (A-170) -- producers emit v9, and `assay verify` refuses v8 exactly as
#: it refuses v7.
#:
#: **v10 (Wave D, the integrity cut).** Six changes, one bump, because each of
#: them was independently "a wire field at the next schema cut" and cutting a
#: version per field is the mistake B007's sequencing note was written to
#: prevent:
#:
#: * `judgment.r2.fail_under` (B050/A-427) -- the mutation floor the document
#:   itself states, REQUIRED under `producer = "ingested"` and FORBIDDEN under
#:   `"native"`, so an ingested R2 PASS is re-derivable against the policy that
#:   actually decided it instead of against a constant the loader forced.
#: * `claim.detail` + `claim.detail_dropped_bytes` (B053/A-428) -- the refusing
#:   sentence on the wire, on NON-PASS claims only, bounded at
#:   `CLAIM_DETAIL_BYTES` with B014's truncation convention.
#:   Declared-not-verified (A-230a): assay copies it and never parses it.
#: * `PROVENANCE_UNVERIFIED` (B004/A-276/A-430) in the `NO_MEASUREMENT` set,
#:   plus the `evidence` narrowing it pays for: `source = "adjudicated"` now
#:   implies `verified_by_assay: false` in BOTH the model and the document.
#: * `judgment.r3.targets`/`aggregation` and `canary.attempts[]` (B007/A-432)
#:   -- an ORDERED, bounded canary target list with a CLOSED aggregation and a
#:   per-attempt payload. The singular `judgment.r3.target` and the flat
#:   `canary` body do NOT survive: a one-element `targets` array is every
#:   existing R3 lane's migration.
#: * `"R4"` in the rigor vocabulary, `judgment.r4` and the `red_first` claim
#:   payload (F015/M7, A-433 as amended by A-434) -- red-first
#:   (fail-before/pass-after) as the next rung of the ordered ladder. The wire
#:   shape lands here; the producer lands in phase 3, which is why
#:   `RED_FIRST_UNPROVEN` is RESERVED in the `FAIL` set now.
#:
#: Same rule again: producers emit v10, and `assay verify` refuses v9 exactly
#: as v9 refused v8.
VERDICT_SCHEMA_VERSION = 10

#: (P21/A-183) the closed R1 exclusion-capability vocabulary, restoring A-008's
#: distinction inside the artifact. `"unavailable"` means the coverage FORMAT
#: cannot express exclusions at all (`FileCoverage.excluded is None`);
#: `"reported"` means it can, and truthfully reported some or none. Without
#: this, a Go lane's structural silence and a Python lane's genuinely empty
#: exclusion set serialised identically, so a reader could not tell a clean
#: lane from a blind format (A-O16).
EXCLUSION_CAPABILITIES: tuple[str, ...] = ("reported", "unavailable")

#: (wave-1 §3.2/§4, A-257) the closed R1 branch-capability vocabulary,
#: mirroring :data:`EXCLUSION_CAPABILITIES` exactly -- a DIFFERENT
#: vocabulary about a different capability, kept as its own named constant
#: rather than reusing the tuple even though the two happen to share values
#: today. `"unavailable"` means the coverage FORMAT cannot express branch
#: arcs at all (`go-cover`, always); `"reported"` means it can, and
#: truthfully reported some or none.
BRANCH_CAPABILITIES: tuple[str, ...] = ("reported", "unavailable")

#: (P33/V5-4) the closed kill-attribution vocabulary. `declared` means the
#: run named the mechanism that refused each kill, via the lane's declared
#: `kill_signal_artifact`; `unattributed` means a killed mutant proves only
#: that the suite failed. Assay cannot observe a database's causality, so
#: this discriminator exists to make that limit visible in every artifact
#: rather than absent from all of them (A-220).
KILL_ATTRIBUTIONS: tuple[str, ...] = ("declared", "unattributed")

#: (B018/A-327) the one digest algorithm a judge identity may name. Recorded in
#: the artifact rather than assumed by the consumer: a digest whose algorithm is
#: implied silently changes meaning the day a second one is added, and CIU V8
#: compares this value against a digest IT resolved, from its own configuration.
JUDGE_DIGEST_ALGORITHMS: tuple[str, ...] = ("sha256",)

#: (B018/A-327) the closed set of build artifacts assay ships --
#: `gate/distribution/build_release.py` builds exactly these two, each with its
#: own `.sha256` sidecar. The KIND is recorded beside the digest because a
#: release publishes both files and their digests necessarily differ; a bare hex
#: string would leave a consumer unable to say which of the two it should be
#: comparing against.
JUDGE_ARTIFACT_KINDS: tuple[str, ...] = ("wheel", "zipapp")

#: (P33/V5-5) the closed `helpers[].role` vocabulary -- exactly the three
#: helper jobs that exist or are ruled (A-227): `statement-positions`
#: (A-217's Go oracle), `mutation-sites` (SQL's parser and P29's Go helper),
#: `executable-code` (Go's existing `has_executable_code`). A fourth role is
#: an additive enum change, not a version bump.
HELPER_ROLES: tuple[str, ...] = (
    "statement-positions",
    "mutation-sites",
    "executable-code",
)

#: (P33/V5-5, A-223c/A-227) what a verdict must ALSO carry for each helper
#: role to be recordable, phrased for the refusal message. `executable-code`
#: takes either because executability gates both changed-line classification
#: and mutation-site discovery.
HELPER_ROLE_REQUIREMENT: Mapping[str, str] = MappingProxyType(
    {
        "statement-positions": "an R1 claim carrying a coverage payload",
        "mutation-sites": "an R2 claim carrying a mutation payload",
        "executable-code": (
            "an R1 claim carrying coverage or an R2 claim carrying mutation"
        ),
    }
)

#: Where the shipped schema lives inside the installed package. Declared as
#: package data in `pyproject.toml`; without that it exists in the source tree
#: and vanishes on install, silently breaking A-029 for every consumer while
#: every in-tree test stays green.
SCHEMA_RESOURCE = "schemas/verdict.schema.json"

#: Computed rigor claims and external evidence are deliberately separate axes.
CLAIM_SOURCES: tuple[str, ...] = ("computed",)
EVIDENCE_SOURCES: tuple[str, ...] = ("adjudicated", "attested")

#: (P16 review) the two reason codes an R2 claim can only ever read off a
#: :class:`Mutation`'s own buckets — :func:`assay.mutation.judge_mutation`
#: reaches both solely on its ``mutation is not None`` branch, and
#: ``execute_command``'s baseline vocabulary contains neither. A claim
#: carrying one of them without the payload it was read from is therefore
#: unproducible, and is refused at construction rather than left for a
#: consumer to trust. See
#: :meth:`Claim._check_a_judged_status_carries_its_own_payload`.
#:
#: (P33/A-228) ``ALL_MUTANTS_EQUIVALENT`` joins them for exactly the same
#: reason: it is read off the ``equivalent``/``killed``/``survived`` buckets
#: and nowhere else, so a claim wearing it without the payload it was read
#: from is unproducible. Adding the terminal without adding it here would
#: have reopened, one bucket over, the forgery A-136 closed for
#: ``NO_MUTANTS``.
_MUTATION_ONLY_REASON_CODES: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.MUTANTS_SURVIVED,
        ReasonCode.NO_MUTANTS,
        ReasonCode.ALL_MUTANTS_EQUIVALENT,
    }
)

#: A-023, in order. `PASS` is not a member: it is what remains when no adverse
#: status is present, which is the same statement as "PASS only when every
#: declared claim passed".
ROLLUP_PRECEDENCE: tuple[Outcome, ...] = (
    Outcome.ERROR,
    Outcome.NO_MEASUREMENT,
    Outcome.BUDGET_EXCEEDED,
    Outcome.FAIL,
    Outcome.INCONCLUSIVE,
)

#: The fields that exist if and only if a lane actually resolved. All present or
#: all absent — see the module docstring on absent-vs-empty. `scope` and
#: `enforcement` joined this group in P16: both are static `Lane` attributes,
#: known the moment a lane loads, exactly like `argv_declared`/`env_declared` —
#: there is no honest intermediate state where a lane resolved but its own
#: declared scope/enforcement are unknown.
LANE_RESOLVED_FIELDS: tuple[str, ...] = (
    "declared_rigor",
    "declared_evidence",
    "argv_declared",
    "argv_appended",
    "argv_effective",
    "argv_modified",
    "env_declared",
    "env_effective",
    "scope",
    "enforcement",
)

#: B014: the maximum retained UTF-8 byte length of each command-output tail.
#: Duplicated deliberately from :mod:`assay.runner`: this module owns artifact
#: acceptance, and a model that could serialize an oversized tail would let a
#: forged verdict agree with itself.
COMMAND_TAIL_BYTES = 64 * 1024

#: (B053/DA-D2(c), A-428, schema v10) the maximum retained UTF-8 byte length of
#: `Claim.detail`. Two bounds exist deliberately and they are not the same
#: bound: JSON Schema's `maxLength` counts CHARACTERS, so the document carries
#: `maxLength: 2048` -- which every <=2048-BYTE UTF-8 string satisfies, so it
#: never rejects a legal document -- and this model plus `assay verify` then
#: complete it with the exact BYTE check, which is where DA-D2(c) puts the
#: bound anyway. Truncation keeps the HEAD, the opposite end from
#: :data:`COMMAND_TAIL_BYTES`: a command's output is most informative at its
#: END (the failure is last), while a refusal message assay COMPOSES is most
#: informative at its START (which artifact, which target, which declaration).
CLAIM_DETAIL_BYTES = 2048

#: (B007/DA-D8, A-432, schema v10) the closed bounds on an R3 lane's ordered
#: canary target list, spelled as a MIN_/MAX_ pair beside
#: `config.MIN_/MAX_MUTANTS`, the module convention. **Measured, not chosen by
#: taste** (DA-R17, 2026-09-02, through the shipped substrate): one canary
#: target costs ~2.76 s of materialisation (control 1.26 s + transform 1.50 s)
#: plus two full runs of the lane's command, and peak disk is ONE snapshot
#: because `run_isolated_canary`'s two contexts are sequential. 8 x 2.76 s =
#: 22.1 s = 7.4 % of the smallest budget any shipped worked example declares
#: (`5m`, `docs/DESIGN-GUIDE.md`), the largest round N whose materialisation
#: floor stays under a tenth of that budget.
MIN_CANARY_TARGETS = 1
MAX_CANARY_TARGETS = 8

#: (B007/A-432) the CLOSED aggregation vocabulary for a multi-target R3 lane.
#: `"any"` -- the claim PASSes as soon as one declared probe is caught, and the
#: first PASS SHORT-CIRCUITS. `"all"` -- every declared probe must be caught,
#: and every target is attempted even after a FAIL (DA-R19 affirms the
#: deliberate 2N bound: naming EVERY surviving probe is why a lane declares
#: several). No default: an aggregation assay invented is a policy no one
#: declared.
CANARY_AGGREGATIONS: tuple[str, ...] = ("any", "all")

#: (B007/A-432) the CLOSED "why this target was never attempted" vocabulary.
#: `"short_circuited"` -- under `any`, an earlier target already PASSed.
#: `"budget_exhausted"` -- the lane deadline had nothing left before this
#: target's control materialisation. `"earlier_target_terminal"` -- an earlier
#: attempt ended the claim (a refusal or an INCONCLUSIVE); the discriminating
#: detail is on THAT attempt, which is why one member suffices where two would
#: duplicate it.
CANARY_NOT_ATTEMPTED_REASONS: tuple[str, ...] = (
    "short_circuited",
    "budget_exhausted",
    "earlier_target_terminal",
)

#: (B007/A-432) whether a recorded canary attempt ran at all.
CANARY_DISPOSITIONS: tuple[str, ...] = ("attempted", "not_attempted")

#: (F015/A-433) where `judgment.r4.broken_commit` came from. Recorded because a
#: reader must be able to tell a lane that NAMED a commit from one that
#: inherited its resolved base, and re-resolving a base later can give a
#: different answer.
RED_FIRST_COMMIT_SOURCES: tuple[str, ...] = ("declared", "resolved_base")

# Kept deliberately identical in intent to `$defs/timestamp` in the schema. The
# duplication is real; the alternative is a model that can build an artifact its
# own shipped schema rejects, which is worse.
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$"
)


def schema_text() -> str:
    """The shipped JSON Schema, read from the packaged FILE.

    Resolved through :mod:`importlib.resources` rather than from a path derived
    from ``__file__``, so it works identically in a source tree and in an
    installed wheel — and so a test that reads it is really reading what a
    consumer would install.
    """
    return files(__package__).joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")


def load_schema() -> dict[str, Any]:
    """The shipped JSON Schema, parsed."""
    return json.loads(schema_text())


def supported_helper_roles(claims: Iterable["Claim"]) -> frozenset[str]:
    """Which :data:`HELPER_ROLES` *claims* can support a ``helpers`` entry
    for (P33/V5-5, A-223c/A-227).

    Public, and the SINGLE definition of the correspondence rule.
    :meth:`Verdict._check_helpers` validates with it, and
    :mod:`assay.runner` reads it when a cleanup-only failure voids the very
    claim a helper produced and its entry has to be voided with it (B047 item
    5). A second copy of the table in the producer would let the two
    disagree, and the disagreement's shape is a verdict the producer built
    and this class then refuses with a bare ``ValueError`` no caller catches
    -- the same one-join-not-two argument A-385/A-367 make for coverage keys.
    """
    materialised = tuple(claims)
    r1_judged = any(
        claim.rigor == "R1" and claim.coverage is not None for claim in materialised
    )
    r2_judged = any(
        claim.rigor == "R2" and claim.mutation is not None for claim in materialised
    )
    return frozenset(
        {
            role
            for role, satisfied in (
                ("statement-positions", r1_judged),
                ("mutation-sites", r2_judged),
                ("executable-code", r1_judged or r2_judged),
            )
            if satisfied
        }
    )


def iso_utc(moment: datetime) -> str:
    """Format *moment* as the artifact's timestamp spelling.

    Rejects a naive datetime rather than assuming UTC: an artifact whose times
    cannot be placed on a timeline is not evidence, and guessing the zone is the
    invention §4.2a forbids.
    """
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(f"timestamp {moment!r} is naive; an explicit offset is required")
    return moment.isoformat()


def rollup(statuses: Iterable[Outcome]) -> Outcome:
    """The verdict outcome implied by a set of claim statuses (A-023).

    ``ERROR > NO_MEASUREMENT > BUDGET_EXCEEDED > FAIL > INCONCLUSIVE``, and
    ``PASS`` only when every claim passed. ``ERROR`` and ``NO_MEASUREMENT``
    outrank ``FAIL`` because they invalidate everything computed beneath them;
    ``BUDGET_EXCEEDED`` outranks ``FAIL`` because a truncated run's finding may
    be partial.

    Raises on an empty input. A rollup over no claims is not a pass — returning
    ``PASS`` there would be a laundering default of exactly the kind this
    project exists to remove.
    """
    present = list(statuses)
    if not present:
        raise ValueError(
            "cannot roll up an empty set of claims; a verdict over no claim is "
            "not a PASS"
        )
    for outcome in ROLLUP_PRECEDENCE:
        if outcome in present:
            return outcome
    return Outcome.PASS


def _check_reason_code(
    outcome: Outcome, reason_code: ReasonCode | None, what: str
) -> None:
    """The A-022/A-051 pairing rule, applied identically to verdict and claim."""
    if outcome is Outcome.PASS:
        if reason_code is not None:
            raise ValueError(
                f"{what}: PASS carries no reason_code (got {reason_code}); a pass "
                f"has no cause to name, and it is OMITTED rather than null"
            )
        return
    if reason_code is None:
        raise ValueError(
            f"{what}: outcome {outcome} requires a reason_code; valid: "
            f"{sorted(REASON_CODES[outcome])}"
        )
    if reason_code not in REASON_CODES[outcome]:
        raise ValueError(
            f"{what}: reason_code {reason_code} is not valid for {outcome}; "
            f"valid: {sorted(REASON_CODES[outcome])}"
        )


#: (P21/A-182) the lowercase-hex SHA-256 spelling every mutant identity's
#: `replacement_sha256` must use. Uppercase is refused rather than folded:
#: the hash is an IDENTITY component, and two spellings of one hash would
#: make two records of the same site look like two different sites.
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: The hard product ceiling on candidate discovery (A-163). `max_mutants` is
#: declared in `1..10_000`, so a bounded observation is at most `max + 1`.
#: This is a defence against a malicious declared cap, not a policy knob.
MAX_CANDIDATE_CEILING = 10_001

#: (B012 remediation, N-2) The hard ceiling on a `--shard` COUNT -- a
#: distinct concept from :data:`MAX_CANDIDATE_CEILING` above, which bounds
#: how many candidate SITES discovery may observe (deliberately `max + 1` to
#: carry the "at least this many exist" sentinel). `JudgmentR2.shard_count`
#: below validates `1..10_000` with no `+ 1`, because a shard count has no
#: such sentinel; `select_mutation_shard`/`merge_mutation_shards` must use
#: the SAME bound so a value the CLI accepts cannot fail schema validation
#: three call sites later.
MAX_SHARD_COUNT = 10_000

#: (P33/V5-3) `Mutation`'s identity buckets, in wire order. FIVE since v5.
#: Named once rather than transcribed at each of the five sites that iterate
#: them: A-228's own root cause was a new bucket reaching some of the layers
#: that constrain the others and not the rest, and a literal list repeated
#: per call site is exactly how that happens again.
MUTATION_BUCKETS: tuple[str, ...] = (
    "killed",
    "survived",
    "crashed",
    "budget_exceeded",
    "equivalent",
)


def _check_wire_path(value: Any, what: str) -> None:
    """The normalized forward-slash path grammar every WIRE path in a v4
    artifact obeys (A-182): `MutantOutcome.path`, `CanaryResult.target` and
    `JudgmentR3.target`.

    Forward-slash components, none of them empty, `.`, or `..`; no leading or
    trailing slash; no backslash; no NUL. A path that still carries `..` is
    not an identity — `src/../p.py` and `p.py` name one file with two
    spellings, so an equality check between a payload target and its policy
    (O3) could be satisfied by two records that merely agree on a
    non-normalized spelling. Refusing the spelling at construction is what
    makes that equality mean what it says.

    Deliberately NOT applied to `judgment.resolved.source_roots` (P33/V5-1
    moved it there from `judgment.r1`): a source root is
    a declared DIRECTORY spelling, and `"."` is both legal and common there
    (a Go module rooted at its own repository top declares exactly that).
    Widening this grammar to cover it would reject a truthful policy record.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} must be a non-empty string, got {value!r}")
    if "\\" in value:
        raise ValueError(
            f"{what} {value!r} contains a backslash; wire paths are "
            f"forward-slash separated regardless of the producing platform"
        )
    if "\x00" in value:
        raise ValueError(f"{what} {value!r} contains a NUL byte")
    if value.startswith("/") or value.endswith("/"):
        raise ValueError(
            f"{what} {value!r} must be relative and must not end in a "
            f"separator"
        )
    for part in value.split("/"):
        if not part or part in (".", ".."):
            raise ValueError(
                f"{what} {value!r} is not normalized: component {part!r} is "
                f"empty, '.', or '..'"
            )


def _instant(value: str, what: str) -> datetime:
    """*value* as a real, offset-aware instant (A-182).

    The two timestamps are compared as INSTANTS, never as strings. The
    canonical combined document is the worked counter-example: it starts at
    `12:00+01:00` and ends at `11:01+00:00`, so lexical order says the
    interval runs backwards while UTC order says it is a valid 1-minute
    window. A string comparison would reject a correct artifact.

    The regex that guards the spelling one level up cannot catch an
    impossible CALENDAR value (month 13, day 32) -- it only checks digit
    shape -- so parsing is what closes that gap.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{what} {value!r} is not a real timestamp: {exc}") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(f"{what} {value!r} carries no usable UTC offset")
    return parsed


def _check_line_location_mapping(value: Any, what: str) -> None:
    """The shape ``missing_lines`` and (P07) ``unclassified_lines`` both
    share: ``Mapping[str, frozenset[int]]``, non-empty-string keys, always a
    non-empty ``frozenset`` of positive line numbers — "a file contributing
    none is omitted from the mapping entirely", never present with an empty
    set. Factored out so P07's new field is held to the identical
    construction-time discipline as P05's, rather than a hand-copied
    near-duplicate that could quietly drift from it (A-096's "exact same...
    discipline").
    """
    if not isinstance(value, Mapping):
        raise ValueError(f"{what} must be a mapping, got {value!r}")
    for path, lines in value.items():
        if not isinstance(path, str) or not path:
            raise ValueError(f"{what} key must be a non-empty string, got {path!r}")
        if not isinstance(lines, frozenset) or not lines:
            raise ValueError(
                f"{what}[{path!r}] must be a non-empty frozenset of line "
                f"numbers, got {lines!r} — a file contributing none is "
                f"omitted from the mapping entirely"
            )
        for line in lines:
            if isinstance(line, bool) or not isinstance(line, int) or line < 1:
                raise ValueError(
                    f"{what}[{path!r}] must contain only positive line "
                    f"numbers, got {line!r}"
                )


def _check_file_tuple(value: Any, what: str) -> None:
    """The shape ``files_missing_coverage`` and (P07)
    ``files_with_unclassified_lines`` both share: a sorted tuple of unique,
    non-empty path strings. Factored out for the same reason
    :func:`_check_line_location_mapping` is.
    """
    if not isinstance(value, tuple):
        raise ValueError(f"{what} must be a tuple, got {value!r}")
    for path in value:
        if not isinstance(path, str) or not path:
            raise ValueError(f"{what} entries must be non-empty strings, got {path!r}")
    if len(set(value)) != len(value):
        raise ValueError(f"{what} contains a duplicate: {list(value)}")
    if list(value) != sorted(value):
        raise ValueError(f"{what} must be sorted, got {list(value)}")


@dataclass(frozen=True, kw_only=True)
class Coverage:
    """The R1 claim payload: changed-line coverage, and why its denominator is
    what it is.

    ``considered`` is srdm's fix and the reason a legitimate 0/0 pass is not
    textually identical to a broken measurement (A-026): *"3 changed file(s)
    under the roots, none contributing executable non-test lines (0/0)"* is a
    different sentence from *"0/0 covered (100.0%)"*. Identical strings for the
    two cases is what started this whole project.

    ``missing_lines`` and ``files_missing_coverage`` are P05's own additive
    pair (A-096): the R1 claim's totals say WHETHER the change passed;
    these say WHERE it did not. Both are ALWAYS present — possibly empty,
    never conditionally omitted — the same "absent means unknowable, empty
    means known-and-empty" discipline A-025 already applies to the coverage
    block as a whole, one level down inside it.

    ``unclassified_lines`` and ``files_with_unclassified_lines`` are P07's
    own additive THIRD pair (A-096), following the identical discipline: a
    changed line whose enclosing multi-line statement could not be
    confidently attributed — overlapping/malformed spans, or a genuinely
    untracked enclosing statement — renders ``FAIL``/``UNCLASSIFIED_LINES``
    (A-100) and names exactly where. Both default to empty so a producer
    that has not been updated to pass them through (P07 does not touch
    :mod:`assay.runner`, which is outside its ``scope.touch``) continues to
    build a valid, schema-conformant ``Coverage`` — always present in the
    OUTPUT (:meth:`to_dict` always emits both keys), never conditionally
    omitted, exactly like ``missing_lines``/``files_missing_coverage``.
    """

    covered: int
    #: (wave-1 §6, A-262) renamed from ``changed_executable``: under B005's
    #: whole-target mode the denominator is a target's executable lines,
    #: which were not "changed" by anything, so the old name would put a
    #: false statement on the wire. The rename is taken now because a major
    #: bump is the only honest moment for it.
    executable: int
    #: (wave-1 §6, A-263) the COMBINED line+branch percentage: ``(covered +
    #: branches_covered) / (executable + branches_total)`` -- exactly
    #: ``coverage.py``'s own ``summary.percent_covered`` under
    #: ``--cov-branch``. ``covered``/``executable`` stay line-only; the
    #: branch side gets its own two integers below, so an independent
    #: consumer can still re-derive ``pct`` from the payload alone. When
    #: ``branch_capability`` is ``"unavailable"``, ``branches_total`` is 0
    #: and the formula degenerates to today's line-only value with no
    #: special case.
    pct: float
    #: changed files under the source roots that were considered at all
    #: (whole-target mode: the number of declared TARGETS judged, §5 rule 6)
    considered: int
    #: (P21/A-183) whether the coverage FORMAT could report exclusions at
    #: all -- `"reported"` or `"unavailable"`, never inferred from whether
    #: :attr:`excluded_lines` happens to be empty. Required, with no
    #: default: it is a fact derived from the parsed profile (every measured
    #: record agreeing on `excluded is None`), and a default here would be
    #: exactly the shadowing default AGENTS.md 4.2a forbids -- it would
    #: silently re-collapse A-008's distinction the moment a producer forgot
    #: to pass it.
    exclusion_capability: str
    #: changed, executable, non-excluded lines that were not executed, keyed
    #: by path — same shape as :attr:`assay.diff.AddedLines.by_file`: a file
    #: contributing none is ABSENT, never present with an empty frozenset.
    missing_lines: Mapping[str, frozenset[int]]
    #: changed files under the source roots contributing executable lines
    #: with no entry at all in the coverage artifact, sorted.
    files_missing_coverage: tuple[str, ...]
    #: (P07) changed lines whose multi-line-statement attribution could not
    #: confidently resolve them — same shape as :attr:`missing_lines`: a
    #: file contributing none is ABSENT, never present with an empty
    #: frozenset. Non-empty renders the claim ``FAIL``/``UNCLASSIFIED_LINES``
    #: (A-100).
    unclassified_lines: Mapping[str, frozenset[int]] = MappingProxyType({})
    #: (P07) paths appearing in :attr:`unclassified_lines`, sorted — the
    #: same shape :attr:`files_missing_coverage` already established.
    files_with_unclassified_lines: tuple[str, ...] = ()
    #: (P16) changed, considered lines the coverage artifact classifies
    #: EXCLUDED (``pragma: no cover`` and its format-specific equivalents),
    #: keyed exactly like :attr:`missing_lines` — a file contributing none
    #: is ABSENT, never present with an empty frozenset. Always present
    #: (possibly empty), the same "empty means known-and-empty" discipline
    #: A-025 already applies one level up: an artifact that hides which
    #: lines were excluded is exactly sol finding 2's "an independent
    #: consumer cannot re-derive whether an R1 status was correct" gap —
    #: without this an ``allow_excluded=False`` lane's ``FAIL``/
    #: ``EXCLUDED_LINES`` claim could not be independently re-checked at
    #: all, only trusted.
    excluded_lines: Mapping[str, frozenset[int]] = MappingProxyType({})
    #: (P16) paths appearing in :attr:`excluded_lines`, sorted — the same
    #: "always present, possibly empty, sorted" shape
    #: :attr:`files_missing_coverage`/:attr:`files_with_unclassified_lines`
    #: already established.
    files_with_excluded_lines: tuple[str, ...] = ()
    #: (wave-1 §6, A-257/A-263) branch arcs COVERED across every branch
    #: source line this claim's mode counted. Zero when
    #: :attr:`branch_capability` is ``"unavailable"``, with no special case
    #: (the ``allOf`` in the shipped schema states the same rule, mirroring
    #: :meth:`_check_capability_agrees_with_detail`'s exclusion analogue).
    branches_covered: int = 0
    #: (wave-1 §6) branch arcs this claim's mode counted in total. Zero when
    #: :attr:`branch_capability` is ``"unavailable"``.
    branches_total: int = 0
    #: (wave-1 §6, A-257) whether the coverage FORMAT could report branch
    #: arcs at all -- ``"reported"`` or ``"unavailable"``, never inferred
    #: from whether :attr:`branches_total` happens to be zero. Defaults to
    #: ``"unavailable"`` with zero arcs so every producer written before
    #: this wave continues to build a valid, schema-conformant
    #: :class:`Coverage` (the identical additive-field discipline P07/P16
    #: already established for :attr:`unclassified_lines`/
    #: :attr:`excluded_lines`) -- but the OUTPUT always carries all five
    #: branch fields (:meth:`to_dict` always emits them), never
    #: conditionally omitted.
    branch_capability: str = "unavailable"
    #: (wave-1 §6) counted, branch-source lines with at least one uncovered
    #: arc, keyed by path -- same "absent means none" shape as
    #: :attr:`missing_lines`. A file contributing none is absent, never
    #: present with an empty frozenset.
    missing_branch_lines: Mapping[str, frozenset[int]] = MappingProxyType({})
    #: (wave-1 §6) paths appearing in :attr:`missing_branch_lines`, sorted --
    #: the same "always present, possibly empty, sorted" discipline every
    #: other ``files_with_*`` summary in this class already keeps.
    files_with_missing_branch_lines: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("covered", "executable", "considered"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"coverage.{name} must be an integer, got {value!r}")
            if value < 0:
                raise ValueError(f"coverage.{name} must not be negative, got {value}")
        if isinstance(self.pct, bool) or not isinstance(self.pct, (int, float)):
            raise ValueError(f"coverage.pct must be a number, got {self.pct!r}")
        if not 0.0 <= float(self.pct) <= 100.0:
            raise ValueError(
                f"coverage.pct must be a percentage between 0 and 100, got {self.pct}"
            )
        if self.covered > self.executable:
            raise ValueError(
                f"coverage.covered ({self.covered}) exceeds executable "
                f"({self.executable})"
            )
        if self.exclusion_capability not in EXCLUSION_CAPABILITIES:
            raise ValueError(
                f"coverage.exclusion_capability must be one of "
                f"{list(EXCLUSION_CAPABILITIES)}, got "
                f"{self.exclusion_capability!r}"
            )
        if self.branch_capability not in BRANCH_CAPABILITIES:
            raise ValueError(
                f"coverage.branch_capability must be one of "
                f"{list(BRANCH_CAPABILITIES)}, got {self.branch_capability!r}"
            )
        if self.branches_covered > self.branches_total:
            raise ValueError(
                f"coverage.branches_covered ({self.branches_covered}) exceeds "
                f"branches_total ({self.branches_total})"
            )
        _check_line_location_mapping(self.missing_lines, "coverage.missing_lines")
        _check_file_tuple(self.files_missing_coverage, "coverage.files_missing_coverage")
        _check_line_location_mapping(
            self.unclassified_lines, "coverage.unclassified_lines"
        )
        _check_file_tuple(
            self.files_with_unclassified_lines,
            "coverage.files_with_unclassified_lines",
        )
        _check_line_location_mapping(self.excluded_lines, "coverage.excluded_lines")
        _check_file_tuple(
            self.files_with_excluded_lines, "coverage.files_with_excluded_lines"
        )
        _check_line_location_mapping(
            self.missing_branch_lines, "coverage.missing_branch_lines"
        )
        _check_file_tuple(
            self.files_with_missing_branch_lines,
            "coverage.files_with_missing_branch_lines",
        )

        # P16 (sol finding 2): the arithmetic no earlier package enforced —
        # a `Coverage` reporting `PASS` at 0% simply disagreed with its own
        # `covered`/`executable`, and construction accepted it. `pct` is
        # DERIVED, never independently supplied. wave-1/A-263: the
        # denominator/numerator are now the COMBINED line+branch values.
        combined_denominator = self.executable + self.branches_total
        expected_pct = (
            100.0
            if combined_denominator == 0
            else 100.0 * (self.covered + self.branches_covered) / combined_denominator
        )
        if abs(float(self.pct) - expected_pct) > 1e-9:
            raise ValueError(
                f"coverage.pct {self.pct} does not agree with "
                f"(covered + branches_covered)/(executable + branches_total) "
                f"({self.covered}+{self.branches_covered})/"
                f"({self.executable}+{self.branches_total}) = {expected_pct}); "
                f"pct is derived from those four fields, never independently "
                f"supplied"
            )
        total_missing = sum(len(lines) for lines in self.missing_lines.values())
        if total_missing != self.executable - self.covered:
            raise ValueError(
                f"coverage.missing_lines names {total_missing} line(s) total, "
                f"but executable - covered = "
                f"{self.executable - self.covered}; the per-file "
                f"detail must sum to the summary"
            )
        self._check_buckets_pairwise_disjoint()
        self._check_summaries_name_their_own_detail()
        self._check_capability_agrees_with_detail()
        self._check_branch_capability_agrees_with_detail()

    def _check_capability_agrees_with_detail(self) -> None:
        """(P21/A-183) `"unavailable"` carries NO exclusion detail.

        A format that structurally cannot report exclusions cannot also have
        reported some, so an artifact claiming both is self-contradictory
        rather than merely odd. The converse is deliberately NOT a rule:
        `"reported"` with an empty mapping is the truthful record of a
        capable format that found nothing to exclude, and forbidding it
        would re-collapse exactly the distinction A-008 exists to keep.
        """
        if self.exclusion_capability != "unavailable":
            return
        if self.excluded_lines or self.files_with_excluded_lines:
            raise ValueError(
                f"coverage.exclusion_capability is 'unavailable' but the "
                f"payload names excluded lines "
                f"{sorted(self.excluded_lines)}/"
                f"{list(self.files_with_excluded_lines)}; a format that "
                f"cannot report exclusions cannot have reported any"
            )

    def _check_branch_capability_agrees_with_detail(self) -> None:
        """(wave-1 §6, A-257) `"unavailable"` carries no branch detail at
        all -- `branches_total`/`branches_covered` are zero and
        `missing_branch_lines`/`files_with_missing_branch_lines` are empty.
        The converse is deliberately NOT a rule (the schema's own `allOf`
        states the same, one-directional): `"reported"` with zero arcs is a
        capable format that truthfully found none on the lines this claim
        counted, and forbidding it would re-collapse the distinction A-008
        keeps -- the exact reasoning
        :meth:`_check_capability_agrees_with_detail` already gives for
        exclusions, one field over.
        """
        if self.branch_capability != "unavailable":
            return
        if (
            self.branches_total
            or self.branches_covered
            or self.missing_branch_lines
            or self.files_with_missing_branch_lines
        ):
            raise ValueError(
                f"coverage.branch_capability is 'unavailable' but the "
                f"payload names branch data (branches_total="
                f"{self.branches_total}, branches_covered="
                f"{self.branches_covered}, missing_branch_lines="
                f"{sorted(self.missing_branch_lines)}); a format that cannot "
                f"report branch arcs cannot have reported any"
            )

    def _check_summaries_name_their_own_detail(self) -> None:
        """Each ``files_*`` summary agrees with the line-level mapping it
        summarises (P16's own work item 3: "missing/excluded/unclassified
        identities agree with their summary fields").

        Two different relations, because the two kinds of summary mean two
        different things:

        * :attr:`files_with_unclassified_lines` and
          :attr:`files_with_excluded_lines` are documented as *"paths
          appearing in* [the mapping]*, sorted"* — an EQUALITY, and
          :mod:`assay.evaluate` builds both by literally sorting the
          mapping it just filled.
        * :attr:`files_missing_coverage` is NOT the keys of
          :attr:`missing_lines`: it names only the considered files with no
          entry at all in the coverage artifact, whose changed lines are
          then all recorded as missing. Every such file therefore appears in
          :attr:`missing_lines` — a CONTAINMENT — while a file that does
          have an artifact entry can contribute missing lines without
          belonging here.

        Without this, a summary could name a file the detail never mentions,
        or stay empty while the detail names several: a consumer reading
        "which files have a problem" (the whole reason A-096 added these
        pairs) would be answered by a field nothing bound to the evidence.
        """
        for mapping, mapping_name, summary, summary_name in (
            (
                self.unclassified_lines,
                "unclassified_lines",
                self.files_with_unclassified_lines,
                "files_with_unclassified_lines",
            ),
            (
                self.excluded_lines,
                "excluded_lines",
                self.files_with_excluded_lines,
                "files_with_excluded_lines",
            ),
            (
                self.missing_branch_lines,
                "missing_branch_lines",
                self.files_with_missing_branch_lines,
                "files_with_missing_branch_lines",
            ),
        ):
            expected = tuple(sorted(mapping))
            if summary != expected:
                raise ValueError(
                    f"coverage.{summary_name} {list(summary)} does not name "
                    f"exactly the paths in coverage.{mapping_name} "
                    f"{list(expected)}; the summary is derived from the "
                    f"detail, never supplied independently of it"
                )
        stray = sorted(set(self.files_missing_coverage) - set(self.missing_lines))
        if stray:
            raise ValueError(
                f"coverage.files_missing_coverage names {stray}, which "
                f"contribute no line to coverage.missing_lines; a file with "
                f"no coverage-artifact entry has every changed line recorded "
                f"as missing"
            )

    def _check_buckets_pairwise_disjoint(self) -> None:
        """A changed line has exactly one classification (P15's own
        ``FileCoverage`` invariant, restated one level up at the artifact):
        it cannot be simultaneously ``missing``, ``excluded`` and
        ``unclassified`` for the same file. Real producer output already
        satisfies this by construction (:mod:`assay.evaluate` derives all
        three from one already-disjoint partition); this is the defensive,
        construction-time check that makes a HAND-BUILT contradictory
        artifact impossible to instantiate, matching A-135's binding
        instruction for this package's own contradictory fixtures.
        """
        pairs = (
            ("missing_lines", self.missing_lines, "excluded_lines", self.excluded_lines),
            ("missing_lines", self.missing_lines, "unclassified_lines", self.unclassified_lines),
            ("excluded_lines", self.excluded_lines, "unclassified_lines", self.unclassified_lines),
        )
        for left_name, left, right_name, right in pairs:
            for path in set(left) & set(right):
                overlap = left[path] & right[path]
                if overlap:
                    raise ValueError(
                        f"coverage[{path!r}]: {left_name} and {right_name} "
                        f"both name line(s) {sorted(overlap)} — a changed "
                        f"line has exactly one classification"
                    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "covered": self.covered,
            "executable": self.executable,
            "pct": float(self.pct),
            "considered": self.considered,
            "exclusion_capability": self.exclusion_capability,
            "missing_lines": {
                path: sorted(lines)
                for path, lines in sorted(self.missing_lines.items())
            },
            "files_missing_coverage": list(self.files_missing_coverage),
            "unclassified_lines": {
                path: sorted(lines)
                for path, lines in sorted(self.unclassified_lines.items())
            },
            "files_with_unclassified_lines": list(self.files_with_unclassified_lines),
            "excluded_lines": {
                path: sorted(lines)
                for path, lines in sorted(self.excluded_lines.items())
            },
            "files_with_excluded_lines": list(self.files_with_excluded_lines),
            "branches_covered": self.branches_covered,
            "branches_total": self.branches_total,
            "branch_capability": self.branch_capability,
            "missing_branch_lines": {
                path: sorted(lines)
                for path, lines in sorted(self.missing_branch_lines.items())
            },
            "files_with_missing_branch_lines": list(
                self.files_with_missing_branch_lines
            ),
        }


@dataclass(frozen=True, kw_only=True)
class CanaryAttempt:
    """ONE canary target's own record (B007/A-432, schema v10): the evidence
    a cause-sensitive canary produces for a single declared target, carried
    separately from the JUDGEMENT :mod:`assay.canary` derives from it — the
    identical split :class:`Coverage` already keeps from :class:`Claim` (this
    is its exact construction-time-validation template, restated for canary's
    own named fields, O2).

    Up to schema v9 this class WAS the R3 payload and carried the
    ``mechanism`` as well. At v10 the payload became :class:`CanaryResult`
    = ``{mechanism, attempts}``: the mechanism is one policy for the whole
    lane (it is what ``judgment.r3.mechanism`` records and what
    :meth:`Verdict._check_judgment_matches_claims` matches against), while
    everything below is per TARGET.

    An attempt is either ``"attempted"`` — it ran, and carries the run
    fields below — or ``"not_attempted"``, which carries NO run fields and a
    :attr:`not_attempted_reason` from the closed
    :data:`CANARY_NOT_ATTEMPTED_REASONS` vocabulary. The two dispositions are
    a wire fact rather than an inference from absent fields, because "this
    probe was never tried" and "this probe was tried and produced nothing"
    are exactly the two statements a multi-target R3 claim must keep apart.

    A known-good CONTROL is run through the real pipeline and MUST have
    produced :attr:`control_outcome`; a deliberately minimal, known-bad
    TRANSFORM of the same input is then run the same way, producing
    :attr:`transformed_outcome` — unless the transform itself was malformed
    or a no-op (O3), in which case :attr:`transformed_outcome` (and
    :attr:`observed_reason_code`) are ``None``: the transformed half was
    never genuinely run, so there is nothing to report an outcome FOR.
    :attr:`expected_reason_code` is the SPECIFIC reason the mechanism is
    supposed to produce (never merely "any failure", O1's own negative);
    :attr:`observed_reason_code` is what the real pipeline actually
    rendered. :mod:`assay.canary`'s ``judge_canary`` compares the two.
    """

    #: (P21/A-152/A-O18) the project-relative file the transform was applied
    #: to, in the normalized wire-path grammar. Its whole reason to exist is
    #: to make ``judgment.r3.targets`` independently witnessable:
    #: :meth:`Verdict._check_judgment_matches_claims` requires the attempts
    #: and the declared targets to be PAIRWISE EQUAL IN ORDER (B007/A-432
    #: generalises P21's single-target equality), so a policy naming one file
    #: while the canary ran against another is now unconstructible. Under
    #: schema v3 nothing in the artifact could witness ``target`` at all,
    #: which A-152 recorded as an accepted gap explicitly waiting for this
    #: migration.
    #:
    #: :attr:`description` remains prose and is never a parseable identity
    #: channel -- inferring the target by scraping it out of the description
    #: is the "a rule that looks like verification and is not" shape A-152
    #: already rejected.
    target: str
    #: Human-readable account of the concrete transform attempted, or of WHY
    #: nothing could be built (a malformed mechanism, or a no-op) — from the
    #: adapter's own ``inject_*``'s ``(text, description)`` return, never
    #: parsed back apart by any caller. On a ``"not_attempted"`` entry it
    #: still names the transform that WOULD have been applied — it is the
    #: mechanism's own account of the target, not a run field.
    description: str
    #: (B007/A-432) ``"attempted"`` or ``"not_attempted"`` — see
    #: :data:`CANARY_DISPOSITIONS`. Required on every attempt, and the
    #: discriminator every rule below forks on.
    disposition: str = "attempted"
    #: (B007/A-432) required under ``"not_attempted"``, FORBIDDEN under
    #: ``"attempted"``; a member of :data:`CANARY_NOT_ATTEMPTED_REASONS`.
    not_attempted_reason: str | None = None
    #: Required under ``"attempted"`` (it is the run that must have happened
    #: for anything else here to mean something) and FORBIDDEN under
    #: ``"not_attempted"``: B007/A-432 moved it out of the unconditionally
    #: required set, because a probe that was never tried has no control run
    #: to report.
    control_outcome: Outcome | None = None
    #: ``None`` exactly when the transformed half was never genuinely run
    #: (O3's two INCONCLUSIVE causes: a malformed/unrecognised mechanism, or
    #: a transform that changed nothing to judge).
    transformed_outcome: Outcome | None = None
    #: The FAIL-shaped reason the mechanism is supposed to produce, or
    #: ``None`` when no expectation could be derived (an unrecognised
    #: mechanism).
    expected_reason_code: ReasonCode | None = None
    #: What the real pipeline actually rendered for the transformed half, or
    #: ``None`` when it was never run, or when it PASSED (a PASS has no
    #: cause to name, mirroring :class:`Claim`'s own A-051 pairing rule).
    observed_reason_code: ReasonCode | None = None

    def __post_init__(self) -> None:
        _check_wire_path(self.target, "canary.attempts[].target")
        if not isinstance(self.description, str) or not self.description:
            raise ValueError(
                f"canary.attempts[].description must be a non-empty string, "
                f"got {self.description!r}"
            )
        if self.disposition not in CANARY_DISPOSITIONS:
            raise ValueError(
                f"canary.attempts[].disposition must be one of "
                f"{list(CANARY_DISPOSITIONS)}, got {self.disposition!r}"
            )
        if self.disposition == "not_attempted":
            if self.not_attempted_reason not in CANARY_NOT_ATTEMPTED_REASONS:
                raise ValueError(
                    f"canary.attempts[{self.target}]: a not_attempted entry "
                    f"requires a not_attempted_reason from "
                    f"{list(CANARY_NOT_ATTEMPTED_REASONS)}, got "
                    f"{self.not_attempted_reason!r} -- an open string is a "
                    f"vocabulary nobody can check (A-432)"
                )
            run_fields = (
                ("control_outcome", self.control_outcome),
                ("transformed_outcome", self.transformed_outcome),
                ("expected_reason_code", self.expected_reason_code),
                ("observed_reason_code", self.observed_reason_code),
            )
            present = [name for name, value in run_fields if value is not None]
            if present:
                raise ValueError(
                    f"canary.attempts[{self.target}]: a not_attempted entry "
                    f"carries no run fields, but {', '.join(present)} "
                    f"{'is' if len(present) == 1 else 'are'} present -- a "
                    f"probe that was never tried reports no run"
                )
            return
        if self.not_attempted_reason is not None:
            raise ValueError(
                f"canary.attempts[{self.target}]: not_attempted_reason "
                f"{self.not_attempted_reason!r} on an attempted entry -- the "
                f"probe ran, so there is no reason it was skipped"
            )
        if not isinstance(self.control_outcome, Outcome):
            raise ValueError(
                f"canary.attempts[].control_outcome must be an Outcome on an "
                f"attempted entry, got {self.control_outcome!r}"
            )
        if self.transformed_outcome is not None and not isinstance(
            self.transformed_outcome, Outcome
        ):
            raise ValueError(
                f"canary.transformed_outcome must be an Outcome or None, got "
                f"{self.transformed_outcome!r}"
            )
        if self.expected_reason_code is not None:
            if self.expected_reason_code not in REASON_CODES[Outcome.FAIL]:
                raise ValueError(
                    f"canary.expected_reason_code must be a FAIL reason code "
                    f"or None (a canary always expects a SPECIFIC failure), "
                    f"got {self.expected_reason_code!r}"
                )
        if self.transformed_outcome is None:
            if self.observed_reason_code is not None:
                raise ValueError(
                    "canary.observed_reason_code requires a transformed_outcome "
                    "— the transformed half was never run"
                )
        elif self.transformed_outcome is Outcome.PASS:
            if self.observed_reason_code is not None:
                raise ValueError(
                    "canary.observed_reason_code must be omitted when "
                    "transformed_outcome is PASS — a pass has no cause to name"
                )
        else:
            if self.observed_reason_code is None:
                raise ValueError(
                    f"canary.observed_reason_code is required when "
                    f"transformed_outcome is {self.transformed_outcome} — "
                    f"every non-PASS outcome names its cause"
                )
            if self.observed_reason_code not in REASON_CODES[self.transformed_outcome]:
                raise ValueError(
                    f"canary.observed_reason_code {self.observed_reason_code} "
                    f"is not valid for transformed_outcome "
                    f"{self.transformed_outcome}; valid: "
                    f"{sorted(REASON_CODES[self.transformed_outcome])}"
                )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target": self.target,
            "description": self.description,
            "disposition": self.disposition,
        }
        if self.not_attempted_reason is not None:
            payload["not_attempted_reason"] = self.not_attempted_reason
        # Absent means never run/unknowable; never null (mirrors A-025/A-051).
        if self.control_outcome is not None:
            payload["control_outcome"] = self.control_outcome.value
        if self.transformed_outcome is not None:
            payload["transformed_outcome"] = self.transformed_outcome.value
        if self.expected_reason_code is not None:
            payload["expected_reason_code"] = self.expected_reason_code.value
        if self.observed_reason_code is not None:
            payload["observed_reason_code"] = self.observed_reason_code.value
        return payload


@dataclass(frozen=True, kw_only=True)
class CanaryResult:
    """The R3 claim payload at schema v10 (B007/A-432): one ``mechanism`` and
    an ORDERED, bounded array of :class:`CanaryAttempt` records, one per
    declared canary target, in the order the lane declared them.

    Up to v9 this was one flat record for one target. A lane that wants to
    prove several probes are caught had to declare several lanes, and B007
    filed that; the migration for every existing single-target lane is a
    one-element :attr:`attempts` array, which is a spelling normalisation of
    the kind ``wire_path`` already performs, not a value assay chose.

    The bound is :data:`MAX_CANARY_TARGETS`, derived from a measurement and
    not from taste — see that constant. The judgement over the array (the
    ``aggregation``, the short-circuit bookkeeping) belongs to
    :mod:`assay.canary`, exactly as the single-target judgement always did;
    this class owns only what a well-formed record looks like.
    """

    #: A short, stable identity for the transform attempted (e.g.
    #: ``"import-break"``/``"uncovered-line"``) — NOT restricted to a closed
    #: set here, because a malformed/unrecognised mechanism name must still
    #: be representable (O3's own "malformed transform" case names the
    #: mechanism it could not resolve). ONE per lane:
    #: :meth:`Verdict._check_judgment_matches_claims` requires it to equal
    #: ``judgment.r3.mechanism``.
    mechanism: str
    attempts: tuple[CanaryAttempt, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.mechanism, str) or not self.mechanism:
            raise ValueError(
                f"canary.mechanism must be a non-empty string, got "
                f"{self.mechanism!r}"
            )
        if not isinstance(self.attempts, tuple) or not all(
            isinstance(attempt, CanaryAttempt) for attempt in self.attempts
        ):
            raise ValueError(
                f"canary.attempts must be a tuple of CanaryAttempt, got "
                f"{self.attempts!r}"
            )
        if not MIN_CANARY_TARGETS <= len(self.attempts) <= MAX_CANARY_TARGETS:
            raise ValueError(
                f"canary.attempts records {len(self.attempts)} attempt(s); a "
                f"canary payload carries between {MIN_CANARY_TARGETS} and "
                f"{MAX_CANARY_TARGETS} (A-432: the bound is the measured "
                f"materialisation cost of a target, ~2.76 s, against the "
                f"smallest documented lane budget)"
            )
        targets = [attempt.target for attempt in self.attempts]
        duplicates = sorted({t for t in targets if targets.count(t) > 1})
        if duplicates:
            raise ValueError(
                f"canary.attempts records target(s) {duplicates} more than "
                f"once -- one attempt per declared target, and two records "
                f"for one target cannot both be that target's result"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


@dataclass(frozen=True, kw_only=True)
class MutantOutcome:
    """One mutant's identity, projected for the R2 artifact (A-116): the
    lightweight subset of :class:`~assay.mutation.Mutant`'s own identity a
    reader needs to find and fix a surviving/crashed/budget-stopped site,
    plus the external *path* context a :class:`~assay.mutation.MutationSite`
    itself has no field for (one ``generate_mutation_sites`` call is scoped
    to a single file; a verdict spans possibly many).

    Deliberately WITHOUT ``mutated_text`` — a verdict carrying many
    survivors would otherwise embed a full replacement file per entry,
    which bloats the artifact for no diagnostic gain :attr:`operator` +
    :attr:`description` + :attr:`lineno` do not already provide.

    **P21/A-180 replaces the identity.** Through v3 this was
    ``(path, lineno, operator, description)``, and killed mutants had no
    entry at all -- only a count. Both were wrong for the same reason: a
    line number and a prose description are DIAGNOSTICS, not identity. Two
    ``and`` tokens on one line (A-115's boolean chain) project onto the
    identical v3 tuple, so two genuinely distinct experiments collapsed into
    one record. The identity is now the SYNTAX SITE itself:
    ``(path, start_byte, end_byte, replacement_sha256, operator)``.

    Byte offsets are zero-based, half-open, UTF-8 byte offsets into the
    exact source file at the recorded commit. :attr:`replacement_sha256` is
    the lowercase SHA-256 of the REPLACEMENT BYTES ONLY, constructed
    directly from the validated :class:`~assay.mutation.MutationSite` -- it
    is never derived by diffing two full texts, which does not recover a
    syntax site at all (A-180: ``<`` -> ``<=`` collapses to a zero-width
    insertion, ``True`` -> ``False`` to ``Tru`` -> ``Fals``).

    :attr:`operator` IS now closed against :data:`~assay.vocabulary.
    MUTATION_OPERATORS`. The v3 reasoning for leaving it open -- that
    closing it here would need an import of :mod:`assay.mutation`, which
    imports this module -- was a real cycle, and :mod:`assay.vocabulary`
    (P21 work item 2) is the leaf that removes it. An artifact naming an
    operator no adapter can produce is no longer model-valid.
    """

    path: str
    lineno: int
    start_byte: int
    end_byte: int
    replacement_sha256: str
    operator: str
    description: str
    #: (P33/V5-4) the mechanism this kill was attributed to, verbatim from
    #: the declared ``judgment.r2.kill_signal_artifact``. Legal ONLY on a
    #: ``killed`` entry (A-223e) -- a kill signal on a mutant nothing killed
    #: is a contradiction -- and present on EVERY killed entry exactly when
    #: ``judgment.r2.kill_attribution`` is ``declared``. Both of those are
    #: relations between this entry and a policy object it cannot see, so
    #: they live one and two levels up rather than here; what this class owns
    #: is the field's own grammar.
    kill_signal: str | None = None

    def __post_init__(self) -> None:
        _check_wire_path(self.path, "MutantOutcome.path")
        for name in ("lineno", "start_byte", "end_byte"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"MutantOutcome.{name} must be an integer, got {value!r}"
                )
        if self.lineno < 1:
            raise ValueError(f"MutantOutcome.lineno must be >= 1, got {self.lineno}")
        if self.start_byte < 0:
            raise ValueError(
                f"MutantOutcome.start_byte must be >= 0, got {self.start_byte}"
            )
        if self.end_byte <= self.start_byte:
            raise ValueError(
                f"MutantOutcome byte span [{self.start_byte}, {self.end_byte}) is "
                f"empty or reversed; a mutation site always replaces at least "
                f"one byte"
            )
        if not isinstance(self.replacement_sha256, str) or not _SHA256_RE.fullmatch(
            self.replacement_sha256
        ):
            raise ValueError(
                f"MutantOutcome.replacement_sha256 must be 64 lowercase hex "
                f"characters, got {self.replacement_sha256!r}"
            )
        # B046 (schema v9): `is_ingested_operator` is consulted FIRST, exactly
        # as it must be at every other prefix-equals-language site. An
        # ingested operator name is copied verbatim out of a foreign tool's
        # report, so it is DATA assay records rather than a catalogue assay
        # closes -- and `operator_language("stryker:X")` answers `"stryker"`,
        # which is a namespace and not a language, so a check that reaches
        # this closed catalogue first would refuse every ingested mutant.
        # Whether an ingested operator is legal for THIS document is a
        # cross-object question (`judgment.r2.producer`) and belongs one layer
        # up, in `Verdict._check_operator_language_agrees`.
        if not is_ingested_operator(self.operator) and (
            self.operator not in MUTATION_OPERATORS
        ):
            raise ValueError(
                f"MutantOutcome.operator must be one of "
                f"{list(MUTATION_OPERATORS)} or an ingested operator matching "
                f"{INGESTED_OPERATOR_RE.pattern}, got {self.operator!r}"
            )
        if not isinstance(self.description, str) or not self.description:
            raise ValueError(
                f"MutantOutcome.description must be a non-empty string, got "
                f"{self.description!r}"
            )
        if self.kill_signal is not None:
            _check_nonempty(self.kill_signal, "MutantOutcome.kill_signal")

    @property
    def identity(self) -> tuple[str, int, int, str, str]:
        """The exact v4 wire identity (A-180). ``lineno``/``description`` are
        deliberately absent: they diagnose, they do not distinguish."""
        return (
            self.path,
            self.start_byte,
            self.end_byte,
            self.replacement_sha256,
            self.operator,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "lineno": self.lineno,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "replacement_sha256": self.replacement_sha256,
            "operator": self.operator,
            "description": self.description,
        }
        # Omitted, never null -- the A-051 discipline this artifact applies to
        # every optional field.
        if self.kill_signal is not None:
            payload["kill_signal"] = self.kill_signal
        return payload


def _check_mutant_outcome_tuple(value: Any, what: str) -> None:
    """Every one of :class:`Mutation`'s FIVE buckets shares this shape: a
    tuple of :class:`MutantOutcome` sorted by :attr:`MutantOutcome.identity`
    — never by whichever mutant's subprocess happened to finish first (O3).

    Unlike v3 this also forbids ties: under the site identity two entries
    that compare equal ARE the same experiment recorded twice, so
    within-bucket uniqueness is checked here and cross-bucket uniqueness one
    level up in :meth:`Mutation._check_identities_are_unique`.
    """
    if not isinstance(value, tuple):
        raise ValueError(f"{what} must be a tuple, got {value!r}")
    for item in value:
        if not isinstance(item, MutantOutcome):
            raise ValueError(f"{what} entries must be MutantOutcome, got {item!r}")
    identities = [item.identity for item in value]
    if identities != sorted(identities):
        raise ValueError(
            f"{what} must be sorted by (path, start_byte, end_byte, "
            f"replacement_sha256, operator); got {identities}"
        )
    if len(set(identities)) != len(identities):
        raise ValueError(
            f"{what} records the same mutant identity twice: "
            f"{sorted({item for item in identities if identities.count(item) > 1})}"
        )


@dataclass(frozen=True, kw_only=True)
class Mutation:
    """The R2 claim payload (P12, A-116): baseline-gated mutation execution
    against changed-line mutants, isolated per mutant, bounded by a
    caller-declared ``jobs`` executor (A-082).

    FIVE buckets, not three, matching O3/O4's own explicit four-way
    enumeration (killed / survived / crashed / budget-exceeded) plus P33's
    ``equivalent`` — nyxloom's own ``MutationResult`` collapses crashed and
    budget-exceeded into "killed" (confirmed reading ``mutation_gate.py``
    directly, A-122), which is exactly the ambiguity this project's own
    oracles exist to catch, so it is not ported.

    **P33/V5-3: ``equivalent`` is the fifth bucket, and it is NOT
    ``survived``.** A mutant that provably produces no change in the judged
    artifact is evidence about the MUTATION, not about the tests. Reporting a
    migration-chain or idempotent-DDL no-op as "survived" says "no test
    asserts this constraint" when the constraint is still enforced — a false
    statement about the suite. The proof is a byte comparison of the declared
    ``judgment.r2.equivalence_artifact`` against the baseline run's, which is
    why the bucket is paired both-present-or-both-absent with that
    declaration one level up (A-209's pattern): no declaration, no
    equivalence claims, because an implementation may not infer equivalence
    from anything else.

    **P21/A-180: ``killed`` is an identity list, not a count.** v3's bare
    count meant the one bucket a reader most needs to audit — *which* sites
    the suite actually caught — could not be checked against the declared
    operator policy at all, so a killed mutant produced by an operator the
    lane never selected was unobservable. Every attempted mutant now carries
    its site identity, and every identity is unique across ALL FOUR buckets.

    **``candidate_count`` is a BOUNDED OBSERVATION, not a hidden total.**
    Normally it equals :attr:`total` and the buckets sum to both. At the
    limit terminal it is exactly ``max_mutants + 1``, :attr:`total` is zero
    and all five buckets are empty: assay observed that many candidates and
    deliberately STOPPED, so all it honestly knows is "at least this many
    exist". That sentinel is the independent evidence for the refusal —
    the alternative, a silently truncated list, is indistinguishable from a
    complete small run (A-163).
    """

    #: The number of candidate site descriptors discovery actually observed,
    #: bounded by construction at ``max_mutants + 1``.
    candidate_count: int
    total: int
    killed: tuple[MutantOutcome, ...] = ()
    survived: tuple[MutantOutcome, ...] = ()
    crashed: tuple[MutantOutcome, ...] = ()
    budget_exceeded: tuple[MutantOutcome, ...] = ()
    #: (P33/V5-3) mutants proven inert by artifact comparison. Excluded from
    #: the mutation score's denominator, and counted in :attr:`total` and
    #: :attr:`candidate_count` like every other attempted mutant — they WERE
    #: attempted; what they failed to do is change anything.
    equivalent: tuple[MutantOutcome, ...] = ()
    #: (B012) Deterministic candidate IDs covered by a shard run. Omitted,
    #: never empty, so non-shard v6 payloads are unchanged.
    candidate_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        for name in ("candidate_count", "total"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"mutation.{name} must be an integer, got {value!r}")
            if value < 0:
                raise ValueError(f"mutation.{name} must not be negative, got {value}")
        if self.candidate_count > MAX_CANDIDATE_CEILING:
            raise ValueError(
                f"mutation.candidate_count ({self.candidate_count}) exceeds the "
                f"product ceiling {MAX_CANDIDATE_CEILING}; discovery stops at "
                f"max_mutants + 1 and max_mutants is bounded at 10,000"
            )
        for name in MUTATION_BUCKETS:
            _check_mutant_outcome_tuple(getattr(self, name), f"mutation.{name}")
        if self.candidate_ids is not None:
            if not self.candidate_ids:
                raise ValueError("mutation.candidate_ids must be omitted when empty")
            if len(self.candidate_ids) != len(set(self.candidate_ids)):
                raise ValueError("mutation.candidate_ids contains a duplicate")
            for candidate in self.candidate_ids:
                if not isinstance(candidate, str) or len(candidate) != 64 or any(
                    character not in "0123456789abcdef" for character in candidate
                ):
                    raise ValueError(
                        f"mutation.candidate_ids entry must be a 64-character "
                        f"hexadecimal digest, got {candidate!r}"
                    )
        self._check_identities_are_unique()
        self._check_arithmetic()
        self._check_kill_signal_is_killed_only()

    def _check_identities_are_unique(self) -> None:
        """One mutant is in exactly one bucket (A-182).

        Cross-bucket duplication would let a single experiment be counted as
        both killed and survived, which is not a shape any producer can
        reach and is precisely the contradiction a hand-forged artifact
        would use to claim a better result than it earned.
        """
        seen: dict[tuple[str, int, int, str, str], str] = {}
        for name in MUTATION_BUCKETS:
            for item in getattr(self, name):
                previous = seen.get(item.identity)
                if previous is not None:
                    raise ValueError(
                        f"mutation identity {item.identity} appears in both "
                        f"{previous!r} and {name!r}; one mutant has exactly "
                        f"one outcome"
                    )
                seen[item.identity] = name

    def _check_kill_signal_is_killed_only(self) -> None:
        """(P33/A-223e) a ``kill_signal`` is legal ONLY on a ``killed`` entry.

        A kill signal names the mechanism that refused the mutated schema; a
        mutant that survived, crashed, ran out of budget or was proven inert
        was refused by nothing, so a signal on it is a contradiction rather
        than a merely unusual record. The locked schema encodes the same rule
        locally, per bucket (A-182 assigns it there); this is the model's
        independent statement of it, so a hand-built
        :class:`Mutation` cannot carry what a validated document cannot.
        """
        for name in MUTATION_BUCKETS:
            if name == "killed":
                continue
            for item in getattr(self, name):
                if item.kill_signal is not None:
                    raise ValueError(
                        f"mutation.{name} entry {item.identity} carries "
                        f"kill_signal {item.kill_signal!r}, but a kill signal "
                        f"names the mechanism that refused a mutant -- only a "
                        f"killed mutant was refused by anything"
                    )

    def _check_arithmetic(self) -> None:
        """Exactly two legal shapes (A-163), and nothing between them."""
        attempted = sum(len(getattr(self, name)) for name in MUTATION_BUCKETS)
        if self.total != attempted:
            raise ValueError(
                f"mutation.total ({self.total}) must equal the number of "
                f"recorded identities across all five buckets ({attempted})"
            )
        if self.total == self.candidate_count:
            return
        # The only other legal shape is the pre-submission limit sentinel:
        # candidates were observed, nothing was attempted.
        if self.total == 0 and 1 <= self.candidate_count <= MAX_CANDIDATE_CEILING:
            return
        raise ValueError(
            f"mutation records {self.candidate_count} candidate(s) but "
            f"{self.total} attempted mutant(s); a payload is either normal "
            f"(candidate_count == total == sum of the five buckets) or the "
            f"pre-submission limit sentinel (total 0, five empty buckets, "
            f"candidate_count in 1..{MAX_CANDIDATE_CEILING})"
        )

    @property
    def is_limit_sentinel(self) -> bool:
        """True for the pre-submission ``max_mutants + 1`` refusal shape.

        A zero/zero payload is NOT a sentinel: it is a supported analysis
        that genuinely observed no candidates (``NO_MUTANTS``). The two are
        distinguished by ``candidate_count`` alone, which is why the field
        is required rather than derived.
        """
        return self.total == 0 and self.candidate_count > 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "candidate_count": self.candidate_count,
            "total": self.total,
        }
        for name in MUTATION_BUCKETS:
            payload[name] = [item.to_dict() for item in getattr(self, name)]
        if self.candidate_ids is not None:
            payload["candidate_ids"] = list(self.candidate_ids)
        return payload


@dataclass(frozen=True, kw_only=True)
class JudgeProvenance:
    """(B018/A-327) WHICH build of assay produced this verdict.

    ``assay_version`` names a version string, and any process at all can print
    one. CIU V8's central tool resolution (proposal §11.3) verifies a
    *download* -- it resolves a judge from ``[testing.judge]``, fetches an
    artifact, checks its digest -- and then has nothing that binds the verdict
    in front of it to that artifact. This object is that binding: the running
    process identifies the build artifact it was installed FROM and records its
    digest, so a consumer can compare it against the digest it resolved itself.

    Every field is required, and :mod:`assay.provenance` returns this object or
    a refusal reason -- never a partial one. A-051's "omitted, never null",
    applied to an identity: half an identity is worse than none, because it
    reads as an identity.
    """

    #: The distribution name as the installed artifact's OWN metadata declares
    #: it, never the literal ``"assay"`` -- the point of the field is to record
    #: what the artifact says it is, which is exactly the claim under audit.
    name: str
    #: The exact version, from the same distribution metadata
    #: ``assay.__version__`` reads.
    version: str
    #: Which of :data:`JUDGE_ARTIFACT_KINDS` the digest identifies.
    artifact: str
    #: One of :data:`JUDGE_DIGEST_ALGORITHMS`.
    digest_algorithm: str
    #: Lowercase hex, exactly 64 characters for ``sha256``.
    digest: str

    def __post_init__(self) -> None:
        _check_nonempty(self.name, "judge_provenance.name")
        _check_nonempty(self.version, "judge_provenance.version")
        if self.artifact not in JUDGE_ARTIFACT_KINDS:
            raise ValueError(
                f"judge_provenance.artifact must be one of "
                f"{list(JUDGE_ARTIFACT_KINDS)}, got {self.artifact!r}"
            )
        if self.digest_algorithm not in JUDGE_DIGEST_ALGORITHMS:
            raise ValueError(
                f"judge_provenance.digest_algorithm must be one of "
                f"{list(JUDGE_DIGEST_ALGORITHMS)}, got "
                f"{self.digest_algorithm!r}"
            )
        # Lowercase is part of the contract, not a formatting preference: a
        # consumer comparing against its own resolved digest compares strings,
        # and two spellings of one digest would not be equal.
        if (
            not isinstance(self.digest, str)
            or len(self.digest) != 64
            or any(character not in "0123456789abcdef" for character in self.digest)
        ):
            raise ValueError(
                f"judge_provenance.digest must be exactly 64 lowercase "
                f"hexadecimal characters, got {self.digest!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "artifact": self.artifact,
            "digest_algorithm": self.digest_algorithm,
            "digest": self.digest,
        }


@dataclass(frozen=True, kw_only=True)
class JudgmentResolved:
    """(P33/V5-1) What was judged, shared by every computed tier above R0.

    Hoisted out of :class:`JudgmentR1` because ``R0,R2`` has been a legal
    rigor declaration since A-192, while ``judgment`` permits ``{r2}`` with
    no ``r1`` — so a v4 ``R0,R2`` lane, Python or Go and with no SQL
    involved, recorded **no language, no source roots and no comparison
    commit at all**, even though R2 scopes mutation to changed lines against
    exactly that commit. That is a v4 bug; SQL is merely the first language
    for which ``R0,R2`` is the *only* honest declaration.

    Two provenance rules, both of which have been got wrong before (A-223f):

    * :attr:`source_roots` is the DECLARED project-relative spelling
      (A-049), never the relocated absolute scratch paths a snapshot run
      works in (A-149). A consumer reading ``/tmp/...`` learns nothing about
      the project.
    * :attr:`base` is the PRE-SNAPSHOT resolution against the consumer's own
      repository. R1's in-snapshot resolution must equal it — merge-base is
      idempotent on an already-ancestor value — and v5 records one field
      where v4 had two independently-resolved values, so which one wins is
      stated rather than left to an implementer.

    :attr:`base` is CONDITIONAL (A-223a/A-227): required exactly when
    ``judgment`` carries ``r1`` or ``r2``, forbidden otherwise.
    ``JUDGE_FIELDS_BY_RIGOR`` carries ``base`` for R1 and R2 and not for R3,
    so an ``R0,R3`` lane genuinely has no comparison commit and must not be
    made to invent one. That conditional needs to see the sibling tiers, so
    it lives on :class:`Judgment`, not here.
    """

    language: str
    source_roots: tuple[str, ...]
    #: the full resolved comparison commit, never a symbolic ref; absent on a
    #: judgment that carries neither r1 nor r2
    base: str | None = None
    #: (B008) which of `resolve_base`'s two branches produced `base`:
    #: `"first-parent"` on a merge-commit HEAD (the branch's own pre-merge
    #: tip -- a common pre-gate `git merge <base>` silently narrows the
    #: changed-line floor and any R2 lane to "what did the merge itself
    #: change"), or `"merge-base"` otherwise. Optional for v7 compatibility;
    #: never present without `base`.
    base_resolution: str | None = None

    def __post_init__(self) -> None:
        _check_nonempty(self.language, "judgment.resolved.language")
        if not isinstance(self.source_roots, tuple) or not self.source_roots:
            raise ValueError(
                f"judgment.resolved.source_roots must be a non-empty tuple, got "
                f"{self.source_roots!r}"
            )
        for root in self.source_roots:
            _check_nonempty(root, "judgment.resolved.source_roots entry")
        if self.base is not None:
            _check_nonempty(self.base, "judgment.resolved.base")
        if self.base_resolution is not None:
            if self.base is None:
                raise ValueError(
                    "judgment.resolved.base_resolution requires judgment.resolved.base"
                )
            if self.base_resolution not in ("merge-base", "first-parent"):
                raise ValueError(
                    f"judgment.resolved.base_resolution must be 'merge-base' or "
                    f"'first-parent', got {self.base_resolution!r}"
                )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "language": self.language,
            "source_roots": list(self.source_roots),
        }
        if self.base is not None:
            payload["base"] = self.base
        if self.base_resolution is not None:
            payload["base_resolution"] = self.base_resolution
        return payload


@dataclass(frozen=True, kw_only=True)
class JudgmentR1:
    """The effective R1 policy (P16, sol finding 2): the coverage
    format+artifact spelling the run used, and the ``fail_under`` floor and
    ``allow_excluded`` choice that decided the R1 claim's ``PASS``/``FAIL``.
    Without this, an independent consumer has a percentage but not the
    policy that judged it: "schema v2 does not record
    fail_under/allow_excluded, so an independent consumer cannot even in
    principle re-derive whether an R1 status was correct from the payload
    alone."

    **P33/V5-1 moved ``language``, ``source_roots`` and ``base`` out** to
    :class:`JudgmentResolved`. What remains here is genuinely R1 *policy*;
    what left were facts about the lane that every computed tier above R0
    consumes, and keeping them in the R1 tier made them unrecordable for a
    lane that declares no R1.

    **wave-1 §6 (A-260) adds ``mode``, ``targets`` and ``require_branch``.**
    ``mode`` is REQUIRED here even though it is OPTIONAL in the lane file:
    the lane file records what a human declared; this records what
    actually judged (P16, sol finding 2's own asymmetry). ``targets`` is
    required, non-empty, unique and SORTED iff ``mode = "whole_target"``,
    and forbidden otherwise. ``require_branch`` records the EFFECTIVE
    policy the same way ``allow_excluded`` already does.
    """

    coverage_format: str
    #: (B045, schema v9) the DECLARED producer of the coverage artifact --
    #: WHICH tool wrote it, where one format has several producers that
    #: disagree about what its own fields mean (`coverage-istanbul-json`'s
    #: `branchMap`, A-344; whether a line ran, A-346). Optional here because
    #: it is optional in the lane file for every format but
    #: `coverage-istanbul-json`; `None` means the lane declared none, never a
    #: default -- B045's whole point is that a producer is a DECLARED fact and
    #: never a sniffed one (A-007). Kept a plain string here, as
    #: :attr:`coverage_format` beside it is: the legal set is keyed BY FORMAT
    #: (`assay.vocabulary.COVERAGE_PRODUCERS_BY_FORMAT`) and closed by the
    #: LOADER, which is the layer that knows both halves of the pair.
    coverage_producer: str | None = None
    coverage_artifact: str
    fail_under: float
    allow_excluded: bool
    #: (wave-1 §6, A-260) defaults to ``"changed_lines"`` -- the only mode
    #: that existed before this wave, and the faithful historical value for
    #: every producer written before this field existed.
    mode: str = "changed_lines"
    #: (wave-1 §6) required, non-empty, unique, sorted iff
    #: ``mode == "whole_target"``; ``None`` otherwise.
    targets: tuple[str, ...] | None = None
    #: (wave-1 §4/§6, A-259) the EFFECTIVE ``judge.require_branch`` policy --
    #: defaults to ``False``, the value every lane declared before this
    #: field existed.
    require_branch: bool = False

    def __post_init__(self) -> None:
        _check_nonempty(self.coverage_format, "judgment.r1.coverage_format")
        # B045: absent is legal (the lane declared none); PRESENT-and-empty is
        # not, for `coverage_format`'s reason -- "" is a producer name nothing
        # can be, and A-051's omitted-never-null rule makes absence the only
        # spelling of "unknown".
        if self.coverage_producer is not None:
            _check_nonempty(self.coverage_producer, "judgment.r1.coverage_producer")
        _check_nonempty(self.coverage_artifact, "judgment.r1.coverage_artifact")
        if isinstance(self.fail_under, bool) or not isinstance(
            self.fail_under, (int, float)
        ):
            raise ValueError(
                f"judgment.r1.fail_under must be a number, got {self.fail_under!r}"
            )
        if not 0.0 <= float(self.fail_under) <= 100.0:
            raise ValueError(
                f"judgment.r1.fail_under must be a percentage between 0 and "
                f"100, got {self.fail_under}"
            )
        if not isinstance(self.allow_excluded, bool):
            raise ValueError(
                f"judgment.r1.allow_excluded must be a boolean, got "
                f"{self.allow_excluded!r}"
            )
        if self.mode not in JUDGE_MODES:
            raise ValueError(
                f"judgment.r1.mode must be one of {list(JUDGE_MODES)}, got "
                f"{self.mode!r}"
            )
        if not isinstance(self.require_branch, bool):
            raise ValueError(
                f"judgment.r1.require_branch must be a boolean, got "
                f"{self.require_branch!r}"
            )
        if self.mode == "whole_target":
            if (
                not isinstance(self.targets, tuple)
                or not self.targets
            ):
                raise ValueError(
                    f"judgment.r1.targets must be a non-empty tuple when "
                    f"mode is 'whole_target', got {self.targets!r}"
                )
            for target in self.targets:
                _check_wire_path(target, "judgment.r1.targets entry")
            if len(set(self.targets)) != len(self.targets):
                raise ValueError(
                    f"judgment.r1.targets contains a duplicate: "
                    f"{list(self.targets)}"
                )
            if list(self.targets) != sorted(self.targets):
                raise ValueError(
                    f"judgment.r1.targets must be sorted, got "
                    f"{list(self.targets)}"
                )
        elif self.targets is not None:
            raise ValueError(
                f"judgment.r1.targets is present ({list(self.targets)}) but "
                f"mode is {self.mode!r}, not 'whole_target' -- targets "
                f"describes nothing outside whole-target mode"
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "coverage_format": self.coverage_format,
            "coverage_artifact": self.coverage_artifact,
            "fail_under": float(self.fail_under),
            "allow_excluded": self.allow_excluded,
            "mode": self.mode,
            "require_branch": self.require_branch,
        }
        # B045: omitted, never null (A-051).
        if self.coverage_producer is not None:
            payload["coverage_producer"] = self.coverage_producer
        if self.targets is not None:
            payload["targets"] = list(self.targets)
        return payload


#: (B046, schema v9) WHO computed the mutants an R2 judgment is about. Closed
#: here, in the schema and in the loader, for `KILL_ATTRIBUTIONS`' reason: a
#: third value would be a third trust story, and there are exactly two --
#: assay ran the engine, or assay read a report a foreign tool wrote inside
#: the snapshot.
MUTATION_PRODUCERS: tuple[str, ...] = ("native", "ingested")


@dataclass(frozen=True, kw_only=True)
class SourcePosition:
    """(B046, schema v9) One position in the judged source: a project-root-
    relative wire path and a one-based line.

    Two fields rather than one ``"path:line"`` string, because a path may
    legally contain a colon and because :class:`MutantOutcome` one class over
    already models the same pair the same way. Sorting is by
    ``(path, lineno)`` and is enforced by the owning field, not here -- a
    position on its own has no order to be wrong about.
    """

    path: str
    lineno: int

    def __post_init__(self) -> None:
        _check_wire_path(self.path, "source position path")
        if isinstance(self.lineno, bool) or not isinstance(self.lineno, int):
            raise ValueError(
                f"source position lineno must be an integer, got {self.lineno!r}"
            )
        if self.lineno < 1:
            raise ValueError(
                f"source position lineno must be >= 1, got {self.lineno}"
            )

    @property
    def sort_key(self) -> tuple[str, int]:
        return (self.path, self.lineno)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "lineno": self.lineno}


@dataclass(frozen=True, kw_only=True)
class MutationProducerTool:
    """(B046, schema v9) The identity of the foreign mutation tool whose
    report assay ingested, copied VERBATIM from that report.

    **Declared by artifact, not verified.** Assay read these three strings
    out of a file the lane's own command wrote inside the snapshot; it has no
    independent evidence for any of them. That is exactly why this is NOT a
    :class:`Helper` entry: ``helpers[]`` records tools assay itself invoked
    and resolved on PATH (A-230a), and assay did not invoke Stryker. Putting
    an ingested tool there would launder a declared fact into a verified one,
    which is the one conflation the whole tier vocabulary exists to prevent.
    """

    #: `framework.name` from the report.
    name: str
    #: `framework.version` from the report.
    version: str
    #: the report's own `schemaVersion` -- the mutation-testing-report-schema
    #: version its producer wrote, kept as a STRING because the upstream field
    #: is one ("1", "1.0", "2" are all real spellings) and reinterpreting it as
    #: a number here would be assay inventing precision the artifact does not
    #: have.
    report_schema_version: str

    def __post_init__(self) -> None:
        _check_nonempty(self.name, "judgment.r2.producer_tool.name")
        _check_nonempty(self.version, "judgment.r2.producer_tool.version")
        _check_nonempty(
            self.report_schema_version,
            "judgment.r2.producer_tool.report_schema_version",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "report_schema_version": self.report_schema_version,
        }


@dataclass(frozen=True, kw_only=True)
class JudgmentR2:
    """Populated by ``assay run``'s own R2 CLI wiring (P18) whenever the
    rendered R2 claim carries a ``mutation`` payload. The mutation policy
    that decided which changed-line sites were candidates and how many ran
    concurrently: ``jobs`` and the ORDERED, closed ``operators`` list the
    lane declared — never a machine-derived job count (A-082/A-122).

    Unlike R1, an independent consumer can already re-derive the R2 claim's
    *status* from :class:`Mutation`'s own bucket fields alone
    (:func:`assay.mutation.judge_mutation`'s mapping needs no external
    policy input) — this record exists so a consumer can also confirm which
    POLICY produced those buckets. P19/A-148 gave this a construction-time
    correspondence check one level up
    (:meth:`Verdict._check_judgment_matches_claims`): present if and only
    if the R2 claim carries a ``mutation`` payload, and every operator a
    survived/crashed/budget-exceeded entry names must be one
    :attr:`operators` actually declared.
    """

    #: (B046, schema v9) `"native"` or `"ingested"` -- see
    #: :data:`MUTATION_PRODUCERS`. Defaulted to `"native"` for
    #: :attr:`mode`'s reason: it is the only producer that existed before this
    #: field did, so it is the faithful historical value for every record
    #: written without one. It is REQUIRED on the wire all the same (always
    #: emitted by :meth:`to_dict`), because an optional discriminator leaves
    #: exactly the ambiguity this field exists to end.
    producer: str = "native"
    #: (B046) `None` under `"native"`; required under `"ingested"`.
    producer_tool: MutationProducerTool | None = None
    #: (B046) The `NoCoverage` mutants -- ones no test even EXERCISED. They
    #: count in the `survived` bucket (a mutant nothing ran is not killed), but
    #: they are listed here as well, BY POSITION, so the worst kind of
    #: survivor is never invisible inside a number. Required under
    #: `"ingested"` and MAY be empty (an empty tuple is the positive statement
    #: "the ingested path looked and found none", which is not what `None`
    #: says); `None` -- absent -- under `"native"`, where assay's own engine
    #: has no `NoCoverage` concept at all.
    survived_uncovered: tuple[SourcePosition, ...] | None = None
    #: (B046) How many mutants the report marked `CompileError`/`RuntimeError`
    #: -- invalid mutants the native engine never emits. Excluded from the
    #: `pct` denominator and COUNTED, because a report that could not compile
    #: most of its own mutants measured far less than its score implies.
    #: Required (possibly `0`) under `"ingested"`, `None` under `"native"`.
    discarded: int | None = None
    #: (B050/DA-D6, A-427, schema v10) the mutation-score floor this judgment
    #: applied, **REQUIRED under `producer = "ingested"` and FORBIDDEN under
    #: `"native"`**. Spelled byte-identically to
    #: :attr:`JudgmentR1.fail_under` -- the same quantity at a different tier,
    #: and two spellings of one policy number is how they drift.
    #:
    #: The fork is not ergonomics. A native R2 has no floor at all --
    #: `judge_mutation`'s `survived` branch fails on any survivor whatsoever
    #: -- so a native document carrying one would record a policy that
    #: nothing applied, which is B050's own un-auditable claim inverted. An
    #: ingested one is judged against a floor the lane declared, and until
    #: this field existed the loader FORCED that floor to `100.0` so that
    #: `verify.py` could assume it; the assumption is now READ from the
    #: document instead, which is what makes the re-derivation total rather
    #: than partial.
    fail_under: float | None = None
    #: (B046) In-scope executable lines the foreign tool produced NO mutant
    #: for. Required (possibly empty) under `"ingested"`, `None` under
    #: `"native"`. Same empty-vs-absent rule as :attr:`survived_uncovered`.
    lines_without_candidates: tuple[SourcePosition, ...] | None = None
    #: **Required under `producer = "native"`, FORBIDDEN under `"ingested"`**
    #: (B046). Assay declared no concurrency for a run it did not orchestrate;
    #: copying the foreign tool's own setting here would put that tool's
    #: configuration on the wire under assay's name.
    jobs: int | None = None
    #: (P21/A-163) the declared candidate ceiling, in ``1..10_000``. Recorded
    #: because the limit terminal's whole evidence is arithmetic against it:
    #: ``candidate_count == max_mutants + 1``. Without the cap in the
    #: artifact a consumer cannot tell a legitimate refusal from a forged
    #: one, so this is required rather than optional -- **under
    #: `producer = "native"`. FORBIDDEN under `"ingested"`** for
    #: :attr:`jobs`' reason (B046).
    max_mutants: int | None = None
    #: **Required under `producer = "native"`, FORBIDDEN under `"ingested"`**
    #: (B046). An ingested lane's operators were chosen by the foreign tool's
    #: configuration, not by assay's policy, and this object is documented as
    #: the effective R2 POLICY -- what decided the claim. Assay's policy for
    #: such a lane is genuinely EMPTY, and is left absent rather than
    #: backfilled from the observed `stryker:*` set: the same
    #: declared-vs-verified line A-230a draws for `helpers[]`. The observed
    #: operators are still fully on the wire, one per mutant, in
    #: `mutation.*[].operator`.
    operators: tuple[str, ...] | None = None
    #: (P33/V5-4) whether kills in this run are attributed to a declared
    #: mechanism or merely observed. **Derived, never declared** (A-223b):
    #: ``declared`` exactly when :attr:`kill_signal_artifact` is present.
    #: A-036 derives ``argv_modified`` from ``argv_appended`` for precisely
    #: this reason -- deriving removes the whole class of artifact in which
    #: the two disagree. `unattributed` is honest and common: it means a
    #: killed mutant proves only that the suite failed, not that it failed
    #: for the reason the mutant created. v5 does NOT close that gap and does
    #: not claim to (A-220/V5-4); it makes the weakness visible instead of
    #: absent.
    kill_attribution: str
    #: (P33/V5-4) a project-relative path the declared command writes, naming
    #: the mechanism that refused. **Reserved in the artifact contract but
    #: NOT declarable in lane config until P34** (A-227): assay ships no
    #: producer for the `kill_signal` values it implies, so a lane declaring
    #: it could not emit a consistent artifact.
    kill_signal_artifact: str | None = None
    #: (P33/V5-3) a project-relative path the lane's own declared command
    #: writes after applying a mutant, whose bytes assay compares against the
    #: baseline run's. Same disposition as `kill_signal_artifact`: reserved
    #: here, refused at config load until P34 (A-230b). Comparing two
    #: command-written artifacts is what keeps A-215's no-DSN boundary
    #: exactly where it is -- assay never connects to a database.
    equivalence_artifact: str | None = None
    #: (B035/A-329, schema v8) the scope R2 judged under, mirroring
    #: :attr:`JudgmentR1.mode` exactly. Until v8 an ``R0,R2`` document put
    #: NOTHING on the wire distinguishing a diff-based R2 (which must carry
    #: `judgment.resolved.base`) from a whole-target one (which must not), so
    #: A-325 had to stop enforcing that rule for the one lane shape that most
    #: needs it -- every SQL lane, dstdns's `cw2b_schema` included. Defaulted
    #: to ``"changed_lines"`` for `JudgmentR1.mode`'s reason: it is the only
    #: mode that existed before whole-target scope, so it is the faithful
    #: historical value.
    mode: str = "changed_lines"
    #: (B035/A-329) the DECLARED target set, required/non-empty/unique/sorted
    #: iff ``mode == "whole_target"`` and forbidden otherwise -- the exact
    #: contract :attr:`JudgmentR1.targets` already carries. Without it a
    #: declared target that legitimately produced zero mutation sites is
    #: indistinguishable from one that was never considered: `mutation.*[].
    #: path` names only files that yielded a mutant.
    targets: tuple[str, ...] | None = None
    #: (B012) The declared zero-based shard position. ``None`` with
    #: :attr:`shard_count` means the whole workload.
    shard_index: int | None = None
    #: (B012) The declared shard cardinality. Required with ``shard_index``.
    shard_count: int | None = None

    def __post_init__(self) -> None:
        # B046: the producer fork, checked FIRST -- every field below is
        # either assay's own policy (legal only under `native`) or a fact
        # derived from an ingested report (legal only under `ingested`), so
        # nothing else here can be judged until this is known good.
        if self.producer not in MUTATION_PRODUCERS:
            raise ValueError(
                f"judgment.r2.producer must be one of "
                f"{list(MUTATION_PRODUCERS)}, got {self.producer!r}"
            )
        self._check_producer_fork()
        if self.producer == "native":
            self._check_native_policy()
        else:
            self._check_ingested_record()
        if self.kill_attribution not in KILL_ATTRIBUTIONS:
            raise ValueError(
                f"judgment.r2.kill_attribution must be one of "
                f"{list(KILL_ATTRIBUTIONS)}, got {self.kill_attribution!r}"
            )
        # A-223b: the derivation IS the contract, so the two fields cannot
        # disagree even in a hand-built model object. `declared` with no
        # artifact names an attribution source that does not exist;
        # `unattributed` with one hides a source the run actually had.
        if self.kill_signal_artifact is not None:
            _check_nonempty(
                self.kill_signal_artifact, "judgment.r2.kill_signal_artifact"
            )
            if self.kill_attribution != "declared":
                raise ValueError(
                    f"judgment.r2 records kill_attribution "
                    f"{self.kill_attribution!r} beside a kill_signal_artifact "
                    f"{self.kill_signal_artifact!r}; attribution is DERIVED "
                    f"from that artifact's presence (A-223b), so the only "
                    f"consistent value here is 'declared'"
                )
        elif self.kill_attribution == "declared":
            raise ValueError(
                "judgment.r2 records kill_attribution 'declared' with no "
                "kill_signal_artifact; attribution is DERIVED from that "
                "artifact's presence (A-223b), and a declared attribution "
                "whose source is absent names a mechanism nothing recorded"
            )
        if self.equivalence_artifact is not None:
            _check_nonempty(
                self.equivalence_artifact, "judgment.r2.equivalence_artifact"
            )
        # B035/A-329: word-for-word the checks `JudgmentR1.__post_init__`
        # already applies to its own `mode`/`targets` pair. Written out here
        # rather than factored into a shared helper deliberately: the two
        # objects are independent records of one lane-level scope, and a
        # shared implementation would mean a single edit could silently
        # weaken both halves of the agreement the layer above now checks.
        if self.mode not in JUDGE_MODES:
            raise ValueError(
                f"judgment.r2.mode must be one of {list(JUDGE_MODES)}, got "
                f"{self.mode!r}"
            )
        if self.mode == "whole_target":
            if not isinstance(self.targets, tuple) or not self.targets:
                raise ValueError(
                    f"judgment.r2.targets must be a non-empty tuple when "
                    f"mode is 'whole_target', got {self.targets!r}"
                )
            for target in self.targets:
                _check_wire_path(target, "judgment.r2.targets entry")
            if len(set(self.targets)) != len(self.targets):
                raise ValueError(
                    f"judgment.r2.targets contains a duplicate: "
                    f"{list(self.targets)}"
                )
            if list(self.targets) != sorted(self.targets):
                raise ValueError(
                    f"judgment.r2.targets must be sorted, got "
                    f"{list(self.targets)}"
                )
        elif self.targets is not None:
            raise ValueError(
                f"judgment.r2.targets is present ({list(self.targets)}) but "
                f"mode is {self.mode!r}, not 'whole_target' -- targets "
                f"describes nothing outside whole-target mode"
            )
        shard_specified = self.shard_index is not None or self.shard_count is not None
        if shard_specified and (
            self.shard_index is None
            or self.shard_count is None
            or isinstance(self.shard_index, bool)
            or isinstance(self.shard_count, bool)
            or not isinstance(self.shard_index, int)
            or not isinstance(self.shard_count, int)
        ):
            raise ValueError(
                "judgment.r2 requires integer shard_index and shard_count together"
            )
        if shard_specified and self.shard_count is not None:
            if not 1 <= self.shard_count <= MAX_SHARD_COUNT:
                raise ValueError(
                    f"judgment.r2.shard_count must be in 1..{MAX_SHARD_COUNT}, "
                    f"got {self.shard_count}"
                )
            assert self.shard_index is not None
            if not 0 <= self.shard_index < self.shard_count:
                raise ValueError(
                    f"judgment.r2.shard_index {self.shard_index} is outside "
                    f"0..{self.shard_count - 1}"
                )

    #: (B046) assay's OWN R2 policy -- the fields a native run declares and an
    #: ingested one has nothing honest to put in.
    _NATIVE_ONLY_FIELDS: ClassVar[tuple[str, ...]] = (
        "jobs",
        "max_mutants",
        "operators",
        "equivalence_artifact",
    )
    #: (B046) facts derived FROM an ingested report -- absent from a native
    #: document, which would otherwise claim a computation that never ran.
    _INGESTED_ONLY_FIELDS: ClassVar[tuple[str, ...]] = (
        "producer_tool",
        "survived_uncovered",
        "discarded",
        "lines_without_candidates",
        # B050/A-427 (schema v10): the floor the ingested judgment applied.
        # It joins this group rather than being optional on both sides for
        # the group's own stated reason -- a native R2 has no floor, so one
        # recorded there would name a policy nothing applied.
        "fail_under",
    )

    def _check_producer_fork(self) -> None:
        """(B046) Exactly one of the two field groups is populated, chosen by
        :attr:`producer`. Written as one loop over two named tuples rather
        than eight hand-written conditionals for `MUTATION_BUCKETS`' reason:
        a ninth field reaching one half of the rule and not the other is the
        precise defect shape A-228 came from.
        """
        if self.producer == "native":
            present, forbidden_label = self._INGESTED_ONLY_FIELDS, "native"
            required = self._NATIVE_ONLY_FIELDS[:-1]  # equivalence_artifact
        else:                                         # is OPTIONAL natively
            present, forbidden_label = self._NATIVE_ONLY_FIELDS, "ingested"
            required = self._INGESTED_ONLY_FIELDS
        forbidden = [name for name in present if getattr(self, name) is not None]
        if forbidden:
            raise ValueError(
                f"judgment.r2 records producer {forbidden_label!r} beside "
                f"{forbidden} -- "
                + (
                    "those fields describe an ingested report assay did not "
                    "read"
                    if forbidden_label == "native"
                    else "assay chose none of that policy for a run it did "
                    "not orchestrate, and copying the foreign tool's own "
                    "settings here would put its configuration on the wire "
                    "under assay's name (A-230a)"
                )
            )
        missing = [name for name in required if getattr(self, name) is None]
        if missing:
            raise ValueError(
                f"judgment.r2 records producer {forbidden_label!r} but is "
                f"missing {missing}, which every {forbidden_label} R2 "
                f"judgment must carry"
            )

    def _check_native_policy(self) -> None:
        """The v8 rules, unchanged in substance, now reached only under
        ``producer = "native"``."""
        if isinstance(self.jobs, bool) or not isinstance(self.jobs, int):
            raise ValueError(f"judgment.r2.jobs must be an integer, got {self.jobs!r}")
        if self.jobs < 1:
            raise ValueError(f"judgment.r2.jobs must be >= 1, got {self.jobs}")
        if isinstance(self.max_mutants, bool) or not isinstance(self.max_mutants, int):
            raise ValueError(
                f"judgment.r2.max_mutants must be an integer, got "
                f"{self.max_mutants!r}"
            )
        if not 1 <= self.max_mutants <= 10_000:
            raise ValueError(
                f"judgment.r2.max_mutants must be in 1..10,000, got "
                f"{self.max_mutants}"
            )
        if not isinstance(self.operators, tuple) or not self.operators:
            raise ValueError(
                f"judgment.r2.operators must be a non-empty tuple, got "
                f"{self.operators!r}"
            )
        # P21 work item 2: closed here as well as in config and the schema.
        # v3 accepted any non-empty string, so a policy naming an operator no
        # adapter implements was model-valid while the shipped schema's own
        # enum rejected it -- the exact model/schema/verifier mismatch this
        # package deletes rather than maintains.
        unknown = [
            operator for operator in self.operators if operator not in MUTATION_OPERATORS
        ]
        if unknown:
            raise ValueError(
                f"judgment.r2.operators names unknown operator(s) {unknown}; "
                f"the catalogue is closed: {list(MUTATION_OPERATORS)}"
            )
        if len(set(self.operators)) != len(self.operators):
            raise ValueError(
                f"judgment.r2.operators contains a duplicate: "
                f"{list(self.operators)}"
            )

    def _check_ingested_record(self) -> None:
        """(B046) The shape of the four fields an ingested judgment carries.
        `_check_producer_fork` has already proved all four are present.
        """
        if not isinstance(self.producer_tool, MutationProducerTool):
            raise ValueError(
                f"judgment.r2.producer_tool must be a MutationProducerTool, "
                f"got {self.producer_tool!r}"
            )
        if isinstance(self.discarded, bool) or not isinstance(self.discarded, int):
            raise ValueError(
                f"judgment.r2.discarded must be an integer, got {self.discarded!r}"
            )
        if not 0 <= self.discarded <= 10_000:
            raise ValueError(
                f"judgment.r2.discarded must be in 0..10,000, got {self.discarded}"
            )
        # B050/A-427: byte-identical to `judgment.r1.fail_under`'s own two
        # checks, deliberately -- the same quantity at a different tier, and
        # two spellings of one policy number is how they drift.
        if isinstance(self.fail_under, bool) or not isinstance(
            self.fail_under, (int, float)
        ):
            raise ValueError(
                f"judgment.r2.fail_under must be a number, got {self.fail_under!r}"
            )
        if not 0.0 <= float(self.fail_under) <= 100.0:
            raise ValueError(
                f"judgment.r2.fail_under must be a percentage between 0 and "
                f"100, got {self.fail_under}"
            )
        for name in ("survived_uncovered", "lines_without_candidates"):
            value = getattr(self, name)
            what = f"judgment.r2.{name}"
            if not isinstance(value, tuple):
                raise ValueError(f"{what} must be a tuple, got {value!r}")
            if len(value) > 10_000:
                raise ValueError(
                    f"{what} holds {len(value)} entries, over the 10,000 ceiling"
                )
            for index, item in enumerate(value):
                if not isinstance(item, SourcePosition):
                    raise ValueError(
                        f"{what}[{index}] must be a SourcePosition, got {item!r}"
                    )
            keys = [item.sort_key for item in value]
            if len(set(keys)) != len(keys):
                raise ValueError(f"{what} contains a duplicate position: {keys}")
            # Sortedness is array ORDER, which the packaged schema cannot
            # express (draft 2020-12 has no $data and no ordering keyword), so
            # it is owned here and, independently worded, by the raw verifier
            # -- the same split `targets` above already lives under.
            if keys != sorted(keys):
                raise ValueError(
                    f"{what} must be ascending by (path, lineno), got {keys}"
                )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kill_attribution": self.kill_attribution,
            # B035/A-329: always emitted, exactly as `judgment.r1.mode` is.
            # An optional scope field would leave the r1-absent case back
            # where B035 found it -- unable to witness its own rule -- for
            # every producer that simply omitted it.
            "mode": self.mode,
            # B046: always emitted, for `mode`'s reason one line up.
            "producer": self.producer,
        }
        if self.producer == "native":
            payload["jobs"] = self.jobs
            payload["max_mutants"] = self.max_mutants
            payload["operators"] = list(self.operators or ())
        else:
            assert self.producer_tool is not None  # _check_producer_fork
            payload["producer_tool"] = self.producer_tool.to_dict()
            payload["survived_uncovered"] = [
                item.to_dict() for item in self.survived_uncovered or ()
            ]
            payload["discarded"] = self.discarded
            payload["lines_without_candidates"] = [
                item.to_dict() for item in self.lines_without_candidates or ()
            ]
            # B050/A-427: always emitted under `ingested`, exactly as
            # `judgment.r1.fail_under` is -- an optional floor leaves the
            # re-derivation partial again.
            payload["fail_under"] = float(self.fail_under)
        if self.targets is not None:
            payload["targets"] = list(self.targets)
        # Omitted, never null (A-051).
        if self.kill_signal_artifact is not None:
            payload["kill_signal_artifact"] = self.kill_signal_artifact
        if self.equivalence_artifact is not None:
            payload["equivalence_artifact"] = self.equivalence_artifact
        if self.shard_index is not None and self.shard_count is not None:
            payload["shard_index"] = self.shard_index
            payload["shard_count"] = self.shard_count
        return payload


@dataclass(frozen=True, kw_only=True)
class JudgmentR3:
    """Populated by ``assay run``'s own isolated R3 CLI wiring (P19)
    whenever the rendered R3 claim carries a ``canary`` payload. The canary
    declaration that produced it: the ``mechanism`` name and the ORDERED
    ``targets`` list (B007/A-432, schema v10 — the singular ``target`` at
    v9), so a consumer can tell WHICH declared canary a rendered R3 claim
    answers for, and in what order the probes were tried.

    :func:`assay.canary.judge_canary` already re-derives R3 *status* from
    :class:`CanaryResult`'s own fields alone, the identical reason
    :class:`JudgmentR2` gives for its own case — this record exists so a
    consumer can also confirm which declared canary produced that result.
    P19/A-148 gave this a construction-time correspondence check one level
    up (:meth:`Verdict._check_judgment_matches_claims`): present if and
    only if the R3 claim carries a ``canary`` payload, and that payload's
    own ``mechanism`` must match :attr:`mechanism` exactly.
    """

    mechanism: str
    #: (B007/A-432, schema v10) the ORDERED declared target list, 1..8. The
    #: singular v9 `target` does NOT survive on the wire: two shapes for one
    #: payload is exactly what a hard cut exists to avoid, and a lane that
    #: declared one target normalises to a one-element list.
    targets: tuple[str, ...]
    #: (B007/A-432) present **iff** the lane declared the plural spelling, so
    #: its ABSENCE is the checkable statement "one declared target, no
    #: aggregation policy" rather than an invented value. A member of
    #: :data:`CANARY_AGGREGATIONS`.
    aggregation: str | None = None

    def __post_init__(self) -> None:
        _check_nonempty(self.mechanism, "judgment.r3.mechanism")
        if not isinstance(self.targets, tuple) or not self.targets:
            raise ValueError(
                f"judgment.r3.targets must be a non-empty tuple, got "
                f"{self.targets!r}"
            )
        if len(self.targets) > MAX_CANARY_TARGETS:
            raise ValueError(
                f"judgment.r3.targets declares {len(self.targets)} targets; "
                f"the bound is {MAX_CANARY_TARGETS} (A-432)"
            )
        duplicates = sorted(
            {t for t in self.targets if list(self.targets).count(t) > 1}
        )
        if duplicates:
            raise ValueError(
                f"judgment.r3.targets declares {duplicates} more than once; "
                f"a repeated probe is the same probe"
            )
        for target in self.targets:
            # P21: the same normalized wire grammar `CanaryAttempt.target`
            # obeys. Both ends of the equality A-152 was waiting for must
            # speak one spelling, or the equality can be satisfied by two
            # records that merely agree on a non-normalized path.
            _check_wire_path(target, "judgment.r3.targets[]")
        if self.aggregation is not None and self.aggregation not in CANARY_AGGREGATIONS:
            raise ValueError(
                f"judgment.r3.aggregation must be one of "
                f"{list(CANARY_AGGREGATIONS)} or absent, got "
                f"{self.aggregation!r}"
            )
        if len(self.targets) > 1 and self.aggregation is None:
            raise ValueError(
                f"judgment.r3 declares {len(self.targets)} targets but no "
                f"aggregation -- with more than one probe the claim's status "
                f"is not defined without one, and assay does not invent a "
                f"policy the lane never stated (A-432)"
            )
        if len(self.targets) == 1 and self.aggregation is not None:
            raise ValueError(
                f"judgment.r3 declares one target and an aggregation "
                f"{self.aggregation!r} -- with one target 'any' and 'all' "
                f"denote the same function, so recording one would record a "
                f"policy the lane never stated (A-432)"
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mechanism": self.mechanism,
            "targets": list(self.targets),
        }
        if self.aggregation is not None:
            payload["aggregation"] = self.aggregation
        return payload


@dataclass(frozen=True, kw_only=True)
class JudgmentR4:
    """(F015/M7, A-433 as amended by A-434, schema v10) the effective R4
    policy: red-first, ``fail-before/pass-after``.

    R4 is the next rung of the ORDERED rigor ladder, not a new keying model
    (DA-R16): claims are keyed by ``rigor``, ``RIGOR_LEVELS`` canonicalises a
    lane's declaration against that order, and
    ``_replace_highest_higher_rigor_claim_with_git_failed`` replaces the
    highest declared higher-rigor claim — so red-first is the claim replaced
    first on a cleanup failure, which is honest: it asserts a strictly more
    specific property than R3's canary ("this test would have caught THIS
    bug" versus "the tests can fail on a synthetic break") and its evidence
    needs TWO materialisations.

    **The wire shape lands at the v10 cut; the PRODUCER lands in phase 3.**
    This class and the ``red_first`` claim payload exist so the drift guard
    pins the shape from the cut onward, before any producer exists — the same
    discipline the reserved ``RED_FIRST_UNPROVEN`` reason code follows
    (A-013/A-086/A-144).
    """

    #: The declared test path(s), project-relative, in the normalized wire
    #: grammar. Ordered, at least one, duplicates refused.
    tests: tuple[str, ...]
    #: The full commit identity of the "broken" commit the tests are run at.
    broken_commit: str
    #: ``"declared"`` or ``"resolved_base"`` — see
    #: :data:`RED_FIRST_COMMIT_SOURCES`. Recorded because a reader must be
    #: able to tell a lane that NAMED a commit from one that inherited its
    #: resolved base, and re-resolving the base later can give a different
    #: answer.
    broken_commit_source: str

    def __post_init__(self) -> None:
        if not isinstance(self.tests, tuple) or not self.tests:
            raise ValueError(
                f"judgment.r4.tests must be a non-empty tuple, got {self.tests!r}"
            )
        duplicates = sorted({t for t in self.tests if list(self.tests).count(t) > 1})
        if duplicates:
            raise ValueError(
                f"judgment.r4.tests declares {duplicates} more than once"
            )
        for test in self.tests:
            _check_wire_path(test, "judgment.r4.tests[]")
        _check_nonempty(self.broken_commit, "judgment.r4.broken_commit")
        if self.broken_commit_source not in RED_FIRST_COMMIT_SOURCES:
            raise ValueError(
                f"judgment.r4.broken_commit_source must be one of "
                f"{list(RED_FIRST_COMMIT_SOURCES)}, got "
                f"{self.broken_commit_source!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tests": list(self.tests),
            "broken_commit": self.broken_commit,
            "broken_commit_source": self.broken_commit_source,
        }


@dataclass(frozen=True, kw_only=True)
class RedFirstResult:
    """(F015/M7, A-433/A-434, schema v10) the R4 claim payload: BOTH recorded
    outcomes, so a consumer re-derives the status rather than trusting it.

    ``assay verify`` re-derives ``PASS`` **iff**
    ``before_outcome != PASS and after_outcome == PASS`` — hand-transcribed
    (A-182), never by importing this model. Every other combination is
    ``FAIL``/``RED_FIRST_UNPROVEN`` (A-434 correcting A-433 under DA-R18):
    both commits materialised and both runs completed, so assay measured
    something — that the declared test does not discriminate the fix — which
    is ``CANARY_SURVIVED`` one rung down and belongs in the same class.

    :attr:`after_outcome` is absent exactly when the before-run already ended
    the claim (the test PASSED at the broken commit, so running HEAD proves
    nothing further). Failures of the MECHANISM itself — snapshot, overlay,
    deadline, unreadable output — never reach this payload at all; they keep
    the outcome and code their own substrate raises.
    """

    broken_commit: str
    tests: tuple[str, ...]
    before_outcome: Outcome
    after_outcome: Outcome | None = None

    def __post_init__(self) -> None:
        _check_nonempty(self.broken_commit, "red_first.broken_commit")
        if not isinstance(self.tests, tuple) or not self.tests:
            raise ValueError(
                f"red_first.tests must be a non-empty tuple, got {self.tests!r}"
            )
        for test in self.tests:
            _check_wire_path(test, "red_first.tests[]")
        if not isinstance(self.before_outcome, Outcome):
            raise ValueError(
                f"red_first.before_outcome must be an Outcome, got "
                f"{self.before_outcome!r}"
            )
        if self.after_outcome is not None and not isinstance(
            self.after_outcome, Outcome
        ):
            raise ValueError(
                f"red_first.after_outcome must be an Outcome or absent, got "
                f"{self.after_outcome!r}"
            )
        if self.before_outcome is Outcome.PASS and self.after_outcome is not None:
            raise ValueError(
                "red_first: the declared test PASSED at the broken commit, "
                "which ends the claim -- recording an after_outcome would "
                "assert a HEAD run that answers a question already closed"
            )
        if self.before_outcome is not Outcome.PASS and self.after_outcome is None:
            raise ValueError(
                "red_first: the before-run did not end the claim, so the HEAD "
                "run is what decides it and after_outcome cannot be absent"
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "broken_commit": self.broken_commit,
            "tests": list(self.tests),
            "before_outcome": self.before_outcome.value,
        }
        if self.after_outcome is not None:
            payload["after_outcome"] = self.after_outcome.value
        return payload


@dataclass(frozen=True, kw_only=True)
class Judgment:
    """The resolved judge policy for whichever declared rigor levels
    actually rendered a real computed judgment (P16). P18/P19 populate
    ``r2``/``r3`` and A-148 applies construction-time correspondence checks
    between every populated policy and the :class:`Claim` it describes.
    Schema v3 cannot witness ``r3.target`` (A-152); that requires a payload
    field and a schema migration rather than an inferred correspondence.

    **P33/V5-1: :attr:`resolved` is REQUIRED whenever a judgment exists**,
    and the at-least-one-real-tier guarantee is restored explicitly (A-223g).
    v4 expressed that guarantee as ``minProperties: 1``, which silently
    stopped meaning it the moment a fourth key joined the object: a
    ``judgment`` holding only ``resolved`` would have satisfied the count
    while judging nothing.
    """

    #: (P33/V5-1) required. A judgment describes something that was judged;
    #: what was judged is a fact of the lane, not of any one tier.
    resolved: JudgmentResolved
    r1: JudgmentR1 | None = None
    r2: JudgmentR2 | None = None
    r3: JudgmentR3 | None = None
    #: (F015/A-433, schema v10) the red-first policy. Wire shape only at the
    #: v10 cut; its producer lands in phase 3.
    r4: JudgmentR4 | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.resolved, JudgmentResolved):
            raise ValueError(
                f"judgment.resolved must be a JudgmentResolved, got "
                f"{self.resolved!r}"
            )
        if (
            self.r1 is None
            and self.r2 is None
            and self.r3 is None
            and self.r4 is None
        ):
            raise ValueError(
                "judgment declares none of r1/r2/r3/r4 -- an empty judgment "
                "records no policy and should be omitted (None) instead"
            )
        # A-223a/A-227, widened by wave-1 §6 (A-260) and CORRECTED by
        # B033/A-325.
        #
        # `mode` is a LANE-level scope, not an R1-level one: whichever mode
        # a lane declares, R1 and R2 both judge under it. The rule this
        # check used to encode -- "R2 always compares a base" -- was
        # therefore false from the moment `whole_file_r2` shipped: a
        # whole-target R2 mutates whole declared files and skips both
        # `check_base_is_head` and the `git diff`, exactly as a whole-target
        # R1 resolves nothing against a base. So on a whole-target lane
        # NEITHER tier compares a base, and A-223a's own "present exactly
        # when a tier that reads one is" makes the base FORBIDDEN there.
        #
        # **B035/A-329 completes it, at schema v8.** A-325 had to write this
        # check to the witnessable part only, and in v7 that part had a hole
        # exactly where it mattered most: `judgment.r1.mode` was on the wire
        # and `judgment.r2`'s scope was not, so for an `R0,R2` lane -- every
        # SQL lane, and dstdns's `cw2b_schema` specifically -- NOTHING
        # distinguished a diff-based R2 (which must carry a base) from a
        # whole-target one (which must not), and neither half of the rule
        # could be enforced. `judgment.r2.mode` now records that scope, so
        # the check below reads the lane's mode off whichever tier witnesses
        # it and enforces both halves for every shape:
        #
        # * r1 present -- its `mode` IS the lane's mode.
        # * r1 absent, r2 present -- `r2.mode` is, and this is the case B035
        #   existed for. A diff-based `R0,R2` verdict that omits its base is
        #   refused again, which 2.4.1 did and 2.4.2 (A-325) could not.
        # * neither -- R3 alone never has a base.
        #
        # Where BOTH tiers are present their modes must AGREE, because there
        # is only one lane scope to record: two disagreeing tiers would make
        # "the lane's mode" ambiguous, and an ambiguous premise cannot carry
        # a rule. The same argument applies to `targets`, which both tiers
        # record as *the declared set* -- so they are compared too, for
        # A-152's reason one level over (an equality no JSON Schema can
        # express belongs to this layer and the raw verifier, not the
        # document).
        lane_mode: str | None = None
        witness = ""
        if self.r1 is not None:
            lane_mode, witness = self.r1.mode, "r1"
            if self.r2 is not None:
                if self.r2.mode != self.r1.mode:
                    raise ValueError(
                        f"judgment.r1 records mode {self.r1.mode!r} but "
                        f"judgment.r2 records {self.r2.mode!r} -- mode is a "
                        f"LANE-level scope that both tiers judge under, so "
                        f"one judgment cannot hold two of them"
                    )
                if self.r2.targets != self.r1.targets:
                    raise ValueError(
                        f"judgment.r1 records targets "
                        f"{list(self.r1.targets or ())} but judgment.r2 "
                        f"records {list(self.r2.targets or ())} -- both "
                        f"record the DECLARED target set of one lane, so "
                        f"they cannot differ"
                    )
        elif self.r2 is not None:
            lane_mode, witness = self.r2.mode, "r2"

        if lane_mode == "changed_lines":
            if self.resolved.base is None:
                raise ValueError(
                    f"judgment carries {witness} in changed-line mode but "
                    f"judgment.resolved records no base -- a changed-line "
                    f"judgment is scoped to a resolved comparison commit, so "
                    f"omitting it leaves the judgment unre-derivable"
                )
        elif self.resolved.base is not None:
            if lane_mode == "whole_target":
                raise ValueError(
                    f"judgment.resolved records base {self.resolved.base!r} "
                    f"but judgment carries {witness} in whole-target mode -- "
                    f"whole-target scope replaces the diff at EVERY tier, so "
                    f"neither R1 nor R2 resolved anything against that "
                    f"commit and recording it would imply a comparison that "
                    f"never happened"
                )
            raise ValueError(
                f"judgment.resolved records base {self.resolved.base!r} but "
                f"judgment carries neither r1 nor r2 -- R3 and R4 alone never "
                f"resolve anything against a base"
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"resolved": self.resolved.to_dict()}
        if self.r1 is not None:
            payload["r1"] = self.r1.to_dict()
        if self.r2 is not None:
            payload["r2"] = self.r2.to_dict()
        if self.r3 is not None:
            payload["r3"] = self.r3.to_dict()
        if self.r4 is not None:
            payload["r4"] = self.r4.to_dict()
        return payload


@dataclass(frozen=True, kw_only=True)
class Claim:
    """One COMPUTED declared rigor level's evidence (A-024)."""

    rigor: str
    source: str
    status: Outcome
    verified_by_assay: bool
    reason_code: ReasonCode | None = None
    coverage: Coverage | None = None
    canary: CanaryResult | None = None
    mutation: Mutation | None = None
    #: (F015/A-433, schema v10) the R4 payload. Wire shape only at the cut.
    red_first: RedFirstResult | None = None
    #: (B053/DA-D2(c), A-428, schema v10) the refusing sentence, byte-copied
    #: from the text the same conversion site puts on the diagnostics stream,
    #: so the line a caller reads and the document a consumer archives can
    #: never say different things.
    #:
    #: **Per CLAIM, not per verdict**: one lane can refuse at two tiers for
    #: two different reasons, and a single verdict-level string would have to
    #: pick one. **On NON-PASS claims only** — absent on ``PASS`` (there is
    #: no refusal to explain) and absent when no refusal produced text.
    #:
    #: **Declared, not verified** (A-230a, ``producer_tool``'s own words):
    #: assay copies this text and never parses it; this layer and
    #: ``assay verify`` check the PRESENCE rule and the BYTE bound and never
    #: the content. The closed ``(status, reason_code)`` pair remains the
    #: only machine-readable statement in the document (A-138/A-170), and a
    #: consumer that pattern-matches ``detail`` is reading diagnosis as
    #: policy.
    detail: str | None = None
    #: (B053/A-428) B014's truncation convention: the bytes removed, present
    #: if and only if :attr:`detail` is, so a reader can never mistake a
    #: short message for a cut one. Truncation keeps the HEAD — see
    #: :data:`CLAIM_DETAIL_BYTES`.
    detail_dropped_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.rigor not in RIGOR_LEVELS:
            raise ValueError(
                f"claim rigor must be one of {list(RIGOR_LEVELS)}, got {self.rigor!r}"
            )
        if self.source not in CLAIM_SOURCES:
            raise ValueError(
                f"claim[{self.rigor}] source must be one of {list(CLAIM_SOURCES)}, "
                f"got {self.source!r}"
            )
        if not isinstance(self.status, Outcome):
            raise ValueError(
                f"claim[{self.rigor}] status must be an Outcome, got {self.status!r}"
            )
        if not isinstance(self.verified_by_assay, bool):
            raise ValueError(
                f"claim[{self.rigor}] verified_by_assay must be a boolean, got "
                f"{self.verified_by_assay!r}"
            )
        _check_reason_code(self.status, self.reason_code, f"claim[{self.rigor}]")
        if self.reason_code in {
            ReasonCode.MISSING_ATTESTATION,
            ReasonCode.STALE_ATTESTATION,
        }:
            raise ValueError(
                f"claim[{self.rigor}]: {self.reason_code} belongs to external "
                f"attested evidence, not a computed rigor claim"
            )

        if not self.verified_by_assay:
            raise ValueError(
                f"claim[{self.rigor}]: a computed claim must carry "
                f"verified_by_assay=True; external evidence belongs in evidence[]"
            )

        if self.coverage is not None:
            if self.rigor != "R1":
                raise ValueError(
                    f"claim[{self.rigor}]: a coverage payload belongs to the R1 "
                    f"claim; R1 is the level that declares a coverage floor"
                )
            # A-025 at claim level.
            if self.status is Outcome.NO_MEASUREMENT:
                raise ValueError(
                    f"claim[{self.rigor}]: NO_MEASUREMENT carries no coverage "
                    f"payload at all — omitted, not zeroed. A consumer that reads "
                    f"pct and ignores status must find nothing to read"
                )

        if self.canary is not None:
            # A-108: the exact R1/coverage template above, restated for R3.
            if self.rigor != "R3":
                raise ValueError(
                    f"claim[{self.rigor}]: a canary payload belongs to the R3 "
                    f"claim; R3 is the level that declares a canary"
                )
            if self.status is Outcome.NO_MEASUREMENT:
                raise ValueError(
                    f"claim[{self.rigor}]: NO_MEASUREMENT carries no canary "
                    f"payload at all — omitted, not zeroed, the same discipline "
                    f"A-025 applies to coverage"
                )

        if self.mutation is not None:
            # A-116: the exact R1/coverage template above, restated for R2.
            # mutation's PRESENCE is baseline-conditional -- None means
            # mutation testing never started (the baseline itself never
            # resolved to PASS); present means it did, even if every
            # attempted mutant crashed.
            if self.rigor != "R2":
                raise ValueError(
                    f"claim[{self.rigor}]: a mutation payload belongs to the R2 "
                    f"claim; R2 is the level that declares mutation testing"
                )
            if self.status is Outcome.NO_MEASUREMENT:
                raise ValueError(
                    f"claim[{self.rigor}]: NO_MEASUREMENT carries no mutation "
                    f"payload at all — omitted, not zeroed, the same discipline "
                    f"A-025 applies to coverage"
                )

        if self.red_first is not None:
            # A-433: the exact R1/coverage template above, restated for R4.
            if self.rigor != "R4":
                raise ValueError(
                    f"claim[{self.rigor}]: a red_first payload belongs to the "
                    f"R4 claim; R4 is the level that declares red-first"
                )
            if self.status is Outcome.NO_MEASUREMENT:
                raise ValueError(
                    f"claim[{self.rigor}]: NO_MEASUREMENT carries no red_first "
                    f"payload at all — omitted, not zeroed, the same discipline "
                    f"A-025 applies to coverage"
                )

        self._check_detail()
        self._check_a_judged_status_carries_its_own_payload()
        self._check_mutation_terminal_correspondence()
        self._check_r1_only_reason_codes()
        self._check_red_first_reason_code()

    def _check_red_first_reason_code(self) -> None:
        """(A-433 as amended by A-434/DA-R18) ``RED_FIRST_UNPROVEN`` is read
        off R4's own two recorded outcomes and nowhere else -- the identical
        binding :meth:`_check_r1_only_reason_codes` applies one tier down."""
        if self.reason_code is ReasonCode.RED_FIRST_UNPROVEN and self.rigor != "R4":
            raise ValueError(
                f"claim[{self.rigor}]: RED_FIRST_UNPROVEN describes the R4 "
                f"fail-before/pass-after judgement and belongs to the R4 claim"
            )

    def _check_detail(self) -> None:
        """(B053/DA-D2(c), A-428) the presence rule and the BYTE bound —
        never the content.

        Three rules, and only three, because ``detail`` is
        declared-not-verified (A-230a): it is present only where a refusal
        could have produced text, its sibling byte count is present exactly
        with it, and the retained text fits :data:`CLAIM_DETAIL_BYTES`. What
        it SAYS is diagnosis, and this layer does not read diagnosis.
        """
        if self.detail is None:
            if self.detail_dropped_bytes is not None:
                raise ValueError(
                    f"claim[{self.rigor}]: detail_dropped_bytes "
                    f"{self.detail_dropped_bytes!r} with no detail -- the "
                    f"count describes a truncation of text that is not here"
                )
            return
        if self.status is Outcome.PASS:
            raise ValueError(
                f"claim[{self.rigor}]: a PASS carries no detail -- detail is "
                f"the refusing sentence, and a pass refused nothing (A-428)"
            )
        if not isinstance(self.detail, str) or not self.detail:
            raise ValueError(
                f"claim[{self.rigor}]: detail must be a non-empty string when "
                f"present, got {self.detail!r} -- absent says 'no refusal "
                f"produced text', which an empty string cannot say"
            )
        if len(self.detail.encode("utf-8")) > CLAIM_DETAIL_BYTES:
            raise ValueError(
                f"claim[{self.rigor}]: detail is "
                f"{len(self.detail.encode('utf-8'))} UTF-8 bytes, over the "
                f"{CLAIM_DETAIL_BYTES}-byte bound (A-428); truncate to the "
                f"HEAD and record the removed bytes in detail_dropped_bytes"
            )
        if (
            self.detail_dropped_bytes is None
            or isinstance(self.detail_dropped_bytes, bool)
            or not isinstance(self.detail_dropped_bytes, int)
            or self.detail_dropped_bytes < 0
        ):
            raise ValueError(
                f"claim[{self.rigor}]: detail requires detail_dropped_bytes as "
                f"a non-negative integer, got {self.detail_dropped_bytes!r} -- "
                f"a silently truncated sentence is worse than no sentence "
                f"(A-428/B014)"
            )

    def _check_r1_only_reason_codes(self) -> None:
        """(wave-1 §4/§5, A-259/A-260) ``BRANCH_UNAVAILABLE`` and
        ``TARGET_NOT_MEASURED`` are read off R1's own guard sequence and
        nowhere else -- the identical reasoning
        :meth:`_check_mutation_terminal_correspondence` already gives for
        binding ``MUTATION_UNSUPPORTED``/``MUTATION_DISCOVERY_FAILED`` to
        R2, one tier over. Both are payload-free by construction (guarded
        before :func:`assay.evaluate.evaluate_coverage`/``evaluate_targets``
        ever runs), so this only needs to bind the RIGOR -- the payload
        constraint is already enforced above (NO_MEASUREMENT carries no
        coverage payload at all).
        """
        if self.reason_code in (
            ReasonCode.BRANCH_UNAVAILABLE,
            ReasonCode.TARGET_NOT_MEASURED,
        ):
            if self.rigor != "R1":
                raise ValueError(
                    f"claim[{self.rigor}]: {self.reason_code.value} describes "
                    f"R1's own measurability guards and belongs to the R1 claim"
                )

    def _check_mutation_terminal_correspondence(self) -> None:
        """(P21/A-163/A-183) the three R2 terminals that are ABOUT a payload
        must agree with whether one is present, and with its exact shape.

        Four rules, each closing a forgery that v3 could not see:

        * ``MUTATION_UNSUPPORTED`` is payload-free. It says the adapter never
          analysed anything; attaching a zero/zero :class:`Mutation` would
          make capability absence indistinguishable from a supported
          analysis that observed nothing — the precise collapse A-183 exists
          to prevent.
        * ``MUTATION_DISCOVERY_FAILED`` is payload-free for the same reason
          one level over: a boundary that failed produced no candidate set.
        * A limit SENTINEL payload is legal only under
          ``BUDGET_EXCEEDED``/``MUTANT_LIMIT_EXCEEDED``. Otherwise a claim
          could report ``PASS`` while its payload says nothing ran.
        * ``MUTANT_LIMIT_EXCEEDED`` conversely REQUIRES that exact sentinel,
          so the reason cannot be worn by an ordinary payload.
        """
        payload_free = {
            ReasonCode.MUTATION_UNSUPPORTED,
            ReasonCode.MUTATION_DISCOVERY_FAILED,
        }
        if self.reason_code in payload_free:
            if self.rigor != "R2":
                raise ValueError(
                    f"claim[{self.rigor}]: {self.reason_code.value} describes "
                    f"mutation discovery and belongs to the R2 claim"
                )
            if self.mutation is not None:
                raise ValueError(
                    f"claim[R2]: {self.reason_code.value} carries a mutation "
                    f"payload -- no candidate analysis produced it, so an "
                    f"attached payload would assert a measurement that never "
                    f"happened"
                )
        if self.mutation is not None and self.mutation.is_limit_sentinel:
            if (self.status, self.reason_code) != (
                Outcome.BUDGET_EXCEEDED,
                ReasonCode.MUTANT_LIMIT_EXCEEDED,
            ):
                raise ValueError(
                    f"claim[{self.rigor}]: a pre-submission limit sentinel "
                    f"(candidate_count "
                    f"{self.mutation.candidate_count}, zero attempted) is only "
                    f"legal as BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED, got "
                    f"{self.status.value}/"
                    f"{None if self.reason_code is None else self.reason_code.value}"
                )
        if self.reason_code is ReasonCode.MUTANT_LIMIT_EXCEEDED:
            if self.rigor != "R2":
                raise ValueError(
                    f"claim[{self.rigor}]: MUTANT_LIMIT_EXCEEDED describes "
                    f"mutation discovery and belongs to the R2 claim"
                )
            if self.mutation is None or not self.mutation.is_limit_sentinel:
                raise ValueError(
                    "claim[R2]: MUTANT_LIMIT_EXCEEDED requires the exact "
                    "pre-submission sentinel payload (total 0, five empty "
                    "buckets, a positive candidate_count) as its evidence"
                )

    def _check_a_judged_status_carries_its_own_payload(self) -> None:
        """The converse of the three ``NO_MEASUREMENT`` rules above (P16
        review): those forbid a payload where none was measured; this
        forbids a *judged status* where no payload was measured.

        Without it, ``assay verify``'s R1/R2/R3 re-derivation is trivially
        evaded — not by a contradictory payload (sol finding 2's three
        artifacts) but by DELETING the payload. Each re-derivation function
        has nothing to judge and returns; the top-level rollup still agrees;
        the artifact is accepted. A ``PASS`` claim backed by no evidence at
        all is the strongest form of exactly the lie P16 exists to catch.

        Each rule states only what its own producer can prove:

        * R1 — :func:`assay.evaluate.evaluate_coverage` returns ``PASS`` or
          ``FAIL`` and *always* with a :class:`Coverage`; the three
          ``NO_MEASUREMENT`` causes are guarded before it is ever called
          (A-090) and carry no payload by the rule above.
        * R2 — :func:`assay.mutation.judge_mutation` reaches ``PASS`` only
          on the ``mutation is not None`` branch; ``None`` means the
          baseline never resolved to ``PASS`` (A-116) and the claim reuses
          that non-``PASS`` baseline's own outcome verbatim. Every other
          status stays representable without a payload, because a failed
          baseline is exactly how they arise.
        * R3 — :func:`assay.canary.judge_canary` returns ``PASS``/``FAIL``/
          ``INCONCLUSIVE``, and :func:`assay.canary.build_canary_claim`
          attaches the :class:`CanaryResult` it judged on every one of
          them. ``ERROR``/``BUDGET_EXCEEDED`` stay representable without
          one: those describe the canary machinery failing to produce a
          result at all, not a judgement of one.
        """
        if self.rigor == "R1" and self.coverage is None:
            if self.status in {Outcome.PASS, Outcome.FAIL}:
                raise ValueError(
                    f"claim[R1]: {self.status.value} without a coverage "
                    f"payload -- a changed-line judgement that names no "
                    f"coverage cannot be re-derived by anyone but its own "
                    f"producer"
                )
        if self.rigor == "R2" and self.mutation is None:
            if self.status is Outcome.PASS:
                raise ValueError(
                    "claim[R2]: PASS without a mutation payload -- an "
                    "absent payload means mutation testing never began "
                    "(the baseline never passed), which cannot itself pass"
                )
            if self.reason_code in _MUTATION_ONLY_REASON_CODES:
                raise ValueError(
                    f"claim[R2]: {self.reason_code.value} without a mutation "
                    f"payload -- that reason code can only be read off the "
                    f"mutation buckets, so a baseline that never got as far "
                    f"as running mutants cannot produce it"
                )
        if self.rigor == "R3" and self.canary is None:
            if self.status in {Outcome.PASS, Outcome.FAIL, Outcome.INCONCLUSIVE}:
                raise ValueError(
                    f"claim[R3]: {self.status.value} without a canary "
                    f"payload -- that status is a judgement OF a canary "
                    f"result, so the result it judged must be recorded"
                )
        # A-433/A-434: R3's rule one rung up. A judged R4 status is derived
        # from the two recorded outcomes, so the record it judged must be
        # present. ERROR/BUDGET_EXCEEDED/NO_MEASUREMENT stay representable
        # without one: those describe the MECHANISM failing to produce a
        # result at all, not a judgement of one.
        if self.rigor == "R4" and self.red_first is None:
            if self.status in {Outcome.PASS, Outcome.FAIL}:
                raise ValueError(
                    f"claim[R4]: {self.status.value} without a red_first "
                    f"payload -- that status is a judgement OF a "
                    f"fail-before/pass-after run, so the two outcomes it was "
                    f"derived from must be recorded"
                )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rigor": self.rigor,
            "source": self.source,
            "status": self.status.value,
            "verified_by_assay": self.verified_by_assay,
        }
        # Omitted, never null (A-051).
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code.value
        if self.coverage is not None:
            payload["coverage"] = self.coverage.to_dict()
        if self.canary is not None:
            payload["canary"] = self.canary.to_dict()
        if self.mutation is not None:
            payload["mutation"] = self.mutation.to_dict()
        if self.red_first is not None:
            payload["red_first"] = self.red_first.to_dict()
        if self.detail is not None:
            payload["detail"] = self.detail
            payload["detail_dropped_bytes"] = self.detail_dropped_bytes
        return payload


def _check_nonempty(value: str, what: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} must be a non-empty string, got {value!r}")


@dataclass(frozen=True, kw_only=True)
class EvidenceDeclaration:
    """One externally sourced requirement, identified independently of rigor."""

    source: str
    key: str

    def __post_init__(self) -> None:
        if self.source not in EVIDENCE_SOURCES:
            raise ValueError(
                f"evidence declaration source must be one of "
                f"{list(EVIDENCE_SOURCES)}, got {self.source!r}"
            )
        _check_nonempty(self.key, "evidence declaration key")

    @property
    def identity(self) -> tuple[str, str]:
        return self.source, self.key

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "key": self.key}


@dataclass(frozen=True, kw_only=True)
class Evidence:
    """One adjudicated or attested result, keyed by ``(source, key)``.

    Attestation loading and git comparison are intentionally not implemented
    here. The contract records the inputs and the honest result: an equal
    commit, or an ancestor whose reviewed paths are unchanged, may satisfy the
    declaration; changed reviewed paths render NO_MEASUREMENT / STALE_ATTESTATION.
    """

    source: str
    key: str
    status: Outcome
    verified_by_assay: bool
    reason_code: ReasonCode | None = None
    producer: str | None = None
    attested_commit: str | None = None
    reviewed_paths: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.source not in EVIDENCE_SOURCES:
            raise ValueError(
                f"evidence source must be one of {list(EVIDENCE_SOURCES)}, got "
                f"{self.source!r}"
            )
        _check_nonempty(self.key, "evidence key")
        if not isinstance(self.status, Outcome):
            raise ValueError(
                f"evidence[{self.source}:{self.key}] status must be an Outcome, "
                f"got {self.status!r}"
            )
        if not isinstance(self.verified_by_assay, bool):
            raise ValueError(
                f"evidence[{self.source}:{self.key}] verified_by_assay must be "
                f"a boolean, got {self.verified_by_assay!r}"
            )
        label = f"evidence[{self.source}:{self.key}]"
        _check_reason_code(self.status, self.reason_code, label)
        if self.reason_code in {
            ReasonCode.MISSING_ATTESTATION,
            ReasonCode.STALE_ATTESTATION,
        } and self.source != "attested":
            raise ValueError(f"{label}: {self.reason_code} requires attested source")

        payload = (self.producer, self.attested_commit, self.reviewed_paths)
        if self.source == "adjudicated" and any(value is not None for value in payload):
            raise ValueError(f"{label}: attestation payload belongs only to attested evidence")
        if self.source == "adjudicated" and self.verified_by_assay:
            # B004/A-430, schema v10. Up to v9 `verified_by_assay` was
            # constrained to False only under `source == "attested"`, and the
            # `adjudicated` branch left it an unconstrained boolean -- so a
            # Tier-2 result could ship `verified_by_assay: true` and be legal
            # in BOTH layers, reading as computed. Tier 2 is a document assay
            # READ, never a measurement assay made, and B004 is the first
            # adjudicated producer, so the narrowing lands in the bump that
            # carries its reason code (A-029: a narrowing is still a
            # compatibility fact).
            raise ValueError(
                f"{label}: adjudicated evidence always carries "
                f"verified_by_assay=False; assay judged a document it read, "
                f"it did not compute the fact the document asserts"
            )
        if self.source == "attested":
            if self.verified_by_assay:
                raise ValueError(
                    f"{label}: attested evidence always carries "
                    f"verified_by_assay=False; assay never upgrades a review"
                )
            has_payload = any(value is not None for value in payload)
            if has_payload and not all(value is not None for value in payload):
                raise ValueError(
                    f"{label}: producer, attested_commit and reviewed_paths are "
                    f"all present or all absent"
                )
            if self.status is Outcome.PASS or self.reason_code is ReasonCode.STALE_ATTESTATION:
                if not all(value is not None for value in payload):
                    raise ValueError(
                        f"{label}: a present attestation requires producer, "
                        f"attested_commit and reviewed_paths"
                    )
            if self.reason_code is ReasonCode.MISSING_ATTESTATION and has_payload:
                raise ValueError(f"{label}: missing attestation cannot carry its payload")
            if self.producer is not None:
                _check_nonempty(self.producer, f"{label} producer")
                _check_nonempty(self.attested_commit, f"{label} attested_commit")
                if not self.reviewed_paths:
                    raise ValueError(f"{label} reviewed_paths must not be empty")
                for path in self.reviewed_paths:
                    _check_nonempty(path, f"{label} reviewed path")
                if len(set(self.reviewed_paths)) != len(self.reviewed_paths):
                    raise ValueError(f"{label} reviewed_paths contains a duplicate")

    @property
    def identity(self) -> tuple[str, str]:
        return self.source, self.key

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self.source,
            "key": self.key,
            "status": self.status.value,
            "verified_by_assay": self.verified_by_assay,
        }
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code.value
        if self.producer is not None:
            payload["producer"] = self.producer
            payload["attested_commit"] = self.attested_commit
            payload["reviewed_paths"] = list(self.reviewed_paths or ())
        return payload


@dataclass(frozen=True, kw_only=True)
class Helper:
    """(P33/V5-5) one external helper an adapter actually INVOKED.

    ``LanguageAdapter.external_tools`` (A-013) declares what a lane *needs*;
    nothing recorded what actually *ran*. The P27 probe's whole pinning
    exercise existed because a symbolic toolchain reference makes a verdict
    unreproducible, and Go's eventual statement-position oracle (A-217) and
    SQL's parser are the same architectural shape — an adapter shelling out
    to a language tool that must be exact.

    :attr:`identity` is what the tool reports about ITSELF, verbatim (``go
    version go1.25.12 linux/amd64``), never inferred from a package name or
    a tag: the inference is the part that goes stale silently.

    P33 only VALIDATES this array; it populates nothing, because it ships no
    helper-invoking adapter. The emission default is therefore OMISSION
    (A-230a) — an artifact that used no helper has no ``helpers`` key and
    never serialises ``helpers: []``, the same rule ``judgment`` already
    follows when nothing was judged.
    """

    role: str
    tool: str
    resolved_path: str
    identity: str

    def __post_init__(self) -> None:
        if self.role not in HELPER_ROLES:
            raise ValueError(
                f"helpers[].role must be one of {list(HELPER_ROLES)}, got "
                f"{self.role!r}"
            )
        for name in ("tool", "resolved_path", "identity"):
            _check_nonempty(getattr(self, name), f"helpers[].{name}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "tool": self.tool,
            "resolved_path": self.resolved_path,
            "identity": self.identity,
        }


@dataclass(frozen=True, kw_only=True)
class SnapshotPolicy:
    """(B006(a)/A-269, ``W1-CARVE-B006a-project-scope.md`` §5) the
    lane-selected initial worktree materialisation policy -- a POLICY
    record, never a claim that materialisation reached any particular
    phase. Replaces the withdrawn ``isolation`` object this wave's own
    first design specified (A-266/A-267/A-268 are superseded in full by
    A-269 for exactly this shape).

    Whenever a higher-rigor command unit actually ran -- whether its
    eventual judged claim passed or failed -- the materialiser consumed the
    SAME immutable policy object recorded here (an end-to-end differential
    test proves that wiring; this class does not and cannot). On an early
    refusal it truthfully records only the policy under which assay
    attempted or refused the lane; the refusal does not masquerade as a
    completed snapshot. There is deliberately no
    ``materialisation: none|partial|complete`` field: the runner's shared
    exception state cannot distinguish those phases at every producer site
    (the withdrawn design's own underivability defect), and this design does
    not need one -- ``snapshot_policy`` says what was SELECTED, while
    ``claims``/``outcome`` already say whether a measurement completed.
    """

    #: mirrors :data:`assay.config.SNAPSHOT_SELECTIONS` exactly.
    selection: str
    #: required, non-empty, 1..64 entries, strictly ascending by UTF-8
    #: bytes iff ``selection == "repository-minus-unsafe-symlinks"``;
    #: forbidden (``None``) under ``"repository"``.
    unsafe_symlink_omissions: tuple[str, ...] | None = None
    #: (B041(b), schema v9) the declared ``[lanes.<n>.isolation] link_paths``
    #: this lane materialised as symlinks from the INVOKING CHECKOUT into the
    #: snapshot, immediately after ``read-tree`` and before any command ran.
    #: Omitted (``None``), never empty (A-051): a lane that declared none
    #: records none.
    #:
    #: Recorded because it is the one thing about a snapshot that is NOT bound
    #: to the recorded commit. Everything else the snapshot holds came out of
    #: the Git object store at ``commit``; every path listed here came out of
    #: whatever the invoking checkout happened to contain at run time. A
    #: verdict carrying a non-empty ``link_paths`` is therefore stating
    #: plainly that its judgment's dependency closure is only as reproducible
    #: as that checkout was -- which is the honest trade the feature exists to
    #: make visible, not to hide.
    #:
    #: Independent of :attr:`selection`: linking content IN is orthogonal to
    #: the unsafe-symlink omission policy, so unlike
    #: :attr:`unsafe_symlink_omissions` there is no pairing between the two.
    link_paths: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        self._check_link_paths()
        if self.selection not in SNAPSHOT_SELECTIONS:
            raise ValueError(
                f"snapshot_policy.selection must be one of "
                f"{sorted(SNAPSHOT_SELECTIONS)}, got {self.selection!r}"
            )
        if self.selection == "repository":
            if self.unsafe_symlink_omissions is not None:
                raise ValueError(
                    f"snapshot_policy.unsafe_symlink_omissions is present "
                    f"({list(self.unsafe_symlink_omissions)}) under "
                    f"selection = 'repository', where it is forbidden"
                )
            return
        # selection == "repository-minus-unsafe-symlinks"
        omissions = self.unsafe_symlink_omissions
        if not isinstance(omissions, tuple) or not (1 <= len(omissions) <= 64):
            raise ValueError(
                f"snapshot_policy.unsafe_symlink_omissions must be a tuple "
                f"of 1..64 entries under selection = "
                f"'repository-minus-unsafe-symlinks', got {omissions!r}"
            )
        encoded: list[bytes] = []
        for index, path in enumerate(omissions):
            what = f"snapshot_policy.unsafe_symlink_omissions[{index}]"
            _check_wire_path(path, what)
            # B006a §5.3's own grammar additionally forbids a bare `.git`
            # component -- `_check_wire_path` does not (its own callers
            # never need that restriction), so it is checked here too.
            if ".git" in path.split("/"):
                raise ValueError(f"{what} {path!r} contains a '.git' component")
            encoded.append(path.encode("utf-8"))
        if any(len(item) > 4096 for item in encoded):
            raise ValueError(
                "snapshot_policy.unsafe_symlink_omissions entries must be "
                "at most 4096 UTF-8 bytes each"
            )
        for previous, current in zip(encoded, encoded[1:]):
            if not previous < current:
                raise ValueError(
                    f"snapshot_policy.unsafe_symlink_omissions must be "
                    f"strictly ascending by UTF-8 bytes, got "
                    f"{list(omissions)}"
                )

    def _check_link_paths(self) -> None:
        """(B041(b)) ``unsafe_symlink_omissions``' own grammar, one field
        over: 1..64 entries, the B006a §5.3 tree-path grammar (no ``.git``
        component), at most 4096 UTF-8 bytes each, strictly ascending by
        those bytes. Deliberately transcribed rather than factored into a
        shared helper: the two lists mean opposite things -- one names what
        the snapshot LEFT OUT, the other what was linked IN -- and a shared
        implementation would let one edit silently weaken both.
        """
        if self.link_paths is None:
            return
        if not isinstance(self.link_paths, tuple) or not (
            1 <= len(self.link_paths) <= 64
        ):
            raise ValueError(
                f"snapshot_policy.link_paths must be a tuple of 1..64 entries "
                f"when present -- a lane declaring none records none, never an "
                f"empty list (A-051); got {self.link_paths!r}"
            )
        encoded: list[bytes] = []
        for index, path in enumerate(self.link_paths):
            what = f"snapshot_policy.link_paths[{index}]"
            _check_wire_path(path, what)
            if ".git" in path.split("/"):
                raise ValueError(f"{what} {path!r} contains a '.git' component")
            encoded.append(path.encode("utf-8"))
        if any(len(item) > 4096 for item in encoded):
            raise ValueError(
                "snapshot_policy.link_paths entries must be at most 4096 "
                "UTF-8 bytes each"
            )
        for previous, current in zip(encoded, encoded[1:]):
            if not previous < current:
                raise ValueError(
                    f"snapshot_policy.link_paths must be strictly ascending "
                    f"by UTF-8 bytes, got {list(self.link_paths)}"
                )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"selection": self.selection}
        if self.unsafe_symlink_omissions is not None:
            payload["unsafe_symlink_omissions"] = list(self.unsafe_symlink_omissions)
        if self.link_paths is not None:
            payload["link_paths"] = list(self.link_paths)
        return payload


@dataclass(frozen=True, kw_only=True)
class Verdict:
    """One verdict: one lane, one commit (§7).

    Every rule the shipped schema encodes is also refused here, plus the two it
    cannot encode — that ``claims`` covers ``declared_rigor`` exactly, and that
    ``argv_effective`` really is ``argv_declared + argv_appended``. Both compare
    two locations in one instance, which JSON Schema draft 2020-12 has no way to
    express (there is no ``$data``); so they are construction-time rejections
    rather than validation rejections, and the tests assert them with
    ``pytest.raises``, never by asserting the model emits the right thing.
    """

    lane: str
    commit: str
    outcome: Outcome
    started: str
    ended: str
    assay_version: str
    #: (B018/A-327) the producing judge's build identity -- see
    #: :class:`JudgeProvenance`. OPTIONAL, and absent rather than partial: a
    #: source-tree invocation has no build artifact to name, and inventing a
    #: digest for `src/` would be exactly the laundering this field exists to
    #: prevent. `assay run --require-judge-provenance` is how a consumer that
    #: needs the binding turns its absence into a refusal instead of a silence.
    judge_provenance: JudgeProvenance | None = None
    claims: tuple[Claim, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    reason_code: ReasonCode | None = None
    declared_rigor: tuple[str, ...] | None = None
    declared_evidence: tuple[EvidenceDeclaration, ...] | None = None
    argv_declared: tuple[str, ...] | None = None
    argv_appended: tuple[str, ...] | None = None
    argv_effective: tuple[str, ...] | None = None
    env_declared: Mapping[str, str] | None = None
    env_effective: Mapping[str, str] | None = None
    #: (B025) True exactly when `env_effective` above is NOT the real
    #: resolved environment -- a refusal whose own infrastructure
    #: declaration was itself unresolvable falls back to `lane.env` alone
    #: (never infrastructure or `env_passthrough` values, since neither
    #: could be safely completed) rather than leaving the whole refusal an
    #: uncaught exception with no artifact at all. `False` (the default)
    #: means `env_effective` is the real, fully-resolved environment, as
    #: every verdict before this field existed already implied.
    env_effective_incomplete: bool = False
    #: (P16) the lane's own declared scope/enforcement — join the
    #: lane-resolved group (:data:`LANE_RESOLVED_FIELDS`), always known the
    #: moment a lane loads.
    scope: str | None = None
    enforcement: str | None = None
    #: (B043, schema v9) the lane's declared working directory, exactly as the
    #: lane file spelled it -- the directory this lane's command, every R2
    #: candidate re-execution and every R3 canary run were rooted at, resolved
    #: against the snapshot's project root.
    #:
    #: **Deliberately NOT a member of** :data:`LANE_RESOLVED_FIELDS`. Every
    #: field in that group is all-present-or-all-absent and carries a full
    #: ``dependentRequired`` cross-matrix in the packaged schema, because each
    #: is known the moment a lane loads and none of them has an honest
    #: "unset". ``cwd`` does: B043's contract says the verdict records it
    #: "absent when not declared -- absent, never ``"."``". Joining that group
    #: would make every verdict from every lane that declares no ``cwd``
    #: schema-invalid, which is nearly all of them.
    #:
    #: NOTHING ELSE RE-ROOTS (A-271, one path grammar): ``judge.coverage.
    #: artifact``, ``equivalence_artifact``, ``source_roots``, ``targets`` and
    #: every infrastructure fact stay project-root-relative whatever this
    #: says, and ``environment_command`` keeps running in the INVOKING
    #: environment's cwd because it is not the lane command.
    cwd_declared: str | None = None
    #: (P16) the resolved judge policy behind whichever claims rendered a
    #: real computed judgment — see :class:`Judgment` and
    #: :meth:`_check_judgment_matches_claims`. Independently optional (not
    #: part of the strict lane-resolved group): a lane may resolve and
    #: render only R0, which judges nothing R1+ policy could describe.
    judgment: Judgment | None = None
    #: (P33/V5-5) every external helper an adapter actually invoked.
    #: ``None`` means none ran and the key is OMITTED (A-230a); an EMPTY
    #: tuple is refused, because ``helpers: []`` would assert a known-empty
    #: fact that no producer in this build can witness.
    helpers: tuple[Helper, ...] | None = None
    #: (B006(a)/A-269, §5) the lane-selected repository snapshot
    #: materialisation policy -- required whenever ``declared_rigor``
    #: contains R1, R2 or R3; absent for an R0-only verdict and for an
    #: error produced before a lane resolves at all. See
    #: :meth:`_check_snapshot_policy_matches_declared_rigor`.
    snapshot_policy: SnapshotPolicy | None = None
    #: (B014) bounded final output from a failed or timed-out lane command.
    #: ``None`` means no command-output contract applies (a pre-command refusal,
    #: a PASS whose evidence was not requested, or an error before the command);
    #: empty strings mean output was captured and known to be empty.
    result_stdout_tail: str | None = None
    result_stderr_tail: str | None = None
    #: (B014) head-side bytes removed by tailing, so truncation is visible and
    #: bounded rather than silently lossy.
    result_stdout_dropped_bytes: int = 0
    result_stderr_dropped_bytes: int = 0
    schema_version: int = VERDICT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, Outcome):
            raise ValueError(f"outcome must be an Outcome, got {self.outcome!r}")
        for name in ("lane", "commit", "assay_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name!r} must be a non-empty string, got {value!r}")
        for name in ("started", "ended"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
                raise ValueError(
                    f"{name!r} must be an ISO-8601 timestamp with an explicit "
                    f"offset, got {value!r}"
                )
        if self.schema_version != VERDICT_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {VERDICT_SCHEMA_VERSION}, got "
                f"{self.schema_version!r}"
            )
        if self.judge_provenance is not None and not isinstance(
            self.judge_provenance, JudgeProvenance
        ):
            raise ValueError(
                f"judge_provenance must be a JudgeProvenance, got "
                f"{self.judge_provenance!r}"
            )
        if self.env_effective_incomplete and self.declared_rigor is None:
            raise ValueError(
                "env_effective_incomplete requires the lane-resolved group "
                "(declared_rigor and friends) to be present"
            )
        if self.scope is not None and self.scope not in SCOPES:
            raise ValueError(f"scope must be one of {sorted(SCOPES)}, got {self.scope!r}")
        if self.enforcement is not None and self.enforcement not in ENFORCEMENTS:
            raise ValueError(
                f"enforcement must be one of {sorted(ENFORCEMENTS)}, got "
                f"{self.enforcement!r}"
            )
        for name in ("result_stdout_tail", "result_stderr_tail"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"{name} must be a string when present")
                if len(value.encode("utf-8")) > COMMAND_TAIL_BYTES:
                    raise ValueError(
                        f"{name} exceeds {COMMAND_TAIL_BYTES} UTF-8 bytes"
                    )
        for name in (
            "result_stdout_dropped_bytes",
            "result_stderr_dropped_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

        _check_reason_code(self.outcome, self.reason_code, "verdict")
        self._check_interval_is_ordered()
        self._check_lane_resolved_group()
        self._check_cwd_declared()
        self._check_claims_cover_declared_rigor()
        self._check_evidence_covers_declarations()
        self._check_outcome_agrees_with_rollup()
        self._check_judgment_matches_claims()
        self._check_helpers()
        self._check_snapshot_policy_matches_declared_rigor()

    # --- the rules the schema cannot express ---------------------------------

    def _check_snapshot_policy_matches_declared_rigor(self) -> None:
        """(B006(a)/A-269, §5.1) ``snapshot_policy`` is present iff
        ``declared_rigor`` contains R1, R2 or R3 -- required rather than
        optional because "absent" would otherwise be indistinguishable
        between an old producer and a genuinely R0-only run, and the whole
        point is that a reviewer can tell which policy a higher-rigor
        verdict ran under without re-running anything.

        Absent for an R0-only verdict (the loader forbids ``[isolation]``
        there, A-062) and for a verdict where no lane resolved at all (no
        ``declared_rigor`` to test) -- both are truthful absences, never a
        producer that forgot the field.
        """
        higher_rigor_declared = self.declared_rigor is not None and any(
            level != "R0" for level in self.declared_rigor
        )
        if higher_rigor_declared and self.snapshot_policy is None:
            raise ValueError(
                f"declared_rigor {list(self.declared_rigor or ())} contains a "
                f"higher-rigor level but snapshot_policy is absent -- every "
                f"R1/R2/R3 lane resolves an explicit snapshot policy, and a "
                f"verdict that ran one must record which"
            )
        if not higher_rigor_declared and self.snapshot_policy is not None:
            raise ValueError(
                f"snapshot_policy is present ({self.snapshot_policy!r}) but "
                f"declared_rigor is {list(self.declared_rigor) if self.declared_rigor else None} "
                f"-- an R0-only lane (or a verdict where no lane resolved) "
                f"never selects a snapshot policy"
            )

    def _check_helpers(self) -> None:
        """(P33/V5-5, A-223c/A-227) helper correspondence, in the OBSERVABLE
        direction only.

        Every ``helpers`` entry requires a correspondingly-judged claim:
        ``mutation-sites`` an R2 claim carrying a ``mutation`` payload,
        ``statement-positions`` an R1 claim carrying ``coverage``, and
        ``executable-code`` either, because executability gates both
        changed-line classification and mutation-site discovery.

        **The converse is deliberately NOT implemented here.** "A claim
        produced with a helper requires an entry" has no readable antecedent
        — nothing in the artifact bytes says a claim *used* a helper — so any
        implementation of it would be vacuous. P29 owns that direction (A-282:
        route (i) gives SQL ``external_tools = ()``, so P34 can never witness
        it), with the adapter that makes the state reachable (A-142/A-144's
        precedent for deferring a check until a producer makes it witnessable).
        """
        if self.helpers is None:
            return
        if not isinstance(self.helpers, tuple):
            raise ValueError(f"helpers must be a tuple, got {self.helpers!r}")
        if not self.helpers:
            raise ValueError(
                "helpers is present but empty; a run that invoked no helper "
                "OMITS the field entirely (A-230a) rather than recording a "
                "known-empty list nothing witnessed"
            )
        supported = supported_helper_roles(self.claims)
        for helper in self.helpers:
            if helper.role not in supported:
                raise ValueError(
                    f"helpers records a {helper.role!r} helper ({helper.tool!r}) "
                    f"but this verdict does not carry "
                    f"{HELPER_ROLE_REQUIREMENT[helper.role]} -- a helper is "
                    f"recorded because it produced a claim payload, so an entry "
                    f"with no such payload describes work that judged nothing"
                )

    def _check_interval_is_ordered(self) -> None:
        """(P21/A-182, work item 7) ``ended >= started`` as INSTANTS.

        Draft 2020-12 has no ``$data``, so it cannot compare two fields; this
        rule and its differently-worded twin in :mod:`assay.verify` are the
        only two places it exists, and the schema is deliberately not
        credited with it. The comparison is on parsed offset-aware instants,
        never on the strings: the canonical combined artifact starts at
        ``12:00+01:00`` and ends at ``11:01+00:00``, which is a valid
        one-minute window whose lexical order runs backwards.
        """
        started = _instant(self.started, "started")
        ended = _instant(self.ended, "ended")
        if ended < started:
            raise ValueError(
                f"ended {self.ended!r} is before started {self.started!r} "
                f"({ended.astimezone(timezone.utc).isoformat()} < "
                f"{started.astimezone(timezone.utc).isoformat()} in UTC); a "
                f"verdict's interval cannot run backwards"
            )

    def _check_cwd_declared(self) -> None:
        """(B043) ``cwd_declared`` is INDEPENDENTLY optional -- it is not in
        :data:`LANE_RESOLVED_FIELDS` and joins none of that group's
        cross-matrix -- but it is still a fact ABOUT a lane, so it cannot
        appear on a verdict where no lane ever resolved. Absence means the
        lane declared no ``cwd`` and ran at the snapshot's project root; it
        never means ``"."``, which is why ``"."`` is refused here by the wire
        path grammar rather than accepted as a synonym.
        """
        if self.cwd_declared is None:
            return
        _check_wire_path(self.cwd_declared, "cwd_declared")
        if ".git" in self.cwd_declared.split("/"):
            raise ValueError(
                f"cwd_declared {self.cwd_declared!r} contains a '.git' component"
            )
        if len(self.cwd_declared.encode("utf-8")) > 4096:
            raise ValueError("cwd_declared must be at most 4096 UTF-8 bytes")
        if self.declared_rigor is None:
            raise ValueError(
                f"cwd_declared is {self.cwd_declared!r} but no lane resolved; a "
                f"declared working directory is a fact ABOUT a lane, and a "
                f"verdict that never loaded one cannot witness it"
            )

    def _check_lane_resolved_group(self) -> None:
        """All present or all absent — absent means the lane never resolved."""
        present = [
            name
            for name in LANE_RESOLVED_FIELDS
            if name != "argv_modified" and getattr(self, name) is not None
        ]
        expected = [name for name in LANE_RESOLVED_FIELDS if name != "argv_modified"]
        if present and len(present) != len(expected):
            missing = sorted(set(expected) - set(present))
            raise ValueError(
                f"the lane-resolved fields are all present or all absent, but "
                f"{', '.join(missing)} is missing. Absent means UNKNOWABLE (no "
                f"lane loaded); empty means known-and-empty. argv_declared = [] "
                f"would assert that the lane declared no argv, which is false"
            )
        if not present:
            if self.claims or self.evidence:
                raise ValueError(
                    "claims or evidence were rendered but no lane resolved; "
                    "evidence without a declaration is evidence about nothing"
                )
            return

        if not self.declared_rigor:
            raise ValueError("declared_rigor must name at least one rigor level")
        for level in self.declared_rigor:
            if level not in RIGOR_LEVELS:
                raise ValueError(
                    f"declared_rigor must contain only {list(RIGOR_LEVELS)}, got "
                    f"{level!r}"
                )
        if len(set(self.declared_rigor)) != len(self.declared_rigor):
            raise ValueError(
                f"declared_rigor contains a duplicate level: "
                f"{list(self.declared_rigor)}"
            )
        if not self.argv_declared:
            raise ValueError("argv_declared is empty; a resolved lane declares argv")

        expected_effective = tuple(self.argv_declared) + tuple(self.argv_appended or ())
        if tuple(self.argv_effective or ()) != expected_effective:
            raise ValueError(
                f"argv_effective {list(self.argv_effective or ())} is not "
                f"argv_declared + argv_appended {list(expected_effective)}; an "
                f"appended flag that is invisible in the verdict lets a PASS "
                f"describe a run that skipped most of the suite"
            )

    def _check_claims_cover_declared_rigor(self) -> None:
        """A-024: exactly one claim per declared level, no more and no fewer."""
        if self.declared_rigor is None:
            return
        seen = [claim.rigor for claim in self.claims]
        duplicates = sorted({level for level in seen if seen.count(level) > 1})
        if duplicates:
            raise ValueError(
                f"more than one claim for rigor level(s) {', '.join(duplicates)}; "
                f"assay records one claim per declared level"
            )
        missing = [level for level in self.declared_rigor if level not in seen]
        if missing:
            raise ValueError(
                f"the lane declared rigor {list(self.declared_rigor)} but rendered "
                f"no claim for {', '.join(missing)}; without an entry, "
                f"'{missing[0]} was declared but rendered no judgement' is "
                f"indistinguishable from '{missing[0]} was never declared'"
            )
        surplus = sorted(set(seen) - set(self.declared_rigor))
        if surplus:
            raise ValueError(
                f"claim(s) for rigor level(s) {', '.join(surplus)} the lane never "
                f"declared {list(self.declared_rigor)}"
            )

    def _check_evidence_covers_declarations(self) -> None:
        """External evidence covers declared ``(source, key)`` identities exactly."""
        if self.declared_evidence is None:
            return
        declared = [item.identity for item in self.declared_evidence]
        rendered = [item.identity for item in self.evidence]
        duplicate_declarations = sorted(
            {identity for identity in declared if declared.count(identity) > 1}
        )
        if duplicate_declarations:
            raise ValueError(
                f"declared_evidence contains duplicate identities: "
                f"{duplicate_declarations}"
            )
        duplicate_evidence = sorted(
            {identity for identity in rendered if rendered.count(identity) > 1}
        )
        if duplicate_evidence:
            raise ValueError(
                f"more than one evidence entry for identities: {duplicate_evidence}"
            )
        missing = [identity for identity in declared if identity not in rendered]
        if missing:
            raise ValueError(
                f"declared evidence {missing} rendered no judgement; without an "
                f"entry it is indistinguishable from never declared"
            )
        surplus = [identity for identity in rendered if identity not in declared]
        if surplus:
            raise ValueError(f"evidence {surplus} was never declared")

    def _check_outcome_agrees_with_rollup(self) -> None:
        """A-023: the outcome is DERIVED from the claims, never chosen beside them."""
        statuses = [claim.status for claim in self.claims]
        statuses.extend(item.status for item in self.evidence)
        if not statuses:
            return
        implied = rollup(statuses)
        if self.outcome is not implied:
            raise ValueError(
                f"outcome {self.outcome} disagrees with the rollup of its claims "
                f"and evidence "
                f"({implied}); precedence is "
                f"{' > '.join(o.value for o in ROLLUP_PRECEDENCE)}, and PASS only "
                f"when every declared claim passed"
            )

    def _check_judgment_matches_claims(self) -> None:
        """P16: ``judgment.r1`` exists if and only if the rendered R1 claim
        (when there is one) carries a ``coverage`` payload — "the effective
        judge policy... whenever changed-line judgment occurred" (sol
        finding 2), never independently of it, and never omitted when it
        did. Run AFTER :meth:`_check_claims_cover_declared_rigor`, which
        already guarantees at most one claim per rigor level, so "the R1
        claim" is unambiguous here.

        **P19/A-148 widens the identical rule to R2 and R3**, in BOTH
        directions, plus one thing schema shape alone cannot express: every
        operator/mechanism a claim's own payload NAMES must be one the
        recorded policy actually declared. `judgment.rN` is tied to the
        claim it describes the same way `judgment.r1` already was — a
        deferral P18 left with no executor (`verdict.py`/`verify.py` were
        both outside every package's `scope.touch` from P16 through P18).
        """
        if self.judgment is not None and self.declared_rigor is None:
            raise ValueError(
                "judgment is present but no lane resolved; a policy for a "
                "lane that never resolved describes nothing"
            )
        r1_claim = next((claim for claim in self.claims if claim.rigor == "R1"), None)
        r1_judged = r1_claim is not None and r1_claim.coverage is not None
        # wave-1 §6, A-264 (forced by the independent pre-dispatch review):
        # R1 records its policy whenever R1 was ATTEMPTED, for exactly two
        # new payload-free terminals -- one case wider than "rendered a
        # payload", mirroring A-183's identical R2 widening for
        # MUTATION_UNSUPPORTED immediately below. Without this,
        # BRANCH_UNAVAILABLE/TARGET_NOT_MEASURED could not record which
        # target was attempted or which floor was asked for, which would
        # gut B005's whole reason for existing.
        r1_attempted = r1_judged or (
            r1_claim is not None
            and r1_claim.reason_code
            in (ReasonCode.BRANCH_UNAVAILABLE, ReasonCode.TARGET_NOT_MEASURED)
        )
        judgment_r1 = None if self.judgment is None else self.judgment.r1
        if judgment_r1 is not None and not r1_attempted:
            raise ValueError(
                "judgment.r1 is present but no R1 claim rendered a coverage "
                "payload or one of BRANCH_UNAVAILABLE/TARGET_NOT_MEASURED -- "
                "a policy is recorded for a judgment that never happened"
            )
        if judgment_r1 is None and r1_attempted:
            raise ValueError(
                "the R1 claim rendered a coverage payload or one of "
                "BRANCH_UNAVAILABLE/TARGET_NOT_MEASURED but judgment.r1 is "
                "absent -- an independent consumer cannot re-derive R1's "
                "status without the policy that decided it"
            )

        r2_claim = next((claim for claim in self.claims if claim.rigor == "R2"), None)
        # P21/A-183: R2 policy is recorded whenever R2 was actually ATTEMPTED,
        # which is one case wider than "rendered a payload". A lane whose
        # adapter has no mutation engine resolved and applied jobs/max_mutants/
        # operators before discovery returned UNSUPPORTED, so dropping the
        # policy there would hide the cap a consumer needs to interpret the
        # refusal. A baseline that never passed is NOT attempted: R2's claim
        # then just propagates R0's own outcome and records no policy.
        r2_judged = r2_claim is not None and r2_claim.mutation is not None
        r2_attempted = r2_judged or (
            r2_claim is not None
            and r2_claim.reason_code is ReasonCode.MUTATION_UNSUPPORTED
        )
        judgment_r2 = None if self.judgment is None else self.judgment.r2
        if judgment_r2 is not None and not r2_attempted:
            raise ValueError(
                "judgment.r2 is present but no R2 claim rendered a mutation "
                "payload or an unsupported-capability terminal -- a policy is "
                "recorded for a judgment that never happened"
            )
        if judgment_r2 is None and r2_attempted:
            raise ValueError(
                "the R2 claim rendered a mutation payload or an "
                "unsupported-capability terminal but judgment.r2 is absent -- "
                "an independent consumer cannot re-derive R2's status without "
                "the policy that decided it"
            )
        if judgment_r2 is not None:
            self._check_operator_language_agrees(judgment_r2)
        if r2_judged and judgment_r2 is not None:
            # P21/A-180: `killed` is now in this sweep. Under v3 it was a bare
            # count, so a killed mutant produced by an operator the lane never
            # selected was structurally unobservable -- the single largest
            # bucket was exempt from the only check that binds a payload to
            # its policy.
            observed_operators = {
                outcome.operator
                for name in MUTATION_BUCKETS
                for outcome in getattr(r2_claim.mutation, name)
            }
            # B046: this check binds a payload to the POLICY that selected it,
            # and an ingested lane has no such policy -- assay selected none
            # of the operators, so `judgment.r2.operators` is absent by
            # construction and there is nothing here to be outside of. The
            # equivalent binding for an ingested lane is that every observed
            # operator sits in the ingested namespace, which
            # `_check_operator_language_agrees` owns below.
            if judgment_r2.operators is not None:
                unknown_operators = sorted(
                    observed_operators - set(judgment_r2.operators)
                )
                if unknown_operators:
                    raise ValueError(
                        f"claim[R2].mutation records outcome(s) for operator(s) "
                        f"{unknown_operators}, outside judgment.r2.operators "
                        f"{list(judgment_r2.operators)} -- a mutant this policy "
                        f"never selected cannot have been submitted"
                    )
            self._check_mutation_cardinality(r2_claim.mutation, judgment_r2)
            self._check_equivalence_pairing(r2_claim.mutation, judgment_r2)
            self._check_kill_attribution(r2_claim.mutation, judgment_r2)

        r3_claim = next((claim for claim in self.claims if claim.rigor == "R3"), None)
        r3_judged = r3_claim is not None and r3_claim.canary is not None
        judgment_r3 = None if self.judgment is None else self.judgment.r3
        if judgment_r3 is not None and not r3_judged:
            raise ValueError(
                "judgment.r3 is present but no R3 claim rendered a canary "
                "payload -- a policy is recorded for a judgment that never "
                "happened"
            )
        if judgment_r3 is None and r3_judged:
            raise ValueError(
                "the R3 claim rendered a canary payload but judgment.r3 "
                "is absent -- an independent consumer cannot re-derive R3's "
                "status without the policy that decided it"
            )
        if r3_judged and judgment_r3 is not None:
            if r3_claim.canary.mechanism != judgment_r3.mechanism:
                raise ValueError(
                    f"claim[R3].canary was produced by mechanism "
                    f"{r3_claim.canary.mechanism!r}, but judgment.r3 "
                    f"records the policy as {judgment_r3.mechanism!r} -- the "
                    f"two must name the same mechanism"
                )
            # P21/A-152/A-O18: the half schema v3 could not witness at all.
            # B007/A-432 generalises it from one equality to a PAIRWISE,
            # IN-ORDER one: attempts run in declared order, so attempt i must
            # be the record of target i, and the two lists must have the same
            # length. A shorter attempts list would let a lane declare probes
            # it never recorded, and a reordered one would let a surviving
            # probe be reported under a caught probe's name.
            attempted_targets = tuple(
                attempt.target for attempt in r3_claim.canary.attempts
            )
            if attempted_targets != tuple(judgment_r3.targets):
                raise ValueError(
                    f"claim[R3].canary recorded attempts for "
                    f"{list(attempted_targets)}, but judgment.r3 declares "
                    f"targets {list(judgment_r3.targets)} -- the attempts are "
                    f"the declared targets, in the declared order, one each; "
                    f"a canary that answers for different files than the ones "
                    f"declared is evidence about nothing the lane asked for"
                )

        # F015/A-433: A-148's correspondence, restated for R4 exactly as the
        # R2 and R3 blocks above state it for their own tiers.
        r4_claim = next((claim for claim in self.claims if claim.rigor == "R4"), None)
        r4_judged = r4_claim is not None and r4_claim.red_first is not None
        judgment_r4 = None if self.judgment is None else self.judgment.r4
        if judgment_r4 is not None and not r4_judged:
            raise ValueError(
                "judgment.r4 is present but no R4 claim rendered a red_first "
                "payload -- a policy is recorded for a judgment that never "
                "happened"
            )
        if judgment_r4 is None and r4_judged:
            raise ValueError(
                "the R4 claim rendered a red_first payload but judgment.r4 is "
                "absent -- an independent consumer cannot re-derive R4's "
                "status without the policy that decided it"
            )
        if r4_judged and judgment_r4 is not None:
            if r4_claim.red_first.tests != tuple(judgment_r4.tests):
                raise ValueError(
                    f"claim[R4].red_first ran tests "
                    f"{list(r4_claim.red_first.tests)}, but judgment.r4 "
                    f"declares {list(judgment_r4.tests)} -- a red-first run "
                    f"that judged different tests than the ones declared is "
                    f"evidence about nothing the lane asked for"
                )
            if r4_claim.red_first.broken_commit != judgment_r4.broken_commit:
                raise ValueError(
                    f"claim[R4].red_first ran against broken commit "
                    f"{r4_claim.red_first.broken_commit!r}, but judgment.r4 "
                    f"records {judgment_r4.broken_commit!r} -- the two must "
                    f"name the same commit"
                )

    def _check_operator_language_agrees(self, policy: JudgmentR2) -> None:
        """(P33/V5-2, invariant 1) every operator's prefix equals
        ``judgment.resolved.language``.

        The shipped schema closes each language's vocabulary, so it already
        refuses an operator no language owns. What it structurally CANNOT do
        is relate the operator to the resolved language recorded elsewhere in
        the document — draft 2020-12 has no ``$data`` — so without this a
        Python lane could record a full set of SQL operators it could not
        possibly have applied, and every layer would agree the artifact was
        well-formed.

        Applied to the DECLARED policy and to every recorded outcome, because
        the two are separately forgeable: a payload naming an operator the
        policy never declared is already caught above, but a policy AND a
        payload that agree with each other while both disagreeing with the
        language is not.

        **B046 (schema v9) splits this by producer, and the split is the whole
        point.** Under ``producer = "ingested"`` there is no declared policy to
        check (assay selected nothing) and the operator names are not
        language-qualified at all -- they are copied verbatim from a foreign
        tool's report into a namespace assay owns, so
        ``operator_language("stryker:X")`` answers ``"stryker"``, which is not
        and must never be compared against a language. The rule that REPLACES
        the language check there is exact, not weaker: every recorded operator
        must be an ingested one. The two namespaces do not mix in either
        direction -- an ingested operator on a native document says assay's own
        engine emitted a name it has no catalogue for, and a language operator
        on an ingested document says assay's engine ran on a lane it never
        touched.
        """
        language = self.judgment.resolved.language
        if policy.producer == "ingested":
            self._check_ingested_operators_only(policy)
            return
        wrong = sorted(
            operator
            for operator in policy.operators or ()
            if operator_language(operator) != language
        )
        if wrong:
            raise ValueError(
                f"judgment.r2.operators names {wrong} on a lane whose "
                f"judgment.resolved.language is {language!r} -- each operator "
                f"is qualified with the language that owns it, and a lane "
                f"cannot apply another language's catalogue"
            )
        r2_claim = next((claim for claim in self.claims if claim.rigor == "R2"), None)
        if r2_claim is None or r2_claim.mutation is None:
            return
        wrong_outcomes = sorted(
            {
                outcome.operator
                for name in MUTATION_BUCKETS
                for outcome in getattr(r2_claim.mutation, name)
                if operator_language(outcome.operator) != language
            }
        )
        if wrong_outcomes:
            raise ValueError(
                f"claim[R2].mutation records outcome(s) for {wrong_outcomes} on "
                f"a lane whose judgment.resolved.language is {language!r} -- a "
                f"mutant carries the operator that produced it, so a foreign "
                f"prefix says the run applied a catalogue it does not have"
            )

    def _check_ingested_operators_only(self, policy: JudgmentR2) -> None:
        """(B046) The ingested half of :meth:`_check_operator_language_agrees`.

        ``policy.operators`` is absent by construction here (``JudgmentR2``
        refuses it under ``producer = "ingested"``), so the only thing to bind
        is the payload: every recorded operator must sit in the ingested
        namespace. A ``python:``/``go:``/``sql:`` operator on an ingested
        document would claim assay's own engine produced a mutant on a lane
        assay never ran an engine for.
        """
        r2_claim = next((claim for claim in self.claims if claim.rigor == "R2"), None)
        if r2_claim is None or r2_claim.mutation is None:
            return
        native = sorted(
            {
                outcome.operator
                for name in MUTATION_BUCKETS
                for outcome in getattr(r2_claim.mutation, name)
                if not is_ingested_operator(outcome.operator)
            }
        )
        if native:
            raise ValueError(
                f"claim[R2].mutation records outcome(s) for {native} on a lane "
                f"whose judgment.r2.producer is 'ingested' -- assay ran no "
                f"mutation engine here, so a mutant carrying an operator from "
                f"assay's own closed catalogue names a run that did not happen"
            )

    def _check_equivalence_pairing(
        self, mutation: Mutation, policy: JudgmentR2
    ) -> None:
        """(P33/V5-3, invariant 2) the ``equivalent`` bucket and
        ``equivalence_artifact`` are both present or both absent.

        Equivalence is PROVEN by comparing the declared artifact's bytes
        against the baseline run's. With no declaration there is no proof
        source, so an equivalence claim would have been inferred from
        something else — which is exactly what A-209's both-present pattern
        exists to forbid one field over.
        """
        if mutation.equivalent and policy.equivalence_artifact is None:
            raise ValueError(
                f"claim[R2].mutation records {len(mutation.equivalent)} "
                f"equivalent mutant(s) but judgment.r2 declares no "
                f"equivalence_artifact -- equivalence is established by "
                f"comparing that artifact's bytes, so with none declared there "
                f"is nothing the proof could have been read from"
            )

    def _check_kill_attribution(self, mutation: Mutation, policy: JudgmentR2) -> None:
        """(P33/V5-4, invariant 4) attribution consistency, per clause.

        ``declared`` requires a ``kill_signal`` on EVERY killed entry — an
        attributed run that attributed only some of its kills is recording
        two different kinds of evidence under one claim. ``unattributed``
        forbids one anywhere, which is the clause the schema deliberately
        leaves open: ``kill_signal`` on a *killed* entry is locally legal, so
        only a layer that can see ``kill_attribution`` can refuse it.
        ``JudgmentR2`` itself already binds ``declared`` to the presence of
        ``kill_signal_artifact`` (A-223b's derivation).
        """
        if policy.kill_attribution == "declared":
            unattributed_kills = [
                item.identity for item in mutation.killed if item.kill_signal is None
            ]
            if unattributed_kills:
                raise ValueError(
                    f"judgment.r2 records kill_attribution 'declared' but "
                    f"killed mutant(s) {unattributed_kills} carry no "
                    f"kill_signal -- an attributed run names the mechanism "
                    f"that refused every kill, or it is not attributed"
                )
            return
        signalled = [
            item.identity
            for name in MUTATION_BUCKETS
            for item in getattr(mutation, name)
            if item.kill_signal is not None
        ]
        if signalled:
            raise ValueError(
                f"judgment.r2 records kill_attribution "
                f"{policy.kill_attribution!r} but mutant(s) {signalled} carry a "
                f"kill_signal -- an unattributed run observed exit status and "
                f"nothing else, so it has no mechanism to name"
            )

    def _check_mutation_cardinality(
        self, mutation: Mutation, policy: JudgmentR2
    ) -> None:
        """(P21/A-163) bind the payload's cardinality to the declared cap.

        Two directions, and both matter. A limit sentinel must record
        EXACTLY ``max_mutants + 1`` — any other count is either a forged
        refusal or a bound that was never actually reached. And a normal
        payload may not exceed the cap it was run under, which is the
        arithmetic that makes ``max_mutants`` a real bound rather than a
        decorative record of one.

        **B046: not applicable under ``producer = "ingested"``.** ``max_mutants``
        is assay's OWN declared ceiling on its OWN discovery, and assay
        performed no discovery for an ingested lane, so there is no cap here
        for a count to be bound to. The ingested path bounds its report by its
        own fixed mutant ceiling at PARSE time -- a refusal to read an
        over-sized artifact at all, which is a stronger guarantee than an
        after-the-fact arithmetic check on a document already accepted.
        """
        if policy.max_mutants is None:
            return
        if mutation.is_limit_sentinel:
            expected = policy.max_mutants + 1
            if mutation.candidate_count != expected:
                raise ValueError(
                    f"claim[R2].mutation is a limit sentinel recording "
                    f"{mutation.candidate_count} candidate(s), but "
                    f"judgment.r2.max_mutants is {policy.max_mutants}, so the "
                    f"only honest sentinel count is {expected} -- discovery "
                    f"stops at the cap plus one and reports what it observed"
                )
            return
        if mutation.total > policy.max_mutants:
            raise ValueError(
                f"claim[R2].mutation attempted {mutation.total} mutant(s), "
                f"exceeding judgment.r2.max_mutants {policy.max_mutants} -- a "
                f"cap that a completed run can exceed bounds nothing"
            )

    # --- serialisation --------------------------------------------------------

    @property
    def exit_code(self) -> int:
        """The exit code IS the verdict; the artifact never disagrees with it."""
        return EXIT_CODES[self.outcome]

    @property
    def argv_modified(self) -> bool | None:
        """Derived, never stored (A-036).

        Deriving it removes a whole class of artifact in which ``argv_appended``
        and ``argv_modified`` disagree. ``None`` when no lane resolved.
        """
        if self.argv_appended is None:
            return None
        return bool(self.argv_appended)

    def to_dict(self) -> dict[str, Any]:
        """The artifact, as plain JSON types."""
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "assay_version": self.assay_version,
            "lane": self.lane,
            "commit": self.commit,
            "outcome": self.outcome.value,
            "exit_code": self.exit_code,
            "started": self.started,
            "ended": self.ended,
        }
        # B018/A-327. Omitted, never null (A-051) and never partial: an
        # unidentifiable invocation records no identity at all.
        if self.judge_provenance is not None:
            payload["judge_provenance"] = self.judge_provenance.to_dict()
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code.value
        if self.declared_rigor is not None:
            payload["declared_rigor"] = list(self.declared_rigor)
            payload["declared_evidence"] = [
                item.to_dict() for item in self.declared_evidence or ()
            ]
            payload["argv_declared"] = list(self.argv_declared or ())
            payload["argv_appended"] = list(self.argv_appended or ())
            payload["argv_effective"] = list(self.argv_effective or ())
            payload["argv_modified"] = bool(self.argv_modified)
            payload["env_declared"] = dict(self.env_declared or {})
            payload["env_effective"] = dict(self.env_effective or {})
            if self.env_effective_incomplete:
                payload["env_effective_incomplete"] = True
            payload["scope"] = self.scope
            payload["enforcement"] = self.enforcement
        # B043: emitted OUTSIDE the lane-resolved block on purpose -- it is
        # independently optional, and `_check_cwd_declared` (not this method)
        # is what ties it to a resolved lane. Omitted, never "." (A-051).
        if self.cwd_declared is not None:
            payload["cwd_declared"] = self.cwd_declared
        if self.judgment is not None:
            payload["judgment"] = self.judgment.to_dict()
        if self.snapshot_policy is not None:
            payload["snapshot_policy"] = self.snapshot_policy.to_dict()
        if self.result_stdout_tail is not None:
            payload["result_stdout_tail"] = self.result_stdout_tail
            payload["result_stdout_dropped_bytes"] = (
                self.result_stdout_dropped_bytes
            )
        if self.result_stderr_tail is not None:
            payload["result_stderr_tail"] = self.result_stderr_tail
            payload["result_stderr_dropped_bytes"] = (
                self.result_stderr_dropped_bytes
            )
        # A-230a: omitted when no helper ran, never `helpers: []`.
        if self.helpers is not None:
            payload["helpers"] = [helper.to_dict() for helper in self.helpers]
        payload["claims"] = [claim.to_dict() for claim in self.claims]
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload

    def to_json(self) -> str:
        """The artifact as text, stable across runs and interpreters."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
