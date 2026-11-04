# Using the TrueNAS VM lifecycle client

This guide covers the API client in
[`../README.md`](../README.md); command design is explained in
[`DESIGN-GUIDE.md`](DESIGN-GUIDE.md).

## Install

From a vbpub checkout, install the project-local `cli-extended` source and the
TrueNAS API client pinned by commit into a virtual environment:

```bash
cd truenas/tnt/docs/prior-art/truenas-vm
python3 -m venv "$HOME/.venvs/truenas-vm"
"$HOME/.venvs/truenas-vm/bin/python" -m pip install -r requirements-runtime.txt
"$HOME/.venvs/truenas-vm/bin/python" ./truenas-vm.py --help
```

The API client pin is `2a04491953d38c13f13f40729d2a4966a77c6297` from the
upstream `TS-27.0.0-RC.1` tag. Review the pin before changing it; the upstream
client must support TrueNAS 27 JSON-RPC WebSocket and SCRAM API-key
authentication.

## Configure a TLS WebSocket connection

Create a TrueNAS API key for an account with the `READONLY_ADMIN`, `VM_READ`,
`VM_WRITE`, `VM_DEVICE_READ`, and `VM_DEVICE_WRITE` roles. `READONLY_ADMIN`
allows the client to read `system.info` for the displayed system details and
query the ZFS dataset name before import. The version gate uses the separate
`system.version_short` API method. The VM roles cover VM and device operations.
Keep the key outside the checkout in a file readable only by your user. The
upstream library accepts either the raw API key line or its documented JSON
key-file format.

```bash
install -d -m 700 "$HOME/.config/truenas"
install -m 600 /dev/null "$HOME/.config/truenas/api-key"
${EDITOR:-vi} "$HOME/.config/truenas/api-key"
chmod 600 "$HOME/.config/truenas/api-key"

export TRUENAS_URL='wss://truenas.example.net/api/v27.0'
export TRUENAS_USERNAME='vm-operator'
export TRUENAS_API_KEY_FILE="$HOME/.config/truenas/api-key"
```

Use a certificate trusted by the client machine. The client requires
`wss://.../api/v27.0` and certificate verification; it has no insecure TLS
mode. This pins requests to the v27 API schema on later TrueNAS releases that
still expose that endpoint. Connection options can also be passed to each
command with `--url`, `--username`, and `--api-key-file`.

Start with the read-only preflight:

```bash
./truenas-vm.py system-info
./truenas-vm.py vm-list
```

`system-info` reports the TrueNAS version and
`vm.supports_virtualization`. VM start refuses if TrueNAS says hardware
virtualization is unavailable. The client refuses TrueNAS releases older than
27.0.

## Create a Debian VM

Use the full TrueNAS API v27 JSON object for VM creation. This small example
creates a UEFI VM with 2 vCPUs, 4 GiB RAM, and autostart disabled:

```json
{
  "name": "debian-vbpub",
  "vcpus": 2,
  "memory": 4096,
  "bootloader": "UEFI",
  "autostart": false
}
```

Save it as `vm.json`, review it, then create the VM:

```bash
./truenas-vm.py vm-create --file vm.json
```

Every mutation prompts with a default-no confirmation. Add `--yes` only after
reviewing the exact operation. The command outputs the created VM record and
ID. TrueNAS API schemas remain authoritative for additional settings such as
CPU model, display, and memory ballooning.

Before importing an image or seed ISO, copy it into a TrueNAS dataset using an
existing secured SMB/NFS share or SSH/SCP. Paths passed to the API are paths on
the TrueNAS server, commonly `/mnt/<pool>/<dataset>/<file>`.

Import a Debian QCOW2 image into a new ZFS volume:

```bash
./truenas-vm.py disk-image-size \
  /mnt/tank/staging/debian-13-genericcloud-amd64.qcow2
./truenas-vm.py disk-image-import 4 \
  --source /mnt/tank/staging/debian-13-genericcloud-amd64.qcow2 \
  --zvol-name tank/vm/debian-root --zvol-size-gib 40 \
  --order 1
```

Replace VM ID `4`, pool/dataset path `tank`, and the staging path with values
from your host. Use one of these lowercase image suffixes: `.qcow2`, `.qed`,
`.raw`, `.vdi`, `.vhdx`, or `.vmdk`. The `--disk-type` vocabulary is `AHCI` or
`VIRTIO` (default); lower `--order` values boot first. `--zvol-size-gib` is
required and sets the final virtual disk capacity; it must be at least the
image's virtual size. Choose a value appropriate for the target install rather
than reusing the QCOW2's compact file size. Before creating anything, the CLI
requires the VM to report `STOPPED` with autostart disabled and checks again
immediately before device creation. It also checks the exact ZFS dataset name
with `pool.dataset.query` and refuses an existing name; it repeats that check
after confirmation. Keep the VM stopped and autostart disabled until the
conversion job is confirmed complete. The device stays at TrueNAS's automatic
boot order while conversion runs; this is not a boot safeguard. The requested
order is applied only after conversion succeeds.

If conversion is interrupted or its result is uncertain, open the TrueNAS
Jobs/Task Manager view and find the `vm.device.convert` entry whose source is
the path passed with `--source` and whose destination is
`/dev/zvol/<zvol-name>`. Do not start the VM until that job reaches a terminal
state. If a complete, unfiltered job history confirms that no matching job was
created, treat conversion as failed. If history is missing or you cannot tell
whether the job ran, leave the VM stopped and the storage untouched until an
administrator can verify it.

