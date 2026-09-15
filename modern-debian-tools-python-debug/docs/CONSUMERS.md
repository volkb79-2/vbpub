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
`--wizard` preserves its values as defaults; add `--force` only to discard
those values as defaults and start from the current repository example. In
both cases replacement/backup happens only after the generated candidate
passes strict validation. Deleting the old file first loses those defaults and
does not create the automatic backup. The wizard also migrates the former
watcher names (`SWEEP_*`, `TESTRUNNER_IMAGE_PATTERNS`,
`BUILDKIT_NAME_PATTERNS`, and `DEVCONTAINER_NAME_PATTERNS`) to their current
`WATCHER_*` names when it opens an existing config, preserving tuned values
during an upgrade.

The wizard reads live `MemTotal`, `MemAvailable`, CPU count, swap, the
configured `IO_BASELINE_ENV` cache and `IO_BASELINE_TESTFILE`, and Docker mount
facts. It walks `dev.slice`, the guaranteed sibling,
`dev-interactive.slice`, `dev-background.slice`, `dev-gates.slice`, and
`dev-buildkitd.slice` in that order. For each it asks `MemoryMin`, `MemoryLow`,
`MemoryHigh`, and `MemoryMax`, plus the applicable CPU, `IOWeight`, swap, and
zswap controls. Its memory policy is:

- configured values within one slice must satisfy `MemoryMin <= MemoryLow <=
  MemoryHigh <= MemoryMax`; the wizard re-prompts the invalid right-hand field;
- sibling controls are independent and are not summed;
- a child `MemoryHigh` or `MemoryMax` above the parent is a review warning only,
  so an intentional overlapping policy remains possible.

Empty means the operator explicitly chose no directive; it is not an invented
fallback. Type `-` to omit any optional memory directive; Enter accepts its
shown proposal. MemoryMax is RAM only; MemorySwapMax is swap only. A baseline
benchmark uses the official kernel `io.cost` matrix against the persistent
`IO_BASELINE_TESTFILE`, never a raw device, and is reusable by identity rather
than by age. It saturates the disk for about 12 minutes at default settings,
so run it in a quiet window. The baseline run is offered only when `fio` and
`pv` are already installed; otherwise the installer installs them after
validation and you run the benchmark later.

The wizard also asks for `DEV_BUILDKITD_MAX_PARALLELISM`. This limits one
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
or `repack` for the optional validated OCI-layout compression path.
