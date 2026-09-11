# assay

**How to judge a change: declared lanes, declared rigor, one machine-readable verdict.**

assay is a standalone testing-rigor library. It answers one question about a
commit — "is this change actually tested?" — at whatever rigor level you
declare, and emits a single JSON verdict a CI system can gate on without
linking against assay itself.

- **License / distribution:** estate-internal; distributed as local wheels
  (see [Installing](#installing)), not published to a public index.
- **Status:** Python is fully supported (R0–R3). SQL/DDL mutation testing is
  supported at **R2 only** (no SQL R1, no SQL R3 — see
  [SQL/DDL mutation testing](#sqlddl-mutation-testing-r2-only) below).
  JavaScript/TypeScript is supported at **R1 and R2** — changed-line coverage
  plus mutation testing over externally-ingested evidence (no native JS mutant
  generator yet), see
  [JavaScript/TypeScript changed-line coverage](#javascripttypescript-changed-line-coverage-r1-only)
  below. Go is supported at **R1 only** — changed-line coverage for `.go`,
  statement-granular, requiring a real Go toolchain on the judging machine;
  see the Go section below. **Full matrix, and what R0–R3 each actually mean:
  [Rigor levels, and what's registered where](#rigor-levels-and-whats-registered-where).**

---

## Why use it

Test suites lie in a specific, boring, recurring way: CI is green, coverage
looks fine in aggregate, and the change that actually mattered was never
exercised. Usually because coverage was measured over the whole project
instead of the diff, or because "100% coverage" meant every line *ran*, not
that any assertion would have caught a wrong version of it.

assay exists to close that gap mechanically, not by policy:

- **You choose which question the judged tiers ask: changed-line, or
  whole-target.** `judge.mode` is a lane-level scope that R1 and R2 read
  together (A-325): under `whole_target`, R1 asserts its floor over the
  declared files and R2 mutates those files whole rather than scoping
  mutation to a diff — which is why `judge.base` is refused on such a lane
  and absent from its verdict. In
  `mode = "changed_lines"` (the default — an existing lane needs no edit), a
  diff that touches 12 lines is judged on those 12 lines; a 0/0 result
  (nothing measurable changed) is reported honestly, with a `considered`
  count, instead of silently reading as 100%. In `mode = "whole_target"` a
  lane instead asserts a coverage floor over one or more explicitly declared
  **files**, independent of any diff — the mode a reconciliation program needs
  when a method can be "fixed" by editing only its docstring, changing zero
  executable lines. One lane declares one mode; a consumer wanting both
  declares two lanes. See
  [§6, two R1 modes, one claim per lane](docs/DESIGN-GUIDE.md#two-r1-modes-one-claim-per-lane-a-260).
- **Branch coverage is judged whenever the artifact reports it — not
  opt-in.** A changed line that is a branch source with an untaken arc lowers
  the reported percentage in *every* lane whose coverage artifact carries
  branch data, including a lane that declared R1 before this shipped. `pct` is
  the combined line+branch percentage the moment branches are present. Declare
  `judge.require_branch = true` to refuse (`NO_MEASUREMENT`/
  `BRANCH_UNAVAILABLE`) rather than silently fall back to line-only judging
  when the artifact's format or argv can't produce branch data — the guard
  against an argv edit quietly downgrading a gate's rigor. See
  [§6, branch coverage is judged whenever reported](docs/DESIGN-GUIDE.md#branch-coverage-is-judged-whenever-the-artifact-reports-it-a-258)
  and
  [§6, `require_branch` governs absence](docs/DESIGN-GUIDE.md#require_branch-governs-absence-never-presence-a-259).
- **Every R1/R2/R3 lane declares its snapshot selection.** `[lanes.X.isolation]`
  is required the moment a lane declares R1, R2 or R3 (and refused on an
  R0-only lane): `snapshot_selection = "repository"` materialises the whole
  commit, or `"repository-minus-unsafe-symlinks"` additionally omits exactly
  the declared, commit-validated unsafe symlink leaves that would otherwise
  refuse the lane. The exact property, stated once and never paraphrased
  stronger: *for each higher-rigor unit using omission mode, assay initially
  hands the command a private worktree in which every declared,
  commit-validated P22-unsafe symlink is absent and every other P22-supported
  tracked path from the resolved commit is materialised.* See
  [§6, snapshot selection](docs/DESIGN-GUIDE.md#snapshot-selection-an-affirmative-materialisation-boundary-not-a-sandbox-b006a).
- **An escalating rigor ladder (R0–R3)**, so "tested" means something
  specific instead of one undifferentiated green checkmark:
  - **R0** — the declared command ran and produced a result.
  - **R1** — every changed, executable line was exercised.
  - **R2** — targeted mutation testing proves those lines are actually
    *asserted on*, not merely executed.
  - **R3** — a canary (a deliberately broken version of the code) proves the
    whole gate — not just one test — rejects a known-bad change for the
    right reason.
- **One evidence model that doesn't let weaker evidence pass as strong.**
  Computed results (R0–R3, deterministic, assay's own), adjudicated results
  (a declared third-party tool's structured output against a declared
  threshold), and attested results (external review, ledgered and checked
  for staleness, never verified) are three distinct, clearly labeled tiers —
  see [§3 of the design guide](docs/DESIGN-GUIDE.md#3-the-three-tiers-of-evidence).
  A stale or missing review can never quietly read as "this was checked."
- **Zero runtime dependencies.** assay imports nothing but the Python
  standard library. It consumes the *output* of tools like `coverage.py`; it
  never imports them. Adoption risk is close to zero — there is no
  dependency tree to audit.
- **One schema, not four divergent copies.** assay's whole reason for
  existing is that this changed-line-coverage logic had been independently
  reimplemented, and had independently diverged, in four different places
  across this estate before assay unified it. See
  [§2 of the design guide](docs/DESIGN-GUIDE.md#2-why-it-exists-four-copies-and-each-one-is-the-sole-holder-of-something)
  for the receipts.

**Compatibility, read before upgrading.** The verdict artifact is schema
`VERDICT_SCHEMA_VERSION = 8` and the lane file is `LANE_SCHEMA_VERSION = 2`.
Both are hard cuts: `assay verify` refuses a v7 verdict exactly as it refuses
v6 today (no dual-version verifier, no upgrade-in-place), and a v2 assay
refuses a v1 `assay.toml`'s `[isolation]`-less R1+ lane while a v1-pinned
assay cannot parse a v2 file's `[isolation]` table at all. Repin the release
and bump `schema_version` **in the same commit** — see
[the consumer guide's ordered adoption step](docs/CONSUMERS.md#adopting-a-v2-capable-release)
for why the order matters and what breaks if you split it across two commits.

## What assay is, and is not

**assay judges. It does not choose what to run, and it does not choose
where to run it.**

- A project's own `assay.toml` **lane** declares *what* exists: which rigor
  levels it claims, what command to run, what coverage/mutation/canary
  configuration backs that claim.
- A lane may also declare an optional top-level `environment_command`. When
  present, `assay run` executes that zero-exit probe in the *invoking*
  environment before repository or snapshot work and refuses with
  `ERROR`/`BAD_LANE_CONFIG` when it fails; the lane command itself still runs
  in the environment assay is given. This makes a wrong dependency closure
  loud instead of surfacing as an unrelated test import failure.
- An environment tool (in this estate, `ciu` or `run-gate.py`) decides
  *where* — which container, which host. The probe names a fact about that
  environment; it does not give assay container orchestration.
- assay decides *how to judge* the result of running that one declared
  command, against exactly the rigor the lane declares. It never invents a
  rigor level a lane didn't ask for, and it never silently claims a
  capability the adapter can't back up — see
  [§0, the one invariant](docs/DESIGN-GUIDE.md#0-the-one-invariant).

### Rigor levels, and what's registered where

Declaring `rigor = ["R0","R1"]` on a lane does not mean "R1, but stricter
than R0" — each level asks a **structurally different question** about the
same execution's evidence, and the verdict carries one independent claim per
declared level:

| Rigor | Question it answers | What it needs beyond the level below |
|---|---|---|
| **R0** | Did the declared command succeed? | Nothing — reads the wrapped `argv`'s exit code by default, or (B078) a **verified-complete test report** in a registered format (`vitest-json` today) when the framework's own machinery can set a misleading exit code. No language adapter is resolved for R0 at all: it is the one fully language-agnostic layer, available for any command in any language, including ones assay has no adapter for. |
| **R1** | Of the code judged, how much did that execution's tests actually exercise? | A coverage profile the same command produced as a side effect (an extra flag, e.g. `-coverprofile=...` or `--cov-report=json`), plus a language-specific reader. Go additionally needs a real Go toolchain on the judging machine (a statement-position oracle — see below). |
| **R2** | If a bug were deliberately introduced into the judged code, would the suite fail? | A language-specific mutant generator (or, for JS, externally-ingested mutation evidence judged rather than produced), and re-running the suite once per candidate. |
| **R3** | Does behavior hold across an independently-mutated scratch copy (canary/parity)? | Two independently-owned scratch copies of the repository; the consumer's own worktree is never touched. |

R1 has two independent axes, and support differs per axis:

**Scope — which lines get judged.** The default, `judge.mode =
"changed_lines"`, judges `base..HEAD`'s diff. `judge.mode = "whole_target"`
(B005) instead judges a **declared file list**, independent of any diff — so
a lane can assert "this module stays fully covered regardless of what the
diff touches," which a changed-line floor cannot express (a docstring-only
edit changes zero executable lines). Both modes produce the identical
structured `coverage` claim and both honor `fail_under`; `whole_target` is
registered and generically implemented for every R1 language, though its own
qualification-by-real-consumer to date is Python-specific (`CONSUMERS.md`'s
`redirect_chain` example).

**Line vs. branch coverage.** Branch capability is derived from the coverage
*artifact's format*, never hardcoded per language
(`assay.coverage.derive_branch_capability`) — a format that can express
branch arcs (`coverage-py-json`, Istanbul, Cobertura, LCOV) reports real
`branches_covered`/`branches_total`; `go-cover` cannot, structurally, because
`go test -coverprofile`'s own file format carries no branch information for
any consumer to parse (the parser hardcodes `branches=None` unconditionally).
This is **not an assay gap** — it is upstream, in the Go toolchain itself:
`go tool cover`'s own modes are `set`/`count`/`atomic`, all statement-level;
there is no branch mode to read from in the first place. Where branches
*are* reportable, `fail_under` is not line-only: it is a single **pooled**
percentage over line and branch counts together
(`100 * (covered + branches_covered) / (executable + branches_total)`), with
a distinct `UNCOVERED_BRANCHES` reason code (vs. `UNCOVERED_LINES`) when a
floor is missed purely on branches — so "enforce line *and* branch coverage
on this module" is `mode = "whole_target"` + `require_branch = true` +
`fail_under`, already shipped, not a gap to design around.

#### The matrix

| Language | R0 | R1 — line coverage | R1 — branch coverage | R2 — mutation | R3 — canary/parity |
|---|---|---|---|---|---|
| *(any)* | ✅ language-agnostic | — | — | — | — |
| **Python** | ✅ | ✅ registered, both scope modes | ✅ **available** — real `coverage.py` branch arcs | ✅ registered, native mutant generator | ✅ registered |
| **JavaScript/TypeScript** | ✅ | ✅ registered, both scope modes | ✅ **available** — real Istanbul/nyc branch arcs (declare `producer = "istanbul"`; `@vitest/coverage-v8`/`c8` report ranges, not per-arm arcs, and stay `"unavailable"` without it) | ✅ registered, **ingested path only** — assay judges mutation evidence an external producer already generated; no native JS mutant generator | ⚠️ **implemented, not registered** — the canary injection methods are real code, not stubs, but no producer path is wired into the CLI's closed registry yet |
| **Go** | ✅ | ✅ registered, both scope modes — needs a real `go` toolchain on the judge (`external_tools = ("go",)`); statement positions are re-derived from source, never trusted from the profile alone (A-217) | ❌ **structurally impossible** — `go-cover`'s own format has no branch concept; no engineering investment inside assay changes this without Go's own coverage instrumentation gaining one | ❌ **not implemented** — `generate_mutation_sites` is unconditionally `UNSUPPORTED`; other Go-ecosystem tools (e.g. `go-mutesting`) prove this is possible in principle, assay just hasn't built it | ❌ not registered |
| **SQL/DDL** | ✅ | ❌ **not registered** — SQL's only rigor entry is R2 | — (moot) | ✅ registered — SQL's only rigor level | ❌ not registered |

Three genuinely different states, worth keeping distinct:

- **Structurally impossible** — the input data does not exist upstream of
  assay (Go branch coverage). No engineering investment inside assay fixes
  this.
- **Not implemented** — nothing prevents it; nobody has built it yet (Go
  mutation testing, SQL line coverage, Go/SQL canary).
- **Implemented but unregistered** — the code exists and presumably works,
  but is not wired as a callable capability through the CLI's own closed
  declaration (JS canary).

Source for every claim above: `src/assay/cli.py`'s `_built_in_registry()`
(the single authority for what's registered — its own docstring notes this
exact table has gone stale in restatement more than once, most recently a
"JavaScript at R1 only" sentence this README itself carried until corrected
here), `src/assay/registry.py`, `src/assay/coverage.py::derive_branch_capability`,
`src/assay/evaluate.py`'s `fail_under` arithmetic, and the five
`coverage_parsers/*.py` modules directly — not restated from an earlier doc
pass.

#### "Why is there always a budget?" — R0/R1's numeric timeout is deliberate, not unfinished

A lane declares `budget` (e.g. `"10m"`); the wrapped command is killed if it
runs longer. Two things are easy to conflate here, and the backlog
(`nyxloom-trove/4-backlog.md` B064/B065/B067) already worked through both in
depth:

**Observability exists.** `--progress`/`--progress-heartbeat SECONDS`
(default 60, B064, shipped) makes an R0/R1 lane write a closed-vocabulary
phase stream — `run` header, `snapshot_materialized`, `command_started`, a
time-based heartbeat tick, `command_finished`, `coverage_parsed`,
`verdict_written` — each event carrying `emitted_at`/`elapsed_s` (B065,
shipped), so a caller with only the progress file can compute rate, ETA and
last-event age without the verdict. A lane that looks hung for nine minutes
is legible with these flags on; it is not legible with them off, which is
the mistake to check for before concluding assay itself is silent.

**The numeric bound stays mandatory for R0/R1 anyway, and that is a ruling
(A-447), not an oversight.** `budget = "unbounded"` is legal — but *only*
when every unit of the lane's work carries its own bound: R2 requires
`budget_per_candidate`, R3 requires `budget_per_attempt`. An R0/R1 lane is
**one command**; its only unit of work *is* the whole command, so there is
no smaller bound to fall back on, and `unbounded` is refused there by name.
Removing the top-level timeout for a single opaque subprocess would mean
launching it with `timeout=None` — genuinely unbounded, no backstop at all
if it hangs forever. The heartbeat is a *liveness signal a caller can act
on*; it is deliberately not treated as license to disable assay's own
last-resort kill switch — "stall detection stays with the caller"
(run-gate's own `stall_timeout`/`LogStreamWatch`, RG-36/RG-41), which can
choose to extend a wait based on the heartbeat still ticking, in front of
assay's unconditional numeric backstop, not instead of it.

**What the heartbeat does *not* give you: true percentage/ETA of the
runner's own internal progress.** `command_finished` can legitimately be the
longest phase by a wide margin — a multi-minute test suite is one opaque
subprocess call from assay's point of view — and a time-based heartbeat only
proves elapsed seconds are ticking, not how much of *that* suite is left.
Getting "47 of 213 tests done" requires reading the runner's own live output
stream (`pytest -v`'s per-test lines, `go test -json`'s one-event-per-test
stream) — real, per-language, per-tool parsing, and a different design
surface than the phase heartbeat (declaring a producer format, never
sniffing one, per this project's own A-007 principle). That is filed and
scoped as **B073**, deliberately deferred — no consumer had asked for
per-test granularity when it was filed; use `--progress` for liveness today,
and see B073 if per-test detail becomes worth the design cost.

### What assay is not (yet)

The verdict schema reserves a `go:*` operator vocabulary and
`external_tools`/`helpers` machinery for a Go adapter. **`judge.language =
"go"` now resolves at R1 — see the Go section below for what that does and
does not get you.** R2 and R3 are still refused
`ERROR`/`BAD_LANE_CONFIG`; the schema surface exists so a later package does
not need a compatibility bump to fill them in. Go support was blocked, for
several packages, on a real and proven design problem: `go test
-coverprofile` cannot express which physical line a statement starts on —
only a block's byte extent plus a bare statement *count* — and two different
gofmt-clean source files can produce byte-identical coverage profiles while
having different statement lines. That's not a rough edge, it's an
unconditional impossibility proof against the naive approach, found and
documented before any Go code shipped. See decisions A-172, A-217, A-218 in
[`nyxloom-trove/decisions.md`](nyxloom-trove/decisions.md) for the full
finding and the ruling (build a real statement-position oracle, not a
line-range heuristic).

**What has landed against that ruling**: the oracle itself ships in the
wheel (`assay/helpers/go/stmtpos/`, adapted from `cmd/cover`'s own
instrumenter and proven against every frozen witness, including the
byte-identical-profile pair); the Go parser keeps each record's whole block
extent instead of expanding it into lines; a new `statement_blocks` adapter
hook supplies source-derived statement positions; and the evaluator now
**refuses** a Go profile that has not been through that correction, rather
than judging block extents as statement truth. The Go adapter accordingly
declares `external_tools = ("go",)`.

If you see `go:` anywhere in the schema or vocabulary and are wondering
whether you can use it today: **at R1, yes — and read the toolchain sentence
before planning around it.** The Go adapter is now in the built-in registry
at `{"R1"}` (decision A-394), which was deliberately the *last* step of the
work above rather than the first: registering it any earlier would have made
a Go lane runnable while the parser still reported block extents as statement
truth, which is the wrong verdict the impossibility proof exists to prevent,
reachable by a consumer who did nothing wrong.

**Qualified on a real project, not only on fixtures.** The Go path was run end
to end against `shared-ramdisk-depot-manager` at a real commit range, through
the shipped zipapp, inside the Go gate image, and compared line by line with
that project's own diff-coverage gate on the byte-identical profile: 418
executable changed lines against its 684, agreeing on all 24 statements that
are genuinely uncovered, with every one of the 266 extra lines it counts shown
to begin no statement. The worked lane is
[CONSUMERS.md point 8](docs/CONSUMERS.md); the qualification also found a real
defect in assay itself (B061), which is what a qualification is for.

**A Go lane requires a real Go toolchain on PATH.** The statement-position
oracle is a Go program — A-217 rules that a Python re-implementation of
`cmd/cover`'s segmentation is not an acceptable substitute — so on a machine
without `go`, an R1 Go lane is refused `NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL`
*before the lane's command runs*, rather than guessing or running a suite it
could not judge. That refusal is what makes the registration safe everywhere
rather than only where a toolchain happens to exist: a Go lane is either
judged against real statement positions or cleanly refused, and there is no
third state in which it is quietly wrong.

**Generated Go sources carrying `//line` directives are not judged, and that
is a per-FILE rule.** A `//line file:line` directive makes the toolchain
report positions as if they came from another file, and `go test
-coverprofile` records those remapped numbers — Go's own `TestLineDup`
fixture is 24 lines long and its profile names lines 100-105. `git diff`
names physical lines, so the two cannot be joined by anyone. Assay reads such
a profile without complaint and simply IGNORES a remapped file that has no
changed line in the judged set; a remapped file that IS judged refuses
`ERROR`/`BAD_LANE_CONFIG`, naming the file and the remedy (keep generated
sources out of `judge.source_roots`). What it will not do is measure the
virtual line numbers, match nothing, and report a clean percentage over
nothing measured. See [CONSUMERS.md point 5](docs/CONSUMERS.md).

**R2 and R3 remain unregistered for Go.** `generate_mutation_sites` is
unconditionally `UNSUPPORTED`, and declaring a rigor level a lane can't
actually back up is exactly the failure this project exists to prevent.

**Getting assay into a Go gate image costs nothing extra**, and this is what
stdlib-only (A-005) buys: a `golang:1.25`-based image already carries
`/usr/bin/python3` 3.13.5, above assay's `>=3.11` floor. It has no pip, so
the shipped **zipapp** is the install path — copy the `.pyz` in, check its
`.sha256`, run it with the interpreter that is there. The Go oracle rides
inside that archive and is staged out to a real directory when it runs, so
there is nothing to unpack.

**Your module path is read from your own `go.mod`, never declared.** A Go
cover profile keys records by import path while `git diff` uses repo-relative
paths; assay bridges them by reading the `module` directive out of the
nearest `go.mod` at or above your project root, because that is where the
fact lives (A-404 — `covergate`'s own `-module srdm` flag is the
anti-pattern this avoids). The practical rule: **one Go module per lane, and
`assay.toml` sits at that module's root.** A project root above several
modules, or in none, refuses and says which — it never picks one silently.
[CONSUMERS.md's Go
section](docs/CONSUMERS.md#go-lanes-what-exists-today-and-what-a-go-lane-will-require)
point 6 has the detail; `go.work` is not supported.

Why the oracle is a subprocess rather than a Python rule:
[DESIGN-GUIDE §11, "Go statement positions"](docs/DESIGN-GUIDE.md#go-statement-positions-come-from-the-source-never-from-the-profile-a-217a-239a-397).
**`sql:*` is different — see below.**

### JavaScript/TypeScript changed-line coverage (R1 only)

`judge.language = "javascript"` resolves at **R1 only** — changed-line
coverage over `.js`, `.jsx`, `.ts` and `.tsx`. One language name covers all
four: TypeScript is JavaScript's own superset, JSX/TSX are syntax extensions
of the two, and every coverage tool in the ecosystem measures them into one
undifferentiated artifact, so splitting them would force a lane touching one
`.ts` and one `.tsx` file to declare two languages for one measurement.

Declare `format = "coverage-istanbul-json"`, point `artifact` at istanbul's
own `coverage-final.json`, and declare `producer = "istanbul"` — that third
key is **required** for this format, because several toolchains write it and
they disagree about what parts of it mean, so there is no value assay could
imply that is correct in every context. `producer` is recorded in the verdict
as `judgment.r1.coverage_producer`; declaring `istanbul` is also what makes
`require_branch = true` legal on a JavaScript lane. The vocabulary, the three
producers assay refuses **by name**, and the one-line migration are in
[`docs/CONSUMERS.md`](docs/CONSUMERS.md#declaring-the-coverage-producer-b045).

The `coverage-final.json` document is emitted natively by nyc/istanbul and by
Jest (`--coverageReporters=json`), and by Vitest through either coverage
provider:

```jsonc
// vite.config.ts / vitest.config.ts -- import `defineConfig` from
// 'vitest/config', NOT from 'vite': only vitest/config's defineConfig
// accepts a `test:` block; vite's own rejects it at the type level.
test: {
  coverage: {
    provider: 'istanbul',      // REQUIRED for a judged lane — see below
    reporter: ['json'],        // 'json' IS coverage-final.json
    reportsDirectory: '.assay', // keep it out of the tree, and gitignore it
    clean: false               // RECOMMENDED inside an assay snapshot — see below
  }
}
```

> **`coverage.clean: false` is RECOMMENDED for a lane assay judges; forget
> it and assay tells you so by name.** Vitest's own default (`clean: true`)
> deletes and recreates `reportsDirectory` before writing, and assay reserves
> that directory's file handle *before* your command runs so it can read the
> artifact back afterward without a second, racy directory walk. A tool that
> deletes and recreates the directory rather than writing into the one assay
> already opened leaves that handle pointing at an orphaned, empty directory —
> assay then reads nothing back, even though a fully-populated
> `coverage-final.json` really exists on disk at that path. Up to 4.1.0 that
> was reported as `NO_MEASUREMENT`/`EMPTY_COVERAGE` ("your tests produced no
> coverage"), which is false. **From 5.0.0 it is `ERROR`/`UNREADABLE_ARTIFACT`
> with a message naming the directory, the cause and the remedy**, for every
> reserved artifact — coverage, a SQL R2 equivalence artifact, a mutation
> lane's kill signal. Why the check is an `fstat` on the held descriptor
> rather than a re-open by name: [DESIGN-GUIDE
> §B049](docs/DESIGN-GUIDE.md#a-replaced-output-directory-is-named-not-folded-into-empty_coverage).
> See B049.

> **Use `@vitest/coverage-istanbul`, not `@vitest/coverage-v8`, for any lane
> you gate on.** The v8 provider reports provably-never-executed lines as
> *executed* whenever a conditional (`? :`) expression appears earlier in the
> same block — so an assay R1 lane PASSes on lines that never ran. Measured on
> Vitest **3.2.4 and 4.1.11 alike**, for one-line and multi-line ternaries,
> and not fixed by `coverage.experimentalAstAwareRemapping`. The
> `@vitest/coverage-istanbul` provider is correct on every case measured. Both
> emit the same `coverage-final.json` and assay reads either — assay cannot
> tell them apart, which is exactly why the choice is yours to make correctly.
> The witness fixtures are committed
> (`tests/fixtures/coverage/probe-js-provider-defect/`), the ruling is A-346,
> and the follow-up is B038/B040. `nyc`/`istanbul` and Jest with its default
> `babel` coverage provider share `@vitest/coverage-istanbul`'s own
> instrumenter and are unaffected. Jest's `coverageProvider: "v8"` was not
> independently measured this wave — treat it as unsafe until a committed
> witness says otherwise — but it is not a clean unknown: `@jest/reporters`
> depends on `v8-to-istanbul@^9.0.1`, the identical remapper package `c8`
> uses (`^9.0.0`), and both ranges resolve to the same latest `9.3.0` absent
> a pinning lockfile, so Jest's v8 provider is a strong candidate to share
> `c8`'s own measured defect below, not merely an untested unrelated
> implementation. `c8` **was measured** (B042 item 2) and is **also** unsafe: on
> the identical ground truth, `c8@12.0.0`'s own `v8-to-istanbul` remapping
> reports the same class of false-executed lines, triggered by the same
> conditional expression — a related but not byte-identical defect (see
> [the CONSUMERS guide](docs/CONSUMERS.md#the-v8-provider-is-not-safe-to-gate-on)
> for the exact measured line sets and the committed witnesses).

The artifact keys every record by absolute filesystem path; assay reconciles
that against the diff's own repo-relative spelling itself, so nothing has to
be configured for it. Test files are excluded by Vitest's own default
`include` glob AND, independently, by the adapter (`*.test.*`, `*.spec.*`,
anything under a `__tests__/` segment); `node_modules`, `dist` and `coverage`
are excluded as directories. A `.d.ts` declaration file is recognised as
having no executable code at all rather than being reported as a coverage
gap.

**A JavaScript/TypeScript lane's dependency closure (`node_modules`) is
absent from assay's own snapshot** — Python's venv and Go's module cache are
both out-of-tree, so neither adapter ever met this; JavaScript's is in-tree
and gitignored. See
[CONSUMERS' "JavaScript lanes and the dependency closure"](docs/CONSUMERS.md#javascript-lanes-and-the-dependency-closure)
for the offline-install pattern, the `npx` fetch hazard, and a worked
monorepo lane (B041).

**There is deliberately no JavaScript R2 yet.** Whether JS/TS mutation should
be a native engine (as Python's and SQL's are) or should ingest an external
producer's evidence (Stryker Mutator's per-mutant report) is a real
architectural ruling that has not been made — it is tracked as **B037**, and
until it is, `judge.language = "javascript"` declaring R2 is refused
`ERROR`/`BAD_LANE_CONFIG`, exactly like an unregistered language. R3 (the
cause-sensitive canary) is unwired for the same "a method existing is not a
producer path" reason, though both canary injection mechanisms are real.

**Branch coverage depends on the declared producer.** istanbul's `branchMap`
means different things under different producers (real per-arm arcs under the
`istanbul` instrumenter family, v8's own executed/unexecuted ranges under
`@vitest/coverage-v8` and `c8`), so the format name alone cannot answer it.
Declare `producer = "istanbul"` and the arcs are real, `branch_capability` is
`"reported"`, and `require_branch = true` is legal; without that declaration
`branch_capability` stays `"unavailable"` — a measured refusal rather than a
gap. See [Declaring the coverage producer](docs/CONSUMERS.md#declaring-the-coverage-producer-b045).

### SQL/DDL mutation testing (R2 only)

Unlike Go, SQL has a real, working adapter: `judge.language = "sql"` resolves
at **R2 only**. There is deliberately no SQL R1 (DDL has no coverage tool)
and no SQL R3 (A-192 forbids R3 without R1) — this is a settled design
choice, not a gap waiting on a later package. The adapter is a stdlib-only
byte-span lexer over tracked `.sql` files, never a database connection: it
locates and replaces one span of DDL text outside every comment, string
literal and quoted identifier, and classifies the mutant using only the
project-declared command's exit status and the bytes of two files the lane
itself declares. See
[§11 of the design guide](docs/DESIGN-GUIDE.md#sqlddl-mutation-a-stdlib-lexer-not-a-database-connection)
for why, and [the consumer guide](docs/CONSUMERS.md#sqlddl-lanes-r2-only) for
a worked, pasteable lane.

The two B015 semantic families, `python:uuid-equality-swap` and
`python:enum-comparison-swap`, are **withdrawn** (A-326). Measured
over assay's own source they produced 87 sites, none of which
`python:compare-swap` did not already produce at the same byte span with the
same replacement bytes — so a lane declaring both families ran every shared
mutation twice for no additional coverage. A lane naming either is refused at
load, by name and with that reason — and at the v7→v8 cut the spellings are
gone too (A-331), so neither name is legal anywhere in a v8 verdict. Verdicts
emitted by 2.3.0/2.4.x are unaffected: they are schema v7 documents, refused
under v8 on `schema_version` alone, and must be verified with a v7 `assay`.

The seven closed `judge.mutation.operators` values a SQL lane may declare:

```
sql:drop-check         sql:drop-unique        sql:drop-not-null
sql:drop-foreign-key    sql:weaken-delete-action    sql:drop-trigger
sql:widen-check-in
```

Two `judge.mutation` config keys exist only for a SQL lane:
`equivalence_artifact` (a project-relative path the lane's command writes
after applying a mutant; **required** on every SQL lane — without it a
mutant that never actually mutated is recorded `survived`, a false
statement about your tests) and `kill_signal_artifact` (optional; declaring
it turns on kill attribution). Both share `judge.coverage.artifact`'s own
path grammar and must be gitignored, exactly like a coverage artifact.

Every mutation lane may declare optional `budget_per_candidate` with the same
duration grammar as `budget`. A candidate whose command exceeds it enters the
existing `budget_exceeded` bucket while unrelated candidates continue.
Progress is opt-in and consumer-directed: `assay run <lane> --progress PATH`
appends a compact NDJSON event to PATH -- a `run` header naming the commit and
start time, then one event after the baseline and one after each completed
candidate, each flushed. Assay never picks this location itself and writes no
progress file when the flag is omitted; point it OUTSIDE the repository (or at
a gitignored path), since a progress file inside the work tree makes the next
run of the same lane refuse `NO_MEASUREMENT`/`DIRTY_TREE`. The verdict does not
record the destination, exactly as it does not record `--verdict-json`'s.

Lanes may declare `[lanes.<name>.infrastructure]` facts with `required-env:` or
`derived:` sources. Assay resolves them in the invoking context before any
snapshot work and injects the values into the isolated command.

**Who owns the comparison base is a lane decision (B019).** A changed-line
lane normally declares `judge.base` itself. A lane that must stay portable
across branches and worktrees instead declares `judge.base_source = "request"`
and leaves `judge.base` out entirely: changed-line judging is still required,
but the base's identity comes from the invoking gate request, as `assay run
<lane> --request-base REF` (`assay plan` takes the same flag). A ref or an
already-resolved commit is accepted, and either goes through exactly the
merge-base resolution `judge.base` always did, landing once in
`judgment.resolved.base`. The two owners are mutually exclusive: declaring
both, or passing `--request-base` to a lane that did not delegate, is refused
by name rather than settled by a precedence rule — whichever side lost would
be configuration nothing reads. A delegating lane invoked with no
`--request-base` refuses too; assay never falls back to `HEAD` or a default
branch.

**Pitfall: a `judge.base` literal pointing at a remote-tracking ref rots
silently.** `resolve_base` always computes `merge-base(base, HEAD)`, never a
diff against `base`'s current tip — so `judge.base = "origin/main"` is really
"diff since wherever local history last touched origin/main." That's exactly
right while origin stays in sync with local work, and exactly wrong the
moment it doesn't: a workflow that batches commits locally before pushing
(a deliberate "not pushed yet, flagged separately" policy, a long review
cycle, a slow CI queue) lets `origin/main` drift dozens of commits behind
without anyone changing a single line of config, and every lane judging
against it silently widens from "this change's own diff" to "everything
since the drift began" — including work already gate-verified through its
own lane. Hit independently at least three times across this estate (a
dstdns lane's frozen-SHA base, generalized repo-wide after a second
incident; vbpub nyxloom's and ciu's `tester-unified`/`ciu` lanes, both
still pointing at `origin/main` until 2026-09-11). Two real fixes, pick
based on how the lane is actually invoked:
- **A local branch ref instead of a remote-tracking one** (`base = "main"`)
  when the lane is always run from a worktree that forks from that branch —
  self-updating via the same merge-base resolution, no re-pin ever needed,
  and no per-invocation flag to remember or forget.
- **`judge.base_source = "request"`** (above) when the lane genuinely needs
  a different base per invocation (e.g. `origin/main` for a pre-push
  sanity check, a feature branch's own fork point for local dev) — but only
  once whatever invokes the lane *always* supplies `--request-base`
  itself. Without that, an omitted flag falls back to the invoking tool's
  own default (commonly `merge-base HEAD @{upstream}`), which is the
  identical stale-origin hazard this pitfall describes, just relocated
  from `assay.toml` into the caller. A frozen SHA (the third option,
  pinning `base` to one specific commit) fixes the drift once but rots
  again the same way `origin/main` did, on its own schedule — it is at
  best a stopgap, not a fix.

**Every verdict may name the build that produced it (B018).** Beside
`assay_version` — a string any process can print — an installed assay records
`judge_provenance`: the distribution name, exact version, artifact kind
(`wheel` or `zipapp`), digest algorithm and the lowercase sha256 of the
artifact it was installed from. A wheel install reports the digest the
installer recorded in PEP 610 `direct_url.json`; a zipapp is hashed directly.
An invocation with no identifiable build artifact — a source checkout, an
editable install — records **nothing** rather than a partial identity, and
says so on stderr. A gate that binds its evidence to a judge binary it
verified passes `assay run <lane> --require-judge-provenance`, which turns
that absence into a refusal before any work runs.

`assay plan` accepts `--operators name,name` and `--shard INDEX/COUNT`; `assay
run` accepts `--resume` plus the same filters. Shards use zero-based indexes,
assign candidates by deterministic digest, and require a manifest merge that
proves every shard is present, shares one lane/commit, and contains no repeated
candidate.

When a lane command fails or times out, its verdict can retain at most the last
64 KiB of each output stream in `result_stdout_tail` and `result_stderr_tail`.
Truncation is head-side and visible through the paired dropped-byte counts, so
the final error survives without turning a verdict artifact into an unbounded
log.

### Codebase-quality metrics beyond coverage

Coverage (line/statement, branch, and mutation-tested "real" coverage) and
canary/parity answer one specific question — "did tests touch this code, and
would they notice if it broke." That is not the only axis of "is this
codebase in good shape," and assay deliberately does not try to be every
tool at once (§0, the one invariant). Two different relationships to
assay's own model are worth telling apart:

**Metrics with no assay involvement at all — a different tool answers them,
and R0 can attest any of them today, language-agnostically.** R0 only needs
"good" to be expressible as a zero exit code; it does not care what the
wrapped command measures. Wrapping any of the tools below as a lane's `argv`
(with a threshold check baked into the command, since most of these tools
don't have their own `--fail-under`) gets a standardized, machine-readable
`PASS`/`FAIL`/`reason_code` in the exact same verdict shape as a coverage
lane — no new assay code needed:

| Metric | What it tells you | Go | Python | JavaScript/TypeScript |
|---|---|---|---|---|
| Static analysis / lint | Style and correctness smells | `go vet` (stdlib), `golangci-lint` (aggregates many linters) | `ruff`, `pylint` | `eslint` |
| Cyclomatic / cognitive complexity | How hard a function is to reason about or fully branch-test | `gocyclo`, `gocognit` | `radon`, `xenon` | `eslint`'s `complexity` rule, `escomplex` |
| Duplication (copy-paste detection) | Maintenance risk, drift between near-identical paths | `dupl` | `pylint`'s duplicate-code check, `jscpd` (multi-language) | `jscpd` |
| Dead code / unused exports | Code nobody calls — a maintenance and audit-surface liability | `deadcode` (`golang.org/x/tools`), `unused` (staticcheck) | `vulture` | `ts-unused-exports`, `knip` |
| Dependency vuln scanning | Supply-chain risk | `govulncheck` | `pip-audit`, `safety` | `npm audit` |
| API/ABI surface stability | Whether a "non-breaking" change actually is | — | — | — |
| Documentation coverage | Undocumented exported symbols — onboarding/audit friction | — | `interrogate` | — |
| Performance/benchmark regression | Whether a change made something slower | `go test -bench` + a comparator (`benchstat`) | `pytest-benchmark` | — |

`jscpd` and `semgrep` (static analysis, not listed per-cell above) are
cross-language and worth knowing about for exactly that reason — one tool,
one R0 wrapper, covers several of a monorepo's languages at once.

**Metrics that *do* touch assay's own model, because coverage's own
structured data already answers part of the question.** A `whole_target`
lane already tells you which declared lines are executed by *any* test in
the whole suite (not just the diff) — a line that's `0` in that mode, on a
target nothing imports, is exactly what a dead-code detector is trying to
find, just from a different direction (execution evidence, not static
reachability analysis). Not a substitute for the tools above — a line can be
imported and executed by one throwaway test and still be dead in every real
sense — but a signal that's already sitting in a verdict you may already be
producing, worth checking before reaching for a new tool.

**What's not on this list on purpose.** API/ABI stability and performance
regression need a *baseline to compare against over time*, not a single run
judged in isolation — a fundamentally different shape than "run this command
once, judge the result," and out of scope for what R0–R3 are built to
answer.

## How it works

1. A project adds an `assay.toml` **lane file** with three axes, matching
   the design guide's WHAT/HOW/WHERE split:
   - `[lanes.<name>]` — WHAT: `scope`, declared `rigor` (a subset of
     `R0`–`R3`), `enforcement` (`gate` or `advisory`), the command `argv`.
   - `[lanes.<name>.judge]` — HOW: coverage format, `source_roots`,
     `fail_under`, mutation operators/budget, canary mechanism — only for
     the rigor levels actually declared.
   - `where` facts (env, env_passthrough) — WHERE this specific lane
     legitimately differs from the ambient environment.
2. `assay run <lane>` executes the lane's declared command exactly once,
   measures the result against every declared rigor level, and writes one
   verdict artifact — a JSON document validated against a shipped JSON
   Schema, with a real exit code (`0` PASS, `1` FAIL, `2` ERROR,
   `3` NO_MEASUREMENT, `4` BUDGET_EXCEEDED, `5` INCONCLUSIVE).
3. Your CI gates on the exit code, or a downstream tool consumes the JSON
   directly — the schema is shipped as data specifically so a consumer never
   has to link against assay to read a verdict.

```toml
# assay.toml
schema_version = 2

[lanes.unit]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["pytest", "--cov=mypkg", "--cov-branch", "--cov-report=json:cov.json"]
env = {}
env_passthrough = ["PATH"]
budget = "20m"
allow_argv_append = false

# Required the moment a lane declares R1, R2 or R3 (refused on an R0-only
# lane); no default. "repository" materialises the whole commit -- see the
# design guide for "repository-minus-unsafe-symlinks", the monorepo case.
[lanes.unit.isolation]
snapshot_selection = "repository"

[lanes.unit.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = true
base = "origin/main"

[lanes.unit.judge.coverage]
format = "coverage-py-json"
artifact = "cov.json"
```

```bash
pip install ./assay-*.whl   # see Installing
assay run unit --verdict-json verdict.json
echo $?                     # 0 on PASS; gate on this, or read verdict.json
```

Two more CLI verbs round out the surface:

- `assay lanes` lists what a project's `assay.toml` actually declares —
  useful for auditing what a project claims before trusting its gate.
- `assay plan <mutation-lane>` discovers candidates through a private commit
  snapshot, reports total/per-file/per-operator counts and deterministic IDs,
  and estimates runtime without running the lane command or any mutant.
- `assay verify <verdict.json>` independently re-checks that a verdict
  artifact is schema-conformant and internally self-consistent. It never
  re-runs a lane and is never the *sole* witness to a producer's
  correctness — see [§9 of the design guide](docs/DESIGN-GUIDE.md) for why
  a second, independent layer exists instead of trusting the producer that
  emitted the artifact.

## Installing

assay is distributed as an estate-local wheel (decision A-001 — not
published to a public package index). Obtain one immutable release wheel, verify its
`release-manifest.json`, then install it with `pip --require-hashes`; the exact workflow is in
[Consuming Assay from another repository](docs/CONSUMERS.md). For local development only:

```bash
pip install ./assay-*.whl
```

Zero runtime dependencies means this is genuinely offline-safe: nothing else
gets pulled in.

## Synergy with other tools

assay is deliberately one piece of a larger, decoupled toolchain, not an
all-in-one platform:

- **Consumes, never wraps:** coverage.py, `go test -cover`, and similar
  tools' *output* — never their APIs. assay has no import-time coupling to
  any of them.
- **`ciu`** owns environment/container orchestration (WHERE). assay owns
  judgment (HOW). They're deliberately not merged — see
  [§4 of the design guide](docs/DESIGN-GUIDE.md#4-the-boundary-with-ciu-and-why-they-are-not-one-tool)
  for why that boundary is topological, not just organizational.
- **`cmru`** owns release transactions and project gates. A consumer's
  `cmru.toml` can invoke a pinned Assay wheel or zipapp through `tester-gate`; CMRU does not
  reinterpret the lane or bake an ambient Assay version into `tester-unified`. See the
  [consumer guide](docs/CONSUMERS.md#cmru--tester-unified-integration).
- **`nyxloom`** orchestrates the *development* of assay itself (and other
  estate projects) through a handoff/carve/review/gate/merge pipeline — see
  [Contributing / development process](#contributing--development-process).
  It is not a runtime dependency of assay and assay does not depend on it
  to function.
- **Tier 2 (adjudicated) evidence** is how assay integrates with SAST, SBOM,
  DAST, accessibility, or visual-regression tools without becoming one: it
  invokes, or consumes the declared structured output of, a declared
  third-party tool, and applies that tool's own decision — nothing more
  opinionated than that. B004's image-provenance adjudicator is the shipped
  example of the second shape: assay never invokes `ciu` itself (A-030;
  at S3/S4 the docker socket the invocation would need is not even reachable
  from inside the lane's own container), so it reads `ciu provenance --json`'s
  declared output instead. See [CONSUMERS.md's "Adjudicated image
  provenance"](docs/CONSUMERS.md#migration-notes-v9--v10) section.

## Contributing / development process

assay's own development is managed by `nyxloom`, this repository's carve →
review → implement → review → gate → merge pipeline — not ad hoc PRs. If
you're changing assay itself:

- Start at [`nyxloom-trove/STATE.md`](nyxloom-trove/STATE.md) for the current
  state of the project and what's merged so far.
- [`nyxloom-trove/decisions.md`](nyxloom-trove/decisions.md) is the append-only
  record of every product decision and why — check it before assuming
  something is an oversight rather than a deliberate choice.
- [`nyxloom-trove/handoffs/README.md`](nyxloom-trove/handoffs/README.md)
  tracks the current package queue and its dependency order.
- The registered gate (`tools/tester-unified-gate.sh`, run only inside its
  dedicated container, never the interactive devcontainer) is the only
  accepted ship signal.

## Further reading

- [`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md) — the *why* behind every
  decision: the three evidence tiers, the rigor ladder, the adapter
  protocol, the boundary with `ciu`, and more.
- [`nyxloom-trove/decisions.md`](nyxloom-trove/decisions.md) — the *what*,
  one line per decision, in the order they were made.
- [`nyxloom-trove/STATE.md`](nyxloom-trove/STATE.md) — current project
  state: what's merged, what's next, and known gaps recorded honestly
  rather than silently dropped.
- `src/assay/schemas/verdict.schema.json` — the verdict artifact's JSON
  Schema, shipped as data. Read this if you're building a consumer that
  reads assay's output without depending on assay's own code.

## Testing

`./run-gate.py` is the canonical test entrypoint — `./run-gate.py --list`
discovers the declared lanes; definitions live in `run-gate.toml`.
See [`../run-gate-project/CONSUMERS.md`](../run-gate-project/CONSUMERS.md).
