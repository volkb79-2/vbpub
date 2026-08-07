# assay — state of play

> Written 2026-08-07, updated the same day after P04 landed. Update this file
> at the end of every session; it is the first thing the next controller
> should read after `handoffs/README.md`.

## Where things stand

**Merged on `main`:** P00 (skeleton + lane config loader), P01 (verdict model +
JSON Schema), P01c (contract repair + the reissued series), **P02** (changed-line
extraction + the `DIRTY_TREE`/`BASE_IS_HEAD` measurability guards), **P03**
(coverage format registry — coverage.py JSON, lcov, Cobertura XML, Go
coverprofile — + the `EMPTY_COVERAGE` guard), **P04** (runner, `assay run` CLI
subcommand, R0 verdict emission). Gate green at **896 passed, exit 0, 100%
statement AND branch** (1169 stmts / 452 branches), independently reverified by
the controller in the foreground gate container on every package (not just
read from the implementer's report), plus a post-merge local run on `main`
itself each time.

**Outstanding:** P05–P14, ten packages, all validating against nyxloom's
real `handoff-frontmatter.schema.json` with a closed dependency graph.
**P05 is next** — *"do all four changed-line sets get judged without the core
knowing a source language?"*

Key commits: `c46b0bcb` (P04 merge), `fd7ae88e` (P04 controller repair,
pre-merge), `bfc467b8` (P04 readiness rulings, A-094/A-095), `e7c92988` (P03
merge), `e97d6e6f` (P03 readiness rulings, A-092/A-093), `89a489a0` (P02
merge), `04e72c9a` (P02 readiness rulings, A-090/A-091), `27fb88d7` (P01c +
reissue), `c1bb518d` (P00/P01 rename), `caf4fc78` (P01), `b2684da9` (P00),
`a0c9e515` (original scoping).

**P04's own diff needed a repair before merge, not just readiness-pass
rulings.** The implementer put "refuse any lane declaring rigor beyond R0"
directly in `cli.py`, and its own successor brief told P05 to go edit that
check — but `cli.py` is not in P05's `scope.touch`, so P05 would have hit
BLOCKED on day one. Caught in controller review (reading the actual diff, not
trusting the LOG's self-described "flagged for the controller" note alone),
relocated into `runner.py`'s `assemble_verdict` (already in every later
producer package's `scope.touch`), which now self-obsoletes the guard the
moment a later package supplies the missing claim — no file to find or touch
when that happens. Two new direct runner-level tests added; the successor
brief corrected. This is the first package needing an actual code repair
(P02/P03 only needed pre-implementation handoff rulings) — see WORKFLOW.md's
repair-vs-redispatch threshold; this qualified as "local" (small, the design
was right, only the file was wrong).

**Open, not blocking:** A-O14 (decisions.md) — `runner.write_verdict` has no
closed `ReasonCode` for "cannot write my own output artifact" (e.g. a missing
parent directory for `--verdict-json`); left as an uncaught `OSError` rather
than inventing a code alone (A-050). Low real-world severity, not fixed.

**Watched but not acted on:** P09, P10 and P12 all touch `runner.py` too (not
just P05). Their handoffs predate P04 and don't cite its actual function
names, the same shape of gap A-090/A-093/A-094 already caught three times at
the P0x→P05 seam. Deliberately NOT pre-emptively edited — P05 will extend
`runner.py` further before any of P09/P10/P12 is dispatched, and editing now
would guess at a shape that might change. Each package's own readiness pass
is the right, information-rich moment to catch this, exactly as it did for
P05 three times running; note it here so a future controller checks
deliberately rather than by luck.

**P02's readiness pass found a real carving gap** (git.py/measurability.py had
no downstream consumer scoped to call them) and fixed it *before* implementation:
A-090 assigns wiring P02's guards + P03's `EMPTY_COVERAGE` ahead of the R1
evaluation to P05, which gained an O4 for it. Reviewing the actual merged code
(not just the report) surfaced a second, P10-scoped trap: `git.run` raises on
ANY non-zero exit, but ancestor-checking git commands use exit codes as data
(`merge-base --is-ancestor` exit 1 means "no", not "broken") — flagged directly
in P10's handoff rather than left to be rediscovered at P10's own readiness
pass.

**P03's readiness pass found the SAME shape of gap again**, one level down:
A-091 (P02's dataclass/direct-`AssayError` convention) was worded around two
named functions rather than as a rule, and P03's own cited sibling
implementations all used the forbidden bare-dict/map shape — generalized by
A-092 into a project-wide rule so it doesn't need rediscovering at every
remaining package. A-093 required P03's `EMPTY_COVERAGE` guard
(`check_empty_coverage`) to be named and independently callable, matching
A-090's fix for P02 — confirmed on review to be exactly what P05's O4 needs,
with the exact call sequence given in `assay-P03-BRIEF.md`. **Pattern for
whoever reviews P04+:** check whether A-092's dataclass rule and the
"named-independently-callable-guard" shape need restating for each new
package, or whether two applications is now enough precedent that a reviewer
can just point to A-090/A-092/A-093 without minting a new decision each time.
lcov and Cobertura XML parsers were built with **zero prior art anywhere in
the estate** — reviewed directly against the public specs, not just trusted;
Cobertura's multi-`<class>`-per-file merge (executed wins on conflict) is the
implementer's own extrapolation from the DTD, untested against any real-world
sample. Low risk, worth remembering if a real Cobertura consumer ever
surfaces a case this parser gets wrong.

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
