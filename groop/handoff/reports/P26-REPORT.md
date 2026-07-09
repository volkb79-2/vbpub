# P26 Report — Snapshot Progress UI

## What changed

- **`src/groop/ui/app.py`** — Replaced the synchronous `action_create_snapshot()` body with an
  async worker pattern:
  - `action_create_snapshot()` now sets `_snapshot_in_progress = True` and shows
    `"snapshot running: <entity>"` on the status bar immediately, then launches the snapshot
    work via `self.run_worker(self._run_snapshot_worker, thread=True)`.
  - A duplicate `x` keypress while a worker is active shows `"snapshot already running"` and
    does not start a second bundle.
  - On success the status bar reports `"snapshot saved: <path>"`; on handled exceptions it
    reports `"snapshot failed: <error>"`.
  - Snapshot context (entity_key, frame, previous_frames) is captured before the worker
    starts and stored in instance attributes to avoid stale-frame races.
  - All P15 behavior (fresh systemctl/docker metadata, provider status, redaction, bundle
    contents) is preserved — the worker calls the same `collect_systemctl_show`,
    `collect_docker_inspect`, and `create_snapshot` functions.

- **`tests/test_ui_app.py`** — Added 4 focused tests:
  - `test_pilot_snapshot_running_status_appears_immediately` — verifies the status line is
    updated before a slow injected provider can complete, and the bundle is written.
  - `test_pilot_snapshot_duplicate_keypress_guard` — verifies a second `x` while
    `_snapshot_in_progress` is True shows `"snapshot already running"`.
  - `test_pilot_snapshot_success_reports_path` — verifies the status bar shows the saved
    bundle path after successful snapshot creation.
  - `test_pilot_snapshot_handled_exception_reports_failure` — uses a provider raising
    `RuntimeError` (not caught by `collect_systemctl_show`) to exercise the failure path.

- **Docs updates:**
  - `README.md` — P26 row changed from `Planned` to `Done`, with report link.
  - `docs/ROADMAP.md` — P15's "progress-UI gap" text removed; P26 section marked `done`.
  - `docs/STATUS.md` — snapshots entry updated to reflect the progress/status UI;
    quality gate updated to P26 validation (181 tests pass).

## Deviations from handoff

- No `textual.worker.Worker` was used. The handoff noted that Textual worker state APIs
  vary by version. Instead, a simple `bool` flag (`_snapshot_in_progress`) plus
  `run_worker(thread=True)` + `call_from_thread` provides the same guard without depending
  on a specific Worker API version.
- The `running_status_appears_immediately` test invokes the action directly instead of using
  `Pilot.press()`, because `Pilot.press()` may process worker callbacks before returning.
  This keeps the immediate status assertion deterministic while still exercising the same
  action method and worker path.

## Proposed contract changes

- None.

## Tests run

```bash
# Snapshot-focused tests
.venv-p26/bin/python -m pytest groop/tests/test_ui_app.py -k "snapshot" -v
# 6 passed (2 existing P15 tests + 4 new P26 tests)

# Full suite
.venv-p26/bin/python -m pytest groop/tests -q
# 181 passed in 28.78s

# Compile check
find groop/src -name '*.py' -print0 | xargs -0 .venv-p26/bin/python -m py_compile
# (no output)
```

## Known gaps / open items

- The worker runs in a thread via `run_worker(thread=True)`. In Textual's test mode,
  `Pilot.press()` can process thread completion before returning, so the immediate status
  assertion calls `action_create_snapshot()` directly after mounting the app.
- No new modal screen was added — the handoff explicitly scoped this out if a status-line
  worker was simpler, which it was.
- All snapshot contents and P15 enrichment behavior are unchanged.
