# assay — state of play

> **P00–P26 ARE COMPLETE AND MERGED. THE PRODUCT IS NOT YET SAFE TO ADOPT.**
> The current review is
> `nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md`;
> read it before the older v1 review or this accumulated state. P15–P19 made
> declared Python R1/R2/R3 reachable through the installed CLI, but the v2
> adversarial pass reproduced false-PASS/integrity failures in exact command
> reuse, monorepo mutation isolation, stale canary coverage, commit binding,
> and ambient Git repository selection. The cost-aware recarve is a serial
> **P20 → … → P32** queue; P20 merged as `618b6f15`, P21 as
> `678104ad`, P22 as `9d30b25b`, P23 as `a7f49bb4`, P24 as `9f522a72`, and
> P25 as `233926ce`, and P26 as `8f121be3`; P23's carver-owned fixture
> correction is `7c52ecc2` (A-197). **P27's post-P26 JIT carve ran on 2026-08-11
> (`239f6671`), was reviewed by CR-opus-0 (PARTIALLY CONFIRMED), and was
> corrected in `a22842c2`. Its blocking question A-O19 is now RULED — option 2,
> a real source-side Go statement-position oracle (A-217); A-172's end-column-1
> premise is disproved and closed (A-218). P27 is still NOT dispatchable: it
> must be re-carved around option 2, which also owes `go_cover.py` an explicit
> scope status and O3 a rewritten negative. Read
> `reports/assay-P27-JIT-CARVE.md` and `carve-assets/P27/BLOCKED-grammar.md`
> before touching P27. NEXT WORK IS NOT P27: the operator resequenced the wave
> SQL-first (A-219), then moved \[ship\] ahead of P34 (A-248). **Execution order
> is now P20–P26 (done) → P33 (done) → \[ship: cmru + zipapp\] → P34 → P27
> (resumed) → P28 → P29–P32; numbers are identity, not sequence.
> P33 (verdict schema v5) was carved at `b6f0b3bf`, reviewed **NOT READY** by
> CR-opus-0 (eleven blocking defects; report
> `reports/assay-P33-pre-dispatch-adversarial-review.md`), and **RE-CARVED at
> `7a774d57`** answering all seventeen findings (A-223/A-224/A-225), then reviewed
> **NOT READY a second time** and **re-carved again** (A-226/A-227/A-228) — round 2
> found the round-1 defect class at a second gate step, so the closure is now an
> INVENTORY: `carve-assets/P33/sweep_v4_consumers.py`, which found a third
> instance neither review named. Round 3 then turned that inventory on itself and
> found its closure claim false on five grounds with a real missed consumer, so
> the sweep is now pinned by a planted-decoy oracle (A-229). Round 4 ran two
> reviews in parallel (CR-opus-0 plus a one-time second opinion from a different
> model family); both NOT READY, neither finding a new design problem — the
> residue was verification, including three locked tests that called a function
> that does not exist (A-230/A-231). Round 5 found that round-4 fix itself
> unverified — the same tests still unsatisfiable — and made the remedy
> procedural: **A-232, a stated pass/fail count is not evidence; paste real
> command output and classify each pre-implementation red as legitimate or
> illegitimate.**
>
> **P33 IS MERGED.** Round 6's three narrow oracle bugs were fixed in
> `e82da152`; the implementation landed and was reviewed through both phases
> with **zero behavioural gaps** across 33 adversarial attacks and 13
> controlled breaks; the post-review carve-asset repair (one locked-asset
> defect only the carver may correct, plus four documentation staleness items)
> is `62305df3`; the merge is `e41ea99f`. **No further P33 review is owed —
> a session resuming from an older banner would re-do finished work.**
> Read `reports/assay-P33-JIT-CARVE.md` and `carve-assets/P33/README.md`; the
> design is `SCHEMA-V5-DESIGN.md` (which carries its own precedence banner per
> A-231) with decisions A-220–A-233 plus A-234–A-244 from the two Fable
> full-codebase review rounds.
>
> **The nyxloom spine now exists** (`1-north-star.md`, `2-product-definition.md`,
> `3-roadmap.md`, beside the pre-existing `4-backlog.md`). `2-product-definition.md`
> is the machine-diffed one: 15 features, 44 structured acceptance criteria, 40
> `proven` with real pytest node ids and 4 `absent` with what blocks each. All
> four validate against `nyxloom/src/nyxloom/schemas/spine-*.schema.json` and
> return **zero findings from `nyxloom.lint.lint_spine`**; every citation
> resolves under `nyxloom.product_evidence.evidence_resolves`. **F008 (Go) is
> `building`, not `shipped`** — that is the honest record of A-234/A-239 and must
> not be "tidied" up. assay's trove does not run under the nyxloom daemon; the
> schemas are the contract.
>
> **NEXT WORK IS THE SHIP MILESTONE (cmru adoption + zipapp), NOT P34.**
> Resequenced 2026-08-11 by A-248: the execution order is now
> **P33 (done) → \[ship: cmru + zipapp\] → P34 → P27 (resumed) → P28 → P29 →
> P30 → P31 → P32.** P34's carve must NOT be dispatched until ship is landed.
> `handoffs/README.md`'s table row for P34 says NOT NEXT for the same reason.
>
> **P34's own rulings are landed, so it inherits rather than makes them:** **A-242** (the flat seven-method `LanguageAdapter`
> stays; the five SQL-dead methods raise rather than return a plausible value)
> and **A-243** (helper provenance is permitted for `MUTATION_DISCOVERY_FAILED`
> only, with a negative test for the other two failure terminals). P34's carve
> inherits these rather than making them.
>
> Before P27's re-carve is dispatched, read **A-234** (the committed Go coverage
> fixtures are wrong in both coordinates and must be regenerated *with* their
> consumer expectations re-derived from the option-2 oracle) and **A-239** (the
> oracle's seam is RULED — explicit block extents from `go_cover.py`, a NEW
> protocol hook for statement positions, intersection as a pure core function,
> Go-specific; do not re-open the shape). Also **A-240/A-241**: A-237's narrowing
> stands but its stated evidence was false, and A-116's verbatim-propagation
> enforcement was PARTIAL. **A-241 is now FIXED (`a7c16d0c`, A-245) — and half
> of it was wrong: only `MUTATION_DISCOVERY_FAILED`, `BASE_IS_HEAD` and
> `UNREADABLE_ARTIFACT` were under-enforced; `GIT_FAILED`/`DIRTY_TREE`/
> `HEAD_CHANGED` beside a failing baseline are legitimate producer output and
> must stay unconstrained.** **A-244** accepts A-O06
> as the next capability after P32 — planning only, no package, no dispatch.**
> **P27's** pinned image inputs, two-commit fixture with real reproduced
> profiles, independent statement manifest, and locked v4 missing-tool artifact
> remain frozen and survived the A-O19 ruling; P27's expected R1 `Coverage` line
> sets and its work item 5/6/9 oracles do not exist yet and must not be
> invented. P26's exact config/safe-I/O/Git/deadline interfaces, complete v4
> templates, process-boundary proof, and 41-test acceptance are recorded in
> `reports/assay-P26-JIT-CARVE.md` (A-209–A-214). Three original bundles were
> split at independent security/integration seams, not expanded into 33
> microtasks. P27–P32 remain provisional until their predecessor merges and
> their named JIT proof assets pass the exact pre-dispatch review.
>
> **Reachable does not mean proven safe.** Sol finding 1 was about
> reachability, and R3's own reachability is what P19 delivered — the
> controller's review of it then found that the single most important
> thing R3 can prove (`uncovered-line`, caught for its own reason) was
> structurally impossible in the shipped branch and invisible to a
> gate-green suite at 100% coverage (A-149/A-150). Read that pair before
> assuming any newly-reachable level is also correct.
> If you are picking this project up: read the v2 review, then the v1 review,
> then `nyxloom-trove/reports/assay-P14-BRIEF.md`
> (the P14-era final-state brief — still accurate for what P00–P14 built,
> just not for whether it's ready to depend on), then this file, then
> `decisions.md`.

## Where things stand

**Merged on `main`, P00 through P26** — the complete P00–P14 series, the
five-package P15–P19 repair/validation series, and P20–P23 of the pre-adoption
integrity queue plus P24 distribution, P25 real-Python qualification, and P26
attested-evidence/deadline hardening. The
historical gate count below is the P19-era receipt; P20–P25 have their own
controller-owned receipts and larger suites. Gate green at
**1831 passed, 1 skipped, exit 0, 100% statement AND branch coverage**
(3070 stmts / 1256 branches) — counts measured INSIDE `tester-unified:
local` itself, not in the devcontainer, because the gate's own `argv`
reports only an exit code and a cockpit venv carries different pins. Run
through the REAL self-hosting mechanism P14
itself built (see below) — independently reproduced by the controller
twice per package: once in the package's own worktree before merge, once
again directly against `main` after merge, by literally parsing
`nyxloom-trove/nyxloom.toml`'s own gate `argv` and running it verbatim
rather than trusting any transcript. Every one of the sixteen packages was
independently reverified by the controller in the foreground gate
container (and post-merge on `main`) rather than trusted from the
implementer's own report — this discipline has held for every package so
far, and it has never once come back clean.

- P00/P01: skeleton, lane config, verdict model + schema.
- P02: changed-line extraction + measurability guards.
- P03: coverage format registry.
- P04: runner, `assay run` CLI, R0 verdict emission.
- P05: adapter protocol, four-way union, R1 verdict emission.
- P06/P08: Python and Go `LanguageAdapter`s.
- P07: statement-span attribution (the multi-line-statement gap).
- P09: cause-sensitive canary — real end-to-end proof the gate rejects
  known-bad input for the intended reason.
- P10: attested evidence staleness — Tier-3 external review loading,
  proving only whether declared reviewed paths are current, never
  verifying content (A-110/A-111).
- P11: valid mutant construction — `generate_mutants`, the adapter
  protocol's 7th and final method; a byte-exact single-site splice, never
  `ast.unparse`'s whole-file reprint (A-112/A-114/A-115).
- P12 (bounded mutation execution — the R2 producer): baseline-gated
  (a red baseline stops before any mutant), `jobs`-bounded via an injected
  executor factory (never `os.cpu_count()`, A-122), per-mutant
  `shutil.copytree` isolation (never in-place, never a git worktree,
  A-120) so the shared source is unchanged BY CONSTRUCTION, four outcome
  buckets (killed/survived/crashed/budget_exceeded, A-116/A-117). New
  `Mutation`/`MutantOutcome` payload types in `verdict.py`, `Claim.mutation`
  gated to R2, a third schema `allOf` branch. `assemble_verdict` gained
  `mutation_claim`. A real circular import (`mutation -> runner ->
  adapters.base -> mutation`) was found and fixed with a deferred import.
- P13 (standalone wheel proof): proved a real `assay run` through the
  INSTALLED console script emits a genuine R0 verdict; a real Python
  fixture passes through the full pipeline; the Go adapter ships and
  works, adapter-level only (A-126). Found and corrected an UNFALSIFIABLE
  oracle negative — `setuptools_scm` is absent from every interpreter in
  the real gate image, so "removing `fallback_version` breaks the build"
  changes nothing observable there (A-124, independently reproduced by the
  controller with two real wheel builds).
- **P14** (self-hosted conformance — the FINAL package): `assay verify`
  (new `verify.py`) is an artifact validator, never a lane-canary runner
  (A-129, correcting a stale note this very file used to carry) — it
  reconstructs the real `verdict.py` dataclass graph rather than shipping
  a runtime `jsonschema` dependency, plus four checks JSON Schema alone
  can't express (outcome-agrees-with-rollup, argv arithmetic, claims/
  evidence coverage). The gate itself was restructured (A-130): it now
  builds and installs a REAL `assay` wheel inside `tester-unified` and
  runs `assay run tester-unified` through THAT wheel — never a
  `PYTHONPATH=src` source-tree shortcut — with a SEPARATE, independently-
  invoked second pytest step as the real oracle, never `assay verify`
  alone. A real, self-consistent "universal PASS" producer-mutation proof
  (A-131, against a disposable `shutil.copytree`'d copy of `runner.py`,
  never the real file) demonstrates `assay verify` wrongly accepting the
  lie while the independent step correctly rejects it. The readiness pass
  found a real 3/19 gap in the outcome/reason-code fixture matrix (two
  pairs structurally unreachable by pre-existing design, one genuinely
  missing and closed, A-128) and resolved a direct conflict between this
  file's own prior text and the handoff's oracles over what `assay verify`
  even was (A-129).
