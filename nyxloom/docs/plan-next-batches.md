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
- **P3a — carver session executor (Start + Resume) + behavioral fake** *(✅ MERGED `fbe97a3f`,
  190/190 gate, Opus APPROVE-WITH-NITS — exit-consumer architecture, byte-identical off)*: in
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
- **P3b — proposal validation + admission** *(✅ MERGED `121848d3`, 184/184 gate, Opus review found
  + fixed a BLOCKING bug: AD1 re-scope supersession never fired because the ordinary new-handoff scan
  pre-created the task, tripping the structural cursor → fixed by making a real `CARVER_PROPOSAL_ADMITTED`
  event the exclusion cursor; proven by an unstubbed run_pass regression test. AD2 path-normalization
  fixed. concern-1 gen-filter live. AD3 repair-over-count = deferred nit.)*: validate `CARVER_PROPOSAL_RECORDED` (envelope schema,
  path/hash, frontmatter, lint, `input_revision==base_revision`, oracle-satisfiability) →
  `ValidatedCarveProposal` in the daemon input-builder, **current-generation-filtered (concern-1)**;
  execute `AdmitCarveProposal` (effect-boundary hash recheck → `CARVER_PROPOSAL_ADMITTED` → create
  tasks from parsed Frontmatter → re-scope supersession ONLY on admission; bounded repair →
  NEEDS_OPERATOR after N). Consider carrying `generation` on `AdmitCarveProposal`.
  **P3a-DEFERRED (HARD requirement — infinite-feed guard):** P3a's Resume executor never emits
  `CARVER_CONTEXT_CONSUMED`, so `snapshot.last_consumed_event_sequence` never advances — a
  successful merge-feed/intake resume does NOT shrink `pending_carver_feeds`, so the planner would
  re-emit the SAME feed every pass (infinite re-feed **once the feature is enabled**). Inert while
  `cfg.carve.session=="fresh"`, but **P3b MUST emit `CARVER_CONTEXT_CONSUMED` (advancing the cursor)
  on a successful feed/intake turn** before the feature is ever enabled. Owner = whoever advances the
  consumption cursor (natural fit: the proposal/consumption pipeline here).
- **P3c — the PRODUCING side (makes the carver AUTHOR): §2.1-CONFIRMED, DAEMON.PY-ONLY.**
  > **STATUS 2026-07-25 — MERGED (b3480e94).** Branch `feat/f018-p3c-producing-side` merged
  > `--no-ff` into main (onto a topos-advanced main; disjoint files, base-guard + ancestry verified);
  > worktree+branch cleaned up. Gate GREEN (full suite + diff-coverage 110/110 changed lines = 100%).
  > Adversarial review CLEAN: byte-identical-off holds; the legacy `_build_carve_packet` output is
  > AST-verified byte-identical after the `_carve_packet_body_lines` extraction (133 identical string
  > constants); AD1 (no launch-time supersede — supersession waits for P3b admission, L6) + AD2
  > (empty-`artifacts` → RESUMED-only, no proposal) both IN.
  > **The pre-compaction "worktree != cfg.root" hypothesis was WRONG (L8):** that assert always
  > PASSED — the real RED was a STALE TEST (the branch-authority oracle wrote an empty-artifacts
  > envelope via `_carve_envelope(task_id)` and asserted `CARVER_PROPOSAL_RECORDED`, which AD2 now
  > correctly suppresses). Fixed by supplying a non-empty artifacts list (commit `26e9314e`); this
  > proved BOTH the AD2 semantics AND the §2.3 branch-authority worktree-prefix path resolution
  > (the worktree IS minted; `_carver_proposal_report_path` finds the envelope). AD3/AD4 → P3d/P4. §2.1:
  `carve` is a TURN MODE of the one session that "author[s] handoff candidates for headroom,
  re-scope, targeted intake, or test health." **Key simplification (do NOT touch reconcile.py):**
  §4.2's "`CarveDispatch` as migration alias" is normalized at the EXECUTOR, not the planner — the
  planner keeps emitting `CarveDispatch`; the executor decides fresh-vs-session. Two coupled halves,
  both in `daemon.py`:
  (a) **`_execute_carve_dispatch` normalize:** when `cfg.carve.session=="project-persistent"` AND a
  WARM session exists, run the carve as a `carve`-mode SESSION RESUME (reuse `_execute_resume_carver_session`'s
  path) instead of a fresh throwaway carve; else → today's fresh carve (byte-identical when
  `session="fresh"` OR no WARM session). A1's ladder stays untouched (feed/intake still pre-empt via
  the shared mutex).
  (b) **proposal recording:** define the carve-turn OUTPUT CONTRACT (a `CarverTurnResult` §4.1 envelope
  the carve turn writes — a handoff file under `handoff_globs` + a JSON envelope, analogous to today's
  `CARVE-<seq>.md` REQUIRED OUTPUT parsed by `_consume_carve_exit`), and extend
  `_consume_carver_session_exit` (P3a) so a `carve`/`repair-proposal` turn parses it → emits
  `CARVER_PROPOSAL_RECORDED{proposal_id, generation, source_ids, artifact paths/hashes, dispositions}`
  (§2.2) — the exact event P3b already validates+admits, closing the loop. Fake must write a
  `CarverTurnResult`. Full review. **Fold-in (§2.2 gap from P3a):** `CARVER_SESSION_RESUMED` should
  carry `{generation, turn-id/mode, source_ids, route}` — P3a emits only `{generation, route}`.
