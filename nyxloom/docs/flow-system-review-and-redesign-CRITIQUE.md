# Flow-System Review — Fable-xhigh Adversarial Critique

**Status:** frontier critique of `docs/flow-system-review-and-redesign.md` (the Sonnet draft) · Fable xhigh, 2026-07-19
**Method:** read-only deep review of `reconcile.py` (full), `daemon.py` (all named surfaces + `_execute` in full), `types.py`, `config.py`, `adapters.py`, `storage.py`, `wrapper.py`, `watchdog.py`, `cli.py` (merge/pause), and the five test suites. Every defect claim below is grounded in file:line as read on this date. Sections: §1 Attack · §2 Missed defects (19, ranked) · §3 Redesign · §4 Sequenced package plan.

---

## 1. ATTACK — is §1–§2 of the draft right?

**Verdict: directionally correct, but under-dimensioned in a way that matters.** The slogan "strong local correctness / zero cross-cutting-invariant enforcement" is true and the evidence is real. But the four-root-cause taxonomy (R1 guard scatter, R2 retrofit-role incompleteness, R3 seam-mocked tests, R4 diff-scoped review) is a cut that re-explains the five past incidents while missing the two dimensions that produce the *worst* of the still-latent defects found below. If the remedy list in draft §3 were built exactly as written, defects M1, M5, M10, M12, M13 and M21 in §2 below would all survive it.

### 1.1 Missing root cause R5: **plan-time guards, execute-time effects — no admission check at the effect boundary**

Every guard the draft catalogs (I1, I2, I6…) is evaluated inside `plan_project` against a **snapshot**; the daemon then executes minutes-of-side-effects later with **no re-check**. Concretely:

- `run_pass` builds input, plans, then executes the whole action list (`daemon.py:612-618`). The watchdog can *auto-pause the project mid-pass* (`daemon.py:1214-1217`, `_auto_pause_for_runaway` writes the flag) and the remaining planned dispatch/review/carve actions of that same pass still execute — the pause takes effect only next pass.
- `_execute` for `DispatchImplementer` (`daemon.py:2244-2285`), `LaunchReview` (`daemon.py:2474-2620`) and `_execute_carve_dispatch` (`daemon.py:1871-1957`) re-load `routes.toml` fresh (`daemon.py:2251`, `2589`, `1881`) but re-check **nothing else**: not pause, not budget, not provider health, not leases.
- `dispatch_targeted_carve` (`daemon.py:638-655`) bypasses `plan_project` entirely; P47 bolted the carve mutex onto it but **not the pause guard** — the P52 class is not actually closed (see M15).

P47's race itself was a plan/execute phase bug ("the scan is a plain read … not atomic with the write that follows", `daemon.py:283-289`). The draft's proposed `dispatch_admissible()` (§3.1) is necessary but the draft never says **where it binds**. If it binds in the planner it re-fixes the past and leaves the class open. It must bind at the **effect boundary** — inside `_execute`/wrapper-launch — with the planner's copy demoted to an optimization.

### 1.2 Missing root cause R6: **unversioned filesystem artifacts as the daemon↔agent interface**

`receipt.json`, `CARVE-<seq>.md`, `<task>-REVIEW.md` are shared mutable files with **no run/attempt binding**. Four incidents/defects are all the same shape — "an artifact read at the wrong place or from the wrong run":

- P51 (report path wrong for one worktree layout) — and its fix is *still* wrong for the other layout (M5).
- P40 round-2 (stale on-branch `-REVIEW.md` re-parsed) — the draft's I8.
- M1 below (stale interrupt receipt consumed after resume) — the receipt-flavored sibling of I8, **worse in consequence**, absent from the draft's catalog.
- M6 below (verdict pooled from any file on the branch that merely *mentions* the task id, `daemon.py:2185`).

The draft treats I5/I8 as isolated invariants. They are one class: **every artifact must be bound to the (attempt, run) that produced it, resolved at write time, and consumption must verify the binding.** That principle, stated once, generates all four fixes.

### 1.3 R1 is real but the surface is undercounted

The draft says "six new-agent-process dispatch sites." The actual launch surface is at least **eleven**: the six it counts, plus `dispatch_targeted_carve` (`daemon.py:638`), plus four `build_dispatch`+launch call sites entirely outside daemon.py — `intake_chat.py:378`, `decision_chat.py:406`, `onboarding_scan.py:451`, `onboarding_questionnaire.py:530` (all on the IMPLEMENTER-default prompt per `adapters.py:177-184`, so an intake interviewer is told "you MUST git add and git commit ALL your work"). An admission gate scoped to reconcile *actions* misses five of eleven sites. The gate has to sit at the **launch chokepoint** (`wrapper.launch_detached` / a mandatory wrapper around it), not at the action-type level.

### 1.4 R3 (hollow tests) is right but mis-specified

The proposed remedy — "flag tests which mock `plan_project`" (§3.3) — would not have caught the two worst latent defects below, because they are not mocked-seam bugs; they are **counterfeit-input** bugs:

- `test_receipt_interrupted_with_session_handle_resume` (`test_reconcile.py:953-985`) seeds `receipt=None` for the INTERRUPTED attempt. The real wrapper **always writes a receipt before ATTEMPT_INTERRUPTED** (`wrapper.py:386-390` + `423-434`). The tested input cannot occur via the wrapper path; the input that *does* occur (receipt on disk + resumed RUNNING attempt) is untested and broken (M1).
- `test_waves_oldest_over_timeout_opens` (`test_reconcile.py:1899-1932`) makes the lexicographically-first task also the oldest, so the "oldest waits too long" rule passes while the code actually checks the *sorted-first* task (M11).
- `test_waves_no_duplicate_review_launch_while_preflighting` (`test_reconcile.py:2408-2438`) filters only `LaunchReview` out of the plan, and so certifies as correct a pass that plans **both** `LaunchReview` and `Transition(BLOCKED)` for the same task (M10).

