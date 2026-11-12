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
python3 -m pip install -r requirements-wizard.txt
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
  "swap_policy": "on",
  "swap_disk_total_gb": 32,
  "swap_partition_count": 8,
  "credential_mode": "systemd",
  "cloud_init_cleanup_mode": "userdata-only"
}
```

This example opts to remove cloud-init's user-data copies after successful
stage two. Omit `cloud_init_cleanup_mode` or set it to `retain` to keep all
cloud-init cache data, including `/var/lib/cloud/instance/user-data.txt`.

Every verb that consumes settings accepts exactly one of `--config FILE` or
`--config-json JSON`; its generated usage marks that choice as required. Prefer
the file form for normal operation, since inline JSON may be visible in process
arguments. For example:

```bash
./debian-install-v2.py plan --config target-host.json
./debian-install-v2.py plan --config-json \
  '{"schema_version":1,"fresh_install":true,"swap_policy":"on","swap_disk_total_gb":32,"swap_partition_count":8,"credential_mode":"systemd","cloud_init_cleanup_mode":"userdata-only"}'
```

The parser rejects both a missing source and supplying both forms before any
installation logic runs.

## Tune the KSM advisor

Add these fields to the target's installer config to choose the scan budget:

```json
{
  "schema_version": 1,
  "ksm_advisor_max_cpu_percent": 10,
  "ksm_target_scan_time_seconds": 300
}
```

`ksm_advisor_max_cpu_percent` is the advisor's maximum CPU use as a percent of
one core (1–100). `ksm_target_scan_time_seconds` is the positive target time
for a full scan. The [DESIGN-GUIDE](DESIGN-GUIDE.md#ksm-scan-budget) explains
why the defaults are 10% and 300 seconds.

On an existing host, rerun the installer step to rewrite and apply
`/etc/tmpfiles.d/vbpub-ksm.conf`. For a manual refresh, first update its two
advisor entries to the selected values, then run this as root:

```bash
systemd-tmpfiles --create --boot /etc/tmpfiles.d/vbpub-ksm.conf
```

The `--boot` option is required for the drop-in's `w!` entries.

## Disk plan and offline preview

The one `partitioning` plan describes root, optional data, reserve, and the
raw benchmark partition. Its defaults are root `"*"`, no data, no reserve,
and an 8 GiB `iobench-raw` partition. The raw partition can be resized with
`partitioning.iobench_raw_gb` (2–10240 GiB) or disabled with `false` when the
benchmark is off. Root sizing no longer has a plan-less policy; the removed
`root_resize_policy`, `disk_layout`, `root_max_size_gb`, and `data_area*`
keys are refused.

This example is for a 512 GiB host with a 160 GiB root, a CoW partition taking
the remaining allocation, four 4 GiB swaps, and the default 8 GiB raw
partition:

```json
{
  "schema_version": 1,
  "partitioning": {
    "root": 160,
    "data": {"kind": "partitions", "volumes": [
      {"fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}
    ]}
  },
  "swap_policy": "on",
  "swap_disk_total_gb": 16,
  "swap_partition_count": 4
}
```

Every managed partition is numbered by physical order: root, `MDT_COW`,
`iobench-raw`, then the tail swap partitions. The exact numbers depend on the
root number and preserved partitions before root. `provision-runner.sh` finds the CoW
partition by PARTLABEL `MDT_COW`; it does not infer `/dev/vda8` or carve a
partition.
Swap partitions have no PARTLABEL. Their filesystem labels are
`vbpub-swap1..N` in activation order (`vbpub-swap-pre1..N` for retained
pre-root swaps), while fstab identifies each partition by PARTUUID. The
preview's `Identifier` column shows `LABEL=vbpub-swapN` for swap and
`PARTLABEL=...` for GPT-named data/raw partitions.

Resolve the table for a real target size before ordering a reinstall:

```bash
./debian-install-v2.py plan --config target-host.json --disk-size 512GiB --ram-gib 8 \
  --root-start-sector 2500608 --root-number 3
```

`--ram-gib` is needed when swap size is automatic. Set
`--root-current-size-gib` when `partitioning.root` is `"keep"`. The offline
resolver defaults to root 1 at sector 2048; pass `--root-start-sector` and
`--root-number` to match a target. For the saved netcup host geometry, pass
sector 2500608 and root number 3. The offline table omits preserved pre-root
geometry; the live plan reads it from the actual disk. Both forms show
the resolved table in GiB, and a refusal includes the resolved table when
the provided disk facts are enough to calculate it.

At config load and `build-customscript`, the installer refuses fixed root/data
sizes without a growable remainder, percentages that do not total 100% without
a star, and percent/fixed mixtures. A numeric fixed reserve can be combined
with percentage sizes; `reserve_gb: "*"` cannot. Disk fit, filesystem
minimums, and GPT's 128-partition limit are checked against the target disk.
`swap_policy` must be `on` or `off`.
`on` replaces the guest's configured swap setup with planned GPT partitions: it
disables all currently active swap, removes configured swapfiles and swap
logical volumes, and replaces `/etc/fstab` swap entries. An eligible pre-root swap partition is retained.
Its capacity counts toward both the requested total size and the total
partition count. The default total count of four includes
retained pre-root partitions.
`off` disables active swap, removes `/etc/fstab` swap entries and
root-disk swap partitions, but does not delete swapfiles or logical volumes.
`untouched` swap is refused because it cannot define the plan boundary. A root
shrink needs enough space for current usage and the ext filesystem minimum plus
a 256 MiB margin. Root-shrink safety is checked before swapfile cleanup.
Automatic swap sizing uses
`min(ceil(RAM × 2), round-half-up(20% of disk capacity))`, in GiB.

Exact mounts on `/`, `/var`, `/var/lib`, `/srv`, and `/home` are refused. A
mount equal to, inside, or above protected OS-state trees `/usr`, `/etc`,
`/boot`, `/root`, `/tmp`, `/proc`, `/sys`, `/dev`, `/run`, `/bin`, `/sbin`,
`/lib`, `/lib64`, `/lib32`, `/libx32`, `/var/lib/dpkg`, `/var/lib/apt`,
`/var/lib/systemd`, `/var/lib/cloud`, `/var/cache/apt`, `/var/log`,
`/srv/tree-cache`, and `/srv/tree-upper` is also refused. The refusal names the
requested mount and protected path. `/usr/local` remains protected because the
installer writes executables there in stage one. Mounts intersecting
`/var/lib/docker` or `/var/lib/containerd` are refused because the installer
mounts data in stage two, after Docker may be running, and it does not stop or
seed that state.

`/var/log/journal` and its descendants are supported for persistent journal
data; `/var/log` itself and other log paths remain protected. For example, a
formatted ext4 volume can use this mount in `partitioning.data.volumes`:

The install-time journal stays on the root filesystem and is hidden when the
data filesystem is mounted there; it is not copied into the new filesystem.
The mounted directory will not show those earlier files. Source inspection
verifies this mount and visibility behavior. Whether `journalctl --flush`,
restarting `systemd-journald`, or rebooting resumes persistent writes on the
mounted filesystem is **not verified** by the current tests. Verify that behavior
in the `r1-vm-real-commit` lane before claiming recovery.

```json
{
  "schema_version": 1,
  "partitioning": {
    "root": 40,
    "data": {
      "kind": "partitions",
      "volumes": [
        {"size": "*", "fs": "ext4", "mount": "/var/log/journal", "label": "JOURNAL"}
      ]
    }
  }
}
```

Other supported data mounts include `/srv/cow`, `/srv/data`, and
`/home/runner`. See [README](../README.md#disk-layout) for filesystem and
ordering details and [DESIGN-GUIDE](DESIGN-GUIDE.md#data-mount-safety) for why
these paths differ.

## Run a swap-benchmark window

Use one managed swap partition for a raw benchmark only through the committed
wrapper. Take its PARTUUID, size, and filesystem label from the live host:

```bash
lsblk -b -o NAME,SIZE,PARTUUID,LABEL,FSTYPE /dev/vda
grep ' none swap ' /etc/fstab
```

Run the dry-run first, then the same command inside `tmux`, `screen`, or under
`nohup` before confirming `--commit`. The terminal session must survive an SSH
disconnect:

```bash
target=/dev/vda9
partuuid=<lower-case PARTUUID from lsblk>
bytes=<SIZE from lsblk>

tmux new-session -s swap-bench
sudo tools/swap-bench-window.sh begin "$target" \
  --expect-partuuid "$partuuid" --expect-size "$bytes"
sudo tools/swap-bench-window.sh begin "$target" \
  --expect-partuuid "$partuuid" --expect-size "$bytes" --commit
```

Run the benchmark while the fixed `iobench-raw` partition is untouched. Always
close the window, including after a failed benchmark:

```bash
sudo tools/swap-bench-window.sh end "$target" \
  --expect-partuuid "$partuuid" --expect-size "$bytes"
sudo tools/swap-bench-window.sh end "$target" \
  --expect-partuuid "$partuuid" --expect-size "$bytes" --commit
sudo swapon --show=NAME,SIZE,USED,PRIO
```

`end` refuses while any live `swapoff` exists or while the recorded PARTUUID,
size, PARTLABEL snapshot, swap UUID, or label changed. Do not delete a state
file by hand. It reads the unique `/etc/fstab` row for the saved PARTUUID and
restores only the selected device with that row's options; a different
configured swap that was inactive stays inactive. It verifies the selected
device's identity, active state, and configured priority before archiving the
state. Do not use `swapon -a` for manual recovery. See the
[design rationale](DESIGN-GUIDE.md#swap-window-is-a-recorded-transaction).

## Cloud-init cache policy

The `cloud_init_cleanup_mode` value is shared by the wizard, config file, and
generated custom-script bundle. Choose `retain` (the default) to keep all
cloud-init cache data, `userdata-only` to delete the raw user-data and its
parsed/intermediate copies (`user-data.txt.i`, `cloud-config.txt`, and `obj.pkl`),
`clean` to run plain `cloud-init clean`, or `clean-all` to add `--logs --seed`.
Neither clean mode removes generated SSH/network/fstab configuration; the
installer does not pass `--configs all`.

Cloud-init stores user-data at
`/var/lib/cloud/instance/user-data.txt` and keeps parsed/intermediate copies in
the same instance directory. Plain `clean` resets
cloud-init state so its stages run again at the next boot; `clean-all` does the
same and also removes cloud-init logs and the guest's cached seed. If the same
NoCloud seed ISO remains attached, it may provide the same user-data again.
Before cleanup the installer writes `/var/lib/vbpub/install-v2-complete`
outside cloud-init's cache. A later fresh-install invocation detects the marker
and refuses before it can start a new disk operation. Detach the seed ISO after
installation as well. These cleanup modes
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
python3 -m pip install -r requirements-wizard.txt
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
For a local QEMU first-boot acceptance test and non-secret guest checks, see
the [VM usage/testing guide](../testing/vm/USAGE-TESTING.md).

Consumers of the per-host measurement read `/var/lib/mdt/host-facts/cpu-benchmark.json`
(`mdt-host-cpu-benchmark/1`, written by the installer step `run_cpu_benchmark` or by
`mdt host-setup cpu-benchmark`; see the README). Accept it only when `cpu_model` and
`cpus_online` equal the live values, treat a different `tool_major` as a different scale,
and never copy `result_url` or the claim file into a published record (a Geekbench result
id identifies the machine). The old v1 `run_geekbench` config key stays rejected; its
replacement is `run_cpu_benchmark`.

For a native-versus-TCG Geekbench 6 comparison, run the VM harness from a
root host shell in a physical checkout (not from the devcontainer) and
explicitly select the built-in `system.slice` parent:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT/scripts/debian-install-v2/testing/vm"
export CGROUP_PARENT_DEV_GATES=system.slice
export BUILDX_BUILDER=mdt-managed  # use the host's configured Buildx builder
export MDT_VM_SOURCE_DIR="$REPO_ROOT/scripts/debian-install"
./run-vm-harness.sh start geekbench-tcg --case case-b \
  --ssh-port 2230 --mem 4096 --smp 4 --disk-gb 20 --no-apt-cache
```

The wrapper checks that `system.slice` is loaded and the slice itself has no
CPU/memory limits or CPU-set restriction. A host-wide Docker transient-scope
backstop may still constrain the runner; inspect its live cgroup as described
in the [benchmark procedure](../testing/vm/USAGE-TESTING.md#geekbench-6-tcg-guest-versus-native-host).
`MDT_VM_SOURCE_DIR` mounts the sibling repository script read-only into the
runner so it can be copied into the guest. This bypasses the gate slice only
for that explicit manual benchmark and can compete with production workloads;
wait for a quiet host and follow the
[network-lockdown Geekbench steps](../testing/vm/USAGE-TESTING.md#geekbench-6-tcg-guest-versus-native-host)
to block guest egress after downloading, with an optional narrow Browser
allowlist if you want to publish the result.
The documented run uses the cloud-init `case-b` image; NoCloud needs a
different first-boot credential path, described in the guide's
[image-choice notes](../testing/vm/USAGE-TESTING.md#image-choice).
Geekbench identifies the virtual CPU as `QEMU TCG CPU`; the native comparison
uses four-CPU affinity on the EPYC host. Geekbench may still report the host's
full CPU topology; record both the affinity and reported topology. The free
Geekbench binary automatically uploads results and returned exit 255 without
scores in our offline attempt, so allow host egress only if publication is
acceptable (or use a license with offline support). The successful run scored
1,667 single-core / 4,596 multi-core and is public at
[Geekbench Browser result 19341831](https://browser.geekbench.com/v6/cpu/19341831);
its claim URL is private under `artifacts/vm-lab/`.
Geekbench's single- and multi-core sequences run sequentially and can exceed
the repository runner's 15-minute convenience timeout; invoke the downloaded
binary directly with the three-hour bound in the
[benchmark procedure](../testing/vm/USAGE-TESTING.md#geekbench-6-tcg-guest-versus-native-host).
The same procedure shows host cgprofile sampling at five-second intervals.
Correlate its whole-runner memory series with host-timestamped Geekbench
workload output and an idle-VM baseline for approximate per-phase elapsed time
and host-charged memory; this is not guest process RSS. Keep any claim URL in
the ignored `artifacts/vm-lab/` folder; do not commit it. The guide links
Geekbench's published per-workload working-set figures, clearly distinguished
from measured peak RSS. The reviewed Geekbench 6 materials do not document a
control for per-workload runtime. Do not substitute another benchmark or invent
flags. No fast mode is planned unless Geekbench officially documents such a
control. Any officially supported shortened run would be diagnostic, not an
official comparable score unless Geekbench says otherwise, because the
published composite aggregates the full suite's workload scores.
The guide also explains that the
documented five-second workload gaps add at most about 2 minutes 35 seconds to
the full CPU suite; the TCG runtime is actual workload execution. Do not
shorten it by using undocumented switches or modifying the binary.

## Boot a Debian cloud VM with the private seed ISO

Use a Debian Trixie generic cloud image as the VM's boot disk and the ISO from
`build-iso` as a separate NoCloud data CD-ROM. The seed ISO is not bootable and
is not an operating-system installer. In every hypervisor, set the Debian disk
first in boot order; keep the seed CD-ROM attached as cloud-init metadata.

The Debian installer grows/manages the guest root and swap partitions on its
boot disk. Give that virtual disk the capacity your target needs. The README
shows nominal 20–512 GB example disks; these are examples, not a supported
capacity range. See the [disk/swap overview](../README.md#disk--swap). The
downloaded QCOW2's file size is compressed storage use, not its guest virtual
capacity. Inspect/resize the virtual disk before first boot.

### Prepare the artifacts

On a Linux workstation, build the private seed ISO as described above. Then
download the current Trixie amd64 generic cloud QCOW2 and verify its SHA-512
checksum using the matching entry from Debian's checksum file:

```bash
IMAGE_BASE='https://cloud.debian.org/images/cloud/trixie/latest'
IMAGE='debian-13-genericcloud-amd64.qcow2'
curl -fLO "$IMAGE_BASE/$IMAGE"
curl -fLO "$IMAGE_BASE/SHA512SUMS"
set -o pipefail
grep -F "$IMAGE" SHA512SUMS | sha512sum --check -
```

The image is published in Debian's [Trixie cloud image directory](https://cloud.debian.org/images/cloud/trixie/latest/).
The `latest` directory moves when Debian publishes a new image. Keep the
checksum file from the same directory as the image. The installer ISO contains
per-host secrets; transfer and store it as a private file.

### TrueNAS 27+

TrueNAS needs hardware virtualization exposed to it, memory for both TrueNAS
and the Debian guest, a VM storage pool, and an active network bridge. Read
TrueNAS's [VM management guide](https://www.truenas.com/docs/scale/27/virtualmachines/managingvms/)
and [VM screen guide](https://www.truenas.com/docs/scale/27/virtualmachines/virtualmachinesscreens/),
and check that its VM preflight reports hardware virtualization support. Do
not select the Debian NoCloud seed ISO as bootable installation media: in the
TrueNAS VM UI, create/import a Debian disk in **Disks**, then add the seed ISO
as a CD-ROM device. The virtual disk is the boot medium.

1. Copy the Debian QCOW2 and private seed ISO into a dataset, for example
   `/mnt/tank/staging/`. Use an existing secured SMB/NFS share or SSH/SCP.
2. In **Virtual Machines**, create a UEFI Debian VM. Set the intended vCPU and
   RAM amounts, and attach a NIC to a bridge that can reach APT and the selected
   Git remote over HTTPS. Keep the VM stopped and disable autostart through disk
   conversion; the API client refuses import unless both conditions hold.
3. Add the Debian disk by importing the QCOW2 into a new ZFS volume. Set the
   target virtual disk capacity explicitly. `tnt` (`truenas/tnt`) does not
   have this verb yet (backlog TNT-61); until it does, use the reference client
   from the [prior-art directory](../../../truenas/tnt/docs/prior-art/truenas-vm/docs/CONSUMERS.md)
   (install and API-key setup are described there):

   ```bash
   ./truenas-vm.py disk-image-import 4 \
     --source /mnt/tank/staging/debian-13-genericcloud-amd64.qcow2 \
     --zvol-name tank/vm/debian-root --zvol-size-gib 40
   ```

   Replace VM ID, pool/dataset, and capacity with the values chosen for this
   host. The CLI checks that the requested ZFS volume is large enough for the
   image and uses TrueNAS to create and fill the volume. Do not start the VM
   until conversion is confirmed complete; the device's automatic boot order
   does not prevent an incomplete disk from booting. If import is interrupted
   or fails, follow the job-check and storage-recovery steps in the
   [reference client guide](../../../truenas/tnt/docs/prior-art/truenas-vm/docs/CONSUMERS.md).
4. Add a CD-ROM device whose path is
   `/mnt/tank/staging/debian-vbpub.iso`. Add or verify a bridged NIC and set
   boot order with the Debian disk first and the CD-ROM second. The ISO only
   supplies cloud-init data.
5. Open the VM console, start the VM, and confirm cloud-init reaches the
   installer launcher. After the installer finishes both stages, detach the
   seed ISO before the next boot.

For VM create, device, power-state and console steps through the API, use
[`tnt`](../../../truenas/tnt/README.md) (for example `tnt vm create` and
`tnt vm console`); it requires a TLS WebSocket endpoint and TrueNAS 27.0 or
newer. The reference client above additionally lists, updates and deletes
individual VM devices.

### Proxmox VE with ZFS storage

On the Proxmox node, select an unused VM ID, a ZFS storage that accepts VM
images, and a bridge connected to the network the Debian guest should use.
`pvesm status` shows configured storage names and types; do not assume a storage
called `local-zfs` exists. Upload the seed ISO to a storage that accepts ISO
images, using the Proxmox UI or its documented storage path.

Create the UEFI VM and import the QCOW2 to the selected ZFS storage:

```bash
VMID=900
ZFS_STORAGE='your-zfs-storage-id'
BRIDGE='your-bridge-name'
IMAGE='./debian-13-genericcloud-amd64.qcow2'
qm create "$VMID" --name debian-vbpub --memory 4096 --cores 2 \
  --machine q35 --bios ovmf --scsihw virtio-scsi-single \
  --net0 "virtio,bridge=$BRIDGE"
qm set "$VMID" --efidisk0 "$ZFS_STORAGE:1"
qm importdisk "$VMID" "$IMAGE" "$ZFS_STORAGE"
qm config "$VMID"
```

Read the `unusedN` disk entry printed by `qm config` and copy its storage volume
ID into `UNUSED_DISK`. The EFI disk may have consumed disk number 0, so derive
this value from `qm config` instead of assuming its number:

```bash
UNUSED_DISK='your-zfs-storage-id:vm-900-disk-1'
qm set "$VMID" --scsi0 "$UNUSED_DISK"
qm resize "$VMID" scsi0 40G
qm set "$VMID" --ide2 'your-iso-storage-id:iso/debian-vbpub.iso,media=cdrom'
qm set "$VMID" --boot 'order=scsi0;ide2'
qm start "$VMID"
```

Replace the VM ID, both storage IDs, bridge, and disk capacity. The example
resizes the imported guest disk to 40 GiB; choose a capacity that fits the
target workload. Disk import and resize are separate Proxmox operations. Open
the VM console to follow the first boot and installer, then remove the seed
CD-ROM device after successful stage two. See the official
[Proxmox VE administration guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.html)
for `qm importdisk`, VM storage, and disk attachment details.

### Hyper-V

Hyper-V needs a VHDX boot disk. Convert the QCOW2 with `qemu-img` on a machine
where it is installed, then grow the VHDX to the intended VM disk capacity
before attaching it. In PowerShell, select an existing virtual switch from
`Get-VMSwitch` and set its exact name below:

```powershell
$Image = (Resolve-Path .\debian-13-genericcloud-amd64.qcow2).Path
$Vhdx = Join-Path $PWD 'debian-vbpub.vhdx'
$SeedIso = (Resolve-Path .\debian-vbpub.iso).Path
$VmName = 'debian-vbpub'
$VmPath = Join-Path $env:ProgramData "Microsoft\Windows\Hyper-V\$VmName"
$SwitchName = 'replace-with-an-existing-switch'

qemu-img convert -p -f qcow2 -O vhdx -o subformat=dynamic $Image $Vhdx
Resize-VHD -Path $Vhdx -SizeBytes 40GB
New-VM -Name $VmName -Generation 2 -MemoryStartupBytes 4GB `
  -VHDPath $Vhdx -Path $VmPath -SwitchName $SwitchName
Add-VMDvdDrive -VMName $VmName -Path $SeedIso
$Disk = Get-VMHardDiskDrive -VMName $VmName
$Dvd = Get-VMDvdDrive -VMName $VmName
Set-VMFirmware -VMName $VmName -EnableSecureBoot On `
  -SecureBootTemplate MicrosoftUEFICertificateAuthority `
  -BootOrder $Disk, $Dvd
Start-VM -Name $VmName
```

Replace the 40 GiB capacity and switch name for the target. This uses a
Generation 2 UEFI VM and boots the hard disk before the NoCloud data CD-ROM.
Open the Hyper-V console to follow first boot. Detach the seed DVD after the
installer finishes stage two. See Microsoft's [`New-VM`](https://learn.microsoft.com/en-us/powershell/module/hyper-v/new-vm?view=windowsserver2025-ps)
and [`Set-VMFirmware`](https://learn.microsoft.com/en-us/powershell/module/hyper-v/set-vmfirmware?view=windowsserver2025-ps)
reference for generation and boot-order details.

### First-boot checks

The VM needs outbound HTTPS and working APT sources. It also needs a reachable
public Git remote and the configured bootstrap and `cli-extended` release
manifest URLs. If cloud-init does not find the seed, verify the ISO label is
`cidata`, the CD-ROM is attached as a data disk, and the VM still boots the
Debian disk first. Do not change the installer ISO or treat the NoCloud ISO as
an operating-system boot medium.

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
