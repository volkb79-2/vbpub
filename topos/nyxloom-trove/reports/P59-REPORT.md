# P59-REPORT — `--container` as an Entity Selector (P55 x P57 composition)

## State

| Field | Value |
|---|---|
| Package | P59 |
| Title | `--container` as an Entity Selector (P55 x P57 composition) |
| Branch | `feat/topos-p59-container-entity-selector` |
| Status | **Done** |
| Base | main after P55 + P57 merge |

## Requirement Coverage

| Requirement | Status | Evidence |
|---|---|---|
| 1. `--container NAME_OR_PREFIX` (repeatable, `action="append"`, default `None`) to `parse_args()` | ✅ | `cli.py:84-85` — `action="append"`, `dest="container_selectors"`, distinct from subcommand `--container` flags |
| 2. Resolution inside collector's `collect_once()` against post-enrich entities | ✅ | `collector.py:81-96` — resolves via `resolve_container_key()` after `enrich_entities()` in the same sweep |
| 3. `ContainerResolveError` surfaces as exit 2 with bounded message | ✅ | `cli.py:501-505` — caught in `--once` path; test 4 asserts stderr message |
| 4. `--container` rejected with `--replay` and `--attach` (exit 2) | ✅ | `cli.py:388,417` — added to existing rejection checks |
| 5. Composes with `--metrics compact` | ✅ | `test_container_metrics_compact` — resolved entity carries only compact families |
| 6. Replace P57 TODO in `_resolve_mutual_exclusive_target` | ✅ | `cli.py:751-762` — `.. note::` replaces `.. todo::`, points to P59 handoff |
| Union semantics with `--entities`/`--slice` | ✅ | `test_container_union_with_slice` — both container key and slice subtree present |
| `_filter_kwargs` passes `container_selectors` to `Collector` | ✅ | `cli.py:250-254` — tuple conversion |
| All three Collector call sites wired | ✅ | `cli.py` lines 441, 485, 502 pass `**_filter_kwargs(args)` |

## Adversarial Test Coverage

| Test # | Description | File | Status |
|---|---|---|---|
| 1 | `--container <exact-name>` collects entity + ancestors, excludes sibling | `test_p59_container_selector.py:99` | ✅ |
| 2 | `--container <prefix>` unambiguous resolves same key | `test_p59_container_selector.py:117` | ✅ |
| 3 | Union `--container` + `--slice` | `test_p59_container_selector.py:131` | ✅ |
| 4 | `--container <nonexistent>` exits 2 with P57 message (captured stderr) | `test_p59_container_selector.py:150` | ✅ |
| 5 | Ambiguous prefix exits 2 listing candidates | `test_p59_container_selector.py:164` | ✅ |
| 6 | `--container` + `--replay` and `--container` + `--attach` each exit 2 | `test_p59_container_selector.py:204,212` | ✅ |
| 7 | `--container --metrics compact` | `test_p59_container_selector.py:220` | ✅ |
| 8 | Resolution-ordering guard (pre-enrich fails, post-enrich succeeds) | `test_p59_container_selector.py:245` | ✅ |

## Deviations from Handoff

- None. All requirements are met as specified.

## Proposed Contract Changes

- `CONTRACTS.md` §5: Updated filtered recordings contract to include `--container` alongside `--entities`/`--slice`/`--metrics compact`.

## Test Evidence

```bash
$ python3 -m pytest topos/tests/test_p59_container_selector.py -q 2>&1 | tail -2
9 passed, 1 warning in 0.37s
```

```bash
$ python3 -m pytest topos/tests -q --tb=short 2>&1 | tail -5
923 passed, 2 skipped, 1 warning in 121.18s (0:02:01)
```

## Known Gaps / Open Items

- Live TUI and `--record` (non-once) paths: `ContainerResolveError` from the first frame propagates through the frame stream generator. For `--once` paths it is caught and exits 2 cleanly. For live paths, the error will crash the UI/recorder with the error message. This is consistent with how other unexpected collector errors would behave.

## Files Changed

| File | Change |
|---|---|
| `topos/src/topos/cli.py` | Added `--container` flag, `_filter_kwargs` for container_selectors, rejection with `--replay`/`--attach`, `ContainerResolveError` handling, TODO update |
| `topos/src/topos/collect/collector.py` | Added `container_selectors` param, resolution logic in `collect_once()` |
| `topos/tests/test_p59_container_selector.py` | 9 new tests covering all 8 required acceptance oracles |
| `topos/README.md` | Updated quickstart docs and P59 work package entry |
| `topos/docs/STATUS.md` | Added P59 implementation entry |
| `topos/docs/ROADMAP.md` | Marked P59 as `:done:` |
| `topos/CONTRACTS.md` | Updated filtered recordings contract to include `--container` |
| `topos/handoff/reports/P59-LOG.md` | Work log |
| `topos/handoff/reports/P59-REPORT.md` | This report |