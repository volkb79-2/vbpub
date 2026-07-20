# Flow stages — architecture & config surface (D-060)

Status: **canonical** · authored 2026-07-20 · package B1 of Wave B
Authoritative parent: `docs/flow-system-review-and-redesign-CRITIQUE.md` §3.2–3.4, §4 Wave B.
This is the decision doc the CRITIQUE flags as B1 (`[D-060]`): the mechanism/policy
line, the stage schema, the pipeline config format, and how much per-project
divergence is allowed. It is a *decision*, not code — B2–B7 implement against it.

## D-060 (locked): stages-as-data, not a flow language
Wave B composes a **fixed set of code-backed stage KINDS** via a per-project
`pipeline` list. Dynamism beyond stage composition — user-defined states, actions,
or conditional flow expressions — is **rejected** (CRITIQUE §3.4): each reopens the
unchecked-invariant hole the frozen core exists to close, and nothing on the table
needs it. A genuinely new stage *kind* is a code change carrying the full
`test_invariants.py` matrix obligation — a feature, not a config knob.

## The mechanism/policy line
The single most important decision. It is drawn **exactly** where CRITIQUE §3.3 puts it:

| | **Mechanism** (frozen · identical for every project · invariant-tested) | **Policy** (per-project · data) |
|---|---|---|
| what | `TaskState` graph + `TASK_TRANSITIONS` edges; `AdmissionGate`; artifact binding; wrapper contract; storage; the reconcile phase order (lifecycle → attempts → waves → spec → carve); **each stage's implementation** | which stages appear, in what order; each stage's `concurrency` / `tier` / `retry` / `context` fields |
| where | `types.py`, `daemon.py`, stage code — code review + matrix tests | `pipeline = […]` (or a preset name) in the project config TOML |

**Corollary — admission is never per-stage policy.** Every launch of every stage
routes through the one `AdmissionGate` (pause/budget/route). A stage cannot opt out.
This is the R5 fix (A3) generalized: policy chooses *which* stages run, never
*whether* a running stage is admissible.

## Stage schema (the registered record)
A `Stage` is a registered, code-backed record (CRITIQUE §3.3) — never user logic:

```
Stage:
  name         "implement" | "self_review" | "frontier_review" | "triage"
               | "carve" | "post_merge_gate" | "auto_merge"     # the frozen menu of 7
  role         Role | None          # prompt + packet builder come from the role (one source)
  entry_state  TaskState            # the state a task enters this stage in
  exit_map     {outcome -> TaskState}   # every target edge MUST exist in TASK_TRANSITIONS
  concurrency  int | "serial"       # per-stage scheduling (replaces the lone max_active_tasks)
  tier         route tier           # model/effort via routes.toml (unchanged)
  retry        RetryPolicy          # max, escalation ladder, feed-context flag
  context      ContextPolicy        # packet: diff-only | wave-batch | +spine-digest | session-reuse
  # admission is NOT a field — always through AdmissionGate
```

### Stage kind → owned state region (grounded in the real frozen graph)
Every non-terminal `TaskState` is owned by **exactly one** stage in a composed
pipeline, or is a documented lifecycle/manual edge. This is the P43 closure
invariant promoted from declaration to *composition* (B2 ports it):

| Stage | Role | Owns | entry_state | exit_map (→ real TaskState) |
|---|---|---|---|---|
| carve | CARVER | READY_TO_CARVE | READY_TO_CARVE | done→CARVED · needs_decision→NEEDS_DECISION |
| implement | IMPLEMENTER | QUEUED, ACTIVE | QUEUED (dispatch→ACTIVE) | done→AWAITING_REVIEW¹ · incomplete→QUEUED · dead_end→BLOCKED |
| self_review | SELF_REVIEW | **SELF_REVIEWING** (new²) | SELF_REVIEWING | approved→AWAITING_REVIEW · rejected→ACTIVE (in-session loop) |
| frontier_review | FRONTIER_REVIEW | AWAITING_REVIEW | AWAITING_REVIEW | approved→MERGE_READY · rejected→REVIEW_REJECTED · incomplete→AWAITING_REVIEW (relaunch, A4) |
| triage | — (mech + cheap LLM) | REVIEW_REJECTED | REVIEW_REJECTED | fixable→QUEUED · stale/architectural→READY_TO_CARVE · product→NEEDS_DECISION |
| auto_merge | — (daemon) | MERGE_READY | MERGE_READY | merged→MERGED · refused→MERGE_READY (escalate, A11) |
| post_merge_gate | GATE | MERGED, VALIDATING | MERGED (→VALIDATING) | pass→COMPLETED · fail→BLOCKED |

