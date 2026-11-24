# Design note: dr-state core and backend split (P-rollback-10)

Status: decided 2026-11-24 for P-rollback-20..24; review round 1 applied. Inputs: PLAN-rollback B1-B15, the
2026-11-24 design review (finding IDs in each heading), `poc/dr-state` and its suites on main `313ae9dcb`, and the ZFS
facts from `330bdb835` (read only to check fit). B1-B15 are not reopened. "Measure" marks an unknown that needs
evidence; it is not a guess.

## 1. Runtime form and module layout (H2, M2, B1)

- **D1 Language (B1).** Stdlib-only Python 3, no third-party imports. **Minimum Python 3.13**, the `python3` of
  Debian 13 (trixie), the only target (D9). The launcher exits 70 with a clear message on an older interpreter.
- **D2 Location (M2).** The runtime lives outside every OS state and all states share it: `/usr/local/sbin/dr-state`
  (launcher) and `/usr/local/lib/dr-state/dr_state/` (package). On btrfs `/usr/local` is `@usr-local`. On ZFS it must
  be a dataset outside every BE (a requirement for P-rollback-12/43). This is a documented rule: no `.deb` into `/usr`.
  The interpreter belongs to each state, so installers add `python3` (full stdlib) to the debootstrap `--include`.
- **D3 Launcher.** `#!/usr/bin/python3 -I`. It inserts `<own dir>/../lib/dr-state` into `sys.path` and calls
  `dr_state.cli.main()`. The same rule works installed and from the repository tree (section 10).
- **D4 Modules.**

```text
dr_state/__init__.py   FORMAT = 1, version        dr_state/lock.py       flock, atomic_write (temp+fsync+rename+dir fsync)
dr_state/cli.py        argparse, verbs, --sysroot, --json, exception -> exit code
dr_state/core.py       verbs as functions over a Context(paths, storage, boot, runner, clock, env)
dr_state/ids.py        grammar, StateId, slug, entry-file mapping, boot-count strip, version sort
dr_state/metadata.py   sidecars, counter floor, format files    dr_state/errors.py   exception types (section 4)
dr_state/apt.py        Pre-Install-Pkgs plan parser, window      dr_state/paths.py    every path, under one sysroot
dr_state/retention.py  prune selection, capacity formula, degraded-menu selection
dr_state/hashcache.py  kernel hash cache (M5)                    dr_state/check.py    check invariants (H4)
dr_state/storage/{__init__,btrfs,zfs}.py   Storage protocol + backends
dr_state/boot/{__init__,grub,sdboot,zbm}.py  Boot protocol + backends
```

- **D5 One subprocess boundary.** Every external program (`btrfs`, `zfs`, `zpool`, `grub-editenv`, `grub-mkimage`,
  `bootctl`, `dpkg-query`, `findmnt`) runs through `Runner.run(argv, *, input=None) -> Completed`: argv only, never a
  shell, found on `PATH`. A non-zero exit raises `StorageError`/`BootError` carrying argv and stderr.
- **D6 Backend selection.** Storage comes from `findmnt -n -o FSTYPE --target <sysroot>` (`btrfs` or `zfs`). This is
  one code path in production and tests: tests put a `findmnt` stub on `PATH`. The `/etc/fstab` root line is not used,
  because ZFS installs have no `/` line. Boot comes from the ESP marker only (M1, section 3.4).
- **D6a Symlink rule.** dr-state never follows a symlink inside the control directory. A symlinked file there reads
  as invalid, and a write replaces the link itself by rename (the counter, D14). A symlinked control directory refuses
  every write (exit 3). Symlinks elsewhere (`/.top`, `/boot/efi`) are followed.

## 2. State names (H1, M8, B3)

- **D7 One grammar, only in `ids.parse()` (H1).**
  `^@os-(?P<seq>[0-9]{6})-(?P<date>[0-9]{8})-(?P<time>[0-9]{6})Z-(?P<slug>[a-z0-9_-]{1,64})$`, plus `seq != 000000`
  and a date/time that round-trips as a real UTC instant. The bound of 64 makes B3's `+` concrete, keeps every entry
  file name far below FAT's 255 bytes, and equals what the generator emits. `parse()` returns a `StateId` or `None`.
  No other module matches state names.
- **D8 Generator.** `ids.slug(source, reason)`: `apt` gives `apt`, `install` gives `install`. A manual reason is
  lower-cased, each run outside `[a-z0-9_-]` becomes `-`, the result is prefixed `manual-` and cut to 64, and it never
  ends in `-` or `_`. The verbatim reason stays in the sidecar.
- **D9 Unmanaged (H1).** Storage reports every `@os-*` object. A name that fails `parse()` is `unmanaged`: listed
  (`list`, `--json`), never published, never pruned, refused as a `delete`/`set-default`/`boot-once` target (exit 3),
  and refused as a snapshot source. A running unmanaged state makes `snapshot` and the apt hook exit 3, which keeps
  "the hook refuses on unsupported disks" without the old counter gate.
- **D10 Deleted legacy (H1).**
  - Deleted: `.conf`-state handling, the `@os-pre-*`/`@os-0001` readers, 12-hex kernel ids (only `<kver>-<64 hex>`
    remains), the bash scratch shapes and the suffix-less `loader.conf` migration.
  - Also deleted: the FAT case-fold listing scan. B-3 checks ownership by a path lookup of the exact target (open or
    lstat), never by comparing directory listings. On VFAT that lookup also finds a case alias such as
    `OS-…-INSTALL.CONF`.
- **D11 Entry file and id.** The entry file is `StateId.bare + ".conf"` (`bare` = the name without `@`).
  systemd-boot's entry id is that file name, `.conf` included. `default` lines and `bootctl set-oneshot` always use it.
- **D12 Ownership and boot counting (M8).**
  - Ownership: an entry is ours if and only if its first line (CR stripped) is
    `# generated by dr-state sdboot; do not edit`. The file name never decides ownership.
  - Mapping: strip a case-insensitive `.conf`, then one trailing `\+[0-9]+(-[0-9]+)?`, prepend `@`, `parse()`. `+` is
    outside the grammar, so the strip is unambiguous. Selector values map the same way. systemd-boot also strips the
    counter from entry ids, so `default os-X.conf` stays correct under counting.
  - An owned file that maps to no managed state is stale. `publish` removes it subject to D17a.
  - `publish` rewrites a state's existing owned file in place (keeping a counter-suffixed name). If several owned
    files map to one state, it keeps the first in byte order.
  - Phase 2 itself is not built.

