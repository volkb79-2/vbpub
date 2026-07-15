# P08 Implementation Report

**Result:** done

**Date:** 2026-07-15

## Implementation Summary

Implemented the `doctor_project()`, `rebuild()`, and `doctor_all()` functions in `src/handoffctl/doctor.py` to provide runtime drift auditing and statefile projection rebuild capabilities. All 11 drift-audit checks and rebuild functionality are now operational, with graceful degradation when cross-package dependencies raise NotImplementedError.

## Oracle Results

| Oracle | ID | Kind | Severity | Result |
|--------|----|----|----------|--------|
| 1 | O1 | Clean project with helpers mocked clean | - | **pass** |
| 2 | O2 | replay-divergence: hand-edited statefile | critical | **pass** |
| 3 | O3 | handoff-lint error surface | error | **pass** |
| 4 | O4 | dangling-dep: ghost reference | error | **pass** |
| 5 | O5 | orphan-worktree: unmatched git worktree | warning | **pass** |
| 6 | O6 | missing-worktree: ACTIVE task | warning | **pass** |
| 7 | O7 | stale-receipt: RUNNING with receipt | warning | **pass** |
| 8 | O8 | unbound-evidence: MERGED without merge_commit | warning | **pass** |
| 9 | O9 | legacy-lock: .STACK_LOCK exists | warning | **pass** |
| 10 | O10 | stale-pause: 8 days old | info | **pass** |
| 11a | O11 | orphan-statefile: QUEUED with missing handoff | warning | **pass** |
| 11b | O11 | orphan-statefile: COMPLETED exemption | - | **pass** |
| 12 | O12 | decision-hold: QUEUED with open D-dep | info | **pass** |
| 13a | O13 | rebuild: diffs on divergence | - | **pass** |
| 13b | O13 | rebuild: write=True creates .bak | - | **pass** |
| 14 | O14 | doctor_all: registry iteration | - | **pass** |

**Oracles: 16 pass, 0 fail**

## Implementation Details

### doctor_project(cfg: ProjectConfig) -> list[DoctorFinding]

Implemented all 11 checks with per-check NotImplementedError handling:

1. **replay-divergence** (critical): Compares replayed event log against on-disk statefiles via `to_dict()` equality.
2. **handoff-lint** (error): Surfaces blocking lint findings from discovered handoffs.
3. **dangling-dep** (error): Validates that all task dependencies have either a handoff file or statefile.
4. **orphan-worktree** (warning): Detects git worktrees under `cfg.worktree_root` with no matching non-terminal task branch.
5. **missing-worktree** (warning): Flags ACTIVE tasks whose `attempt.worktree` path doesn't exist.
6. **stale-receipt** (warning): Detects receipt.json present on attempts still in RUNNING/PREFLIGHTING state.
7. **unbound-evidence** (warning): Identifies MERGED/VALIDATING/COMPLETED tasks with `merge_commit=None`.
8. **legacy-lock** (warning): Finds `.STACK_LOCK` / `.CARVE_LOCK` files under the repo.
9. **stale-pause** (info): Detects pause flags older than 7 days.
10. **orphan-statefile** (warning): Identifies non-terminal statefiles whose `handoff_path` no longer exists.
11. **decision-hold** (info): Surfaces QUEUED/NEEDS_DECISION tasks waiting on OPEN decisions (refs the D-id).

Each check is wrapped in try/except; if a cross-package helper raises `NotImplementedError`, a single `check-unavailable` finding is emitted with refs=[module_name].

### rebuild(project: str, write: bool = False) -> tuple[dict[str, TaskStateFile], list[str]]

- Replays the entire event log via `storage.replay()`.
- Diffs replayed states against on-disk via recursive dict comparison (`_dict_diff()`) with recursive depth limit of 3.
- Returns diffs as dotted-path strings (e.g., `'task-id.notes: old != new'`), capped at 50 entries.
- When `write=True`: Creates `.bak` copies of on-disk statefiles before saving the replayed version, enabling safe recovery.

### doctor_all() -> dict[str, list[DoctorFinding]]

Loads the registry and calls `doctor_project()` on each registered project, catching any ProjectConfig.load() exceptions gracefully.

## Files Touched

- `src/handoffctl/doctor.py` — Implementation of all three public functions + `_dict_diff()` helper.
- `tests/test_doctor.py` — All 14+ oracle test cases covering 16 test methods.

## Cross-Package Dependencies

The implementation gracefully degrades when the following raise NotImplementedError:
- `frontmatter.discover_handoffs()` — used in checks 3, 10, 11
- `frontmatter.parse_handoff()` — used in checks 3, 10, 11
- `lint.lint_project()` — used in check 2
- `decisions.open_ids()` — used in check 11

Each missing dependency emits a single `check-unavailable` DoctorFinding, allowing doctor to remain useful during parallel development.

## Gate Output (verbatim tail)

```
============================= test session starts ==============================
platform linux -- Python 3.13.5, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/handoffctl2
configfile: pyproject.toml
plugins: hypothesis-6.156.6, cov-7.1.0, anyio-4.14.2, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None
collected 16 items

tests/test_doctor.py ................                                    [100%]

============================== 16 passed in 0.91s ==============================
```

## Deviations & Assumptions

- **Dotted-path diffs for rebuild**: Diffs use a simple recursive dict comparison with path notation (e.g., `task.attempts[0].state`), not full JSON Pointer RFC 6901. Depth capped at 3 to avoid noise on deeply nested structures.
- **Git worktree parsing**: Parsed via `git -C root worktree list --porcelain` and manual line-by-line extraction (no external regex).
- **Branch matching for orphan-worktree**: Non-terminal tasks match branches by exact string equality; decorated branch names (e.g., `refs/heads/feat/zombie`) are normalized to their unqualified form.
- **Test fixture monkeypatching**: All cross-package helpers are monkeypatched in tests; actual implementations (P01, P07) will be wired at test time when they're available.

## Notes for Reviewer

- All tests are deterministic and pass in the test-runner environment (no sleep loops, no external APIs).
- The implementation satisfies the "always-available lint" contract: doctor is safe to run at any point in the project lifecycle, even when some upstream packages are still unimplemented.
- No file mutations occur except in `rebuild(write=True)` mode, which is explicitly opt-in and creates `.bak` backups.
