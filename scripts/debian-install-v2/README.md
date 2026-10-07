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

## Operator CLI

`debian-install-v2.py` is the operator entry point. It has grouped verbs for
configuration, installation, status, verification, planning, and generating a
provider-neutral cloud-init custom-script bundle. Run it without a verb for the
usage map, or use `--help` for details:

```text
./debian-install-v2.py --help
./debian-install-v2.py wizard --help
```

The usage map explains the CLI's overall job and lists each verb by name with
an aligned one-line description. Use `debian-install-v2.py VERB --help` for
that operation's required settings and full invocation syntax.

The settings wizard writes a validated mode-0600 JSON file. The same shipped
configuration loader validates both wizard output and hand-authored files; the
wizard is not a second schema. Adoption, safe install examples, and remote
bootstrap steps are in [docs/CONSUMERS.md](docs/CONSUMERS.md); command and
wizard design rationale is in [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md).
Verbs that consume settings require exactly one of `--config FILE` and
`--config-json JSON`; their generated usage shows that required choice.
The generated remote custom-script launcher uses Python’s standard-library
HTTPS client and fails nonzero if it cannot retrieve the bootstrap.

### Library dependency and remote bootstrap

`debian-install-v2.py` needs the `cli_extended` library, resolved from exactly
one place: a single `cli_extended-*.whl` beside the script (imported directly
from the wheel; pure Python, no pip), otherwise an installed `cli_extended`.
With neither it exits 2 with
`[ERROR] debian-install-v2: cli-extended is not installed; run via bootstrap-remote.py or install the cli-extended wheel`;
two wheels beside it is also exit 2. The repository’s library source is never
used as a fallback. The product version is the single line in
[`VERSION`](VERSION).

`bootstrap-remote.py` (stdlib only) downloads the installer tree plus the
**released** cli-extended wheel and verifies its sha256 before writing it:

| Environment | Meaning |
| --- | --- |
| *(none)* | read `https://github.com/volkb79-2/vbpub/releases/download/cli-extended-latest/latest.json` and use its `url` and `sha256` |
| `CLI_EXTENDED_LATEST_URL` | read that release pointer instead |
| `CLI_EXTENDED_WHEEL_URL` + `CLI_EXTENDED_WHEEL_SHA256` | pin one exact wheel (set both or neither; the URL must end in a `cli_extended-*.whl` filename) |

