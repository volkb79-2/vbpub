# Wave 1 carve — branch coverage, the whole-target judge, and verdict schema v6

**Backlog items closed:** B006 (both papercuts) and B005 (the whole-module
coverage judge). **Branch:** `assay-B005-B006-coverage-v6`, worktree
`/workspaces/vbpub/.worktrees/assay-B005-B006-coverage-v6`. **Base:** `main` at
`c66b67a0`.

**Authority.** This file is the contract. Where it disagrees with
`4-backlog.md`'s B005/B006 prose, this file wins — the backlog recorded the
consumer's proposal, this records the operator's rulings and the design that
survived them. Where it disagrees with `decisions.md`, decisions.md wins and
this file is wrong; say so rather than implementing around it.

**Process for this wave** (lighter than the P20–P33 wave protocol, deliberately,
and stated so nobody mistakes it for the full one): the controller carves this
contract and reviews adversarially by driving the shipped entry points; an
independent `codex gpt-5.6-sol` high-effort pass reviews this contract before
dispatch and reviews the implementation before merge; one serialized Sonnet 5
session implements. There is no CR-opus-0 pre-dispatch fork and no locked
acceptance suite authored ahead of the implementation — **except** the six real
coverage artifacts under `tests/fixtures/coverage/`, which ARE carver-owned and
must not be edited (see their `PROVENANCE.md`).

---

## 0. The rulings this wave rests on

Operator decisions taken 2026-08-16, to be recorded as A-257…A-263 in
`decisions.md` by the implementer as work item 0, in these words:

* **A-257 — the coverage model gains branch data, in all four formats.** A
  format that genuinely cannot express branch arcs declares that fact rather
  than being omitted from the work: `go-cover`'s branch capability is an
  explicit, tested `"unavailable"`, because a Go cover profile carries
  statement counts and no arcs at all. Faking one would be the exact
  `declared_unverified`-class lie A-O12 was corrected for.
* **A-258 — branch coverage is judged whenever the artifact reports it.** Not
  opt-in. A changed line that is a branch source with an untaken arc lowers the
  percentage in every lane whose artifact carries branch data. This changes what
  PASS means for existing R1 lanes, which is precisely why it lands with a
  schema major bump and not before.
* **A-259 — `judge.require_branch` (default `false`) governs ABSENCE, never
  presence.** With it `true`, an artifact whose branch capability is
  `"unavailable"` is refused (`NO_MEASUREMENT`/`BRANCH_UNAVAILABLE`) instead of
  being judged on lines alone. It exists because the alternative is a silent
  rigor downgrade: drop `--cov-branch` from the argv and a line+branch gate
  quietly becomes a line gate that still says PASS.
* **A-260 — the whole-target judge is a MODE of R1, not a new rigor level.** One
  lane declares one mode. A consumer wanting both a changed-line gate and a
  whole-module floor declares two lanes. Inventing an R1.5 or a second R1 claim
  would break `_check_claims_cover_declared_rigor`'s one-claim-per-level
  contract for no capability gain.
* **A-261 — verdict schema v6, hard cut.** `assay verify` refuses a v5 document
  exactly as it refuses v4 today. No dual-version verifier, no compatibility
  shim. The producer emits v6 only.
* **A-262 — `changed_executable` is renamed to `executable` in the v6 coverage
  payload.** Under the whole-target mode the denominator is a target's
  executable lines, which were not "changed" by anything; keeping the old name
  would put a false statement on the wire. This is the one rename v6 takes, and
  it is taken because a major bump is the only honest moment for it.
* **A-263 — `pct` is the COMBINED line+branch percentage.** `(covered +
  branches_covered) / (executable + branches_total)`, which is exactly
  `coverage.py`'s own `summary.percent_covered` under `--cov-branch` and
  therefore exactly what `--cov-fail-under` compares against. `covered` and
  `executable` stay line-only; the branch side gets its own two integers, so an
  independent consumer can still re-derive `pct` from the payload alone. When
  branch capability is `"unavailable"`, `branches_total` is 0 and the formula
  degenerates to today's line-only value with no special case.

---

## 1. B006 (a) — an escaping symlink anywhere in the tree fails every R1+ lane

### What is true today, verified

`isolation.py::_check_symlink_target` (~:860) refuses any tracked symlink whose
target is absolute or resolves outside the snapshot root, raising `GIT_FAILED`.
It is applied to every entry of the whole committed tree, because the snapshot
materializes the whole tree — which it must, since a lane's argv may legitimately
read a config, fixture or script far from any `source_root`.

