---
kind: backlog-entry
schema_version: 1
id: NL-58
title: "`extract --agent-comms off|summary|full`: ordinary modes drop subagent results, truncate Agent/SendMessage prompts, and omit separate Codex/OpenCode run logs"
status: open
type: "bugfix"
severity: "medium"
component: "session-extract"
provenance: "controller session record 2026-11-10; backlog filing for the operator"
filed_date: "2026-11-10"
---

## Problem

Agent communication is neither fully operator text nor ordinary assistant prose, and the current extractor handles it as noise on every path:

- Claude subagent completion arrives as a user-role `<task-notification>` message and `adapters/claude_code.py` strips the whole tagged body as pure harness noise in every mode;
- Agent and SendMessage ledger entries collapse the prompt/message to a one-line, 100-character clip;
- Codex and OpenCode runs launched through Bash write their useful result in a separate run log that the parent session extractor does not follow.

The information is operationally important for a controller readback (which subagent returned what, and what prompt produced it), but it should not silently inflate every watch/successor/review extraction.

## Evidence

The controller session record 2026-11-10 measured 31 KB / 7.7k tokens of `<task-notification>` content in five hours. All of it was excluded in every extraction mode. The same record observed Agent/SendMessage prompts reduced to one line and Codex/OpenCode Bash-launched run results living only in their separate run logs.

This is distinct from NL-31, which covers missing Claude-only tool-call/error/ledger/successor extraction in the Codex and OpenCode adapters. NL-58 concerns the representation and opt-in inclusion of agent-to-agent communication that already exists in the transcript or a referenced run log.

## Proposed minimal solution

Add one closed option, `--agent-comms {off,summary,full}`, default `off` so current output stays byte-compatible:

- `off`: current behavior, including task-notification stripping and one-line Agent/SendMessage prompts.
- `summary`: emit `AGENT-OUT` records for the full launch/message intent (whitespace-normalized and bounded by a declared cap), and `AGENT-IN` records for the result's identity, status, and final-message summary. Do not emit intermediate progress payloads.
- `full`: emit the same records with untruncated textual payloads, subject only to XML/tag framing removal. Preserve stable envelope metadata such as task id, agent id, status, and referenced run-log path.

Normalize all included communication to explicit `AGENT-OUT` / `AGENT-IN` event kinds and keep it out of `OPERATOR_TEXT`; `--prose-only` remains prose-only. Add a repeatable `--agent-run-log PATH` input for Codex/OpenCode run logs known to belong to the current session. Its parser may reuse the existing Codex/OpenCode drivers, but the external log is labelled with the originating session/item rather than merged indistinguishably into the parent. Automatic discovery of arbitrary Bash-created log files is out of scope until the transcript supplies an unambiguous path reference.

## Where

- `nyxloom/src/nyxloom/session_extract/adapters/claude_code.py`: `_HARNESS_TAG_RE`, `_strip_harness_tags`, and parse-time suppression.
- `nyxloom/src/nyxloom/session_extract/ledger.py`: `AGENT_CONTROL_TOOLS` and `agent_control_line`.
- `nyxloom/src/nyxloom/session_extract/toolresult.py`: `one_line` and `summarize_call`.
- `nyxloom/src/nyxloom/session_extract/config.py`, `select.py`, `render.py`, and `presets.py`: event class, selection, labels, and preset interaction.
- `nyxloom/src/nyxloom/cli.py`: command option and `ExtractConfig` construction.
- `nyxloom/tests/test_cli_extract.py`, `test_session_extract_successor.py`, and adapter-specific tests.

## Acceptance test idea

Use synthetic Claude and Codex/OpenCode transcripts containing a tagged task notification and a long Agent/SendMessage prompt, plus a separate referenced run log. Assert that `off` reproduces the current bytes and emits neither tag; `summary` emits one `AGENT-OUT` and one `AGENT-IN` with bounded summaries; `full` retains the key input and final result text verbatim apart from framing; the external log is associated with the parent item; and `--prose-only` excludes every agent record. Re-applying unconditional task-notification stripping makes the summary/full cases fail.

**Close when.** The `off` byte-compatibility, `summary`, `full`, referenced-run-log, and `--prose-only` oracles pass on a clean final commit, and the extraction mode, caps, and run-log association are documented. A recorded operator decision to retain the current stripping/truncation behavior also closes the item when the suppression is documented as a known by-design limit and a regression test locks the stated behavior.

## Priority

Medium. It is opt-in and preserves the current default, but it closes a material controller-observability gap with a measured 7.7k-token volume in five hours.

## Updates

2026-11-11: record-level search must expose the same communication classes that
NL-58 adds to extraction. A search for session evidence has to include complete
Agent/SendMessage launch prompts, `<task-notification>` subagent results, and
explicitly associated Codex/DeepSeek run-log results, rather than inheriting
extract's default stripping and one-line truncation. This is a search-facing
application of NL-58's `off|summary|full` representation and provenance contract,
not a fourth extraction mode. NL-62 owns the source-wide search control surface
and role/record-type hit counts; this update prevents communication records from
disappearing silently on the search path.
