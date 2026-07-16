# P60 - Free-form `--metrics` field/family list selector

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P55 (merged)
> **Base:** main after P55 merge
> **Session-hint:** fresh
> **Serialize-with:** P59 (shared file: `src/topos/cli.py` argument parsing / `_filter_kwargs`)
> **Escalate-if:** a named contract cannot be met as specified; >2 files outside scope needed

## Goal

Generalize P55's closed `--metrics full|compact` enum into an additional
open form: `--metrics FIELD_OR_FAMILY,FIELD_OR_FAMILY,...` selecting an explicit
subset of metric families and/or individual metric names, validated against the
registry. `full` and `compact` remain valid literal values (backward compatible);
anything else is parsed as a comma-separated selector list. This is the
"arbitrary field-list selector is future work" item P55 named out-of-scope
(see P55 handoff "Out Of Scope").

## Context To Read First

- `src/topos/registry.py`: `REGISTRY` (all metric names), and P55's
  `METRIC_GROUPS` / `COMPACT_GROUPS` (family -> metric-name tuples).
- `src/topos/collect/collector.py`: `Collector.__init__` `metrics_mode` handling
  and the compact-prune step in `collect_once()` (both metrics-dict pruning and
  the P55-R1 per-entity network/damon/governance block dropping).
- `src/topos/cli.py`: `--metrics` argparse entry (currently
  `choices=["full","compact"]`) and `_filter_kwargs`.
- Exemplar to imitate: P55's registry-backed grouping + the collector's
  final-step prune. Keep the source of truth in `registry.py`.

## Requirements

1. `--metrics` no longer uses argparse `choices` (which would reject a list);
   instead accept a free-form string, then parse/validate in one helper. `full`
   (default) and `compact` keep their exact P55 meaning. A value containing a
   comma, or any single token that is not `full`/`compact`, is treated as a
   selector list.
2. **Resolution contract:** each comma token is either (a) a family name in
   `METRIC_GROUPS` (expands to that family's metric-name tuple) or (b) an exact
   metric name in `REGISTRY`. The kept set is the union. Unknown tokens are a
   hard error (exit 2, message naming the unknown token(s)) — never silently
   dropped (standing "never silently clamp" contract).
3. The keep-set prunes the per-entity `metrics` dict exactly as compact does.
   **Reuse** the existing prune step: compute a `frozenset[str]` keep-set and
   feed the same code path P55 already has (do not add a second prune loop).
4. **Structured-block contract:** define per family whether the token also keeps
   the structured per-entity block. `net`/`network` family keeps
   `eframe.network`; `damon` keeps `eframe.damon`; `governance` keeps
   `eframe.governance`. Any keep-set that omits a family drops its block (same
   rule P55-R1 established for compact). Document this mapping in one place
   (registry or a small module-level dict), not scattered.
5. Empty selector (e.g. `--metrics ""` or all-unknown) exits 2, does not produce
   an empty-metric frame silently.
6. `--metrics <list>` composes with `--entities`/`--slice` (and `--container` if
   P59 is merged) unchanged; rejected with `--replay`/`--attach` like the P55
   enum.

## Acceptance Oracles / Tests (numbered, adversarial)

1. `--metrics ram,psi_mem_some_avg10` keeps exactly those two names on every
   entity and drops everything else (assert exact kept set; fails if family/name
   union is wrong).
2. `--metrics psi` (family token) expands to all six PSI names — assert the full
   PSI set, not just one — a test that would pass under single-name handling
   must fail.
3. `--metrics net` keeps the net metric names **and** leaves `eframe.network`
   non-None, while `--metrics ram` drops `eframe.network` (proves the
   block-keep mapping, not just dict pruning).
4. `--metrics ram,bogus_metric` exits 2 with `bogus_metric` named in stderr.
5. `--metrics ""` exits 2 (empty selector rejected).
6. `--metrics full` and `--metrics compact` behave byte-identically to P55
   (regression guard: reuse/point at P55's compact field-set assertions).
7. `--metrics ram --replay X` and `--metrics ram --attach S` each exit 2.

## Out Of Scope

- Renaming or restructuring `METRIC_GROUPS`/`COMPACT_GROUPS` (additive only).
- Per-entity *different* metric shapes (all entities share one keep-set, as in
  P55).
- Host-level metric selection (`frame.host` / `host_meta` stay as-is, matching
  P55's compact scope decision recorded in P55-REVIEW).
- Daemon/`--attach` or replay-side metric selection (rejected).

## Docs

Update `README.md` (document the list form beside `full`/`compact`),
`CONTRACTS.md` (recording-format note: a field-list frame is still a valid P2
subset), and `docs/ROADMAP.md`/`docs/STATUS.md` entries.
