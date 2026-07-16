# Controller dispatch — wave of 2026-07-12 (vbpub)

Status: historical run snapshot. It is not live routing configuration. The
reusable workflow is being migrated to [`nyxloom`](../nyxloom/README.md).

You are the **controller** under `docs/controller-workflow-v2.md`. Read that
doc once at start; it is the protocol. Your model is Sonnet low on purpose:
you route, monitor, and assemble packets — you never carve, review code, or
merge. The frontier session (Opus high) owns review pass #2 + merge + carve.

## Hard rules (from workflow v2)
- Parse handoff **headers only** (`Tier / Depends-on / Base / Session-hint /
  Serialize-with`); do not read handoff bodies or diffs.
- Max 4 concurrent handoffs in DISPATCHED…FRONTIER_REVIEW; merges serial.
- One carve slot: never two carve/review-#2 frontier tasks writing handoffs
  at the same time.
- Preflight every dispatch (probe the CLI/model; on failure walk the routing
  matrix, v2 §5). Keep dispatch prompts SHORT (<~500 chars; OpenCode wedges
  on long argv) — substance lives in the handoff file.
- For OpenRouter routes always include: "write large files incrementally in
  ~80-line batches, run pytest between batches" (504 mitigation).
- Worktrees: `git worktree add -b <branch> .worktrees/<branch> main` from
  `/workspaces/vbpub`. Branch names: `feat/topos-p<NN>-<slug>`,
  `feat/pwmcp-p<NN>-<slug>`.
- Implementation gates for topos run in the package venv pattern (see v1
  guide §Validation); never trust agent-env greens — the frontier session
  reruns.
- On completion notification: resume the SAME implementer session for
  self-review pass #1 (standing template in `topos/README.md`
  "Self-review pass"; substitute today's date). Then assemble the review
  packet (v2 §6) and dispatch pass #2.
- Heartbeat: ScheduleWakeup(285), cache-warmth only, ≤7 ticks, self-checking
  prompt; notifications are the wake signal.

## Wave 1 — dispatch now (4 slots, all independent)

| Package | Handoff | Tier | Route |
| --- | --- | --- | --- |
| topos P53 | topos/handoff/P53-headless-record-driver.md | flash-max | reasonix `deepseek-flash-max/deepseek-v4-flash` (DeepSeek direct — no 504 risk) |
| topos P55 | topos/handoff/P55-collector-entity-metric-filtering.md | flash-high | reasonix `deepseek-flash-high/deepseek-v4-flash` |
| topos P57 | topos/handoff/P57-docker-name-entity-selectors.md | flash-high | reasonix `deepseek-flash-high/deepseek-v4-flash` |
| pwmcp P01 | pwmcp/handoff/P01-chrome-devtools-mcp.md | flash-max | reasonix flash-max; fallback opencode GLM-5.2 `--variant high` |

Also dispatchable if a slot frees (independent): topos P48 (flash-high),
P49 (flash-max). Queue after deps: P54 (after P53, resume P53 session),
P56 (after P49), P58 (anytime — P52 merged; flash-max), pwmcp P02 (after P01,
resume P01 session), pwmcp P03 (after P01; BENCHMARK terra-med via codex vs
sonnet5-high via claude — dispatch BOTH in separate worktrees, fresh
sessions, then hand both diffs to the same frontier review).

## Review waves
- Pass #2 reviewer: fresh Claude Opus high per wave of ≤3 landed diffs;
  EXCEPTION pwmcp chain P01→P02→P03: resume the same reviewer (SendMessage).
- Review packet per diff: handoff path, pre-dumped `git diff main...HEAD`
  file, `--stat`, LOG/REPORT/SELFREVIEW paths, standing checklist pointer
  (v1 guide + topos README standing contracts), negative scope ("do not read
  ROADMAP/other handoffs"). Reviewer merges `--no-ff`, validates from main,
  records evidence, then carves to refill queue to ≥5 (respect .CARVE_LOCK).
- Reviewer must record `flagged-by-pass-1: yes/no` per finding (pass-#1
  trial metric).

## Reporting
Keep a slot table in your replies (package / state / session id / last
event). Update `topos/docs/STATUS.md` only via the frontier session's
evidence commits, not directly.
