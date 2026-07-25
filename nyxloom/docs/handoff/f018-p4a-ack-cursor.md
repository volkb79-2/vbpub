# F018 P4a — merge-feed acknowledgement cursor (concern-2, the last pre-enablement blocker)

> **Manual controller dispatch (not nyxloomd).** deepseek implementer, run from `main` in a
> dedicated worktree. The controller (Opus) independently re-gates + adversarially reviews before
> merge. Everything lands DARK behind `cfg.carve.session == "project-persistent"` (default `"fresh"`).

## The one idea

Today the persistent carver's merge-feed consumption cursor (`last_consumed_event_sequence`) is
**read** correctly everywhere but **never advanced**, so a merge-feed turn consumes the same digests
and the planner re-emits the SAME merge-feed every pass forever (a budget-burning model-turn runaway —
PRE-ENABLEMENT CHECKLIST item 1). The fix is a single `daemon.py` executor emit: after a **successful
merge-feed turn**, emit `CARVER_CONTEXT_CONSUMED` carrying the highest consumed feed sequence. Every
other piece already exists:
- the projector fold (`carver_session.py:project_session`, `CARVER_CONTEXT_CONSUMED` →
  `last_consumed_event_sequence = max(...)`) — **already done, do NOT touch**;
- the input filter (`_pending_carver_feeds`, daemon.py — drops MERGE_RECORDED `<= cursor`) —
  **already done, do NOT touch**;
- the planner sort/consumption (`reconcile.py` slot 3) — **already done, do NOT touch**.

## CRITICAL invariant you must NOT break (why this is safe, and why you must not "fix" the cursor)

`last_consumed_event_sequence` is ALSO read by `_validated_carve_proposals` (daemon.py ~1877, it skips
`CARVER_PROPOSAL_RECORDED` events `<= cursor`). Advancing the cursor via merge-feed is **safe** and you
MUST NOT change that method or add a separate cursor. Reason: the planner's carver ladder
(`reconcile.py` ~1119) gives `AdmitCarveProposal` (slot 1) strict priority over merge-feed (slot 3) and
they share the single carve mutex — so on any pass with a pending validated proposal, the *admit* fires
and the merge-feed does NOT. A merge-feed only ever runs (and thus only ever advances the cursor) on a
pass with **no** pending proposal, so it can never advance past an un-admitted current-generation
proposal. Do not touch `_validated_carve_proposals`; do not add a second cursor field to
`carver_session.py`. If you believe the cursor is unsafe, STOP and BLOCK (you are missing the ladder
priority) — do not "repair" frozen-core code.

## Context to read first (exact seams)

1. `docs/plan-next-batches.md` — PRE-ENABLEMENT CHECKLIST **item 1** (this package closes it).
2. `docs/plan-long-running-carver.md` §2.2 (durable session projection, `last_consumed_event_sequence`)
   and §3.2 (event-order / at-least-once).
3. `src/nyxloom/carver_session.py:project_session` — the `CARVER_CONTEXT_CONSUMED` fold (READ-ONLY):
   it reads `p.get("highest_event_sequence", 0)`, `p.get("spine_revisions", {})`,
   `p.get("context_tokens")`, `p.get("context_ratio")`. Your emit payload keys must match these.
4. `src/nyxloom/daemon.py`:
   - `_pending_carver_feeds` (~1773, READ-ONLY) — how a MERGE_RECORDED becomes a CarverFeed: the
     `digest_id` is `ev.payload["carver_digest"]["digest_id"]` and the feed's sequence is `ev.sequence`.
     Your helper maps consumed `digest_id`s back to those sequences.
   - `_consume_carver_session_exit` (~4189) — the `else:` (successful-resume) branch that emits
     `CARVER_SESSION_RESUMED` (~4438). `kind`/`mode`/`source_ids` come from `_carver_turn_marker`.
     A merge-feed turn is `kind=="resume"`, `mode=="merge-feed"`, `source_ids` = the consumed digest_ids.
   - `_spine_revisions(cfg)` — reuse for the emit's `spine_revisions`.
5. `tests/test_daemon.py` (~5448) `test_pending_carver_feeds_orders_and_excludes_consumed_and_missing_digest`
   and the cursor-replay assertions — the payload shape (`highest_event_sequence`) and cursor semantics
   your emit must produce. Mirror these fixtures.

## Work (all in `daemon.py` + the test file)

1. **Helper `_highest_consumed_feed_sequence(project, source_ids) -> int | None`.** Scan
   `storage.iter_events(project)` for `MERGE_RECORDED` events whose
   `payload["carver_digest"]["digest_id"]` is in `source_ids`; return the MAX `ev.sequence` among them,
   or `None` if none match (nothing to acknowledge → no emit). Guard the `storage.iter_events` call in
   `try/except` returning `None`, mirroring the other carver helpers.
