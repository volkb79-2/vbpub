# Debian install v2

Python v2 is a clean break from the shell bootstrap’s environment-variable
interface. It accepts one strict JSON configuration, persists validated state,
and performs the current fresh-host setup in two internal phases.

## Run

```bash
sudo ./debian-install-v2.py --action install --config /root/install.json
```

### Remote install (curl | python3)

`bootstrap-remote.py` (sibling of this README, one directory up) is the v2
equivalent of v1's `scripts/debian-install/bootstrap.sh` one-liner — but
it's a clean-break companion, not a continuation: pure Python (stdlib
`urllib`+`tarfile` only, no `git`/`tar`/bash dependency), and it produces
the strict-JSON config this tool actually takes, not v1's env-var surface.
It fetches only `scripts/debian-install-v2/` from the public
`volkb79-2/vbpub` repo via a codeload tarball, translates a small set of
env vars 1:1 to `Config` fields, and execs the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/scripts/debian-install-v2/bootstrap-remote.py \
  | SWAP_DISK_TOTAL_GB=32 SWAP_FILE_COUNT=8 \
    ZSWAP_COMPRESSOR=zstd ZSWAP_POOL_PERCENT=25 \
    AUTO_REBOOT_AFTER_STAGE1=yes NEVER_REBOOT=no \
    TELEGRAM_BOT_TOKEN=123:token TELEGRAM_CHAT_ID=456 \
    python3 -
