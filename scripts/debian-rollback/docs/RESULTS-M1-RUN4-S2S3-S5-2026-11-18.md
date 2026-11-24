# M1 run 4: strengthened s2-s3 re-run and S5, broken initramfs (2026-11-18)

Result: **both pass in fresh guests.** The strengthened driver re-confirms S2, S3, S4 and
S10 of [run 2](RESULTS-M1-RUN2-2026-11-18.md) with a boot-id proof for every reboot, and S5
shows that a state whose initramfs is broken does not boot, that the earlier state can be
chosen in the GRUB menu with keys over the serial line, and that making it the default
gets the system booting again (the broken state is left as it is, with its menu entry).

Same variant (btrfs, own snapshots, ESP GRUB menu; snapper not used), same guest setup
and limits as run 2 (v1001, historical direct launcher now represented by
`vm-lab start --plain-host`; vm-lab was not used at the time, TCG, 2 vCPUs, 2 GiB),
a fresh guest per scenario on the disk installed in run 2a, driver `poc/dr-serial.py` at
`d694a0ba2` (the s2-s3 part is the independently reviewed `2068bf9d2`; the `s5` scenario
and the QMP helper were added after that review). Transcripts: `artifacts/debian-rollback/
m1-run4s/` and `m1-run5s/` (local). On the host a vbpub-cb campaign started beside the
S5 guest (anon of the slice stayed far below the stop line).

## s2-s3 (13:06:09Z, about 4.5 min), 15 checks PASS

Every value is a tagged line the echoed command cannot satisfy; every reboot required a
changed `boot_id` and a fresh `login:` prompt.

| Step | Value seen |
|---|---|
| precondition: running state is the default | `@os-0001` / `@os-0001` |
| markers written (`/home`, `/srv`, `/var/lib/docker`, `/etc`) | `m1_m2_m3_etc1` |
| `apt install hello` | rc 0, `/usr/bin/hello`; the apt hook created exactly one snapshot `@os-pre-20261118130719-641-apt`; the menu has one entry per state (2) |
| `boot-once` the snapshot, reboot | booted `@os-pre-…`; `hello` absent; `/home` marker `m1+m1b+` (the line written after the snapshot survives); `/srv` `m2`, `/var/lib/docker` `m3` survive; `/etc/dr-etc-marker` `etc1+` (the later `etc2` line is gone) |
| the next plain reboot | back in `@os-0001`: the one-shot is consumed |
| `set-default` the snapshot, reboot | booted `@os-pre-…` with no key press |

## S5, broken initramfs (13:11:09Z, about 3.5 min), 11 checks PASS

1. A manual snapshot (`dr-state snapshot --reason s5`) made `@os-pre-20261118131158-605-s5`.
2. The running state's `/boot/initrd.img-6.12.107+deb13-amd64` was overwritten with 7 bytes.
3. After `reboot` the default entry did not boot: the serial log shows `Initramfs
   unpacking failed: invalid magic at start of compressed archive` and `Kernel panic -
   not syncing: VFS: Unable to mount root fs`; no login prompt.
4. The guest was reset from outside (QMP `system_reset`), the GRUB menu appeared, and
   the driver sent Down and Enter over the serial line: the second entry (the snapshot)
   booted. `rootflags` named the snapshot.
5. The saved default still named the broken `@os-0001` (a menu choice is not a repair).
6. `dr-state set-default` on the good state, then a plain reboot with no key press:
   the good state booted.

## What this settles and what it does not

- Settled in the guest, for this variant: the earlier state is a working way out of a
  broken new state, by a manual menu choice (decision D5, phase 1).
- The panic is assumed not to reboot the guest by itself (kernel default `panic=0`); the
  driver reset the guest through QMP right after the panic and did not wait it out. A
  real host needs a watchdog or the phase 2 fallback either way (automatic fallback
  after a failed boot is not built).
- The run 5 driver did not assert `Initramfs unpacking failed` or the absence of a login
  prompt (it checked only the panic text); that evidence is in the transcript. The
  scenario now asserts both, and computes the number of key presses from the menu order
  instead of assuming one `Down`; these changes have not run in a guest yet.
- **Not yet verified:** S6 (removing the kernel an earlier state needs), S7 (unattended
  upgrades), S8 (30 states and pruning), S9 (full disk before an upgrade), S11
  (backports kernel), S12 (phase 2 probation), Secure Boot, snapper on the flat layout,
  btrfs with systemd-boot, ZFS (ZFSBootMenu, GRUB), encryption, real hardware.
- The s5 scenario, `qmp_command` and the key sequence were reviewed by nobody before
  they ran; they are in the next review round.
