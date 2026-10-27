# Consumer configuration

This document is the pasteable adoption contract for MDT's host governance and
managed BuildKit backend. Host setup is intentionally manual because it
changes systemd, Docker, and cgroup policy. For the end-to-end picture of how a devcontainer is
created, initialised and started, read
[DEVCONTAINER-MECHANISMS.md](DEVCONTAINER-MECHANISMS.md) first.

## PWMCP browser service

The MDT image includes PWMCP at `/srv/pwmcp-instance`. Start it when you need
the shared remote browser:

```bash
cd /srv/pwmcp-instance
ciu up
```

The connected devcontainer joins the instance network. Configure an MCP client
inside it with:

```json
{
  "mcp": {
    "servers": {
      "pwmcp": {
        "type": "http",
        "url": "http://pwmcp:8931/mcp"
      }
    }
  }
}
```

The Playwright WebSocket endpoint is `ws://pwmcp:3000/`. The `pwmcp` CLI can
inspect rendered pages through that remote browser:

```bash
pwmcp inspect https://example.com --format snapshot --output page.yml
pwmcp inspect https://example.com --format screenshot --output page.png --full-page
```

The MDT devcontainer template exposes `/srv/pwmcp-instance/bin` on remote
process `PATH` and sets `PWMCP_MCP_URL=http://pwmcp:8931/mcp`. Agent harnesses
still need an HTTP MCP server entry pointing at that URL; installing the
packaged PWMCP skill does not change a harness's MCP configuration. A consumer
using another devcontainer must expose its installed CLI on `PATH` and ensure
the agent can resolve and reach the MCP endpoint on its internal network.

For the devcontainer that is already running while its image cannot be
rebuilt, install the current release into `/srv` and start it manually:

```bash
sudo install -d -m 0755 -o vscode -g vscode /srv/pwmcp-instance
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/pwmcp/get.py \
  | XDG_DATA_HOME=/srv python3 - install --scope user
export PATH="/srv/pwmcp-instance/bin:$PATH"
cd /srv/pwmcp-instance
ciu up
```

