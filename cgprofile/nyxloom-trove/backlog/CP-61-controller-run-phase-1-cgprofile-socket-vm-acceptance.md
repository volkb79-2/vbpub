---
kind: backlog-entry
schema_version: 1
id: CP-61
title: "Controller-run phase-1 cgprofile socket VM acceptance"
status: open
type: "bugfix"
severity: "medium"
component: "tests"
context_estimate: "small"
provenance: "Host-service sockets plan 2026-11, section 7; cgroup-profiler section 5.1 implementation report"
filed_by: "Codex implementation"
spec_owner: "cgroup-profiler service"
priority: 2
filed_date: "2026-11-06"
---

## Acceptance still required

Run the phase-1 cgroup-profiler service acceptance against the reviewed 2.2.0 candidate in a disposable QEMU guest without mdt host-setup. The implementation worktree contains the acceptance contract in `docs/NATIVE-DEPLOYMENT.md` section 5; no host or VM acceptance was run while implementing.

Use the existing `scripts/debian-install-v2/testing/vm` harness (do not add a new lane): prepare `case-b`, start a fresh guest, and wait for SSH. The harness source must be the exact reviewed worktree so the controller can copy the candidate wheels and test material into the guest. Use a second disposable guest seeded with the prior host-setup files for refusal checks.

## Required behavioral evidence

- A TTY wizard install and a non-interactive install with explicit wheel pins, group flags, and `--enable` both finish with green `cgpd doctor --json` and `cgpctl doctor --json`.
- The control and observer socket nodes and parent/state directories have the declared owner, group, and modes; each socket unit has one `ListenStream=` and `systemd-analyze verify` succeeds.
- Restarting `cgpd.service` preserves both socket inode numbers and both levels answer. Restarting each socket unit restarts the service and the affected level answers on its new inode.
- `cgpd run` without activation and without `--self-bind` exits 2. Explicit `--self-bind` works on disposable test paths.
- An observer-only user can probe the observer endpoint and receives EACCES on control; the doctor reports control connect as skip for that caller.
- On the old-layout guest, install exits 2 and prints the manual removal list; after manual removal, fresh install succeeds.
- Downgrade below the socket-unit release exits 2 without `--allow-downgrade`.
- Uninstall removes all cgpd units, sysusers/config/version files, socket nodes and runtime directories; groups and `/var/lib/cgprofile` remain by default, and removed units have no failed state.

Added by the review fix round (guest-side script `tools/vm-socket-acceptance.sh`, run through the existing harness with `copy` and `ssh`):

- `stat` of `/run/cgprofile` shows root:root 0755 after the service started, i.e. the nested `RuntimeDirectory=cgprofile/state` did not turn the parent 0700 (reasoned, not yet run).
- `systemctl show -p ActiveState cgprofile.slice` is not `active` after uninstall.
- Uninstall run from `/opt/cgprofile/current/venv/bin/cgpd` (the version tree it runs from is removed after the result is printed) and again through the zipimport route; a second uninstall is a no-op; uninstall on the mdt-style guest exits 2 and changes nothing.
- `cgpd install --yes` with no other flag over an `install.toml` that lists users succeeds and keeps an operator edit in `cgpd.toml`.
- `systemctl enable cgprofile.slice` (a slice without `[Install]`) returns 0.
- With `OLD_WHEEL_PIN` set, the real `--allow-downgrade` rollback removes the socket units and a roll-forward works.

The exact controller sequence is also listed in `docs/NATIVE-DEPLOYMENT.md`. Record the guest image, wheel pins, commands, doctor JSON, inode observations, access-denial result, refusal output, and uninstall residue check before closing this item.
