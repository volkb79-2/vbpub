# Consumers guide

## Debian fresh-install v2

Use `scripts/debian-install-v2/debian_install_v2/` only on a freshly installed,
supported Debian host. The current minimal release applies a fixed disk shape:
32 GiB of native GPT swap in eight partitions, with zswap configured before
`swap.target`.

### Paste-ready install config

Create `/root/install.json` on the target:

```json
{
  "schema_version": 1,
  "fresh_install": true,
  "swap_disk_total_gb": 32,
  "swap_file_count": 8,
  "zswap_compressor": "zstd",
  "telegram_bot_token": "",
  "telegram_chat_id": "",
  "auto_reboot_after_stage1": true,
  "never_reboot": false
}
```

The committed equivalent is
[`scripts/debian-install-v2/known-shape.json`](../scripts/debian-install-v2/known-shape.json).
Fill both Telegram values only if notifications are wanted; supply neither or both.

### Install and rehearse

```bash
# Rehearsal: prints/plans actions; executes no commands and writes no files.
./debian-install-v2.py --action install --config /root/install.json --dry-run

# Fresh-host install: stage1 runs, installs stage2, then reboots automatically.
sudo ./debian-install-v2.py --action install --config /root/install.json
```

Stage2 appends stdout/stderr to `/root/custom_script.output2`. Read state with:

```bash
sudo ./debian-install-v2.py --action status --config /root/install.json
```

### Compatibility boundary

v2 rejects v1 environment names such as `SWAP_ARCH`, `SWAP_TOTAL_GB`,
`SWAP_FILES`, and `USE_PARTITION`; it does not translate them. Benchmarking is
intentionally deferred. APT uses release, updates, security, backports,
testing, and unstable; backports are preferred at 600 and unstable remains
pinned at 50.
