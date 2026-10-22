# Leveraging btrfs copy-on-write for our tools: options, reasoning, concerns

Companion to [BTRFS-COW.md](BTRFS-COW.md) (the host area, its contract and its checks). That guide says *how the area works*;
this document records *what we could build on it, why, and what can go wrong*, so that no valid idea or objection from the
design discussion of 2026-10-22 is lost. It is a design record, not a commitment: section 9 is the plan, section 10 the open
decisions. Evidence tags as in the guide: **[M]** measured on the reference host, **[R]** verified on a scratch VM (Debian 13,
kernel 7.2.9, btrfs-progs 7.1), **[L]** checked locally in this discussion, **[K]** known behaviour not re-verified,
**[T]** untested, **[unv]** a reviewer's claim nobody verified.

How the material was produced: our own measurements, a blind design exploration by an independent Opus session (given the
problem, the devcontainer lifecycle and a read allow-list, but not our ideas), then the same session attacking our proposal.
Where it matters, "reviewer" below means that session.

## 1. The problem, with numbers

- 173 worktrees under `.worktrees` hold 17 GB **[M]**, but source checkouts are tiny (an assay launcher worktree is 76 KB).
  The weight is generated artifacts: the TrueNAS project (`scripts/truenas`) keeps ISOs of 2.3-2.7 GB, golden VM disks of
  16-20 GiB, qcow2 overlays, probe extractions of 230-280 MB per version, and about 88 GB of scratch in a temp directory.
- Regular storage is a slow virtual disk (10-20 MB/s sustained writes), so copying one 2.5 GB ISO takes minutes and a 16 GiB raw
  disk takes 14-27 minutes. Heavy writes also stall latency-sensitive services through the shared ext4 journal.
- Repos must stay on ordinary, uncompressed storage. ext4 has no transparent compression.
- A rebuilt-by-`xorriso` restore ISO shares about 0.3% of its blocks at identical offsets with its vanilla ISO; an in-place
  modified one shares 97.1% **[M, sampled; offset-aligned, so shifted layouts are invisible]**. Reflinks help in-place edits, not rebuilt ISOs.
- A TrueNAS ISO compressed to 97% with zstd **[M]**: compression helps sparse disks and extracted trees, not ISOs, `squashfs`, or `.zst`.
- The project already uses CoW-style techniques: qcow2 overlays on a backing disk (`tcg_vm.py`), patched ISOs stored as xdelta3
  deltas against the vanilla ISO, and a multisession-overlay proof of concept.
- `vanilla-iso-patcher create` must run as root (unpack/repack of the live filesystem), which leaves root-owned trees in
  worktrees; tracked in `scripts/truenas/TODO.md` (2026-10-22).

## 2. Use cases (union of ours and the reviewer's)

Ranked by value for us. Items marked * were raised only by the reviewer.
1. **Reclaim space with proof**, not by age: know per class and per worktree what is regenerable.*
2. **Protect irreplaceable data from `rm` and branch pruning.** Ignored files never make a checkout dirty, so a worktree holding a
   20 GiB golden can be pruned or removed without warning.*
3. **Per-run VM disks as instant copies** of a golden (reflink), instead of a 20 GiB copy at 10-20 MB/s. **Our main case.**
4. **Compression of VM disks and extracted trees** on shared storage. **Our stated main case; see 4.6 for the open conflict.**
5. **Share immutable inputs** (vanilla ISOs, golden bases, probe extractions) across worktrees without copying.
6. **Warm start for assay**: warm checkout plus installed environment, snapshot per run.
7. **Crash and reboot recovery**: find orphaned scratch by proof (pid, boot-id, container-id), not age; resume guests.*
8. **Keep heavy writes off the latency-sensitive filesystem** (own LV, own journal).
9. **Snapshot / restore of declared folders** (VM-disk checkpoints, cheap test resets).
10. **Promote an artifact** from a feature worktree to shared scope before its branch dies.*
11. **Single-flight production and reader pinning** during GC (two worktrees must not extract the same probe at once).*
12. **Provenance**: recipe plus input hashes, so a deleted derived artifact can be rebuilt and verified.*
13. **Quotas and refusal before disk full**; **rehoming** storage without breaking resume; **multi-user attribution**.*
14. **Keep secrets out of shared stores** (see 7.1: golden disks embed credentials).*

