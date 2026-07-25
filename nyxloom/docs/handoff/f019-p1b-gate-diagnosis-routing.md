# F019 P1b — gate-failure diagnosis routing (reviewer classifies, planner routes)

> **Manual controller dispatch (not nyxloomd).** Sonnet implementer, frozen-core
> (`reconcile.py`). The controller (Opus) independently re-runs the REAL gate + a
> full adversarial review before merge. **SOLO gate** (do not run concurrent with
> other frozen-core work). Read `docs/plan-f019-failure-diagnosis.md` first.

## The one idea

A pre-merge/mutation gate failure already routes the task to `REVIEW_REJECTED`
(`daemon.py`, "pre-merge gate failed ...; not published"), which already flows
into B4b's triage table in `reconcile.plan_project` (`if tsf.state ==
TaskState.REVIEW_REJECTED:` ~L914). But a gate-caused rejection carries **no
`reject_class`** (no reviewer looked at it), so `inp.triage_class.get(fm_id)` is
None and it always lands in the `fixable/none` mechanical-retry branch — a blind
retry until attempts exhaust → `BLOCKED`. P1a (merged) now persists the gate's
output in `GateResult.output_tail`. **P1b makes the reviewer classify a
gate-caused rejection, so the EXISTING routing table sends it to the right
place** (architectural→carver re-scope, product→operator, transient→plain retry,
fixable→targeted retry now carrying the gate output).

## Invariant you MUST preserve (determinism + safety)

1. **The reviewer is a CLASSIFIER, not a controller.** It emits a typed
   `reject_class` into a `REVIEW_RECORDED` event; the pure `reconcile.plan_project`
   routes on it. Never move routing/branching decisions into the LLM leg. This is
   the same contract as B4b — extend it, do not subvert it.
2. **A gate-failed task NEVER re-enters the merge path.** The task stays in
   `REVIEW_REJECTED` throughout diagnosis (a state that never merges). Do NOT route
   a gate-failed, un-re-gated diff to `AWAITING_REVIEW`/approval — that risks
   merging un-gated code. Diagnosis is an *attempt dispatch on a REVIEW_REJECTED
   task*, not a state change toward merge.
3. **`types.py` `TASK_TRANSITIONS` is FROZEN.** The task stays in `REVIEW_REJECTED`;
   no new state and no new transition edge is needed. If you think you need one,
   STOP and BLOCK — you are off the intended design.

## Context to read first (exact seams)

1. `docs/plan-f019-failure-diagnosis.md` — the P1 design + D-F019-1/2/3.
2. `reconcile.py` `if tsf.state == TaskState.REVIEW_REJECTED:` (~L914) — the triage
   routing table (`tclass = inp.triage_class.get(fm_id)` → product/architectural/
   fixable). Your new trigger goes at the TOP of this branch, BEFORE the `tclass`
   routing: if the rejection is gate-caused and unclassified and past the threshold,
   plan a diagnosis dispatch and DO NOT fall through to the fixable/none retry.
3. `daemon.py` `_parse_reject_class` (~L4961) and `_triage_classes` (input-build) —
   how a reviewer's `reject_class` becomes `inp.triage_class`. The gate-diagnosis
   review must emit `REVIEW_RECORDED{verdict: rejected, reject_class}` in the SAME
   shape so these pick it up with no change to the routing table.
4. `daemon.py` review dispatch / `build_dispatch` for `Role.REVIEW_INDEPENDENT`
   (~L5680, `stage_context("review_independent")`, session-reuse B6/D-R10) — add a
   **gate-diagnosis mode**: the dispatch packet carries `GateResult.output_tail`
   (the P1a field) + the failed diff + the handoff, and instructs the reviewer to
   CLASSIFY-ONLY (the gate already decided "fail") into
   `{fixable | architectural | product | transient}`.
5. `daemon.py` gate sites (P1a) — `GATE_FINISHED{gate_result: {exit_code, output_tail}}`
   is the failure signal + evidence. A "gate-caused rejection" = the latest
   `GATE_FINISHED{exit!=0}` for the task with no later reviewer `REVIEW_RECORDED`.
6. `config.py` `CarveStageConfig`/`policy` — add `gate_diagnosis_after_failures: int
   = 1` (diagnose on the Nth consecutive gate-fail; default 1 — a warm-session
   diagnosis is cheaper than a blind implementer re-dispatch).

## Work

1. **Detect a gate-caused, unclassified rejection** — a daemon helper (mirror
   `_triage_classes`' own "read latest relevant event" pattern) → a new
   `ReconcileInput` field (e.g. `gate_diagnosis_pending: dict[str,bool]` or a set of
   task_ids) computed in `_build_input`. True when: state REVIEW_REJECTED, the
   latest rejection cause is a `GATE_FINISHED{exit!=0}` (no later REVIEW_RECORDED),
   no `reject_class` yet, and the consecutive gate-fail count ≥
   `gate_diagnosis_after_failures`.
2. **Plan the diagnosis dispatch** in the `REVIEW_REJECTED` branch, BEFORE the
   `tclass` routing: when the field is set for `fm_id`, emit the diagnosis-review
   dispatch action and `continue` (do NOT also plan the fixable/none retry this
   pass — the class must arrive first).
3. **Execute the diagnosis review** (daemon): dispatch `Role.REVIEW_INDEPENDENT` in
   gate-diagnosis mode (reuse the warm session), packet = {output_tail, diff,
   handoff}. On its DONE receipt, emit `REVIEW_RECORDED{verdict: rejected,
   reject_class}`. `transient` is a valid class (a flaky gate) → routes to a plain
   retry (feeding B24 backoff), NOT a re-scope/escalation.
4. **Route** — none needed beyond the existing table: once `reject_class` is set,
   `_triage_classes` surfaces it and the existing branch routes architectural→
   READY_TO_CARVE, product→NEEDS_DECISION, fixable/transient→the retry path (whose
   packet now embeds `output_tail`).

## Oracles (observable + negative + gate)

- **O1 (classify → route):** seed a REVIEW_REJECTED task caused by a
  `GATE_FINISHED{exit!=0, output_tail:"...FAILED..."}`, no class. A pass plans a
  gate-diagnosis REVIEW_INDEPENDENT dispatch; feed a DONE receipt whose verdict
  classifies `architectural`; the next pass routes the task to READY_TO_CARVE
  (re-scope) — NOT the blind retry.
- **O2 (no blind retry before class):** the SAME task, on the pass that plans the
  diagnosis, must NOT also plan the mechanical fixable/none re-dispatch.
- **O3 (transient → retry):** a diagnosis classifying `transient` routes to the
  retry path (feeding backoff), not re-scope/operator.
- **O4 (threshold):** with `gate_diagnosis_after_failures=2`, a single gate-fail
  does NOT trigger diagnosis (one retry absorbs a flake); the 2nd does.
- **O5 (reviewer-rejection untouched):** a NORMAL reviewer rejection (with its own
  reject_class) routes EXACTLY as today — the gate-diagnosis trigger only fires for
  a gate-caused, unclassified rejection. Full existing suite green.
- **O6 (determinism):** `reconcile.plan_project` stays pure — the new field is read
  from `ReconcileInput`, computed by the daemon; no I/O or LLM call in the planner.

## Gate (run it yourself, synchronously; commit first)

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c \
 'cd /workspaces/vbpub/.worktrees/feat/f019-p1b-gate-diagnosis/nyxloom && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -n auto -q \
    --cov=src/nyxloom --cov-report=json:/tmp/nyxloom-cov.json && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate \
    --base main --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom'
```
DONE = pytest 0 AND `diff-coverage OK` (100% changed lines). Read the verdict line
yourself. Commit before gating (coverage_gate diffs merge-base). Any `# pragma: no
cover` you add will be scrutinized in review — prefer a test that reaches the branch
(a defensive `except` is reachable via monkeypatch).

