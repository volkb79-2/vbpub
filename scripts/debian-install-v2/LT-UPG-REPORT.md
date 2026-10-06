# LT-UPG report

Package: fix LT-F-v1001-10 + run unattended-upgrades once during the install.
Branch `lt-upg` from `073eb08e3`. Nothing here was run on a live host; the
claims below are what unit tests and the gates cover.

## 1. LT-F-v1001-10: backports origin pattern

- Fix: `origin=Debian Backports,codename=<release>-backports`
  (`installer.py` `_unattended_upgrade_origins`). Chosen over `a=stable-backports`
  because the codename is stable across the release's life while the Suite
  becomes `oldstable-backports` after the next release; Origin is the field that
  was wrong. Values with a space are fine inside the quoted apt-config string.
- Every pattern verified against Release fields (fixtures in
  `tests/test_lt_upg.py` `RELEASE_STANZAS`):

  | stanza | Origin | Label | Suite | Codename | matched by |
  |---|---|---|---|---|---|
  | trixie | Debian | Debian | stable | trixie | `origin=Debian,codename=trixie` |
  | trixie-updates | Debian | Debian | stable-updates | trixie-updates | `...codename=trixie-updates` |
  | trixie-security | Debian | Debian-Security | stable-security | trixie-security | `...codename=trixie-security,label=Debian-Security` |
  | trixie-backports (lane-captured) | Debian Backports | Debian Backports | stable-backports | trixie-backports | the fixed pattern only |
  | testing | Debian | Debian | testing | forky | `origin=Debian,suite=testing` |
  | unstable | Debian | Debian | unstable | sid | `origin=Debian,suite=unstable` |

  Only the backports pattern was wrong. Source of the values: the backports and
  Docker stanzas come from the lane (`LT-07/uu-origin-evidence.txt`, apt
  PackageFile dumps); the others are Debian's published Release fields from
  knowledge (not captured on the lane except trixie: `Origin: Debian, Label:
  Debian, Suite: stable, Codename: trixie`). The legacy
  `codename=<release>,label=Debian-Security` security line matches no real
  trixie stanza (security is `<release>-security`); it is harmless and was kept.
- Test: a matcher with unattended-upgrades semantics (comma-separated
  key=value, keys o/origin, l/label, a/suite/archive, n/codename; exact match)
  evaluates the generated patterns against each stanza: full allows all six,
  security-only allows only trixie-security, Docker / bookworm / bookworm-backports
  are never allowed, and every full pattern is live. A power check shows the
  old pattern does not match the real backports stanza.

## 2. Install-time unattended-upgrade run

- Where: `_stage1`, after `_configure_apt_auto_upgrade` (sources, pins, 51-config
  written; apt timers held) and BEFORE `_plan_root_shrink` and `_reboot`. The
  existing stage1 reboot is reused; no reboot added.
- What: `unattended-upgrade -v` through `_run_lock_retry` (same bounded
  lock-contention retry as `_apt_get`). It takes its lock wait from apt config:
  `DPkg::Lock::Timeout "600";` is now written into
  `51-vbpub-unattended-upgrades` (I could not confirm in this environment that
  unattended-upgrade has a `-o` switch, so config is used; side effect: later
  interactive apt/dpkg on the host also waits up to 600 s for the dpkg lock
  instead of failing at once - decision flag below).
  Mode comes from that config: full = all origins incl. backports kernel,
  security-only = security only. `notify-only`: no run, `apt-get -s full-upgrade`
  counts `Inst` lines (`apt_upgrade_at_install` step `skipped`, count in detail).
  `run_apt_auto_upgrade=false` or `apt_upgrade_at_install=false`: skipped.
- Config: `apt_upgrade_at_install` (default true); wizard field (section
  "updates"); env var `APT_UPGRADE_AT_INSTALL` in `bootstrap-remote.py`
  `_BOOL_FIELDS`; README. The cli-extended surface catalog covers CLI options,
  not config keys, so it needed no change (gate result below).
