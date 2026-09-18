# Consumer configuration

This document is the pasteable adoption contract for MDT's managed BuildKit
backend. Host setup is intentionally manual because it changes systemd,
Docker, and cgroup policy.

## Host prerequisite

On the Docker host, review and apply the shipped host configuration:

```bash
sudo ./host-setup/install.sh --wizard
sudo mdt-host-check.sh
```

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
MemorySwapMax is swap only. A benchmark uses the official kernel `io.cost` matrix against the persistent
`IO_BASELINE_TESTFILE`, never a raw device, and is reusable by identity rather
than by age. It saturates the disk for about 12 minutes at default settings,
so run it in a quiet window. `--with-baseline` installs missing `fio`/`pv`
before the wizard, asks for confirmation near the start, and runs one
background measurement while the remaining questions continue. Without that
flag, the wizard offers the measurement only when both tools are already
installed; otherwise the candidate is installed first and you can run the
benchmark later.

After that run, the wizard asks for four explicit static IO fallback values:
`DEV_STATIC_RIOPS`, `DEV_STATIC_WIOPS`, `DEV_STATIC_RBW`, and
`DEV_STATIC_WBW`. They are hard caps on the aggregate `dev.slice` root and
apply at boot or whenever no identity-verified benchmark results are
available. A successful measurement temporarily replaces them with
`DEV_IO_CAP_PCT` percent of the measured ceilings. Without current results,
containers receive no guessed per-container IO cap.

The wizard also asks for `CGROUP2_FLAGS`, the timer interval, and the one
out-of-tree Buildx name-pattern field. Correctly placed containers are selected
by their cgroup path, not a pattern. `CGROUP2_FLAGS=fix` lets the periodic host
service restore the `memory_recursiveprot` mount flag; `warn` only reports a
missing flag. The interval is a single systemd duration such as `5min`; match
patterns are space-separated shell globs. The wizard also asks for
`DEV_BUILDKITD_MAX_PARALLELISM`. This limits one
managed BuildKit daemon's internal solver parallelism; it does not serialize
client requests and is separate from release repack concurrency.

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

The accidental-worker policy vocabulary is:

```text
BUILDX_ACCIDENTAL_CONTAINER_POLICY=terminate    # default; remove unapproved workers
BUILDX_ACCIDENTAL_CONTAINER_POLICY=report-only  # detect and log, do not remove
```

Missing or invalid policy fails closed. The guard approves only the service
container whose inspected name, configured image, cgroup parent, and labels
match the host setup. A reserved name alone is not approval.

## Consumer devcontainer

Copy the current MDT template and retain these mandatory values in
`containerEnv` and `mounts`:

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

After the image is rebuilt, `postCreateCommand` runs
`finalize_container_environment.py`. It validates/reuses the named remote and
does not create a per-container or container-driver builder. A partial or
different `BUILDX_BUILDER`/`BUILDKIT_HOST` pair fails finalization. With the
explicit environment above, both commands use the managed remote:

```bash
docker build -t example:dev .
docker buildx build --load -t example:dev .
docker buildx inspect mdt-managed
```

Do not rely on a user's Buildx “current builder” state. The environment is the
consumer contract and is re-applied by the template/finalizer.

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

### Persistent AI CLI state

The vendored template uses source-backed bind mounts under
`${localEnv:HOME}/mdt--mounted-folders/`. In particular, keep these complete roots. The
OpenCode source is `${localEnv:HOME}/mdt--mounted-folders/opencode-data`.

| Tool | Host source suffix | Container target | Durable state |
|---|---|---|---|
| Pi | `.pi` | `/home/vscode/.pi` | config and `~/.pi/agent/sessions/` |
| ClaudeLink | `.claudelink` | `/home/vscode/.claudelink` | `nexus.db`, scheduler state/logs, and related runtime files |
| OpenCode | `opencode-data` | `/home/vscode/.local/share/opencode` | auth, sessions, logs, and runtime state |

Copy the current template's mount entries unchanged so the host sources and container
targets stay aligned. The bootstrap creates empty sources but does not migrate existing
data. Before the first rebuild, run this on the host; it contains no credentials or
secret literals:

```sh
mdt_state="$HOME/mdt--mounted-folders"
mkdir -p "$mdt_state"
for d in .claude .claudelink .codex .config .gnupg .local .minisign .openclaw .pi .reasonix; do
  if [ -d "$HOME/$d" ]; then
    mkdir -p "$mdt_state/$d"
    cp -a "$HOME/$d/." "$mdt_state/$d/"
  fi
done
if [ -d "$HOME/.local/share/opencode" ]; then
  mkdir -p "$mdt_state/opencode-data"
  cp -a "$HOME/.local/share/opencode/." "$mdt_state/opencode-data/"
fi
```

The separate `opencode-data` mount overlays the same path inside `.local`, so OpenCode
must be copied to that dedicated source. For the complete mount list, bootstrap behavior,
and rollback, see [the template guide](../templates/README.md) and the
[devcontainer lifecycle reference](../DEVCONTAINER-LIFECYCLE.md).

If Pi or ClaudeLink state currently exists only in a running devcontainer, use the
[running-container migration runbook](../DEVCONTAINER-LIFECYCLE.md#migrating-a-running-devcontainer-before-adopting-the-mounts)
before rebuilding. It stops and verifies the container, then uses `docker cp` only after
ClaudeLink is quiesced; the complete `.claudelink` directory is copied so `nexus.db` stays
paired with any SQLite WAL/SHM sidecars.

## Release consumer

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

When collecting an MDT script failure, keep the first diagnostic line: `MDT <version> — modern Debian tools and Python debug`, with the version read from `MDT_VERSION` or `MDT_IMAGE_VERSION` at invocation time.
