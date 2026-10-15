---
kind: backlog-entry
schema_version: 1
id: NL-50
title: "nyxloom-harness search output is not consumable: no session view, default limit, sort, identity, size hint, or stable columns"
status: open
type: "feature"
severity: "low"
component: "session-extract"
context_estimate: "medium"
provenance: "operator request, 2026-10-15: review of search output while locating session 384f276e"
filed_by: "Claude Haiku 5.5 (Claude Code session f8e59e42)"
spec_owner: "src/nyxloom/harness_search.py"
filed_date: "2026-10-15"
---

## Observed mechanism and reproduction

On 2026-10-15 `nyxloom-harness search get-py --client claude` printed one row per matching transcript file, in this shape: client, id, score, date, matched terms, path. Observed problems:

- The score (`0.043`, `0.009`) has no stated meaning, so a reader cannot tell why one row outranks another.
- Nothing identifies the session's topic. The first operator message, project (cwd), branch, and message counts are absent, so the path must be opened to learn what a session was about.
- Subagent files appear as rows beside their parent session, with no grouping (see NL-48).
- Every match is printed. A common word floods the terminal; there is no limit, footer, or total.
- Dates are not uniform: some rows show second precision with `Z`, others do not. The 36-character id pushes the useful columns to the right.
- The matched-terms column shows the tokens `get, py`, not the phrase that matched, which is the tokenising problem of NL-47 made visible.
- There is no size hint. Before running `extract`, the reader cannot tell whether the output will be a page or a book.

## Why Nyxloom owns it

Result shape, ranking, and the session metadata used to describe a result are produced by `harness_search.py`. Each consumer that wants "which session was it" otherwise has to open files and count records itself.

## Proposed contract

Two views, selected with `--view hits|sessions`:

- `--view hits` (the current behaviour, improved): one row per matching record group, with timestamp, role, matched phrase, and optional snippet (NL-48). Includes subagent rows unless grouped.
- `--view sessions` (proposed default for discussion lookups): one row per session, concise, `--limit 10` by default. Columns, in order:
  - last active (date and time, one fixed format, UTC unless `--tz`)
  - first active
  - title: first operator message, truncated to about 80 characters
  - project (cwd basename) and branch
  - hits: matching record count, with `user`/`assistant` split
  - size: operator words, assistant-prose words, epochs crossed
  - activity sparkline across the matched date range (one character per day)
  - short id (8 characters, resolved per NL-41)
  - full path only with `--paths`

Common options:

- `--limit N` (default 10 for sessions, 20 for hits) with a footer such as `showing 10 of 137; --limit N or --all`.
- `--sort last|first|hits|best` (default `last` for discussion lookups, so recent work is first).
- `--explain` replaces the opaque score with per-term hit counts and a rank label, and prints the arithmetic when asked.
- `--format table|tsv|ids` so results can be piped into `extract` or `xargs`. `--json` keeps the existing machine form.

## Oracles

- With 137 matching sessions, default output shows exactly 10 rows and the footer names the total.
- `--view sessions` prints one row per session; the same query with `--view hits` prints one row per matching record group.
- A session whose first operator message is 200 characters prints a title of at most 80 characters ending in an ellipsis.
- `--sort last` orders by last active date descending; `--sort hits` orders by matching record count.
- `--format ids` prints only short ids, one per line; piping it to `xargs nyxloom-harness extract` runs without a parser error.
- Sizes and sparklines are computed from the same records the search matched, so the size hint for a session matches what `extract --profile all` would emit within a documented tolerance.

## SPEC ownership

`src/nyxloom/harness_search.py` (view, sort, limit, metadata), the Claude and Codex metadata readers (title, project, branch, sizes). Update the `search` help text, `docs/CLI-REFERENCE.md`, and `docs/CONSUMERS.md` with an example of each view.

## Updates
