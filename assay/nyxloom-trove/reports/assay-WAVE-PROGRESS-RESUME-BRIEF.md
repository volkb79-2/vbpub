# Continuation brief 1 — progress/resume wave (2026-09-08)

> **SUPERSEDED — the wave is complete.** B064, B065 and B066 all landed after
> this brief was written (`940b5ba2`, `243de634`) and the registered gate is
> green on the tip that carries them. Read
> `assay-WAVE-PROGRESS-RESUME-LOG.md` and
> `assay-WAVE-PROGRESS-RESUME-REPORT.md` instead; everything below that is
> still load-bearing — most of all the gate-discipline lesson in "Two runs"
> — is carried forward there. This file is kept as the record of the
> checkpoint it documents, and the "what is left" section below is now
> history, not instructions.

**Written at the E-008 checkpoint clause** (~60 tool calls crossed during
B067), cut at the strongest available boundary: a commit with a clean tree,
a locally green suite, and the registered gate run on that exact tip.

| fact | value |
| --- | --- |
| branch | `feat/assay-progress-resume-2026-09-08` |
| worktree | `/workspaces/vbpub/.worktrees/assay-progress-resume` |
| tip at the cut | `7f2ba056` (B067) |
| wave prompt | `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-progress-resume.md` |
| items done | **B067 only** (1 of 4) |
| items remaining | **B064, B065, B066** — in that order |
| local suite at `7f2ba056` | 4266 passed, 11 skipped (baseline before the wave: 4235/11) |
| registered gate at `7f2ba056` | see "Gate" below |

---

## What landed (B067) — read this before touching `budget` anywhere

`budget` now accepts the single literal `"unbounded"` beside a duration.
`assay.config.UNBOUNDED_BUDGET` is the constant; `Lane.budget_seconds` is
`float | None`, and **`None` means and only means `"unbounded"`** — never
"not computed", never "defaulted".

Admissibility, refused at load by
`config._refuse_unbounded_without_unit_bounds`:

| lane | `budget = "unbounded"` |
| --- | --- |
| R0/R1 | refused — one command, whose only bound *is* `budget` |
| ingested R2 (`judge.mutation.format`) | refused — likewise one command |
| native R2 | requires `judge.mutation.budget_per_candidate` |
| R3 | requires `judge.canary.budget_per_attempt` (**new key**) |

`judge.canary.budget_per_attempt` bounds one canary probe end to end
(control materialisation + control run + transformed run) and is re-derived
fresh per declared target, via `LaneDeadline.tightened(seconds)` at the top
of `canary.run_isolated_canary`. It also works under a numeric `budget`,
where it only tightens. `tightened(None)` returns `self`, which is what
keeps every pre-B067 lane byte-identical.

### The four `math.inf` boundaries — the part that is easy to break

An unbounded `LaneDeadline` carries `expires_at = math.inf` and
`remaining()` returns `math.inf`. **An infinity handed to `selectors` or
`Popen.wait` raises `OverflowError`, it does not wait**, so every layer that
turns a remainder into a real child timeout converts it:

| site | converts `math.inf` to |
| --- | --- |
| `runner.execute_plan` | `subprocess` `timeout=None` (via local `child_timeout`) |
| `git._sample_remaining` | `None` — covers every git child |
| `git._P22Deadline.remaining` | `None` — covers P22 materialisation |
| `isolation._check_timeout` | admits `math.inf`, and only `math.inf` |

`ProcessRunner.__call__`'s `timeout` is now `float | None`. If a later item
adds a new place where a remainder becomes a child timeout, it needs the
same conversion.

### The one honest gap, filed not hidden

**B076** (new backlog entry, appended in `7f2ba056`): an unbounded R2 lane's
own *baseline* run is the one command left with no bound. Measured:
`timeouts == [None, 45.0]` in
`tests/test_config_unbounded_budget.py::test_a_real_unbounded_R2_lane_runs_every_candidate_with_no_lane_timeout`.
Deliberate — `budget_per_candidate` is a per-*mutant* bound and a baseline
runs the whole suite, so tightening it there would refuse healthy lanes.
Stated in the docstring, in `docs/CONSUMERS.md`, and filed with three
options and none chosen. **Do not "fix" it inside this wave.**

