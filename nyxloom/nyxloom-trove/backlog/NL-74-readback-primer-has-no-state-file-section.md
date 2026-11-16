---
kind: backlog-entry
schema_version: 1
id: NL-74
title: "the readback primer carries no living state file, so 'what is left to do' has no home in it"
status: fixed
type: "feature"
severity: "high"
component: "session-extract"
provenance: "operator decision 2026-11-16 (pi-extract-pi package): the successor primer is original goal anchor + the living state file (forward-oriented: done, decisions and why, next steps, open questions, exact commands) + recent prose + delta"
filed_by: "Codex deepseek-v4.1-flash"
filed_date: "2026-11-16"
closed_date: "2026-11-16"
closed_reason: "Delivered on pi-extract-pi-20261116: `--state-file PATH` on the readback layout. layout.py renders the file VERBATIM as the `STATE (forward-oriented, written by the agent)` section between the goal anchor and the recent prose, bounded by --budget-tokens, truncated only at a paragraph boundary with a marker naming the omitted size and the file to read, redacted by --redact-pattern, takes priority over the delta while the tail keeps a reserve. Footer accounting reports state=none|file and state_tokens. Evidence: tests/test_session_extract_layout.py's STATE section tests (order, opt-in absence, budget/paragraph-boundary truncation, priority over the delta, redaction, empty file refusal, CLI contract)."
---

## Problem

The readback layout (NL-71) carries three parts: the goal anchor, the newest
user/assistant prose, and the delta since the previous read-back. For a spawned
or targeted session, that answers "what is the work for" and "what happened
most recently", but never "what is LEFT to do" -- which is the part the
predecessor actually knows and the part a successor must act on. Controllers
worked around it by hand-writing a state summary into the brief, so the
successor read a second-hand paraphrase instead of the agent's own forward
notes.

Agents that keep a living state file (done, decisions and why, next steps,
open questions, exact commands) had no way to hand it to the primer.

## Wanted

A `--state-file PATH` readback option that embeds the file VERBATIM as a
clearly labelled section, placed AFTER the goal anchor and BEFORE the recent
prose: orders first, then the history that explains them. It must be bounded
by the same `--budget-tokens`, truncated only at a paragraph boundary with a
marker naming the omitted size and the file still to read, and redacted by
`--redact-pattern` like every other kept paragraph -- and it must serve every
harness, not only pi, because the file belongs to the agent, not to a CLI's
storage format.