## 3. Data classes, properties, and `volatile` versus `.gitignore`

| Class | Examples | Reproducible | Scope | Durability |
|---|---|---|---|---|
| Source | tracked files | yes | branch | git |
| Local config, secrets | `.env`, `truenas.toml`, golden secrets, SSH keys | **no** | user | high, mode 0600 |
| Upstream inputs | vanilla ISOs + sha256 | until upstream drops them | user/shared | verify by hash |
| Derived deterministic | patched ISOs, extracted probes | yes (recipe + input) | cache by recipe hash | none |
| Small evidence | vcdiff + manifests, gate logs | partly | family | high |
| Precious derived | golden raw/qcow2 | expensive, not bit-exact | shared, read-only | high, secret-bearing |
| Mutable run state | overlays, OVMF vars, `runner.json`, resume state | no (mid-run) | worktree | crash-consistent |
| Scratch | extractions, temp files | yes | process | none |
| Runtime handles | sockets, locks, pids | n/a | host | none; local fs, short paths |
| Toolchain environments | venvs, node_modules | by lockfile | lock hash | none |

Per-folder properties a project could declare: owning tool, scope (process, run, worktree, family, host, user), sharing mode
(exclusive, shared read-only, CoW clone), retention, regenerate command + inputs + cost, content key, sensitivity, produced as
root, dependency edges (overlay to base), placement tier, size hint, and whether it must survive `rm` (with its promotion target).

**`volatile` versus `.gitignore`.** `.gitignore` answers "does git track this?"; it carries no value, lifetime or safety-to-delete
information and enforces nothing. A retention class is a contract with tooling: may it be deleted, skipped, snapshotted, restored?
`volatile` must be a *subset* of ignored (ciu verifies with `git check-ignore`), never the reverse: `secrets.toml`, a 20-minute venv
and a golden raw are ignored and precious. Proposed classes: `volatile` (reproducible and cheap; deleted on rm/reap, never
snapshotted or restored), `cache` (reproducible but expensive; evicted by LRU or pressure, content-addressed, survives worktree
removal), `retained` (never deleted automatically). Do **not** call the last one `durable`: the LV is unbacked, single-device
storage that the guide itself calls scratch. A class must be declared so that *"deleting it at any instant, including mid-run,
loses only recomputation that the next run repeats automatically; anything a `--resume` needs is not volatile"*. The class is frozen
in the instance record at allocation; downgrades need an operator verb; declaration changes on later commits are reported, not applied.

## 4. Mechanisms compared

| ID | Mechanism | Solves | Breaks / costs | Privilege | Host vs container paths | Rebuild / reboot |
|---|---|---|---|---|---|---|
| M0 | **Conventions + survey + proof-based cleanup** (class manifest, scratch markers with pid/boot-id/container-id, prune/rm treat precious ignored data as dirt, `TMPDIR` policy) | 1, 2, 7 | does not share or speed anything | none (root helper for root-owned leftovers) | container only | stateless |
| M1 | Content-addressed store with hardlinks | 5, 11 | writers corrupt every holder; `link()` across bind mounts and subvolumes is `EXDEV`; assay forbids hardlinked writable trees | none | must sit inside one mount | persistent |
| M2 | **Separate bulk filesystem with reflinks (btrfs or XFS)** at the same path on host and container | 3, 5, 6, 8, 13 | operator work, new failure domain; an unmounted mountpoint silently fills root unless guarded | operator once | identical paths remove translation | mount must be declared and present before Docker |
| M3 | Docker as privilege broker (named volumes, image layers) | root-produced scratch, attribution | the devcontainer cannot read volume contents; data lands on Docker's data-root | none | needs bind visibility | survives both |
| M4 | tmpfs for small scratch, sockets, locks | semantics of "cleared at boot" | MB only, container-local | none | sibling containers cannot see it | cleared |
| M5 | Lazy materialisation from deltas (keep vcdiffs + pinned vanilla ISOs, rebuild patched ISOs) | space for time | 2-4 min of writing per rebuild | none | n/a | n/a |

