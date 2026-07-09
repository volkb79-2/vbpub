# P30 Report — Daemon Default Client UX

## What Was Built

- **Default-socket `--attach`**: `--attach` now uses `nargs='?'` with
  `const=DEFAULT_DAEMON_SOCKET` so `groop --attach --once --json` (no explicit
  path) attaches to `/run/groop/groop.sock`. Existing `groop --attach /custom.sock`
  continues to work unchanged.
- **`groop daemon current`**: New read-only subcommand that prints one canonical
  frame JSON payload from the daemon socket. Supports `--json`, `--socket PATH`
  (defaults to `DEFAULT_DAEMON_SOCKET`), and `--pretty-json`. Uses existing
  `_print_frame_json` for canonical output.
- **Tests (9 net new after controller review)**:
  - 4 default-socket attach tests: argparse parsing, in-process fixture broker
    using bare `--attach`, backward-compatible explicit socket, UI smoke flag.
  - 5 daemon current tests: canonical JSON, pretty-json, missing-socket error
    (no live fallback), default args parsing, custom socket args parsing.
- **Docs updated**: `README.md` (P30 → Done), `ROADMAP.md` (P30 → done),
  `STATUS.md` (added P30 items, updated v2 %, updated Quality Gate),
  `DAEMON.md` (default-socket attach + daemon current command sections).

## Worktree

- Branch: `feat/groop-p30-daemon-default-client`
- Worktree: `/home/vb/volkb79-2/vbpub/.worktrees/-groop-p30-daemon-default-client`
- Python: `/tmp/vbpub-groop-p30-venv/bin/python` (Python 3.13.5)

## Deviations from Handoff

- The handoff suggested a possible narrow helper to deduplicate "current frame
  then JSON" logic. I chose to keep the implementations separate because the
  attach path has complex validation (--replay/--cgroup-root/--record rejections)
  that doesn't apply to `daemon current`. The shared `_print_frame_json` helper
  already ensures canonical output.

## Test Evidence

```bash
/tmp/vbpub-groop-p30-venv/bin/python -m pytest groop/tests/test_attach_cli.py groop/tests/test_daemon_deploy.py -v
# 24 passed in 11.04s

PYTHONPATH=groop/src /tmp/vbpub-groop-p30-venv/bin/python -m pytest groop/tests/test_attach_cli.py groop/tests/test_daemon_deploy.py -q
# 23 passed in 11.11s after controller review

/tmp/vbpub-groop-p30-venv/bin/python -m pytest groop/tests -q
# 271 passed in 32.34s

PYTHONPATH=groop/src /tmp/vbpub-groop-p30-venv/bin/python -m pytest groop/tests -q
# 270 passed in 32.21s after controller review

/tmp/vbpub-groop-p30-venv/bin/python -m py_compile \
  groop/src/groop/cli.py \
  groop/tests/test_attach_cli.py \
  groop/tests/test_daemon_deploy.py
# clean, exit 0

PYTHONPATH=groop/src /tmp/vbpub-groop-p30-venv/bin/python -m py_compile groop/src/groop/cli.py groop/tests/test_attach_cli.py groop/tests/test_daemon_deploy.py
# clean, exit 0 after controller review

PYTHONPATH=groop/src /tmp/vbpub-groop-p30-venv/bin/python -c "
from groop.cli import parse_args
from groop.daemon.deploy import DEFAULT_DAEMON_SOCKET
args = parse_args(['--attach', '--once', '--json'])
assert args.attach == DEFAULT_DAEMON_SOCKET
print('--attach --once --json: OK')
args = parse_args(['--attach', '/tmp/custom.sock'])
assert args.attach.name == 'custom.sock'
print('--attach /tmp/custom.sock: OK')
args = parse_args(['--once'])
assert args.attach is None
print('no --attach: OK')
"

# CLI smoke: daemon current against fixture broker (in-process)
PYTHONPATH=groop/src:groop/tests /tmp/vbpub-groop-p30-venv/bin/python -c "
import json, threading
from pathlib import Path
from groop.daemon import FrameBroker, serve_unix_socket
from conftest import fixture_frame
sock = Path('/tmp/p30-test.sock')
if sock.exists(): sock.unlink()
server = serve_unix_socket(sock, FrameBroker([fixture_frame()]))
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
from groop.cli import _main_daemon
import sys, io
sys.stdout = io.StringIO()
code = _main_daemon(['current', '--socket', str(sock)])
out = sys.stdout.getvalue()
sys.stdout = sys.__stdout__
server.shutdown(); server.server_close(); sock.unlink(missing_ok=True)
assert code == 0
payload = json.loads(out)
assert 'schema_version' in payload
print('groop daemon current: OK')
"
```

## Known Gaps

- Socket discovery is limited to `DEFAULT_DAEMON_SOCKET`; no auto-discovery of
  alternate sockets or environment variable overrides.
- No `EnvironmentFile` or config file override for the default socket path.
- `daemon current` always outputs JSON; `--json` is accepted for explicitness
  and compatibility with the handoff examples, but text/table rendering is not
  implemented.
- No audit log or `--admin` gating for `daemon current` (it's a read-only
  command using the same daemon protocol).

## Contract-Change Proposals

None. P30 is entirely additive and changes no shared interfaces.
