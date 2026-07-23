# P24 - Replay timestamp jump controls

**Cut:** v1 polish. **Depends:** P13. Branch:
`feat/topos-p24-replay-jump`. Worktree:
`.worktrees/-topos-p24-replay-jump`.

## Goal

Close the remaining replay timestamp-jump UX gap. In replay mode, operators
should be able to jump directly to the start/end, to a frame number, or to a
timestamp without stepping through long recordings.

## Required context

- Read `topos/README.md`, especially "Workflow protocol".
- Read `topos/CONTRACTS.md`.
- Read `topos/docs/STATUS.md` and `topos/docs/ROADMAP.md` replay sections.
- Read existing replay/UI code and tests:
  - `src/topos/record/replay.py`
  - `src/topos/ui/app.py`
  - `src/topos/ui/keys.py`
  - `tests/test_record.py`
  - `tests/test_ui_app.py`

## Scope

1. Add replay seek helpers.
   - Keep `ReplayDriver.seek(index)` behavior as the primitive.
   - Add a tested helper for timestamp seeking, e.g. `seek_timestamp(ts)` or
     `seek_at_or_after(ts)`, returning the selected frame.
   - Use deterministic behavior: first frame whose `Frame.ts >= ts`; clamp to
     first/last when outside the recording range.
2. Add TUI replay jump controls.
   - Bind start/end jumps in replay mode, preferably `home` and `end`.
   - Bind an explicit jump prompt, preferably `j`.
   - The prompt accepts either:
     - a 1-based frame number such as `42`, or
     - an epoch timestamp such as `1720000000.25`.
   - Invalid input should leave the current frame unchanged and show a clear
     status message.
   - Any jump pauses replay and cancels pending timers, matching step behavior.
3. Update visible replay status/help.
   - Status controls text should mention jump controls compactly.
   - Key help should include the new controls.
4. Add tests.
   - Unit tests for timestamp seek clamping and exact/nearest-at-or-after
     behavior.
   - UI pilot tests for start/end jump and prompt input.
   - Non-replay mode should show a clear unavailable message for replay-only
     jump actions.
5. Update docs.
   - `README.md`, `docs/STATUS.md`, and `docs/ROADMAP.md` should no longer
     list timestamp jump as an open replay gap.

## Out of scope

- No timeline scrubber widget.
- No date/time parsing beyond numeric frame numbers and numeric epoch seconds.
- No record/replay file format changes.
- No broad keybinding/profile customization work.

## Acceptance criteria

- Replay timestamp seek is deterministic and clamped.
- Jumping pauses replay and updates the visible frame/status.
- Invalid jump input preserves the current replay index and reports the issue.
- Existing pause/step/speed behavior remains intact.
- Focused replay/UI tests pass, plus full `topos/tests` and py_compile over
  changed files.

## Handoff artifacts

- Keep `topos/handoff/reports/P24-LOG.md` current using
  `handoff/AGENT-LOG-TEMPLATE.md`.
- Write `topos/handoff/reports/P24-REPORT.md` with implementation summary,
  deviations, test evidence, known gaps, and proposed contract changes.
- Commit the feature branch before handoff.
