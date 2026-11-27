# Debian install v2 CLI and wizard design

This note explains why the operator interface is shaped as it is. The
supported commands and copyable recipes are in
[`CONSUMERS.md`](CONSUMERS.md); the product feature surface is in
[`../README.md`](../README.md).

## Verbs represent operations

The CLI exposes `wizard`, `build-iso`, `install`, `resume`, `status`, `verify`,
`plan`, `disable-stage2`, and `build-customscript` as verbs rather than one `--action`
option. Each operation has its own help, accepted options, risk declaration,
examples, and handler. Shared parser behavior—identity/version output, grouped
help, diagnostics, `--yes`, `--debug`, output modes, progress behavior, and
Ctrl-C handling—comes from `libraries/cli-extended`.

The top-level catalog uses the registry's overall CLI description to explain
the workflow, then lists verb names with aligned short summaries. Full argument
and config-source syntax stays in per-verb help, where it can be read alongside
the corresponding option descriptions without crowding the operation map.

This prevents unrelated options from appearing valid for every operation and
lets help show the workflow directly. `install` is destructive and requires a
confirmation unless `--yes` is supplied; `--dry-run` records planned actions
without executing host commands. `--yes` accepts the operation’s confirmation,
not missing settings or wizard answers.

## Configuration has one authority

`Config` plus `load_config()`/`validate_config()` in `config.py` is the source
of truth for accepted fields, defaults, and validation. The wizard describes
which fields belong together and how to prompt for them; it does not re-create
the schema in a separate form model. It builds a candidate `Config`, runs it
through the same loader, and only writes a validated result.

`save_config()` writes atomically with mode `0600`, refuses symlink targets,
and does not overwrite an existing file unless the operator confirms that
write. Secret fields are hidden while prompting and replaced with a status
marker in the summary. A `--from-config` file is validated before it can seed
the wizard.

The wizard is section-oriented so operators can keep the existing settings
they do not want to revisit. All mutable `Config` fields are covered by
prompt metadata; only `schema_version` and the currently required
`fresh_install=true` are fixed by this v2 release.

Every install/config-inspection verb requires one explicit settings source:
`--config FILE` or `--config-json JSON`. There is no implicit target config to
mistake for the intended host. These alternatives are declared as one required
mutually-exclusive group in `cli-extended`, so parser enforcement, error help,
and the displayed synopsis all come from the same metadata rather than a
hand-maintained usage string.

## KSM scan budget

The installer enables ksmd, but KSM only merges anonymous pages in processes
that opt in. The ciu/run-gate shim supplies that opt-in for its workers, and
the measured gain for pytest workers is small, so the scan-time advisor uses a
deliberately low ceiling: `advisor_max_cpu=10` (percent of one CPU) and a
300-second target scan. Measurements showed the advisor CPU cap alone did not
bind on a small opted-in set: ksmd stayed near 15% of one core under load.
The kernel's default `sleep_millisecs=20` let a ~4 ms scan repeat about 40
times per second; increasing the sleep to 100 ms lowered ksmd to roughly 4%
under load and 0.05–0.08% idle. The defaults are installer-configurable
through `ksm_advisor_max_cpu_percent` (1–100),
`ksm_target_scan_time_seconds` (positive seconds), and
`ksm_sleep_millisecs` (positive milliseconds). The advisor CPU cap and target
scan time govern the eligible-page scan budget; the sleep interval limits
repeated wakeups when those advisor controls do not bind.

The advisor knobs are kernel-version dependent. A missing sysfs file produces
a warning for that tmpfiles entry; the installer keeps going and applies the
other available KSM entries. Since the drop-in is written at install time,
existing hosts must refresh it with the desired values by rerunning the
installer step, or update the entries manually and apply them with
`systemd-tmpfiles --create --boot /etc/tmpfiles.d/vbpub-ksm.conf` as root. The
`--boot` option is required to execute these `w!` rules.

## Control-plane memory protection

fleetd (FD-007) uses the sum of reported per-service `MemoryMin` values as the
control-plane reservation only when every unit of its six-unit list reports a
positive value; otherwise it uses a fixed profile constant. This installer
writes six per-service floors and one derived `system.slice` floor. The floors
are **measured**, not policy placeholders: the 2026-11-15 per-service hot-set
measurement on the two 8 GiB test hosts (report `control-plane-hotset-REPORT.md`;
evidence directory `control-plane-hotset`, outside this repository) derived each
floor as 1.25 × the service's measured hot working set, taking the max of the
two hardware flavors so one table serves both. The six floors total 345 MiB
(361,758,720 bytes), replacing the earlier 640 MiB placeholder table; the saving
comes almost entirely from journald (128 → 24 MiB) and ssh (32 → 10 MiB), while
docker and containerd stay close to their old values because the measurement
found they really do hot about that much. Values do not vary with installed RAM.

