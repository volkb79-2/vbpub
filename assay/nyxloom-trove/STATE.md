# assay — state of play

> Written 2026-08-07, updated the same day after P09 landed. Update this file
> at the end of every session; it is the first thing the next controller
> should read after `handoffs/README.md`.

## Where things stand

**Merged on `main`, P00 through P13** — thirteen packages, gate green
throughout, each independently reverified by the controller in the
foreground gate container (and post-merge on `main`) rather than trusted
from the implementer's own report. **Only P14 remains.**

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
- **P12** (bounded mutation execution — the R2 producer): baseline-gated
  (a red baseline stops before any mutant), `jobs`-bounded via an injected
  executor factory (never `os.cpu_count()`, A-122), per-mutant
  `shutil.copytree` isolation (never in-place, never a git worktree,
  A-120) so the shared source is unchanged BY CONSTRUCTION, four
  outcome buckets (killed/survived/crashed/budget_exceeded, A-116/A-117).
  New `Mutation`/`MutantOutcome` payload types in `verdict.py`,
  `Claim.mutation` gated to R2, a third schema `allOf` branch.
  `assemble_verdict` gained `mutation_claim`. A real circular import
  (`mutation -> runner -> adapters.base -> mutation`) was found and fixed
  with a deferred import — independently verified safe under every entry
  order by the controller. The controller also independently drove
  `run_mutation` directly (not just the test suite) against a fresh copy
  of the real fixture: confirmed a red baseline creates zero scratch
  dirs, `jobs=1`/`jobs=3` render byte-identical results under real
  concurrency, and the shared tree survives a real six-mutant run
  byte-unchanged.
- **P13** (standalone wheel proof): one new test module,
  `tests/test_standalone.py`, reusing `conftest.py`'s already-existing
  `standalone` fixture (A-123) — no new build/install mechanism, no
  `src/assay` change at all (confirmed by empty diff). Proved a real
  `assay run` through the INSTALLED console script emits a genuine R0
  verdict; a real Python fixture passes through the full pipeline; the
  Go adapter ships and works, adapter-level only (A-126, no Go toolchain
  ever). The readiness pass found and the controller independently
  reproduced (two real wheel builds inside the gate image, with and
  without `fallback_version`) that O1's original fallback-version
  negative was UNFALSIFIABLE in this gate image — `setuptools_scm` is
  absent from every interpreter there, so removing the declaration
  changes nothing observable. O1 was corrected (A-124) rather than
  shipping a silently-vacuous test.

Gate green at **1414 passed, exit 0, 100% statement AND branch** (2234
stmts / 874 branches — unchanged from P12's own totals, since P13 touches
no `src/assay` code).

**Outstanding: P14 only — the final package.** *"assay can gate itself
without becoming the only witness to its own correctness"* (depends on
P13, merged). P14's handoff carries a propagated citation to P13's own
successor brief this session: the `assay_version == "0.0.0"` gate-image
gotcha (exclude/normalize it in any artifact comparison), the exact `assay
run` CLI shape, and confirmation that A-125's `collect_ignore_glob` trap
applies to P14 too (`tests/conftest.py` is not in P14's `scope.touch`
either) — though P14 most likely never needs a new committed fixture
project at all, since O3 is about running assay's OWN existing test suite
through its own already-declared lane, not a synthetic one.

Key commits: `bca3c345` (P13 merge), `a42fe02a` (P13 rulings,
A-123–A-127), `fa65dd58` (P12 merge), `f828d14e` (P12 rulings,
A-116–A-122). Full history for P02–P11 in `decisions.md` (A-090–A-115)
and their own merge commit messages; not repeated here now that thirteen
packages are in — this file's own job is "where things stand," not a full
changelog.

**The pattern across all nine packages so far**: every readiness pass has
found at least one real gap, never zero — most either a capability built
with no package yet scoped to consume it, a wrong/stale citation (P09's
"canary" pointer had zero occurrences of the word; the REAL prior art was
found elsewhere), or a decision needing generalizing as later packages
rediscover it. Three packages (P04, P07, and effectively P09 via its own
citation fix) needed the controller to correct something the implementer
either couldn't reach (scope) or wasn't told correctly. P08 (flagged lowest-
confidence) and P09 (largest, most architecturally involved) both needed
neither repair nor further rulings once dispatched with a corrected handoff
— reviewed with extra care each time and held up. One self-correction this
session: an earlier claim in this file ("adapter protocol frozen for good")
was itself wrong and had to be fixed before it misled P09's own dispatch —
recorded as a reminder to verify past controller claims here too, not only
implementer/readiness-pass claims. Full detail on each package's specific
findings lives in `decisions.md` (A-090 through A-109) and each merge commit
message; not repeated here.

