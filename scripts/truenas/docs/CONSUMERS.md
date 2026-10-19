# Using shared TrueNAS tooling

## Run a local TrueNAS TCG guest

Use the local runner when API or patch acceptance needs a TrueNAS host. The
runner starts a separate Docker container in the configured background slice,
not a QEMU process inside the interactive devcontainer. It requires the
environment values `CGROUP_PARENT_DEV_BACKGROUND` and
`DOCKER_NETWORK_INTERNAL`, a running host cgroup inspector with system D-Bus
access, an existing image containing QEMU, and local OVMF code and variable
template files. The caller selects these explicitly so an absent or stale
image or firmware file cannot silently choose a different build.

```bash
cd scripts/truenas
mkdir -p .vm/v27-path-b
export TRUENAS_TCG_IMAGE='debian-install-vm:<installed-image-tag>'
python3 tcg_vm.py start \
  --name truenas-v27-path-b \
  --base-disk golden-iso/vm/v5-tcg-target.qcow2 \
  --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd \
  --ovmf-vars /usr/share/OVMF/OVMF_VARS_4M.fd \
  --state-dir .vm/v27-path-b \
  --image "$TRUENAS_TCG_IMAGE" \
  --max-concurrent-guests 3
python3 tcg_vm.py status --name truenas-v27-path-b --state-dir .vm/v27-path-b
```

Choose the `--image` value from an image already built for QEMU testing; list
candidate images with `docker image ls debian-install-vm --format
'{{.Repository}}:{{.Tag}}'`. The runner checks that QEMU tools exist in the
image, copies the explicitly selected OVMF files into the state directory,
allocates a fresh qcow2 COW overlay, and preserves the writable OVMF variable
store. Up to three managed guests may run at once; the default maximum is
three, and `--max-concurrent-guests 1` or `2` sets a lower limit. Each start
checks memory PSI from `memory.pressure` at the selected slice and its ancestors. Admission
refuses when `some avg10` reaches 10% or `full avg10` reaches 1%, when PSI is
unavailable at a finite-memory cgroup, or when current usage has reached a
finite hard `memory.max`. `memory.high` is advisory, and the 3.5 GiB container
limit is an upper bound rather than reserved capacity; nominal cap
overcommit is allowed while recent PSI remains below policy. Starts are
serialized across worktrees. For a stopped
guest, retain the same directory and repeat the command
with `--resume`; the overlay, EFI variables, and guest state continue. The
base disk is mounted read-only.

For disposable storage-topology tests, add one or more sparse QCOW2 data disks
to a fresh guest by repeating `--data-disk-size`:

```bash
python3 tcg_vm.py start \
  --name truenas-storage-topology \
  --base-disk golden-iso/vm/v5-tcg-target.qcow2 \
  --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd \
  --ovmf-vars /usr/share/OVMF/OVMF_VARS_4M.fd \
  --state-dir .vm/storage-topology \
  --image "$TRUENAS_TCG_IMAGE" \
  --data-disk-size 4G \
  --data-disk-size 1G \
  --data-disk-size 1G \
  --data-disk-size 1G
```

