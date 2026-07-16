# P88 — Unified bounded frame query core — REPORT

**Date:** 2026-07-15
**Branch:** `feat/topos-p88-unified-frame-query-core` (worktree
`/workspaces/vbpub/.worktrees/topos-p88-unified-frame-query-core`, based on
`main` @ `bf74607`)
**Status:** Complete. All seven Required Contracts implemented, all Acceptance
Oracles covered, focused + zero-skip full-suite gates green. No BLOCKED trigger
fired.

## What was built

A new package `topos.query` — one bounded aggregation engine over a single typed
`FrameSource` boundary, plus a `topos query` JSON CLI. No consumer re-aggregates:
the engine is the single source of truth CLI/TUI/HTTP/MCP will call.

| File | Role |
|------|------|
| `topos/src/topos/query/errors.py` | Typed, bounded, coded errors (`QueryError` ⊃ `UnknownFieldError`, `InvalidQueryError`, `IncompatibleQueryError`, `BoundExceededError`). All subclass `ValueError` so the CLI's existing bounded-error handling maps them to exit 2. |
| `topos/src/topos/query/source.py` | The `FrameSource` boundary (Contract 1). `SourceFrame(seq, frame, gap_before)`, `SourceProvenance`, and two adapters: `RecordingFrameSource` (over `RecordReader`) and `DaemonHistoryFrameSource` (over P63 `DaemonHistoryResult`, via `from_history_result`). Adapters preserve timestamps/sequence/provenance/eviction/gap and never aggregate. |
| `topos/src/topos/query/semantics.py` | Value semantics (Contract 3). Canonical classifier + compatibility, and reset-aware reducers for `gauge`/`rate`/`counter_delta`/`integral`/`event_count`/`state_duration`. Percentile math is **reused** from `report._nearest_rank_percentile`; rate derivation mirrors `report._derive_rate` and only *adds* reset counting. |
| `topos/src/topos/query/engine.py` | The strict `Query` object (Contract 2), registry-driven subtree aggregation (Contract 5), projection/sort, bounds enforcement (Contract 6), full result metadata (Contract 4), and `run_query`. |
| `topos/src/topos/query/__init__.py` | Public API. |
| `topos/src/topos/cli.py` | `topos query FILE …` subcommand (Contract 7), following the `topos report` exemplar. |
| `topos/tests/test_query.py` | 62 tests: every numbered acceptance oracle + contract. |

## Contract-by-contract

1. **One typed `FrameSource`.** Both adapters yield the canonical `Frame` their
   existing reader already produces (`RecordReader`, `DaemonClient.request_history`),
   so **neither the P2 nor the P52 wire format changes** — the boundary is an
   additive wrapper. Sequence, `gap_before`, provenance and eviction are carried
   through; adapters never aggregate.
2. **Strict query object.** `Query` covers shape (`current`/`raw`/`summary`),
   window, entity selector, projection, visibility, metric+semantic, sort and
   row/point/byte caps. `Query.from_dict` rejects unknown fields (top-level and
   nested); incompatible combinations are typed (`raw`+`hierarchy`, `raw`+sort,
   sort metric not selected, current-shape summary stat, duplicate metric, a
   stat not sortable for a semantic).
3. **Value semantics, reused math.** Every summarized value declares one of the
   six semantics. Canonical: rate-suffix / `cpu_pct` → `rate`, everything else →
   `gauge`. A caller may request a compatible non-canonical reading
   (`ram:integral`, `io_r_bps:counter_delta`); an incompatible one is a typed
   error. Gauge percentiles use `report._nearest_rank_percentile` verbatim; rate
   sampling is reset-aware and byte-identical to `topos report`'s figures (see
   O11/O14).
4. **Full result metadata.** `meta` always carries requested_window,
   observed_start/end, sample_count, coverage (frames/span/gap_count/complete),
   gaps, eviction, resets, source, freshness (newest/oldest ts) and truncation.
   `complete` is honestly `false` whenever a gap or eviction is present; an empty
   window is a valid result (rows `[]`, not an error).
5. **Explicit projection, registry aggregation, never assumed additive.**
   `flat` = a global rank carrying an ownership `path`; `hierarchy` = sibling-local
   DFS order (children ranked only within their real parent). Subtree totals
   follow `registry.branch_policy`: `kernel_subtree` → the node's own value (the
   kernel already folded in the subtree — **not** the child sum);
   `child_sum` → additive; `local_only` → own value. The hierarchy row exposes
   `subtree.{policy, additive, value}` so the semantics are observable.
6. **Bounds before materialization.** Order: pull+window → select → rank →
   enforce row/point caps → build → enforce encoded-byte cap. Oversized either
   returns a typed `BoundExceededError` (default `on_exceed=error`) or degrades by
   an explicit `truncate` policy that records `truncation.{reason, dropped_*}`.
   The byte cap never returns an oversize body (binary-searches the largest row
   prefix that fits); a cap below the empty-rows meta floor is a typed error, not
   a partial lie. Raw's point cap is checked on the upper bound before any series
   is built.
7. **`topos query` JSON surface + byte-identity.** `topos query FILE --shape …
   --metric … --json`. A recording fixture and a daemon fixture over the **same
   frames** produce byte-identical payloads apart from `meta.source` — proven for
   all three shapes (O/C7). Absolute source sequence numbers are kept internal
   (recording numbers 0..N-1, daemon uses ring seq); gaps/eviction are reported
   structurally so identity holds.

## Acceptance oracles → tests (all in `topos/tests/test_query.py`)

