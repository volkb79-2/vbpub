# ZFSBootMenu PoC guest results — 2026-11-23

Host names replaced by 'the test host' and host paths made relative to the job dir (2026-11-24); VM and run-dir names are verbatim evidence identifiers.

This is the evidence record for the current ZFSBootMenu installer iterations.
It is not a release claim: the target install and boot path is still under
guest validation.

## Inputs and artifacts

| Item | Identity |
|---|---|
| Debian Live base | `debian-live-13.7.0-amd64-standard.iso`, SHA-256 `040a44f35186321eb6cdaab52a8a2d06224fe6b77f6fbb9f1861ad89c14e17ec` |
| ZFSBootMenu EFI | `zfsbootmenu-recovery-x86_64-v3.1.0-linux6.12.EFI`, SHA-256 `9910458dfe7fc2895214922e22b6ae2513cd0f5a43d4d3c5526eee26e69a2198`, signer fingerprint `971D2693A47CA0E203E1C5DA312485BE75E3D7AC` |
| Previous candidate ISO | `debian-rollback-zbm-v3.1.0-kernel-order-20261123.iso`, SHA-256 `c71b839afd716cd8995631915c48cce790a0aff2ce47c009a18cb4cca3212f13` |
| Current candidate ISO | `debian-rollback-zbm-v3.1.0-altroot-check-20261123.iso`, 2,103,967,744 bytes, SHA-256 `8b71bb21ad030fcc6e7ba906f6275d063165ab2220fb5e7705c54db2c52ff941` |

The current candidate was built by registered lane `build-zfs-iso`, and its
checksum was verified on the build host and the test host before boot. The registered
`offline` lane passed with the mountpoint regression test. Run-Gate logs are
retained outside the repository at
`/tmp/run-gate/lanes/build-zfs-iso/6ae0e61a3822dfa835bbcf6ddab37367.log` and
`/tmp/run-gate/lanes/offline/d84c0755a71cf2457b10c6631ebb75ee.log`. The
registered vm-lab `r0-r1` lane also passed (466 tests, 75.79% coverage); its
latest passing log is
`/tmp/run-gate/lanes/r0-r1/52949dab55ba8c5c44f2b3e794c7efd3.log`. A follow-up
run caught a transient `NameError` after a cleanup removed the PID still
returned in VM status; the assignment was restored and this final run passed.

## Installer attempts

1. The first target install stopped before configuring DKMS because
   debootstrap had not created `/etc/dkms`. The installer now creates this
   directory before writing its ZFS DKMS configuration.
2. The next install found that the debootstrap kernel was older than the
   refreshed target kernel and had no matching ZFS module. Source now omits
   kernel and headers from debootstrap and installs the target kernel,
   matching headers, and ZFS packages together after refreshing target APT.
3. The kernel-order candidate reached target kernel `6.12.118+deb13-amd64`,
   generated its initramfs, installed the ZFSBootMenu EFI copies, and then
   stopped on `initial BE mountpoint is not /`. This was a false failure caused
   by validating the effective mountpoint while the pool was imported with the
   installer's temporary altroot.

The failed third install created `rpool/ROOT/os-000001-20261123-215214Z-install`
and then exported `rpool` during cleanup. In the preserved Live guest, the pool
was imported read-only once with `-R /mnt/drinspect` and once without an
altroot. With the altroot, `zpool get` reported `/mnt/drinspect` and
`zfs get mountpoint` reported `/mnt/drinspect` (source `local`); without it,
`zpool get altroot` reported `-` and the same dataset reported `/` (source
`local`). `canmount` remained `noauto` in both observations. This matches the
OpenZFS [`zpoolprops(7)` description of `altroot`](https://openzfs.github.io/openzfs-docs/man/master/7/zpoolprops.7.html):
it prefixes pool mountpoints and is not persistent.

The installer now checks that the pool altroot equals the target mount path and
that the initial BE has a local mountpoint whose effective value is exactly
that altroot. Its regression test accepts that root case and refuses a
non-root mountpoint, a different altroot, or an inherited mountpoint.

The serial and runner evidence for attempt 3 is retained on the build host at
`scripts/debian-rollback/artifacts/zfs-rebuild-20261123/` (gitignored) and on the
test host under run directory
`vm-lab/zbm-20261123/runs/kernel-order-install/` (relative to the job dir).
The local serial log SHA-256 is
`6ac5c0283ddb67af98d4db2488e3abd40a8c6de42711070c0419359644a6a4ad`.

## Current retest

The altroot-check ISO is running on a new blank UEFI guest on the test host:

- VM: `debrollback-zbm-altrootcheck-20261123`
- Run directory (relative to the job dir): `vm-lab/zbm-20261123/runs/altroot-check-install/`
- Blank qcow2 base, 3072 MiB guest RAM, four TCG vCPUs; the ISO is read-only.
- At 23:56Z DKMS had advanced from kernel-interface checks into compiling ZFS
  module objects for `6.12.118+deb13-amd64`. The VM remained running and Docker
  reported no OOM kill; no target-install completion marker had been recorded.
  A pressure profile was unavailable because no `cgpctl` client was installed
  on the cockpit or the test host. The serial log is retained locally at
  `scripts/debian-rollback/artifacts/zfs-rebuild-20261123/altroot-check-install-progress.serial.log`
  (SHA-256 `36ac7f66a2ff4ecf0c22acd24e7c145cc489504798662cc848039e30e107b83e`).

The next proof points are completion of the target install, boot from the
installed ESP without the Live ISO, and the destructive dependency-break
acceptance. Until those pass, the ZFS install and rollback paths remain
unproven.