The runner creates these as sparse files in the guest state directory and
records their ordered virtual sizes in `runner.json`. `--resume` requires the
same ordered sizes and checks each disk image's virtual size before starting
QEMU. At most eight extra disks are supported. Use TrueNAS `disk.query` and the
UI to identify the guest disks before assigning them; do not rely on a guessed
device name. The runner pins these devices to PCI slots above the boot disk so
the base image's UEFI boot entry remains valid. Sparse QCOW2 disks are suitable
for topology and API/UI behavior checks, but they do not represent physical-disk
performance or independent failure domains. See the [runner rationale](DESIGN-GUIDE.md#sparse-additional-data-disks).

After a host restart, `--resume` also tolerates a changed filesystem device
number for the two OVMF source files when their recorded paths, inode, size,
modification time, and SHA-256 still match. A changed path or file content is
still refused; do not edit `runner.json` by hand.

The default full-pressure threshold is 1%. For an explicitly approved run
with available headroom, `--memory-psi-full-avg10-limit 2` raises only that
per-run full-pressure threshold to 2%; the 10% `some avg10` limit, finite
`memory.max` checks, cgroup placement, and three-guest cap still apply. The
runner prints the selected threshold and measured PSI during preflight.

The fixed caps are 200% CPU, 3.5 GiB container memory, no swap, and 3 GiB guest
RAM. Before creating a container, the runner verifies the selected systemd
slice is loaded, its finite CPU and hard memory limits, recent memory PSI at
each finite ancestor, and the Docker cgroup path and resource caps after
launch. It refuses starts above the configured maximum of three running
managed guests. A graceful stop requests
ACPI shutdown and waits up to two minutes; it never force-kills a guest.

## Use the graphical installer console

The runner uses `-display none` by default. For an installer that requires VGA,
add `--vnc-console` to its `start` command. QEMU then listens on
`<state-dir>/vnc.sock` as a Unix-domain socket only; it does not expose VNC on
TCP. The runner prints the host socket path, using a short `/tmp` symlink when
the state path is too long for AF_UNIX. A VNC client that supports Unix sockets
can connect to that path.

To add the console to a stopped guest, repeat the original `start` invocation
with `--resume --vnc-console`, keeping its name, base disk, image, firmware,
network, slice, ports, and any read-only media the same. The overlay and OVMF
variables are reused. For the current VGA frame and basic keyboard input, use
the QMP-backed helpers while the managed guest is running:

```bash
python3 tcg_vm.py console screendump \
  --name truenas-v28-installer \
  --state-dir .vm/v28-installer \
  --filename installer.ppm
python3 tcg_vm.py console sendkey \
  --name truenas-v28-installer \
  --state-dir .vm/v28-installer \
  ret
```

The screenshot is written in PPM format under the state directory. A screenshot
filename must be a simple basename and cannot overwrite an existing file.
`sendkey` accepts a QEMU key name or a hyphen-separated combination such as
`ctrl-alt-delete`. These helpers target only a running managed container whose
state-directory identity matches the supplied path. The socket and QMP commands
are described in the [runner design](DESIGN-GUIDE.md#graphical-installer-console).

For an installer boot entry that uses the serial port, start the guest with
`--serial-console`. For a stopped guest, repeat its original `start` command
with `--resume --serial-console` and the same name, disks, firmware, network,
slice, ports, and read-only media. This changes the QEMU console backend, so it
cannot be added to an already-running process. Connect from an interactive
terminal with:

```bash
python3 tcg_vm.py console serial \
  --name truenas-v28-installer \
  --state-dir .vm/v28-installer
```

The command relays terminal input and guest serial output; press `Ctrl-]` to
disconnect. Serial traffic is also appended to `<state-dir>/serial.log`. The
socket is Unix-domain only. QEMU does not choose the guest baud rate; select a
boot entry such as 115200 baud when the guest provides one. A serial socket
only helps when the guest's firmware or bootloader routes output to its UART.

```bash
python3 tcg_vm.py stop --name truenas-v27-path-b --state-dir .vm/v27-path-b
python3 tcg_vm.py destroy --name truenas-v27-path-b  # removes container only
```

If graceful stop times out and QMP reports that the guest is paused, use
`tcg_vm.py terminate-paused` with the same name and state directory, then run
`destroy`. This stops the QEMU process without guest OS shutdown and preserves
the COW and EFI state for inspection; use it only when that loss of guest-level
flush is acceptable.

`destroy` preserves the state directory. Delete the COW overlay and EFI
variable store only when their contents are no longer needed. The guest's SSH
and HTTPS are reachable to peers on `DOCKER_NETWORK_INTERNAL` as
`truenas-v27-path-b:2222` and `truenas-v27-path-b:8443` by default. A TrueNAS
live acceptance runs through the networked registered lanes below; do not run
the test suite in the TrueNAS VM runner itself.

## Rebind an ISO path while preserving guest state

Use this when an identical ISO has moved to its canonical project path but a
stopped guest's runner manifest still records an old path. Stop the guest
through ACPI, remove only its stopped Docker container, then ask the runner to
verify and update the ISO path:

```bash
python3 tcg_vm.py stop --name truenas-v27-path-b --state-dir .vm/v27-path-b
python3 tcg_vm.py destroy --name truenas-v27-path-b
python3 tcg_vm.py rebind-media \
  --name truenas-v27-path-b \
  --state-dir .vm/v27-path-b \
  --readonly-media boot-pool-patch/artifacts/TrueNAS-28.0.0-MASTER-20261014-230204.iso
```

Then repeat the original `start` command with `--resume` and the canonical
`--readonly-media` path. Keep the same base disk, QEMU image, network, slice,
ports, and state directory. `rebind-media` requires the old ISO to still exist,
checks its recorded file identity, and compares the new ISO's size and
SHA-256 before changing the manifest. It refuses to change guest state or to
bind a different ISO. Rebind every retained guest manifest that names the old
path. After each manifest points at the canonical path, remove the old path
only if it is a redundant alias and the canonical file remains in place; then
resume the guest. This keeps old aliases from accumulating while preserving
the overlay and EFI state.

## Reserve boot-pool size during install

The stock installer uses the rest of each selected disk for partition 3 and
creates `boot-pool` there. To leave a larger disk tail available, patch an
official TrueNAS x86-64 installer ISO with an explicit partition size. Verify
the input ISO against its published checksum first. Install `xorriso` and
`squashfs-tools`, then build a separate output ISO:

```bash
sudo apt-get install python3 xorriso squashfs-tools
sudo python3 vanilla-iso-patcher/patch_iso.py \
  /path/to/TrueNAS.iso \
  /path/to/TrueNAS-boot-pool-24GiB.iso \
  --boot-pool-size-gib 24
```

The patcher requires root to preserve the live filesystem's numeric ownership,
special device files, and extended attributes. It reports the source and
patched ISO SHA-256 values, refuses to overwrite the input or an existing
output, and fails if the expected installer code or BIOS/UEFI boot entries are
missing. The output keeps the standard PC BIOS/UEFI boot entries but drops the
Apple-specific HFS+/APM hybrid layer because the tested xorriso version
produced overlapping GPT entries when replaying it. Do not use the patched
image when Apple-specific boot support is required. The configured value is
applied to every disk selected for the boot pool; a multi-disk boot pool uses
the same partition size on each member.
Supported sizes are 16–1024 GiB. For a 16 GiB partition, use a destination disk
at least about 17 GiB so the installer can also create its BIOS and EFI
partitions.

Boot the output ISO and install normally. The selected disks are still wiped.
After first boot, inspect the actual layout and available tail before creating
anything there:

```bash
sudo sgdisk -p /dev/sda
sudo sgdisk -v /dev/sda
zpool list boot-pool
```

The ISO leaves the tail unallocated; it does not create a data partition or a
pool. Stock TrueNAS pool creation still expects whole disks, so using this tail
as a separate pool requires the partition-aware workflow described in the
[single-disk investigation](../boot-pool-patch/README.md). The generator rebuilt
the official 25.10.7, 27.0.0-RC.1, and selected v28 nightly ISOs; each retained
BIOS and UEFI entries and passed `sgdisk -v`. TrueNAS 25.04.2.6 has
source-level evidence only. Compatibility is tied to the checked installer
module and partition command, not just a version string. See the
[patcher consumer guide](../vanilla-iso-patcher/CONSUMERS.md), the
[ISO checksum record](../vanilla-iso-patcher/COMPATIBILITY.md), the
[patcher implementation notes](../vanilla-iso-patcher/README.md) and
[design rationale](DESIGN-GUIDE.md#bounded-boot-pool-installs).

The patched v28 nightly was also installed under QEMU TCG on a blank 20 GiB
disk. The install succeeded with a 16 GiB partition 3; `sgdisk -v` passed and
reported 3.5 GiB of free space on the disk. The other release builds have not
had a live installation acceptance.

## Setup

From `scripts/truenas`, install the in-repository CLI framework and the pinned
official TrueNAS API client into the Python environment used to run these
scripts:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ../../libraries/cli-extended
python -m pip install -r requirements.txt
cp truenas.toml.example truenas.toml
cp .env.example .env
```

Edit `truenas.toml` (schema version 1) with the `wss://HOST/api/current`
endpoint, API-key username, and TLS verification choice. Put the API key in
`.env` or export `TRUENAS_API_KEY` in the process environment. Keep `.env` and
`truenas.toml` local; they are ignored by Git. Help and version output work
before setup.

Create an API key with the permissions needed for the selected operations.
The official client uses SCRAM-SHA-512 on TrueNAS 26 and later. Image upload
uses the separate HTTPS `/_upload` endpoint with the key as a Bearer
credential, so `api.verify_ssl` must be `true` for the install workflow. The
`filesystem.put` API method requires the TrueNAS `FULL_ADMIN` role. The tools
do not need SSH or SFTP for staging image files.

## Explore the API and storage

```bash
./truenas-api.py info
./truenas-api.py info --json
./truenas-api.py methods vm.
./truenas-api.py vms
./truenas-api.py jobs
./truenas-api.py watch-job 153
./truenas-api.py watch-event vm.query --timeout-seconds 60

./install-vm.py pools
./install-vm.py datasets
./install-vm.py datasets tank
```

`pools` reports pool status, health, and capacity. `datasets` lists filesystem
datasets and ZVOLs with their full dataset id, type, pool, relative path, and
mountpoint. Use the `relative_path` shown by `datasets POOL` as the
`--dataset` value; it is the full dataset path below that pool.
Machine-readable `--json` output converts middleware date/time values to
ISO-8601 strings. A TrueNAS boot pool is not a substitute for a data pool for
VM images; a host with no data pools reports an empty list from `pools`.

Generic API parameters are a JSON array of positional arguments. Mutating calls
require `--effect mutating` and a confirmation:

```bash
./truenas-api.py call vm.query --effect read
./truenas-api.py call vm.device.convert \
  '[{"source":"/mnt/tank/images/debian.qcow2","destination":"/dev/zvol/tank/vms/debian/root"}]' \
  --effect mutating --job --dry-run
```

The declared effect is the caller's classification. The client does not
consult method metadata to verify it. Use the versioned server schema before
calling an unfamiliar method.

## Install a Debian cloud image

1. Choose an official Debian cloud image for the guest architecture. For an
   x86-64 TrueNAS host, a `generic-amd64` QCOW2 image is a virtual disk. Debian
   also publishes raw cloud images and other architectures on its
   [cloud image page](https://www.debian.org/distrib/).
2. Choose an ONLINE pool and inspect existing dataset paths:

   ```bash
   ./install-vm.py pools
   ./install-vm.py datasets tank
   ```

3. Select the dataset path under the pool where the uploaded source image will
   live. The path is relative to the pool. Missing components are created as
   filesystem datasets. Choose a new ZVOL name and a virtual size large enough
   for the source image. The installer does not infer virtual capacity from a
   compressed QCOW2 file's byte size.
4. Select a bridge or MACVLAN parent from
   `vm.device.nic_attach_choices`, then review a dry-run plan:

   ```bash
   ./install-vm.py install debian13 \
     --source-image ./debian-13-generic-amd64.qcow2 \
     --pool tank \
     --dataset vm-images/debian13 \
     --zvol-name root \
     --disk-size-gib 32 \
     --memory-mib 2048 \
     --vcpus 2 \
     --nic-attach br0 \
     --dry-run
   ```

5. Run the same command without `--dry-run`; confirm the operation when
   prompted. The installer creates missing filesystem datasets, reads the
   selected dataset's actual mountpoint from TrueNAS, uploads the local image
   over verified HTTPS, creates the VM-managed ZVOL, converts the uploaded
   image directly into that ZVOL, and attaches the network interface. Add
   `--start` to start the VM and wait for `RUNNING`, or start it later with
   `./install-vm.py start VM_ID`.
6. Provide the Debian cloud image's cloud-init user-data and metadata through a
   separately staged NoCloud seed or another guest-provisioning method. This
   installer does not create a seed, set a guest password, or create a user.
   Without guest provisioning, a successfully booted image may not be
   remotely accessible.

The QCOW2 image is not ISO media. It is transferred as a local file, then
converted into a ZVOL used as the VM disk. TrueNAS's `filesystem.put` API job
can also upload an ISO file, but this install command only converts virtual
disk formats. To boot an ISO, a separate workflow must create a VM CD-ROM
device pointing to the uploaded ISO and arrange an installer/unattended
installation path.

The upload and VM workflow is multi-step, not transactional. On failure the
CLI reports the current stage plus any created datasets, uploaded image path,
ZVOL, and VM id. It does not automatically remove artifacts because cleanup
could delete operator data. The ZVOL size is an explicit input; if it is too
small for the virtual source disk, TrueNAS conversion fails after earlier
artifacts may already exist.

Firmware choices are `UEFI` (default) and `UEFI_CSM`. The installer otherwise
uses the host's default guest architecture. It does not resize guest
partitions/filesystems or create cloud-init metadata.

## Manage VM lifecycle

```bash
./install-vm.py list
./install-vm.py status VM_ID
./install-vm.py start VM_ID --timeout-seconds 180
./install-vm.py watch VM_ID --until RUNNING --timeout-seconds 180
./install-vm.py stop VM_ID
./install-vm.py delete VM_ID
```

`watch --until` accepts `RUNNING`, `STOPPED`, or `SUSPENDED`. The default stop
requests a graceful shutdown and follows the TrueNAS job's progress and
completion. `stop --force` requests forced power-off. VM deletion requires
confirmation and is allowed by default only after TrueNAS reports `STOPPED`.
If status is null or the VM is running/suspended, stop it or pass `--force`
after reviewing the state. Deletion retains storage by default; add
`--delete-zvols` only when TrueNAS-managed ZVOLs should also be removed. The
uploaded source image remains in the selected dataset and needs separate
cleanup.

Progress-reporting commands accept `--progress` with `auto`, `tty`, `plain`,
`quiet`, or `rawjson`. `auto` is the default and selects a terminal or plain
stream based on the output destination.

## Version scope

These scripts require the modern `/api/current` JSON-RPC endpoint and target
TrueNAS v27. The generic client passed a live smoke test against TrueNAS
`27.0.0-RC.1` in both local QEMU and the Netcup host. The local test covered
API-key authentication, runtime method discovery, event subscription, and one
job's progress/completion. The Netcup test covered API-key authentication,
`system.info`, and pool, dataset, and VM queries. VM installation has not been
end-to-end tested because the live host has no data pool and lacks nested
virtualization. VM schemas have been reviewed against v28 docs, which are
currently Preview. A v28 host has passed the live patch/API gate, while
storage, boot-remount, UI, and KVM acceptance remain open. TrueNAS 24.10 needs a
legacy WebSocket, authentication, and job adapter described in the
[design guide](DESIGN-GUIDE.md#api-version-compatibility). Do not point the
current config at a 24.10 host.