- **P15** (measurement input integrity — first of the v1.1 repair series):
  `parse_added_lines` rewritten from unconditional content-sniffing to a
  real two-state machine driven by each hunk's own declared counts (an
  added line whose content begins `++` can no longer be read as a file
  header; git's no-newline marker advances neither side); `dirty_paths`
  reads `git status --porcelain=v1 -z` as raw bytes, so a rename is two
  NUL-terminated fields rather than a split on displayed `" -> "` text a
  real path can legally contain; `FileCoverage` enforces positive line
  numbers and pairwise-disjoint executed/missing/known-excluded at
  construction, in the one place every format passes through; two raw
  coverage keys normalizing to one repository path are refused rather than
  silently last-key-wins; a source root resolving outside the project root
  and a name in both `env` and `env_passthrough` are refused at load.
  **Controller review found three further defects in P15's own new input
  layer (A-134)**, each reproduced against a real git binary first: git
  appends its space-disambiguation tab to the printed LINE, so on an
  already-quoted path it lands after the closing quote and the whole
  quoted spelling went unrecognised (a real file `a "b c.py` recorded
  under the key `'"b/a \"b c.py"'` and thus dropped from measurement);
  `str.splitlines()` also breaks on `\r`, form feed, vertical tab,
  `\x1c`-`\x1e`, `\x85`, U+2028 and U+2029 — ordinary source content —
  and against the new counts-driven machine a torn line closes the hunk
  early and silently DROPS its remaining additions (a form feed in a real
  Python file gave `[2, 4]` for `[2, 3, 4]`); and `git.run`'s
  `text=True` decoded with the ambient locale, raised an untyped
  `UnicodeDecodeError`, and rewrote a lone `\r` inside a source line into
  a phantom second line. All seven forbidden files confirmed untouched by
  empty diff (the merge commit message miscounts them as eight).
