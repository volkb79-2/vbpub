---
kind: backlog-entry
schema_version: 1
id: NL-44
title: "Onboarding should offer safe config upgrades beside existing project values"
status: open
type: "feature"
severity: "medium"
component: "onboarding"
context_estimate: "medium"
provenance: "operator session, netcup-api-filter CLI use, 2026-10-15"
filed_by: "operator request via Codex"
spec_owner: "src/nyxloom/onboarding.py; docs/nyxloom-operating-model.md"
filed_date: "2026-10-15"
---

## Observed mechanism and reproduction

`nyxloom init` scaffolds a new trove and refuses to replace an existing one. `nyxloom onboard` is idempotent for existing content: it creates missing spine documents, appends missing `[project]` spine keys, and leaves any existing key value untouched. It does not compare an existing `nyxloom.toml` with the currently shipped defaults/schema or offer a guided upgrade.

A project can therefore keep valid but old settings and an older config shape without seeing what changed. The user expectation is that an onboarding/upgrade run shows the existing value next to the current shipped default and lets the user decide whether to retain or adopt the new value.

## Why nyxloom owns it

`init`, `onboard`, and the shipped config schema/template are Nyxloom's local adoption interface. A safe upgrade path belongs with those workflows, not in each consumer project.

## Proposed contract

Provide an explicit config review/upgrade path during onboarding or as a separate local command. For each supported setting it proposes to change, display the current value and the current shipped default side by side. Preserve existing custom values by default; only apply a default or migration after an explicit user choice. Show additions, removals, renames, and unsupported legacy keys from the current shipped schema, and identify the source config file. Support a preview that writes nothing. Applying choices edits only the selected keys and preserves comments and unrelated formatting; a rerun with no new choices is idempotent. Greenfield `init` remains separate from upgrading an existing trove.

Secrets or credential-like values must be redacted in review output. A missing value may use the shipped default only where the schema declares that default authoritative; no guessed substitute values.

## Oracles

- A fixture with custom current values displays both current and shipped values and leaves the file unchanged in preview mode.
- Choosing to retain a custom value preserves its bytes; choosing a shipped value changes only the selected key.
- Added, renamed, retired, and invalid keys are reported against the shipped schema, with no silent deletion or overwrite.
- Repeating an applied upgrade makes no additional changes.
- README, DESIGN-GUIDE, CONSUMERS, CLI reference, and onboarding/configuration guidance show a pasteable upgrade example and state the preservation rules.

## SPEC ownership

`src/nyxloom/onboarding.py`; `_INIT_NYXLOOM_TOML`; `schemas/nyxloom-config.schema.json`; `docs/nyxloom-operating-model.md`; user-facing adoption docs.

## Updates
