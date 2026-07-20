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
| self_review | SELF_REVIEW | **SELF_REVIEWING** (new²) | SELF_REVIEWING | approved→AWAITING_REVIEW · rejected→QUEUED (fresh fix attempt; D-063) |
| frontier_review | FRONTIER_REVIEW | AWAITING_REVIEW | AWAITING_REVIEW | approved→MERGE_READY · rejected→REVIEW_REJECTED · incomplete→AWAITING_REVIEW (relaunch, A4) |
| triage | — (mech + cheap LLM) | REVIEW_REJECTED | REVIEW_REJECTED | fixable→QUEUED · stale/architectural→READY_TO_CARVE · product→NEEDS_DECISION |
| auto_merge | — (daemon) | MERGE_READY | MERGE_READY | merged→MERGED · refused→MERGE_READY (escalate, A11) |
| post_merge_gate | GATE | MERGED, VALIDATING | MERGED (→VALIDATING) | pass→COMPLETED · fail→BLOCKED |

¹ If `self_review` is the next stage, implement-done targets SELF_REVIEWING instead.
² **Frozen-graph addition (B5, DONE 2026-07-20):** insert `SELF_REVIEWING` with edges
  ACTIVE→SELF_REVIEWING, SELF_REVIEWING→{AWAITING_REVIEW, QUEUED, BLOCKED} (+ the
  universal SUPERSEDED/CANCELLED). A reject routes to QUEUED (a fresh fix attempt), NOT
  back to ACTIVE — see D-063 for why the warm in-session fix loop was deferred. Nothing
  routes into SELF_REVIEWING unless the `self_review` stage is in the pipeline, so a
  legacy pipeline without it plans byte-identically to today. Adding the state is the
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
- **`self_review` is the compiled default AND in every preset.** It runs as a WARM
  resume of the implementer's session (`context = session-reuse`), so the self-check
  pays no 35–40k startup tax and the expensive frontier reviewer only ever sees
  already-self-checked diffs — near-free, strictly raises review signal. **Greenfield
  decision (operator, 2026-07-20):** with no external byte-compat contract to preserve,
  `DEFAULT_PIPELINE` itself includes self_review (`full` aliases it) — the compiled
  default IS the recommended flow, not a legacy subset. A project may still compose an
  explicit legacy list to drop it (proven byte-identical by test). On a REJECT the leg
  routes to QUEUED (a fresh fix attempt), not ACTIVE — see D-063. This closes
  long-standing task #15 and un-reserves `Role.SELF_REVIEW` (RESERVED_ROLES is now
  empty; every role is dispatched).
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
- **D-063** (resolved 2026-07-20 — my call during B5, no sign-off): on a self-review
  REJECT the task routes to **QUEUED (a fresh, budget-bounded fix attempt)**, NOT to
  ACTIVE as B1 first sketched. Routing a reject back to ACTIVE re-exposes the
  ACTIVE-scoped stale-implementer-receipt re-consumption the proven frontier reject loop
  deliberately avoids (it parks in a non-ACTIVE state), and would need novel
  loop-termination + receipt-archival machinery to be safe. The self-review ATTEMPT is
  still warm (the primary "cheap, every time" win is fully delivered); only the *fix*
  after a reject is a fresh cold attempt. The warm in-session fix loop (rejected→ACTIVE
  + implementer resume) is a deferred optimization for once the gate is proven live.