- **P16** (independent verdict conformance — schema v3): the verdict
  artifact now records the policy that judged it, and `assay verify`
  re-derives R1/R2/R3 status from payload plus that policy instead of
  trusting the producer's own choice — closing sol finding 2, and the
  schema half of finding 6 (`judgment.r1.base` records the full resolved
  comparison commit). Top-level `scope`/`enforcement` join the
  lane-resolved group; a closed `judgment` object carries the effective R1
  policy and reserved R2/R3 shapes P18/P19 populate additively without
  another bump; `Coverage` gains `excluded_lines`/
  `files_with_excluded_lines` plus real arithmetic invariants. All 34
  hand-written fixtures converted to v3 by hand; two pre-existing fixture
  defects (`fail.json`'s rounded `pct`, `inconclusive.json`'s
  mutation-less R2 claim) fell out of the new invariants.
  **Controller review found five more defects (A-136–A-138)**, none of
  which the package's own ten mutation counts or four finding-2
  reproductions could see, because a mutation test only interrogates lines
  that exist: a `PASS` claim with its payload simply DELETED was accepted
  at every rigor level (`r1_pass` minus `coverage`, `r2_pass` minus
  `mutation`, `r3_pass` minus `canary` — each cheaper to forge than the
  contradictions it does catch, each leaving the rollup in perfect
  agreement); R2 re-derivation was skippable entirely by declaring
  `rigor = ["R2"]` and omitting R0, though `judge_mutation` provably never
  reads its baseline once a mutation payload is present; work item 7's v2
  diagnostic was never built (a real v2 artifact reported `schema:
  'excluded_lines'`, a bare `KeyError`); work item 3's summary-field
  identity was never enforced; and four of work item 6's named negatives
  (crash/budget precedence, broken prerequisite propagation, wrong canary
  cause, broken control) had no test at all. All five were found by
  feeding real v3 documents through `verify_text` the way a consumer
  would. Deleting `_check_r2_rederivation` or `_check_r3_rederivation`
  wholesale failed exactly ONE test each before that pass; six and three
  after. All five forbidden files confirmed untouched by empty diff.
