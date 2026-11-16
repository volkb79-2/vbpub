---
kind: backlog-entry
schema_version: 1
id: NL-75
title: "pi has no --follow, extract-lossless, extract-report or extract-sessions support, so a live pi run and a pi usage ledger are still unreadable"
status: open
type: "feature"
severity: "medium"
component: "session-extract"
provenance: "pi-extract-pi package, 2026-11-16: the one-shot pi adapter landed; the incremental and derived surfaces were out of that package's scope"
filed_by: "Codex deepseek-v4.1-flash"
filed_date: "2026-11-16"
---

## Problem

`session_extract/adapters/pi.py` (NL-73) reads a pi session one-shot: `extract`
selects, classifies and renders it, and `--stop-state` reports how it ended.
Four neighbouring surfaces still do not know pi:

- `--follow`: `follow.py`'s `JsonlSource` dispatches on `claude-code`, `codex`
  and `reasonix` only, so `extract --format pi --follow` is not implemented
  (the CLI does not refuse it either -- worth checking in this item).
- `extract-lossless`: `lossless.py` has `dump_claude_code`/`dump_codex`/
  `dump_reasonix`/`dump_opencode` but no `dump_pi`, so the raw-prose mode
  cannot read a pi session.
- `extract-report`: `stats.py::build_call_rows` raises `NotImplementedError`
  for pi, although pi's assistant messages DO carry a per-call `usage`
  (`input`/`output`/`cacheRead`/`cacheWrite`/`reasoning`/`totalTokens` plus a
  `cost` block), so a pi usage/timeline ledger is genuinely derivable rather
  than a guess.
- `extract-sessions`: `sessions.list_agents`/documentation covers Claude
  Code, Codex and opencode; pi's one-file-per-session store has no
  parent/child lineage to report, but listing the sessions in a project
  directory would still answer the discovery question.

## Wanted

Add pi to each surface where the format genuinely establishes the answer
(follow, lossless, report -- its usage block is real), and state plainly in
`sessions.py` why the family/lineage answer does not exist for pi (one file
per session, no sub-agent records) instead of leaving it as an accidental
hole.

## Priority

Medium: the controller's pi workers are read one-shot and continued from a
readback, which is covered. A live pi stream and a pi cost report are the two
uses that wait.
