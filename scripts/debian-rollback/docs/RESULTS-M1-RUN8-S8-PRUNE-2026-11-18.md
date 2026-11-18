# M1 run 8: S8, many states and pruning (2026-11-18)

Result: **S8 passes: 30 snapshots, a 31-entry menu that boots, `dr-state prune` keeps the newest N and
never deletes a one-shot target, and the protected target still boots.**

Same variant and guest setup as [run 7](RESULTS-M1-RUN7-FRESH-INSTALL-S7-2026-11-18.md) (v1001,
`poc/dr-qemu.sh`, TCG, 2 vCPUs, 2 GiB, a fresh guest on `installed-btrfs-3.qcow2`, 14:45:33Z to 14:49:04Z, 3.5 min).
The `dr-state` on the disk had no `prune`; the scenario first installed the one from the seed medium (commit
`1be8fae64`, checked by the presence of `cmd_prune`). The `prune` command and the scenario were written
and run in the same session. Review round 7 (FIX-FIRST on a wrong check count, a swallowed delete failure) is
fixed afterwards in `dr-state`: `prune` now exits 1 when a delete fails (menu still rewritten) and refuses under
`DR_STATE=none`; those two changes, and the later `|| true` fail-open fix of the dedupe window, have **not run in a
guest** (the offline selection test covers the choice of snapshots only). Transcript: `artifacts/debian-rollback/m1-run8/`.

## What ran (11 checks PASS, 0 FAIL)

- 30 x `dr-state snapshot --reason s8`, no failures; the menu listed all 31 states.
- A reboot with the 31-entry menu booted the default state.
- `dr-state prune --keep 5` deleted 25 snapshots and left the running state plus exactly the newest five; the
  menu followed (6 entries).
- `boot-once` on the oldest of the five, then `prune --keep 2`: it deleted the two unprotected ones and kept the
  one-shot target next to the newest two; the next reboot booted the protected target.
- Disk use of `/`: 714,000 KB before, 723,040 KB with 30 snapshots, 723,104 KB after pruning (btrfs shares the
  extents; snapshots of an unchanged state cost almost nothing, and `df` does not reflect deleted subvolume
  space until the cleaner has run).

## What prune does

`dr-state prune [--keep N] [--dry-run]` (default 10) deletes the oldest `@os-pre-*` snapshots beyond the newest N,
by name (the name carries the creation time). It never deletes the running state, the menu default (`saved_entry`)
or the pending one-shot (`next_entry`), and ignores `@os-0001`. The parent of the running state may be pruned
(btrfs snapshots are independent copies). The selection is a pure function with an offline
test (`poc/test-dr-state-prune.sh`, 13 cases).

## Not verified

Nothing calls `prune` automatically yet (no timer, no apt hook); what the age or count policy should be is a
design decision, not made here. Real-host disk accounting after the btrfs
cleaner has run was not measured.
