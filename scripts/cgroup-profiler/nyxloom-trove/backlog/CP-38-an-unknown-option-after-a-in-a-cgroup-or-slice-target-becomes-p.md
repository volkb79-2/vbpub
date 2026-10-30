---
kind: backlog-entry
schema_version: 1
id: CP-38
title: "An unknown option after '@' in a cgroup:/slice:/bare target becomes part of the name and fails late (since the last-@ grammar)"
status: open
type: "bugfix"
severity: "low"
component: "targets"
context_estimate: "small"
provenance: "Sonnet review of 753d2fa6b, 2026-10-27"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler targets"
priority: 3
filed_date: "2026-10-27"
---

## Observed

Since 753d2fa6b the option suffix is recognised only when every key is in `OPTION_KEYS` (so `user@1000.service` survives). A typo
therefore no longer vanishes silently but is read as part of the name: `cgroup:/a@follo` targets the cgroup `/a@follo`;
`slice:dev.slice@follo` resolves to `/dev.slice@follo.slice`; `@Follow` (keys are case-sensitive) likewise. `pid:` and
`container:` targets fail with a message that quotes the typo (acceptable). The failure for `cgroup:`/`slice:` depends on how the
collector reports a missing cgroup.

## Required contract

`slice:` rejects `@` in the unit name at parse time (a slice unit name cannot contain `@`); for `cgroup:` and bare targets, when a
target resolves to a nonexistent cgroup and its last `@`-segment looks like an option (`word` or `word=value`), the error names
that segment as a probable misspelt option and mentions the trailing-`@` escape.

## Oracles

Parse/resolve tests for the three inputs above produce an error naming the suspect segment; `user@1000.service` still resolves.
