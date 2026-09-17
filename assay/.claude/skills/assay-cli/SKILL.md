---
name: assay-cli
description: The assay judge CLI itself (lanes/run/plan/verify) — declared lanes, verdict schema, mutation resume/rejudge, progress streaming, verdict-JSON verification. Use when you need to reason about what assay itself does (inspect a lane's declaration, understand a verdict, plan a mutation run's cost) rather than just running it through run-gate.
---

> **Tool versions as of last verified update (2026-09-17):** assay `6.3.0`
> (`pip show assay` — **not** `assay --version`, which prints a placeholder
> `0.0.0` regardless of the real installed version). Current verdict schema
> is v11; v10→v11 was a hard cut (see `assay/docs/CONSUMERS.md` "Migration
> notes (v10 → v11)") — a consuming project's gate lanes may still pin an
> older frozen `assay-*.pyz` artifact rather than this installed pip
> version; check both before assuming a given schema applies to what
> actually ran. Re-verify against `assay --help` / `assay <verb> --help` if
> a flag below drifts.

> **MANDATE.** assay is the ONLY judge for a declared coverage/mutation
> lane. Never hand-run `coverage.py`/`pytest --cov` outside a declared lane
> and treat its output as a gate verdict — assay's verdict-JSON is the one
> artifact a review/merge decision may cite. See the **nyxloom-carve** skill
> for the corollary: never hand-PREDICT what assay will report either —
> execute it (or `coverage.py`/`runpy.run_module` against a real stand-in)
> instead of reasoning about a rendered report.

# assay CLI

assay judges a change against a project's `assay.toml`-declared lanes and
emits one machine-readable verdict. Four subcommands total; each is narrow
and does exactly one thing.

## Inspect what's declared (runs nothing)

```bash
assay lanes                 # human-readable listing of every declared lane
assay lanes --json          # machine-readable inventory: scope/rigor/enforcement,
                             # coverage/mutation/canary shape, which rigor levels
                             # THIS build reaches for the lane's language, and the
                             # facts a gate tool needs to preflight without
                             # re-parsing assay.toml itself
```
A lane file that fails to load exits 2 with no output — use this before
`run` to confirm a lane you're about to invoke actually parses.

## Plan a mutation lane before running it (runs nothing, executes no lane command)

```bash
assay plan <lane> [--operators OPERATORS] [--shard INDEX/COUNT] [--request-base REF]
```
Discovers the lane's mutation candidates, prints total/grouped counts with
deterministic identities, and estimates serial and wall-clock runtime.
Creates no mutant snapshots. **Use this before dispatching a long mutation
lane** to know its actual candidate count and rough duration ahead of time —
this is how you answer "how long will this take" without guessing or
starting the real run.

## Run a lane

```bash
assay run <lane> [--file PATH] [--verdict-json PATH]
```
Executes exactly the named lane's declared argv once — no discovery,
selection, ordering, or retry. Key flags:
- `--verdict-json PATH` (or `-` for stdout) — write the verdict atomically;
  omit to skip artifact emission.
- `--resume` — for mutation lanes, resume from `.assay/mutation-state/`
  (or `--state-dir`) keyed by exact source bytes; a real code change
  re-executes what it touches, an unchanged mutant resumes.
- `--rejudge ID[,ID...]` / `--rejudge-outcome BUCKET[,...]` (with `--resume`)
  — force specific candidates (by id, or by outcome bucket: killed,
  survived, crashed, budget_exceeded, equivalent, hung — `error` aliases
  `crashed`) to re-execute instead of replaying their resumed record.
- `--request-base REF` — REQUIRED (and only accepted) on a lane declaring
  `judge.base_source = "request"`; its absence there is a refusal, never a
  silent fallback to HEAD.
- `--progress PATH` — append NDJSON phase events (every rigor tier now, not
  just R2). **Must point OUTSIDE the repo** (or a gitignored path) — a
  progress file inside the tree makes the NEXT run of the same lane refuse
  with `NO_MEASUREMENT`/`DIRTY_TREE`.
- `--state-dir PATH` — durable mutation-resume store when the worktree
  itself is ephemeral (a fresh checkout per run); a path inside the judged
  tree that git can see is refused up front for the same dirty-tree reason.
- `--require-judge-provenance` — refuse unless this assay binary can
  identify its own build artifact and record its sha256 as
  `judge_provenance`; a gate that must bind evidence to a verified judge
  passes this.

## Verify a verdict artifact independently (runs nothing)

```bash
assay verify <path-or->
```
Checks a verdict-JSON is schema-conformant and internally self-consistent.
**Never re-runs a lane, and is never the sole witness to a producer's
correctness** (assay's own DESIGN-GUIDE §9) — use it to sanity-check a
verdict file you're about to cite in a review, not as a substitute for
actually re-running the lane when correctness itself is in question.

## What this build evaluates

R0, Python R1, Python R2, Python R3, JavaScript R1, Go R1, SQL R2. If a
lane's declared rigor tier isn't in this list for its language, that's a
real capability gap to check `assay lanes --json` for (the "which rigor
levels THIS build reaches" field), not something to route around silently.
