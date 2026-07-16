# P22 Report

## What Was Built

- Added `topos/src/topos/daemon/deploy.py` with a read-only daemon deployment
  preflight helper.
- Added `topos daemon preflight [--socket PATH] [--group NAME] [--json]` to
  `topos/src/topos/cli.py`.
- Added packaged deployment templates under
  `topos/src/topos/assets/systemd/`:
  - `topos.service`
  - `topos.tmpfiles`
- Updated `topos/pyproject.toml` so the templates are included in wheels.
- Added focused tests for:
  - usable and unsafe preflight states,
  - JSON and text CLI output,
  - no mutation/systemd invocation in the preflight helper,
  - packaged template availability.
- Updated `topos/docs/DAEMON.md`, `topos/docs/STATUS.md`,
  `topos/docs/ROADMAP.md`, and `topos/README.md` to reflect the deployed
  preflight slice.

## Deviations

- The service template uses `/usr/bin/env topos daemon serve ...` rather than
  assuming one fixed install prefix. That keeps the operator artifact portable
  across editable, wheel, and packaged installs.
- The preflight helper reports the socket/runtime-dir/group state only; it does
  not try to create or repair anything.

## Contract Changes

- None.

## Test Evidence

```bash
/tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests/test_daemon_deploy.py -q
# 4 passed in 1.39s

/tmp/vbpub-topos-p13-venv/bin/python -m py_compile \
  topos/src/topos/daemon/deploy.py \
  topos/src/topos/cli.py \
  topos/tests/test_daemon_deploy.py
# no output

/tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q
# 108 passed in 25.60s

PYTHONPATH=/tmp/vbpub-topos-p22-daemon-deployment/topos/src \
  /tmp/vbpub-topos-p13-venv/bin/python - <<'PY'
  ...
PY
# {"can_connect": true, "checks": ["runtime_dir", "daemon_group", "socket", "connect"], "ok": true, "socket_present": true}

PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli daemon preflight --socket /tmp/nonexistent-topos-preflight.sock --json
# exit 1 as expected for failed checks; ok=False socket_present=False checks=['runtime_dir', 'daemon_group', 'socket']

/tmp/vbpub-topos-p13-venv/bin/python -m pip wheel ./topos -w /tmp/topos-p22-dist --no-deps
# wheel contains topos/assets/systemd/topos.service and topos/assets/systemd/topos.tmpfiles
```

## Known Gaps

- The templates are packaged, but installation/enabling is still an operator
  action rather than an automated installer.
- Production daemon installation automation and service hardening remain future
  work.
- The preflight command is intentionally read-only and does not attempt any
  remediation.
- The daemon socket boundary remains the only authorization layer in this
  slice; there is no extra auth or policy engine yet.

## Controller Merge Review

- Feature commit reviewed and amended: `3d88a7c`.
- Merge commit: `d535b1e`.
- Post-merge validation from `main`:
  - `/tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests/test_daemon_deploy.py -q` -> `4 passed in 1.34s`
  - `/tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q` -> `108 passed in 24.82s`
  - `/tmp/vbpub-topos-p13-venv/bin/python -m py_compile topos/src/topos/daemon/deploy.py topos/src/topos/cli.py topos/tests/test_daemon_deploy.py` -> passed
  - `PYTHONPATH=topos/src /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli daemon preflight --socket /tmp/nonexistent-topos-preflight-main.sock --json` -> exit `1`, `ok=False`, `socket_present=False`
  - `/tmp/vbpub-topos-p13-venv/bin/python -m pip wheel ./topos -w /tmp/topos-p22-main-dist --no-deps` -> wheel contains both systemd assets