## 3. Interfaces (H2)

### 3.1 Shared types

```python
@dataclass(frozen=True)
class StateId:                 # built only by ids.parse(); managed by construction
    name: str                  # "@os-000002-20261118-120100Z-apt"
    sequence: int; created: datetime; slug: str   # created is UTC
    bare: str                  # "os-000002-…-apt": entry stem, ZFS leaf and snapshot name
Kind = Literal["live", "checkpoint"]
@dataclass(frozen=True)
class Stored:                  # one @os-* object as storage sees it (managed or not)
    name: str; kind: Kind | None; origin: str | None   # kind None: storage cannot tell (btrfs)
@dataclass(frozen=True)
class KernelPair:              # host paths valid inside Storage.session()
    version: str; vmlinuz: Path; initrd: Path
    fs_vmlinuz: str; fs_initrd: str   # path as a loader's fs driver sees it ("/@os-…/boot/vmlinuz-…"), "" if none
@dataclass(frozen=True)
class Space: size: int; free: int     # free includes bytes the backend reports as being freed (zfs `freeing`)
@dataclass(frozen=True)
class Selector:                # a parsed default or one-shot
    raw: str; state: str | None; entry: str | None; known: bool   # known=False: unreadable or unmappable
```

### 3.2 Storage

```python
class Storage(Protocol):
    name: str                                   # "btrfs" | "zfs"
    control_dir: Path                           # outside every state; btrfs: <top>/.dr-state
    def session(self) -> ContextManager[None]   # scope of temporary mounts; unmounts on exit
    def list(self) -> list[Stored]
    def running(self) -> str | None             # booted state's name; None when unknown
    def snapshot(self, source: StateId, target: StateId) -> None
    def activate(self, state: StateId) -> None  # make state a bootable live object named by its ID
    def delete(self, state: StateId) -> None
    def kernels(self, state: str) -> list[KernelPair]   # complete non-empty pairs, newest first
    def parent(self, state: StateId) -> StateId | None
    def locator(self, state: StateId) -> str    # btrfs: filesystem UUID; zfs: dataset
    def root_args(self, state: StateId) -> str  # btrfs: "root=UUID=<fs> rootflags=subvol=@os-…"
    def space(self) -> Space
```

- **What is added to B2's five methods, and why.** `running`, `activate`, `session`, `locator`, `root_args` and
  `space` are added now, so that ZFS needs no later interface change.
  - `running` is storage-specific: btrfs reads `rootflags=subvol=` from `<sysroot>/proc/cmdline`, ZFS uses findmnt.
    It is now detected in one place instead of four (H2).
  - `activate` is ZFS clone+promote and a btrfs no-op.
  - The btrfs `locator` is the filesystem UUID from the `<sysroot>/etc/fstab` root line, which GRUB needs for
    `search --fs-uuid`. The loader paths are in `KernelPair.fs_*`.
- **Argument types.** `kernels` takes a plain name, not a `StateId`, because `list`/`show` report the kernel of
  unmanaged objects too; publication passes `StateId.name`.
- **btrfs `parent`.** `parent` uses `btrfs subvolume show`: the Parent UUID is mapped to a listed name. A non-zero
  exit raises `StorageError` (D5). The window (D21) treats that as "no match", so it fails open as today
  (`dr-state:1032`).
