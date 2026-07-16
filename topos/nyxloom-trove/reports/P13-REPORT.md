# P13 Report

## What changed

Implemented the P13 v1 UI polish slice in the required worktree:
- added tree branch collapse/expand state in the TUI, including descendant reveal during filtering and stable selection handling;
- added replay-aware UI state in `ToposApp` with live/replay mode marker, paused/playing status, frame index/time, replay stepping, replay pause/resume, and replay speed controls;
- routed replay UI through the existing `ReplayDriver`/`ToposApp` path instead of creating a separate replay frontend;
- made reserved v2 admin action UX explicit with a status message instead of a silent no-op;
- exposed unsupported custom profile columns gracefully via profile metadata and title suffixes;
- updated operations docs and added focused UI/table tests.

## Deviations from handoff

- Kept the reserved-action UX focused on the current explicit v2 admin placeholder (`k`) rather than introducing broader v1.5/v2 gating changes.
- Did not add timestamp-jump replay UX or hotkey-profile remapping; those are outside the P13 handoff scope and the current contracts.

## Proposed contract changes

- None.

## Tests run

```bash
# /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q
# 84 passed in 11.98s

# find topos/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-topos-p13-venv/bin/python -m py_compile
# (no output)

# /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
# schema_version=1 entities=8 host_metrics=20

# /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto
```

## Known gaps / open items

- Replay controls cover pause/resume, step, speed, and visible status, but timestamp jump remains unimplemented.
- Reserved v2 action feedback is explicit for the current admin placeholder; actual admin actions remain out of scope.
- Validation used an isolated venv at `/tmp/vbpub-topos-p13-venv` because system `pytest` was unavailable.