### Files touched by B067

`config.py`, `runner.py`, `canary.py`, `cli.py`, `git.py`, `isolation.py`,
`docs/CONSUMERS.md`, `CHANGES.md` (`[Unreleased]` → `### Added`),
`nyxloom-trove/4-backlog.md` (B076), `tests/conftest.py` (`make_lane`
/`make_deadline` accept `budget_seconds=None`), `tests/test_canary_multi_target.py`
(`_Gate` now records `timeouts`; `_lane` takes `budget_per_attempt`),
`tests/test_config_unbounded_budget.py` (new, 29 tests).

---

## What is left, with the design work already done

The remaining three all extend the SAME substrate (`mutation.progress_writer`
/ `_progress_event` / `_write_mutation_state_record`). Read the wave prompt's
sections 2–4 verbatim; the two settled rulings there
(**stall detection stays with the caller**, **the heartbeat is a pure
time-based tick, never a percentage — that is B073**) are binding and must
not be re-litigated.

### B064 — R0/R1 phase-boundary progress stream, with a heartbeat

Design already worked out, and the seams located:

* **Where the stream must be OPENED.** `verdict_written` is the R0/R1
  stream's terminal event, and the verdict is written by
  `cli.write_verdict` *after* `runner.run_lane` returns — so a writer opened
  inside `run_lane` cannot emit it. Open the stream in `cli._run_reserved`
  (right where `validate_progress_destination` already runs, `cli.py:626`)
  as a `with` block spanning both `run_lane` and `write_verdict`, and thread
  the writer down. Keep `run_lane(progress_artifact=...)` for library callers:
  if no writer was passed and a path was, `run_lane` opens its own.
* **Wrap the raw writer.** Add a small `ProgressStream` around
  `mutation.progress_writer`'s callable holding `started_monotonic` + the
  clock + a `threading.Lock`, so B065's `emitted_at`/`elapsed_s` are added
  once, centrally, to *every* event rather than at each of the ~7 call
  sites. This is what makes B065 nearly free once B064 is in.
* **`progress_path = progress_artifact if r2_declared else None`**
  (`runner.py`, in `_run_prepared_lane`, search for `B031/A-320`) is the line
  that currently confines the stream to R2. B064 lifts it.
* **Event sites.** `snapshot_materialized` after
  `prepared.materialize(...)` in `_run_prepared_lane`; `command_started` /
  `command_finished` around `execute_plan` in `_execute_snapshot_unit`
  (`runner.py`, the `result = execute_plan(` at ~line 2530 after B067's
  edits); `coverage_parsed` on the R1 branch; `verdict_written` from `cli`.
  The DIRECT R0-only path (`run_lane`'s non-snapshot branch, and
  `_finish_direct_r0_lane`) has **no snapshot**, so it must NOT emit
  `snapshot_materialized` — the vocabulary is closed, and emitting a phase
  that did not happen is a lie. That is a decision to state, not to hide.
* **Heartbeat.** A daemon thread + `threading.Event`, started around the
  `execute_plan` call **only where a stream was explicitly passed** — i.e.
  the baseline unit. Do NOT put it inside `execute_plan` unconditionally:
  `mutation._execute_mutation_jobs` calls `execute_plan` once per mutant,
  N-way concurrent, and would flood the file. Cancel in a `finally`;
  swallow-and-stop on a write error so a heartbeat cannot kill a lane.
* **`--progress-heartbeat SECONDS`**, default 60, floor 5 (refuse below,
  naming the floor), no-op without `--progress`, same validation shape as
  `--progress`. Document default + floor in CONSUMERS' progress paragraph.
* **Naming collision to resolve deliberately.** B065 defines `elapsed_s`
  uniformly as "since the `run` header". The heartbeat also wants "how long
  has THIS command been running". Recommended (and what CONSUMERS was
  written to accept): keep `elapsed_s` run-relative on every event, and give
  the heartbeat an additional `command_elapsed_s`. Say so in the docs.

