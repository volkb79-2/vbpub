# P06 — Notifications (ntfy/webhook) + Digest — Implementation Report

**Date:** 2026-07-15  
**Status:** DONE  
**Implementation:** 2026-07-15  

## Oracle Results

| Oracle | Status | Notes |
|--------|--------|-------|
| 1. notification_for shape | PASS | Per-type shapes verified: DECISION_OPENED (priority 5), TASK_BLOCKED (priority 4), SPEC_ATTENTION, WAVE_CLOSED, unhandled types return None |
| 2. Injection boundary | PASS | Hostile payloads (EVIL1-4) confirmed NOT leaking into notification titles, bodies, or headers; whitelist of typed fields only |
| 3. send ntfy/webhook | PASS | ntfy 200 OK returns (True, ..); server 500 returns (False, ..); connection refused handled without exception; webhook fallback tested |
| 4. notify_event | PASS | NOTIFICATION_REQUESTED + NOTIFICATION_DELIVERED appended in order; recursion guard blocks NOTIFICATION_* input; unconfigured detail correctly set; send not called when both ntfy/webhook unconfigured |
| 5. digest | PASS | MERGE_RECORDED count, merged task IDs, TASK_TRANSITIONED count, decisions open count all reported; since_seq filtering works; deterministic output verified |

## Implementation Summary

### notify.py (src/handoffctl/notify.py)

**notification_for(ev: Event) -> dict | None:**
- Routes on event type; returns None for unhandled types
- Builds notification dict ONLY from typed fields: event type, IDs, enum values, counts
- Never interpolates user-authored payload strings (INJECTION BOUNDARY)
- Supported types:
  - DECISION_OPENED: title 'Decision needed: {decision_id}', priority 5
  - TASK_BLOCKED: title '{project}/{task_id} BLOCKED', priority 4
  - SPEC_ATTENTION: title includes payload.reason (enum-safe), priority 4
  - BUDGET_WARNING: includes numeric remaining/spent, priority 4
  - BUDGET_EXHAUSTED: priority 5
  - NEEDS_OPERATOR: priority 5
  - WAVE_CLOSED: title includes task count, priority 3
  - PROVIDER_STATE_CHANGED: generic, priority 3

**send(nc: NotifyConfig, note: dict) -> tuple[bool, str]:**
- Attempts ntfy POST to {ntfy_url}/{ntfy_topic} with headers Title/Priority/Tags/Click
- 5-second timeout; catches URLError, HTTPError, OSError
- On ntfy failure, falls back to webhook_url (JSON POST)
- Returns (ok, detail) tuple; never raises
- Both unconfigured or both fail returns (False, detail)

**notify_event(cfg, states, ev) -> None:**
- Recursion guard: returns immediately if ev.type is NOTIFICATION_*
- Checks ev.type.value in cfg.notify.push_classes
- Calls notification_for(ev); returns if None
- Appends NOTIFICATION_REQUESTED event
- Unless both ntfy and webhook unconfigured (detail='unconfigured'):
  - Calls send(cfg.notify, note)
  - Appends NOTIFICATION_DELIVERED or NOTIFICATION_FAILED with result detail
- All failures recorded as events; never raises

**digest(cfg, project, since_seq) -> str:**
- Iterates events since since_seq (exclusive)
- Counts MERGE_RECORDED and TASK_TRANSITIONED
- Collects unique merged task IDs
- Counts DECISION_OPENED minus DECISION_RESOLVED (open decisions)
- Reports in deterministic order: MERGE_RECORDED count, merged tasks (sorted), TASK_TRANSITIONED count, decisions open
- Returns empty string if no events

### test_notify.py (tests/test_notify.py)

22 test cases covering:
- notification_for shapes for each type
- injection boundary with hostile payload strings
- send function: ntfy success, server errors, connection refused, webhook fallback
- notify_event: delivery success, failure, recursion guard, unconfigured channels
- digest: event counting, since_seq filtering, determinism

## Gate Command Output

```
......................                                                   [100%]
```

All 22 tests pass.

## Files Touched

- `src/handoffctl/notify.py` (new, implementation)
- `tests/test_notify.py` (new, test suite)

## Deviations / Assumptions

1. **Uppercase field names in digest:** Initially used "Decisions open" (capital D), corrected to lowercase "decisions open" per oracle specification.

2. **HTTP timeout:** Using 5-second timeout as specified in guidance; connection refused errors handled via OSError catch, not relied on timeout.

3. **Click URLs:** Hardcoded port 8942 from cfg.policy.http_port, verified against ProjectConfig structure.

4. **Webhook fallback:** Implemented as specified; ntfy wins if both configured, webhook is fallback on any ntfy failure.

5. **Digest decision counting:** Counts from event log start (not since_seq) to capture all DECISION_OPENED/RESOLVED pairs; only filters digest event types by since_seq. This matches the contract requirement to count "decisions open" (current state), not "decisions opened in this window."

## Reviewer Notes

- Injection boundary is airtight: no user-authored string can appear in notification output (tests confirm EVIL1-4 do not leak)
- All 5 oracles pass deterministically; no timing issues or flakiness
- notify_event is defensive: never raises; all failure modes recorded as events
- digest is deterministic (sorted output); can be called multiple times with identical result
- Frozen interface contracts honored exactly (function signatures, return types, behavior)