- **P3d — rotation + bounded resume recovery + DEGRADED policy (concern-3) + compaction→rotation
  fallback (§6.1/§5.4)**: typed rotation conditions, cold recovery from durable truth, and the
  concern-3 decision (DEGRADED-exhausted sets the mutex / escalates, not silent legacy fall-through).
  > **STATUS 2026-07-25 — MERGED (5b7fd935).** daemon.py-only executor package (the A1 planner + the
  > carver_session projector already had every ladder branch + status fold), built by deepseek
  > (reasonix), controller-gated (96/96 changed lines, 100%) + adversarially reviewed. Closes
  > checklist items 3/4/5/6 (5=partial WARN, hard-REJECT deferred until P4). AD3 + concern-2 ack
  > cursor deferred to P4 (need reconcile.py). Byte-identical-off.
### ✅ PRE-ENABLEMENT CHECKLIST — CLEAR (from the P3a Opus review — feature-on runaways, all INERT feature-off)
`cfg.carve.session="project-persistent"` was gated on ALL of these clearing. P3a shipped them latent
(default `session="fresh"` keeps them dormant); each later package closed one, and as of 2026-07-25 all
six are ✅ (see the CHECKLIST FULLY CLEAR note below). Kept here as the enablement audit trail:
1. ✅ **Ack cursor (concern-2, the headline) — CLOSED by P4a (`54f1ac0f`).** A successful merge-feed
   turn now emits `CARVER_CONTEXT_CONSUMED{highest_event_sequence, spine_revisions}` in
   `_consume_carver_session_exit`, advancing `last_consumed_event_sequence` so consumed digests stop
   re-firing every pass. Safe on the SHARED cursor (also read by `_validated_carve_proposals`) because
   the ladder gives `AdmitCarveProposal` (slot 1) strict priority over merge-feed (slot 3) under one
   mutex — a merge-feed never runs, never advances the cursor, on a pending-proposal pass. daemon.py-
   only; frozen core untouched; gate 23/23 (100%).
2. ✅ **Unhandled `CompactCarverSession`/`AdmitCarveProposal` (AF1) — CLOSED.** `_execute` now
   dispatches both to real handlers (`AdmitCarveProposal`→P3b `_execute_admit_carve_proposal`;
   `CompactCarverSession`→P3d `_execute_compact_carver_session`, the compaction→rotation fallback). The
   `else` `raise` now only fires for a genuinely unknown action type — no `TICK_ERROR` storm.
