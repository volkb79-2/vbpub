---
kind: backlog-entry
schema_version: 1
id: NL-80
title: "nyxloom-harness search returns the invoking session as its top hit, so a query with no matches looks like one match"
status: open
type: "bugfix"
severity: "medium"
provenance: "vbpub session a8193e5d, 2026-11-17: finding the debian-rollback session"
filed_date: "2026-11-17"
---

## Observed mechanism and reproduction

`nyxloom-harness search` indexes the invoking session's own transcript. The command line you just typed is in it, so the current session matches its own query:

- `nyxloom-harness search zzzqqqnomatch --client claude --progress quiet` printed one row, the current session, with score 16.079, and exited 0. The term appears nowhere else.
- `search debian rollback --word-match all --client claude` ranked the current session first (4.409, tied with four other sessions), ahead of the session that held the actual discussion.

Two consequences: a query with no real matches cannot be told apart from one with a single match, and the top row is the least useful one. There is also no "0 matches" footer or total in any case.

## Why Nyxloom owns it

Result ranking and record roles are produced by `harness_search.py`. It already distinguishes synthetic records (NL-47), and the search command's own tool call and result are the same kind of self-referential record.

## Proposed change

- Exclude records that are the search invocation itself (a tool call whose command is `nyxloom-harness search ...`, and its result) from every session.
- Exclude the current session by default. Detect it from the harness environment where available, otherwise as the newest-written transcript. Add `--include-current` to opt back in.
- Always print a footer with the match count (`0 sessions matched` included), the number of rows hidden as subagents, and the stores searched.
- Tests: the zzz-style query returns an empty result and the "0 sessions matched" footer; the current session is excluded unless `--include-current` is passed.
