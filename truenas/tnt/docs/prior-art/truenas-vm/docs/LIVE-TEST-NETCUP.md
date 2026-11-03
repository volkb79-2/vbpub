# Netcup host inspection for a TrueNAS live test

**Result on 2026-10-14: no TrueNAS installation was attempted, and the
existing server was left running with its existing ISO attached.** The server
cannot host the Debian VM that this work needs to exercise, and the available
Netcup workflow did not provide the interactive installer console.

## What was inspected

The Netcup SCP API identified `v1001.vxxu.de` as server `804027`, a running
AMD64 VPS with 4 CPUs, 8192 MiB RAM, one 500 GiB `vda` disk, UEFI firmware, and
`nestedGuest: false`. Its boot order was `HDD,CDROM,NETWORK`; an existing
`v1001-default-private.iso` was attached. The provider's available image
flavours and bootable ISO catalogue did not include TrueNAS. The snapshot list
was empty.

The read-only inspection used the existing Netcup client in this checkout:

```bash
cd scripts/netcup
./scp-api.py server-details 804027 --json
./scp-api.py disks 804027 --json
./scp-api.py boot-order 804027
./scp-api.py iso-bootable --filter truenas
./scp-api.py imageflavours --filter truenas
./scp-api.py iso-attached 804027
./scp-api.py snapshots 804027 --json
```

The provider API supports uploading an arbitrary ISO and attaching it. The
separate `install-host.py` path installs provider-supported image flavours;
it does not install an arbitrary TrueNAS ISO. Completing the TrueNAS installer
requires the provider's interactive console. That browser console was not
available in this agent session, so the upload and attachment operations were
not run.

## Why this VPS is not a useful target

`nestedGuest: false` means the VPS does not expose the nested hardware
virtualization TrueNAS needs to start a Debian guest VM. The client checks
TrueNAS's `vm.supports_virtualization` result and would refuse VM starts even
if TrueNAS itself were installed successfully.

The VPS has one disk and no separate data devices for a VM storage pool. Its
8 GiB RAM is at TrueNAS's documented minimum, leaving no practical headroom for
TrueNAS plus the Debian guest. TrueNAS's [hardware guide](https://cdn.truenas.com/docs/scale/gettingstarted/scalehardwareguide/)
describes its memory and boot-device recommendations. Reinstalling would erase
the currently running OS without producing a viable test host. No disk,
boot-order, ISO attachment, power, or filesystem state was changed.

## What would make a live run possible

Use a host or VPS that exposes VT-x/AMD-V to the TrueNAS guest and has enough
memory for both TrueNAS and the Debian VM. Provide a separate TrueNAS boot
device and usable VM storage pool; preferably provide the matching data-device
layout recommended by TrueNAS. Provide access to the provider's interactive
console so the installer can be completed. Then check `vm.supports_virtualization`
before configuring the Debian guest.

On a suitable disposable Netcup server, the API-only staging sequence would be:

```bash
cd scripts/netcup
SERVER_ID='replace-with-disposable-server-id'
./scp-api.py boot-order "$SERVER_ID"
./scp-api.py user-iso upload ./TrueNAS-installer.iso \
  --name truenas-installer.iso --yes
./scp-api.py attach-iso "$SERVER_ID" --user-iso-name truenas-installer.iso \
  --change-boot-device-to-cdrom --yes --json
# Copy the returned attach task UUID, then wait for its FINISHED result.
./monitor-task.py watch replace-with-attach-task-uuid
./scp-api.py power cycle "$SERVER_ID" --yes
```

The operator must complete installation and initial TrueNAS setup in the
provider console. After installation, shut TrueNAS down normally from its UI
or console. Inspect `serverLiveInfo.state` in the `server-details` output; it
must be exactly `SHUTOFF` before detaching. `SHUTDOWN` is a distinct state and
does not satisfy this check:

```bash
./scp-api.py server-details "$SERVER_ID" --json
```

If that field is not `SHUTOFF`, request a
provider power-off:

```bash
./scp-api.py power off "$SERVER_ID" --yes --json
```

If the power-off response contains a task UUID, wait for it to finish with
`./monitor-task.py watch TASK_UUID`. Then verify that
`serverLiveInfo.state` is exactly `SHUTOFF` before detaching. If you cannot
confirm that state, leave the ISO attached and stop. The
detach command reports `detached` but does not expose a task UUID. It checks
for active tasks and refuses to detach if one exists or the task query fails.
Run the state query after the power-off task completes:

```bash
./scp-api.py server-details "$SERVER_ID" --json
```

Read back the media assignment as JSON after detaching:

```bash
./scp-api.py iso-attached "$SERVER_ID" detach --yes
./scp-api.py iso-attached "$SERVER_ID" --json
```

Proceed with boot-order restoration when the JSON read-back is an empty object,
or when it has `isoAttached: false` and `iso` is missing, null, or empty. If
`isoAttached` is true, a non-empty response omits `isoAttached`, or `iso` is
non-empty, wait briefly and query again. If the state is still not clearly
detached, stop and resolve it while the server is off.

The attach command prints a restore command when it can derive one. Read the
current order and compare it with the saved initial order. The `boot-order`
read uses `serverLiveInfo.bootorder`; if Netcup omits that field while the
server is off, the read fails. In that case, restore only the recorded value
and leave the server off until the provider interface confirms the resulting
order:

```bash
./scp-api.py boot-order "$SERVER_ID"
```

Then restore the exact order recorded before attachment (or run the exact
restore command printed by `attach-iso`):

```bash
./scp-api.py boot-order "$SERVER_ID" set 'replace-with-recorded-boot-order' --yes
```

If the boot-order change returns a task UUID, wait for it to finish with
`./monitor-task.py watch TASK_UUID`. Query the boot order again and confirm it
exactly matches the order recorded before attaching the ISO. If the query
fails or the order differs, resolve that provider state and keep the server
off until the exact order is confirmed. Before doing this on any host, confirm
that the target is disposable, has an appropriate disk layout, and exposes
nested virtualization. The Debian VM setup and API client instructions are in
[`CONSUMERS.md`](CONSUMERS.md) and
[`scripts/debian-install-v2/docs/CONSUMERS.md`](../../../../../../scripts/debian-install-v2/docs/CONSUMERS.md#boot-a-debian-cloud-vm-with-the-private-seed-iso).
The existing Netcup installer and API workflow is documented in
[`scripts/netcup/README.md`](../../../../../../scripts/netcup/README.md).