- **P17** (Python R1 CLI pipeline — closing the first half of sol
  finding 1): `assay run` now executes AND judges a declared Python R1
  lane as one commit-bound operation. New `runner.run_lane` ties the four
  P04/P05 functions together and owns the prerequisites none of them did:
  the WHOLE worktree must be clean (sol finding 6, live in the shipped R0
  path until now), the declared coverage artifact must be untracked, a
  regular file and not a symlink, and a stale copy is removed so the
  artifact judged is provably this run's own output. `evaluate_r1` widened
  to render `GIT_FAILED`/`FORMAT_MISMATCH`/`UNREADABLE_ARTIFACT` as
  complete R1 claims (closing A-128, below) via an additive
  `on_base_resolved` callback that surfaces the resolved base WITHOUT a
  second git resolution and WITHOUT breaking `canary.py`'s frozen call
  contract. `registry.py` redesigned so an entry names the rigor levels
  **this build actually reaches** through an adapter — shipping
  `adapters/go.py` is no longer mistakable for "Go works". `judge.base` is
  now required for R1/R2 and never defaulted.
  **Controller review found three defects (A-139–A-141), all invisible
  from the diff and from the green gate, and all found within minutes of
  feeding real documents to the real entry point** — the method the P16
  review's own successor brief prescribed, and its first independent
  confirmation. Two terminal paths with a known HEAD emitted no artifact
  at all, and the worse of them (an R2/R3 lane, which never reached the
  capability gate because the registry was only ever consulted for the
  literal `"R1"`) **ran the lane's command to completion first** — side
  effects committed, nothing written. A refusal deleted a file before
  refusing: the stale-artifact removal ran BEFORE the cleanliness guard
  instead of after it, so a lane naming ANY untracked regular file as its
  `artifact` had that file destroyed and the guard then judged a tree the
  code had itself modified. And the conformance matrix that audits
  reachability never moved, while simultaneously asserting the new shape
  must never exist — so a correct fixture would have failed the suite.
  Four oracle clauses were honored only in part and are now closed: O1's
  "complete artifact" was eight field assertions; nothing distinguished a
  RESOLVED `judgment.r1.base` from an echoed one (every test declared a
  full SHA, so `base=judge.base` passed everything); and two of O4's six
  named terminal shapes had no pipeline-level test. All four forbidden
  files confirmed untouched by empty diff. **No P17 LOG was written** —
  its deferral notes and mutation counts live in the implementation
  commit's own body instead, which is better than nothing and still not
  where A-072 says a ruling belongs. **Still not written at the P18 merge,
  and deliberately not reconstructed then**: writing a package's record
  after the fact from its own diff would produce a plausible artifact
  nobody measured, which is the precise failure this project exists to
  refuse. What P17 knew lives in its four commit bodies, in A-139–A-144,
  and in the "Carried in from P17" sections of the P18/P19 handoffs.
- **P18** (Python R2 CLI pipeline — sol finding 1 now two thirds closed):
  `assay run` executes real changed-line mutation for a declared Python R2
  lane. `judge.mutation` stops being an opaque passthrough and becomes a
  validated closed table (positive integer `jobs` — never machine-derived,
  A-082/A-122 — and a non-empty, duplicate-free, order-preserving
  `operators` list cross-checked against `MUTATION_OPERATORS` at LOAD
  time). `run_mutation`'s own internal baseline is GONE: the baseline is
  now the exact `CommandResult` R0 already produced, so the lane's command
  runs at most once per invocation (sol finding 11) and `assay verify`'s R2
  baseline proxy becomes an identity (A-137). New
  `resolve_mutation_targets` builds R2's candidate list from the SAME
  resolved diff R1 measures against; new operator filtering keeps only the
  declared subset before anything is submitted.
  **Controller review found four defects (A-145–A-148) plus two oracle
  gaps, on a branch that arrived gate-green at 100% coverage.** The worst:
  `assay run` CRASHED with no artifact — after running the lane's command —
  for any project living in a SUBDIRECTORY of its git repository, which is
  assay's own layout inside `vbpub`. A mutation target's path is
  repo-top-relative while each mutant's scratch tree is a copy of the
  PROJECT root, and the two had never had to agree because every fixture in
  the suite makes them the same directory (A-145). Work item 7 and O4 — an
  installed-wheel R2 fixture with complete artifacts and shared-tree hashes
  — were skipped entirely and not declared skipped (A-147); five of O4's
  six shapes now exist, and the sixth is recorded in the suite as
  unreachable, with the argument. `--help` still under-declared the build's
  capability (A-146). And `judgment.r2` was populated while tied to nothing,
  with no package in P19–P25 able to touch `verdict.py`/`verify.py` to fix
  it — a deferral with no executor, found by reading downstream scopes
  rather than the diff (A-148). Work item 8's own mutation set had never
  been run; running it found two more properties with no discriminating
  oracle. A LOG exists this time:
  `nyxloom-trove/reports/assay-P18-r2-cli-pipeline-LOG.md`.
- **P19** (isolated R3 CLI pipeline — sol finding 1 CLOSED): `assay run`
  proves one declared canary for a Python R3 lane. `judge.canary` stops
  being an opaque passthrough and becomes a closed, validated
  `CanaryConfig` — exactly `mechanism` (cross-checked at load time against
  `assay.canary.CANARY_MECHANISMS`) and `target` (a project-relative,
  existing, ordinary file beneath a declared source root; absolute, empty,
  traversing, symlinked, missing and directory targets each refused with
  their own diagnostic). The consumer's whole repository is `copytree`d —
  `.git` included, so a symbolic `judge.base` still resolves — into scratch
  state the run owns end to end; the real worktree is never staged,
  committed, or written to. **A-148 is closed**: `judgment.r2`/`r3` now
  carry the same correspondence check `judgment.r1` had, in the model AND
  in `verify.py`'s independent raw-document layer.
  **Controller review found one structural defect, one absent oracle, one
  false recorded impossibility and one misleading public name (A-149–A-152),
  again on a branch that arrived gate-green at 100% coverage.** The worst:
  the canary judged the scratch COPY using the consumer's own absolute
  `judge.source_root_paths`, so every changed file fell outside every root,
  `considered` was 0, `pct` was a vacuous 100.0, and R1 PASSed having
  measured nothing — which made `uncovered-line`, one of exactly two
  mechanisms, unable to ever PASS (A-149). It was invisible because every
  R3 fixture declared a rigor set in which that mechanism's expected reason
  was unreachable (A-150), so half of O3 had no witness at all. The suite
  also recorded a wrong-observed-cause as unreachable through the real
  adapter; it is reachable, and P24 had already been told to trust the
  claim (A-151). `judgment.r3.target` stays untied on purpose — nothing in
  schema v3 can witness it (A-152/A-O18). LOG:
  `nyxloom-trove/reports/assay-P19-isolated-r3-cli-pipeline-LOG.md`.
