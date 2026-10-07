---
kind: backlog-entry
schema_version: 1
id: NL-36
title: "nyxloom MCP edit/write tool with a required intent field (Claude Code Edit/Write reject an intent parameter)"
status: open
type: "feature"
severity: "medium"
component: "session_extract"
provenance: "nyxloom SUCCESSOR-2 package 2026-10; scratchpad/resume-exp/intent-exp.md"
filed_by: "Claude Sonnet (SUCCESSOR-2 implementer)"
filed_date: "2026-10-06"
---

**Observed mechanism (measured 2026-10-06, Claude Code 2.1.290; evidence `scratchpad/resume-exp/intent-exp.md` of the SUCCESSOR session, summarised in `docs/design-context-lifecycle-experiments.md` E-020).**

- Bash has a schema field (`description`) and the model fills it when told to: 45/45 with a rule line in the dispatch prompt, 0/141 without one in realistic prompts. Edit/Write have NO such field.
- The built-in Edit and Write tools REJECT an extra `intent` parameter (InputValidationError), so the Bash approach cannot be copied.
- Free-text pairing ("an `Intent:` line before every Edit/Write") is fragile: no rule 0/3; a rule without one-edit-per-message 1/3 (three calls batched under one line, and an Edit turned into a whole-file Write); strict rule (one Edit/Write per message) 5/5, at the cost of all parallel edits.
- A PreToolUse hook cannot enforce it: at hook time neither the in-flight tool_use nor the current message's text is in the transcript (6/6), and in a second run the model wrote the Intent lines only in thinking (persisted as null) while claiming it had written them (0/11 visible).

**Why filed here.** The extractor (`session_extract`, successor/review presets) pairs an `Intent:` text line with the following Edit/Write as best effort; only a schema field is a reliable carrier. The tool belongs to nyxloom's product surface (agent pipeline), not to Claude Code.

**Proposed contract (operator-approved as a SEPARATE package, 2026-10-06).** A nyxloom MCP server offering edit and write tools with a REQUIRED `intent` string, offered alongside the built-ins and preferred for interactive sessions (instruct via the dispatch skill and CLAUDE.md). Same semantics as Edit/Write (exact-string replace, refuse non-unique match, read-before-write). The extractor then reads the intent from the MCP tool's `input.intent` (tool names `mcp__nyxloom__edit`/`write`, whatever the final names are) in the same `intent-or-call` rendering path, and treats the `Intent:` text line as a fallback only. The ledger shows the intent next to each touched file.

**Oracles.** A tool call without `intent` is rejected by the schema; an edit through the tool produces the same file bytes as the built-in; the extractor test with a fixture of MCP edit calls renders the intent verbatim and the controlled wrong implementation (falls back to the call text) fails; re-measure compliance on a real wave (count MCP edits with a non-empty intent versus built-in edits).

**Not in scope of the SUCCESSOR package.** Do not build it there; this entry records it and the extractor follow-up together.

**Spec owner.** `docs/design-context-lifecycle-experiments.md` E-020 (Edit/Write intent pairing).