Every one of those URLs (the pointer, a pinned wheel, and the `url` field inside
the pointer) must be `https`; `http:`, `file:` and every other scheme are refused
with no override. The wheel is capped at 16 MiB and the pointer at 1 MiB. The
wheel is resolved, downloaded and verified (sha256, zip, `cli_extended/__init__.py`)
before the installer tree is written, so a bad pin or digest leaves the install
directory untouched. The wheel is then written atomically (temp file and rename)
and only afterwards is every other `cli_extended-*.whl` removed, so only one
remains; a directory with such a name is refused. See
[docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md#remote-bootstrap-stays-self-contained)
for the rationale and [docs/CLI-SPEC.md](docs/CLI-SPEC.md) for the reviewed CLI
surface and its semantic decisions.

Terminal help and diagnostic tags use `cli-extended`'s shared color policy:
automatic color on a TTY, `NO_COLOR`/`--no-color` to disable, and `--color` to
force it. JSON and primary status/plan results remain uncolored. The installer
does not add its own ANSI formatting.

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

A `zswap-config.service` (sysinit.target, before `swap.target`, mechanism
copied from the gstammtisch host setup) configures zswap before any swap
partition activates. Each `ExecStart` line writes ONE knob, in this order:
`modprobe zstd`, `compressor`, `max_pool_percent`, `accept_threshold_percent`,
`shrinker_enabled`, and `enabled=1` last, followed by an `ExecStartPost`
status line. `/etc/modules-load.d/` also loads `zstd`. Nothing zswap-related
is ever put on the kernel command line, and there is deliberately **no
`zpool` write**: that knob is absent on 7.x kernels, and the unit must work
on both 6.12 (stable) and 7.x (trixie-backports).

- `zswap_compressor` (`zstd` / `lz4` / `lzo-rle`)
- `zswap_pool_percent` (5–60, default 25, ceiling as % of RAM)
- `zswap_accept_threshold_percent` (0–100, default 90, resume-accepting
  hysteresis once the ceiling is hit)
- `zswap_shrinker_enabled` (default `true`; the unit writes `Y` or `N`) —
  the kernel's memory-pressure-driven, per-cgroup zswap writeback shrinker.
- `zswap_zpool` was **removed**. An old config or resume state that still
  carries it logs one warning and the key is ignored.

The post-install health gate reads `enabled`, `compressor`,
`max_pool_percent`, `accept_threshold_percent` and `shrinker_enabled` back
and compares them with the configuration.

**Shrinker caution** (`TODO.md`, `zswap-shrinker-threshold-feasibility.md`,
gstammtisch live incident 2026-09-08): the shrinker has no per-cgroup floor or
rate limit; under sustained pressure it can drain a single cgroup's zswap pool
to 0% in seconds to minutes, and it does not recover once disabled. A
userspace fill-watermark governor is designed but **not implemented**; set
`zswap_shrinker_enabled=false` on hosts where that matters.

### KSM and THP (tmpfiles.d)

KSM and THP are `systemd-tmpfiles` `w!` entries (as on gstammtisch), applied
immediately with `systemd-tmpfiles --create --boot` (`--boot` is required:
without it `w!` lines are skipped silently) and again at every boot. They
replace the earlier `ksm-config.service` / `thp-config.service` units; on a
re-run or resume those old units are disabled and removed if present.

- THP: `enabled=madvise`, `defrag=madvise`.
- KSM: `run=1`, `advisor_mode=scan-time`, `advisor_target_scan_time=200`,
  `use_zero_pages=1` (installed when `run_ksm` is true).

### cgroup2 and sysctls

cgroup2 mount flags `memory_recursiveprot`, `nsdelegate`. The rendered
sysctl file carries a one-line "why" per value:

- `vm.swappiness` default **100**, range 0–200 (the kernel range): with zswap
  in front of swap, reclaiming cold anonymous pages is cheap, so they go to
  the compressed pool early and file cache stays resident. systemd-oomd is the
  safety net. An already-saved resume state keeps its saved value.
- `vm.watermark_scale_factor = 50`, `vm.vfs_cache_pressure = 50`,
  `vm.page-cluster = 0`, `vm.dirty_ratio = 15`, `vm.dirty_background_ratio = 5`.
- `vm.admin_reserve_kbytes = 65536`.
- `vm.min_free_kbytes`: a FLOOR of 65536, applied by the boot-time
  `vbpub-min-free-floor.service` only when the kernel's computed value is
  lower. It never lowers the kernel's own value (large-RAM hosts compute more).

### io.cost (from the benchmark, not bfq)

If the install-time io benchmark (`/var/lib/vbpub/bootstrap/io-benchmark.json`)
produced a valid result and `iocost_enabled` is true (default), a boot-time
oneshot unit resolves the root disk's `MAJ:MIN` at boot (so a device rename
cannot misconfigure it), writes `/sys/fs/cgroup/io.cost.model` from the
persisted coefficients (`ctrl=user model=linear rbps= rseqiops= rrandiops=
wbps= wseqiops= wrandiops=`) and enables `/sys/fs/cgroup/io.cost.qos` with
`enable=1 ctrl=user` **only**. There are deliberately no `rpct`/`wpct`/`min`/
`max` tokens: no latency-based vrate throttling, the cost model alone gives
proportional `io.weight` control. Operator-set `rlat`/`wlat` latency targets are
a future option (mdt iocost plan D4: do not enable latency QoS without them).
The I/O scheduler is untouched. With no valid result no unit is installed and
the completion notification says so; this is not a failure. io.cost is
advisory: if the boot unit fails to start, or the health gate's model/qos
read-back does not match, the install continues, the `iocost` step is marked
`warned` and the failure text appears in the completion notification. Setting
`iocost_enabled=false` on a re-run removes a previously installed unit.

### `vbpub-swap-health`

`/usr/local/sbin/vbpub-swap-health [watch]` prints a one-command view of
zswap stats, compression ratio, writeback ratio and PSI. It works on 6.12 and
7.x and degrades gracefully when debugfs is not mounted.

### User ergonomics

Root and `/etc/skel` receive mc, htop, iftop and top rc files, nano settings
and a few shell aliases (`df -h`, `du -h`, `free -h`, `catlog`, ...). A file
is written only when it does not exist yet: an existing file (hand-edited, or
rewritten by htop/mc itself) is operator-owned and is never overwritten, so a
re-run or resume is safe.

### apt

Own `debian.sources` (main/contrib/non-free/non-free-firmware across
release + `-updates` + `-security` + `-backports`), `apt_auto_upgrade_mode`
(`full` / `security-only` / `notify-only`) driving an unattended-upgrades
style timer.

**Backports pin is 600, deliberately.** `/etc/apt/preferences.d/debian-priorities`
pins `stable-backports` at 600, above stable (500), so apt prefers the backports
version of EVERY package that has one, not only of packages pulled explicitly
with `-t`. Consequence: a `full` unattended upgrade moves the kernel (and any
other package with a backports build) to the backports series. This is an
operator decision (2026-10-06). Priority 100 would be the "only when explicitly
requested" setting.

