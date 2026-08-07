# assay — state of play

> Written 2026-08-07, updated the same day after P09 landed. Update this file
> at the end of every session; it is the first thing the next controller
> should read after `handoffs/README.md`.

## Where things stand

**Merged on `main`:** P00/P01 (skeleton, lane config, verdict model + schema),
**P02** (changed-line extraction + measurability guards), **P03** (coverage
format registry), **P04** (runner, `assay run` CLI, R0 verdict emission),
**P05** (adapter protocol, four-way union, R1 verdict emission), **P06**
(Python `LanguageAdapter`), **P07** (statement-span attribution), **P08** (Go
`LanguageAdapter` — last new-LANGUAGE package), **P09** (cause-sensitive
canary — real end-to-end proof that the gate rejects known-bad input for the
intended reason; Python's canary runs the FULL real R0+R1 pipeline twice
against real git commits, Go's proves R1 only via committed coverprofiles,
deliberately, since no Go toolchain exists here), **P10** (attested evidence
staleness — new `attestation.py`: loads a Tier-3 external review into the
already-reserved `Evidence` shape and proves only whether its declared
reviewed paths are current, never verifying review content; equal-or-ancestor
proven via `merge-base`, with every git-level failure on the externally
supplied attested commit — unrelated history, malformed ref, descendant —
caught and remapped to `ERROR`/`UNREADABLE_ARTIFACT` per A-110, never left to
propagate as `GIT_FAILED`; the declared `(source, key)` list is a direct
caller parameter per A-111, `config.py`/`assay.toml` untouched;
`runner.assemble_verdict` gained optional `evidence`/`declared_evidence`
parameters, defaulting to `()` so every prior caller is unaffected). Gate
green at **1241 passed, exit 0, 100% statement AND branch** (1917 stmts / 746
branches), independently reverified by the controller in the foreground gate
container on every package, plus a post-merge local run on `main` each time.
P10's own claimed mutation count for its highest-risk case (outer
`try`/`except` removed in `evaluate_attestation`, A-110's core promise) was
independently reproduced by the controller with a local spot-check mutation
— 7 failing, exact match. **P11** (valid mutant construction — new
`mutation.py`: frozen `Mutant` dataclass plus UTF-8-byte<->line arithmetic;
`generate_mutants` added as `LanguageAdapter`'s 7th and final method,
`tuple[Mutant, ...] | Literal["UNSUPPORTED"]`, whole-adapter-call level per
A-114; Python's real engine splices each mutation site's own byte span
against the ORIGINAL text — never `ast.unparse`'s whole-file reprint, which
would fail O1's byte-preservation oracle — and treats a boolean chain's
N-1 operator tokens as N-1 independent sites per A-115; Go is unconditional
`UNSUPPORTED`, no toolchain ever). Gate green at **1301 passed, exit 0, 100%
statement AND branch** (2064 stmts / 796 branches). The readiness pass for
P11 caught the same wrong-citation defect class a third time (srdm
`covergate/` cited for "mutation logic," zero occurrences — real prior art
is `nyxloom/src/nyxloom/mutation_gate.py`) plus a genuinely unpinned return
shape and a real technical trap (the reference implementation's
`ast.unparse` mechanism is fundamentally incompatible with this package's
own byte-preservation oracle) — all independently verified by the
controller before ruling (grep counts, live `ast` structural checks,
direct interactive exercise of `generate_mutants` against fresh inputs:
3- and 4-operand boolean chains, chained comparisons, non-ASCII byte
preservation, malformed-text `UNSUPPORTED`, empty-result-vs-`UNSUPPORTED`
distinction, Go's unconditional `UNSUPPORTED`).

**Outstanding:** P12–P14, three packages. **P12 is next** — *"do tests kill
valid changed-line mutants under a declared, deterministic execution
bound?"* (depends on P11 and P04, both merged). P12's handoff carries three
propagated citations now: P10's `assemble_verdict` signature, the corrected
mutation-execution prior art (`mutation_gate.py`'s `evaluate()`, A-113), and
P11's own successor brief (the exact `Mutant`/`generate_mutants` shape it
consumes, including the `() `-vs-`"UNSUPPORTED"` distinction it must handle
separately). P13/P14 need nothing new from P11's landing — P11 added zero
new runtime dependencies (stdlib only), and P11 attaches nothing to a
`Claim`/`Verdict`, so P14's "P04–P12" range already covers it without
special-casing.

Key commits: `f3f13580` (P11 merge), `887bae41` (P11 rulings,
A-112–A-115), `638b4a54` (P10 merge), `aa5b28c7` (P10 rulings, A-110/A-111),
`732d0dd4` (P09 merge), `23122f9b` (P09 rulings, A-105–A-109),
`fde78867` (P08 merge), `c6bb7aa6` (P08 rulings, A-102–A-104), `9ae93057`
(P07 merge), `9b9d38e8` (P07 controller repair), `90f9de44` (P07 rulings,
A-100/A-101), `8e65b1c7` (P06 merge), `05ab843e` (P06 rulings, A-098/A-099),
`291d6e30` (P05 merge), `0958efdf` (P05 rulings, A-096/A-097), `c46b0bcb`
(P04 merge), `fd7ae88e` (P04 controller repair), `bfc467b8` (P04 rulings,
A-094/A-095), `e7c92988` (P03 merge), `e97d6e6f` (P03 rulings, A-092/A-093),
`89a489a0` (P02 merge), `04e72c9a` (P02 rulings, A-090/A-091), `27fb88d7`
(P01c + reissue).

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

**Watched but not acted on:**
- P12 touches `runner.py`/`verdict.py` and now postdates both P10's
  extension of `assemble_verdict` (`evidence`/`declared_evidence`,
  identity-coverage guard) and P11's addition of `mutation.py`/`Mutant` —
  propagated as citations in P12's own handoff this session; its own
  readiness pass is still the right moment to catch anything these notes
  missed, especially the new R2 `Claim.mutation` payload P12 must design
  (no shape pinned yet, unlike P09/P10's own R1/R3 payloads — P12's own
  readiness pass is where that gets pinned, matching A-101/A-108/A-114's
  precedent).
- `cli.py` is only touched again by P14 among all remaining packages — full
  `assay run` CLI wiring across rigor levels is entirely P14's job by
  design, not a defect (confirmed intentional: assay's own `assay.toml` is
  deliberately R0-only). Worth double-checking at P14's own readiness pass
  that the accumulated `runner.py` surface is sufficient by then.
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
