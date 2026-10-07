---
kind: backlog-entry
schema_version: 1
id: NL-31
title: "Extract tool-call/error/ledger/stop-state/successor-brief features are Claude-Code-only (Codex and OpenCode adapters)"
status: open
type: "feature"
severity: "medium"
component: "session_extract"
provenance: "nyxloom SUCCESSOR package 2026-10 (E-020)"
filed_by: "Claude Sonnet (SUCCESSOR implementer)"
filed_date: "2026-10-06"
---

**Observed mechanism.** The SUCCESSOR package (nyxloom-successor-2026-10) added `--tool-calls`, `--tool-errors`, `--stop-state`, `--successor-brief`, the whole-session ledger and the external-effects bucket to `nyxloom extract`. They are implemented for the Claude Code adapter only (`src/nyxloom/session_extract/adapters/claude_code.py`, `ledger.py`, `stopstate.py`, `successor.py`); the guards in `cli.py` `_extract_guard` refuse the options for other formats. Codex and OpenCode subagent/session transcripts get none of it.

**Why nyxloom owns it.** The extract is nyxloom's own mechanical-handoff surface (E-009, E-015, E-020); a successor from a stopped Codex or OpenCode agent has the same need (what was run, what failed, what was in flight).

**Proposed contract.** Per adapter, map the harness's tool-call and tool-result records onto the shared `toolresult.py` helpers (`tool_intent`, `summarize_call`, `is_failed`), emit STOP events for the harness's own interrupt records, and let `ledger.session_ledger()` read Bash-equivalent commands. Until an adapter does, the CLI keeps refusing the option with a message naming the format.

**Oracles.** One anonymised fixture per harness with a failed tool result, an interrupt, and an external-effect command; the extract shows the error line, the STOP marker and the ledger effect. A controlled wrong implementation (adapter that ignores `is_error`) must fail the error test.

**Spec owner.** `src/nyxloom/session_extract/README.md`, `docs/CLI-REFERENCE.md` option matrix.