**What `apt_auto_upgrade_mode` allows.** The installer writes
`/etc/apt/apt.conf.d/51-vbpub-unattended-upgrades`, which starts with
`#clear Unattended-Upgrade::Origins-Pattern;` and `#clear Unattended-Upgrade::Allowed-Origins;`
(apt MERGES list entries across `apt.conf.d` files, so without the clears the
package's own `50unattended-upgrades` defaults stay allowed, including the main
pocket `origin=Debian,codename=<release>,label=Debian`). After the clears:
`security-only` allows ONLY `<release>-security` (plus the legacy
`codename=<release>,label=Debian-Security` spelling, which matches no real trixie
stanza); `full` allows release, `-updates`, `-security` and `-backports`;
`notify-only` runs no upgrade, only a count. `apt_upgrade_at_install` (default
true) runs one `unattended-upgrade` during stage1 under that mode, after the apt
timers are held and `apt-daily(-upgrade).service` have finished (waited for, up to
600 s). The run is retried on the lock-contention messages of unattended-upgrade
and of apt; a failed run is a warning (step `apt_upgrade_at_install` = `warned`,
with the last lines of its output) and never fails the install.

### `never_reboot` and what it does not hold back

`never_reboot=true` (and `auto_reboot_after_stage1=false`) only stops the
INSTALLER from scheduling a reboot after stage1: the stage1 `reboot` step is
reported as "disabled by configuration", and a new kernel installed by the
stage1 upgrade is reported as "reboot required". The enabled stage2 unit still
starts at the next boot, whoever causes that boot. It is not a hold on stage2.

Provider-driven restarts are outside the installer's control. On netcup (live
finding LT-F-r1002-04, run LT-04-run3) the provider's cloud-init ends with
`Post-Script finished / restarting... / Poweroff requested`, so the host is
restarted after the customScript returns REGARDLESS of `never_reboot`. The
enabled stage2 unit then runs on that boot (about a minute after it), without
any manual `systemctl start`, and the new kernel from the stage1 upgrade is the
one that boots (6.12 to 7.2 in that run). To hold stage2 back on such a
provider, disable the unit yourself in time (`systemctl disable
vbpub-bootstrap-stage2.service`, or the `disable-stage2` verb) before the
provider restart; `never_reboot` alone cannot do it.

### Stage2 launch and failure notification

The stage2 systemd unit runs the installed entrypoint
(`python3 <install dir>/debian-install-v2.py resume --yes`), never
`python3 -m debian_install_v2...`: only the entrypoint puts the
`cli_extended-*.whl` beside it on `sys.path`. The unit has
`OnFailure=vbpub-bootstrap-failed@%n.service`, which runs the stdlib-only
`debian_install_v2/failure_notify.py`: it marks `state.json` `status=failed`
(`failed_unit`, redacted `failed_journal_tail`) and posts a failure message via
the configured backend, using the credential files under `/etc/vbpub/credentials`.
`LoadCredential=` lines appear only in `credential_mode=systemd`.

### Docker

Full Docker CE install from Docker's own official apt repo
(`https://download.docker.com/linux/debian`, GPG-key-verified), `daemon.json`
(`docker_log_driver`, `docker_log_max_size`, `docker_log_max_file`,
`docker_live_restore`, `docker_default_address_pools`), plus a cleanup timer
(`docker_cleanup_max_age_hours`) that prunes stale images/containers.

`docker_default_address_pools` (default `[{"base":"10.240.0.0/16","size":24}]`,
from mdt MDT-002) is rendered as `default-address-pools` in the same
`daemon.json`. Each pool needs a valid network `base` and an integer `size`
with `base prefix <= size <= 30` (IPv4) / `<= 128` (IPv6); at most 16 pools,
no overlapping bases. Bases must be private ranges (RFC 1918 / ULA); unspecified,
loopback, link-local, multicast, IPv4-mapped IPv6 and public ranges are rejected,
with no opt-out. The installer owns `default-address-pools` in `daemon.json`
exactly as it owns `log-opts`: an existing value is overwritten, not preserved
(installs are fresh). Before writing `daemon.json` the docker step compares every
pool with the host's own addresses (`ip -j addr`) and IPv4 routes (`ip -j route`)
and fails, naming the pool and the conflicting address or route, on any overlap.
An empty list means Docker's built-in default: the key
is omitted (and removed on an idempotent re-run). The wizard accepts a JSON
list; the bundle / `--config-json` carry it as a JSON array.

### Host hygiene

