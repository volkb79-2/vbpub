# Debian install v2

Python v2 is a clean break from the shell bootstrap’s environment-variable
interface. It accepts one strict JSON configuration, persists validated state,
and performs the current fresh-host setup in two internal phases.

## Run

```bash
sudo ./debian-install-v2.py --action install --config /root/install.json
```

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

## APT policy

The generated deb822 sources contain release, updates, security, backports,
testing, and unstable for the detected release. Backports are preferred at 600;
security remains intentionally higher at 550; oldstable/testing are visible at
100; unstable is visible but pinned to 50.