### B065 — enrich every event

Mostly falls out of `ProgressStream` above. Still to do by hand: the `run`
header gains `budget_s` (**`null` for an unbounded lane — B067 makes that a
real case now**) and `budget_per_candidate_s`; a terminal `end` event with
bucket counts for the mutation sweep only (R0/R1's terminal is
`verdict_written`). Check first whether the per-candidate site already
carries `outcome_bucket`/`elapsed_seconds` — **it does**, at
`mutation.py`'s `write_progress` inside `_execute_mutation_jobs`; so B065 is
about propagating the same enrichment to the header and to B064's new
events, not duplicating it.

### B066 — `--state-dir PATH`

`mutation_state_record_path(candidate)` returns the project-relative
`.assay/mutation-state/<id>.json`; `_write_mutation_state_record(project_root, …)`
and `_load_validated_state_record(project_root, job)` join it. Introduce a
resolved `state_root` (`state_dir` if given, else
`project_root/".assay"/"mutation-state"`) and address records as
`<state_root>/<id>.json`. `safeio.read_bounded_input(root, relative, limit=…)`
takes any root and needs no git repo, so the read side is a one-line
change of root + relative. Validate the directory the way
`output.validate_progress_destination` validates a file (outside the
repository or git-ignored; a directory, created on demand), **before any
work starts**. Thread `--state-dir` → `cli` → `run_lane` → `run_mutation`'s
`state_project_root` (rename it; it is no longer a project root).

Acceptance needs a real two-worktree resume test: same commit, two
worktrees, one shared `--state-dir`; the second run emits `event: resume`
with `resumed_total > 0`, and a source edit between them re-executes the
touched file's candidates (candidate ids fold the file's exact bytes, so
this is by construction — but it must be *measured*, not asserted).

---

## Binding constraints that still apply to the successor

* **Do NOT write the LOG/REPORT into the tree before the final gate run.**
  The previous wave's implementer hit `NO_MEASUREMENT`/`DIRTY_TREE` doing
  exactly that. Gate a clean commit; write LOG/REPORT only once green.
* Read the gate verdict from its own log markers in a **separate step**,
  never a piped exit code (LESSONS L4).
* Host: 8 cores, shared with a production game server. `docker ps` **and**
  `pgrep -af tester-unified-gate.sh` before starting anything; wait, never
  race. Serial pytest under `nice -n 19 ionice -c 3`. `docker update
  --cpus=3` on the gate container **you** launched, right after it starts —
  identify it by the `--inner <your worktree>` argument in
  `docker ps --no-trunc`, because peer agents run containers from the same
  image (a peer's `run-gate-vbpub-ciu-*` container was live during this
  cut).
* `assay verify` is unaffected by every item; progress and resume state are
  diagnostic, never evidence. No verdict-schema change in this wave:
  `VERDICT_SCHEMA_VERSION` stays 10, `schema_version` stays 2,
  `inventory_schema` stays 1.
* One commit (or a small tight group) per backlog item, in order. Trailer:
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
* Commit from the worktree with `git -C <worktree> commit --only -- <paths>`
  (note: `-F <msgfile>` must come **before** `--only`, or git reads it as a
  pathspec).
* Do not dispatch a reviewer. That is the controller's step.

---

## Self-authored retention prompt (paste as the successor's `/compact` seed)

