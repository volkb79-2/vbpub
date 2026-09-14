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

The wizard reads live `MemTotal`, `MemAvailable`, CPU count, swap, and I/O
baseline facts. It walks `dev.slice`, the guaranteed sibling,
`dev-interactive.slice`, `dev-background.slice`, `dev-gates.slice`, and
`dev-buildkitd.slice` in that order. For each it asks `MemoryMin`, `MemoryLow`,
`MemoryHigh`, and `MemoryMax`, plus the applicable CPU, `IOWeight`, swap, and
zswap controls. Empty means the operator explicitly chose no directive; it is
not an invented fallback. The wizard compares binary sizes after converting
them to KiB and re-prompts on `MemoryMin <= MemoryLow <= MemoryHigh <=
MemoryMax` or aggregate violations.

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

## Release consumer

The MDT release configuration (schema version 1) contains these values:

```toml
schema_version = 1

[env]
BUILDX_BUILDER = "mdt-managed"
BUILDKIT_HOST = "unix:///run/mdt-buildkitd/buildkitd.sock"
```

`scripts/ensure-release-builder.sh` only verifies the remote endpoint before a
release. If Docker/Buildx, the socket, the service identity, or the named
builder is unavailable, the release stops; it cannot silently create an
ungoverned replacement.
