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

Before the 2026-09-25 session-extraction surface work,
`session_extract`'s `EventKind` docstring (`session_extract/events.py`)
stated plainly: "Adapters never emit a kind for tool_use/tool_result content
that isn't one of the above ... that's the noise this whole tool exists to
discard." Tool calls were then represented only by dropped-record gap counts.

Current behavior has a separate, opt-in path: `--show-tool-calls` emits a
short tool-name label for Claude Code and Codex, while
`--show-tool-call-intent` adds a source-provided `description` or `intent`
field when available. Raw tool inputs and results remain hidden. These labels
are standalone kept events, so they reduce the anonymous gap size; they are
not aggregated into the gap marker itself.

That's correct for most tool types, but not all: Claude Code's own Bash-tool
schema carries a natural-language `description` param ("Clear, concise
description of what this command does in active voice"), and the Task/Agent
tool likewise carries a short `description`. **Not universally filled**:
a full-corpus check (152 transcripts, this session's own top-level JSONL plus
all 151 subagent transcripts, 16,738 total Bash tool_use blocks) found 70.6%
(11,816) carry a non-empty `description`; 29.4% (4,922) have the field
missing entirely (never present-but-blank -- all-or-nothing per call; no
verified cause for the omitted fraction). Where filled, it's a genuinely
legible intent string (e.g. "Verify test-runner lane collects tests/config",
"Confirm O2 coverage and size delta") sitting in the raw JSONL today,
discarded along with everything else in a gap for ~7 in 10 calls.

A working precedent for "annotate intent alongside the action, mechanically
legible" already exists in this project: dstdns's own `.vscode/copilot-cmd.sh`
wrapper sources `COPILOT_PLAN`/`COPILOT_EXEC` and echoes both to the same
stream before running the command -- not in Claude Code's own Bash-tool code
path today, but proof the pattern is already trusted here for another agent.

## Proposed contract

Remaining design question: should the standalone opt-in tool labels stay the
only representation, or should intent strings be summarized alongside a gap
without becoming standalone transcript events? If gap aggregation proceeds,
keep it opt-in and bounded: one short source `description` per Bash/Task call,
never the full `tool_use`/`tool_result` payload. A rendered gap could read
`[gap: 20 records omitted -- intents: Verify test-runner lane collects
tests/config; Confirm O2 coverage and size delta; ...]`. Read/Edit/Write/Grep/
Glob have no free-text intent field today and would still contribute only to
the gap count.

## Behavioral oracles this would need

- Render one of this project's own real long Bash-heavy gap segments both
  ways (bare count vs. intents-attached) and compare legibility -- cheap,
  concrete oracle named in E-019.
- Compare the existing standalone `--show-tool-calls --show-tool-call-intent`
  view with any proposed gap-attached form; do not ship both by default or
  silently duplicate an intent.
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
