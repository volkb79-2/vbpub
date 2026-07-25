# F018 P3d — carver rotation, compaction-rotation fallback, ack validation, pre-enablement guards

> **Manual controller dispatch (not nyxloomd).** This handoff is authored for a
> deepseek implementer run from `main` in a dedicated worktree. The controller
> (Opus) independently re-gates and adversarially reviews the committed branch
> before merge — your self-reported "done" is a hint, not the merge signal.

**Status:** ready · **Tier:** implement-2 · **Feature:** F018 long-running carver ·
**Last P3 package.** All work lands DARK behind `cfg.carve.session == "project-persistent"`
(default `"fresh"`); every legacy path must stay byte-identical when the feature is off.

## The one idea

The A1 planner (`reconcile.py`, merged, FROZEN — do not touch) already plans the entire
rotation/recovery/compaction ladder, and the projector (`carver_session.py`, merged, FROZEN —
do not touch) already folds every status event. **P3d is purely the EXECUTOR side in
`daemon.py`**: emit the already-folded events at the right moments, plus one config knob.
If you find yourself needing to edit `reconcile.py`, `carver_session.py`, `storage.py`, or
`types.py`, STOP and BLOCK — that means the scope is wrong.

## Context to read first (exact seams — read these, not the whole files)

1. `docs/plan-long-running-carver.md`:
   - §2.4 (Cold/warm/degraded/rotated states) — the status semantics.
   - §5.4 (Rotation and cold recovery) — the six typed rotation conditions.
   - §6.1 (compaction driver boundary) — **there is NO proven compaction driver; you MUST NOT
     hardcode `/compact`.** Production enablement needs "a compaction driver OR an approved
     rotation fallback decision" — P3d ships that rotation fallback.
   - §6.2 (trigger policy) — informational; the planner already computes the trigger.