| # | Oracle | Test(s) |
|---|--------|---------|
| O1 | gauge mean/p95/max (nearest-rank) | `TestGaugeSummary` |
| O2 | reset-aware rate summary | `TestRateSummary::test_rate_is_reset_aware` |
| O3 | counter delta | `TestCounterAndIntegral::test_counter_delta_*` |
| O4 | integral | `TestCounterAndIntegral::test_integral_of_gauge_is_trapezoidal` |
| O5 | gapped / evicted windows | `TestWindowsAndCoverage::test_temporal_gap_*`, `_daemon_eviction_*`, `_daemon_sequence_gap_*` |
| O6 | empty windows | `TestWindowsAndCoverage::test_*empty*` |
| O7 | hierarchy-vs-flat sort | `TestProjection` |
| O8 | selector misses | `TestSelector::test_selector_miss_is_empty_not_error` |
| O9 | hard bounds (row/point/byte, error+truncate) | `TestHardBounds` (7 tests, each violates a bound) |
| O10 | byte determinism (2 invocations) | `TestDeterminism` (incl. real CLI subprocess) |
| O11 | differential vs P54 report figures | `TestDifferentialAgainstReport` (cold + warm) |
| O12 | mutation tests on gap/reset metadata | `TestMutationMetadata` |
| O13 | large synthetic tree performance | `TestPerformance::test_large_tree_within_budget` |
| O14 | P70 adversarial near-CoV-boundary suffix | `TestP70AdversarialSuffix` |

O12 is a genuine mutation test: it monkeypatches `semantics._rate_samples` to a
reset-blind version and asserts the result *changes* (resets→0, a bogus negative
spike appears) — the metadata propagation is load-bearing, so breaking it turns
the test red.

## Gate results (environment: agent worktree venv, Python 3.14.6, `topos[dev]`)

Focused P88:
```
$ PYTHONPATH=topos/src .venv/bin/python -m pytest topos/tests/test_query.py -q -W error -p no:schemathesis
62 passed in 2.49s
```

Focused query + report + daemon (P63/P52):
```
$ PYTHONPATH=topos/src .venv/bin/python -m pytest topos/tests/test_query.py topos/tests/test_report.py topos/tests/test_daemon_client_p63.py topos/tests/test_daemon_p52.py -q -W error -p no:schemathesis
262 passed in ~33s
```

Full suite (zero-skip P84 gate), timeout-wrapped:
```
$ timeout 900 env PYTHONPATH=topos/src .venv/bin/python -m pytest topos/tests -q -W error -p no:schemathesis
1513 passed in 182.17s (0:03:02)
```
Zero skips — the P84 session gate is satisfied (a skip would have failed the run).

Compile + hygiene:
```
$ .venv/bin/python -m py_compile topos/src/topos/query/*.py topos/src/topos/cli.py topos/tests/test_query.py   # clean
$ git diff --check   # clean (no output)
```

## Recorded measurements (agent worktree venv, Python 3.14.6)

- **Performance budget (O13):** 2008 entities (8 slices × 250 children) × 30
  frames, `summary` of 2 metrics, `hierarchy` projection, sort `ram:p95:desc`:
  **wall 0.27–0.34 s** across runs; well under the 10 s test budget.
- **Encoded size:** that result serializes to **819 594 bytes** (~800 KiB) —
  under the 4 MiB default byte cap; deterministic across runs.
- **Byte-identity:** recording vs daemon fixtures over identical frames produce
  byte-identical `summary`/`current`/`raw` payloads apart from `meta.source`.

## Deviations / decisions

- **Reused `report._nearest_rank_percentile` (underscore-private) by import**
  rather than duplicating or promoting it. `test_report.py` imports that symbol
  by name, so moving it would break P54's tests; importing it is the intended
  "reuse, do not duplicate." No change to `report.py`'s public surface, no
  `CONTRACTS.md` change proposed.
- **Absolute sequence numbers are internal**, not emitted. Required for the
  Contract-7 byte-identity (recording 0..N-1 vs daemon ring seq). Sequence still
  drives gap/eviction detection; gaps are reported by observed position + ts.
- **Canonical semantic = gauge for bounded percentages** (PSI, headroom,
  `cpu_throttled_pct`, `io_cap_saturation_pct`). They are summarized by
  percentile like any gauge; only `_per_s`/`_bps`/`_pps`/`_iops`/`cpu_pct` are
  canonically `rate`. Documented in `semantics.py`.
- **`topos query` reads a recording only.** The daemon `FrameSource` is exercised
  through the Python API (and the byte-identity oracle). Automatic source
  selection is explicitly P89's scope; a live-daemon CLI path would pre-empt it.
- **Freshness = newest/oldest observed ts**, not wall-clock age. Keeps output
  deterministic (O10); the age-relative-to-now display is a render concern for
  P65.

## Proposed contract changes

None to `CONTRACTS.md`. The engine is additive, package-private code.

## Known gaps / follow-ups (also appended to `docs/BACKLOG.md`)

- Ranking a **large** tree by a `child_sum` metric (`net_*`) recomputes subtree
  sums per node → worst-case super-linear. The common ranking metrics
  (`ram`/`psi`/`cpu`) are `kernel_subtree`/`local_only` → O(1) per node, so the
  measured budget is linear; a memoized subtree pass would harden the rare
  `child_sum`-on-huge-tree case.
- Slice-grain rollup (`group_by slice`) is not offered by `topos query`; the
  hierarchy projection already surfaces slice structure, and `topos report`
  retains its slice rollup. A future consumer wanting flat slice sums can add it
  on the same subtree-aggregation primitive.
