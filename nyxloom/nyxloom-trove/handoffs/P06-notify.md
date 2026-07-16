# P06 — notifications (ntfy/webhook) + digest

> Tier: haiku · Depends-on: none · Read first: handoff/STANDING.md,
> src/nyxloom/notify.py (docstring = normative, esp. the INJECTION
> BOUNDARY), docs/ARCHITECTURE.md §8, docs/SPEC.md §13.

## Owned files
- `src/nyxloom/notify.py`
- `tests/test_notify.py`

## Oracles
1. `notification_for` per-type shape: DECISION_OPENED (decision_id 'D-013')
   → title 'Decision needed: D-013', priority 5, click URL ending
   '/www/index.html' (project-scoped); TASK_BLOCKED for demo/t1 → title
   'demo/t1 BLOCKED', priority 4, click ending
   '/www/task/demo/t1.html'; SPEC_ATTENTION payload {'reason':'ratchet'} →
   title contains 'ratchet'; WAVE_CLOSED payload {'task_ids': ['a','b']} →
   body contains '2'; a type not handled (ARTIFACT_REGISTERED) → None.
2. **Injection boundary (the test that matters)**: craft events whose
   payloads carry hostile strings in every prose-bearing field —
   TASK_BLOCKED payload {'blocker': {'type':'contract','unblock_condition':
   'EVIL1','detail':'EVIL2'}, 'notes':'EVIL3'}, NEEDS_OPERATOR payload
   {'detail':'EVIL4'} — assert none of EVIL1..4 appears in title, body,
   or any header value of the produced note. Only ids/enums/counts allowed.
3. `send` ntfy: stdlib http.server on 127.0.0.1:0 in a thread capturing
   one POST; assert path '/{topic}', headers Title/Priority/Click, body ==
   note body; returns (True, ...). Server returning 500 → (False, ...).
   Connection refused (closed port) → (False, ...) and NO exception within
   1s. Webhook fallback: ntfy_url set to the closed port, webhook_url to
   the live capture server → webhook receives JSON of the note.
4. `notify_event`: with monkeypatched send→(True,'ok'): a TASK_BLOCKED
   event → events gain NOTIFICATION_REQUESTED then NOTIFICATION_DELIVERED
   (assert order and that both carry task_id); send→(False,'boom') →
   NOTIFICATION_FAILED with payload detail 'boom'; a NOTIFICATION_DELIVERED
   input event → NO new events (recursion guard); type not in push_classes
   → no events; ntfy+webhook both unconfigured → NOTIFICATION_FAILED with
   detail 'unconfigured' and send NOT called (monkeypatch send to raise if
   called).
5. `digest`: seed events (2×MERGE_RECORDED for t1/t2 with distinct
   sequences, 1×TASK_TRANSITIONED, 1×DECISION_OPENED left open) →
   digest(cfg,'demo', 0) mentions 'MERGE_RECORDED: 2', lists t1 and t2,
   'decisions open: 1'; digest(..., since_seq=<after first merge>) counts
   only 1. Two calls → identical string (determinism).

## Guidance
- urllib.request only; timeout=5; catch URLError/OSError/HTTPError.
- Click URLs: port from cfg.policy.http_port.
- notify_event appends via storage.append_and_apply with actor
  Actor(NOTIFIER, 'notify') and the ORIGINAL event's task_id/decision_id.
- Body templates: f-strings over ids and counts ONLY — the injection test
  will catch any payload leak; write the code so it cannot leak (whitelist
  the fields you read, never str(payload)).
