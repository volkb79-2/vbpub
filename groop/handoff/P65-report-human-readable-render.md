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
