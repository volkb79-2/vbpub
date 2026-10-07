---
kind: backlog-entry
schema_version: 1
id: NL-32
title: "E-020 open experiments: restart and main-session Ctrl-C resume, cache-miss repeat, Bash description fill rate"
status: open
type: "feature"
severity: "medium"
component: "session_extract"
provenance: "nyxloom SUCCESSOR package 2026-10 (E-020)"
filed_by: "Claude Sonnet (SUCCESSOR implementer)"
filed_date: "2026-10-06"
---

**Observed mechanism.** E-020 (docs/design-context-lifecycle-experiments.md) measured resumability of stopped Agent-tool subagents on Claude Code 2.1.290. Three questions stayed open and each changes what the `nyxloom-successor` skill should tell a controller:

1. **Host/Claude restart.** Probes P6 (`a6aa06aa949843fa8`, completed, nonce prepared) and P7 (`ad441305d9912cded`, running foreground sleeps) were prepared in the experiment session. The test is: operator quits Claude Code and restarts with `claude --resume <session>`; then ListAgents, SendMessage both asking for their nonce, and compare `cache_read` in their transcripts. Needs the operator; run only when no real agent is running.
2. **Ctrl-C in the MAIN controller session** (stops all subagents): not tested. Is the result "stopped by user" (refused) or "stopped by Claude"?
3. **Cache-miss repeat.** The two zero-tool completed agents (P1, P2) missed the prompt cache on resume (cw ~21.5k), the two agents with a tool call (P3b, P4) hit (cr 38k/40k). n=2 each, unexplained; repeat with n>=5 per shape.
4. **Bash description fill rate.** Re-count `tool_use.input.description` on Bash calls in the next real wave that carries the standing dispatch line; the experiment had one realistic pair (0/6 without, 6/6 with) plus 0/141 on real lanes without the line.

**Why nyxloom owns it.** The skill's step 0 ("when is a successor needed") rests on these facts.

**Oracles.** Each item ends in a row in the E-020 matrix with the evidence file path.

**Spec owner.** `docs/design-context-lifecycle-experiments.md` E-020.
