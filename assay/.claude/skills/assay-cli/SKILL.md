---
name: assay-cli
description: The assay judge CLI itself (analyze/lanes/run/plan/campaign/verify) — declared lanes, verdict schema, mutation resume/rejudge, shard and carry-over campaigns, progress streaming, verdict-JSON verification. Use when you need to reason about what assay itself does (inspect a lane's declaration, understand a verdict, plan or split a mutation run) rather than just running it through run-gate.
---

> **Versions.** This text describes assay `12.2.0` (the release this repository
> is at; the newest tag is `assay-v12.2.0`). A consumer environment may have an
> older wheel — check `assay --version` (it is real, not a placeholder: the old
> "prints 0.0.0" note is obsolete) and `assay <verb> --help` before relying on a
> flag below. Current verdict schema is **v19** (a hard cut from v18). Consumers
> that pin an older frozen `assay-*.pyz` rather than the pip wheel can be a
> different world; check both before assuming a schema applies to what ran.

> **MANDATE.** assay is the ONLY judge for a declared coverage/mutation lane.
> Never hand-run `coverage.py`/`pytest --cov` outside a declared lane and treat
> its output as a gate verdict — assay's verdict-JSON is the one artifact a
> review/merge decision may cite. Never hand-PREDICT what assay will report
> either: execute it (or `coverage.py`/`runpy.run_module` against a real
> stand-in) instead of reasoning about a rendered report.

# assay CLI

assay judges a change against a project's `assay.toml`-declared lanes and emits
one machine-readable verdict. Six subcommands: `analyze`, `lanes`, `run`,
`plan`, `campaign`, `verify`. Lane discovery searches upward from the current
directory; `--file PATH` names one explicitly.

## Inspect what's declared (runs nothing)

```bash
assay lanes                 # human-readable listing of every declared lane
assay lanes --json          # one machine-readable inventory document
```
`--json` (B044) carries every lane's scope/rigor/enforcement, the
coverage/mutation/canary shape, which rigor levels THIS build actually reaches
for its language, and the facts a gate tool needs to preflight an environment
without re-parsing `assay.toml` itself. A lane file that fails to load is
`ERROR/BAD_LANE_CONFIG`, exit 2, with no output — run this before `run` if you
are unsure a lane you are about to invoke parses.

## Plan a lane before running it (executes nothing)

```bash
assay plan <lane> [--json] [--operators OP] [--shard INDEX/COUNT] [--request-base REF]
assay plan <lane> --list-pending --state-dir DIR [--judge SHA]   # ids still to do, one per line
```
Discovers the lane's candidates, prints total/grouped counts with deterministic
identities, and estimates serial and wall-clock runtime. Creates no mutant
snapshots. Use it to answer "how long will this take" without starting the real
run, and the `--list-pending` form to split a resumed campaign across machines.
`--list-pending` needs assay >= 12.2.0 (B531) — it is absent from 12.0.0.

## Run a lane

```bash
assay run <lane> [--file PATH] [--verdict-json PATH|-] [--progress PATH]
```
Executes exactly the named lane's declared argv (plus anything after a literal
`--` if the lane permits it). Ordinary runs execute the command once — no
discovery, selection, ordering or retry. Still true, and still the ones agents
get wrong:

- `--verdict-json PATH` (or `-`) writes the verdict atomically; omit it and assay
  emits no artifact at all.
- `--progress PATH` appends the NDJSON phase stream (every rigor tier writes
  `run`/`command_*`/`coverage_parsed`/`verdict_written`, not only R2).
  **Point it OUTSIDE the repo** (or at a gitignored path): a progress file inside
  the judged tree makes the NEXT run of that lane refuse
  `NO_MEASUREMENT`/`DIRTY_TREE`.
- `--progress-heartbeat SECONDS` (default 60, floor 5) emits one
  `command_running` tick per interval; a pure time tick that never reads the
  child's output.
- `--state-dir PATH` keeps mutation resume records outside
  `<project>/.assay/mutation-state/`. Use it whenever the worktree is ephemeral —
  otherwise `--resume` is inert because the store leaves with the checkout. A
  path inside the judged tree that git can see is refused up front.
- `--request-base REF` is REQUIRED (and only accepted) on a lane declaring
  `judge.base_source = "request"`; its absence there is a refusal, never a
  silent fallback to HEAD.
- `--require-judge-provenance` refuses unless this assay can identify the build
  artifact it was installed from and records its sha256 as `judge_provenance`.
- `--jobs N` / `--memory-budget-bytes BYTES` size native R2 parallelism
  (candidate scheduling, B207); `--jobs` may not exceed the lane's
  `judge.mutation.jobs_max`. `--candidates-file PATH` is a NON-qualifying pilot
  over listed candidate ids: it needs `--state-dir` and never writes a verdict.
