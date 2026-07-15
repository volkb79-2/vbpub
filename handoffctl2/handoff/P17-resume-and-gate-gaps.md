# P17 — stream-json session capture + merge-gate rejection path

> Tier: sonnet · Date: 2026-07-15 · Two gaps found live during the first
> production day (do NOT dispatch until P14/P15/P16 have merged — shared
> files). Read handoff/STANDING.md first.

## Gap 1 — resume handle not captured from stream-json (the important one)

P14 switched claude routes to `--output-format stream-json`, whose FIRST
log line carries `"session_id":"<uuid>"`. But `capture_session` still uses
the `newest-jsonl` heuristic (scan `~/.claude/projects/<slug>/`), which is
now both unnecessary and unreliable. Consequence observed live: interrupted
claude attempts had `session_handle=None`, so the P14 dead-end logic
correctly BLOCKED them (no resume path) — but the resume path SHOULD have
existed. Every daemon restart that interrupts a claude leg currently
strands it.

Fix: for claude routes, `capture_session` (or the wrapper, which already
tails the log) extracts `session_id` from the first stream-json line
(`json.loads(line).get("session_id")`) and records it on the attempt as
soon as it appears. This makes interruption cheap-resumable as designed
(the whole point of stream-json + resumability). Keep newest-jsonl as the
fallback for non-stream routes. Regression test: a stream-json fixture log
-> capture_session returns the embedded session_id; wrapper records it on
ATTEMPT_STARTED.

## Gap 2 — no merge-gate rejection transition

The task state machine has no `MERGE_READY -> REVIEW_REJECTED` edge, so a
merge authority (human or a future auto-gate) that rejects at the gate —
e.g. the controller's own post-review gate re-run fails, or a pre-contract
review's verdict was in its CONTENT while the process exited 0 (groop-P89,
2026-07-15) — cannot route the task back to rework without SUPERSEDE +
statefile reset (what was hand-done for P89). Add the transition
`MERGE_READY -> REVIEW_REJECTED` to types.TASK_TRANSITIONS (this touches
the frozen core — frontier-authored, hence carved not agent-guessed) and a
daemon path/CLI verb `reject <project> <task>` that uses it. Regression:
the transition is allowed; a rejected MERGE_READY task can re-enter QUEUED.

## Also fold in (small, same area)
- The `MERGE_RECORDED` payload should carry the REAL merge commit (today
  the controller hand-pads it to 40 chars); wire the merge step to record
  `git rev-parse HEAD` of the merge.

## Rules
STANDING.md applies; types.py edit is the ONLY frozen-core change and must
keep every existing transition test green. Do not commit. REPORT to
handoff/reports/P17-REPORT.md; receipt-only final.