- **Running state.** The core uses `DR_STATE` only when `running()` is `None`. `DR_STATE=none` skips `snapshot` and
  makes `prune` and `delete` refuse. If `running()` is `None` and `DR_STATE` is unset, `snapshot`, the apt hook,
  `prune` and `delete` exit 3 ("cannot tell the running state; set DR_STATE=<state>, or DR_STATE=none to skip the
  snapshot"), because the tool never guesses. `list`, `show`, `check` and `gen-menu` proceed without a running label.
- **Invariants.**
  - **S1** `snapshot` is atomic: on an exception, no object named `target` remains.
  - **S2** `delete` acts on one managed object. It raises `Refused`, naming them, when there are dependents or
    unexpected children.
  - **S3** `list` reports each name once, and only `@os-*`-shaped names.
  - **S4** `kernels` returns only pairs whose `vmlinuz-<v>` and `initrd.img-<v>` are both non-empty, newest first in
    GNU `sort -V` order (a unit test pins it against `sort -V`).
  - **S5** `running` never guesses, and `activate` is idempotent.

### 3.3 Boot

```python
@dataclass(frozen=True)
class BootTarget:
    state: StateId; title: str; kernel: KernelPair   # title "Debian @os-… (kernel <v>)"
    root_args: str; locator: str                     # from Storage
@dataclass(frozen=True)
class Inspection:
    published: frozenset[str]; stale_owned: tuple[str, ...]; violations: tuple[Violation, ...]
class Boot(Protocol):
    name: str                       # "grub" | "systemd-boot" | "zfsbootmenu" = ESP marker value
    supports_oneshot: bool
    def install(self) -> None       # install time only; writes the ESP marker and format file
    def capacity(self, keep: Sequence[BootTarget]) -> int | None    # D19; None = unbounded
    def payload(self, target: BootTarget) -> list[tuple[str, int]]  # (dedupe key, bytes)
    def publish(self, targets: Sequence[BootTarget], default: StateId) -> None
    def default(self, *, strict: bool) -> Selector | None
    def oneshot(self, *, strict: bool) -> Selector | None
    def set_oneshot(self, state: StateId) -> None   # Unsupported when not supports_oneshot
    def protected(self) -> frozenset[str]           # strict: every state the loader may boot without us
    def inspect(self) -> Inspection
```

`publish` publishes exactly `targets` (the core chose them, section 6), and `default` is always among them.

**Two selector read modes (H5).** An absent selector returns `None` in both modes.
- **Strict** is used by `protected()`, `delete` and `prune`:
  - a selector that resolves to an existing managed state yields it;
  - one that names an existing foreign entry yields nothing of ours;
  - anything else raises `BootConfigError` (exit 3). That covers unreadable, malformed, a duplicate grubenv key,
    suffix-less, a glob, an undecodable EFI variable, or a missing state.

  This keeps today's fail-closed `delete` rule and applies it to GRUB `prune` too (H2 asymmetry removed).
- **Publication** is used by every publish, including `snapshot` and the hooks:
  - A default that is unreadable, stale, foreign or malformed counts as "no configured default", and D17 falls
    through.
  - A one-shot that is unreadable or unmappable returns `known=False`: publish, but remove no stale owned entry
    (today's `oneshot_unknown`).
  - A one-shot whose name maps but whose state is missing returns `entry` set: that file is kept (B-4), and other
    stale entries may go.

**sdboot `LoaderEntryDefault` (finding 9).** This EFI variable is set by the `d` key or `bootctl set-default`, and it
overrides `loader.conf`.
- `default()` returns it when set, otherwise the `loader.conf` default, and `protected()` includes both.
- `set-default` writes `loader.conf` and, when the variable is set, clears it with `bootctl set-default ''`. The
  clearing form is verified in P-rollback-24's bootctl test (U8).
- `check` C3 judges the effective default, and C13 warns that the variable is set.

**Invariants.**
- **B-1** The default that `loader.conf` currently names resolves to an existing entry at every step. Its entry and
  pair are removed only after the new default's rename.
- **B-2** Every file goes through `atomic_write`. Scratch has one shape, `.dr-state.tmp.*`, only in our directories,
  and `publish` removes leftovers first.
- **B-3** Ownership is checked by a path lookup of the exact target. A file without the marker is never written,
  renamed or deleted. A clash raises `BootError` (exit 1) "… is not owned by dr-state", because it is a publication
  failure; exit 3 is kept for refusals of the request made before any change.
- **B-4** The entry of a pending one-shot target is never removed.
- **B-5** sdboot pairs are `<kver>-<sha256(sha256(vmlinuz)+sha256(initrd))>`, a shape matched case-insensitively.
  A copy is verified against the source hashes before its rename. A pair is removed only when no remaining owned or
  foreign entry references it (VFAT case-folding and CRLF tolerated).
- **B-6** A repeated identical `publish` changes nothing and reads no kernel bytes (M5).
- **B-7** GRUB `set-default` republishes like sdboot instead of only editing grubenv (H2).

### 3.4 ESP marker (M1, B6)

`<esp>/EFI/dr/bootloader` holds exactly `grub`, `systemd-boot` or `zfsbootmenu`. `install-grub` now writes it too.
- **Missing marker or unmounted ESP.** Either one makes publication raise `Refused`.
  - In `snapshot` and the hooks this is a D16 warning: the checkpoint is kept and apt is not blocked.
  - In `gen-menu`, `set-default`, `boot-once`, `prune` and `delete` it is exit 3.
  - Today an unmounted ESP blocks apt (exit 100). The change is decided by H5: the checkpoint lives in storage, and
    blocking apt would also block the repair. `check` C6 reports the condition.
- **Deleted.** `/etc/dr-state.conf`, `BOOTLOADER=` and the conflict logic. The installer stops writing the file.
- **Unsupported.** GRUB PoC disks without a marker (B15 takes evidence from fresh disks).

### 3.5 ZFS fits unchanged (B8)

| Method | ZFS + ZBM behaviour |
|---|---|
| `list` | Datasets `<pool>/ROOT/os-*` are `live`; snapshots `<ds>@os-*` are `checkpoint`. After `activate(X)` promotes `ROOT/X`, `ROOT/X@X` is the origin of the previously live BE: a snapshot whose leaf equals its own dataset's leaf is not a separate state. |
| `snapshot` | `zfs snapshot -o org.dr-state:parent=<source> <source-ds>@<target.bare>`: atomic, O(1). No send/receive, staging, registry or orphans. |
| `activate` | `zfs clone -o canmount=noauto -o mountpoint=/ -o org.zfsbootmenu:commandline=<source's local value> <holder>@<bare> <pool>/ROOT/<bare>`, then `zfs promote`. Name = ID. Clones do not copy local properties, so they are set. |
| `delete` | Checkpoint: `zfs destroy <holder>@<bare>`, `Refused` if it has clones. Live `X`: `Refused` while it holds other managed checkpoints or children. Its own `ROOT/X@X` is destroyed first if it has no clones; otherwise `Refused`, naming the dependent BE (the one `X` was activated from). Then `zfs destroy ROOT/X` without `-r`. |
| `kernels` | Running BE: `/boot`. Other BE: read-only temporary mount inside `session()`. Checkpoint held by the running BE: `/.zfs/snapshot/<bare>/boot` (U5). Held by another BE: read-only temporary mount of `<holder>@<bare>` inside `session()` (every BE's mountpoint is `/`). |
| `parent` / `running` / `locator` | The checkpoint's `org.dr-state:parent` (it survives promote), or the BE's origin / `findmnt -n -o SOURCE --target /` mapped to `@<leaf>` / the dataset. |
| `space` | Pool `free` plus `freeing`, re-read after each destroy (D20c). |
| Boot `publish` | Writes no menu files: ZBM discovers BEs. Sets `zpool set bootfs=<default locator>` when it differs. Checkpoints stay reachable in ZBM's snapshot menu; they are not entries. |
| `default` / `protected` | The BE named by `bootfs` / `{that BE}`. |
| `oneshot` / `set_oneshot` | `None` / `Unsupported` (exit 4); `supports_oneshot = False`. `capacity`/`payload`: `None` / `[]`. |

The core calls `storage.activate(X)` before `boot.publish(..., default=X)`, so `set-default` on a checkpoint becomes
clone+promote, then `bootfs`.

Unknown U4: the dataset name ZBM gives a BE the operator clones from its own snapshot menu. The rule is fixed now:
- a non-`os-*` name is unmanaged;
- `set-default <ID>`, run inside that BE, renames it to `<pool>/ROOT/<origin bare>` and then proceeds (the rename
  mechanism is measured in P-rollback-12);
- until the rename, `snapshot` refuses there (D9).

## 4. Errors, exit codes, lock (H2)

| Exception (`errors.py`) | Exit | Meaning |
|---|---:|---|
| `UsageError` | 2 | Bad arguments. |
| `Refused`, `FormatError`, `BootConfigError` (strict mode) | 3 | A safety rule refused the request; nothing changed. |
| `Unsupported` | 4 | The backend lacks the operation (ZBM `boot-once`). |
| `CheckFailed` | 5 | `check` found an error-level violation. |
| `LockTimeout` | 6 | Another writer held the lock for 60 s. |
| `StorageError`, `BootError`, `CapacityError` | 1 | An operation failed after its own cleanup. |
| any other exception | 70 | Bug: a one-line message; a traceback only with `DR_STATE_DEBUG=1`. |

Writers take `flock(LOCK_EX)` on `<sysroot>/run/dr-state.lock` and wait up to 60 s, as today. `check` takes `LOCK_SH`,
so it never sees a writer's scratch. `list` and `show` take no lock.

## 5. Counter floor (M6, B4)

- **D13** `floor = max(counter, max sequence of managed names from Storage.list(), max sequence in sidecars)`. The
  allocator never returns a sequence at or below the floor. It reserves `floor + 1` durably, then snapshots.
- **D14** A missing, malformed, symlinked (D6a) or too-low counter is repaired under the lock, with a warning, by
  atomically replacing the path itself. Allocation continues, so the counter never blocks apt. B4's "refuse only
  below the floor" reads as "refuse to allocate below it, then repair" (agreed in review). Reaching 999999 raises
  `Refused`.
- **D15 Documented repeat.** If the counter is lost and the highest state is deleted with its sidecar, the floor is
  below the historical maximum and the next state repeats that sequence. Its full ID still differs (the timestamp),
  and order among existing states stays monotonic. Accepted (B4).

## 6. Snapshot versus publication, hook, retention (H5, M3, L2, B5, B7)

- **D16 Snapshot success (H5).** `snapshot` succeeds once `storage.snapshot` has returned and the sidecar is durable.
  If the sidecar write fails, the object is deleted and the exit is 1. Publication afterwards is best-effort, in
  publication read mode. These become a warning with exit 0: `StorageError` from `kernels`, `BootError`,
  `CapacityError`, `BootConfigError`, `Unsupported`, and a publication-phase `Refused` (missing marker, unmounted
  ESP, newer ESP format). The sequence stays consumed. A newer control-directory format still refuses the snapshot
  itself (exit 3).
- **D17 Degraded menu (H5).** Every publication uses one selection. Candidates are the managed states with a kernel
  pair, minus a state being deleted. The order:
  1. the default: the requested state; else the configured default (publication mode) if it is a candidate; else the
     running state if it is one; else the first candidate in sort order (today's rule);
  2. the running state;
  3. the pending one-shot target;
  4. the other candidates, highest sequence first.

  Items 1 and 2 are mandatory. Items 3 and 4 are added greedily: each is kept if D19's fit still holds, else
  skipped, and the walk goes on to older states, which may share a pair. If the mandatory set does not fit,
  the selection-time `CapacityError` leaves the old menu: a warning in `snapshot` and the hooks, exit 1 in
  `gen-menu`/`set-default`. After publication has begun, a copy-check `CapacityError` leaves the menu already shrunk
  by D19 phase 1; nothing new is written and B-1 still holds. The sort order of the menu does not change. `list` and
  `check` show unpublished states.
- **D17a Stale removal.** Owned entries that are not in the target set are removed, except the current default's
  entry and the pending one-shot's entry. With an unknown one-shot (`known=False`), no stale owned entry is removed.
- **D18 One snapshot hook (M3, B7): `DPkg::Pre-Install-Pkgs` alone.** It runs once per apt commit and carries the
  plan. The Pre-Invoke snapshot and its `|| exit 100` are deleted. Post-Invoke `gen-menu || true` and the
  `zz-dr-state` kernel hooks stay; they only republish.
  - **Hook line.** `DPkg::Pre-Install-Pkgs { "/usr/local/sbin/dr-state-apt-hook"; };`, with
    `DPkg::Tools::Options::/usr/local/sbin/dr-state-apt-hook::Version "2"` and `::InfoFD "3"`.
  - **Wrapper.** `dr-state-apt-hook` is a POSIX `sh` script in `files/`:
    `[ "${DR_STATE:-}" = none ] && exit 0; [ -x /usr/local/sbin/dr-state ] || exit 0;
    exec /usr/local/sbin/dr-state apt-pre-install-pkgs`.
  - **Why a wrapper and not the reviewer's inline `[ … ] ||` line.** APT looks up
    `DPkg::Tools::Options::<first word of the command>::Version`/`InfoFD` (`apt-pkg/deb/dpkgpm.cc`,
    `RunScriptsWithPkgs`). A line that starts with `[` therefore gets protocol 1 on stdin and loses the plan. The
    wrapper keeps the guard in `sh`, so `DR_STATE=none apt-get …` works even with a broken `python3`.
  - **Exit codes.** apt aborts on non-zero: 1 (no checkpoint), 3 (unmanaged or unknown running state, newer
    control-directory format), 6 (lock), 70, and a failed interpreter exec. Exit 0 covers success, every D16 warning,
    a window dedupe and `DR_STATE=none`. A missing or unparseable plan still snapshots, with
    `apt_actions = "package plan unavailable"`.
  - **Proof, before P-rollback-22 builds on it.** It runs in a capped Debian 13 container, since APT hook dispatch is
    userspace only:
    - With the installed hook line and a logging Version-2 hook behind the wrapper, there is exactly one call
      containing `**REMOVE**` on fd 3 for each of `apt-get remove -y <pkg>`, `apt-get purge -y <pkg>` and
      `apt-get autoremove -y`.
    - With a failing `dr-state` stub, `DR_STATE=none apt-get remove -y <pkg>` succeeds and plain `apt-get` aborts.

    P-rollback-40 repeats the first part as scenario `apt-removal-only` on an ISO-built disk with
    `DR_SNAPSHOT_MIN_INTERVAL=0`: one checkpoint whose `apt_actions` names the removal.
  - **Fallback if the proof fails.** Pre-Invoke snapshots and writes the ID to `/run/dr-state/pending`,
    Pre-Install-Pkgs annotates it, and Post-Invoke clears it.
- **D19 Capacity (L2, F13).** sdboot only; for GRUB and ZBM, `capacity` is `None`.
  - **Payload.** `(pair id, vmlinuz + initrd bytes)` and `(entry id, 4096)`.
  - **Fit for selection** (D17, D20b): `need(S) = Σ bytes of the distinct payload keys of S + M`, with M = 8 MiB
    fixed. `capacity(S) = statvfs free + bytes of owned entries and owned pairs`, excluding:
    - the pending one-shot's entry and pair;
    - the current default's entry and pair when they are not in S (they stay until the new default is published);
    - pairs that any foreign entry references.

    Pairs of S already on the ESP count on both sides.
  - **Publication phases.**
    1. **Shrink.** Remove the owned entries that D17a allows, then the pairs that no remaining entry and no target
       references.
    2. **Copy check.** Re-read statvfs. `new bytes + M ≤ free` must hold. New bytes are absent or repaired pairs at
       full size, rewritten entries, and `loader.conf`. In-place rewrites get no credit, because `atomic_write` holds
       both copies until the rename. On failure, `CapacityError`, and nothing more is written. The shrunken menu
       remains; the default named by the existing `loader.conf` still resolves, so B-1 holds.
    3. **Write.** Copy the pairs, write the entries, rename `loader.conf` to the new default, then remove the old
       default's leftovers.
  - **Why B-1 holds.** The default that `loader.conf` names resolves at every step.
  - **Deleted.** The cluster, directory-record and staging models (`:622-703`, `:874-916`).
  - **Evidence.** P-rollback-40's ESP-exhaustion scenario covers "kernel upgrade on a full ESP" and checks the 8 MiB
    (U7).
- **D20 Retention (B5).** Victims are the managed states that are not running, not in `protected()` (strict) and not
  pinned, oldest sequence first. Victims are deleted one at a time until all three hold:
  - (a) the unpinned managed count is at most N (default 10 on every backend; `--keep N` overrides);
  - (b) the remaining candidates fit (D19);
  - (c) `space().free ≥ max(2 GiB, 5 % of size)`, re-read after each delete rather than summed in advance.

  btrfs reclaim latency is unknown (U2), so on btrfs (c) only warns until it is measured. `--dry-run` evaluates (a)
  and (b), and reports (c) as "evaluated live". If the victims run out first, `prune` warns.
- **D21 Window (M3, M4).** The dedupe window stays policy only: `DR_SNAPSHOT_MIN_INTERVAL`, default 900 s, 0 disables
  it. It is keyed on sidecar `source == "apt"` and `storage.parent(c) == running`, never on a slug. Inside the window,
  plan lines are appended to the newest such checkpoint.
- **D34 No automatic retention (operator 2026-11-24, Q1: "not yet").** `prune` stays manual, and `check` C9 warns
  about unpublished states. `snapshot` and the hooks never delete a state. Revisit after P-rollback-40's
  ESP-exhaustion evidence.

## 7. Metadata and hash cache (M4, M5)

- **D22 Sidecar schema 2 (M4).** `<control_dir>/metadata/<name>.meta` keeps the schema-1 encoding (`schema=N`, then
  `key=base64(value)` lines) and adds `kind` (`live`|`checkpoint`) and `pinned` (`0`|`1`).
  - A schema-1 sidecar reads as `kind=checkpoint, pinned=(source == install)`, so nothing migrates.
  - `initialize-state` writes `kind=live, pinned=1`. `snapshot` writes `kind=checkpoint`. Any locked verb sets the
    running state's sidecar to `kind=live`.
  - The effective kind is storage's when it is not `None` (ZFS), else the sidecar's, and always `live` for the
    running state. `list` shows live states as "contents changed since capture".
  - New verbs: `pin STATE`, `unpin STATE`, `snapshot --pin`. Retention never selects a pinned state; `delete`
    ignores `pinned`. The slug-`install` rule is deleted.
- **D23 Hash cache (M5).** `<control_dir>/cache/kernel-hashes.json`, mode 0600, atomic, written only under the
  writer lock.
  - Record: `{"v": 1, "files": {"<dev>:<ino>": {"size", "mtime_ns", "ctime_ns", "sha256"}}}`, keyed on
    `(dev, ino, size, mtime_ns)`.
  - `ctime_ns` is an extra validator. btrfs snapshots share inode numbers, subvolume `st_dev` values can be
    renumbered across mounts, and an in-place rewrite changes ctime.
  - An entry is invalid when any field differs. Entries that a full publication did not use are dropped. An
    unreadable or other-version file counts as empty, never as an error.
  - ESP pairs are trusted by their content-addressed name plus both sizes. A fresh copy is hashed once before its
    rename (B-5). `check --verify-esp` rehashes every pair.

## 8. `check` and JSON (H4, B12)

- **D24 `check` (H4).** Read-only under `LOCK_SH`, strict selector mode. Boot rules come from `Boot.inspect()`, the
  rest from `check.py`.
  - **Errors (exit 5):**
    - C2: an owned entry references a missing or empty pair file (GRUB: a missing kernel file);
    - C3: the effective default does not resolve to a published managed state;
    - C4: a pending one-shot names no owned entry;
    - C5: `.dr-state.tmp.*` scratch is left in an ESP or control directory;
    - C6: the ESP marker or a format file is missing, invalid or newer;
    - C7: the counter is missing or below the floor.
  - **Warnings (exit 0):**
    - C1: a stale owned entry;
    - C8: a managed state without a sidecar;
    - C9: a managed state with a kernel but no entry (degraded menu; ZBM checkpoints exempt);
    - C11: a sidecar whose `state` differs from its file name, or one without a state;
    - C12: ZFS BE properties (P-rollback-42);
    - C13: `LoaderEntryDefault` is set.
  - **Info:** C10, unmanaged objects.
- **D25 Versioned JSON (H4, B12).** Fields can be added freely. A removal or rename bumps the major version. `null`
  means unknown.

```json
{"schema": "dr-state.list/1", "format": 1, "storage": "btrfs", "boot": "systemd-boot",
 "running": "@os-…", "default": "@os-…", "oneshot": null,
 "states": [{"name": "@os-…", "sequence": 2, "created_utc": "2026-11-18T12:01:00Z", "slug": "apt",
   "kind": "checkpoint", "pinned": false, "contents_changed_since_capture": false, "source": "apt", "reason": "apt",
   "note": "", "parent": "@os-…", "metadata": "recorded", "kernel": "6.12.117+deb13-amd64", "entry": "os-….conf",
   "published": true, "running": false, "default": false, "oneshot": false}],
 "unmanaged": [{"name": "@os-0001", "kind": null}]}
```

- **`show` and `check` shapes.** `show S --json` returns `{"schema": "dr-state.show/1", "state": {<list fields>,
  "os": {...}, "packages": [...], "apt_actions": [...]}}`. `check --json` returns `{"schema": "dr-state.check/1",
  "ok": bool, "violations": [{"code": "C3", "severity": "error", "subject": "...", "detail": "..."}]}`.
- **Guest scenarios (B12, P-rollback-26)** assert on these, on `bootctl --json`, on `/proc/cmdline`, on boot_id and
  on packages, never on file shapes.

## 9. Global format versions (M2, B1)

- **D26** `<control_dir>/format` and `<esp>/EFI/dr/format` each hold one integer. An absent file means format 1, which
  is exactly today's layout, so nothing migrates; the first locked verb writes `1`. A malformed value, or one greater
  than `dr_state.FORMAT`, refuses writes (`FormatError`, exit 3). For the ESP file that means publication only (D16).
  Reads continue with a warning. A future bump is applied by the newer tool under the lock.

## 10. Payload as files (M10)

- **D27** `scripts/debian-rollback/files/` is the target image. Installers and the ISO builder copy it verbatim with
  modes, and the guest driver reads it.
  - **Contents:**
    - `usr/local/sbin/dr-state` (the launcher) and `usr/local/sbin/dr-state-apt-hook` (D18);
    - `usr/local/lib/dr-state/dr_state/` (the only source tree; unit tests import it there);
    - `etc/apt/apt.conf.d/80dr-state`;
    - `etc/kernel/{postinst.d,postrm.d}/zz-dr-state`.
  - **Timing.** The runtime tree lands in P-rollback-20, but installs switch to it only in P-rollback-21, for both
    variants at once (D33). The runtime tree and target hooks now ship as files (P-rollback-21 and P-rollback-25).
  - **Installer.** The two-step install packs `$HERE/files` (ISO, seed) or `$HERE/../files` (repository) into `usr/`
    and `etc/` tar archives whose members are explicitly owned `0:0`, then extracts them as root with `--same-owner`.
    The `usr/` archive lands at the runtime's existing early point; the `etc/` archive lands only after
    `gen-menu --default`. Each archive omits directory members, so `/`, `/usr`, `/usr/local`, `/etc` and every other
    pre-existing target directory keep their owner and mode. The installer prints the initial state name with
    `printf` instead of calling `dr-state _state-name`; `initialize-state` stays the only validator, so the Live
    system needs no Python.
  - **Seed and ISO ownership.** The seed ISO is packed with `-uid 0 -gid 0`; the remastered ISO applies
    `-chown_r 0 /dr/files -- -chgrp_r 0 /dr/files --` before commit. A non-root builder therefore cannot stamp its
    uid on a root-executed runtime.
  - **Hot patching.** The dev-only hot-patch helpers in dr-serial ship the whole `files/usr/local` tree and are
    refused with `DR_ACCEPTANCE=1` (P-rollback-25).

## 11. Parity strategy (L1, R3)

- **D28 Injection (L1).** `DR_STATE_TEST_*`, `TEST_MODE` path rewriting and every `_xxx` verb are deleted. Three
  mechanisms replace them:
  1. **`--sysroot DIR`**, a documented global option. Every absolute path resolves under `DIR`: `/.top`, `/boot/efi`,
     `/run`, `/etc/os-release`, `/etc/fstab`, `/proc/cmdline`, `/proc/sys/kernel/osrelease` (the kernel at snapshot,
     the same value as `uname -r`) and `/sys/firmware/efi/efivars`. dr-state mounts nothing there and skips
     mountpoint checks, because the operator assembled the tree; this also serves a mounted target. Programs still
     come from `PATH`.
  2. **Python unit tests** (stdlib `unittest`, `scripts/debian-rollback/tests/`) import the modules with fake
     `Storage`, `Boot`, `Runner`, filesystem (including a case-insensitive one) and `Clock` objects. Publication-order
     fault injection lives only there (keep list).
  3. **PATH stubs** for `btrfs`, `grub-editenv`, `bootctl`, `dpkg-query` and `findmnt`, as black-box boundaries.
- **D29 Bash suites stay the black-box oracle.** The case and harness edits below are the complete list, and the
  P-rollback-20/21 commit messages carry it.
  - **Binary per case.**
    - Until P-21, `workflow_run` branches on `$DR`. For `DR_BASH=poc/dr-state` it sets the old environment; for
      `DR_PY=files/usr/local/sbin/dr-state` it runs `"$DR" --sysroot "$ROOT" CMD…` (FREE and CLUSTER are ignored).
    - The boot-agnostic cases are wrapped in one harness function, `boot_agnostic_cases`, which P-20b calls twice:
      `DR=$DR_BASH FIXTURE_BOOT=systemd-boot` and `DR=$DR_PY FIXTURE_BOOT=grub` (D33). The set is
      `workflow-snapshot-reason-*` plus the overlong case, the `-metadata`/`-show`/`-list`/`-list-long`/
      `-delete-metadata` cases, `workflow-sequence-not-reused-after-delete`, `workflow-initialize-state*` and
      `workflow-apt-plan-*`.
    - All other cases use `DR_BASH` until P-21. From P-21 every case uses `DR_PY`, the function runs for both
      markers, and the bash branch is deleted.
  - **Fixture.**
    - `workflow_fixture` keeps `esp/` and `top/` and adds `boot/efi -> esp` and `.top -> top` symlinks.
    - It writes the ESP marker from `$FIXTURE_BOOT` instead of `etc/dr-state.conf` (bash accepts the marker alone).
    - It puts a `findmnt` stub that prints `btrfs` on `PATH`, plus the `grub-editenv` stub for `grub` (items a, f).
    - `workflow_run` turns the inline `DR_STATE_TEST_{OS_RELEASE,OS_ID,OS_VERSION_ID,KERNEL,PACKAGES}` values into
      `etc/os-release`, `proc/sys/kernel/osrelease` and a `dpkg-query` stub, so the product never sees them.
    - The GRUB fixture drops its `.conf` state; its three `workflow-snapshot-grub-*` cases stay.
  - **(b)** `workflow-foreign-exact` keeps exit 1 and "is not owned by dr-state", because the parity contract wins
    (B-3).
  - **(c)** `workflow-prune`'s orphan becomes `9.9.9+deb13-amd64-<64 lowercase hex>`. `workflow-prune-vfat-case`
    uses `Foreign-Kernel-<64 UPPER hex>` with mixed-case entry paths; the pair-dir shape matches it (B-5), so only the
    foreign reference keeps it.
  - **(d)** `workflow-same-initrd` (`:1026-1027`) and `workflow_assert_entry` use a harness function
    `pair_id KVER KSHA ISHA` (the B-5 rule) instead of `_sdboot-kernel-id`. `workflow_snapshot_overlong_reason`
    (`:312`) and `workflow_assert_entry` derive the entry id as `${name#@}` (D11) instead of `_sdboot-entry-id`.
  - **(e) Parity contract.** These outputs are part of the contract:
    - `list` lines (`<state> (<status>) kernel=… created=… reason=… os=… note=… changes=…`);
    - `show` lines (`state:`, `status:`, `sequence:`, `origin-slug:`, `source:`, `reason:`, `os-at-snapshot:`,
      `running-kernel-at-snapshot:`, `key-package-versions:`, `apt-package-actions:`);
    - the BLS entry text;
    - `would delete <s>` and `deleted <s>`;
    - the stderr substrings "pending systemd-boot one-shot", "configured systemd-boot default", "running state",
      "requires --yes" and "is not owned by dr-state";
    - every exit code a kept case asserts.

    `--json` is additive.
  - **(g) Seed and ISO, from P-21.**
    - `build-seed.sh:23` copies `files/` to `$stage/poc/files/` instead of `poc/dr-state`.
    - `test-build-seed.sh:27` replaces its `cmp` with `diff -r files "$stage/poc/files"`. `:31`'s expected list keeps
      the fixed entries and adds `find files -type f`.
    - `iso/build-iso.sh:69` runs `cp -a files/. "$STAGE/dr/files/"`.
  - **`offline`** adds `python3 -I scripts/debian-rollback/tests/run.py`, once U1 is measured.
- **D30 Moved to Python unit tests** (same assertion, no CLI hook):
  - every remaining `_sdboot-*`, `_snapshot-name`, `_state-name` and `_apt-plan-summary` case (entry-text,
    kernel-id, entry-id, state-from-id, snapshot-name, state-name, apt-plan, entry-refs, orphan), with 12-hex fixtures
    changed to 64-hex;
  - `loader-replace`, `-add-default`, `-new`, `-duplicates` and `-malformed-*` (now on the strict reader);
  - the 14 `_prune-select` cases (`main-state-ignored` and `only-main-state` are restated with `pinned` in P-22);
  - 11 `_window-blocks` cases;
  - 9 fault-injection workflows: `workflow-order-{entry-write,entry-publish,loader-write,loader-publish,stale-delete}`,
    `workflow-order-loader-publish`, `workflow-prune-order-loader-publish`, `workflow-prune-order-stale`,
    `workflow-delete-menu-failure`;
  - `workflow-collision`, as `test_sdboot_refuses_case_alias_foreign_entry` on a case-insensitive fake filesystem;
  - `workflow-conf-state-stale-selectors`, as `test_sdboot_publish_unknown_oneshot_keeps_stale_owned_entries`;
  - `workflow-conf-state-stale-cleanup-after-selector-clear`, as
    `test_sdboot_publish_cleared_oneshot_removes_stale_owned_entry`;
  - `clean-scratch`, restated for B-2 as `test_publish_removes_only_own_scratch`. It seeds `.dr-state.tmp.*` in
    `EFI/dr/kernels`, `loader/entries`, `loader/`, `EFI/dr/` and the control directory, plus look-alikes that must
    survive, including the old bash shapes `.tmp.Ab12Cd`, `.dr-state-entry.new.123`, `loader.conf.new.123`,
    `bootloader.new.123` and `.vmlinuz.tmp.123`. Bash-era scratch is foreign now: it is never cleaned, and C5 does
    not flag it. `workflow-cleanup` stays black-box, re-seeded the same way.
- **D31 Removed cases (93).** Each is deleted without replacement, or its behaviour changes by decision; a
  replacement is named where one exists. The P-rollback-20/21/22/26 commit messages repeat the list.
  - **M1/B6 (10):** `backend-default`, `backend-explicit-grub`, `backend-conf-systemd`, `backend-both-agree`,
    `backend-conflict`, `backend-source-present`, `backend-source-absent`, `backend-source-empty-config`,
    `backend-empty-config-file`, `backend-empty-config-message`. The five marker cases move to unit tests.
  - **H1, ids (7):** `entry-ids-case-collision`, `entry-ids-case-message`, `entry-ids-conf-suffix`,
    `entry-ids-unsafe-state`, `snapshot-name-overlong-conf-boundary`, `workflow-entry-id-240`,
    `workflow-entry-id-241`.
  - **H1, `.conf` states on GRUB (5):** `workflow-grub-conf-{gen-menu,list,set-default,gen-menu-default,prune}`.
    Replaced by `workflow-unmanaged-listed` (P-20b): `@os-0001` is listed as unmanaged, is not in the menu, and
    `delete` exits 3.
  - **H1, `.conf` states on sdboot (9):** `workflow-conf-state-{gen-menu,list,set-default,default,boot-once-invalid,
    boot-once-valid,prune}`, `workflow-conf-running-prune` and `workflow-conf-running-snapshot`. The last is replaced
    by `workflow-unmanaged-running-snapshot` (P-20b): running `@os-0001` makes `snapshot` exit 3 with "unmanaged",
    and no new state is created and the counter is unchanged.
  - **H1, suffix-less selectors and selector API (8):** `loader-migrate-suffixless`, `workflow-migrate-default`, the
    suffix-less iteration of `workflow-list-default-*`, `workflow-prune-protect-suffixless`,
    `workflow-oneshot-suffixless`, `loader-suffix-arg`, `loader-bad-default-id`, `loader-empty-without-default`.
    `workflow-oneshot-suffixed` stays and exits 0: its one-shot names a missing state, so its file is kept (B-4).
  - **L1 (2):** `clean-scratch-requires-test-mode`, `fail-step-requires-test-mode`.
  - **L2/H5 (25):** `space-{needed-less,needed-equal,needed-wins,zero-needed,no-free,bad-needed,negative-needed,
    bad-free,refusal-message,malformed-message}`, `byte-length-{ascii,utf8}`, `alloc-sum-{rounds,zero,bad-size}`,
    `dir-alloc-{empty-tree,shared-parent,existing-parent,all-existing,loader,nondirectory}`,
    `workflow-space-{baseline,reserve,over,under}`. Replaced by D19 unit tests in P-21 (selection fit, shrink-first
    and copy check) and degraded-menu tests in P-22.
  - **M6 (3):** `workflow-counter-{missing,malformed,symlink}-fails-closed`. Replaced by repair-and-proceed cases in
    P-22.
  - **`test-dr-state-window.sh` (1):** `old-format-does-not-block`.
  - **`test-install-btrfs-bootloader.sh` (4):**
    - `sdboot-config` and `grub-default-no-systemd-config` (M1);
    - `sdboot-marker`, which greps the bash source; replaced by a `--sysroot install-systemd-boot` marker check;
    - `apt-preinvoke-fallback` (B7, P-22), which is replaced by a check for the D18 hook line.
  - **`test-dr-serial.py` `main()` (19, P-26, B6 mirror):**
    - `detect_backend`: "grub config only", "systemd-boot config only", "config with comments and keys", "both
      agree", "both agree on grub", "empty config value", "unknown config value", "sources conflict", "duplicate
      BOOTLOADER", "malformed config line", "embedded CR is not a line break";
    - `guest_backend`: "config grub only", "config systemd only", "both agree", "both agree on grub", "conflicting
      sources", "empty config", "unknown config", "duplicate config key".

    The `parse_efi_probe` fixture uses the marker instead.

  Every other workflow case stays black-box and must pass against Python under the D29 edits. That includes
  `workflow-oneshot-suffixed`, `workflow-foreign-exact`, boot-once, prune, the invalid-loader cases,
  `workflow-conf-protected-prune` and the delete cases.

## 12. Boot path, order, unknowns (R4)

- **D32 No Python on the boot path (R4).** GRUB reads `grub.cfg`/`grubenv`, systemd-boot reads `loader.conf`, the
  entries and the pairs, and ZBM reads pool properties. No dr-state unit runs at boot.
  - **Recovery** is the boot menu: an older entry, or a BE or snapshot in ZBM. `set-default` and `delete` then run in
    the recovered state, on its intact `python3` and the shared runtime.
  - **apt with a broken interpreter.** In a state whose `python3` is broken, apt still works with
    `DR_STATE=none apt-get …`, because the D18 wrapper exits 0 in `sh` before Python starts.
  - **Broken runtime.** If the runtime itself is broken, the menu still boots, and ROLLBACK-RECOVERY documents the
    manual one-liners (`grub-editenv … set saved_entry=`, editing `loader.conf`, `zpool set bootfs=`) and the
    `DR_STATE=none` escape.
- **D33 Order and size** (rough LOC, production + tests):

| Package | Content | Size |
|---|---|---|
| P-20a | `paths`, `errors`, `lock`, `ids`, `metadata` (sidecar, floor, format), `apt`, retention count selection, `cli` with `--sysroot`/`--json`, launcher, `files/` runtime tree, unit tests | M, ~900 + 700 |
| P-20b | `storage/btrfs`, `boot/grub`, core verbs, `install-grub` marker; harness: the binary branch and `boot_agnostic_cases` run against Python with the grub marker, the GRUB cases and the two new unmanaged cases; GRUB/backend/window/prune/install removals. Installs stay bash. | M-L, ~800 + harness |
| P-21 | `boot/sdboot` (two selector modes, `LoaderEntryDefault`, the D19 phases, inspect, efivars one-shot, `bootctl`), every case on Python, `poc/dr-state` deleted, installers/seed/ISO switched to `files/` for both variants (D29 g) | L, ~750 + 650 |
| P-22 | D16-D21 (degraded menu, one hook + wrapper + container proof, capacity and pool terms), D22 (`kind`, `pinned`, `pin`/`unpin`), counter acceptance, hook payload | M, ~400 + 400 |
| P-23 | `hashcache`, sdboot fast path, `check --verify-esp` | S, ~150 + 150 |
| P-24 | `check.py`, the three JSON schemas, the bootctl oracle test (if P-rollback-11 says yes), the `bootctl set-default ''` check (U8) | M, ~450 + 300 |

P-rollback-25 (hooks to `files/etc`) can follow P-20 in parallel; landing it before P-22 lets P-22 edit files, not
heredocs. P-rollback-42 implements `storage/zfs.py` and `boot/zbm.py` against section 3 without changing it.

**Unknowns to measure.**
- U1: the `python3` version in tester-unified (first step of P-20).
- U2: btrfs free-space reclaim after `subvolume delete`, measured in a guest, never on a host loop device (P-40).
- U3: Pre-Install-Pkgs on removal-only runs, and the wrapper guard (the D18 proof).
- U4: the name of a BE cloned from ZBM's snapshot menu, and renaming a mounted root (P-12).
- U5: `.zfs/snapshot` access on the running BE (P-42).
- U6: which of several `default` lines systemd-boot honours (P-11); `protected()` covers all of them until then.
- U7: whether the 8 MiB margin holds (P-40).
- U8: `bootctl set-default ''` clears `LoaderEntryDefault` (P-24).