- **P20** (repository/artifact boundary integrity): one sanitized explicit
  Git identity, descriptor-owned bounded coverage I/O, post-command whole-repo
  dirt/HEAD checks, and a controller-owned registered gate receipt. Merged as
  `618b6f15`; A-173–A-179 and the P20 JIT/review reports are authoritative.
- **P21** (v4 evidence contract): the sole current schema is v4; verdict output
  is reserved atomically; mutation identities/sites/caps, canary target,
  exclusion capability, and timestamp ordering are independently checkable.
  The first dispatch correctly blocked on Go capability ambiguity; A-183 fixed
  it before the successful Opus implementation/review. Merged as `678104ad`;
  locked acceptance was 28 passed and the registered gate carried all markers.

Key commits: `d9839e81` (P17 controller repairs, A-139–A-143),
`e5b81d4c` (P17 implementation), `340d9633` (P16 merge), `50110247` (P16 controller repairs),
`326507f1` (P15 merge), `a8357405` (P15 rulings, A-134–A-135),
`2ab50f75` (P14 merge, the P00–P14 series-closing commit), `56c821c2`
(P14 rulings, A-128–A-133), `bca3c345` (P13 merge), `a42fe02a` (P13
rulings, A-123–A-127), `fa65dd58` (P12 merge), `f828d14e` (P12 rulings,
A-116–A-122). Full history for every package in `decisions.md` (A-090
through A-138) and each merge commit message — not repeated here.

**Post-series review and v1.1 (live status, updated same day as P14
merged):** `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` is a
second adversarial review, resumed on the same codex session that shaped
the original P02 carving, this time reviewing the actual shipped code
rather than the carving process. Verdict: the design is mostly sound, but
the product is not — three CRITICAL confirmed defects (the CLI never
wires R1/R2/R3 in at all; `assay verify` doesn't re-derive R1/R2/R3
*correctness*, only schema shape; a real patch-parsing bug can silently
drop or misattribute changed lines), several HIGH findings (an
order-dependent coverage-key-collision bug that can flip PASS↔FAIL on
identical data; non-NUL-safe Git path parsing; no dirty-worktree check
before `assay run` executes — live in the shipped R0 path today; an
attestation directory-staleness bug; unbounded/traversal-exposed
attestation-file loading; a placeholder `0.0.0` version unsafe for real
consumers), plus a real, live `env_passthrough`-overrides-declared-`env`
gap in the current R0 path. The controller independently reproduced the
two most surprising claims (the patch-parser bug, the coverage-key
collision) directly against the merged code before trusting the rest of
the review. Full findings, direct answers to the controller's own
questions, and an estate-adoption order are all in that file — read it in
full, this summary is not a substitute.

**Carving status (live, as of this update): P15 through P32 are carved. A
TypeScript adapter and any consumer-side adoption package are NOT yet
carved.** Adoption belongs in the selected
consumer's trove after Assay's wheel contract is real; it is not an Assay
handoff with authority to edit another project.

**Implementation status of that series: P15 through P25 are MERGED. P26 is
READY for Sonnet xhigh implementation at the Sol freeze commit after source
anchor `233926ce`.** Its frozen packet is
`handoffs/assay-P26-attested-evidence-cli-hardening.md`, its locked acceptance/
construction material is `carve-assets/P26/`, and its JIT disposition is
`reports/assay-P26-JIT-CARVE.md`. The execution order is:

`P20 repository/artifact boundary → P21 verdict v4 → P22 committed snapshot →
P23 exact reexecution integration → P24 wheel → P25 real Python qualification →
P26 attestation → P27 Go gate/adapter → P28 real srdm R1 → P29 Go helper/site
protocol → P30 real Go R2 → P31 real Go R3 → P32 Vitest formats`.

P16's outcome originally propagated into FIVE later handoffs, each now
carrying a "Carried in from P16, merged" section (all six edited handoffs
re-linted `clean`): **P17** (the `judgment.r1`-iff-`coverage` trap — a
lane that resolves its R1 policy and then renders `NO_MEASUREMENT` must
NOT record it; plus where `base` and `source_roots` come from), **P18**
(its work item 4 turns `assay verify`'s R2 baseline proxy into an
identity — do not reintroduce a second baseline run), **P19** (an
inconclusive canary still renders a real `CanaryResult`; `ERROR`/
`BUDGET_EXCEEDED` stay payload-free), historical **P22** (current **P27/P28**;
its independently
calculated R1 expectation must now calculate `judgment.r1` too, not copy
the Python fixture's), and historical **P25** (current **P32**; Istanbul has
no exclusion channel).
The v2 review supersedes the stale parts of those briefs: P21 closes
A-O16/A-O18 in v4, P20 closes A-O17, P22/P23 replace working-tree copy
isolation, and P27–P31 add real disposable-srdm validation.

