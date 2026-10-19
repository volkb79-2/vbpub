---
kind: backlog-entry
schema_version: 1
id: NL-47
title: "nyxloom-harness search matches separate tokens, not phrases, and counts synthetic records as discussion"
status: open
type: "feature"
severity: "medium"
component: "session-extract"
context_estimate: "medium"
provenance: "operator request, 2026-10-15: search for 'cmru get-py' and 'install' on 05.10.2026 returned unrelated sessions and compaction summaries"
filed_by: "Claude Haiku 5.5 (Claude Code session f8e59e42)"
spec_owner: "src/nyxloom/harness_search.py"
filed_date: "2026-10-15"
---

## Observed mechanism and reproduction

On 2026-10-15, `nyxloom-harness search get-py --client claude` returned sessions containing the separate tokens `get` and `py`, in any order, not the phrase `cmru get-py`. Results included an unrelated Claude subagent transcript with no `cmru` text, and the scan was repeated per token. The search also cannot be restricted to the operator's own words. Compaction summaries (`This session is being continued from a previous conversation ...`), skill preambles (`Base directory for this skill: ...`) and `/compact KEEP` echoes contain the same keywords, so a search for "install" and "ciu" was dominated by these synthetic records and not by the discussion itself.

## Why Nyxloom owns it

Tokenisation, record roles and synthetic-record detection belong to the transcript adapters in `harness_search.py` and `session_extract`. Every consumer that searches for a discussion needs the same signal filter, and reimplementing it in ad hoc scripts is what made the search slow and noisy.

## Proposed contract

- `--phrase "cmru get-py"` matches the exact contiguous phrase, case-insensitively, after whitespace normalisation. Hyphenated and dotted terms such as `get-py` and `get.py` are not split into tokens for phrase queries.
- `--role user|assistant|any`, default `user`. Assistant-only matching remains available.
- Synthetic records are excluded by default: compaction summaries, skill base-directory injections and `/compact` echoes. `--include-synthetic` restores them. The classifier is a named, tested list of markers, not a free-text heuristic.
- Pasted-content blocks (`<pasted_content>`) are counted as operator text but reported with a flag, because they may be reviewer output rather than the operator's own words.

## Oracles

- A phrase query `get-py` does not match a record containing only `get` and `py` in separate positions.
- A compaction-summary record containing the query words is not counted with `--role user` unless `--include-synthetic` is given.
- `--role assistant` returns a hit that `--role user` does not.
- Existing word-match behaviour (`--word-match`, `--term-match`) is unchanged when `--phrase` is absent.

## SPEC ownership

`src/nyxloom/harness_search.py` (query and role handling) and the session-extract adapters for synthetic-record markers. Update the search help text and `docs/CLI-REFERENCE.md`.

## Updates