So the backlog's first proposal — "scope the walk to `source_roots`" — is
**rejected**: it would silently stop materializing files that lanes genuinely
run against, trading a loud failure for a mysterious one. The second proposal,
an explicit allow/skip knob, is what ships.

### What ships

A new optional lane table:

```toml
[lanes.X.isolation]
allow_escaping_symlinks = ["infra-global/reverse-proxy/etc-nginx/modules"]
```

* Paths are **repository-top-relative**, the same spelling the snapshot's own
  walk uses and the same spelling the diagnostic prints — NOT project-relative.
  A-145 is the standing trap here: assay's project root is not its repository
  top, and every boundary crossing between the two must say which it speaks.
  Say it in the loader docstring and in the config error text.
* Load-time refusal (`ERROR`/`BAD_LANE_CONFIG`, before any Git work): a
  non-list, a non-string entry, an empty entry, an absolute path, any entry with
  a `.` or `..` component, a backslash, or a duplicate.
* Snapshot-time behaviour: an entry naming an escaping symlink causes that entry
  to be **omitted from the snapshot entirely** — never materialized, not even as
  a dangling link. It cannot be read, so it cannot exfiltrate; that is the whole
  security argument and it must appear in the code as a comment, not only here.
* Snapshot-time refusal (`GIT_FAILED`), because an attestation that protects
  nothing is worse than none: a declared entry that does not exist in the
  snapshot commit, or exists but is not a symlink, or is a symlink that does NOT
  escape. Each names the offending path and why.
* The undeclared case still refuses — but the diagnostic must now list **every**
  offending path found in the whole walk, sorted, not just the first, and must
  print the exact TOML the consumer can paste. dstdns spent a debugging round per
  path; a repository with three of these should cost one round, not three.

### Recorded in the verdict

`allow_escaping_symlinks`, when non-empty, appears in the artifact as
`isolation.allowed_escaping_symlinks` (new optional top-level object, sorted,
unique, non-empty when present). A hermeticity waiver that leaves no trace in
the evidence artifact is a waiver a reviewer cannot audit. Absent from the
document entirely when the lane declares none — never present-and-empty.

---

## 2. B006 (b) — the coverage artifact's parent must pre-exist in a tracked-only snapshot

### What is true today, verified

`safeio.reserve_output` opens the artifact's parent chain with
`O_DIRECTORY|O_NOFOLLOW` and refuses a missing parent (`_open_parent_chain`); it
never creates one. The lane's argv runs with `cwd = snapshot.project_root`, and
the snapshot contains tracked files only. So the near-universal convention —
write coverage JSON into a gitignored scratch dir — fails, and it fails as
`UNREADABLE_ARTIFACT`, which reads as "your tests produced no coverage".

### What ships

assay reserves the artifact path, so assay may create its parent chain **inside
its own ephemeral snapshot**. Two hard constraints:

* **Only in the snapshot.** The consumer's real worktree is never mkdir'd. The
  creation is driven by an explicit parameter from the snapshot-running call
  site, defaulting to off; a test must prove that an R0/in-place path with a
  missing parent still refuses rather than creating anything. "It only ever runs
  in the snapshot in practice" is not the contract — the default is.
* **Same safety discipline as the read.** Create each missing component
  descriptor-relative with `mkdir` at `dir_fd`, `O_DIRECTORY|O_NOFOLLOW` on the
  reopen, mode `0o700`. A component that exists as a non-directory, or as a
  symlink, refuses exactly as today. `os.makedirs`-style recursion that follows
  a symlinked component is the failure mode to avoid, and there must be a test
  that plants a symlinked component and proves refusal.

Independently of the creation: when a parent chain still cannot be opened, the
diagnostic must name the missing component and the declared artifact path. The
generic message is what cost the consumer the debugging round.

---

## 3. Branch coverage in the model — all four formats

Read `tests/fixtures/coverage/PROVENANCE.md` first. Every rule below is a claim
about what those six real artifacts contain, and each is checkable against them
in about a minute. **A rule you cannot witness in one of those files is a rule
this contract got wrong — report it, do not implement it.**

### 3.1 The model (`coverage_parsers/model.py`)

```python
@dataclass(frozen=True, kw_only=True)
class BranchCoverage:
    #: source line -> (covered_arcs, total_arcs). A line with no branch at all
    #: is ABSENT, never present as (0, 0) — the same "absent means none, not
    #: empty" contract AddedLines and every existing payload mapping keeps.
    by_line: Mapping[int, tuple[int, int]]
```

