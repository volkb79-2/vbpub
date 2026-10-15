---
kind: backlog-entry
schema_version: 1
id: NL-46
title: "nyxloom-harness search cannot restrict hits to a calendar day or find a day's active discussion"
status: open
type: "feature"
severity: "medium"
component: "session-extract"
context_estimate: "medium"
provenance: "operator request, 2026-10-15: find the session with a ciu-install discussion on 05.10.2026"
filed_by: "Claude Haiku 5.5 (Claude Code session f8e59e42)"
spec_owner: "src/nyxloom/harness_search.py"
filed_date: "2026-10-15"
---

## Observed mechanism and reproduction

`nyxloom-harness search WORD [WORD ...]` (src/nyxloom/harness_search.py, `search_sessions`) has no time filter. Its only ordering and selection controls are `--sort-by {best,date}`, `--word-match`, `--term-match`, `--client` and `--source-root`. The question "which session had an active discussion on 05.10.2026" cannot be expressed. On 2026-10-15 the operator asked for exactly this. The only route was to scan every Claude Code and Codex transcript with ad hoc grep/Python over ~1.7 GB, filtering each record by its `timestamp` field by hand. The 2026-10-05 discussion was found that way in session `384f276e-fadc-4611-bf1d-973b249d83c0`, which spans 2026-10-04 to 10-08. Session-level date ranges alone cannot identify the day of activity.

## Why Nyxloom owns it

Session discovery, timestamp parsing and the Claude/Codex/OpenCode adapters already live in `harness_search.py` and `session_extract`. Recovering the date of a discussion is a harness-store concern, and every consumer that asks "what happened on day X" would otherwise repeat the same raw scan.

## Proposed contract

- `--on DATE` restricts hits to records whose timestamp falls on that calendar day (UTC unless `--tz` is given).
- `--from DATE --to DATE` gives an inclusive window.
- Accept ISO `YYYY-MM-DD` and `DD.MM.YYYY`. Reject a day/month-ambiguous input such as `05.10.2026` unless `--date-order dmy|mdy` is given, so the two readings cannot be confused silently.
- `--active-on DATE` ranks sessions by the number of operator-authored prose records on that day. Each result shows its per-day count, the first and last timestamp on that day, whether it is a top-level session or a subagent, and the session's store path.
- Date filtering applies to record timestamps, not only to session start and end, so a session that began months earlier and was active on the requested day is still returned.

## Oracles

- A fixture session with records on 2026-10-04 and 2026-10-05 returns for `--on 2026-10-05` and not for `--on 2026-10-06`.
- `--on 05.10.2026` is accepted only with `--date-order dmy` or as an unambiguous form; `--on 05.10.2026` with no order flag exits non-zero with a message naming both readings.
- `--active-on` ranks a session with 40 operator records on the day above one with 3, and reports the counts.
- Without any date flag, output is unchanged for existing callers.

## SPEC ownership

`src/nyxloom/harness_search.py` owns the search contract. Update the `search` help text, `docs/CLI-REFERENCE.md`, and the Claude/Codex metadata reader that supplies record timestamps.

## Updates