btrfs-specific variants of M2 considered for the *ciu* layer:
- **B1 mount only** (what the guide implements): the area exists, tools consume `MDT_COW_ROOT` explicitly.
- **B2 explicit consumption in tools**: tcg/golden-iso/assay take the path from the environment or config. No ciu, no symlinks.
- **B3 ciu-registered stores**: the project declares folders; ciu creates subvolumes and symlinks them (section 6).
- **B4 the whole worktree as a subvolume**: rejected. Checkouts are tiny; repos stay on ordinary storage; the workspace identity is
  derived from the canonical checkout path and the `.git` pointer is path-sensitive (already broken across host/container paths:
  the worktree's `.git` names `/workspaces/vbpub/.git/worktrees/...`, which does not exist at the host path).

Reviewer's verdict: M0 is mandatory regardless and is most of the value; add M2 for immutable inputs, and only if the new device has
physically separate backing (the guide admits the isolation benefit is unmeasured on the slow disk). Reflinks supersede M1.

### 4.6 Open conflict: compression of VM disks

Our stated main case is VM disks on compressed storage. The reviewer says compression is wrong for random-write images (4 KiB guest
writes into 128 KiB compressed extents cause read amplification, partially pinned extents and fragmentation on a slow disk) **[K, unv]**.
Related measured facts: `chattr +C` (no-CoW) is refused on a directory that has a compression property **[R]**, so a folder is either
compressed or no-CoW; golden raws are already sparse (`qemu-img convert -S 4k`), so zeros cost nothing and compression only helps the
non-zero part (an installed OS of a few GB in a 16 GiB image). **Decision pending a benchmark on the scratch VM**: random-write fio on a
raw image in a compressed subvolume versus a plain one versus no-CoW versus ext4. Until then the guide should not recommend
`compression zstd` for `shared/vm-disks`.

## 5. Boundaries

- **Projects** (truenas, assay) own declaration: classes, regenerate recipes, what counts as precious.
- **A neutral workspace-storage component**, ciu-agnostic, owns store layout, pins, leases, survey and GC. It belongs beside the neutral
  `<git-common-dir>/.workspace-instances` family record (which already carries leases, S16.9) and uses the git-common-dir `flock` pattern.
  Reviewer's view: a neutral library that tcg and assay can use without ciu, not inside ciu and not baked into the mdt image. Our leaning
  was ciu (it will absorb `run-gate`). **Open (section 10).**
- **ciu** owns ordering only: run the artifact check/clean before `git worktree remove`, make `branches -y` treat declared precious data as
  dirt. It must not learn about ISOs.
- **mdt-cockpit / host-setup** own the mount, the preflight that it is present, `/tmp` semantics, `io.max`; they stay ciu-agnostic.
- **tcg_vm** owns guest state; it needs a base-disk rebind and must publish its references to state directories so the survey sees them.
- **assay** keeps its isolation rules and consumes warm environments without owning them.

## 6. The `ciu storage` design (deferred; corrected by the review)

What we proposed, in one place so it can be revived. Today `ciu worktree` creates a git worktree plus a schema-v5 record, a runtime identity
(network, prefix, volumes), and `rm` runs `ciu clean` then `git worktree remove`; the spec text I read has no per-instance storage concept.

```toml
[ciu.storage.vm-disks]
path = "scripts/truenas/.vm"     # relative to the ciu root; git-ignored; no '..', no symlink components
compression = "none"             # see 4.6
retention = "volatile"           # volatile | cache | retained
snapshot = true
```

- ciu applies it for **every committed ciu root** it finds (the way `create` already discovers roots), namespaced by the root's relative path.
- It creates one subvolume per store (flat; plain directories above), sets the property on the subvolume itself (no inheritance **[R]**), and
  links the registered path to it. A symlink is the only unprivileged "mount" (the worktree is on ext4; unprivileged users cannot bind-mount).
- Primary checkout: `ciu storage ensure` (idempotent; also called by `worktree create/ensure/adopt`); `ciu storage adopt NAME` moves existing
  data in once (a real copy across filesystems). Verbs: `ensure|adopt|snapshot|restore|du`; `inspect`/`list --json` show path and size; an
  orphan survey covers stores whose record is gone; additive record schema bump.
- Fallback: we proposed `required=false` = declared, reported fallback to a plain directory. **Review: drop it** (a reported fallback nobody
  reads is silent); absent `MDT_COW_ROOT` means plain directories for the whole family, declared once; switching is an explicit migration verb.

### Corrections from the review (must be applied before any build)

- Store key **not** repo + logical name (two clones of one repo collide; a reused name inherits old state): key by family id + workspace id +
  allocation nonce, with an owner manifest (family path, instance, class, created_at) in each store. Survey deletes only stores whose manifest
  names this family and whose record is proven gone.
- `rm` order: preflight (git removability, container-mount scan) -> `git worktree remove` -> delete stores **last**, driven by the family
  record in the git-common-dir (the in-checkout record dies with the checkout). `--force` never extends to stores.
- Deleting a subvolume succeeds with a file still open **[R-local]** (the VM would keep writing to a deleted disk), so the guard must scan every
  container, running or stopped, whose `Mounts[].Source` lies under the store, plus `/proc/*/fd`. Leases are off by default (S16.9).
- Tools never create store paths; they ask a helper that refuses when the link is missing. Prefer handing the path through an environment
  variable (`CIU_STORE_<NAME>`) instead of relying on the symlink.
- `restore` and `snapshot` refuse while any holder exists (qcow2 uses `cache=writeback`, so a live snapshot is inconsistent).

## 7. Per-project notes

### 7.1 TrueNAS (`scripts/truenas`, from the user's proposal and the review)
- One shared artifact location with checksum-verified upstream ISOs (TrueNAS 25.10.7, 27.0.0-RC.1, the exact 28 nightly of 2026-10-14, Debian
  Live 13.7.0 with checksum and signature). Resolve it from config (`${MDT_COW_ROOT}/shared/truenas/...`), **not** from "the main checkout":
  that path differs between host and container, while the CoW mount keeps one path.
- Golden: keep `truenas-27-16GiB-golden.raw` as canonical until a replacement is rebuilt and verified; the older `.raw.zst` is the ISO payload and
  is not an identical copy. Golden disks embed credentials and step1 requires the secret sidecar beside the image, so "secrets stay out of the
  CoW area" is **false for goldens**: classify them secret-bearing (mode 0700, never in shared scope for other users) and change step1's sidecar
  rule deliberately.
- `secrets.toml`: versioned schema (instance table, user tables, optional passwords, named API keys, per-user SSH key references; private keys as
  separate 0600 files); tracked schema example; explicit `.gitignore` entry (current one lacks it); path resolved by config, not by checkout;
  the reader refuses anything but mode 0600; never inside worktrees or the CoW area (snapshots copy it).
- Removal set (user-confirmed only): generated restore/patch ISOs, recreatable VM state and test disks, `/tmp/truenas*` scratch, duplicate source
  ISOs after hash comparison, the older `.raw.zst` only after comparison. Per-run `restore-target*.raw` become reflinks of the golden; overlays
  stay qcow2 where portability matters; probe extractions become a cache keyed by ISO sha256.
- Restore ISOs are not stored: keep recipe + xdelta and materialise on demand into per-run scratch, or build in place / multisession onto a reflink copy.
- tcg: `runner.json` pins the base disk's device and inode (`tcg_vm.py:852-857`); identify the base by sha256 + size and add `rebind-base`
  (st_dev did not change across remounts here **[R-local]**, but its number comes from an allocator, so a reboot with other mounts could differ **[unv]**).
  `destroy` removes only the container and keeps state, and no verb prunes state directories (`.vm/` has 6, `qemu-test/` has 16): add a prune verb.
  Rebinding via hard links fails across subvolumes (`EXDEV`): add a `promote` helper (reflink, verify, unlink).
- `vanilla-iso-patcher` root requirement: backlog in `scripts/truenas/TODO.md` (rootless options: squashfs-tools-ng tar streaming, fakeroot, rootful
  throwaway container or VM, private work directory with a root cleanup helper).

### 7.2 assay
Warm base + per-run snapshots under `<repo>/assay/...`. **An editable install in the base breaks it**: the snapshot's `.pth` points at the base's
source, so mutants written in the snapshot are never imported and the judge scores the wrong code. No editable installs or absolute-path artifacts
in a base; re-install per snapshot and assert `module.__file__` lies under the snapshot root. Console-script shebangs have the same flaw **[R]**.

### 7.3 Devcontainer lifecycle (reviewer's walk)
`/tmp` and home persist across rebuilds by design, so `/tmp` never gets "cleared at boot" (likely why 88 GB accumulated); `shutdownAction: none`
keeps the container running across IDE disconnects; guests started with `docker run --detach` and no restart policy stay exited after a host
reboot; sibling containers survive a rebuild and the VM's state identity hashes the container-namespace path; Dev Containers can renumber the uid.
Therefore: a `TMPDIR` policy for producers, a scratch reaper based on pid/boot-id/container-id proof, and a `${localEnv:USER}` guard in
`initializeCommand` (see issue A14).

## 8. Verified facts used by this document

| Fact | Tag |
|---|---|
| Host `umount` of the whole filesystem succeeds while a container holds a bind of a subdirectory; the container keeps the superblock, the device stays busy | [R] |
| `nofail` mounts are only wanted by `local-fs.target`, not ordered before it; `RequiresMountsFor=` fixes ordering | [R] |
| New subvolumes do not inherit the parent's compression property; snapshots carry it | [R] |
| `chattr +C` is refused on a compressed directory; no capability beyond Docker defaults needed for non-root | [R] |
| Second mount cannot change filesystem-wide options (silently ignored; tested only for `discard`) | [R] |
| Space returns 20-40 s after a 900 MiB subvolume delete (fast/loop disk); `rm` of files frees at once only for unshared extents | [R] |
| `rm -rf link/` (trailing slash) deletes the target's contents and leaves the link | [L] |
| `btrfs subvolume delete` succeeds with a file open; the open descriptor keeps reading | [R] |
| `st_dev` of a subvolume was identical across three remounts | [R-local] |
| Fsync coupling: sibling snapshot storm p99 0.26 -> 1.03 ms on a RAM disk; loop-on-ext4 showed 50 ms stalls with heavy writes on either filesystem; not representative of a slow disk | [R] |

## 9. Issue register (adversarial review; classification)

FIX = change the design before building; BY-DESIGN = declare it; KNOWN-ISSUE = accept, document, monitor. Severity: DL data loss, SWR silent wrong
result, OUT outage, ANN annoyance. All are [unv] unless tagged.

| ID | Issue | Sev | Class |
|---|---|---|---|
| A1 | `rm` deletes stores before git can refuse (dirty checkout, failed clean, `--force`) | DL | FIX: stores last, preflight first |
| A2 | Another clone's survey deletes stores keyed by repo+logical name | DL | FIX: family id + nonce + owner manifest |
| A3 | `volatile` declared on resumable guest state is deleted | DL | FIX + declare class wording (section 3) |
| A4 | rm/restore with a stopped-but-existing container or live qemu; leases off by default | DL | FIX: mount-source scan, running and stopped |
| A5 | Editable install in assay base makes the judge test the base's code | SWR | FIX (7.2) |
| A6 | A checkout/merge makes the declared path tracked; git replaces the ignored link | SWR | FIX: `ciu check` refuses a tracked path; realpath check before every write |
| A7 | `git clean -fdx` then a tool's `mkdir -p` recreates a plain ext4 directory | SWR/OUT | FIX: tools never create store paths; env var path |
| A8 | Reused logical name inherits old state; `--resume` runs new code on old overlays | SWR | FIX: bind to allocation nonce |
| A9 | `st_dev`/inode pin in `runner.json` breaks `--resume` after remount/restore/reflink | OUT | FIX in tcg (hash identity, `rebind-base`); remount case not reproduced |
| A10 | snapshot/restore under a running VM (`cache=writeback`) | DL/SWR | FIX: same guard as A4 |
| A11 | A buggy tool user-deletes `shared/vm-disks/base`; `ro` is a flag the owner can flip | DL | BY-DESIGN: "filesystem permissions in the user area do not protect anything from that user's own tools"; protection = pin manifests + backup |
| A12 | "durable" promised on an unbacked single-device LV; backups copy only symlinks | DL | BY-DESIGN: rename to `retained`, document |
| A13 | Several users in one checkout: links point into user A's 0750 area | OUT | BY-DESIGN: a checkout with stores belongs to one user; gate containers mount `MDT_COW_ROOT` explicitly |
| A14 | Empty `${localEnv:USER}` makes the bind source the top level | DL/sec | FIX: `initializeCommand` refuses empty USER and checks owner uid |
| A15 | Root/`SYS_ADMIN` in the container can touch other users' and shared subvolumes | DL | BY-DESIGN: the per-user split is a namespace, not a security boundary |
| A16 | Disk-pressure eviction with async delete feeds back (no pending accounting; no LRU signal with `noatime`) | DL(cache) | FIX/DEFER: no auto-eviction in v1; explicit `gc` with `subvolume sync` and an explicit access log |
| A17 | One sparse overlay plus data disks fill the 200 G pool; metadata ENOSPC may need root | OUT | KNOWN-ISSUE: monitor, tcg admission check, evaluate simple quotas |
| A18 | Host unmount "succeeds"; `btrfs check`/`lvreduce` run while a container still writes | DL | FIX (runbook): maintenance check scans all namespaces' `mountinfo` |
| A19 | Shutdown race: `nofail` mount not ordered against docker | DL(minor) | FIX: docker.service drop-in `After=` the mount unit (ordering only) |
| A20 | Golden disks embed credentials; step1's sidecar rule | DL(exposure) | BY-DESIGN: secret-bearing class (7.1) |
| A21 | The 88 GB is in the host-backed `/tmp`; declared stores do not move it | ANN | FIX: `TMPDIR` policy + scratch reaper |
| A22 | `required=false` reported fallback is unread; later enabling CoW means copying GBs and breaking identities | ANN/OUT | FIX: drop it (section 6) |
| A23 | zstd on `shared/vm-disks`: read amplification, fragmentation on random writes | ANN | FIX pending benchmark (4.6) |
| A24 | `defragment -czstd` unshares reflinked and snapshotted extents | OUT | FIX (guide): warn, never on shared/bases/snapshotted data |
| A25 | Nested store subvolumes under a per-instance subvolume: snapshots show empty dirs; delete order | ANN | FIX: flat layout |
| A26 | Cross-subvolume `mv`/hardlink: `EXDEV` or silent 16 GiB copy | ANN | FIX: `promote` helper; stage inside the destination subvolume |
| A27 | Two agents run `ensure` concurrently; lock scope wrong | ANN | FIX: `flock` on the store-area directory fd; idempotent create-then-verify |
| A28 | uid mapping (host 1003, image 1000) and a second person opening the host-folder cockpit | OUT | BY-DESIGN: fails loudly; document |

### Symlink attacks (for B3)
git: clean removes the link not the target (safe, opens the A7 window); checkout/merge may replace an ignored link (A6); `git stash -u` includes the link only.
Deletion: **`rm -rf link/` with a trailing slash empties the target [L]**; `shutil.rmtree(link)` raises; `cp -rL`, `tar -h`, `rsync -L`, `find -L`,
`copytree` follow it and copy gigabytes. `realpath`/`st_dev`: every subvolume has its own `st_dev`, so `os.replace`/`link` into a store fail with
`EXDEV`, and `du -x`/`find -xdev`/`rsync -x` stop at the link; tcg hashes the resolved path, so every caller must resolve. Docker/Compose resolves
bind sources in the host namespace, so the link must be absolute and same-path; sibling gate containers need the CoW bind explicitly. qemu: a
relative backing file resolves against the opened path **[unv]**; sockets need short paths. Backups see only links and `du .worktrees` drops
to near zero (false reclaim); use `btrfs filesystem du` (exclusive versus shared) because plain `du` double-counts reflinks.

### Retention-class attacks
`volatile` wrongly deletes resumable guest state, root-produced evidence, and (after a reap misjudged `checkout-missing` because the checkout's mount
was down) a perfectly good store. `cache` wrongly deletes a base still used as a qcow2 backing file (reflink copies are independent, backing files
are not), an ISO bound read-only into a running guest, or over-evicts under async delete; a golden is not "reproducible", so `cache` is the wrong class
for it. Anyone who can edit committed config can downgrade a class, so the class is frozen at allocation. Declaration drift: `ensure` fast-returns
`ready`, so a removed declaration leaves an orphan behind a live link. Path validation: relative, no `..`, no symlink components, inside its own root,
not inside a nested root, not tracked at HEAD, no overlapping stores across roots.

### Claims to qualify (reviewer, applied to BTRFS-COW.md where marked)
"Second mount cannot change filesystem-wide options" is shown only for `discard` and may be kernel-version specific; "rm frees space at once" is false
for reflinked or snapshotted extents; the async-delete timing and the "unprivileged cleanup works when full" result come from fast or tiny loop
filesystems (metadata ENOSPC is the real failure mode); the per-directory compression level was tested on 7.2.9 while gstammtisch runs 7.2.6; the
`chattr +C` refusal cause is not established; "an LV isolates journals" was never measured on the slow disk; the guide's per-user split is not a
security boundary while root or `SYS_ADMIN` exists.

## 10. Phased plan, deferrals, refusals, decisions

**Phase 0 (no btrfs needed):** survey verbs (per-worktree ignored bytes, per-store exclusive bytes, `/tmp` by producer); prune/`rm` guard that treats
large ignored data as dirt; a tcg state-directory prune verb; a `TMPDIR` policy and a proof-based scratch reaper.
**Phase 1 (host area + explicit consumers, no ciu, no symlinks):** apply the guide fixes (A14, A18, A19, A23, A24); tools consume `MDT_COW_ROOT`
explicitly: `shared/upstream-iso` content-addressed and write-once, tcg/golden-iso make reflinked per-run disks in a flat per-run subvolume with an
owner manifest after the A9 fix; assay warm base after the A5 fix.
**Defer:** ciu-managed symlinked stores, the `cache` class and automatic eviction, `snapshot`/`restore` verbs, the `required` flag, the helper's home.
**Refuse to build:** disk-pressure auto-eviction; restore/snapshot while any container mounts the store; logical-name store keys; a class called
`durable` on this filesystem; automatic link recreation after `git clean`.
Where M0 alone is the better answer: most of the 17 GB and the 88 GB, with no new failure domain. btrfs earns its place for reflinked VM disks and the
assay warm base, and only after A5, A9 and A23.

**Open decisions:** (1) order: Phase 0 first? (2) run the compression-versus-random-write benchmark before fixing the VM-disk property; (3) where the
shared helper lives (neutral library vs ciu vs mdt-cockpit); (4) file `CIU-NN` and tcg backlog items; (5) rootless patcher route (TODO entry); (6) which
removal-set items the user confirms; (7) whether a checkout with stores is single-user (A13).
