# Shared TrueNAS tooling design

## Isolated local QEMU guests

The devcontainer is the operator cockpit; TCG is CPU-intensive and can make
that cockpit unresponsive. `tcg_vm.py` therefore launches QEMU in a separate,
unprivileged Docker container under `$CGROUP_PARENT_DEV_BACKGROUND`. This
keeps guest CPU and memory accounting in the host's background tier, while
Docker applies a 200% CPU ceiling and a 3.5 GiB memory ceiling with swap
disabled. The guest receives 3 GiB RAM. These are fixed guardrails rather than
operator-tuned defaults because a larger guest can consume the interactive
container's headroom through shared host memory pressure.

The launcher derives host bind paths from Docker's live mount table and checks
the systemd slice through the host D-Bus and cgroupfs exposed by the host
inspector. It refuses an unloaded slice, a selected background slice without
a finite `memory.max`, memory pressure above policy, an unverified Docker
network/image, or more than three running managed guests. The default limit is
three; the operator may lower it with `--max-concurrent-guests`. Starts share
an advisory lock in the repository's common Git directory, so concurrent
worktree invocations cannot both pass the count and pressure checks before
creating a container.

The runner reads each cgroup's `memory.pressure` at the selected slice and its
ancestors. It refuses admission at `some avg10 >= 10%` or `full avg10 >= 1%`;
missing PSI at a finite-memory cgroup also refuses. `memory.high` is advisory,
and the guest's 3.5 GiB container limit is an upper bound rather than reserved
capacity. The runner permits nominal cap overcommit when measured PSI is low,
while retaining each guest's hard container limit and the selected slice's
finite `memory.max`. It refuses only when actual `memory.current` has reached
that hard maximum. The live PSI thresholds are a short-term load signal, not a
guarantee against a later burst or a host-wide OOM event. The runner verifies
the actual container cgroup and limits after startup. The base disk is
read-only; guest writes, a copy of the
explicit OVMF code file, and the writable OVMF variable store live in a
separate COW state directory. A graceful stop uses
QMP/ACPI and leaves the runner running if the guest does not shut down within
the timeout, so the operator can inspect it before taking further action.
The explicit `terminate-paused` recovery checks the managed-container and
state-directory identities and requires QMP to report the guest paused before
stopping the QEMU process. It does not ask the guest OS to shut down; the COW
overlay and EFI state remain for inspection, and `destroy` remains a separate
step.

The runner records device numbers for its OVMF source files. Recreating the
devcontainer after a host restart can change `st_dev` even when the source
path, inode, size, modification time, and SHA-256 remain identical. Resume
normalizes only that device-number field for `firmware_code` and
`firmware_vars_template`; every other manifest field must still match, and the
staged firmware code is checked against the recorded SHA-256 before QEMU
starts. A changed path or file content remains a refusal.

### Sparse additional data disks

Storage acceptance sometimes needs several guest-visible disks, such as a
single-disk pool and a three-disk RAIDZ1 pool. `tcg_vm.py start` can create up
to eight extra QCOW2 images with repeated `--data-disk-size` flags. It records
the ordered byte sizes in the runner manifest, creates the files with
preallocation disabled inside the existing state directory, and attaches each
as a separate virtio block device. Resume requires the same ordered sizes and
checks the stored QCOW2 virtual sizes before QEMU starts.

These images stay sparse on the host and remain writable guest state beside
the main overlay. They are appropriate for middleware/API/UI topology tests
and cannot establish physical disk latency, real device failure behavior, or
independent redundancy. We attach virtual files rather than host block devices
so the managed guest stays isolated from production storage and the runner can
preserve the exact disposable state across restart. Each extra virtio device
uses a fixed PCI address above the primary disk's existing UEFI boot path. An
initial topology guest exposed the failure mode when automatic slot assignment
moved the boot disk and left OVMF at its shell.

An operator may set `--memory-psi-full-avg10-limit` for one start when current
headroom is known and a temporary PSI spike would otherwise block it. The
default remains 1%; the explicit limit changes only the full-pressure test.
The `some` threshold, finite hard `memory.max`, cgroup placement and guest cap
remain enforced, and the chosen value is printed with the preflight evidence.

The host connects to QMP through the state-directory bind mount. Linux limits
pathname-based AF_UNIX sockets to 107 bytes, so the runner creates a short-lived
symlink under `/tmp` when the resolved `qmp.sock` path exceeds that limit. This
keeps deep worktree paths usable while ensuring startup checks and graceful
shutdown can still reach the guest's QMP socket.

### Graphical installer console

