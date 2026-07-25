# F018 AD3 — structural-invalid carve envelope → bounded repair-proposal turn

> **Manual controller dispatch (not nyxloomd).** Sonnet implementer, frozen-core
> (`reconcile.py` carver ladder). The controller (Opus) independently re-runs the
> REAL gate + a full adversarial review before merge. **SOLO gate.** Read
> `docs/plan-next-batches.md` §"P4b / AD3" and `docs/plan-long-running-carver.md`
> §4.1 first. Feature-DARK: everything below is inert unless
> `cfg.carve.session == "project-persistent"` (default `"fresh"`).

## The one idea

P3b already **counts** structurally-invalid carve proposals for the current
generation and, at `invalid >= cfg.carve.max_proposal_repairs`, escalates
`NEEDS_OPERATOR{carver-proposal-invalid}` (`daemon._carve_proposal_repair_escalations`,
debounced per generation). But the **repair turn itself was deferred** (P3b line
~2196: "no repair ResumeCarverSession mode exists in reconcile.py's ladder yet").
So today: 1 invalid proposal → nothing happens until the ceiling → escalate. The
warm session is never asked to *fix* its own broken output.

AD3 adds the missing turn: while the invalid count is **below** the ceiling,
plan a `ResumeCarverSession(mode="repair-proposal")` that hands the warm carver a
re-validation checklist so it re-emits a corrected proposal — **before** ingesting
any new merge feed (a broken premise is repaired first). The
`"repair-proposal"` string is **already** a recognized write-authority mode
(`daemon._CARVE_WRITE_AUTHORITY_MODES`), and the resume executor already routes
any non-`recover` mode against a WARM session — so the executor surface is one new
packet branch, not a new dispatch path.

## Invariants you MUST preserve

1. **Compose with P3b's escalation — never double-fire.** Repair fires iff
   `1 <= invalid_count < max_proposal_repairs`. At/above the ceiling, the repair
   signal is EMPTY (planner plans nothing) and P3b's escalation fires (unchanged).
   The two are mutually exclusive by construction (a single `<` vs `>=` boundary).
2. **A repair turn is NOT a feed.** It carries **no `source_ids`**, so it does not
   touch `last_consumed_event_sequence` — the P4a merge-feed shared cursor is
   completely unaffected (verify this in an oracle).
3. **Slot order in the WARM branch becomes admit(1) > repair(new) > merge-feed(3)
   > compaction(4).** The whole carver ladder stays gated by the existing
   hoisted `not carve_in_flight` guard (reconcile ~L1169), so a repair turn in
   flight never re-fires (same mechanism merge-feed relies on).
4. **`reconcile.plan_project` stays pure.** The invalid-proposal set is computed
   by the daemon into a new `ReconcileInput` field; the planner only reads it.
5. **No new EventType / TaskState / TASK_TRANSITIONS edge.** The repair turn reuses
   `CARVER_SESSION_RESUMED` + `CARVER_PROPOSAL_RECORDED` exactly like a carve turn.

## Design (resolved from scouting 2026-07-25)

### The `CarveRepairRequest` DTO — where it lives
Put it in **`carver_session.py`** beside `CarverFeed` / `ValidatedCarveProposal`
(both already pure `_Serde` dataclasses there). It is a pure DTO with **no fold
logic** — the frozen projector's `apply`/fold is untouched, so this honors the
frozen-core forbid (a new dataclass ≠ a projection change). Minimal shape:
```python
@dataclass
class CarveRepairRequest(_Serde):
    proposal_id: str        # the invalid CARVER_PROPOSAL_RECORDED's id
    generation: int         # pin to the current generation (defensive)
```
Do NOT add a precise `validation_error` string. `_validate_carve_proposal_payload`
returns `None` on any of ~15 dimensions **without reporting which**; threading a
reason would mean refactoring that frozen 15-branch function + a full per-branch
coverage matrix. Instead the repair PACKET names the validation **criteria** as a
checklist (below) — the warm carver self-diagnoses against them. (A precise-reason
variant is a clean P2 follow-up; note it, do not build it here.)

### 1. `carver_session.py` — the DTO (above). No other change to this file.

