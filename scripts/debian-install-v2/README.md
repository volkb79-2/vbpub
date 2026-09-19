# debian-install-v2

An idempotent, config-driven bootstrap installer for fresh Debian VPS hosts
(built and live-tested against Netcup Cloud VPS, but nothing in it is
Netcup-specific). Two-stage flow: stage1 partitions/configures the host and
schedules a reboot; stage2 resumes automatically after that reboot and
finishes the rest. Full success confirmed live end-to-end 2026-09-09 against
both starting-disk shapes (see "Case A vs. Case B" below).

Design/status docs elsewhere in this tree, not duplicated here:
`CASE-B-ROOT-SHRINK-DESIGN.md` (the offline root-shrink mechanism), `TODO.md`
+ `zswap-shrinker-threshold-feasibility.md` (open zswap-shrinker work),
`testing/vm/DESIGN.md` (the QEMU/TCG test-VM harness).

## What it actually does today

### Disk / swap

Two starting shapes, detected automatically:

- **Case A** — free space already exists after the root partition (the
  normal case when the disk is larger than the deployed image). Swap
  partitions are added directly into that free space.
- **Case B** — root fills the entire disk (the cloud-init default: `growpart`
  grows root to fill whatever disk it's given, so this is actually the more
  common real-world starting shape, not the edge case the name suggests).
  Handled via an offline shrink: an `initramfs-tools` `local-premount` hook
  that fires pre-mount, before root is ever mounted live, running
  `e2fsck` → `resize2fs` → `sfdisk` to shrink the root partition down to
  `preserve_root_size_gb`, then rebooting into the now-correctly-sized disk.

Swap is always native GPT partitions (`swap_file_count` of them, each sized
from `swap_disk_total_gb`), never swap files. Every partition write goes
through a plan → dry-run → apply → readback-verify → rollback-on-mismatch
cycle (`inuse_partition_editor.py`) — this live disk surgery on a mounted,
booting root with no console access is the tool's actual hard-to-replicate
value; see "Is this the right tool for everything?" below.

Config: `swap_disk_total_gb`, `swap_file_count`, `swap_priority`,
`swap_discard`, `preserve_root_size_gb`.

### zswap

A `zswap-config.service` (sysinit.target, before `swap.target`) sets the
compressor, zpool backend, and pool ceiling before any swap partition
activates:

- `zswap_compressor` (`zstd` / `lz4` / `lzo-rle`)
- `zswap_zpool` (`z3fold` / `zbud` / `zsmalloc`)
- `zswap_pool_percent` (5–60, ceiling as % of RAM)
- `accept_threshold_percent` fixed at 90 (resume-accepting hysteresis once
  the ceiling is hit)
- `shrinker_enabled` is written unconditionally (`echo Y`) — this turns on
  the kernel's memory-pressure-driven, per-cgroup zswap writeback shrinker.

  **Know this before relying on it under real memory pressure with
  multiple cgroups on the same host**: a separate live incident
  (`TODO.md`, `zswap-shrinker-threshold-feasibility.md`, gstammtisch game
  host, 2026-09-08) found this shrinker has no per-cgroup floor and no
  rate limit — under sustained pressure it can walk a *single* selected
  cgroup's zswap pool from a healthy level to **0%** in seconds to a few
  minutes (observed: a 2.7GB pool drained in ~150s, in-game FPS crashed
  25→8.3, a sibling cgroup untouched the whole time), and it does not
  recover once disabled. A userspace fill-watermark governor to bound this
  is designed (`zswap-shrinker-threshold-feasibility.md` §7) but **not
  implemented** — currently nothing paces or floors this on hosts v2
  provisions.

### THP, cgroup2, sysctls

