# Case B: root-fills-disk shrink — design (not yet implemented)

Status: design only, per operator decision 2026-09-03 ("design it now, implement
separately"). Nothing in this document is wired into `debian_install_v2` yet.

## Problem recap

`inuse_partition_editor.py`'s `Table.add()`/`free_regions()` only place new
partitions into space that's *already* free on disk — by design, it never
touches an existing partition's boundaries (see its docstring: edits a
disk currently in use, "existing entries are kept verbatim"). That's
**Case A**, and it's a safe, already-solid operation.

**Case B** — a fresh host whose root partition already spans (most of) the
disk, so there's no free space until root's own declared size is reduced —
is a different, much higher-stakes operation: shrinking a live filesystem's
container out from under it.

## Why this can't be a normal stage1/stage2 step

ext4 supports **online grow** but not **online shrink**. debian-install-v2's
existing two-stage model (`_stage1()` runs during first boot with root
already mounted read-write; `_stage2()` resumes after a reboot, root
*again* already mounted read-write by the time `vbpub-bootstrap-stage2.service`
fires) never has a window where root is unmounted. Shrinking root's
filesystem is therefore impossible from either existing stage.

## Prior art: the old script's proven mechanism

`scripts/debian-install/create-swap-partitions.sh`'s `schedule_offline_ext_shrink()`
(≈L293-460) solves this by running the shrink **inside the initramfs**, before
the real root is ever mounted read-write:

1. Installs `initramfs-tools`/`e2fsprogs`/`util-linux` if missing (L302-313).
2. Computes target block count from `dumpe2fs -h` block size + the planned
   new partition size in sectors (L317-333).
3. Persists the planned partition table at `/etc/vbpub/offline-ptable.sfdisk`
   (L334-335) for the hook to apply later.
4. Registers a one-shot script and rebuilds initramfs via `update-initramfs`
   (L441+), so the sequence below runs on the *next* boot, pre-mount:
   `e2fsck -f -y` → `resize2fs` to the computed block count → `e2fsck -f -y`
   again → apply the saved sfdisk plan.
5. L664: explicit comment confirming the online-shrink impossibility is why
   this exists at all: *"ext* cannot shrink online. Scheduling an OFFLINE
   shrink+repartition in initramfs is required to avoid boot failure."*

The mechanism is correct; the operator's own assessment is that the
*script* implementing it was written badly (worth a clean-room port, not a
copy-paste).

## Proposed design for debian-install-v2

### Key simplification: no extra reboot needed

`_stage1()` already ends in exactly one reboot (`_install_stage2()` +
`_reboot()`) before `_stage2()` resumes. `initramfs-tools` hooks run on
**every** boot, unconditionally, before any systemd unit. So if the shrink
hook is installed *during* `_stage1()` (before that same reboot), the
existing single reboot is suffient to trigger it — Case B does not need a
new boot stage or a second reboot cycle. This is the single most important
design property: the blast radius of new machinery is one narrow,
self-contained initramfs hook, not a new stage in the state machine.

### Stage1 addition: `_plan_root_shrink()`

Runs late in `_stage1()`, after the existing swap-shape planning
(`_plan_swap_partitions()`/`_disk_facts()`, unchanged) determines whether
free space already covers the planned swap shape:

- **Free space already sufficient → Case A.** Do nothing new; the existing
  (Case-A, `inuse_partition_editor.Table`-based, per the separate
  in-flight integration) swap-placement path in stage2 handles it exactly
  as today. No hook installed.