- **D-064** (design 2026-07-20 — test-completeness enforcement, from the operator's
  question): implementer-generated tests are structurally happy-path-biased, so add
  test-completeness as a LAYERED discipline, NOT one big LLM pass:
  1. **self_review is oracle-anchored + negative-checked** (done, B5-hardened): run each
     oracle's observable on REAL data and check its NEGATIVE (the edge case; a test that
     also passes on the negative is HOLLOW). This is MECHANICAL — deliberately not "review
     with fresh eyes / reflect", which AUTHORING flags as false confidence (models are
     poor judges of what they missed; the historical P40 prompt already rejected it). The
     operator's "test edge cases, not just the happy path" IS this negative check.
  2. **Mechanical diff-coverage gate** (planned, pairs with #57): fail the gate when
     changed/added source lines have no test hitting them. Deterministic, no LLM — would
     have caught the B5 `_attempt_scan` gap. The reliable floor coverage-% can enforce.
  3. **`test_audit` as a 2nd turn of the frontier_review session** (folds into B6): after
     the COLD reviewer's correctness pass, a second prompt in the SAME (session-reused)
     review session audits test completeness — hollow tests, missing negatives, un-tested
     ripple. Cold+independent (unlike warm self_review, which shares the implementer's
     blind spots) and cache-cheap (reuses the review session). Opt-in like triage.
- **D-066** (resolved 2026-07-20 — my call during B4b, no sign-off): the reject-triage
  **Tier-2 classifier is the frontier reviewer itself**, not a separate cheap-model call
  (as the critique's prose literally sketched) nor a new dispatched triage leg. The daemon
  has no inline-completion primitive — its only model interaction is launching agents — so a
  synchronous classification call inside the reconcile input-build is architecturally
  impossible, and a dispatched TRIAGE leg would be a whole B5-sized package. Instead the
  reviewer, which already read the full diff to reach REJECTED, stamps one extra
  `REJECT_CLASS: <fixable|architectural|product>` line into the same committed report. It is
  the cheapest correct classifier (full context, zero extra dispatch), fits stages-as-data
  (enriches the existing frontier_review output rather than adding a stage), and satisfies
  the critique's matrix oracle + its "no bare same-model retry" ban. If the reviewer omits
  the line (older reviewer, or an infra 'incomplete' leg), the task is unclassified and falls
  to the mechanical attempt-budget path — graceful degradation, byte-identical to A4a.
- **D-065** (design 2026-07-20 — strategic test-health, from the operator's question): the
  strategic carver already exists as the untargeted *headroom-refill* CarveDispatch
  (reconcile item 9 — carves from backlog/roadmap/review-follow-ups when ready_count <
  carve_ahead_target). Add a seldom-run, project-WIDE **test-health trigger** of the same
  shape: it steps back from per-task work, evaluates suite-wide test debt, and carves
  test-improvement tasks — the test analog of the strategic carver reading the north star.

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
  - **B4a — DONE 2026-07-20.** Pipeline-aware exhausted-reject routing: attempts remain →
    QUEUED; exhausted + carve present → READY_TO_CARVE; exhausted + carve-less (gated/lean)
    → NEEDS_DECISION (so a carve-less pipeline still closes). Presets made real.
  - **B4b — DONE 2026-07-20 (hand-driven).** The full reject-triage matrix
    {infra, stale-premise, fixable, architectural, product} (D-066). `ReconcileInput`
    gains `head_revision` (daemon `git rev-parse main`) + `triage_class`; reconcile routes
    in precedence product→NEEDS_DECISION > stale-premise(I4 drift)→re-carve >
    architectural→re-carve > fixable/unclassified→mechanical budget, with the carve-less
    escalation to NEEDS_DECISION preserved for drift/architectural. The frontier reviewer
    self-stamps `REJECT_CLASS` (Tier-2 producer — no new leg, no inline model call), the
    daemon records it in REVIEW_RECORDED and derives `triage_class` from the latest event,
    and the implementer re-dispatch embeds the review prose (`prior_verdict`) so a fixable
    requeue is targeted, never a bare same-model retry. **Proof shipped:** the I4 property
    test (+ placeholder/abbreviated/unknown negatives), the four semantic routes each with
    a differently-routing negative, product-beats-drift precedence, carve-less closure,
    graceful degradation to A4a; daemon tests for REJECT_CLASS→event (+approved/no-line
    negatives), `_parse_reject_class`/`_review_rationale`/`_head_revision`/`_triage_classes`
    units (+negatives incl. stale-class-bleed), the DispatchImplementer verdict-embed
    end-to-end, and a `_build_input` plumbing gap-closer (the B5 dead-wire lesson).
    **Deferred (explicitly optional in the critique):** the RetryPolicy route-escalation
    ladder for a fixable requeue.
- **B5 (P73) — DONE 2026-07-20 (hand-driven).** self_review stage: added SELF_REVIEWING
  + edges, the `LaunchSelfReview` action (a warm resume borrowing the implementer's
  session_handle), the daemon consumption branch (approved→AWAITING_REVIEW,
  rejected→QUEUED, missing→AWAITING_REVIEW graceful), un-reserved `Role.SELF_REVIEW`,
  flipped the P43 guard, and made it the compiled default (greenfield). **Proof shipped:**
  daemon tests for implement-done→SELF_REVIEWING (default) vs →AWAITING_REVIEW (legacy),
  the three verdict outcomes, and the warm-borrowed-session launch; reconcile tests for
  LaunchSelfReview planning + in-flight guard + drain-parking; stages tests for the
  adjacency rule (rule 5). Reject routes to QUEUED (D-063); the warm in-session fix loop
  is deferred.
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
