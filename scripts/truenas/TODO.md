# Shared TrueNAS tooling follow-up work

The boot-pool patch and golden-image workflows keep their own open work under
their project directories. This file tracks shared API, VM runner, and installer
infrastructure.

## 2026-10-17 v28 VGA capture and serial console path

The resumed v28 installer guest reached UEFI, but the QMP screenshot and a
direct Unix-VNC capture were both reported as patterned/corrupt at 800x600
with a 32-bit RFB pixel format. The serial log stopped at GRUB; no install
input was sent. The operator stopped only that v28 guest cleanly and retained
its COW and OVMF state; the v27 guest remains untouched. There is not enough
capture evidence here to attribute the VGA symptom to QEMU, the guest, or the
RFB decoder.

The runner now has an opt-in `--serial-console` chardev backed by a bidirectional
Unix socket and an interactive `console serial` relay. It preserves a serial
log and adds no TCP listener. Unit coverage exercises QEMU argv construction,
socket aliasing, and terminal input/output. The registered unit lane passed
with 139 tests and 45 subtests. A serial-enabled v28 installer guest has since
been resumed, but its live serial console path still needs inspection. A serial
socket does not force UEFI or GRUB to mirror the menu to COM1; the boot entry
must route output there.

## 2026-10-17 installer-console resume and duplicate QEMU options

The operator resumed their stopped v28 installer state with the new VNC mode.
Admission passed for the second guest, but its runner exited and was cleaned
up; the COW overlay and OVMF variables remain in
`vanilla-iso-patcher/artifacts/qemu-test/v28-install`. Inspection found that
`host_command_script()` expanded its optional-argument list twice, duplicating
the installer CD-ROM and, in VNC mode, the same Unix VNC listener. The saved
serial log ends at the earlier clean poweroff, so it contains no new guest-side
failure detail. The runner now emits the optional list once, and its shell
argv test asserts one CD-ROM and one VNC listener. The registered unit lane
later passed with 139 tests and 45 subtests. A serial-enabled resume has since
started, but no successful installer console interaction is recorded here;
leave the running v27 guest untouched.

## 2026-10-16 isolated TCG runner status

The reusable [`tcg_vm.py`](tcg_vm.py) runner starts a disposable guest in its
own Docker container under `$CGROUP_PARENT_DEV_BACKGROUND` (resolved to
`dev-background.slice`). It requires a loaded finite background slice, checks
ancestor memory headroom, resolves bind mounts from Docker's live mount table,
and verifies the placed container and limits. The guest container is capped at
200% CPU and 3.5 GiB memory with swap disabled; the guest receives 3 GiB RAM.
Its COW disk and explicit OVMF state live in a private `.vm/` state directory;
the base image is mounted read-only. The registered unit lane most recently
passed with 139 tests and 45 subtests.

Patch VM, release adapter, and acceptance history now lives in
[`boot-pool-patch/TODO.md`](boot-pool-patch/TODO.md), with its test logs under
`boot-pool-patch/artifacts/`. Keep this file focused on the shared runner and
its resource boundary.


## Shared API compatibility

- Implement and test a separate legacy WebSocket, authentication, and job
  adapter for TrueNAS 24.10. The current shared client targets the v27+ API.
