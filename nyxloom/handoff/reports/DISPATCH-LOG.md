# Wave 1 dispatch log — 2026-07-15

Carve commit: `59b495d`. All 11 packages dispatched in parallel (user waived
the 5-agent cap for this wave). Implementer tiers: haiku (bounded packages),
sonnet (P09 daemon, P11 adversarial tests). Controller = the frontier session
itself this wave (carver/reviewer/merger); agents are harness-tracked with
resume handles recorded session-side.

| pkg | scope | tier | state |
| --- | --- | --- | --- |
| P01 | frontmatter + lint + golden corpus | haiku | MERGED |
| P02 | reconcile planner | haiku | MERGED |
| P03 | route adapters | haiku | MERGED |
| P04 | attempt wrapper | haiku | MERGED |
| P05 | dashboard renderer | haiku | MERGED |
| P06 | notifications | haiku | MERGED |
| P07 | decisions inbox | haiku | MERGED |
| P08 | doctor | haiku | MERGED |
| P09 | nyxloomd daemon + HTTP/SSE | sonnet | MERGED |
| P10 | operator CLI | haiku | MERGED |
| P11 | property tests + crash drills | sonnet | MERGED |

Review gate: frontier review of every diff against its handoff oracles +
full-suite run from the repo, then batch commit. BLOCKED receipts route to
re-carve or tier escalation per v2 §7.

## Wave closed — 2026-07-15

All 11 packages merged after frontier review. Full suite: 288 tests green.
Review interventions: P04 rejected once (hollow signal drills; redispatch
exposed + fixed a real wrapper signal-window bug); P08 depth-capped diff
report accepted with note; P01 fixture naming resolved a carve defect (L1
vs bad-* names). Post-merge integration (frontier review-fix, this commit):
the E2E smoke found the stuck-ACTIVE gap — wrapper emits its own
ATTEMPT_EXITED, nobody transitioned the task (reconcile contract defect) —
fixed via monotonic attempt-upsert guard (storage), broadened receipt
collection (reconcile), idempotent exit healing + terminal-attempt receipt
scan (daemon), regression-anchored in tests/test_integration.py. Live CLI
smoke: register -> lint -> tick -> dispatch -> collect -> AWAITING_REVIEW.
