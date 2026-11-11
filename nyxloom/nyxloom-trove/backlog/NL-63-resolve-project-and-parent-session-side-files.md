---
kind: backlog-entry
schema_version: 1
id: NL-63
title: "search and extract cannot resolve a project directory or parent session id into its complete side-file family"
status: open
type: "feature"
severity: "medium"
component: "session-extract"
provenance: "operator direction, 2026-11-10: one search/readback call had to find 394 subagent transcripts and 504 Codex run logs outside extract modes"
filed_by: "Codex deepseek-v4.1-flash"
spec_owner: "src/nyxloom/session_extract/locate.py"
filed_date: "2026-11-11"
---

## Problem

`extract` already accepts a path or bare session id and can target one Claude
Code subagent transcript. It does not expose the resolved parent's whole side-file
family: the `subagents/agent-*.jsonl` transcripts and their matching
`agent-*.meta.json` records. `search` discovers individual transcript files but
does not accept a project directory or parent session as a scope, and its
current result shape can present subagent files without their owning parent.

A caller investigating one long controller session therefore has to know each
harness's on-disk layout, enumerate the side directory separately, infer
parentage from metadata, and pass each file to a different extract invocation.

## Evidence

The controller session record for 2026-11-10 records 394 subagent transcript
files under one parent session, each with a sibling metadata sidecar in the
supported Claude layout. The same investigation had to reach 504 separate
`codex/runs/*.jsonl` records and three earlier project sessions outside the
current extract window. The existing bare-id resolver was useful for one target
but had no project/family scope that both search and extract could share.

## Proposed minimal solution

Extend the existing resolver in `session_extract/locate.py` and make both
`search` and `extract` consume it:

1. Accept an existing path, a bare supported session id, or an explicit
   `--project-dir DIR` scope. Resolve the project directory through the harness's
   declared project/session mapping, never through a guessed escaped-name rule
   alone.
2. For a resolved parent file, discover sibling `subagents/agent-*.jsonl`
   transcripts and pair each with `agent-<id>.meta.json` by the exact id. Expose
   parent id, subagent id, spawn depth, agent type, and description when the
   metadata contains them; absence remains explicit rather than invented.
3. Return a typed family with the primary target, side transcripts, sidecars,
   and any externally associated run logs. Search defaults to the whole family
   for an explicit parent/project scope; extract keeps single-target behavior
   unless `--include-subagents` is passed.
4. A missing, malformed, duplicated, or unreadable sidecar is reported as an
   indeterminate family or a named partial result according to a documented
   policy. Never silently omit part of the family and still call it complete.
5. Keep deduplication by physical file identity, so hard-linked or symlinked
   aliases cannot inflate hit counts.

## Oracles

- A parent fixture with three subagent files and three matching metadata files
  resolves to one family with one primary plus three side transcripts; search
  reports all matches and extract reports only the primary without
  `--include-subagents`.
- A bare parent id and an equivalent `--project-dir` selection produce the same
  family and normalized ordering.
- A malformed sidecar, an orphan transcript, an orphan metadata file, and a
  hard-linked transcript alias each produce the documented named disposition
  and never a silent complete family.
- Extract with `--include-subagents` labels each emitted block with its parent,
  subagent id, and metadata type; search keeps enough provenance to recover the
  same identity.
- A controlled implementation that treats every `agent-*.jsonl` as an unrelated
  top-level session fails the parentage and deduplication oracles.

## Where

- `nyxloom/src/nyxloom/session_extract/locate.py`: typed project/family
  resolution and sidecar pairing.
- `nyxloom/src/nyxloom/cli.py`: shared reference resolution and
  `--include-subagents` / project-scope option plumbing.
- `nyxloom/src/nyxloom/harness_search.py`: consume the resolver instead of
  independently classifying subagent paths.
- `nyxloom/tests/test_session_extract_locate.py`,
  `nyxloom/tests/test_cli_extract.py`, and search fixtures.
- `nyxloom/docs/CLI-REFERENCE.md` and `nyxloom/docs/CONSUMERS.md`: the parent,
  child, and project-directory examples.

## Priority

Medium. It removes fragile path discovery before the larger record-level search
contract in NL-62 can be implemented cleanly.

**Close when.** Path, bare-id, and project-directory references resolve to the
same family; parent/child metadata pairing, incomplete-family dispositions,
physical deduplication, search provenance, and explicit extract subagent
inclusion pass on a clean final commit; and the user docs describe the default
and opt-in scopes. A recorded operator decision to keep subagent discovery as a
manual single-file workflow also closes the item when its exact supported
procedure and a regression test for the deliberate limitation are documented.

Related: [NL-48](NL-48-nyxloom-harness-search-results-do-not-group-subagents-show-snip.md)
owns grouped presentation and the search-to-extract handoff; NL-63 owns the
shared project/session/family resolver used beneath it.
