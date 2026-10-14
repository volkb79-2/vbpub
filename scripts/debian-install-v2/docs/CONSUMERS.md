# Using debian-install-v2

This guide is the adoption path: make a config, review it, and then either run
the installer on the target Debian host or produce a remote custom-script
bundle. Product capabilities are summarized in
[`../README.md`](../README.md); design rationale is in
[`DESIGN-GUIDE.md`](DESIGN-GUIDE.md).

The bare usage map includes a short description of the CLI and aligned verb
summaries. It lists verb names alone; use each verb's `--help` for its full
syntax and accepted configuration/options.

## Quickstart: create and inspect settings

From a vbpub checkout, enter the project directory. The CLI works without
installing the optional wizard package; install that project-specific
requirement only if you want interactive prompts. Use the same Python
interpreter for the install and CLI run; if using a virtual environment,
activate it first (the executable resolves `python3` from `PATH`):

```bash
cd scripts/debian-install-v2
python3 -m pip install -r wizard-requirements.txt
./debian-install-v2.py wizard --output target-host.json
./debian-install-v2.py wizard --output target-host.json --from-config existing.json
```

The wizard asks which settings sections to edit. Unselected sections keep
their validated starting values (or the shipped defaults). It shows a
secret-safe summary and asks before saving. The output is JSON written with
mode `0600`; the destination directory must already exist. Press Ctrl-C at a
prompt to cancel without a traceback or partial config write.

If the optional prompt package is unavailable or cannot be imported, the CLI
prints an actionable dependency-install hint instead of a Python traceback.
Generated help and diagnostic severity tags use the shared `cli-extended`
color policy; `--color`, `--no-color`, and `NO_COLOR` behave consistently.
Primary status/plan data and JSON stay plain for piping and parsing.

The config is ordinary versioned JSON. This minimal example is accepted by
the shipped loader; omitted fields receive that loader’s current defaults:

```json
{
  "schema_version": 1,
  "fresh_install": true,
  "root_resize_policy": "resize",
  "swap_policy": "on",
  "swap_disk_total_gb": null,
  "swap_partition_count": 4,
  "credential_mode": "systemd",
  "cloud_init_cleanup_mode": "userdata-only"
}
```

This example opts to remove only the cached raw user-data after successful
stage two. Omit `cloud_init_cleanup_mode` or set it to `retain` to keep all
cloud-init cache data, including `/var/lib/cloud/instance/user-data.txt`.

Every verb that consumes settings accepts exactly one of `--config FILE` or
`--config-json JSON`; its generated usage marks that choice as required. Prefer
the file form for normal operation, since inline JSON may be visible in process
arguments. For example:

```bash
./debian-install-v2.py plan --config target-host.json
./debian-install-v2.py plan --config-json \
  '{"schema_version":1,"fresh_install":true,"root_resize_policy":"resize","swap_policy":"on","swap_disk_total_gb":null,"swap_partition_count":4,"credential_mode":"systemd","cloud_init_cleanup_mode":"userdata-only"}'
```

The parser rejects both a missing source and supplying both forms before any
installation logic runs.

## Root and swap policy

The shared config keys `root_resize_policy` and `swap_policy` independently
control root sizing and swap:

```json
{
  "schema_version": 1,
  "root_resize_policy": "grow-only",
  "swap_policy": "on",
  "swap_disk_total_gb": 8,
  "swap_partition_count": 4,
  "preserve_root_size_gb": 10
}
```

Choose one root policy: `resize` (default) uses the largest safe root extent
after managed swap and can grow or shrink; `grow-only` expands when possible
but never shrinks; `untouched` forbids root partition changes. Choose one swap
policy: `on` (default) replaces the guest's configured swap setup with native
partitions: it disables all active swap, removes configured swapfiles and swap
logical volumes, and replaces fstab swap entries with the managed partitions.
A pre-root swap partition (physically before root) is retained and counts
toward both the requested total size and the default total count of four, so
less swap is placed after root. If that retained partition already meets or
exceeds the requested total, no new tail swap partition is needed. `off` also
disables all active swap and removes fstab swap entries and root-disk swap
partitions; it does not delete swapfiles or logical volumes. `untouched`
preserves swap state and geometry. With `swap_policy: "untouched"`, root
growth can still use contiguous free space before the next existing partition.

