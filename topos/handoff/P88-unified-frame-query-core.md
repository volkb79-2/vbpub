# P88 - Unified bounded frame query core

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P54 (merged), P63 (merged), P70 (merged)
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** P64, P65 (report/query surface)
> **Escalate-if:** recordings and daemon frames cannot enter one canonical iterator without changing P2/P52 wire compatibility, or a requested result cannot be bounded before materialization. Do not create a second aggregation engine.

## Goal

Build the single query engine required by D-003 through D-007. It consumes a
bounded canonical frame sequence from a recording or daemon-history adapter and
produces current rows, raw series or window summaries for CLI, TUI, HTTP and MCP.

## Required contracts

1. One typed `FrameSource` boundary for recording and daemon history. Source
   adapters preserve timestamps, sequence, provenance, eviction/gap and reset
   information; they do not aggregate.
2. A strict query object covers window, entity selector, projection, visibility,
   metric/profile, sort, row/point/byte caps and result shape (`current`, `raw`,
   `summary`). Reject unknown fields and incompatible combinations.
3. Reuse/generalize P54/P70 math. Every value declares `gauge`, `rate`,
   `counter_delta`, `integral`, `event_count` or `state_duration` semantics.
4. Every result reports requested window, observed start/end, sample count,
   coverage, gaps/eviction, resets, source, freshness and truncation. Empty is a
   valid result; incomplete is never silently labelled complete.
5. Projection is explicit: hierarchy preserves sibling-local order; global
   ranking is a labelled flat projection. Parent/child totals follow registry
   aggregation semantics and are never assumed additive.
6. Enforce row, point and encoded-byte bounds before returning. Oversized
   requests degrade by an explicit approved projection/truncation policy or
   return a typed bound error—never an unbounded full-frame fallback.
7. Expose a minimal `topos query` JSON surface so the engine is executable and
   independently useful. Daemon and recording fixtures for the same frames must
   produce byte-identical result payloads apart from declared source provenance.

## Acceptance oracles

Cover gauge mean/p95/max, reset-aware rate summary, counter delta, integral,
gapped/evicted windows, empty windows, hierarchy-vs-flat sort, selector misses,
hard bounds and byte determinism. Differential-test existing P54 report cases,
and mutation-test gap/reset metadata. Include a large synthetic tree performance
budget and the P70 adversarial suffix case.

## Out of scope

Automatic source choice (P89), processes (P90), disk persistence (P91), HTTP/UI
(P92/P73/P77), live push, and new collection providers.

## Gates

Focused query/report/daemon tests, dependency-complete zero-skip full suite,
compile checks, `git diff --check`, and recorded performance/encoded-size
measurements. Write P88-LOG.md and P88-REPORT.md.
