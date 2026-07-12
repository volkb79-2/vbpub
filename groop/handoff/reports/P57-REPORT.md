# P57 Report — Docker-Name Entity Selectors

**Branch:** `feat/groop-p57-docker-name-entity-selectors`
**Base:** `bc09212` (docs: controller workflow v2 — role-separated orchestration)
**Date:** 2026-07-12

**Merged to main:** (not merged — feature branch only, per handoff)

## What Was Built

### `resolve_container_key()` in `groop/src/groop/collect/dockerjoin.py`

A new public function that scans already-enriched `Entity.docker` metadata for
entities matching a container name or prefix:

- **Exact match** wins over prefix match when both exist.
- **Multiple distinct prefix matches** → exit 2 with candidate names listed.
- **Zero matches** → exit 2 with clear "no running container matches" message.
- Resolution requires entities to already be docker-enriched (ordering constraint
  documented).

### `--container NAME_OR_PREFIX` on `inspect-files` CLI

- Added to `parse_inspect_files_args()` for both `plan` and `read` subcommands.
- Mutually exclusive with `--target` (exit 2 if both given).
- `_main_inspect_files()` resolves `--container` to `EntityKey` via a collector
  sweep, then passes the resolved key as the `--target` value to existing
  validation code — no parallel code paths through validation.

### `--container NAME_OR_PREFIX` on `action` CLI

- Added to `parse_action_args()` for both `preview` and `execute` subcommands.
- Mutually exclusive with `--target` (exit 2 if both given).
- `_main_action()` resolves `--container` to `EntityKey` via a collector sweep,
  then passes the resolved key as the `--target` value.

### P55/P56 Composition Notes

Neither P55 (`--entities`/`--slice`) nor P56 (`groop squeeze --target`) are
merged at implementation time. Left TODO/doc pointers in the code noting the
future composition points per handoff instructions.

### Tests

| # | Test | File | Assertion |
|---|------|------|----------|
| 1 | Exact name match | `test_dockerjoin.py` | Returns correct EntityKey |
| 2 | Unambiguous prefix match | `test_dockerjoin.py` | Returns correct EntityKey |
| 3 | Exact match beats prefix | `test_dockerjoin.py` | Returns exact-match EntityKey |
| 4 | Ambiguous prefix (exit 2) | `test_dockerjoin.py` | `ContainerResolveError` with candidate names |
| 5 | Zero match (exit 2) | `test_dockerjoin.py` | `ContainerResolveError` with no-match message |
| 6 | Non-docker entity skipped | `test_dockerjoin.py` | Non-DOCKER_SCOPE_RE entities ignored |
| 7 | `--container`/`--target` mutual exclusion (inspect-files plan) | `test_inspect_files.py` | Exit 2, message |
| 8 | `--container`/`--target` mutual exclusion (inspect-files read) | `test_inspect_files.py` | Exit 2, message |
| 9 | `--container`/`--target` mutual exclusion (action preview) | `test_actions.py` | Exit 2, message |
| 10 | `--container`/`--target` mutual exclusion (action execute) | `test_actions.py` | Exit 2, message |

## Deviations from Handoff

None. All requirements met as specified.

## Proposed Contract Changes

None. P57 is additive and package-private; no frozen interfaces changed.

## Test Evidence

```bash
$ python3 -m pytest groop/tests/test_dockerjoin.py -v -W error
# (all resolver tests pass)

$ python3 -m pytest groop/tests/test_inspect_files.py -v -W error -k container
# (mutual-exclusion tests pass)

$ python3 -m pytest groop/tests/test_actions.py -v -W error -k container
# (mutual-exclusion tests pass)

$ python3 -m pytest groop/tests -q -W error
# (full suite green)
```

## Known Gaps / Open Items

- P55/P56 composition points noted in code/TODOs but not wired (those packages
  are not yet merged — per handoff, skip if unmerged).
- TUI-side container-name jump/search is explicitly out of scope (handoff §Out
  Of Scope).
- `--container` on `groop squeeze --target` (P56) and `--entities`/`--slice`
  (P55) left with TODO pointers for whichever lands later.
