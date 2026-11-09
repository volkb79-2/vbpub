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
reserve, the raw benchmark partition, and swap have one physical order and one
whole-disk accounting rule. See the [feature overview](../README.md#disk-layout)
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

Every plan reserves the default 8 GiB Linux partition with PARTLABEL
`iobench-raw`. Its fixed size can be configured or disabled with
`iobench_raw_gb: false`. It is never formatted or mounted; an enabled
benchmark wipes it immediately before the direct iobench run and leaves it
raw in the final layout. Stage two runs the benchmark before configuring
io.cost, so the measured coefficients reach the model on a fresh install. A
first-class plan entry prevents a benchmark from temporarily changing
partition numbers or leaving an interim-table hole.

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

Exact mounts on `/`, `/var`, `/var/lib`, `/srv`, and `/home` are refused.
## Data mount safety

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
the same filesystem label and UUID before archiving its state.

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