To update that install, replace `install` with `update` in the pipeline, add
the launcher directory to `PATH`, then run `ciu up` again. The instance remains internal-only and unauthenticated,
matching PWMCP's existing network policy. See
[PWMCP's consumer guide](../../pwmcp/docs/CONSUMERS.md#install-and-update).

## Host prerequisite

On the Docker host, review and apply the shipped host configuration:

```bash
sudo ./host-setup/install.sh --wizard
sudo mdt-host-check.sh
```

mdt defaults to disabling `DAMON_STAT` so the cgroup-profiler daemon can use the DAMON sysfs
interface: `DAMON_STAT_DISABLE_DEFAULT="yes"` in the example, also without the wizard.
The wizard asks for `DAMON_STAT_DISABLE_DEFAULT=yes|no`. Enter accepts the
default `yes`: MDT writes `N` to the kernel's
`/sys/module/damon_stat/parameters/enabled` parameter now and on each boot,
letting cgprofile collect DAMON samples. Choose `no` to keep the kernel's
configured default. Removing a previous MDT override leaves the live parameter
unchanged until reboot. `mdt-host-check.sh` reports a stale or unapplied choice.
To identify the MDT build before choosing an operation, run:

    ./build-push.py --version

The declared MDT entrypoints use the same top-level probe and print exactly one
`MDT <version>` line on stdout, exit 0, and write nothing to stderr. Their
parser help, usage, and configuration diagnostics begin with the MDT headline
as line 1 at every parser depth; normal command output is unchanged.

On a fresh host, use the wizard: a plain first run refuses the incomplete
example and leaves `/etc/mdt` untouched. A manual `install.sh` run requires a
complete, host-specific config already present. With an existing config,
`--wizard` preserves its values as defaults; add `--reset` only to discard
those values as defaults and start from the current repository example. In
both cases replacement/backup happens only after the generated candidate
passes strict validation. Deleting the old file first loses those defaults and
does not create the automatic backup. The watcher configuration uses the
current `WATCHER_*` names shown in `host-setup.env.example`; old names are not
aliases for the new clean configuration.

Fresh installs default to `report-only`. If a plain install finds an existing
`terminate` policy that the wizard already confirmed (recorded in
`/var/lib/mdt/guard-terminate.confirmed` with the policy, the BuildKit image and this host's
machine-id digest), it proceeds and keeps the running guard; the guard itself verifies the record
before it acts. A new or
changed `terminate` without that record refuses BEFORE touching anything (the running guard
is left as it was) until `--wizard` confirms it; the host config stays available for review. During `--wizard`, accepting the default answer
changes `terminate` to `report-only`.

### cgprofile resource-observer host setup

For an existing MDT host with a valid `/run/cgprofile` control directory,
provision the contract-2 observer path with this focused host command:

```bash
sudo ./host-setup/install.sh --observer-only
stat -c '%u:%g %a %n' /run/cgprofile/observer
getent group cgprofile-observer
sudo mdt-host-check.sh
```

The command installs the dedicated `cgprofile-observer` group and its separate
tmpfiles rule, then checks for a real `root:<observer-gid>` directory with mode
`0751`. It refuses a pre-existing observer group that shares Docker's GID
before changing units or creating the directory. It also refuses a pre-existing
observer path whose type, owner, group, or mode has drifted; inspect and repair
that path before retrying rather than relying on tmpfiles to re-own it. The
host checker independently verifies the observer group, directory metadata,
and the complete active sysusers/tmpfiles rule sets. Blank lines and comments
are ignored; additional active directives fail the check. It points back to
this command when provisioning is missing. It also refuses if
`/run/cgprofile` is missing, a symlink, or not `root:docker 0770`. Run it from the Docker host; the
[host-escape consumer guidance](#host-commands-from-an-mdt-devcontainer)
shows how to run host commands from the cockpit. Deploy the daemon through the
supported CIU consumer workflow after the observer directory exists.

The wizard reads live `MemTotal`, `MemAvailable`, CPU count, swap, the
configured `IO_BASELINE_ENV` benchmark-results file and `IO_BASELINE_TESTFILE`, and Docker mount
facts. It walks `dev.slice`, the guaranteed sibling,
`dev-interactive.slice`, `dev-background.slice`, `dev-gates.slice`, and
`dev-buildkitd.slice` in that order. For each it asks `MemoryMin`, `MemoryLow`,
`MemoryHigh`, and `MemoryMax`, plus the applicable CPU, `IOWeight`, swap, and
zswap controls. Its memory policy is:

- configured values within one slice must satisfy `MemoryMin <= MemoryLow <=
  MemoryHigh <= MemoryMax`; the wizard re-prompts the invalid right-hand field;
- sibling controls are independent and are not summed;
- a child `MemoryMin`/`MemoryLow` without matching parent protection remains
  valid but is reported because the requested protection may be ineffective;
- a single child `MemoryHigh` or `MemoryMax` above the configured parent is
  re-prompted; sibling `MemoryLow`/`MemoryHigh` totals above the parent are
  advisory only and do not block an intentional overlapping policy.

Enter accepts the shown default. For CPUQuota/MemorySwapMax, `auto` derives at
install time and `-` omits the directive (unlimited/default); for optional
memory fields, `-` also omits the directive. MemoryMax is RAM only;
MemorySwapMax is swap only. A benchmark uses the shared Linux-derived
`io.cost` matrix against the persistent
`IO_BASELINE_TESTFILE`, never a raw device, and is reusable by identity rather
than by age. It saturates the disk for about 12 minutes at default settings,
so run it in a quiet window. `--with-baseline` installs missing `fio`/`pv`
before the wizard, asks for confirmation near the start, and runs one
background measurement while the remaining questions continue. Without that
flag, the wizard offers the measurement only when both tools are already
installed; otherwise the candidate is installed first and you can run the
benchmark later.

### Host benchmark file targets

MDT and `debian-install-v2` use the same generated Linux-derived coefficient
tool. MDT always writes to a persistent file on the Docker-data filesystem;
its identity check refuses a target on a different filesystem. To run the
manual debian-install-v2 wrapper against an already mounted scratch target,
use file mode:

```bash
sudo scripts/debian-install-v2/tools/iocost-calibrate.sh --duration 30 -- \
  --testfile /mnt/iocost-benchmark/target.bin --testfile-size-gb 1
```

This writes and measures a regular file on that mounted filesystem. Raw
`--testdev /dev/DEVICE` mode is destructive to the named block device; the
shared tool can resolve a partition's scheduler sysfs path, but it does not
make raw testing non-destructive. Do not use it on a disk or partition with
data you need. Partition creation, formatting, mounting, and cleanup remain
operator-managed until the debian-install-v2 installer integration is built.

The file path must be dedicated disposable benchmark scratch space: the tool
fills a new file with random data, and fio overwrites the target during its
measurements. An existing target is reused only when it is a single-link
regular file with exactly the requested size; any other existing target is
refused and left untouched. Reuse does not preserve the file's contents.

After that run, the wizard asks for four explicit static IO fallback values:
`DEV_STATIC_RIOPS`, `DEV_STATIC_WIOPS`, `DEV_STATIC_RBW`, and
`DEV_STATIC_WBW`. They are hard caps on the aggregate `dev.slice` root and
apply at boot or whenever no identity-verified benchmark results are
available. A successful measurement temporarily replaces them with
`DEV_IO_CAP_PCT` percent of the measured ceilings. Without current results,
containers receive no guessed per-container IO cap.

### Dev-tier IO governance: pin, verify, swap

On a virtual disk a benchmark can read the hypervisor's write-back cache (one
host: 4.3 GB/s measured, ~20 MB/s real), so pin the dev.slice caps from a
sustained measurement instead of trusting a percentage of the baseline. The four
`DEV_IO_*_MAX` keys are read by the periodic reconcile with no reinstall:

```bash
# 1. measure sustained write for minutes, use the LAST minute only
sudo iostat -x 5            # while the disk is saturated; or fio --direct=1 --fsync=1 --time_based
# 2. pin: add or edit these lines in /etc/mdt/host-setup.env
#    positive integers or K/M/G/T suffixes; 0 is refused
#      DEV_IO_RBPS_MAX=80M
#      DEV_IO_WBPS_MAX=40M
#      DEV_IO_RIOPS_MAX=800
#      DEV_IO_WIOPS_MAX=600
sudo ./host-setup/install.sh        # refresh the unit-file fallback from the same keys
# 3. apply and verify
sudo systemctl start mdt-dev-governance-reconcile.service
systemctl is-failed mdt-dev-governance-reconcile.service     # expect: active (not "failed")
sudo mdt-cgroup-io-limits.sh dev.slice --all --systemd       # limits, EFFECTIVE, drop-in paths
```

On a pin-only host (no valid baseline, the normal state on a virtio disk) the per-container
caps, the Docker-events watcher caps and the per-scope `IOWeight=1` deprioritisation of
gate and BuildKit containers are inactive: they need a valid baseline. The slice-level caps
(the `dev.slice` pins and the per-tier keys, plus the slices' own `IOWeight`) are the whole
protection, and are enough.

Upgrading an existing host: the 20 pin keys (`DEV_IO_*_MAX` and `DEV_<TIER>_IO_*_MAX`) and
`IO_BASELINE_ROTATIONAL_FLAG` are **runtime-optional**: absent from an older
`/etc/mdt/host-setup.env` they mean "not set", so a plain `sudo ./host-setup/install.sh`
works and prints one NOTICE naming the effective cap source (the static fallback
`DEV_STATIC_*`, lowered only by a current, plausible baseline), plus a NOTICE counting the
absent runtime-optional keys. Render-time keys still refuse when absent. Run
`install.sh --wizard` to review and add them.

virtio-blk reports `rotational=1` by default, so a genuinely fast virtio disk can have
its baseline refused as implausible; set `IO_BASELINE_ROTATIONAL_FLAG=ignore` in `/etc/mdt/host-setup.env` only if you
know the disk is fast (`mdt-io-baseline.py`, the wizard benchmark and the reconcile all read it
from there), and pin the caps from a sustained measurement either way.

The values above are an illustration (one 16 GiB virtio host), not a default.
Expected after a good run: the journal line
`dev.slice: verified kernel io.max 254:0 rbps=83886080 wbps=41943040 riops=800 wiops=600`
followed by `is authoritative`; `dev-gates.slice` and `dev-buildkitd.slice` show
`riops=480 wiops=360` (`DEV_SUBSLICE_IOPS_PCT`% of the effective values). A
**kernel update** makes the baseline stale until `mdt-io-baseline.py --force`
is re-run; pinned values keep the cap meanwhile. The reconcile logs `ERROR` and
exits non-zero if the kernel does not hold the intended values, and warns
`UNCAPPED SWAP` when a swap device sits on a disk with no `dev.slice` `io.max`
entry (`mdt-host-check.sh` reports the same).

Swap stays ample (80% of host swap cascades to `dev.slice` and to every child;
that default is kept). The protection against a gate thrashing the shared disk is
a tight IO cap on the gate tier: swap-out IO is charged to the page owner's
cgroup (per kernel source, not measured here: swap writeback bios are associated with the page owner's blkcg via `bio_associate_blkg_from_page` in `mm/page_io.c`), so a gate that swaps is throttled by its own cap. Runtime keys in
`host-setup.env`, picked up by the next reconcile (illustration from one host,
not a default):

```bash
DEV_GATES_IO_RBPS_MAX=40M
DEV_GATES_IO_WBPS_MAX=20M
DEV_GATES_IO_RIOPS_MAX=300    # min(DEV_SUBSLICE_IOPS_PCT sub-ceiling, 300)
DEV_GATES_IO_WIOPS_MAX=200
```

The same four keys exist for `INTERACTIVE`, `BACKGROUND` and `BUILDKITD`. A
throttled gate stalls and runs slowly: classify that as an infrastructure
slowdown, not a product failure. `DEV_SWAP_MAX` / `DEV_GATES_MEMORY_SWAP_MAX`
(absolute values such as `8G`/`2G`) remain available for a smaller allowance. If
the reconcile ever has to withdraw a cap it re-asserts explicit values with
`set-property --runtime`: a `daemon-reload` after removing a runtime drop-in was
measured not to re-apply the unit-file value.

The wizard also asks for `CGROUP2_FLAGS`, the timer interval, and the one
out-of-tree Buildx name-pattern field. Correctly placed containers are selected
by their cgroup path, not a pattern. `CGROUP2_FLAGS=fix` lets the periodic host
service restore the `memory_recursiveprot` mount flag; `warn` only reports a
missing flag. The interval is a single systemd duration such as `5min`; match
patterns are space-separated shell globs. The wizard also asks for
`DEV_BUILDKITD_MAX_PARALLELISM`. This limits one
managed BuildKit daemon's internal solver parallelism; it does not serialize
client requests and is separate from release repack concurrency.

It asks for each per-container `WATCHER_*_MEMORY_HIGH` and
`WATCHER_*_MEMORY_MAX` setting. The shipped default for all six is `-`, which
leaves per-container memory and swap uncapped by this watcher while the parent
slice still bounds the aggregate tier. Enter explicit sizes only when this
host wants the watcher to add a leaf cap. It only fills in scopes whose Docker
configuration has no `mem_limit`; CIU-managed Compose services commonly set
one. CIU can also apply `MemoryMin` to a scope, so any explicitly configured
watcher limits must be compatible with that value. The wizard requires a
separate confirmation before it writes any non-`-` per-container memory pair.

## Apt priorities: backports and testing

The image ships `/etc/apt/preferences.d/debian-priorities` (stable 500, stable-backports 600) and
`/etc/apt/preferences.d/preview-archives` (testing, oldstable, oldstable-backports 100). Paste:

```bash
sudo apt-get update
apt-cache policy <pkg>                      # candidate = highest priority offering; backports (600) beats stable (500)
sudo apt-get install <pkg>                  # from backports when it offers the package, else stable
sudo apt-get install -t trixie <pkg>        # stay on stable although backports has a newer one
sudo apt-get install -t testing <pkg>       # explicit testing, below
```

The vendor repositories (sury, pgdg, redis.io, docker.com) are pinned to 700 in
`/etc/apt/preferences.d/vendor-origins` (`Pin: origin <host>`), so they beat backports. Add one such stanza when you add
your own third-party source.

If you add your own preference for a Debian archive, match it with `a=<archive name>` (`stable-backports`),
not the codename: `a=trixie-backports` matches nothing, because apt compares `a=` with the Release `Suite:`
(`n=` is the codename).

## Installing a package from Debian testing

The image ships `/etc/apt/sources.list.d/testing.sources` and `/etc/apt/preferences.d/preview-archives`
(from `apt/`). Paste to preview a newer package without changing anything else:

```bash
sudo apt-get update
apt-cache policy skopeo
sudo apt-get install -t testing skopeo
```

Plain `apt install` never chooses testing (priority 100), and a package that stable also offers stays on
stable on `apt upgrade`. A package you installed with `-t testing` that stable does not offer at a higher
version keeps following testing on later upgrades (the installed version ties at 100 and the newer one
wins); pinning the archives at 99 instead would stop that, `-t testing` and `pkg/testing` still working.
If you add your own preference
for these archives, write ONE `Pin: release a=<archive>` stanza per archive: the comma-separated
conditions of a single `Pin: release` line must all match, so a line naming three archives matches none.

## Per-container memory watcher

The shipped policy disables MDT's inotify memory watcher. Correctly placed
containers remain under their parent slice's aggregate limit. The separate
`docker-.scope.d` backstop still applies its configured generous MemoryMax and
MemorySwapMax to every Docker scope; the wizard reviews those in section f.
The memory wizard asks for the service policy and all six values. Set
`WATCHER_MEMORY_POLICY=enabled` only
when at least one per-container value is configured; unused values must stay
`-`. This is a complete example for an operator who explicitly wants an
interactive-container leaf cap. Size it for the workload and host:

```dotenv
WATCHER_MEMORY_POLICY=enabled
WATCHER_INTERACTIVE_MEMORY_HIGH=3G
WATCHER_INTERACTIVE_MEMORY_MAX=4G
WATCHER_BACKGROUND_MEMORY_HIGH=-
WATCHER_BACKGROUND_MEMORY_MAX=-
WATCHER_GATES_MEMORY_HIGH=-
WATCHER_GATES_MEMORY_MAX=-
```

The installer refuses an enabled policy with no caps or a disabled policy with
configured caps. The health check reports policy/service drift. During an
upgrade from a config that predates `WATCHER_MEMORY_POLICY`, the wizard treats
existing non-`-` caps as user settings and presents them for review. See the
[design rationale](DESIGN-GUIDE.md#per-container-memory-policy).

Host setup is host-only: run the installer and wizard from a shell on the
Docker host, never from the consumer devcontainer. UID 0 in a devcontainer is
not host root and cannot apply the host's `/etc`, systemd, or cgroup policy;
the preflight refuses before reading or writing host setup. If `findmnt`
reports `overlay`, stop and leave the container—that is the container's mount
namespace, not a usable host block-device source. From the host shell, use a
device such as `/dev/nvme0n1` or `/dev/mapper/vg-root` if discovery cannot
expose it. Do not enter `/`, `UUID=...`, or `MAJ:MIN`. The `/dev/...` check is
shape-only because the path is for the host being configured; it is not a
local `stat` of a path in the wizard's namespace.

`MemoryMin` is hard hierarchical protection, `MemoryLow` is soft best-effort
protection, `MemoryHigh` is soft reclaim throttling, and `MemoryMax` is a hard
RAM ceiling. `DEV_MEMORY_MIN_GUARANTEED_CEILING` is the one authoritative
`dev.slice` MemoryMin and is mirrored on `dev-memory_min_guaranteed.slice`.
`DEV_MEMORY_LOW`, `DEV_MEMORY_HIGH`, and `DEV_MEMORY_MAX` are the other three
root controls. Keep sizes/weights host-specific; do not copy values from this
repository's example onto a different host.

The default accidental-worker policy is report-only:

```text
BUILDX_ACCIDENTAL_CONTAINER_POLICY=report-only
```

`terminate` can interrupt a live Docker-container Buildx build. The wizard asks
for a second confirmation before writing the explicit opt-in:

```text
BUILDX_ACCIDENTAL_CONTAINER_POLICY=terminate
```

These are the complete closed vocabulary. Missing or invalid policy fails
closed. The guard approves only the service container whose inspected name,
configured image, cgroup parent, and labels match the host setup. A reserved
name alone is not approval.

## Host commands from an MDT devcontainer

`host-escape` is a compatibility alias for the MDT host-exec helper. It starts
a privileged helper, enters the host's namespaces, and runs with host-root
authority; use it like a root shell and review each command before executing
it. It runs `mdt doctor` afterward, which repairs missing required cgroup2
mount flags. The standalone check-only form does not repair:

```bash
host-escape -- systemctl show dev-interactive.slice -p MemoryMax
host-escape -- docker inspect dstdns-devcontainer-vb
host-escape -- /usr/local/sbin/mdt-cgroup-io-limits.sh dev.slice --all --systemd   # dev-tier IO caps really in force
host-escape                         # interactive host shell
mdt doctor --check-only             # inspect cgroup2 flags without repair
host-escape --help
```

`mdt doctor` also starts a privileged host helper. Without `--check-only`, it
may remount the host cgroup2 filesystem to restore required flags.

For host changes, run the command from the Docker host shell where possible.
The escape helper exists for host diagnostics and operations that genuinely
need host namespaces; it does not preserve devcontainer isolation.

## Consumer devcontainer

Copy both `devcontainer.json` and `initialize_container_environment.py` from the
current MDT template. Keep the host-side `initializeCommand` and interactive
`runArgs` entry together:

```jsonc
{
  "initializeCommand": "python3 .devcontainer/initialize_container_environment.py",
  "runArgs": ["--cgroup-parent=dev-interactive.slice"]
}
```

### Host cgroup preflight

The initializer runs on the Docker host before container creation. It refuses
startup unless `dev-interactive.slice`, `dev-background.slice`, and
`dev-gates.slice` are loaded from installed unit files. Install the host policy
and verify it before rebuilding:

```bash
sudo ./host-setup/install.sh --wizard
sudo mdt-host-check.sh
```

Keep `--cgroup-parent` as an effective Docker option in `runArgs`. The
initializer follows Docker's option/value boundaries and refuses a token that
is consumed as another option's value or whose surrounding option syntax it
cannot classify.

Do not remove the initializer while keeping the `--cgroup-parent` entry;
otherwise systemd can silently create an unlimited transient slice. The full
template also supplies these mandatory values in `containerEnv` and `mounts`.
See the [preflight design](DESIGN-GUIDE.md#host-cgroup-preflight) for why the
check runs before container creation.

```jsonc
{
  "containerEnv": {
    "BUILDX_BUILDER": "mdt-managed",
    "BUILDKIT_HOST": "unix:///run/mdt-buildkitd/buildkitd.sock"
  },
  "mounts": [
    "source=/run/mdt-buildkitd,target=/run/mdt-buildkitd,type=bind"
  ]
}
```

The actual file may contain other JSONC properties. The source path is a host
path and must already exist; `--mount` is deliberate because a missing source
must stop container creation rather than become an empty directory.

The host service preserves this directory across stop/start so installer-driven
BuildKit restarts do not strand a running container's bind mount on a deleted
inode. After upgrading from a host setup that did not preserve it, check the
mount from inside an existing devcontainer with
`findmnt -T /run/mdt-buildkitd -o SOURCE`. If the source ends in `//deleted`,
rebuild that devcontainer once; future service restarts retain the mount's
directory identity. The same mount rule applies to `/run/docker-api`: keep the
directory-level mount from the MDT template, since its socket entry can be
recreated safely inside the stable directory. See the [directory bind-mount
design](DESIGN-GUIDE.md#directory-bind-mount-identity) and the
[BuildKit socket design](DESIGN-GUIDE.md#managed-buildkit-socket-access).

After the image is rebuilt, `postCreateCommand` runs
`finalize_container_environment.py`. It validates/reuses the named remote and
does not create a per-container or container-driver builder. A partial or
different `BUILDX_BUILDER`/`BUILDKIT_HOST` pair fails finalization. With the
explicit environment above, both commands use the managed remote:

Host setup keeps the socket at mode `0660` and assigns it to the host
`docker` group after verifying the live service socket. The consumer must
receive that same supplementary group (the MDT template wires it from the
host's `DOCKER_GID`). Do not make the socket world-accessible to work around a
group mismatch; check `id` in the consumer and `stat` on the host socket. See
the [socket-access design](DESIGN-GUIDE.md#managed-buildkit-socket-access).
The service seals its runtime directory after socket creation and compares
ownership within the container's user namespace, so host-side UID remapping
does not require manual `chown` or a directory permission change.

```bash
docker build -t example:dev .
docker buildx build --load -t example:dev .
docker buildx inspect mdt-managed
```

Do not rely on a user's Buildx “current builder” state. The environment is the
consumer contract and is re-applied by the template/finalizer.

### Adding a `PATH` entry

Set it in the consumer's `devcontainer.json`; the MDT template itself sets no `PATH`. Reference the
container's own value so the image's entries are kept:

```jsonc
{
  "containerEnv": {
    "PATH": "/workspaces/tools/bin:${containerEnv:PATH}"
  }
}
```

The entry survives **login** shells too (`bash -l`, `zsh -l`, the Dev Containers `userEnvProbe`
login shell that feeds VS Code): the image patches Debian's `/etc/profile` to keep an inherited
`PATH`, and its login hook only moves the image's own entries (`/opt/mdt/bin`, the venv, the npm
prefix, PWMCP first, `~/.local/bin` last) into place. Your entry keeps its position among the
others. Only a shell started with a *cleared* environment (`env -i`, `su -`) gets just the image
entries plus the Debian default. See [One PATH order](DESIGN-GUIDE.md#one-path-order).

## Shell selection and configuration

A VS Code terminal opens **bash** unless the devcontainer sets
`terminal.integrated.defaultProfile.linux` (the templates set it to `zsh`). To set it yourself:

```jsonc
"customizations": { "vscode": { "settings": {
  "terminal.integrated.defaultProfile.linux": "zsh"
} } }
```

Put settings that must apply in both shells in `~/.config/mdt-cockpit/shell.env` (exports),
`ai.env` (keys) and `aliases.sh` (aliases, interactive only); `profile.sh` sources them in bash
and zsh alike. The full startup-file order for both shells, the flow diagram, the bash-versus-zsh
compatibility notes and the troubleshooting table are in
[How an mdt devcontainer works, end to end](DEVCONTAINER-MECHANISMS.md#8-shell-selection-and-startup).

## VM-backed test runners

The MDT cockpit has headless x86 QEMU, `qemu-img`, OVMF firmware, and
`cloud-localds` available for disposable guest tests. This is an opt-in runner
capability, not a host VM service. Package presence alone does not expose host
virtualization devices.

Use a VM for loop devices, partition devices, `partx`, `mkswap`, `swapon`,
`swapoff`, initramfs, reboot, UEFI, kernel, or any other test that changes
kernel-global state. A privileged Docker container shares the host kernel;
loop and swap are not namespaced, so it is not an acceptable boundary for
those operations. Keep the existing privileged container only for tests that
need its userland/systemd shape without creating or activating host-visible
devices.

The standard runner uses QEMU TCG and user-mode networking, so it needs no
`--privileged`, `/dev/kvm`, or `/dev/net/tun`. Place it in the configured test
slice and keep its guest disks on disposable storage:

```bash
docker run --rm -d --name debian-vm-test \
  --cgroup-parent="$CGROUP_PARENT_DEV_GATES" \
  --tmpfs /run --tmpfs /tmp \
  -v "$PWD/.vm-state:/vm:rw" \
  <vm-runner-image>
```

Inside that runner, the MDT tools are used directly, for example:

```bash
qemu-img create -f qcow2 /vm/guest.qcow2 20G
qemu-system-x86_64 -accel tcg,thread=multi -nographic \
  -nic user,hostfwd=tcp:127.0.0.1:2222-:22 \
  -drive file=/vm/guest.qcow2,format=qcow2
```

The device arguments are intentionally absent: exposing KVM or TAP by default
would silently broaden the cockpit's privilege. KVM may be selected explicitly
on hosts that provide it; otherwise TCG is slower but keeps the runner
unprivileged. Keep VM disks under disposable storage and never attach a
production block device.

MDT's system acceptance uses the repository's shared Debian-install VM
harness. From the MDT project root, run `run-gate vm-system`; the lane
requires the governed `CGROUP_PARENT_DEV_GATES`,
`CGROUP_PARENT_DEV_INTERACTIVE`, `BUILDX_BUILDER`, `BUILDX_CONFIG`, and
`DOCKER_HOST` variables from the configured host environment. Run-Gate checks
all five before launching the controller. The harness maps the selected
worktree's physical source mount and runs the installer, systemd, cgroup,
Docker, and slice assertions inside the QEMU guest. It does not run those
product checks in the cockpit. The harness override is
`MDT_VM_SOURCE_DIR`, which is resolved through Docker's authoritative mount
table and refuses an unmapped or missing source. The single prerequisite list
lives in `run-gate.toml`; see the [design note](DESIGN-GUIDE.md#vm-system-prerequisites-live-in-run-gate).

The image release gate is `run-gate release`. CMRU invokes that declared
lane, so the OCI release-flow tests remain in `tester-unified` and do not have
a second hand-written tester command in `cmru.toml`.

### Persistent AI CLI state

The vendored template mounts one host directory, `${localEnv:HOME}/mdt-cockpit-home/`, over
`/home/vscode`. This keeps every agent state root and all other home data across rebuilds.

| Tool | Persistent home path | Durable state |
|---|---|---|
| Pi | `~/.pi` | config and `~/.pi/agent/sessions/` |
| ClaudeLink | `~/.claudelink` | `nexus.db`, scheduler state/logs, and related runtime files |
| Codex | `~/.codex`, optional `~/.codex2` | auth, profiles, history, shared SQLite database |
| OpenCode | `~/.local/share/opencode` | auth, sessions, logs, and runtime state |
| Claude Code | `~/.claude`, `~/.claude.json` | auth, projects, page state, and history |

The same mount includes `.config`, `.local`, `.ssh`, `.gnupg`, `.minisign`, shell startup files,
cache directories, and user-created paths. The host creates a new home source with mode `0700`
and `/tmp` as a separate `mdt-cockpit-tmp` source with mode `1777`. The native host's readonly SSH
directory remains separate at `/run/mdt/host-ssh`.

The image package names are `mdt-cockpit` and `mdt-cockpit-devcontainer`, and the runtime
namespace matches: your own MDT overrides go under `~/.config/mdt-cockpit/` (a directory *you*
create; `ai.env` for keys, optional `shell.env` and `aliases.sh`), and the image's files,
examples and manifest are under `/usr/local/share/mdt-cockpit/`.

Image-owned executables and runtimes are outside the mounted home, in the `mdt` group tree:
`/opt/mdt/bin`, `/opt/mdt/venv`, `/opt/mdt/venvs/pyNN`, `/opt/mdt/npm`, `/opt/mdt/python`,
`/opt/mdt/oh-my-zsh` and `/srv/pwmcp-instance`. Defaults for bash, zsh, nano, htop, mc, Neovim
and `less` are wired system-wide, so a **new home is empty** and you override a default by
creating your own file. See the
[design rationale](DESIGN-GUIDE.md#the-home-is-user-state-image-assets-live-outside-it) and
[System-wide defaults](DESIGN-GUIDE.md#system-wide-defaults).

Update an image tool in place, with no `sudo`, because your user is in group `mdt`:

```bash
npm install -g @openai/codex@latest        # goes to /opt/mdt/npm (NPM_CONFIG_PREFIX)
/opt/mdt/venv/bin/python -m pip install --upgrade ciu   # writes into the primary venv
```

Such changes live in the container filesystem; recreating the container restores the image's
version. Put your own CLIs in `~/.local/bin` - it is last on `PATH`, so an image tool of the same
name wins.

### Codex profile state and container lifetime

The shipped template sets `CODEX_SQLITE_HOME=/home/vscode/.codex/sqlite-shared`, placing the
shared SQLite database under the complete persistent home. It also sets `shutdownAction` to `none`,
so the container remains running when the attached Dev Containers session disconnects.

### Optional Git config mount

The complete home already persists a container-local `.gitconfig`. If a consumer wants to share
the host's native Git config instead, add this entry to its `mounts` array (any further mount source
is named `mdt-cockpit-<purpose>`):

```jsonc
"source=${localEnv:HOME}/mdt-cockpit-gitconfig,target=/home/vscode/.gitconfig,type=bind"
```

The bootstrap uses the host filesystem type for existing sources, so an existing regular-file
source works regardless of its filename or suffix. The default
`DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=create-by-spelling` creates missing sources according to
their spelling: append `/` for a directory; without `/`, MDT creates an empty regular file with
mode `0600`. This is an MDT convention, not a Docker mount type option. For example, this missing
custom directory source is marked for creation:

```jsonc
"source=${localEnv:HOME}/mdt-cockpit-cache/,target=/home/vscode/.my-cache,type=bind"
```

A directory mount must keep its trailing `/`, including in copied template entries. For a missing
`$HOME` source without `/`, MDT creates a file regardless of its basename or suffix. Other existing
host sources retain their actual type. The MDT host-setup wizard can instead set
`DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=fail`; this requires every `$HOME` source managed by the
bootstrap to exist before Docker starts the devcontainer (allowed values: `create-by-spelling`, `fail`);
under `fail` the bootstrap's error names the variable, `/etc/mdt/host-setup.env`, the allowed values and the
exact `mkdir -p`/`touch` for the missing path. `mdt-cockpit-gitconfig` is recognized as
a file source and a directory at that path is refused.

Which source is the home and which is `/tmp` comes from each mount's **target** (the container home is
`/home/<remoteUser>` from `devcontainer.json`, and `/tmp`), so renaming the sources, for example
`mdt-cockpit-<project>-home` / `mdt-cockpit-<project>-tmp` for several checkouts on one host, keeps the
`0700`/`1777` modes. The source *names* are free; the *targets* are the contract.

Choose the policy with `sudo ./install.sh --wizard` from MDT's `host-setup/` directory on the Docker
host. The bootstrap reads the resulting `/etc/mdt/host-setup.env` when `initializeCommand` runs.

To seed the source from the host's native Git config, run this on the host before the bootstrap has
created the source, or after inspecting any already-created file:

```sh
set -eu
source_file="$HOME/.gitconfig"
target_file="$HOME/mdt-cockpit-gitconfig"
test -f "$source_file"
if [ -e "$target_file" ] || [ -L "$target_file" ]; then
  printf 'Refusing to overwrite existing Git config source: %s\n' "$target_file" >&2
  exit 1
fi
install -m 600 "$source_file" "$target_file"
```

If an earlier bootstrap created a directory or empty file at the `mdt-cockpit-gitconfig` path, inspect
it before moving, replacing, or removing it. If Git config is not needed in the container, remove
the mount instead.

### Using the fresh-home template

Copy the vendored template and host initializer into the consumer's `.devcontainer` directory:

```bash
cp <vbpub>/mdt-cockpit/templates/{devcontainer.json,initialize_container_environment.py} .devcontainer/
# then "Dev Containers: Rebuild Container"
```

Working in several repositories from one cockpit? Use the host-side-folder variant instead: the config
lives in a folder outside every repo and the container opens a directory of repos
([templates/host-folder](../templates/host-folder/README.md)). An optional per-user btrfs copy-on-write area
is described in [BTRFS-COW.md](BTRFS-COW.md).

The template binds the complete home from `${localEnv:HOME}/mdt-cockpit-home/`; the host
initializer prepares that directory (mode `0700`) and `${localEnv:HOME}/mdt-cockpit-tmp/` (mode
`1777`, the separate `/tmp` mount). The new home starts **empty**. Nothing is migrated: there is
no stopped-container step, no marker file, and no startup refusal.

If you used the previous split-mount template, the initializer prints one informational line
while `~/mdt--mounted-folders/` still holds files and the new home is empty. Copy what you want by
hand: [Moving from modern-debian-tools-python-debug](HOME-PERSISTENCE.md#moving-from-modern-debian-tools-python-debug)
has a pasteable `cp -a` recipe and the list of old-image paths that must **not** be copied
(`~/.venv*`, `~/.oh-my-zsh`, `~/.local/bin`, `~/.local/lib/node_modules`,
`~/.codex/packages/standalone`). The old state is never touched; delete it yourself when satisfied.

### Local-dev build

Build and test an image from artifacts that are not released yet - always an explicit opt-in,
never a fallback for a missing release.

```bash
cd <vbpub>/mdt-cockpit
cp local-dev-artifacts.toml.example local-dev-artifacts.toml     # git-ignored
sha256sum /path/to/ciu-7.11.0-py3-none-any.whl                    # fill sha256 + version
$EDITOR local-dev-artifacts.toml
```

```toml
schema_version = 1

[[artifact]]
name = "ciu"
path = "/path/to/ciu-7.11.0-py3-none-any.whl"
sha256 = "<64 hex digits>"
version = "7.11.0"

[[artifact]]
name = "pwmcp-bundle"
path = "/path/to/pwmcp-1.64.0-r4.tar.xz"
sha256 = "<64 hex digits>"
version = "1.64.0-r4"
```

```bash
nice -n 19 ionice -c 3 ./build-push.py --build --local-dev local-dev-artifacts.toml \
    --target trixie-py314-php85 --target trixie-py314-php85-vsc
docker image inspect <printed -dev- tag> \
    --format '{{index .Config.Labels "net.volkb79.mdt.build-mode"}}'   # -> local-dev
```

Test the result with the runtime lane. `MDT_RUNTIME_LOCAL_DEV_MANIFEST` is **required** for a
local-dev image (the provenance tests compare its labels with your manifest entry for entry, and the
lane fails without it) and unused for a release image:

```bash
MDT_RUNTIME_IMAGE_NATIVE=<native -dev- tag> MDT_RUNTIME_IMAGE_VSC=<vsc -dev- tag> \
MDT_RUNTIME_LOCAL_DEV_MANIFEST=$PWD/local-dev-artifacts.toml run-gate image-runtime
```

Allowed names are the packages in `pip/wheels.list` (`cli-extended`, `ciu`, `cmru`, `topos`, `nyxloom`) plus
`pwmcp-installer` and `pwmcp-bundle`.
An unknown, missing, unreadable or hash-mismatched entry refuses the build and names it; names you
do not list still come from their releases. The images are loaded into your local Docker daemon as
`...-dev-<YYYYMMDD>[-<n>]` and `...-dev-latest`, labelled `local-dev`, and cannot be pushed - the
`--push`/`--rebuild` paths and the release wrapper refuse them. Point a devcontainer at one with
`"image": "<that tag>"`. Why it is built this way: [Local-dev builds](DESIGN-GUIDE.md#local-dev-builds-are-explicit-verified-and-unpublishable).

## Docker Hub login

Anonymous pulls from Docker Hub are limited to 100 per hour per IP, and every session on this host
shares that IP, so a build or `cmru versions resolve` can fail with `429`. Create a **read-only** access
token in Docker Hub (Account settings, Personal access tokens, "Public Repo Read-only") and log in once:

```bash
printf '%s' "$HUB_READ_TOKEN" | docker login docker.io --username <your-hub-user> --password-stdin
```

* **Where it is kept:** `~/.docker/config.json` in the persisted home, or a credential helper
  (`credsStore` / `credHelpers`). Nothing else holds it: no env file, no `ciu` secret, no cmru variable.
* **Who reads it:** `docker`, `buildx` (including the remote `mdt-managed` builder), `crane` and `regctl`
  today; `cmru versions` once its backlog item lands (it currently needs `username_env`/`token_env`).
* **Check:** `mdt doctor` (also `--check-only`) prints a notice, never an error, when there is no
  `docker.io` entry. The check reads the config's `auths`, `credHelpers` and `credsStore` keys; for a `credsStore` it runs the helper's `list` verb (server URLs only, never `get`, usernames are not printed) and says "cannot confirm" when the helper is absent or fails.

## AI CLI versions (copilot, openclaw, claudelink)

These three install the newest release that is at least 14 days old. The decision is recorded in
`cmru.toml` (`[versions.targets."npm.<tool>".resolved]`); `scripts/stage_tool_artifacts.py` reads that
record offline and refuses to stage when it is missing, unresolved or older than 28 days.

```bash
cd mdt-cockpit
cmru versions check        # is a newer eligible version available? (queries every target)
cmru versions resolve      # refresh the record, then commit cmru.toml, package-lock.json, constraints/, versions/
```

To pin one on purpose, give the target a reasoned override (cmru's supported form) and resolve:

```toml
[versions.targets."npm.copilot"]
mode = "single"
constraint = "*"
version = "1.0.90"
reason = "1.0.92-4 breaks the MCP handshake; revisit when 1.0.93 is eligible"
```

Staging fails when an override was edited without re-resolving (the declared `version` must equal the
resolved one) and the reason appears in the image manifest. Dependencies are held to the same cutoff
(`npm install --before`), so a build today installs the same tree as the day you resolved. `package-lock.json`
is written by `cmru versions resolve` and not read by the build.

**The eight `oci.*` targets are advisory: not consumed by the build (MDT-013).** They keep `resolve`
working and show what a vetted base would be; the base image is still the floating devcontainers tag chosen
by `scripts/resolve-devcontainers-release.py`, so do not read their digests as a certification of it.

`COPILOT_VERSION`, `OPENCLAW_VERSION`, `CLAUDELINK_VERSION`, `AIDER_VERSION` and `AIDER_COMMIT` are
refused with a message pointing here. aider is the documented exception: `aider-main-pin.toml` holds an
exact upstream `main` commit and the version it builds, until an aider release supports Python 3.14.
The R16 tools (claude, codex, reasonix, opencode, pi) keep following upstream latest with an exact
recorded version and no age window.

## Release consumer

A release also runs the `oci-push` lane (the push mechanics against a real local registry), inside
the image that same release just built. You do not name an image: `cmru release` runs `prepare`
(`build-push.py --build`, which leaves the OCI layouts) and then the lane. To run it by hand after
a build:

```bash
python3 build-push.py --build      # leaves build/oci-layouts/<target>/
run-gate oci-push             # fails if no layout exists
```

The MDT release configuration (schema version 1) contains these values:

```toml
schema_version = 1

[env]
BUILDX_BUILDER = "mdt-managed"
BUILDKIT_HOST = "unix:///run/mdt-buildkitd/buildkitd.sock"
RELEASE_IMAGE_FLOW = "load"
```

`scripts/ensure-release-builder.sh` only verifies the remote endpoint before a
release. If Docker/Buildx, the socket, the service identity, or the named
builder is unavailable, the release stops; it cannot silently create an
ungoverned replacement. `load` is the shipped source-first OCI-layout flow:
the build phase creates the artifact once and the push phase publishes that
same artifact with digest verification. Set `push` for direct registry export,
or `repack` for the optional validated OCI-layout compression path. The named
`mdt-managed` remote owns the persistent BuildKit cache; the release wrapper
reuses that cache without streaming a second local cache export through the
client API. Forced recompression is disabled so large layers that already have
a suitable encoding do not take a second compression pass.

When collecting an MDT script failure, keep the first diagnostic line: `MDT <version> — mdt-cockpit`, with the version read from `MDT_VERSION` or `MDT_IMAGE_VERSION` at invocation time.