P21's JIT pass also supersedes A-168's package ownership: the cap cannot be
true while the Python adapter returns an unbounded tuple of full mutated files,
and deriving byte identity from old/full text pairs produces false spans for
insertions and shared suffixes. P21 therefore lands the already-designed
bounded common/Python `MutationSite` seam; P23 consumes it without editing
adapters (A-180). P21 also freezes the descriptor-owned verdict output boundary
and honest Schema/model/raw-verifier ownership (A-181/A-182). Its first Opus
dispatch correctly stopped on the residual forbidden Go import: A-183 widens
only that forced seam migration and preserves adapter-wide `UNSUPPORTED` as
payload-free `INCONCLUSIVE/MUTATION_UNSUPPORTED`, distinct from supported
zero-site `NO_MUTANTS`. Go syntax discovery and R2 registration remain P29/P30.

For historical clarity, the next paragraph uses the handoff ids that existed
when Sol carved them; A-153 and then A-167 record both renumbering steps. Sol was given write access (scoped by prompt, not sandbox, to new files
under `nyxloom-trove/handoffs/` only) to materialize the twelve-package plan
from its own review (P15 correctness repairs → P16 schema v3 → P17/P18/P19
Python R1/R2/R3 CLI wiring → P20 attestation hardening → P21 versioned
wheel → P22/P23/P24 real Go R1/R2/R3 → P25/P26 real lcov/Istanbul +
TypeScript adapter), one file at a time. Its own `git commit` was blocked
at the SANDBOX level (`.git/index.lock: Read-only file system` under
`workspace-write` mode) — not a choice, a hard restriction — so it wrote
files only and reported exactly why; the controller committed each one
individually after independent verification (nyxloom lint, `input_revision`
matched real HEAD at carve time, a sample of cited estate paths/decisions
confirmed to exist — e.g. `tester-unified-go/Dockerfile`, cited as proof a
real Go toolchain already exists in the estate, and `ciu`/`cmru`/`topos`'s
own `pyproject.toml`s, cited as release-wheel prior art, both confirmed
real). Resumed a second time with the fixed instruction (write-only, no
commit attempts), it produced P16 through P25 — then hit a hard EXTERNAL
usage cap ("You've hit your usage limit... try again at Aug 12th, 2026 9:39
PM") mid-way through P26. Because every completed file had already been
written to disk (not held only in the session's own memory), nothing was
lost — this is the exact "commit/persist immediately" discipline paying
off even though the actual git-commit half of it turned out to be
sandboxed away. **P26 and P27 cannot be attempted again before Aug 12,
2026** using this same account/session. Key commits for this phase:
`48771e48` through `bcf9afb9` (P15 through P25, one commit each, `git log`
has the full list).

**The pattern across the original package-local series**: every single readiness pass found
at least one real gap, never zero — wrong/stale citations (P09's "canary"
pointer, P11's "mutation" pointer, both pointing at the wrong estate
project entirely), capability built with no package yet scoped to consume
it, decisions needing generalizing as later packages rediscovered them,
and — in the last two packages — a genuinely unfalsifiable oracle negative
(P13) and a direct conflict between two authoritative-looking sources
about what a whole subcommand should even be (P14). The discipline that
caught all of it, every time: verify agent claims yourself, independently,
before ruling — grep counts, live structural checks, direct interactive
exercise of the real code, real gate re-runs in the foreground, never
trusting a report at face value. Every package held up under that
package-local discipline; the v2 post-series review then disproved its
completeness by combining axes and comparing all repeated executions as one
system. "Independently reviewed" was real, but it did not mean
"independently specified against a different input distribution."

**A-O14 is DECIDED and assigned to P21 (A-157):** reserve/validate the
declared verdict destination before execution and add the closed
`OUTPUT_WRITE_FAILED` terminal. The physical impossibility of writing to an
unavailable destination remains explicit; Assay does not invent a fallback.

**A-O15 is assigned to P26 and depends on P20.** `attestation._changed_paths` still uses
newline-delimited Git display paths plus `splitlines()`. Real filenames with
newlines or U+2028 were reproduced as wrong identities. P20 supplies the
sanitized Git boundary; P26 now avoids filename transport entirely by using one
bounded `diff --quiet` pathspec query per reviewed identity.

**A-O16 is DECIDED and assigned to P21 (A-157).** Schema v4 records the
closed exclusion capability `reported` versus `unavailable`; P32 consumes
that decision for Istanbul rather than redesigning it.

**A-O17 is assigned to P20, before P27.** The known Go-normalization
collision is one instance of the general rule that every expected post-HEAD
Git/coverage/source/evaluation error renders a complete artifact. P27 keeps a
real Go collision fixture but owns no runner workaround.

**A-O18 is DECIDED and assigned to P21 (A-157).** P21 is the deliberate
schema-v4 consumer migration and adds `canary.target`, so
`judgment.r3.target` becomes independently witnessable. No successor may
reopen the schema outside a newly carved migration.

**A-128's "three structurally unreachable pairs" is CLOSED — merged, not
merely carved (2026-08-08, P17 + A-141).** Work item 6 widened
`evaluate_r1` to render every `AssayError` its own guard sequence raises
as a complete R1 claim, so `ERROR`/`GIT_FAILED` and claim-level
`ERROR`/`FORMAT_MISMATCH` and `ERROR`/`UNREADABLE_ARTIFACT` are now
ordinary producer terminals, each with a hand-written fixture. All 19
pairs are covered; `EXCLUDED_ENTIRELY` in `tests/test_verdict_
conformance.py` is empty and the audit is level-aware. **The debt bullet
that used to sit below is DELETED rather than annotated** — a
"permanent debt" entry that is no longer true is worse than no entry.

The half of that closure worth carrying forward: **the capability landed
and the audit that measures it did not move with it.** The conformance
test still excluded two pairs as structurally unreachable, still told its
reader not to "fix" `evaluate_r1` to reach them (which is precisely what
this package was chartered to do), and still asserted claim-level
`ERROR`/`UNREADABLE_ARTIFACT` "must never appear" — so a CORRECT fixture
would have turned the suite red. It stayed green only because nobody
wrote one. When a package's charter is to make something reachable, the
matrix that calls it unreachable is inside that package's scope, not a
follow-up.

**Accepted, permanent debt — recorded here rather than silently dropped,
since there is no more series left to fold any of it into:**
- Two deliberate wiring gaps from P12, never closed by P13 or P14 (neither
  was scoped to): nothing builds real `MutationTarget`s from a real diff;
  nothing reads `assay.toml`'s `judge.mutation` table (`jobs`/`operators`
  stay opaque, A-121). If either is ever needed, it is new, deliberately
  scoped work — not an oversight.
- `assay.toml`/`nyxloom-trove/nyxloom.toml` both declare R0-only,
  permanently, by design (A-133) — assay judges OTHER projects' diffs;
  applying R1+ rigor to its own diff was never in scope for this series
  and isn't an oversight either.
- **P00/P01's own handoffs still fail nyxloom's linter** (`L7`/`L11`/`L12`/
  `L13` — missing BLOCKED-marker/branch-name mentions, an oracle
  referencing a path outside `scope.touch`, unresolved cross-repo globs).
  Flagged at every package's own review since P02, explicitly named as a
  candidate for P14's own audit, and explicitly NOT fixed by P14 either
  (its own `scope.touch` only matches `assay/README.md`, not the trove's
  handoff files) — this is now permanent, accepted debt on two historical,
  already-merged, gate-green planning documents. P02 through P14's own
  handoffs are all clean.
- lcov/Cobertura parsers (P03) have zero prior art anywhere in the estate;
  Cobertura's multi-`<class>`-per-file merge is untested against any
  real-world sample. Low risk, never exercised by assay's own self-hosting
  (a Python project).
- One test (`test_standalone.py::test_a_real_pass_matches_the_documented_r0_pass_shape`)
  fails when run from this devcontainer's own ambient Python (which has a
  working `setuptools_scm`, unlike the real `tester-unified:local` gate
  image) — environment-specific and expected, passes in the real gate.