The standard TrueNAS installer writes its interface to VGA, so the serial log
cannot drive it. Keep QEMU headless by default, but allow an explicit
`--vnc-console` start to replace `-display none` with
`-vnc unix:/state/vnc.sock`. QEMU listens only on that Unix-domain socket; the
option does not add a TCP listener. The socket lives in the existing state
directory and is not part of the disk/firmware identity, so a stopped guest can
resume the same COW overlay with or without the console option. Before a VNC
start, the runner removes a stale socket only after confirming it refuses
connections, and refuses a live socket or any non-socket path. When the host
state path exceeds the Unix socket limit, it prints a stable short symlink in
`/tmp` that points to `<state-dir>/vnc.sock`.

The `tcg_vm.py console` commands send `screendump` and `sendkey` through the
existing QMP socket, scoped to a running managed guest and its recorded state
directory. Screenshots are written as PPM files under that state directory;
key input accepts only one QEMU key name or a hyphen-separated combination.
This lets a graphical install be inspected or driven without changing the
normal headless testing path. See the
[operator steps](CONSUMERS.md#use-the-graphical-installer-console).

Some installers expose a serial boot entry even when the VGA capture is not
usable. An explicit `--serial-console` start replaces the one-way serial file
backend with QEMU's two-way Unix socket chardev at `<state-dir>/serial.sock`
and records both directions in `serial.log`. `tcg_vm.py console serial`
attaches the operator's terminal to that socket; it is scoped to the same
managed guest and state directory, and does not open a TCP port. The guest's
firmware/bootloader must route its menu or kernel console to the emulated UART
for output to appear there. The runner
does not set a baud rate: a boot entry such as 115200 baud configures that in
the guest. Adding this chardev requires a guest restart and cannot attach to a
QEMU process that is already running.

### Rebinding read-only media paths

Runner identity records include the path and SHA-256 of an attached ISO, so a
resume cannot silently follow a renamed, replaced, or aliased file. When
identical media moves to its canonical project directory, `rebind-media`
provides an explicit transition: the guest's container must be removed after a
clean stop, the old file must still match its recorded identity, and the new
file must have the same size and SHA-256. The command then updates only the
manifest's read-only media record. It leaves the COW disk and OVMF variables
untouched; a subsequent `start --resume` creates a fresh container with the
new bind path.

This explicit check supports retiring compatibility aliases without allowing
a stale manifest to attach different installer media. If the old path is
already gone, the operation fails and requires restoring the old file or
reviewing the manifest source. See the [consumer steps](CONSUMERS.md#rebind-an-iso-path-while-preserving-guest-state).

This is test infrastructure, not a hypervisor or a resource-isolation
boundary from the host: TCG still consumes host CPU and RAM. Check live slice
pressure before each start and keep each COW state directory separate. Up to
three guests may run when `memory.pressure` stays below the admission thresholds
and the hard cgroup limits remain finite. See the
[consumer steps](CONSUMERS.md#run-a-local-truenas-tcg-guest).

## Bounded boot-pool installs

The x86-64 installer ISO has an ISO9660 tree with its live system at
`/live/filesystem.squashfs`, separate BIOS and UEFI El Torito boot entries,
and a hybrid GPT/APM system area. Inside the live SquashFS,
`truenas_installer/install.py` creates GPT partition 1 for BIOS boot, partition
2 for EFI boot, and partition 3 for ZFS. The installer source checked for
TrueNAS 25.04/25.10, 27, and the inspected 28 nightly uses
`sgdisk -n3:0:0` for partition 3, extending it through the end of each
selected disk. The installer then creates `boot-pool` from that partition, or
mirrors those partitions when multiple disks are selected. Replacing that
one end-sector argument with `-n3:0:+<size>G` caps the pool backing partition
and leaves the disk tail unallocated. GPT fdisk interprets `G` as GiB.

The patcher runs as root, extracts the live SquashFS, changes only the
partition 3 argument in the installer module, and rebuilds that SquashFS with
the original compression and block size. It replaces `/live/filesystem.squashfs`
in the ISO tree, replays BIOS and UEFI metadata, and verifies both the patched
source and El Torito entries from the written ISO. The matcher refuses changes
to the package path or partitioning call. This is a build-time choice: the ISO
embeds one cap and does not prompt during installation. The input ISO's
Apple-specific HFS+/APM layer is omitted because replaying it with GPT on the
tested xorriso 1.5.6 produced overlapping GPT entries; this retains standard
PC BIOS/UEFI installer boot entries, not Apple-specific booting. xorriso adds
trailing write padding after the ISO's logical last sector, so the generator
trims that padding to keep the GPT backup header at the end of the image. The
official 25.10.7, 27.0.0-RC.1, and v28 nightly ISO rebuilds retained BIOS and
UEFI entries, and each output passed `sgdisk -v`; the v25.04 evidence is
source-level only. The patched v28 nightly also completed an installer run
under QEMU TCG on a blank 20 GiB disk. The resulting partition 3 was exactly
16 GiB, with 3.5 GiB unallocated; `sgdisk -v` reported no GPT problems. The
v25.10.7 and v27 ISO builds have not had live installation acceptance. Exact
ISO checksums and the disk layout evidence are in the
[compatibility record](../vanilla-iso-patcher/COMPATIBILITY.md).

Leaving the tail unallocated is the smallest change that supports a later
partition or data pool without changing TrueNAS middleware. It does not create
that second partition, make the stock UI treat a boot disk as a data disk, or
change the installer's whole-disk wipe behavior. The target disk must be larger
than the selected boot-pool partition because the installer also needs its
BIOS and EFI partitions and GPT overhead. A future installer source change
must be inspected and accepted before the patcher can support it.

## API version compatibility

The client targets the versioned JSON-RPC WebSocket endpoint
`wss://HOST/api/current`. It uses the official TrueNAS Python client for
WebSocket framing, authentication, event subscriptions, and API jobs. The local
shared module supplies strict settings, secret loading, HTTPS file upload, and
generic method/event helpers. The upstream package is loaded under an alias
because this project module intentionally shares its import filename.

The implementation targets TrueNAS v27, with the VM methods used by the
installer reviewed against v28. A local QEMU smoke test against TrueNAS
`27.0.0-RC.1` exercised API-key authentication, `system.info`, and
`core.get_methods`, subscribed to `core.get_jobs`, and observed `RUNNING` then
`SUCCESS` for an SSH service start job. The same release was then tested on
Netcup through the shared API client: `system.info`, pool, dataset, and VM
queries succeeded after SCRAM API-key authentication. All three storage/VM
queries returned empty lists. This did not exercise VM installation: the
Netcup host has no data pool and does not support nested virtualization. The
v28 VM API adds `vm.bootloader_aavmf_choices`,
`vm.get_guest_network_interfaces`, and `vm.reset`. It also changes several
schemas: runtime status can be `null`; VM responses require a non-null UUID (the
create request still permits `uuid: null` and can generate one); and VM/device
schemas add an iSCSI disk variant and a NIC PCI address. The installer sends an
explicit UUID and keeps unknown runtime state explicit. The VM workflow remains
schema-reviewed rather than live-accepted; v28's API docs currently label the
API Preview.

TrueNAS 24.10 needs a separate compatibility path. The official API client
supports a legacy websocket URI ending in `/websocket`, but these scripts
currently require `/api/current`. Compatibility work would need to:

1. Add explicit endpoint/protocol selection and route the old WebSocket framing
   and call/response shapes through the shared client.
2. Use the legacy API-key authentication method. SCRAM API-key login was added
   in 26, so a 24.10 connection needs an explicit PLAIN mechanism protected by
   verified TLS; the official client deliberately does not downgrade
   automatically.
3. Adapt event subscriptions and job handling. New-style jobs were added in
   25.10, so 24.10 progress and terminal states need their own adapter.
4. Check every method used by the installer against the 24.10 schema and a live
   host, including pool/dataset creation, file upload, VM devices, conversion,
   and lifecycle operations.

The API changed in stages from 24.10 to 27:

- 24.10 uses the legacy WebSocket API.
- 25.04 introduced JSON-RPC 2.0 over WebSocket while retaining the legacy
  protocol for compatibility.
- 25.10 added the new-style job support used for long-running operations.
- 26 added SCRAM-SHA-512 API-key authentication and the username parameter.
- 27 removed the server RPC `auth.login_with_api_key`. The replacement is
  `auth.login_ex` with an explicit mechanism such as `SCRAM` or
  `API_KEY_PLAIN`.
- VM product support also changed: 25.04 introduced Incus instances and classic
  KVM VM support returned in 25.04.2. This CLI manages `vm.*` KVM virtual
  machines, not Incus containers.

Sources: [TrueNAS API version index](https://api.truenas.com/), [v24.10 API
docs](https://api.truenas.com/v24.10/), [official API client compatibility
notes](https://github.com/truenas/api_client), [v27 JSON-RPC
docs](https://api.truenas.com/v27.0/jsonrpc.html), [v27 jobs
docs](https://api.truenas.com/v27.0/jobs.html), [v27
`auth.login_ex`](https://api.truenas.com/v27.0/api_methods_auth.login_ex.html),
[v27 deprecated login method](https://api.truenas.com/v27.0/api_methods_auth.login_with_api_key.html),
[v28 changelog](https://api.truenas.com/v28.0/changelog.html), and the
[25.04 virtualization update](https://forums.truenas.com/t/truenas-virtualization-plans-for-25-04-2/46236/).

## API schema source and authentication

TrueNAS publishes versioned HTML pages for JSON-RPC methods, events, jobs, and
schemas, plus a downloadable archive from each API documentation root. The
current WebSocket API does not have a published OpenAPI JSON/YAML document.
At runtime, `core.get_methods` returns the available method names, signatures,
documentation, and metadata. That helps inspect a live host, but the versioned
API docs remain the schema source for request and response fields.

SCRAM means **Salted Challenge Response Authentication Mechanism**. TrueNAS
uses SHA-512 SCRAM for API keys from v26. The client and server exchange
nonces; the server provides a salt and iteration count; the client proves it
knows the API key without sending the key itself; and the server returns a
proof that lets the client verify the server. This resists replay and provides
mutual validation. When channel binding is negotiated over TLS, the exchange is
also tied to the server certificate. TLS remains necessary to protect the
whole connection and its later API traffic.

`auth.login_ex` is a general mechanism-based entry point, so the server can
support SCRAM, explicit plaintext compatibility, passwords, tokens, and OTP
steps through one authentication flow. The official client does not silently
fall back from SCRAM to plaintext if a challenge fails; that avoids a downgrade
turning a secure login into one that sends the key itself.

On v27, `auth.login_with_api_key` as a server method is gone. `auth.login_ex`
uses the explicit `SCRAM` mechanism for challenge-response or
`API_KEY_PLAIN` for the legacy plaintext mechanism. The installed official
Python client still exposes `login_with_api_key` as a convenience wrapper; it
selects the supported server authentication exchange. The shared client uses
that upstream wrapper and does not implement SCRAM itself.

File uploads use a separate HTTP job endpoint: `filesystem.put` accepts a
multipart upload at `/_upload`. The uploader sends the API key as a Bearer
credential over HTTPS, so this path requires certificate verification even
though WebSocket login itself uses SCRAM. The tooling refuses uploads when
`api.verify_ssl` is false. No SSH/SFTP service is needed for this file
transfer.

Sources: [TrueNAS API docs root and download link](https://api.truenas.com/v27.0/),
[runtime `core.get_methods`](https://api.truenas.com/v27.0/api_methods_core.get_methods.html),
[SCRAM login schema](https://api.truenas.com/v27.0/api_methods_auth.login_ex.html),
[official API client authentication notes](https://github.com/truenas/api_client),
and [RFC 5802](https://www.rfc-editor.org/rfc/rfc5802).

## Image format and installation boundary

An ISO is optical installation media. A VM boots it as a CD-ROM and runs an
installer. A Debian cloud QCOW2 is already a virtual disk image; it is attached
as a disk and boots the installed guest. The Debian project publishes cloud
images in QCOW2 and raw formats.

`install-vm.py` takes a local disk image path and explicit TrueNAS storage
choices. It reads the live pool inventory, lists datasets with their type and
mountpoint, verifies that the selected pool is ONLINE, and refuses to reuse a
ZVOL or an image filename already present in the target dataset. The
`--dataset` argument is relative to `--pool`; missing path components are
created one at a time as filesystem datasets. After creation, the installer
reads the target's actual mountpoint from `pool.dataset.query` instead of
guessing `/mnt/<pool>/...`.

The local image is streamed to TrueNAS through the `filesystem.put` upload job.
The installer verifies the resulting remote file type and byte size. It then
creates the VM, uses `vm.device.create` to create and attach a VM-managed ZVOL
at the explicitly supplied `--disk-size-gib`, and runs `vm.device.convert` from
the uploaded file into `/dev/zvol/<pool>/<dataset>/<zvol>`. The output is a ZFS
volume used directly as the VM disk, not a raw file-backed disk.

The compressed QCOW2 file size does not tell us its virtual disk capacity.
`--disk-size-gib` is therefore an explicit input and must be large enough for
the virtual source disk. The conversion job reports failure if the target is
too small; in that case the created dataset, uploaded file, VM, or ZVOL may
remain for operator review. The installer does not resize guest partitions or
filesystems and does not delete partial artifacts automatically.

The TrueNAS upload endpoint accepts files rather than only ISO-specific
payloads, so an ISO can also be staged with `filesystem.put`. This installer
accepts virtual disk formats only and does not attach boot media. Booting an
uploaded ISO would require a separate workflow that creates a VM CD-ROM device
(`dtype: "CDROM"`, with the ISO's TrueNAS path) and defines how its installer
is automated. Uploading an ISO alone does not make installation unattended.

Accepted disk-image extensions are `.qcow2`, `.qed`, `.raw`, `.vdi`, `.vhdx`,
and `.vmdk`, matching the reviewed v27 conversion formats. The installer does
not create cloud-init metadata or a seed ISO. Debian cloud images need their
intended cloud-init configuration to provide first-boot credentials and
network settings; configure that separately before expecting remote guest
access.

Sources: [Debian cloud images](https://www.debian.org/distrib/), [v27 VM
conversion](https://api.truenas.com/v27.0/api_methods_vm.device.convert.html),
[v27 VM device creation](https://api.truenas.com/v27.0/api_methods_vm.device.create.html),
[v27 dataset query](https://api.truenas.com/v27.0/api_methods_pool.dataset.query.html),
[v27 file upload](https://api.truenas.com/v27.0/api_methods_filesystem.put.html),
and [v27 job uploads](https://api.truenas.com/v27.0/jobs.html).

## Shared client and frontends

`truenas_api_client.py` is the shared API boundary, parallel to
`netcup_scp_client.py`. It owns connection settings, credentials, generic
method calls, jobs, events, and streamed upload. It does not own command
parsing or VM policy.

`truenas-api.py` is the generic exploration and operations CLI. Its `call` verb
accepts positional parameters as a JSON array and requires the operator to
declare `--effect read|mutating`. Mutating calls require confirmation and can
be planned with `--dry-run`. Its `methods`, `watch-job`, and `watch-event`
verbs inspect server methods and report API event/job changes.

`install-vm.py` is the higher-level VM installation and lifecycle CLI. It owns
pool/dataset selection, missing dataset creation, upload, ZVOL sizing,
conversion, VM/device setup, explicit destructive choices, and VM-state
waiting. `pools` and `datasets [POOL]` enumerate the available storage.

Job methods report progress and terminal state. The upload endpoint returns a
job id; the shared client subscribes to `core.get_jobs` notifications and reads
fresh snapshots for state/progress. VM state watching likewise subscribes to
`vm.query` and reads a fresh VM snapshot on the caller thread. This keeps API
calls out of the WebSocket callback and preserves `null` status as unknown.

`cli-extended` owns grouped help, common output, version discovery,
confirmation, dry-run, and progress rendering. The two frontends load settings
and secrets only after a verb is dispatched, so `--help` and `--version` do
not require credentials or a live host.

The official client decodes some API date/time fields into Python `datetime`
objects. Before passing results to `cli-extended`, the CLI recursively converts
dates and datetimes to ISO-8601 strings while preserving JSON-native values.
Unsupported result types still fail visibly rather than being silently
stringified.

## Release-bound boot-pool patch and recovery

The boot-pool patch changes middleware files inside the selected TrueNAS
release. A version string does not identify every file shape in a nightly, so
each adapter binds a closed target list to captured source hashes and refuses
an unknown set. The v27 nightly and v28 nightly use separate adapters even
though they expose related storage operations.

An update replaces patched middleware files. On v1001, the first RC.1-to-v27
nightly update left the RC.1 install record stale and Path A unmounted. The old
exact-build installation was refused because it could not certify that stale
record as current. The supported reapply action now checks the exact prior RC.1
bundle digest, all 19 pristine nightly targets, and the bounded Path A root
before it changes `/usr`. It archives the stale record, applies the selected
adapter, verifies middleware, restores read-only protection, and mounts the
eligible Path A tree. This explicit transition prevents a record or preserved
dataset from claiming patch behavior that the active system does not have.

The external `live-boot` lane takes two explicit mutation guards because it
reboots the selected host. It captures boot-pool and optional Path B identities,
unmounts eligible Path A filesystems, reboots, and checks the mount sources,
pool health, GUIDs, PARTUUID, and partition table. It also exports Path B with
`destroy=false`, discovers it again, and imports the captured pool through
middleware. This keeps provider snapshot handling outside the test lane and
requires the operator to plan the reboot on the disposable target.

On 2026-10-30, the v27 nightly Path A/B API/UI matrix and the v27 and v28
reboot/import proofs passed on v1001. The v27-to-v28 update preserved boot-pool
and Path B identity; the exact v28 adapter was then applied before the v28
reboot proof. No newer v28 nightly was listed in the official feed. The
[patch design](../boot-pool-patch/docs/DESIGN-GUIDE.md#v27-nightly-update-and-reapply)
explains the adapter and recovery contract; the
[consumer guide](../boot-pool-patch/docs/CONSUMERS.md#reapply-v27-after-an-update)
has the guarded commands.