- `--cold-witness` / `--r2-manifest PATH` prove native Python R2 kills with the
  no-coverage cold attempt; `--campaign-deadline PATH` binds a run and its
  resume records to a deadline from `assay campaign init`.

## Resuming, rejudging and splitting a mutation campaign

```bash
assay run <lane> --resume --state-dir DIR \
  [--rejudge ID,ID ...] [--rejudge-outcome killed,hung,budget_exceeded,error] \
  [--shard K/N] [--carry-over DIR --carry-over-reason TEXT] [--merge-state DIR] \
  [--only-candidates FILE] [--exclude-candidates FILE]
assay run <lane> --merge-shard-store DIR   # repeatable: one per shard, STRICT merge
```

- `--resume` replays recorded candidates keyed by exact source bytes; a real
  code change re-executes what it touches. Execution-resource settings are NOT
  part of that identity (B529): `budget`, `jobs`/`jobs_max`, isolation knobs,
  `[isolation.limits]` and Run-Gate's lane budget/limits/profile are projected
  out, so changing workers no longer throws away finished records. Tests,
  sources, `argv`, `env` and the assay version still invalidate.
- `--rejudge` / `--rejudge-outcome` force re-execution of specific candidates or
  whole buckets instead of replaying their records. Buckets are closed:
  `killed`, `survived`, `crashed`, `budget_exceeded`, `equivalent`, `hung`;
  `error` is a CLI alias for `crashed`. Both require `--resume`, and an id that
  does not match a current candidate identity is refused before any work.
- `--shard K/N` runs one shard and leaves a `shard.manifest`; `--merge-shard-store`
  is the strict whole-lane merge and refuses absent, empty, duplicated,
  foreign-judge, changed-plan, incomplete or polluted stores — before anything
  runs. Both need assay >= 12.2.0 (B530).
- `--carry-over DIR --carry-over-reason TEXT` imports `killed`/`survived` records
  of a DIFFERENT judge identity with provenance; `--merge-state DIR` copies
  same-judge records of another store (permissive).
- `--only-candidates FILE` / `--exclude-candidates FILE` run a subset in every
  mode. Candidates left out without a resumed/carried-over record are reported
  never started, the R2 claim is NOT a PASS, and its detail starts `PARTIAL:`.

## Campaign deadlines and campaign analysis

```bash
assay campaign init --campaign NAME --lane LANE (--hours H | --expires-at ISO8601Z) [--out PATH]
assay analyze campaign <lane> --file assay.toml --expected-commit SHA \
  --progress P [--verdict V] [--state-dir DIR] [--format json|text] ...
```
`analyze` collects and inspects EXISTING artifacts (`collect`, `check`,
`launcher`, `record`, `receipt`, `verdict`, `report`, `progress`,
`plan-estimate`, `campaign`); it never re-runs a lane. `campaign` reports
`state.imported` and a `carry-over:` line, and without a verified `--verdict`
the result is never "complete".

## Verify a verdict artifact independently (runs nothing)

```bash
assay verify <path-or->
```
Checks a verdict-JSON is schema-conformant and internally self-consistent (v19
only; older versions are refused on the version field alone). **Never re-runs a
lane, and is never the sole witness to a producer's correctness** (DESIGN-GUIDE
§9) — use it to sanity-check a file you are about to cite, not as a substitute
for re-running the lane when correctness itself is in question.

## What this build evaluates

R0, Python R1, Python R2, Python R3, JavaScript R1, JavaScript R2 (ingested Stryker report; B046), JavaScript R3, Go R1, Rust R1, SQL R2.

If a lane's declared rigor tier isn't in this list for its language, that is a
real capability gap to check `assay lanes --json` for, not something to route
around silently. **Rust is R1 only:** the `RustAdapter` judges line coverage
(`cargo llvm-cov` LCOV, branch totals unavailable on stable Rust, so a lane that
requires branches refuses with `BRANCH_UNAVAILABLE`); there is no Rust R2
(mutation) or R3 canary yet (B243 is PARTIAL). See `docs/RUST-METHODOLOGY.md`
and "Rust adapter status and tier design" in `docs/DESIGN-GUIDE.md`; the
per-language matrix is in `docs/CONSUMERS.md`.

JavaScript R3 is registered and locally exercised. B087's current uncovered-line
transform still needs retained dstdns qualification; the archived first attempt
used an earlier transform and is not current qualification evidence. Go is R1
only (`generate_mutation_sites` is unconditionally UNSUPPORTED), and SQL is R2
only. Contemporary verdicts are v19; v18 is a cold start for `--reuse-from`.