**Open, not blocking:** A-O14 (decisions.md) — `runner.write_verdict` has no
closed `ReasonCode` for "cannot write my own output artifact." Low severity.

**Watched but not acted on — all of it is now P14's own concern, being the
last package:**
- **Two deliberate wiring gaps P12 left**: nothing builds real
  `MutationTarget`s from a real diff (P12 owns execution only); nothing
  reads `assay.toml`'s `judge.mutation` table (`jobs`/`operators` stay
  opaque, A-121). P14's `scope.touch` has no `mutation.py`/`config.py`
  either — matching how attested evidence's own `assay.toml` wiring was
  also left deliberately unbuilt by this whole v1 series. If P14's own
  readiness pass concludes either gap must close to satisfy its oracles,
  that is a real, reportable escalation (`escalate_if`'s own "a producer
  outcome cannot be represented..." is adjacent but not identical) — not
  something to quietly route around.
- `cli.py` is only touched again by P14 among all remaining packages — full
  `assay run` CLI wiring across rigor levels is entirely P14's job by
  design, not a defect (confirmed intentional: assay's own `assay.toml` is
  deliberately R0-only). This is now the moment to confirm the
  accumulated `runner.py`/`assemble_verdict` surface (five optional
  parameters after P09/P10/P12: `evidence`, `declared_evidence`,
  `mutation_claim`, plus the base `claims`) is sufficient for P14's own
  self-hosting lane.
- lcov/Cobertura parsers (P03) have zero prior art anywhere in the estate;
  Cobertura's multi-`<class>`-per-file merge is untested against any
  real-world sample. Low risk, unlikely to matter for self-hosting (assay
  gates itself, a Python project, not a Go/lcov consumer).
- **P00/P01's handoffs still fail nyxloom's linter** (see "Longer-lived
  notes," below) — STATE.md has flagged folding this into P14's own audit
  since P02. This is the last chance to do that before the series ends;
  otherwise it should be named explicitly as accepted, permanent debt
  rather than silently dropped.

## How the work is run

`WORKFLOW.md` is the loop. `MEASUREMENTS.md` is what it costs and what it
catches — read both; several of their claims are corrections of earlier claims
that were wrong, and the corrections are the useful part.

Per package: **readiness dispatch** (orient, report, implement nothing) →
controller rules on every raised item **in the go-message** → implement →
self-review → successor brief → controller reviews, repairs, merges → controller
asks what the result changes about *remaining* handoffs.

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
- `019fd977-f091-7dd1-8af5-38c41db89507` — the review → guidance → repair thread.
- Invoke: `codex exec --sandbox read-only -m gpt-5.6-sol -c model_reasoning_effort=high --cd <dir> resume <id> "<prompt>"`, and **run it via `run_in_background` with NO `nohup`/`&`** or the completion notification is spurious.

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
- **`assay verify` / R3** is recorded as `assay verify --lane X` producing R3
  *about* lane X — deliberately not something `assay run` performs recursively.
  P14 owns it.
- **P00/P01's handoffs fail nyxloom's linter** (`L7`/`L11`/`L12`/`L13` findings —
  missing BLOCKED-marker/branch-name mentions, an oracle referencing a path
  outside `scope.touch`, unresolved cross-repo globs). Found running A-089's
  check for P02; harmless since both packages are already merged and
  gate-green, but real debt on historical planning docs. P02–P14 are all
  clean. Not fixed — out of scope for any current package; sweep separately
  or fold into P14's own audit.

## Things that will bite a newcomer

- `/workspaces/vbpub` is shared with a concurrent committer. Commit scoped:
  `git add -- <paths> && git commit --only -F msg.txt -- <paths>`. Never
  `reset`/`rebase`/`--amend`.
- The gate runs in Docker and its bind mount uses the **host** path
  (`/home/vb/volkb79-2/vbpub`), not the container path.
- `/opt/tester-venv` exists only inside the container; there is no
  `setuptools_scm` in it, so built wheels version as `0.0.0` (A-069).
- There is **no Go toolchain** and none is needed — Go fixtures ship
  pre-generated (A-042).
- A hook blocks some scripted file edits. Use the editor, not `sed -i` or
  script-driven writes; a silent no-op reads as success.
