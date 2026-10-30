---
kind: backlog-entry
schema_version: 1
id: NL-49
title: "nyxloom-harness search rescans every transcript per query and does not finish unscoped within a shell timeout"
status: open
type: "feature"
severity: "low"
component: "session-extract"
context_estimate: "medium"
provenance: "operator request, 2026-10-15: unscoped search for get-py exceeded a 120 s timeout; --client claude finished in ~13 s"
filed_by: "Claude Haiku 5.5 (Claude Code session f8e59e42)"
spec_owner: "src/nyxloom/harness_search.py"
filed_date: "2026-10-15"
---

## Observed mechanism and reproduction

On 2026-10-15 an unscoped `nyxloom-harness search get-py` (no `--client`, no `--source-root`) did not finish within my 120 s shell timeout and was moved to the background. The same query with `--client claude` finished in about 13 s, and returned results sorted by date. The search re-reads every transcript on each call. Repeated discovery questions such as "which session discussed X on date Y" therefore pay the full scan cost each time.

## Why Nyxloom owns it

The harness store is owned by Nyxloom's search and extract layer. Per-session date spans and per-record prose hits are derived data that the search can cache safely, keyed by file path, size and mtime.

## Proposed contract

- Build an incremental index of session date spans, record counts by day and operator-prose hits, stored under the nyxloom state directory. Update it only for files whose size or mtime changed.
- `--fast` keeps its approximate raw-token semantics. The indexed path must give the same results as the full scan for the oracles below.
- Report progress and an estimated time when a full index build is needed, rather than running silently past a caller's timeout.
- `nyxloom-harness search --reindex` rebuilds from scratch; the index is never the source of truth and may be deleted safely.

## Oracles

- With an unchanged store, a second identical query reads no transcript files (verified by instrumentation or by a file-access count).
- Appending one record to one transcript causes exactly that file to be re-indexed, and the new record appears in results.
- Indexed and full-scan results for the same query are identical on a fixture store.
- Deleting the index directory and re-running the query produces the same results, with the rebuild reported.

## SPEC ownership

`src/nyxloom/harness_search.py`. The index location and format need a line in `docs/DESIGN-GUIDE.md`.

## Updates
