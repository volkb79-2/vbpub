# P22 Report

## What Was Built

- Added `groop/src/groop/daemon/deploy.py` with a read-only daemon deployment
  preflight helper.
- Added `groop daemon preflight [--socket PATH] [--group NAME] [--json]` to
  `groop/src/groop/cli.py`.
- Added packaged deployment templates under
  `groop/src/groop/assets/systemd/`:
  - `groop.service`
  - `groop.tmpfiles`
- Updated `groop/pyproject.toml` so the templates are included in wheels.
- Added focused tests for:
  - usable and unsafe preflight states,
  - JSON and text CLI output,
  - no mutation/systemd invocation in the preflight helper,
  - packaged template availability.
- Updated `groop/docs/DAEMON.md`, `groop/docs/STATUS.md`,
  `groop/docs/ROADMAP.md`, and `groop/README.md` to reflect the deployed
  preflight slice.

## Deviations

- The service template uses `/usr/bin/env groop daemon serve ...` rather than
  assuming one fixed install prefix. That keeps the operator artifact portable
  across editable, wheel, and packaged installs.
- The preflight helper reports the socket/runtime-dir/group state only; it does
  not try to create or repair anything.

## Contract Changes

- None.

## Test Evidence

```bash
/tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests/test_daemon_deploy.py -q
# 4 passed in 1.39s

/tmp/vbpub-groop-p13-venv/bin/python -m py_compile \
  groop/src/groop/daemon/deploy.py \
  groop/src/groop/cli.py \
  groop/tests/test_daemon_deploy.py
# no output

/tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests -q
# 108 passed in 25.60s

PYTHONPATH=/tmp/vbpub-groop-p22-daemon-deployment/groop/src \
  /tmp/vbpub-groop-p13-venv/bin/python - <<'PY'
  ...
PY
# {"can_connect": true, "checks": ["runtime_dir", "daemon_group", "socket", "connect"], "ok": true, "socket_present": true}

PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli daemon preflight --socket /tmp/nonexistent-groop-preflight.sock --json
# exit 1 as expected for failed checks; ok=False socket_present=False checks=['runtime_dir', 'daemon_group', 'socket']

/tmp/vbpub-groop-p13-venv/bin/python -m pip wheel ./groop -w /tmp/groop-p22-dist --no-deps
# wheel contains groop/assets/systemd/groop.service and groop/assets/systemd/groop.tmpfiles
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
  - `/tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests/test_daemon_deploy.py -q` -> `4 passed in 1.34s`
  - `/tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests -q` -> `108 passed in 24.82s`
  - `/tmp/vbpub-groop-p13-venv/bin/python -m py_compile groop/src/groop/daemon/deploy.py groop/src/groop/cli.py groop/tests/test_daemon_deploy.py` -> passed
  - `PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli daemon preflight --socket /tmp/nonexistent-groop-preflight-main.sock --json` -> exit `1`, `ok=False`, `socket_present=False`
  - `/tmp/vbpub-groop-p13-venv/bin/python -m pip wheel ./groop -w /tmp/groop-p22-main-dist --no-deps` -> wheel contains both systemd assets
