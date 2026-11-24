# M1 run 3: kernel upgrade in a guest (2026-11-18)

Result: **a kernel change is handled correctly by the btrfs + own-snapshot + ESP-GRUB
variant in this guest.** `apt dist-upgrade` replaced kernel 6.12.107 by 6.12.117 in
the running state; the menu then offered the new kernel for that state and the old
kernel for the snapshot taken before the upgrade; both booted with their own kernel
and modules; a one-shot boot into the earlier state did not stick.

Same snapper-free variant, same guest setup and limits as
[run 2](RESULTS-M1-RUN2-2026-11-18.md) (v1001, historical direct launcher now
represented by `vm-lab start --plain-host`; vm-lab was not used at the time,
TCG, 2 vCPUs, 2 GiB). Scenario `kernel-upgrade` of `poc/dr-serial.py` at the head
`2068bf9d2` (independently reviewed), run in a fresh guest (`drb3`, new overlay on
the disk installed in run 2a, which has no updates or security suites; the scenario
adds them first). Run time of the scenario: about 8 minutes (12:21:03Z to 12:29Z on
the host clock). Every reboot was proven by a changed `boot_id`; every value is a
tagged line the echoed command cannot satisfy (see the driver's docstring). The
transcript is the evidence (`artifacts/debian-rollback/m1-run3k/`, local).

## Checks, all PASS

| Check | Value seen |
|---|---|
| precondition: running state is the default | `@os-0001` / `@os-0001` |
| newer kernel package on offer after adding `trixie-updates` and `trixie-security` | `apt list --upgradable` shows `linux-image` (1 package) |
| `dist-upgrade` succeeded, download size | rc 0, 293,232 KB (about 286 MiB) in `/var/cache/apt/archives` |
| the new kernel is installed next to the old one | `/vmlinuz` -> 6.12.117+deb13-amd64; `/boot` has `vmlinuz-` and `initrd.img-` for 6.12.107 and 6.12.117 |
| the apt hook snapshotted before the upgrade | exactly one new state, `@os-pre-20261118122229-655-apt` |
| the menu was refreshed by the hooks | `@os-0001` -> kernel 6.12.117; the snapshot -> kernel 6.12.107 |
| the ESP boot files are untouched by the upgrade | `EFI/BOOT`, `EFI/dr` contents and a hash of `BOOTX64.EFI` equal before and after (whether `grub-efi` was among the upgraded packages, so whether its postinst ran at all, is not recorded) |
| default boot runs the new kernel in the running state | `uname -r` 6.12.117, rootflags `@os-0001` |
| `boot-once` the snapshot, reboot | `uname -r` 6.12.107, rootflags the snapshot, `/lib/modules` has only 6.12.107, `modprobe loop` works |
| the one-shot is consumed | next plain reboot: kernel 6.12.117 in `@os-0001` again |

## What this settles and what it does not

- Settled for this variant: finding 1 of the first review (menu not refreshed after
  a kernel change) is fixed in practice; each state keeps its own kernel and
  modules (design guide section 3); in this run the menu entry of each state named
  that state's own kernel. (That `gen-menu` skips a kernel without an initrd, and
  refuses an empty menu, is covered by reading and a dry run only, not by a guest run.)
- A kernel upgrade changes the running state only. Rolling back to the snapshot
  therefore also rolls back the kernel, as designed (D6, D18).
- **Not yet verified:** S5 (an initramfs that does not boot, recovery by choosing the
  earlier entry in the GRUB menu, which needs key input at the menu), S6 (removing
  the kernel an earlier state needs; the design keeps kernels inside each state, so
  this should be impossible, but it has not been tried), S7 (unattended-upgrades
  taking a snapshot), S8 (many states and pruning), S9 (full disk before an upgrade),
  S11 (backports kernel), S12 (phase 2), Secure Boot, other candidates (systemd-boot,
  ZFS), snapper itself. (S5 and the strengthened `s2-s3` re-run have since passed,
  see [run 4](RESULTS-M1-RUN4-S2S3-S5-2026-11-18.md).)
- The download of the upgrade (286 MiB) is for a minimal system; a real host may
  need more.
