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

## Netcup SCP API provisioning

From `scripts/netcup/`, create the refresh token and local image recipe before
the first install:

```bash
python3 scp-api-install-host.py login
python3 scp-api-install-host.py configure
python3 scp-api-install-host.py --payload target-host.jsonc --dry-run
python3 scp-api-install-host.py --payload target-host.jsonc --monitor
```

The generated `default-recipe.jsonc` is local and ignored. Its customScript
uses a controller-side bootstrap placeholder; configure a feature-branch
source before a live test:

```bash
export NETCUP_SCP_API_BOOTSTRAP_REPO_BRANCH=netcup-v2-integration
python3 scp-api-install-host.py --payload target-host.jsonc --dry-run
```

The dry run must show the intended branch before a real Netcup API install is
confirmed. See [`scripts/netcup/README.md`](../scripts/netcup/README.md) for
attach-only, exploration, and bootstrap-source details.

## Shared CLI contract

For a new or migrating user-facing Python CLI, install the shared contract
layer from the checkout:

```bash
python3 -m pip install --editable ./libraries/cli-extended
PYTHONPATH=libraries/cli-extended/src pytest -q libraries/cli-extended/tests
```

Use `cli_extended.CliIdentity`, `ExtendedArgumentParser`, `HelpCatalog`, and
`run_cli` for the parser shell; use `CliOutput` or `CliRuntime.output` for
stderr diagnostics and stdout results. The package README contains a complete
pasteable parser example, while its design guide explains the argparse-first
compatibility choice.
