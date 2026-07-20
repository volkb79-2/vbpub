# nyxloom Flow-System Review & Redesign

**Status:** SUPERSEDED IN PART by the Fable-xhigh critique — read `flow-system-review-and-redesign-CRITIQUE.md` as the authoritative synthesis. That review verified the diagnosis (§1) but found **19 additional latent defects (2 CRITICAL: M1 stale-receipt double-dispatch, M2 lease-race carve deadlock) this draft's §2 missed**, added two root causes this draft lacked (R5: plan-time guards / no execute-time admission; R6: unversioned filesystem artifacts), **corrected two errors in this draft** (§5.4's "wave batching exists" is false — it's a label, `reconcile.py:927`; and the P51 report-path fix is wrong for repo-root projects like dstdns — M5), and produced the sequenced package plan (Wave A correctness → Wave B flow redesign). The redesign verdict: do NOT build a declarative flow language (§5.1 here) — build one admission gate + run-bound artifacts + closed matrices + stages-as-data. Draft below retained as the grounded input record.

**Original status:** Sonnet draft 2026-07-19, to be adversarially reviewed + extended by Fable xhigh
**Method (hybrid):** this Sonnet draft holds the grounded evidence from a live self-hosted operating session (bugs P49–P52 + P40, all found while nyxloomd controlled its own development). A Fable-xhigh agent then (a) adversarially critiques this diagnosis, (b) hunts for the cross-cutting invariant violations this draft *missed*, and (c) open-mindedly redesigns §5 toward a dynamic/configurable/intelligent control plane. **§1–§4 are grounded findings; §5–§6 are proposals to be challenged, not decisions.**

---

## 0. Snapshot at time of writing

Quiet point: **0 non-terminal tasks** in the `nyxloom` project. `carve_ahead_target=0` (convergence freeze). Merged this session on top of P44/P45: P47 (carve mutex), P48 (guarded-automatic merge), P49 (watchdog streak reset), P50 (carve-exit scan), P51 (carve report path), P52 (carve triggers respect pause). dstdns/topos are pause-frozen with `carve_ahead_target=0` after P52's live incident.

---

## 1. The correctness diagnosis (grounded)

### 1.1 The unifying pattern

> **Strong *local* correctness; zero *cross-cutting-invariant* enforcement.**

The frozen `TASK_TRANSITIONS` table (`types.py`) and the pure leaf functions are genuinely well-tested. Every bug this session was in a property that spans **files, roles, or dispatch-paths** — a property enforced *nowhere in code*, existing only as English prose in `reconcile.py`'s 14-item module docstring. Prose doesn't fail a build.

### 1.2 Four structural root causes

| # | Root cause | Evidence |
|---|-----------|----------|
| R1 | **Dispatch-guard scatter — no single admission gate.** Six new-agent-process dispatch sites, each with its own hand-written pause guard, using three mechanisms (`inp.project_paused` bool; `inp.pause_mode == "drain-agents"` str; `tsf.paused or inp.project_paused`). | **P52**: carve trigger added in P16, its pause guard *never written*, until it fired 4 unauthorized carves against a live-paused project (dstdns) in ~15 min. |
| R2 | **Retrofit-role incompleteness.** CARVER was bolted onto implementer/review machinery ("a synthetic task hosting a CARVER attempt"). Every Role-enumerating site had to be manually extended; some were missed. No exhaustiveness check. | **P50**: `_attempt_scan` receipt filter never included CARVER → 2 carve tasks stuck ACTIVE forever. **P51**: `report_path` never accounted for branch-authority's whole-repo worktree layout → valid reports parse-failed. |
| R3 | **Seam-mocked hollow tests.** All `test_carver.py` tests monkeypatch `plan_project` (`_scripted`), validating the leaf but never the integration. | **P50** lived precisely in the mocked seam. The strategic carver *itself* already diagnosed this class ("hollow tests are the dominant defect class… found by reviewer mutation testing, not by reading" — carve-6 reflection). |
| R4 | **Diff-scoped review is blind to cross-cutting gaps.** The frontier reviewer reads one package's diff; is never asked "does `_attempt_scan` also need this role?" because that file isn't in the diff. | Every R1/R2 bug passed frontier review at merge time. Cross-cutting completeness is nobody's job. |