`thp-config.service` (same sysinit-early timing as zswap). cgroup2 mount
flags `memory_recursiveprot`, `nsdelegate`. `vm.swappiness` (default 50 —
deliberately not the Linux default of 60, chosen to favor keeping zswap's
compressed tier full before spilling to real disk, without going so low
that RAM pressure gets reclaimed too late; see the comment at
`config.py`'s `vm_swappiness` field for the full tradeoff).

### apt

Own `debian.sources` (main/contrib/non-free/non-free-firmware across
release + `-updates` + `-security` + `-backports`), `apt_auto_upgrade_mode`
(`full` / `security-only` / `notify-only`) driving an unattended-upgrades
style timer.

### Docker

Full Docker CE install from Docker's own official apt repo
(`https://download.docker.com/linux/debian`, GPG-key-verified), `daemon.json`
(`docker_log_driver`, `docker_log_max_size`, `docker_log_max_file`,
`docker_live_restore`), plus a cleanup timer
(`docker_cleanup_max_age_hours`) that prunes stale images/containers.

### Host hygiene

KSM (host-wide `ksmd`, opt-in per process via `madvise`), systemd-oomd
(`SwapUsedLimit=90%`, pressure threshold 60%/20s), `fstrim.timer` (daily,
whole-disk), a scheduled auto-reboot window (`reboot_window_time`), a
a persistent journald (1G cap, 60-day retention — sized generously since
Docker's `journald` log-driver, below, routes every container's logs
through the same journal).

### Orchestration / observability

`state.json` under `state_dir` tracks per-step status across stage1 → reboot
→ stage2, so a re-run resumes rather than repeats. Telegram progress +
completion notifications (`telegram_bot_token`/`telegram_chat_id`,
`telegram_verbose_progress`) including host facts. An ephemeral controller
SSH pubkey (`controller_ssh_pubkey`) is installed for external monitoring
during the run and removed again only *after* the stage2-done marker is
written (removing it earlier can strand an external poller mid-install with
no way back in — a real bug found and fixed live, 2026-09-09).
`credential_mode` (`root-storage` / `systemd`) selects how the Telegram
token and controller pubkey are stored on disk.

## Not yet in v2 (v1 had some of this)

`scripts/debian-install/bootstrap.sh` (v1) had a benchmark/adaptive-sizing
layer v2 has no equivalent of: fio/Geekbench-based benchmarking,
`TEST_ZSWAP_LATENCY`, a "matrix test" that picked how many pre-created swap
partitions to actually activate based on measured performance (v2's swap
count is a static config value, never measured), ZRAM support, and multiple
swap-backing strategies (files-in-root, partitions, zvol). v2's
`config.py` `UNSUPPORTED_V2_SETTINGS` explicitly rejects a v1 config file
that still sets `run_geekbench`, `run_ssh_setup`, `swap_ram_solution`,
`pre_shrink_root_extra_gb`, or `extend_root` — these were deliberately not
ported, not merely forgotten.

An io.cost calibration benchmark is planned but not yet built: `config.py`
has the (currently inert) `run_io_benchmark`/`io_benchmark_duration_s`/
`io_benchmark_max_size_gb` fields, and the kernel's own official
`tools/cgroup/iocost_coef_gen.py` is vendored (`debian_install_v2/vendor/`)
rather than reimplementing a bespoke fio job. A real `--testdev`-vs-
partition bug found while vendoring it is already resolved as a carried
patch (`debian_install_v2/vendor/0001-*.patch`, applied at deploy/invoke
time — the vendored copy itself stays untouched). See `IO-BENCHMARK-
DESIGN.md` for the full design and why the actual partition-surgery
integration (create/benchmark/delete a throwaway partition) is
deliberately deferred to its own reviewed pass rather than built inline
here.

## Is this the right tool for everything?

v2's genuinely hard-to-replicate value is the live, no-console-access disk
surgery: shrinking a mounted, booting root and adding real swap partitions
safely, verified by readback, with rollback on mismatch. No general config
management tool does that safely today. Anything past "the disk and OS
foundation are correct" — arbitrary package sets, ongoing config drift,
per-host variable data, app deployment — is a better fit for a declarative,
idempotent, replayable tool like Ansible than for growing this one-shot
Python installer into a general config manager. The likely exception is a
one-time hardware-characterization benchmark layer (io.cost/rlat/wlat): that
measures the physical host once at provision time, which is provisioning
scope, not ongoing config management, so it plausibly still belongs here.

## Testing

`testing/` — a privileged systemd container for `inuse_partition_editor.py`'s
real `--commit`/loop-device partition tests (never run real `swapon` there —
see `testing/README.md`). `testing/vm/` — a genuinely unprivileged QEMU/TCG
VM harness for anything that needs a real, separate guest kernel (real swap
activation, eventually the full installer run end-to-end); see
`testing/vm/README.md` and `testing/vm/DESIGN.md`.
