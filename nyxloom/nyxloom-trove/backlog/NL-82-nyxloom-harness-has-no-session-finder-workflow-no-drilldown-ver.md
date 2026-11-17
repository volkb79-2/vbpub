---
kind: backlog-entry
schema_version: 1
id: NL-82
title: "nyxloom-harness has no session-finder workflow: no drilldown verb, intent presets, or index behind search"
status: open
type: "feature"
severity: "high"
provenance: "vbpub session a8193e5d, 2026-11-17, operator: 'still opened the JSONL ... means our tool is unusable'"
filed_date: "2026-11-17"
---

## Observed mechanism and reproduction

Finding one session to continue took a manual JSONL read even after `search` returned it (2026-11-17, vbpub session a8193e5d). Result rows carry only ids, dates and paths, so the user cannot judge a hit, and there is no way from a result to "more about this session" short of writing a parser. The session metadata needed is already in the transcripts that `search` scans in the same pass: `custom-title` and `ai-title` records, first operator prompt, cwd, record counts per role, compaction markers, last assistant message.

This entry owns what NL-50 (card contents), NL-48 (grouping) and NL-49 (rescans) do not: a drilldown path from a result to detail, intent-specific search, and the architecture that makes both cheap. NL-50 is where the card columns are specified; NL-81 is ranking.

## Why Nyxloom owns it

Search, extract and the per-client adapters already live in the harness layer. The search verb is the entry point to extract; the missing step between them is nyxloom's, not the consumer's.

## Proposed contract

**1. Result handles and a `show` verb (drilldown without a TUI).** `search` numbers its rows (`#1`...) and stores the last result set. `nyxloom-harness show #1` (or a session id) prints a session sheet: title, project and cwd, date range and duration, turn counts per role, compaction points, subagent family with intents, the operator prompts as a timestamped outline (first line each), files touched and external effects (the extract ledger), the last assistant message, and the exact resume and extract commands. `show --matches` lists matching records with surrounding context. Plain text and `--json`, so agents and humans use the same path.

**2. Optional TUI on top of 1.** `nyxloom-harness browse [QUERY]`: list pane with the card, detail pane with the `show` sheet, keys to open `extract`, copy the resume command, and toggle subagent families. It is a view over the same data as `show`; it adds no capability, so it follows only after 1 and is not required for the agent path.

**3. Intent presets** (like the extract presets), each fixing role weights, filters and card shape:

- `--as resume`: find a session to continue (operator-prompt and title weighted; card shows resume line, last assistant message, open work).
- `--as decision`: operator messages only; "what did I tell it".
- `--as incident`: tool errors, failure and stop states; sessions whose last record is not a completed reply.
- `--as touched --path P`: sessions that edited or ran commands against a path, repo, worktree, host or commit, from the ledger.
- `--as usage`: ranks by tokens and cost (NL-70).
- Time scoping on all of them: `--on DATE`, `--since`, `--until` (NL-46).

**4. Persistent index.** SQLite FTS5 at `~/.cache/nyxloom/harness-index.db`: one row per record (session, role, timestamp, text) plus a session table (title, first prompt, cwd, counts, family parent). Incremental update keyed by path, size and mtime, and by appended payload (the `extract --follow` machinery already tracks it). FTS5 `bm25()` with column weights gives the field-weighted ranking of NL-81 for free, snippets come from `snippet()`, and queries drop from 12 to 99 s to well under a second (NL-49).

**5. Cross-client resume line.** Each row and sheet prints the right command per client (`claude --resume <id>` from the project cwd; the codex equivalent) and the parent id for subagent rows.

## Sequencing

P0: NL-79 store isolation, NL-80 self-hit, NL-81 without the field weights. P1: card columns from NL-50 and the resume line. P2: result handles plus `show`. P3: FTS5 index, intents, family grouping. P4: TUI.

## Behavioural oracles

- Starting from a cold shell, `search debian rollback` then `show #1` must identify the right session and print its resume command without opening a transcript; add this as an acceptance test against a fixture store.
- `show` on a subagent row resolves to its parent family.
- Controlled wrong implementation: results with no title or first prompt must fail the fixture test.
- Index consistency: after appending a record to a transcript, the next query finds it; after rewriting the file, stale rows are dropped.

## Updates

**2026-11-17** — 2026-11-17: execution plan for P0 (NL-79/80/81-minimum) and P1 (card) is docs/PLAN-2026-11-HARNESS-SESSION-FINDER.md: intent, evidence, code map, per-package contract, tests, budget, ETA.
