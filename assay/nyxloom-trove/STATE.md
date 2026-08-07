# assay — state of play

> Written 2026-08-07, updated the same day after P05 landed. Update this file
> at the end of every session; it is the first thing the next controller
> should read after `handoffs/README.md`.

## Where things stand

**Merged on `main`:** P00, P01 (skeleton, lane config, verdict model + schema),
**P02** (changed-line extraction + `DIRTY_TREE`/`BASE_IS_HEAD` guards), **P03**
(coverage format registry — coverage.py JSON, lcov, Cobertura XML, Go
coverprofile — + `EMPTY_COVERAGE` guard), **P04** (runner, `assay run` CLI,
R0 verdict emission), **P05** (adapter protocol, four-way union, registry, R1
verdict emission — the first real coverage-judged verdict), **P06** (the
Python `LanguageAdapter` — first real adapter, union of dstdns/topos/nyxloom).
Gate green at **986 passed, exit 0, 100% statement AND branch** (1363 stmts /
512 branches), independently reverified by the controller in the foreground
gate container on every package (not just read from the implementer's
report), plus a post-merge local run on `main` each time.

**Outstanding:** P07–P14, eight packages, all validating against nyxloom's real
`handoff-frontmatter.schema.json` with a closed dependency graph. **P07 is
next** — *"is a changed line never passed merely because its executable
statement spans multiple lines?"*

Key commits: `8e65b1c7` (P06 merge), `05ab843e` (P06 readiness rulings,
A-098/A-099), `291d6e30` (P05 merge), `0958efdf` (P05 readiness rulings,
A-096/A-097), `c46b0bcb` (P04 merge), `fd7ae88e` (P04 controller repair,
pre-merge), `bfc467b8` (P04 readiness rulings, A-094/A-095), `e7c92988` (P03
merge), `e97d6e6f` (P03 readiness rulings, A-092/A-093), `89a489a0` (P02
merge), `04e72c9a` (P02 readiness rulings, A-090/A-091), `27fb88d7` (P01c +
reissue), `c1bb518d` (P00/P01 rename), `caf4fc78` (P01), `b2684da9` (P00).

**The pattern is now well-established across five packages**: every
readiness pass so far has found at least one real gap (never zero), most
of them the same shape — a capability built with no package yet scoped to
consume it, or a decision (A-091→A-092, A-090→A-093→A-096) that needs
restating/generalizing as later packages independently rediscover it. P04
additionally needed a genuine controller REPAIR after implementation, not
just a pre-dispatch ruling — see git log for `fd7ae88e` if the mechanics
matter. Full narrative detail on P02–P04's individual findings lives in
`decisions.md`'s A-090 through A-095 entries and each package's merge commit
message; not repeated here to keep this file navigable.

**P05's readiness pass found a genuine BLOCKER**, the first of the series:
O3 required "missing locations" in the R1 claim, but `Coverage`/the schema's
`coverage` `$def` had no such field anywhere — traced through git history to
a schema `$comment` (naming `files_missing_coverage`, `excluded`,
`unclassified`) that the `9bd7d206` P01c repair deleted without adding a
replacement. A-096 pinned the shape (`missing_lines`, `files_missing_coverage`,
both always present); A-097 pinned the adapter protocol's exact eight-member
surface after auditing every later package's `scope.touch` (P06/P08 never
touch `adapters/base.py` — whatever P05 shipped is final until P07/P09/P11
extend it). Both confirmed correctly implemented on review — no repair needed
this time, unlike P04.

**Open, not blocking:** A-O14 (decisions.md) — `runner.write_verdict` has no
closed `ReasonCode` for "cannot write my own output artifact." Low severity,
not fixed.

**P07's readiness pass resolved a real vocabulary gap**, the one flagged
during P06's propagation: A-100 rules unattributable/overlapping/malformed
statement spans render `FAIL`/`UNCLASSIFIED_LINES` (already exists, unused),
never a new `INCONCLUSIVE` pairing — matching dstdns's own cited reference
behavior (its `unclassified` bucket is an unconditional FAIL, zero
"INCONCLUSIVE" occurrences anywhere in that file) and DESIGN-GUIDE §6's own
outcome semantics. A-101 pins `statement_spans`'s return shape (a new frozen
`StatementSpan` dataclass, not dstdns's bare tuple-list) — the protocol's one
deliberate post-P05 extension had no pinned shape anywhere before this.
**Correction to a readiness-pass claim**, caught by the controller rather
than trusted: the pass reported "no srdm checkout exists in this workspace"
because it searched for a directory literally named `srdm`; the real
checkout is `/workspaces/vbpub/shared-ramdisk-depot-manager` (already used
successfully for P03's own Go coverprofile parser). Checked directly:
`covergate/profile.go`'s `FileCoverage` is BLOCK-based (`[startLine,
endLine]` expanded to every line in range), so Go's coverprofile format
genuinely does NOT have Python's "interior line of a multi-line statement
vanishes" gap — confirms the pass's own inferred conclusion about P08 was
right, just under-evidenced.

**Watched but not acted on:**
- P09, P10, P12 touch `runner.py` and predate its current shape (now
  significantly larger after P05). Each package's own readiness pass is the
  right moment to catch a mismatch, as it has 4 times running for P05 itself.
- **Architectural note worth remembering, not a defect**: `cli.py` is only
  touched again by P14 among all remaining packages. `assay run`'s actual
  end-to-end CLI wiring across multiple rigor levels is therefore entirely
  P14's job — P05/P09/P12 each extend `runner.py`'s orchestration surface
  incrementally (as P05 did with `evaluate_r1`), and P14 wires a single,
  by-then-complete entry point into `cli.py`, rather than any earlier package
  touching the CLI. Confirmed intentional (assay's own `assay.toml` is
  explicitly R0-only "and P11 upgrades this file when the capability is real,
  not before" per its own comment — though the actual upgrade, given scope,
  can only really land in P14, which is the only package scoped for
  `assay.toml`). Worth double-checking again at P14's own readiness pass that
  `runner.py`'s accumulated surface by then is actually sufficient for one
  generic CLI entry point.
- **P07's own handoff already has a vocabulary gap, found while propagating
  P06**: O2 requires rendering `INCONCLUSIVE`/`UNCLASSIFIED`, but `errors.py`'s
  closed vocabulary has no such pairing — `UNCLASSIFIED_LINES` exists but is
  paired only with `FAIL`, and `INCONCLUSIVE` pairs only with `NO_MUTANTS`/
  `CANARY_INCONCLUSIVE`. Neither the code's exact name nor the outcome pairing
  P07's oracle names currently exists. This needs a real ruling (likely a new
  `ReasonCode` member, matching A-050/A-073/A-086's precedent for evolving the
  closed vocabulary) at P07's own readiness pass — flagged here so it isn't
  missed, not resolved in advance since it deserves full context.
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