### 1.3 The incident ledger (this session)

- **P49** — watchdog streak incremented every pass *including while already paused*, never reset → unpausing re-paused within ~90s repeatedly. (Cross-cutting: pause-state ↔ in-memory counter lifecycle.)
- **P50** — see R2.
- **P51** — see R2.
- **P52** — see R1. *The most serious: escaped the project's intended scope and acted on the user's active project.*
- **P40** (not a code bug — a *policy* bug) — a stale carve (blocked pre-P44, unblocked by merged P44) was **dumb-retried with the same model (sonnet/high), no escalation, no rejection-context fed back**, and re-rejected via stale-verdict re-parse. Its premise changed under it and nothing re-validated. Resolved by cancel-as-stale (deliverable preserved as open B6).

### 1.4 The positive template: P43

The one cross-cutting completeness guard we *did* build — P43's role-declaration invariants (`test_every_role_is_dispatched_or_reserved`, `test_every_reserved_role_cites_a_live_backlog_item`) — is **clean and it worked**. No defined-but-unwired role slipped through. Every bug is in a cross-cutting *behavioral* site P43 didn't cover. **The remedy is to generalize P43's proven pattern from role *declaration* to role *behavior* and to dispatch guarding.**

**This is not a "half-built features / stubs" problem.** Stub status is clean: SELF_REVIEW is properly RESERVED + P43-guarded; the one documented non-implementation (wrapper doesn't run gates, `daemon.py:41`) is deliberate. It is a **"the invariants that matter are prose, not tests"** problem.

---

## 2. Invariant catalog (the load-bearing cross-cutting invariants)

Each: statement · enforced-today · gap · proposed enforcement.

- **I1 — No new agent process starts while `project_paused` (any mode).** Enforced ad-hoc at 4 of 6 sites; was missing at both carve sites until P52. → *One `dispatch_admissible()` gate; property test "paused ⇒ zero dispatch actions of any kind" parametrized over pause mode.*
- **I2 — At most one CarveDispatch per pass (single carve authority).** Enforced by shared `carve_dispatch_planned` flag (P45). → *Property test.*
- **I3 — Every dispatched Role is handled at every role-enumerating site** (`_attempt_scan` filter, receipt-consumption branch, `report_path`, lease naming). Enforced nowhere; R2 bugs. → *Role×site completeness matrix test (generalize P43).*
- **I4 — A handoff's premises are valid against current main before dispatch** (`input_revision`). Stamped, never re-validated (B12); P40's root. → *Drift guard: compare handoff `input_revision` to current main; stale ⇒ re-carve, not dispatch.*
- **I5 — A carve report is read where the carver actually wrote it** (worktree layout depends on carve_authority). Was wrong for branch authority (P51). → *Covered by I3's matrix if `report_path` is a site.*
- **I6 — Watchdog auto-pause is one-shot per fresh condition; streak resets on operator action.** Fixed P49. → *Property test: N passes while paused ⇒ streak stays 0.*
- **I7 — No dead-end states; every non-terminal state has a handler or escalates.** Partially guarded by `test_invariants.py` (was xfail-pinned for READY_TO_CARVE until P45). → *Extend the invariant test to assert every `TaskState` has a reconcile handler.*
- **I8 — Reviewer verdict reflects *this attempt's* review, not a stale on-branch report.** Violated in P40 round-2 (stale `-REVIEW.md` re-parsed; no fresh review attempt). → *Bind verdict to the wave/attempt id that produced it; refuse to consume a verdict older than the attempt under review.*

---

## 3. Remedy — machine-checked invariants + single gates

1. **`dispatch_admissible(inp, action) -> (bool, reason)`** — one predicate every new-agent action (implementer/resume/review/carve/auto-merge) passes through. Centralizes pause/budget/route/lease. "Forgetting pause" becomes structurally impossible (kills the R1 class).
2. **Role×site completeness matrix test** — parametrize `Role` × {scan, receipt-consume, report_path, lease}. A new role or site can't be added without coverage (kills the R2 class).
3. **De-hollow the integration seam** — replace `_scripted` mocking in the carver suite with real `plan_project` integration tests (started in P50; finish the file). Add a lint/guard that flags tests which mock `plan_project` in the *behavioral* suites (kills the R3 class).
4. **Property tests for the prose invariants** — I1, I2, I6, I7 as executable properties.
5. **`input_revision` drift guard** (I4) + **verdict-freshness binding** (I8).
6. **Review-completeness checklist as a review-agent tool** — the frontier reviewer is explicitly prompted, for any diff touching a role/dispatch/scan site, to check the *other* enumerated sites (mitigates R4 without a full architecture change).

---

## 4. Escalation taxonomy (for full-auto operation)

You're right that "a human must merge" is config-dependent — P48's guarded-automatic already does real unattended merges. Full-auto still needs an **enumerated set of MUST-STOP conditions**. Each needs: precise trigger · escalation channel · who-resolves · auto-resume condition.

| Class | Trigger | Channel | Resolver |
|-------|---------|---------|----------|
| Merge conflict | real 3-way conflict (P48) | NEEDS_OPERATOR / D-NNN | human or re-carve |
| Product/direction decision | user-facing contract, visual direction, irreversible schema | decision-chat D-NNN | human |
| Architecturally-unsatisfiable-as-scoped | reviewer BLOCK: no in-scope fix (P40) | re-carve request | strategic carver (re-scope), not retry |
| Spec-vs-reality contradiction | handoff premise false vs current main (I4 drift) | re-carve | strategic carver |
| Safety/destructive | secrets, prod data, irreversible infra touched | hard stop | human, always |
| Reviewer/carver low-confidence | frontier agent self-flags uncertainty, or reviewer↔carver disagree | escalate | human |
| Budget/resource exhaustion at decision boundary | budget < threshold with work pending | pause + notify | human |

The distinction that P40 makes concrete: **a rejection is not a retry signal by default.** It must be triaged — *fixable gate-fail* (requeue **with rejection context fed back + model/scope escalation**) vs *architectural block* (re-carve/human). This is backlog **B8's unbuilt half**.

---

## 5. Forward redesign — a configurable, intelligent flow system (PROPOSALS to challenge)

The user's framing: *"maybe we need a more dynamic, configurable, intelligent flow system, at least not hard-coded."* This session's bugs are evidence *for* that: `plan_project` is a ~1000-line imperative function with 14 hard-coded "module contract items" baking in the state transitions, dispatch guards, role sequence, retry policy, carve triggers, and escalation. Every requested flow change — a self-review stage, a serial/parallel mode, smart retry, token-optimized review — means editing this frozen core and risking exactly the cross-cutting bugs above.

### 5.1 The core proposal: separate MECHANISM from POLICY

- **Mechanism (stays code; frozen; well-tested):** event log, state persistence, attempt/wrapper lifecycle, git operations, and — critically — the *single* `dispatch_admissible()` gate and the invariant guards from §3. This is the trusted kernel.
- **Policy (becomes declarative per-project config — a "flow spec"):** the pipeline stages and their order, concurrency per stage, retry/escalation policy, review batching/token policy, guard thresholds.

A **flow spec** might declare a pipeline like:
`carve → implement → [self_review?] → frontier_review → [merge: manual | guarded-automatic]`
with, per stage: concurrency (serial | parallel N), model/effort route, retry policy (max attempts, model-escalation ladder, feed-rejection-context, re-carve-on-architectural-block), and context-assembly policy (how much/what the agent sees).

This directly enables every theme raised across the session:

### 5.2 Self-review step (B6)
A configurable pipeline stage between implement and frontier-review — enabled in the flow spec, not a hard-coded branch. (Cheaper first pass; the frontier reviewer then confirms.)

### 5.3 Serial vs parallel scheduling
A per-stage concurrency policy. Today only `max_active_tasks` exists (a global). Generalize: the flow spec declares concurrency per stage, so implement-parallel + review-serial (or any mix) is config, not code. (Observed this session: post-merge gates are *synchronous/serial by construction* in daemon.py regardless of `max_active_tasks` — a hidden hard-coded scheduling decision that a flow spec would surface and make configurable.)

### 5.4 Token-optimized review & carving with broad vision
Measured this session (from real review transcripts): a frontier review spends **~35–40k "startup" tokens** (read packet, `git diff --stat`, orient) before the task-specific investigation begins — a roughly task-independent tax that is **cache-reusable across a wave** but currently paid fresh each time (review dispatch always `build_dispatch`, never `build_resume`). Levers, all expressible as review-policy config:
- **Wave batching** (exists: `wave_max_diffs=3`) — amortize startup across N diffs in one session.
- **Reviewer session-reuse** — resume the same review session across a wave to hit prompt-cache (D-R10 / P46-designate territory).
- **Broad-vision context assembly** — give the carver/reviewer a curated whole-system view (spine + recent review reflections) rather than one diff, so decisions are holistic. The carve-6 reflection ("hollow tests are the dominant defect class") is exactly the kind of cross-package insight a broad-vision carver produces and a diff-scoped reviewer cannot.

### 5.5 Strategic overarching carver
Single strategic carver (already the model) with **session-reuse across cycles** (D-R10) and **review-feedback + north-star as inputs**, so it re-scopes rejected/stale work (P40's case) intelligently rather than the daemon dumb-retrying. The carver, not the retry loop, owns "how to proceed" judgment.

### 5.6 Smart reject-triage as policy (B8)
The retry/escalation policy object in the flow spec: on rejection, classify (gate-fail vs architectural block vs stale-premise vs product-decision) and route per §4, with model-escalation ladder and rejection-context feedback — never a same-model dumb retry.

### 5.7 The tension (deliberate open question for the critique)
A fully data-driven flow engine is powerful but can *reintroduce* the "unchecked invariant" problem one level up: if the flow is config, the invariant guards (§3) must apply to the *flow engine*, and the config itself needs a schema + completeness lint (à la the spine's `nyxloom lint`). **How much dynamism without losing the correctness guarantees of the frozen state machine?** Where exactly is the mechanism/policy line? Is a declarative flow spec the right abstraction, or a plugin/hook model, or something else entirely?

---

## 6. Explicit charge to the Fable-xhigh critique

1. **Attack §1–§2.** Is the "local-correct / cross-cutting-unchecked" framing right, or a just-so story fit to 5 incidents? What's missing or misattributed?
2. **Find what I missed.** Read the real dispatch/scan/guard surface (`reconcile.py`, `daemon.py` `_attempt_scan`/`_consume_carve_exit`/`_execute`/`_apply_watchdog`, `types.py`) and enumerate cross-cutting invariant violations *not* in §2 — more latent P50/P51/P52-class bugs before they fire live.
3. **Redesign §5 open-mindedly.** Is mechanism/policy separation the right move? Design the best dynamic/configurable/intelligent control plane you can — challenge the state-machine model itself if warranted. Address self-review, serial/parallel, token-optimized broad-vision review/carving, strategic carver, smart reject-triage as first-class.
4. **Sequence it.** Produce a prioritized package plan (correctness-hardening first, then the flow-system redesign), each package small and scoped per AUTHORING.md, with the invariant-test for each.
