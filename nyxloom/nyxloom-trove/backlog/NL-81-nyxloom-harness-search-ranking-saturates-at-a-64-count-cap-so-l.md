---
kind: backlog-entry
schema_version: 1
id: NL-81
title: "nyxloom-harness search ranking saturates at a 64-count cap, so long sessions tie and the newest wins"
status: open
type: "bugfix"
severity: "high"
provenance: "vbpub session a8193e5d, 2026-11-17: finding the debian-rollback session"
filed_date: "2026-11-17"
---

## Observed mechanism and reproduction

`search_sessions` (src/nyxloom/harness_search.py:1907-1934) scores a session as the sum over matched terms of `idf * (1 + log(tf))`, where `tf` is clamped by `_TERM_FREQUENCY_CAP = 64` (line 49). With `--sort-by best` the order is matched-term count, then score, then last activity (newest first).

Live reproduction, 2026-11-17, `nyxloom-harness search debian rollback --word-match all --client claude --progress quiet`:

- Five sessions tied at exactly 4.409: the session that held the discussion (368 mentions of `debian-rollback`), a controller session that mentions it 41 times, this session (which only contains the query text itself), and two others. Both terms exceed 64 occurrences in every one of them, so the cap erases the difference.
- Recency then decides: the discussion session (last activity 2026-11-13) ranked 5th behind newer sessions. The relevant session lost to this session only because it was written later.

Other properties of the score, from the code:

- The count is raw token occurrences over the whole file. Tool output, pasted logs, thinking and subagent traffic count the same as something the operator typed.
- No length normalisation: a 100 MB controller session beats a focused 6 MB session simply by being long.
- No field weights: the custom title, the first operator prompt and operator messages carry no extra weight.
- No breadth signal: it does not distinguish one pasted log containing both words from twelve operator turns about the topic.
- Subagent files score as independent documents, so one discussion produces hundreds of rows (555 rows for this query, over 500 of them subagent files of one controller session).

## Why Nyxloom owns it

Scoring is `harness_search.py`. The cap is a cost bound on counting. It is a reasonable bound for counting, but wrong as a ranking input.

## Proposed contract

1. Replace the hard clamp with saturating term weight `tf / (tf + k1 * (1 - b + b * len/avglen))` (BM25 with length normalisation), with `len` in records, not bytes.
2. Count records, not token occurrences: score on the number of distinct records (and distinct operator turns) containing each term. A pasted log is one record.
3. Weight by field: session title and first operator prompt (highest), operator messages (high), assistant prose (medium), tool results, thinking and subagent traffic (low).
4. Add a coverage bonus when all query words occur in the same record or within a short window, and an exact-phrase bonus for quoted or hyphenated queries (NL-47).
5. Roll subagent files into their parent family: family score is the parent's score plus a discounted best child. One row per family, with the subagent count shown (NL-48).
6. Recency is a bounded tie-break, never the primary key. Exclude the current session (NL-80).
7. `--explain` prints the per-term, per-field breakdown for each row, so a ranking can be debugged.

## Behavioural oracles

- Golden set in the test fixtures: query plus expected session, scored by mean reciprocal rank. First case: `debian rollback` must rank the discussion session above a newer session that only mentions the topic in passing. Add `get-py` (NL-47) and a query that matches only a subagent.
- Controlled wrong implementation: keep the cap and the recency tie-break; the first golden case must fail.
- Saturation: a session with 10 operator turns about the topic must outrank a session with 500 matches in one pasted log.
