---
kind: backlog-entry
schema_version: 1
id: NL-62
title: "`nyxloom-harness search` cannot query record-level evidence across a session, project stores, subagents, Codex run logs, and extra paths in one call"
status: open
type: "feature"
severity: "high"
component: "session-extract"
provenance: "operator direction, 2026-11-10: search the whole session and agent communication for every eBPF reference, compare nyxloom-harness with the ad hoc approach, and optimize the tool"
filed_by: "Codex deepseek-v4.1-flash"
spec_owner: "src/nyxloom/harness_search.py; session-extract adapters and source resolution"
filed_date: "2026-11-11"
---

## Problem

`nyxloom-harness search` ranks session files by query words. It cannot answer a
forensic question at record level across all of the evidence that belongs to one
project: a regex, case policy, role or record-type filter, normalized context,
timestamps, and hit counts have to be obtained by chaining search, `extract`,
raw `grep`, and a bespoke Python walker. The walker then needs its own knowledge
of every transcript and side-file format, and each source is exposed in a
different way.

The operator needs one command over:

1. the current session;
2. every session associated with a project directory;
3. the selected parent session's subagent transcripts and metadata;
4. Codex/DeepSeek run logs recorded separately from their parent session;
5. optional additional files or directories, such as reports, docs, and a
   repository subtree.

## Evidence

The controller session record for 2026-11-10 records all of the following.
An initial search for every eBPF reference needed raw `grep` (0.17 s over a
50 MB JSONL file) plus a custom Python walker. The existing extract with
`--profile all --tool-calls intent-or-call` took 1.7 s and showed only 18 of the
38 raw hits: tool-use inputs, tool results, and `<task-notification>` subagent
results were excluded, while communication prompts were truncated to one line.

The same investigation found earlier project sessions outside the current
extract window: 26 files were inspected, and three more contained eBPF
discussions, including the operator's 2026-10-27 15:30 question about using
`bpftrace` as input for the IO governor. One parent session also had 394
subagent transcript files, and the relevant Codex run area had 504
`codex/runs/*.jsonl` files. None of those side sources was reachable through one
extract mode.

## Why nyxloom owns it

Nyxloom already owns harness discovery, transcript adapters, normalized roles,
record timestamps, redaction, and extraction. A caller should not have to
reconstruct those semantics differently for a parent transcript, a subagent
file, a run log, and a Markdown report. One source-aware matcher also makes the
hit count and provenance reproducible instead of dependent on the exact shell
pipeline used after an incident.

## Proposed minimal solution

Extend the existing search surface rather than add another top-level tool:

1. Make the detailed hit view in NL-50's `--view hits` record-oriented: one row
   per matching normalized record group, retaining the current session-ranking
   behavior as `--view sessions` rather than adding a third view.
2. Accept source scopes as repeatable closed values: current session, a project
   directory, a parent session family, the default harness stores, Codex run
   logs associated with the selected project, and explicit extra files or
   directories. When no source is supplied, use the current project only when
   the invocation supplies enough identity to derive it unambiguously and the
   current session only when harness metadata identifies it exactly; otherwise
   refuse and require a scope. Never invent a session/project or silently expand
   to every home-owned store.
3. Match `PATTERN` as a case-insensitive regular expression by default, with
   `--literal` for literal matching and `--case-sensitive` as an explicit opt-in.
   Compile and refuse an invalid regex before scanning any source.
4. Normalize every supported JSONL, SQLite, reasonix, and text artifact through
   its adapter. Emit timestamp, source kind, project, source path, parent session
   id when applicable, subagent id when applicable, role, record type, and the
   matching text. A `--context N` value adds up to N normalized neighbor records,
   never raw multi-line JSON envelopes.
5. Filter with closed role and record-type values. The record-type vocabulary
   must distinguish operator text, assistant prose, tool input, tool result,
   agent input/prompt, agent result/notification, system metadata, and persisted
   document text.
