---
kind: backlog-entry
schema_version: 1
id: NL-48
title: "nyxloom-harness search results do not group subagents, show snippets, or hand off a day-scoped extract command"
status: open
type: "feature"
severity: "low"
component: "session-extract"
context_estimate: "medium"
provenance: "operator request, 2026-10-15: locating session 384f276e for the 05.10.2026 discussion"
filed_by: "Claude Haiku 5.5 (Claude Code session f8e59e42)"
spec_owner: "src/nyxloom/harness_search.py"
filed_date: "2026-10-15"
---

## Observed mechanism and reproduction

`nyxloom-harness search` prints matching session ids and metadata and "never transcript text", by design. Three consequences showed up on 2026-10-15:

- Subagent transcripts (`.../subagents/agent-*.jsonl`) are listed as separate rows, next to their parent session, with no grouping. A single discussion produced many rows.
- To confirm a hit I had to open the raw JSONL, because no snippet or timestamp was shown.
- To read the conversation I had to build the `extract` command myself, from the session id, and `extract` cannot be limited to the matched day. `--since`/`--until` take source markers, not calendar dates, so the matched window cannot be selected from a search result.

## Why Nyxloom owns it

Search results are the entry point to `extract`. The handoff from "found it" to "read it" belongs to the same tool, and the window of the match is known only to the search.

## Proposed contract

- Group subagent hits under their parent session by default, with a count. `--include-subagents` lists them as separate rows.
- `--snippet N` (opt-in, off by default to keep the current metadata-only contract) prints N characters of matched prose around each hit with its timestamp, after synthetic-record exclusion and with `--redact-pattern` applied.
- Every result prints a ready-to-run line, for example `nyxloom-harness extract <id> --preset watch --on 2026-10-05`.
- `extract` accepts `--on DATE` and `--from/--to DATE`, using the same parser as the search contract in NL-46, so the printed line selects exactly the matched window.

## Oracles

- A parent session with two subagent files yields one row with a count of two unless `--include-subagents` is given.
- Default output contains no transcript text. With `--snippet 80` each hit prints timestamp and at most 80 characters of matched prose, and the redaction pattern is applied before printing.
- The printed `extract` line, when run, produces prose only for records on the matched date.
- `--json` output contains the same fields in machine-readable form.

## SPEC ownership

`src/nyxloom/harness_search.py` (result shape, grouping, snippet), `session_extract` (window selection for `extract`). Update `docs/CONSUMERS.md` and `docs/CLI-REFERENCE.md`.

## Updates
