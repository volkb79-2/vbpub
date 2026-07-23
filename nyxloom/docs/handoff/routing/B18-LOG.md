# B18 — model capability catalog (D-R13) — implementation log

Worktree: `/workspaces/vbpub/.worktrees/nyxloom-B18-capability-map/nyxloom`
Branch: `feat/nyxloom-B18-capability-map` (based on main `7c7b6562`)

## Context read (in order, per handoff)

1. `docs/routing-model-redesign.md:253-296` (D-R13) and `:115-143` (D-R5).
2. `src/nyxloom/benchmark_sources.py` — `BenchmarkRecord`, `fetch_all`.
3. `src/nyxloom/band_thresholds.py` — `assign_band`/`assign_bands`/`AxisThresholds`/`DEFAULT_CUTOFFS`.
4. `src/nyxloom/free_models.py:102-153` (`DiscoveredModel`) and `:359-475`
   (managed-block writer: `_MANAGED_BEGIN`/`_MANAGED_END`, `_strip_managed_block`,
   `_locate_table`, `_render_managed_block`, `write_routes_toml`).
5. `src/nyxloom/config.py:447-500` — confirmed `RouteDef` has no capability
   fields and `Routes.load` only reads `data.get("tiers")`/`data.get("routes")`
   — read only, not edited.
6. `src/nyxloom/paths.py` — `paths.routes_path()`.
7. `tests/test_free_models.py` (writer-idempotence tests, `SAMPLE_ROUTES`
   fixture), `tests/test_benchmark_sources.py` (HTTP-mock test style),
   `tests/test_band_thresholds.py` (banding assertions style).

## What I built

`src/nyxloom/capability_map.py`:
- `AXES = ("intelligence", "coding", "agentic")` — the fixed vocabulary.
- `CapabilityRecord` (frozen dataclass) — model_id/source/scores/price_*/
  context_length/bands/may_review/may_carve/raw, per contract.
- `CapabilityMapConfig` — own `[capability_map]` routes.toml loader
  (`default()`/`from_dict()`/`load()`), mirroring
  `benchmark_sources.BenchmarkConfig.load` (reads via `tomllib` straight
  from `paths.routes_path()`, never through `config.py`). Fields:
  `role_gating` ("operator" default), per-axis `cutoffs` (falls back to
  `band_thresholds.DEFAULT_CUTOFFS`), `review_min_band`/`carve_min_band`
  (defaults 2/3), `review_grants`/`carve_grants`, `min_context`,
  `required_flags`. `thresholds()` builds an `AxisThresholds` from
  `cutoffs`.
- `assemble_catalog(records, cfg)` — bands every axis via
  `band_thresholds.assign_bands`, fills any axis missing from a record's
  `scores` to band 0 (unrated) so `bands` always carries all three axes,
  applies hard filters (EXCLUDE semantics — see decision below), then
  resolves `may_review`/`may_carve` per `role_gating`.
- `write_capability_catalog(path, records)` — mirrors
  `free_models.write_routes_toml`'s strip-then-append discipline with this
  module's OWN markers (`nyxloom-capability-map: BEGIN/END`), rendering
  `[[capability_catalog.records]]` array-of-tables entries (or a bare
  `records = []` under `[capability_catalog]` when empty).
- `refresh_catalog(cfg=None, sources=None)` — `benchmark_sources.fetch_all`
  + `assemble_catalog`, mirroring `free_models.refresh`'s `cfg = cfg or
  Config.load()` default pattern.

`tests/test_capability_map.py` — 22 tests, in-memory `BenchmarkRecord`
inputs, `tmp_path` for all file I/O, no network (source calls are mocked
via `patch("nyxloom.capability_map.benchmark_sources.fetch_all", ...)`).

## D-B18-1 shape decisions (and one deviation worth flagging)

- **Hard filters = EXCLUDE, not flag.** A record failing `min_context` or
  `required_flags` never appears in the returned catalog list at all. This
  reads as the more faithful rendering of D-R13's own language
  ("hard-exclude a route... regardless of score"), and O3 phrases the
  assertion as "does not appear as review/carve-eligible" — exclusion
  trivially satisfies that. Recorded here since the contract explicitly
  allowed either choice.
- **`raw` is persisted as a `raw_json` JSON string field**, not a nested
  TOML table. An arbitrary source-native `raw` dict (from
  `benchmark_sources`, ultimately hand-configured `field_map` output) can
  contain values TOML cannot represent directly (e.g. `None`) or deep
  nesting that risks table-header collisions with the rest of the file.
  Serializing it as one JSON string keeps `write_capability_catalog`
  correct for ANY `raw` shape a source can produce, at the cost of the
  on-disk `raw` not being a native TOML table (the in-memory
  `CapabilityRecord.raw` keeps the real dict for the dashboard, B19,
  unaffected). O6 only requires model_id/bands/may_review to round-trip,
  which they do natively; `raw` round-trips too, via `json.loads(row
  ["raw_json"])`, verified in the O6 test.
- **TOML string escaping reuses `json.dumps`.** JSON and TOML basic-string
  escaping rules coincide for every character this module emits
  (backslash, double-quote, control chars, `\uXXXX`) — documented in
  `_toml_str`'s docstring rather than hand-rolling a second escaper.

## Bug found and fixed during self-testing

The first writer draft accumulated an extra blank line on every
write/strip/rewrite cycle: `_strip_managed_block` leaves the blank
separator line (that `write_capability_catalog` itself appends before the
managed block) in place, and the next write appended ANOTHER blank line
on top — breaking the O4 byte-identical-idempotence contract on the
*second* write (caught by `test_writing_twice_is_byte_identical` and
`test_empty_catalog_writes_valid_idempotent_block` both failing on first
run). Fixed by trimming trailing blank lines from `lines` right after
`_strip_managed_block`, before appending the fresh separator + block. Left
a comment at the fix site explaining why.

## Coverage

Ran locally with `pytest-cov` (not part of the required verification per
the handoff, but a useful sanity check before commit):

```
python -m pytest tests/test_capability_map.py -q --cov=nyxloom.capability_map --cov-branch --cov-report=term-missing
```

Result: 136 statements, 36 branches — 100% line, 100% branch, 0 missing,
after adding two targeted tests to close gaps the first pass missed
(the "source lacks a trailing newline" writer branch, and the "price/
context fields are `None` so omitted, not emitted as null" branches).

## Blockers

None. No file outside `scope.touch` was needed for any oracle.