The durable fix is not a mocking lint but **trace-level tests**: drive plan→execute→wrapper-artifact cycles over multiple passes with a fake CLI, asserting whole-plan consistency (no contradictory actions; inputs generated by the real wrapper contract, not hand-built). Note the draft's R3 evidence is also slightly stale: post-P50, `tests/test_carver.py:163,203` are genuinely un-mocked (2 of ~13).

### 1.5 Overstatements and misattributions

- **P49 is not really cross-cutting.** The streak lifecycle bug lived inside one function's in-memory dict (`daemon.py:1198-1203`). Calling it "pause-state ↔ counter lifecycle" stretches the unifying pattern to cover all five incidents — mild just-so-story symptom. The pattern is real for P50/P51/P52; it does not need P49.
- **§1.4 overclaims P43's reach.** The P43 scan is a regex over `daemon.py`+`reconcile.py` for `role=Role.X` (`test_types.py:33-40`). The four non-daemon launch sites never pass `role`, so they are structurally invisible to it; a role named in a comment would count as "dispatched." It worked, but it is weaker than "clean and it worked" suggests.
- **§5.4's factual premise is wrong: wave batching does not exist.** `plan_project` emits `LaunchReview(..., task_ids=[task_id])` **per task** (`reconcile.py:927`); `WAVE_OPENED` merely stamps a shared `wave_id` (`storage.py:211-216`). A "wave" of 3 launches **three separate frontier sessions**, each paying the measured ~35-40k startup tax (see M3). The draft's token-optimization section builds on an amortization mechanism that is currently a label, not a mechanism.
- **§2's I1 is under-specified.** Pause is only one admission dimension. Budget is equally scattered: `ResumeAttempt` is planned with **no budget check** (`reconcile.py:789-794`) and `LaunchReview` with **no budget, route-health, or provider check** (`reconcile.py:907-927`) — a budget-exhausted project stops implementer dispatch and carving but keeps launching the *most expensive* legs (M9). Task-level pause (`tsf.paused`) is honored only by `_check_paused` (dispatch/fresh-start); auto-merge, wave launch, and the READY_TO_CARVE carve handler ignore it (M18).
- **§2's I3 site list is one dimension short.** "Role × site" misses Role × **AttemptState** (FAILED and CREATED attempts have no handler for two of three roles — M2) and Role × **ReceiptResult** (`_consume_carve_exit` ignores `receipt.result` entirely — M4).

### 1.6 What the draft got right

To be explicit: the frozen `TASK_TRANSITIONS` core + pre-append validation (`storage.py:313-345`) is genuinely solid; the incident attributions for P50/P51/P52 are accurate; the P43-generalization instinct is correct; §4's "a rejection is not a retry signal" is correct and is independently re-derived by M7 below from a different direction (infra-failure conflation). The draft's biggest virtue — grounding in live incidents — is also its bias: it catalogs invariants the incidents already revealed, and its §2 list contains **zero** invariants that have not yet fired. §2 below is the list of ones that will.

---

## 2. MISSED DEFECTS — latent P50/P51/P52-class bugs not in the draft's §2

Ranked by (confidence × severity × likelihood-to-fire). "Fires live" = will occur under current deployed behavior without adversarial input.

### M1 — Stale interrupt receipt is consumed one pass after a resume: premature task exit + concurrent double-dispatch  ⚠ CRITICAL, fires on every wrapper-mediated interrupt→resume cycle

- The wrapper writes `receipt.json` on **every** exit classification, including interrupted (`wrapper.py:423-434`; interrupted classification `wrapper.py:386-390`).
- `ResumeAttempt` execution relaunches into the same attempt dir and **never clears the old receipt** (`daemon.py:2287-2317`; no unlink of `receipt.json` anywhere in `src/`).
- `ATTEMPT_RESUMED` sets the attempt back to RUNNING (`daemon.py:2307`).
- Next pass: `_attempt_scan` reads the stale receipt for the non-terminal attempt (`daemon.py:885-893`) and `plan_project` treats RUNNING+receipt as "wrapper died before its exit event" → `EmitAttemptExit` (`reconcile.py:707-709`).
- `_execute` heals the attempt to EXITED **with the stale ERROR/interrupted receipt** and runs the ERROR path (`daemon.py:2383-2397`, `2451-2464`): task → QUEUED (attempt budget stolen) → next pass **dispatches a second implementer into the same `feat/<task>` worktree while the resumed session is still running**. When the resumed wrapper later exits DONE and overwrites the receipt, the now-EXITED-ACTIVE-IMPLEMENTER re-scan branch (`daemon.py:877-884`) consumes it *again* → task jumps to AWAITING_REVIEW while implementer #2 is mid-flight; a review can launch over a half-written tree.
- Why tests are green: the only resume tests seed `receipt=None` (`test_reconcile.py:961`), an input the wrapper contract cannot produce.
- Failure scenario: any confirmed stall or wall-clock-cap interrupt (both wrapper-mediated SIGTERM, `daemon.py:2319-2354`) followed by a successful session resume. Deterministic within one reconcile interval (~30 s) of the resume.

### M2 — `ATTEMPT_FAILED` is unconsumed: lease-race losers permanently strand their task; a carve lease-race permanently deadlocks all carving  ⚠ CRITICAL