2. `docs/plan-next-batches.md` — the "P3d" bullet and the **PRE-ENABLEMENT CHECKLIST** (items
   1–6). P3d owns items **3, 4, 5, 6** (item 1 = ack cursor and item 2's Compact-half are P4).
3. `src/nyxloom/reconcile.py:1104-1183` — the carver-session ladder (READ-ONLY). Note precisely:
   - `ABSENT/COLD/ROTATING` → `StartCarverSession` (consumes the carve mutex).
   - `DEGRADED` with `resume_failures < max_resume_failures` → `ResumeCarverSession(mode="recover")`.
   - `DEGRADED` with budget **exhausted** → plans NOTHING and **does NOT consume the mutex**, so a
     legacy `CarveDispatch` (item 9/12/15) can still fire this pass — this is the "silent legacy
     fall-through" you must close (Work item 2).
   - `WARM` + `_compaction_due()` → `CompactCarverSession(trigger=...)`.
4. `src/nyxloom/carver_session.py:149-223` — `project_session()` (READ-ONLY). Confirm the folds you
   rely on: `CARVER_SESSION_ROTATED`→`ROTATING`(+`new_generation`, `resume_failures=0`),
   `CARVER_COMPACTION_REQUESTED`→`COMPACTING`, `CARVER_COMPACTION_FINISHED`→`WARM`(+`new_generation`),
   `CARVER_SESSION_DEGRADED`→`DEGRADED`(+`resume_failures = retry_count | +1`).
5. `src/nyxloom/daemon.py` — the executor you WILL edit. Study these as your templates:
   - `_execute_carve_dispatch` (~3238) — the P3c WARM-normalize branch (~3273) is where Work item 2
     hooks in, right after it.
   - `_execute_start_carver_session` (~3789) and its bootstrap-packet builder — Work item 3 adds the
     required ACK output to that packet and its consume-time validation.
   - `_execute_resume_carver_session` (~3906) — already handles `mode="recover"` (line ~3923); the
     `carver-no-route` NEEDS_OPERATOR emission (~3933) is one of the debounce sites (Work item 4).
   - `_consume_carver_session_exit` (~4189) — the START/RESUME exit consumer; Work items 3 folds the
     ACK check into its `ok` computation, exactly as P3c folded the envelope check.
   - `_execute` action dispatch tail (~5602-5615) — add the `CompactCarverSession` branch here;
     today it falls to `else: raise ValueError("unhandled action type")` (AF1 → per-pass TICK_ERROR).
   - `_carver_session(project, cfg)` (~3576) — the MASTER GATE (returns `None` unless
     `session=="project-persistent"`). Every new branch must be reached only via a non-None snapshot.
6. `src/nyxloom/config.py` `CarveStageConfig` (~top) — knobs: `session`, `compact_after_turns`,
   `max_resume_failures=2`, `max_proposal_repairs=2`. Work item 6 adds `compaction_strategy`.
7. `tests/test_carver_session_executor.py` — your test home; mirror its fixtures
   (`carver_project` = files-authority, `carver_project_branch_authority` = branch; `_bootstrap_to_warm`,
   `patch_launch`, `_mark_turn_outcome`, `_snapshot`).

## Work (numbered, imperative — all in `daemon.py` + `config.py` + the test file)

1. **AF1 — `CompactCarverSession` executor (compaction→rotation fallback).**
   Add `_execute_compact_carver_session(project, cfg, states, action)` and route it from `_execute`
   (the `elif isinstance(action, reconcile.CompactCarverSession)` branch — put it next to the
   Start/Resume branches). Because §6.1 gives us NO proven compaction driver, the ONLY supported
   strategy is `compaction_strategy == "rotate"`: emit a single `CARVER_SESSION_ROTATED` event with
   payload `{"new_generation": snap.generation + 1, "reason": "compaction-rotate-fallback",
   "trigger": action.trigger, "from_generation": snap.generation}` (the projector folds it to
   `ROTATING`, and the planner cold-bootstraps a fresh generation next pass). Recheck the snapshot
   at the effect boundary (stale-plan → refuse cleanly, like the other executors). Do NOT hardcode
   `/compact`, do NOT launch a wrapper, do NOT call any driver. If `compaction_strategy` is any
   other value, this is an un-proven driver → emit `NEEDS_OPERATOR{"reason":"carver-compaction-no-driver"}`
   and do not rotate (subject to the same debounce as Work item 4).

2. **concern-3 + checklist #6 — close the not-WARM legacy fall-through in `_execute_carve_dispatch`.**
   Immediately AFTER the P3c WARM-normalize check (which returns early when `snap.status is WARM`),
   add: if `snap is not None and snap.status is CarverStatus.DEGRADED` (reachable here only when the
   recovery budget is exhausted — the planner consumes the mutex for every other feature-on state),
   the daemon must NOT run the legacy fresh-carve-and-supersede. Instead ROTATE: emit
   `CARVER_SESSION_ROTATED{"new_generation": snap.generation + 1, "reason": "degraded-recovery-exhausted",
   "from_generation": snap.generation}` and return (no synthetic carve task, no `TASK_SUPERSEDED`).
   This closes BOTH concern-3 (no silent legacy fall-through) and checklist #6 (a cold/degraded
   re-scope never launch-supersedes its origin with nothing to replace it — the §4.2 data-loss window).
   Add a defensive `else` for any *other* non-WARM feature-on status that could reach here
   (STARTING/COMPACTING/ROTATING/COLD should not, per the planner): emit
   `NEEDS_OPERATOR{"reason":"carve-dispatch-unexpected-status","status":<value>}` and return —
   never fall through to legacy carve while feature-on and not-WARM.

