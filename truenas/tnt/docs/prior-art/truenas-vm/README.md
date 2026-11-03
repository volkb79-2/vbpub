# TrueNAS VM lifecycle client

`truenas-vm.py` manages TrueNAS 27+ virtual machines and their devices over
TrueNAS's versioned JSON-RPC WebSocket API. It uses the upstream TrueNAS Python
client, API-key authentication, and the shared `cli-extended` command
framework. The client covers VM discovery and create/update/delete, power
state, device create/update/delete, and importing a dataset disk image into a
new ZFS-backed VM disk. Disk import requires a new ZFS name, an explicit
capacity, and a stopped VM with autostart disabled until conversion completes;
see the
[design rationale](docs/DESIGN-GUIDE.md#stop-the-vm-during-disk-import).

Install and authentication instructions are in
[`docs/CONSUMERS.md`](docs/CONSUMERS.md). The design choices and API-client pin
are in [`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md). The live-test host
inspection and its blockers are recorded in
[`docs/LIVE-TEST-NETCUP.md`](docs/LIVE-TEST-NETCUP.md).

The Debian installer consumer guide shows how to boot its private NoCloud seed
ISO with a Debian Trixie cloud image on TrueNAS, Proxmox/ZFS, or Hyper-V:
[`scripts/debian-install-v2/docs/CONSUMERS.md`](../../../../../scripts/debian-install-v2/docs/CONSUMERS.md#boot-a-debian-cloud-vm-with-the-private-seed-iso).

> **Status: prior art, reference only (2026-11-04).** This directory preserves
> the standalone client written on 2026-10-14 before `tnt` existed. `tnt`
> (`../../../README.md`) now owns TrueNAS VM lifecycle; the parts `tnt` lacks
> (VM device list/update/delete and the disk-image import into a ZFS volume via
> `vm.device.virtual_size` + `vm.device.convert`) are tracked as TNT-60 and
> TNT-61 in `../../../BACKLOG.md`, to be re-implemented there with plan/apply.
> The code is not part of any gate or release; do not extend it, and delete this
> directory when TNT-60 and TNT-61 are done. Its error messages still name the
> old `scripts/truenas` location.
