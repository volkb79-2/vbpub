# Debian install v2 operator guide

`debian-install-v2.py` is the CLI for creating settings, inspecting an
installation plan, applying stage one, and checking or resuming installation
state. Run it without arguments to see the grouped usage map. Help is available
at the top level and for every verb:

```bash
./debian-install-v2.py --help
./debian-install-v2.py wizard --help
./debian-install-v2.py install --help
```

The quickstart, wizard dependency, example settings, direct-install recipe,
and remote custom-script flow are documented in
[`../docs/CONSUMERS.md`](../docs/CONSUMERS.md). The rationale for the verb
model, shared CLI library, settings validation, and optional prompt package is
in [`../docs/DESIGN-GUIDE.md`](../docs/DESIGN-GUIDE.md). The installer’s
feature surface is summarized in [`../README.md`](../README.md).

The CLI is backed by the shipped `Config` loader. A wizard-generated file is
ordinary versioned JSON and can also be reviewed or edited by hand; every
execution validates it again before doing host work. `install` is for the
Debian host being installed and can repartition its root disk. Read the
consumer guide’s safety notes before running it.

## Safety boundary for tests

The ordinary gate runs safe tests in `tester-unified`: regular-file fixtures,
mocked commands, and dry-run paths. It does not run real loop-device, swap,
initramfs, reboot, or host-kernel tests in the cockpit or in a privileged
Docker container. Docker shares the host kernel, so those operations can change
host-global state. The explicit `r1-vm-real-commit` lane (not part of
`[lanes.gate]`; run it by name) runs the real device tests through the
unprivileged QEMU/TCG harness under [`../testing/README.md`](../testing/README.md).

## Environment-variable wrapper (`bootstrap-remote.py`)

`bootstrap-remote.py` (one directory up) translates a small set of environment
variables 1:1 into `Config` fields and runs the `install` verb with `--yes`.
Anything not covered by a named variable goes through `VBPUB_CONFIG_EXTRA_JSON`,
a raw JSON object merged into the config; the full variable list and
`DRY_RUN=yes` are in that script's docstring.

v1's obsolete names (`SWAP_ARCH`, `SWAP_TOTAL_GB`, `SWAP_FILES`,
`USE_PARTITION`, `SWAP_PARTITION_SIZE_GB`, `SWAP_BACKING`, `BOOTSTRAP_STAGE`,
`NEVER_REBOOT_STAGE2`) are rejected outright by `config.py`
(`OBSOLETE_VARIABLES`): v2 only does GPT swap partitions, so there is no
`USE_PARTITION` toggle. Translate `SWAP_TOTAL_GB` to `SWAP_DISK_TOTAL_GB` and
`SWAP_FILES` to `SWAP_FILE_COUNT`. `AUTO_REBOOT_AFTER_STAGE1` and `NEVER_REBOOT`
are strict yes/no; v1's three-state `auto` is rejected rather than guessed.

## Stage2 log

Stage2 appends both stdout and stderr to `stage2_output`
(default `/root/custom_script.output2`); its systemd unit also records normal
failures in journald. `status` shows persisted state and recent logs.

## Host tuning notes

- Docker cleanup (`run_docker_cleanup` / `docker_cleanup_max_age_hours`,
  default `true` / `240`) is a weekly, age-filtered prune of images, stopped
  containers and build cache. It **never** runs `docker volume prune` (or
  `--volumes`): Wings bind-mounts `/var/lib/pterodactyl/volumes/<uuid>` by host
  path for all server state, so nothing durable is reachable through Docker's
  volume subsystem and a volume prune would only risk unrelated data.
- `vm_swappiness` defaults to `100` (range 0-200); `run_oomd_config` thresholds
  are the safety net that makes any value safe unattended. See the top-level
  `README.md` for zswap, KSM/THP, sysctl, io.cost and swap-health.

## `vbpub-notify`

`/usr/local/sbin/vbpub-notify MESSAGE` is a standalone, reusable notification
helper for any unit or script (reboot-check, boot-notify, apt-update-notify).
It reads credential files from `$CREDENTIALS_DIRECTORY` or, by default, the
fixed `/etc/vbpub/credentials/`, which the installer writes for either
`credential_mode`: a Mattermost incoming-webhook URL (`mattermost_webhook_url`,
posted as `{"text": ...}`, never logged) or the Telegram pair
(`telegram_bot_token`, `telegram_chat_id`). The helper uses Mattermost when its
file is present, otherwise Telegram, and exits 0 when neither exists or a post
fails. Because the helper picks its backend by which file exists, the installer
writes ONLY the selected backend's files and removes the other backend's
credential files (both locations, all of them for `notify_backend=none`) on
every run, so a re-run cannot leave a stale credential active. Webhook URLs must
be `https://`, and an explicit `notify_backend` with a credential for a
different backend is a configuration error.

## APT policy

The generated deb822 sources contain release, updates, security, backports,
testing, and unstable for the detected release. Pin priorities: backports 600,
security 550, stable and stable-updates 500, oldstable/oldstable-backports and
testing 100 (visible, never chosen by default), unstable 50. `apt_auto_upgrade_mode`
`full` covers all pinned origins, `security-only` just the security origin, and
`notify-only` installs nothing and reports the pending count via `vbpub-notify`.
Package-manager `Automatic-Reboot` stays off; `run_auto_reboot` owns rebooting.