> **KEEP.** You are the successor implementer on the assay progress/resume
> wave, worktree `/workspaces/vbpub/.worktrees/assay-progress-resume`,
> branch `feat/assay-progress-resume-2026-09-08`, tip `7f2ba056`. B067 is
> DONE and committed; B064 → B065 → B066 remain, in that order. Read
> `assay/nyxloom-trove/reports/assay-WAVE-PROGRESS-RESUME-BRIEF.md` in full
> first, then `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-progress-resume.md`,
> then each remaining item's own section in
> `assay/nyxloom-trove/4-backlog.md`. Load-bearing seams, already located:
> `cli.py:626` (`validate_progress_destination`, where the progress stream
> must now be opened so `verdict_written` can be emitted after
> `write_verdict`); `runner._run_prepared_lane`'s
> `progress_path = progress_artifact if r2_declared else None` (the line
> that confines the stream to R2); `runner._execute_snapshot_unit`'s
> `result = execute_plan(` (where `command_started`/`command_finished` and
> the heartbeat go — and where it must NOT go for mutants);
> `mutation.progress_writer` / `_progress_event` /
> `_write_mutation_state_record` / `mutation_state_record_path` (the
> substrate all three items extend). Two settled rulings, not to be
> re-litigated: stall detection stays with the CALLER, never inside assay;
> the heartbeat is a pure time-based tick, never a percentage or an
> activity signal (that is B073, deliberately deferred). B067 added four
> `math.inf` → "no timeout" conversion boundaries (`execute_plan`,
> `git._sample_remaining`, `git._P22Deadline.remaining`,
> `isolation._check_timeout`); any new remainder-to-child-timeout site
> needs the same. B076 (unbounded R2 baseline is unbounded) is filed and
> explicitly out of scope.
>
> **DROP.** B067's own file-by-file implementation walkthrough, the config
> loader reading, the canary loader reading, and every intermediate test
> failure and its fix — all resolved, all recorded in `7f2ba056`'s message
> and in this brief.

---

## Gate — GREEN at `7f2ba056`

```
cd /workspaces/vbpub/assay
./run-gate.py --worktree /workspaces/vbpub/.worktrees/assay-progress-resume tester-unified
```

```
tester-unified: PASS (exit 0)
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

All **12** `ASSAY_GATE_PHASE` markers present (`wheel-installed` →
`attestation-hardened` → `verdict-v5-accepted` →
`lane-schema-v2-successors-verified` →
`verdict-v6-v7-v8-v9-hard-cut-verified` → `verdict-v10-successors-verified`
→ `judge-provenance-bound-to-the-installed-wheel` →
`self-hosted-lane-passed` → `topos-qualified` → `cmru-b006a-qualified` →
`independent-self-hosting-passed` → `pyflakes-clean`), **zero**
`ASSAY_GATE_DIAGNOSTIC` lines. Verdict read from the log's own markers in a
separate step, never from a piped exit code (LESSONS L4).

### Two runs, and what the first one teaches — READ THIS BEFORE YOUR GATE RUN

The FIRST run of this same commit went red,
`NO_MEASUREMENT`/`DIRTY_TREE`, and the cause was **me writing this very
file into the worktree while the gate was running**:

```
assay: NO_MEASUREMENT/DIRTY_TREE: the lane's own command left 1 uncommitted
file(s) in /workspaces/vbpub/.worktrees/assay-progress-resume/assay --
assay observed that tree CLEAN at 7f2ba056... immediately before starting
the command
Affected: assay/nyxloom-trove/reports/assay-WAVE-PROGRESS-RESUME-BRIEF.md
```

The wave prompt's warning is **stronger than it reads**. It says "do not
write your LOG/REPORT into the tree *before* your final gate run". The real
rule, measured: the `tester-unified` lane's own self-hosted `assay run`
judges the **LIVE worktree**, not the exact-OID clone the driver builds
from — so **write nothing into the tree at any point between launching the
gate and reading its verdict.** Park the file outside the tree (the session
scratchpad), run the gate, and move it in only once green. That is what
produced this file's own green.

Two further operational notes for the successor:

* **Identify YOUR container before capping it.** Peer agents run containers
  from the same `tester-unified:local` image; during this cut a peer's
  `run-gate-vbpub-ciu-*` container and a `cmru-release-*` assay lane were
  both live. Match on your own worktree path in the argv:
  `docker ps --no-trunc --format '{{.ID}}|{{.Command}}' | grep -F -- "--inner <your worktree>"`,
  then `docker update --cpus=3 <that id>` and nothing else.
* **The host really does saturate.** Between the two runs it reached load
  17 with ~5 GB RAM free and killed every background task this session had
  armed, including the one queued to relaunch the gate. Wait for load < 7,
  ≥ 6 GB available and no other gate process before launching; do not race.