If the job succeeded but the client did not set the requested boot order, save
the intended order in `disk-order.json` and apply it after checking the device:

```bash
cat > disk-order.json <<'JSON'
{"order": 1}
JSON
DEVICE_ID='replace-with-the-device-id-from-TrueNAS'
./truenas-vm.py device-update "$DEVICE_ID" --file disk-order.json
```

If the job failed or was aborted, inspect the new device and ZFS volume first.
After confirming there is no active conversion and the partial volume is
disposable, delete that device and its newly created volume, then repeat the
import. The same cleanup applies when no conversion job was created:

```bash
DEVICE_ID='replace-with-the-device-id-from-TrueNAS'
./truenas-vm.py device-delete "$DEVICE_ID" --delete-zvol
```

The command asks for confirmation before deleting the device and volume. If the
creation request was interrupted before it returned a device ID, disk
conversion was not started. List devices on every VM and check for the exact
`attributes.zvol_name` or `/dev/zvol/<zvol-name>` before deciding what to
remove:

```bash
./truenas-vm.py device-list
```

The importer checked the name just before creation, but another actor could
have claimed it in the small interval after that check.

If a device exists and you have confirmed it is the new disposable device from
this import, delete it and its volume with the command above. If a ZFS volume
exists without a device record, this client cannot remove it. Delete it from
**Storage > Datasets** only when you can confirm it belongs to this failed
import. If its provenance is unclear, leave it untouched and retry with a new
unique `--zvol-name`; never delete a volume based only on its name.

Create a NoCloud CD-ROM device for the private installer seed ISO. Device
creation takes the TrueNAS v27 `vm.device.create` JSON shape. Add a NIC on an
existing bridge so the guest can reach APT and the selected Git remote; use a
bridge name from the TrueNAS UI or
[`vm.device.nic_attach_choices`](https://api.truenas.com/v27.0/api_methods_vm.device.nic_attach_choices.html).

```json
{
  "vm": 4,
  "order": 2,
  "attributes": {
    "dtype": "CDROM",
    "path": "/mnt/tank/staging/debian-vbpub.iso"
  }
}
```

Save that file as `seed-device.json`. For a bridged VirtIO NIC, replace
`bridge-name-from-truenas` with a returned bridge name and save as
`nic-device.json`:

```json
{
  "vm": 4,
  "order": 3,
  "attributes": {
    "dtype": "NIC",
    "type": "VIRTIO",
    "nic_attach": "bridge-name-from-truenas"
  }
}
```

```bash
./truenas-vm.py device-create 4 --file seed-device.json
./truenas-vm.py device-create 4 --file nic-device.json
./truenas-vm.py vm-show 4
./truenas-vm.py device-list --vm-id 4
./truenas-vm.py vm-start 4
```

Use the TrueNAS UI to review the VM console, network bridge, disk, and device
order. The Debian disk must be first in boot order; the seed ISO is a cloud-init
data source, not the operating-system installer. After Debian first boot and
successful `debian-install-v2` stage two, shut down the VM and remove the seed
CD-ROM device so its private credentials are not offered again.

## Lifecycle and storage operations

The client exposes these VM operations: `vm-list`, `vm-show`, `vm-create`,
`vm-update`, `vm-delete`, `vm-start`, `vm-stop`, `vm-poweroff`, `vm-restart`,
`vm-suspend`, and `vm-resume`. Device operations are `device-list`,
`device-create`, `device-update`, and `device-delete`. Every create/update
JSON file must be a JSON object accepted by the corresponding TrueNAS API
method.

`vm-stop` requests a guest shutdown by default. `--force` powers it off
immediately; `--force-after-timeout` requests force-off only if graceful
shutdown times out. `vm-start --overcommit` allows a start when TrueNAS reports
insufficient free memory. These options require explicit review.

VM deletion preserves associated ZFS volumes by default. Use `vm-delete ID
--delete-zvols` only when those volumes should also be removed. Device deletion
also preserves its backing store unless `--delete-zvol` or
`--delete-raw-file` is supplied. Both deletion commands require confirmation.
If a `device-update` JSON object changes `vm`, its confirmation names the
current and target VM because TrueNAS moves the device.

The role requirements and method schemas are listed in the
[TrueNAS v27 API docs](https://api.truenas.com/v27.0/). The client reads
[`system.version_short`](https://api.truenas.com/v27.0/api_methods_system.version_short.html)
for its version gate and [`system.info`](https://api.truenas.com/v27.0/api_methods_system.info.html)
for displayed system details. It reads
[`vm.get_instance`](https://api.truenas.com/v27.0/api_methods_vm.get_instance.html)
to check VM state and autostart, and [`pool.dataset.query`](https://api.truenas.com/v27.0/api_methods_pool.dataset.query.html)
to check the requested ZFS name before disk import. It calls [`vm.create`](https://api.truenas.com/v27.0/api_methods_vm.create.html),
[`vm.device.create`](https://api.truenas.com/v27.0/api_methods_vm.device.create.html),
[`vm.device.virtual_size`](https://api.truenas.com/v27.0/api_methods_vm.device.virtual_size.html),
and the asynchronous [`vm.device.convert`](https://api.truenas.com/v27.0/api_methods_vm.device.convert.html)
job. The API's JSON schemas, rather than a client-side copy, define the fields
accepted by VM and device create/update requests.

For the inspection of `v1001.vxxu.de` and why it was judged unsuitable for a
live TrueNAS test, see [`LIVE-TEST-NETCUP.md`](LIVE-TEST-NETCUP.md).