- **When measuring coverage by hand, do NOT pass
  `--ignore=tests/test_self_hosting.py`.** That file holds the test which
  covers `src/assay/__init__.py`'s `except PackageNotFoundError` fallback
  (lines 43-44); ignoring it reports 99% and two missed statements that
  look like a real regression and are not. Only the one case needing
  `ASSAY_SELF_HOSTING_VERDICT` skips without it. The gate's own `argv` does
  ignore that file, correctly — it runs it as a separate second step.

## How the work was and will be run

The paragraphs below record the P00–P19 process. P20–P32 use the supported
frozen-orientation/fork pilot in
`nyxloom/docs/frozen-orientation-fork-workflow.md` and the mechanical Luna
prompt `nyxloom-trove/FROZEN-WAVE-CONTROLLER-PROMPT.md`; transcript-file
backup/restore is superseded. Each run still processes one package serially.

`WORKFLOW.md` is the loop. `MEASUREMENTS.md` is what it costs and what it
catches — read both; several of their claims are corrections of earlier claims
that were wrong, and the corrections are the useful part.

Per package: **readiness dispatch** (orient, report, implement nothing) →
controller rules on every raised item **in the go-message** → implement →
self-review → successor brief → controller reviews, repairs, merges → controller
asks what the result changes about *remaining* handoffs. Ran this way,
serially, one package at a time, for all fifteen packages, P00 through
P14.

Three rules with scar tissue behind them:

1. **A ruling delivered only in an agent message is not applied** (A-072). It
   reaches one agent and nowhere else. Land it in `decisions.md` and in the
   handoff files before the next dispatch.
2. **Base rebuilds batch; truth does not** — editing `decisions.md` does not
   mutate an already-frozen provider transcript. Land required rulings in Git
   immediately, force each child to reconcile the frozen OID-to-HEAD diff, and
   rebuild affected bases once at a deliberate epoch boundary.
3. **Run nyxloom's linter as part of review** (A-089). A defect lived through
   two implementations and two reviews because nobody ran it.

## Roles, and why they are split this way

The controller carved the original series and **reviewed its own carving**. An
external adversarial review (gpt-5.6-sol, high effort, series-level remit) then
found **23 confirmed defects**, five critical, two in already-merged code —
including an oracle that could not fail, a carving defect spanning four
packages, and a wrong sentence in the design guide.

So the roles were **swapped, not collapsed**: sol carved the repair and the
reissued series; the controller reviewed and merged. The independence is the
asset; the seating is not. If the controller carves again, something else must
review it.

