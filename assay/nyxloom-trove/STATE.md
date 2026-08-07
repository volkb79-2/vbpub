# assay — state of play

> Written 2026-08-07, updated the same day after P08 landed. Update this file
> at the end of every session; it is the first thing the next controller
> should read after `handoffs/README.md`.

## Where things stand

**Merged on `main`:** P00/P01 (skeleton, lane config, verdict model + schema),
**P02** (changed-line extraction + measurability guards), **P03** (coverage
format registry: coverage.py JSON, lcov, Cobertura XML, Go coverprofile),
**P04** (runner, `assay run` CLI, R0 verdict emission), **P05** (adapter
protocol, four-way union, registry, R1 verdict emission), **P06** (Python
`LanguageAdapter`), **P07** (statement-span attribution — P07's own
extension of the protocol), **P08** (Go `LanguageAdapter` — the second and
last new-LANGUAGE package; both adapters now exist). **Correction to a
phrase in this file's own prior revision**: "`adapters/base.py` is frozen
for good" was an overreach, trusting P07's/P08's own successor-brief wording
("frozen," "final shape") too literally rather than checking it against
A-084's actual text, which explicitly names THREE anticipated protocol
extensions — statement-span (P07), canary (P09), mutation (P11) — each
"only in the package that first proves the need." P09's own `scope.touch`
correctly includes `adapters/base.py`; what's actually frozen after P08 is
only the set of packages allowed to add a NEW LANGUAGE, not the protocol
surface itself. Gate green at **1110 passed, exit 0, 100% statement AND
branch** (1626 stmts / 640 branches), independently reverified by the
controller in the foreground gate container on every package, plus a
post-merge local run on `main` each time.

**Outstanding:** P09–P14, six packages. **P09 is next** — *"does the whole
gate reject valid known-bad input for the intended cause?"* (cause-sensitive
canary — depends on P04 and P08, touches both adapters again to add
`inject_import_break`/`inject_uncovered_line`).

Key commits: `fde78867` (P08 merge), `c6bb7aa6` (P08 rulings, A-102/A-103/
A-104), `9ae93057` (P07 merge), `9b9d38e8` (P07 controller repair), `90f9de44`
(P07 rulings, A-100/A-101), `8e65b1c7` (P06 merge), `05ab843e` (P06 rulings,
A-098/A-099), `291d6e30` (P05 merge), `0958efdf` (P05 rulings, A-096/A-097),
`c46b0bcb` (P04 merge), `fd7ae88e` (P04 controller repair), `bfc467b8` (P04
rulings, A-094/A-095), `e7c92988` (P03 merge), `e97d6e6f` (P03 rulings,
A-092/A-093), `89a489a0` (P02 merge), `04e72c9a` (P02 rulings, A-090/A-091),
`27fb88d7` (P01c + reissue).

**The pattern across all eight packages so far**: every readiness pass has
found at least one real gap, never zero. Most are the same shape — a
capability built with no package yet scoped to consume it (the `runner.py`
seam: A-090→A-093→A-096; the adapter-protocol seam: A-097→A-101→A-102), or a
decision that needs generalizing as later packages rediscover it
(A-091→A-092). Two packages (P04, P07) additionally needed a genuine
controller REPAIR after implementation — both the same shape: real code the
implementer correctly identified as outside their own `scope.touch`, left as
a documented gap, closed by the controller because leaving it would have
persisted with no later package guaranteed to touch that file either. P08
(the project's own flagged lowest-confidence package) needed neither a
repair nor further rulings — reviewed with extra care (hand-traced several
adversarial lexer cases myself) and held up. Full detail on each package's
specific findings lives in `decisions.md` (A-090 through A-104) and each
merge commit message; not repeated here.

**P09/P11 propagation note**: P08's own successor brief for them is
unusually thorough (exact current `GoAdapter` shape, a proven-correct
"function body start" anchor point to reuse for `inject_uncovered_line`, and
three explicitly named known-limitations). No further handoff edits made —
P08 is the last new-adapter package, so nothing else needs the adapter
shape pinned down further; P09/P11's own readiness passes will read the
brief directly.

**Open, not blocking:** A-O14 (decisions.md) — `runner.write_verdict` has no
closed `ReasonCode` for "cannot write my own output artifact." Low severity.

**Watched but not acted on:**
- **P08's own O2 may be unsatisfiable as worded, found while propagating
  P07**: it requires "an unknown Go syntax region becomes UNCLASSIFIED", but
  `UNCLASSIFIED_LINES` (A-100) is only reachable through span-attribution
  (rule 3b in `evaluate.py`), which only runs when an adapter declares
  `requires_span_attribution=True` — and P07's own successor brief correctly
  recommends Go declare it `False`, since Go's coverprofile format is
  block-based and structurally cannot have Python's gap (verified directly
  against `covergate/profile.go`). If Go follows that correct recommendation,
  rule 3b never fires for it, and O2's literal text becomes undeliverable.
  Needs resolving at P08's own readiness pass: either O2's wording is loose
  (means "has_executable_code's fail-closed True", a different mechanism) or
  something about Go genuinely needs a second path to UNCLASSIFIED that
  isn't span attribution. Not resolved here — flagged so it isn't missed.
- P09, P10, P12 touch `runner.py` and predate its current (much larger)
  shape. Each package's own readiness pass is the right moment to catch a
  mismatch, as it has repeatedly for the `runner.py` seam so far.
- `cli.py` is only touched again by P14 among all remaining packages —
  full `assay run` CLI wiring across rigor levels is entirely P14's job by
  design, not a defect. P05/P07/P09/P12 each extend `runner.py`'s
  orchestration surface incrementally; P14 wires one generic entry point.
  Confirmed intentional (assay's own `assay.toml` is deliberately R0-only).
  Worth double-checking at P14's own readiness pass that the accumulated
  `runner.py` surface is actually sufficient by then.
- Sol's own stated lowest confidence: **P08's conservative Go lexer** — a
  thin fixture corpus would make it "correct only on toy Go." Weight P08's
  review accordingly.
- lcov/Cobertura parsers (P03) have zero prior art anywhere in the estate;
  Cobertura's multi-`<class>`-per-file merge is untested against any
  real-world sample. Low risk.

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
- **P10's handoff already carries a `git.run`/exit-code trap** found while
  reviewing P02's merged code: `run` raises on ANY non-zero exit, but
  ancestor-checking git commands (`merge-base --is-ancestor`) use exit code
  as data. Landed directly in P10's handoff already — nothing further to do
  until P10 is dispatched.
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
