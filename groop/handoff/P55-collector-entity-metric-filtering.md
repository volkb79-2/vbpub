# P55 - Collector-Level Entity & Metric Filtering

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** none
> **Base:** main
> **Session-hint:** fresh
> **Escalate-if:** a named contract cannot be met as specified; >2 files outside scope needed

## Goal

Add `--entities GLOB` (repeatable) / `--slice NAME` entity-subtree selectors and
a `--metrics compact` gauge-family selector to the collector itself, so a run
scoped to one tier/subtree does not pay for reading or emitting the rest of
the cgroup tree. Filtering happens at collection time (skipping cgroup reads
for excluded entities), not just at output-serialization time.

## Independence

This package is implementable and independently useful **today**, on
`--once` alone — it does not require P53 (headless record driver) or P54
(steady-state report), and does not require either of them to land first.
The filtering lives in `Collector`/`walk_entities`, which every existing
frame-producing path (`--once`, `--once --json`, TUI-driven `--record`, the
live TUI, `--replay`, `--attach`) already shares by construction (all
`Collector(...)` call sites in `src/groop/cli.py` — currently lines 376, 400,
and 416 — go through `main()`'s single top-level `parse_args()`). Because of
that shared plumbing, once P55 lands, `groop --record FILE --headless` (P53,
whenever it merges) inherits `--entities`/`--slice`/`--metrics` for free with
no P53-side code change — P55 does not need to touch, wait for, or coordinate
with P53's branch.

## Origin note

This requirement was originally sketched inside P53's "Amendment
2026-07-10" section (`handoff/P53-headless-record-driver.md`) as one of three
size-mitigation ideas for long unattended recordings. It is extracted here
into its own package specifically so P53 and P55 can be built in parallel by
different agents without a shared-file dependency between their diffs. Add a
short pointer in P53's amendment text ("entity/metric filtering now specified
separately in P55") — do not otherwise rewrite P53's amendment.

## Workflow

- Branch: `feat/groop-p55-collector-entity-metric-filtering`
- Worktree: `.worktrees/-groop-p55-collector-entity-metric-filtering`
- Touch only `groop/**`; write P55-LOG.md/P55-REPORT.md; commit, do not merge.

## Requirements

- Add `--entities GLOB` to the top-level parser in `parse_args()`
  (`src/groop/cli.py`), `action="append"`, default `None` (unset = no entity
  filtering). Glob syntax matches against the cgroup-relative `EntityKey`
  path (e.g. `'besteffort.slice/*'`, `'*.slice/docker-*.scope'`), using
  `fnmatch`/`fnmatchcase` semantics consistent with existing glob use
  elsewhere in the codebase (audit `src/groop` for an existing glob helper
  before adding a second one). Repeating the flag is a union of matches (an
  entity is included if it matches *any* given glob).
- Add `--slice NAME` as a subtree selector: shorthand for "include this
  `*.slice` (or other) entity key and everything under it." Composable with
  `--entities` (union of both selector sets). Reject `--slice` values that do
  not correspond to a plausible cgroup path segment (same validation rigor as
  existing target validators, e.g. `src/groop/actions/catalog.py
  validate_target` or `src/groop/inspect_files` target checks — cite and
  reuse existing validation helpers rather than writing a third one if a
  shared one already fits).
- Add `--metrics compact`, a closed enum (`full` default, `compact`
  explicit) rather than a free-form list in v1. `compact` keeps only: the
  memory gauge family (`ram`, `anon`, `file`, `shmem`, `sock`, `z_pool`,
  `z_eq`, `swap_disk`), the PSI family (`psi_mem_some_avg10`,
  `psi_mem_full_avg10`, `psi_io_some_avg10`, `psi_io_full_avg10`,
  `psi_cpu_some_avg10`, `psi_cpu_full_avg10`), and the refault-rate family
  (`rf_z_per_s`, `rf_d_per_s`, `rf_f_per_s`). It drops the network family
  (`net_rx_bps`, `net_tx_bps`, `net_rx_pps`, `net_tx_pps`), DAMON blocks
  (`frame.damon`, `host_damon_*`), and the governance-drift block
  (`governance_drift`, `effective_memory_min`, `frame.governance`). Use
  `src/groop/registry.py`'s existing metric-name/group metadata as the
  source of truth for which metric belongs to which family instead of
  hand-listing metric names in a second place, if the registry already
  encodes grouping; if it does not, add the minimal grouping data needed
  there rather than inline in `cli.py`.
- **Collection-time filtering, not just output pruning:** modify
  `walk_entities()` (`src/groop/collect/cgroup.py`) — or add a filtering
  wrapper the `Collector` calls in its place — to prune `os.walk`'s
  `dirnames` in-place for excluded subtrees, so `collect_cgroup()` is never
  invoked (no sysfs reads: `memory.stat`, `memory.pressure`, `io.stat`, etc.)
  for entities outside the selected scope. This is the actual point of the
  package per the P53 amendment's numbers below — output-only pruning would
  keep paying the sysfs-read cost per sweep.
- **Ancestor resolution:** governance/drift math (`effective_memory_min`,
  origin/drift classification in `src/groop/drift/origin.py`) and slice
  rollups depend on walking up `parent_key()` chains. When `--entities`/
  `--slice` narrows the entity set, always additionally include every
  ancestor of each matched entity (root through the matched entity's parent
  chain, via the existing `parent_key()` helper in
  `src/groop/collect/cgroup.py`), even though those ancestors themselves did
  not match the glob/slice. Document in this handoff and in code comments
  that ancestor entities appear in output for path-completeness (tree
  rendering, effective-min clamping) even under `--metrics compact`, and are
  not "extra" matches a caller needs to filter out again downstream. When
  `--metrics compact` also applies, ancestors get the same compact metric
  set as matched entities — no separate "ancestor-only" metric shape.
- `--metrics compact` applies independent of whether `--entities`/`--slice`
  is given (a caller can compact metrics on the full unfiltered tree, or
  filter entities on the full metric set, or both together).
- These flags apply uniformly to `--once`, `--once --json`, `--record` (both
  TUI-driven today and headless once P53 lands — no P55-side branching by
  record mode needed since both paths share `Collector`), and the live TUI.
  They are rejected (exit 2, clear message) in combination with `--replay`
  and `--attach`, matching the existing pattern of flag-combination checks
  already in `main()` (e.g. `--attach does not accept --cgroup-root`) —
  filtering a recorded/replayed or daemon-side frame stream is out of scope
  here (a recording made by an unfiltered collector cannot be filtered after
  the fact by this package; that is a `report`/reader-side concern, see
  P54's "filtered recordings" amendment).
- Motivation, cited from `handoff/P53-headless-record-driver.md`'s "Amendment
  2026-07-10": a full frame is **~447 KB** across **89 entities** with the
  full metric set (pretty-printed one-shot). A `besteffort.slice`-scoped,
  `--metrics compact` frame is a small fraction of that — both from fewer
  entities and from fewer metric families per entity — which matters most
  for `--record`'s per-day volume (P53's amendment: ~3.9 GB/day at 10 s
  cadence uncompressed, unfiltered) but is a first-class benefit for `--once`
  today too: fewer sysfs reads per invocation, smaller JSON to parse/render.
- Add tests: glob matching against representative `EntityKey` fixtures
  (including no-match, single-match, multi-match, and root-key `""` edge
  cases), `--slice` subtree inclusion, ancestor auto-inclusion correctness
  (a matched deep entity pulls in every ancestor, not siblings), `--metrics
  compact` field-set correctness (assert the exact kept/dropped metric
  names), collection-time pruning (assert `collect_cgroup`/sysfs-reading
  helpers are not called for excluded entities — use an injected/counted
  reader, not a live sysfs assumption), combination with `--replay`/
  `--attach` rejected with exit 2, and `--entities`/`--slice`/`--metrics`
  applied to `--record` output (fixture cgroup tree, assert written frames
  only contain the selected entities/metrics).
- Update `README.md` quickstart/CLI docs, `CONTRACTS.md` (recording format
  section, noting filtered recordings are a subset of the existing schema,
  not a new one), and `docs/ROADMAP.md`/`docs/STATUS.md` package entries.
- Amend `handoff/P53-headless-record-driver.md`'s "Amendment 2026-07-10"
  section with a one-line pointer that entity/metric filtering now lives in
  P55 (`handoff/P55-collector-entity-metric-filtering.md`) rather than being
  re-specified there. Do not otherwise rewrite P53.

## Out Of Scope

- Filtering already-recorded/replayed frames after the fact (a `report`
  reader-side concern; see P54's "filtered recordings" amendment, which
  already specifies `groop report` tolerating filtered recordings).
- A free-form `--metrics field1,field2,...` list — v1 ships only the closed
  `full`/`compact` enum; an arbitrary field-list selector is future work.
- Daemon-side (`--attach`) filtering — the daemon's frame broker is a
  separate producer (P16/P51) outside this package's scope; `--entities`/
  `--slice`/`--metrics` are explicitly rejected with `--attach` in v1.
- Any new frame/file schema — a filtered frame is a normal `Frame` with fewer
  `entities`/metric keys populated, using the existing P2 JSONL/zst format
  unchanged.
