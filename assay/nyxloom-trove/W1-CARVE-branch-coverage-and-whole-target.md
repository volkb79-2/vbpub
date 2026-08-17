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

Operator decisions taken 2026-08-16 (A-257…A-263), two the first independent
pre-dispatch review forced (A-264, A-265 — see Addendum B), one taken upstream in
`main` on 2026-08-17 that superseded this wave's own first design (A-266 — see
Addendum C), and one the SECOND review round forced on top of that (A-267 — see
Addendum D). All eleven to be recorded in `decisions.md` by the implementer as
work item 0, in these words:

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
* **A-267 — the boundary is a MATERIALISATION boundary, not a sandbox, and the
  artifact says so.** Ruled 2026-08-17 after the second independent review found
  the first design promising what the substrate cannot deliver. Out-of-scope
  paths are never written into the working tree, so nothing can open, follow or
  execute them — the whole property, and exactly what unblocks CMRU. Explicitly
  NOT claimed: the retained object closure still yields out-of-scope committed
  bytes to `git show`, and assay does not confine the process. A
  mount-namespace/Landlock sandbox is rejected, not deferred (DESIGN-GUIDE §7,
  A-030). Ruled with it: `snapshot_scope` is required on every R1+ lane and
  refused on R0-only ones, with no default and no inference from the
  `assay.toml` location; the attestation gains a closed `materialisation` state;
  one immutable boundary object is built once and shared; and the impossible
  "removing an input refuses at preflight" oracle becomes the command failing on
  the absent file. See §1 and Addendum D.
* **A-268 — the guarantee is "assay never materialises an out-of-scope path",
  and the mechanism is named.** Ruled after the first review of this design
  measured that the COMMAND can restore an excluded path with stock git from the
  closure B006.3 requires assay to retain. Ruled with it: the mechanism IS a
  full index plus `skip-worktree` outside the boundary (measured — the
  alternative dies in `_verify`); `boundary_prefix` need only CONTAIN the
  project root; the runner preflight is defence-in-depth over a public API; the
  selection walk does not descend into out-of-scope subtrees; and every producer
  path derives its recorded boundary FROM THE LANE rather than from a constant.
  See §1, Addendum E and Addendum F.
* **A-269 — B006(a) ships unsafe-symlink OMISSIONS, not a project boundary, and
  the three summaries above are therefore partly withdrawn.** Read this before
  acting on A-266/A-267/A-268: `snapshot_scope`, `boundary_prefix`, `inputs`,
  the expanded-input attestation, the five containment preflights and the closed
  `materialisation` state are all gone. What ships is the full repository
  snapshot minus exact, commit-validated omissions of the symlink leaves P22
  would otherwise refuse, keyed `snapshot_selection`. **What SURVIVES from the
  rows above is the part that was measured rather than designed:** A-267's
  not-a-sandbox property and its enumerated non-guarantees, and A-268's full
  index plus `skip-worktree` mechanism and its "the command can restore an
  excluded path" narrowing. The design of record is
  `W1-CARVE-B006a-project-scope.md`; §1 of this document is dead.
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

## 1. B006 (a) — explicit, attested project-scoped snapshots (A-266) — **SUPERSEDED**

