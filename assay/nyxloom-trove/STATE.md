# assay — state of play

> Written 2026-08-07, updated the same day after P02 landed. Update this file
> at the end of every session; it is the first thing the next controller
> should read after `handoffs/README.md`.

## Where things stand

**Merged on `main`:** P00 (skeleton + lane config loader), P01 (verdict model +
JSON Schema), P01c (contract repair + the reissued series), **P02** (changed-line
extraction + the `DIRTY_TREE`/`BASE_IS_HEAD` measurability guards). Gate green at
**762 passed, exit 0, 100% statement AND branch** (777 stmts / 326 branches),
independently reverified by the controller in the foreground gate container
(not just read from the implementer's report), plus a post-merge local run on
`main` itself.

**Outstanding:** P03–P14, twelve packages, all validating against nyxloom's
real `handoff-frontmatter.schema.json` with a closed dependency graph.
**P03 is next** — *"is coverage format explicit and language-independent?"*

Key commits: `89a489a0` (P02 merge), `04e72c9a` (P02 readiness rulings,
A-090/A-091), `27fb88d7` (P01c + reissue), `c1bb518d` (P00/P01 rename),
`caf4fc78` (P01), `b2684da9` (P00), `a0c9e515` (original scoping).

**P02's readiness pass found a real carving gap** (git.py/measurability.py had
no downstream consumer scoped to call them) and fixed it *before* implementation:
A-090 assigns wiring P02's guards + P03's `EMPTY_COVERAGE` ahead of the R1
evaluation to P05, which gained an O4 for it. Reviewing the actual merged code
(not just the report) surfaced a second, P10-scoped trap: `git.run` raises on
ANY non-zero exit, but ancestor-checking git commands use exit codes as data
(`merge-base --is-ancestor` exit 1 means "no", not "broken") — flagged directly
in P10's handoff rather than left to be rediscovered at P10's own readiness
pass.

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

## Not done, and worth doing before or during P02

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
- Sol's own stated lowest confidence: **P08's conservative Go lexer**. It is
  fail-closed and falsifiable, but a thin fixture corpus would make it "correct
  only on toy Go". Weight the P08 review accordingly.
- Sol also flagged that **P10's ancestor/path staleness is contractual only**
  until that package lands — P01c froze the shape, not the git behaviour. Now
  concrete: P10's handoff carries the `git.run`/exit-code trap found while
  reviewing P02's merged code (see above).
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
