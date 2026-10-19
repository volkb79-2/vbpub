---
kind: backlog-entry
schema_version: 1
id: NL-42
title: "Provide a simple inbox capture path and explain managed backlog setup"
status: open
type: "feature"
severity: "medium"
component: "cli"
context_estimate: "medium"
provenance: "operator session, netcup-api-filter CLI use, 2026-10-15"
filed_by: "operator request via Codex"
spec_owner: "docs/backlog-entries-spec.md; docs/CLI-REFERENCE.md"
filed_date: "2026-10-15"
---

## Observed mechanism and reproduction

On 2026-10-15, a user ran Nyxloom from the `netcup-api-filter` repository root and from `nyxloom-trove/`. Both locations resolve the same `nyxloom-trove/nyxloom.toml`: `_resolve_backlog_project()` walks upward from the current directory and checks each ancestor for the managed trove config. The consumer config declares `[project].backlog = "nyxloom-trove/4-backlog.md"`, but omits `[backlog_entries]`. The CLI therefore finds the project and then refuses `backlog list` with only `project has no [backlog_entries] table in nyxloom.toml`.

This is two distinct backlog modes: the configured spine inbox is for lightweight ideas, while `nyxloom backlog new/list/...` manages per-entry files and requires opt-in. The current group help and refusal do not make that distinction or tell the user what to do next.

## Why nyxloom owns it

`nyxloom` owns local trove discovery and both backlog authoring surfaces. A project-local command can update its configured inbox without registry or daemon access.

## Proposed contract

1. Add a one-line quick-capture command, for example `nyxloom backlog capture TITLE`, that appends an idea to the path declared by `[project].backlog`, preserving existing item order, comments, and unrelated bytes. It must handle the supported inbox formats and must not require `[backlog_entries]`.
2. Keep `backlog new/list/edit/...` as the managed-entry interface. When its table is absent, report the discovered `nyxloom.toml` path, explain that managed entries are opt-in, and show that `[backlog_entries].id_prefix` is required. Show `dir` only as optional, with the documented default. If the table exists without `id_prefix`, name that missing key. Never guess a project prefix or silently redirect a managed operation into the inbox.
3. Make `nyxloom backlog` help distinguish quick inbox capture from managed entries and include the appropriate command for each.
4. If no supported inbox path is configured, fail with the exact missing configuration fact and a useful next step; do not invent a path.

## Oracles

- From both a repository root and a nested trove directory, discovery resolves the same nearest project config.
- With a configured inbox and no `[backlog_entries]`, quick capture writes exactly one item to the configured inbox and preserves its other items and formatting.
- Managed commands still refuse a missing table with a message naming the found config and required `id_prefix`; a present table missing that key gets a distinct message.
- A project with neither a config nor a declared inbox fails without writing to a guessed location.
- README, DESIGN-GUIDE, CONSUMERS, CLI reference, and command help explain the two paths with examples.

## SPEC ownership

`src/nyxloom/cli.py` project resolution and backlog handlers; `src/nyxloom/backlog_items.py`; `src/nyxloom/backlog_entries.py`; `docs/backlog-entries-spec.md`; `docs/CLI-REFERENCE.md`.

## Updates
