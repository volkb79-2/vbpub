# LT-EARLY report (LT-F-r1002-02, MAJOR) — stacked on LT-UPG

## Cause (confirmed by code and by reproduction)

`bootstrap._install()` built the `Installer`, called `installer.show_plan()`, and only then
called `installer.install()`, the one place with failure handling. `show_plan()` ->
`_resolve_swap_plan()` raises `InstallerError` for `preserve_root_size_gb=1` (Case B floor below the
filesystem minimum). Nothing caught it, so the process exited non-zero with no `state.json`, no
controller key (the key step is the first thing `_stage1()` does, which never ran) and no post.
Reproduced: the new tests run against the pre-fix `installer.py`/`bootstrap.py` failed 8 of 15
(LT-05 case, show_plan raise, host-inspection raise, both parse-post cases, nested guard).

## Fix (structural)

- `Installer.failure_guard(phase)` (context manager): any exception inside records a failed
  `state.json` with the cause (creating the state when absent), ensures the controller key is on the
  host, re-enables apt timers, posts ONE failure notice, writes `failure_notified_at` when delivered
  (the dedup mark for LT-S2's OnFailure notifier), then re-raises. Nested guards report a given
  exception once (identity check). Dry runs write nothing; they still call `_notify` (a no-op) as before.
- `install()` now runs entirely under the guard (`save_new` included); the old inline handler is gone.
- `Installer.inspect()` split from `__init__`; `_install` constructs with `inspect_host=False` and runs
  `inspect()` + `show_plan()` inside the guard. Exit code is whatever the CLI gives a domain error
  (non-zero; message is the existing one-line `[ERROR]`).
- Key ordering: `_ensure_controller_key_after_failure()` installs the key when the `controller_ssh_key`
  step has not succeeded, idempotently. DECISION: it does this whenever `controller_ssh_pubkey` is
  configured, NOT only when `retain_controller_ssh_key` is set. Reason: the existing failure path
  already keeps the key regardless of retain (it is only removed on success), so an early failure now
  leaves the same state a late failure does. Reviewer: challenge if retain=false should differ.
- Config-PARSE errors: `_load_config(report_failure=True)` (install verb only) calls
  `_post_config_failure()`: reads only the notify settings leniently from the raw JSON, keeps those
  that are string-typed and valid (webhook URL, host label, telegram pair), and posts once through
  `Installer._notify` (same redaction). Exit code stays 2 with `[ERROR] invalid installation
  configuration: ...`. No state is written (there is no run yet). Read-only verbs and `--dry-run` never post.

## Audit: other raise-before-guard paths

| Path | Status |
|---|---|
| `_load_config` parse/validation (`ConfigError`) | best-effort post added; exit 2 |
| `Installer.__init__` host inspection (`_detect_release`, `_discover_root`) | now inside guard via `inspect()` |
| `show_plan()` / `_resolve_swap_plan` | now inside guard |
| `install()`: `state.save_new` before the old try | now inside guard |
| `install()`: initial-report notify | already wrapped in `except Exception` (unchanged) |
| `resume()` pre-try segment (state load, config reload, credential read, first `_notify`) and `_make_resume_installer` | NOT changed: runs under systemd, where the OnFailure notifier (LT-S2) posts since `failure_notified_at` is cleared on entry |

## Gaps not closed

- `bootstrap-remote.py` failures before the entrypoint exists (env parse `BootstrapError`, wheel/subtree
  download, non-root) cannot post: the notify code is not on the host yet. Would need a stdlib post in
  `bootstrap-remote.py` itself; not attempted.
- A parse error whose notify settings are themselves invalid or absent posts nothing (by design).
- The parse-error post for the Telegram backend shares the `_notify` path but is not covered by a test;
  only Mattermost is tested.
- Stage-one failure now also writes `failure_notified_at` (previously stage2 only). Harmless: `resume()`
  clears it on entry.

## Tests (`debian_install_v2/tests/test_lt_early.py`, 15 tests; `FakeInstaller` in `test_cli_review_cases.py` gained `failure_guard`/`inspect`)

Drive the real `main(["install", ...])` with the Case B host fake. LT-05 config: failed state with the
cause, key written, one post carrying the cause with no webhook text, `failure_notified_at` honoured by
`failure_notify.recently_notified`, non-zero exit. Also: show_plan raise, non-domain exception, host
inspection raise, stage-one failure (one post), nested guard once-only, parse error posts once / via
`--config-json` / nothing when notify settings unusable / unreadable file / dry run / read-only verb.

## Plants (each applied to committed code, run against the full unit suite, then reverted)

| Plant | Killed by |
|---|---|
| `show_plan` moved back outside the guard | 4 tests: both LT-05 tests, show_plan raise, non-domain show_plan raise |
| key step skipped on early failure (`_ensure_controller_key_after_failure` returns at once) | 4 tests: LT-05, show_plan raise, inspection raise, non-domain raise |
| double post (guard always handles, no once-per-exception check) | `test_nested_guards_report_one_exception_once_but_a_later_run_again` |

## Gate verdicts (code commit `2629963e5`)

- `scripts/debian-install-v2/run-gate.py --worktree .../lt-upg r0-r1`: PASS, exit 0 (954 passed, 11 skipped).
- `scripts/netcup/run-gate.py --worktree .../lt-upg suite`: PASS, exit 0 (716 passed).