```

This is the v1 example translated 1:1: `SWAP_TOTAL_GB`→`SWAP_DISK_TOTAL_GB`,
`SWAP_FILES`→`SWAP_FILE_COUNT` (v1's obsolete names, `SWAP_ARCH`/
`USE_PARTITION` included, are rejected outright by `config.py`'s own
`OBSOLETE_VARIABLES` — v2 only ever does GPT swap partitions, there's no
`USE_PARTITION` toggle to set). `AUTO_REBOOT_AFTER_STAGE1`/`NEVER_REBOOT`
are strict yes/no now — v1's three-state `auto` has no v2 equivalent and is
rejected rather than silently guessed. Full env var list, `DRY_RUN=yes`,
and `VBPUB_CONFIG_EXTRA_JSON` (a raw JSON object for any `Config` field not
covered by a named var) are documented in `bootstrap-remote.py`'s own
docstring.

**Known gap as of this writing**: `scripts/debian-install-v2/` exists only
on the `debian-install-update` branch, not yet on `main` — the one-liner
above 404s until that branch merges. For pre-merge testing, add
`REPO_BRANCH=debian-install-update` to the env vars above.

For rehearsal on an existing machine:

```bash
./debian-install-v2.py --action install --config /tmp/install.json --dry-run
```

Dry run records every privileged action and file write; it does not execute
commands or create output files. State writes are also dry-run only.

## Configuration

```json
{
  "schema_version": 1,
  "fresh_install": true,
  "swap_disk_total_gb": 32,
  "swap_file_count": 8,
  "zswap_compressor": "zstd",
  "telegram_bot_token": "123:token",
  "telegram_chat_id": "123456"
}
```

The known swap shape is 32 GiB split into eight native GPT swap partitions.
Benchmarking is intentionally deferred. Obsolete v1 names such as `SWAP_ARCH`,
`SWAP_TOTAL_GB`, `SWAP_FILES`, and `USE_PARTITION` are rejected rather than
silently ignored.

## Stage behavior

- `install`: validate config, configure APT/users/journald/Docker as selected,
  persist complete JSON state and Telegram credentials outside the manifest,
  install stage2, then reboot when configured.
- `resume`: internal stage2 continuation after reboot; loads state, applies the
  known partition shape, formats/activates all eight swaps, and marks success.
- `status`: read persisted state and recent logs.

Stage2 appends both stdout and stderr to `/root/custom_script.output2`.
Its systemd unit also records normal failures in journald.

### Partitioning: Case A vs Case B

`resume`'s partition step handles two disk shapes, auto-detected from the
live `sfdisk --dump` at stage2 time — never an operator choice:

- **Case A** (implemented): the disk already has free space after root
  (fresh installs on a base image with the whole disk pre-partitioned but
  not pre-filled). `_disk_facts()`/`_apply_known_swap_shape()` reuse
  `inuse_partition_editor.Table`'s regex-based attribute parser (tolerant of
  padded/quoted `sfdisk --dump` attributes, e.g. a `name="..."` value with a
  space in it) to read the current table, and its `partx -a` + `udevadm
  settle` apply mechanism (more reliable than `partprobe` alone at getting
  new `/dev/xxxN` nodes to exist) — both ported to go through `HostActions`
  so the allowlist and dry-run gate still cover every subprocess call, not
  `Table`'s own bare `run()`. debian-install-v2's own preflight mount-check,
  `disk-transaction.json` manifest, backup+checksum, and readback-triggered
  rollback wrap around this — none of those have a counterpart in the editor
  tool itself, which is also independently runnable as a CLI
  (`inuse_partition_editor.py`, importable as
  `debian_install_v2.inuse_partition_editor`).
- **Case B** (designed, not implemented — see
  `CASE-B-ROOT-SHRINK-DESIGN.md`): the disk's root partition already fills
  (most of) the disk, so free space must be created by shrinking root's
  filesystem and partition first — a destructive, offline (unmount-required)
  operation ext4 can't do online, needing an initramfs-tools pre-mount hook.

## Host tuning (incorporated from gstammtisch-guide)

Stage2 also configures, each behind its own `run_*` flag (all default `true`):

| Setting | Default | What / why |
|---|---|---|
| `vm_swappiness` | `50` | Anon pages stay the precious tier even with zswap making reclaim cheap. `100` documented for a memory-fungible/zswap-protected host (gstammtisch's own value); `5`-`10` for latency-sensitive workloads (databases) that want almost no anon reclaim regardless of swap cost. `run_oomd_config`'s thresholds are the safety net that makes any of these values safe unattended. |
| `run_oomd_config` | `true` | Global `systemd-oomd` thresholds only (`SwapUsedLimit=90%`, pressure-based kill) — the per-slice `ManagedOOMMemoryPressure=` opt-in for dev-tier slices is mdt host-setup's concern, not this tool's. |
| `run_ksm` | `true` | Enables `ksmd` host-wide; idle/free until a process opts in via `prctl(PR_SET_MEMORY_MERGE, 1)` — not game-specific, only the opt-in is. |
| `run_fstrim` | `true` | Overrides Debian's stock `fstrim.timer` from weekly to daily — whole-disk batched TRIM (root + swap + everything mounted), for this fleet's thin-provisioned virtual disks. |
| `swap_discard` | `true` | Adds `discard=once` to each swap partition's fstab line. |
| `run_docker_cleanup` / `docker_cleanup_max_age_hours` | `true` / `240` | Weekly prune of aged images, stopped containers, and build cache — **never** `docker volume prune`: Wings bind-mounts `/var/lib/pterodactyl/volumes/<uuid>` by host path for all server state, so nothing durable is reachable through Docker's volume subsystem at all. |
| `run_apt_auto_upgrade` / `apt_auto_upgrade_mode` | `true` / `full` | `full`: `unattended-upgrades` across all four pinned origins (release/security/backports, testing, unstable). `security-only`: just the security origin. `notify-only`: installs nothing, just reports the pending-upgrade count via `vbpub-notify`. Package-manager `Automatic-Reboot` is left off — `run_auto_reboot` owns rebooting instead, so the two mechanisms can't race. |
| `run_auto_reboot` / `reboot_window_time` | `true` / `03:00` | Daily check for `/var/run/reboot-required`; if present, notifies then reboots at the configured HH:MM. A `vbpub-boot-notify` unit sends a follow-up notice on the next boot. |

`/usr/local/sbin/vbpub-notify MESSAGE` is a standalone, reusable Telegram
sender any unit or script can call — it reads the same credential pair this
tool already writes to `/etc/vbpub/credentials/` for either
`credential_mode`, so there's one credential path, not a second one bolted
on for notifications.

## APT policy

The generated deb822 sources contain release, updates, security, backports,
testing, and unstable for the detected release. Backports are preferred at 600;
security remains intentionally higher at 550; oldstable/testing are visible at
100; unstable is visible but pinned to 50.
