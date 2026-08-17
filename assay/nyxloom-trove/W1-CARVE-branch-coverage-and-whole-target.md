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

Operator decisions taken 2026-08-16 (A-257…A-263), two the independent
pre-dispatch review forced (A-264, A-265 — see Addendum B), and one taken
upstream in `main` on 2026-08-17 that superseded this wave's own first design
(A-266 — see Addendum C). All ten to be recorded in `decisions.md` by the
implementer as work item 0, in these words:

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
* **A-266 — a lane declares its snapshot boundary affirmatively; assay never
  ignores a path it materialises.** Ruled upstream in `main` `c7bc9b59` and
  taken here on 2026-08-17, superseding this wave's own first design. An
  allowlist or an exclude list states which paths not to look at while still
  putting them in the tree the command runs against; a declared boundary states
  which paths exist at all. Only the second is provable, and only the second can
  be attested in the verdict. The withdrawn allowlist must not be re-proposed —
  §1 records the argument so it does not have to be re-had.
* **A-264 — R1 records its policy whenever R1 was ATTEMPTED, for exactly two
  new terminals.** Today `judgment.r1` is present iff the R1 claim carries a
  coverage payload, enforced in the model (`verdict.py:2242`: "judgment.r1 is
  present but no R1 claim rendered a coverage payload"). That makes the two new
  payload-free refusals — `BRANCH_UNAVAILABLE` and `TARGET_NOT_MEASURED` —
  incapable of recording which target was attempted or which floor was asked
  for, which would gut B005's whole reason for existing (a rigor decision
  invisible to the verdict is the thing it replaces). R2 already has exactly
  this widening for `MUTATION_UNSUPPORTED` (A-183, `verdict.py:2256`: "R2 policy
  is recorded whenever R2 was actually ATTEMPTED, which is one case wider than
  rendered a payload"). R1 takes the same shape, narrowed to a CLOSED set of
  two reason codes. Every other payload-free R1 terminal — `DIRTY_TREE`,
  `BASE_IS_HEAD`, `GIT_FAILED`, `EMPTY_COVERAGE`, `FORMAT_MISMATCH`,
  `UNREADABLE_ARTIFACT` — keeps today's rule and must have a negative test
  proving it.
* **A-265 — an artifact's branch DETAIL is authoritative over its capability
  METADATA, and disagreement is a refusal.** Plus: arc identities are validated
  for uniqueness and executed/missing disjointness BEFORE they are aggregated
  into per-line counts, because aggregation destroys the evidence a duplicate
  could be caught with. See §3.1a and §3.2.
* **A-263 — `pct` is the COMBINED line+branch percentage.** `(covered +
  branches_covered) / (executable + branches_total)`, which is exactly
  `coverage.py`'s own `summary.percent_covered` under `--cov-branch`. The METRIC
  is identical; the COMPARISON is not, and the difference is stated rather than
  glossed: coverage.py compares a value rounded to the configured precision
  (`round(total, precision) < fail_under`), assay compares the unrounded value,
  which is stricter and precision-independent. At the floor the consumer
  actually uses they agree — measured here, `coverage report --fail-under=100`
  over a 99.60% module printed `99%` and exited 2. `covered` and
  `executable` stay line-only; the branch side gets its own two integers, so an
  independent consumer can still re-derive `pct` from the payload alone. When
  branch capability is `"unavailable"`, `branches_total` is 0 and the formula
  degenerates to today's line-only value with no special case.

---

## 1. B006 (a) — explicit, attested project-scoped snapshots (A-266)

**This section was rewritten on 2026-08-17.** `main` commit `c7bc9b59`
("docs(assay): specify safe monorepo snapshot scope") replaced B006's proposal
while this wave was being implemented, and merge `0791d9c4` brings it onto this
branch. The backlog is the requirement; this section is its design.

### The withdrawn design, and why it must not come back

The first version of this section shipped a lane-declared
`allow_escaping_symlinks` allowlist. **It is withdrawn**, in the backlog's own
words: *"source roots do not include every test dependency, and an ignore list
only hides a path from validation without proving the executed command cannot
reach it."* Both halves matter. An allowlist is a promise about a path assay
still materialises; the executed command can still open it. What the consumer
needs is not permission to ignore a path but proof the path was never there.

The other rejected option — scoping the walk to `source_roots` — fails for the
same reason from the other direction: source roots are what a lane *judges*, not
what its command *reads*, so scoping to them silently withholds files lanes
legitimately need.

### What is true today, verified

`isolation.py::_check_symlink_target` (~:860) refuses any tracked symlink whose
target is absolute or escapes the snapshot root, raising `GIT_FAILED`, and it is
applied to every entry of the whole committed tree because P22 materialises the
whole tree. The consequence is now reproduced by a second consumer: CMRU's
higher-rigor lane is rooted at `cmru`, and assay refuses Topos's tracked fixture
`topos/tests/fixtures/inspect_files/_danger/passwd_link -> /etc/passwd` before
CMRU's command ever runs. CMRU neither owns nor reads that path. Its `assay.toml`
now says so in writing and holds itself to R0 until this ships: *"R0 is the only
honest Assay declaration in this monorepo today… A project-scoped snapshot is a
versioned Assay product decision, not a consumer-side bypass."*

### What ships — an affirmative materialisation boundary

```toml
[lanes.cmru.isolation]
snapshot_scope = "project"          # closed: "repository" | "project"
project_prefix = "cmru"             # repo-top-relative, the owned tree
inputs = ["release/samples/pinned-manifest.json"]   # additional tracked paths
```

Named `snapshot_scope`, not `scope`, because a lane already has a `scope` key
meaning its S-level, and `mode` is taken by `judge.mode`.

1. **The choice is explicit.** `snapshot_scope = "repository"` is exactly today's
   P22 behaviour, unchanged. Absent means `"repository"` — the only behaviour
   that existed before — stored as the declared value or `None` so
   `as_declared()` stays faithful, with the EFFECTIVE value resolved in one named
   place and recorded in the artifact, the same treatment `judge.mode` gets in
   §5. There is no ambient discovery and no fallback to the caller's checkout: a
   project that needs a sibling path names it.
2. **Every scope path is canonicalised as a Git-tree path**, repo-top-relative
   (A-145: say which spelling, everywhere). Refused: absolute, empty, any `.`/
   `..` component, a backslash, a non-canonical spelling whose `PurePosixPath`
   round-trip differs from the raw string, a duplicate, an `inputs` entry that
   already lies inside `project_prefix` (ambiguous overlap), and — checked
   against the resolved commit rather than the loader — a path that is missing or
   untracked there. Shape refusals are `ERROR`/`BAD_LANE_CONFIG` at load;
   commit-relative refusals are `ERROR`/`BAD_LANE_CONFIG` at preflight, before
   any materialisation, following `_coverage_artifact_is_tracked`'s precedent
   rather than `GIT_FAILED` (the declaration is wrong, the repository is not).
3. **The commit closure is retained; only the boundary is materialised.** The
   resolved commit, its object closure, base resolution and provenance are
   exactly as they are today — this changes what lands in the working tree, not
   what assay resolves or records. Materialise `project_prefix` plus each
   `inputs` entry, nothing else, in a private index/worktree.
4. **An in-scope symlink keeps P22's containment check and fails closed** — with
   containment now meaning *the materialised boundary*, not the repository. A
   relative symlink inside `cmru/` pointing at `../topos/x` escapes the boundary
   and is refused exactly as an absolute target is. Out-of-scope symlinks are
   never examined because they are never materialised; that is the whole security
   argument, and it belongs in the code as a comment, not only here.
5. **Preflight validates the boundary covers the work.** Every `source_root`, the
   coverage artifact path, every mutation candidate, the canary target, and the
   command's working directory must resolve inside the materialised boundary,
   checked before execution and by `is_relative_to` on resolved paths, never
   string prefixes. Outside ⇒ `ERROR`/`BAD_LANE_CONFIG` naming the item and the
   boundary. **The boundary is never broadened automatically** because something
   is missing — that would be the invented-fallback failure this whole item
   exists to remove.
6. **Private index/worktree only.** No flag, generated parent, hook, index entry
   or source replacement may appear in the consumer's checkout, and a nested
   command must not be able to regain an omitted file through the environment or
   relative traversal. A private full-HEAD index with non-selected entries marked
   `skip-worktree` is one permitted implementation; whatever is chosen must prove
   a clean checkout and that the command cannot read a sibling worktree.

### Recorded in the verdict

A new top-level `isolation` object, REQUIRED in v6 (see §6): the effective
`snapshot_scope`, and — for project scope — `project_prefix` and the canonical
expanded `inputs`. It deliberately does **not** repeat the resolved commit: the
document already carries a required top-level `commit`, and a second copy is a
second thing to disagree with itself.

This is the half that makes the capability honest rather than convenient. A
reviewer must be able to tell a full-repository verdict from a project-scoped one
without re-running anything, which is precisely what the backlog demands: *"a
schema/versioned attestation so reviewers can distinguish full-repository from
project-scoped evidence."*

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

* **Confined to the assay-owned output path, inside the already-declared
  scope** (added 2026-08-17 with §1's rewrite, from the backlog's own wording).
  This is not permission to create arbitrary missing paths, and never permission
  to climb above the boundary: under `snapshot_scope = "project"` the artifact
  path must already have passed §1's preflight, and the creation happens beneath
  it. A symlinked or escaping parent remains a loud refusal.

Independently of the creation: when a parent chain still cannot be opened, the
diagnostic must name the missing component and the declared artifact path, and
must distinguish **setup failure** ("the parent could not be created/opened")
from **a genuinely unreadable artifact** ("the command produced nothing usable").
Collapsing those two into one generic `UNREADABLE_ARTIFACT` is what cost the
consumer a debugging round. The verdict records the requested artifact path
either way — it already does, via `judgment.r1.coverage_artifact`, so this is a
check that the existing field is populated on the refusal path, not a new field.

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

### 3.1a Arc identity is validated BEFORE it is aggregated (A-265)

Aggregating `(covered, total)` per line throws the arc identities away, so every
identity rule has to fire in the parser, before the counts exist. Without this,
a tampered artifact that simply REPEATS a covered arc inflates the numerator,
satisfies every totals cross-check below (because the stated totals were
tampered to match), and is undetectable afterwards — the same reasoning
`FileCoverage` already applies to executed/missing line arrays, which the model
calls out as "three INDEPENDENT arrays straight from external, potentially
adversarial input" (`model.py:47`).

Per file, refuse `ERROR`/`UNREADABLE_ARTIFACT` when:

* the same `(src, dst)` appears twice within `executed_branches`, or twice
  within `missing_branches`; or
* the same `(src, dst)` appears in BOTH arrays — an arc cannot be simultaneously
  taken and not taken, exactly as a line cannot be both executed and missing;
* (lcov) the same `(line, block, branch_id)` triple appears twice, whatever the
  `taken` values — identical or contradictory, a repeated identity means the
  record cannot be read without inventing a precedence rule;
* (Cobertura) the same file+line is reported by two `<class>` elements with
  DIFFERENT `(covered, total)` — see §3.3.

### 3.2 Capability derivation is ARTIFACT-level, and DETAIL is the authority

`coverage.py` gains `derive_branch_capability(profile)`, mirroring
`derive_exclusion_capability` exactly: all-`None` ⇒ `"unavailable"`,
none-`None` ⇒ `"reported"`, **mixed ⇒ `ERROR`/`UNREADABLE_ARTIFACT`**.

**The authority rule, which every parser obeys (A-265):** an artifact's branch
DETAIL decides its capability. Capability METADATA — coverage.py's
`meta.branch_coverage`, Cobertura's root `branches-valid` — is a cross-check,
and **any disagreement between metadata and detail is
`ERROR`/`UNREADABLE_ARTIFACT`**, never a silent resolution in either direction.

That rule exists because the obvious alternative — "metadata decides" — has a
false-PASS hole in it: an artifact with `meta` absent (or `branch_coverage:
false`) but real arc arrays present would be read as `"unavailable"`, its branch
evidence silently dropped, and with `require_branch = false` the lane would
report a line-only PASS over an artifact that had measured branches all along.
A-258 says branches are judged *whenever the artifact reports them*; letting
metadata veto present detail makes that sentence false. Concretely:

| artifact says | capability |
|---|---|
| detail present, metadata agrees or is absent | `"reported"` |
| no detail anywhere, metadata absent or says none | `"unavailable"` |
| detail present, metadata says none | **refused** |
| no detail anywhere, metadata claims branches exist | **refused** |

That refusal is only safe because each parser decides capability once for the
whole artifact and applies it to every file it emits. The trap, witnessed in
`lcov.branch.info`: coverage.py emits `BRF`/`BRH` for `sample.py` and **nothing
at all** for `test_sample.py`, which has no branches. A per-file rule would call
that single real, correct artifact "mixed" and refuse it. Every parser must
therefore emit `branches=BranchCoverage(by_line={})` — not `None` — for a
branch-free file in a branch-tracking artifact.

### 3.3 Per-format rules

**`coverage-py-json` — metadata and detail must agree.** Detail = any file
record carrying `executed_branches` or `missing_branches`. Metadata =
`meta.branch_coverage` when the `meta` object is present. Detail present ⇒
`"reported"` for every file (a branch-free record gets an empty `by_line`, never
`None`); no detail anywhere ⇒ `"unavailable"` for every file. `meta` present and
disagreeing with that — `false` beside arrays, or `true` with no arrays anywhere
— is `UNREADABLE_ARTIFACT`. Then:

* `executed_branches` / `missing_branches` are arrays of `[src, dst]` pairs.
  Group by `src`: `total` = arcs with that source, `covered` = those in
  `executed_branches`. `dst` is used ONLY for §3.1a's identity checks and is not
  stored — assay judges branches at their source line, but it must see the whole
  identity before it collapses one.
* In a `"reported"` artifact, a file record carrying ONE array and not the other
  is `UNREADABLE_ARTIFACT`, with a message naming the likely cause: a
  coverage.py too old to report per-line arcs. Failing closed is deliberate —
  the alternative is judging a branch floor against file-level totals that
  cannot be attributed to a line.
* Cross-check, when `summary` carries them: `summary.num_branches` must equal
  the derived total and `summary.covered_branches` the derived covered.
  Mismatch is `UNREADABLE_ARTIFACT` — the artifact's own claims about itself
  disagree, which is the same refusal the normalized-key collision already
  makes. A `summary` that omits the branch keys is not malformed; skip the
  cross-check and say so in the docstring.
* Malformed pair shapes (not a 2-element list, non-int, bool) are
  `UNREADABLE_ARTIFACT` per the existing `_int_list` discipline. `src` must be a
  positive line; `dst` is an opaque integer and **is legitimately negative** —
  `coverage-py-json.exitarc.json` carries `[11, -10]` for an arc that leaves the
  function starting at line 10. A parser requiring both members positive rejects
  that real artifact.

**`lcov` — detail is the only signal the format has.** If ANY record in the
artifact carries `BRDA`, `BRF` or `BRH`, branch tracking was on: every file gets
a `BranchCoverage`, empty for records with no branch lines. If NO record
anywhere carries one, every file gets `None`. lcov has no capability metadata,
so §3.2's disagreement case cannot arise here.

* `BRDA:<line>,<block>,<branch>,<taken>` — group by `line`; `taken` is `-`
  (block never entered) or a decimal count. `covered` counts entries whose
  `taken` is a count `> 0`; `-` and `0` are both uncovered. `<block>` and
  `<branch>` are opaque identity fields, not numbers to sum — coverage.py writes
  the human strings `jump to line 6` and `return from function
  'falls_off_the_end'` there, so a parser requiring an integer rejects a real
  artifact.
* Split the record as: `line` and `block` off the LEFT on the first two commas,
  `taken` off the RIGHT with `rsplit(",", 1)`, branch id is the remainder.
  **This is a defensive choice, not a fixture-proven necessity** — every
  witnessed record has exactly three delimiter commas and would survive a
  four-field split; no artifact here carries a comma inside a branch id. It is
  specified this way because it is correct for every witnessed record AND
  degrades safely for an unwitnessed one, and the reason is written down rather
  than dressed up as evidence.
* `BRF`/`BRH`, when present, must equal the derived total/covered for that
  record. Mismatch is `UNREADABLE_ARTIFACT`.

**`cobertura` — per-line detail decides, the root count cross-checks.** Any
`<line branch="true" …>` anywhere ⇒ `"reported"` for every file; none anywhere ⇒
`"unavailable"`. Root `branches-valid`, when present, must agree: `> 0` with no
per-line detail, or `0`/absent with detail present, is `UNREADABLE_ARTIFACT` per
§3.2. The acknowledged, documented edge remains: branch tracking on for a
project with zero branches anywhere emits neither signal and is read as
`"unavailable"`, which fails CLOSED under `require_branch`.

* Per line: `condition-coverage="P% (C/T)"`. Parse `(C/T)` for the covered/total
  arcs and **ignore `P` entirely** — do not verify it. No fixture witnesses a
  disagreement, `P`'s rounding grammar is unspecified by the DTD, and a
  tolerance rule invented here would be behaviour nothing measured. A missing or
  unparsable `(C/T)` on a `branch="true"` line IS refused. `missing-branches` is
  a coverage.py extension and is not read.
* `branches-valid`/`branches-covered` at the root must equal the summed derived
  totals across all files. Mismatch is `UNREADABLE_ARTIFACT`.
* **Multiple `<class>` elements may name the same file** — the existing parser
  permits it and merges line hits with "executed wins" (`cobertura.py:23`). For
  branch data there is no safe merge: summing double-counts, taking a maximum
  invents a measurement, and executed-wins has no meaning for a ratio. So a
  file+line reported twice must carry IDENTICAL `(C, T)`; anything else is
  `UNREADABLE_ARTIFACT`. The existing line-merge behaviour is untouched.

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
  component, backslash, duplicate, or a **non-canonical spelling** — an entry
  whose `PurePosixPath` round-trip differs from the raw string. Without that
  last rule `src/good.py` and `src//good.py` are two distinct strings naming one
  file: a raw-string uniqueness check accepts both, the schema's `uniqueItems`
  accepts both, and evaluation then counts a well-covered target TWICE, raising
  the aggregate enough to carry a poorly covered sibling over the floor. That is
  a false PASS reachable from a plausible typo. Uniqueness is additionally
  re-checked after conversion to the repository-top-relative spelling, so two
  different-but-equivalent declarations can never both be judged.
* `require_branch` and `mode` interact with R3: `judge.canary.mechanism =
  "uncovered-line"` proves "a changed-line coverage floor rejects an uncovered
  line" (`canary.py:118`), a premise whole-target mode replaces. On a
  `whole_target` lane that mechanism is **refused at load unless
  `judge.canary.target` is itself one of `targets`** — inside the targets it
  still means exactly what it says; outside them it proves nothing and would
  produce an accidental `CANARY_SURVIVED` that looks like a real finding.
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

**Exactly one of the new invariants belongs in the schema.** Draft 2020-12 has
no `$data`, which the shipped schema states about itself
(`verdict.schema.json:5`: "claiming those here would be a hollow contract"), and
A-182 forbids crediting a layer with a relation it cannot express. So:

* **Schema (`allOf`, mirroring the exclusion-capability branch that exists
  today):** `branch_capability = "unavailable"` ⇒ `branches_total = 0`,
  `branches_covered = 0`, `missing_branch_lines` empty,
  `files_with_missing_branch_lines` empty. The converse is deliberately NOT a
  rule — `"reported"` with zero branches is a capable format truthfully finding
  none, and forbidding it re-collapses the very distinction A-008 keeps.
* **Model (`Coverage.__post_init__`) AND the raw verifier, independently:**
  `branches_covered <= branches_total`; `covered <= executable`; and
  `files_with_missing_branch_lines` equals the key set of `missing_branch_lines`
  — the last joining the pairs already covered by
  `Coverage._check_summaries_name_their_own_detail`. Both implementations must
  be proven load-bearing by deleting each in turn and recording that a test goes
  red, the same evidence O12 requires for the base conditional.

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

**And `judgment.r1` must now survive two payload-free terminals (A-264).**
Today the model requires `judgment.r1` present iff the R1 claim carries a
coverage payload (`verdict.py:2242`), and the producer only builds one then
(`runner.py:1368`). Widen BOTH — model, raw verifier, schema
`dependentRequired`, and producer — so `judgment.r1` is ALSO present when the R1
claim is payload-free with reason `BRANCH_UNAVAILABLE` or `TARGET_NOT_MEASURED`,
and remains forbidden for every other payload-free R1 terminal. Copy A-183's R2
wording and structure; do not invent a second shape for the same idea. Without
this, a `TARGET_NOT_MEASURED` artifact cannot say which target it could not
measure, and the whole-target judge records less than the argv stopgap it
replaces.

### `judgment.resolved.base`

The conditional widens. Today: required iff `judgment` carries `r1` or `r2`.
v6: required iff `judgment` carries `r2`, **or** carries `r1` with
`mode = "changed_lines"`; **forbidden** iff `judgment` carries `r1` with
`mode = "whole_target"` and no `r2`. Both `verdict.py::Judgment` and
`verify.py::_check_base_matches_the_tiers_present` own this rule
independently — that duplication is intentional (A-181/A-182's model/raw-verifier
split) and both must move together.

### `isolation`

New **required** top-level object recording the materialisation boundary that
actually ran (§1, A-266):

| field | rule |
|---|---|
| `snapshot_scope` | required, enum `repository` \| `project` — the EFFECTIVE value, never absent because the lane omitted the key |
| `project_prefix` | required iff `snapshot_scope = "project"`, forbidden otherwise; repo-top-relative, canonical, non-empty |
| `inputs` | required iff `snapshot_scope = "project"` (possibly empty), forbidden otherwise; canonical repo-top-relative paths, unique, sorted |

Required rather than optional because "absent" would be indistinguishable
between an old producer and a repository-scoped run, and the whole point is that
a reviewer can tell project-scoped evidence from full-repository evidence
without re-running anything. It does NOT carry the commit: the document already
has a required top-level `commit`, and two copies of one fact is one fact too
many.

The conditional pairs (`project_prefix`/`inputs` present iff project scope) ARE
expressible in Draft 2020-12 via `if`/`then`/`else`, unlike §6's numeric
comparisons — so they live in the schema as well as the model and the raw
verifier.

An R0-only lane never snapshots (`runner.py:1857`, "direct live-tree execution")
so it records `snapshot_scope: "repository"` — the truthful statement that no
project boundary was applied — and declaring an `[isolation]` table on an
R0-only lane is refused at load as inert configuration (A-062).

### `reason_code`

Three new members of the closed enum: `UNCOVERED_BRANCHES`,
`BRANCH_UNAVAILABLE`, `TARGET_NOT_MEASURED`. Each needs its
outcome-pairing entry wherever `_check_reason_code` and the conformance matrix
enumerate legal `(outcome, reason_code)` pairs, and each needs a hand-written
fixture — the P14 lesson is that a capability lands and the matrix that measures
it does not move with it, leaving a *correct* fixture failing the suite.

### Migration

Mechanical, and it must be auditable rather than 40-odd hand edits. **The unit
of migration is a TYPED BUCKET, not a grep hit.** `git grep -l '"schema_version":
5'` currently returns 56 tracked files and they are not 56 verdict documents:
`carve-assets/P33/migration-manifest.json` carries its OWN manifest schema
version, `tests/test_output_reservation.py` contains the literal string as
expected output, and this specification matches itself. Transforming any of
those corrupts unrelated data.

* `nyxloom-trove/carve-assets/W1/migrate_v5_to_v6.py`, in the register of P33's
  `migrate_v4_to_v5.py`: a logged, itemised transform with a `--check` mode that
  exits 0 **before and after** implementation. It classifies every matching file
  into exactly one of four buckets and **refuses to run if any file falls into
  none** — a fail-closed classifier is stronger than a sweep, because an
  unclassifiable file stops the migration instead of being silently skipped:
  1. **transform** — verdict documents that become v6 (`tests/fixtures/verdicts/**`);
  2. **preserve byte-identical** — locked carver-owned evidence, including all
     six `carve-assets/P33/expected/*-v5-template.json` and every earlier
     package's frozen templates (A-222);
  3. **hand-edit source** — `src/**`, the schema, `tests/conftest.py`, and the
     tests that assert version-coupled facts;
  4. **must not change** — files that merely mention the string, listed
     explicitly with the reason each is exempt.
* **Do not write `sweep_v5_consumers.py`.** P33's sweep answers a different
  question — "does this file read a path under a carver-owned frozen tree and
  compare it" (`sweep_v4_consumers.py:350`) — so it is an inventory of frozen-
  asset consumers, not of schema-version consumers. RUN it (running a locked
  asset is not editing it), paste its output into the LOG, and use it to check
  bucket 2 is complete. The bucket classifier above is what closes the
  schema-version question.
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
the way the existing four deselections are justified.

**Derive the deselection list by MEASUREMENT, not by reading.** The coupling is
wider than the six template-consuming tests: the suite also asserts the exact
`$id`, the version constant, and byte-identical schema equality with
`verdict.schema.v5.json` (`test_acceptance_v5.py:98`). So: implement v6, run the
locked suite unmodified, list every red, and classify each one as
"legitimately v5-coupled → deselect, with the v6 suite covering the same
property" or "a real regression → fix the code". Paste both lists. A deselection
without a named v6 successor test is coverage silently dropped. The v6 suite
gets its OWN `expected/` templates — the six v5 templates stay frozen and are
never rewritten into v6. The `--deselect` values are
**rootdir-relative nodeids** — an absolute spelling silently deselects nothing,
which leaves the gate looking wired while running the tests it claims to have
suppressed.

---

## 7. Work items, in order

Each lands as its own commit. Do not batch.

0. **Decisions first.** A-257…A-266 into `decisions.md`, verbatim from §0. A
   ruling that reaches only an agent message is not applied (A-072).
1. **B006 (a) — the project-scoped snapshot** — §1. **This is now the largest
   single item in the wave**, not the small papercut the original backlog
   described, and it is the one with a consumer blocked on it today. It splits:
   1a. config — the `[isolation]` table, its closed grammar, every load-time
       refusal, and the R0-only refusal;
   1b. isolation — canonicalisation against the resolved commit, the private
       index/worktree materialisation of prefix + inputs, boundary-relative
       symlink containment, and the no-leakage proof;
   1c. runner — the preflight that every source root, artifact, mutation
       candidate, canary target and cwd lies inside the boundary.
   1a/1b/1c land as three commits. 1b is where the security property lives; if
   any of its oracles cannot be written honestly, STOP and report rather than
   weakening the oracle.
2. **B006 (b)** — §2. Independent of §1's mechanism, but its "inside the
   declared scope" constraint only means something once 1c exists, so land it
   after.
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

**O1 — the real artifacts parse to the real numbers.** For each of the EIGHT
fixtures: the parsed profile's derived branch totals equal the artifact's own
stated totals, and `sample.py`'s combined percentage equals `57.142857…`, which
is `coverage.py`'s own `summary.percent_covered`. Not a hand-computed number —
the one the tool printed. The two `*.exitarc.*` artifacts additionally parse
without error, which is the negative-`dst` and free-text-branch-id proof.

**O2 — capability is not inferred from emptiness.** `lcov.branch.info` parses to
`"reported"` with `test_sample.py` carrying an EMPTY `by_line`, not `None`, and
the whole artifact is not "mixed". `lcov.nobranch.info` parses to
`"unavailable"`. The same pair for Cobertura and coverage.py JSON. A parser that
returns `None` for the branch-free file fails this.

**O3 — go-cover declares, rather than omits.** A real go-cover fixture parses to
`branch_capability = "unavailable"`, and a `require_branch = true` lane over it
refuses with `NO_MEASUREMENT`/`BRANCH_UNAVAILABLE` rather than passing on lines.

**O4 — the tamper invariants bite.** Mutations of the real coverage.py JSON,
each a one-key edit of a COPY (never the fixture): `covered_branches` off by
one; `num_branches` off by one; an arc whose source line is not in
`executed|missing`; an arc whose source line is in `missing` but appears in
`executed_branches`; `meta.branch_coverage` flipped to `false` with the arrays
left in place; `meta` deleted entirely with the arrays left in place (this one
must parse as `"reported"`, NOT refuse — detail is authoritative); `meta` saying
`true` with every arc array removed; a record carrying `executed_branches` but
not `missing_branches`; **a duplicated arc inside `executed_branches` with
`covered_branches` incremented to match** (the coherent-tamper case: every
totals cross-check passes, and only the identity rule catches it); and the same
`(src,dst)` present in both arrays. Each must raise
`ERROR`/`UNREADABLE_ARTIFACT` except the `meta`-deleted case, and the unmodified
control must parse clean in the same test.

**O4b — the other two formats' identity and merge rules.** An lcov copy with a
repeated `(line, block, branch_id)` triple refuses. A Cobertura copy with two
`<class>` elements naming one file, agreeing on `(C, T)` for a shared line,
parses clean; the same document with those two disagreeing refuses. A Cobertura
copy whose `condition-coverage` percentage text is nonsense but whose `(C/T)` is
intact parses clean — the percentage is deliberately not read, and this test is
what stops someone "helpfully" adding a tolerance rule later.

**O4c — capability disagreement is a refusal in both directions.** A Cobertura
copy with `branches-valid="0"` but per-line `branch="true"` detail refuses; one
with `branches-valid="8"` and no per-line detail refuses.

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

**O9 — the project boundary is real, not a filter.** These are the backlog's own
acceptance tests, and they are the acceptance bar for work item 1:

* a fixture repository carrying an absolute-target symlink OUTSIDE the project
  scope: project-scoped R1, R2 and R3 all run normally, and the external target
  is **never materialised and not readable from the command** — assert both, and
  assert the file's absence from the materialised tree rather than inferring it
  from the run having proceeded. The SAME repository under
  `snapshot_scope = "repository"` still fails with P22's existing diagnostic, in
  the same test, so the two scopes are distinguished by evidence;
* an absolute-target or escaping-relative symlink INSIDE the owned prefix or a
  declared input fails before the command runs — including a relative symlink
  that escapes the boundary while staying inside the repository, which is the
  case P22 alone would have allowed;
* malformed, missing, untracked, absolute and `..` declarations each refuse,
  each with their own diagnostic;
* a named root test dependency is present, and REMOVING it from `inputs` causes
  a deterministic preflight failure rather than the command silently reading it
  from the ambient checkout. This is the oracle that proves there is no
  fallback, and it is the one most easily written so it cannot fail — it must
  assert the command's own observation of the file, not just assay's refusal;
* private `HEAD`, `git status`, `git diff`, base diff and replacement semantics
  are exact inside the snapshot;
* mutation and canary targets outside the boundary are refused; in-scope targets
  are modified only inside the private snapshot, and a deliberately failing run
  leaves **no** temporary index, worktree or `skip-worktree` flag in the source
  repository — assert the source repo's `git status` and index are byte-unchanged
  after a red run, not only after a green one.

**O9b — the end-to-end consumer proof.** A CMRU lane running in
`tester-unified` makes genuine R1/R2/R3 claims while the Topos
`/etc/passwd` fixture remains tracked; its bounded mutation campaign kills every
non-equivalent mutant and its canary fails for the required coverage reason.
This is the backlog's own final acceptance test and the reason the capability
exists. If it cannot be run inside this wave, say so explicitly rather than
substituting a synthetic repository and calling it done.

**O10 — the artifact parent is created only in the snapshot.** A lane writing
coverage into a gitignored `.assay/` with no `.gitkeep` runs green end to end;
the same reservation against a real (non-snapshot) project root with a missing
parent still refuses; and a symlinked path component refuses in both.

**O11 — v6 is a hard cut in both directions.** A v5 document is refused by
`assay verify` with a version diagnostic; a v6 document produced by `assay run`
verifies clean; and every committed fixture is valid under v6 and invalid under
v5 (the differential sweep, output pasted).

**O13 — the two attempted-policy terminals record their policy, and no others
do (A-264).** A `TARGET_NOT_MEASURED` artifact carries `judgment.r1` naming the
targets it could not measure; a `BRANCH_UNAVAILABLE` artifact carries the
`require_branch` that refused it; and an artifact for each of the six OTHER
payload-free R1 terminals still carries no `judgment.r1` and is refused if one
is injected. All through the real CLI plus the raw verifier.

**O14 — two spellings of one target cannot both be judged.** A lane declaring
`["src/a.py", "src//a.py"]` is refused at load; so is `["src/a.py/"]`. Prove the
refusal is the loader's, not an accident of a later stage.

**O15 — the isolation table is refused where it would be inert, and recorded
where it ran.** An R0-only lane declaring `[lanes.X.isolation]` is refused at
load; the same table on an R1 lane loads, and the resulting artifact carries
`isolation.snapshot_scope` with the prefix and expanded inputs. An R0-only lane
that declares no table still records `snapshot_scope: "repository"` — the
attestation is required, so "no boundary was applied" is a statement the
artifact makes rather than an absence a reader has to interpret.

**O16 — the whole-target/R3 interaction is deliberate.** A `whole_target` lane
declaring an `uncovered-line` canary whose target is NOT in `targets` is refused
at load; the same lane with the canary target inside `targets` loads and the
canary still kills.

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

## 10. Addendum A — the carver's own corrections

Found by verifying this contract against the real code and the real artifacts
AFTER writing it. Each supersedes the corresponding sentence in the body. They
are recorded rather than silently edited in, because "trust but verify, even
your own prior ruling" is a standing rule here (A-240) and a spec that hides its
own corrections teaches the implementer that the body is infallible.

**A1 — `dst` CAN be negative; this is now measured, not a caution.** §3.3 asked
the implementer to prove it. It is proven: `coverage-py-json.exitarc.json`
carries `missing_branches: [[5,7],[11,-10]]`, where `-10` encodes "this arc
leaves the function that starts at line 10". Validate `src` as a positive line;
treat `dst` as an opaque integer and never store it.

**A2 — the lcov branch id is free text. WITHDRAWN AS WRITTEN, see B8.** The
original claim — that `lcov.exitarc.info` proves `BRDA` "cannot be split by
comma count" — is false of the evidence: `BRDA:11,0,return from function
'falls_off_the_end',0` contains exactly the three delimiter commas a four-field
split expects. What it does prove is that the branch id is non-numeric free
text, so a parser reading it as an integer refuses a real record. The
`rsplit(",", 1)` rule stands in §3.3 as an explicitly DEFENSIVE choice with its
reason stated, not as a fixture-proven necessity.

**A3 — do NOT write `sweep_v5_consumers.py`.** §6's migration bullet asked for
one. P33's `sweep_v4_consumers.py` is version-agnostic in its inventory role —
it asks "does this file read a path under a carver-owned frozen tree and compare
it", not "does this file mention v4" — and it RUNS clean on this branch today
(`python3 nyxloom-trove/carve-assets/P33/sweep_v4_consumers.py`, 20-odd
consumers reported, several flagged `<-- compares a VERDICT artifact`). Running
a locked asset is not editing it. Run it, paste its output into the LOG, and
write a new sweep ONLY if its predicate proves genuinely v4-bound — in which
case say which line proves that.

**A4 — the migration is not the ~47 in `tests/`, and some matches are not
verdicts at all. PARTLY WITHDRAWN, see B7 and §6's bucket rule.** The count in
the original text (55) was taken before this file itself matched the grep; it is
56 now, and — the part that actually matters and that this addendum got wrong —
**they are not all verdict documents**. `git grep -l '"schema_version": 5'`
includes files whose match is a manifest's own version or a literal string in a
test. Migration is by typed bucket, never by grep hit. What survives from the
original wording: six of the matches are LOCKED, including
`nyxloom-trove/carve-assets/P33/expected/*-v5-template.json`. A-222 already
ruled the analogous case for P26's v4 templates: **frozen historical evidence is
not rewritten**. So: leave all six P33 templates byte-identical, give
`carve-assets/W1/` its own `expected/` templates for the v6 suite, and deselect
the P33 suite's template-coupled tests in the gate with the same written
justification the existing four deselections carry. An implementer who "migrates
all 55" has destroyed evidence.

**A5 — `reserve_output` has exactly one caller.** `runner.py:1092`, inside
`_execute_snapshot_unit`, always against `snapshot.project_root`. So §2's "prove
the non-snapshot path still refuses" is a direct `safeio` unit test over a real
temporary directory, not an end-to-end lane run — there is no end-to-end
non-snapshot path to drive. The defaulted-off parameter still ships: it is the
contract for the next caller, and this project's own history is full of
capabilities that arrived before the caller that needed them.

**A6 — three loader edits the body only implies.** `_KNOWN_JUDGE_FIELDS`
(`config.py:159`) is a closed tuple and an unlisted key is refused as unknown —
`mode`, `targets` and `require_branch` must be added to it or every new lane
fails to load. And A-062's surplus-config refusal (`config.py`, the comment
block at ~:750: "Judge config for a rigor level the lane does NOT declare is
refused… if it is wrong nothing fails") must be extended so that `base` on a
whole-target lane with no R2 is refused as inert config, by the same argument
that motivated A-062 in the first place.

**A8 — the scaffolding for the end-to-end oracles already exists; use it.**
`tests/conftest.py` ships `git_repo` (:233), `project` (:183), `make_lane`
(:538), `make_r1_judge` (:577), `prepared_snapshot` (:263) and
`write_coverage_json` (:711). O5/O7/O8 are therefore cheap end-to-end tests, not
new harnesses — and the one real change they need is
`write_coverage_json` learning to emit `meta.branch_coverage` and the two arc
arrays. Extend that ONE helper rather than hand-rolling coverage JSON inside
twenty tests; a per-test hand-rolled artifact is how a fixture drifts from what
the real tool emits, which is the defect class `tests/fixtures/coverage/`
exists to prevent.

**A7 — where the branch-capability guard runs.** `derive_exclusion_capability`
is called inside `evaluate_coverage` "BEFORE any per-file evaluation, so it
describes the artifact rather than this evaluation's own outcome"
(`evaluate.py:297-300`). `derive_branch_capability` goes in the same place for
the same reason. `require_branch`'s REFUSAL, however, is a measurability guard
and belongs beside `check_empty_coverage` in `evaluate_r1`'s guard sequence
(§4), so the two are not the same call site and must not be collapsed.

---

## 11. Addendum B — the independent pre-dispatch review, and what it changed

An adversarial review by `codex gpt-5.6-sol` at high effort, against the
worktree at `af918715`, returned **NOT READY — 9 blocking, 2 non-blocking**.
Every finding is accepted; the body above is already rewritten for all of them.
Recorded here so the reasoning is not lost and so nobody re-proposes a rejected
shape. The controller independently verified the load-bearing citations before
accepting — `git grep` really does return 56 with seven non-verdict members,
`verdict.py:2242` really does reject `judgment.r1` beside a payload-free R1
claim, and `verdict.py:2256` really is the R2 precedent A-264 copies.

| # | finding | disposition |
|---|---|---|
| 1 | three "schema invariants" are inexpressible in Draft 2020-12 | §6 now assigns one to the schema and three to the model + raw verifier, with a delete-each-and-count-reds proof |
| 2 | metadata could silently veto present branch detail → line-only PASS | A-265: detail is authoritative, disagreement refuses, with a four-row truth table |
| 3 | arc identity discarded before uniqueness was checked, so a duplicated covered arc inflates the numerator undetectably | new §3.1a validates identity BEFORE aggregation; O4 gains the coherent-tamper case |
| 4 | Cobertura multi-`<class>` branch merge undefined; percentage tolerance unwitnessed | identical `(C,T)` required or refuse; the percentage is now explicitly NOT read, with O4b pinning that so nobody adds a tolerance rule later |
| 5 | the two new payload-free refusals could not record the policy they refused under | A-264, modelled verbatim on A-183's R2 widening; O13 proves the closed set of two |
| 6 | the isolation waiver was legal and recorded on an R0-only lane, which never snapshots | refused at load per A-062; O15 |
| 7 | the v5 inventory was wrong and the sweep had the wrong predicate | §6 replaced by a fail-closed four-bucket classifier; P33's sweep is RUN, not replaced; the deselection list is derived by measurement |
| 8 | fixture lock said six while eight exist, and A2's oracle was vacuous | PROVENANCE and O1 normalised to eight; A2 withdrawn as written and the rule restated as defensive |
| 9 | lexical duplicate rejection let `src//a.py` and `src/a.py` both be judged | canonical-spelling refusal plus post-conversion uniqueness; O14 |
| 10 | "exactly what `--cov-fail-under` compares against" over-claimed | A-263 narrowed: same metric, different comparison; measured — 99.60% prints `99%` and exits 2 at `--fail-under=100`, so they agree at the consumer's floor |
| 11 | no whole-target/R3 interaction rule | `uncovered-line` refused on a whole-target lane unless the canary target is one of `targets`; O16 |

Two of the nine landed on the carver's own Addendum A rather than the body —
which is the argument for commissioning a review the carver cannot overrule,
stated again in evidence rather than in principle.

---

## 12. Addendum C — B006 was rewritten upstream mid-wave (2026-08-17)

`main` commit `c7bc9b59`, "docs(assay): specify safe monorepo snapshot scope",
rewrote backlog item B006 while work items 0–2 were being implemented against
the previous version. Merge `0791d9c4` brings it onto this branch. The
implementer was halted the same hour; whatever it had built for the withdrawn
allowlist is discarded rather than adapted, because the two designs are not
variations of each other.

What changed, and what it costs:

* **B006 is no longer "two papercuts", `context_estimate: small`.** It is
  `large`: an explicit, attested project-scoped snapshot mechanism, with its own
  acceptance oracles written into the backlog. §1 is rewritten to it; §7's work
  item 1 splits into 1a/1b/1c.
* **The allowlist is withdrawn, not deferred** (A-266). §1 keeps the argument so
  it is not re-proposed: an ignore list still materialises the path it ignores,
  so it cannot prove the command could not reach it.
* **A second consumer is blocked, today.** CMRU's higher-rigor lane cannot run
  at all — assay refuses Topos's tracked `/etc/passwd` symlink fixture before
  CMRU's command starts. CMRU has bounded itself to R0 in writing rather than
  route around it (`cmru/assay.toml`, `4b8009d5`), which is the correct
  behaviour and also means the block is real and visible.
* **It lands in the SAME v6 bump, and that is a saving, not a coincidence.** The
  backlog requires the scope to be "a schema/versioned attestation"; v6 is
  already open in this wave. Two separate bumps would mean paying the
  43-fixture migration and the locked-suite deselection dance twice.
* **The release step gains a condition.** The backlog asks that the feature be
  released as a versioned assay artifact and pinned in CMRU *before* CMRU drops
  its temporary whole-source coverage gate. So the cmru-release step at the end
  of this wave is not optional for this capability — it is how the consumer
  stops running a stopgap.
* **This section exists because the alternative is silent drift.** A spec whose
  requirement changed under it, and which is quietly edited to match, teaches a
  reader that it was always right. It was not: the design it shipped for B006(a)
  on 2026-08-16 is one the requirement's own author has since rejected in
  writing.

---

## 13. What must NOT change

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
