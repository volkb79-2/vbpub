# P26 - Snapshot Progress UI

**Cut:** v1.5 polish. **Depends:** P15, P24. Branch:
`feat/groop-p26-snapshot-progress-ui`. Follow `groop/README.md` workflow
protocol exactly.

## Goal

Make the TUI snapshot hotkey visibly bounded and nonblocking enough for real
operators. Pressing `x` should immediately show that snapshot creation started,
avoid duplicate concurrent writes, and finish with a clear success or failure
status.

## Required Context

- `groop/README.md` workflow protocol.
- `groop/TUI-SPEC.md` snapshot and status-bar expectations.
- `groop/handoff/P15-snapshot-enrichment.md` and
  `groop/handoff/reports/P15-REPORT.md`.
- `groop/src/groop/ui/app.py` around `action_create_snapshot()`.
- `groop/tests/test_ui_app.py` existing snapshot hotkey tests.
- `groop/handoff/AGENT-LOG-TEMPLATE.md`.

## Scope - In

1. Replace the synchronous body of `GroopApp.action_create_snapshot()` with a
   small helper that can be run as a Textual worker/thread.
2. Pressing `x` with a valid selected row must immediately update `#status`
   with a message like `snapshot running: <entity>` before the slow
   systemctl/docker/bundle work is performed.
3. While a snapshot worker is already active, a second `x` must not start a
   second bundle. It should update status with a clear "already running"
   message.
4. Completion must update status with the bundle path on success, or
   `snapshot failed: ...` on handled failures.
5. Preserve P15 behavior: fresh systemctl/docker metadata, provider status,
   redaction, partial provider degradation, and current bundle contents.
6. Add focused tests proving:
   - immediate running status appears before an intentionally delayed injected
     provider returns;
   - duplicate keypresses do not create duplicate snapshots;
   - success still writes a bundle and reports the path;
   - handled exceptions still report failure.
7. Update docs after implementation:
   - `README.md` P26 row should become Done;
   - `docs/ROADMAP.md` P15/P26 text should no longer list snapshot progress as
     remaining polish;
   - `docs/STATUS.md` snapshot state and quality-gate evidence should be
     refreshed.

## Scope - Out

- No new modal/screen unless it is clearly simpler than a status-line worker.
- No arbitrary command execution.
- No Docker/systemd daemon dependency in tests.
- No changes outside `groop/**`.
- No host mutation, root operations, or live provider requirements.

## Design Notes

- Keep this small. A private dataclass/result helper is acceptable if it makes
  worker completion handling cleaner.
- Do not mutate Textual widgets from a worker thread. Collect snapshot data in
  the worker and publish status back on the app thread through Textual's worker
  completion path or `call_from_thread`.
- Capture `current_frame`, `selected_key`, and `previous_frames` before starting
  the worker so frame updates do not change the target mid-snapshot.
- If Textual worker state APIs vary by version, prefer a simple app-owned
  boolean/worker reference that tests can observe indirectly through behavior.

## Acceptance

- Full suite passes:

```bash
python3 -m pytest groop/tests -q
```

- Compile check passes for changed Python files.
- UI tests prove the duplicate-keypress guard and visible progress behavior.
- `groop/handoff/reports/P26-LOG.md` and
  `groop/handoff/reports/P26-REPORT.md` are written and current.

## Handoff Requirements

- Keep `groop/handoff/reports/P26-LOG.md` current using
  `groop/handoff/AGENT-LOG-TEMPLATE.md`.
- Write `groop/handoff/reports/P26-REPORT.md` with implementation summary,
  deviations, tests, known gaps, and contract-change proposals.
- Commit the feature branch with a focused message.
