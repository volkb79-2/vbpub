---
kind: backlog-entry
schema_version: 1
id: NL-70
title: "nyxloom-harness has no session-family usage table: sessions, subagents, tokens per session, context growth and compaction points"
status: open
type: "feature"
severity: "medium"
component: "harness"
priority: 2
filed_by: "controller, 2026-11-14 operator question"
filed_date: "2026-11-14"
---

## Problem

The operator asked (2026-11-14): when was the controller's last compaction, how
many Sonnet/Opus subagent sessions were used in the last days, and what do they
cost (input, output and cache-read tokens per session, growth of the context
window over time, compaction points)? `nyxloom-harness` answers only a part of
this:

- `extract-report SESSION_LOG` prints one session's timeline with compaction rows
  (kind `compaction`, trigger, tokens before and after, duration) and the sum of
  tokens, but its subagent activity is aggregated into one `↳agents` row per
  block of the parent timeline: no table lists each subagent session with its
  model, effort, calls, input/output/cache tokens, peak context and wall time.
- `extract-sessions` lists session families with record counts and first/last
  timestamps, but no token or model information.
- `nyxloom/tools/jsonl-metrics.py curve` prints per-agent context growth, but it
  is a separate script that takes transcript paths one by one and is not a
  `nyxloom-harness` verb (it has no family view and no compaction markers).
- Codex runs (the implementation and review routes) have their own transcripts
  and are not part of the Claude Code family report at all.

One controller session accumulated 265 subagent transcripts over about nine
days; on 2026-11-12/13 alone 62 of them carried about 1.2 billion cache-read
tokens (about 1.97 billion with the controller's own). Answering "which sessions cost what" took hand-written scripting over
several transcript files.

## Wanted

A `nyxloom-harness` verb (for example `extract-usage SESSION_OR_FAMILY`) that,
for one session and all its subagents (and optionally the Codex runs named in a
runs directory), prints one table and a `--json` form:

1. one row per session: id, role if known (controller / subagent / Codex run),
   model and effort, first/last timestamp, wall time, assistant calls,
   input, output, cache-creation and cache-read tokens, peak context tokens;
2. context growth over time for the parent (a compact curve: timestamp and
   context tokens at every Nth call plus every compaction point) and per
   subagent (peak and last value);
3. the compaction points of every session in the family with trigger
   (auto/steered), tokens before and after, and the time since the previous one;
4. totals per model and per day, with a clear statement of what is estimated.

It must read transcripts incrementally (large files), never print transcript
content, and share the parsing with `extract-report` and `jsonl-metrics.py`
instead of duplicating it.

## Close when

The verb exists with tests on synthetic transcripts (one parent with two
compactions, three subagents on different models, a Codex run); a documented
example answers the three operator questions above (last compaction, number of
subagent sessions, tokens per session) in one call; `extract-report` documents
that its `↳agents` rows are aggregates and points to the new verb.