- **Free space insufficient → Case B.** Compute the shrink:
  - Read root's current filesystem facts via `dumpe2fs -h` (block size) and
    `resize2fs -P` (minimum required blocks) — both read-only, both safe to
    run against the live mounted root filesystem.
  - `target_blocks = max(resize2fs_minimum_blocks, requested_blocks_for(preserve_root_size_gb))`.
    If `requested_blocks_for(preserve_root_size_gb) < resize2fs_minimum_blocks`,
    **refuse outright** (raise, do not silently clamp) — this is exactly
    the "always verify at runtime, refuse if it doesn't fit" rule the
    operator specified for the filesystem-safety check.
  - Build the new sfdisk plan (root truncated to the sector-equivalent of
    `target_blocks` + a safety margin, swap partitions placed in the freed
    tail — reuses the same plan-construction the Case-A path already uses
    once the free space conceptually exists).
  - Write a single JSON shrink-plan file consumed by the hook (device,
    target_blocks, block_size, the sfdisk plan text, a checksum of the
    plan) — analogous to the old script's `/etc/vbpub/offline-ptable.sfdisk`
    but self-describing enough that the hook needs no other state.
  - Install the hook script (see below) to
    `/etc/initramfs-tools/scripts/local-premount/vbpub-root-shrink`,
    `chmod +x`, run `update-initramfs -u`.
  - `state.mark_step("root_shrink", "planned", ...)`.

### The hook itself: lean POSIX shell, not Python

`local-premount` hooks run in the initramfs's own tmpfs environment before
the real root is mounted — the `debian_install_v2` Python package is not
available there (bundling a Python interpreter into the initramfs to avoid
a shell script is not worth the added image size/failure surface). The
hook is a small, carefully-reviewed shell script, structurally:

```
#!/bin/sh
PREREQS=""
case "$1" in prereqs) echo "$PREREQS"; exit 0 ;; esac
. /scripts/functions   # initramfs-tools helper functions (log_*, panic, etc.)

PLAN=/etc/vbpub/root-shrink-plan.json   # copied into the initramfs image at build time
[ -f "$PLAN" ] || exit 0                 # nothing to do — already cleaned up or never planned

# ... parse PLAN's device/target_blocks/expected-current-size without a JSON
# library (busybox sh has none) — e.g. the plan file is written by Python
# as a flat `KEY=value` shell-sourceable file instead of JSON, sourced
# directly with `.`, sidestepping any parsing at all in the hook.

DEV=... TARGET_BLOCKS=... SFDISK_PLAN_FILE=...

# Idempotent no-op if already shrunk (covers: hook ran successfully on a
# prior boot but stage2 hasn't cleaned it up yet, or a stray re-trigger).
CURRENT_SECTORS=$(sfdisk --dump "$DISK" | ...)   # read current root size
[ "$CURRENT_SECTORS" -le "$TARGET_SECTORS" ] && exit 0

e2fsck -f -y "$DEV" || { log_failure_msg "vbpub-root-shrink: fsck failed, skipping shrink"; exit 0; }
MIN_BLOCKS=$(resize2fs -P "$DEV" 2>&1 | ...)
[ "$MIN_BLOCKS" -le "$TARGET_BLOCKS" ] || { log_failure_msg "vbpub-root-shrink: target too small (min=$MIN_BLOCKS)"; exit 0; }

resize2fs "$DEV" "$TARGET_BLOCKS" || { log_failure_msg "vbpub-root-shrink: resize2fs failed"; exit 0; }
e2fsck -f -y "$DEV" || { log_failure_msg "vbpub-root-shrink: post-shrink fsck failed"; exit 0; }

sfdisk --no-reread --force "$DISK" < "$SFDISK_PLAN_FILE" || {
    log_failure_msg "vbpub-root-shrink: sfdisk apply failed; growing filesystem back to stay consistent"
    resize2fs "$DEV"     # grow back to fill the (still large) partition — always safe/online-capable
    exit 0
}
# no partprobe/udevadm here — initramfs re-reads the table itself when it
# opens the (now correctly sized) root device moments later in the normal
# mount sequence; do not touch anything else.
```