**Codex sessions (resumable, keep for continuity):**
- `019fd977-f091-7dd1-8af5-38c41db89507` — the review → guidance → repair
  thread. Used four times so far: the original pre-P02 carving review (23
  confirmed defects); the post-P14 shipped-code review
  (`assay-v1-post-series-review-sol.md`, ~1.58M tokens for that pass alone
  — expect a large fixed per-turn cost even on resume); a write-access
  attempt that produced one correct handoff (P15) but discovered `git
  commit` is blocked at the SANDBOX level under `workspace-write`
  (`.git/index.lock: Read-only file system` — not a policy choice,
  confirmed by the session's own honest report); and a second write-access
  pass (commit attempts removed from the instructions) that produced P16
  through P25 before hitting a hard EXTERNAL usage cap — **this account
  cannot be resumed again before Aug 12th, 2026, 9:39 PM** (its own literal
  error text). Total token spend across all four turns so far: ~5.87M.
- Read-only invocation (review/analysis only, cannot write):
  `codex exec --sandbox read-only -m gpt-5.6-sol -c model_reasoning_effort=high --cd <dir> resume <id> "<prompt>"`.
- Write-access invocation (`workspace-write` lets it WRITE files under
  `--cd`'s tree, confirmed working for P15–P25; it does NOT let it commit —
  budget the controller's own time to verify-and-commit each file
  afterward, do not ask it to commit again):
  `codex exec --sandbox workspace-write -m gpt-5.6-sol -c model_reasoning_effort=high --cd /workspaces/vbpub resume <id> "<prompt>"`.
- Either way, **run it via `run_in_background` with NO `nohup`/`&`** or the
  completion notification is spurious.

## Longer-lived notes (not repeated in "Watched" above)

- **HISTORICAL/SUPERSEDED: the transcript-snapshot lineage was considered for
  P02 and deliberately not built.**
  Orientation measures ~142k tokens per package — more than the implementation
  it precedes — and a restored snapshot costs ~14k, so the paper savings are
  real (~128k/package × 13). But the only *verified* case is a trivial 0-tool,
  two-line resume; the actual workload is ~85 turns of reads then ~90+ tool
  calls with file writes and a gate run, and that path is untested. The
  mechanism works by copying an internal, explicitly "unstable across
  versions" transcript file to fake memory erasure — the same shape of risk as
  the carving-review collapse that cost this project 23 defects (see Roles,
  below): trusting an unverified process instead of checking it. Building the
  reusable package-neutral S0 and validating restore through a real
  implementation-scale turn is itself not free, which eats into the very first
  package's savings. P02 was ready to dispatch now; that won by not blocking on
  unproven infrastructure. Revisit if wall-clock/token pressure in the
  remaining packages makes it worth the validation cost — `MEASUREMENTS.md`
  still has the procedure.
- **S0 must be package-neutral.** The one taken for P01 was not; it carried that
  package's own handoff and plan.

## Things that will bite a newcomer

- `/workspaces/vbpub` is shared with a concurrent committer. Commit scoped:
  `git add -- <paths> && git commit --only -F msg.txt -- <paths>`. Never
  `reset`/`rebase`/`--amend`.
- **The gate no longer runs from a source-tree `PYTHONPATH=src` shortcut
  (P14/A-130).** It builds and installs a real `assay` wheel inside
  `tester-unified` first, then runs `assay run tester-unified` through
  THAT wheel — read `nyxloom-trove/nyxloom.toml`'s own `[gates.
  tester-unified]` comment block in full before touching this script
  again; three non-obvious, empirically-found corrections
  (`--override-ini=pythonpath=`, a `.pth`-file site-injection instead of
  `--system-site-packages`, building via a fresh blank scratch venv's own
  pip) are documented there and are easy to silently break while editing
  the surrounding bash.
- The gate runs through `tools/tester-unified-gate.sh`. Its bind source is a
  **host** path, never the container path, but it is no longer hardcoded: the
  driver derives it from this devcontainer's `/workspaces/vbpub` Docker mount
  or reads explicit `ASSAY_GATE_HOST_REPO_ROOT`, then uses Docker `--mount` so
  absence fails rather than creating an empty directory. The final outer
  receipt marker is mandatory evidence in addition to exit zero.
- `/opt/tester-venv` exists only inside the container; there is no
  `setuptools_scm` in it, so built wheels version as `0.0.0` (A-069) —
  this is now load-bearing, not incidental: `assay verify`/the self-hosting
  proof both compare against this real, documented value. **The visible
  consequence: `tests/test_standalone.py::test_a_real_pass_matches_the_
  documented_r0_pass_shape` FAILS in the devcontainer and only there**
  (`0.1.0` vs `0.0.0`), because `setuptools_scm` IS importable here. It
  passes in the gate image. Do not read a bare `pytest` run's single red as
  a regression, and do not "fix" it.
- **A project root is not always its repository top, and assay's own is
  not** (A-145). `git diff` paths are repo-top-relative; source roots,
  coverage-artifact paths and each mutant's scratch copy are
  project-root-relative. Every boundary that crosses between the two must
  say which spelling it speaks — R2 shipped a crash for exactly this and
  no fixture could see it, because every fixture in the suite makes the two
  the same directory.
- There is **no Go toolchain** and none is needed — Go fixtures ship
  pre-generated (A-042).
- A hook blocks some scripted file edits. Use the editor, not `sed -i` or
  script-driven writes; a silent no-op reads as success.
- **Since P17, `assay run` refuses a dirty worktree — and the gate's own
  first step IS an `assay run`.** So uncommitted work in the worktree now
  makes the gate report `NO_MEASUREMENT`/`DIRTY_TREE` instead of running
  the suite. Commit before gating; a red gate whose only symptom is exit
  3 is almost always this, not a test failure.
- **A lane's declared `judge.coverage.artifact` must be git-ignored (or
  absent), A-140.** It is this run's own OUTPUT, so an unignored copy is
  untracked worktree state and the run is refused. assay used to delete
  it first and proceed; it no longer does, because that also deleted
  files that were not coverage artifacts at all. Every R1 fixture in the
  suite now commits a `.gitignore` for exactly this reason.
- `--verdict-json <path-whose-parent-is-unwritable>` currently raises a bare
  `OSError` and exits **1** — which a consumer reads as `FAIL`, not as a
  tooling error. A-O14 is no longer open: P21 adds pre-execution destination
  reservation and `OUTPUT_WRITE_FAILED`. Until P21 lands, do not point a CI
  job at an unverified destination.