`FileCoverage` gains `branches: BranchCoverage | None`. `None` carries the exact
meaning `excluded=None` already carries (A-008): the format cannot express the
concept for this artifact, which is a different fact from "expressed, and there
are zero".

Invariants enforced in `__post_init__`, in the model, once, for every format —
this is P15's own lesson (`FileCoverage` enforces the common invariants in the
one place every parser passes through, rather than four parsers each getting it
right independently):

1. every branch line is positive;
2. `1 <= total`, `0 <= covered <= total` — a recorded line with zero total arcs
   is malformed, not "no branches";
3. every branch line is in `executed | missing` — an arc from a line the format
   does not consider code at all is a self-inconsistent artifact;
4. no branch line is in `excluded`;
5. **a line in `missing` carries `covered == 0`** — a line that never ran cannot
   have taken an arc. This is the strongest anti-tamper invariant available here
   and all three branch-bearing formats agree on it (PROVENANCE.md's last row).

`ValueError` on violation, wrapped by each parser into its own
`ERROR`/`UNREADABLE_ARTIFACT`, exactly as the existing disjointness violations
are wrapped today.

### 3.2 Capability derivation is ARTIFACT-level, never per-file

`coverage.py` gains `derive_branch_capability(profile)`, mirroring
`derive_exclusion_capability` exactly: all-`None` ⇒ `"unavailable"`,
none-`None` ⇒ `"reported"`, **mixed ⇒ `ERROR`/`UNREADABLE_ARTIFACT`**.

That refusal is only safe because each parser decides capability once for the
whole artifact and applies it to every file it emits. The trap, witnessed in
`lcov.branch.info`: coverage.py emits `BRF`/`BRH` for `sample.py` and **nothing
at all** for `test_sample.py`, which has no branches. A per-file rule would call
that single real, correct artifact "mixed" and refuse it. Every parser must
therefore emit `branches=BranchCoverage(by_line={})` — not `None` — for a
branch-free file in a branch-tracking artifact.

### 3.3 Per-format rules

**`coverage-py-json` — capability is stated explicitly.**
`meta.branch_coverage` is the authority: `true` ⇒ every file gets a
`BranchCoverage`; `false` or the whole `meta` object absent ⇒ every file gets
`None`. Then:

* `executed_branches` / `missing_branches` are arrays of `[src, dst]` pairs.
  Group by `src`: `total` = arcs with that source, `covered` = those in
  `executed_branches`. `dst` is not otherwise used and is not stored — assay
  judges branches at their source line.
* When `meta.branch_coverage` is `true`, a file record MISSING either array is
  `UNREADABLE_ARTIFACT` with a message naming the cause: the artifact was
  produced by a coverage.py too old to report per-line arcs, and assay needs
  them to attribute a branch to a changed line. Failing closed here is
  deliberate — the alternative is judging a branch floor against file-level
  totals that cannot be attributed to a line.
* When `meta.branch_coverage` is `false` but a record carries either array, that
  is a self-inconsistent artifact: `UNREADABLE_ARTIFACT`.
* Cross-check, when `summary` carries them: `summary.num_branches` must equal
  the derived total and `summary.covered_branches` the derived covered.
  Mismatch is `UNREADABLE_ARTIFACT` — the artifact's own claims about itself
  disagree, which is the same refusal the normalized-key collision already
  makes. A `summary` that omits the branch keys is not malformed; skip the
  cross-check and say so in the docstring.
* Malformed pair shapes (not a 2-element list, non-int, bool, non-positive) are
  `UNREADABLE_ARTIFACT` per the existing `_int_list` discipline. Note `dst` may
  legitimately be **negative or zero** in coverage.py's own output for an exit
  arc (`[line, -line]` spellings appear in some versions) — so validate `src`
  as a positive line and treat `dst` as an opaque integer identity. Prove
  whichever is true against the real fixture before choosing; do not assume this
  sentence is right.

**`lcov` — capability is inferred from the whole document.** If ANY record in
the artifact carries `BRDA`, `BRF` or `BRH`, branch tracking was on: every file
gets a `BranchCoverage`, empty for records with no branch lines. If NO record
anywhere carries one, every file gets `None`.

* `BRDA:<line>,<block>,<branch>,<taken>` — group by `line`; `taken` is `-`
  (block never entered) or a decimal count. `covered` counts entries whose
  `taken` is a count `> 0`; `-` and `0` are both uncovered. `<block>` and
  `<branch>` are opaque identity fields, not numbers to sum — coverage.py writes
  the human string `jump to line 6` in the `<branch>` field, so a parser that
  requires an integer there rejects a real artifact.
* `BRF`/`BRH`, when present, must equal the derived total/covered for that
  record. Mismatch is `UNREADABLE_ARTIFACT`.

**`cobertura` — capability is the document-level count.** Root
`branches-valid` present and `> 0` ⇒ reported for all files; `0` or absent ⇒
`None` for all. The acknowledged, documented edge: branch tracking on for a
project with zero branches anywhere is indistinguishable from tracking off, and
is treated as `"unavailable"`. That fails CLOSED — with `require_branch` it
refuses rather than passing a vacuous branch floor.

* Per line: `branch="true"` with `condition-coverage="P% (C/T)"`. `C`/`T` are
  the covered/total arcs; the percentage is redundant and must NOT be trusted —
  parse `(C/T)`, and refuse a `condition-coverage` whose stated percentage is
  inconsistent with `C/T` beyond rounding, or whose shape does not match.
  `missing-branches` is a coverage.py extension and is not required.
* `branches-valid`/`branches-covered` at the root must equal the summed derived
  totals across all files. Mismatch is `UNREADABLE_ARTIFACT`.

**`go-cover` — always `None`.** The cover profile's records are
`file:startLine.startCol,endLine.endCol numStmt count`: statement counts, no
arcs. The parser sets `branches=None` unconditionally, with a docstring
paragraph saying why, and a test asserting it — so the capability is a
*measured* property of the format rather than an omission that later looks like
an oversight (A-O16's exact failure).

---

## 4. The changed-line R1 judge, with branches (A-258)

`evaluate.py::evaluate_coverage` gains branch arithmetic. Rules, in the module
docstring's numbering style:

* A changed, considered line that is a branch source contributes its `total`
  arcs to the branch denominator and its `covered` arcs to the branch numerator.
* A branch line reached only through rule 3b's span attribution contributes
  **nothing** to the branch side. Attribution answers "which statement's status
  does this line inherit", which says nothing about arcs leaving that statement;
  crediting them would invent a measurement. Say this in the docstring — it is
  the kind of silent inference this project rejects.
* A branch source line that is `excluded` contributes nothing (invariant 4 makes
  this unreachable, but state the intent).
* Files with no coverage entry contribute nothing to the branch side; their
  lines already fail through `files_missing_coverage`.

`CoverageEvaluation` gains `branches_covered: int`, `branches_total: int`,
`branch_capability: str`, `missing_branch_lines: Mapping[str, frozenset[int]]`
(changed lines with at least one uncovered arc, absent-means-none), and
`files_with_missing_branch_lines: tuple[str, ...]`.

`pct` becomes A-263's combined value. The existing zero-denominator rule holds
on the combined denominator: `executable + branches_total == 0` ⇒ `pct = 100.0`.

### Outcome precedence

Unchanged at the top, extended at the bottom:

1. `unclassified_lines` ⇒ `FAIL`/`UNCLASSIFIED_LINES`
2. disallowed excluded ⇒ `FAIL`/`EXCLUDED_LINES`
3. `pct < fail_under` ⇒ `FAIL`/**`UNCOVERED_BRANCHES`** when there are zero
   missing lines and at least one uncovered arc; `FAIL`/`UNCOVERED_LINES`
   otherwise
4. `PASS`

`UNCOVERED_BRANCHES` is a new member of the closed `ReasonCode` enum, which is
part of why this is a major bump. Its condition is exact: a floor missed purely
because of branches must not be reported as uncovered lines, because "which
mechanism refused" is the distinction this project exists to keep (B001's
false-PASS story is the same shape one layer up).

### `require_branch` (A-259)

New optional `judge.require_branch` boolean, default `false`, legal on any lane
declaring R1. When `true` and the parsed profile's branch capability is
`"unavailable"`, `evaluate_r1` renders `NO_MEASUREMENT`/**`BRANCH_UNAVAILABLE`**
(second new `ReasonCode`), payload-free, before any evaluation. The guard lives
beside `check_empty_coverage` in the same guard sequence, not inside
`evaluate_coverage` — it is a measurability question, not an arithmetic one.

---

## 5. B005 — the whole-target mode (A-260)

### Declaration

```toml
[lanes.redirect_chain.judge]
language = "python"
source_roots = ["libs/common/src"]
fail_under = 100.0
allow_excluded = false
require_branch = true
mode = "whole_target"
targets = ["libs/common/src/common/redirect_chain.py"]
# base is FORBIDDEN here, and its absence is not a defect
```

* `mode` is a closed enum: `"changed_lines"` | `"whole_target"`. **Absent means
  `"changed_lines"`** — the only mode that existed before this wave. The
  `JudgeConfig` dataclass stores the declared value or `None`, so
  `as_declared()` stays a faithful reproduction of the file and no default is
  ever written back into it; the *effective* mode is resolved at exactly one
  named place and is what the verdict records explicitly. This is how "never
  invent a value" and "do not force every existing `assay.toml` to be edited"
  are both kept.
* `targets`: required and non-empty iff `mode = "whole_target"`; **refused**
  otherwise (a target list under the changed-line mode is a declaration that
  does nothing, and silently ignoring it is how a consumer comes to believe a
  floor is enforced when it is not). Project-relative file paths, the same
  spelling as `judge.coverage.artifact` and `judge.canary.target` (A-145 again).
  Load-time refusal for: non-list, non-string, empty, absolute, any `.`/`..`
  component, backslash, duplicate.
* `base` is **refused at load time** for a `whole_target` lane that declares no
  R2 — it resolves nothing and recording it would imply a comparison that never
  happened. `JUDGE_FIELDS_BY_RIGOR` must be consulted rather than duplicated:
  R1's required-field set becomes mode-dependent, and R2 continues to require
  `base` independently, so an `R0,R1,R2` lane in whole-target mode still
  declares and records one.

### Judging

A new pure function beside `evaluate_coverage` — do NOT overload it with a mode
flag, because its whole contract is "intersect a diff with a profile" and a
mode parameter that makes half its parameters meaningless is the shape that
produces vacuous passes:

```python
def evaluate_targets(*, profile, adapter, repo_top, project_root,
                     targets, source_root_paths, fail_under, allow_excluded,
                     ...) -> CoverageEvaluation
```

Returning the SAME `CoverageEvaluation` type keeps one claim-assembly path.

Per target, in order:

1. Resolve the project-relative target to its repository-top-relative spelling
   (the profile's keys, after `normalize_coverage_key`, are repo-top-relative).
2. It must exist as a regular file — not a directory, not a symlink — under a
   declared source root, be adapter-recognised source, and not be a test path.
   Otherwise `ERROR`/`BAD_LANE_CONFIG`, naming the target and the gate it failed.
   `is_relative_to` on resolved absolute paths, never string prefixes
   (`src/foo` vs `src/foo_evil`).
3. It must have an entry in the coverage profile, and that entry must carry at
   least one executable line. Otherwise **`NO_MEASUREMENT`/`TARGET_NOT_MEASURED`**
   (third new `ReasonCode`), naming the target. This is the load-bearing
   anti-vacuity guard and the single most important test in this work item: the
   stopgap it replaces reports `100%` of zero when `--cov=` names a module that
   was never imported, and a first-class judge that reproduces that hole has
   closed nothing.
4. Every executable line of that entry counts: `executable` is
   `executed | missing`, `covered` is `executed`, `missing_lines` records the
   rest. Excluded lines are excluded from the denominator and recorded in
   `excluded_lines` exactly as today; `allow_excluded=false` still FAILs.
5. Branch arithmetic is identical to §4, over every branch line of the target
   rather than the changed subset.
6. `considered` is the number of targets judged.

There is no diff, no base, no `BASE_IS_HEAD` guard, and no span attribution
(nothing is unattributed: every line comes from the artifact, so
`unclassified_lines` is always empty and `files_with_unclassified_lines` always
`()`).

`DIRTY_TREE` still applies — `run_lane` refuses a dirty worktree for every lane
regardless of mode, because the measurement runs against a committed snapshot
and uncommitted work is invisible to it either way. Do not remove it and do not
special-case it.

### What this fixes for the consumer

`base = main` run from `main` post-merge no longer refuses: with no base there
is no `BASE_IS_HEAD`. The lane runs anywhere, including as a post-merge floor,
which is the blocker that put dstdns on the argv stopgap.

---

## 6. Verdict schema v6

`$id: urn:assay:schema:verdict:6`, `VERDICT_SCHEMA_VERSION = 6`, hard cut
(A-261).

### `coverage` object

| field | change |
|---|---|
| `changed_executable` | **renamed** `executable` (A-262) |
| `branches_covered` | NEW, integer ≥ 0, required |
| `branches_total` | NEW, integer ≥ 0, required |
| `branch_capability` | NEW, required, enum `reported` \| `unavailable` |
| `missing_branch_lines` | NEW, required, same shape/discipline as `missing_lines` |
| `files_with_missing_branch_lines` | NEW, required, same shape as `files_with_excluded_lines` |
| everything else | unchanged |

Schema-level invariants (`allOf`), each mirroring the exclusion-capability
branch that already exists:

* `branch_capability = "unavailable"` ⇒ `branches_total = 0`,
  `branches_covered = 0`, `missing_branch_lines` empty,
  `files_with_missing_branch_lines` empty. The converse is deliberately NOT a
  rule — `"reported"` with zero branches is a capable format truthfully finding
  none, and forbidding it re-collapses the very distinction A-008 keeps.
* `branches_covered <= branches_total`.
* `covered <= executable`.
* `files_with_missing_branch_lines` and the key set of `missing_branch_lines`
  are the same set — the identity check the existing pairs already get in
  `Coverage._check_summaries_name_their_own_detail`.

### `judgment.r1`

| field | change |
|---|---|
| `mode` | NEW, required, enum `changed_lines` \| `whole_target` |
| `targets` | NEW, required iff `mode = "whole_target"`, forbidden otherwise; non-empty, unique, sorted |
| `require_branch` | NEW, required boolean — the effective policy, recorded like `allow_excluded` |
| existing four | unchanged |

`mode` is REQUIRED in the artifact even though it is optional in the lane file.
The lane file records what a human declared; the artifact records what actually
judged. That asymmetry is the point of `judgment` existing at all (P16, sol
finding 2).

### `judgment.resolved.base`

The conditional widens. Today: required iff `judgment` carries `r1` or `r2`.
v6: required iff `judgment` carries `r2`, **or** carries `r1` with
`mode = "changed_lines"`; **forbidden** iff `judgment` carries `r1` with
`mode = "whole_target"` and no `r2`. Both `verdict.py::Judgment` and
`verify.py::_check_base_matches_the_tiers_present` own this rule
independently — that duplication is intentional (A-181/A-182's model/raw-verifier
split) and both must move together.

### `isolation`

New optional top-level object, `{"allowed_escaping_symlinks": [...]}`, present
only when the lane declares a non-empty list. Sorted, unique, min 1 item.

### `reason_code`

Three new members of the closed enum: `UNCOVERED_BRANCHES`,
`BRANCH_UNAVAILABLE`, `TARGET_NOT_MEASURED`. Each needs its
outcome-pairing entry wherever `_check_reason_code` and the conformance matrix
enumerate legal `(outcome, reason_code)` pairs, and each needs a hand-written
fixture — the P14 lesson is that a capability lands and the matrix that measures
it does not move with it, leaving a *correct* fixture failing the suite.

### Migration

Mechanical, and it must be auditable rather than 47 hand edits:

* `nyxloom-trove/carve-assets/W1/migrate_v5_to_v6.py`, in the register of P33's
  `migrate_v4_to_v5.py`: a logged, itemised transform with a `--check` mode that
  exits 0 **before and after** implementation, over `tests/fixtures/**`, the
  schema, and every other v5 consumer.
* `nyxloom-trove/carve-assets/W1/sweep_v5_consumers.py`, modelled on P33's
  `sweep_v4_consumers.py` (read it — it exists because two review rounds each
  found a consumer the previous closure missed, and round 3 pinned it with a
  planted decoy). Same discipline: scan the git index, union with a content
  scan, and pin the sweep itself with a planted decoy so a sweep that finds
  nothing is distinguishable from a sweep that looks at nothing.
* A-252's **differential** validity sweep, output pasted into the LOG: every
  committed artifact validated before and after. Under a hard cut the
  expectation is inverted from P33's — every migrated document must be valid
  under v6 and invalid under v5, and any document valid under BOTH is a document
  the migration did not actually change. The naive sweep will flag
  `@PLACEHOLDER@` carve templates as invalid under both; those are the known
  false positives, classify them explicitly rather than filtering them silently.

### The registered gate

`tools/tester-unified-gate.sh` runs P33's locked `test_acceptance_v5.py` against
the installed wheel. It will redden under v6, and P33's suite is a **locked
carver-owned asset that must not be edited**. Follow the exact precedent P33 set
for P26: keep the module, `--deselect` only the tests genuinely coupled to the
v5 artifact shape, add a new `carve-assets/W1/test_acceptance_v6.py` carrying
that coverage forward, and write the reasoning into the script's comment block
the way the existing four deselections are justified. The `--deselect` values are
**rootdir-relative nodeids** — an absolute spelling silently deselects nothing,
which leaves the gate looking wired while running the tests it claims to have
suppressed.

---

## 7. Work items, in order

Each lands as its own commit. Do not batch.

0. **Decisions first.** A-257…A-263 into `decisions.md`, verbatim from §0. A
   ruling that reaches only an agent message is not applied (A-072).
1. **B006 (a)** — §1. Independent of everything else.
2. **B006 (b)** — §2. Independent of everything else.
3. **Branch model + four parsers** — §3. No judge changes, no schema changes;
   `evaluate_coverage` still ignores `branches`. This item must be green on its
   own so the parser work is reviewable without the schema churn on top of it.
4. **The v6 contract** — §4, §5, §6 together: `evaluate.py`, `evaluate_targets`,
   `config.py`, `runner.py`, `verdict.py`, `verify.py`, the schema, the
   migration, the sweep, the fixtures, the gate wiring. These cannot be split;
   a half-migrated schema is a red tree.
5. **Documentation and spine** — `DESIGN-GUIDE.md` (§5 registry, §6 measurement
   causes, §11 the model), `STATE.md`, `4-backlog.md` (B005/B006 status lines
   AND their prose sections — a status line that contradicts its own section is
   how B002/B003 became confusing), the nyxloom spine
   (`2-product-definition.md` criteria for the new capability, with real pytest
   node ids — note `evidence_resolves` is AST-based and CANNOT resolve a
   parametrised node id, so cite the bare function id), and a LOG under
   `nyxloom-trove/reports/`.

---

## 8. Acceptance oracles

Every one of these is differential — it asserts the unmodified control behaves
correctly in the same test that asserts the injected defect does not — because a
negative that cannot fail is this project's most expensive recurring defect
(A-124, A-131).

**O1 — the real artifacts parse to the real numbers.** For each of the six
fixtures: the parsed profile's derived branch totals equal the artifact's own
stated totals, and `sample.py`'s combined percentage equals `57.142857…`, which
is `coverage.py`'s own `summary.percent_covered`. Not a hand-computed number —
the one the tool printed.

**O2 — capability is not inferred from emptiness.** `lcov.branch.info` parses to
`"reported"` with `test_sample.py` carrying an EMPTY `by_line`, not `None`, and
the whole artifact is not "mixed". `lcov.nobranch.info` parses to
`"unavailable"`. The same pair for Cobertura and coverage.py JSON. A parser that
returns `None` for the branch-free file fails this.

**O3 — go-cover declares, rather than omits.** A real go-cover fixture parses to
`branch_capability = "unavailable"`, and a `require_branch = true` lane over it
refuses with `NO_MEASUREMENT`/`BRANCH_UNAVAILABLE` rather than passing on lines.

**O4 — the tamper invariants bite.** Six mutations of the real coverage.py JSON,
each a one-key edit of a COPY (never the fixture): `covered_branches` off by
one; `num_branches` off by one; an arc whose source line is not in
`executed|missing`; an arc whose source line is in `missing` but appears in
`executed_branches`; `meta.branch_coverage` flipped to `false` with the arrays
left in place; a record missing `missing_branches` while `branch_coverage` is
`true`. Each must raise `ERROR`/`UNREADABLE_ARTIFACT`, and the unmodified
control must parse clean in the same test.

**O5 — an existing R1 lane's verdict actually changes.** Drive `assay run`
end-to-end on a real fixture repository whose changed lines include a partially
taken branch, with `--cov-branch` in the argv. Before this wave it PASSes at
100.0; after, it FAILs at the combined percentage with
`UNCOVERED_BRANCHES` and a `missing_branch_lines` entry naming the line. This is
A-258's whole consequence and it must be witnessed, not asserted.

**O6 — `UNCOVERED_BRANCHES` is not a synonym.** A lane failing with both a
missing line and an uncovered arc reports `UNCOVERED_LINES`; a lane failing with
only an uncovered arc reports `UNCOVERED_BRANCHES`. Both in one test.

**O7 — the whole-target floor cannot pass vacuously.** Four cases, all through
the real CLI: a target absent from the artifact ⇒
`NO_MEASUREMENT`/`TARGET_NOT_MEASURED`; a target present with zero executable
lines ⇒ the same; a target with an unexercised branch and `require_branch=true`
⇒ `FAIL`; and the same lane run from a commit where `base` would resolve to
HEAD ⇒ still judged, never `BASE_IS_HEAD`.

**O8 — a docstring-only change is still judged.** The consumer's actual failure
mode: a commit that changes zero executable lines of the target must produce
exactly the same whole-target verdict as any other commit. Prove it by running
the same lane at two commits differing only in a docstring and comparing the
coverage payloads.

**O9 — the escaping symlink knob is exact.** An undeclared escaping symlink
still refuses and the message lists ALL offenders; a declared one is omitted
from the snapshot (assert it is absent from the materialized tree — not merely
that the run proceeded); a declared entry that names a non-existent path, a
non-symlink, or a non-escaping symlink refuses; and a declared entry does not
weaken the check for any other path.

**O10 — the artifact parent is created only in the snapshot.** A lane writing
coverage into a gitignored `.assay/` with no `.gitkeep` runs green end to end;
the same reservation against a real (non-snapshot) project root with a missing
parent still refuses; and a symlinked path component refuses in both.

**O11 — v6 is a hard cut in both directions.** A v5 document is refused by
`assay verify` with a version diagnostic; a v6 document produced by `assay run`
verifies clean; and every committed fixture is valid under v6 and invalid under
v5 (the differential sweep, output pasted).

**O12 — the base conditional holds in the model AND the raw verifier
independently.** A whole-target artifact carrying a `resolved.base` is refused;
one omitting it verifies clean; a changed-line artifact omitting it is refused.
Deleting either implementation of the rule must turn a test red — check that by
actually deleting each in turn and recording the counts.

---

## 9. Traps, all of them paid for already

* **`git commit --only -- <paths>`, never stage-then-commit.** `/workspaces/vbpub`
  has a concurrent committer; `git add` + `git commit` races it. Never `reset`,
  `rebase`, or `--amend`.
* **Use the editor for file edits.** A hook blocks scripted writes (`sed -i`,
  Python `write_text` loops); a blocked write reads as success and leaves the
  file untouched.
* **Commit before gating.** `assay run` refuses a dirty worktree and the gate's
  own first step IS an `assay run`; an exit-3 gate whose only symptom is
  `NO_MEASUREMENT`/`DIRTY_TREE` is almost always uncommitted work.
* **Do not measure coverage by hand with
  `--ignore=tests/test_self_hosting.py`** — that file holds the only test
  covering `__init__.py`'s `PackageNotFoundError` fallback, and ignoring it
  reports a phantom 99% that looks like a regression.
* **`test_standalone.py::test_a_real_pass_matches_the_documented_r0_pass_shape`
  fails in this devcontainer and only here** (`0.1.0` vs `0.0.0`, because
  `setuptools_scm` is importable here and absent in the gate image). Do not
  "fix" it.
* **A stated pass/fail count is not evidence (A-232).** Paste the actual command
  and its actual output for every suite run, sweep and fixture load, and
  classify every red as legitimate or illegitimate.
* **A controlled break that comes back GREEN is a finding, not a broken probe**
  (A-179). Re-break at the property that actually owns the behaviour.
* **Locked carve assets under `carve-assets/P26/` and `carve-assets/P33/` are
  frozen historical evidence.** Never edit one to make the gate pass; deselect
  with a written justification instead.
* **No `pragma: no cover`.** 100% line AND branch under the gate's own suite.
  Note the standing exception recorded in STATE.md: the locked acceptance suites
  live outside `tests/`, so a new module reads 89–95% under bare `pytest tests`
  and only reaches 100% with the locked suite included. Do not "fix" that by
  copying carver-owned acceptance into `tests/`.

---

## 10. What must NOT change

* `A-030`: assay never shells out to docker, and nothing here needs to.
* `A-116`'s payload-free propagation shape, and the four hollow-PASS/FAIL
  `claim.allOf` branches from A-251 — extend them to the new modes, never widen
  past them. A payload-free R2 FAIL stays legal.
* `A-120`/`A-122`: mutation isolation and `jobs`, untouched.
* The `judgment.r2` / `judgment.r3` objects, untouched apart from the shared
  `resolved.base` conditional.
* P27's frozen Go inputs and P34's SQL scope: this wave must not "helpfully"
  touch either. `go-cover`'s branch capability is the only Go-adjacent line of
  code in scope, and it is one unconditional assignment plus its test.
