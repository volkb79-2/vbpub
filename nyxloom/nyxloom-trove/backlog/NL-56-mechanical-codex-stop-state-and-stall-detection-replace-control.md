---
kind: backlog-entry
schema_version: 1
id: NL-56
title: "Mechanical Codex stop-state and stall detection replace controller hand-parsing"
status: open
type: "feature"
severity: "medium"
component: "session-extract"
context_estimate: "medium"
provenance: "operator task 2026-11-10; real codex/runs and matching review-rg-flake rollout"
filed_by: "Codex deepseek-v4.1-flash"
spec_owner: "src/nyxloom/session_extract/README.md; docs/CLI-REFERENCE.md"
filed_date: "2026-11-10"
---

## Observed mechanism and reproduction

`nyxloom-harness extract --stop-state` accepted Claude Code transcripts but
refused Codex with an unsupported-format error. The release controller therefore
hand-parsed `codex exec --json` streams in `codex/job-check.sh` to discover
intent-only endings, serialized tool calls, empty runs, and endings while a
gate or process was still in flight.

Real controller runs from 2026-11-09/10 reproduce the failure modes:
`review-rg-flake` ends by announcing the next lane run without its outcome,
`hc4` announces the hand-mutation round, `session-audit-6` announces checks
without producing them, and `nyx-stall` records a terminal usage-limit error.
`hc4-fin` is the completed negative case. The durable rollout carrying
`review-rg-flake`'s thread id uses a different schema from the exec stream.

## Why nyxloom owns it

Session extraction already owns the mechanical record parsers and their
versioned CLI. A controller-local shell heuristic duplicates adapter knowledge,
does not share tests, and cannot consume the rollout form. A closed verdict
vocabulary also makes the controller's action decision reviewable instead of
depending on ad hoc grep output.

## Proposed contract

- Codex rollout files and `codex exec --json` streams both feed the same
  stop-state facts. The exec format is explicitly registered as `codex-exec`
  and auto-detected from `thread.started` / `item.*` records.
- Stop state reports completed-message, interrupted, killed-mid-call, or empty
  causes, with bounded last assistant prose and an unresolved call when one
  exists.
- `extract --stall-check` prints one closed run verdict and exits 0 only for
  `ok`; text and versioned JSON expose the same fields.
- An optional expected report uses `report-missing` / `report-skeleton` when
  the path is absent, below 1,500 bytes, or has more than three pending
  markers. Both thresholds are explicit CLI parameters.
- Detection uses records and bounded final-prose lexical checks only: no model
  call, wall-clock threshold, host-load signal, or process-mtime proxy.

## Oracles

- Tests use small trimmed fixtures from the real runs above plus the matching
  `review-rg-flake` rollout.
- Each closed verdict has a positive fixture; `hc4-fin` is the completed
  negative case.
- Report missing, byte-floor, pending-marker ceiling, and CLI usage failures
  are exercised independently.
- Hand mutations kill each new classifier/parser guard; any survivor gets a
  distinguishing regression test before closure.
- The full `nyxloom` tester-unified lane runs on the clean final head after a
  fresh `origin/main` merge.

## SPEC ownership

`src/nyxloom/session_extract/{adapters/codex_exec.py,stopstate.py,stall.py}`,
`src/nyxloom/cli.py`, `src/nyxloom/cli_registry.py`, their tests and real-run
fixtures, plus the session extraction README, design guide, consumer guide,
and canonical CLI reference.

## Updates