3. ✅ **Bootstrap/resume ack-content validation (concern-3, §5.1 item-6) — CLOSED by P3d (5b7fd935).**
   `_consume_carver_session_exit` now requires a valid `BOOTSTRAP-ACK.json` (echoing the spine
   revisions) for a start/recover turn; a DONE-but-didn't-bootstrap turn folds to
   `DEGRADED{bootstrap-ack-invalid}`, never falsely WARM.
4. ✅ **`NEEDS_OPERATOR` route-storm (concern-5) — CLOSED by P3d (5b7fd935).** `_needs_operator_recently_
   emitted` debounces `carver-no-route`/`carver-compaction-no-driver` to once per unresolved episode
   (cleared by `CARVER_SESSION_ROTATED`/`STARTED`).
5. ✅ **Enablement guard (AF3) — DONE (`a962e731`).** Now the checklist is fully clear, the once/daemon
   startup line is an INFORMATIONAL `carver.enablement.active` pilot acknowledgement (no more
   `missing_items`, no "premature", and deliberately NOT promoted to a hard REJECT): the feature is
   enablement-ready, so enabling `project-persistent` is the operator's decision, and the guard just
   makes an active pilot visible once at boot.
6. ✅ **Cold/degraded re-scope launch-supersede — CLOSED by P3d (5b7fd935).** `_execute_carve_dispatch`
   now catches a feature-on non-WARM session after the WARM-normalize check: a DEGRADED-recovery-
   exhausted session ROTATES (no legacy fresh-carve-and-supersede), closing the §4.2 data-loss window
   off the warm path; any other unexpected non-WARM status escalates instead of falling through.

**✅ CHECKLIST FULLY CLEAR (2026-07-25).** All six items closed (1 by P4a `54f1ac0f`; 2 by P3b+P3d; 3/4/6
by P3d `5b7fd935`; 5 by `a962e731`). F018 is **enablement-ready** — `cfg.carve.session="project-persistent"`
is now safe to set, and doing so is the **operator's decision** (see ENABLEMENT CHECKPOINT below).

