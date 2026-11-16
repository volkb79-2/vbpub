---
kind: backlog-entry
schema_version: 1
id: NL-73
title: "session-extract cannot read pi sessions, so a pi run that must be continued by a fresh session cannot be primed from the readback/successor presets"
status: fixed
type: "feature"
severity: "high"
component: "session-extract"
provenance: "operator decision 2026-11-16 (pi-extract-pi package): add a pi adapter AND embed the living state file as the readback primer's 'what is left' section; the overwatch wiring that launches successors from it is a separate package"
filed_by: "Codex deepseek-v4.1-flash"
filed_date: "2026-11-16"
closed_date: "2026-11-16"
closed_reason: "Delivered on pi-extract-pi-20261116: session_extract/adapters/pi.py reads BOTH pi shapes (the stored session file and a `pi --mode json` run stream) into the shared event model; `--format pi` is registered and auto-detected from pi's `session` header; locate.py resolves a bare pi session uuid under $PI_CODING_AGENT_SESSION_DIR, $PI_CODING_AGENT_DIR/sessions or ~/.pi/agent/sessions; stopstate.py translates pi's own stopReason vocabulary (length = CONTEXT-FULL, error/aborted/deferred/pending, stop with no content = EMPTY-COMPLETION, toolUse end = TOOL-USE, unresolved pair = killed mid-call). Evidence: tests/test_session_extract_pi.py (synthetic fixtures only: normal finish, empty completion, thinking-only, length, tool error, resumed two-cycle file, non-UTF-8, truncated last line, compaction, launcher, since/until) and tests/test_session_extract_locate.py's pi section."
---

## Problem

`nyxloom-harness extract --format` read claude-code, codex, codex-exec,
opencode and reasonix sessions, but nothing read pi
(`@earendil-works/pi-coding-agent`). A pi run that had to be continued by a
fresh session -- context full, stalled, killed -- therefore could not be
primed from the readback or successor presets, and the controller fell back to
a hand-written brief written from its own memory of the run.

pi writes TWO shapes with the same conversation model, and both matter:

- the durable stored session `~/.pi/agent/sessions/<encoded-cwd>/
  <ISO-time>_<uuid>.jsonl`, where a `resume` appends further agent cycles to
  the SAME file (so one file can hold several agent cycles);
- the run stream `pi --mode json` produces (`agent_start`, `turn_start`,
  `message_start`/`message_update`/`message_end`,
  `tool_execution_start`/`..._update`/`..._end`, `turn_end`,
  `compaction_start`/`compaction_end`, `agent_end`, `agent_settled`), which is
  what a controller keeps per non-interactive run.

## Wanted

1. One `pi` adapter: both shapes -> the normalized events, with operator turns,
   assistant prose, opt-in thinking, tool calls with their arguments, truncated
   failed tool results, compaction boundaries, API-error records, and
   timestamps where the record carries one.
2. `--format pi` plus auto-detection, and session discovery by id.
3. Stop state: how the session ENDED, in pi's own vocabulary, surfaced so a
   successor brief can say it (empty completion, context-full, tool-use, error).

## Notes

`message_start`/`message_update` must be skipped: `message_end` carries the
finished message, and one real 292k-line run stream was 99.2% cumulative
`message_update` partial snapshots. `tool_execution_*` is read only by the stop
state, where the call/result pair is the in-flight evidence. pi's lowercase tool
names (`bash`/`read`/`edit`/...) are mapped onto the shared capitalized
vocabulary so `--edit-calls`/`--read-calls` compression and the ledger typology
keep working.
