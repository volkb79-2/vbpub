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

## BATCH A — F018 P2b: reconcile.py deterministic planner (THE DOGFOOD UNLOCK) · HIGH frozen-core
Spec: `docs/plan-long-running-carver.md` §4 (lines 346–475) + §2 (architecture) + §5
(session lifecycle). **Big (1285-line spec) — READ then DECOMPOSE before dispatching.**
Likely sub-packages:
- **A1** — `ReconcileInput` snapshot type + `plan_project()` purity refactor (keep it PURE;
  carve-less path byte-identical; one-carver-per-pass).
- **A2** — typed action set (`Start`/`Resume`/`Compact`/`AdmitCarveProposal`) +
  deterministic transitions (§4.3 no-nondeterminism); one-turn priority.
- **A3** — default-off wiring + session lifecycle/lease interaction (§5).

`reconcile.py` is FROZEN-CORE: each sub-package gets a SOLO gate + a full adversarial
review (reuse the review agent). After A lands → **DOGFOOD TRANSITION**: `docker start
nyxloom-prod-nyxloomd`; decide `max_active_tasks` (concurrency is memory-safe per the
2026-07-25 measurement — the gating factor is flake-tolerance, not RAM).

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
