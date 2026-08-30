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
  JavaScript/TypeScript is supported at **R1 only** — changed-line coverage
  for `.js`/`.jsx`/`.ts`/`.tsx`, see
  [JavaScript/TypeScript changed-line coverage](#javascripttypescript-changed-line-coverage-r1-only)
  below. Go still has reserved schema surface but no real adapter yet — see
  [What assay is not (yet)](#what-assay-is-not-yet).

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

### What assay is not (yet)

The verdict schema reserves a `go:*` operator vocabulary and
`external_tools`/`helpers` machinery for a Go adapter. **None of that is a
working Go adapter today** — `judge.language = "go"` is refused
`ERROR`/`BAD_LANE_CONFIG` at every rigor level; the schema surface exists so
a later package does not need a compatibility bump to fill it in. Go support
in particular is blocked on a real, proven design problem: `go test
-coverprofile` cannot express which physical line a statement starts on —
only a block's byte extent plus a bare statement *count* — and two different
gofmt-clean source files can produce byte-identical coverage profiles while
having different statement lines. That's not a rough edge, it's an
unconditional impossibility proof against the naive approach, found and
documented before any Go code shipped. See decisions A-172, A-217, A-218 in
[`nyxloom-trove/decisions.md`](nyxloom-trove/decisions.md) for the full
finding and the ruling (build a real statement-position oracle, not a
line-range heuristic).

If you see `go:` anywhere in the schema or vocabulary and are wondering
whether you can use it today: no. Declaring a rigor level a lane can't
actually back up is exactly the failure this project exists to prevent, so
don't be the first exception. **`sql:*` is different — see below.**

### JavaScript/TypeScript changed-line coverage (R1 only)

`judge.language = "javascript"` resolves at **R1 only** — changed-line
coverage over `.js`, `.jsx`, `.ts` and `.tsx`. One language name covers all
four: TypeScript is JavaScript's own superset, JSX/TSX are syntax extensions
of the two, and every coverage tool in the ecosystem measures them into one
undifferentiated artifact, so splitting them would force a lane touching one
`.ts` and one `.tsx` file to declare two languages for one measurement.

Declare `format = "coverage-istanbul-json"` and point `artifact` at
istanbul's own `coverage-final.json`. That document is emitted natively by
nyc/istanbul and by Jest (`--coverageReporters=json`), and by Vitest through
either coverage provider:

```jsonc
// vite.config.ts / vitest.config.ts -- import `defineConfig` from
// 'vitest/config', NOT from 'vite': only vitest/config's defineConfig
// accepts a `test:` block; vite's own rejects it at the type level.
test: {
  coverage: {
    provider: 'istanbul',      // REQUIRED for a judged lane — see below
    reporter: ['json'],        // 'json' IS coverage-final.json
    reportsDirectory: '.assay', // keep it out of the tree, and gitignore it
    clean: false               // REQUIRED inside an assay snapshot — see below
  }
}
```

> **`coverage.clean: false` is REQUIRED for a lane assay judges, not a style
> preference.** Vitest's own default (`clean: true`) deletes and recreates
> `reportsDirectory` before writing, and assay reserves that directory's file
> handle *before* your command runs so it can read the artifact back
> afterward without a second, racy directory walk. A tool that deletes and
> recreates the directory rather than writing into the one assay already
> opened leaves that handle pointing at an orphaned, empty directory — assay
> then reads nothing back, even though a fully-populated `coverage-final.json`
> really exists on disk at that path. Measured: with Vitest's default
> `clean: true`, `assay run` on a real, correctly-covered lane returns
> `NO_MEASUREMENT`/`EMPTY_COVERAGE`; the *only* change that fixes it is
> `clean: false`. See B049.

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
> instrumenter and are unaffected. Jest's `coverageProvider: "v8"` remains
> genuinely unmeasured — treat it as unsafe until a committed witness says
> otherwise. `c8` **was measured** (B042 item 2) and is **also** unsafe: on
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

**Branch coverage is reported as unavailable for this format**, and that is a
measured refusal rather than a gap: istanbul's `branchMap` means different
things under the two Vitest providers (real per-arm arcs under `istanbul`,
v8's own executed/unexecuted ranges under `v8`), and a lane declares the
format, not the producer. `require_branch = true` on a JavaScript lane will
therefore refuse. See **B038**.

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
  invokes a declared tool and applies a declared threshold to its
  structured output, nothing more opinionated than that.

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
