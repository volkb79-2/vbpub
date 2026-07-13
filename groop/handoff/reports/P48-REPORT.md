# P48 Report — Bounded Journald Inspection Snapshot

## What Was Built

- **Enhanced systemd target validation** in `catalog.py`:
  - Rejects option-like tokens starting with ``-`` (e.g. ``-u``, ``--unit``).
  - Allows ``+`` character (used in some systemd unit names).
  - Preview commands updated to use absolute ``/usr/bin/journalctl`` and
    ``/usr/bin/systemctl`` paths.

- **Added bounded journald read support** in `reader.py`:
  - `_JournaldRunResult` — frozen dataclass for bounded subprocess result
    (stdout, stderr, returncode, timed_out).
  - `_default_journald_runner()` — production runner using `subprocess.run`
    with `capture_output=True`, `shell=False`, `stdin=subprocess.DEVNULL`,
    bounded `timeout`, minimal environment, and safe UTF-8 decode with
    `errors="replace"`.
  - `_validate_journald_read_target()` — safety double-check on systemd unit
    names, rejecting paths, option-like tokens, and unsafe characters.
  - `_run_journald_snapshot()` — validates timeout and target, builds fixed
    absolute argv `("/usr/bin/journalctl", "--unit", target, "--no-pager",
    "--output=short-iso", "-n", str(max_lines))`, invokes the runner, and
    returns `InspectFilesReadResult` or `InspectFilesReadError`.
  - Wired `SYSTEMD_JOURNAL` into `build_inspect_read()` with injectable
    `journald_runner` and `journal_timeout` kwargs.

- **CLI update**: `groop inspect-files read --kind` help text now lists
  `systemd-journal` as a valid kind.

- **18 new tests** (`test_inspect_files.py`):
  - 5 systemd target validation tests: leading dash (`-u`), long option
    (`--unit`), rejection, and valid name acceptance (ssh.service, cron.service,
    docker.service, user@1000.service, sshd.socket, multi-user.target, etc.).
  - 13 journald content read tests: success, JSON format, text format, timeout
    error, nonzero exit error, runner OSError, empty/dash/path target rejection,
    line truncation, gating (--inspect-files/--admin), root requirement, and
    timeout validation.

- **Updated docs**: INSPECT-FILES.md (journald read contract, scope, examples),
  STATUS.md (P48 done, v2 estimate 65-70%), RELEASE-READINESS.md (subprocess
  limitation scoped), ROADMAP.md (P48 marked done).

## Worktree

- Branch: `feat/groop-p48-inspect-files-journal-snapshot`
- Worktree: `.worktrees/-groop-p48-inspect-files-journal-snapshot`
- Python: Python 3.14.6
- Environment: devcontainer

## Deviations from Handoff

None. All named contract requirements are met:

- [x] Support `systemd-journal` through the same `--inspect-files`, `--admin`,
      root, result, rendering, and audit/sensitivity posture as P45.
- [x] Validate a real systemd unit name and reject aliases that parse as
      options, paths, control text, globs, or multiple units.
- [x] Invoke only a fixed absolute `journalctl` argv, `shell=False`, with
      `--unit`, `--no-pager`, a deterministic output mode, and bounded line
      count. No follow mode. Use an injected runner for tests.
- [x] Bound timeout (default 30s, max 60s), bytes, lines, stdout/stderr, and
      errors. A timeout/nonzero exit is typed unavailable/error output and
      never falls back to arbitrary reads.
- [x] Add CLI, runner, gate, target, output-bound, timeout, and structural
      no-shell/no-mutation tests; no live journal dependency in the normal
      suite.
- [x] Update inspect/operations/readiness/status/measurements docs honestly.

## Test Evidence

### Focused inspect-files tests

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_inspect_files.py -q
# 132 passed in 0.85s
```

### Full suite

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 845 passed, 2 skipped in 121.53s
```

### Full-source py_compile

```bash
mapfile -d '' pyfiles < <(find groop/src/groop groop/tests -name '*.py' -print0)
python3 -m py_compile "${pyfiles[@]}"
# ALL COMPILE OK
```

## Known Gaps

- No follow/stream mode for journald (intentional — out of scope).
- No daemon transport for journald reads.
- No TUI integration for file inspection reads.
- Live journald requires root and a running journald — not tested in CI.
- Only `--output=short-iso` is used; other output formats are not exposed.

## Contract-Change Proposals

None. The API is additive and backward-compatible. The new `journald_runner`
and `journal_timeout` kwargs are keyword-only with defaults (None and 30.0).
