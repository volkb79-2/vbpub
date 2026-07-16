# P20 Report

## What Was Built

- Added `topos/src/topos/daemon/client.py` with a Unix-socket JSONL client for
  the P16 broker protocol.
- Added top-level `topos --attach SOCKET` support in `topos/src/topos/cli.py`.
- Wired attach mode into the existing UI path with attach-aware status labeling
  in `topos/src/topos/ui/app.py`.
- Added focused tests for daemon client parsing, daemon error/malformed
  handling, attach `--once --json`, attach `--ui-smoke`, and ambiguous attach
  combinations including replay-only pacing flags.
- Updated `topos/docs/DAEMON.md`, `topos/docs/STATUS.md`,
  `topos/docs/ROADMAP.md`, and `topos/README.md` to reflect the implemented
  attach mode.

## Deviations

- `--attach` is implemented as a polling current-frame client for the live TUI
  path. That keeps the slice narrow and works cleanly with the existing UI
  consumer.
- `--attach --record` was intentionally left out of this slice and is rejected
  with a clear error.
- `--attach` also rejects replay-only flags such as `--step` and custom
  `--speed` values instead of silently ignoring them.

## Contract Changes

- None.

## Test Evidence

```bash
/tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests/test_daemon_client.py topos/tests/test_attach_cli.py -q
# 6 passed in 8.37s

/tmp/vbpub-topos-p13-venv/bin/python -m py_compile \
  topos/src/topos/daemon/client.py \
  topos/src/topos/daemon/__init__.py \
  topos/src/topos/cli.py \
  topos/src/topos/ui/app.py \
  topos/tests/test_daemon_client.py \
  topos/tests/test_attach_cli.py
# no output

attach smoke against a local FrameBroker socket:
# once-ok
# {"entities":...}
# smoke-ok
# ui smoke ok frames=1 view=tree profile=auto

/tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q
# 104 passed in 23.31s
```

## Known Gaps

- Production daemon packaging/service installation is still out of scope.
- `--attach --record` is not implemented in this slice.
- The daemon still relies on Unix-socket permissions for access control; there
  is no additional authentication layer yet.
- Further daemon history/stream refinements can come later if attach needs a
  richer subscription model.

## Controller Merge Review

- Feature commit reviewed and amended: `a1f9f31`.
- Merge commit: `e8fb0cb`.
- Post-merge validation from `main`:
  - `/tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests/test_daemon_client.py topos/tests/test_attach_cli.py -q` -> `6 passed in 8.89s`
  - `/tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q` -> `104 passed in 23.08s`
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch` -> `schema_version=1 entities=8 host_metrics=36`
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke` -> `ui smoke ok frames=1 view=tree profile=auto`
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli bpf gate --proc-root topos/tests/fixtures/procfs/network --json` -> safe no-op JSON
