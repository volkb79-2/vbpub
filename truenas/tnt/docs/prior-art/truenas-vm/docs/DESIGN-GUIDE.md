# TrueNAS VM client design

The command and adoption examples are in
[`CONSUMERS.md`](CONSUMERS.md); the product surface is in
[`../README.md`](../README.md).

## Use the upstream WebSocket client

TrueNAS exposes versioned JSON-RPC 2.0 WebSocket endpoints. `truenas-vm.py`
uses the v27 endpoint, `/api/v27.0`, rather than `/api/current`, so later
TrueNAS releases continue using the v27 request schemas while that endpoint is
available. It uses iXsystems' `truenas_api_client` package instead of
implementing WebSocket framing, SCRAM-SHA-512 exchange, job notifications, and
JSON-RPC error mapping itself. The package is pinned to the upstream
`TS-27.0.0-RC.1` commit recorded in `requirements-runtime.txt`; update that pin as a
reviewed dependency change when a suitable stable 27.x client is available.

The client requires a `wss://` endpoint and keeps certificate verification
enabled. API keys are read from a protected file by the upstream library and
are never accepted in process arguments. The CLI checks the numeric release
from `system.version_short`; `system.info.version` is a full product string and
is retained for display. It refuses a server version below 27 or one whose
short version cannot be read, instead of guessing API compatibility.

The command surface is registered with `cli-extended`, so help, JSON output,
safe default-no confirmations, and `--yes` behavior follow the same contract
as the other vbpub command-line tools.

## Keep TrueNAS storage paths in TrueNAS's namespace

The computer running this client cannot stat a path on the TrueNAS host.
`disk-image-size` and `disk-image-import` therefore pass an absolute
`/mnt/<pool>/<dataset>/...` path to TrueNAS and let its API validate and read
the file. The importer asks TrueNAS for the image's virtual size and requires
the operator to provide the final ZFS volume capacity; it refuses a capacity
smaller than the image's virtual size. It also checks that the exact ZFS name
is unused before confirmation and immediately before creation. TrueNAS's
device-create operation remains authoritative if another actor claims that
name between the check and create.

The importer creates a new ZFS volume through `vm.device.create` at TrueNAS's
automatic boot order, then calls the upstream-supported `vm.device.convert`
job to fill it. Only after conversion succeeds does it set the requested boot
order.

## Stop the VM during disk import

TrueNAS's automatic device order is not a safety mechanism: an incomplete disk
may be the VM's only boot disk. Before creating the device, the importer reads
`vm.get_instance` and requires `status.state` to be `STOPPED` and `autostart`
to be false. It checks again immediately before device creation. The operator
must keep the VM stopped and autostart disabled until the conversion job is
confirmed complete; this avoids a partial disk being used after a host reboot
or an operator start.

If conversion fails or is interrupted, TrueNAS may still be running the job.
The client leaves the device and ZFS volume for inspection and tells the
operator not to start the VM until the job is terminal. A successful conversion
with an uncertain boot-order update can be repaired with `device-update`; a
failed or aborted conversion can be retried after the job is terminal and the
new device and disposable volume are explicitly deleted. If a volume exists
without a device record and its provenance cannot be established, the operator
leaves it untouched and retries with a new name. The importer does not
automatically remove storage that may contain data. Device and VM deletion
preserve backing ZFS volumes by default; separate flags request those
destructive removals.

File transfer remains an operator step. The ISO and cloud image must first be
placed in a dataset using an existing secured SMB/NFS share or SSH/SCP access.
This keeps API credentials and file-transfer credentials separate and avoids
inventing a TrueNAS upload protocol in this client.

## The live test host decision

The Netcup VPS at `v1001.vxxu.de` was inspected but not reimaged. Netcup
reports no nested guest virtualization, and the VPS exposes one disk. A
TrueNAS guest on that VPS could not itself host the Debian VM under test, and
the disk layout provides no separate VM storage pool. The provider's arbitrary
ISO upload and attach API also does not supply the interactive console needed
to finish the TrueNAS installer. The complete inspection and the conditions
for a future live run are recorded in
[`LIVE-TEST-NETCUP.md`](LIVE-TEST-NETCUP.md).