6. Print hit totals by source kind, role, and record type, with the per-cluster
   counts immediately beside the representative hits. Add `--json` output with
   the same fields.
7. Reuse the shared source/family resolver from NL-63 and the incremental index
   contract from NL-49. Do not implement a second recursive walker inside the
   renderer.

The match pass must not invoke the full extract renderer merely to answer a
query. The measured ad hoc raw scan found all 38 references in 0.17 seconds,
while the semantic extract pass took 1.7 seconds and then omitted 20 of them.
Normalize and match each source in one streaming pass, compute context and
counts from the same records, and parse only records selected by an exact
prefilter when such a prefilter is provably equivalent to the requested regex.
Otherwise correctness wins: refuse the prefilter and scan normalized records
directly. Cache only reusable derived data under NL-49 so repeated
investigations do not pay the full traversal again.

## Oracles

- A fixture parent session, two subagent files, one sidecar metadata file, one
  separate Codex run log, and an extra report directory produce exactly the
  expected records from every selected source in one invocation.
- A regex matches text that the current word tokenizer cannot express; an
  invalid regex exits non-zero before any source is opened.
- `--role` and `--record-type` filters select disjoint expected sets, while the
  unfiltered totals equal their sum where the vocabularies do not overlap.
- `--context 2` prints at most two normalized neighboring records on each side
  and never prints a raw JSON object as a neighboring record.
- The allocation between earlier project sessions and the current session is
  deterministic and repeated runs produce byte-identical ordering and counts.
- A controlled implementation that still sends all matching through
  `--tool-calls intent-or-call` misses the task-notification and separate-run-log
  fixtures, so the new path must fail that negative.
- A source that cannot be narrowed by a safe raw prefilter still returns every
  normalized match; a prefilter that drops a regex match is a failed optimization,
  not an acceptable approximation.

## Where

- `nyxloom/src/nyxloom/harness_search.py`: source selection, match policy,
  normalized record shape, grouping, counts, and renderers.
- `nyxloom/src/nyxloom/cli.py`: `--view`, pattern, scope, filter, context, and
  persistence options.
- `nyxloom/src/nyxloom/session_extract/adapters/` and `locate.py`: reuse
  format-specific parsing and family discovery.
- `nyxloom/tests/test_harness_search.py` plus adapter fixtures containing a
  parent, subagents, metadata, a separate run log, and non-session documents.
- `nyxloom/docs/CLI-REFERENCE.md` and `nyxloom/docs/CONSUMERS.md`: one pasteable
  forensic-search example and the source boundary.

## Priority

High. This is a repeated controller investigation surface, and the measured raw
approach both under-reported communication hits (18/38) and required parallel
tooling outside nyxloom.

**Close when.** The one-call source matrix, regex/literal and case behavior,
role and record-type filters, normalized context, per-role and per-record-type
counts, deterministic ordering, and negative adapter case pass on a clean final
commit; the CLI reference and consumer guide document the exact source scopes
and default. A recorded operator decision to keep this as a deliberately
session-ranking-only tool also closes the item when that limit, the supported
manual procedure, and tests for the intended refusal are documented.

Related: [NL-46](NL-46-nyxloom-harness-search-cannot-restrict-hits-to-a-calendar-day-o.md),
[NL-47](NL-47-nyxloom-harness-search-matches-separate-tokens-not-phrases-and.md),
[NL-48](NL-48-nyxloom-harness-search-results-do-not-group-subagents-show-snip.md),
[NL-49](NL-49-nyxloom-harness-search-rescans-every-transcript-per-query-and-d.md),
[NL-50](NL-50-nyxloom-harness-search-output-is-not-consumable-no-session-view.md),
[NL-57](NL-57-session-extract-since-iso-time-and-relative-duration.md),
[NL-58](NL-58-session-extract-include-agent-communications-optionally.md), and
[NL-63](NL-63-resolve-project-and-parent-session-side-files.md). Those items own
date grammar, phrase matching, grouping/presentation, indexing, time bounds,
extract communication modes, and source-family resolution respectively.
