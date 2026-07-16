# P13 — UI navigation, replay controls, and v2-disabled action UX

**Cut:** v1 stabilization. **Depends:** P5, P7, P11. Branch:
`feat/topos-p13-ui-polish`. Follow `topos/README.md` workflow protocol.

## Goal

Make the TUI feel like a durable daily tool: predictable tree navigation,
visible replay state, clear disabled future actions, and cleaner profile/key
behavior.

## Scope — in

1. Tree expand/collapse state:
   - branch rows can collapse/expand;
   - selection remains stable;
   - filtering still reveals matching descendants.
2. Replay controls and status:
   - visible replay/live mode marker;
   - step/pause/resume controls when replaying;
   - current frame index/time when available.
3. Reserved v2 action UX:
   - destructive/admin keys are either unbound or show a clear "requires v2
     admin mode" message;
   - no silent no-op for reserved actions.
4. Profile polish:
   - verify configured custom profiles;
   - document unsupported column names gracefully;
   - improve width-tier/custom override behavior if small and contained.
5. Tests:
   - Textual pilot tests for collapse/expand, replay status, and reserved key
     feedback;
   - no Textual imports outside `src/topos/ui/`.

## Scope — out

- Actual v2 admin actions.
- Daemon attach mode.
- New collector metrics.

## Acceptance

- Full test suite passes.
- Replay UI smoke still passes.
- A golden or pilot test proves a collapsed branch hides descendants and expands
  them again without losing selection.
- `docs/OPERATIONS.md` key table is updated.

## Notes

- Keep cards/panels simple. The current UI is an operational TUI, not a landing
  page.
- Do not add a parallel row model; reuse `Frame` and `RenderedRows`.