What is measured and what is inferred, per claim, is tabulated in the
[control-plane value table](CONTROL-PLANE-MEMORY.md#what-is-measured-and-what-is-inferred).
In short: the per-service hot sets and the v1001 floor ladder are measured (high
confidence); the r1002 1.25 × floor is inferred between measured 1.0 × and
1.5 × (r1002 carries a pre-existing `MemoryLow=1G` on `system.slice`, so the later
steps could not discriminate); the 32 MiB slice headroom is inferred, because
the other `system.slice` children were not measured individually; and the
containers-count effect is inferred, so the floors are a base, not a bound —
docker and containerd hot sets grow with the number of running containers.
Re-measure when the Docker or containerd version changes.

`MemoryMin` is written only in systemd drop-ins: `/etc/systemd/system/<unit>.d/`
for the six services and `/etc/systemd/system/system.slice.d/` for the ancestor
floor. It is a reclaim-protection floor; debian-install-v2 emits no `MemoryMax`,
`MemoryHigh`, or `MemoryLow` for these units or for `system.slice`. Its
post-install check compares `systemctl show -p MemoryMin <unit>` (and
`system.slice`) with the exact expected byte value.

### Why the ancestor slice has to carry a floor

`MemoryMin` on a service is limited by the protection its ancestors hand out.
Both measured hosts mount cgroup2 with `memory_recursiveprot` (restored by this
installer's `cgroup2-flags.service` if a remount strips it), so while
`system.slice.memory.min` is 0 the per-unit floors are inert: on v1001, docker
refaulted 231–251 MiB with 1.5 × hot-set unit floors and no slice floor, and 0–4
MiB with the same unit floors plus a slice floor. The installer therefore derives
the slice floor in code from the same per-service table — the sum of the floors
of the units that live under `system.slice` (`ssh`, `docker`, `containerd`,
`journald`, `psi-guard` = 341 MiB) plus 32 MiB headroom for the slice's other
children (`logind`, `dbus`, `udevd`, about 1–3 MiB hot each) = 373 MiB. It is
never a second hand-written number, and it follows the Docker pair's omission
when `run_docker_install=false`. `cgprofiled.service` runs under
`cgprofile.slice`, which already has `MemoryMin=128M`, so its floor does not
enter that sum (it is kept anyway, because fleetd's reserve rule needs a
positive per-service fact from every listed service). The slice drop-in sets
`MemoryMin` only; it leaves any pre-existing `MemoryLow` on `system.slice`
(for example mdt-cockpit host-setup's `MemoryLow=1G` on r1002) untouched.

### The documented ceiling above about 460 MiB

The same measurement found that the largest `system.slice` floors (608 and
585 MiB) produced journald/docker refaults that the 430–455 MiB steps did not,
with `memory.current` far below the floors, and could not explain them. The
installer therefore refuses a derived `system.slice` floor above 460 MiB, so a
future table change cannot silently cross into the unexplained range
(DIV2-BL-10). The current derived value, 373 MiB, stays inside it.

### The installed-RAM bound: 10% of `MemTotal`, refuse rather than cap

The second bound on the same floors is installed RAM (DIV2-BL-12). They were
measured on 8 GiB hosts; on a much smaller host the 373 MiB slice floor would
claim a large share of memory and the kernel would clamp it silently, so the
configuration would look applied while protecting nothing that matters.

- **What is charged.** The derived `system.slice` floor already contains its
  children's floors, so they are not counted twice; only the floors of units
  *outside* that slice are added. Today that is 373 MiB + `cgprofiled.service`
  4 MiB = 377 MiB (4.6% of an 8 GiB host).
- **The fraction.** 10% of `MemTotal`, twice the measured ratio. It accepts
  both 8 GiB test hosts even though they report a `MemTotal` below the nominal
  8 GiB (firmware/kernel reservations): 10% of that is 780–819 MiB. The
  smallest accepted host is 377 × 10 = 3,770 MiB of `MemTotal`.
- **Refuse, not cap.** Scaling measured floors down to fit a smaller host would
  produce numbers the measurement never supported — a floor below about 1.0 ×
  the measured hot set is the configuration the 2026-11-15 measurement showed
  failing. A refusal is loud and recoverable; a silent scale-down is neither.
- **Where it is enforced.** The configure step checks the bound *before* writing
  any drop-in, so a refusal leaves no partial set behind; verification checks the
  same bound against the host's real `MemTotal`, so a completed install that was
  restored onto a smaller machine does not read back as healthy. The charge is
  recorded in the `control_plane_memory_min` step detail.
- **The one documented escape hatch.** A genuine test guest (1–2 GiB VM) sets
  `VBPUB_ALLOW_CONTROL_PLANE_MEMORY_MIN_OVER_RAM_BOUND=yes`. It has no default,
  no other spelling is accepted, the refusal message names it, and it is written
  to `/etc/vbpub/bootstrap.env` only when set, so a resumed stage-two unit run
  re-checks the bound the same way instead of behaving differently from stage
  one.
- **Dry run.** `--dry-run` has no host to measure and keeps the 8 GiB planning
  assumption swap sizing already uses off-host, so planning on the cockpit's
  own 8 GiB container never refuses.

`MemTotal` comes from the installer's existing single reader
(`_memory_total_bytes()`, shared with automatic swap sizing); an unreadable
`/proc/meminfo` is an error, never a guessed host size.

What the bound cannot say: the measurement itself is an 8 GiB-host measurement,
so “fits under 10% of RAM” is the property this change can check. It is a sanity
bound against silent mis-configuration, not a validated support matrix for small
hosts.

The exact six-unit list is `ssh.service`, `docker.service`,
`containerd.service`, `systemd-journald.service`, `cgprofiled.service`, and
`mdt-psi-guard.service`. The mandatory units in the default DI2 profile are
SSH, journald, Docker, and containerd; the Docker pair is omitted when
`run_docker_install=false`. `cgprofiled.service` and `mdt-psi-guard.service`
are optional units installed by cgprofile and mdt-cockpit host-setup
respectively. DI2 writes each system-level drop-in whether or not an optional
unit is installed. Readback checks a loaded optional unit and skips it only
when systemd reports `LoadState=not-found` or `masked`; mandatory units missing
from systemd still fail verification. `system.slice` is built in and always
loads, so its derived floor is checked unconditionally.

For fleetd-reserve, an absent optional unit is now a stated fact, not a missing
one (FD-007c, landed): fleetd collects the unit's `LoadState`, and only a
declared-optional unit reporting `not-found` or `masked` contributes 0. A
mandatory unit with no positive value contributes this table's documented
default, so v1001 (no psi-guard unit) plans with the five measured floors it
does run plus zero for psi-guard, rather than with the profile constant. An
unknown load state is a missing fact, not an absent unit.
The Debian image provides SSH and journald; the installer configures them but
does not install their base packages. cgprofile's `cgprofiled.service` base
unit has its own `MemoryHigh`/`MemoryMax`; DI2's drop-in adds only `MemoryMin`.
The psi-guard unit has no other memory directives.

Still open: the per-container increment for docker/containerd floors
(DIV2-BL-11), the large-floor anomaly (DIV2-BL-10), r1002's inferred
confidence, and the retrofit for already completed installs. The installed-RAM
bound closed DIV2-BL-12; what it leaves open is whether a host that passes the
bound (for example a 4 GiB host) protects its control plane as well as an 8 GiB
host does — the floors were measured on 8 GiB only. An already
completed installation needs a separately scheduled, operator-approved in-place
drop-in application or reinstall (DIV2-BL-08). This change does not run against
hosts or add a retrofit operation.

## Cloud-init cache cleanup policy

`cloud_init_cleanup_mode` is part of the shared validated `Config`, not a
separate command-line switch, so it travels with saved profiles and the
provider-neutral custom-script bundle. `retain` is the default. The other
modes run only after all stage-two install actions complete; earlier stage-two
failures leave the cloud-init cache available for diagnosis. A failure in the
cleanup action itself is reported as a stage-two failure and may have partially
cleaned cloud-init state.

The policy deliberately separates removing secret-bearing user-data copies
from running `cloud-init clean`. `userdata-only` removes the raw `user-data.txt`
and cloud-init's parsed/intermediate copies `user-data.txt.i`,
`cloud-config.txt`, and `obj.pkl`. A clean resets cloud-init's run state, so its
stages run again on the next boot. `clean-all` adds `--logs --seed`, but not
`--configs all`: cloud-init does not preserve a pristine copy of files for
rollback. Its cleanup implementation removes specific paths it recognizes as
cloud-init-generated, without comparing them to later edits; it does not
restore a vanilla file. Removing those paths could therefore discard
configuration changes made by this installer. The mode also omits
`--machine-id` (intended for golden-image cloning) and `--reboot`.

`--seed` clears only cloud-init's guest-side seed cache. It cannot detach an
ISO from the VM, and a still-attached NoCloud ISO can supply the same
user-data again after a clean. Before cleanup, the installer writes the fixed
`/var/lib/vbpub/install-v2-complete` marker outside the cache. The fresh-install
entrypoint checks it before modifying state or host files, so a replayed seed
cannot start another partitioning run. Operators should still detach the seed
ISO after installation. `cloud-init clean` also executes any local cleanup hooks installed
under `/etc/cloud/clean.d/`; these remain the host administrator's
responsibility.

## Root and swap policies

The previous independent root policy and rule-sized `dev-host` layout created
two sizing authorities. On the Netcup test disks, swap sat directly behind a
40 GiB root and the CoW partition was carved after swap; later numbering put a
data partition behind swaps even though it sat earlier on disk. The single
`partitioning` plan removes that ambiguity: root, configured data, intentional
reserve, and swap have one physical order and one whole-disk accounting rule.
There is no permanent benchmark allocation. See the [feature overview](../README.md#disk-layout)
and [design contract](DESIGN-data-area-v2.md).

Root behavior comes from `partitioning.root`: `"keep"` retains current size,
an explicit GiB or percentage is the target, and `"*"` takes the available
allocation. This keeps the root target in the same plan as data and swap.
Static size-shape errors are caught when loading config or building a
custom-script bundle; disk-dependent fit is available before reinstall through
`plan --disk-size`. The offline resolver can take the real root start sector
and root partition number (`--root-start-sector`, `--root-number`) so its
physical table matches a cloud disk with preserved pre-root partitions. A
numeric fixed reserve is subtracted before percentage sizes are resolved;
`reserve_gb: "*"` remains incompatible with percentages. The plan table is
included in live `show_plan` output and in refusals when the disk facts suffice.

`iobench_raw_gb` is removed: absent or `false` is accepted for compatibility,
while an integer fails as `iobench_raw_removed` and must be deleted. For an
enabled ext4 benchmark, stage two temporarily replaces the contiguous tail
swap extents with one `vbpub-swap-span` partition. It uses the first tail
swap's number, the Linux filesystem type GUID, and the full extent from the
first swap's start through the last swap's end. Retained pre-root swaps remain
outside the span. After iobench succeeds or returns an advisory failure, the
installer runs `wipefs -a` again and writes the planned regular swaps. A
read-only probe before the first table write rejects non-swap signatures and
nested partition tables with manual recovery instructions. After package
setup, identity and quiescence are checked again, then `wipefs -a` clears the
span before the runner. No zeroing command is used: the marker stays in the
last MiB of the last swap, identity checks reject a stale marker, and a later
init overwrites it. A fresh install may replace another inactive blank-or-swap
tail layout that the benchmark-disabled path would replace; resumes require
planned swaps or the exact span. No tail swap produces a
`no_tail_swap_span` warning. Unreapable recovery is in the README.

On ext4 schema 1 and 2, the benchmark requires `swap_policy: "on"` and at
least one configured swap slot; otherwise it refuses as
`io_benchmark_requires_tail_swap_span`. Before each conversion, every affected
partition must be unmounted, absent from `/proc/swaps`, and blank or carry only
a swap signature. Each write uses the existing transaction: checksum-backed
pre-change table, geometry and data checks, sfdisk, scoped partx updates,
udev settle, and table readback. Every written swap fstab row has `nofail` and
`x-systemd.device-timeout=5s`.

The stage-two resume rule is derived from the live table and benchmark status:

| Live tail table | `io_benchmark` status | Resume action |
| --- | --- | --- |
| Planned swaps | not final | Write span, run, wipe signatures, restore swaps |
| `vbpub-swap-span` | not final | Run, wipe signatures, restore swaps |
| `vbpub-swap-span` | `success`, `warned`, or `skipped` | Wipe signatures, restore swaps |
| Planned swaps | final | Continue without a table rewrite |
| Any other state | any | Named refusal, no table write |

Dry-run records both table writes. Re-benchmarking an installed host remains
unimplemented, and btrfs benchmark/swap support remains outside this package.

Swap is sized independently from the pool, then placed last. `swap_policy`
has only `on` and `off`. `on` replaces the guest's configured swap setup with
planned GPT partitions: it disables all currently active swap, removes
configured swapfiles and swap logical volumes, and replaces `/etc/fstab` swap
entries. An eligible pre-root swap partition is retained; its capacity counts toward
both the requested total size and the configured total partition count.
`off` disables active swap, removes `/etc/fstab` swap entries and root-disk
swap partitions, but does not delete swapfiles or logical volumes.
Root-shrink safety is checked before swapfile cleanup.
There
is no `untouched` mode because it would leave the pool boundary unknown.
Automatic swap sizing uses actual block-device bytes and guest `MemTotal`:
`min(ceil(2 × RAM in GiB), round-half-up(20% of disk capacity in GiB))`.

Managed swap partitions deliberately have no GPT PARTLABEL. Their stable
identity is the partition GUID plus the swap filesystem label written by
`mkswap`: `vbpub-swap<N>`, or `vbpub-swap-pre<N>` for a retained pre-root
partition. `/etc/fstab` binds the GUID as `PARTUUID=`. Writing a second
PARTLABEL would create another naming authority without improving identity;
the preview therefore reports these rows as `Identifier: LABEL=...`, while GPT
named partitions report `PARTLABEL=...`.

Root shrinks use the offline ext4 hook after current-use and filesystem-minimum
checks, including the 256 MiB safety margin. Growth updates only the root
partition with `sfdisk -N`, then runs `partx -u` and checks the exact geometry;
it cannot absorb a planned reserve. Table updates are crash-safe and verified.
The plan orders partitions by sector extent and preserves complete partitions
before root, including the Debian genericcloud boot partitions numbered 14
and 15 before root partition 1. Unknown partitions after root are refused.

## The install-time iobench rung (`io_benchmark_profile`)

`run_io_benchmark` chooses *whether* the install measures the temporary swap span;
`io_benchmark_profile` chooses *how long* that measurement runs. The rungs are
the three iobench ladder profiles the install can afford inside a rebuild:
`veryquick` (about 3 min), `quick` (about 11 min) and `standard` (about
59 min). One closed vocabulary, one default, one timeout table
(`config.IO_BENCHMARK_OUTER_TIMEOUT_S`):

| rung | iobench budget | installer outer timeout |
|---|---|---|
| `veryquick` | 210 s | 270 s |
| `quick` (**default**) | 720 s | 780 s |
| `standard` | 3600 s | 3660 s |

The outer timeout is the rung's own hard budget plus 60 s for process startup
and result persistence; it exists to stop an unbounded wait, and it is not the
budget the rung plans against. The values are the operator decision of
2026-11-24 (WP-10 Q3) and supersede DIV2-BL-05's earlier 5/12/45 min ladder,
whose 45 min was *below* standard's own 3600 s budget and would have killed a
run iobench considers on time.

Why `quick` by default: on the temporary swap span veryquick's read numbers already
match the longer rungs, so the deciding gain is elsewhere — quick measures
`rseqiops` and `wseqiops` (about 6 % and 27 % off the true values) where
veryquick only estimates them, and the write numbers iogovmon corrects after
install either way. The operator's decision is recorded in `TODO.md`
DIV2-BL-05.

Every rung keeps its own state directory, `state_dir/iobench/<rung>`. The
runner records the rung in `run.profile` and refuses to reuse a directory
whose record names another rung (`state_dir_profile_mismatch`), so a quick
run can never overwrite a full standard measurement — and vice versa — just
because both were asked for by an install that shares one `state_dir`. (iobench's
own guard is narrower: only a `veryquick` run checks the directory it is about
to overwrite, and it refuses unless that record is already a `veryquick` one.)

A rung may finish without producing every one of the six io.cost
coefficients. That is not a failure and not a license to invent values: the
step records `warned`, names the missing keys, and io.cost stays unconfigured
because its boot unit needs the complete model. A partial model is never
written, and the previous `io-benchmark.json` is not reused for the keys a
rung did not measure.

## Btrfs-root validation and data subvolumes

The rollback ISO installs a GPT disk whose second partition holds one
single-device btrfs filesystem. Its top level (subvolume 5) contains the
`@os-*` states and the excluded data objects; the running state and the data
objects are separate subvolumes. v2 must configure this layout without
becoming a second layout creator. Root discovery therefore uses
`findmnt -n -o SOURCE,FSTYPE --nofsroot /`, which reports the backing
partition rather than a `/dev/...[/@os-...]` source, and separately asks
`btrfs filesystem show --raw` for the device list. Zero or multiple devices
fail closed. ZFS is detected only to refuse it with `root_fs_unsupported`;
IC-M6 owns ZFS-root support.

`schema_version: 2` adds the root-aware validation. Schema 1 remains loadable
and retains its legacy ext4 meaning. With a discovered btrfs root, the only
valid shape released by IC-M1a is root `"*"`, reserve 0, data kind `"none"`
or `"subvolumes"`, and
`swap_policy: "off"` with `swap_partition_count: 0`. The root type is never a
config key. The refusal codes are deliberately part of the contract because
the ISO layout facts, not generic schema errors, determine the remedy.
Swap remains refused as `btrfs_swap_needs_p3`. The `run_io_benchmark`
request is accepted when `iobench_raw_gb` is absent or false, then stage two
records the skipped benchmark detail `iobench_raw_unavailable_on_btrfs`.

For `data.kind: "subvolumes"`, v2 requires `/.top` to be mounted at
`subvolid=5` on the same backing partition as `/`; a tmpfs or another btrfs
filesystem is refused. It creates one top-level `@...` child per configured
volume and mounts it at the configured path. Existing subvolumes are accepted
when their NOCOW property matches. An empty existing subvolume that lacks
`+C` gets it reapplied; a regular file/directory at the expected path, or a
non-empty subvolume with a different NOCOW setting, is refused. NOCOW is set
with `chattr +C` immediately after creation and before any file can be
written. Each fstab row reuses the root entry's filesystem source and option
set, adds `user_subvol_rm_allowed`, and adds `subvol=<name>`. A pre-existing
mount at the target is accepted only after `findmnt` verifies its backing
source, filesystem type, target, and fsroot.

The ISO's excluded data mounts come from D18 plus `/usr/local`:
`/home`, `/root`, `/srv`, `/var/www`, `/var/log`, `/var/lib/docker`,
`/var/lib/containers`, `/var/lib/postgresql`, `/var/lib/mysql`, and
`/usr/local`. A new data subvolume mount at one of those paths or at an
ancestor would cover ISO-owned state and is refused; a descendant such as
`/srv/cow` is allowed. v2's existing `_check_mount` policy remains in force on
top of C9: a path at, inside, or above a protected tree such as `/boot`,
`/etc`, `/usr`, `/proc`, `/var/lib/dpkg`, or `/var/log` is refused, except
the documented `/var/log/journal` subtree; a path intersecting
`/var/lib/docker` or `/var/lib/containerd` is refused; and an exact or
ancestor path for the reserved mount points `/var`, `/var/lib`, `/srv`, or
`/home` is refused. A descendant such as `/srv/cow` remains accepted when no
other rule covers it. The subvolume work happens in stage two, after Docker
may already be running, so the live-service rule applies exactly as it does
to partition and LVM data mounts below.

IC-M1a keeps `user_subvol_rm_allowed` on each data-subvolume fstab row as the
brief requires. Btrfs applies most options per filesystem; IC-M1b owns adding
the effective option to the root mount via `install-btrfs.sh` `OPTS`, and
IC-M1c owns the live `/srv/cow` mount-option check. The data-row option is
harmless in the meantime.

The C9 swap row is superseded for btrfs by WP-10 P3. IC-M1a creates no
swapfile and no permanent raw benchmark partition. It reports the benchmark
and io.cost as skipped until the Q-A raw-result import lands. The ZFS-root
path, IC-M1b/c handoff pieces, and the VM/runner acceptance are also outside
this package; see the README's btrfs section for the exact split.

When the rollback layer is present and `dr-state list` shows a managed running
state, stage two first writes `phase=done` and `stage2_done`, then takes one
final `dr-state snapshot --reason host-setup`. The marker exists at snapshot
time, so the checkpoint contains a completed stage two; the snapshot remains
the last change to the OS state. Its failure removes the marker again and fails
stage two. Both v2 stage environments set `DR_STATE=none`; the snapshot call
uses `env -u DR_STATE` so it alone is visible to the rollback layer. A re-run
after `stage2_done` is a no-op, including no second snapshot. When the command
is absent or the root is unmanaged, v2 records a named skip rather than
inventing a snapshot.

For C4's rollback-to-the-pre-v2-initial-state boundary, v2's marker and saved
done state are inside `/var/lib` and therefore roll back with the OS state.
The unchanged install guard cannot prevent a replay there; only C4's deletion
of the two files in `/root/.vbpub`, which live in `@root` outside every state,
does.

## Data mount safety

Exact mounts on `/`, `/var`, `/var/lib`, `/srv`, and `/home` are refused.

The data filesystem is configured in stage two. By then stage one has already
written installer executables under `/usr/local/sbin` and, when enabled, has
installed and started Docker. The installer does not copy existing contents
onto a new data filesystem or stop and seed those services. Mounts under
`/usr/local` are refused as protected `/usr` state because they would hide
stage-one files. Mounts that intersect `/var/lib/docker` or
`/var/lib/containerd` are refused with a stage-order explanation. This keeps a
successful mount from hiding files or live container state that the installer
assumes is present.

Other protected trees include `/usr`, `/etc`, `/boot`, `/root`, `/tmp`,
`/proc`, `/sys`, `/dev`, `/run`, `/bin`, `/sbin`, `/lib`, `/lib64`, `/lib32`,
`/libx32`, `/var/lib/dpkg`, `/var/lib/apt`, `/var/lib/systemd`,
`/var/lib/cloud`, `/var/cache/apt`, `/var/log`, `/srv/tree-cache`, and
`/srv/tree-upper`. A proposed mount equal to, inside, or above one of those
trees is refused. `/var/log/journal` is the narrow exception: journal files
are generated service data, and a dedicated data volume there is a supported
persistent-journal pattern. `/var/log` itself and other log subtrees remain
protected.

The install-time journal remains on the root filesystem and is hidden when
stage two mounts the data filesystem there; it is not copied or seeded onto
the new filesystem. The mounted directory will not show those earlier files.
Source inspection confirms this mount and visibility behavior. Whether
`journalctl --flush`, restarting `systemd-journald`, or rebooting resumes
persistent writes on the mounted filesystem is **not verified** by the current
tests. Verify that behavior in the `r1-vm-real-commit` lane before claiming
recovery.

The runner finds its planned CoW partition by GPT PARTLABEL `MDT_COW`; it does
not carve or guess a partition number.
Copyable configs and offline previews are in
[CONSUMERS.md](CONSUMERS.md#disk-plan-and-offline-preview).

## Swap window is a recorded transaction

`tools/swap-bench-window.sh` exists because a post-install raw benchmark needs
swap space temporarily, but a failed or interrupted `swapoff` must never lead
to a wipe. The script binds the operation to one `PARTUUID` and size, records
the swap UUID and filesystem label, holds a single lock, and treats any live
non-zombie `swapoff` process as relevant. It does not assume that only its own
child can be running. The identity is re-read through `lsblk` and the low-level
`blkid -p` probe immediately before the single `wipefs`, then `end` recreates
the same filesystem label and UUID before archiving its state. To keep the
transaction scoped to one partition, `end` reads the unique fstab row for the
saved PARTUUID and invokes `swapon` for that device with the row's options.
Other configured swaps that were inactive remain inactive. It verifies the
restored identity, active state, and configured priority before archiving.

The wrapper does not edit fstab: a PARTUUID survives the wipe and re-`mkswap`.
It also does not add a degraded identity mode. All commands that can fail open
are required before a window opens, and the process runs with a fixed system
PATH. The reviewed QEMU rehearsal and earlier independent reviews are the
behavioral reference; the repo test exercises the no-PARTLABEL layout and each
new guard with fake roots and commands.

## Private VM seed ISO and moving profiles

`build-iso` is a local interactive operation because the final NoCloud seed
must contain per-host notification credentials and one or more persistent
root SSH public keys. A
generic public ISO or a repository profile must never carry those values. The
admin owns the resulting mode-0600 ISO; cloud-init also normally retains the
raw user-data inside the guest, so the existing shared `cloud_init_cleanup_mode`
config/profile key controls whether that cache is retained or cleaned after
successful stage two.

The ISO contains only the one-time launcher plus its private payload. It does
not freeze a copy of the installer: it points to a moving repository branch
and either a released profile ID, a locally embedded JSONC profile, or an
HTTPS profile URL (optionally protected by origin-scoped Basic Auth). Built-in
profile IDs are filenames under `profiles/`, validated as lowercase
letters/digits/hyphens and resolved from the branch at VM boot. `default`
intentionally consists of the schema version alone, so omitted values track
the selected channel's current `Config()` defaults. `vanilla` explicitly
disables optional configuration/services while retaining the core disk, swap,
and stage machinery. `run_zswap_config` and `run_cgroup2_config` are explicit
shared config/profile switches, default `true` to preserve current behavior;
the vanilla profile sets both false. `run_zswap_config` also gates the zswap
read-back health check. Required disk/swap transactions and stage orchestration
are core behavior, not optional host features. The profile files carry comments
explaining their use case; the identifier is a file lookup, not another
mini-language.

Python's stdlib JSON parser does not understand JSONC. Rather than add a
runtime dependency or accept the much larger JSON5 grammar, `profiles.py`
strips only `//` and `/* ... */` comments outside quoted strings and passes
the result to strict `json.loads()` and the existing config validator. Trailing
commas remain invalid. Profiles reject per-host notification credentials, the
temporary controller key, and `ssh_authorized_keys`. Persistent SSH keys are
collected as a list in the private ISO wizard, syntax-validated by the config
loader, and each is checked with `ssh-keygen` before ISO creation. The installer
adds them idempotently in stage one and never removes them during controller-key
cleanup. The existing controller key remains a separate ephemeral/retained
lifecycle.

The remote bootstrap uses a shallow Git clone with `--filter=blob:none`, then
sets a sparse path list before checkout and copies only those runtime files to
the install directory. The blob filter is what avoids transferring unrelated
file contents; sparse checkout alone only limits the worktree. Git tree/commit
metadata is still needed. Git is not assumed to be installed on cloud images,
so bootstrap preflight checks for it and installs it through the target's APT
sources when missing, before resolving or downloading the cli-extended wheel.
The repository fetch uses ordinary HTTPS Git smart transport and is not
GitHub-specific, but the server must advertise partial-clone blob filtering;
the bootstrap refuses if it would otherwise fall back to downloading all
blobs. The initial raw-file URL for the bootstrap and the cli-extended wheel
manifest are separate HTTPS endpoints. Their defaults use GitHub; the ISO
wizard lets an operator point either at a compatible mirror. A non-GitHub
repository URL by itself does not imply where those two HTTP artifacts live.

Known issue accepted by design: the first launcher fetches
`bootstrap-remote.py` from a floating branch before that script can fetch the
branch's Git tree. If the branch moves in that interval, the bootstrap file
and the checked-out installer/profile can come from adjacent commits. Once
the Git fetch resolves, installer code and released profiles are taken from
that one checkout. The release channel is intentionally moving, and this
small bootstrap handoff race is documented rather than pinning old ISOs.

## Real OS binaries are tested in the VM, not in the gate container

A test that executes a real low-level binary (systemd-tmpfiles, sfdisk, losetup, sysctl) is marked
`os_integration`. The gate containers are built on `tester-base`, which deliberately carries none of
those tools (its DESIGN-GUIDE has the placement rule), so they deselect the marker explicitly instead
of letting the tests skip quietly; the QEMU VM lane `r1-vm-real-commit` is where the binaries exist
and runs the marked tests with `VBPUB_REQUIRE_OS_INTEGRATION=1`, so a skip there fails. Docker-driving
tests are not marked: the TCG VM cannot carry CPU-heavy work.

## QEMU validation and host cgroup visibility

The Debian guest runs a separate kernel, so guest cloud-init or installer
commands cannot change the host's cgroup2 mount. Host diagnostics are a
separate boundary: `host-escape` invokes a privileged Docker helper to enter
host namespaces, then runs an `mdt doctor` postflight that repairs missing
cgroup2 mount flags. That repair makes the host safe again but means the
postflight warning is an observation, not attribution of the remounting
process. For the operational QEMU workflow and the 2026-10-14 incident
evidence, see the [VM usage/testing guide](../testing/vm/USAGE-TESTING.md#host-checks-when-cgroup2-flags-disappear).

The installer's own per-host measurement is a different thing from the manual
comparison below: step `run_cpu_benchmark` (default on, advisory) runs the shared
`tools/cpu_benchmark.py` once at the end of stage two and persists the host's
Geekbench scores in `/var/lib/mdt/host-facts/cpu-benchmark.json`. It replaces the
rejected v1 `run_geekbench` key (see the README, "Host CPU benchmark step"). Inside
the VM harness it measures the TCG guest's `QEMU TCG CPU`, so a harness install
needs the Browser egress allowlist below or the step `warned`s (which is also a
valid outcome to assert); never read those scores as the physical host's.

The ordinary VM test lane belongs in the admission-controlled
`dev-gates.slice`; a CPU benchmark there can measure the slice quota as much as
TCG overhead. The manual Geekbench comparison therefore has one narrow
exception: a host-shell launch can explicitly select the built-in
`system.slice`, after verifying through host `systemctl` and cgroupfs that it
has no CPU/memory ceilings and exposes every host CPU. That verifies the slice,
not every ancestor or the Docker transient scope: host-wide Docker backstops
may still apply, so inspect the runner's live cgroup. The harness refuses this
exception from a container and refuses it if the slice's live properties
change. This is not the default for tests: a TCG workload can contend with
production, so use it only for a controlled, quiet-window benchmark. The
guest's outbound network defaults to locked after the benchmark binary is
downloaded. If publication is desired, a narrow allowlist for the current
Geekbench Browser HTTPS address can be added; all other guest egress remains
blocked. Geekbench itself reports the VM CPU as `QEMU TCG CPU`, so the result
does not impersonate the physical host processor. Its single- and multi-core
sequences are sequential and may exceed the runner helper's fixed 15-minute
timeout, so the manual procedure executes the binary directly with a
three-hour outer bound. Host cgprofile samples the entire runner cgroup, but
that is enough for approximate per-phase timing and host-charged memory:
timestamp Geekbench's workload-boundary output and correlate it with
five-second samples against an idle-VM baseline. This includes QEMU overhead
and is not guest process RSS; guest pages may remain resident or be reused
after a workload, so label phase memory estimates accordingly. Geekbench's
published working-set figures are a useful reference, but represent
touched-page working set rather than peak RSS or a RAM requirement; the guide
records that distinction and links the source. The reviewed Geekbench 6
materials do not document a per-workload runtime control. The full CPU suite's
32 single-/multi-core workload phases have at most 31 five-second
gaps (about 2 minutes 35 seconds), so a multi-hour TCG runtime is actual work,
not repeated sampling; removing the pauses saves little and weakens the
thermal-isolation rationale. Do not add a fast mode unless Geekbench officially
documents a way to configure per-workload runtime. Any shortened run must be
labelled diagnostic unless Geekbench explicitly documents its score as
comparable: changing workload duration can change the measured metric.

Preserve cgprofile's Markdown report beside its `charts/` directory and raw
samples. In this host setup, HTML rendering was killed with exit 137 under
a small container memory limit, while `cgprofile report --md-only` succeeded;
the daemon never renders. The usage guide gives the render sequence.

The free Linux binary automatically uploads to Geekbench Browser and, in our
offline native attempt, returned exit 255 without printing scores after DNS
resolution failed. The successful native comparison therefore ran with host
network access, after publication was explicitly accepted; users who need
offline scoring require a Geekbench license that supports it. `taskset` limits
execution CPUs but does not necessarily change the topology reported by
Geekbench: our four-CPU-affinity run still reported eight processors, while
TBB confirmed the worker pool was capped to three workers plus the caller.
Record both the reported topology and effective affinity, and do not describe
the resulting score ratio as pure virtualization overhead.

The current manual benchmark starts from `case-b`'s generic-cloud image because
the harness uses cloud-init to provision its SSH tester account and to decide
when first boot has completed. Debian's NoCloud image is a plausible leaner
base for a later CPU-only run, but it needs a separate credential/setup path;
its Trixie first boot also asks for a root password on the serial console.
Cloud-init is not expected to affect an idle CPU benchmark materially, so the
main benefit would be a simpler, more explicit guest setup rather than a
guaranteed score improvement. See the [image-choice notes](../testing/vm/USAGE-TESTING.md#image-choice).

## Hypervisor boot disk and NoCloud seed

The intended VM starts from Debian's generic cloud image. That image already
contains cloud-init and avoids treating this project as a Debian ISO
installer. The private `build-iso` artifact carries NoCloud user-data and
meta-data, so the hypervisor must present it as a data CD-ROM while booting the
Debian disk first. Making the seed ISO first in firmware boot order would try
to boot a non-bootable data volume and would not start the installer.

The generic cloud QCOW2 is intentionally compact as a downloaded file, and
its virtual disk capacity can be smaller than the VM's intended installation
disk. Hyper-V must grow its converted VHDX; Proxmox/ZFS must resize the imported
volume; TrueNAS must create the destination ZFS volume at the requested final
capacity before importing. The consumer guide requires that capacity to be
chosen explicitly instead of silently accepting a too-small image size.

The setup differs by hypervisor because they expose storage differently:
Hyper-V uses VHDX, Proxmox imports a QCOW2 into an explicitly selected storage,
and TrueNAS imports a dataset image into a ZFS volume and attaches it as a VM
device. The TrueNAS importer requires the VM to be stopped with autostart
disabled during conversion. An incomplete ZFS volume may be the VM's only boot
disk, so device boot order does not protect it from an early start. VM
lifecycle on TrueNAS 27+ is managed with [`truenas/tnt`](../../../truenas/tnt/README.md)
(`tnt vm ...`); its reference VM disk-image importer (TNT-61) is the
[prior-art client](../../../truenas/tnt/docs/prior-art/truenas-vm/README.md)
until the verb ships in `tnt`. Copyable workflows are in
[`CONSUMERS.md`](CONSUMERS.md#boot-a-debian-cloud-vm-with-the-private-seed-iso).

## Prompt library boundary

The prompt package is an optional dependency and is imported only by the
`wizard` and `build-iso` verbs. `questionary` supplies terminal interactions such as grouped
choices, confirmations, text fields, and hidden password entry. The installer
does not depend on it for help, config loading, planning, installation, or
remote execution. If it is absent, the wizard reports the precise optional
install command; the rest of the CLI remains usable.

Questionary is preferred here over building another prompt framework: the
interaction is conventional and relatively small, while validation, defaults,
secret handling, cancellation, and file safety remain owned by this project.
Those decisions are product behavior and should not be delegated to a UI
library. The dependency is pinned in the installer project's
`requirements-wizard.txt` so the reusable CLI library does not depend on an
interactive UI package and prompt API changes are deliberate rather than
silently changing installation flows. Requirement manifests use the
`requirements-<use-case>.txt` pattern, with the use case naming what installs
them.

## Remote bootstrap stays self-contained

`build-customscript` belongs to this project because it serializes this
installer’s validated `Config` and the matching remote bootstrap command. It
does not speak a hosting provider’s API. `install-host.py` and other provider
clients can consume the generated JSON without learning Debian installer
internals.

The remote bootstrap fetches the installer tree from a Git remote and the
**released** `cli_extended` wheel separately. The wheel is resolved from
the `cli-extended-latest/latest.json` release pointer (or pinned with
`CLI_EXTENDED_WHEEL_URL` plus `CLI_EXTENDED_WHEEL_SHA256`), verified by sha256
before anything is written, and kept beside the entrypoint as the only
`cli_extended-*.whl`. The wheel is pure Python, so `debian-install-v2.py` puts it
on `sys.path` and imports it by zipimport: the target needs no pip installation
and no live checkout of the controller’s repository. This replaced copying the
library’s source subtree out of the same archive (decision CX-D3 of the unified
cli-extended program): the library that runs on a host is now a released,
checksummed artifact, never whatever the repository branch held at download
time. Keeping the library as a separately released artifact preserves its
checksummed version boundary while the source branch remains a moving channel.
Without a wheel beside the script the
entrypoint falls back to an installed `cli_extended`, and otherwise refuses with
exit status 2 and a pointer to the bootstrap; it never searches the repository
source tree. Questionary
is not bundled because remote installation does not run the wizard. The
generated custom-script launcher fetches the first bootstrap file through
Python’s standard-library HTTPS client and reports download failure with a
nonzero exit, rather than relying on shell-specific pipeline semantics.

## Augeas tool availability and the bootstrap boundary

The stage-one base package set includes `augeas-tools` on every host and
verifies that `augtool` and `augparse` resolve on `PATH` after installation.
This makes the commands available to `augeaswrap` without requiring a later
APT network step. The installer’s remote bootstrap remains stdlib-only and
does not use Augeas: it runs before the installer package stage, when it only
needs to fetch the installer tree and its Python library. Keeping the package
in the base APT stage gives every installed host the tools while preserving
the bootstrap’s early, dependency-free handoff.

## Terminal color remains a shared presentation concern

The installer registers commands and routes output through `cli-extended`;
it does not add its own ANSI strings or color package. The shared boundary
styles generated terminal help and diagnostic severity tags according to the
destination TTY, `NO_COLOR`, `--color`, and `--no-color`. Config, status/plan
results, and JSON stay plain. This keeps copied configuration and redirected
output stable while allowing terminal help/errors to be readable at a glance.

Generated bundles can contain credentials if the chosen configuration does.
The normal output path therefore uses the shared CLI’s secret redaction and
refuses to emit a secret-bearing bundle unless `--debug-raw` is explicitly
requested. Operators who opt in must protect the destination and any provider
configuration/API record that stores the custom script.
