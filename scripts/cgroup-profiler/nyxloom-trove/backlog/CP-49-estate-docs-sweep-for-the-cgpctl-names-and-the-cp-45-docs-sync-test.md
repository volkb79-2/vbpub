---
kind: backlog-entry
schema_version: 1
id: CP-49
title: "Estate docs sweep for the cgpctl names, plus the CP-45 README overview and docs-sync test (plan package 5)"
status: open
type: "docs"
component: "docs"
context_estimate: "small"
provenance: "Operator acceptance 2026-11-04 of the cgpctl plan defaults (CGPCTL-PLAN-2026-11-04 sections 1-4, Q1-Q11); contract in docs/CGPCTL-SPEC.md, docs/PROTOCOL.md section 10, docs/NATIVE-DEPLOYMENT.md (package 0)"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler docs"
priority: 3
filed_date: "2026-11-04"
---

## Problem

After package 2 and 3, the name `cgprofile` as a command and `docker exec cgprofile-host-daemon cgprofile ctl ...` are gone. Many documents name them.

## Design

Branch `cgp2-5-docs-20261104`, after CP-47 and CP-48. The checklist (live documents only; historical reports, logs and dated review files are not
edited) is the "DOCS SWEEP checklist" in `docs/CONSUMERS.md`, produced by `git grep` in package 0; re-run the grep first because it will have moved.

- cgprofile itself: README (rewrite per CP-45), ATTACH-GUIDE, DESIGN, DESIGN-GUIDE, CONSUMERS (delete the exec-carrier text, point the deploy chapter
  at NATIVE-DEPLOYMENT), PROTOCOL sections 1 and 4 (make contract 2 the primary description), `.claude/skills` moved into the package, CHANGES.
- Other tools: run-gate README, CONSUMERS, SPEC (with the run-gate socket-only package), mdt-cockpit docs and host-setup docs (with MDT-048 and
  MDT-055), `docs/design/RESOURCE-PROFILES.md`, `docs/BOOTSTRAP-FROM-ZERO.md`, `scripts/LANE-MATRIX-PROGRESS.md`, debian-install-v2 USAGE-TESTING and
  DESIGN-GUIDE, the netcup backlog and RUNBOOK, ciu SPEC-V8 mentions, devcontainer templates comments, cmru `run-gate.toml` and its bootstrap test names.
  Edits outside cgprofile land in the owning tool packages or in one coordinated docs commit per repo area; the owner is named per row.
- CP-45: the README product overview and its docs-sync test (socket paths, group names and verb names in the README match PROTOCOL.md and the
  `cgpctl --help` surface).

## Acceptance criteria

- `git grep -nE "cgprofile ctl|docker exec cgprofile-host-daemon"` outside historical files is empty.
- The docs-sync test fails when a documented verb, socket path or group name drifts.

## Dependencies and order

After CP-47 and CP-48; before the release (plan package 6).