¹ If `self_review` is the next stage, implement-done targets SELF_REVIEWING instead.
² **Frozen-graph addition (B5):** insert `SELF_REVIEWING` with edges
  ACTIVE→SELF_REVIEWING, SELF_REVIEWING→{ACTIVE, AWAITING_REVIEW}. Nothing routes
  into it unless the `self_review` stage is in the pipeline, so a pipeline without
  it is byte-identical to today (the state is simply unreachable). Adding it is the
  sanctioned "new stage kind = code change + matrix test" path.

**Lifecycle/manual edges (not stages):** DRAFT/NEEDS_DECISION (intake, human),
CARVED→QUEUED (queue admission, `ready_queue_target`-driven), BLOCKED→* (operator/
triage escalation), MERGED→VALIDATING (always, emitted by auto_merge),
VALIDATING→COMPLETED (immediate when no `post_merge_gate` stage is present).

## Pipeline config format
A new top-level key in the project config TOML — either a preset name or an explicit list:

```toml
pipeline = "gated"                                   # a named preset, OR:
pipeline = ["implement", "self_review", "frontier_review", "auto_merge", "post_merge_gate"]

[stage.frontier_review]     # optional per-stage policy overrides (all have safe defaults)
concurrency = "serial"
[stage.implement]
concurrency = 4             # replaces the old top-level policy.max_active_tasks
```

### Load-time validation (same rigor as `nyxloom lint`; B2 implements)
A pipeline is **rejected at config-load** (never a runtime surprise) unless:
1. Every stage name is in the frozen menu of 7.
2. Every `exit_map` target edge exists in `TASK_TRANSITIONS` (no invented transitions).
3. Every non-terminal state **reachable** in the composition is owned by exactly one
   stage or a documented lifecycle edge (the P43 closure test, run against the
   composition — `test_invariants.py` ported in B2).
4. There is a path to a terminal state (no unmergeable/unreachable pipeline).
5. `self_review`, if present, is immediately adjacent to `implement` (it shares
   implement's session via `context = session-reuse`).

This is where the mechanism/policy line is *enforced*: a project literally cannot
express a merge-without-review or a state no stage owns, even by fat-fingering the TOML.

## How much per-project divergence — DECISION
**Moderate / composed divergence** (the recommendation the operator asked me to make):
projects freely **compose and tune** from the frozen seven-stage menu; custom stage
*kinds*, states, transitions, and conditionals are **forbidden**. Concretely:

- **Composition is free but validated to close** against the frozen graph (rules above).
- **Every policy field is tunable** per stage (`concurrency`, `tier`, `retry`, `context`).
- **Presets ship so hand-authoring is the exception** (below).
- **A new kind is a code PR**, not a config edit.

This delivers every capability the operator asked for (self-review, per-stage
serial/parallel, token-optimized broad-vision review, strategic carver, smart triage)
while making the invariant surface *smaller* than today's per-project `plan_project`
prose — the divergence lives in **composition**, never in the **semantics** of a stage.

### Presets (ship these three)
| Preset | Pipeline | For |
|---|---|---|
| `full` | carve → implement → self_review → frontier_review → auto_merge → post_merge_gate → triage | self-developing projects (nyxloom itself) |
| `gated` | implement → self_review → frontier_review → auto_merge → post_merge_gate | projects with a real gate, externally-fed handoffs (dstdns) |
| `lean` | implement → self_review → frontier_review → auto_merge | low-ceremony projects, no gate |

**Operator decisions baked in (2026-07-20):**
- **`self_review` is in every preset.** It runs in the implementer's warm session
  (`context = session-reuse`), so a self-reject fix loop pays no 35–40k startup tax,
  and the expensive frontier reviewer only ever sees already-self-checked diffs. It is
  near-free and strictly raises review signal. (A project may still drop it explicitly;
  the *default* is on.) This also closes long-standing task #15 and un-reserves
  `Role.SELF_REVIEW` (B5 updates the P43 reservation guard).
- **`triage` is opt-in (`full` only).** Without it, a REVIEW_REJECTED task falls back
  to the built-in default (requeue under `max_attempts_per_task`, escalate to BLOCKED
  when exhausted) — fine for projects whose rejects are almost always "just fix and
  retry." Triage *adds* drift-to-carver routing + LLM classification (fixable /
  architectural / product), which earns its per-reject model call only on a
  self-developing project. Note the infra-vs-rejection split (M7) is already in the
  frozen core post-A4, so omitting triage never reintroduces it.

