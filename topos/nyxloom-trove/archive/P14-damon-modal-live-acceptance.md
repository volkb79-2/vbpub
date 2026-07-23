# P14 — DAMON control modal and live-root acceptance

**Cut:** v1.5 stabilization. **Depends:** P8-P11, P13 preferred. Branch:
`feat/topos-p14-damon-modal`. Follow `topos/README.md` workflow protocol.

## Goal

Finish the TUI side of controlled DAMON sessions and record real-root acceptance
evidence for deliberate test use.

## Scope — in

1. Textual typed-confirmation modal for vaddr entity sessions:
   - shows planned sysfs writes;
   - requires exact `START`;
   - handles root-required, no-pids, no-free-slot, and stale-pids errors.
2. Textual typed-confirmation modal for paddr host session:
   - shows planned sysfs writes;
   - requires exact `START`;
   - refuses duplicate topos-owned paddr session.
3. Stop control surface:
   - explicit cleanup for topos-owned sessions;
   - never offers stop for foreign sessions.
4. Optional `damon_stat` conflict handling:
   - detect built-in default session if present;
   - either refuse with explanation or implement disable/restore safely.
5. Live-root acceptance:
   - start vaddr on a test entity, observe passive columns, stop;
   - start paddr, observe banner heat after roughly two aggregation windows,
     stop;
   - prove foreign sessions are untouched;
   - record results in `MEASUREMENTS.md`.

## Scope — out

- Auto-start paddr.
- DAMOS schemes or reclamation actions.
- Per-entity paddr attribution.

## Acceptance

- Fixture tests cover modal success/error paths without live sysfs mutation.
- Live-root acceptance is documented or explicitly marked blocked with reason.
- Existing CLI start/stop tests remain green.
- `docs/OPERATIONS.md` reflects the final DAMON control UX.

## Notes

- Keep `damon/control.py` and `damon/paddr.py` as the source of truth for sysfs
  writes. UI should call planning/apply APIs, not duplicate write lists.
