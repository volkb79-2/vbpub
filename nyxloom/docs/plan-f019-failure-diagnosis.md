# F019 — reviewer-diagnosis failure routing

Status: active · authored 2026-07-25 · owner: controller (Opus) → Sonnet implementers

## The principle

nyxloom already learned, for **review rejections**, that a failure verdict is a
*diagnostic signal to be classified and routed*, not a boolean to retry against:
B4b has the frontier reviewer self-classify its rejection into
`{fixable | architectural | product}`, and `reconcile.plan_project` routes the
class deterministically (architectural → carver re-scope; product → operator;
fixable → a **targeted** re-queue whose packet embeds the verdict — explicitly
"never the bare context-free same-model retry the critique bans").

**F019 generalizes that pattern to every other path that currently dead-ends or
dumb-retries.** The invariant that keeps this safe: **the reviewer is a
CLASSIFIER, not a controller.** It emits a typed class into an event; the pure,
deterministic planner routes on it. Control flow never moves into the LLM — that
would break event-sourcing determinism. Same shape as B4b, new triggers.

The persistent independent reviewer (B6/D-R10 session-reuse) is the instrument:
it already makes the *harder* judgment (subjective code-quality rejection → class);
classifying a concrete gate/attempt failure is strictly easier and cache-warm-cheap.

## The paths (holistic map)

| Path | Today | F019 |
|---|---|---|
| Review rejection | ✅ B4b classify → route (the model) | — |
| **Pre-merge / mutation gate failure** | → `REVIEW_REJECTED` but **no class** → always the blind-retry branch; gate stdout discarded | **P1 (this doc)** |
| Attempts exhausted (any cause) | → `BLOCKED` dead-end | P2: diagnose before dead-ending |
| Transient persists after B24 backoff | resume-same, no escalation | P2: after N, escalate to diagnosis |
| Carve envelope structurally invalid | → `DEGRADED` (AD3 adds a repair turn) | fold with AD3: structural→repair, semantic→re-scope |

Route vocabulary (shared with B4b): `fixable | architectural | product | transient`
→ `{targeted-retry-with-evidence | carver re-scope (RESCOPED) | operator (NEEDS_DECISION) | plain backoff-retry}`.

## Status

- **P1a — gate output capture** — MERGED `4f00870a` (GateResult.output_tail).
- **P1b — gate-failure diagnosis routing** — MERGED (this doc's §P1). Reviewer
  classifies a gate-caused REVIEW_REJECTED into
  `{fixable|architectural|product|transient}`; the pure planner routes via the
  existing triage table; the task never leaves REVIEW_REJECTED during diagnosis;
  `TASK_TRANSITIONS` untouched. Config `gate_diagnosis_after_failures` (default 1).

### P2 follow-ups (deferred, non-blocking)

- **Rejection-metric bucketing.** A diagnosis emits `REVIEW_RECORDED{result:
  "rejected"}`, which `_history` counts in `review_rejections_by_area` under
  `area="unknown"` — the same bucket normal reviewer rejections already use (the
  review path never sets `area`). Net delta: a repeatedly-failing gate now
  contributes to that metric where it previously did not, so a persistently
  failing gate could trip `SpecAttention('rejections')`. Defensible (a stuck gate
  deserves attention) and mitigated by the window + debounce, but if it proves
  noisy, tag diagnosis rejections with a distinct `area` (e.g. `gate-diagnosis`)
  so they bucket separately from genuine code-quality rejections.
- **Transient → true backoff.** A `transient` class currently routes to a plain
  QUEUED retry (retry, not re-scope/operator — O3's load-bearing assertion). It
  does NOT yet feed B24's transient backoff delay (that path is keyed on a
  transient ATTEMPT exit, not a reject_class). Wire the backoff if flaky-gate
  retries need pacing.

## P1 — gate-failure diagnosis routing (BUILT — see Status above)

### Why it currently feels like dumb retry (verified)
A pre-merge/mutation gate failure already transitions the task to `REVIEW_REJECTED`
(`daemon.py:2701` / `:2733`, "pre-merge gate failed (exit N); not published"),
which already flows into B4b's triage table (`reconcile.py:~937`). But:
1. It carries **no `reject_class`** — no reviewer looked at it — so `triage_class`
   is None and it always lands in the `fixable/none` mechanical-retry branch.
2. `GateResult` (`types.py:551`) stores only `exit_code`; `proc.stdout` (the actual
   pytest/coverage failure, captured at `daemon.py:2684`) is **discarded**, so even
   the retry packet is near-context-free.

Result: every gate failure → blind targeted-retry → attempts exhaust → `BLOCKED`.

### The change (max reuse — the state + routing table already exist)
1. **Capture gate output.** Persist a bounded tail (e.g. last ~4 KB) of the gate's
   stdout+stderr into the `GATE_FINISHED` payload / `GateResult`. This is the
   material the diagnosis reads and the fixable-retry packet embeds.
2. **Diagnose gate-induced rejections.** New `reconcile.py` branch: a task in
   `REVIEW_REJECTED` whose latest rejection cause is a **gate failure** (a
   `GATE_FINISHED{exit!=0}` with no subsequent reviewer `REVIEW_RECORDED`) and that
   has **no `reject_class` yet**, and whose consecutive gate-fail count ≥
   `cfg.policy.gate_diagnosis_after_failures` (default **1** — diagnosis is cheap,
   a blind implementer re-dispatch is not), dispatches the reviewer in a
   **gate-diagnosis mode** of `Role.REVIEW_INDEPENDENT` (reuses the B6 warm session).
   Packet = {gate output tail, the failed diff, the handoff spec}. It emits
   `REVIEW_RECORDED{verdict: rejected, reject_class ∈ {fixable|architectural|product|transient}}`
   — classify-only (the gate already decided fail).
3. **Route.** `_parse_reject_class`/`_triage_classes` already read `reject_class`;
   the existing routing table then sends architectural→carver re-scope,
   product→operator, transient→plain retry, fixable→targeted retry **now carrying
   the gate output**.

### Design decisions locked
- **D-F019-1: reuse `REVIEW_INDEPENDENT` (gate-diagnosis mode), not a new role/event.**
  A gate failure already *is* a `REVIEW_REJECTED`; the reviewer already produces
  `reject_class` consumed by the existing table. A mode flag on the dispatch packet
  is the minimal seam and inherits session-reuse for free.
- **D-F019-2: `transient` joins the class vocabulary** so a flaky gate routes to a
  plain retry (feeding B24's backoff) rather than a re-scope/escalation.
- **D-F019-3: diagnose-first (K=1 default).** Cheaper to spend one warm-session
  diagnosis than one blind implementer re-dispatch; tune via the config knob.

### Open (resolve in authoring)
- Where exactly the "latest rejection cause = gate, not reviewer" predicate lives
  (a daemon `_build_input` helper feeding a new `ReconcileInput` field vs. inline in
  the ladder) — mirror `_triage_classes`' own daemon-computes-input pattern.
- Output-tail size + redaction (no secrets in the persisted gate log).

### Scope
- **Frozen-core:** `reconcile.py` (the diagnosis-dispatch trigger branch) — SOLO
  gate + full adversarial review, Sonnet (L10).
- **Executor:** `daemon.py` (gate output capture, gate-diagnosis dispatch mode,
  `_triage_classes`/`_build_input` extension), `types.py` (GateResult field / any new
  marker), `config.py` (`gate_diagnosis_after_failures`).
- All behind existing pipeline composition; byte-identical where the knob is absent
  and no gate fails.
