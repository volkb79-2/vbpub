# P31 Report — Daemon Client Error Guidance

## What Was Built

- Added `_format_daemon_error()` helper in `groop/src/groop/cli.py` that:
  - Preserves the original daemon client exception text
  - For default socket (`/run/groop/groop.sock`) failures: suggests `groop daemon preflight` and `groop daemon install-plan`
  - For custom socket failures: suggests `groop daemon preflight --socket <path>`
  - For protocol/response errors: suggests checking for compatible groop daemon and daemon logs
- Wired the helper into both `DaemonClientError` catch blocks:
  - `main()` attach path
  - `_main_daemon()` current path
- **Tests (9 new after controller review)**:
  - 6 attach error-guidance tests (default socket, custom socket, protocol,
    response, custom-socket CLI integration, bare default-socket CLI integration)
  - 3 daemon current error-guidance tests (default socket, custom socket, CLI integration)
- **Docs updated**: `README.md` (P31 → Done), `ROADMAP.md` (P31 → done), `STATUS.md` (added P31 items, updated Quality Gate), `DAEMON.md` (troubleshooting section with examples)

## Worktree

- Branch: `feat/groop-p31-daemon-client-errors`
- Worktree: `/home/vb/volkb79-2/vbpub/.worktrees/-groop-p31-daemon-client-errors`
- Python: `/tmp/vbpub-groop-p31-venv/bin/python` (Python 3.13.5)

## Deviations from Handoff

None. Implementation follows the handoff design exactly.

## Test Evidence

```bash
/tmp/vbpub-groop-p31-venv/bin/python -m pytest groop/tests/test_attach_cli.py groop/tests/test_daemon_deploy.py -v
# 31 passed in 10.56s

PYTHONPATH=groop/src /tmp/vbpub-groop-p31-venv/bin/python -m pytest groop/tests/test_attach_cli.py groop/tests/test_daemon_deploy.py groop/tests/test_daemon_client.py -q
# 35 passed in 12.07s after controller review

/tmp/vbpub-groop-p31-venv/bin/python -m pytest groop/tests -q
# 278 passed in 30.90s

PYTHONPATH=groop/src /tmp/vbpub-groop-p31-venv/bin/python -m pytest groop/tests -q
# 279 passed in 31.67s after controller review

/tmp/vbpub-groop-p31-venv/bin/python -m py_compile \
  groop/src/groop/cli.py \
  groop/tests/test_attach_cli.py \
  groop/tests/test_daemon_deploy.py
# clean, exit 0

PYTHONPATH=groop/src /tmp/vbpub-groop-p31-venv/bin/python -m py_compile groop/src/groop/cli.py groop/tests/test_attach_cli.py groop/tests/test_daemon_deploy.py
# clean, exit 0 after controller review

# Format helper smoke tests:
PYTHONPATH=groop/src /tmp/vbpub-groop-p31-venv/bin/python -c "
from groop.cli import _format_daemon_error
from groop.daemon.client import DaemonConnectError, DaemonProtocolError, DaemonResponseError
from groop.daemon.deploy import DEFAULT_DAEMON_SOCKET
from pathlib import Path

# Default socket connect error
msg = _format_daemon_error(
    DaemonConnectError('cannot connect to /run/groop/groop.sock: No such file or directory'),
    DEFAULT_DAEMON_SOCKET)
assert 'preflight' in msg and 'install-plan' in msg

# Custom socket connect error
msg = _format_daemon_error(
    DaemonConnectError('cannot connect to /tmp/custom.sock: Connection refused'),
    Path('/tmp/custom.sock'))
assert 'preflight --socket /tmp/custom.sock' in msg and 'install-plan' not in msg

# Protocol error
msg = _format_daemon_error(
    DaemonProtocolError('daemon at socket returned malformed JSON'),
    DEFAULT_DAEMON_SOCKET)
assert 'compatible groop daemon' in msg and 'daemon logs' in msg

# Response error
msg = _format_daemon_error(
    DaemonResponseError('daemon at socket returned an error: denied'),
    DEFAULT_DAEMON_SOCKET)
assert 'compatible groop daemon' in msg

print('All format smoke tests passed')
"
```

## Known Gaps

- Guidance is text-only on stderr; no JSON guidance field for `--json` consumers.
- No retries, auto-start, or service control — failures remain manual-diagnosis.
- Socket discovery is still limited to `DEFAULT_DAEMON_SOCKET`; no environment variable overrides.

## Contract-Change Proposals

None. P31 is entirely additive and changes no shared interfaces.