3. **concern-3 (#3) — bootstrap/recover ACK-content validation.**
   Today `_consume_carver_session_exit` records `CARVER_SESSION_STARTED`/`RESUMED` (→ WARM) on
   `DONE + captured session_handle` ALONE — a turn that finished but never actually loaded the
   bootstrap context is falsely WARM. Fix both halves:
   (a) **Prompt:** the bootstrap packet (in `_execute_start_carver_session`) AND the recover-mode
   resume prompt (`_build_carver_resume_prompt`, mode `"recover"`) must REQUIRE the carver to write a
   structured `BOOTSTRAP-ACK.json` under `cfg.reports_dir` (resolve its path with the SAME
   worktree-prefix helper P3c added, `_carver_proposal_report_path`-style, so branch authority
   works) containing at least `{"kind":"bootstrap-ack","spine_revisions":{<doc>:<rev>,...}}` echoing
   the spine revisions the packet supplied (get them via the existing `_spine_revisions(cfg)` used in
   the STARTED payload).
   (b) **Validation:** in `_consume_carver_session_exit`, for a `kind=="start"` turn AND a
   `kind=="resume" mode=="recover"` turn, parse+validate that ACK (structural parse + the ack's
   `spine_revisions` must match `_spine_revisions(cfg)`); a missing/malformed/mismatched ACK folds
   into the SAME `ok=False` DEGRADED path P3c uses for a bad envelope, with reason
   `"bootstrap-ack-invalid"` — never STARTED/RESUMED. Read-only non-recover modes
   (merge-feed/targeted-intake) are unaffected (byte-identical).

4. **concern-5 (#4) — debounce `NEEDS_OPERATOR{carver-no-route}` (and the item-1 compaction-no-driver).**
   These escalations currently fire EVERY pass while the condition persists (route drift since the
   pinned generation; no compaction driver). Emit each at most once per unresolved episode: before
   appending a `NEEDS_OPERATOR` with one of these reasons, scan the durable event log for an
   already-emitted, not-yet-resolved `NEEDS_OPERATOR` with the same `reason` for this project since
   the last `CARVER_SESSION_ROTATED`/`STARTED` (a rotation/new-generation clears the episode). If one
   exists, suppress (emit nothing). Add a small helper; keep it pure/log-derived (no new durable
   marker event needed if a log scan suffices — prefer that).

5. **AF3 (#5) — enablement-guard startup WARN.**
   At daemon startup (or the earliest per-project config resolution), for every registered project
   whose `cfg.carve.session == "project-persistent"`, emit ONE loud structured `log.warning`
   ("carver.enablement.premature") listing the still-open PRE-ENABLEMENT CHECKLIST items — at minimum
   item 1 (ack cursor `CARVER_CONTEXT_CONSUMED`, owned by P4) and item 2's Compact-half status. This
   is a WARN, NOT a reject (P4 has not landed, so project-persistent is still incomplete). Feature-off
   projects (`session=="fresh"`) log nothing.

6. **config — `compaction_strategy`.** Add `compaction_strategy: str = "rotate"` to `CarveStageConfig`
   (values: `"rotate"` = the P3d fallback; any other string = "a driver name we do not yet support",
   handled by Work item 1's NEEDS_OPERATOR path). Keep it inert config, like the sibling knobs.

**DEFERRED to P4 (state this in your LOG; do NOT implement):** AD3 (structural-invalid envelope →
bounded repair-proposal turn) and concern-2/#1 (the `CARVER_CONTEXT_CONSUMED` ack cursor) both
require `reconcile.py` planner changes (emitting `ResumeCarverSession(mode="repair-proposal")` and the
§3.2 consumption cursor) — out of scope for this daemon.py-only package.

## Oracles (each: observable + negative + gate). Add these as tests in the test file.

- **O1 (AF1 rotate-fallback):** with a WARM session at generation N and a `CompactCarverSession`
  action, `_execute_compact_carver_session` returns exactly one `CARVER_SESSION_ROTATED` with
  `new_generation == N+1` and no wrapper launch (`patch_launch == []`); `_snapshot(...).status is
  ROTATING`. **Negative:** with `compaction_strategy` set to an unsupported driver name, it emits
  `NEEDS_OPERATOR{carver-compaction-no-driver}` and does NOT rotate.
- **O2 (routing):** `_execute` dispatch of a `CompactCarverSession` no longer raises
  `ValueError`/TICK_ERROR (a regression test that the action type is handled).
- **O3 (concern-3 fall-through closed):** feature-on, session DEGRADED with
  `resume_failures == max_resume_failures`, a `CarveDispatch` (headroom AND a re-scope variant with
  `task_id=`) → emits `CARVER_SESSION_ROTATED{reason:"degraded-recovery-exhausted"}`, creates NO
  `carve-<project>-<seq>` task, and emits NO `TASK_SUPERSEDED` for the re-scope origin (the origin
  stays `READY_TO_CARVE`). **Negative:** the SAME dispatch with `session=="fresh"` (feature off) runs
  the legacy fresh carve UNCHANGED (byte-identical — task minted, origin superseded as today).
- **O4 (ack validation):** a START turn that is DONE + captured session but writes NO / malformed /
  spine-mismatched `BOOTSTRAP-ACK.json` → `CARVER_SESSION_DEGRADED{reason:"bootstrap-ack-invalid"}`,
  status DEGRADED, NOT STARTED/WARM. **Positive:** a START turn with a valid, spine-matching ACK →
  `CARVER_SESSION_STARTED`, WARM. Same pair for `mode=="recover"` (RESUMED vs DEGRADED). **Negative:**
  a `mode=="merge-feed"` turn requires NO ack and is byte-identical to P3a.
- **O5 (debounce):** two consecutive passes with an unresolvable pinned route emit
  `NEEDS_OPERATOR{carver-no-route}` on the FIRST pass only; a `CARVER_SESSION_ROTATED`/`STARTED`
  between them re-arms it (a later unresolved pass may escalate again).
- **O6 (enablement WARN):** loading a project with `session=="project-persistent"` logs exactly one
  `carver.enablement.premature` WARN naming the open items; a `session=="fresh"` project logs none.
- **O7 (byte-identical-off, the master invariant):** the full existing suite stays green, and any
  test exercising the legacy carve/carve-dispatch/consume paths with `session=="fresh"` is unchanged.

## Gate (the REAL gate — run it yourself, synchronously, do not park)

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c \
 'cd /workspaces/vbpub/.worktrees/feat/f018-p3d-rotation-recovery/nyxloom && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -n auto -q \
    --cov=src/nyxloom --cov-report=json:/tmp/nyxloom-cov.json && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate \
    --base main --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom'
```
Two conditions BOTH required: pytest exits 0, AND `coverage_gate` prints `diff-coverage OK` (100% of
your changed `src/nyxloom` lines covered). `coverage_gate` diffs merge-base(main,HEAD), so **commit
before gating** or it reads 0/0 (a vacuous pass). Read the actual verdict line yourself.

## Scope / forbid

- **Touch:** `src/nyxloom/daemon.py`, `src/nyxloom/config.py`, `tests/test_carver_session_executor.py`.
  If (and only if) emitting `CARVER_SESSION_ROTATED`/`CARVER_COMPACTION_REQUESTED` trips
  `event.schema.json` payload validation or `tests/test_invariants.py` KNOWN_IGNORED_EVENT_TYPES,
  you MAY make the minimal additive schema/invariants edit to permit the payload you emit — say so in
  the LOG and keep it additive (no removals, no renames).
- **FORBID (frozen-core / already-complete):** `reconcile.py`, `carver_session.py`, `storage.py`,
  `types.py` (all events you need already exist). Needing any of these ⇒ your approach is wrong ⇒ BLOCK.

## BLOCKED rule (mechanical — a clean signal, not a failure)

If a named contract cannot be met as specified, or an oracle requires a forbidden file, STOP: write
`BLOCKED: <one-line reason + the exact file/oracle>` to `docs/handoff/f018-p3d-LOG.md`, commit only
that LOG (zero code), and exit. Do NOT improvise a hollow test or reach into a forbidden file.
A product/ambiguity call (e.g. "should rotate reason X escalate instead?") is a `D-<NNN>` decision in
`docs/plan-next-batches.md`, not a BLOCKED.

## Definition of done

All of O1–O7 pass; the gate is GREEN (pytest 0 + `diff-coverage OK` 100%); byte-identical-off holds;
AD3 + concern-2/#1 explicitly deferred to P4 in the LOG; branch committed with a clear message.