### 2. `reconcile.py` — field + ladder slot
- **ReconcileInput** (beside `validated_carve_proposals` / `pending_carver_feeds`,
  ~L775): `pending_carve_repairs: tuple[CarveRepairRequest, ...] = ()` — same
  "empty tuple = feature-off / nothing to repair" convention as its neighbours, so
  every pre-AD3 test plans byte-identically.
- **WARM ladder** (`elif status is CarverStatus.WARM:`, ~L1217): add the repair
  slot at the TOP of the WARM body, demoting the existing `if inp.pending_carver_feeds:`
  to `elif`:
  ```python
  elif status is CarverStatus.WARM:
      if inp.pending_carve_repairs:
          # Slot: repair a structurally-invalid proposal before ingesting new
          # feeds. NO source_ids -> not a feed -> P4a cursor untouched.
          carver_actions.append(ResumeCarverSession(
              project=inp.cfg.project_id, mode="repair-proposal",
              generation=snap.generation,
          ))
          carve_dispatch_planned = True
          trace.note("carver", None, "repair-proposal")
      elif inp.pending_carver_feeds:
          ... existing merge-feed, unchanged ...
      else:
          ... existing compaction, unchanged ...
  ```
  Note the action carries NO `source_ids` and NO per-proposal targeting — the
  daemon executor re-derives the invalid proposals for the packet (keeps the
  action minimal and the planner pure).

### 3. `daemon.py` `_build_input` (~L1033) — compute the field
Add `pending_carve_repairs = self._pending_carve_repairs(project, cfg, carver_session_snap)`
and pass it to `ReconcileInput`. New helper `_pending_carve_repairs`:
- Return `()` when `snap is None` (feature-off) — byte-identical parity.
- Reuse P3b's exact counting: iterate `CARVER_PROPOSAL_RECORDED`, filter to
  `snap.generation` via `_parse_proposal_id`, count those where
  `_validate_carve_proposal_payload(cfg, snap, payload) is None` (invalid), and
  collect their `proposal_id`s.
- Apply the ceiling gate: return `()` when `invalid == 0` OR
  `invalid >= cfg.carve.max_proposal_repairs` (so it composes with P3b's escalate,
  never double-fires); otherwise a `CarveRepairRequest` per invalid proposal
  (deterministically sorted by proposal_id).
- **Exclude already-repaired-into-validity**: an invalid proposal that a later
  turn superseded with a VALID one still counts as invalid in the raw log — that's
  fine and intended (the count is "how many failed attempts this generation", the
  same number P3b escalates on; a subsequent VALID proposal is admitted via slot 1
  which out-prioritizes repair, so repair only fires when there is no valid
  proposal to admit — the ladder ordering already guarantees this).

### 4. `daemon.py` `_build_carver_resume_prompt` — the repair packet
Add a `mode == "repair-proposal"` branch. The packet:
- States: "Your proposal(s) `<ids>` for generation `<g>` failed structural
  validation and were NOT admitted." (re-derive the invalid ids via the same
  counting helper, or accept them via a small internal arg).
- Names the re-validation CHECKLIST (the failure surface, from
  `_validate_carve_proposal_payload`): for every artifact — (1) `sha256` matches
  the file content, (2) frontmatter parses, (3) `input_revision == source.base_revision`,
  (4) `nyxloom lint` is clean, (5) every oracle is satisfiable within `scope.touch`
  (L13). Plus proposal-level: proposal_id/turn_id structure + generation match.
- Instructs: emit a corrected `CARVER_PROPOSAL_RECORDED` envelope. Do NOT merge.
- Keep it a WRITE-AUTHORITY turn (it re-writes the proposal artifacts) — already
  guaranteed by `_CARVE_WRITE_AUTHORITY_MODES` containing `"repair-proposal"`.

No change to `_execute_carve_via_session_resume` itself: `required_status = WARM`
for any non-`recover` mode already covers `"repair-proposal"`; the exit is consumed
by `_consume_carver_session_exit` exactly like a carve turn (write-authority →
CARVER_PROPOSAL_RECORDED), and the next pass re-validates the new proposal (admit
if valid, repair again if still invalid + below ceiling, escalate at ceiling).

## Oracles (observable + negative + gate)

