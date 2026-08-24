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
  [SQL/DDL mutation testing](#sqlddl-mutation-testing-r2-only) below). Go
  still has reserved schema surface but no real adapter yet — see
  [What assay is not (yet)](#what-assay-is-not-yet).

---

## Why use it

Test suites lie in a specific, boring, recurring way: CI is green, coverage
looks fine in aggregate, and the change that actually mattered was never
exercised. Usually because coverage was measured over the whole project
instead of the diff, or because "100% coverage" meant every line *ran*, not
that any assertion would have caught a wrong version of it.

assay exists to close that gap mechanically, not by policy:

- **You choose which question R1 asks: changed-line, or whole-target.** In
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
`VERDICT_SCHEMA_VERSION = 6` and the lane file is `LANE_SCHEMA_VERSION = 2`.
Both are hard cuts: `assay verify` refuses a v5 verdict exactly as it refuses
v4 today (no dual-version verifier, no upgrade-in-place), and a v2 assay
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
Progress is appended to `.assay/<lane>.progress.jsonl` after the baseline and
each candidate; the verdict's optional `mutation.progress_artifact` names that
file.

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