- Outcome: state step `apt_upgrade_at_install` = `<mode>: N package(s) upgraded;
  kernel <running> -> <highest /boot/vmlinuz>` (N parsed from
  "Packages that will be upgraded:"; boot kernel = highest installed vmlinuz by
  numeric version, approximating GRUB's default). The same text is appended to
  the single stage1-complete post (Mattermost/Telegram) - NOT a separate post,
  because `test_install_and_resume_send_expected_stage_boundary_messages` pins
  one post per milestone and my first version broke it. In telegram-verbose mode
  the step also posts via `_mark_step` as every step does.
- `never_reboot` (or `auto_reboot_after_stage1=false`) with a new kernel: detail and
  notification say "reboot required for the new kernel (reboot disabled by
  configuration)"; also copied into the `reboot` step detail.
- A failed run (non-lock error) is recorded `failed`, reported in the post, and the
  install continues (the upgrade is not essential to a working host). Say if you
  want it fatal.
- INITRAMFS, decision: order the upgrade BEFORE the hook install, and keep
  `update-initramfs -u -k all` (already there). Justification: any kernel the
  upgrade installs gets its initrd from its own postinst WITHOUT the hook; the
  later `-k all` rebuild then adds the hook to every kernel. The reverse order
  would also work (postinst runs initramfs-tools hooks) but leaves kernel
  postinst as the only thing guarding the invariant. New: after the rebuild,
  `lsinitramfs` is run on every `/boot/initrd.img-*` and the install fails
  loudly if any lacks `vbpub-root-shrink` (the r1002 silent no-op class).
  Cleanup in stage2 already used `-k all`; it now also lists every initrd and
  records `WARNING hook still in initramfs of: ...` in the step detail (not
  fatal; a stale hook no-ops). New allowlist entries in `actions.py`:
  `unattended-upgrade -v`, `lsinitramfs`, `apt-get full-upgrade` (used only with `-s`).
- needrestart is configured `restart=a`, so the install-time run may restart
  services (sshd, docker with live-restore) during stage1. Not exercised live.

## 3. Decision ask: testing/unstable in `full` (NO behaviour change made)

Evidence (LT-07 `uu-dryrun-diagnosis.txt`, `trigger-apt-daily-upgrade.txt`):
- The installer installs NOTHING from testing/unstable. Pins are testing 100,
  unstable 50, stable 500, so apt's own candidate for every package is stable
  (or backports 600). The lane's read-only `apt-get -s full-upgrade` listed only
  backports packages (kernel + 11), no libnghttp3/libngtcp2/curl.
- unattended-upgrade, however, logged `Checking: libnghttp3-9=1.17.0-1
  (testing, unstable)` and `libcurl4t64=8.22.0-1 (testing)`: its candidate for
  those packages was the testing version, i.e. it did not follow the pin. The
  evidence is consistent with unattended-upgrade choosing the highest version
  offered by ANY allowed origin and ignoring pin priorities (the pins only
  exclude non-allowed origins for it). I have not read the unattended-upgrade
  source on a host to confirm; a read-only check on v1001:
  `grep -n candidate /usr/bin/unattended-upgrade`, and
  `apt-cache policy curl libnghttp3-9`.
- Effect: the `testing`/`unstable` origins make u-u a partial `testing`
  upgrader. Libraries move to forky versions, then dependents (curl,
  libcurl*) are "kept back because a related package is kept back": a mixed
  set. After the backports fix the same mechanism may also prefer a
  testing/unstable `linux-image-amd64` over the backports one if it is newer.
  This is a real risk to the intended "full moves to the backports kernel".
- Options:
  1. Drop testing and unstable from the `full` origins (keep release, updates,
     security, backports). Pins already say testing/unstable are "visible, never
     auto-selected"; this makes u-u agree. Smallest change, removes the mixed
     set. Recommended.
  2. Keep them, add `Unattended-Upgrade::Package-Blacklist` for core libs and
     kernel metapackages. Brittle.
  3. Leave as is. Accept mixed sets and a possible testing kernel.
  4. Rename `full` semantics: `full` = stable+updates+security+backports and add
     a separate explicit opt-in mode (e.g. `full-testing`) for the old list.
