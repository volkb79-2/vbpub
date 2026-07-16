# P32 Report — Daemon Status Command

## What Was Built

- Added `topos/src/topos/daemon/status.py` with:
  - `ProtocolStatus` dataclass (ok, message, schema_version, frame_ts, entity_count)
  - `DaemonStatusReport` dataclass combining preflight + protocol checks, with `to_jsonable()` and `to_text()` renderers
  - `build_daemon_status()` combining `preflight_daemon_deployment()` with a `DaemonClient.current_frame()` protocol check
  - Protocol error guidance matching P31 style (connect errors suggest preflight/preflight --socket, protocol errors suggest compatible daemon/logs)
- Added `topos daemon status --socket PATH --group NAME [--json] [--pretty-json]` CLI subcommand (exit 0 ok, 1 degraded, 2 arg errors)
- Exported `DaemonStatusReport`, `ProtocolStatus`, `build_daemon_status` from `topos.daemon`
- **Tests (10)**: JSON success, text success, pretty-json, missing default socket, missing custom socket, protocol error message, CLI JSON, CLI text, CLI missing socket, CLI pretty-json
- **Docs updated**: `README.md` (P32 → Done), `DAEMON.md` (status command section), `OPERATIONS.md` (daemon status workflow in Common Commands)

## Worktree

- Branch: `feat/topos-p32-daemon-status`
- Worktree: `/home/vb/volkb79-2/vbpub/.worktrees/-topos-p32-daemon-status`
- Python: `/tmp/vbpub-topos-p32-venv/bin/python` (Python 3.13.5)

## Deviations from Handoff

- **No separate daemon/status helper for preflight report JSON**: The `preflight_report_to_jsonable()` is reused directly in `DaemonStatusReport.to_jsonable()` rather than creating a nested derivative. The handoff suggested "a clearly nested derivative" but the existing shape already works well as a nested object under `"preflight"`.
- **Protocol guidance on stdout**: Missing-socket guidance appears in the status report text/JSON on stdout, not on stderr. The status command always prints a structured report; stderr is reserved for unexpected errors.

## Test Evidence

```bash
/tmp/vbpub-topos-p32-venv/bin/python -m pytest topos/tests/test_daemon_status.py -v
# 10 passed in 3.12s

/tmp/vbpub-topos-p32-venv/bin/python -m pytest topos/tests -q
# 289 passed in 34.91s

/tmp/vbpub-topos-p32-venv/bin/python -m py_compile \
  topos/src/topos/daemon/status.py \
  topos/src/topos/daemon/__init__.py \
  topos/src/topos/cli.py \
  topos/tests/test_daemon_status.py
# clean, exit 0

PYTHONPATH=topos/src /tmp/vbpub-topos-p32-venv/bin/python -c "
from topos.daemon.status import build_daemon_status
from topos.daemon.deploy import DEFAULT_DAEMON_SOCKET

# Missing default socket
report = build_daemon_status(DEFAULT_DAEMON_SOCKET / 'nonexistent.sock')
assert report.ok is False
assert report.protocol.ok is False
assert 'Cannot connect' in report.protocol.message
assert 'preflight' in report.protocol.message
print('Missing socket guidance: OK')

# Protocol status JSON
j = report.to_jsonable()
assert j['ok'] is False
assert 'protocol' in j
assert 'socket' in j
assert 'group' in j
print('Status JSON shape: OK')

# Text output
text = report.to_text()
assert 'topos daemon status' in text
assert 'DEGRADED' in text
print('Status text output: OK')
"

# Controller review validation
PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest \
  topos/tests/test_daemon_status.py \
  topos/tests/test_daemon_client.py \
  topos/tests/test_daemon_deploy.py \
  topos/tests/test_attach_cli.py -q
# 46 passed in 15.70s

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m py_compile \
  topos/src/topos/daemon/status.py \
  topos/src/topos/daemon/__init__.py \
  topos/src/topos/cli.py \
  topos/tests/test_daemon_status.py
# clean, exit 0

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q
# 290 passed in 34.63s

# Post-merge main validation
PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest \
  topos/tests/test_daemon_status.py \
  topos/tests/test_daemon_client.py \
  topos/tests/test_daemon_deploy.py \
  topos/tests/test_attach_cli.py -q
# 46 passed in 16.18s

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q
# 303 passed in 37.10s after P32/P33 merge
```

## Known Gaps

- Protocol check uses the default `DaemonClient` timeout (5s); no `--timeout` flag.
- Preflight check is only attempted once; no retry logic for transient failures.
- No JSON-schema enforcement on the status JSON output.

## Contract-Change Proposals

None. P32 is entirely additive and changes no shared interfaces.
