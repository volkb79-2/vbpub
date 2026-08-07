# assay — state of play

> **P00–P15 ARE COMPLETE AND MERGED. THE PRODUCT IS NOT YET SAFE TO ADOPT.**
> Written 2026-08-07, updated the same day after a post-series adversarial
> review (`nyxloom-trove/reports/assay-v1-post-series-review-sol.md`) found
> that "gate green" proved the library components, not the executable
> product: no rigor above R0 is reachable through `assay run` at all, and
> several confirmed defects (some live in the R0 path shipped today) exist
> in code every package's own review had already passed. **Read that review
> file before trusting anything else in this document as current** — the
> P15–P25 repair series is carved directly against its findings and is now
> being implemented, **P15 merged, P16 next** (see "Where things stand"
> below for live status). If you are picking this project up: read the
> review, then `nyxloom-trove/reports/assay-P14-BRIEF.md`
> (the P14-era final-state brief — still accurate for what P00–P14 built,
> just not for whether it's ready to depend on), then this file, then
> `decisions.md`.

## Where things stand

**Merged on `main`, P00 through P15** — the complete P00–P14 series, plus
the first package of the P15–P25 repair series. Gate green at **1585
passed, 1 skipped, exit 0, 100% statement AND branch coverage** (2531
stmts / 988 branches), run through the REAL self-hosting mechanism P14
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

Key commits: `326507f1` (P15 merge), `a8357405` (P15 rulings, A-134–A-135),
`2ab50f75` (P14 merge, the P00–P14 series-closing commit), `56c821c2`
(P14 rulings, A-128–A-133), `bca3c345` (P13 merge), `a42fe02a` (P13
rulings, A-123–A-127), `fa65dd58` (P12 merge), `f828d14e` (P12 rulings,
A-116–A-122). Full history for every package in `decisions.md` (A-090
through A-135) and each merge commit message — not repeated here.

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

**Carving status (live, as of this update): P15 through P25 are carved,
committed, and controller-verified. P26 (TypeScript adapter) and P27
(the `dstdns`-side adoption package) are NOT yet carved.**

**Implementation status of that series: P15 is MERGED. P16 is next**, and
its handoff already carries the two rulings P15 produced (A-134/A-135) in
a "Carried in from P15" section — read it, the second one changes how P16's
own contradictory-artifact fixtures must be built. P17–P25 are untouched.
Nothing in P15's outcome invalidated any later handoff: the only
cross-package consequence found was A-135, and it is landed.

Sol was given write access (scoped by prompt, not sandbox, to new files
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

**The pattern across the whole series**: every single readiness pass found
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
discipline; several needed controller repairs or corrected rulings before
they did.

**Open, not blocking:** A-O14 (decisions.md) — `runner.write_verdict` has no
closed `ReasonCode` for "cannot write my own output artifact." Low severity.

**A-O15, new (open questions table): `attestation._changed_paths` has the
exact pair of defects A-134 just closed everywhere else.** It reads `git
diff --name-only` and splits with `str.splitlines()`, undoing neither of
git's two path spellings. Reproduced against a real repository during
P15's review, not inferred: `weird\nname.py` comes back as the literal
quoted spelling `'"weird\nname.py"'`, and `sep<U+2028>name.py` comes back
as two phantom entries `'sep'` and `'name.py'` with the real identity
gone. Deliberately NOT fixed in P15 — `attestation.py` is in that
package's `scope.forbid`, and improvising past a forbid is what the
BLOCKED rule exists to prevent. Pre-existing since P10, unchanged by P15,
unreachable without an adversarially-named file. P20 (attestation
hardening) is the natural home but its handoff does not name it; decide
before dispatching P20.

**Accepted, permanent debt — recorded here rather than silently dropped,
since there is no more series left to fold any of it into:**
- Three `(outcome, reason_code)` pairs in the closed 19-pair vocabulary are
  structurally unreachable as complete `Verdict` artifacts and are not
  fixtured: `ERROR`/`GIT_FAILED`, and claim-level `ERROR`/`FORMAT_MISMATCH`
  and `ERROR`/`UNREADABLE_ARTIFACT` (the evidence-level `UNREADABLE_ARTIFACT`
  pair IS reachable and IS fixtured, P14). All three propagate uncaught out
  of `evaluate_r1`/`cli.py`'s own top-level handler before any `Verdict` is
  ever constructed — documented, deliberate, pre-existing behavior (A-128),
  not a gap any future package is expected to close without a real design
  decision first.
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

## How the work was run

The loop is over — this section is now a record of the process for
whoever runs a similarly-shaped effort next, not a live instruction set.

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
2. **Ratifications batch** — `decisions.md` is read inside the orientation
   snapshot, so editing it invalidates the snapshot. Accumulate rulings, apply
   them at a deliberate rebuild point.
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

- **The snapshot lineage was considered for P02 and deliberately NOT built.**
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
- The gate runs in Docker and its bind mount uses the **host** path
  (`/home/vb/volkb79-2/vbpub`), not the container path.
- `/opt/tester-venv` exists only inside the container; there is no
  `setuptools_scm` in it, so built wheels version as `0.0.0` (A-069) —
  this is now load-bearing, not incidental: `assay verify`/the self-hosting
  proof both compare against this real, documented value.
- There is **no Go toolchain** and none is needed — Go fixtures ship
  pre-generated (A-042).
- A hook blocks some scripted file edits. Use the editor, not `sed -i` or
  script-driven writes; a silent no-op reads as success.
