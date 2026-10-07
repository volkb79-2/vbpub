---
kind: backlog-entry
schema_version: 1
id: NL-34
title: "Upstream to Claude Code: no resume path for a user-stopped Agent-tool subagent; docs conflate --bg sessions with subagents"
status: open
type: "feature"
severity: "medium"
component: "session_extract"
provenance: "nyxloom SUCCESSOR package 2026-10 (E-020)"
filed_by: "Claude Sonnet (SUCCESSOR implementer)"
filed_date: "2026-10-06"
---

**Observed mechanism (Claude Code 2.1.290, E-020).** A subagent stopped from the task list ("stopped by user") cannot be messaged: `SendMessage` returns "was stopped by the user and was not resumed. Treat its work as cancelled; only start a new agent for it if the user explicitly asks." The operator found no UI path to open or message it either (`/list-agents` shows only running subagents, anonymously). The same agent stopped by the controller's `TaskStop` resumes with context intact. In both cases the transcript renders as a rejected tool call plus "[Request interrupted by user for tool use]", so the agent believes the user declined. Separately, `claude attach/respawn` and `claude agents` act on `--bg` sessions, not Agent-tool subagents; Claude Code's own docs agent claimed otherwise.

**Why filed here.** Claude Code is not a vbpub tool; this is the record to upstream. Nyxloom's workaround is the `nyxloom-successor` skill.

**Proposed upstream asks.** A user-visible way to resume or message a user-stopped subagent; a distinct transcript marker for who stopped it; docs that separate `--bg` sessions from Agent-tool subagents.

**Oracles.** Re-run the P5 probe on a later Claude Code release; if SendMessage works, retire step 0's refusal branch.

**Spec owner.** `docs/design-context-lifecycle-experiments.md` E-020.
