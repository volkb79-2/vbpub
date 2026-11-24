# M1 run 9: dedupe window negative case and the hook's failure path (2026-11-18)

Result: **both pass: an apt run in a rolled-back state still takes its own snapshot (parent = that state), and when
the snapshot step fails the Pre-Invoke hook stops apt before dpkg changes anything.** S9b is a *simulated* failure.

Same variant and guest setup as [run 8](RESULTS-M1-RUN8-S8-PRUNE-2026-11-18.md) (v1001,
historical direct launcher now represented by `vm-lab start --plain-host`; vm-lab was not
used at the time, TCG, fresh guest per scenario on `installed-btrfs-3.qcow2`,
14:55:02Z to 14:59:01Z). Each scenario first installed
the `dr-state` of commit `22bbc0e1f` from the seed medium (HEAD at that time: the `|| true` fail-open fix, prune
exit code and `DR_STATE=none` refusal are in it, so those three changes have now been in a guest, though only
the first is exercised by these scenarios). The two scenarios are new and have had no independent review yet.
Transcripts: `artifacts/debian-rollback/m1-run9/` (local).

## window-negative (2.6 min, 9 checks PASS)

`apt install hello` in the running state took one snapshot A. After `boot-once A` and a reboot, the guest ran A.
`dr-state _window-blocks A 900 now` exited 0: A's own name was inside the 900 s window, so a window that ignored
the lineage would have suppressed the next snapshot. An `apt install tree` in A then succeeded and took
**exactly one** new snapshot, and its btrfs Parent UUID equals A's UUID (a copy of A, not of the original state).

## s9b, the hook's own failure path (1.4 min, 8 checks PASS), simulated

Why simulated: in S9 a really full filesystem made apt fail by itself before the hook ran. Getting a state where
apt works but `btrfs subvolume snapshot` fails for lack of space is fragile, so a wrapper in `/usr/local/sbin/btrfs`
made only `subvolume snapshot` fail with `No space left on device` (all other calls pass through). Then:
`apt install hello` exited **100** (the Pre-Invoke hook's exit), `hello` was not installed, `dpkg --audit` was empty,
the failure text was in apt's log, and no snapshot existed. With the wrapper removed, the same install worked and
took its snapshot. This verifies the hook policy (a failed snapshot stops apt, which is deliberate: no change
without a rollback point); it does not verify what a real ENOSPC leaves behind in btrfs metadata.

## Not verified

A real ENOSPC during the snapshot (only simulated); the unreadable-`grubenv` abort of `prune`; S12 (phase 2);
Secure Boot; snapper; the systemd-boot and ZFS variants; real hardware.
