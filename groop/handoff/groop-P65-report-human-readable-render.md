---
schema_version: 1
id: groop-P65-report-human-readable-render
project: groop
title: "Human-readable query and report rendering"
tier: flash-high
input_revision: "f77727e"
session: fresh
source: {kind: roadmap, ref: docs/ROADMAP.md}
stack: none
depends_on: []
scope:
  touch: ["groop/**"]
  forbid: []
oracles:
  - id: O1
    observable: "every displayed metric value matches the corresponding P88 JSON value verbatim for both current and summary shapes"
    negative: "a displayed value differs from the P88 JSON value it renders through rounding, recomputation or inference"
    gate: groop-suite
  - id: O2
    observable: "missing, redacted, warming, stale, permission-denied and truncated states each render a distinct ASCII spelling, and zero renders as 0"
    negative: "two distinct typed states render with the same ASCII spelling, or zero renders as a blank/dash instead of 0"
    gate: groop-suite
  - id: O3
    observable: "hierarchy output preserves ancestry/sibling order; a global rank view is explicitly labelled flat"
    negative: "a global rank view is rendered without the flat label, or hierarchy ordering does not preserve ancestry/sibling order"
    gate: groop-suite
  - id: O4
    observable: "output uses deterministic column widths and order, ASCII only, with no ANSI codes or trailing whitespace"
    negative: "output contains ANSI escape codes, trailing whitespace, or non-deterministic widths/order across runs"
    gate: groop-suite
  - id: O5
    observable: "existing --json output remains byte-compatible with its current shape"
    negative: "the --json output changes format or shape from its current contract"
    gate: groop-suite
  - id: O6
    observable: "requesting both --json and a text/table format together exits 2 as a format conflict"
    negative: "requesting both formats together does not exit 2"
    gate: groop-suite
  - id: O7
    observable: "P61 and optional P64 breach outcomes retain their exit codes independently of the text/JSON presentation choice"
    negative: "the P61/P64 exit code changes depending on which output format was requested"
    gate: groop-suite
  - id: O8
    observable: "the renderer performs no collection, selection, aggregation or rounding from raw floats, and no file reads"
    negative: "the renderer recomputes a value, rounds a raw float, or reads a file instead of consuming only the canonical JSONable query result"
    gate: groop-suite
gates: [groop-suite, py-compile]
escalate_if: ["a displayed number cannot be taken verbatim from the P88 result"]
advances: []
---

# P65 - Human-readable query and report rendering

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** flash-high
> **Depends-on:** P88, P61 (merged)
> **Base:** main after P88
> **Session-hint:** fresh
> **Serialize-with:** P64 (shared query/report CLI)
> **Escalate-if:** a displayed number cannot be taken verbatim from the P88 result. Do not recompute or infer missing coverage in the renderer.

## Goal

Add deterministic, pipe-safe text/table output for P88 current/raw/summary query
results and existing report assertions. JSON remains the machine contract.

## Required contracts

- A pure renderer consumes the canonical JSONable query result. It performs no
  collection, selection, aggregation, rounding from raw floats or file reads.
- Tables show projection/scope, source, requested/observed window, samples,
  coverage, freshness, gaps/evictions/resets and truncation before metric rows.
- Values preserve registry unit and semantic. Missing, redacted, warming, stale,
  permission-denied and truncated have distinct ASCII spellings; zero is `0`.
- Hierarchy output preserves ancestry/sibling order; a global rank is explicitly
  labelled flat. Deterministic widths/order, ASCII only, no ANSI or trailing
  whitespace.
- Existing `--json` remains byte-compatible. A format conflict is exit 2; P61
  and optional P64 breach outcomes retain exit 1 independently of presentation.

## Acceptance oracles

Cross-check displayed figures against P88 JSON for current and summary shapes;
cover all typed value states, hierarchy versus flat ordering, gapped/evicted
metadata, assertions, width determinism, no trailing whitespace, JSON regression
and real subprocess exits.

## Out of scope

TUI/web rendering, colors/paging, baseline math (P64), collection and query
aggregation.

## Gates

Focused report/query/CLI tests, zero-skip full suite, compile checks and
`git diff --check`. Write P65-LOG.md/P65-REPORT.md and update operator docs.

## Conversion addendum (handoffctl2 execution notes)

- **Worktree:** create a git worktree for branch `feat/groop-p65-report-human-readable-render`
  at `.worktrees/groop-p65-report-human-readable-render` (repo-root-relative, per
  `worktree_root` in `groop/.handoffctl/project.toml`) from `main`; do all
  implementation work there, never in the primary checkout.
- **Branch:** `feat/groop-p65-report-human-readable-render`
- **Context to read first:** the Goal, Required contracts, Acceptance oracles and Out
  of scope sections above; `docs/ROADMAP.md`; and any handoff listed as this file's
  frontmatter `depends_on`.
- If a named contract cannot be met as specified, STOP, write `BLOCKED: <reason>` to
  the LOG file, commit, and exit. The declared gates (frontmatter `gates:`) are
  mandatory; never bypass them.