- The wrapper's lease-race loser writes a receipt and `ATTEMPT_FAILED` (`wrapper.py:189-225`) — exactly the "clean exit 75" path P47 advertises (`daemon.py:280-307`).
- `_attempt_scan` skips **all** terminal attempts except three EXITED special cases (`daemon.py:877-884`); `plan_project` has no branch for FAILED attempts either (`reconcile.py:700-763` covers RUNNING/PREFLIGHTING/STALLED/EXITED/INTERRUPTED only).
- Consequence per role: IMPLEMENTER → task stuck ACTIVE forever, silently eating a wip slot (`reconcile.py:650-654` counts it in `active_count`). CARVER → the synthetic carve task stays ACTIVE forever → `carve_in_flight` is permanently True (`reconcile.py:591-595`) → **no carve can ever dispatch again**, no escalation, nothing in the event log but one ATTEMPT_FAILED. (FRONTIER_REVIEW is incidentally safe: the wave loop relaunches over a FAILED latest attempt, `reconcile.py:914-925`.)
- Likelihood: higher than P47 suggests, because the planner *creates* same-pass lease collisions itself: the dispatch loop checks `leases_free` from the snapshot and never decrements it as it plans (`reconcile.py:667-683`, `_check_lease` `reconcile.py:1038-1044`) — two QUEUED tasks sharing a capacity-1 mutex are both dispatched in one pass whenever wip capacity ≥ 2, and one *must* lose the flock race.

### M3 — Wave review batching does not exist: one frontier session per task, `wave_max_diffs` is decorative  ⚠ HIGH (cost + correctness-adjacent)

- `plan_project` iterates per task and always emits singleton `task_ids=[task_id]` (`reconcile.py:907-927`); this predates the 2026-07-17 stale-wave fix (its diff shows the singleton already present).
- `WAVE_OPENED` only stamps `wave_id` on each task (`storage.py:211-216`); next pass every stamped task independently fails `has_review_in_flight` (its own `tsf.attempts` has no review attempt — the attempt lands only on the launched task, `daemon.py:2596-2601`) → N parallel `LaunchReview` executions, N frontier sessions, N × the ~35-40k startup tax the draft measured. The multi-task packet loop in `_execute` (`daemon.py:2532-2586`) is dead generality.
- Also correctness-adjacent: N parallel reviewers are told to re-run the gate in the same repo concurrently (`daemon.py:2503-2504`) with **no leases on the review wrapper** (`daemon.py:2610-2614` passes none) — gate collisions on stack-exclusive projects.
- No test covers a multi-task wave's launch count; `test_waves_launch_review_no_running_attempt` uses one task (`test_reconcile.py:1966-2004`).

### M4 — `_consume_carve_exit` ignores `receipt.result`: a rate-limited carver becomes "carve-parse-failed" + an immediate re-dispatch burn loop  ⚠ HIGH

- The carver branch consumes the report file only and never looks at the receipt (`daemon.py:1959-2055`; `_execute` reads `result` at `2401` but the CARVER branch at `2403-2409` returns before any result branching).
- A carver hitting a provider limit (receipt LIMIT) or erroring writes no report → `NEEDS_OPERATOR{carve-parse-failed}` (`daemon.py:2046-2049`) → task SUPERSEDED → carve slot free → **next pass dispatches a fresh carver into the same rate limit**. No `ProviderPause` is ever applied (contrast the implementer LIMIT path, `daemon.py:2448-2450`).
- The watchdog cannot see it: attempt-loop counts ATTEMPT_CREATED **per task_id** (`watchdog.py:150-159`) and every carve mints a fresh `carve-<project>-<seq>` task. Only the NEEDS_OPERATOR reason-storm (>5/hr) eventually trips graduated auto-pause — after ~6 wasted frontier dispatches and 6 operator pings.

### M5 — P51's report-path fix is itself wrong for repo-root projects: dstdns branch-authority carves will all parse-fail  ⚠ HIGH, deterministic when it fires

- The fix keys on `worktree.resolve() != cfg.root.resolve()` and then inserts `cfg.root.name` (`daemon.py:1999-2003`). That discriminator is true for **every** branch-authority carve regardless of layout, but the `cfg.root.name` path component is correct only for nyxloom's own nested layout (`worktree_root = "../.worktrees"`, nyxloom-trove/nyxloom.toml:36).
- dstdns: `cfg.root` **is** the repo root and `worktree_root = ".worktrees"` (dstdns nyxloom-trove/nyxloom.toml:15). A branch-authority carve worktree is a whole-repo worktree whose top-level equals the worktree dir; the carver writes `<worktree>/nyxloom-trove/reports/CARVE-N.md`; the daemon will look in `<worktree>/dstdns/nyxloom-trove/reports/CARVE-N.md`. Every dstdns carve (default `carve_authority="branch"`, `config.py:129`) reproduces the P51 incident the moment dstdns carving is resumed.
- Root fix is R6-shaped: resolve the path at **dispatch time** via `git rev-parse --show-prefix` and store it on the attempt record; never re-derive at read time.

### M6 — The reviewer's argv prompt contradicts its packet, and verdict parsing accepts cross-task/stale files  ⚠ HIGH (nondeterministic merge gate)

- `build_dispatch(role=FRONTIER_REVIEW)` says: *"Do not commit anything to git — write your verdict to the receipt path above"* (`adapters.py:222-230`). The packet says: *"Write {reports_dir}/<task>-REVIEW.md … Commit it to the feat/ branch"* (`daemon.py:2515-2520`). The daemon derives the verdict **exclusively** from that committed file (`git show branch:path`, `daemon.py:2151-2160`); the receipt is wrapper-owned and carries no verdict. A reviewer that obeys the argv prompt yields verdict "missing" → false REVIEW_REJECTED → re-implementation of approved work. Which instruction wins is model-dependent — a nondeterministic merge gate.
- Separately, the broadened verdict search pools VERDICT lines from **any** `*REVIEW*.md` on the branch whose *content mentions* the task id (`daemon.py:2162-2188`, mention check at `2185`). Reports are committed under `reports_dir` and merged to main; branches contain all of main's history — a prior task's review that mentions this task ("unlike P42…") can supply this task's verdict. This is the general form of the draft's I8, and I8's fix (bind to attempt id) must cover it.

