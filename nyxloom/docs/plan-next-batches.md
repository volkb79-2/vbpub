# Plan — next implementation batches (2026-07-25 checkpoint)

**State:** vbpub/nyxloom `main` @ `8af765b7`, tree clean, daemon `nyxloom-prod-nyxloomd`
STOPPED. Pipeline hardened this session: A/F/G/GA1/GA2/D-part-1 merged; gate is
parallel (`-n auto`) + coverage-honest (pytest-cov) + auto-reverting (F) + verifiable
(`nyxloom gate verify`, GA1) + rigor-declaring (`asserts=`, GA2); review is
carve-targetable (`review_focus`, D-part-1); `STANDARD.md` has the gate contract +
validation methodology; concurrency **measured memory-safe** (gates don't OOM).

**Strategic switch-point:** the pipeline is hardened enough to DOGFOOD. The
highest-leverage next move is **F018 P2b (the unlock)** — after it lands, turn the
daemon on and let the factory self-build the rest. Batches B–E are dogfood candidates
(or continue controller-driven if preferred).

## BATCH A — F018 P2b: deterministic carver-session planner (THE DOGFOOD UNLOCK) · HIGH frozen-core
Spec: `docs/plan-long-running-carver.md` §4.1–4.3 (346–475, contract+determinism) +
§3.3 (329–345, one-turn priority) + §2.4 (165–187, session states) + §6.2 (571–591,
compaction triggers) + §12 Package 2 (927–969, the buildable unit + oracles).

**DECOMPOSITION (code-grounded 2026-07-25 — supersedes the earlier guess):** the P1
view types (`CarverSessionSnapshot`/`MergeDigest`/`CarverTurnResult`/`ValidatedCarveProposal`/
`CarverFeed`/`HumanIntake`) ALREADY exist in `carver_session.py` (merged `facdac74`); P2a's
`merge_digest.py` producer ALREADY exists (`7b0913b7`). **§5 session lifecycle/lease is
Package 3, NOT P2b** (that's the *executor*; P2b is planner-only). So P2b = **two serial
packages**:
- **A1 — reconcile.py deterministic planner (the frozen-core HEART, full adversarial review).**
  Add 4 `Action` dataclasses (`StartCarverSession(mode,source_ids)` /
  `ResumeCarverSession(mode,source_ids,generation)` / `CompactCarverSession(generation,trigger)` /
  `AdmitCarveProposal(proposal_id,artifact_ids)`) + 4 additive `ReconcileInput` fields
  (`carver_session: CarverSessionSnapshot|None=None`, `pending_carver_feeds=()`,
  `pending_human_intakes=()`, `validated_carve_proposals=()` — same empty-default convention as
  every field since P34/B4b) + the `plan_project()` logic emitting **≤1 carver turn per pass** in
  the §3.3 priority order (proposal-admit→bootstrap→merge-feed→compact→re-scope→intake→
  test-health→headroom), branching on `carver_session.status` (§2.4) and compaction triggers
  (§6.2, precomputed on the snapshot) + short `ReconcileTrace` breadcrumbs. **Minimal seam:** the
  new actions cover only the NET-NEW slots (admit/bootstrap/feed/compact/intake); existing
  re-scope/test-health/headroom keep emitting `CarveDispatch` (spec-sanctioned migration alias,
  §4.2) under ONE shared "carver-turn-planned" mutex. **Byte-identical when `carver_session is
  None`** (feature-off ⇒ every new slot empty ⇒ today's exact path). CarveDispatch→ResumeCarverSession
  rename deferred to P3.
- **A2 — daemon.py input-builder derivation (`_build_reconcile_input`, the `ReconcileInput(...)`
  at daemon.py:962; medium review).** Derive the 4 snapshots from durable event cursors
  (`carver_session.project_session(events)`/`load_session`; unconsumed feeds/proposals/intakes
  since the session cursor). Default-off ⇒ no `CARVER_*` events ⇒ empty snapshots ⇒ byte-identical
  production. Tested with synthetic event streams.

Oracles (from §12 Package 2): property test — identical `ReconcileInput` → identical ordered
actions, never >1 carver turn; NEGATIVE — pending merge feed + headroom schedules feed, not carve;
pipeline matrix — `full` plans session work, `gated`/`lean` never do; NEGATIVE — `carver_session
None` byte-identical to today. **P2b does NOT execute the new actions** (no executor until P3) —
keep `[stage.carve]` default-off in every live config until P3 lands.

`reconcile.py` is FROZEN-CORE: A1 gets a SOLO gate + a full adversarial review; A2 gets a SOLO
gate + medium review. A1→A2 serial (A2 depends on A1's fields). After A2 lands → **DOGFOOD
TRANSITION**: `docker start nyxloom-prod-nyxloomd`; decide `max_active_tasks` (concurrency is
memory-safe per the 2026-07-25 measurement — the gating factor is flake-tolerance, not RAM).

## BATCH B — finish gate-adoption · dogfood candidates (or manual)
Spec: `docs/plan-gate-adoption.md`.
- **GA2b** — coverage-canary: verify a *declared* `changed-line-coverage` assert (inject a
  never-called line, expect a coverage-floor gate to FAIL). Makes GA2's forward-defensive
  exit-override live. Advise a floor in the guide but never mandate it. SMALL; extends
  `gate_canary.py` + `cmd_gate_verify`.
- **GA4** — carver periodic gate re-verify: cadence knob (`gate_verify_interval_days`) + a
  reconcile item running `gate verify` per project + escalate on LAUNDERS/BROKEN. SMALL;
  touches `reconcile.py` (frozen-core — careful).
- **GA3** — onboarding offers to build a gate + separate test-env when a project has none or
  an untrustworthy one. MEDIUM; needs the onboarding engine (F2/F3/F4) internals.

## BATCH C — review-depth routing · D part 2 · MEDIUM
Spec: `plan-factory-hardening.md` §D + `plan-gate-adoption.md` §GA2. Route review
depth by the carver's complexity band AND declared gate rigor (`asserts`). **~19-test
frontier-review blast radius; needs a complexity-band signal** (add one, or derive from
scope size / frozen-core touch). Frozen-core-adjacent (adapters/routing); argv_max-bounded
prompt appends (same idiom as D-part-1's `review_focus`).

## BATCH D — test-health + mutation (enables H + reliable concurrency)
- **Flake-hardening** — deterministic tests for the intrinsic flakes: `commands.py:269`
  poll race; the real-`os.fork()` daemon/wrapper tests (fragile under load/py3.14). Enables
  reliable concurrent gates. Test-health theme (D-065).
- **Mutation fan-out** — G's deferred half: parallelize `mutation_gate` per-mutant (needs
  per-mutant worktree isolation — `_run_is_killed` writes in-place). Leaf. Enables H.
- **H** — frozen-core mutation audit (reconcile/daemon/storage/types, whole-module). Epic;
  needs fan-out + a budget.

## BATCH E — epic (design-first)
- **C** — system→system lessons channel: a `LESSON_DISCOVERED` record → `nyxloom-trove/
  LESSONS.md` (+ upstream proposal for `scope: product`). Design doc first.

## Per-project gate adoption (cross-repo; dogfood candidates for those projects)
dstdns `B040`, naf `B039`, topos `B-046` — add a coverage floor + xdist + canary-verify to
each project's own gate (mirrors what G did for nyxloom).

## Recommended order
1. **BATCH A (P2b)** — the unlock; fresh context, decompose first, careful frozen-core review.
2. **DOGFOOD ON** — then the factory builds B → C → D → E + per-project adoption itself,
   or continue controller-driven if preferred.