Every failure branch **falls through to `exit 0`** (never `panic`/abort the
boot) — this is the fail-safe-not-fail-stop principle from the operator's
own runtime-safety-check answer, extended to the whole hook: a host that's
reachable over the network and flagged "shrink didn't happen, investigate"
beats a host stuck at an initramfs rescue shell with no console access
(these are remote/unattended netcup-style installs). The one exception —
sfdisk failing *after* the filesystem was already shrunk — actively repairs
consistency (grow the fs back) rather than merely not-making-it-worse,
because at that point "do nothing" would leave a shrunk filesystem inside
a still-large partition, which is safe but wasteful, not dangerous; growing
back is strictly better and cheap.

`e2fsck`/`resize2fs` are already bundled into every Debian initramfs by
default (`e2fsprogs` ships its own `/usr/share/initramfs-tools/hooks/e2fsprogs`
hook) — no extra `copy_exec` needed for those. `sfdisk` (from `util-linux`)
needs verifying; if it's not already present, the planning-time hook
installer must also install a small companion `hooks/` script that
`copy_exec`s `sfdisk` (and its shared libs) into the image at
`update-initramfs` time.

### Stage2 addition: verify, apply, and clean up

First action in `_stage2()`, before the existing zswap/cgroup2-flags work:

- If state has no `root_shrink` step recorded → Case A, nothing to do,
  proceed exactly as today.
- If `root_shrink: planned` → read the current `sfdisk --dump` and compare
  root's actual size against the plan:
  - **Matches (hook succeeded)**: `mark_step("root_shrink", "success", ...)`,
    delete the hook script + shrink-plan file, run `update-initramfs -u`
    to strip it back out (keeps every later boot lean — the hook's own
    idempotency check makes leaving it installed *safe*, but not free:
    every boot would still pay an `sfdisk --dump` + read-only fsck-probe
    cost forever otherwise). Continue into the normal Case-A swap-placement
    path — free space now exists exactly as if it had been there all along.
  - **Doesn't match (hook silently no-op'd/failed)**: `mark_step("root_shrink",
    "failed", ...)`, call `vbpub-notify` ("root shrink did not complete,
    swap was not configured, host is otherwise healthy"), and **stop short**
    of attempting swap placement — do not guess, do not retry destructively
    without a fresh operator decision. Leaves the host in a clearly-flagged,
    always-bootable, partially-provisioned state rather than a silent
    wrong one.

### What stays exactly as it is

- `Table.add()`/`free_regions()`/`Table.write()` (the Case-A engine) are
  unchanged by any of this — Case B's whole job is to make a Case-B disk
  *look like* a Case-A disk by the time stage2's swap-placement code runs.
- Config surface: no new user-facing knob. Whether Case A or B applies is
  entirely derived from the disk's actual state at install time, not
  something an operator chooses up front — `preserve_root_size_gb` (already
  a Config field) is the only input, same as today.
- Rollback/backup discipline for the partition-table write itself is
  identical to Case A's (`Table.write()`'s backup+checksum+stdin-piped
  `sfdisk`), just invoked from the shell hook instead of Python.

## Open items for the follow-up implementation session

1. Verify `sfdisk` is (or isn't) already present in a stock Debian
   initramfs; add the `copy_exec` hook if not.
2. Decide the shrink-plan file format concretely (flat `KEY=value` shell
   source vs. a tiny hand-rolled parser) — leaning shell-sourceable to
   avoid any parsing code in the hook at all.
3. Real contract test: `mkfs.ext4` + `e2fsck -f` + `resize2fs -P`/shrink
   against a plain regular file (no loop device needed, matching how the
   sfdisk contract tests already work in this repo) to prove the exact
   command sequence before it's wired into a hook that only gets exercised
   at real boot time.
4. A real end-to-end proof needs an actual initramfs rebuild + reboot
   cycle — almost certainly `scripts/debian-install-v2/testing/`'s existing
   privileged-container harness (built for exactly this class of
   real-`sfdisk`/`--commit` testing) rather than anything mockable in the
   normal r0-r1 dry-run lane.
5. `dumpe2fs`/`resize2fs -P` output parsing (exact field names/units) needs
   verifying against the real tool versions this fleet's Debian release
   ships, not assumed from the old script's `awk` pattern.