### M7 — Review-leg infra failures are recorded as semantic rejections and burn the implementation budget  ⚠ HIGH

- A review receipt of LIMIT/ERROR/BLOCKED → verdict "rejected" and `REVIEW_RECORDED{result: rejected}` + transition to REVIEW_REJECTED (`daemon.py:2422-2436`, pinned as desired by `test_daemon.py:942`).
- Downstream: (a) the reject loop re-queues → a **full re-implementation** of possibly-fine work (`reconcile.py:532-541`); (b) the rejection pollutes `review_rejections_by_area` (`daemon.py:1077-1081`) → SpecAttention('rejections') treats a provider outage as a quality signal; (c) unlike the implementer LIMIT path, **no ProviderPause** — the relaunched review dives into the same limit.
- This is a precondition failure for the draft's §4/§5.6 smart triage: you cannot triage rejections whose recorded cause conflates "provider 429" with "reviewer found defects". A review-leg failure must be a distinct outcome (REVIEW_INCOMPLETE → relaunch review), never REVIEW_REJECTED.

### M8 — Attempt-budget accounting counts reviewer receipts against the implementer budget; four sites, three different formulas  ● MEDIUM-HIGH

- Review attempts land in `tsf.attempts` with DONE receipts. `dispatch_eligible` check 5 (`reconcile.py:1091-1096`), the REVIEW_REJECTED counter (`reconcile.py:533-536`), and the daemon ERROR path (`daemon.py:2451-2455`) all count receipted attempts **role-blind**; each reject cycle therefore burns 2 units. With `max_attempts_per_task=3`, a task is superseded after 2 rejections, not 3 implementer attempts. Meanwhile `implementer_record_count` (`reconcile.py:1141-1147`) filters by role, and the resume path adds a terminal-state condition (`reconcile.py:791-792`) — three formulas for one concept. Invariant: **one** `attempts_used(tsf)` accessor.

### M9 — LaunchReview and ResumeAttempt have no budget/route/provider admission at all; empty review tier = per-pass TICK_ERROR that aborts the rest of the pass  ● MEDIUM-HIGH

- Planner: `reconcile.py:907-927` gates review launch only on drain-agents; `reconcile.py:789-794` gates resume only on attempts+handle. No budget, no route health (contrast carve, `reconcile.py:596-606`).
- Executor: `review_routes[0]` raises IndexError when the tier is empty (`daemon.py:2589-2591`); `run_pass`'s pass-wide try/except converts that into a TICK_ERROR **that skips all remaining actions** (`daemon.py:617-636`) — and the task stays AWAITING_REVIEW, so it recurs every pass. A provider-paused review route is still dispatched into (no `provider_ok` consult in the executor).

### M10 — Planner self-contradiction: one pass plans both Transition(BLOCKED) and LaunchReview for the same task  ● MEDIUM

- AWAITING_REVIEW + latest review attempt INTERRUPTED with no session handle: the attempt loop plans BLOCKED (`reconcile.py:775-809`), the wave loop plans a relaunch (`reconcile.py:914-927`). Execution order applies BLOCKED first, then launches a frontier review **for a BLOCKED task**; its exit receipt is never consumed (scan special-cases only AWAITING_REVIEW, `daemon.py:880-881`) — a wasted frontier session plus an orphan attempt. Session-capture returning None is a known production occurrence (`adapters.py:66-72`). The pinning test filters only LaunchReview and blesses the contradiction (`test_reconcile.py:2421,2437-2438`). Invariant: a single pass must never plan both a dead-end transition and an agent launch for one task.

### M11 — Wave age-trigger reads the lexicographically-smallest task, not the oldest  ● MEDIUM (liveness)

- `awaiting_review` is sorted by task id; the "oldest" age check reads `task_ids_to_batch[0]` (`reconcile.py:855-869`). A 3-hour-old task "b" plus a fresh task "a" → age is measured on "a" → no wave opens until the count threshold is met. Under low throughput a review can be delayed unboundedly. The existing test aligns sort order with age order and passes over the bug (`test_reconcile.py:1899-1932`).

### M12 — No per-action isolation and no execute-time re-check: one failing executor aborts the rest of the pass; a mid-pass auto-pause doesn't stop the pass  ● MEDIUM (mechanism weakness amplifying M9/M21)

