# P24-REPORT — Replay Timestamp Jump Controls

**Branch:** `feat/topos-p24-replay-jump`
**Worktree:** `.worktrees/-topos-p24-replay-jump`
**Base commit:** `47717b6` (main, P24 handoff carve)

## What was built

1. **`ReplayDriver.seek_timestamp(ts)`** (`topos/src/topos/record/replay.py`)
   - Deterministic at-or-after seeking: first frame with `Frame.ts >= ts`.
   - Clamped to first/last when `ts` is outside the recording range.

2. **TUI jump actions** (`topos/src/topos/ui/app.py`)
   - `action_replay_first` — `home` key, seek to index 0.
   - `action_replay_last` — `end` key, seek to last index.
   - `action_replay_jump_prompt` — `j` key, opens a `JumpScreen` input overlay.
   - `_on_jump_applied` — parses input: no-dot integers → 1-based frame number,
     input with a dot → epoch timestamp; validates range; shows clear error
     on invalid input; preserves current frame on failure.
   - All jumps pause replay and cancel pending timers.

3. **Key binding changes** (`topos/src/topos/ui/keys.py`)
   - Added `home` → `replay_first`, `end` → `replay_last`.
   - Reassigned `j` from `select_next` (alias for `down`) to `replay_jump_prompt`.
   - Updated `key_help()` with "Home/End replay first/last" and "j replay jump to frame/ts".

4. **Status/help updates**
   - Status line shows `controls=space ,/. +/- home/end j` in replay mode.
   - `JumpScreen` placeholder explains acceptable input format.

5. **Non-replay mode messaging**
   - `home`, `end`, `j` all show `"replay jump is only available in --replay mode"` in live mode.

## Deviations from handoff

None. All items in scope were implemented.

## Proposed contract changes

None. All changes are additive (`seek_timestamp` method) or package-private (TUI actions).

## Test evidence

```bash
$ python3 -m py_compile topos/src/topos/record/replay.py topos/src/topos/ui/app.py topos/src/topos/ui/keys.py topos/tests/test_record.py topos/tests/test_ui_app.py
# clean

$ PYTHONPATH=topos/src:topos/tests /tmp/vbpub-topos-p17-venv/bin/python -m pytest topos/tests/test_record.py topos/tests/test_ui_app.py -q
34 passed in 14.54s

$ PYTHONPATH=topos/src:topos/tests /tmp/vbpub-topos-p17-venv/bin/python -m pytest topos/tests -q
170 passed in 26.70s after merge
```

## Merge evidence

P24 merged to `main` with:

```bash
git merge --no-ff feat/topos-p24-replay-jump -m "Merge topos P24 replay jump controls"
```

Post-merge validation from the main checkout: py_compile clean and full suite
`170 passed in 26.70s`.

9 new tests:
- `test_replay_seek_timestamp_exact_and_clamped` — exact match, at-or-after, before-first, beyond-last, boundary.
- `test_replay_seek_timestamp_single_frame` — single-frame edge case.
- `test_pilot_replay_first_and_last_jump` — home/end keys, pause on jump.
- `test_pilot_replay_jump_prompt_with_frame_number` — `j` prompt, enter "2".
- `test_pilot_replay_jump_prompt_invalid_input_preserves_current_frame` — "not-a-number", frame unchanged, error shown.
- `test_pilot_replay_jump_prompt_rejects_nonfinite_input` — "nan", frame unchanged, finite-value error shown.
- `test_pilot_replay_jump_out_of_range_frame_number` — "99", frame unchanged, error shown.
- `test_pilot_replay_jump_prompt_with_timestamp` — epoch timestamp input jumps to correct frame.
- `test_pilot_replay_jump_in_non_replay_mode_shows_unavailable_message` — home/end/j in live mode.

## Known gaps / open items

1. **Frame-number vs. timestamp heuristic**: an integer input without a dot is
   always treated as a frame number. Epoch timestamps that happen to be whole
   integers (e.g. `1720000000`) must be entered with a trailing `.0` to be
   treated as a timestamp. This is an acceptable trade-off: actual epoch
   timestamps are large (≥1e9) and unlikely to collide with valid frame numbers.
2. **No date/time parsing**: the handoff explicitly excluded datetime parsing
   beyond numeric frame numbers and epoch seconds.
3. **`j` key removed from selection navigation**: the `down` key still selects
   next row; `j` no longer does. This is a minor muscle-memory change for any
   user who exclusively used `j` for selection.