## Scope / forbid

- **Touch:** `reconcile.py` (the diagnosis trigger in the REVIEW_REJECTED branch +
  the new ReconcileInput field), `daemon.py` (`_build_input` compute, the
  gate-diagnosis review dispatch mode + REVIEW_RECORDED emit), `config.py`
  (`gate_diagnosis_after_failures`), tests.
- **FORBID:** `types.py` `TASK_TRANSITIONS` and any new TaskState/EventType (the
  task stays REVIEW_REJECTED; reuse REVIEW_RECORDED). `carver_session.py`,
  `storage.py`. The existing triage ROUTING table (product/architectural/fixable
  branches) — you ADD a trigger above it; do not alter its routes. Needing any of
  these ⇒ BLOCK.

## BLOCKED rule

If a named contract can't be met, or an oracle needs a forbidden file, write
`BLOCKED: <reason + file/oracle>` to `docs/handoff/f019-p1b-LOG.md`, commit only
that, exit. A product/ambiguity call is a `D-<NNN>` in the plan doc, not a BLOCKED.

## Definition of done

O1–O6 pass; gate GREEN (pytest 0 + diff-coverage 100%); reviewer-rejection path
byte-identical; determinism preserved (planner pure); `TASK_TRANSITIONS` untouched;
branch committed. This makes a gate failure route as intelligently as a review
rejection already does.