2. **Emit `CARVER_CONTEXT_CONSUMED`** in `_consume_carver_session_exit`, inside the successful-resume
   (`else`) branch, immediately AFTER the `CARVER_SESSION_RESUMED` append, gated on
   `mode == "merge-feed" and source_ids`. Payload:
   `{"highest_event_sequence": <helper result>, "spine_revisions": self._spine_revisions(cfg)}`.
   Only emit when the helper returns a non-None sequence. Do NOT emit for start/recover/
   targeted-intake/carve turns (byte-identical to today for those). Do NOT emit on a failed
   (not-`ok`) merge-feed turn — a failed feed must be re-delivered, so its cursor must NOT advance.
3. (No config, no schema, no reconcile.py, no carver_session.py, no storage.py changes.)

## Oracles (add as tests; observable + negative + gate)

- **O1 (headline — no re-fire):** WARM session; append 2 pending `MERGE_RECORDED`-with-`carver_digest`
  events (sequences S1<S2); plan → a merge-feed `ResumeCarverSession(source_ids=[d1,d2])`; run the turn
  to a DONE receipt + captured session; `_consume_carver_session_exit` emits `CARVER_CONTEXT_CONSUMED`
  with `highest_event_sequence == S2`; the projected snapshot's `last_consumed_event_sequence == S2`;
  and a subsequent `_pending_carver_feeds` returns `()` (the consumed feeds no longer re-fire).
- **O2 (partial/failed feed re-delivers):** a merge-feed turn that FAILS (no session_handle OR receipt
  not DONE) emits NO `CARVER_CONTEXT_CONSUMED`; the cursor stays; the feeds are still pending next pass.
- **O3 (only merge-feed):** a successful `mode=="recover"` / `"carve"` / start turn emits NO
  `CARVER_CONTEXT_CONSUMED` (byte-identical to today).
- **O4 (proposal-safety, the invariant):** interleave a `CARVER_PROPOSAL_RECORDED` at sequence S_p with
  S1 < S_p < S2; prove that a validated, un-admitted proposal is STILL returned by
  `_validated_carve_proposals` when the ladder would admit it first (i.e. demonstrate the admit-slot
  priority means the cursor never advances on a pending-proposal pass — a `run_pass`-level test that a
  pending proposal is admitted, and only later merge-feeds advance the cursor). If you cannot construct
  this without touching frozen-core, a `run_pass` E2E asserting "proposal admitted AND later feed
  consumed, in that order" suffices.
- **O5 (byte-identical-off):** with `session=="fresh"`, `_consume_carver_session_exit` for any resume
  turn emits no `CARVER_CONTEXT_CONSUMED` (the whole path is behind the `_carver_session` master gate /
  the merge-feed mode only exists feature-on); full existing suite green.

## Gate (run it yourself, synchronously; commit first)

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c \
 'cd /workspaces/vbpub/.worktrees/feat/f018-p4a-ack-cursor/nyxloom && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -n auto -q \
    --cov=src/nyxloom --cov-report=json:/tmp/nyxloom-cov.json && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate \
    --base main --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom'
```
DONE = pytest exits 0 AND `coverage_gate` prints `diff-coverage OK` (100% of changed `src/nyxloom`
lines). Commit before gating (coverage_gate diffs merge-base). Read the verdict line yourself.

## Scope / forbid

- **Touch:** `src/nyxloom/daemon.py`, `tests/test_daemon.py` (and/or
  `tests/test_carver_session_executor.py` — put the tests wherever the existing merge-feed/cursor
  fixtures live).
- **FORBID:** `reconcile.py`, `carver_session.py`, `storage.py`, `types.py`, `config.py`,
  `event.schema.json`, and — specifically — `_pending_carver_feeds` and `_validated_carve_proposals`
  (read them, do not edit them). Needing any of these ⇒ BLOCK.

## BLOCKED rule

If a named contract can't be met, or an oracle needs a forbidden file, write
`BLOCKED: <reason + file/oracle>` to `docs/handoff/f018-p4a-LOG.md`, commit only that, exit. A product/
ambiguity call is a `D-<NNN>` in `docs/plan-next-batches.md`, not a BLOCKED.

## Definition of done

O1–O5 pass; gate GREEN (pytest 0 + `diff-coverage OK` 100%); byte-identical-off; no frozen-core touched;
branch committed. This closes PRE-ENABLEMENT CHECKLIST item 1 — the last hard enablement blocker.
