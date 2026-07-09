# P25 Report — Daemon Deployment Install Plan

## What Was Built

- Added install-plan data model and helper to `groop/src/groop/daemon/deploy.py`:
  - `InstallPlanStep` and `DaemonInstallPlan` frozen dataclasses
  - `build_install_plan()` — constructs a plan with 7 ordered operator steps
  - `install_plan_to_jsonable()` — deterministic JSON serialization
  - `render_install_plan_text()` — human-readable text output with copy/pasteable commands
  - `_read_asset()` — loads packaged templates via `importlib.resources`
  - New constants: `DEFAULT_SERVICE_DEST`, `DEFAULT_TMPFILES_DEST`, `SERVICE_ASSET`, `TMPFILES_ASSET`
- Added CLI subcommand `groop daemon install-plan` to `groop/src/groop/cli.py`:
  - Flags: `--socket`, `--group`, `--service-dest`, `--tmpfiles-dest`, `--json`
  - Exit `0` for successful rendering, `2` for CLI/rendering errors
  - Text output is concise copy/pasteable plan; JSON output is deterministic
- Updated documentation:
  - `docs/DAEMON.md` — references `install-plan` before the deployment checklist
  - `docs/STATUS.md` — adds install-plan to Implemented, updates Quality Gate
  - `docs/ROADMAP.md` — marks P25 as done with handoff/report links
  - `README.md` — marks P25 as Done in the work package table
- Added 7 focused tests in `tests/test_daemon_deploy.py`:
  1. `test_install_plan_deterministic_defaults` — default plan is deterministic JSON/text
  2. `test_install_plan_custom_args` — custom socket, group, dest paths flow through
  3. `test_install_plan_contains_correct_template_content` — template content in plan matches packaged assets
  4. `test_install_plan_steps_reference_every_phase` — all 7 steps present, commands expected
  5. `test_install_plan_cli_json` — `--json` CLI emits valid JSON with exit 0
  6. `test_install_plan_cli_text` — text CLI prints ordered steps, warnings, no stderr
  7. `test_install_plan_does_not_mutate_host` — monkeypatched chown/chmod/subprocess.run prove no mutation

## Deviations

- Step commands for template installation use heredocs with rendered template
  content. This makes custom `--socket` and `--group` plans self-consistent
  instead of copying the default packaged templates unchanged.
- The embedded `service_content` and `tmpfiles_content` in JSON output are the
  rendered full template text. The handoff said "source asset names" but
  including the content lets operators review the exact files before writing.
- Step 2 (`usermod`) uses `<username>` as placeholder — the handoff did not specify a mechanism for listing approved users, so the operator must fill in actual usernames.

## Contract Changes

- None.

## Test Evidence

```bash
python3 -m py_compile groop/src/groop/daemon/deploy.py groop/src/groop/cli.py groop/tests/test_daemon_deploy.py
# (no output — clean)

python3 -m pytest groop/tests/test_daemon_deploy.py -v
# 11 passed in 1.60s

python3 -m pytest groop/tests -q
# 177 passed in 27.93s

PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m groop.cli daemon install-plan
# groop daemon install plan
# ============================================================
# socket path : /run/groop/groop.sock
# daemon group : groop
# service unit : /etc/systemd/system/groop.service
# tmpfiles conf: /etc/tmpfiles.d/groop.conf
# --- plan steps (read-only; no host mutation) ---
# Step 1: Create the system group 'groop' ...
# ... (7 steps, warnings, PLAN disclaimer)
# This is a PLAN only. No files were written and no system state was changed.

PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m groop.cli daemon install-plan --json > /dev/null
# exit 0; valid JSON with plan, steps, warnings, service_content, tmpfiles_content

PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m groop.cli daemon install-plan --group custom --socket /tmp/custom/groop.sock --json
# rendered service_content uses Group=custom and --socket /tmp/custom/groop.sock;
# rendered tmpfiles_content creates /tmp/custom with root:custom
```

## Known Gaps

- The plan is a guide only — there is no automated installer that executes the steps. The operator must copy and run each command manually.
- Step 2 (`usermod -aG groop <username>`) uses a placeholder; no user-discovery or batch-add mechanism is provided.
- No distro-specific logic: the plan assumes systemd with `groupadd`/`usermod`/`systemctl` commands (Debian/Ubuntu/RHEL compatible). BSD or non-systemd systems are not covered.
- Custom socket paths outside `/run/groop` are rendered into the service and
  tmpfiles content, but operators should still review systemd `RuntimeDirectory`
  behavior for their target host.
- The command does not inspect host state (the preflight command is the companion for that).

## Controller Merge Review

- Feature commit(s) on `feat/groop-p25-daemon-install-plan`.
- Pre-merge validation:
  - `python3 -m pytest groop/tests/test_daemon_deploy.py -v` -> `11 passed in 1.60s`
  - `python3 -m pytest groop/tests -q` -> `177 passed in 27.93s`
  - `python3 -m py_compile groop/src/groop/daemon/deploy.py groop/src/groop/cli.py groop/tests/test_daemon_deploy.py` -> clean
  - `python3 -m groop.cli daemon install-plan --json | python3 -m json.tool > /dev/null` -> exit 0, valid JSON
