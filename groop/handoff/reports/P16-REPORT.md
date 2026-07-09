# P16 Report

## What changed

- Added a narrow `groop.daemon` broker package with a read-only Unix-socket
  JSON-lines protocol.
- Added `groop daemon serve --socket PATH` to serve frames from the existing
  collector/live-stream path.
- Added a bounded in-memory `FrameBroker` with `current` and short `stream`
  requests.
- Added socket protocol tests for current/stream responses, socket permissions,
  and rejection of arbitrary file-read/command-style operations.
- Updated `docs/ARCHITECTURE.md` and added `docs/DAEMON.md` with the socket
  contract, authorization model, threat model, and retention sketch.

## Protocol

One request object per connection:

```json
{"op":"current"}
{"op":"stream","limit":3}
```

Responses are canonical frame JSON lines followed by `{"type":"end","count":N}`.
Unsupported operations return `{"type":"error","error":"unsupported operation"}`.
There are no mutation, arbitrary file-read, command execution, Docker mutation,
systemd mutation, BPF, or DAMON mutation verbs.

## Deviations from handoff

- This is a spike/prototype, not a packaged production daemon. No systemd unit is
  installed; docs specify the intended `/run/groop/groop.sock` and `root:groop`
  `0660` deployment model.
- `groop --attach` remains out of scope for P20.

## Proposed contract changes

- None.

## Tests run

```bash
# /tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests -q
# 96 passed in 15.44s

# find groop/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-groop-p13-venv/bin/python -m py_compile
# (no output)

# PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch
# schema_version=1 entities=8 host_metrics=36

# PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto
```

## Known gaps / open items

- No attached TUI client yet.
- No systemd unit/package install automation yet.
- No persistent on-disk history. The prototype uses bounded in-memory history.