> **DEAD SECTION — do not implement any of it. Superseded in full by A-269 and
> by `W1-CARVE-B006a-project-scope.md`.** The project-prefix-plus-`inputs`
> boundary specified below failed three independent adversarial reviews at
> 8 → 9 → 11 blocking findings, diverging rather than converging. B006(a) now
> ships as a full-repository snapshot minus exact, commit-validated
> P22-unsafe symlink leaves. `snapshot_scope`, `boundary_prefix`, `inputs`,
> the expanded-input attestation and the five containment preflights are all
> WITHDRAWN.
>
> It is kept unrewritten because its MEASUREMENTS survived and are cited by the
> replacement: the full-index-plus-`skip-worktree` mechanism (§1.3), the
> not-a-sandbox property and its enumerated non-guarantees (§1.6 preamble), and
> the restore-is-possible narrowing. Read those as evidence, never as
> instructions.

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
boundary_prefix = "cmru"            # repo-top-relative, the owned tree
inputs = ["cmru.project.sample.toml"]   # a REAL tracked root file CMRU's suite reads
```

That input is not decorative and not invented: `cmru/tests/test_cli_dispatch.py:151`
resolves `parents[2] / "cmru.project.sample.toml"`, i.e. a repo-ROOT file outside
CMRU's own tree, and `git ls-files` confirms it is tracked. It is the concrete
reason `inputs` exists at all — a project boundary with no escape hatch for named
root artifacts would break CMRU's suite on day one.

Named `snapshot_scope`, not `scope`, because a lane already has a `scope` key
meaning its S-level, and `mode` is taken by `judge.mode`.

### What the boundary IS, and what it is NOT (operator ruling, 2026-08-17)

Say this before the mechanism, because the second review round's central finding
was that the first draft promised something assay cannot deliver, and a security
claim that overstates its mechanism is worse than no claim.

**It IS a materialisation boundary.** Out-of-scope paths are never written into
the working tree the command runs in. Nothing the command executes can open,
follow or execute a file that was never created — which is exactly and entirely
the property CMRU is blocked on: Topos's `passwd_link -> /etc/passwd` is never
materialised, so no symlink to `/etc/passwd` exists for anything to follow.

The property, stated exactly: **assay never materialises an out-of-scope path.**
Not "the path cannot exist" — the command can put one there itself, see below.

**It is NOT a confidentiality sandbox, and NOT a guarantee about what the
command does afterwards:**

* **the command can RESTORE any committed path, and this is measured, not
  theoretical.** The snapshot is an ordinary repository whose object store holds
  the complete closure (`isolation.py:486`) with a full-commit index
  (`isolation.py:500`), so `git checkout -- <path>` after clearing its skip bit,
  or `git worktree add`, recreates exactly the file the boundary excluded — and
  a suite that runs `git stash` or `git worktree add` in a fixture can hit this
  by accident, without meaning to. Retaining that closure is B006.3's own
  requirement, so this is a consequence of the requirement, not a defect in it.
  What assay guarantees is that IT did not put the path there;

* the snapshot's Git object store retains the COMPLETE closure, because B006.3
  requires it ("retain the complete resolved commit, object closure, base
  resolution, and provenance"). So `git show HEAD:topos/...` from inside the
  snapshot still reads committed bytes of an out-of-scope path. Note what that
  does and does not give: it reads the symlink's TARGET STRING, not `/etc/passwd`
  itself, because no symlink was created;
* assay does not sandbox the process. `execute_plan` is
  `subprocess.run(env=..., cwd=...)` (`runner.py:201`), which stops no absolute
  path, no `../..`, and no `/proc/$PPID/cwd`. It cannot: CMRU's own argv starts
  `/opt/tester-venv/bin/python`, which lives outside every snapshot;
* a mount-namespace/Landlock sandbox would deliver the stronger property and is
  **rejected here**, not deferred: DESIGN-GUIDE §7 puts container, image and
  provisioning knowledge outside assay permanently (A-030). That property
  belongs to the execution environment — ciu's lane — not to this library.

The attestation in §6 therefore describes a materialisation boundary in those
words, and no reader should be able to mistake it for a sandbox.

### The mechanism

1. **The choice is explicit, and required.** `snapshot_scope` is REQUIRED on any
   lane declaring R1, R2 or R3, and REFUSED on an R0-only lane (inert config,
   A-062, since R0 never snapshots). There is no default, no inference from where
   `assay.toml` happens to sit, and no fallback to the caller's checkout. The
   backlog's wording is literal — "a lane explicitly chooses" — and inference was
   considered and rejected: it would silently re-scope every existing R1+
   consumer, whose `assay.toml` files all sit in subdirectories, so a lane whose
   tests read a sibling path would begin failing for a reason nothing in its
   config mentions. That is the failure class this item exists to remove. The
   cost is one declared line per existing R1+ consumer, landing in a v6 bump
   those consumers must absorb anyway.
2. **Every scope path is canonicalised as a Git-tree path**, repo-top-relative
   (A-145: say which spelling, everywhere). Refused at LOAD
   (`ERROR`/`BAD_LANE_CONFIG`): absolute, empty, any `.`/`..` component, a
   backslash, a non-canonical spelling whose `PurePosixPath` round-trip differs
   from the raw string, a duplicate, `boundary_prefix` of `"."` or `""` (a
   repo-root project scope is repository scope — declare that instead), and more
   than 64 `inputs` entries or a declared path longer than 4096 bytes.
   **Overlap is refused in BOTH directions, and between inputs**: an input inside
   `boundary_prefix`, an input that is an ANCESTOR of `boundary_prefix` (prefix
   `apps/cmru`, input `apps` — which would silently swallow sibling projects), an
   input inside another input, and two inputs where either is an ancestor of the
   other. Ancestry is decided on canonical path components, never string prefixes
   (`src/foo` is not an ancestor of `src/foo_evil`).
3. **The mechanism is a full index with `skip-worktree` on everything outside
   the boundary — named, not left to the implementer.** Measured against the
   real substrate: narrowing the index instead makes `git status` report
   `D .gitignore` and a deletion for every omitted entry, which `_verify`
   (`isolation.py:590`) and the post-run dirt check (`runner.py:1120`) both
   refuse, and it makes `write-tree` (`isolation.py:510`) build mutant commits
   that delete the rest of the monorepo. With a full index plus skip bits,
   `git status` is empty, `ls-files --others --exclude-per-directory=.gitignore`
   is empty (git reads a skip-worktree'd `.gitignore` from the index, which
   matters here because the only ignore policy covering `cmru` is the repo-root
   one), and `write-tree` returns exactly `HEAD^{tree}`. Only this reading
   survives; say so, or work item 1b's first end-to-end run dies in `_verify`
   and the implementer will not know whether the design or their code is wrong.
4. **`boundary_prefix` must CONTAIN the project root — the prefix specifically,
   not the boundary as a whole.** The command runs at `snapshot.project_root`
   (`runner.py:1113`), so the project's own tree must be materialised or there is
   no working directory. Checked where git is actually available — at snapshot
   preparation, not at load: `config.py` imports no git (`config.py:56-72`) and
   cannot know the project's repo-relative path, so a load-time rule here would
   be unenforceable. The earlier draft required the prefix to EQUAL the project's
   own path; that is dropped, because it made every downstream containment check
   unreachable (see §1.7) while adding nothing.
   **Naming the prefix specifically matters**: "the BOUNDARY must contain the
   project root" is satisfiable by an *input*, so `boundary_prefix = "docs"` with
   `inputs = ["cmru"]` and the project at `cmru` would load and run, and the
   headline attested field would name a tree with no relation to the evidence.
   It also restores §1.2's ancestor-input rationale, which refuses an input that
   swallows sibling projects — a harm the loose reading let the prefix cause
   directly.
   The key is `boundary_prefix`, not `project_prefix`, because
   `SnapshotSpec.project_prefix` (`isolation.py:148`) and
   `refuse_lane(project_prefix=…)` (`runner.py:867`) already exist and mean the
   project's own repo-relative path — which §1.4 now deliberately allows to
   DIFFER from the boundary. Three meanings for one name, in a codebase whose
   A-145 rule is that every boundary says which spelling it speaks, is how the
   next incident starts. The backlog leaves TOML names to assay.
5. **Kinds and expansion are closed.** Checked against the resolved commit
   (`ERROR`/`BAD_LANE_CONFIG` at preflight, before any materialisation, following
   `_coverage_artifact_is_tracked`'s precedent rather than `GIT_FAILED` — the
   declaration is wrong, the repository is not): `boundary_prefix` must name a
   TREE; each `inputs` entry must name a tree or a regular/executable blob;
   a gitlink is refused (the substrate already refuses submodules); an input that
   is ITSELF a symlink is refused, because its target would need a boundary
   membership rule of its own and inventing one here is how scope creeps. A tree
   input expands recursively. `inputs = []` is legal and means "the prefix only".
   **The selection walk does not descend INTO an out-of-scope subtree** — it
   still reads the names and modes of sibling entries at every level it does
   traverse, because deciding whether a sibling is in scope requires decoding
   its name (`isolation.py:966`). So the structural refusals — gitlink
   (`isolation.py:955`), unsupported mode (`:960`), non-UTF-8 name (`:966`) —
   stop firing for anything INSIDE a skipped subtree, but a malformed entry
   sitting beside the boundary at a traversed level still refuses. State that
   limit rather than claiming the walk is blind: a root-level non-UTF-8 name
   will still fail every project-scoped lane, and a consumer should learn that
   here rather than from a red gate. Otherwise one
   submodule added anywhere in the monorepo would keep failing every
   project-scoped lane, which is the exact failure class B006 exists to remove,
   wearing a different hat. Which bounds narrow, stated because they point in
   two directions: `max_entries` and `max_total_path_bytes` are counted over the
   SELECTED set (they are counted during traversal today, `isolation.py:950`,
   `:990`, and traversal is now scoped); the closure bounds — `max_objects`,
   `max_blob_bytes`, `max_total_object_bytes`, `max_pack_bytes` — deliberately do
   NOT narrow, because B006.3 requires the complete closure to be retained. A
   monorepo whose repository-wide closure trips a ceiling therefore still fails
   every lane; vbpub is at ~29k objects against `max_objects = 100_000`, so this
   is headroom rather than a live break, and it is recorded rather than
   discovered later.
6. **An in-scope symlink keeps P22's containment check and fails closed** — with
   containment now meaning *the materialised boundary*, not the repository. A
   relative symlink inside `cmru/` pointing at `../topos/x` escapes the boundary
   and is refused exactly as an absolute target is; P22 alone would have allowed
   it. The selection walk MAY read out-of-scope tree names and modes — it has to,
   to find the prefix — but an out-of-scope symlink's target is neither validated
   nor materialised, and must never affect acceptance nor appear in the
   child-visible tree. ("Never examined" was too strong and made the substrate
   change harder than the requirement needs.)
7. **The preflight is DEFENCE-IN-DEPTH over a public API, and says so.** Once
   §1.4 puts the project root inside the boundary, every other judged item is
   already contained by the LOADER: a source root outside the project root is
   refused at `config.py:1280`, the coverage artifact at `config.py:998`, the
   canary target must live beneath a declared source root, mutation candidates
   are gated on source roots (`runner.py:1432`), and the cwd IS the project root.
   So no file-loaded `assay.toml` can produce an out-of-boundary item, and the
   preflight's refusal branches are unreachable from config — which matters,
   because this project forbids `pragma: no cover` and an unreachable branch
   cannot be honestly covered. Keep the checks: `Lane` is a public frozen
   dataclass constructed directly all over the test suite, so the branches ARE
   reachable through the API and their tests construct `Lane` objects directly.
   Label them defence-in-depth in the code comment; do NOT present them as the
   thing that keeps the boundary honest, because §1.4 is. Outside ⇒
   `ERROR`/`BAD_LANE_CONFIG` naming the item, its spelling and the boundary. The
   spellings still have to be stated at each conversion (A-145): source roots,
   coverage artifact, canary target and B005's `targets` are PROJECT-relative;
   mutation replacement paths are repo-top-relative (`isolation.py:26`); the
   boundary is repo-top-relative; conversion joins the project's own
   repo-relative prefix and compares with `is_relative_to` on resolved paths,
   never string prefixes. **The boundary is never broadened automatically.**
8. **Base resolution and diffs are unchanged, and that is a deliberate
   consequence of §the-boundary-is-not-a-sandbox.** The base is resolved
   pre-snapshot against the consumer repository (`runner.py:1245`) exactly as
   today, and an in-snapshot `git diff base..HEAD` still reports the FULL
   repository delta because the object closure is complete. Out-of-scope changed
   files are filtered out downstream, where they already are: `evaluate_coverage`
   only iterates `added.by_file` entries under a source root, and
   `resolve_mutation_targets` does the same. A test must prove a changed file
   outside the boundary never enters `considered` and never becomes a mutation
   target.
9. **One resolved boundary object, constructed once.** An immutable
   `ResolvedSnapshotBoundary` — effective scope, canonical prefix, canonical
   sorted inputs — is built ONCE after commit-relative validation and passed
   unchanged to the manifest builder, the preflight, every snapshot unit and the
   verdict producer. Today `SnapshotSpec` carries no scope (`isolation.py:142`)
   and `assemble_verdict` takes no isolation argument (`runner.py:688`), so
   without this the implementer would be free to serialise a separately
   canonicalised copy and the artifact could disagree with what `_build_manifest`
   actually selected. A test must prove they are the same object, not two objects
   that happen to agree.
10. **Nothing leaks into the consumer's checkout.** The private index and worktree
   live outside it; no flag, generated parent, hook, index entry or replacement
   ref may appear in it. O19 carries the exact criterion. What assay can enforce
   here it must; what it cannot — a command that daemonises a child outliving the
   post-run checks — is named as a limitation in the same breath rather than
   quietly assumed away.

### Recorded in the verdict

A new top-level `isolation` object, REQUIRED in v6 (see §6): the effective
`snapshot_scope`, a closed `materialisation` state, and — for project scope —
`boundary_prefix` and the canonical `inputs` as validated (the declared roots
after canonicalisation, NOT their recursive expansion, which is unbounded and
already implied by prefix + roots + commit).

`materialisation` exists because the earlier draft's object could describe a
boundary that never ran: an R0 lane never snapshots, and a preflight refusal
happens before materialisation, yet both would have recorded a scope. Its closed
values are `"none"` (no snapshot was materialised — direct R0 execution, or a
refusal before materialisation) and `"complete"`. Without it, "repository" on an
R0 artifact is a false attestation.

It deliberately does **not** repeat the resolved commit: the document already
carries a required top-level `commit`, and a second copy is a second thing to
disagree with itself.

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
  to climb above the boundary. A symlinked or escaping parent remains a loud
  refusal.
  **AMENDED by A-269.** The original sentence here — "under
  `snapshot_scope = "project"` the artifact path must already have passed §1's
  preflight" — named a scope and a preflight that no longer exist. The guard
  that actually protects this seam is **B006(a) WI-3's coverage-artifact
  collision check**: a declared omission that is equal to, or an ancestor of,
  the artifact path refuses with `ERROR`/`BAD_LANE_CONFIG` **before any
  materialisation**, so parent creation can never silently replace an omitted
  committed symlink with a generated directory. B006(b) does not re-check it.

Independently of the creation: when a parent chain still cannot be opened, the
diagnostic must name the missing component and the declared artifact path, and
must distinguish **setup failure** ("the parent could not be created or opened")
from **a genuinely unreadable artifact** ("the command produced nothing usable").
Collapsing those two into one generic message is what cost the consumer a
debugging round.

**The distinction is in the DIAGNOSTIC, not in a new reason code** — ruled here
rather than left to the implementer, because `safeio`'s every refusal is
hardwired to `ERROR`/`UNREADABLE_ARTIFACT` (`safeio.py:42`) and the reason
vocabulary is closed (`errors.py`). A fourth new code in one bump, bought purely
for message fidelity, is not worth another closed-enum widening and another
migration; a stable, prefixed diagnostic naming the failing component gives the
consumer the same answer.

Two alternatives were considered and are recorded so the next reviewer can
challenge the trade rather than rediscover it: a new reason code, and the
EXISTING `OUTPUT_WRITE_FAILED`, whose docstring already reads "assay could not
write its OWN declared output artifact. Distinct from `UNREADABLE_ARTIFACT`,
which is about reading an INPUT" (`errors.py:85`). The second is the stronger
counter-proposal and is declined because that code belongs to the VERDICT
destination (A-O14/P21); reusing it here would make two different artifacts
share one terminal, and a consumer could no longer tell which one failed.

**The verdict does NOT record the artifact path on this path, and the earlier
draft's claim that it does was false.** Both reservation-failure shapes reach a
document with no `judgment.r1` at all: `reserve_output` raising inside
`_execute_snapshot_unit` propagates to `refuse_all` with no judgment
(`runner.py:1092`, `:1786`), and a consume/parse failure yields an R1 claim with
no coverage payload, which is exactly when `judgment.r1` is not built
(`runner.py:1368`). The model forbids it there anyway (`verdict.py:2242`), and
A-264 has just fixed that widening to a CLOSED set of two codes which does not
include `UNREADABLE_ARTIFACT`. So the artifact path lives in the diagnostic, and
nowhere else, on this path — do not add a third member to A-264 to make this
sentence true.

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
* **A target names a REGULAR FILE. Never a directory.** ~~A target may name a
  FILE or a DIRECTORY, expanding to every adapter-recognised source file
  beneath it, with the anti-vacuity rule applied to the EXPANSION rather than
  the declaration.~~ **That rule is WITHDRAWN — it was the worst finding of the
  third review round, and it contradicted this section's own Judging step 2,
  which has always said "a regular file — not a directory, not a symlink".**

  Why it is withdrawn, because the usability argument for it was real and will
  be made again: relaxing the guard from per-file to per-declaration means a
  directory expanding to 36 files **of which ONE appears in the coverage
  artifact passes**, and the other 35 go silently unjudged. That is `--cov`'s
  vacuity with a first-class judge wrapped around it — precisely the hole B005
  exists to close. A convenience that dissolves the guarantee the feature is
  named for is not a convenience.

  The consumer cost is real and accepted: CMRU owning 25 modules must name 25
  paths, and a file somebody adds is not judged until it is declared. That is
  the honest failure direction — an undeclared file is visibly absent from
  `targets`, whereas under expansion an unmeasured file was invisibly present.
  A future capability may close the gap (a declared directory whose expansion is
  pinned by count or digest, so growth fails closed), but it needs its own
  ruling and its own anti-vacuity proof.

  **This is what shipped**: `evaluate._resolve_whole_target` refuses a
  non-regular-file target with `ERROR`/`BAD_LANE_CONFIG` naming it, and
  `evaluate_targets` applies `TARGET_NOT_MEASURED` **per declared target**.
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

### `isolation` — **SUPERSEDED**

> **DEAD. Do not add this object to v6.** A-269 replaces it with
> `snapshot_policy`, specified in `W1-CARVE-B006a-project-scope.md` §5. The
> `materialisation` enum in particular is withdrawn as underivable: `_verify`
> failures and `prepare_snapshot` refusals reach the same `except` with
> byte-identical state, so no call site can distinguish `partial` from the
> others. An implementer who builds both objects ships a required field with no
> honest producer.

New **required** top-level object recording the materialisation boundary that
actually ran (§1, A-266):

| field | rule |
|---|---|
| `snapshot_scope` | required, enum `repository` \| `project` — the EFFECTIVE value |
| `materialisation` | required, enum `none` \| `partial` \| `complete` |
| `boundary_prefix` | required iff `snapshot_scope = "project"`, forbidden otherwise; repo-top-relative, canonical, non-empty |
| `inputs` | required iff `snapshot_scope = "project"` (possibly empty), forbidden otherwise; canonical repo-top-relative paths, unique, sorted |

Required rather than optional because "absent" would be indistinguishable
between an old producer and a repository-scoped run, and the whole point is that
a reviewer can tell project-scoped evidence from full-repository evidence
without re-running anything. It does NOT carry the commit: the document already
has a required top-level `commit`, and two copies of one fact is one fact too
many.

**`materialisation` is what keeps the object from lying.** Three values, because
three states are genuinely reachable and the previous two-value draft had no
legal answer for the middle one:

* `none` — nothing was written: a direct R0 execution, or any refusal before
  `prepared.materialize(...)` is entered;
* `partial` — the private tree was written and then the run failed inside it.
  `_run_prepared_lane` enters `with prepared.materialize(...)` with no `try`
  around it (`runner.py:1297`), so a `_verify` failure (`isolation.py:592`), a
  `_write_worktree` OSError, a stale replacement site (`isolation.py:452`), or
  §2's own `reserve_output` refusal (`runner.py:1092`) all propagate to
  `refuse_all` (`runner.py:1786`) with a tree already on disk and torn down.
  `none` is false there by its own definition and `complete` is undefined;
* `complete` — the boundary was materialised and the lane ran in it.

It does NOT separate "materialised but the command never started" from
"materialised and the command ran" — the claims already answer that, since an R0
claim exists iff the command ran. §2 and §6 must agree on which value a
`reserve_output` failure records: it is `partial`. An R0-only lane records
`{snapshot_scope: "repository", materialisation: "none"}` — no boundary was
applied and none was materialised, both true — and declaring an `[isolation]`
table on an R0-only lane is refused at load as inert configuration (A-062).
Recording `"repository"` alone there, as this spec's previous draft did, is a
false attestation: it names a policy for machinery that never ran.

The object's own schema `description` must say, in the artifact, that this is a
MATERIALISATION boundary and not a sandbox — §1's ruling. An attestation whose
meaning lives only in a design document is one a consumer will over-read.

The conditional pairs (`boundary_prefix`/`inputs` present iff project scope) ARE
expressible in Draft 2020-12 via `if`/`then`/`else`, unlike §6's numeric
comparisons — so they live in the schema as well as the model and the raw
verifier.

**The refusal paths derive the DECLARED boundary from the lane. There is no
constant, and the previous draft's constant was false.** `cli.py:385` is the
adapter refusal, and `_resolve_declared_adapters` skips R0 (`cli.py:230`) — so
that path is reachable ONLY for R1+ lanes, which are exactly the lanes §1.1
requires to declare `snapshot_scope`. A constant recording `"repository"` there
would report `repository` for a lane that declared `project`, which §6's own
next paragraph calls a false attestation.

The justification for a constant ("repository identity does not exist yet") was
a non-sequitur: `snapshot_scope`, `boundary_prefix` and `inputs` are LANE
CONFIG, fully parsed before any git work, and `refuse_lane` already receives the
lane (`runner.py:859`). `assemble_verdict` already derives `scope` and
`enforcement` from the lane on every call for exactly this reason. Only
VALIDATION against the commit needs the repository; the DECLARATION does not.

So every producer path records the lane's declared boundary, with
`materialisation` carrying what actually happened. That covers the five paths
that emit a complete verdict before §1.8's validated boundary exists —
`cli.py:355`, `cli.py:385`, the `env_required` refusal (`runner.py:1936`),
`refuse_all(DIRTY_TREE)`/`(HEAD_CHANGED)` (`runner.py:1745`/`:1747`), and the
generic `except AssayError` (`runner.py:1786`) — none of which had a rule
before. An R0-only lane, which may not declare the table at all, records
`{snapshot_scope: "repository", materialisation: "none"}`; that value is
DERIVED from "this lane never snapshots", not defaulted into its config.

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
  1. **transform** — verdict documents that become v6 (`tests/fixtures/verdicts/**`).
     **AMENDED by A-269 — the rule below REPLACES the withdrawn one.** The
     synthesised object is `snapshot_policy`, not `isolation`, and the rule is
     keyed on DECLARED RIGOR rather than on the outcome:
     * `declared_rigor` contains R1, R2 or R3 ⇒ insert
       `"snapshot_policy": {"selection": "repository"}`;
     * R0-only ⇒ insert **no object at all**;
     * a document that ALREADY carries a `snapshot_policy` key ⇒ **refuse**,
       rather than overwrite or merge.

     The transform must LOG the assignment per file, so a wrong one is visible
     in review rather than buried in 43 diffs.

     Why this is simpler than what it replaces, stated because the difference is
     the whole point of A-269: the withdrawn rule had to derive a
     `materialisation` phase from each document's own outcome/reason pair —
     `"complete"` normally, `"none"` for refusals before any snapshot. That
     derivation is exactly the value the third review round proved **no producer
     can distinguish at runtime**, so a migration that synthesised it would have
     been inventing evidence the live code could never emit. Rigor is on the
     document already, and the live producer copies the policy straight off the
     lane, so migration and runtime now agree by construction;
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

0. **Decisions — VERIFY, do not re-add.** A-257…A-268 are ALREADY in
   `decisions.md` (commits `6bd75c0c`, `d57cb2f1`). Re-adding them "verbatim
   from §0" would duplicate rows AND overwrite the fuller A-266/A-267 entries
   with §0's thinner summaries. Check they are present and correct; add only
   A-269+ if this wave rules anything further. `decisions.md` outranks §0 where
   they differ.
1. **B006 (a) — the project-scoped snapshot** — §1. **SUPERSEDED — items 1a,
   1b and 1c are all DEAD.** A-269 replaces them with WI-0…WI-6 of
   `W1-CARVE-B006a-project-scope.md`. Do not build the `[isolation]` table,
   `ResolvedSnapshotBoundary`, the prefix+inputs materialisation, or the
   five-item runner preflight. The replacement's own WI-1 keeps exactly one
   thing from the text below — the **`LANE_SCHEMA_VERSION` bump to 2**, noted
   at the end of this item — and even that lands under the replacement's
   migration rules, which forbid touching `cmru/assay.toml`. The dead text
   follows unrewritten because its loader-edit inventory (`_OPTIONAL_LANE_FIELDS`,
   `Lane.as_declared()`) is still an accurate map of what a lane-grammar change
   must touch:
   1a. config — the `[isolation]` table, its closed grammar, the required-on-R1+
       and refused-on-R0-only rules, both-direction overlap refusal, and the
       bounds; plus the immutable `ResolvedSnapshotBoundary` type (§1.8) which
       everything downstream consumes;
   1b. isolation — canonicalisation and kind/expansion validation against the
       resolved commit, the private index/worktree materialisation of prefix +
       inputs, boundary-relative symlink containment, and the no-leakage proof;
   1c. runner — the preflight over every source root, artifact, mutation
       candidate, canary target and cwd, with the project↔repo-top spelling
       conversion stated at each; plus threading the ONE boundary object into
       `assemble_verdict`.
   1a/1b/1c land as three commits. 1b is where the property lives; if any of its
   oracles cannot be written honestly, STOP and report rather than weakening the
   oracle. **Do not promise more than §1's ruling allows** — the boundary is a
   materialisation boundary; no test may be phrased as proving confinement the
   mechanism does not deliver.
   **Item 1's acceptance bar is O9, O17 and O19 only.** O15 and O18 assert the
   `isolation` OBJECT, which does not exist in the schema or the model until
   item 4 — so they belong to item 4 and must not be demanded here. Three
   further loader edits belong to 1a and are easy to miss:
   `_OPTIONAL_LANE_FIELDS` (`config.py:133`) must gain `"isolation"` or every
   lane declaring the table is refused as an unknown key; `Lane.as_declared()`
   (`config.py:395`) must round-trip it, or its "compares equal to tomllib's own
   parse" contract breaks; and **`LANE_SCHEMA_VERSION` (`config.py:114`) bumps
   to 2**, because the lane grammar changes incompatibly and a consumer on the
   old grammar otherwise gets a bare unknown-key or missing-table failure with
   no version signal. That is the LANE schema, distinct from the verdict schema
   v6 — §1.1's "consumers absorb it anyway" argument conflated the two.
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
   **AMENDED by A-269.** The `isolation` object this item was to add is
   WITHDRAWN, and with it O15 and O18. In its place this item lands
   `snapshot_policy` — `W1-CARVE-B006a-project-scope.md` §5 — **in this same
   commit**, because that carve's WI-4 is defined as the same single v5→v6 hard
   cut, not a second one. The v5→v6 migration therefore gains one rule it did
   not have: insert `{"selection": "repository"}` into every R1+ v5 document,
   emit no object for R0-only ones, and refuse a document that already carries
   the key. Everything else in this item — branch payloads, B005, the
   `changed_executable`→`executable` rename — is untouched by A-269.
5. **Documentation and spine** — `DESIGN-GUIDE.md` (§5 registry, §6 measurement
   causes, §11 the model), `STATE.md`, `4-backlog.md` (B005/B006 status lines
   AND their prose sections — a status line that contradicts its own section is
   how B002/B003 became confusing), the nyxloom spine
   (`2-product-definition.md` criteria for the new capability, with real pytest
   node ids — note `evidence_resolves` is AST-based and CANNOT resolve a
   parametrised node id, so cite the bare function id), and a LOG under
   `nyxloom-trove/reports/`.

   **WIDENED 2026-08-17 (operator).** The list above covered the trove and the
   design guide but named neither user-facing document, and both are wrong the
   moment this wave lands. Add them, with the division of labour stated once:
   **README = what assay does; DESIGN-GUIDE = why it does it that way;
   CONSUMERS.md = how to adopt it.** Every feature named in the README links to
   the DESIGN-GUIDE section carrying its rationale rather than re-arguing it.

   * **`README.md` — the feature surface, which this wave makes FALSE.** Its
     headline bullet currently reads "**Changed-line coverage, not
     whole-project coverage**". B005 ships exactly the whole-target mode that
     bullet denies, so leaving it is not a stale doc but a contradiction of the
     product. The README must name, in its own voice and each linked to its
     DESIGN-GUIDE section: (a) the two R1 **modes** — changed-line and
     whole-target — and when a consumer wants each, replacing the "not
     whole-project" framing with "you choose which question is asked";
     (b) **branch coverage judged whenever the artifact reports it** (A-258),
     including that this changes what PASS means for an existing R1 lane, and
     `require_branch` as the guard against a silent rigor downgrade;
     (c) **snapshot selection** — one line for repository mode, one for
     `repository-minus-unsafe-symlinks`, and the one-sentence property from the
     B006(a) carve §2 verbatim, never a stronger paraphrase;
     (d) **verdict schema v6 and lane schema v2** as the compatibility facts a
     reader needs before adopting. Its `assay.toml` example migrates to v2 with
     an explicit `[isolation]` table.
   * **`docs/CONSUMERS.md` — named in NO work item before this amendment, and
     the file a consumer actually follows.** Its "Adopt R1 only after…"
     paragraph predates the mandatory `[isolation]` table, so a consumer
     following it today writes a lane that refuses to load with
     `BAD_LANE_CONFIG` and no hint why. It must gain: the required
     `[isolation]` declaration on every R1+ lane and how to choose the
     selection; a **worked whole-target example** for the B005 use case that
     motivated it (a module-level floor that survives a docstring-only change,
     with the argv, the judge table, and what the verdict then attests — this
     is the feature dstdns is waiting on, so it gets a real example, not a
     mention); a **worked monorepo example** for B006(a) showing a lane
     declaring `unsafe_symlink_omissions`, what the refusal looks like when a
     link is undeclared, and the maintenance obligation that a new unsafe
     symlink reds the lane until its owner declares it; a note that the
     coverage artifact's parent is now created inside the snapshot (B006(b)),
     so the tracked-`.gitkeep` work-around is no longer needed; and the
     **ordered consumer adoption step** — repin a v2-capable release and bump
     the lane file's `schema_version` in the SAME commit, because a v2 assay
     refuses a v1 lane file and a v1 pinned assay refuses a v2 one.
   * **Checked, not just written — three tests, ruled as A-270 and recorded in
     `DESIGN-GUIDE.md` §16 and the estate-wide `AGENTS.md`.** Each must be able
     to go red; a check that cannot fail is this project's most expensive
     recurring defect (A-124, A-131):
     1. **every TOML example** in `README.md`, `docs/CONSUMERS.md` and
        `docs/DESIGN-GUIDE.md` parses with the **shipped loader** and declares
        the current `LANE_SCHEMA_VERSION`. Extract them from the fenced blocks;
        do not maintain a duplicated copy, which would drift from the rendered
        document and defeat the point;
     2. **every value of every closed public vocabulary a consumer must type**
        — `isolation.snapshot_selection`, `judge.mode`, the rigor levels, and
        the coverage `format` registry — **appears in at least one of the
        three documents.** Derive each vocabulary from the shipped module, never
        from a hand-written list in the test, or the test stops noticing new
        values on the day it matters. This is the check that makes "a capability
        shipped undocumented" mechanically impossible;
     3. **every DESIGN-GUIDE anchor the README links to resolves.** §16 makes
        README→DESIGN-GUIDE linking the rule instead of re-argument, so a dead
        link silently restores the duplication it exists to prevent.
     The test asserting (2) must itself be non-vacuous: assert the derived
     vocabularies are non-empty, or an import that silently yields nothing
     would make the whole check pass forever.

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

> **O9, O15, O17, O18, O19 and O9b are SUPERSEDED by A-269**, and replaced by
> O1–O7 of `W1-CARVE-B006a-project-scope.md`. They assert a boundary, a
> `ResolvedSnapshotBoundary` object and an `isolation` verdict field that no
> longer exist. O19's criterion in particular was measured to be unfailable as
> written (`git ls-files -v` prints uppercase `S`, not lowercase), which is why
> the replacement states the exact uppercase parse. Do not implement any of the
> six; they are kept as the record of what was asked for and why it changed.

**O9 — the project boundary is real, not a filter.** These are the backlog's own
acceptance tests, and they are the acceptance bar for work item 1:

* a fixture repository carrying an absolute-target symlink OUTSIDE the project
  scope: project-scoped R1, R2 and R3 all run normally, and the symlink is
  **never materialised** — assert its absence from the materialised tree with a
  real filesystem check (`lstat`), not inferred from the run having proceeded,
  and assert that the target it would have pointed at is not reachable through
  any path in that tree. Do NOT assert it is "unreadable": §1's ruling says the
  retained object closure can still yield its bytes via `git show`, and a test
  claiming otherwise would be false. The SAME repository under
  `snapshot_scope = "repository"` still fails with P22's existing diagnostic, in
  the same test, so the two scopes are distinguished by evidence;
* **an ordinary out-of-scope regular file and an out-of-scope DIRECTORY are also
  absent** — without these, a minimal implementation that materialises the whole
  tree and merely stops validating out-of-scope symlinks passes every other
  bullet, and it is the cheapest way to "unblock CMRU" while building nothing;
* the assertions run against the tree **while it exists**: the materialised tree
  is destroyed when its context exits (`isolation.py:434` → `_remove_owned_tree`),
  so a test that lstats after `run_lane` returns is checking nothing. Use a
  `process_runner` double that inspects its own `cwd`, or the oracle silently
  degrades into a `prepare_snapshot` unit test that proves the substrate and not
  the wiring;
* `git status` inside the snapshot is clean AND `git ls-files -v` shows `S`
  (skip-worktree) only outside the boundary;
* the in-snapshot `git diff base..HEAD` is BYTE-IDENTICAL to the same lane under
  repository scope — but that comparison needs a SECOND, symlink-free fixture,
  because the escaping-symlink fixture above never materialises under repository
  scope and so has no repository-scoped diff to compare against. Name it. (The
  underlying claim is sound: both diff call sites, `runner.py:589` and `:1405`,
  run `git diff --unified=0 <base_rev> <head_rev>`, commit-to-commit and
  worktree-independent.)
* an absolute-target or escaping-relative symlink INSIDE the owned prefix or a
  declared input fails before the command runs — including a relative symlink
  that escapes the boundary while staying inside the repository, which is the
  case P22 alone would have allowed;
* malformed, missing, untracked, absolute and `..` declarations each refuse,
  each with their own diagnostic;
* a named root test dependency is present and the command reads it; REMOVING it
  from `inputs` makes **the command itself fail because the file is absent**.
  The backlog's wording ("deterministic preflight failure") is impossible as
  literally stated and is resolved by operator ruling: once an entry is removed
  from `inputs`, nothing declares the command needs it, and ambient discovery is
  forbidden — so assay cannot refuse in preflight without a second
  `required_inputs` declaration that would let the same fact be declared twice
  in two places that can disagree. The honest negative is stronger anyway: it
  proves the boundary is real by having the command discover the file's absence,
  which is precisely what "no fallback to the caller's checkout" means. Assert
  the COMMAND's observation, not assay's;
* private `HEAD`, `git status`, `git diff`, base diff and replacement semantics
  are exact inside the snapshot;
* mutation and canary targets outside the boundary are refused; in-scope targets
  are modified only inside the private snapshot, and a deliberately failing run
  leaves **no** temporary index, worktree or `skip-worktree` flag in the source
  repository — see **O19** for the exact criterion, which is `git status` plus
  the absence of `S` entries in `git ls-files -v`, and explicitly NOT index
  byte-identity. (The previous wording demanded byte-identity here while O19
  called byte-identity wrong; one contract cannot say both about the same
  assertion.)

**[SUPERSEDED by A-269 — replaced by O6/O7 of the B006(a) carve, which also
avoids the R2 gate trap this oracle would have walked into: a permanent CMRU
R1/R2/R3 lane renders `INCONCLUSIVE`/`NO_MUTANTS` (exit 5) on any commit that
does not touch `cmru/src`, and `cmru/cmru.toml` runs `assay run cmru` as a gate
step.]**
**O9b — the end-to-end consumer proof, DEFERRED to the release pin by operator
ruling.** The backlog's final acceptance test is a CMRU lane in `tester-unified`
making genuine R1/R2/R3 claims while the Topos `/etc/passwd` fixture stays
tracked, killing every non-equivalent mutant and failing its canary for the
required coverage reason. **It cannot be written in this wave**, and the reason
is not effort: `cmru/assay.toml` declares R0 only, with no source roots, no
coverage argv or artifact, no base, no mutation policy and no canary target. An
implementer writing O9b would have to invent CMRU's entire rigor policy on its
owner's behalf, which the carve rules say to hand back rather than land.

**SUPERSEDED 2026-08-17 — O9b is no longer deferred.** The operator has granted
authority to edit CMRU's lanes and rigor, which removes exactly the blocker
above. This wave WRITES CMRU's R1/R2/R3 lane and makes the backlog's own final
acceptance test real.

CMRU is a strong consumer for it — 100% lines (6060/6060) and branches
(2184/2184) under its own gate. **But two things must be ruled here or the lane
is written wrong, and both were caught before dispatch:**

**(a) The assay lane's argv must NOT carry `--cov-fail-under`.** CMRU's reverted
lane had `--cov-fail-under=100` in it. R3's transform half appends an uncovered
line to the canary target inside the snapshot; with that flag, **pytest itself
exits non-zero**, so `canary.py:350` short-circuits on the failing R0 status and
the observed cause is `COMMAND_FAILED` where `uncovered-line` expects
`UNCOVERED_LINES` (`canary.py:133`) — yielding `FAIL`/`CANARY_SURVIVED`
deterministically. That is not a finding about assay; it is a lane whose argv
pre-empts the judge. The whole-source floor already lives in `cmru/cmru.toml` as
a separate gate step and stays there. So: `--cov-branch` and
`--cov-report=json:...` yes, `--cov-fail-under` **no** — the floor is
`judge.fail_under`, which is the entire point of the tier.

**(b) The lane must state its R1 mode, its base, and a non-vacuity assertion.**
`fail_under = 100.0` and `require_branch = true` alone let O9b pass on nothing:
under `changed_lines`, a release commit touching no `src/cmru` file gives zero
considered lines, `pct = 100.0` by the zero-denominator rule, and R2 renders
`INCONCLUSIVE`/`NO_MUTANTS` — the letter of the oracle, none of the backlog.
Use **`mode = "whole_target"`** with an explicit `targets` list (that is what
B005 is FOR, and it is what makes the lane meaningful post-merge), keep the
canary target inside `targets` per §5's rule, and declare `base` for R2, which
requires it independently. Assert explicitly that R1's `considered > 0` and R2's
candidate count `> 0` before believing any of it — O7's discipline, applied to
the wave's headline oracle.

Note also that CMRU's "29/29 mutants killed" figure does NOT transfer: that is
its own whole-source gate, while assay's R2 candidate set comes from the
`base..HEAD` diff (`runner.py:1405`). Citing it as evidence the lane will pass
is a category error, so do not cite it.

**Enumerate CMRU's root dependencies before writing `inputs`; do not guess.**
`cmru.project.sample.toml` is read at `cmru/tests/test_cli_dispatch.py:151`, and
`cmru.release.sh` is referenced AND EXECUTED at
`cmru/tests/test_release_wrapper.py:9-20` — a second root file the earlier draft
missed entirely. Note also that `cmru/tests/test_cli_dispatch.py:491` asserts a
repo-root file does NOT exist; project scope satisfies that vacuously, and it
would be satisfied by an empty boundary too, so it is not evidence of anything.
Sweep the suite for `parents[2]` and equivalents rather than trusting this list.

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

**[SUPERSEDED by A-269 — see the note at O9.]**
**O15 — the isolation table is required where it acts and refused where it is
inert.** A lane declaring R1/R2/R3 with NO `[isolation]` table is refused at
load, naming both legal values; an R0-only lane WITH one is refused at load
(A-062); an R0-only lane without one produces
`{snapshot_scope: "repository", materialisation: "none"}`, and a project-scoped
R1 lane that reaches execution produces `materialisation: "complete"` with its
prefix and canonical inputs. A preflight refusal under project scope produces
`materialisation: "none"` — prove that one specifically, because it is the case
the previous draft got wrong.

**[SUPERSEDED by A-269 — see the note at O9.]**
**O17 — overlap and kinds are refused in every direction.** Separate cases, each
refused at load or preflight as §1 assigns: an input inside the prefix; an input
that is an ANCESTOR of the prefix (`apps/cmru` + `apps`); an input inside another
input; two inputs where either is an ancestor of the other; a prefix of `"."`;
a prefix that does NOT contain the project root (§1.4 — and note the case that
must LOAD: a prefix that contains the project root without equalling it, since
the equality rule was dropped); an input naming a
gitlink; an input that is itself a symlink; an input or prefix missing from the
resolved commit; and `src/foo` alongside `src/foo_evil`, which must LOAD — the
ancestry check is on path components, and a string-prefix implementation fails
exactly here.

**[SUPERSEDED by A-269 — see the note at O9.]**
**O18 — one boundary object, not two that agree.** (Work item 4, not item 1 —
the `isolation` object does not exist until then.) On a lane that MATERIALISES,
the boundary the materialiser consumed and the boundary the verdict serialises
must be THE SAME OBJECT: assert with `is`, plus a construction probe that counts
exactly one validated construction per `run_lane`. On the refusal paths that
never validate against a commit, the artifact's boundary is DERIVED FROM THE
LANE (§6) and the probe expects ZERO validated constructions — state both
expectations, because a single global count fails on the paths that legitimately
have none. The earlier wording said "mutate it and show the artifact changes",
which is impossible against the frozen dataclass §1.9 requires — an implementer
would have reached for `object.__setattr__` or a mutable double that proves
nothing.

**[SUPERSEDED by A-269 — see the note at O9.]**
**O19 — nothing reaches the consumer's checkout, including on failure.** After a
project-scoped run that MATERIALISED AND RAN and then went red (not a preflight
refusal, which materialises nothing and would make this vacuous), the source
repository carries no `skip-worktree` bit and `git status` is clean.

**The criterion is `S`, not lower case, and getting this backwards makes the
oracle unfailable** — measured: `git ls-files -v` prints `S` for skip-worktree
alone and a lower-case letter only when assume-unchanged is ALSO set. The
previous wording ("no lower-case entry") passes with a leaked skip bit sitting
right there, which is precisely the cannot-fail negative this project pays for
repeatedly (A-124/A-131). Assert no `S` entries.

Do NOT assert index byte-identity: `git status` legitimately refreshes and
rewrites `.git/index` for unrelated reasons. And say plainly what this oracle
is worth — under this design assay never touches the consumer's index at all
(the snapshot is a separate repository, `isolation.py:478`), so O19 is a
regression guard against a future change, not the proof that leakage is
impossible.

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

## 13. Addendum D — review round 2, and the claim this wave stopped making

The rewritten §1 went back for an independent adversarial review and returned
**NOT READY — 13 blocking**. The findings were not about details; the central one
is that the design promised a security property assay's architecture does not
have, and four of the thirteen were different views of that same overreach.

The load-bearing facts, verified rather than accepted on assertion:

* each consumer snapshot receives the COMPLETE seed pack and a `read-tree` of the
  whole commit (`isolation.py:478`, `:500`, `:674`), so `git show HEAD:<path>`
  reads out-of-scope committed bytes from inside the snapshot, and
  `skip-worktree` is a status-reporting bit, not access control;
* the consumer command is `subprocess.run(env=..., cwd=...)` (`runner.py:201`) —
  no namespace, no process group — so absolute paths, `../..` and
  `/proc/$PPID/cwd` all still work, and a daemonised child can outlive the
  post-run checks;
* assay's `GIT_NO_REPLACE_OBJECTS`/no-hooks protections apply to assay's own Git
  children (`git.py:116`), not to the lane's command;
* CMRU's own argv begins `/opt/tester-venv/bin/python`, which lives outside every
  snapshot — so "the command cannot read outside the boundary" was never going to
  be true of any real lane.

**The operator ruled the honest scope: a materialisation boundary, not a
sandbox.** §1 now says so before it says anything else, the attestation says so
in the artifact, and the oracles are phrased to prove only what the mechanism
delivers — O9 asserts the symlink is never created, and explicitly forbids a test
claiming its bytes are unreachable. The stronger property would need a
mount/Landlock sandbox, which DESIGN-GUIDE §7 and A-030 put permanently outside
this library.

The other rulings this round forced, each now in §1: scope is REQUIRED on every
R1+ lane rather than defaulting (inference from the `assay.toml` location was
considered and rejected — it would silently re-scope every existing consumer);
the backlog's "removing an input causes deterministic preflight failure" oracle
is impossible as written and is resolved by having the COMMAND fail on the absent
file; overlap must be refused in both directions and between inputs; kinds,
expansion and bounds are closed; one immutable boundary object is constructed
once and shared, so the attestation cannot disagree with what was materialised;
`materialisation: none|complete` stops the object describing a boundary that
never ran; and O9b is deferred to the release pin because `cmru/assay.toml`
declares no R1+ policy for it to test.

**What this cost is worth recording.** Two review rounds have now found that the
first written design was wrong in a way the author could not see: round 1 caught
a false-PASS in the branch rules, round 2 caught a security claim the substrate
cannot back. Neither was a detail, and both were found by a reviewer who could
not be overruled by the author.

---

## 14. Addendum E — review round 3, and the wave's scope widening

Round 3 (a fresh Opus reviewer, no inherited context) returned **NOT READY — 8
blocking, 14 non-blocking**. The operator then reset the round budget, counting
it as the first review of the post-`c7bc9b59` design rather than the third of the
old one: **two rounds remain**, and B006(a) stays in wave 1 rather than being
split out. Every finding is taken as a decision below; none was sent back as a
question.

| # | finding | decision |
|---|---|---|
| 1 | the command can RESTORE an out-of-scope path with stock git from the retained closure | accepted and written into §1's NOT-claimed list. The property narrows to "assay never materialises one". Preventing it needs a pruned closure (breaks B006.3 and base resolution) or a sandbox (A-030 forbids). Measured, not theoretical: `git checkout` after clearing a skip bit, or `git worktree add` |
| 2 | no materialisation mechanism was named, and only one candidate survives the substrate | §1.3 now NAMES it: full index plus `skip-worktree` outside the boundary. The alternative was measured dead — a narrowed index makes `git status` report a deletion per omitted entry, which `_verify` and the post-run dirt check both refuse, and makes `write-tree` build mutants that delete the monorepo |
| 3 | §1.3's old "prefix must equal the project path" rule made the whole preflight unreachable | rule DROPPED, replaced by "the boundary must contain the project root". The preflight survives, relabelled honestly as defence-in-depth over a public API, with its tests constructing `Lane` directly |
| 4 | that rule could not be enforced at load — `config.py` imports no git | moot once the rule is dropped; the containment check moves to snapshot preparation, where git exists |
| 7 | `materialisation`'s two values did not separate the four situations its own prose named | prose narrowed to what two values carry; the claims already say whether the command ran |
| 8 | `cli.py`'s two pre-repository refusal paths must emit a REQUIRED `isolation` with no boundary in scope | they record a module-level CONSTANT `{repository, none}` — truthful on both counts, and not a second constructed boundary, so §1.8 and O18 are intact |
| 11 | §2 claimed the verdict records the artifact path via `judgment.r1`; false on both refusal paths and contradicts A-264 | claim deleted. The path lives in the diagnostic. Explicitly: do NOT add a third member to A-264 to rescue the sentence |
| 15 | O18 asked the implementer to mutate an object §1.8 requires to be immutable | rewritten as an `is` identity assertion plus a construction-count probe |

Non-blocking findings taken: the selection walk descends only into the boundary,
so an out-of-scope gitlink or bad mode no longer fails every lane (which was the
same "unrelated corner blocks everything" shape B006 exists to remove); which
`SnapshotLimits` narrow and which deliberately cannot; `OUTPUT_WRITE_FAILED`
recorded as the strongest declined alternative in §2, with its reason; O9 gains
an ordinary out-of-scope file and directory, a live-tree timing seam, and a
byte-identical diff comparison; O19 asserts skip bits rather than index bytes and
requires a red that actually ran; and O9b names CMRU's SECOND root dependency
(`cmru.release.sh`, executed by `test_release_wrapper.py`) which every earlier
draft missed.

**The scope also widened, in the wave's favour.** The operator granted authority
over CMRU's lanes and rigor, so O9b stops being deferred and becomes the
capability's real acceptance test — a genuine R1/R2/R3 CMRU lane, on a project
already at 100% line and branch coverage with 29/29 mutants killed. That is
better evidence than any synthetic fixture, and it is the thing the backlog asked
for.

---

## 15. Addendum F — review round 2-of-3, and the one fix that held

**NOT READY — 9 blocking, 14 non-blocking.** All folded in. The reviewer was
asked not to re-report Addendum E's eight but to ask whether each fix worked,
what the fixes introduced, and whether O9b's new scope is achievable — and that
framing is what produced the findings, because six of the nine are in those
three categories.

**The mechanism ruling held under empirical attack, and that matters.** §1.3
claims specific behaviour of `git status`, `write-tree`, `ls-files --others` and
`_verify` under a full index with skip bits. The reviewer rebuilt a vbpub-shaped
monorepo in a scratch repo and measured every one: status empty (with a planted
control correctly reported), `write-tree` returning `HEAD^{tree}` exactly,
`--exclude-per-directory` reading the skip-worktree'd `.gitignore` from the
INDEX — which is what lets §2's artifact-parent creation and project scope
compose at all — and the mutation path's child commits intact under skip bits.
A ruling stated as measured fact was re-measured by someone who did not write
it, and survived. That is the standard the rest of this document should be held
to.

| # | finding | decision |
|---|---|---|
| B1 | the `cli.py` constant lies — that path is reachable ONLY for R1+ lanes, exactly the lanes required to declare a scope | constant DELETED. Every path derives the declared boundary from the lane, which `refuse_lane` already receives and which `assemble_verdict` already does for `scope`/`enforcement` |
| B2 | three more producer paths had no rule (`env_required`, `DIRTY_TREE`/`HEAD_CHANGED`, the generic `except`) | same fix covers all five paths; they are now enumerated |
| B3 | `materialisation` had no legal value for "tree written, then failed" — reachable, since `materialize(...)` has no `try` around it | third value `partial`, with §2's `reserve_output` failure explicitly mapped to it |
| B4 | O17 still demanded the prefix-equality rule §1.4 dropped | case replaced, and the case that must LOAD is now stated |
| B5 | O19's skip-bit assertion could not fail — `ls-files -v` prints `S` for skip-worktree; lower case means assume-unchanged | criterion corrected to `S`, with the measurement recorded. This was a cannot-fail negative introduced by the previous round's own fix |
| B6 | O9 demanded index byte-identity while O19 called it wrong | O9 defers to O19; the contract no longer says both |
| B7 | O9's byte-identical-diff bullet was unsatisfiable on its own fixture, which never materialises under repository scope | a second symlink-free fixture is now required for that comparison |
| B8 | O9b would have had the implementer write a lane that fails its own canary — `--cov-fail-under` in the argv makes pytest exit non-zero during the R3 transform, so the observed cause is `COMMAND_FAILED`, not `UNCOVERED_LINES` | ruled: the assay lane carries no `--cov-fail-under`; the floor is `judge.fail_under`. The instruction to treat a failure as "a REAL finding about assay" would have pointed the implementer at a non-defect |
| B9 | O9b named no R1 mode and no base, so it could pass on zero considered lines and `NO_MUTANTS` | `whole_target` with explicit targets, canary target inside them, `base` for R2, and non-vacuity assertions on `considered` and the candidate count |

Non-blocking taken: `boundary_prefix` renamed away from a name that already
means two other things; the prefix (not the boundary) must contain the project
root, which also restores §1.2's rationale; §1.5's walk claim narrowed to "does
not descend INTO"; three loader edits named (`_OPTIONAL_LANE_FIELDS`,
`as_declared`, and a `LANE_SCHEMA_VERSION` bump — the lane grammar changes
incompatibly and that is a different schema from the verdict's); the migration
now rules what `isolation` value each of the 43 fixtures gets and requires the
assignment be logged; work item 0 changed from "record the decisions" to "verify
they are there", since they already are and re-adding would overwrite fuller
text with thinner; O15/O18 reassigned to work item 4, where the object exists;
and §1's duplicate item numbering fixed.

**A usability call taken here rather than deferred:** a `whole_target` target may
name a DIRECTORY, expanding to the adapter-recognised source files beneath it.
dstdns wants one module and CMRU owns 25; making a consumer enumerate and then
maintain 25 paths is how a floor silently stops covering a file somebody added.
The anti-vacuity rule applies to the expansion, so an empty one still refuses.

---

## 16. What must NOT change

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