KSM (host-wide `ksmd`, tmpfiles.d, opt-in per process via `madvise`), systemd-oomd
(`SwapUsedLimit=90%`, pressure threshold 60%/20s), `fstrim.timer` (daily,
whole-disk), a scheduled auto-reboot window (`reboot_window_time`), a
a persistent journald (1G cap, 60-day retention — sized generously since
Docker's `journald` log-driver, below, routes every container's logs
through the same journal).

### Orchestration / observability

`state.json` under `state_dir` tracks per-step status across stage1 → reboot
→ stage2, so a re-run resumes rather than repeats. Progress +
completion notifications through one backend, `notify_backend` =
`mattermost` | `telegram` | `none` (unset: inferred from whichever credential is
present; both present and unset is a config error). There is no backend unless a credential or `notify_backend` is configured; the wizard offers Mattermost first. Mattermost posts a
`{"text": markdown}` to an incoming webhook (`mattermost_webhook_url`, a
secret; `notify_host_label` is the optional leading label; stdlib `urllib`
only, 2 attempts / 10 s timeout, 4xx other than 429 is not retried, after 3 failed messages posting stops for the rest of the process; a failed POST only warns). Every interpolated field is sanitized (control characters collapsed, length-capped, markdown escaped, `@` defused) and `notify_host_label` is rejected at validation if unsafe. Milestone messages
look like `✅ **netcup-1** (`vmi123`) | run `ab12cd34` | stage1 | done`; a
failure carries a short redacted log tail as a fenced block. Telegram
(`telegram_bot_token`/`telegram_chat_id`, `telegram_verbose_progress`) keeps
working. Both include host facts. See `mattermost-server/CONSUMER.md` for the
producer contract. The webhook URL is never printed (only its host), is
redacted in `--debug`/`--debug-raw`, and is not written to `state.json`. An ephemeral controller
SSH pubkey (`controller_ssh_pubkey`) is installed for external monitoring
during the run. By default it is removed only *after* the stage2-done marker
is written (removing it earlier can strand an external poller mid-install with
no way back in — a real bug found and fixed live, 2026-09-09).
`retain_controller_ssh_key=true` (env `RETAIN_CONTROLLER_SSH_KEY=yes|no`)
deliberately leaves that exact line in `authorized_keys` after successful
stage2 (step `controller_ssh_key_retained`, and the install-complete message
says "controller key retained on host"); failure paths retain it for
diagnosis regardless of this setting, including a failure before stage1 starts
(unplannable config, `show_plan`): the key is installed then too. The one
exception is a host-identity refusal (unsupported or undetected Debian release,
root not a plain block-device mount): that posts the failure and installs
nothing (no key, no state, no timer change). A successful stage2 also prunes
stale `vbpub-controller-ephemeral-*` keys left by earlier failed runs: all of
them with `retain_controller_ssh_key=false`, all but the current run's key with
`true`. Keys without that comment marker are never touched. The operator's
persistent account key is never removed. An early failure on a host that already
has a `state.json` from a DIFFERENT run leaves that file untouched and says so in
the post. When netcup `install-host.py` sees host retention in the
customScript it refuses `--local-controller-key remove`.
`credential_mode` (`root-storage` / `systemd`) selects how the Telegram
token / Mattermost webhook URL and controller pubkey are stored on disk.

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

The optional io.cost benchmark (`run_io_benchmark`, off by default;
`io_benchmark_duration_s`, `io_benchmark_max_size_gb`) runs in stage 2 before
the swap shape is written: it carves ONE throwaway partition from the tail of
the free space (sized `min(io_benchmark_max_size_gb, free - swap requirement -
1 GiB margin)`, skipped under 2 GiB), formats and mounts it, runs the shared
generated `tools/iocost_coef_gen.py` in `--testfile` mode, then unmounts and
deletes the partition and reads the table back before swap placement. Results
(`rbps`/`rseqiops`/`rrandiops`/`wbps`/`wseqiops`/`wrandiops`) go to
`state_dir/io-benchmark.json` (0600), the `io_benchmark` step and one
milestone notification. A benchmark failure is advisory (step `warned`, the
install continues); a cleanup that cannot be verified stops the install.
It needs `fio` and `pv` (installed only when the benchmark runs) and, while
it runs, the tool sets the disk's scheduler to `none` and disables merging
(the installer restores both itself, success or failure). Every long command has
a hard timeout (the whole process group is killed; an unreapable child stops the
install), and the artifact's body must match `tools/iocost_coef_gen.py.sha256`.
See `IO-BENCHMARK-DESIGN.md` for the full flow.

Still not done: the result is NOT written to `io.cost.model`/`io.cost.qos`
and io.cost is not enabled; choosing and applying those is a separate,
later decision.

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
