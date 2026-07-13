# P72 — Admin Action Kill/Update Verbs — Agent Log

## Session

- **Date:** 2026-07-13   <!-- was 2026-07-17 (a future date); corrected at pass #2 -->

- **Package:** P72
- **Worktree:** `.worktrees/groop-p72-admin-action-kill-update`
- **Branch:** `feat/groop-p72-admin-action-kill-update`
- **Base:** main@27e0a6a

## Actions

1. **Read context**: Read P46 handoff/report, P49 handoff/report, CONTRACTS.md,
   actions/__init__.py, catalog.py, preview.py, execute.py, governance.py,
   squeeze.py, cli.py, test_actions.py.

2. **Add ActionKind enum members**: Added `DOCKER_KILL`, `SYSTEMD_KILL`,
   `DOCKER_UPDATE` to `catalog.py`. Added catalog builders
   (`_docker_kill`, `_systemd_kill`, `_docker_update`) and catalog entries.
   Updated `EXECUTION_ALLOWLIST` to include the new kinds.
   Extended `validate_target` for Docker and systemd kill/update target formats.

3. **Create kill_ops.py**: Implemented signal validation (closed allowlist:
   TERM, INT, HUP, KILL, QUIT, USR1, USR2; SIG-prefix and numeric rejection),
   KILL signal `--force` gate, `build_kill_argv`, `KillPlan` dataclass,
   `build_kill_preview`, `render_kill_preview`, `kill_plan_to_jsonable`,
   and `ProtectedCheck` type for injectable protected-entity check.

4. **Create update_ops.py**: Implemented `validate_memory` (reusing `parse_size`
   from squeeze.py for suffix support), `validate_cpus` (bounded positive float),
   `_default_current_memory_reader`, systemd target rejection
   (`_reject_systemd_target`), `build_update_argv`, `UpdatePlan` dataclass,
   `build_update_preview`, `render_update_preview`, `update_plan_to_jsonable`,
   and below-current-usage guard (Oracle 5).

5. **Update preview.py**: Added `KillPlan`, `UpdatePlan` to `AdminPreviewResult`
   union type. Extended `build_admin_preview` with kill-specific arguments
   (signal, force) and update-specific arguments (memory, cpus, below_current,
   current_memory_reader).

6. **Update execute.py**: Added `execute_kill` and `execute_update` functions
   following the same P46 gate pattern (root/admin/typed-confirmation/audit/
   timeout). Each uses its own per-verb confirmation token (KILL / UPDATE).
   Added systemd target check to `execute_update`.

7. **Update __init__.py**: Exported all new symbols (KillPlan, UpdatePlan,
   build_kill_preview, build_update_preview, validate_signal, validate_cpus,
   validate_memory, execute_kill, execute_update, etc.).

8. **Update cli.py**: Added `--signal`, `--force`, `--memory`, `--cpus`,
   `--below-current` arguments to both preview and execute subcommands.
   Added routing for kill and update kinds in `_main_action`.

9. **Write tests** (test_p72_kill_update.py): 45 tests covering all 9
   acceptance oracles.

10. **Run gates**: Focused tests (245 passed), full suite (1165 passed, 2
    skipped, 1 pre-existing failure), py_compile (clean), git diff --check
    (clean).

## Files changed

| File | Change |
|---|---|
| `groop/src/groop/actions/catalog.py` | Add DOCKER_KILL, SYSTEMD_KILL, DOCKER_UPDATE kinds, builders, EXECUTION_ALLOWLIST update, validate_target extension |
| `groop/src/groop/actions/kill_ops.py` | New module: signal validation, KillPlan, preview/execute support |
| `groop/src/groop/actions/update_ops.py` | New module: memory/CPU validation, UpdatePlan, systemd target check |
| `groop/src/groop/actions/preview.py` | KillPlan/UpdatePlan in union type, build_admin_preview extension |
| `groop/src/groop/actions/execute.py` | execute_kill, execute_update functions |
| `groop/src/groop/actions/__init__.py` | Export new symbols |
| `groop/src/groop/cli.py` | kill/update CLI arguments and routing |
| `groop/tests/test_actions.py` | Update EXECUTION_ALLOWLIST expected value |
| `groop/tests/test_p72_kill_update.py` | New file: 45 acceptance oracle tests |

## Decisions

- **Confirmation tokens**: kill uses `--confirm KILL`, update uses
  `--confirm UPDATE`. Both are distinct from the `EXECUTE` token used by
  start/stop/restart (Contract 3).
- **Override flag for below-current usage**: `--below-current` (no extra
  confirmation required beyond `--confirm UPDATE`).
- **Memory validation**: reuses `parse_size` from `squeeze.py` (which handles
  K/M/G suffixes, overflow, and range) — the same established code path.
- **Protected entity check**: injectable `ProtectedCheck` callable in
  `execute_kill`; production default returns False for safety.

## Next steps

- Draft and commit P72-REPORT.md and P72-LOG.md.
- Commit all changes to the feature branch.