- `run_pass` wraps the whole action loop in one try/except (`daemon.py:617-636`); any raise (M9's IndexError, a `routes.toml` KeyError at `daemon.py:2252`, a receipt race at `2383`) starves every remaining action that pass. `_apply_watchdog`'s auto-pause (`daemon.py:1214-1217`) does not filter the remaining agent-launch actions of the pass that triggered it (only the matching runaway shape is suppressed, `daemon.py:1237-1270`).

### M13 — Auto-merge mutates the live checkout: silently overwrites dirty operator files, mishandles deletions, wrong-branch contamination  ● MEDIUM-HIGH severity, gated on enabling guarded-automatic

- After `update-ref`, `_execute_auto_merge` runs `git checkout <default> -- <changed_files>` **in the shared repo root** (`daemon.py:1580-1589`): uncommitted local edits to those files are silently clobbered (nyxloom self-hosts inside the operator's live vbpub checkout); files the merge *deleted* error out of the checkout and leave the tree/index inconsistent; if the checkout currently has a non-default branch checked out, main's content is grafted onto it. The docstring's own "monorepo-dirty-state safety" concern (`reconcile.py:212-214`) is violated by the sync step. Kindred defect: `_run_post_merge_gate` validates **whatever is checked out** at the live root (`cwd=cfg.root`, `{worktree}`=live top-level, `daemon.py:1427-1432`) — a checkout sitting on a feature branch mints COMPLETED (or CONTRACT-BLOCKED) verdicts against the wrong tree.

### M14 — Carver INTERRUPTED dead-ends wedge carving forever; carver resume drops the P47 mutex  ● MEDIUM

- The role-blind INTERRUPTED handler applies to CARVER attempts: no session handle → `Transition(BLOCKED)` (`reconcile.py:795-809`) → synthetic carve task BLOCKED, which is **non-terminal** (`types.py:63-65`) → `carve_in_flight` True forever (`reconcile.py:591-595`): carving silently dead until an operator cancels the synthetic task. With a handle → `ResumeAttempt` rebuilds leases from the task's frontmatter — which a synthetic carve task doesn't have — so the resumed carver runs **without** the `strategic-carver` lease (`daemon.py:2299-2304`), voiding P47. Same wedge if the daemon crashes between the carve ATTEMPT_CREATED and wrapper launch: a CREATED attempt has no liveness handler at all (`reconcile.py:687-763` never matches CREATED), so the carve task idles ACTIVE forever.

### M15 — `dispatch_targeted_carve` still bypasses pause (and everything else); it is also currently a defined-but-unwired entry point  ● MEDIUM

- `daemon.py:638-655` consults no pause, no budget, no carve-ahead, no `carve_in_flight`; P47 added only the lease (`daemon.py:1949`). An operator-initiated carve arguably may override pause — but that is a product decision (D-NNN), not an accident of omission; the drafted I1 ("no new agent process while paused, any mode") is violated as stated. Meanwhile grep finds **no caller** of `dispatch_targeted_carve` anywhere in `src/` — P41's CLI/UI wiring never landed; the method is a P43-class silent stub that the role guard cannot see (it guards Roles, not entry points).

### M16 — Post-merge gate failures are counted as "underspecified handoffs", over the full log, forever  ● LOW-MEDIUM

- `_run_post_merge_gate` types its blocker CONTRACT (`daemon.py:1454-1457`); `_history` counts **all** TASK_BLOCKED contract blockers over the entire log with no window (`daemon.py:1082-1085`, docstring admits "UNCHANGED (still full-log)"). Three post-merge gate failures ever → `SpecAttention('blocked-underspecified')` re-fires each time its dedup event scrolls out of the 500-event window. A new writer of an old event type silently changed an existing counter's meaning — R2 in event-payload form.

### M17 — The progress ratchet and rejection-area signals are structurally dead or false-positive  ● LOW-MEDIUM

- No code anywhere emits `progress_units` or `source_kind` in MERGE_RECORDED (emitters: `cli.py:519-525`, `daemon.py:1592-1594`; sole reader `daemon.py:1071-1074` defaults to `(0, 'review')`); `PROGRESS_RECORDED` has a projection handler and **zero emitters**. Therefore every merge is a zero-progress review merge and the ratchet (`reconcile.py:933-943`) fires after any 3 merges — a periodic false alarm, plausibly a contributor to the SPEC_ATTENTION bleed history. Likewise REVIEW_RECORDED never carries `area` (`daemon.py:2426-2429`), so `review_rejections_by_area` is one "unknown" bucket (`daemon.py:1080`) and the "≥2 in one area" rule is really "≥2 anywhere". `WAVE_CLOSED` is a push-class with no emitter. P43 should be generalized: every EventType needs an emitter or an explicit reservation.

### M18 — Task-level pause is ignored by auto-merge, wave launch, and re-carve  ● LOW-MEDIUM

- `tsf.paused` is consulted only in `_check_paused` (`reconcile.py:1026-1029`). A task the operator paused at MERGE_READY is still auto-merged under guarded-automatic (`reconcile.py:581-584` checks only `project_paused`); a paused AWAITING_REVIEW task still gets reviews launched; a paused REVIEW_REJECTED task is still routed to READY_TO_CARVE → SUPERSEDED.

### M19 — `leases_free` defaults fail-open  ● LOW

- `inp.leases_free.get(lease_name, True)` (`reconcile.py:1042`): a lease-name drift between `_leases_free` (`daemon.py:822-831`) and `_check_lease` silently disables the planner-side mutex check (the wrapper flock remains the real enforcement, hence LOW — but contrast `provider_ok.get(..., False)` fail-closed one function below; the asymmetry is exactly the kind of unstated convention R1 breeds).

Two additional planner-atomicity notes folded into the plan (§4): **M20** — the READY_TO_CARVE handler plans `CarveDispatch` + `Transition(SUPERSEDED)` as two independent actions (`reconcile.py:637-647`); if the carve execution early-returns (no route, `daemon.py:1884-1887`) the SUPERSEDED transition still executes and the rejected task's re-carve is silently dropped; if the pass aborts between them the task re-triggers. **M21** — the four non-daemon launch sites (§1.3) sit outside every guard and every P43-style scan.

**Count: 19 catalogued missed defects (M1–M19) + 2 folded notes (M20–M21).** None appears in the draft's §2.

---

## 3. REDESIGN — §5 evaluated, and what to build instead

### 3.1 Verdict on the draft's proposal

Mechanism/policy separation: **right instinct, wrong first cut.** The draft's §5.7 worry is decisive against its own §5.1: a general declarative flow spec moves the 14 contract items from a docstring into a config language whose semantics live in an interpreter — the invariant-enforcement problem recurs one level up with *less* type safety, and none of M1–M19 would have been prevented by it (they live in the mechanism the spec would still call into: scans, receipts, artifact paths, admission). Equally, "keep hard-coding it" is refuted by the session's history: every flow change (self-review, serial/parallel, smart retry) currently means editing `plan_project` and re-risking exactly the R1/R2 classes.

The resolution is that **the operator's ask does not actually require a flow *language*.** It requires (a) one admission gate, (b) stages as *data*, (c) policies as *typed config*, with composition validated against the frozen graph. Concretely:

### 3.2 The trusted kernel (mechanism — frozen, invariant-tested)

1. **Event log + projection + frozen `TASK_TRANSITIONS`** — unchanged. This part earned its keep.
2. **`AdmissionGate.check(project, effect) -> (allow, reason)` at the effect boundary.** One predicate, evaluated **inside** the executor immediately before any side effect, covering: project pause (mode-aware), task pause, budget, route health, lease headroom, wip. Every agent launch funnels through one chokepoint — make `wrapper.launch_detached` require an `AdmissionToken` minted only by the gate, so the four non-daemon call sites (§1.3) and `dispatch_targeted_carve` *cannot* bypass it (an operator-override flag on the token is an explicit, audited decision, not an omission). The planner may pre-filter as an optimization; the gate is authoritative. This kills R1 *and* R5 (M9, M12's pause half, M15, M18).
3. **Run-bound artifacts.** Every wrapper run gets a `run_id`; receipts are written to `receipt.<run_id>.json` (or stamped with run_id and cleared on resume); report/verdict paths are resolved at **dispatch time** and stored on the attempt record; verdict lines must carry the attempt id and the parser matches only the current attempt. Kills R6 (M1, M5, M6, I8).
4. **Closed matrices, machine-checked.** Three completeness tests generalizing P43: Role × AttemptState (every non-terminal task with a terminal or stale attempt yields an action or a documented park — kills M2/M14/CREATED gaps); Role × ReceiptResult (kills M4/M7); EventType × {emitter ∨ reserved} (kills M17-class stubs). Plus a whole-plan consistency property: no pass plans both a dead-end transition and a launch for one task (M10), and paired actions (dispatch+transition) are emitted as one atomic action executed transactionally (M20).

### 3.3 Stages as data (the configurable middle)

A **Stage** is a registered, code-backed record — not user-supplied logic:

```
Stage:
  name:            "implement" | "self_review" | "frontier_review" | "triage" | "carve" | "post_merge_gate" | "auto_merge"
  role:            Role                     # prompt + packet builder come from the role, one source
  entry_state:     TaskState                # e.g. QUEUED
  exit_map:        {outcome -> TaskState}   # e.g. done->AWAITING_REVIEW, approved->MERGE_READY, incomplete->retry-same-stage
  concurrency:     int | "serial"           # per-stage scheduling (operator ask (b))
  tier:            route tier               # model/effort via routes.toml, unchanged
  retry:           RetryPolicy              # max, escalation ladder, feed-context flag
  context:         ContextPolicy            # packet assembly: diff-only | wave-batch | +spine-digest | session-reuse
  admission:       always through AdmissionGate (not per-stage overridable)
```

The **per-project pipeline** is then a validated list of stage names in `nyxloom.toml` — e.g. `pipeline = ["implement", "self_review", "frontier_review", "auto_merge"]`. Load-time validation (same rigor as `nyxloom lint`): each stage's `entry_state`/`exit_map` edges must exist in the frozen `TASK_TRANSITIONS`; every non-terminal state must be owned by exactly one stage or documented manual — the `test_invariants.py` machinery, run **against the composed pipeline** rather than against `plan_project`'s text. This is the exact P43 pattern promoted from declaration to composition, and it is where the mechanism/policy line sits: **states, edges, admission, artifact binding, and stage implementations are mechanism; the stage list and every policy field on a stage are policy.** No plugin/hook model — hooks reintroduce unauditable behavior at unknowable points, which is R1 with extra steps.

This directly delivers the five first-class requirements:

- **(a) Self-review stage:** register a `self_review` stage (Role.SELF_REVIEW un-reserved) between implement and frontier review; enabling it is one line of project config. Its exit_map routes "self-rejected" back to implement *within the same session* (cheap fix loop) and "self-approved" onward — the frontier reviewer confirms. No `plan_project` surgery per project.
- **(b) Serial vs parallel per stage:** `concurrency` on the stage replaces the single global `max_active_tasks` and *surfaces* today's hidden hard-codings (post-merge gates synchronous, reviews accidentally parallel-per-task, carve serial-by-flag). Default: implement N, review serial-1 (which also restores gate-safety, M3), carve 1, gates async-with-timeout.
- **(c) Token-optimized broad-vision review/carving:** three levers in `ContextPolicy`, in priority order: (1) **restore real wave batching** — one review session per wave over up to `wave_max_diffs` diffs (M3): the single largest, purely mechanical token win (~2-3× on the measured 35-40k startup tax); (2) **reviewer session-reuse** across a wave/cycle via the existing `session_handle` machinery + `build_resume`, safe only *after* verdict-attempt binding (3.2.3) — this is D-R10, now unblocked; (3) **spine digest**: the carver maintains a versioned `nyxloom-trove/reports/SPINE-DIGEST.md` (recent review reflections, invariant catalog, open risks) that review/carve packets include *by reference* — broad vision at file-pointer cost, and the digest is precisely where carve-6-style reflections ("hollow tests dominate") stop being one-off prose and become standing reviewer instructions.
- **(d) Single strategic carver owning "how to proceed":** already the architecture (single carve authority, P45/P47); what it lacks is *inputs* and *invocation on judgment events*. The carver stage gains two entry kinds: headroom refill (today's item 9) and **re-scope requests** — a task exiting triage as "architectural" or "stale-premise" is packaged (handoff + review verdict + diff summary + `input_revision` drift report) into the carve packet. The carver, with session reuse and the spine digest, is the only component with whole-system context — it decides re-carve vs. drop vs. D-NNN. The daemon never "decides" anything on rejection except which stage to route to.
- **(e) Smart reject-triage:** a `triage` stage with a **mechanical first tier and an LLM second tier**. Tier 1 (no model call, pure code): receipt/verdict classification — review receipt ≠ DONE or verdict "missing" ⇒ REVIEW_INCOMPLETE ⇒ relaunch review + ProviderPause (M7 fixed structurally: infra failures never enter REVIEW_REJECTED); `input_revision` ≠ current main ⇒ stale-premise ⇒ carver (I4). Tier 2 (cheap model, only for genuine verdicts): classify REJECTED-verdict prose into fixable-gate-fail (requeue **with the review file embedded in the re-dispatch packet** + optional route escalation ladder from RetryPolicy) vs architectural (→ carver) vs product (→ D-NNN). Same-model context-free retries cease to exist as a path.

### 3.4 What stays hard-coded on purpose

The state graph, the admission gate, artifact binding, the wrapper contract, storage. Also the *order* of reconcile phases (lifecycle → attempts → waves → spec → carve). Dynamism beyond stage composition — user-defined states, user-defined actions, conditional flow expressions — is explicitly rejected: each would reopen the unchecked-invariant hole the frozen core exists to close, and no requirement on the table needs it. If a future need genuinely requires a new stage *kind*, it is a code change with the full matrix-test obligation — that is a feature, not a limitation.

---

## 4. SEQUENCED PACKAGE PLAN

Correctness first (Wave A: each closes an invariant class with a machine-checked test, per draft §3's own bar), then the flow system (Wave B). Sized per AUTHORING.md: one concern, explicit files, a real gate. `[D]` = requires a product decision first.

### Wave A — correctness hardening (dependency-ordered)

| # | Package (one line) | Files | Invariant test that proves it | Deps |
|---|---|---|---|---|
| A1 | **P53 receipt-run binding**: clear/rename `receipt.json` on resume; scan matches receipts to the current run only | `daemon.py` (`ResumeAttempt`, `_attempt_scan`), `wrapper.py` (write `run_id`) | interrupt→resume→N passes: zero `EmitAttemptExit` while resumed pid lives; then real exit consumed exactly once (kills M1) | — |
| A2 | **P54 attempt-state closure**: consume ATTEMPT_FAILED for all roles; CREATED-attempt liveness timeout; carve-task BLOCKED escalates + frees carve slot | `daemon.py` (`_attempt_scan`), `reconcile.py` (attempt loop) | Role × AttemptState matrix: every (non-terminal task, terminal-or-stale attempt) yields an action or documented park (kills M2, M14, CREATED gap) | — |
| A3 | **P55 admission gate at the effect boundary**: `dispatch_admissible()` called in `_execute` for every launch + `dispatch_targeted_carve`; `launch_detached` requires the gate's token | `daemon.py`, `wrapper.py`, `reconcile.py` (planner pre-filter only), 4 chat/onboarding modules | property test: paused (any mode/level) ∨ budget≤0 ∨ no healthy route ⇒ zero wrapper launches, parametrized over ALL action types and entry points (kills M9-admission, M12-pause, M15, M18; I1 done right) | — |
| A4 | **P56 review-leg failure ≠ rejection**: non-DONE review receipt or "missing" verdict ⇒ REVIEW_INCOMPLETE (relaunch review, ProviderPause on LIMIT), never REVIEW_REJECTED | `daemon.py` (`_execute` review branch), `reconcile.py` (relaunch path), `types.py` untouched (reuse AWAITING_REVIEW) | ReceiptResult × FRONTIER_REVIEW matrix: LIMIT/ERROR/BLOCKED leave task AWAITING_REVIEW, no REVIEW_RECORDED{rejected}, provider paused (kills M7) | A3 |
| A5 | **P57 carve-exit reads receipt.result**: limit ⇒ ProviderPause + no parse-fail escalation; error/blocked typed distinctly | `daemon.py` (`_consume_carve_exit`) | ReceiptResult × CARVER matrix; burn-loop test: limited provider ⇒ no second carve dispatch within pause window (kills M4) | A3 |
| A6 | **P58 report-path bound at dispatch**: compute report path via `git rev-parse --show-prefix` in `_execute_carve_dispatch`, store on attempt; `_consume_carve_exit` reads the stored path | `daemon.py` | layout matrix test: repo-root project (dstdns shape) AND nested project (nyxloom shape) × {branch,main,files} authorities all parse (kills M5) | — |
| A7 | **P59 verdict-attempt binding + prompt/packet consistency**: verdict line must name the attempt id; parser ignores unbound/stale/cross-task verdicts; fix the FRONTIER_REVIEW argv prompt contradiction | `daemon.py` (`_parse_review_verdict`, packet text), `adapters.py` (review prompt) | stale on-branch verdict from a prior attempt is not consumed (I8); a file mentioning the task but bound to another attempt is ignored (M6); source-scan test: review prompt contains no "do not commit" while packet requires commit | — |
| A8 | **P60 one attempts_used()**: single role-filtered accessor; all four call sites import it | `reconcile.py`, `daemon.py` | source-scan: no inline receipt-counting formula outside the accessor; behavior: one reject cycle consumes exactly 1 implementer unit (kills M8) | — |
| A9 | **P61 real wave batching**: one LaunchReview per wave carrying all task_ids; review attempt recorded on every wave member; review wrapper takes the union of member leases | `reconcile.py` (wave loop), `daemon.py` (`LaunchReview`, attempt bookkeeping) | 3-task wave ⇒ exactly 1 frontier session; per-task verdicts each parsed; second cycle after a reject still relaunches (preserves the 2026-07-17 fix) (kills M3) | A7 |
| A10 | **P62 plan atomicity + consistency**: paired CarveDispatch+SUPERSEDED becomes one action executed transactionally; whole-plan check rejects contradictory (dead-end + launch) pairs; per-action exception isolation in `run_pass` | `reconcile.py`, `daemon.py` (`run_pass`) | property test over generated inputs: no plan contains both BLOCKED-transition and launch for one task (M10); carve-exec failure leaves task in READY_TO_CARVE (M20); one raising action doesn't starve the rest (M12) | A2 |
| A11 | **P63 merge/gate tree safety**: auto-merge refuses (escalates) on dirty/wrong-branch live checkout, handles deletions; post-merge gate runs in a clean scratch worktree at the merge commit | `daemon.py` (`_execute_auto_merge`, `_run_post_merge_gate`) | dirty-file test: uncommitted edit survives an auto-merge (escalated, not clobbered); gate-tree test: gate sees the merge commit even when the live checkout is elsewhere (kills M13) | — |
| A12 | **P64 dead-signal audit**: emit `progress_units`/`source_kind` at merge (or retire the ratchet via D); window `blocked_underspecified`; split gate-failure blocker type from CONTRACT; EventType emitter-or-reserved guard | `daemon.py` (`_history`, merge paths, gate blocker), `cli.py` (cmd_merge), `tests/test_invariants.py` | EventType × emitter matrix (WAVE_CLOSED/PROGRESS_RECORDED accounted); 3 ordinary merges ⇒ no ratchet false alarm (kills M16, M17) | `[D-061]` ratchet semantics: fix or retire |
| A13 | **P65 counterfeit-input test hardening**: trace-level plan→execute→wrapper-artifact cycle tests (fake CLI); fixture builders derive inputs from the wrapper contract (an INTERRUPTED fixture *with* receipt); age-vs-sort wave test | `tests/` only (+ a `testing.py` fake-CLI helper) | the M1 and M11 reproducers themselves, checked in as regression tests; R3 class closed at the input level | A1 |

Merge order A1→A2→A3 first (they close the two CRITICALs and the gate); A4–A8 parallelizable after A3; A9 after A7; A10 after A2; A11/A12 independent; A13 lands alongside A1.

### Wave B — flow system (after Wave A is green)

| # | Package | Files | Proof | Deps |
|---|---|---|---|---|
| B1 | **`[D-060]` Stage-architecture decision doc**: mechanism/policy line per §3.2-3.4, stage schema, pipeline config format — a decision, not code | `docs/` | operator sign-off (product call: how much per-project divergence is allowed) | Wave A |
| B2 | **P70 stage registry + composed-pipeline validation**: implement/frontier_review/carve/post_merge_gate/auto_merge as stage records; `pipeline=` in nyxloom.toml; load-time edge validation; port `test_invariants.py` to run against the composition | `reconcile.py` (thins toward an engine), `config.py`, `daemon.py`, `tests/` | stage × site closure test; pipeline lint rejects an exit_map edge absent from TASK_TRANSITIONS; behavior parity suite (same plans as pre-B2 for the default pipeline) | B1 |
| B3 | **P71 per-stage concurrency**: `concurrency` field replaces lone `max_active_tasks`; review default serial-1; gates async-with-timeout | `config.py`, `reconcile.py`, `daemon.py` | scheduling matrix test: implement-parallel + review-serial mix honored; post-merge gate no longer blocks other projects' passes | B2 |
| B4 | **P72 triage stage (B8's other half)**: mechanical tier (drift guard I4 = `input_revision` vs main; infra classes from A4) + LLM tier classifying genuine rejections; re-dispatch packets embed the review verdict; route-escalation ladder in RetryPolicy | `reconcile.py`/stage code, `daemon.py`, `adapters.py` | reject-triage matrix: {infra, stale-premise, fixable, architectural, product} each routes correctly and never a bare same-model retry; I4 property test | B2, A4 |
| B5 | **P73 self-review stage**: un-reserve Role.SELF_REVIEW, register the stage, per-project enable; P43 guard updated (reservation removed ⇒ dispatch site required) | `types.py` (RESERVED_ROLES), stage code, `adapters.py` (prompt), `tests/test_types.py` | pipeline with self_review enabled runs implement→self_review→frontier_review end-to-end on the fake CLI; disabled pipeline byte-identical to today | B2 |
| B6 | **P74 reviewer session-reuse + spine digest (D-R10)**: ContextPolicy session-reuse across a wave/cycle via `build_resume`; carver-maintained SPINE-DIGEST.md referenced by review/carve packets | `daemon.py`, `adapters.py`, carve packet builder | cache-hit assertion on second wave review (usage `cached_in` > 0 via the fake); digest referenced-not-slurped in packets; verdict binding (A7) still enforced on resumed sessions | B2, A7, A9 |
| B7 | **P75 carver re-scope entry**: triage's "architectural/stale" output packaged into carve packets (handoff + verdict + drift report); carve outcomes extended with RESCOPED | `daemon.py` (packet builder), stage code | end-to-end: rejected-architectural task reaches the carver with its review verdict embedded; original task SUPERSEDED only after the carve dispatch actually launched (A10's atomicity) | B4 |

**Flagged product decisions:** `[D-060]` stage architecture & config surface (B1); `[D-061]` ratchet semantics (A12); `[D-062]` does operator-initiated targeted carve override pause? (folds into A3's gate as an explicit audited override — currently it is an accident, M15).

---

### Closing assessment

The draft's diagnosis deserves to survive review; its §2 catalog does not — 19 additional cross-cutting defects were reachable by reading the same files, including two CRITICALs (M1, M2) that will fire under routine operation (a stall-interrupt-resume cycle; a same-pass mutex collision) before any of the draft's proposed guards would notice. The correct synthesis of §5.7's tension: **do not build a flow language; build one admission gate, run-bound artifacts, closed role/state/receipt matrices, and stages-as-data whose composition is validated against the frozen graph.** That yields every capability the operator asked for — self-review, per-stage scheduling, token-optimized broad-vision review, a strategic carver that owns judgment, smart triage — while making the invariant surface *smaller* than today's, not larger.
