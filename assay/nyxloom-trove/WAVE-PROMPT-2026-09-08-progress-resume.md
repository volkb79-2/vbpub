# Wave prompt — progress/resume family (2026-09-08)

Branch: `feat/assay-progress-resume-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-progress-resume`
Controller log: `assay/nyxloom-trove/reports/assay-WAVE-PROGRESS-RESUME-CONTROLLER-LOG.md`

No verdict-schema change in this wave. `VERDICT_SCHEMA_VERSION` stays 10,
`assay.toml`'s `schema_version` stays 2, `assay lanes --json`'s
`inventory_schema` stays 1. `assay verify` is unaffected by every item —
progress files and resume state are diagnostic, never evidence.

## Context to read first

1. `assay/nyxloom-trove/4-backlog.md` — read each item's own full section:
   **B067** (`budget = "unbounded"`), **B064** (R0/R1 progress stream),
   **B065** (progress event enrichment), **B066** (`--state-dir`). Each
   has a measured baseline and acceptance criteria — this prompt sequences
   and rules the open forks, it does not repeat every detail.
2. `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-b068-quickwins.md` (the
   prior wave's prompt) for the shape of a well-run wave on this project —
   same host-load/checkpoint/gate discipline applies here.
3. `assay/CHANGES.md`'s `[5.1.0]` entry — the current shipped baseline.
4. `mutation.py`'s existing `--progress`/`--resume` machinery
   (`_progress_event`, `write_progress`, `_write_mutation_state_record`,
   `mutation_state_record_path`) — B064/65/66 all extend this same
   substrate rather than inventing a new one.

## Order and rulings

### 1. B067 — `budget = "unbounded"`, do this first (the others build on it)

Allow `budget = "unbounded"` **only when every unit of the lane's work
carries its own bound**: an R2 (mutation) lane requires
`judge.mutation.budget_per_candidate`; an R3 canary requires the
per-attempt bound (if R3 canary multi-target/B007 already ships a
per-attempt structure, thread through it; if not, this entry's own
acceptance criteria define the minimum shape). An R0/R1 lane is ONE
command with no sub-unit to bound — `budget = "unbounded"` is refused
there **by name**, naming the reason (an R0/R1 lane has no per-unit
bound to require).

**Ruling, already settled — do not re-litigate:** stall detection stays
with the CALLER (run-gate, or whatever orchestrator comes later), never
with assay itself. assay's job stops at making the observable signal
(B065) rich enough for an external watcher to compute staleness; it does
not become the watcher, and it does not gain a stall-threshold config of
its own. If your implementation instinct is to add any kind of
"lane looks stuck, abort early" logic inside assay, that is out of scope
— stop and flag it rather than building it.

The lane-wide deadline machinery for a NUMERIC budget is unchanged. Stall
detection remains entirely the caller's job (run-gate RG-36).

### 2. B064 — R0/R1 phase-boundary progress stream, WITH a heartbeat

The plain phase boundaries the backlog entry names: `run` header →
`snapshot_materialized` → `command_started` → **(new, see below)** →
`command_finished` (with exit status) → `coverage_parsed` (R1 lanes only)
→ `verdict_written`. Same closed vocabulary, same shape, at every rigor
tier — R0/R1 just has far fewer events than R2's per-candidate stream,
which is expected and correct (there is no per-unit to iterate).

**Ruling, already settled — do not re-derive:** add a **time-based
heartbeat** emitted on a fixed interval WHILE the lane's command is
running (`runner.py`'s `execute_plan`/`execute_command`, currently a
single blocking `subprocess.run`). This is a lightweight background
thread/timer that ticks `{"event": "command_running", "emitted_at": ...,
"elapsed_s": ...}` at the configured interval, cancelled the moment the
command returns — it does **not** parse the subprocess's own stdout/
stderr in any way (no activity/byte-count tracking, no percentage, no
per-tool awareness). That is the explicitly separate, explicitly
deferred, explicitly bigger scope of **B073** (filed, not this wave —
do not build any part of it here even if it looks tempting).

**New CLI option: `--progress-heartbeat SECONDS`**, default `60`, only
meaningful when `--progress PATH` is also passed (no-op otherwise —
follow the same validation shape `--progress`'s destination already
uses). Floor of 5 seconds — a value below that refuses at load naming
the floor, to prevent a misconfigured value from flooding the progress
file. Document the default and the floor in `docs/CONSUMERS.md`'s
progress paragraph.

### 3. B065 — enrich every progress event, not just B064's new ones

Every event `write_progress` emits — the EXISTING mutation-sweep events
(`run`, `shard`, `resume`, per-candidate) as well as B064's new R0/R1
events — gains:
- `emitted_at` (UTC, ISO 8601, matching this project's existing
  `iso_utc(clock())` convention),
- `elapsed_s` (monotonic seconds since the `run` header's own
  `started`/`emitted_at`).

Each COMPLETED-candidate event (mutation sweep) additionally carries its
`outcome_bucket` — check whether this already exists (`mutation.py`'s
`write_progress` call at the per-candidate site already has
`outcome_bucket`/`elapsed_seconds` per B071's own recent discovery; if
so, this item is about propagating the SAME enrichment to the `run`
header and to B064's new R0/R1 events, not duplicating it).

The `run` header gains `candidate_total` (mutation sweep; already
present per earlier code), `budget_s`, and `budget_per_candidate_s` where
applicable, so a reader knows the bounds without the lane file.

A terminal `end` event (new) carries bucket counts for the mutation
sweep, so a reader can tell a finished run from a dead one without
reading the verdict. For B064's R0/R1 stream, `verdict_written` already
serves as the terminal event — no separate `end` event needed there.

**Acceptance, adapted from the backlog entry:** a reader with ONLY the
progress file computes rate/ETA/last-event-age for a REAL run (not a
fixture), agreeing with the verdict's own counts and measured wall time;
`assay verify` is unaffected (the stream is not evidence);
`docs/CONSUMERS.md`'s progress paragraph names every field.

### 4. B066 — `--state-dir PATH`

`assay run … --state-dir PATH`, default unchanged (today's
`<project_root>/.assay/mutation-state/`), validated the same way
`--progress`'s destination is (outside the repository or git-ignored; a
directory, created on demand). Mutation records move under it. Verify
and refusal semantics unchanged — this is purely a relocation of where
resume state lives, not a change to what it contains (B071's new
`result_stdout_tail`/`result_stderr_tail` fields on `crashed` records
travel with it unchanged).

**Acceptance, adapted from the backlog entry:** two runs of the same
commit from two DIFFERENT worktrees sharing one `--state-dir`: the
second genuinely resumes (`event: resume`, `resumed_total > 0`); a
source edit between them re-executes the touched file's candidates. A
`--state-dir` inside the judged tree and not git-ignored refuses before
any work starts, naming the reason.

## Binding constraints (every item)

- `assay verify` is unaffected by every item in this wave — none of it
  is evidence.
- Read the registered gate's verdict from its own log markers after it
  finishes, never from a piped exit code (LESSONS L4).
- Host: 8 cores shared with a production game server. `docker ps` AND
  `pgrep -af tester-unified-gate.sh` before starting anything — wait for
  any existing gate container/process, never race. Serial pytest under
  `nice -n 19 ionice -c 3`. `docker update --cpus=3` right after any gate
  container you launch starts.
- **Do NOT write your LOG/REPORT into the tree before your final gate
  run** — the prior wave's implementer hit `NO_MEASUREMENT/DIRTY_TREE`
  by doing exactly this. Run the gate on a clean commit; write LOG/REPORT
  and commit them only after you have a green verdict to report.
- **Checkpoint clause (E-008):** if you cross ~120k context tokens or
  ~60 tool calls, cut at the next coherent boundary (green gate > commit
  > LOG/REPORT write; never on a red gate) and write a continuation
  brief to `assay/nyxloom-trove/reports/assay-WAVE-PROGRESS-RESUME-BRIEF.md`
  plus a self-authored `/compact`-retention prompt, commit, and stop.
- Commit trailer:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```
- When all four items are done and the registered gate is green, write
  `assay/nyxloom-trove/reports/assay-WAVE-PROGRESS-RESUME-LOG.md` (what
  you did, per item, with commit hashes) and a REPORT summarizing
  acceptance-box status per item, then stop — do not dispatch a reviewer
  yourself, the controller does that next.
