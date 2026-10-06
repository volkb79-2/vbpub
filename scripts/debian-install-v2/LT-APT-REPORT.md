# LT-APT report (LT-F-r1002-01, apt lock race; plus duplicated io_benchmark post)

Base `3f0b85095` (lt-2026-10-w9b). Branch `lt-apt`.

## Verified cause (by reading the code)
- Stage1 `_configure_apt_auto_upgrade` ran `systemctl enable --now apt-daily.timer apt-daily-upgrade.timer`
  (and Debian enables them by default anyway), so the persistent apt-daily-upgrade timer fires
  minutes after the stage2 reboot.
- `_packages` ran `apt-get update`/`install` with no lock timeout and no retry; exit 100 on a held lock.
- The installer never set `DEBIAN_FRONTEND=noninteractive` anywhere (the task assumed it did).
- Duplicate io_benchmark post: with `telegram_verbose_progress` on, `_mark_step("io_benchmark", ...)`
  posts the outcome AND the explicit `_notify(...)` right after it posts it again.

## Changes
1. `installer._apt_get` is now the only place that builds an apt-get argv; always
   `-o DPkg::Lock::Timeout=600` (`APT_LOCK_TIMEOUT_S`, a module constant, not a Config field: it is a
   safety margin, so no schema/wizard change). Call sites: `_packages` (update+install, used by every
   stage1/stage2 package step incl. oomd, fio/pv, docker), `_configure_apt` update loop. The
   notify-only `vbpub-apt-check` script also passes the option.
2. `actions.py` allowlist: for apt-get a `-o` must be followed by exactly
   `DPkg::Lock::Timeout=<1-5 digits>`; `-oX`, `--option`, any other `-o` value are refused.
   apt-get runs with `DEBIAN_FRONTEND=noninteractive` in its environment (set in `HostActions.run`).
3. Timers: `_hold_apt_timers()` (`systemctl disable --now` of both apt timers) at stage1 start and
   after the unattended-upgrades package step; `_release_apt_timers()` (`enable --now`, plus
   `vbpub-apt-check.timer` in notify-only mode) as the LAST stage2 step, after the swap health gate.
   Only when `run_apt_auto_upgrade` is on. Plain "enable without --now" was rejected: the timers
   would still start at the stage2 reboot, which is exactly where the race happened.
4. Retry: `DPkg::Lock::Timeout` covers the dpkg frontend lock, but I believe (from apt behaviour,
   NOT tested live here) `apt-get update`'s lists lock is not covered. So `_apt_get` retries up to
   3 attempts, 30 s apart, only when the error text contains an apt lock message, with a warning log
   line. Non-lock failures raise immediately.
5. io_benchmark: the explicit `_notify` calls (success and advisory failure) are made only when
   `telegram_verbose_progress` is off, since `_mark_step` already posts in verbose mode.
   Same pattern exists for `reboot` steps in `_reboot`; left unchanged (out of scope, noted).

## Tests (`debian_install_v2/tests/test_lt_apt.py`)
Table-driven lock option over the full dry-run planned list (3 config scenarios) plus a source check
that the apt-get path literal exists once; timers held before the first apt-get and enabled with
`--now` only after the last; allowlist rejects 9 `-o` shapes; noninteractive env; retry bounded /
non-lock not retried; io benchmark posted once (verbose on and off). Existing tests that pinned the
old apt-get argv were updated.

## Added from the v1001 lane
- LT-F-v1001-07: `Installer._secure_bootstrap_files()` runs first in `_stage1` and chmods 0600
  `/root/custom_script`, `/root/custom_script.output*` and `stage2_output` (skipped in dry-run, missing
  files ignored). The webhook text stays in the file (the provider owns it); README documents this.
  Tests: real chmod on a tmp dir, and call order (first in stage1).
- LT-F-v1001-08: CHECKED the code: nothing writes to `log_dir`; it is only read by the status/backup
  listing (`installer.py` ~408). Resolution is a doc fix (README "Stage2 log" section), not code:
  logs go to the provider's `custom_script.output*`, `stage2_output` and journald.

## Review round 1 fixes
- Test conditions: a test that a `disable --now` of the apt timers follows the unattended-upgrades
  package step (second hold); the io_benchmark post-once test now also covers the advisory-failure
  branch, verbose on and off.
- D1: `_restore_apt_timers_after_failure()` runs in the stage1 (`install`) and stage2 (`resume`)
  failure paths before the failure notice: best-effort `enable --now` of the two apt timers,
  logged, never raises, skipped in dry-run. Idempotent because stage2 is a new process.
- D2: the Debian apt timers are held at stage1 start and restored with `enable --now` at the end of
  stage2 regardless of `run_apt_auto_upgrade` (`vbpub-apt-check.timer` is added only when the step
  is on and the mode is notify-only).
- D3: running apt services are not stopped. `APT_LOCK_RETRY_ATTEMPTS = APT_LOCK_TIMEOUT_S // 30 + 1`
  (21 attempts, 20 sleeps of 30 s = 600 s), one warning log line per retry. Test asserts the total
  sleep equals the constant. The lists-lock claim is the reviewer's (apt 3.0.3); I did not test it.
- `_reboot`: in verbose mode the "Stage1 complete. Rebooting" milestone is skipped (the
  "reboot: scheduled ... delayed 60s" step post is the informative one); the reboot-disabled branch
  uses the silent `state.mark_step` so its explicit warn post is the only one. Test covers all four
  verbose x never_reboot combinations.

## Gate and plants
Verdicts and the plant table are in the hand-back message (from actual runs on the final commit).