- **O1 (repair below ceiling):** `max_proposal_repairs=2`, one invalid
  CARVER_PROPOSAL_RECORDED for the current generation, WARM session, no valid
  proposal, no pending feed → `_build_input` yields one `CarveRepairRequest`;
  `plan_project` plans exactly one `ResumeCarverSession(mode="repair-proposal",
  generation=g)` with **no source_ids**, and NO merge-feed/compaction that pass.
- **O2 (compose, no double-fire at ceiling):** `max_proposal_repairs=2`, TWO
  invalid proposals this generation → `pending_carve_repairs` is EMPTY (no repair
  planned) AND P3b's `_carve_proposal_repair_escalations` still emits exactly one
  `NEEDS_OPERATOR{carver-proposal-invalid}`. The two must not both fire.
- **O3 (admit out-prioritizes repair):** one invalid + one VALID proposal this
  generation → slot 1 plans `AdmitCarveProposal` for the valid one; NO repair turn
  (the valid proposal is admitted first; repair only matters when nothing is
  admittable).
- **O4 (repair before feed):** one invalid proposal AND a pending merge feed →
  the repair turn is planned, the merge-feed is NOT (this pass); assert the feed is
  still pending afterwards.
- **O5 (shared cursor untouched):** the repair `ResumeCarverSession` carries no
  `source_ids`; executing it does not advance `last_consumed_event_sequence`
  (P4a cursor byte-identical before/after).
- **O6 (byte-identical off):** `cfg.carve.session == "fresh"` (snap None) →
  `pending_carve_repairs == ()`, plan is byte-identical to pre-AD3; and with the
  persistent carver on but zero invalid proposals, likewise `()`.
- **O7 (packet):** executing the repair action writes a carver resume packet that
  names the invalid proposal id(s) and the 5-point re-validation checklist, and
  the turn is a write-authority (repair-proposal) resume of the warm session_id.

## Gate (run it yourself, synchronously; commit first)

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c \
 'cd /workspaces/vbpub/.worktrees/feat/f018-ad3-carve-repair/nyxloom && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -n 4 -q \
    --cov=src/nyxloom --cov-report=json:/tmp/nyxloom-cov.json && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate \
    --base main --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom'
```
DONE = pytest 0 AND `diff-coverage OK` (100% changed lines). Read the verdict line
yourself. Commit before gating. Watch for the same class of meta-invariant tests
P1b tripped: no new EventType here (safe), but if you add any `build_dispatch`
call site update `test_adapters` (you should NOT need to — this uses
`build_resume`), and there is no new Policy field (max_proposal_repairs already
exists), so `test_test_health_carve` should stay green.

## Scope / forbid

- **Touch:** `carver_session.py` (the `CarveRepairRequest` DTO ONLY — no fold
  change), `reconcile.py` (the field + the WARM repair slot), `daemon.py`
  (`_build_input` compute + `_pending_carve_repairs` helper + the
  `_build_carver_resume_prompt` repair branch), tests.
- **FORBID:** `types.py` `TASK_TRANSITIONS` / any new TaskState/EventType.
  `storage.py`. `_validate_carve_proposal_payload`'s body (do NOT refactor it to
  report reasons — that is the deferred P2). The P3b escalation
  (`_carve_proposal_repair_escalations`) — you COMPOSE with it, do not alter its
  ceiling or debounce. `_execute_carve_via_session_resume`'s dispatch body (the
  new mode flows through it unchanged — only `_build_carver_resume_prompt` gets a
  branch). Needing any of these ⇒ BLOCK.

## BLOCKED rule

If a named contract can't be met, or an oracle needs a forbidden file, write
`BLOCKED: <reason + file/oracle>` to `docs/handoff/f018-ad3-LOG.md`, commit only
that, exit. A product/ambiguity call is a `D-<NNN>` in the plan doc, not a BLOCKED.

## Definition of done

O1–O7 pass; gate GREEN (pytest 0 + diff-coverage 100%); the P3b escalation path
byte-identical; determinism preserved (planner pure); `TASK_TRANSITIONS` untouched;
byte-identical when the persistent carver is off; branch committed. A structurally
broken carve proposal is now *repaired by the warm session* before it burns the
escalation ceiling — the carve-side analog of F019's gate-failure diagnosis.
