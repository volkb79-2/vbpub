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
> **STATUS 2026-07-25: F018 P2b COMPLETE.** A1 (reconcile.py planner) merged `7654549f`;
> A2 (daemon.py input-builder) merged `41dffad2`. Both gate-green (85/85, 35/35), Opus-reviewed,
> post-merge suites green. Feature gate = `cfg.carve.session == "project-persistent"` (default
> `"fresh"` = off), so the new carver-session machinery is LIVE-BUT-DARK: the planner+input exist,
> byte-identical in prod, awaiting P3 (the executor) + an explicit enable.
> **NEXT FORK:** (a) F018 P3 (persistent bootstrap/resume/admission executor — makes the machinery
> functional, closes A2 concern-1 gen-filter + concern-3 DEGRADED policy; frozen-core, keep on the
> controller loop), or (b) DOGFOOD TRANSITION (start daemon + lift the `carve_ahead_target=0` /
> `test_health_interval_days=0` freeze → let the daemon self-build B–E via the EXISTING carve path;
> a manual→autonomous control handoff — user's call). Dogfooding does NOT exercise the new P2b
> machinery (feature-off until P3+enable), so P3-first is the natural sequence.

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

**A1 review findings (Opus adversarial, 2026-07-25) — carry these forward:**
- **A2 HARD REQUIREMENT (concern 1, single-authority):** the daemon input-builder MUST populate
  `validated_carve_proposals` with **current-generation-only** proposals (filter by
  `snapshot.generation`). The pure planner deliberately does NOT filter (no drifting second copy);
  it admits the sorted-first proposal in the tuple. Without A2's filter, a stale gen-N proposal left
  on disk after a rotation to gen N+1 would be admitted (the admit slot fires before the
  ROTATING→bootstrap slot; the effect-boundary hash-recheck does NOT catch a generation mismatch
  because the handoff file still hashes identically). Consider also carrying `generation` on
  `AdmitCarveProposal` so P3's effect boundary can re-check.
- **P3 POLICY CALL (concern 3):** DEGRADED-with-exhausted-recovery currently plans nothing and does
  NOT set the carve mutex, so legacy item 12/15/9 `CarveDispatch` falls through. Inert feature-off,
  but once P3's `CarveDispatch→ResumeCarverSession(mode="carve")` migration alias lands, that
  fall-through would *resurrect* a dead session (defeating `max_resume_failures`) and could run a
  re-scope ahead of an un-ingested feed on that dead session (violates §3.3). Decide the policy when
  P3 lands: DEGRADED-exhausted should set the mutex (no legacy carve) or emit an explicit
  operator escalation — not silently revert to old-vocabulary carving. Spec §2.4/§5 under-specify
  this → likely a D-NNN.
- A1 fix-before-merge (DONE, merged): merge-feed + intake slots sort by `event_sequence` (§3.2
  event-order), not by `digest_id`/`intake_id` (arbitrary hash / id) — gate-invisible (determinism
  holds either way), caught only by review.

## F018 P3 — persistent bootstrap/resume executor + proposal admission (STARTED 2026-07-25)
Spec: `docs/plan-long-running-carver.md` §12 Package 3 (970–1010) + §5 (476–540 lifecycle) +
§4.1–4.3 (proposal contract) + §2.5 (bootstrap context) + §6.1 (compaction driver boundary —
P3 leaves compaction DISABLED; a compaction-due session uses the rotation fallback, no `/compact`).
**Frozen-core-adjacent** (daemon launch/effect boundary + adapters). Executes the A1 actions the
daemon currently can't run. Decomposition (serial; A2-reviewer reused):
- **P3a — carver session executor (Start + Resume) + behavioral fake** *(dispatched)*: in
  `daemon._execute` (:3515, isinstance-chain at :4289) add `StartCarverSession`/`ResumeCarverSession`
  branches. Start = mirror `_execute_carve_dispatch` (:2824): admissibility recheck → synthetic
  carver attempt → bootstrap packet (§2.5) → wrapper-launch with `{project}.strategic-carver` lease
  (:2950) → `adapters.capture_session` → `CARVER_SESSION_STARTED`+WARM; capture-fail →
  `CARVER_SESSION_DEGRADED`, never WARM on exit alone. Resume = `adapters.build_resume(session=
  snapshot.session_id, …)` (reviewer B6 pattern :3636/:3715), fresh turn-id, route-pinned,
  mode-specific packet (merge-feed/targeted-intake/recover). Byte-identical feature-off. Behavioral
  fake carver for E2E. Oracles: bootstrap captures S1→WARM; capture-fail→DEGRADED never WARM; resume
  reuses S1 fresh turn-id; daemon restart resumes S1; lease contention→one winner, loser
  `lease-lost-race`, no new generation/cursor advance; adapter lacking session-capability→ineligible.
- **P3b — proposal validation + admission**: validate `CARVER_PROPOSAL_RECORDED` (envelope schema,
  path/hash, frontmatter, lint, `input_revision==base_revision`, oracle-satisfiability) →
  `ValidatedCarveProposal` in the daemon input-builder, **current-generation-filtered (concern-1)**;
  execute `AdmitCarveProposal` (effect-boundary hash recheck → `CARVER_PROPOSAL_ADMITTED` → create
  tasks from parsed Frontmatter → re-scope supersession ONLY on admission; bounded repair →
  NEEDS_OPERATOR after N). Consider carrying `generation` on `AdmitCarveProposal`.
- **P3c — planner migration (`reconcile.py`): route `every carver turn` through the session when
  WARM** (Package 3 work-item 2): headroom/re-scope/test-health emit `ResumeCarverSession(mode=…)`
  instead of `CarveDispatch` when feature-on + WARM (completes the §4.2 alias — without this the
  persistent session never AUTHORS). Frozen-core; full review. *(Scope note: implied by "every
  carver turn"; not in Package 3's explicit item list — confirm vs §2.1 before building.)*
- **P3d — rotation + bounded resume recovery + DEGRADED policy (concern-3) + compaction→rotation
  fallback (§6.1/§5.4)**: typed rotation conditions, cold recovery from durable truth, and the
  concern-3 decision (DEGRADED-exhausted sets the mutex / escalates, not silent legacy fall-through).
After P3 → the carver is functional → enable `cfg.carve.session="project-persistent"` on a pilot,
then dogfood.

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