**OPTIONAL, non-blocking follow-ups** (all feature-dark; none gate enablement):
- **P4b / AD3 — structural-invalid carve envelope → bounded repair-proposal turn** — ✅ **DONE, MERGED
  `87d1f4e1` (2026-07-25).** Implemented exactly as scouted: `CarveRepairRequest` DTO in the frozen
  projector (pure dataclass, no fold change — design-open #2 resolved that way); WARM-ladder repair slot
  `admit(1) > repair(new) > merge-feed(3) > compaction(4)` with NO `source_ids` (P4a cursor untouched);
  daemon `_pending_carve_repairs` gated `1 <= invalid < max_proposal_repairs` so it composes with P3b's
  escalation by one `<`/`>=` boundary (never double-fires); repair packet names the invalid ids + a
  5-point re-validation checklist. Oracles O1–O7 (+ wrong-gen/storage-error/determinism); SOLO gate
  GREEN (pytest 0, diff-cov 41/41 = 100%). LOG: `docs/handoff/f018-ad3-LOG.md`. **Discovered a
  pre-existing frozen-core defect while doing so — see the new pre-enablement blocker below.** Original
  design notes retained for the record:
  **Half already built by P3b:** the `"repair-proposal"` write-authority mode string exists
  (`daemon.py:569` `_CARVE_WRITE_AUTHORITY_MODES`) and the *escalation ceiling* exists
  (`_carve_proposal_repair_escalations` daemon.py:2060 — at `invalid >= cfg.carve.max_proposal_repairs`
  it emits `NEEDS_OPERATOR{carver-proposal-invalid}`, debounced per generation). **Missing = the repair
  TURN itself** (P3b line 2071 explicitly deferred it: "no repair ResumeCarverSession mode exists in
  reconcile.py's ladder yet"). AD3 adds it:
  1. **reconcile.py ladder** (`plan_project`, the `elif status is WARM:` branch ~1153): BEFORE the
     merge-feed check, if there is an un-admitted invalid proposal for the current generation below the
     repair ceiling, plan `ResumeCarverSession(mode="repair-proposal", generation=snap.generation, …)`.
     A broken proposal is repaired before ingesting new feeds; it does NOT touch
     `last_consumed_event_sequence` (not a feed), so the P4a shared-cursor invariant is unaffected. Slot
     order becomes: admit(1) > repair(new) > merge-feed(3) > compaction(4).
  2. **ReconcileInput** (~627, add a field beside `validated_carve_proposals`/`pending_carver_feeds` —
     e.g. `pending_carve_repairs: tuple[…]=()`). **OPEN DESIGN (resolve in authoring): where does the
     repair-request type live?** `CarverFeed`/`ValidatedCarveProposal` live in the *frozen* projector
     `carver_session.py`; a `CarveRepairRequest{proposal_id, validation_error}` either joins them there
     (a pure dataclass, no fold change — arguably OK) or lives in reconcile.py. Pick the one that keeps
     the frozen-core forbid honest (see memory: "handoff scope.touch can be wrong" / "don't forbid the
     needed file").
  3. **daemon.py `_build_input`** (~1033): compute the field, reusing the P3b invalid-counting logic
     (`_validate_carve_proposal_payload` + generation filter from `_carve_proposal_repair_escalations`).
  4. **daemon.py `_execute`** (~5831, beside the existing resume/admit/compact dispatch): build the
     repair packet naming the validation failure(s) and resume the session in `"repair-proposal"` mode.
  5. **Oracles:** repair turn fires below ceiling (feeding the error back); NO repair at/above ceiling
     (the P3b escalation still fires — the two must compose, not double-fire); byte-identical off;
     shared-cursor unchanged. **Why deferred (not rushed):** enablement-ready without it, and a
     substantial reconcile.py change past the milestone warrants a carefully-authored spec + operator
     awareness rather than an unattended tail-end merge.
- Two P3d-review REFINEMENTS (both self-healing): (a) ack validation compares consume-time
  `_spine_revisions(cfg)` to the launch-time echo, so a spine commit racing a bootstrap yields one
  false `DEGRADED` that self-heals next recover turn — align with §6.2's "2+ docs" drift semantics;
  (b) `_needs_operator_recently_emitted` scans the full event log per no-route error path (rare) —
  window it if it ever matters.
- Minor cleanups: de-hollow the lease-lost test (uses `_scripted` stub, not the real planner);
  `_recent_merge_digest_ids` uses a raw `["digest_id"]` index vs A2's `.get` (KeyError on a malformed
  digest, feature-on only).

### ✅ PRE-ENABLEMENT BLOCKER CLEARED (found + fixed during AD3, 2026-07-25) — projector `last_turn_sequence` TypeError
**FIXED, MERGED `9c195269`.** `carver_session.project_session` had set `snap.last_turn_sequence` to
the **string** `turn_id` on `CARVER_PROPOSAL_RECORDED` (old `carver_session.py:217`); a following
`CARVER_SESSION_RESUMED` then folded `last_turn_sequence += 1` (`:200-201`) → `TypeError: str + int`.
Because `daemon._carver_session` wraps only `iter_events`, not the fold (`daemon.py:1904-1908`), it
became a **persistent per-tick TICK_ERROR** the moment any proposal-then-resume ordering existed in a
project's log — pre-existing (repro'd with two plain merge-feed/carve turns) but reliably produced by
the AD3 repair flow. **Fix (structural):** drop the string assignment — `last_turn_sequence` is an int
RESUME-turn counter whose sole authority is `CARVER_SESSION_RESUMED`, and the proposal identity is
already captured by `last_proposal_id`. Regression oracle folds `[STARTED, RESUMED, PROPOSAL_RECORDED,
RESUMED]` without raising and asserts the counter stays an int. SOLO gate GREEN. Nothing regressed
today (feature was DARK throughout). Detail in `docs/handoff/f018-ad3-LOG.md`.

### ▶ ENABLEMENT CHECKPOINT (operator decision — do NOT auto-flip)
With the checklist clear, dogfooding the persistent carver is a deliberate manual→autonomous
control-handoff, reserved for the operator. The pilot steps, when approved:
1. Set `cfg.carve.session = "project-persistent"` on ONE pilot project (leave others `"fresh"`).
2. `docker start nyxloom-prod-nyxloomd` (it stays STOPPED until then — two-dispatcher guard).
3. Lift `carve_ahead_target` 0→N and `test_health_interval_days` 0→14 for the pilot.
4. Watch the once-per-daemon `carver.enablement.active` line + merge-feed cadence + context growth.

`reconcile.py` is FROZEN-CORE: A1 gets a SOLO gate + a full adversarial review; A2 gets a SOLO
gate + medium review. A1→A2 serial (A2 depends on A1's fields). After A2 lands → **DOGFOOD
TRANSITION**: `docker start nyxloom-prod-nyxloomd`; decide `max_active_tasks` (concurrency is
memory-safe per the 2026-07-25 measurement — the gating factor is flake-tolerance, not RAM).

## BATCH B — finish gate-adoption · dogfood candidates (or manual)
Spec: `docs/plan-gate-adoption.md`.
- **GA2b** — coverage-canary: verify a *declared* `changed-line-coverage` assert (inject a
  never-called line, expect a coverage-floor gate to FAIL). Makes GA2's forward-defensive
  exit-override live. Advise a floor in the guide but never mandate it. SMALL; extends
  `gate_canary.py` + `cmd_gate_verify`. ✅ **DONE (merge `a8ac7b3b`, 2026-07-25):** `inject_uncovered_line`
  + `verify_gate_enforces_coverage` (shared `_verify_gate_kills_canary` engine, `verify-coverage-canary`
  phase) + `cmd_gate_verify` `coverage-floor:` line gated on the assert being declared; discriminating
  integration test (coverage gate KILLS / tests-only gate LAUNDERS the same canary); `mutation` now the
  sole probe-less assert; advisory-not-mandate note in `reference/STANDARD.md`. SOLO gate GREEN
  (pytest 0, diff-coverage 42/42 = 100%); post-merge re-verify on main GREEN.
- **GA4** — carver periodic gate re-verify: cadence knob (`gate_verify_interval_days`) + a
  reconcile item running `gate verify` per project + escalate on LAUNDERS/BROKEN. SMALL;
  touches `reconcile.py` (frozen-core — careful).
- **GA3** — onboarding offers to build a gate + separate test-env when a project has none or
  an untrustworthy one. MEDIUM; needs the onboarding engine (F2/F3/F4) internals.
  - **v1 ✅ DONE** (`3cc0f40f`): detect + offer (`onboarding_gate.assess_gate`, `--check-gate`).
  - **v2 ✅ DONE** (`bdb0daab`, 2026-07-26): `gate_scaffold.py` writes a Dockerfile + a real
    `[gates.scaffolded-pytest]` skeleton (adjust-markers) via `--scaffold-gate`; flips
    `has_gate` True; line-surgical TOML (no `tomli_w`). Gate 58/58. v3 = LAUNDERS/BROKEN-verdict
    reaction + multi-ecosystem (deferred).

## BATCH C — review-depth routing · D part 2 · MEDIUM · ✅ DONE (merge `d64138a1`, 2026-07-26)
Spec: `plan-factory-hardening.md` §D + `plan-gate-adoption.md` §GA2. Route review
depth by the carver's complexity band AND declared gate rigor (`asserts`).
**DONE via the PROMPT-DIRECTIVE lever, NOT tier selection** (D-BATCHC): only ONE review
tier (`review-3`) exists today, so routing depth by *tier* is D-R2/D-R3 (unbuilt, after F5).
Instead `adapters.compute_review_depth_directive(tier, scope_touch, gate_asserts)` derives a
review-depth directive from two ALREADY-EXISTING signals — the handoff's `Frontmatter.tier`
band (`implement-1/2/3`, scope-size fallback >5 touched paths) + the project's declared gate
rigor (`select_verification_gate(cfg).asserts`; shallow = missing `changed-line-coverage`
and/or `mutation`) — and `build_dispatch` appends it to the REVIEW_INDEPENDENT prompt with
the argv-bounded `review_focus` idiom (appended LAST, truncate-or-skip, role-scoped).
**Byte-identical when neutral** (low band + rigorous gate → `""` → no-op; proven vs the
`_PRE_D1` snapshot). No new schema field, no route/tier change (dodged the ~19-test
`for_role`/`for_tier` blast radius entirely). Daemon touch = the single `LaunchReview`
cold-dispatch site (both `None` cases guarded). Gate 36/36 diff-cov green; adversarially
reviewed (frozen-core-adjacent). **Deferred to D-R2/D-R3:** actual reviewer-tier/model
SELECTION by band (needs `review-1`/`review-2` routes built first).

## BATCH D — test-health + mutation (enables H + reliable concurrency)
- **B3-followon — ✅ DONE (merge `f9f5234f`, 2026-07-26).** "gates async-with-timeout"
  from `plan-flow-hardening.md`'s B3/P71 (per-stage concurrency shipped the
  `concurrency`/serial-1 knobs; this was its deferred second half). The
  post-merge gate ran as a fully blocking `subprocess.run(...,
  timeout=gate.timeout_seconds)` inline in the reconcile pass — `Daemon.run()`
  iterates registered projects sequentially in one thread, so a slow gate for
  one project stalled every other project's pass for up to `timeout_seconds`
  (flagged as a known follow-up when the sync design was originally chosen).
  Converted to the background-thread-plus-drain shape GA4's gate-verify
  cadence already proved: `_run_post_merge_gate` is now the idempotent
  dispatcher (keyed per `task_id`), `_run_post_merge_gate_bg` does all
  git/filesystem/subprocess work off-thread and never touches daemon state,
  `_drain_post_merge_gate_results` (once per pass) is the sole main-thread
  seam appending GATE_FINISHED/MERGE_REVERTED/TASK_BLOCKED/COMPLETED,
  byte-identical to the old branch logic. Pre-merge/mutation gates inside
  `_execute_auto_merge` remain synchronous (deliberately out of scope). Scope
  grew by 2 files beyond the original handoff (`test_post_merge.py`,
  `test_auto_merge.py` — a scoping gap, not overreach: 6 existing tests
  assumed synchronous completion within one `run_pass`); adapted via
  join-then-drain, with the two real-`plan_project` tests draining directly
  to avoid a genuine double-dispatch race a naive extra pass would hit. Gate
  green 2× (agent) + 2× (controller re-gate) + post-merge 2×, 100% diff
  coverage (59/59) throughout; one post-merge run hit the same pre-existing
  `test_mutation_gate.py` hash-seed flake noted under P27-followon below —
  attributed, not a regression (unrelated files).
- **Flake-hardening** — deterministic tests for the intrinsic flakes: `commands.py:269`
  poll race; the real-`os.fork()` daemon/wrapper tests (fragile under load/py3.14). Enables
  reliable concurrent gates. Test-health theme (D-065).
  - **B25 — ✅ DONE (merge `33055a38`, 2026-07-25).** De-flaked the xfail'd
    `test_transient_throttle_resumes_same_attempt_end_to_end` by driving the transient leg
    through a synchronous in-process wrapper (`_sync_launch` mirrors `fake_launch_detached` +
    `wrapper_main` inline, sequenced AFTER `run_pass` to respect the storage monotonic guard)
    instead of a real `os.fork()`; xfail dropped, full behavioral contract kept. Also: bounded
    receipt-poll for the two blind `time.sleep()`s in `test_launch_detached_script` (real fork
    KEPT), and `threading.Event` sync for the `test_commands.py` listener tests. Authoritative
    gate GREEN 3×. Test-only.
  - **B27 — ✅ DONE (merge `e615e765`, 2026-07-26).** `test_daemon.py::test_nonloopback_bind_
    prints_unauthenticated_notice` — a THIRD real-fork/global-state flake (distinct from B26's
    two), surfaced by BATCH C's post-merge (this round added ~17 tests → more `-n4` load → the
    latent flake became reliably reproducible: 3/3 red under full-suite load, 5/5 green in
    isolation). Root cause (found by emission-level instrumentation after TWO wrong hypotheses):
    `daemon.run()` reconfigures the process-global logger in-thread (`log_module.configure(paths.
    logs_dir())`, which closes all handlers); under load a concurrent/leaked daemon thread's
    `configure()` closes the file handler *between* this daemon's "daemon started" info write and
    its UNAUTHENTICATED warning write, so the warning is emitted but dropped to a closed handler
    (the daemon called `log.warning(..., http_bind="0.0.0.0")` once, yet the record reached NO
    file anywhere). Fix (test-only): assert on the warning EMISSION captured at `daemon.log.warning`,
    not the shared JSONL file (file persistence is `test_log.py`'s job). Flake-fix gate GREEN 3×
    under `-n4`; post-merge 2×. Lesson in `nyxloom-trove/LESSONS.md` PL7.
  - **B26 — CLEARED (watch-item), 2026-07-26:** ran the full suite 3× under `-n 4` on main
    @`4d1c26f8` — 3/3 `PYTEST_EXIT:0`; neither of B26's two suspect tests reproduced (a
    *different* real-fork flake, B27 above, did surface later under the round's heavier load). No
    de-flake dispatched for B26 (can't verify a fix against a non-reproducing flake). Remains a
    watch-item: if either recurs under load, de-flake via the B25 in-process seam or B27's
    assert-at-emission approach. Original candidate note follows:
  - **B26 (candidate, reported 2026-07-25 during B25 de-flake verification — NOT yet
    independently confirmed):** two OTHER real-fork behavioral tests intermittently red under
    `-n 4` parallel load, green 6/6 in isolation — `test_config_ui.py::test_policy_update_full_flow`
    (a captured reconcile-pass-count race, `assert 2 == 1`) and
    `test_behavioral.py::test_fake_approved_review_reaches_merge_ready` (state stuck QUEUED, not
    MERGE_READY, after 20 ticks). Same real-fork-under-parallel-load class as B24/B25. If it
    recurs, de-flake them the same way (in-process wrapper seam). Until then, a spurious full-suite
    red in EITHER of these two is a known flake, not a regression — attribute before reacting.
- **Mutation fan-out** — G's deferred half: parallelize `mutation_gate` per-mutant (needs
  per-mutant worktree isolation — `_run_is_killed` writes in-place). Leaf. Enables H.
- **H** — frozen-core mutation audit (reconcile/daemon/storage/types, whole-module). Epic;
  needs fan-out + a budget.
- **P27-followon — ✅ DONE (merge `be04f602`, 2026-07-26).** `log.configure()`'s root cause
  behind B27: `removeHandler`-then-`addHandler` left a real window where `root.handlers` was
  the empty list; a concurrent `log.warning()` landing in that window silently vanished (no
  exception, no output — exactly B27's symptom). Fixed at the source: build the new handler
  set off to the side, swap it in with one atomic list-object rebind (CPython's GIL makes a
  plain attribute rebind observably atomic — a concurrent reader sees fully-old or fully-new,
  never partial), close old handlers only after the swap. New concurrency regression test
  proven to fail 3/3 against the old code and pass 3/3 against the fix. Gate green 2× (agent)
  + 1× (controller) + post-merge 1× green with zero failures.
  - **New watch-item discovered during controller re-gate:** `test_mutation_gate.py::
    test_evaluate_parallel_matches_serial_reference` failed once on the pre-merge gate run
    (`total=10, killed=10, survivors=[]`) — unrelated to this branch (touches only `log.py`/
    `test_log.py`). Root cause: the test's stub bucket function is `hash(key) % 3`, and
    Python randomizes string-hash seeds per process by default (no `PYTHONHASHSEED` pinned
    anywhere in this harness) — its own "arbitrary but stable" comment is only true *within*
    one process, not *across* invocations. Confirmed pre-existing: passed in isolation against
    bare `main` on a separate invocation; post-merge full-suite run was clean (0 failures). Not
    de-flaked this round (attribution was sufficient to safely merge P27-followon). Fix, when
    picked up: either pin the stub's split via an explicit deterministic mapping instead of
    `hash()`, or set `PYTHONHASHSEED=0` for this test via a fixture/marker.
- **DRY standing instructions — ✅ DONE (merge `3a6e7541`, 2026-07-26).** Standing DRY
  (non-duplication) instruction on every IMPLEMENTER/REVIEW_INDEPENDENT dispatch
  (`routing-model-redesign.md` D-R2). Both budgeted, skip-if-tight appends; REVIEW_INDEPENDENT's
  respects the same `-200` margin the doctrine manifest reserves (a real past incident, B4b,
  documented in `test_review_independent_prompt_stays_under_argv_max_with_real_paths`'s own
  docstring). Controller had to fix the implementer's own approach twice: an unconditional literal
  append broke a real `argv_max=1000` route (1023>1000), and a follow-up attempt widened test
  thresholds instead of fixing the underlying append pattern (masking the regression rather than
  fixing it) — reverted, then correctly rebuilt as a budgeted append mirroring `review_focus`/
  `review_depth`'s existing idiom.
- **incapable REJECT_CLASS + tier-bump re-carve + escalated-review handoff — ✅ DONE (merge
  `a7f8c111`, 2026-07-26).** New reviewer-facing class distinct from `architectural` (D-R3,
  "refined 2026-07-26"): `architectural` = scope/design wrong, re-scope; `incapable` = scope fine,
  model wasn't capable, bump tier. Threaded through `reconcile.py`'s triage table (distinguishable
  transition notes, never collapsed), `daemon.py`'s rescope-dict builder + carve-prompt conditional
  intro (byte-identical otherwise), and a new `escalation_note` bounded-append seeding the
  escalated review with a terse, non-anchoring meta-note (via `source.ref` provenance — deliberately
  not `depends_on`, which would deadlock dispatch on a superseded origin task). High-quality
  self-contained implementation; merging it with the just-landed DRY package produced a real,
  correctly-resolved `adapters.py` conflict (both append blocks at the same insertion point —
  kept both, `escalation_note` before `DRY` per the existing priority-order convention) and
  surfaced that `incapable`'s longer `REJECT_CLASS` text (a *permanent* unconditional addition,
  unlike DRY's *optional* one) itself ate into the `real_paths` regression test's margin — the
  implementer had already correctly re-anchored that test's threshold `1300→1400`; the sibling DRY
  test needed the same update, done post-merge.
  - **New watch-item, third occurrence:** `test_daemon.py::test_resume_attempt_emits_warning_
    attempt_retry` failed identically 3/3 post-merge runs under `-n4` (log record captured in
    stdout per the test's own capture, but absent from `_read_log_records`' re-read) — passes 3/3
    in isolation, untouched by any file in this round's diffs. Same shared-global-state-under-load
    signature as B26/B27 (PL7) — attributed as pre-existing, not a regression. Not de-flaked this
    round.

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