## Concurrency, wave-batch, session-reuse (the operator's parallel question)
- **Parallel implementation:** `concurrency` on the `implement` stage replaces the
  lone `policy.max_active_tasks` (B3). Implement can run N-parallel while
  `frontier_review` is `serial` (which also restores gate-safety — one review session
  at a time). Strictly more capable than today's single global knob.
- **One reviewer over several packages:** already live (A9/P61 wave batching) — one
  frontier session takes a whole wave (`wave_max_diffs`) and emits per-task verdicts.
  B6 adds `context = session-reuse` so the reviewer keeps its warm session across a
  wave/cycle for cache hits (D-R10, unblocked now that A7 verdict-binding makes
  resumed sessions safe).

## Decisions locked / resolved
- **D-060** (locked): stages-as-data, not a flow language.
- **D-061** (resolved 2026-07-20): the progress ratchet is FIXED (P64), not retired.
- **D-062** (resolved 2026-07-20 — my call, no sign-off): an operator-initiated
  *targeted* carve is an **explicit, audited override** of pause, not a silent bypass.
  It still passes through `AdmissionGate` (A3) but carries an operator-override token
  that the gate records in the event log. A human asking for a specific carve during a
  pause is a legitimate intent; the audit trail makes it honest. (Today M15 makes it an
  accidental total bypass — the fix folds into A3's gate.)

## Sequenced implementation (B2–B7) — proofs keep each package honest
- **B2 (P70)** stage registry + composed-pipeline validation. `reconcile.py` thins
  toward an engine that walks the pipeline. **Parity proof:** for the *pre-B2 default*
  pipeline (implement → frontier_review → auto_merge, no self_review), the engine emits
  the byte-identical action plan to today's hardcoded `plan_project`. This proves B2 is
  a pure refactor; behavior changes come only in later packages.
- **B3 (P71)** per-stage `concurrency`; review defaults `serial`; gates
  async-with-timeout so a slow gate can't block another project's pass.
- **B4 (P72)** triage stage (mechanical tier: drift-guard I4 + infra classes from A4;
  LLM tier: fixable/architectural/product). Re-dispatch packets embed the review verdict.
- **B5 (P73)** self_review stage: add SELF_REVIEWING + edges, un-reserve
  `Role.SELF_REVIEW`, update the P43 guard. **Proof:** self_review-enabled pipeline runs
  implement→self_review→frontier_review on the fake CLI; **disabled pipeline byte-identical
  to today.** Becomes the new default (self_review in every preset).
- **B6 (P74)** reviewer session-reuse (`context = session-reuse` via `build_resume`) +
  carver-maintained `SPINE-DIGEST.md` referenced-by-pointer in review/carve packets.
- **B7 (P75)** carver re-scope entry: triage "architectural/stale" packaged into a carve
  packet (handoff + verdict + drift report); original task SUPERSEDED only *after* the
  carve dispatch actually launches (uses A10 atomicity). Adds a RESCOPED carve outcome.

## Build discipline (unchanged from Wave A)
Worktree `feat/flow-stages` under `.worktrees/`; gate ONLY via `tester-unified`
(never the devcontainer); merge with the shared-main `merge-tree` + CAS discipline
(the repo is shared with the operator's own commits). B2/B3 (the engine) are done by
hand; B4–B7 (additive stages) may be dogfooded through the hardened daemon once B2/B3
are proven live.
