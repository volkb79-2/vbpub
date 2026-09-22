---
kind: backlog-entry
schema_version: 1
id: NL-22
title: "session_extract: surface a Bash/Task tool_use's own description instead of an anonymous gap count"
status: open
type: "feature"
severity: "low"
provenance: "dstdns controller session, 2026-09-22, operator brainstorm; see design-context-lifecycle-experiments.md E-019"
filed_date: "2026-09-22"
---

## Observed mechanism

`session_extract`'s `EventKind` docstring (`session_extract/events.py`)
states plainly: "Adapters never emit a kind for tool_use/tool_result content
that isn't one of the above ... that's the noise this whole tool exists to
discard." Every dropped run of tool activity collapses into a bare
`"[gap: N records omitted]"` count (`session_extract/render.py`), with no
attempt to characterize what happened.

That's correct for most tool types, but not all: Claude Code's own Bash-tool
schema REQUIRES a natural-language `description` param on every call
("Clear, concise description of what this command does in active voice"),
and the Task/Agent tool likewise requires a short `description`. Verified
against a real session's subagent transcripts: 100% of sampled Bash tool_use
blocks carried one (e.g. "Verify test-runner lane collects tests/config",
"Confirm O2 coverage and size delta") -- a genuinely legible intent string
sitting in the raw JSONL today, currently discarded along with everything
else in a gap.

A working precedent for "annotate intent alongside the action, mechanically
legible" already exists in this project: dstdns's own `.vscode/copilot-cmd.sh`
wrapper sources `COPILOT_PLAN`/`COPILOT_EXEC` and echoes both to the same
stream before running the command -- not in Claude Code's own Bash-tool code
path today, but proof the pattern is already trusted here for another agent.

## Proposed contract

Extend `session_extract` selection to optionally KEEP a Bash/Task tool_use's
own `description` as a new lightweight `EventKind` (e.g. `TOOL_INTENT`)
instead of folding it into the anonymous gap count -- one short string per
call, not the full tool_use/tool_result payload. A rendered gap could then
read "[gap: 20 records omitted -- intents: Verify test-runner lane collects
tests/config; Confirm O2 coverage and size delta; ...]" instead of a bare
count. Read/Edit/Write/Grep/Glob have no free-text intent field today and
would still collapse to a bare count.

## Behavioral oracles this would need

- Render one of this project's own real long Bash-heavy gap segments both
  ways (bare count vs. intents-attached) and compare legibility -- cheap,
  concrete oracle named in E-019.
- A controlled wrong implementation: including the full tool_use `input`
  (e.g. the whole shell command, not just `description`) instead of the
  short intent string -- must be rejected as reintroducing the bloat the
  gap-collapse mechanism exists to avoid (see render.py's own 2026-09-10
  operator-feedback history on per-occurrence explanation bloat).

## Spec section that owns this behavior

None yet.

## Full writeup

`vbpub/nyxloom/docs/design-context-lifecycle-experiments.md` E-019. Smaller
and lower-risk than NL-21/E-018 -- does not depend on the pack-orientation
pause, could proceed independently.
