# P59 - `--container` as an Entity Selector (P55 x P57 composition)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P55 (merged), P57 (merged)
> **Base:** main after P55 + P57 merge
> **Session-hint:** fresh
> **Serialize-with:** P60 (shared file: `src/groop/cli.py` argument parsing / `_filter_kwargs`)
> **Escalate-if:** a named contract cannot be met as specified; >2 files outside scope needed

## Goal

Let `--container NAME_OR_PREFIX` act as a third entity-selector form alongside
P55's `--entities GLOB` / `--slice NAME` on the top-level collection path
(`--once`, live TUI, `--record`). The container name resolves (via P57's
`resolve_container_key()`) to a single matched `EntityKey`, which is then fed
into exactly the same ancestor-inclusion + collection-time-pruning machinery
P55 already implements — no re-implementation of pruning or ancestor logic.

This is the composition bullet P57 explicitly deferred while P55 was unmerged
(see the `.. todo::` block in `_resolve_mutual_exclusive_target` in
`src/groop/cli.py` and P57-REPORT "P55/P56 Composition Notes"). Both are now
merged, so this wiring is unblocked.

## Context To Read First

- `src/groop/cli.py`: the top-level `parse_args()` (`--entities`/`--slice`/
  `--metrics` from P55), `_filter_kwargs()`, and the three `Collector(...)`
  call sites; also the P57 `_resolve_mutual_exclusive_target` TODO block.
- `src/groop/collect/dockerjoin.py`: `resolve_container_key()`,
  `ContainerResolveError` (P57).
- `src/groop/collect/collector.py`: `Collector` filtering params
  (`entities_globs`, `slice_names`) and `collect_once()` entity-filter step.
- `src/groop/collect/cgroup.py`: `build_entity_predicate`, `add_entity_ancestors`.
- Exemplar to imitate: P55's `_filter_kwargs` + collector wiring; P57's
  "resolve-first, then hand to existing path" shape.

## Requirements

1. Add a top-level `--container NAME_OR_PREFIX` (repeatable, `action="append"`,
   default `None`) to `parse_args()`, distinct from the existing
   `inspect-files`/`action` subcommand `--container` flags (do not disturb
   those). It composes as a **union** with `--entities`/`--slice` (same union
   semantics P55 uses across its selectors).
2. **Resolution ordering contract:** container names can only resolve after the
   sweep's `enrich_entities()` has populated `Entity.docker` (P57's documented
   constraint). Therefore the resolved-key set must be computed **inside** the
   collector's `collect_once()` against that sweep's entities — NOT pre-resolved
   in `cli.py` against a separate throwaway sweep. Extend the `Collector`
   filtering seam to accept container selectors and resolve them via
   `resolve_container_key()` against the freshly enriched entity dict, then merge
   the resolved keys into the matched set before `add_entity_ancestors()`. State
   in the LOG why resolution moved into the collector rather than staying in
   `cli.py` (staleness/cross-sweep correctness).
3. A `--container` value that resolves to no running container, or ambiguously,
   raises the same `ContainerResolveError` P57 defines; on the collection path
   this must surface as exit 2 with the bounded P57 message (no raw paths), the
   same way `--slice` validation failure exits 2 in `main()`.
4. `--container` on the collection path is rejected (exit 2, clear message) with
   `--replay` and `--attach`, exactly matching P55's rejection of
   `--entities`/`--slice`/`--metrics` there.
5. Composes with `--metrics compact` with no special-casing (resolved container
   entities get the same compact metric shape as any other matched entity).
6. Replace the P57 `.. todo::` P55-composition note in
   `_resolve_mutual_exclusive_target` with a one-line pointer to this now-done
   wiring (leave the P56 TODO intact — P56 is still unmerged).

## Acceptance Oracles / Tests (numbered, adversarial)

Use the existing gstammtisch fixture cgroup tree (it has a
`docker-<64hex>.scope` entity whose `Entity.docker.name` is populated by the
fixture docker-inspect stub — reuse P55's `_make_collector` and P57's
`_make_enriched` patterns; do not invent new fixture shapes).

1. `--container <exact-name>` on `--once` collects that container's `EntityKey`
   plus its ancestors and nothing else (assert exact key set, incl. root `""`
   ancestor; assert a sibling scope is absent) — fails if resolution isn't wired
   into pruning.
2. `--container <prefix>` unambiguous resolves the same single key.
3. `--container` union with `--slice`: assert both the resolved container key
   and the slice subtree appear.
4. `--container <nonexistent>` on `--once` exits 2 with the P57 no-match message
   (assert on captured stderr, not just the code).
5. Ambiguous `--container <prefix>` (two fixture containers sharing a prefix)
   exits 2 and the message lists both candidate names.
6. `--container` + `--replay` and `--container` + `--attach` each exit 2.
7. `--container <name> --metrics compact`: resolved container entity is present
   and carries only the compact metric families (assert `ram` present,
   `net_rx_bps` absent, and — per P55-R1 — `eframe.network is None`).
8. Resolution-ordering guard: a test that would pass if resolution ran against a
   pre-`enrich_entities` entity set must fail — e.g. assert resolution succeeds
   for a container whose docker metadata is only present post-enrichment (prove
   the in-collector ordering, not a cli.py pre-sweep).

## Out Of Scope

- The `inspect-files`/`action` subcommand `--container` flags (already shipped
  in P57 — do not modify their behavior).
- `--attach`/`--replay` container filtering (rejected, as above).
- TUI-interactive container jump/search (P57 out-of-scope; still future work).
- Any new Docker API calls — resolve only against data the sweep already
  gathered (P57 contract).
- A free-form `--metrics FIELD,...` list — that is P60.

## Docs

Update `README.md` (document `--container` as a collection-path selector next to
`--entities`/`--slice`), `CONTRACTS.md` if any selector-composition contract
needs a line, and `docs/ROADMAP.md`/`docs/STATUS.md` package entries.
