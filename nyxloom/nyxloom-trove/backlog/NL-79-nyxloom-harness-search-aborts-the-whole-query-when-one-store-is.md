---
kind: backlog-entry
schema_version: 1
id: NL-79
title: "nyxloom-harness search aborts the whole query when one store is unusable, discarding finished Codex/Claude hits"
status: open
type: "bugfix"
severity: "high"
provenance: "vbpub session a8193e5d, 2026-11-17: finding the debian-rollback session"
filed_date: "2026-11-17"
---

## Observed mechanism and reproduction

On 2026-11-17 `nyxloom-harness search debian-rollback` (no `--client`) ran for 99 s, printed nothing on stdout and exited 1. The only diagnostic was the last stderr line:

`error: could not search OpenCode store /home/vscode/.local/share/opencode/opencode.db: no such table: session`

The Codex (1,659 transcripts) and Claude (1,035 files) scans had already finished and found hits. The same query with `--client claude` returned 1,196 rows in 12 s. One unusable store therefore turns a successful search into a total failure, and the failure only becomes visible after the slow scans finish. When stderr is redirected and the exit code is not read, as in a background run, the result looks like "no matches".

## Why Nyxloom owns it

Store discovery and per-client search are in `harness_search.py`. Only the search layer knows which stores exist and are readable, so it must decide whether an unreadable store is fatal.

## Proposed change

- Probe every selected store first (open the db, check the `session` table, check directories are readable). The probe is cheap and runs before any long scan.
- Isolate failures per client: a broken or uninitialised store becomes a one-line warning (`skipped opencode: <reason>`), and the other clients' hits are printed.
- Exit codes: 0 for a complete search, a distinct non-zero code for a partial search (some store skipped), and a failure code only when nothing could be searched.
- Default the client set to stores that exist and are valid. An explicit `--client opencode` against a broken store stays a hard error.
- Tests: an empty `opencode.db` with no `session` table plus valid Claude and Codex fixtures. Assert the hits are printed, the skip warning appears and the exit code is the partial one.
