---
kind: backlog-entry
schema_version: 1
id: NL-41
title: "Resolve unique short UUID prefixes in nyxloom-harness session extraction"
status: open
type: "feature"
severity: "low"
component: "session-extract"
context_estimate: "small"
provenance: "operator request, 2026-10-14; CMRU Claude job daemonShort f2dc69bb maps to session f2dc69bb-e448-407d-8c7b-b48f7761675a"
filed_by: "Codex"
spec_owner: "session_extract/locate.py"
priority: 3
filed_date: "2026-10-14"
---

## Observed mechanism and reproduction

The shared resolver in `session_extract/locate.py` accepts an existing transcript path or recognized full session identifiers. Claude Code and Codex UUID lookup currently starts only when the input matches the complete canonical `8-4-4-4-12` UUID form. The CMRU Claude job exposed `daemonShort: f2dc69bb` alongside full session ID `f2dc69bb-e448-407d-8c7b-b48f7761675a`. Passing the short handle to `nyxloom-harness extract f2dc69bb --format claude-code --preset watch` was rejected as an unrecognized session reference, although its unique full UUID was locally discoverable.

## Why Nyxloom owns it

`nyxloom-harness extract` advertises bare session-ID resolution and owns the shared resolver used by the extract command family. Callers often receive a short daemon/job handle while the transcript store contains the full UUID. Requiring a separate manual mapping step makes the documented local-discovery path less useful.

## Proposed contract

Extend the shared Claude Code/Codex UUID resolver to accept a case-insensitive prefix of at least eight hexadecimal digits from a canonical UUID's compact spelling. Preserve existing full-ID and existing-path behavior. Resolve a prefix only when the full scan across the applicable Claude Code and Codex stores yields exactly one physical session. If multiple sessions match, fail with an ambiguity error that lists the full IDs and source paths; never prefer the current project, newest session, first traversal result, or one client silently. If no session matches, keep the current fail-closed not-found behavior. Preserve indeterminate-scan errors. Do not apply prefix matching to OpenCode, Reasonix, or Claude sub-agent IDs unless a separately justified identifier contract is added.

## Oracles

- Given one Claude Code or Codex session whose UUID begins with an eight-hex prefix, extracting with that prefix resolves the same source as the full UUID.
- Given two distinct matching UUIDs anywhere in the scanned Claude/Codex roots, the short prefix is rejected and the diagnostic identifies both candidates; no candidate is selected.
- A prefix with no match and a UUID fragment shorter than eight hex digits are refused; an existing path and a complete UUID continue to resolve as before.
- Exercise the shared resolver through `extract` and confirm the other extract commands that call it inherit the same result.

## SPEC ownership

`session_extract/locate.py` owns the resolver contract. If implemented, update `docs/DESIGN-GUIDE.md`'s “Session extraction selection and boundaries” rationale, add a pasteable short-ID example and ambiguity behavior to `docs/CONSUMERS.md`, and link the user-facing capability from `README.md`; keep the CLI reference and command help in sync.

## Updates
