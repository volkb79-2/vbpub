# P21 Report

## What Was Built

- Added `groop/src/groop/actions/` with allowlisted Docker/systemd action
  preview planning and JSONL preview audit logging.
- Added `groop action preview --kind KIND --target TARGET [--admin] [--json]
  [--audit-log PATH]`.
- Kept actions preview-only: no subprocess, Docker, systemctl, shell execution,
  daemon protocol mutation, or host mutation.
- Added focused tests in `groop/tests/test_actions.py`.
- Updated `README.md`, `docs/ROADMAP.md`, `docs/STATUS.md`, and
  `docs/OPERATIONS.md`.

## Reasonix Quality Note

Reasonix successfully created and used the required `/tmp` worktree and kept
`main` clean. Its first implementation needed controller repair: it appended
CLI functions after the `__main__` guard, created source `__pycache__` files,
claimed docs were updated before they were, and had an over-broad no-import
test. The resulting branch is controller-repaired and focused-test clean, but
it still needs normal controller review and full-suite validation before merge.

## Test Evidence

```bash
/tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests/test_actions.py -q
# 30 passed in 0.21s

/tmp/vbpub-groop-p13-venv/bin/python -m py_compile groop/src/groop/actions/__init__.py groop/src/groop/actions/catalog.py groop/src/groop/actions/preview.py groop/src/groop/actions/audit.py groop/src/groop/cli.py groop/tests/test_actions.py
# passed

PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli action preview --kind docker-restart --target c1 --admin --json
# preview JSON with argv ["docker", "restart", "c1"]

PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli action preview --kind docker-restart --target c1
# exit 2, admin mode is not enabled
```

## Known Gaps

- Full `groop/tests` suite has not yet been run after controller repair.
- Branch has not been reviewed or merged.
- Real Docker/systemd execution remains intentionally out of scope.
- TUI `k` remains disabled; no admin preview modal was added.