- Decision also needed: keep `DPkg::Lock::Timeout` in the 51-config (above) or
  drop it in favour of a run-scoped mechanism.

## 4. Tests (`tests/test_lt_upg.py`, 44 tests)

- pattern-vs-stanza matching; install-time run in full / security-only, skipped
  in notify-only (simulation count) / run_apt_auto_upgrade off / option off;
  ordering unattended-upgrades install < 51 config write < run < reboot, and
  run < hook write < `update-initramfs -k all` < reboot (Case B fixture);
  exactly one reboot; single post carrying the summary; never_reboot report;
  no-kernel-change makes no reboot claim; zero count; failure continues;
  lock retry; hook missing from second / older kernel fails; boot-kernel version
  ordering (7.2.10 > 7.2.6 > 6.12.111); cleanup lists every kernel;
  config default/validation, env mapping, wizard field.
- Existing test updated: `test_lt_apt.test_only_one_source_call_site_...`
  asserted the literal `argv = [...]` line that `_apt_get` no longer contains
  (now calls `_run_lock_retry`); the apt-get path still appears once.

## 5. Plants (each applied, run against `tests/test_lt_upg.py`, reverted)

| plant | result |
|---|---|
| backports pattern back to `origin=Debian,...` | 3 failed |
| upgrade call moved after `_reboot()` | 5 failed |
| upgrade call removed | 8 failed |
| security-only returns the full origin list | 5 failed |
| hook verification covers only the first kernel | 1 failed (`test_hook_missing_from_a_second_kernel_fails_the_install`) |

## 6. Gates

Run on the committed tree (HEAD before this report edit), foreground under
flock/nice/ionice, verdicts read in a separate step from the output files:
- `scripts/debian-install-v2/run-gate.py --worktree ... r0-r1`: lane verdict PASS,
  exit 0, 941 passed / 11 skipped (log `/tmp/run-gate/lanes/r0-r1/a741735242a8387fcb47c537f344a7c7.log`).
- `scripts/netcup/run-gate.py --worktree ... suite`: lane verdict PASS, exit 0,
  716 passed (log `/tmp/run-gate/lanes/suite/94fd3ac93c1dc01cf4f3b3ee6c37302d.log`).
- cli-extended surface: the existing CLI-case/surface tests ran inside r0-r1 and
  passed; no CLI option was added.
- No mutation (assay) lane was run.

## Not verified

- Real unattended-upgrade behaviour (output format, `DPkg::Lock::Timeout` honoured,
  the corrected pattern actually installing and booting 7.x, zswap/iocost on 7.x).
- Release-stanza values other than backports/Docker/trixie are from knowledge.
- The "u-u ignores pins" explanation in section 3 is a hypothesis fitting the log lines.

## Controller rulings (applied; supersede sections 1-3 where they differ)

1. Testing/unstable: option 1. `suite=testing` and `suite=unstable` are removed from
   the unattended-upgrades origins; `full` now means release, `-updates`, `-security`,
   `-backports`. Apt sources and pins are untouched (manual `-t testing` still works).
   Tests: testing/unstable stanzas moved to the never-allowed set (checked in full and
   security-only); `test_gstammtisch_incorporation` full-mode test inverted. Plant
   (testing pattern re-added): killed by `test_foreign_origins_never_allowed[full-testing]`
   and `test_apt_auto_upgrade_full_mode_excludes_testing_and_unstable` (2 failed), reverted.
   README documents the new meaning of `full`.
2. `DPkg::Lock::Timeout "600";` stays in the 51 config; README states the side effect
   (interactive apt waits up to 600 s for the dpkg lock while unattended-upgrades runs).
3. A failed install-time upgrade stays non-fatal: step status is now `warned` (was
   `failed`) and the stage1-complete post carries "⚠️ apt upgrade failed (<mode>),
   install continued". Test updated.

Gates for the rulings (run on the committed tree before this line was added):
`r0-r1` PASS exit 0, 939 passed / 11 skipped; netcup `suite` PASS exit 0, 716 passed.

