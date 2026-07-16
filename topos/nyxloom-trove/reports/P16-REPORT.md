# P16 Report

## What changed

- Added a narrow `topos.daemon` broker package with a read-only Unix-socket
  JSON-lines protocol.
- Added `topos daemon serve --socket PATH` to serve frames from the existing
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
  installed; docs specify the intended `/run/topos/topos.sock` and `root:topos`
  `0660` deployment model.
- `topos --attach` remains out of scope for P20.

## Proposed contract changes

- None.

## Tests run

```bash
# /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q
# 96 passed in 15.44s

# find topos/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-topos-p13-venv/bin/python -m py_compile
# (no output)

# PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
# schema_version=1 entities=8 host_metrics=36

# PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto
```

## Known gaps / open items

- No attached TUI client yet.
- No systemd unit/package install automation yet.
- No persistent on-disk history. The prototype uses bounded in-memory history.