`swap_disk_total_gb: null` derives the target from actual disk capacity and
guest RAM using `min(RAM × 2, round(20% of disk capacity))`, expressed in GiB.
Twice the RAM rounds up to a whole GiB; 20% of actual disk bytes rounds to the
nearest GiB, with half-GiB ties upward. The retained pre-root swap partition,
when present, is included in that target. For the suggested 20–512 GB disk and
4–128 GB RAM range, see the [default sizing table](../README.md#disk-swap).
The default count is four total managed partitions, including retained
pre-root swap; set `swap_partition_count` to change it. The nominal-capacity
examples and the reasons for this sizing rule are in the
[feature overview](../README.md#disk-swap) and
[design rationale](DESIGN-GUIDE.md#root-and-swap-policies).

If `swap=on` needs more tail space than exists and root policy is `untouched`
or `grow-only`, planning refuses with the available/requested sizes. A
`resize` shrink is refused if root data usage or the ext filesystem's minimum
plus 256 MiB would exceed the planned target. Managed swap refuses layouts
with an unknown non-swap partition after root rather than moving it.
Root-shrink safety is checked before swapfile cleanup, so existing swapfile
and ordinary-file usage are both included in that check.

## Cloud-init cache policy

The `cloud_init_cleanup_mode` value is shared by the wizard, config file, and
generated custom-script bundle. Choose `retain` (the default) to keep all
cloud-init cache data, `userdata-only` to delete only the raw cached user-data,
`clean` to run plain `cloud-init clean`, or `clean-all` to add `--logs --seed`.
Neither clean mode removes generated SSH/network/fstab configuration; the
installer does not pass `--configs all`.

Cloud-init stores raw user-data at
`/var/lib/cloud/instance/user-data.txt` by default. Plain `clean` resets
cloud-init state so its stages run again at the next boot; `clean-all` does the
same and also removes cloud-init logs and the guest's cached seed. If the same
NoCloud seed ISO remains attached, it may provide the same user-data again.
Detach it before a later boot if that replay is not wanted. These cleanup modes
run only after successful stage-two install work, leaving cache data intact if
an earlier install step fails. See the [feature overview](../README.md#cloud-init-cache-cleanup-policy)
and [design rationale](DESIGN-GUIDE.md#cloud-init-cache-cleanup-policy).

## Build a private VM seed ISO

For a VM launched from a Debian cloud image on TrueNAS SCALE, Proxmox, Hyper-V,
or another hypervisor, build a NoCloud seed ISO on a Linux workstation. This
is a cloud-init data disk, not a bootable Debian installer ISO: attach it as a
CD-ROM/seed device alongside the VM's Debian cloud-image disk. The guest needs
cloud-init, Python 3, outbound HTTPS, and working APT sources.

Install the ISO builder dependencies in the checkout:

```bash
cd scripts/debian-install-v2
sudo apt-get install cloud-image-utils openssh-client
python3 -m pip install -r wizard-requirements.txt
./debian-install-v2.py build-iso --output ./debian-vbpub.iso
```

The wizard collects a source branch, profile source, notification settings,
and zero or more persistent root SSH public-key lines (one complete
`authorized_keys` entry per prompt). It checks the line structure and asks
`ssh-keygen` to parse every key before building. These persistent keys are
added to `/root/.ssh/authorized_keys` in stage one and remain after stage two;
they are separate from the temporary controller SSH key, which has its own
cleanup policy. `vanilla` requires at least one persistent key; `default` lets
you skip them. The builder writes a new ISO with mode `0600` and refuses to
overwrite an existing file. Keep the artifact private. The ISO's NoCloud
user-data contains the selected notifications credentials, SSH keys,
and—when used—the profile endpoint's Basic Auth username/password. The local
JSONC profile itself is embedded in the ISO; it is not uploaded to the
repository. The generated user-data is also normally retained by cloud-init
inside the guest, so the default `cloud_init_cleanup_mode: "retain"` keeps
those values under `/var/lib/cloud/instance/user-data.txt`. Set another
cleanup mode in the selected profile if that is the intended policy; cleanup
runs only after successful stage two. See [Cloud-init cache policy](#cloud-init-cache-policy).

Released profile IDs are `vanilla` and `default`. `vanilla` keeps the disk
resize/partition and swap transaction, disables optional tuning and services,
and requires a persistent SSH key. `default` follows the selected branch's current
installer defaults, including Docker, APT policy/upgrades, journald, zswap,
cgroup2 flags, oomd and fstrim. These IDs are filenames in that branch's
`scripts/debian-install-v2/profiles/` directory. The ISO records the ID, not a
copy of the profile, so an old ISO intentionally follows changes to the
selected branch and profile.

A third-party builder can select a profile released in its configured Git
repository, embed a custom `.jsonc` file from the build host, or point to an
HTTPS JSONC URL. For a profile protected by Apache `.htaccess`/Basic Auth, enter
the HTTPS URL without userinfo or a query string, then enter the username and
password in the separate wizard prompts. The password is placed in the private
ISO, never in Git or a published generic ISO. JSONC accepts only `//` and
`/* ... */` comments; all other syntax is strict JSON and `schema_version`
must match the shipped loader. Per-host notification and SSH fields are
rejected in profiles.

The source settings are distinct: a public HTTPS Git remote/branch supplies
the installer tree and released profiles; an HTTPS URL supplies the initial
`bootstrap-remote.py`; another HTTPS URL points to the cli-extended release
manifest. The defaults use GitHub, but the wizard accepts alternate bootstrap
and release-manifest URLs. Git checkout itself is provider-neutral and uses a
shallow `--filter=blob:none` clone plus sparse paths. Bootstrap preflight
checks for Git before it resolves or downloads the cli-extended wheel, and
installs Git from APT if missing. The Git server must support the partial-clone filter;
otherwise bootstrap fails rather than transferring the whole repository.

After first boot and installation, detach the seed ISO so a later cloud-init
clean/reboot cannot consume the same credentials again. Review
[the design note](DESIGN-GUIDE.md#private-vm-seed-iso-and-moving-profiles) for
the moving-profile contract and the documented bootstrap handoff race.

To inspect a real target host’s partition plan, run `plan` on that host. It
reads the host’s root disk and does not write to it:

```bash
sudo ./debian-install-v2.py plan --config /root/target-host.json
sudo ./debian-install-v2.py plan --config /root/target-host.json --json
```

A plan is read-only, so it does not need a synthetic dry-run mode. A plan on
the controller would describe the controller’s own root disk, not a remote
server.

## Install directly on a Debian host

Copy the CLI/project to the Debian machine being installed, put the validated
config at a protected path, then run:

```bash
sudo ./debian-install-v2.py install --config /root/target-host.json
```

Before making changes, the CLI prints the disk/swap plan and asks for
confirmation. For unattended use, `--yes` pre-accepts that confirmation; it
does not bypass config validation or other safety checks:

```bash
sudo ./debian-install-v2.py install --config /root/target-host.json --yes
```

To rehearse the complete action plan without executing privileged commands or
writing state files:

```bash
sudo ./debian-install-v2.py install --config /root/target-host.json --dry-run
```

The live install modifies the root disk and can reboot. Use it only on the
intended fresh-install target with a valid recovery path. The systemd service
invokes `resume --yes` after reboot; operators can also inspect progress with
`status --config FILE`, run post-install checks with `verify --config FILE`,
or explicitly disable the remaining automatic stage-two service with
`disable-stage2 --config FILE`.

## Create a remote custom-script bundle

For a hoster custom-command field, first generate the provider-neutral bundle:

```bash
./debian-install-v2.py build-customscript --config target-host.json
```

The JSON result contains the validated config, `customScript` command, and
completion marker. The command’s bootstrap URL is derived from the selected
repository branch. A non-default repository must be paired with an explicit
`--bootstrap-url`; do not let a branch/repository mismatch silently target an
unrelated endpoint. The generated command downloads and executes the bootstrap
with Python’s standard-library HTTPS client; a failed or empty download exits
nonzero before the installer runs.

For a Netcup-style replacement of the controller SSH-key marker:

```bash
./debian-install-v2.py build-customscript \
  --config target-host.json --controller-ssh-placeholder
```

If the configuration contains a Telegram bot token or a Mattermost webhook URL (`mattermost_webhook_url`), the generated bundle is
secret-bearing. The CLI refuses to print it by default. If you deliberately
need to capture the raw bundle, protect the file and explicitly bypass
redaction:

```bash
umask 077
./debian-install-v2.py build-customscript \
  --config target-host.json --controller-ssh-placeholder --debug-raw \
  > target-host-customscript.json
```

The provider may retain custom-script contents in its API, UI, or task logs;
`umask` only protects the local file. Avoid embedding credentials if the
provider’s storage/visibility is not acceptable. The target bootstrap writes
its transient config with mode `0600` and removes that handoff file when the
stage-one invocation exits (successfully or with an error).

To test the remote fetch/translation path without applying host operations,
set `DRY_RUN=yes` in the cloud-init environment. A remote dry run still needs
root to exercise the real bootstrap flow and will perform the sparse Git fetch
and download the released cli-extended wheel, but the installer records rather
than executes privileged actions.

The bootstrap resolves the cli-extended wheel from the
`cli-extended-latest/latest.json` release pointer and verifies its sha256. To
make a fleet install reproducible, pin one wheel in the same cloud-init
environment (both variables, or neither; take the real URL and digest from the
release's `latest.json` or its versioned manifest, the URL below is only the
shape):

```bash
CLI_EXTENDED_WHEEL_URL=https://github.com/volkb79-2/vbpub/releases/download/cli-extended-v0.2.0/cli_extended-0.2.0-py3-none-any.whl \
CLI_EXTENDED_WHEEL_SHA256=<64 lowercase hex digits from that release's manifest> \
python3 bootstrap-remote.py
```

`CLI_EXTENDED_LATEST_URL` points the lookup at a different pointer (a mirror).
A digest mismatch, a payload that is not the library, or a half-set pin aborts
the bootstrap before the installer runs.

## Finding help

With no verb, the CLI prints the grouped usage map. Use `--help` for the full
map or append it to a verb for that verb’s options and examples:

```bash
./debian-install-v2.py
./debian-install-v2.py --help
./debian-install-v2.py build-customscript --help
./debian-install-v2.py --version
```

The top-level verbs are:

- Exploration: `status`, `verify`, `plan`, `build-customscript`.
- Modification: `wizard`, `build-iso`, `install`, `resume`, `disable-stage2`.

Use `--json` where offered for machine-readable status/plan output. For
diagnostics, `--debug` adds troubleshooting context; `--debug-raw` is only for
intentionally unredacted output and should be treated as a secret-handling
decision.
